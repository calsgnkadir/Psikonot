"""
scripts/backup_format.py — the encrypted backup archive, shared by backup and restore
====================================================================================
A backup is a folder with two files:

    backup.json   when it was taken, the app version, the SHA-256 and size of the
                  archive, how it is encrypted, and record counts. No personal data.
    data.enc      a tar archive, encrypted. Inside: manifest.json (every file with
                  its SHA-256 and size, counts, key check values, erasure-ledger
                  position), sql/vault.db and projects/<store>/chaindata.lmdb/data.mdb.

The archive is encrypted because the SQLite file holds client names, phone
numbers, e-mail addresses, appointments, staff password hashes and TOTP secrets
in the clear. Encryption: AES-256-GCM in 1 MiB chunks. Each chunk has its own
nonce (random 8-byte prefix + chunk counter) and authenticates the header, its
number and whether it is the last chunk, so a changed, reordered, dropped or cut
off chunk is refused. The key comes from a passphrase through scrypt
(n=2^17, r=8, p=1, random salt), or from a 32-byte key file through HKDF-SHA256
with a random salt.

This module opens no database, so the restore script can use it first.
"""

import base64
import getpass
import hashlib
import json
import os
import re
import tarfile
from typing import Optional, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = 1
MAGIC = b"PSKNBAK1"
CHUNK = 1 << 20
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
MAX_SCRYPT_N = 2 ** 20
MIN_PASSPHRASE = 12
ARCHIVE = "data.enc"
OUTER = "backup.json"
MANIFEST = "manifest.json"
KCV_CONTEXT = "psikonot-backup-key-check-v1"

PASSPHRASE_ENV = "PSIKONOT_BACKUP_PASSPHRASE"
KEY_FILE_ENV = "PSIKONOT_BACKUP_KEY_FILE"

_SAFE_NAME = re.compile(r"[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*")


class BackupError(Exception):
    """The backup cannot be used: damaged, changed, wrong secret, wrong keys."""


# ── the secret that protects the archive ─────────────────────────────────

def resolve_secret(passphrase_file: Optional[str] = None,
                   key_file: Optional[str] = None) -> Tuple[str, bytes]:
    """("key-file", 32 bytes) or ("passphrase", utf-8 bytes), from the command
    line, then the environment, then a prompt."""
    key_file = key_file or os.getenv(KEY_FILE_ENV)
    if key_file:
        with open(key_file, "rb") as f:
            raw = f.read().strip()
        try:
            key = base64.b64decode(raw, validate=True)
        except ValueError:
            key = b""
        if len(key) != 32:
            raise BackupError(f"{key_file} must hold 32 random bytes, base64-encoded")
        return "key-file", key
    if passphrase_file:
        with open(passphrase_file, "r", encoding="utf-8") as f:
            phrase = f.read().strip()
    else:
        phrase = os.getenv(PASSPHRASE_ENV, "")
        if not phrase and os.isatty(0):
            phrase = getpass.getpass("Backup passphrase: ")
    if not phrase:
        raise BackupError(
            f"No backup passphrase. Set {PASSPHRASE_ENV}, pass --passphrase-file, "
            f"or use a key file (--key-file / {KEY_FILE_ENV}).")
    if len(phrase) < MIN_PASSPHRASE:
        raise BackupError(f"The backup passphrase must be at least {MIN_PASSPHRASE} characters")
    return "passphrase", phrase.encode("utf-8")


def _derive(kind: str, secret: bytes, params: dict) -> bytes:
    salt = base64.b64decode(params["salt"])
    if params["kdf"] == "scrypt":
        if kind != "passphrase":
            raise BackupError("This backup is protected by a passphrase, not a key file")
        n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
        if not (2 <= n <= MAX_SCRYPT_N and 1 <= r <= 16 and 1 <= p <= 4):
            raise BackupError("Unsupported key-derivation parameters")
        return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(secret)
    if params["kdf"] == "hkdf-key-file":
        if kind != "key-file":
            raise BackupError("This backup is protected by a key file, not a passphrase")
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                    info=b"psikonot-backup-v1").derive(secret)
    raise BackupError(f"Unknown key derivation {params['kdf']!r}")


def new_header(kind: str) -> dict:
    salt = base64.b64encode(os.urandom(16)).decode()
    if kind == "passphrase":
        kdf = {"kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt}
    else:
        kdf = {"kdf": "hkdf-key-file", "salt": salt}
    return {"format": FORMAT, "cipher": "AES-256-GCM", "chunk": CHUNK,
            "nonce_prefix": base64.b64encode(os.urandom(8)).decode(), **kdf}


def describe(header: dict) -> str:
    if header["kdf"] == "scrypt":
        return (f"AES-256-GCM in {header['chunk']}-byte chunks; key from a passphrase via "
                f"scrypt(n={header['n']}, r={header['r']}, p={header['p']})")
    return f"AES-256-GCM in {header['chunk']}-byte chunks; key from a key file via HKDF-SHA256"


# ── chunked AES-GCM stream ───────────────────────────────────────────────

def _aad(header_digest: bytes, counter: int, final: bool) -> bytes:
    return header_digest + counter.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


class EncryptingWriter:
    """A write-only file object for tarfile's stream mode."""

    def __init__(self, f, kind: str, secret: bytes, header: dict):
        self._f = f
        header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
        head = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes
        f.write(head)
        self._digest = hashlib.sha256(head).digest()
        self._aes = AESGCM(_derive(kind, secret, header))
        self._prefix = base64.b64decode(header["nonce_prefix"])
        self._counter = 0
        self._buf = bytearray()

    def write(self, data) -> int:
        self._buf += data
        while len(self._buf) > CHUNK:
            self._emit(bytes(self._buf[:CHUNK]), final=False)
            del self._buf[:CHUNK]
        return len(data)

    def _emit(self, chunk: bytes, final: bool) -> None:
        nonce = self._prefix + self._counter.to_bytes(4, "big")
        ct = self._aes.encrypt(nonce, chunk, _aad(self._digest, self._counter, final))
        self._f.write((b"\x01" if final else b"\x00") + len(ct).to_bytes(4, "big") + ct)
        self._counter += 1

    def finish(self) -> None:
        self._emit(bytes(self._buf), final=True)
        self._buf = bytearray()


class DecryptingReader:
    """A read-only file object: hands out only chunks that authenticated."""

    def __init__(self, f, kind: str, secret: bytes):
        self._f = f
        magic = f.read(len(MAGIC))
        if magic != MAGIC:
            raise BackupError("Not a PsikoNot backup archive")
        size_bytes = f.read(4)
        size = int.from_bytes(size_bytes, "big")
        if size > 4096:
            raise BackupError("Damaged archive header")
        header_bytes = f.read(size)
        try:
            self.header = json.loads(header_bytes)
        except ValueError:
            raise BackupError("Damaged archive header")
        if self.header.get("format") != FORMAT:
            raise BackupError("Unsupported backup format")
        self._digest = hashlib.sha256(magic + size_bytes + header_bytes).digest()
        self._aes = AESGCM(_derive(kind, secret, self.header))
        self._prefix = base64.b64decode(self.header["nonce_prefix"])
        self._counter = 0
        self._buf = bytearray()
        self._done = False

    def _next(self) -> None:
        flag = self._f.read(1)
        if not flag:
            raise BackupError("The archive is cut short")
        if flag not in (b"\x00", b"\x01"):
            raise BackupError("Damaged archive")
        final = flag == b"\x01"
        length = int.from_bytes(self._f.read(4), "big")
        if length > CHUNK + 16:
            raise BackupError("Damaged archive")
        ct = self._f.read(length)
        nonce = self._prefix + self._counter.to_bytes(4, "big")
        try:
            self._buf += self._aes.decrypt(nonce, ct, _aad(self._digest, self._counter, final))
        except InvalidTag:
            raise BackupError("The archive does not decrypt: wrong passphrase or key file, "
                              "or the archive was changed")
        self._counter += 1
        if final:
            if self._f.read(1):
                raise BackupError("Unexpected data after the end of the archive")
            self._done = True

    def read(self, n: int = -1) -> bytes:
        while not self._done and (n < 0 or len(self._buf) < n):
            self._next()
        if n < 0:
            n = len(self._buf)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


# ── helpers ──────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def key_check_values() -> dict:
    """Short check values of the signing key and the pseudonym secret, so a
    restore with different keys is refused before it writes anything. They are
    MACs of a fixed string; they do not reveal the keys."""
    from core.kms.registry import get_kms
    from core.pseudonymization.engine import PseudonymizationEngine
    return {
        "signing_key": get_kms().mac(KCV_CONTEXT.encode("utf-8")).hex()[:16],
        "pseudonym_secret": PseudonymizationEngine().generate_anon_id(KCV_CONTEXT)[:16],
    }


def decrypt_archive(backup_dir: str, dest: str, passphrase: Optional[str] = None,
                    key_file: Optional[str] = None, secret: Optional[Tuple[str, bytes]] = None) -> dict:
    """Decrypt and unpack a backup into `dest` (which must not exist yet) and
    check every file against the manifest. Returns the manifest. Raises
    BackupError on any mismatch; `dest` may then hold partial files."""
    if secret is None:
        secret = ("passphrase", passphrase.encode("utf-8")) if passphrase else resolve_secret(None, key_file)
    try:
        with open(os.path.join(backup_dir, OUTER), "r", encoding="utf-8") as f:
            outer = json.load(f)
    except (OSError, ValueError):
        raise BackupError(f"{backup_dir} has no readable {OUTER}")
    archive = os.path.join(backup_dir, ARCHIVE)
    if not os.path.isfile(archive) or sha256_file(archive) != outer.get("archive_sha256"):
        raise BackupError(f"{ARCHIVE} does not match the SHA-256 in {OUTER}: the archive was changed")

    os.makedirs(dest)
    seen = {}
    with open(archive, "rb") as f:
        reader = DecryptingReader(f, *secret)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                name = member.name
                if not _SAFE_NAME.fullmatch(name) or ".." in name.split("/"):
                    raise BackupError(f"Unsafe path in archive: {name!r}")
                target = os.path.join(dest, *name.split("/"))
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                if not member.isfile():
                    raise BackupError(f"Unexpected entry in archive: {name!r}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                digest = hashlib.sha256()
                src = tar.extractfile(member)
                with open(target, "wb") as out:
                    for block in iter(lambda: src.read(1 << 20), b""):
                        digest.update(block)
                        out.write(block)
                seen[name] = {"sha256": digest.hexdigest(), "size": member.size}

    if MANIFEST not in seen:
        raise BackupError("The archive has no manifest")
    with open(os.path.join(dest, MANIFEST), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    del seen[MANIFEST]
    if seen != manifest.get("files"):
        missing = set(manifest.get("files", {})) - set(seen)
        extra = set(seen) - set(manifest.get("files", {}))
        changed = [n for n in seen if n in manifest.get("files", {}) and seen[n] != manifest["files"][n]]
        raise BackupError(f"The archive does not match its manifest (missing {sorted(missing)}, "
                          f"extra {sorted(extra)}, changed {sorted(changed)})")
    return manifest
