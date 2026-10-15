"""
database/paths.py — where the vault keeps its data
==================================================
The places on disk, read from the environment. Importing this module opens
nothing and creates nothing, so the restore script can find its target before
anything else touches it (database.sql_db creates the SQLite file on import).
"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sqlite_path() -> str:
    """The SQLite database: accounts, client cards, appointments, erasure keys.
    Overridable so a container keeps it in a data volume instead of next to the
    code in database/ (a volume mounted there would freeze the code)."""
    return os.getenv("VHV_SQLITE_PATH", os.path.join(_PROJECT_ROOT, "database", "vault.db"))


def projects_dir() -> str:
    """The LMDB environments: one per client chain, plus the attachment store and
    the system and user stores."""
    return os.getenv("VHV_PROJECTS_DIR", os.path.join(_PROJECT_ROOT, "backend", "projects"))


def erasure_ledger_path() -> str:
    """The list of erased clients (core/services/erasure_ledger.py). Point it at
    storage that a data restore never rolls back; by default it sits next to the
    SQLite file."""
    return os.getenv("VHV_ERASURE_LEDGER") or os.path.join(
        os.path.dirname(sqlite_path()), "erasure_ledger.jsonl"
    )
