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
from database.sql_db import get_sql_db, run_sql, to_placeholder

# Each approval is for one kind of action. A token approved for reading a
# client's records must not also unlock the irreversible erasure of that client.
REQUEST_TYPES = ("DECRYPT_RAW_RECORD", "ERASE_PATIENT", "REVOKE_PASSKEY",
                 "RESET_ACCOUNT", "ENABLE_ACCOUNT")

# Approvals that act on an account (backend/routers/accounts.py). The target is
# a username, and each token is good for one action: it is consumed when used.
ACCOUNT_REQUEST_TYPES = ("RESET_ACCOUNT", "ENABLE_ACCOUNT")

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
        # REAL is a 4-byte float in PostgreSQL: too coarse for epoch seconds.
        double_type = "DOUBLE PRECISION" if get_sql_db().is_postgres else "REAL"
        run_sql(f"""
            CREATE TABLE IF NOT EXISTS dual_control_tokens (
                token_id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL,
                target_patient_id TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                co_signed_by TEXT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at {double_type} NOT NULL,
                expires_at {double_type} NOT NULL
            )
        """)

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

        run_sql(
            """
            INSERT INTO dual_control_tokens
            (token_id, request_type, target_patient_id, requested_by, reason, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token_id, request_type, target_patient_id, requested_by, reason, "PENDING_CO_APPROVAL", now, expires_at)
        )

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

        # One connection for the read, the checks and the update.
        conn = get_sql_db().get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                to_placeholder(
                    "SELECT requested_by, status, expires_at, target_patient_id, request_type "
                    "FROM dual_control_tokens WHERE token_id = ?"),
                (token_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Dual-control request token not found.")

            requested_by, status, expires_at, target_patient_id = row[0], row[1], row[2], row[3]

            if co_signer_username == requested_by:
                raise ValueError("Dual-Control Violation: The requesting user cannot self-approve their own request!")

            # A reset hands the requester a code to the target account. The
            # second person must be someone other than the account itself.
            if row[4] in ACCOUNT_REQUEST_TYPES and co_signer_username == target_patient_id:
                raise ValueError(
                    "Dual-Control Violation: An account cannot approve a request about itself.")

            # The operator who provisions an account holds its enrollment code,
            # so the two are one person as far as approval goes — either way round.
            if self._provisioned_by(cursor, co_signer_username) == requested_by or \
                    self._provisioned_by(cursor, requested_by) == co_signer_username:
                raise ValueError(
                    "Dual-Control Violation: An account cannot approve a request from the operator "
                    "who provisioned it, or the other way round.")

            if time.time() > expires_at:
                cursor.execute(
                    to_placeholder("UPDATE dual_control_tokens SET status = 'EXPIRED' WHERE token_id = ?"), (token_id,))
                conn.commit()
                raise ValueError("Dual-control request has expired.")

            if status != "PENDING_CO_APPROVAL":
                raise ValueError(f"Dual-control request is in state {status} and cannot be co-signed.")

            cursor.execute(
                to_placeholder("UPDATE dual_control_tokens SET status = 'APPROVED', co_signed_by = ? WHERE token_id = ?"),
                (co_signer_username, token_id)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

        return {
            "token_id": token_id,
            "status": "APPROVED",
            "co_signed_by": co_signer_username,
            "target_patient_id": target_patient_id,
            "message": f"Dual-Control request co-signed successfully by {co_signer_username}. Access granted."
        }

    @staticmethod
    def _provisioned_by(cursor, username: str) -> Optional[str]:
        """Who provisioned `username`, or None (seeded, or not a user)."""
        cursor.execute(to_placeholder("SELECT provisioned_by FROM users WHERE username = ?"), (username,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_request(self, token_id: str) -> Optional[Dict]:
        """Read-only status of a dual-control request, or None when unknown."""
        row = run_sql(
            """
            SELECT token_id, request_type, target_patient_id, requested_by,
                   reason, status, created_at, expires_at, co_signed_by
            FROM dual_control_tokens WHERE token_id = ?
            """,
            (token_id,), fetch="one")[0]
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
        row = run_sql(
            "SELECT status, expires_at, target_patient_id, request_type, requested_by "
            "FROM dual_control_tokens WHERE token_id = ?",
            (token_id,), fetch="one")[0]
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


    def consume(self, token_id: str, target: str, *, request_type: str, username: str) -> bool:
        """Use an approved token once: True if it was approved, unexpired and
        issued for exactly this target, action and user, and is now spent. One
        conditional UPDATE, so two requests with the same token cannot both
        succeed."""
        if not self.is_dual_control_approved(token_id, target, request_type=request_type, username=username):
            return False
        changed = run_sql(
            "UPDATE dual_control_tokens SET status = 'USED' "
            "WHERE token_id = ? AND status = 'APPROVED' AND target_patient_id = ? "
            "AND request_type = ? AND requested_by = ? AND expires_at >= ?",
            (token_id, target, request_type, username, time.time()), fetch="rowcount")
        return changed == 1


# Global singleton instance
dual_control_engine = DualControlEngine()
