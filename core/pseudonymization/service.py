"""
core/pseudonymization/service.py — Pseudonymization Service
==============================================================
Persistent service layer that manages pseudonym mappings in the
SQL database.  All record storage operations should use this service
to translate between real patient IDs and anonymous IDs.

Usage:
    from core.pseudonymization.service import get_pseudonymization_service

    svc = get_pseudonymization_service()

    # When storing a record:
    anon_id = svc.pseudonymize(patient_id="CL-001")
    # → store record under anon_id, never under "CL-001"

    # When retrieving for an authorized user:
    real_id = svc.depseudonymize(anon_id)
    # → "CL-001" (only works with access to the mapping table)
"""

import time
from typing import Optional, Dict

from core.pseudonymization.engine import PseudonymizationEngine, PseudonymMapping


class PseudonymizationService:
    """
    High-level service for pseudonymizing patient identities.
    Manages persistence of pseudonym mappings via SQL database.
    """

    def __init__(
        self,
        engine: Optional[PseudonymizationEngine] = None,
    ):
        self._engine = engine or PseudonymizationEngine()
        self._mapping = PseudonymMapping(self._engine)
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Load existing mappings from DB on first use."""
        if self._initialized:
            return
        self._initialized = True
        try:
            self._load_mappings_from_db()
        except Exception:
            pass  # DB may not be ready yet (e.g. during testing)

    def pseudonymize(self, patient_id: str) -> str:
        """
        Convert a real patient_id to its anonymous identifier.
        Creates and persists the mapping if it doesn't exist.

        Args:
            patient_id: Real identifier (e.g. "CL-001")

        Returns:
            Anonymous identifier (64-char hex string)
        """
        self._ensure_initialized()
        anon_id = self._mapping.get_or_create_anon_id(patient_id)

        # Persist to DB if new
        try:
            self._save_mapping_to_db(patient_id, anon_id)
        except Exception:
            pass  # Best-effort persistence

        return anon_id

    def anon_id(self, patient_id: str) -> str:
        """
        Deterministic anonymous identifier for a patient — pure, no DB write.

        Used to namespace the clinical chain store so the on-disk data never
        carries the real patient identifier. Because it is a keyed HMAC it is
        stable across reads and writes without a lookup table, so it is safe to
        call on every storage operation. Persisting the reverse mapping (for
        ``depseudonymize``) is done separately via :meth:`pseudonymize`.
        """
        return self._engine.generate_anon_id(patient_id)

    def depseudonymize(self, anon_id: str) -> Optional[str]:
        """
        Reverse-lookup: convert an anonymous ID back to the real patient_id.
        Only works if the mapping exists in memory or database.

        Args:
            anon_id: Anonymous identifier

        Returns:
            Real patient_id, or None if mapping not found
        """
        self._ensure_initialized()

        # Check in-memory cache first
        real_id = self._mapping.resolve_patient_id(anon_id)
        if real_id:
            return real_id

        # Try loading from DB
        real_id = self._lookup_from_db(anon_id)
        if real_id:
            self._mapping.register_mapping(real_id, anon_id)
        return real_id

    def forget(self, patient_id: str) -> bool:
        """
        Drop a patient's mapping from the in-memory cache (erasure). The caller
        deletes the database row first, so nothing can load it back.
        Returns True if the cache held it.
        """
        return self._mapping.remove(patient_id)

    def get_anon_id_for_display(self, patient_id: str) -> str:
        """
        Get a human-readable pseudonym prefix for UI display.
        Format: "ANON-<16 hex chars>"
        """
        return self._engine.generate_anon_id_with_prefix(patient_id)

    def get_all_mappings(self) -> Dict[str, str]:
        """Return all known pseudonym mappings."""
        self._ensure_initialized()
        return self._mapping.get_all_mappings()

    def hash_pii_field(self, value: str) -> str:
        """
        One-way hash a PII field for safe storage/comparison.
        """
        return self._engine.hash_field(value)

    # ── Database Persistence ────────────────────────────────

    def _save_mapping_to_db(self, patient_id: str, anon_id: str) -> None:
        """Persist a pseudonym mapping to the SQL database."""
        try:
            from database.sql_db import get_sql_db
            from infrastructure.repositories.sql_repositories import _to_placeholder

            db = get_sql_db()
            conn = db.get_connection()
            cursor = conn.cursor()
            try:
                # Check if mapping already exists
                sql = _to_placeholder(
                    "SELECT 1 FROM patient_pseudonyms WHERE patient_id = ?"
                )
                cursor.execute(sql, (patient_id,))
                if cursor.fetchone():
                    return  # Already persisted

                sql = _to_placeholder(
                    "INSERT INTO patient_pseudonyms (patient_id, anon_id, created_at) "
                    "VALUES (?, ?, ?)"
                )
                cursor.execute(sql, (patient_id, anon_id, time.time()))
                conn.commit()
            finally:
                cursor.close()
                conn.close()
        except Exception:
            pass  # Table may not exist yet in testing

    def _lookup_from_db(self, anon_id: str) -> Optional[str]:
        """Look up a real patient_id from the SQL database by anon_id."""
        try:
            from database.sql_db import get_sql_db
            from infrastructure.repositories.sql_repositories import _to_placeholder

            db = get_sql_db()
            conn = db.get_connection()
            cursor = conn.cursor()
            try:
                sql = _to_placeholder(
                    "SELECT patient_id FROM patient_pseudonyms WHERE anon_id = ?"
                )
                cursor.execute(sql, (anon_id,))
                row = cursor.fetchone()
                if row:
                    return row[0] if isinstance(row, (list, tuple)) else row["patient_id"]
                return None
            finally:
                cursor.close()
                conn.close()
        except Exception:
            return None

    def _load_mappings_from_db(self) -> None:
        """Load all existing mappings from DB into memory cache."""
        try:
            from database.sql_db import get_sql_db

            db = get_sql_db()
            conn = db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT patient_id, anon_id FROM patient_pseudonyms")
                for row in cursor.fetchall():
                    if isinstance(row, (list, tuple)):
                        pid, aid = row[0], row[1]
                    else:
                        pid, aid = row["patient_id"], row["anon_id"]
                    self._mapping.register_mapping(pid, aid)
            finally:
                cursor.close()
                conn.close()
        except Exception:
            pass  # Table may not exist yet


# ── Singleton ───────────────────────────────────────────────

_service_instance: Optional[PseudonymizationService] = None


def get_pseudonymization_service() -> PseudonymizationService:
    """Return the global PseudonymizationService singleton."""
    global _service_instance
    if _service_instance is None:
        _service_instance = PseudonymizationService()
    return _service_instance


def reset_pseudonymization_service() -> None:
    """Reset the singleton (for testing)."""
    global _service_instance
    _service_instance = None


def project_name_for(patient_id: str) -> str:
    """
    Canonical chain-store namespace for a patient.

    The clinical store is keyed by the deterministic pseudonym, never the raw
    patient identifier, so a breach of the block store (or a stolen `projects/`
    backup) reveals only opaque anon ids. This is the single source of truth for
    the mapping — every read and write path must derive the project name here so
    they cannot drift apart.
    """
    return f"patient_{get_pseudonymization_service().anon_id(patient_id)}"
