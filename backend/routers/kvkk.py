"""
backend/routers/kvkk.py — privacy notice, data export, erasure requests
=======================================================================
The client's KVKK rights as screens rather than emails to the practice. The
rules live in core/services/kvkk.py. Carrying out an erasure stays where it
was — POST /api/v1/erasure/{id}, gated by dual control; a request can only be
closed as done once that has happened.
"""

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.dependencies import (
    _get_client_ip, current_user, get_audit_service, get_query_handler, require_role,
)
from core.cqrs.queries import GetConsentsQuery, GetPatientRecordsQuery, QueryHandler
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book, kvkk
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


# ── Privacy notice ────────────────────────────────────────────
@router.get("/notice", summary="The privacy notice, and whether I accepted it")
def get_notice(u: dict = Depends(current_user)):
    return {"version": kvkk.NOTICE_VERSION, "text": kvkk.NOTICE_TEXT,
            "accepted_at": _iso(kvkk.accepted_at(u["username"]))}


@router.post("/notice/accept", summary="Accept the privacy notice and give explicit consent")
def accept_notice(request: Request, u: dict = Depends(require_role("client"))):
    accepted = kvkk.accept(u["username"], _get_client_ip(request))
    _audit("KVKK_NOTICE_ACCEPTED", u, version=kvkk.NOTICE_VERSION)
    return {"version": kvkk.NOTICE_VERSION, "accepted_at": _iso(accepted)}


# ── Data export ───────────────────────────────────────────────
@router.get("/export", summary="Download a copy of my data (KVKK Art. 11)")
def export_my_data(
    u: dict = Depends(require_role("client")),
    query_handler: QueryHandler = Depends(get_query_handler),
    audit_service: AuditService = Depends(get_audit_service),
):
    """Everything the client can see about themselves, in one JSON file:
    the records visible to them (locked ones stay locked), appointments,
    the consents they gave and who accessed their records."""
    pid = u.get("patient_id")
    records = query_handler.handle_get_patient_records(
        GetPatientRecordsQuery(patient_id=pid, requester_username=u["username"], requester_role="client"))
    appointments = appointment_book.list_appointments(patient_id=pid, start=0, end=time.time() + 5 * 365 * 86400)
    export = {
        "exported_at": _iso(time.time()),
        "about": "A copy of your data in Mahrem (KVKK Art. 11). Locked records stay locked: "
                 "open them in the app with their password.",
        "account": {"username": u["username"], "full_name": u.get("full_name"), "client_id": pid},
        "privacy_notice": {"version": kvkk.NOTICE_VERSION, "accepted_at": _iso(kvkk.accepted_at(u["username"]))},
        "records": [
            {k: r.get(k) for k in ("block_index", "timestamp_iso", "record_type", "title", "record_date",
                                   "access_level", "doctor_name", "institution", "data", "notes",
                                   "is_protected", "is_corrected")}
            for r in records
        ],
        "appointments": [
            {"starts_at": _iso(a["starts_at"]), "duration_min": a["duration_min"],
             "session_format": a["session_format"], "status": a["status"]}
            for a in appointments
        ],
        "consents_given": query_handler.handle_get_consents(GetConsentsQuery(patient_id=pid)),
        "access_log": audit_service.get_access_logs(pid, 1000, 0, "db"),
        "erasure_requests": [_request_view(r) for r in kvkk.list_requests(patient_id=pid)],
    }
    _audit("KVKK_DATA_EXPORTED", u, patient_id=pid)
    filename = f"mahrem-export-{pid}-{datetime.now(timezone.utc):%Y%m%d}.json"
    return Response(content=json.dumps(export, ensure_ascii=False, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── Erasure requests ──────────────────────────────────────────
@router.post("/erasure-requests", summary="Ask for my data to be erased")
def request_erasure(u: dict = Depends(require_role("client"))):
    try:
        req = kvkk.request_erasure(u.get("patient_id"), u["username"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    _audit("KVKK_ERASURE_REQUESTED", u, patient_id=req["patient_id"])
    return {"request": _request_view(req)}


@router.get("/erasure-requests", summary="Erasure requests (own, or all for operators)")
def list_erasure_requests(u: dict = Depends(current_user)):
    if u["role"] == "client":
        items = kvkk.list_requests(patient_id=u.get("patient_id"))
    elif u["role"] in OPERATORS:
        items = kvkk.list_requests()
    else:
        raise HTTPException(403, "Only the client or an operator can see erasure requests")
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
