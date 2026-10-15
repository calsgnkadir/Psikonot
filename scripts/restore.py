"""
scripts/restore.py — put a PsikoNot backup back, without bringing erased clients back
=====================================================================================
    python -m scripts.restore --from <backup folder> [--erasure-ledger <file>] [--force]

Run it with the app STOPPED and with the same environment as the app
(VHV_SQLITE_PATH, VHV_PROJECTS_DIR, HEALTH_BLOCKCHAIN_KEY, PSEUDONYM_SECRET) —
those decide where the data goes and which keys read it.

Nothing is written to the target until every check has passed:

1. The target must be empty (no SQLite file, nothing in the projects folder),
   unless --force, which DELETES what is there — but only at step 6.
2. The erasure ledger must be readable, unbroken, and at least as new as the
   backup (it must contain the backup's ledger position). Missing ledger:
   refused, unless --confirm-no-erasures.
3. The archive is decrypted into a temp folder; its SHA-256, every chunk's
   AES-GCM tag and every file's SHA-256 in the manifest must match.
4. The signing key and the pseudonym secret must be the ones the backup was
   taken with (key check values in the manifest).
5. Every client in the ledger is erased again in the restored copy: erasure
   key, pseudonym mapping, client card and appointments are deleted.
6. The restored copy is verified: every chain passes is_chain_valid, every
   record of a client that is not erased decrypts, erased clients' records do
   not, attachments match their hashes, and the counts match the manifest
   (less what step 5 removed). Only then is it moved into place.
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Only modules that open no database: the target must stay untouched until the end.
from core.services import erasure_ledger  # noqa: E402
from database import paths  # noqa: E402
from scripts import backup_format as fmt  # noqa: E402

# What erasing a client removes from SQLite (as backend/routers/erasure.py does),
# keyed by the count it lowers in the manifest.
_ERASED_ROWS = {
    "erasure_keys": ("SELECT patient_id FROM patient_erasure_keys",
                     "DELETE FROM patient_erasure_keys WHERE patient_id = ?"),
    "pseudonym_mappings": ("SELECT patient_id FROM patient_pseudonyms",
                           "DELETE FROM patient_pseudonyms WHERE patient_id = ?"),
    "clients": ("SELECT patient_id FROM clients",
                "DELETE FROM clients WHERE patient_id = ?"),
    "appointments": ("SELECT DISTINCT patient_id FROM appointments",
                     "DELETE FROM appointments WHERE patient_id = ?"),
}


class RestoreError(Exception):
    pass


def _target_is_empty(sqlite_path: str, projects_dir: str) -> bool:
    if os.path.exists(sqlite_path):
        return False
    return not (os.path.isdir(projects_dir) and os.listdir(projects_dir))


def _check_ledger(path: str, confirm_no_erasures: bool):
    if not os.path.exists(path):
        if not confirm_no_erasures:
            raise RestoreError(
                f"No erasure ledger at {path}. Without it this restore could bring back clients "
                "erased after the backup (KVKK Art. 17). Point --erasure-ledger at the current "
                "copy. Only if you are certain no client was ever erased after this backup, "
                "pass --confirm-no-erasures.")
        print("WARNING: no erasure ledger; you confirmed no client was erased after this backup.")
        return None
    try:
        return erasure_ledger.read_entries(path)
    except erasure_ledger.LedgerError as e:
        raise RestoreError(f"The erasure ledger is damaged: {e}")


def _check_ledger_against_backup(entries, manifest: dict) -> None:
    at_backup = manifest.get("erasure_ledger", {"count": 0, "head": ""})
    count = at_backup["count"]
    if entries is None:
        return
    if len(entries) < count or (count and entries[count - 1]["hash"] != at_backup["head"]):
        raise RestoreError(
            f"The erasure ledger is older than this backup or is a different ledger: the backup "
            f"was taken when the ledger had {count} entries, this one has {len(entries)}. "
            "Find the current ledger before restoring.")


def _reapply_erasures(db_path: str, erased_anon_ids: set) -> dict:
    """Erase every client in the ledger again, in the restored SQLite copy."""
    from core.pseudonymization.engine import PseudonymizationEngine
    engine = PseudonymizationEngine()
    removed = {"clients_erased": 0, "erasure_keys": 0, "pseudonym_mappings": 0,
               "clients": 0, "appointments": 0}
    conn = sqlite3.connect(db_path)
    try:
        ids = set()
        for select, _ in _ERASED_ROWS.values():
            ids |= {r[0] for r in conn.execute(select)}
        mapped = dict(conn.execute("SELECT patient_id, anon_id FROM patient_pseudonyms").fetchall())
        for pid in ids:
            if pid is None:
                continue
            if engine.generate_anon_id(pid) not in erased_anon_ids and mapped.get(pid) not in erased_anon_ids:
                continue
            removed["clients_erased"] += 1
            for name, (_, delete) in _ERASED_ROWS.items():
                removed[name] += conn.execute(delete, (pid,)).rowcount
        conn.commit()
    finally:
        conn.close()
    return removed


def _verify(stage: str, manifest: dict, erased_anon_ids: set, removed: dict) -> dict:
    """Check the restored copy in `stage` with the app's own code. The app's
    modules are imported here for the first time, pointed at `stage`."""
    os.environ["VHV_SQLITE_PATH"] = os.path.join(stage, "sql", "vault.db")
    os.environ["VHV_PROJECTS_DIR"] = os.path.join(stage, "projects")
    import hashlib

    from core.pseudonymization.engine import PseudonymizationEngine
    from core.services.erasure_service import get_erasure_key_store
    from core.services.record_service import RecordService
    from database.sql_db import run_sql
    from database.storage import default_db_manager
    from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
    from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
    from scripts.backup import sql_counts, store_counts

    engine = PseudonymizationEngine()
    known = set()
    for sql in ("SELECT patient_id FROM patient_pseudonyms", "SELECT patient_id FROM patient_erasure_keys",
                "SELECT patient_id FROM clients"):
        known |= {r[0] for r in run_sql(sql, fetch="all")}
    by_anon = {f"patient_{engine.generate_anon_id(pid)}": pid for pid in known}

    report = {"chains": 0, "chains_valid": 0, "readable": 0, "unreadable": [], "erased_chains": 0,
              "still_readable_after_erasure": [], "unmapped": 0, "problems": []}
    repo = LMDBBlockRepository()
    keys = get_erasure_key_store()
    for name in manifest["stores"]:
        if not name.startswith("patient_"):
            continue
        report["chains"] += 1
        chain = repo.load_all_blocks(name)
        svc = RecordService(repo, AESGCMStrategy())
        if svc.find_broken_link_index("", chain) == -1:
            report["chains_valid"] += 1
        else:
            report["problems"].append(f"chain {name[:20]}… fails is_chain_valid")
        erased = name[len("patient_"):] in erased_anon_ids
        pid = by_anon.get(name)
        if erased:
            report["erased_chains"] += 1
            # Step 5 deleted the key, mapping and card, so nothing should name this
            # chain any more; if something still does, its key must be gone.
            if pid is not None and keys.get(pid) is not None:
                report["still_readable_after_erasure"].append(name[:20])
            continue
        if pid is None:
            report["unmapped"] += 1
            continue
        for value in svc.get_final_data(pid, chain).values():
            if isinstance(value, dict) and (value.get("__unreadable__") or value.get("__erased__")):
                report["unreadable"].append(name[:20])
                break
        else:
            report["readable"] += 1

    if "attachments" in manifest["stores"]:
        env = default_db_manager.open_db("attachments")
        with env.begin() as txn:
            for key, value in txn.cursor():
                if key.startswith(b"attachment_") and hashlib.sha256(value).hexdigest() != key[11:].decode():
                    report["problems"].append("an attachment does not match its hash")
    default_db_manager.close_all()

    expected = dict(manifest["counts"])
    for name in ("erasure_keys", "pseudonym_mappings", "clients", "appointments"):
        expected[name] -= removed[name]
    actual = {**sql_counts(os.environ["VHV_SQLITE_PATH"]),
              **store_counts(os.environ["VHV_PROJECTS_DIR"], manifest["stores"])}
    for name, value in expected.items():
        if actual.get(name) != value:
            report["problems"].append(f"count of {name}: expected {value}, found {actual.get(name)}")
    report["counts"] = actual
    if report["unreadable"]:
        report["problems"].append(f"{len(report['unreadable'])} client(s) with records that do not decrypt")
    if report["still_readable_after_erasure"]:
        report["problems"].append("an erased client is readable again")
    return report


def _clear_target(sqlite_path: str, projects_dir: str) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(sqlite_path + suffix):
            os.remove(sqlite_path + suffix)
    if os.path.isdir(projects_dir):
        for name in os.listdir(projects_dir):
            full = os.path.join(projects_dir, name)
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)


def _move_into_place(stage: str, manifest: dict, sqlite_path: str, projects_dir: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(sqlite_path)), exist_ok=True)
    shutil.move(os.path.join(stage, "sql", "vault.db"), sqlite_path)
    os.makedirs(projects_dir, exist_ok=True)
    for name in manifest["stores"]:
        src = os.path.join(stage, "projects", name)
        lock = os.path.join(src, "chaindata.lmdb", "lock.mdb")
        if os.path.exists(lock):
            os.remove(lock)
        shutil.move(src, os.path.join(projects_dir, name))
    for name in manifest.get("empty_projects", []):
        os.makedirs(os.path.join(projects_dir, name), exist_ok=True)


def restore(backup_dir: str, ledger_path: str, force: bool, confirm_no_erasures: bool, secret) -> dict:
    sqlite_path, projects_dir = paths.sqlite_path(), paths.projects_dir()
    if os.getenv("VHV_DATABASE_URL"):
        raise RestoreError("VHV_DATABASE_URL is set (PostgreSQL): this tool restores SQLite only. "
                           "See docs/BACKUP_RESTORE.md.")
    if not _target_is_empty(sqlite_path, projects_dir) and not force:
        raise RestoreError(f"The target is not empty ({sqlite_path}, {projects_dir}). Restore "
                           "refuses to overwrite data; --force deletes it first.")
    entries = _check_ledger(ledger_path, confirm_no_erasures)

    work = tempfile.mkdtemp(prefix="pkr-")
    try:
        stage = os.path.join(work, "x")
        try:
            manifest = fmt.decrypt_archive(backup_dir, stage, secret=secret)
        except (fmt.BackupError, OSError, ValueError, EOFError) as e:
            raise RestoreError(f"The backup cannot be used: {e}")
        print(f"Backup of {manifest['created_at']} (app {manifest['app'].get('version')}, "
              f"commit {(manifest['app'].get('commit') or '?')[:10]}): archive and "
              f"{len(manifest['files'])} file hashes verified.")
        _check_ledger_against_backup(entries, manifest)

        if fmt.key_check_values() != manifest["key_check"]:
            raise RestoreError("The signing key or PSEUDONYM_SECRET in this environment is not the "
                               "one the backup was taken with. Load the key export first.")

        erased = {e["anon_id"] for e in entries or []}
        after = [e for e in entries or [] if e["erased_at"] > manifest["created_at"]]
        if entries is not None:
            last = entries[-1]["erased_at"] if entries else "never"
            print(f"Erasure ledger: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, "
                  f"{len(after)} recorded after the backup, last erasure {last}.")
            print("  If any client was erased after that time, this ledger is out of date: stop, "
                  "find the current copy and run the restore again.")
        removed = _reapply_erasures(os.path.join(stage, "sql", "vault.db"), erased)
        print(f"Erasures re-applied: {removed['clients_erased']} client(s) found in the backup "
              f"and erased again.")

        report = _verify(stage, manifest, erased, removed)
        print(f"Verification: {report['chains_valid']}/{report['chains']} chains valid, "
              f"{report['readable']} client file(s) readable, {report['erased_chains']} erased "
              f"(unreadable as they should be), {report['unmapped']} chain(s) with no client mapping.")
        c = report["counts"]
        print(f"  counts: {c['clients']} client card(s), {c['records']} record(s), "
              f"{c['attachments']} attachment(s), {c['users']} account(s)")
        if report["problems"]:
            raise RestoreError("Verification failed, nothing was written: " + "; ".join(report["problems"]))

        if force:
            _clear_target(sqlite_path, projects_dir)
        _move_into_place(stage, manifest, sqlite_path, projects_dir)
        print(f"Restored into {sqlite_path} and {projects_dir}. All {report['chains']} chains valid.")
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.restore", description=__doc__.split("\n\n")[0])
    parser.add_argument("--from", dest="source", required=True, help="the backup folder (psikonot-backup-…)")
    parser.add_argument("--erasure-ledger", default=None,
                        help="the CURRENT erasure ledger (default: VHV_ERASURE_LEDGER or next to the SQLite file)")
    parser.add_argument("--confirm-no-erasures", action="store_true",
                        help="restore without a ledger: you confirm no client was erased after the backup")
    parser.add_argument("--force", action="store_true", help="delete the existing data in the target first")
    parser.add_argument("--passphrase-file")
    parser.add_argument("--key-file")
    args = parser.parse_args(argv)
    try:
        secret = fmt.resolve_secret(args.passphrase_file, args.key_file)
        restore(args.source, args.erasure_ledger or paths.erasure_ledger_path(),
                args.force, args.confirm_no_erasures, secret)
    except (RestoreError, fmt.BackupError) as e:
        print(f"RESTORE REFUSED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
