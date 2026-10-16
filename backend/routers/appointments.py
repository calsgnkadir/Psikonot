"""
backend/routers/appointments.py — the appointment book
======================================================
Practitioners and their secretaries book, move and close appointments for the
practitioner's clients. The rules live in core/services/appointment_book.py;
this router only decides whose book a request may touch.

A secretary may run the books of several practitioners (a center's front
desk). A request for one book then names the practitioner (?practitioner=);
a request about one appointment is checked against the book it is in. The
links are read on every request, so removing one takes effect at once.

A secretary sees a client's name, ID and appointment times — nothing from the
client's file. The record endpoints refuse the "secretary" role outright
(access_policy.RECORD_ROLES), so running the book never opens a record.
"""

import os
import re
import time
from datetime import datetime, time as clock, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.dependencies import current_user
from backend.schemas.requests import BookAppointmentReq, UpdateAppointmentReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book as book
from core.services import client_registry, rooms
from infrastructure.repositories.sql_repositories import SQLUserRepository

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])

DAY = 86400
MAX_RANGE_DAYS = 366

# The day view reads a date in the practice's own timezone (VHV_TIMEZONE).
DEFAULT_TIMEZONE = "Europe/Istanbul"

# One answer for "no such practitioner" and "not one you work for".
NOT_YOUR_BOOK = "You do not run this practitioner's appointment book."


def books_of(u: dict) -> list:
    """Every book this practitioner or secretary runs; 403 if none."""
    books = book.books_for(u)
    if not books:
        if u.get("role") == "secretary":
            raise HTTPException(403, "This secretary account is not linked to a practitioner.")
        raise HTTPException(403, "Only a practitioner or their secretary can manage appointments.")
    return books


def book_owner(u: dict, practitioner: Optional[str] = None) -> str:
    """The one book a request acts on. A secretary who runs several books must
    name it, and whoever names a book must run it."""
    books = books_of(u)
    if practitioner is None:
        if len(books) == 1:
            return books[0]
        raise HTTPException(422, "You run several practitioners' books: choose one (practitioner=<username>).")
    if practitioner not in books:
        raise HTTPException(403, NOT_YOUR_BOOK)
    return practitioner


def server_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("VHV_TIMEZONE") or DEFAULT_TIMEZONE)


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _names(owner: str) -> dict:
    return {c["patient_id"]: c["full_name"] for c in client_registry.list_for(owner)}


def _view(a: dict, names: dict, room_names: Optional[dict] = None) -> dict:
    if room_names is None:
        room_names = rooms.names()
    return {
        "id": a["id"],
        "patient_id": a["patient_id"],
        "client_name": names.get(a["patient_id"], a["patient_id"]),
        "starts_at": _iso(a["starts_at"]),
        "ends_at": _iso(a["starts_at"] + a["duration_min"] * 60),
        "duration_min": a["duration_min"],
        "session_format": a["session_format"],
        "room_id": a.get("room_id"),
        "room_name": room_names.get(a.get("room_id")),
        "status": a["status"],
    }


def _audit(action: str, u: dict, a: dict) -> None:
    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action=action, username=u["username"],
        device_id=get_device_id(),
        extra={"appointment_id": a["id"], "practitioner": a["practitioner_username"], "status": a["status"],
               "room_id": a.get("room_id")},
    ))


def _refuse(e: book.BookingError):
    raise HTTPException(e.status, str(e))


@router.get("", summary="Appointments in my book")
def list_appointments(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    practitioner: Optional[str] = None,
    u: dict = Depends(current_user),
):
    owner = book_owner(u, practitioner)
    now = time.time()
    t0 = start.timestamp() if start else now - 30 * DAY
    t1 = end.timestamp() if end else now + 60 * DAY
    if not 0 < t1 - t0 <= MAX_RANGE_DAYS * DAY:
        raise HTTPException(422, f"The range must be positive and at most {MAX_RANGE_DAYS} days")
    items = book.list_appointments(practitioner=owner, start=t0, end=t1)
    names, room_names = _names(owner), rooms.names()
    return {"appointments": [_view(a, names, room_names) for a in items]}


@router.post("", summary="Book an appointment")
def book_appointment(req: BookAppointmentReq, practitioner: Optional[str] = None,
                     u: dict = Depends(current_user)):
    owner = book_owner(u, practitioner)
    if client_registry.owner_of(req.patient_id) != owner:
        raise HTTPException(404, "Not one of this practitioner's clients")
    try:
        a = book.book(practitioner=owner, patient_id=req.patient_id, starts_at=req.starts_at.timestamp(),
                      duration_min=req.duration_min, session_format=req.session_format,
                      room_id=req.room_id, created_by=u["username"])
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
    books = books_of(u)
    a = book.get(appointment_id)
    # Someone else's appointment gets the same answer as a missing one.
    if a is None or a["practitioner_username"] not in books:
        raise HTTPException(404, "Appointment not found")
    owner = a["practitioner_username"]

    try:
        # room_id sent as null takes the appointment out of its room.
        room_given = "room_id" in req.model_fields_set
        if req.starts_at is not None or req.duration_min is not None or room_given:
            room = {"room_id": req.room_id} if room_given else {}
            a = book.reschedule(a, by=u["username"],
                                starts_at=req.starts_at.timestamp() if req.starts_at else a["starts_at"],
                                duration_min=req.duration_min or a["duration_min"], **room)
            _audit("APPOINTMENT_MOVED", u, a)
        if req.status is not None:
            a = book.set_status(a, req.status, by=u["username"])
            _audit("APPOINTMENT_" + req.status.upper(), u, a)
    except book.BookingError as e:
        _refuse(e)
    return {"appointment": _view(a, _names(owner))}


# ── A center's front desk ─────────────────────────────────────
def _practitioner(username: str) -> dict:
    user = SQLUserRepository().load_user(username)
    return {"username": username, "full_name": user.full_name if user else username}


@router.get("/practitioners", summary="The books I run")
def my_books(u: dict = Depends(current_user)):
    """A practitioner runs their own book, a secretary the book of every
    practitioner they are linked to, anyone else none."""
    return {"practitioners": [_practitioner(p) for p in book.books_for(u)]}


@router.get("/day", summary="One day of every book I run")
def day_view(day: str = Query(..., alias="date", description="YYYY-MM-DD in the practice's timezone"),
             u: dict = Depends(current_user)):
    """The front desk's day: for each book the caller runs, that day's
    appointments — client, time, length, format and whether it happened. The
    day runs from midnight to midnight in the server's timezone (VHV_TIMEZONE,
    Europe/Istanbul by default), whatever the browser's."""
    books = books_of(u)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    try:
        first = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    tz = server_timezone()
    # Each midnight on its own: a day with a clock change lasts 23 or 25 hours.
    t0 = datetime.combine(first, clock.min, tzinfo=tz).timestamp()
    t1 = datetime.combine(first + timedelta(days=1), clock.min, tzinfo=tz).timestamp()
    practitioners = []
    for owner in books:
        names = _names(owner)
        items = book.list_appointments(practitioner=owner, start=t0, end=t1)
        practitioners.append({**_practitioner(owner), "appointments": [_view(a, names) for a in items]})
    return {"date": day, "timezone": tz.key, "practitioners": practitioners}
