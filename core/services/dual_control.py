"""
core/services/dual_control.py — Dual-Control (M-of-N Co-Approval) Engine
========================================================================
Implements the Dual-Control principle to prevent insider threat abuses.
Admins alone CANNOT decrypt client records.
Privileged operations require an active Dual-Control Approval Token issued and
co-signed by an authorized Security Officer.
"""

import time
import secrets
from typing import Optional, Dict
from database.sql_db import get_sql_db

# Each approval is for one kind of action. A token approved for reading a
# client's records must not also unlock the irreversible erasure of that client.
REQUEST_TYPES = ("DECRYPT_RAW_RECORD", "ERASE_PATIENT", "REVOKE_PASSKEY")

# The same range the web form offers. Without an upper bound a requester could
# ask for a token that stays valid for years.
MIN_VALIDITY_MINUTES = 5
MAX_VALIDITY_MINUTES = 120


class DualControlEngine:
    """
    Manages dual-control authorization tokens and co-signatures.
    """

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dual_control_tokens (
                    token_id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    target_patient_id TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    co_signed_by TEXT,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            conn.commit()

    def request_dual_control_access(
        self,
        request_type: str,
        target_patient_id: str,
        requested_by: str,
        reason: str,
        validity_minutes: int = 30
    ) -> Dict:
        if request_type not in REQUEST_TYPES:
            raise ValueError(f"Unknown request type. Use one of {', '.join(REQUEST_TYPES)}.")
        if not MIN_VALIDITY_MINUTES <= validity_minutes <= MAX_VALIDITY_MINUTES:
            raise ValueError(
                f"Validity must be between {MIN_VALIDITY_MINUTES} and {MAX_VALIDITY_MINUTES} minutes.")
        self._ensure_table()
        token_id = f"dc_{secrets.token_hex(12)}"
        now = time.time()
        expires_at = now + (validity_minutes * 60)

        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO dual_control_tokens
                (token_id, request_type, target_patient_id, requested_by, reason, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_id, request_type, target_patient_id, requested_by, reason, "PENDING_CO_APPROVAL", now, expires_at)
            )
            conn.commit()

        return {
            "token_id": token_id,
            "status": "PENDING_CO_APPROVAL",
            "message": f"Dual-control request created for patient {target_patient_id}. Awaiting co-signature by Security Officer.",
            "expires_at": expires_at
        }

    def co_sign_request(self, token_id: str, co_signer_username: str, co_signer_role: str) -> Dict:
        """
        Co-signs a dual-control request (Must be executed by a user with role 'security_officer' or 'admin').
        """
        if co_signer_role not in ("admin", "security_officer"):
            raise ValueError("Only a Security Officer or System Administrator can co-sign dual-control requests.")

        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT requested_by, status, expires_at, target_patient_id FROM dual_control_tokens WHERE token_id = ?",
                (token_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Dual-control request token not found.")

            requested_by, status, expires_at, target_patient_id = row[0], row[1], row[2], row[3]

            if co_signer_username == requested_by:
                raise ValueError("Dual-Control Violation: The requesting user cannot self-approve their own request!")

            if time.time() > expires_at:
                cursor.execute("UPDATE dual_control_tokens SET status = 'EXPIRED' WHERE token_id = ?", (token_id,))
                conn.commit()
                raise ValueError("Dual-control request has expired.")

            if status != "PENDING_CO_APPROVAL":
                raise ValueError(f"Dual-control request is in state {status} and cannot be co-signed.")

            cursor.execute(
                "UPDATE dual_control_tokens SET status = 'APPROVED', co_signed_by = ? WHERE token_id = ?",
                (co_signer_username, token_id)
            )
            conn.commit()

        return {
            "token_id": token_id,
            "status": "APPROVED",
            "co_signed_by": co_signer_username,
            "target_patient_id": target_patient_id,
            "message": f"Dual-Control request co-signed successfully by {co_signer_username}. Access granted."
        }

    def get_request(self, token_id: str) -> Optional[Dict]:
        """Read-only status of a dual-control request, or None when unknown."""
        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT token_id, request_type, target_patient_id, requested_by,
                       reason, status, created_at, expires_at, co_signed_by
                FROM dual_control_tokens WHERE token_id = ?
                """,
                (token_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

        status = row[5]
        expires_at = row[7]
        if status == "APPROVED" and time.time() > expires_at:
            status = "EXPIRED"

        return {
            "token_id":          row[0],
            "request_type":      row[1],
            "target_patient_id": row[2],
            "requested_by":      row[3],
            "reason":            row[4],
            "status":            status,
            "created_at":        row[6],
            "expires_at":        expires_at,
            "co_signed_by":      row[8],
        }

    def is_dual_control_approved(self, token_id: str, patient_id: str, *,
                                 request_type: str, username: str) -> bool:
        """
        Verifies that the token is approved, unexpired, and issued for exactly
        this target, this kind of action and this user.

        The token is bound to the person who requested it: the co-signer, or a
        third operator who saw the token id, cannot use it. Otherwise one
        approval would open the record for everyone who learns the id.
        """
        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, expires_at, target_patient_id, request_type, requested_by "
                "FROM dual_control_tokens WHERE token_id = ?",
                (token_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False

            status, expires_at, target_patient_id = row[0], row[1], row[2]
            if target_patient_id != patient_id:
                return False
            if row[3] != request_type or row[4] != username:
                return False

            if time.time() > expires_at:
                return False

            return status == "APPROVED"


# Global singleton instance
dual_control_engine = DualControlEngine()
