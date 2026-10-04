"""
backend/routers/erasure.py — GDPR / KVKK Art. 17 crypto-shredding erasure
=========================================================================
The right to be forgotten on an append-only, tamper-evident chain cannot delete
blocks — that would break the very integrity the vault promises. Instead it
destroys the patient's per-patient erasure key: the at-rest key is derived from
the KMS root AND that secret, so once the secret is gone every record encrypted
under it is permanently undecryptable. The chain and its signatures remain intact.
The client's card (name, phone, e-mail) and appointments are deleted outright:
they are not on the chain, so nothing stops them from simply going.

Erasure is irreversible, so it requires a privileged operator AND a co-signed
Dual-Control token — the same second-principal gate that protects raw record
access.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

import database.audit_storage as audit_storage
from backend.dependencies import current_user, get_db_manager
from backend.routers.records import check_patient_id, _enforce_privileged_dual_control
from core.events.event_bus import event_bus, SystemAuditEvent
from core.pseudonymization.service import project_name_for
from core.security import get_device_id
from core.services import appointment_book, client_registry
from core.services.erasure_service import get_erasure_key_store
from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder

router = APIRouter(prefix="/api/v1/erasure", tags=["erasure"])

_PRIVILEGED = {"admin", "security_officer"}


def _delete_pseudonym_mapping(patient_id: str) -> bool:
    """Drop the identity↔pseudonym mapping so the pseudonym can never be resolved."""
    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            _to_placeholder("SELECT 1 FROM patient_pseudonyms WHERE patient_id = ?"),
            (patient_id,),
        )
        existed = cur.fetchone() is not None
        cur.execute(
            _to_placeholder("DELETE FROM patient_pseudonyms WHERE patient_id = ?"),
            (patient_id,),
        )
        conn.commit()
        return existed
    finally:
        cur.close()
        conn.close()


@router.post("/{patient_id}", summary="Cryptographically erase a patient (GDPR/KVKK Art. 17)")
def erase_patient(
    patient_id: str,
    request: Request,
    u: dict = Depends(current_user),
    db_manager=Depends(get_db_manager),
):
    check_patient_id(patient_id)
    if u.get("role") not in _PRIVILEGED:
        raise HTTPException(403, "Only an administrator or security officer may erase a patient")
    # Irreversible → the same co-signed Dual-Control gate as raw record access.
    _enforce_privileged_dual_control(request, u, patient_id)

    store = get_erasure_key_store()
    was_already_erased = not store.exists(patient_id)
    key_destroyed = store.destroy(patient_id)
    mapping_removed = _delete_pseudonym_mapping(patient_id)
    card_removed = client_registry.remove(patient_id)
    appointment_book.remove_for_client(patient_id)

    # Tombstone in the tamper-evident access ledger; the chain blocks are untouched.
    try:
        audit_storage.append_access_log(
            project_name_for(patient_id),
            u["username"],
            "ERASURE_EXECUTED",
            get_device_id(),
            {"note": "crypto-shred: erasure key destroyed"},
            db_manager=db_manager,
        )
    except Exception:
        pass

    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action="ERASURE_EXECUTED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"patient_id": patient_id},
    ))

    return {
        "success": True,
        "patient_id": patient_id,
        "erased": True,
        "was_already_erased": was_already_erased,
        "erasure_key_destroyed": key_destroyed,
        "identity_mapping_removed": mapping_removed,
        "client_card_removed": card_removed,
        "note": "Records remain on the append-only chain but are now permanently undecryptable.",
    }
