"""
core/services/erasure_ledger.py — the list of erased clients
============================================================
Erasure (KVKK Art. 17) destroys a client's erasure key, mapping, card and
appointments. A backup taken before the erasure still holds all of them, so
restoring it would make the client readable again. This ledger is how a restore
knows which clients to erase again (scripts/restore.py).

One JSON line per erased client: the client's pseudonym (the anon id that names
their chain store), the time, and a hash chained to the previous line. No name,
no client ID, nothing clinical. The line is written BEFORE the key is destroyed:
if it cannot be written, the erasure does not happen.

The file lives at VHV_ERASURE_LEDGER (default: next to the SQLite file). It is
not part of the data backup, because a restore must never roll it back: keep it
on storage the data restore does not touch, and copy it off the host with the
key export. VHV_ERASURE_LEDGER_MIRROR, if set, gets every line as well.

This module imports nothing from the database layer, so the restore script can
read the ledger before any database is opened.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import List

from database import paths

_lock = threading.Lock()


class LedgerError(Exception):
    """The ledger file is damaged: a line does not parse or the chain is broken."""


def ledger_paths() -> List[str]:
    found = [paths.erasure_ledger_path()]
    mirror = os.getenv("VHV_ERASURE_LEDGER_MIRROR")
    if mirror:
        found.append(mirror)
    return found


def _entry_hash(seq: int, anon_id: str, erased_at: str, prev: str) -> str:
    body = json.dumps({"seq": seq, "anon_id": anon_id, "erased_at": erased_at, "prev": prev},
                      sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_entries(path: str) -> List[dict]:
    """Every entry, checked line by line. Raises FileNotFoundError if the file is
    missing and LedgerError if it is damaged."""
    entries: List[dict] = []
    prev = ""
    with open(path, "r", encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ok = (entry["seq"] == len(entries) + 1 and entry["prev"] == prev
                      and entry["hash"] == _entry_hash(entry["seq"], entry["anon_id"],
                                                       entry["erased_at"], entry["prev"]))
            except (ValueError, KeyError, TypeError):
                ok = False
            if not ok:
                raise LedgerError(f"{path}: line {number} is damaged or out of order")
            entries.append(entry)
            prev = entry["hash"]
    return entries


def ensure_exists(path: str) -> None:
    """Create an empty ledger if there is none, so its absence later means it
    was lost, not that nobody was ever erased."""
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass


def record(anon_id: str) -> None:
    """Append one erased client to every ledger file. Raises if any write fails,
    so the caller stops before destroying anything."""
    with _lock:
        for path in ledger_paths():
            ensure_exists(path)
            entries = read_entries(path)
            if any(e["anon_id"] == anon_id for e in entries):
                continue
            seq = len(entries) + 1
            prev = entries[-1]["hash"] if entries else ""
            erased_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry = {"seq": seq, "anon_id": anon_id, "erased_at": erased_at, "prev": prev,
                     "hash": _entry_hash(seq, anon_id, erased_at, prev)}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
