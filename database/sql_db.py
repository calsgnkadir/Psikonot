import logging
import os
import sqlite3
import time

from database import paths

logger = logging.getLogger("vhv.sqldb")

# Dynamic PostgreSQL import
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    POSTGRES_AVAILABLE = True
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    POSTGRES_AVAILABLE = False

# VHV_SQLITE_PATH, or database/vault.db (see database/paths.py).
DEFAULT_SQLITE_PATH = paths.sqlite_path()

# practice_staff: one row per (secretary, practitioner) link.
_STAFF_TABLE = """
    CREATE TABLE IF NOT EXISTS {name} (
        staff_username        VARCHAR(100) NOT NULL,
        practitioner_username VARCHAR(100) NOT NULL,
        created_at            {double_type} NOT NULL,
        PRIMARY KEY (staff_username, practitioner_username)
    )
"""
_STAFF_KEY = ["staff_username", "practitioner_username"]


def _primary_key(cursor, is_postgres: bool, table: str) -> list:
    """The primary key columns of `table`, in key order."""
    if is_postgres:
        cursor.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "WHERE tc.table_schema = current_schema() AND tc.table_name = %s "
            "AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.ordinal_position",
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]
    # PRAGMA takes no bound parameters; `table` is one of our own constants.
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [(row[5], row[1]) for row in cursor.fetchall() if row[5]]
    return [name for _, name in sorted(columns)]


def migrate_practice_staff(cursor, is_postgres: bool) -> None:
    """Create practice_staff keyed on (staff_username, practitioner_username),
    or rebuild an old one keyed on staff_username alone (one practitioner per
    secretary). Neither SQLite nor a portable ALTER changes a primary key in
    place, so: a new table, copy every link, drop the old one, rename. Runs on
    every start and does nothing once the key is right; a leftover new table
    from an interrupted rebuild is dropped and the rebuild done again. The
    caller commits, so on PostgreSQL the swap is one transaction."""
    double_type = "DOUBLE PRECISION" if is_postgres else "REAL"
    cursor.execute(_STAFF_TABLE.format(name="practice_staff", double_type=double_type))
    if _primary_key(cursor, is_postgres, "practice_staff") != _STAFF_KEY:
        cursor.execute("DROP TABLE IF EXISTS practice_staff_new")
        cursor.execute(_STAFF_TABLE.format(name="practice_staff_new", double_type=double_type))
        cursor.execute(
            "INSERT INTO practice_staff_new (staff_username, practitioner_username, created_at) "
            "SELECT staff_username, practitioner_username, created_at FROM practice_staff"
        )
        cursor.execute("DROP TABLE practice_staff")
        cursor.execute("ALTER TABLE practice_staff_new RENAME TO practice_staff")
        logger.info("[SQL DB] practice_staff now links a secretary to several practitioners.")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_staff_practitioner ON practice_staff (practitioner_username)"
    )


class SQLDatabaseManager:
    def __init__(self):
        self.db_url = os.getenv("VHV_DATABASE_URL")
        self.is_postgres = False

        # Check environment and try PostgreSQL
        if self.db_url and POSTGRES_AVAILABLE:
            try:
                # Test connection
                conn = psycopg2.connect(self.db_url)
                conn.close()
                self.is_postgres = True
                logger.info("[SQL DB] Connected successfully to PostgreSQL database.")
            except Exception as e:
                logger.error(f"[SQL DB Warning] Failed to connect to PostgreSQL ({e}). Falling back to SQLite.")
        elif self.db_url and not POSTGRES_AVAILABLE:
            logger.warning("[SQL DB Warning] VHV_DATABASE_URL is set but psycopg2 is not installed. Falling back to SQLite.")

        if not self.is_postgres:
            logger.info(f"[SQL DB] Using SQLite database at: {DEFAULT_SQLITE_PATH}")
            # Ensure database directory exists
            os.makedirs(os.path.dirname(DEFAULT_SQLITE_PATH), exist_ok=True)

        self.init_db()

    def get_connection(self):
        if self.is_postgres:
            conn = psycopg2.connect(self.db_url)
            # Use RealDictCursor to act like dict-like objects
            return conn
        else:
            conn = sqlite3.connect(DEFAULT_SQLITE_PATH)
            conn.row_factory = sqlite3.Row
            return conn

    def init_db(self):
        """Creates tables if they do not exist."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Determine syntax compatibility
        text_type = "TEXT"
        boolean_type = "BOOLEAN"
        double_type = "DOUBLE PRECISION" if self.is_postgres else "REAL"

        try:
            # Users Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS users (
                    id VARCHAR(100) PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL,
                    full_name VARCHAR(100) NOT NULL,
                    specialty VARCHAR(100),
                    institution VARCHAR(100),
                    patient_id VARCHAR(100),
                    clearance VARCHAR(50),
                    totp_secret VARCHAR(100),
                    totp_enabled {boolean_type} DEFAULT FALSE,
                    account_status VARCHAR(30) DEFAULT 'ACTIVE_ENROLLED',
                    provisioned_by VARCHAR(100),
                    totp_last_step BIGINT
                )
            """)
            # Existing databases predate the onboarding lifecycle column.
            try:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN account_status VARCHAR(30) DEFAULT 'ACTIVE_ENROLLED'"
                )
            except Exception:
                pass
            # Who provisioned the account. Dual-control refuses a co-signature
            # between an account and its provisioner: the provisioner held the
            # account's enrollment code, so it is not a second person. NULL for
            # seeded accounts.
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN provisioned_by VARCHAR(100)")
            except Exception:
                pass
            # The last TOTP time step accepted for the account. A code is valid
            # for ~90 seconds, so without it the same code could be used again.
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN totp_last_step BIGINT")
            except Exception:
                pass

            # Raised whenever the account's open sessions must end: a password
            # change or reset, or the account being disabled. A session token
            # carries the value it was issued under (backend/dependencies.py).
            if_not_exists = "IF NOT EXISTS " if self.is_postgres else ""
            try:
                cursor.execute(
                    f"ALTER TABLE users ADD COLUMN {if_not_exists}session_epoch INTEGER DEFAULT 0")
            except Exception:
                pass

            # Out-of-band enrollment tokens. A provisioned account stays inactive
            # until the holder redeems a single-use token delivered out of band.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS enrollment_tokens (
                    token_hash VARCHAR(128) PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    expires_at {double_type} NOT NULL,
                    used {boolean_type} DEFAULT FALSE,
                    created_by VARCHAR(100) NOT NULL,
                    created_at {double_type} NOT NULL,
                    purpose VARCHAR(20) DEFAULT 'ENROLL'
                )
            """)
            # ENROLL opens a new account; RESET replaces a forgotten password
            # (backend/routers/accounts.py). Each is redeemed only at its own
            # endpoint. Older rows are all enrollment codes.
            try:
                cursor.execute(
                    f"ALTER TABLE enrollment_tokens ADD COLUMN {if_not_exists}purpose VARCHAR(20) DEFAULT 'ENROLL'")
            except Exception:
                pass

            # Per-patient erasure keys. The at-rest key is derived from the KMS
            # root AND this per-patient secret, so destroying this row alone
            # cryptographically shreds one patient's records (GDPR/KVKK Art. 17)
            # while leaving the append-only chain and its signatures intact.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS patient_erasure_keys (
                    patient_id VARCHAR(100) PRIMARY KEY,
                    secret_hex VARCHAR(128) NOT NULL,
                    created_at {double_type} NOT NULL
                )
            """)

            # Token Blacklist Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS blacklisted_tokens (
                    jti VARCHAR(255) PRIMARY KEY,
                    exp {double_type} NOT NULL
                )
            """)

            # WebAuthn / Passkeys Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS webauthn_credentials (
                    credential_id VARCHAR(255) PRIMARY KEY,
                    username VARCHAR(100) NOT NULL,
                    public_key {text_type} NOT NULL,
                    sign_count INTEGER DEFAULT 0,
                    created_at {double_type} NOT NULL
                )
            """)
            # NOTE: No passkey credential is ever seeded here. A pre-seeded
            # credential would let anyone authenticate as its owner straight from
            # the login screen. Passkeys must be enrolled per device by the
            # account holder via POST /api/v1/auth/webauthn/register.
            cursor.execute(
                "DELETE FROM webauthn_credentials WHERE credential_id = 'passkey_default_demo'"
            )

            # Rate Limits Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    ip VARCHAR(100) NOT NULL,
                    timestamp {double_type} NOT NULL
                )
            """)

            # Patient Pseudonyms Table (Identity Decoupling)
            # Maps real patient IDs to cryptographic anonymous identifiers.
            # If the clinical data store is breached, records cannot be
            # linked back to real identities without this mapping table.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS patient_pseudonyms (
                    patient_id   VARCHAR(100) PRIMARY KEY,
                    anon_id      VARCHAR(100) UNIQUE NOT NULL,
                    created_at   {double_type} NOT NULL
                )
            """)

            # Client cards. Clients do not sign in: each client is a card owned
            # by one practitioner, with the contact details the practice needs
            # and the date the KVKK forms were signed. Nothing clinical — that
            # is in the encrypted record chain (core/services/client_registry.py).
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS clients (
                    patient_id            VARCHAR(20) PRIMARY KEY,
                    practitioner_username VARCHAR(100) NOT NULL,
                    full_name             VARCHAR(100) NOT NULL,
                    phone                 VARCHAR(30),
                    email                 VARCHAR(120),
                    kvkk_signed_on        VARCHAR(10),
                    created_by            VARCHAR(100) NOT NULL,
                    created_at            {double_type} NOT NULL,
                    updated_by            VARCHAR(100),
                    updated_at            {double_type}
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_clients_practitioner ON clients (practitioner_username)"
            )

            # Practice staff: which practitioners a secretary works for. One row
            # per link, so a center's front desk can serve several
            # practitioners. A secretary sees those practitioners' appointment
            # books and nothing else — never a client's records (see
            # core/services/access_policy.py).
            migrate_practice_staff(cursor, self.is_postgres)

            # Appointments. Only scheduling facts live here — who, with whom,
            # when, how — and deliberately no free-text field, so clinical
            # content cannot end up in this unencrypted table by accident.
            # Times are UTC epoch seconds.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS appointments (
                    id                    VARCHAR(40) PRIMARY KEY,
                    practitioner_username VARCHAR(100) NOT NULL,
                    patient_id            VARCHAR(100) NOT NULL,
                    starts_at             {double_type} NOT NULL,
                    duration_min          INTEGER NOT NULL,
                    session_format        VARCHAR(20) NOT NULL,
                    status                VARCHAR(20) NOT NULL,
                    created_by            VARCHAR(100) NOT NULL,
                    created_at            {double_type} NOT NULL,
                    updated_by            VARCHAR(100),
                    updated_at            {double_type}
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_appointments_practitioner "
                "ON appointments (practitioner_username, starts_at)"
            )

            # Therapy rooms the centre's practitioners share. A name is a short
            # label ("Oda 1"); rooms are switched off, never deleted, so past
            # appointments keep theirs (core/services/rooms.py).
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS rooms (
                    id         VARCHAR(40) PRIMARY KEY,
                    name       VARCHAR(40) NOT NULL,
                    active     {boolean_type} NOT NULL DEFAULT TRUE,
                    created_by VARCHAR(100) NOT NULL,
                    created_at {double_type} NOT NULL,
                    updated_by VARCHAR(100),
                    updated_at {double_type}
                )
            """)
            # The room of an in-person appointment. Optional: appointments
            # booked before rooms existed keep NULL.
            try:
                cursor.execute(f"ALTER TABLE appointments ADD COLUMN {if_not_exists}room_id VARCHAR(40)")
            except Exception:
                pass
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_appointments_room ON appointments (room_id, starts_at)"
            )

            # KVKK: a request to erase a client's data, filed by their
            # practitioner. An operator carries it out with the
            # dual-control-gated crypto-shred, then closes the request.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS erasure_requests (
                    id           VARCHAR(40) PRIMARY KEY,
                    patient_id   VARCHAR(100) NOT NULL,
                    requested_by VARCHAR(100) NOT NULL,
                    requested_at {double_type} NOT NULL,
                    status       VARCHAR(20) NOT NULL,
                    handled_by   VARCHAR(100),
                    handled_at   {double_type}
                )
            """)

            # Version 6.0 renamed two role ids (doctor -> practitioner,
            # vip_patient -> client). Rewrite existing rows in place so an old
            # database keeps working. Safe to run on every start.
            cursor.execute("UPDATE users SET role = 'practitioner' WHERE role = 'doctor'")
            cursor.execute("UPDATE users SET role = 'client' WHERE role = 'vip_patient'")
            # Clients used to have accounts. They no longer sign in, so any
            # client account left in an old database is switched off. Disabled,
            # not deleted: its audit history stays intact.
            cursor.execute("UPDATE users SET account_status = 'DISABLED' WHERE role = 'client'")
            # Accounts provisioned before provisioned_by existed: the operator
            # who created their enrollment code provisioned them. Safe to run on
            # every start.
            cursor.execute(
                "UPDATE users SET provisioned_by = ("
                "SELECT MIN(t.created_by) FROM enrollment_tokens t WHERE t.username = users.username"
                ") WHERE provisioned_by IS NULL"
            )

            conn.commit()
            logger.info("[SQL DB] Tables initialized successfully.")
        except Exception as e:
            conn.rollback()
            logger.error(f"[SQL DB Error] Schema initialization failed: {e}")
            raise e
        finally:
            cursor.close()
            conn.close()

    def seed_default_users(self):
        """Seeds default users if database is empty."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            from core.security import hash_password
            defaults = [
                (
                    "USR-ADMIN-001",
                    "admin",
                    hash_password("Admin@2026Secure!"),
                    "admin",
                    "System Administrator",
                    None,
                    None,
                    None,
                    None,
                    None,
                    False
                ),
                (
                    # New id: an old database still holds "USR-DOC-001" for the
                    # pre-6.0 demo doctor, and reusing it breaks the insert.
                    "USR-PRAC-001",
                    "psk.elif",
                    hash_password("Practitioner@2026!"),
                    "practitioner",
                    "Uzm. Psk. Elif Yılmaz",
                    "Clinical Psychology",
                    "Yılmaz Psychology Practice",
                    None,
                    None,
                    None,
                    False
                ),
                # The practitioner's secretary: runs the appointment book, never
                # sees a record. Linked to psk.elif below.
                (
                    "USR-SEC-001",
                    "secretary.ayse",
                    hash_password("Secretary@2026!"),
                    "secretary",
                    "Ayşe Demir",
                    None,
                    "Yılmaz Psychology Practice",
                    None,
                    None,
                    None,
                    False
                ),
                # The Security Officer exists so the M-of-N Dual-Control policy is
                # actually satisfiable: an administrator cannot co-sign their own
                # request, so a second privileged principal is required.
                (
                    "USR-SECOFF-001",
                    "sec.officer",
                    hash_password("SecOfficer@2026!"),
                    "security_officer",
                    "Security Officer",
                    None,
                    None,
                    None,
                    "TOP_SECRET",
                    None,
                    False
                ),
            ]

            insert_sql = """
                INSERT INTO users (id, username, password_hash, role, full_name, specialty, institution, patient_id, clearance, totp_secret, totp_enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """ if self.is_postgres else """
                INSERT INTO users (id, username, password_hash, role, full_name, specialty, institution, patient_id, clearance, totp_secret, totp_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            # Seed per account rather than all-or-nothing, so an existing database
            # picks up accounts added in later versions.
            seeded = 0
            for account in defaults:
                cursor.execute(
                    "SELECT COUNT(*) FROM users WHERE username = %s" if self.is_postgres
                    else "SELECT COUNT(*) FROM users WHERE username = ?",
                    (account[1],)
                )
                row = cursor.fetchone()
                if row and row[0] > 0:
                    continue
                cursor.execute(insert_sql, account)
                seeded += 1

            cursor.execute(
                "SELECT 1 FROM practice_staff WHERE staff_username = %s AND practitioner_username = %s"
                if self.is_postgres else
                "SELECT 1 FROM practice_staff WHERE staff_username = ? AND practitioner_username = ?",
                ("secretary.ayse", "psk.elif"),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO practice_staff (staff_username, practitioner_username, created_at) "
                    + ("VALUES (%s, %s, %s)" if self.is_postgres else "VALUES (?, ?, ?)"),
                    ("secretary.ayse", "psk.elif", time.time()),
                )

            # The demo practitioner's clients: cards, not accounts.
            for patient_id, full_name, phone, email in DEMO_CLIENTS:
                cursor.execute(
                    "SELECT 1 FROM clients WHERE patient_id = %s" if self.is_postgres
                    else "SELECT 1 FROM clients WHERE patient_id = ?",
                    (patient_id,),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO clients (patient_id, practitioner_username, full_name, phone, email, "
                        "kvkk_signed_on, created_by, created_at) "
                        + ("VALUES (%s, %s, %s, %s, %s, %s, %s, %s)" if self.is_postgres
                           else "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"),
                        (patient_id, "psk.elif", full_name, phone, email,
                         time.strftime("%Y-%m-%d", time.gmtime(time.time() - 35 * 86400)),
                         "secretary.ayse", time.time()),
                    )

            # Demo accounts from before the 6.0 rename. Their passwords are
            # published in old READMEs, so an old database must not keep them
            # usable. Disabled, not deleted: their audit history stays intact.
            cursor.execute(
                "UPDATE users SET account_status = 'DISABLED' WHERE username IN (%s, %s)"
                if self.is_postgres else
                "UPDATE users SET account_status = 'DISABLED' WHERE username IN (?, ?)",
                LEGACY_DEMO_USERNAMES,
            )
            conn.commit()
            if seeded:
                logger.info(f"[SQL DB] Default users seeded successfully ({seeded} account(s)).")
        except Exception as e:
            conn.rollback()
            logger.error(f"[SQL DB Error] Seeding failed: {e}")
        finally:
            cursor.close()
            conn.close()

# Pre-6.0 demo accounts, switched off by seed_default_users().
LEGACY_DEMO_USERNAMES = ("dr.smith", "vip001")

# The accounts seed_default_users() creates. Their passwords are published, so
# in demo mode nobody may change, reset or disable them (backend/routers/accounts.py).
DEMO_USERNAMES = ("admin", "psk.elif", "secretary.ayse", "sec.officer")

# The demo practitioner's client cards (made-up people and numbers).
DEMO_CLIENTS = (
    ("CL-001", "Ahmet Karataş", "+90 532 000 00 01", "ahmet@example.com"),
    ("CL-002", "Zeynep Arslan", "+90 532 000 00 02", None),
    ("CL-003", "Mert Kaya", "+90 532 000 00 03", "mert@example.com"),
)

# Singleton instance
default_sql_db = SQLDatabaseManager()

def to_placeholder(sql: str) -> str:
    """SQL here is written with SQLite's ? placeholders; psycopg2 only takes %s.
    Every statement with parameters goes through this (or run_sql)."""
    if default_sql_db.is_postgres:
        return sql.replace("?", "%s")
    return sql


def run_sql(sql: str, params: tuple = (), fetch: str = ""):
    """Run one statement on its own connection, commit it and close it.

    fetch="" returns [], "one" returns [row or None], "all" every row, and
    "rowcount" the number of rows changed. Rolls back on an error. Use it
    instead of `with get_connection() as conn:`, which in sqlite3 and psycopg2
    only ends the transaction and leaves the connection open. Statements that
    must share one transaction keep their own connection and wrap each SQL
    with to_placeholder()."""
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(to_placeholder(sql), params)
        if fetch == "all":
            result = cursor.fetchall()
        elif fetch == "one":
            result = [cursor.fetchone()]
        elif fetch == "rowcount":
            result = cursor.rowcount
        else:
            result = []
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def blacklist_token(jti: str, exp: float) -> None:
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(to_placeholder("SELECT 1 FROM blacklisted_tokens WHERE jti = ?"), (jti,))
        if cursor.fetchone():
            cursor.execute(to_placeholder("UPDATE blacklisted_tokens SET exp = ? WHERE jti = ?"), (exp, jti))
        else:
            cursor.execute(to_placeholder("INSERT INTO blacklisted_tokens (jti, exp) VALUES (?, ?)"), (jti, exp))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def is_token_blacklisted(jti: str) -> bool:
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        sql = to_placeholder("SELECT exp FROM blacklisted_tokens WHERE jti = ?")
        cursor.execute(sql, (jti,))
        row = cursor.fetchone()
        if row:
            try:
                exp = float(row[0])
            except (TypeError, KeyError, IndexError, ValueError):
                exp = float(dict(row)["exp"])
            if time.time() > exp:
                return False
            return True
        return False
    finally:
        cursor.close()
        conn.close()


def clean_expired_blacklisted_tokens() -> None:
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        sql = to_placeholder("DELETE FROM blacklisted_tokens WHERE exp < ?")
        cursor.execute(sql, (time.time(),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()

def get_sql_db() -> SQLDatabaseManager:
    return default_sql_db
