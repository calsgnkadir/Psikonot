"""
backend/routers/kvkk.py — a client's data export and erasure requests
=====================================================================
Clients do not sign in, so the practitioner acts for them: downloads a copy of
the client's data to hand over (KVKK Art. 11) and files the client's erasure
request (Art. 17). Operators then carry the erasure out — POST
/api/v1/erasure/{id}, gated by dual control — and a request can only be closed
as done once that has happened. The rules live in core/services/kvkk.py.
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.dependencies import current_user, get_audit_service, get_query_handler, require_role
from backend.routers.clients import _view as client_view
from backend.routers.records import _require_file_access, check_patient_id
from backend.schemas.requests import ErasureRequestReq
from core.cqrs.queries import GetPatientRecordsQuery, QueryHandler
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book, client_registry, kvkk
from core.services.audit_service import AuditService
from core.services.erasure_service import get_erasure_key_store

router = APIRouter(prefix="/api/v1/kvkk", tags=["kvkk"])

OPERATORS = ("admin", "security_officer")


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _audit(action: str, u: dict, **extra) -> None:
    event_bus.publish(SystemAuditEvent(project_name="__system__", action=action,
                                       username=u["username"], device_id=get_device_id(), extra=extra))


def _request_view(r: dict) -> dict:
    return {**r, "requested_at": _iso(r["requested_at"]), "handled_at": _iso(r["handled_at"])}


def _own_client(u: dict, patient_id: str) -> dict:
    """The practitioner's own client's card; anyone else's answers 403, the
    same as the record endpoints."""
    check_patient_id(patient_id)
    _require_file_access(u, patient_id)
    return client_registry.get(patient_id)


# ── Data export ───────────────────────────────────────────────
@router.get("/export/{patient_id}", summary="Download a copy of a client's data (KVKK Art. 11)")
def export_client_data(
    patient_id: str,
    request: Request,
    u: dict = Depends(require_role("practitioner")),
    query_handler: QueryHandler = Depends(get_query_handler),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Everything Mahrem holds about one client, in one JSON file the
    practitioner can hand over: the card, the records (locked ones stay
    locked), the appointments and who accessed the file."""
    card = _own_client(u, patient_id)
    records = query_handler.handle_get_patient_records(
        GetPatientRecordsQuery(patient_id=patient_id, requester_username=u["username"],
                               requester_role=u["role"]))
    appointments = appointment_book.list_appointments(patient_id=patient_id, start=0,
                                                      end=time.time() + 5 * 365 * 86400)
    export = {
        "exported_at": _iso(time.time()),
        "exported_by": u["username"],
        "about": "A copy of this client's data in Mahrem (KVKK Art. 11). Locked records stay locked: "
                 "open them in the app with their password.",
        "client": client_view(card, appointment_book.tally(u["username"]).get(patient_id)),
        "records": [
            {k: r.get(k) for k in ("block_index", "timestamp_iso", "record_type", "title", "record_date",
                                   "doctor_name", "institution", "data", "notes", "is_protected", "is_corrected")}
            for r in records
        ],
        "appointments": [
            {"starts_at": _iso(a["starts_at"]), "duration_min": a["duration_min"],
             "session_format": a["session_format"], "status": a["status"]}
            for a in appointments
        ],
        "access_log": audit_service.get_access_logs(patient_id, 1000, 0, "db"),
        "erasure_requests": [_request_view(r) for r in kvkk.list_requests(patient_id=patient_id)],
    }
    _audit("KVKK_DATA_EXPORTED", u, patient_id=patient_id)
    filename = f"mahrem-export-{patient_id}-{datetime.now(timezone.utc):%Y%m%d}.json"
    return Response(content=json.dumps(export, ensure_ascii=False, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Erasure requests ──────────────────────────────────────────
@router.post("/erasure-requests", summary="File a client's erasure request (practitioner)")
def request_erasure(req: ErasureRequestReq, u: dict = Depends(require_role("practitioner"))):
    _own_client(u, req.patient_id)
    try:
        filed = kvkk.request_erasure(req.patient_id, u["username"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    _audit("KVKK_ERASURE_REQUESTED", u, patient_id=req.patient_id)
    return {"request": _request_view(filed)}


@router.get("/erasure-requests", summary="Erasure requests (own, or all for operators)")
def list_erasure_requests(u: dict = Depends(current_user)):
    if u["role"] == "practitioner":
        items = kvkk.list_requests(requested_by=u["username"])
    elif u["role"] in OPERATORS:
        items = kvkk.list_requests()
    else:
        raise HTTPException(403, "Only practitioners and operators can see erasure requests")
    return {"requests": [_request_view(r) for r in items]}


@router.post("/erasure-requests/{request_id}/{status}", summary="Close an erasure request (operator)")
def close_erasure_request(request_id: str, status: str, u: dict = Depends(require_role(*OPERATORS))):
    req = kvkk.get_request(request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    if req["status"] != kvkk.OPEN:
        raise HTTPException(409, f"This request is already {req['status']}")
    if status not in (kvkk.DONE, kvkk.REJECTED):
        raise HTTPException(422, "Status must be done or rejected")
    # "Done" must be true: the client's key has actually been destroyed, which
    # only the dual-control-gated erasure endpoint can do.
    if status == kvkk.DONE and get_erasure_key_store().exists(req["patient_id"]):
        raise HTTPException(409, "Erase the client first (POST /api/v1/erasure/{id}, needs dual control)")
    req = kvkk.close_request(request_id, status, u["username"])
    _audit("KVKK_ERASURE_REQUEST_" + status.upper(), u, request_id=request_id, patient_id=req["patient_id"])
    return {"request": _request_view(req)}
