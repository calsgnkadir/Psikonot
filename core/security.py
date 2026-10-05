"""
core/security.py — PsikoNot · Security Layer v4.0 (KMS-Backed)
========================================================================
All cryptographic operations now delegate to the pluggable KMS provider
(see core/kms/).  This file retains the original function signatures so
that every existing ``from core.security import ...`` continues to work
without modification.

Layers:
  1. Device fingerprint   → KMSProvider.get_device_id()
  2. Argon2id password hash  (unchanged — local auth concern, not KMS)
  3. PBKDF2 key derivation → KMSProvider.derive_key()
  4. AES-256-GCM encrypt   → KMSProvider.encrypt()
  5. AES-256-GCM decrypt   → KMSProvider.decrypt()
  6. HMAC-SHA256 signing   → KMSProvider.mac()  (key never leaves the provider)
  7. Password policy       (unchanged)
"""

import os
import re
import hmac
import hashlib
import base64
from typing import Optional, Tuple

from core.kms.registry import get_kms

# ──────────────────────────────────────────────
# Argon2 — preferred password hashing
# ──────────────────────────────────────────────
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _ARGON2_AVAILABLE = True
    _ph = PasswordHasher(
        time_cost=3,
        memory_cost=65536,   # 64 MB
        parallelism=2,
        hash_len=32,
        salt_len=16,
    )
except ImportError:
    _ARGON2_AVAILABLE = False

# Bcrypt — fallback
try:
    import bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False

# ──────────────────────────────────────────────
# CONSTANTS (retained for backward compat)
# ──────────────────────────────────────────────
PBKDF2_ITERATIONS = 600_000


# ══════════════════════════════════════════════
# 1. DEVICE FINGERPRINT  (delegates to KMS)
# ══════════════════════════════════════════════

def get_device_id() -> str:
    """Return the device fingerprint via the active KMS provider."""
    return get_kms().get_device_id()


def get_current_device_id() -> str:
    """Alias — same as get_device_id()."""
    return get_device_id()


def verify_device_access(stored_device_id: str) -> bool:
    """Check whether this environment matches the stored device id."""
    return get_kms().verify_device(stored_device_id)


# ══════════════════════════════════════════════
# 2. PRIVATE KEY MANAGEMENT  (delegates to KMS)
# ══════════════════════════════════════════════

def get_private_key() -> bytes:
    """Return the HMAC signing key via the active KMS provider."""
    return get_kms().get_signing_key()


# ══════════════════════════════════════════════
# 3. PASSWORD POLICY
# ══════════════════════════════════════════════

class PasswordPolicyError(ValueError):
    """Password policy violation."""
    pass


def validate_password(password: str) -> Tuple[bool, str]:
    """
    Validates the password policy rules.
    Returns: (is_valid, error_message)
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least 1 uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least 1 lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least 1 digit."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        return False, "Password must contain at least 1 special character (!@#$%^&* etc.)."
    return True, ""


# ══════════════════════════════════════════════
# 4. ARGON2 / BCRYPT PASSWORD HASH
# ══════════════════════════════════════════════

def hash_password(password: str) -> str:
    """
    Hashes password with Argon2id (bcrypt fallback).
    Used for storage (vault password, user password).
    """
    if _ARGON2_AVAILABLE:
        return _ph.hash(password)
    elif _BCRYPT_AVAILABLE:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode(), salt).decode()
    else:
        # Fallback — PBKDF2 + random salt
        salt = os.urandom(32)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
        return base64.urlsafe_b64encode(salt + dk).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a hashed password."""
    try:
        if _ARGON2_AVAILABLE and hashed.startswith("$argon2"):
            try:
                return _ph.verify(hashed, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False
        elif _BCRYPT_AVAILABLE and hashed.startswith("$2b$"):
            return bcrypt.checkpw(password.encode(), hashed.encode())
        else:
            # PBKDF2 fallback
            raw = base64.urlsafe_b64decode(hashed.encode())
            salt, dk_stored = raw[:32], raw[32:]
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
            return hmac.compare_digest(dk, dk_stored)
    except Exception:
        return False


# ══════════════════════════════════════════════
# 5. PBKDF2 ENCRYPTION KEY  (delegates to KMS)
# ══════════════════════════════════════════════

def get_encryption_key(password: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Derives AES 256-bit key via the active KMS provider.
    Returns: (raw_key_32_bytes, salt)
    """
    return get_kms().derive_key(password, salt)


# ══════════════════════════════════════════════
# 6. AES-256-GCM ENCRYPTION / DECRYPTION  (delegates to KMS)
# ══════════════════════════════════════════════

def encrypt_data(data: str, password: str, salt: Optional[bytes] = None) -> Tuple[str, bytes]:
    """
    Encrypts data with AES-256-GCM via the active KMS provider.
    Returns: (encrypted_data_base64, used_salt)
    """
    return get_kms().encrypt(data, password, salt)


def decrypt_data(encrypted_data: str, password: str, salt: bytes) -> str:
    """
    Decrypts AES-256-GCM encrypted data via the active KMS provider.
    """
    return get_kms().decrypt(encrypted_data, password, salt)


def derive_rest_secret(context: str) -> str:
    """
    Server-held secret for at-rest record encryption.

    Bound to the KMS signing key and a context (the patient id), so every
    patient chain is encrypted under a distinct key. The signing key lives
    outside the chain store — an environment variable or the OS keyring in
    production — so a stolen backup of the ``projects/`` directory alone cannot
    be decrypted. The server holds the key and decrypts for authorized sessions;
    this protects data at rest, not against a fully compromised running server.
    """
    # A MAC over the context under the signing key. Going through the provider's
    # mac() means an HSM/Vault-backed key never has to enter this process.
    return get_kms().mac(f"rest-v1:{context}".encode("utf-8")).hex()


# ══════════════════════════════════════════════
# 7. HMAC-SHA256 SIGNATURE  (delegates to KMS)
# ══════════════════════════════════════════════

def signaturedata(message: str, device_id: str = None) -> str:
    """
    Generates HMAC-SHA256 signature.
    Key = combination of signing_key + device_id.
    """
    if device_id is None:
        device_id = get_device_id()

    # The device-binding step is the only one that touches the signing key, so it
    # runs inside the provider (mac); the second HMAC uses that derived, scoped key
    # and can stay local. An HSM/Vault key therefore never leaves its boundary.
    combined_key = get_kms().mac(device_id.encode("utf-8"))

    return hmac.new(
        combined_key,
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_message(message: str, signature: str, device_id: str = None) -> bool:
    """Verifies HMAC signature (timing-safe comparison)."""
    if device_id is None:
        device_id = get_device_id()

    combined_key = get_kms().mac(device_id.encode("utf-8"))

    expected = hmac.new(
        combined_key,
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ══════════════════════════════════════════════
# 8. SIMPLE HASH (Block Password Wrappers)
# ══════════════════════════════════════════════

def hash_block_password(password: str) -> str:
    """Hashes block access password (using Argon2 or fallback)."""
    return hash_password(password)


def verify_block_password(password: str, stored_hash: str) -> bool:
    """Verifies block access password."""
    return verify_password(password, stored_hash)
