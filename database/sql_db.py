import logging
import os
import sqlite3
import time

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

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Overridable so a container can keep the database in a data volume instead of
# next to the code in database/ (a volume mounted there would freeze the code).
DEFAULT_SQLITE_PATH = os.getenv(
    "VHV_SQLITE_PATH", os.path.join(_PROJECT_ROOT, "database", "vault.db")
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
                    account_status VARCHAR(30) DEFAULT 'ACTIVE_ENROLLED'
                )
            """)
            # Existing databases predate the onboarding lifecycle column.
            try:
                cursor.execute(
                    "ALTER TABLE users ADD COLUMN account_status VARCHAR(30) DEFAULT 'ACTIVE_ENROLLED'"
                )
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
                    created_at {double_type} NOT NULL
                )
            """)

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

            # Notifications Table
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS notifications (
                    id VARCHAR(100) PRIMARY KEY,
                    patient_id VARCHAR(100) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    message {text_type} NOT NULL,
                    severity VARCHAR(50) NOT NULL,
                    timestamp {double_type} NOT NULL,
                    read {boolean_type} DEFAULT FALSE
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

            # Practice staff: which practitioner a secretary works for. A
            # secretary sees that practitioner's appointment book and nothing
            # else — never a client's records (see core/services/access_policy.py).
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS practice_staff (
                    staff_username        VARCHAR(100) PRIMARY KEY,
                    practitioner_username VARCHAR(100) NOT NULL,
                    created_at            {double_type} NOT NULL
                )
            """)

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

            # KVKK: which version of the privacy notice (aydınlatma metni) each
            # client accepted, with their explicit consent, and when.
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS kvkk_notice_acceptances (
                    username       VARCHAR(100) NOT NULL,
                    notice_version VARCHAR(20) NOT NULL,
                    accepted_at    {double_type} NOT NULL,
                    client_ip      VARCHAR(64),
                    PRIMARY KEY (username, notice_version)
                )
            """)

            # KVKK: a client's request to have their data erased. An operator
            # carries it out with the dual-control-gated crypto-shred, then
            # closes the request.
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

            # Mahrem renamed two role ids (doctor -> practitioner,
            # vip_patient -> client). Rewrite existing rows in place so an old
            # database keeps working. Safe to run on every start.
            cursor.execute("UPDATE users SET role = 'practitioner' WHERE role = 'doctor'")
            cursor.execute("UPDATE users SET role = 'client' WHERE role = 'vip_patient'")

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
                    # pre-Mahrem demo doctor, and reusing it breaks the insert.
                    "USR-PRAC-001",
                    "psk.elif",
                    hash_password("Practitioner@2026!"),
                    "practitioner",
                    "Uzm. Psk. Elif Yılmaz",
                    "Clinical Psychology",
                    "Mahrem Psychology Practice",
                    None,
                    None,
                    None,
                    False
                ),
                (
                    "USR-CL-001",
                    "client001",
                    hash_password("Client@2026Secure!"),
                    "client",
                    "Ahmet Karataş",
                    None,
                    None,
                    "CL-001",
                    "TOP_SECRET",
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
                    "Mahrem Psychology Practice",
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
                "SELECT 1 FROM practice_staff WHERE staff_username = %s" if self.is_postgres
                else "SELECT 1 FROM practice_staff WHERE staff_username = ?",
                ("secretary.ayse",),
            )
            if not cursor.fetchone():
                import time
                cursor.execute(
                    "INSERT INTO practice_staff (staff_username, practitioner_username, created_at) "
                    + ("VALUES (%s, %s, %s)" if self.is_postgres else "VALUES (?, ?, ?)"),
                    ("secretary.ayse", "psk.elif", time.time()),
                )

            # Demo accounts from before the Mahrem rename. Their passwords are
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

# Pre-Mahrem demo accounts, switched off by seed_default_users().
LEGACY_DEMO_USERNAMES = ("dr.smith", "vip001")

# Singleton instance
default_sql_db = SQLDatabaseManager()

def blacklist_token(jti: str, exp: float) -> None:
    from infrastructure.repositories.sql_repositories import _to_placeholder
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(_to_placeholder("SELECT 1 FROM blacklisted_tokens WHERE jti = ?"), (jti,))
        if cursor.fetchone():
            cursor.execute(_to_placeholder("UPDATE blacklisted_tokens SET exp = ? WHERE jti = ?"), (exp, jti))
        else:
            cursor.execute(_to_placeholder("INSERT INTO blacklisted_tokens (jti, exp) VALUES (?, ?)"), (jti, exp))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def is_token_blacklisted(jti: str) -> bool:
    from infrastructure.repositories.sql_repositories import _to_placeholder
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        sql = _to_placeholder("SELECT exp FROM blacklisted_tokens WHERE jti = ?")
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
    from infrastructure.repositories.sql_repositories import _to_placeholder
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        sql = _to_placeholder("DELETE FROM blacklisted_tokens WHERE exp < ?")
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
