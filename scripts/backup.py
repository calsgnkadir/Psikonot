"""
scripts/backup.py — an encrypted snapshot of a running PsikoNot vault
=====================================================================
    python -m scripts.backup --out <dir>        data backup (no keys)
    python -m scripts.backup --keys-out <dir>   key export (no data) — keep it elsewhere

The data backup is safe to take while the app runs:

- every LMDB environment (client chains, attachment store, system and user
  stores) is copied with env.copy(compact=True), which reads it inside one LMDB
  read transaction — a consistent snapshot of that environment;
- the SQLite database is copied with the sqlite3 backup API, not as a file;
- the stores are copied in an order where references only point backwards:
  client chains first, then attachments (written before the record that names
  them), then the system stores, and SQLite last (a client's erasure key is
  created before their first record is written).

The result is one folder, psikonot-backup-<UTC time>, holding an encrypted
archive and a plain backup.json (see scripts/backup_format.py). The archive is
encrypted because SQLite holds client names, phones, e-mails and appointments.
The passphrase comes from PSIKONOT_BACKUP_PASSPHRASE, --passphrase-file, or a
prompt; or use a key file (--key-file / PSIKONOT_BACKUP_KEY_FILE).

The signing key and the pseudonym secret are never in the data backup. Export
them once with --keys-out and keep that file apart from the data backups
(docs/KEY_MANAGEMENT.md, docs/BACKUP_RESTORE.md).

PostgreSQL (VHV_DATABASE_URL) is not handled here: use pg_dump, see
docs/BACKUP_RESTORE.md.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database import paths  # noqa: E402
from scripts import backup_format as fmt  # noqa: E402

_BLOCK_KEY = re.compile(rb"[0-9]{10}")
_SQL_COUNTS = {
    "users": "SELECT COUNT(*) FROM users",
    "clients": "SELECT COUNT(*) FROM clients",
    "appointments": "SELECT COUNT(*) FROM appointments",
    "pseudonym_mappings": "SELECT COUNT(*) FROM patient_pseudonyms",
    "erasure_keys": "SELECT COUNT(*) FROM patient_erasure_keys",
}


def _fail(message: str) -> "SystemExit":
    print(f"ERROR: {message}", file=sys.stderr)
    return SystemExit(1)


def app_version() -> dict:
    version = None
    try:
        with open(os.path.join(_ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            match = re.search(r"^## \[([0-9][^\]]*)\]", f.read(), re.MULTILINE)
            version = match.group(1) if match else None
    except OSError:
        pass
    commit = os.getenv("VHV_GIT_COMMIT")
    git = shutil.which("git")
    if not commit and git:
        try:
            commit = subprocess.run(
                [git, "rev-parse", "HEAD"], cwd=_ROOT, capture_output=True,
                text=True, timeout=10).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            commit = None
    return {"version": version, "commit": commit}


def lmdb_counts(env_dir: str) -> dict:
    """Blocks, records (not genesis or audit blocks) and attachments in one copied store."""
    import lmdb
    env = lmdb.open(env_dir, readonly=True, lock=False, subdir=True)
    blocks = records = attachments = 0
    try:
        with env.begin() as txn:
            for key, value in txn.cursor():
                if key.startswith(b"attachment_"):
                    attachments += 1
                elif _BLOCK_KEY.fullmatch(key):
                    blocks += 1
                    try:
                        data = json.loads(value).get("data")
                    except (ValueError, AttributeError):
                        data = None
                    if not (isinstance(data, dict) and data.get("type") in ("genesis", "audit")):
                        records += 1
    finally:
        env.close()
    return {"blocks": blocks, "records": records, "attachments": attachments}


def sql_counts(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        counts = {}
        for name, sql in _SQL_COUNTS.items():
            try:
                counts[name] = conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                counts[name] = 0
        return counts
    finally:
        conn.close()


def store_counts(projects_root: str, names) -> dict:
    totals = {"chains": 0, "blocks": 0, "records": 0, "attachments": 0}
    for name in names:
        counted = lmdb_counts(os.path.join(projects_root, name, "chaindata.lmdb"))
        if name.startswith("patient_"):
            totals["chains"] += 1
            totals["blocks"] += counted["blocks"]
            totals["records"] += counted["records"]
        totals["attachments"] += counted["attachments"]
    return totals


def _ordered_stores(projects_dir: str):
    """(stores with an LMDB environment, empty project folders), in copy order."""
    names = sorted(d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d)))
    with_env = [n for n in names if os.path.isfile(os.path.join(projects_dir, n, "chaindata.lmdb", "data.mdb"))]
    empty = [n for n in names if n not in with_env]

    def rank(name):
        if name.startswith("patient_"):
            return 0
        return 1 if name == "attachments" else 2
    return sorted(with_env, key=lambda n: (rank(n), n)), empty


def create_backup(out_dir: str, secret) -> str:
    if os.getenv("VHV_DATABASE_URL"):
        raise _fail("VHV_DATABASE_URL is set (PostgreSQL). This tool backs up SQLite only; "
                    "use pg_dump for the database, see docs/BACKUP_RESTORE.md.")
    sqlite_path, projects_dir = paths.sqlite_path(), paths.projects_dir()
    if not os.path.isfile(sqlite_path):
        raise _fail(f"No SQLite database at {sqlite_path} (VHV_SQLITE_PATH)")
    if not os.path.isdir(projects_dir):
        raise _fail(f"No record store at {projects_dir} (VHV_PROJECTS_DIR)")

    from core.services import erasure_ledger
    ledger_path = paths.erasure_ledger_path()
    erasure_ledger.ensure_exists(ledger_path)
    ledger = erasure_ledger.read_entries(ledger_path)

    from database.storage import default_db_manager

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(out_dir, exist_ok=True)
    folder = os.path.join(out_dir, f"psikonot-backup-{stamp}")
    n = 1
    while os.path.exists(folder):
        n += 1
        folder = os.path.join(out_dir, f"psikonot-backup-{stamp}-{n}")

    staging = tempfile.mkdtemp(prefix="pkb-")
    try:
        stores, empty = _ordered_stores(projects_dir)
        for name in stores:
            dest = os.path.join(staging, "projects", name, "chaindata.lmdb")
            os.makedirs(dest)
            default_db_manager.open_db(name).copy(dest, compact=True)
        sql_dest = os.path.join(staging, "sql", "vault.db")
        os.makedirs(os.path.dirname(sql_dest))
        src = sqlite3.connect(sqlite_path)
        dst = sqlite3.connect(sql_dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        files = {}
        for dirpath, _, names in os.walk(staging):
            for fname in names:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, staging).replace(os.sep, "/")
                files[rel] = {"sha256": fmt.sha256_file(full), "size": os.path.getsize(full)}
        counts = {**sql_counts(sql_dest), **store_counts(os.path.join(staging, "projects"), stores)}
        manifest = {
            "format": fmt.FORMAT,
            "created_at": now.isoformat(timespec="seconds"),
            "app": app_version(),
            "sql": {"engine": "sqlite", "file": "sql/vault.db"},
            "stores": stores,
            "empty_projects": empty,
            "files": files,
            "counts": counts,
            "erasure_ledger": {"count": len(ledger), "head": ledger[-1]["hash"] if ledger else ""},
            "key_check": fmt.key_check_values(),
        }
        with open(os.path.join(staging, fmt.MANIFEST), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)

        os.makedirs(folder)
        header = fmt.new_header(secret[0])
        archive = os.path.join(folder, fmt.ARCHIVE)
        with open(archive + ".partial", "wb") as f:
            writer = fmt.EncryptingWriter(f, secret[0], secret[1], header)
            with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                tar.add(os.path.join(staging, fmt.MANIFEST), arcname=fmt.MANIFEST)
                for rel in sorted(files):
                    tar.add(os.path.join(staging, *rel.split("/")), arcname=rel)
            writer.finish()
            f.flush()
            os.fsync(f.fileno())
        os.replace(archive + ".partial", archive)
        outer = {
            "format": fmt.FORMAT,
            "created_at": manifest["created_at"],
            "app": manifest["app"],
            "archive": fmt.ARCHIVE,
            "archive_sha256": fmt.sha256_file(archive),
            "archive_size": os.path.getsize(archive),
            "encryption": fmt.describe(header),
            "counts": counts,
            "contains_keys": False,
            "note": "Data only. Restore needs HEALTH_BLOCKCHAIN_KEY and PSEUDONYM_SECRET from the "
                    "separate key export, the backup passphrase or key file, and the current "
                    "erasure ledger. See docs/BACKUP_RESTORE.md.",
        }
        with open(os.path.join(folder, fmt.OUTER), "w", encoding="utf-8") as f:
            json.dump(outer, f, indent=2, sort_keys=True)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"Backup written to {folder}")
    print(f"  {counts['clients']} client card(s), {counts['chains']} chain(s), "
          f"{counts['records']} record(s), {counts['attachments']} attachment(s), "
          f"{counts['users']} account(s)")
    print(f"  erasure ledger at backup time: {len(ledger)} entr{'y' if len(ledger) == 1 else 'ies'}")
    print(f"  {outer['encryption']}")
    print("  Keys are NOT in this backup. Copy the folder off this host (docs/BACKUP_RESTORE.md).")
    return folder


def _inside(path: str, parent: str) -> bool:
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def export_keys(keys_out: str) -> str:
    """Write what is needed to read a data backup back: the signing key, the
    pseudonym secret and the device fingerprint. Refuses to write next to the data."""
    for data_dir in (paths.projects_dir(), os.path.dirname(paths.sqlite_path())):
        if _inside(keys_out, data_dir):
            raise _fail(f"{keys_out} is inside the data directory {data_dir}. "
                        "Keys must never be stored with the data or its backups.")

    from core.kms.registry import get_kms
    from core.kms.software_provider import SoftwareKMSProvider
    from core.pseudonymization import engine

    kms = get_kms()
    lines = [
        "# PsikoNot KEY EXPORT — " + datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "#",
        "# Anyone with this file AND a data backup (and its passphrase) can read every record.",
        "# Keep it OFF the host and AWAY from the data backups: a password manager, a sealed",
        "# envelope in a safe, a hardware token. See docs/KEY_MANAGEMENT.md.",
        "# Without it, a data backup can never be read again.",
        "#",
        f"KMS_PROVIDER={os.getenv('KMS_PROVIDER', 'software')}",
    ]
    if isinstance(kms, SoftwareKMSProvider):
        lines.append(f"HEALTH_BLOCKCHAIN_KEY={kms.get_signing_key().decode('utf-8')}")
        lines.append(f"# device fingerprint (contents of VHV_DEVICE_FINGERPRINT_FILE): {kms.get_device_id()}")
    else:
        lines.append("# The signing key is held by the external KMS (e.g. Vault Transit) and is not")
        lines.append("# exported here: back it up with that system's own backup procedure.")
    secret = os.environ.get("PSEUDONYM_SECRET")
    if not secret:
        if engine._is_production():
            raise _fail("PSEUDONYM_SECRET is not set")
        secret = engine._DEFAULT_SECRET
        lines.append("# PSEUDONYM_SECRET is not set: this install uses the development default.")
    lines.append(f"PSEUDONYM_SECRET={secret}")

    os.makedirs(keys_out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(keys_out, f"psikonot-keys-{stamp}.env")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    bar = "!" * 72
    print(bar, file=sys.stderr)
    print(f"KEY EXPORT written to {path}", file=sys.stderr)
    print("Store it ELSEWHERE: not on this host, not next to the data backups.", file=sys.stderr)
    print("Delete this copy once it is stored safely.", file=sys.stderr)
    print(bar, file=sys.stderr)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.backup", description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="folder to write the encrypted data backup into")
    mode.add_argument("--keys-out", help="folder to write the key export into (separate, keep elsewhere)")
    parser.add_argument("--passphrase-file", help="file holding the backup passphrase")
    parser.add_argument("--key-file", help="file holding a 32-byte base64 key instead of a passphrase")
    args = parser.parse_args(argv)
    try:
        if args.keys_out:
            export_keys(args.keys_out)
        else:
            create_backup(args.out, fmt.resolve_secret(args.passphrase_file, args.key_file))
    except fmt.BackupError as e:
        raise _fail(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
