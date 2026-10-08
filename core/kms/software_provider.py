"""
core/kms/software_provider.py — Software KMS Provider
=======================================================
Local key management using Argon2id + PBKDF2 key derivation and
AES-256-GCM authenticated encryption.  No external cloud service
required — suitable for development, standalone deployments, and
air-gapped environments.

Device identification uses a cross-platform strategy:
  • Linux/Docker : /etc/machine-id  or  /sys/class/dmi/id/product_uuid
  • Windows      : MAC + hostname + platform.node()
  • Cloud/CI     : secure random token persisted to .device_fingerprint
  • All platforms: SHA-256 of combined identifiers

This replaces the previous WMI-dependent implementation that silently
fell back to a weak fingerprint on non-Windows platforms.
"""

import os
import hashlib
import base64
import logging
import uuid
import socket
import platform
from typing import Optional, Tuple

logger = logging.getLogger("vhv.kms")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.kms.provider import KMSProvider

# ── constants ───────────────────────────────────────────────
PBKDF2_ITERATIONS = 600_000          # OWASP 2024 recommendation
# Both files default to the working directory. A container sets these to a
# volume path, otherwise a rebuilt container mints a new key (orphaning every
# stored record) and a new device id (breaking notarization anchor checks).
_DEVICE_FINGERPRINT_FILE = os.getenv("VHV_DEVICE_FINGERPRINT_FILE", ".device_fingerprint")
_PRIVATE_KEY_ENV = "HEALTH_BLOCKCHAIN_KEY"
_PRIVATE_KEY_FILE = os.getenv("VHV_KEY_FILE", ".private_key")
_KEYRING_SERVICE = "VIPHealthVault"
_KEYRING_KEY_NAME = "private_key"
# Opt-in that lets a production boot mint a brand-new signing key on first run.
# Without it, production refuses to auto-generate — a silently generated key would
# either land in a plaintext file next to the data or orphan every record already
# encrypted under the previous (now lost) key.
_ALLOW_GENERATED_KEY_ENV = "VHV_ALLOW_GENERATED_KEY"


def _is_production() -> bool:
    """Production unless explicitly in development, demo, or a test run."""
    env = os.getenv("ENVIRONMENT", "production").strip().lower()
    demo = os.getenv("VHV_DEMO_MODE", "false").strip().lower() == "true"
    testing = os.getenv("TESTING", "false").strip().lower() == "true"
    return env != "development" and not demo and not testing


class SoftwareKMSProvider(KMSProvider):
    """
    Software-only KMS using PBKDF2 key derivation and AES-256-GCM.
    Cross-platform device fingerprinting (no WMI dependency).
    """

    def __init__(self, iterations: int = PBKDF2_ITERATIONS):
        self._iterations = iterations
        self._device_id_cache: Optional[str] = None
        self._signing_key_cache: Optional[bytes] = None

    # ── KMSProvider interface ───────────────────────────────

    def derive_key(
        self,
        password: str,
        salt: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> Tuple[bytes, bytes]:
        if salt is None:
            salt = os.urandom(32)

        binding = context or self.get_device_id()
        key_material = (binding + password).encode("utf-8")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._iterations,
        )
        return kdf.derive(key_material), salt

    def encrypt(
        self,
        plaintext: str,
        password: str,
        salt: Optional[bytes] = None,
    ) -> Tuple[str, bytes]:
        raw_key, used_salt = self.derive_key(password, salt)
        return self.encrypt_with_key(plaintext, raw_key), used_salt

    def decrypt(
        self,
        ciphertext_b64: str,
        password: str,
        salt: bytes,
    ) -> str:
        raw_key, _ = self.derive_key(password, salt)
        return self.decrypt_with_key(ciphertext_b64, raw_key)

    def get_device_id(self) -> str:
        if self._device_id_cache is not None:
            return self._device_id_cache

        # 1. Check cached fingerprint file
        fp = self._read_cached_fingerprint()
        if fp:
            self._device_id_cache = fp
            return fp

        # 2. Compute cross-platform hardware fingerprint
        fp = self._compute_fingerprint()
        self._persist_fingerprint(fp)
        self._device_id_cache = fp
        return fp

    def get_signing_key(self) -> bytes:
        if self._signing_key_cache is not None:
            return self._signing_key_cache

        key = self._load_signing_key()
        self._signing_key_cache = key
        return key

    # ── Cross-Platform Device Fingerprint ───────────────────

    @staticmethod
    def _compute_fingerprint() -> str:
        """
        Compute a deterministic device fingerprint without WMI.
        Uses platform-appropriate identifiers:
          - Linux:   /etc/machine-id (systemd) or /sys/class/dmi/id/product_uuid
          - Windows: MAC + hostname + platform.node()
          - Docker:  /etc/machine-id (usually set by container runtime)
          - Fallback: MAC address hash
        """
        try:
            mac = str(uuid.getnode())
            hostname = socket.gethostname()
            proc = platform.processor() or "unknown"
            machine_id = "N/A"

            # Linux / Docker machine-id files
            for path in (
                "/etc/machine-id",
                "/sys/class/dmi/id/product_uuid",
                "/var/lib/dbus/machine-id",
            ):
                try:
                    if os.path.exists(path):
                        with open(path, "r") as f:
                            val = f.read().strip()
                        if val:
                            machine_id = val
                            break
                except (PermissionError, OSError):
                    continue

            # Windows: use platform.node() which returns the hostname
            # (WMI BIOS UUID is no longer used — unreliable cross-platform)
            if machine_id == "N/A" and platform.system() == "Windows":
                machine_id = platform.node()

            raw = f"{mac}::{hostname}::{machine_id}::{proc}"
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()
        except Exception:
            return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()

    @staticmethod
    def _read_cached_fingerprint() -> Optional[str]:
        # Legacy migration: .device_id → .device_fingerprint
        if os.path.exists(".device_id") and not os.path.exists(_DEVICE_FINGERPRINT_FILE):
            try:
                with open(".device_id", "r") as f:
                    old = f.read().strip()
                with open(_DEVICE_FINGERPRINT_FILE, "w") as f:
                    f.write(old)
            except Exception:
                pass

        if os.path.exists(_DEVICE_FINGERPRINT_FILE):
            try:
                with open(_DEVICE_FINGERPRINT_FILE, "r") as f:
                    cached = f.read().strip()
                if cached:
                    return cached
            except Exception:
                pass
        return None

    @staticmethod
    def _persist_fingerprint(fp: str) -> None:
        try:
            with open(_DEVICE_FINGERPRINT_FILE, "w") as f:
                f.write(fp)
        except Exception:
            pass

    # ── Signing Key Management ──────────────────────────────

    @staticmethod
    def _load_signing_key() -> bytes:
        """
        Load or generate the HMAC signing key.

        This key both signs every block AND derives the at-rest encryption key
        (``core.security.derive_rest_secret``), so losing it makes every encrypted
        record permanently unrecoverable. Source priority:

          1. HEALTH_BLOCKCHAIN_KEY env var          (recommended for production)
          2. OS keyring (DPAPI / Keychain / libsecret)
          3. .private_key file (legacy — auto-migrates to keyring)
          4. Generate a new random key             (blocked in production unless
             VHV_ALLOW_GENERATED_KEY=true)

        Back up whatever source you use; see docs/KEY_MANAGEMENT.md.
        """
        production = _is_production()

        # 1. Env var — the recommended production source.
        key_str = os.environ.get(_PRIVATE_KEY_ENV)
        if key_str:
            return key_str.encode() if isinstance(key_str, str) else key_str

        # 2. OS keyring.
        try:
            import keyring as _keyring
            key_str = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)
            if key_str:
                os.environ[_PRIVATE_KEY_ENV] = key_str
                return key_str.encode()
        except Exception:
            pass

        # 3. Legacy plaintext file — try to migrate it into the keyring.
        if os.path.exists(_PRIVATE_KEY_FILE):
            with open(_PRIVATE_KEY_FILE, "r") as f:
                key_str = f.read().strip()
            if key_str:
                migrated = False
                try:
                    import keyring as _keyring
                    _keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, key_str)
                    os.remove(_PRIVATE_KEY_FILE)
                    migrated = True
                except Exception:
                    pass
                if not migrated and production:
                    logger.warning(
                        "Signing key is stored in the plaintext file %s, on the same "
                        "disk as the encrypted chain store. A stolen backup then holds "
                        "both the ciphertext and its key. Move it to HEALTH_BLOCKCHAIN_KEY "
                        "or an OS keyring (pip install keyring). See docs/KEY_MANAGEMENT.md.",
                        _PRIVATE_KEY_FILE,
                    )
                os.environ[_PRIVATE_KEY_ENV] = key_str
                return key_str.encode()

        # 4. Generate a new key. In production this is refused unless explicitly
        #    opted in: a silently minted key would orphan any data already
        #    encrypted under a previous key, or land in plaintext on disk.
        if production and os.getenv(_ALLOW_GENERATED_KEY_ENV, "false").lower() != "true":
            raise RuntimeError(
                "No signing key is configured. Refusing to auto-generate one in "
                "production: it would orphan any existing encrypted records and may be "
                "written to disk in plaintext. Set HEALTH_BLOCKCHAIN_KEY (or provision "
                "the OS keyring), or set VHV_ALLOW_GENERATED_KEY=true for a first-run "
                "install with no data yet. See docs/KEY_MANAGEMENT.md."
            )

        key_str = base64.urlsafe_b64encode(os.urandom(32)).decode()
        stored_in_keyring = False
        try:
            import keyring as _keyring
            _keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, key_str)
            stored_in_keyring = True
        except Exception:
            with open(_PRIVATE_KEY_FILE, "w") as f:
                f.write(key_str)

        if stored_in_keyring:
            logger.info("Generated a new signing key and stored it in the OS keyring.")
        else:
            logger.warning(
                "Generated a new signing key and wrote it to the plaintext file %s "
                "(OS keyring unavailable — pip install keyring). Back this file up and "
                "keep it off any shared backup of the chain store. See docs/KEY_MANAGEMENT.md.",
                _PRIVATE_KEY_FILE,
            )

        os.environ[_PRIVATE_KEY_ENV] = key_str
        return key_str.encode()
