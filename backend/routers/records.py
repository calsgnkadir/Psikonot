import os
import re
import base64
import secrets
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, Request, Response, Path
from pydantic import ValidationError

from backend.dependencies import (
    current_user, get_record_service, get_command_handler, get_query_handler, get_db_manager,
    get_attachment_store,
)
from core.services.attachment_store import AttachmentStore
from backend.schemas.requests import (
    RecordCreate, DecryptRequest, CorrectionCreate, RECORD_TYPES, DATA_SCHEMAS
)
from core.security import encrypt_data, decrypt_data, get_device_id
from core.cqrs.commands import AddRecordCommand, AddCorrectionCommand
from core.cqrs.queries import GetPatientRecordsQuery, DecryptRecordQuery
import database.storage as storage
from database.connection import LMDBConnectionManager
from core.services.record_service import RecordService
from core.cqrs.commands import CommandHandler
from core.cqrs.queries import QueryHandler
from core.services import access_policy, client_registry

router = APIRouter(prefix="/api/v1/records", tags=["records"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.dual_control import dual_control_engine
from core.services.alert_service import alert_service
from backend.dependencies import _get_client_ip
from core.pseudonymization.service import project_name_for

def check_patient_id(patient_id: str):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", patient_id):
        raise HTTPException(400, "Invalid patient_id format")

# The content a correction may change — the fields the record form sends.
CORRECTABLE_FIELDS = (
    "record_type", "title", "doctor_name", "institution",
    "record_date", "data", "notes",
)


# Who may see which record is decided in one place: core/services/access_policy.py.
# The helper below only connects it to the request (the user, the client card).

NOT_YOUR_CLIENT = "This client's file is not kept by you."


def _require_file_access(u: dict, patient_id: str):
    """File level: a practitioner opens only the files of their own clients;
    operators pass here and are held by dual control. The answer is the same
    whether or not the client exists, so client IDs cannot be probed. Any role
    the access policy does not know (the secretary) is refused outright."""
    if u["role"] not in access_policy.RECORD_ROLES:
        raise HTTPException(403, "This role has no access to client records.")
    if not access_policy.can_open_file(u["role"], u["username"], client_registry.owner_of(patient_id)):
        raise HTTPException(403, NOT_YOUR_CLIENT)

# Operator roles that administer the vault but have no clinical relationship with
# the patient. None of them may read raw records on their own authority.
PRIVILEGED_NON_CLINICAL_ROLES = access_policy.OPERATOR_ROLES


def _enforce_privileged_dual_control(request: Request, u: dict, patient_id: str,
                                     request_type: str = "DECRYPT_RAW_RECORD"):
    if u.get("role") in PRIVILEGED_NON_CLINICAL_ROLES:
        dc_token = request.headers.get("X-Dual-Control-Token") or request.query_params.get("dual_control_token")
        if not dc_token or not dual_control_engine.is_dual_control_approved(
                dc_token, patient_id, request_type=request_type, username=u["username"]):
            client_ip = _get_client_ip(request)
            alert_service.raise_alert(
                alert_type="DUAL_CONTROL_VIOLATION_BLOCKED",
                severity="CRITICAL",
                title=f"Admin Dual-Control Access Blocked for {patient_id}",
                description=f"Admin {u.get('username')} attempted unauthorized raw record access to client {patient_id} without an active Security Officer co-signed token.",
                username=u.get("username"),
                client_ip=client_ip,
                # Whether a token was sent, never the token itself: alerts are
                # read by every operator.
                extra={"patient_id": patient_id, "request_type": request_type,
                       "token_provided": bool(dc_token)}
            )
            raise HTTPException(
                status_code=403,
                detail=f"Dual-Control Policy Violation: privileged operators cannot view or decrypt client records without an active co-signed token for patient {patient_id}. Open Dual-Control Access to request one."
            )

@router.post("", summary="Add a record to a client's file")
def add_record(
    rec: RecordCreate,
    u: dict = Depends(current_user),
    command_handler: CommandHandler = Depends(get_command_handler),
    attachments: AttachmentStore = Depends(get_attachment_store),
):
    # Only the practitioner who keeps the client's file writes into it. An
    # operator does not write clinical records, co-signature or not.
    if u["role"] != "practitioner":
        raise HTTPException(403, "Only the client's practitioner can add records")
    _require_file_access(u, rec.patient_id)

    # Check the type-specific `data` fields. Free-form types have no schema.
    schema = DATA_SCHEMAS.get(rec.record_type)
    try:
        if schema:
            schema(**rec.data)
    except ValidationError as e:
        err_msgs = [".".join(str(x) for x in error["loc"]) + ": " + error["msg"] for error in e.errors()]
        raise HTTPException(status_code=422, detail=f"Validation failed: {', '.join(err_msgs)}")

    # Stored verbatim; the client escapes at render. See sanitize_html().
    block_data = ({
        "record_type":       rec.record_type,
        "record_type_label": RECORD_TYPES[rec.record_type],
        "title":             rec.title,
        "doctor_name":       rec.doctor_name,
        "institution":       rec.institution,
        "record_date":       rec.record_date,
        "access_level":      access_policy.PRACTITIONER_ONLY,
        "is_confidential":   rec.is_confidential,
        "data":              rec.data,
        "notes":             rec.notes or "",
        "created_by":        u["username"],
        "created_at":        datetime.now(timezone.utc).isoformat(),
        "patient_id":        rec.patient_id,
        "file_name":         rec.file_name,
        "file_type":         rec.file_type,
        "file_data":         None,
    })

    if rec.file_data:
        file_pwd = secrets.token_hex(16)
        enc_data_b64, file_salt_bytes = encrypt_data(rec.file_data, file_pwd)

        # Store the (already-encrypted) blob in the encrypted attachment store.
        ref = attachments.put(enc_data_b64)

        block_data["file_hash"] = ref
        block_data["file_salt"] = base64.b64encode(file_salt_bytes).decode("utf-8")
        block_data["file_pwd"] = file_pwd

    cmd = AddRecordCommand(
        patient_id=rec.patient_id,
        data=block_data,
        is_protected=rec.is_confidential,
        protection_password=rec.confidential_password if rec.is_confidential else None,
        username=u["username"]
    )
    block = command_handler.handle_add_record(cmd)

    return {
        "success":     True,
        "block_index": block.index,
        "block_hash":  block.hash[:20] + "...",
        "message":     "Record added to blockchain",
    }

@router.get("/{patient_id}", summary="Get Patient Records")
def get_records(
    patient_id: str,
    request: Request,
    u: dict = Depends(current_user),
    record_service: RecordService = Depends(get_record_service),
    query_handler: QueryHandler = Depends(get_query_handler),
    db_manager: LMDBConnectionManager = Depends(get_db_manager),
):
    check_patient_id(patient_id)
    _enforce_privileged_dual_control(request, u, patient_id)
    _require_file_access(u, patient_id)
    role = u["role"]

    query = GetPatientRecordsQuery(
        patient_id=patient_id,
        requester_username=u["username"],
        requester_role=role,
    )
    records = query_handler.handle_get_patient_records(query)
    records.sort(key=lambda x: x["timestamp"], reverse=True)

    from core.events.event_bus import SystemAuditEvent, event_bus
    proj_name = record_service._get_project_name(patient_id)
    event_bus.publish(SystemAuditEvent(
        project_name=proj_name,
        action="RECORDS_VIEWED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"record_count": len(records)}
    ))

    # Every read of a file lands in its tamper-evident access ledger.
    storage.append_access_log(
        project_name=proj_name,
        username=u["username"],
        action="RECORDS_VIEWED",
        device_id=get_device_id(),
        extra={"role": u["role"], "record_count": len(records),
               "client_ip": _get_client_ip(request)},
        db_manager=db_manager,
    )

    chain = record_service.get_chain(patient_id)
    return {
        "patient_id":   patient_id,
        "total_blocks": len(chain),
        "records":      records,
        "chain_valid":  record_service.is_chain_valid(patient_id),
    }

@router.get("/{patient_id}/{block_index}", summary="Get Single Record")
def get_single_record(
    patient_id: str,
    request: Request,
    block_index: int = Path(..., ge=0),
    version: str = "current",
    u: dict = Depends(current_user),
    record_service: RecordService = Depends(get_record_service),
):
    # This endpoint used to check only that a client stayed in their own file:
    # any practitioner could read any client's unprotected records by walking
    # block numbers (an IDOR). It now applies the same policy as the record list.
    check_patient_id(patient_id)
    _enforce_privileged_dual_control(request, u, patient_id)
    _require_file_access(u, patient_id)

    # A record the user may not see gets the same answer as one that does not
    # exist, just as the list leaves it out rather than showing it locked.
    not_found = HTTPException(404, "Block not found")
    chain = record_service.get_chain(patient_id)
    block = next((b for b in chain if b.index == block_index), None)
    if block is None:
        raise not_found

    if block.is_protected:
        if not access_policy.can_view_stored(u["role"], block.data):
            raise not_found
        return {
            "block_index": block_index,
            "is_protected": True,
            "data": "ENCRYPTED — use POST /decrypt with the correct password",
        }

    # version=original returns the pre-correction content; the original block is
    # never modified, so both versions remain readable. Both are checked, though
    # a correction carries the original's access level over.
    original = record_service.get_original_block_data(patient_id, block_index)
    if not access_policy.can_view_stored(u["role"], original):
        raise not_found
    if version == "original":
        data = original
    else:
        data = record_service.get_final_block_data(patient_id, block_index, password=None, username=u["username"])
        if not access_policy.can_view_stored(u["role"], data):
            raise not_found
    return {"block_index": block_index, "is_protected": False, "version": version, "data": data}

@router.post("/{patient_id}/{block_index}/decrypt", summary="Decrypt Encrypted Record")
def decrypt_record(
    patient_id: str,
    request: Request,
    block_index: int = Path(..., ge=0),
    req: DecryptRequest = None,
    u: dict = Depends(current_user),
    query_handler: QueryHandler = Depends(get_query_handler),
    db_manager: LMDBConnectionManager = Depends(get_db_manager),
):
    check_patient_id(patient_id)
    _enforce_privileged_dual_control(request, u, patient_id)
    _require_file_access(u, patient_id)

    if not req or not req.password:
        raise HTTPException(400, "Password is required to decrypt this record")

    query = DecryptRecordQuery(
        patient_id=patient_id,
        block_index=block_index,
        password=req.password,
        requester_username=u["username"],
        requester_role=u["role"],
    )
    data = query_handler.handle_decrypt_record(query)

    if isinstance(data, str) and ("INCORRECT" in data or "SECURE" in data or "ERROR" in data):
        raise HTTPException(403, "Incorrect password — decryption failed")

    # Record immutable audit access log
    proj_name = project_name_for(patient_id)
    storage.append_access_log(
        project_name=proj_name,
        username=u["username"],
        action="RECORD_DECRYPTED",
        device_id=get_device_id(),
        extra={
            "block_index": block_index,
            "role": u["role"],
            "client_ip": _get_client_ip(request)
        },
        db_manager=db_manager
    )

    from core.events.event_bus import SystemAuditEvent, event_bus
    event_bus.publish(SystemAuditEvent(
        project_name=proj_name,
        action="RECORD_DECRYPTED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"block_index": block_index, "role": u["role"]}
    ))

    return {"block_index": block_index, "data": data}

@router.post("/{patient_id}/{block_index}/correct", summary="Correct a Record (append-only)")
def correct_record(
    patient_id: str,
    request: Request,
    block_index: int = Path(..., ge=1),
    req: CorrectionCreate = None,
    u: dict = Depends(current_user),
    record_service: RecordService = Depends(get_record_service),
    command_handler: CommandHandler = Depends(get_command_handler),
    db_manager: LMDBConnectionManager = Depends(get_db_manager),
):
    """
    Append a correction. The original block is never modified — a client record
    is not overwritten, it is superseded by a correction, and both remain on the
    chain. Like writing, only the client's own practitioner may do it.
    """
    check_patient_id(patient_id)
    if u["role"] != "practitioner":
        raise HTTPException(403, "Only the client's practitioner can correct records")
    _require_file_access(u, patient_id)
    if not req or not isinstance(req.corrected_data, dict) or not req.corrected_data:
        raise HTTPException(400, "corrected_data (the superseding record) is required")

    original = record_service.get_block_data(patient_id, block_index, username=u["username"])
    if original is None:
        raise HTTPException(404, "Record not found")
    if isinstance(original, str):
        # Password-protected records need their password to read and re-seal;
        # correcting them is out of scope for this flow.
        raise HTTPException(400, "Password-protected records cannot be corrected here")
    if not isinstance(original, dict) or original.get("type") in ("audit", "correction"):
        raise HTTPException(400, "Only clinical records can be corrected")

    rec_type = original.get("record_type", "other")

    # Correcting requires seeing the record first.
    if not access_policy.can_view(u["role"], original):
        raise HTTPException(404, "Record not found")

    # A correction must pass the same checks as a new record: this endpoint used
    # to store corrected_data as-is, so any record_type, any data shape and any
    # extra key went straight onto the chain. Fields the client leaves out are
    # taken from the original; keys outside CORRECTABLE_FIELDS are dropped.
    merged = {k: original[k] for k in CORRECTABLE_FIELDS if original.get(k) is not None}
    merged.update({k: v for k, v in req.corrected_data.items() if k in CORRECTABLE_FIELDS})
    merged.setdefault("record_type", rec_type)
    try:
        checked = RecordCreate(patient_id=patient_id, **merged)
        schema = DATA_SCHEMAS.get(checked.record_type)
        if schema:
            schema(**checked.data)
    except ValidationError as e:
        err_msgs = [".".join(str(x) for x in err["loc"]) + ": " + err["msg"] for err in e.errors()]
        raise HTTPException(status_code=422, detail=f"Validation failed: {', '.join(err_msgs)}")

    corrected = checked.model_dump(include=set(CORRECTABLE_FIELDS))
    # Who may see a record is not content: a correction keeps the original's level.
    corrected["access_level"] = original.get("access_level", access_policy.PRACTITIONER_ONLY)
    corrected["record_type_label"] = RECORD_TYPES[checked.record_type]
    corrected["patient_id"] = patient_id
    corrected["created_by"] = u["username"]
    corrected["created_at"] = datetime.now(timezone.utc).isoformat()

    cmd = AddCorrectionCommand(
        patient_id=patient_id,
        block_index=block_index,
        corrected_data=corrected,
        username=u["username"],
        reason=req.reason,
    )
    correction_block = command_handler.handle_add_correction(cmd)

    storage.append_access_log(
        project_name=project_name_for(patient_id),
        username=u["username"],
        action="RECORD_CORRECTED",
        device_id=get_device_id(),
        extra={"block_index": block_index, "correction_index": correction_block.index,
               "reason": req.reason, "client_ip": _get_client_ip(request)},
        db_manager=db_manager,
    )

    return {
        "success": True,
        "corrected_block_index": block_index,
        "correction_block_index": correction_block.index,
        "message": "Correction appended. The original record remains on the chain.",
    }


@router.get("/offchain/download/{patient_id}/{block_index}", summary="Download Off-chain File")
def download_offchain_file(
    patient_id: str,
    block_index: int,
    request: Request,
    password: Optional[str] = None,
    u: dict = Depends(current_user),
    record_service: RecordService = Depends(get_record_service),
    attachments: AttachmentStore = Depends(get_attachment_store)
):
    check_patient_id(patient_id)
    _enforce_privileged_dual_control(request, u, patient_id)
    _require_file_access(u, patient_id)
    denied = HTTPException(403, "Access denied to this file.")

    # Checked BEFORE decrypting, so this endpoint cannot be used to test
    # passwords on a record the user may not see.
    meta = record_service.get_block_data(patient_id, block_index, username=u["username"])
    if meta is None:
        raise HTTPException(404, "Record not found")
    if not access_policy.can_view_stored(u["role"], meta):
        raise denied

    try:
        data = record_service.get_final_block_data(patient_id, block_index, password=password, username=u["username"])
        if isinstance(data, str) and ("SECURE" in data or "INCORRECT" in data or "ERROR" in data):
            raise HTTPException(400, f"Decryption failed: {data}")

        # Checked again AFTER decrypting, when the real access level is known.
        if not access_policy.can_view(u["role"], data):
            raise denied
        if not isinstance(data, dict) or not data.get("file_hash"):
            raise HTTPException(404, "File not found or not stored off-chain")

        file_hash = data["file_hash"]
        file_salt = base64.b64decode(data["file_salt"])
        file_pwd = data["file_pwd"]
        file_name = data.get("file_name", "download")
        file_type = data.get("file_type", "application/octet-stream")

        # Check cache/legacy local off-chain store first
        enc_data_b64 = None
        legacy_path = os.path.join(_PROJECT_ROOT, "backend", "offchain_storage", file_hash)
        if os.path.exists(legacy_path):
            with open(legacy_path, "r", encoding="utf-8") as f:
                enc_data_b64 = f.read()
        else:
            try:
                enc_data_b64 = attachments.get(file_hash)
            except Exception as e:
                raise HTTPException(404, f"Encrypted attachment not found: {str(e)}")

        decrypted_b64 = decrypt_data(enc_data_b64, file_pwd, file_salt)
        file_bytes = base64.b64decode(decrypted_b64)

        return Response(
            content=file_bytes,
            media_type=file_type,
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Off-chain download error: {str(e)}")


@router.get("/proof/{patient_id}/{block_index}", summary="Generate Merkle Inclusion Proof for Block")
def get_merkle_proof_endpoint(
    patient_id: str = Path(...),
    block_index: int = Path(...),
    u: dict = Depends(current_user),
    record_service: RecordService = Depends(get_record_service),
):
    # A proof holds no record content, but it confirms that a block exists and
    # when the chain last changed — so it follows the same rules as reading.
    check_patient_id(patient_id)
    _require_file_access(u, patient_id)
    if u["role"] == "practitioner":
        stored = record_service.get_final_block_data(patient_id, block_index, password=None, username=u["username"])
        if stored is None or not access_policy.can_view_stored(u["role"], stored):
            raise HTTPException(404, f"Block #{block_index} not found in chain for patient {patient_id}")

    project_name = record_service._get_project_name(patient_id)
    chain = record_service.block_repo.load_all_blocks(project_name)
    if not chain:
        raise HTTPException(404, f"No blockchain record chain found for patient {patient_id}")

    target_block = None
    target_idx_in_hashes = -1
    hashes = []
    for idx, b in enumerate(chain):
        if b.hash:
            hashes.append(b.hash)
            if b.index == block_index:
                target_block = b
                target_idx_in_hashes = len(hashes) - 1

    if not target_block or target_idx_in_hashes == -1:
        raise HTTPException(404, f"Block #{block_index} not found in chain for patient {patient_id}")

    from core.utils.crypto_utils import generate_merkle_proof, verify_merkle_proof
    proof_result = generate_merkle_proof(hashes, target_idx_in_hashes)
    root = proof_result["root"]
    proof = proof_result["proof"]
    is_valid = verify_merkle_proof(target_block.hash, proof, root) if root else False

    return {
        "patient_id": patient_id,
        "block_index": block_index,
        "block_hash": target_block.hash,
        "merkle_root": f"0x{root}" if root else None,
        "proof": proof,
        "is_valid": is_valid
    }
