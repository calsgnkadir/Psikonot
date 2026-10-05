"""
core/kms/provider.py — Abstract Key Management Service Interface
==================================================================
Defines the contract for all KMS providers.  Every cryptographic key
operation in the vault flows through this interface so the underlying
backend can be swapped without touching business logic.

Supported backends (via concrete subclasses):
  • SoftwareKMSProvider  — local Argon2id + PBKDF2 derivation (dev / standalone)
  • CloudKMSProvider     — AWS KMS / Azure Key Vault / HashiCorp Vault (production)
"""

import base64
import hashlib
import hmac
import os
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KMSProvider(ABC):
    """
    Abstract base class for Key Management Service providers.

    Every concrete provider must implement:
      • derive_key     — deterministic key derivation from password + context
      • encrypt        — authenticated encryption (AES-256-GCM)
      • decrypt        — authenticated decryption
      • get_device_id  — stable, cross-platform device/environment identifier
      • get_signing_key — key material for HMAC block signatures
    """

    @abstractmethod
    def derive_key(
        self,
        password: str,
        salt: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> Tuple[bytes, bytes]:
        """
        Derive a 256-bit symmetric key from a password.

        Args:
            password: user-supplied passphrase
            salt:     optional 32-byte salt (generated if None)
            context:  optional binding context (e.g. device_id, patient_id)

        Returns:
            (raw_key_32_bytes, salt_used)
        """
        ...

    @abstractmethod
    def encrypt(
        self,
        plaintext: str,
        password: str,
        salt: Optional[bytes] = None,
    ) -> Tuple[str, bytes]:
        """
        Encrypt plaintext with AES-256-GCM.

        Returns:
            (ciphertext_base64, salt_used)
        """
        ...

    @abstractmethod
    def decrypt(
        self,
        ciphertext_b64: str,
        password: str,
        salt: bytes,
    ) -> str:
        """
        Decrypt AES-256-GCM ciphertext.

        Raises:
            ValueError on authentication failure or corrupted data.
        """
        ...

    @abstractmethod
    def get_device_id(self) -> str:
        """
        Return a stable identifier for the current device / environment.
        Must be deterministic on the same host and cross-platform
        (Windows, Linux, Docker, cloud).
        """
        ...

    def get_signing_key(self) -> bytes:
        """
        Return the raw HMAC signing key material.

        Only software providers can honour this — a provider that holds its key in
        an HSM or a remote KMS (Vault, AWS) must never release the key and instead
        overrides :meth:`mac`. Such providers raise ``NotImplementedError`` here.
        """
        raise NotImplementedError(
            "This KMS provider does not expose raw key material; use mac() instead."
        )

    # ── convenience helpers (non-abstract) ──────────────────────

    def mac(self, message: bytes) -> bytes:
        """
        Compute HMAC-SHA256 of ``message`` under the signing key and return the
        raw digest.

        This is the single key-using primitive the vault needs: block signatures,
        the notarizer anchor and the at-rest key derivation are all expressed as a
        MAC over some context. An HSM- or KMS-backed provider overrides this to
        perform the MAC *inside* the trust boundary (e.g. Vault Transit ``/hmac``),
        so the key never reaches this process. The default computes it locally from
        the raw key, which only a software provider can supply.
        """
        key = self.get_signing_key()
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).digest()

    # AES-256-GCM under a key that is already derived. Kept apart from
    # derive_key() so a caller that needs the same key many times — every block
    # of one client's file uses the same one — derives it once (see
    # infrastructure/cryptography/crypto_strategies.py).

    def encrypt_with_key(self, plaintext: str, raw_key: bytes) -> str:
        """AES-256-GCM with a fresh 96-bit nonce; returns base64(nonce + ciphertext)."""
        nonce = os.urandom(12)
        ciphertext = AESGCM(raw_key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("utf-8")

    def decrypt_with_key(self, ciphertext_b64: str, raw_key: bytes) -> str:
        """Reverse of encrypt_with_key(). Raises ValueError on a wrong key or tampering."""
        try:
            payload = base64.urlsafe_b64decode(ciphertext_b64.encode("utf-8"))
            if len(payload) < 28:  # 12 nonce + 16 auth tag minimum
                raise ValueError("Invalid encrypted payload size")
            return AESGCM(raw_key).decrypt(payload[:12], payload[12:], None).decode("utf-8")
        except Exception as e:
            raise ValueError(f"Decryption error: {e}")

    def verify_device(self, stored_device_id: str) -> bool:
        """Check whether this environment matches a stored device id."""
        return stored_device_id == self.get_device_id()
