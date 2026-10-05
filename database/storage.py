"""
database/storage.py — PsikoNot · LMDB Storage Layer Facade
=====================================================================
Acts as a backward-compatible facade routing user database and audit
operations to dedicated split persistence modules.
"""

import logging
import os
import json
import base64
import lmdb
import shutil
from typing import Optional, List, Any

from database.connection import (  # noqa: F401 - re-exported for callers
    LMDBConnectionManager, active_txn, active_project,
    after_commit_hooks, run_after_commit,
)

logger = logging.getLogger("vhv.storage")

# This module is the storage facade: the unit of work and the repositories reach
# the transaction context through `storage.active_txn` and friends rather than
# importing database.connection directly. They are re-exports, not dead imports.
__all__ = [
    "LMDBConnectionManager", "active_txn", "active_project",
    "after_commit_hooks", "run_after_commit",
]

# ──────────────────────────────────────────────
# CONSTANTS & SETUP
# ──────────────────────────────────────────────
_STORAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_STORAGE_DIR)
# Overridable, like VHV_SQLITE_PATH, so a test run or a container can keep the
# record chains somewhere other than next to the code.
PROJECTS_DIR = os.getenv("VHV_PROJECTS_DIR", os.path.join(_PROJECT_ROOT, "backend", "projects"))

env_map_size = os.getenv("VHV_LMDB_MAP_SIZE")
if env_map_size:
    try:
        env_map_size_clean = env_map_size.strip().upper()
        if env_map_size_clean.endswith("G"):
            LMDB_MAP_SIZE = int(float(env_map_size_clean[:-1]) * 1024 * 1024 * 1024)
        elif env_map_size_clean.endswith("M"):
            LMDB_MAP_SIZE = int(float(env_map_size_clean[:-1]) * 1024 * 1024)
        elif env_map_size_clean.endswith("K"):
            LMDB_MAP_SIZE = int(float(env_map_size_clean[:-1]) * 1024)
        else:
            LMDB_MAP_SIZE = int(env_map_size_clean)
    except Exception:
        LMDB_MAP_SIZE = 2 * 1024 * 1024 * 1024
else:
    LMDB_MAP_SIZE = 2 * 1024 * 1024 * 1024

USERS_DB_NAME = "__users__"

# Default instance for process-wide usage and backward compatibility
default_db_manager = LMDBConnectionManager(PROJECTS_DIR, LMDB_MAP_SIZE)

# ──────────────────────────────────────────────
# BACKWARD COMPATIBLE DELEGATES
# ──────────────────────────────────────────────

def ensure_projects_dir() -> str:
    return default_db_manager.ensure_projects_dir()

def get_project_path(project_name: str) -> str:
    return default_db_manager.get_project_path(project_name)

def get_db_path(project_name: str) -> str:
    return default_db_manager.get_db_path(project_name)

def create_project(project_name: str) -> bool:
    return default_db_manager.create_project(project_name)

def project_exists(project_name: str) -> bool:
    return default_db_manager.project_exists(project_name)

def list_projects() -> List[str]:
    return default_db_manager.list_projects()

def open_db(project_name: str) -> lmdb.Environment:
    return default_db_manager.open_db(project_name)

def run_write_transaction(project_name: str, txn_func) -> Any:
    return default_db_manager.run_write_transaction(project_name, txn_func)

def _block_key(index: int) -> bytes:
    return f"{index:010d}".encode("utf-8")

# ──────────────────────────────────────────────
# BLOCK OPERATIONS
# ──────────────────────────────────────────────

def save_block_to_db(project_name: str, index: int, block_data: dict, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    manager = db_manager or default_db_manager
    def txn_block(txn):
        key = _block_key(index)
        value = json.dumps(block_data, ensure_ascii=False).encode("utf-8")
        txn.put(key, value)
        txn.put(b"meta_last_index", str(index).encode("utf-8"))
    manager.run_write_transaction(project_name, txn_block)


def load_all_blocks(project_name: str, db_manager: Optional[LMDBConnectionManager] = None) -> List[dict]:
    manager = db_manager or default_db_manager
    if not manager.project_exists(project_name):
        return []

    env = manager.open_db(project_name)
    blocks = []
    with env.begin(write=False) as txn:
        cursor = txn.cursor()
        for key, value in cursor:
            if (
                key.startswith(b"meta_")
                or key.startswith(b"salt_")
                or key.startswith(b"audit_")
                or key.startswith(b"user_")
                or key.startswith(b"access_log_")
                or key.startswith(b"pwd_hash_")
                or key.startswith(b"consent_")
                or key.startswith(b"notif_")
            ):
                continue
            try:
                block_data = json.loads(value.decode("utf-8"))
                blocks.append(block_data)
            except Exception:
                continue
    return blocks


def reset_db(project_name: str, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    manager = db_manager or default_db_manager
    manager.close_db(project_name)
    path = manager.get_db_path(project_name)
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
        except Exception as e:
            logger.error(f"DB reset error: {e}")

# ──────────────────────────────────────────────
# PER-PATIENT ENCRYPTION SALT MANAGEMENT
# ──────────────────────────────────────────────

def save_patient_salt(project_name: str, salt: bytes, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    manager = db_manager or default_db_manager
    def txn_block(txn):
        key = f"meta_salt_{project_name}".encode("utf-8")
        txn.put(key, base64.urlsafe_b64encode(salt))
    manager.run_write_transaction(project_name, txn_block)


def get_patient_salt(project_name: str, db_manager: Optional[LMDBConnectionManager] = None) -> bytes:
    manager = db_manager or default_db_manager
    env = manager.open_db(project_name)
    with env.begin(write=False) as txn:
        key = f"meta_salt_{project_name}".encode("utf-8")
        val = txn.get(key)
        if val:
            return base64.urlsafe_b64decode(val)

    salt = os.urandom(32)
    save_patient_salt(project_name, salt, manager)
    return salt


def save_block_salt(project_name: str, block_index: int, salt: bytes, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    manager = db_manager or default_db_manager
    def txn_block(txn):
        key = f"salt_{block_index:010d}".encode("utf-8")
        txn.put(key, base64.urlsafe_b64encode(salt))
    manager.run_write_transaction(project_name, txn_block)


def load_block_salt(project_name: str, block_index: int, db_manager: Optional[LMDBConnectionManager] = None) -> Optional[bytes]:
    manager = db_manager or default_db_manager
    env = manager.open_db(project_name)
    with env.begin(write=False) as txn:
        key = f"salt_{block_index:010d}".encode("utf-8")
        value = txn.get(key)
        if value:
            return base64.urlsafe_b64decode(value)
        return None


def save_block_pwd_hash(project_name: str, block_index: int, pwd_hash: str, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    manager = db_manager or default_db_manager
    def txn_block(txn):
        key = f"pwd_hash_{block_index:010d}".encode("utf-8")
        txn.put(key, pwd_hash.encode("utf-8"))
    manager.run_write_transaction(project_name, txn_block)


def load_block_pwd_hash(project_name: str, block_index: int, db_manager: Optional[LMDBConnectionManager] = None) -> Optional[str]:
    manager = db_manager or default_db_manager
    env = manager.open_db(project_name)
    with env.begin(write=False) as txn:
        key = f"pwd_hash_{block_index:010d}".encode("utf-8")
        val = txn.get(key)
        if val:
            return val.decode("utf-8")
        return None

# ──────────────────────────────────────────────
# DYNAMIC ROUTING & FACADE INTEGRATIONS
# ──────────────────────────────────────────────

from database.user_storage import (
    save_user as _save_user,
    load_user as _load_user,
    load_all_users as _load_all_users,
    user_exists as _user_exists,
    delete_user as _delete_user,
    seed_default_users as _seed_default_users
)

from database.audit_storage import (
    append_access_log as _append_access_log,
    load_access_logs as _load_access_logs,
    append_audit_log as _append_audit_log,
    load_audit_logs as _load_audit_logs,
    verify_access_log_integrity as _verify_access_log_integrity,
)

from database.sql_db import (
    blacklist_token as _blacklist_token,
    is_token_blacklisted as _is_token_blacklisted,
    clean_expired_blacklisted_tokens as _clean_expired_blacklisted_tokens
)

def save_user(user_data: dict, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _save_user(user_data, db_manager or default_db_manager)

def load_user(username: str, db_manager: Optional[LMDBConnectionManager] = None) -> Optional[dict]:
    return _load_user(username, db_manager or default_db_manager)

def load_all_users(db_manager: Optional[LMDBConnectionManager] = None) -> List[dict]:
    return _load_all_users(db_manager or default_db_manager)

def user_exists(username: str, db_manager: Optional[LMDBConnectionManager] = None) -> bool:
    return _user_exists(username, db_manager or default_db_manager)

def delete_user(username: str, db_manager: Optional[LMDBConnectionManager] = None) -> bool:
    return _delete_user(username, db_manager or default_db_manager)

def seed_default_users(db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _seed_default_users(db_manager or default_db_manager)

def append_access_log(project_name: str, username: str, action: str, device_id: Optional[str] = None, extra: Optional[dict] = None, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _append_access_log(project_name, username, action, device_id, extra, db_manager or default_db_manager)

def load_access_logs(project_name: str, limit: int = 100, offset: int = 0, db_manager: Optional[LMDBConnectionManager] = None) -> List[dict]:
    return _load_access_logs(project_name, limit, offset, db_manager or default_db_manager)

def verify_access_log_integrity(project_name: str, db_manager: Optional[LMDBConnectionManager] = None) -> dict:
    return _verify_access_log_integrity(project_name, db_manager or default_db_manager)

def append_audit_log(project_name: str, action: str, username: str, block_index: Optional[int] = None, device_id: Optional[str] = None, extra: Optional[dict] = None, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _append_audit_log(project_name, action, username, block_index, device_id, extra, db_manager or default_db_manager)

def load_audit_logs(project_name: str, limit: int = 100, db_manager: Optional[LMDBConnectionManager] = None) -> List[dict]:
    return _load_audit_logs(project_name, limit, db_manager or default_db_manager)

def blacklist_token(jti: str, exp: float, db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _blacklist_token(jti, exp)

def is_token_blacklisted(jti: str, db_manager: Optional[LMDBConnectionManager] = None) -> bool:
    return _is_token_blacklisted(jti)

def clean_expired_blacklisted_tokens(db_manager: Optional[LMDBConnectionManager] = None) -> None:
    _clean_expired_blacklisted_tokens()
