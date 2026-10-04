from typing import Optional, List
from core.domain.entities import User
from core.ports.repositories import IUserRepository
from database.sql_db import default_sql_db

def _to_placeholder(sql: str) -> str:
    """Helper to convert standard sqlite ? placeholders to PostgreSQL %s if active."""
    if default_sql_db.is_postgres:
        return sql.replace("?", "%s")
    return sql

_USER_COLS = ["id", "username", "password_hash", "role", "full_name", "specialty", "institution", "patient_id", "clearance", "totp_secret", "totp_enabled", "account_status"]

def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, (tuple, list)):
        return {col: row[i] for i, col in enumerate(_USER_COLS) if i < len(row)}
    try:
        d = dict(row)
        if d and not any(isinstance(k, str) for k in d.keys()):
            return {col: row[i] for i, col in enumerate(_USER_COLS) if i < len(row)}
        return d
    except (TypeError, ValueError):
        return {}

class SQLUserRepository(IUserRepository):
    def save_user(self, user: User) -> None:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        try:
            # Check if user exists by username
            sql_check = _to_placeholder("SELECT id FROM users WHERE username = ?")
            cursor.execute(sql_check, (user.username,))
            exists = cursor.fetchone()

            if exists:
                # Update
                sql_update = _to_placeholder("""
                    UPDATE users
                    SET password_hash = ?, role = ?, full_name = ?, specialty = ?,
                        institution = ?, patient_id = ?, clearance = ?, totp_secret = ?, totp_enabled = ?, account_status = ?
                    WHERE username = ?
                """)
                cursor.execute(sql_update, (
                    user.password_hash,
                    user.role,
                    user.full_name,
                    user.specialty,
                    user.institution,
                    user.patient_id,
                    user.clearance,
                    user.totp_secret,
                    user.totp_enabled,
                    user.account_status,
                    user.username
                ))
            else:
                # Insert
                sql_insert = _to_placeholder("""
                    INSERT INTO users (id, username, password_hash, role, full_name, specialty, institution, patient_id, clearance, totp_secret, totp_enabled, account_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """)
                cursor.execute(sql_insert, (
                    user.id,
                    user.username,
                    user.password_hash,
                    user.role,
                    user.full_name,
                    user.specialty,
                    user.institution,
                    user.patient_id,
                    user.clearance,
                    user.totp_secret,
                    user.totp_enabled,
                    user.account_status
                ))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()

    def load_user(self, username: str) -> Optional[User]:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        try:
            sql = _to_placeholder("SELECT * FROM users WHERE username = ?")
            cursor.execute(sql, (username,))
            row = cursor.fetchone()
            if row:
                d = _row_to_dict(row)
                # Map db row to User
                # In PostgreSQL, custom cursors return dict, in SQLite,Row returns Row
                # Ensure boolean conversion
                totp_enabled = bool(d.get("totp_enabled"))
                return User(
                    id=d.get("id"),
                    username=d.get("username"),
                    password_hash=d.get("password_hash"),
                    role=d.get("role"),
                    full_name=d.get("full_name"),
                    specialty=d.get("specialty"),
                    institution=d.get("institution"),
                    patient_id=d.get("patient_id"),
                    clearance=d.get("clearance"),
                    totp_secret=d.get("totp_secret"),
                    totp_enabled=totp_enabled,
                    account_status=d.get("account_status") or "ACTIVE_ENROLLED"
                )
            return None
        finally:
            cursor.close()
            conn.close()

    def load_all_users(self) -> List[User]:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM users")
            rows = cursor.fetchall()
            users = []
            for row in rows:
                d = _row_to_dict(row)
                totp_enabled = bool(d.get("totp_enabled"))
                users.append(User(
                    id=d.get("id"),
                    username=d.get("username"),
                    password_hash=d.get("password_hash"),
                    role=d.get("role"),
                    full_name=d.get("full_name"),
                    specialty=d.get("specialty"),
                    institution=d.get("institution"),
                    patient_id=d.get("patient_id"),
                    clearance=d.get("clearance"),
                    totp_secret=d.get("totp_secret"),
                    totp_enabled=totp_enabled,
                    account_status=d.get("account_status") or "ACTIVE_ENROLLED"
                ))
            return users
        finally:
            cursor.close()
            conn.close()

    def user_exists(self, username: str) -> bool:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        try:
            sql = _to_placeholder("SELECT 1 FROM users WHERE username = ?")
            cursor.execute(sql, (username,))
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            conn.close()

    def delete_user(self, username: str) -> bool:
        conn = default_sql_db.get_connection()
        cursor = conn.cursor()
        try:
            sql = _to_placeholder("DELETE FROM users WHERE username = ?")
            cursor.execute(sql, (username,))
            # rowcount check
            # For sqlite: cursor.rowcount
            # For postgres: cursor.rowcount
            affected = cursor.rowcount > 0
            conn.commit()
            return affected
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()
