from typing import Dict, Optional, Tuple
from core.ports.cryptography import IEncryptionStrategy
from core.security import get_kms


class AESGCMStrategy(IEncryptionStrategy):
    """
    AES-256-GCM under a key derived from (password, salt) with PBKDF2.

    Every block of one client's file is encrypted with the same secret and the
    same per-client salt, so it has the same key. Deriving it is slow on
    purpose (600,000 PBKDF2 iterations), and it used to be done again for every
    block, so opening a file took longer with every record in it. Each derived
    key is now kept for the lifetime of this object, so a request derives it once.

    One object lives for one request (backend/dependencies.py creates a new one
    each time), so a key never outlives the request that needed it — after a
    client is erased, no derived key of theirs is left in memory.
    """

    def __init__(self):
        self._keys: Dict[Tuple[str, bytes], bytes] = {}

    def _key(self, password: str, salt: bytes) -> bytes:
        cache_key = (password, bytes(salt))
        if cache_key not in self._keys:
            self._keys[cache_key], _ = get_kms().derive_key(password, salt)
        return self._keys[cache_key]

    def encrypt_data(self, data: str, password: str, salt: Optional[bytes] = None) -> Tuple[str, bytes]:
        if salt is None:
            # A fresh random salt means a new key; nothing to reuse.
            return get_kms().encrypt(data, password, None)
        return get_kms().encrypt_with_key(data, self._key(password, salt)), salt

    def decrypt_data(self, encrypted_data: str, password: str, salt: bytes) -> str:
        return get_kms().decrypt_with_key(encrypted_data, self._key(password, salt))
