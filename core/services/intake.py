"""
core/services/intake.py — a center's intake inbox and waitlist
==============================================================
Someone who wants to become a client calls, writes, walks in or is referred.
Until a client card exists (core/services/client_registry.py) their request is
an *application*: who they are, how to reach them, whom and when they would
like to see, and where the request stands.

No clinical text, by design
  This table is not encrypted like a client's records, and the people who fill
  it in are the front desk. So every field is a fixed code — the channel, the
  session format, the preferred days and times of day, the priority, the
  status and the reason an application was declined — and there is no note
  field at all. The only free text is the applicant's name, which is
  restricted to letters, spaces and . ' - (no digits, brackets or colons), so
  "Ayşe (panik)" is refused. A name field cannot stop every word; that residual
  risk is in docs/THREAT_MODEL.md.

Status
  new -> contacted -> waitlisted, and from any of those to declined (with a
  reason code) or withdrawn. An application becomes a client card ("convert",
  for its assigned practitioner) while it is open, and turns "scheduled" once
  the first appointment is in that practitioner's book: booked from the
  application (book_first: the appointment and "scheduled" in one
  transaction, so a second click books nothing). mark_scheduled() is left for an
  application whose appointment is already in the book. declined, withdrawn,
  scheduled and lapsed (see below) are final.

Who sees an application (the router enforces it with visible_to / can_see)
  * a secretary: unassigned ones (the center's shared inbox) and those
    assigned to a practitioner they are linked to (practice_staff, read on
    every request);
  * a practitioner: those assigned to them, and unassigned ones that name
    them as the preferred practitioner;
  * nobody else. Operators (admin, security officer, auditor) never see a
    client card, so they see no applicant either — only counts per status.

KVKK: keep personal data only as long as it is needed
  * on conversion the name, phone and e-mail move to the client card and are
    removed here: the application keeps the client ID and its dates;
  * declined / withdrawn applications, and open ones nobody touched, lose
    their name, phone and e-mail after VHV_INTAKE_RETENTION_DAYS (90 by
    default). An open one is closed as "lapsed". The anonymous rest (channel,
    dates, status, codes) stays for the counts. purge_expired() runs at
    startup and, at most once an hour, when someone reads the waitlist — one
    process, so a module-level timestamp is enough (ADR-0002).

SQL: fixed statements, every value a bound parameter (run_sql).
"""

import os
import time
import uuid
from typing import Optional

from core.services import appointment_book, client_registry
from database.sql_db import run_sql as _run
from database.sql_db import to_placeholder

CHANNELS = ("phone", "email", "walk_in", "referral")
FORMATS = ("In-person", "Online", "either")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIMES = ("morning", "afternoon", "evening")
PRIORITIES = ("normal", "high")
REASONS = ("no_capacity", "outside_scope", "no_answer", "applicant_withdrew", "other")

OPEN = ("new", "contacted", "waitlisted")
CLOSED = ("scheduled", "declined", "withdrawn", "lapsed")
STATUSES = OPEN + CLOSED

# The moves a person makes by hand. "scheduled" comes only from booking the
# first appointment (book_first, mark_scheduled) and
# "lapsed" only from the purge.
TRANSITIONS = {
    "new": ("contacted", "waitlisted", "declined", "withdrawn"),
    "contacted": ("waitlisted", "declined", "withdrawn"),
    "waitlisted": ("declined", "withdrawn"),
}

DEFAULT_RETENTION_DAYS = 90
PURGE_EVERY = 3600          # seconds between purges triggered by reads
DAY = 86400
_PENDING = "PENDING"        # client_id while a conversion is under way

_COLUMNS = ("id", "received_at", "channel", "full_name", "phone", "email", "preferred_practitioner",
            "assigned_practitioner", "session_format", "preferred_days", "preferred_times", "priority",
            "status", "close_reason", "contact_attempts", "last_contact_at", "client_id", "created_by",
            "created_at", "updated_by", "updated_at", "last_activity_at", "closed_at", "purged_at")
# Fixed SQL text: every value is a bound parameter.
_SELECT = ("SELECT id, received_at, channel, full_name, phone, email, preferred_practitioner, "
           "assigned_practitioner, session_format, preferred_days, preferred_times, priority, "
           "status, close_reason, contact_attempts, last_contact_at, client_id, created_by, "
           "created_at, updated_by, updated_at, last_activity_at, closed_at, purged_at FROM "
           "applications")
_INSERT = ("INSERT INTO applications (id, received_at, channel, full_name, phone, email, "
           "preferred_practitioner, assigned_practitioner, session_format, preferred_days, "
           "preferred_times, priority, status, close_reason, contact_attempts, last_contact_at, "
           "client_id, created_by, created_at, updated_by, updated_at, last_activity_at, closed_at, "
           "purged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
           "?)")

_last_purge = 0.0


class IntakeError(ValueError):
    """A request the intake refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _codes(value) -> list:
    return [v for v in (value or "").split(",") if v]


def _row(row) -> Optional[dict]:
    if not row:
        return None
    d = {col: row[i] for i, col in enumerate(_COLUMNS)}
    d["preferred_days"] = _codes(d["preferred_days"])
    d["preferred_times"] = _codes(d["preferred_times"])
    return d


def _csv(codes, allowed) -> str:
    """Canonical order, no duplicates, known codes only."""
    codes = list(codes or [])
    unknown = [c for c in codes if c not in allowed]
    if unknown:
        raise IntakeError(f"Unknown code: {unknown[0]}")
    return ",".join(c for c in allowed if c in codes)


# ── Practitioners ─────────────────────────────────────────────
def active_practitioners() -> list:
    """Every practitioner account in use, for the preferred / assigned pickers."""
    rows = _run("SELECT username, full_name FROM users WHERE role = ? "
                "AND COALESCE(account_status, ?) = ? ORDER BY full_name",
                ("practitioner", "ACTIVE_ENROLLED", "ACTIVE_ENROLLED"), fetch="all")
    return [{"username": r[0], "full_name": r[1]} for r in rows]


def is_practitioner(username: Optional[str]) -> bool:
    if not username:
        return False
    rows = _run("SELECT 1 FROM users WHERE username = ? AND role = ? AND COALESCE(account_status, ?) = ?",
                (username, "practitioner", "ACTIVE_ENROLLED", "ACTIVE_ENROLLED"), fetch="one")
    return bool(rows and rows[0])


# ── Who sees what ─────────────────────────────────────────────
def can_see(user: dict, app: Optional[dict]) -> bool:
    """May this user see and work on this application? A purged one is
    nobody's any more."""
    if not app or app.get("purged_at") is not None:
        return False
    role, username = user.get("role"), user.get("username")
    assigned = app.get("assigned_practitioner")
    if role == "secretary":
        return assigned is None or appointment_book.is_linked(username, assigned)
    if role == "practitioner":
        return assigned == username or (assigned is None and app.get("preferred_practitioner") == username)
    return False


_VISIBLE_SQL = {
    "secretary": ("SELECT id, received_at, channel, full_name, phone, email, preferred_practitioner, "
                  "assigned_practitioner, session_format, preferred_days, preferred_times, priority, "
                  "status, close_reason, contact_attempts, last_contact_at, client_id, created_by, "
                  "created_at, updated_by, updated_at, last_activity_at, closed_at, purged_at FROM "
                  "applications WHERE purged_at IS NULL AND (assigned_practitioner IS NULL OR "
                  "assigned_practitioner IN (SELECT practitioner_username FROM practice_staff WHERE "
                  "staff_username = ?))"),
    "practitioner": ("SELECT id, received_at, channel, full_name, phone, email, preferred_practitioner, "
                     "assigned_practitioner, session_format, preferred_days, preferred_times, priority, "
                     "status, close_reason, contact_attempts, last_contact_at, client_id, created_by, "
                     "created_at, updated_by, updated_at, last_activity_at, closed_at, purged_at FROM "
                     "applications WHERE purged_at IS NULL AND (assigned_practitioner = ? OR "
                     "(assigned_practitioner IS NULL AND preferred_practitioner = ?))"),
}


def _visible_rows(user: dict) -> list:
    role, username = user.get("role"), user.get("username")
    if role == "secretary":
        rows = _run(_VISIBLE_SQL["secretary"], (username,), fetch="all")
    elif role == "practitioner":
        rows = _run(_VISIBLE_SQL["practitioner"], (username, username), fetch="all")
    else:
        return []
    return [_row(r) for r in rows]


def _matches(app: dict, status, practitioner, session_format, day, time_of_day) -> bool:
    if status == "open" and app["status"] not in OPEN:
        return False
    if status not in ("open", "all") and app["status"] != status:
        return False
    if practitioner and practitioner not in (app["assigned_practitioner"], app["preferred_practitioner"]):
        return False
    # "either", and no preferred day or time at all, mean "any".
    if session_format and app["session_format"] not in (session_format, "either"):
        return False
    if day and app["preferred_days"] and day not in app["preferred_days"]:
        return False
    if time_of_day and app["preferred_times"] and time_of_day not in app["preferred_times"]:
        return False
    return True


def list_visible(user: dict, *, status: str = "open", practitioner: Optional[str] = None,
                 session_format: Optional[str] = None, day: Optional[str] = None,
                 time_of_day: Optional[str] = None) -> list:
    """The waitlist this user may see: high priority first, then the oldest."""
    items = [a for a in _visible_rows(user)
             if _matches(a, status, practitioner, session_format, day, time_of_day)]
    return sorted(items, key=lambda a: (0 if a["priority"] == "high" else 1, a["received_at"]))


def counts(user: Optional[dict] = None) -> dict:
    """Applications per status. With a user: the ones they see. Without (the
    operators' view): the whole center, purged ones included — no names."""
    result = {s: 0 for s in STATUSES}
    if user is None:
        for status, n in _run("SELECT status, COUNT(*) FROM applications GROUP BY status", fetch="all"):
            result[status] = result.get(status, 0) + n
    else:
        for a in _visible_rows(user):
            result[a["status"]] = result.get(a["status"], 0) + 1
    return result


def get(app_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (app_id,), fetch="one")
    return _row(rows[0]) if rows else None


# ── Writing ───────────────────────────────────────────────────
def create(data: dict, *, by: str, received_at: Optional[float] = None) -> dict:
    """A new application. `data` comes validated from the request schema;
    `received_at` is for the demo seed and tests (default: now)."""
    if data.get("channel") not in CHANNELS:
        raise IntakeError("Unknown channel")
    if data.get("session_format", "either") not in FORMATS:
        raise IntakeError("Unknown session format")
    if data.get("priority", "normal") not in PRIORITIES:
        raise IntakeError("Unknown priority")
    if not (data.get("phone") or data.get("email")):
        raise IntakeError("Give a phone number or an e-mail address")
    now = time.time()
    app_id = f"APP-{uuid.uuid4().hex[:12].upper()}"
    values = {
        "id": app_id, "received_at": received_at or now, "channel": data["channel"],
        "full_name": data["full_name"], "phone": data.get("phone"), "email": data.get("email"),
        "preferred_practitioner": data.get("preferred_practitioner"),
        "assigned_practitioner": data.get("assigned_practitioner"),
        "session_format": data.get("session_format", "either"),
        "preferred_days": _csv(data.get("preferred_days"), DAYS),
        "preferred_times": _csv(data.get("preferred_times"), TIMES),
        "priority": data.get("priority", "normal"), "status": "new", "close_reason": None,
        "contact_attempts": 0, "last_contact_at": None, "client_id": None,
        "created_by": by, "created_at": now, "updated_by": None, "updated_at": None,
        "last_activity_at": now, "closed_at": None, "purged_at": None,
    }
    _run(_INSERT, tuple(values[c] for c in _COLUMNS))
    return get(app_id)


EDITABLE = ("channel", "full_name", "phone", "email", "preferred_practitioner", "assigned_practitioner",
            "session_format", "preferred_days", "preferred_times", "priority")
# Held by the client card once the application is converted.
_ON_THE_CARD = ("full_name", "phone", "email", "assigned_practitioner")

# One fixed statement per field: the SQL text never changes, only the values.
_UPDATE_SQL = {
    "channel": ("UPDATE applications SET channel = ?, updated_by = ?, updated_at = ?, "
                "last_activity_at = ? WHERE id = ?"),
    "full_name": ("UPDATE applications SET full_name = ?, updated_by = ?, updated_at = ?, "
                  "last_activity_at = ? WHERE id = ?"),
    "phone": ("UPDATE applications SET phone = ?, updated_by = ?, updated_at = ?, "
              "last_activity_at = ? WHERE id = ?"),
    "email": ("UPDATE applications SET email = ?, updated_by = ?, updated_at = ?, "
              "last_activity_at = ? WHERE id = ?"),
    "preferred_practitioner": ("UPDATE applications SET preferred_practitioner = ?, updated_by = ?, updated_at = ?, "
                               "last_activity_at = ? WHERE id = ?"),
    "assigned_practitioner": ("UPDATE applications SET assigned_practitioner = ?, updated_by = ?, updated_at = ?, "
                              "last_activity_at = ? WHERE id = ?"),
    "session_format": ("UPDATE applications SET session_format = ?, updated_by = ?, updated_at = ?, "
                       "last_activity_at = ? WHERE id = ?"),
    "preferred_days": ("UPDATE applications SET preferred_days = ?, updated_by = ?, updated_at = ?, "
                       "last_activity_at = ? WHERE id = ?"),
    "preferred_times": ("UPDATE applications SET preferred_times = ?, updated_by = ?, updated_at = ?, "
                        "last_activity_at = ? WHERE id = ?"),
    "priority": ("UPDATE applications SET priority = ?, updated_by = ?, updated_at = ?, "
                 "last_activity_at = ? WHERE id = ?"),
}


def _require_open(app: dict) -> None:
    if app["status"] not in OPEN or app.get("purged_at") is not None:
        raise IntakeError("This application is closed", status=409)


def update(app: dict, changes: dict, *, by: str) -> dict:
    """Change the fields in `changes` (only EDITABLE ones) of an open application."""
    _require_open(app)
    if app.get("client_id") and any(f in changes for f in _ON_THE_CARD):
        raise IntakeError("This application is already a client card", status=409)
    merged = {**app, **changes}
    if "full_name" in changes and not changes["full_name"]:
        raise IntakeError("The name cannot be empty")
    if not app.get("client_id") and not (merged.get("phone") or merged.get("email")):
        raise IntakeError("Give a phone number or an e-mail address")
    checks = {"channel": CHANNELS, "session_format": FORMATS, "priority": PRIORITIES}
    for field, allowed in checks.items():
        if field in changes and changes[field] not in allowed:
            raise IntakeError(f"Unknown {field.replace('_', ' ')}")
    now = time.time()
    for field, value in changes.items():
        if field not in _UPDATE_SQL:
            continue
        if field == "preferred_days":
            value = _csv(value, DAYS)
        elif field == "preferred_times":
            value = _csv(value, TIMES)
        _run(_UPDATE_SQL[field], (value, by, now, now, app["id"]))
    return get(app["id"])


def set_status(app: dict, status: str, *, by: str, reason: Optional[str] = None) -> dict:
    if status not in STATUSES:
        raise IntakeError("Unknown status")
    if app.get("purged_at") is not None or status not in TRANSITIONS.get(app["status"], ()):
        raise IntakeError(f"An application cannot go from {app['status']} to {status}", status=409)
    if status == "declined" and reason not in REASONS:
        raise IntakeError("Choose a reason to decline")
    if status == "withdrawn":
        reason = "applicant_withdrew"
    elif status != "declined":
        reason = None
    now = time.time()
    closed_at = now if status in CLOSED else None
    _run("UPDATE applications SET status = ?, close_reason = ?, closed_at = ?, updated_by = ?, updated_at = ?, "
         "last_activity_at = ? WHERE id = ?", (status, reason, closed_at, by, now, now, app["id"]))
    return get(app["id"])


def record_contact_attempt(app: dict, *, by: str) -> dict:
    """Someone tried to reach the applicant (whether or not they answered)."""
    _require_open(app)
    now = time.time()
    _run("UPDATE applications SET contact_attempts = contact_attempts + 1, last_contact_at = ?, "
         "updated_by = ?, updated_at = ?, last_activity_at = ? WHERE id = ?", (now, by, now, now, app["id"]))
    return get(app["id"])


def convert(app: dict, *, by: str) -> tuple:
    """Create the client card for the assigned practitioner, with the
    applicant's name, phone and e-mail, and link it. The application then
    keeps the client ID and its dates, not a second copy of the personal data.

    The application is claimed first with a conditional UPDATE, so two
    simultaneous conversions cannot both create a card. Returns (application, card)."""
    _require_open(app)
    if app.get("client_id"):
        raise IntakeError("This application has already been converted", status=409)
    practitioner = app.get("assigned_practitioner")
    if not practitioner:
        raise IntakeError("Assign the application to a practitioner first")
    claimed = _run("UPDATE applications SET client_id = ? WHERE id = ? AND client_id IS NULL "
                   "AND purged_at IS NULL AND status IN (?, ?, ?)",
                   (_PENDING, app["id"]) + OPEN, fetch="rowcount")
    if claimed != 1:
        raise IntakeError("This application has already been converted", status=409)
    try:
        card = client_registry.add(practitioner=practitioner, full_name=app["full_name"], phone=app["phone"],
                                   email=app["email"], kvkk_signed_on=None, created_by=by)
    except Exception:
        _run("UPDATE applications SET client_id = NULL WHERE id = ? AND client_id = ?", (app["id"], _PENDING))
        raise
    now = time.time()
    _run("UPDATE applications SET client_id = ?, full_name = NULL, phone = NULL, email = NULL, "
         "updated_by = ?, updated_at = ?, last_activity_at = ? WHERE id = ?",
         (card["patient_id"], by, now, now, app["id"]))
    return get(app["id"]), card


def mark_scheduled(app: dict, *, by: str) -> dict:
    """The first appointment is already in the book (booked before booking
    closed applications, or by the service directly): the application is
    done. Checked against the book, not taken on trust."""
    _require_open(app)
    client_id = app.get("client_id")
    if not client_id or client_id == _PENDING:
        raise IntakeError("Create the client card first", status=409)
    now = time.time()
    booked = appointment_book.list_appointments(
        practitioner=app["assigned_practitioner"], patient_id=client_id, start=now - DAY, end=now + 400 * DAY)
    if not any(a["status"] == "scheduled" for a in booked):
        raise IntakeError("Book the first appointment first", status=409)
    _run("UPDATE applications SET status = ?, closed_at = ?, updated_by = ?, updated_at = ?, "
         "last_activity_at = ? WHERE id = ?", ("scheduled", now, by, now, now, app["id"]))
    return get(app["id"])


_SCHEDULE_SQL = ("UPDATE applications SET status = ?, closed_at = ?, updated_by = ?, updated_at = ?, "
                 "last_activity_at = ? WHERE id = ? AND client_id = ? AND purged_at IS NULL "
                 "AND status IN (?, ?, ?)")


def _claim_scheduled(cursor, app_id: str, client_id: str, by: str) -> bool:
    """Close one open, converted application as "scheduled", on the caller's
    connection. False when it is no longer open (closed meanwhile)."""
    now = time.time()
    cursor.execute(to_placeholder(_SCHEDULE_SQL), ("scheduled", now, by, now, now, app_id, client_id) + OPEN)
    return cursor.rowcount == 1


def book_first(app: dict, *, starts_at: float, duration_min: int, session_format: str,
               room_id: Optional[str], by: str) -> tuple:
    """Book the first appointment of a converted application in its assigned
    practitioner's book and close the application as "scheduled", in one
    transaction: the book's own rules (core/services/appointment_book.book),
    then the application is claimed with a conditional UPDATE. If it is no
    longer open — a second click, or someone closed it meanwhile — or
    anything else fails, nothing is booked and the application is left as it
    was. Returns (application, appointment)."""
    _require_open(app)
    client_id = app.get("client_id")
    if not client_id or client_id == _PENDING:
        raise IntakeError("Create the client card first", status=409)

    def claim(cursor):
        if not _claim_scheduled(cursor, app["id"], client_id, by):
            raise IntakeError("This application is closed", status=409)
    appointment = appointment_book.book(
        practitioner=app["assigned_practitioner"], patient_id=client_id, starts_at=starts_at,
        duration_min=duration_min, session_format=session_format, room_id=room_id, created_by=by,
        within=claim)
    return get(app["id"]), appointment


def add_existing(data: dict, *, by: str, received_at: float, status: str = "new",
                 contact_attempts: int = 0, close_reason: Optional[str] = None) -> str:
    """For the demo seed only: an application as it stands, backdated."""
    app_id = create(data, by=by, received_at=received_at)["id"]
    last_contact = received_at + DAY if contact_attempts else None
    closed_at = time.time() if status in CLOSED else None
    _run("UPDATE applications SET status = ?, contact_attempts = ?, last_contact_at = ?, close_reason = ?, "
         "closed_at = ? WHERE id = ?", (status, contact_attempts, last_contact, close_reason, closed_at, app_id))
    return app_id


# ── KVKK: retention ───────────────────────────────────────────
def retention_days() -> int:
    """VHV_INTAKE_RETENTION_DAYS, or 90 when unset or not a positive number."""
    raw = os.getenv("VHV_INTAKE_RETENTION_DAYS", "")
    try:
        days = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def purge_expired(now: Optional[float] = None) -> list:
    """Remove the name, phone and e-mail of applications past the retention
    period: closed (declined / withdrawn) that long ago, or open and untouched
    that long (closed as "lapsed"). Converted ones hold no personal data and
    are left as they are. Returns the IDs purged."""
    global _last_purge
    now = now if now is not None else time.time()
    _last_purge = now
    cutoff = now - retention_days() * DAY
    purged = []

    rows = _run("SELECT id FROM applications WHERE purged_at IS NULL AND status IN (?, ?) AND closed_at < ?",
                ("declined", "withdrawn", cutoff), fetch="all")
    for (app_id,) in rows:
        changed = _run("UPDATE applications SET full_name = NULL, phone = NULL, email = NULL, purged_at = ? "
                       "WHERE id = ? AND purged_at IS NULL AND status IN (?, ?) AND closed_at < ?",
                       (now, app_id, "declined", "withdrawn", cutoff), fetch="rowcount")
        if changed:
            purged.append(app_id)

    rows = _run("SELECT id FROM applications WHERE purged_at IS NULL AND client_id IS NULL "
                "AND status IN (?, ?, ?) AND last_activity_at < ?", OPEN + (cutoff,), fetch="all")
    for (app_id,) in rows:
        changed = _run("UPDATE applications SET full_name = NULL, phone = NULL, email = NULL, status = ?, "
                       "closed_at = ?, purged_at = ? WHERE id = ? AND purged_at IS NULL AND client_id IS NULL "
                       "AND status IN (?, ?, ?) AND last_activity_at < ?",
                       ("lapsed", now, now, app_id) + OPEN + (cutoff,), fetch="rowcount")
        if changed:
            purged.append(app_id)

    if purged:
        from core.events.event_bus import SystemAuditEvent, event_bus
        from core.security import get_device_id
        event_bus.publish(SystemAuditEvent(
            project_name="__system__", action="APPLICATIONS_PURGED", username="system",
            device_id=get_device_id(),
            extra={"application_ids": purged, "count": len(purged), "retention_days": retention_days()},
        ))
    return purged


def maybe_purge() -> None:
    """purge_expired(), at most once every PURGE_EVERY seconds."""
    if time.time() - _last_purge >= PURGE_EVERY:
        purge_expired()
