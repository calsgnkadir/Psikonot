"""
tests/backup_fixture.py — helper processes for tests/test_backup_restore.py
===========================================================================
Not a test module. The backup tests need a vault of their own (its own SQLite
file, record chains and keys) and a second, empty one to restore into. The app
reads its paths from the environment once, at import time, so each step runs in
a child process started with that vault's environment:

    python -m tests.backup_fixture populate   -> JSON of what was written
    python -m tests.backup_fixture erase CL-…  -> erases one client, as the API does
    python -m tests.backup_fixture read        -> JSON of what can be read back
"""

import json
import sys
import time

CLIENTS = {
    "CL-101": ("Deniz Backupoğlu", "+90 532 111 11 01", "deniz@example.com"),
    "CL-102": ("Selin Restoreci", "+90 532 111 11 02", None),
    "CL-103": ("Erased Person", "+90 532 111 11 03", "erase.me@example.com"),
}
PROTECTED_PASSWORD = "Protected-Pass-2026!"
ATTACHMENT_BLOB = "ZW5jcnlwdGVkLWJsb2ItYnl0ZXMtZm9yLXRoZS10ZXN0"


def _service():
    from core.services.record_service import RecordService
    from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
    from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
    return RecordService(LMDBBlockRepository(), AESGCMStrategy())


def populate() -> dict:
    from core.services import appointment_book, client_registry
    from core.services.attachment_store import AttachmentStore
    from database.sql_db import default_sql_db

    default_sql_db.seed_default_users()
    svc = _service()
    out = {"plain": {}, "protected": {}, "attachment": {}}
    for pid, (name, phone, email) in CLIENTS.items():
        client_registry.add(practitioner="psk.elif", full_name=name, phone=phone, email=email,
                            kvkk_signed_on="2026-10-01", created_by="psk.elif", patient_id=pid)
        appointment_book.add_existing(practitioner="psk.elif", patient_id=pid,
                                      starts_at=time.time() - 86400, duration_min=50,
                                      session_format="in_person", status="attended",
                                      created_by="secretary.ayse")
        note = {"record_type": "session_note", "title": f"Session for {pid}",
                "data": {"note": f"NOTE-{pid}", "mood": "calm"}}
        block = svc.add_record(pid, note, username="psk.elif")
        out["plain"].setdefault(pid, {})[str(block.index)] = note

    secret = {"record_type": "session_note", "title": "Confidential",
              "data": {"note": "PROTECTED-CONTENT"}}
    block = svc.add_record("CL-101", secret, is_protected=True,
                           protection_password=PROTECTED_PASSWORD, username="psk.elif")
    out["protected"] = {"patient_id": "CL-101", "index": block.index, "data": secret}

    ref = AttachmentStore().put(ATTACHMENT_BLOB)
    with_file = {"record_type": "worksheet", "title": "Worksheet", "data": {"note": "see file"},
                 "file_name": "worksheet.pdf", "file_hash": ref}
    block = svc.add_record("CL-102", with_file, username="psk.elif")
    out["plain"]["CL-102"][str(block.index)] = with_file
    out["attachment"] = {"ref": ref, "blob": ATTACHMENT_BLOB}
    return out


def erase(patient_id: str) -> dict:
    """The steps of POST /api/v1/erasure/{id}, without the HTTP and dual-control
    layer that tests/test_erasure.py already covers."""
    from backend.routers.erasure import _delete_pseudonym_mapping
    from core.services import appointment_book, client_registry
    from core.services.erasure_service import get_erasure_key_store

    destroyed = get_erasure_key_store().destroy(patient_id)
    _delete_pseudonym_mapping(patient_id)
    client_registry.remove(patient_id)
    appointment_book.remove_for_client(patient_id)
    return {"destroyed": destroyed}


def read() -> dict:
    from core.services import appointment_book, client_registry
    from core.services.attachment_store import AttachmentStore
    from core.services.erasure_service import get_erasure_key_store
    from database.sql_db import run_sql

    svc = _service()
    out = {"clients": {}}
    for pid in CLIENTS:
        final = svc.get_final_data(pid)
        rows = run_sql("SELECT 1 FROM patient_pseudonyms WHERE patient_id = ?", (pid,), fetch="all")
        out["clients"][pid] = {
            "chain_valid": svc.is_chain_valid(pid),
            "records": {str(i): v for i, v in final.items()},
            "card": client_registry.get(pid),
            "erasure_key": get_erasure_key_store().exists(pid),
            "mapping": bool(rows),
            "appointments": len(appointment_book.list_appointments(
                patient_id=pid, start=0, end=time.time() + 86400)),
        }
    out["protected_read"] = svc.get_block_data("CL-101", _protected_index(svc), PROTECTED_PASSWORD)
    try:
        out["attachment_blob"] = AttachmentStore().get(sys.argv[2]) if len(sys.argv) > 2 else None
    except FileNotFoundError:
        out["attachment_blob"] = None
    return out


def _protected_index(svc) -> int:
    return next(b.index for b in svc.get_chain("CL-101") if b.is_protected)


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "populate":
        result = populate()
    elif command == "erase":
        result = erase(sys.argv[2])
    elif command == "read":
        result = read()
    else:
        raise SystemExit(f"unknown command {command}")
    print("@@JSON@@" + json.dumps(result, ensure_ascii=False, default=str))
