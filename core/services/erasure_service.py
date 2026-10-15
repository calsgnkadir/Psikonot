"""
core/services/erasure_service.py — per-patient erasure keys (crypto-shredding)
==============================================================================
The at-rest key for a patient's records is derived from the KMS root secret AND a
per-patient random secret held here. Destroying that per-patient secret makes the
combined key unrecoverable, so every record encrypted under it becomes permanently
undecryptable — the GDPR/KVKK Art. 17 "right to be forgotten" on an append-only
chain, achieved by crypto-shredding rather than by deleting blocks (which would
break the tamper-evident chain).

The secret is created lazily on the first write for a patient and destroyed by the
erasure endpoint. Once destroyed it is gone: the ciphertext remains on the chain
as opaque bytes that nothing can read.
"""

import secrets
import time
from typing import Optional

from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder


class ErasureKeyStore:
    """SQL-backed store of per-patient erasure secrets."""

    def get(self, patient_id: str) -> Optional[bytes]:
        db = get_sql_db()
        conn = db.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                _to_placeholder("SELECT secret_hex FROM patient_erasure_keys WHERE patient_id = ?"),
                (patient_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
        if not row:
            return None
        try:
            value = row["secret_hex"]
        except Exception:
            value = row[0]
        return bytes.fromhex(value)

    def get_or_create(self, patient_id: str) -> bytes:
        existing = self.get(patient_id)
        if existing is not None:
            return existing

        secret = secrets.token_bytes(32)
        db = get_sql_db()
        conn = db.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                _to_placeholder(
                    "INSERT INTO patient_erasure_keys (patient_id, secret_hex, created_at) "
                    "VALUES (?, ?, ?)"
                ),
                (patient_id, secret.hex(), time.time()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            # A concurrent writer may have created it first; prefer the stored one.
            got = self.get(patient_id)
            if got is not None:
                return got
            raise
        finally:
            cur.close()
            conn.close()
        return secret

    def exists(self, patient_id: str) -> bool:
        return self.get(patient_id) is not None

    def destroy(self, patient_id: str) -> bool:
        """Delete the erasure secret. Returns whether one existed. Irreversible.

        The client's pseudonym goes into the erasure ledger first, so restoring
        an older backup erases them again (scripts/restore.py). If the ledger
        cannot be written this raises and nothing is destroyed."""
        from core.pseudonymization.service import get_pseudonymization_service
        from core.services import erasure_ledger
        erasure_ledger.record(get_pseudonymization_service().anon_id(patient_id))

        existed = self.get(patient_id) is not None
        db = get_sql_db()
        conn = db.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                _to_placeholder("DELETE FROM patient_erasure_keys WHERE patient_id = ?"),
                (patient_id,),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return existed


_store = ErasureKeyStore()


def get_erasure_key_store() -> ErasureKeyStore:
    return _store
