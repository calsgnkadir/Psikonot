"""
core/pseudonymization/engine.py — Pseudonymization Engine
===========================================================
Cryptographic identity decoupling layer that separates a client's PII
(Personally Identifiable Information) from clinical content in storage.

Architecture:
  ┌─────────────────┐     ┌──────────────────────┐
  │  Identity Store  │     │  Clinical Data Store  │
  │  (PII isolated)  │     │  (uses anon_id only)  │
  │                  │     │                       │
  │  patient_id ─────┼──→  │  anon_id (opaque)     │
  │  full_name       │     │  encrypted records    │
  │  clearance       │     │  blockchain blocks    │
  └─────────────────┘     └──────────────────────┘

If the clinical data store is breached, the attacker sees only
opaque anonymous identifiers — no way to link records back to
a real person without the identity store + pseudonymization key.

Key properties:
  1. Deterministic: same (patient_id, salt) always produces the same anon_id
  2. One-way: cannot reverse anon_id → patient_id without the secret key
  3. Collision-resistant: SHA-256 based, effectively zero collision probability
  4. Key-dependent: changing the pseudonymization secret invalidates all mappings
"""

import os
import hashlib
import hmac
import secrets
from typing import Optional, Dict


# Development-only default. In production PSEUDONYM_SECRET must be set — otherwise
# anon_id = HMAC(default, patient_id) would be computable by anyone with the code,
# defeating the pseudonymisation (and the erasure/decoupling story built on it).
_DEFAULT_SECRET = "VHV_PSEUDONYM_SECRET_CHANGE_IN_PRODUCTION"


def _is_production() -> bool:
    """Production unless explicitly in development, demo, or a test run."""
    env = os.getenv("ENVIRONMENT", "production").strip().lower()
    demo = os.getenv("VHV_DEMO_MODE", "false").strip().lower() == "true"
    testing = os.getenv("TESTING", "false").strip().lower() == "true"
    return env != "development" and not demo and not testing


class PseudonymizationEngine:
    """
    Core engine for generating and managing cryptographic pseudonyms.

    A pseudonym (anon_id) is an HMAC-SHA256 digest of the patient_id
    keyed with a server-side secret.  This means:
      • The same patient always gets the same anon_id (deterministic lookup)
      • An attacker with only the anon_id cannot derive the patient_id
      • Changing the secret key regenerates all pseudonyms (key rotation)
    """

    def __init__(self, secret: Optional[str] = None):
        resolved = secret or os.environ.get("PSEUDONYM_SECRET")
        if not resolved:
            if _is_production():
                raise RuntimeError(
                    "PSEUDONYM_SECRET is not set. Refusing to fall back to the built-in "
                    "default in production: pseudonyms would be derivable by anyone with "
                    "the code. Set PSEUDONYM_SECRET to a long random value."
                )
            resolved = _DEFAULT_SECRET
        self._secret = resolved.encode("utf-8")

    def generate_anon_id(self, patient_id: str) -> str:
        """
        Generate a deterministic anonymous identifier for a patient.

        Args:
            patient_id: Real patient identifier (e.g. "CL-001")

        Returns:
            64-character hex string (HMAC-SHA256 digest)
        """
        return hmac.new(
            self._secret,
            patient_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def generate_anon_id_with_prefix(self, patient_id: str) -> str:
        """
        Generate an anon_id with a human-readable prefix for debugging.
        Format: "ANON-<first 16 hex chars>"
        """
        full_hash = self.generate_anon_id(patient_id)
        return f"ANON-{full_hash[:16].upper()}"

    def verify_mapping(self, patient_id: str, anon_id: str) -> bool:
        """
        Verify that a given anon_id matches the expected pseudonym
        for a patient_id.
        """
        expected = self.generate_anon_id(patient_id)
        return hmac.compare_digest(expected, anon_id)

    def generate_session_pseudonym(self) -> str:
        """
        Generate a one-time random pseudonym for ephemeral/session use.
        Not linked to any patient — used for temporary audit entries.
        """
        return f"EPHEMERAL-{secrets.token_hex(16)}"

    def hash_field(self, value: str) -> str:
        """
        One-way hash a single PII field (e.g. full_name, phone).
        Uses HMAC-SHA256 with the same secret for consistency.
        """
        return hmac.new(
            self._secret,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


class PseudonymMapping:
    """
    In-memory + persistent mapping between real patient IDs and anon IDs.
    Uses the SQL database for persistence.
    """

    def __init__(self, engine: PseudonymizationEngine):
        self.engine = engine
        self._cache: Dict[str, str] = {}   # patient_id → anon_id
        self._reverse: Dict[str, str] = {} # anon_id → patient_id

    def get_or_create_anon_id(self, patient_id: str) -> str:
        """
        Get the anonymous ID for a patient, creating the mapping if needed.
        """
        if patient_id in self._cache:
            return self._cache[patient_id]

        anon_id = self.engine.generate_anon_id(patient_id)
        self._cache[patient_id] = anon_id
        self._reverse[anon_id] = patient_id
        return anon_id

    def resolve_patient_id(self, anon_id: str) -> Optional[str]:
        """
        Reverse-lookup: given an anon_id, return the real patient_id.
        Only works if the mapping was previously created in this instance
        or loaded from the database.
        """
        return self._reverse.get(anon_id)

    def register_mapping(self, patient_id: str, anon_id: str) -> None:
        """
        Explicitly register a known mapping (e.g. loaded from database).
        """
        self._cache[patient_id] = anon_id
        self._reverse[anon_id] = patient_id

    def remove(self, patient_id: str) -> bool:
        """Drop one patient's mapping in both directions. True if it was held."""
        anon_id = self._cache.pop(patient_id, None)
        if anon_id is not None:
            self._reverse.pop(anon_id, None)
        return anon_id is not None

    def get_all_mappings(self) -> Dict[str, str]:
        """Return all known mappings (patient_id → anon_id)."""
        return dict(self._cache)

    def clear_cache(self) -> None:
        """Clear the in-memory cache (for testing / key rotation)."""
        self._cache.clear()
        self._reverse.clear()
