"""
backend/routers/appointments.py — the appointment book
======================================================
Practitioners and their secretaries book, move and close appointments for the
practitioner's clients. The rules live in core/services/appointment_book.py;
this router only decides whose book a request may touch.

A secretary sees a client's name, ID and appointment times — nothing from the
client's file. The record endpoints refuse the "secretary" role outright
(access_policy.RECORD_ROLES), so running the book never opens a record.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import current_user
from backend.schemas.requests import BookAppointmentReq, UpdateAppointmentReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book as book
from core.services import client_registry

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])

DAY = 86400
MAX_RANGE_DAYS = 366


def book_owner(u: dict) -> str:
    """The practitioner whose book this practitioner or secretary runs."""
    owner = book.practitioner_for(u)
    if not owner:
        if u.get("role") == "secretary":
            raise HTTPException(403, "This secretary account is not linked to a practitioner.")
        raise HTTPException(403, "Only a practitioner or their secretary can manage appointments.")
    return owner


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _names(owner: str) -> dict:
    return {c["patient_id"]: c["full_name"] for c in client_registry.list_for(owner)}


def _view(a: dict, names: dict) -> dict:
    return {
        "id": a["id"],
        "patient_id": a["patient_id"],
        "client_name": names.get(a["patient_id"], a["patient_id"]),
        "starts_at": _iso(a["starts_at"]),
        "ends_at": _iso(a["starts_at"] + a["duration_min"] * 60),
        "duration_min": a["duration_min"],
        "session_format": a["session_format"],
        "status": a["status"],
    }


def _audit(action: str, u: dict, a: dict) -> None:
    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action=action, username=u["username"],
        device_id=get_device_id(),
        extra={"appointment_id": a["id"], "practitioner": a["practitioner_username"], "status": a["status"]},
    ))


def _refuse(e: book.BookingError):
    raise HTTPException(e.status, str(e))


@router.get("", summary="Appointments in my book")
def list_appointments(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    u: dict = Depends(current_user),
):
    owner = book_owner(u)
    now = time.time()
    t0 = start.timestamp() if start else now - 30 * DAY
    t1 = end.timestamp() if end else now + 60 * DAY
    if not 0 < t1 - t0 <= MAX_RANGE_DAYS * DAY:
        raise HTTPException(422, f"The range must be positive and at most {MAX_RANGE_DAYS} days")
    items = book.list_appointments(practitioner=owner, start=t0, end=t1)
    names = _names(owner)
    return {"appointments": [_view(a, names) for a in items]}


@router.post("", summary="Book an appointment")
def book_appointment(req: BookAppointmentReq, u: dict = Depends(current_user)):
    owner = book_owner(u)
    if client_registry.owner_of(req.patient_id) != owner:
        raise HTTPException(404, "Not one of this practitioner's clients")
    try:
        a = book.book(practitioner=owner, patient_id=req.patient_id, starts_at=req.starts_at.timestamp(),
                      duration_min=req.duration_min, session_format=req.session_format,
                      created_by=u["username"])
    except book.BookingError as e:
        _refuse(e)
    _audit("APPOINTMENT_BOOKED", u, a)
    return {"appointment": _view(a, _names(owner))}


@router.patch("/{appointment_id}", summary="Move an appointment or change its status")
def update_appointment(
    appointment_id: str,
    req: UpdateAppointmentReq,
    u: dict = Depends(current_user),
):
    owner = book_owner(u)
    a = book.get(appointment_id)
    # Someone else's appointment gets the same answer as a missing one.
    if a is None or a["practitioner_username"] != owner:
        raise HTTPException(404, "Appointment not found")

    try:
        if req.starts_at is not None or req.duration_min is not None:
            a = book.reschedule(a, by=u["username"],
                                starts_at=req.starts_at.timestamp() if req.starts_at else a["starts_at"],
                                duration_min=req.duration_min or a["duration_min"])
            _audit("APPOINTMENT_MOVED", u, a)
        if req.status is not None:
            a = book.set_status(a, req.status, by=u["username"])
            _audit("APPOINTMENT_" + req.status.upper(), u, a)
    except book.BookingError as e:
        _refuse(e)
    return {"appointment": _view(a, _names(owner))}
