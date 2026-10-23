"""
backend/routers/intake.py — the center's intake inbox and waitlist
==================================================================
Applications from people who want to become clients, before a client card
exists. The rules — fixed codes, status flow, conversion, retention — live in
core/services/intake.py; this router decides who may see and touch which
application:

  * a secretary: unassigned applications and those assigned to a practitioner
    they are linked to, and may assign only to such a practitioner;
  * a practitioner: those assigned to them and unassigned ones that prefer
    them, and may assign only to themselves;
  * operators (admin, security officer, auditor): the counts per status, never
    a name — as they never see a client card.

An application someone may not see gets the same 404 as a missing one. The
audit trail names applications and clients by ID only.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import current_user
from backend.schemas.requests import ApplicationReq, ApplicationStatusReq, ApplicationUpdateReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book, client_registry, intake
from core.services.access_policy import OPERATOR_ROLES

router = APIRouter(prefix="/api/v1/applications", tags=["intake"])

STAFF_ROLES = ("secretary", "practitioner")
NOT_STAFF = "Only the front desk and practitioners work with applications."
NOT_FOUND = "Application not found"
NOT_YOUR_BOOK = "You can assign only to a practitioner whose book you run"


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _staff(u: dict) -> dict:
    if u.get("role") not in STAFF_ROLES:
        raise HTTPException(403, NOT_STAFF)
    return u


def _load(u: dict, app_id: str) -> dict:
    app = intake.get(app_id)
    if not intake.can_see(u, app):
        raise HTTPException(404, NOT_FOUND)
    return app


def _view(a: dict) -> dict:
    full_name = a["full_name"]
    client_id = a["client_id"] if a["client_id"] != intake._PENDING else None
    if client_id:
        # The card holds the name now; the application keeps only the link.
        card = client_registry.get(client_id)
        full_name = card["full_name"] if card else None
    waiting = None
    if a["status"] in intake.OPEN:
        waiting = max(0, int((time.time() - a["received_at"]) // intake.DAY))
    return {
        "id": a["id"],
        "received_at": _iso(a["received_at"]),
        "channel": a["channel"],
        "full_name": full_name,
        "phone": a["phone"],
        "email": a["email"],
        "preferred_practitioner": a["preferred_practitioner"],
        "assigned_practitioner": a["assigned_practitioner"],
        "session_format": a["session_format"],
        "preferred_days": a["preferred_days"],
        "preferred_times": a["preferred_times"],
        "priority": a["priority"],
        "status": a["status"],
        "close_reason": a["close_reason"],
        "contact_attempts": a["contact_attempts"],
        "last_contact_at": _iso(a["last_contact_at"]),
        "client_id": client_id,
        "waiting_days": waiting,
        "created_by": a["created_by"],
        "created_at": _iso(a["created_at"]),
        "updated_by": a["updated_by"],
        "updated_at": _iso(a["updated_at"]),
    }


def _audit(action: str, u: dict, app_id: str, **extra) -> None:
    # IDs and codes only: never the applicant's name, phone or e-mail.
    event_bus.publish(SystemAuditEvent(project_name="__system__", action=action, username=u["username"],
                                       device_id=get_device_id(), extra={"application_id": app_id, **extra}))


def _refuse(e: intake.IntakeError):
    raise HTTPException(e.status, str(e))


def _check_people(u: dict, changes: dict) -> None:
    """The preferred practitioner must exist; the assigned one must also be
    someone whose book the caller runs (a practitioner: only themselves)."""
    for field in ("preferred_practitioner", "assigned_practitioner"):
        username = changes.get(field)
        if username is not None and not intake.is_practitioner(username):
            raise HTTPException(422, "No such practitioner")
    assigned = changes.get("assigned_practitioner")
    if assigned is not None and assigned not in appointment_book.books_for(u):
        raise HTTPException(403, NOT_YOUR_BOOK)


# ── Reading ───────────────────────────────────────────────────
@router.get("", summary="The waitlist: applications I may see")
def list_applications(
    status: str = "open",
    practitioner: Optional[str] = None,
    session_format: Optional[str] = None,
    day: Optional[str] = None,
    time_of_day: Optional[str] = None,
    u: dict = Depends(current_user),
):
    """High priority first, then the oldest. `status` is open (the default:
    new, contacted, waitlisted), all, or one status."""
    _staff(u)
    allowed = {"status": ("open", "all") + intake.STATUSES, "session_format": intake.FORMATS,
               "day": intake.DAYS, "time_of_day": intake.TIMES}
    given = {"status": status, "session_format": session_format, "day": day, "time_of_day": time_of_day}
    for name, value in given.items():
        if value is not None and value not in allowed[name]:
            raise HTTPException(422, f"Unknown {name.replace('_', ' ')}")
    intake.maybe_purge()
    items = intake.list_visible(u, status=status, practitioner=practitioner, session_format=session_format,
                                day=day, time_of_day=time_of_day)
    return {"applications": [_view(a) for a in items], "retention_days": intake.retention_days()}


@router.get("/summary", summary="How many applications per status")
def summary(u: dict = Depends(current_user)):
    """Staff: the applications they see (the menu badge). Operators: the whole
    center, as numbers only."""
    if u.get("role") in OPERATOR_ROLES:
        return {"counts": intake.counts()}
    _staff(u)
    return {"counts": intake.counts(u)}


@router.get("/practitioners", summary="The center's practitioners, for the preferred / assigned pickers")
def practitioners(u: dict = Depends(current_user)):
    _staff(u)
    mine = set(appointment_book.books_for(u))
    return {"practitioners": [dict(p, assignable=p["username"] in mine) for p in intake.active_practitioners()]}


@router.get("/{app_id}", summary="One application")
def get_application(app_id: str, u: dict = Depends(current_user)):
    _staff(u)
    return {"application": _view(_load(u, app_id))}


# ── Writing ───────────────────────────────────────────────────
@router.post("", summary="Record a new application")
def create_application(req: ApplicationReq, u: dict = Depends(current_user)):
    _staff(u)
    data = req.model_dump()
    # A practitioner's own new application is theirs unless they say otherwise.
    if u["role"] == "practitioner" and "assigned_practitioner" not in req.model_fields_set:
        data["assigned_practitioner"] = u["username"]
    _check_people(u, data)
    try:
        app = intake.create(data, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_CREATED", u, app["id"], channel=app["channel"],
           assigned_practitioner=app["assigned_practitioner"])
    return {"application": _view(app)}


@router.patch("/{app_id}", summary="Change an application")
def update_application(app_id: str, req: ApplicationUpdateReq, u: dict = Depends(current_user)):
    _staff(u)
    app = _load(u, app_id)
    changes = req.model_dump(exclude_unset=True)
    for field in ("channel", "full_name", "session_format", "preferred_days", "preferred_times", "priority"):
        if field in changes and changes[field] is None:
            raise HTTPException(422, "This field cannot be empty")
    _check_people(u, changes)
    try:
        app = intake.update(app, changes, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_UPDATED", u, app_id, fields=sorted(changes),
           assigned_practitioner=app["assigned_practitioner"])
    return {"application": _view(app)}


@router.post("/{app_id}/status", summary="Contacted, waitlisted, declined or withdrawn")
def set_status(app_id: str, req: ApplicationStatusReq, u: dict = Depends(current_user)):
    _staff(u)
    app = _load(u, app_id)
    try:
        app = intake.set_status(app, req.status, reason=req.reason, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_STATUS_CHANGED", u, app_id, status=app["status"], reason=app["close_reason"])
    return {"application": _view(app)}


@router.post("/{app_id}/contact-attempt", summary="We tried to reach the applicant")
def contact_attempt(app_id: str, u: dict = Depends(current_user)):
    _staff(u)
    app = _load(u, app_id)
    try:
        app = intake.record_contact_attempt(app, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_CONTACT_ATTEMPT", u, app_id, attempts=app["contact_attempts"])
    return {"application": _view(app)}


@router.post("/{app_id}/convert", summary="Create the client card for the assigned practitioner")
def convert(app_id: str, u: dict = Depends(current_user)):
    _staff(u)
    app = _load(u, app_id)
    try:
        app, card = intake.convert(app, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_CONVERTED", u, app_id, patient_id=card["patient_id"],
           practitioner=card["practitioner_username"])
    return {"application": _view(app),
            "client": {"patient_id": card["patient_id"], "full_name": card["full_name"],
                       "practitioner": card["practitioner_username"]}}


@router.post("/{app_id}/scheduled", summary="The first appointment is booked")
def scheduled(app_id: str, u: dict = Depends(current_user)):
    """Called after the first appointment is booked through the appointment
    book (POST /api/v1/appointments). Checked against the book."""
    _staff(u)
    app = _load(u, app_id)
    try:
        app = intake.mark_scheduled(app, by=u["username"])
    except intake.IntakeError as e:
        _refuse(e)
    _audit("APPLICATION_STATUS_CHANGED", u, app_id, status=app["status"], patient_id=app["client_id"])
    return {"application": _view(app)}
