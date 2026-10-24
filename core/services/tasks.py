"""
core/services/tasks.py — the staff task list ("İşler")
======================================================
The front desk and the practitioners share a to-do list of practical work:
call someone back, get the KVKK forms signed, ask for a document, confirm an
appointment, follow up an application, prepare a room.

No free text, by design
  This table is not encrypted like a client's records, and the front desk
  writes in it. So a task has no title and no note: it is a fixed type code
  (TYPES), a priority, a due day (and time), a status, and optionally a link
  to exactly ONE item — a client card (CL-...), an application (APP-...) or an
  appointment (APT-...). The interface builds the line it shows from the type
  and the linked item ("Geri ara — Deniz Aydın"). A clinical word has nowhere
  to go; the request schemas refuse any field they do not name.

Whose task it is
  A task is in one practitioner's book (practitioner_username). The
  practitioner sees the tasks in their book; a secretary sees the tasks in
  every book they are linked to (practice_staff, read on every request, so
  unlinking ends access at once); operators see counts only. A linked item
  must be in the same book: the client card's owner, the application's
  assigned practitioner (or, while nobody is assigned, the one it prefers),
  the appointment's practitioner. The assignee is the practitioner, a
  secretary linked to that book, or nobody ("unassigned").

Status
  open -> done / cancelled, and back to open ("reopen").

Automatic tasks (created once each; task_triggers remembers it)
  * a client card created without a KVKK signature date gets a kvkk_forms
    task (on_client_created, called by client_registry.add); entering the date
    on the card closes it (on_kvkk_signed);
  * an application still "new" more than VHV_TASK_FOLLOWUP_HOURS (24) after
    it came in gets a follow_up_application task in the book of its assigned,
    or else preferred, practitioner. An application with neither is in the
    center's shared inbox and gets none. Checked by run_automatic(), at
    startup and at most once an hour when the list is read — one process, so a
    module-level timestamp is enough (ADR-0002).

KVKK
  * erasure: remove_for_client() deletes every task linked to the client's
    card, appointments or converted application (backend/routers/erasure.py);
  * done and cancelled tasks are deleted VHV_TASK_RETENTION_DAYS (180) after
    they were closed (purge_expired, run with the automatic check);
  * the audit trail names tasks and their links by ID only.

SQL: fixed statements, every value a bound parameter (run_sql).
"""

import os
import re
import time
import uuid
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core.services import appointment_book, client_registry, intake
from database.sql_db import run_sql as _run

TYPES = ("call_back", "kvkk_forms", "confirm_appointment", "request_document", "follow_up_application",
         "prepare_room", "other")
PRIORITIES = ("normal", "high")
STATUSES = ("open", "done", "cancelled")
LINK_TYPES = ("client", "application", "appointment")
VIEWS = ("today", "upcoming", "done")
CLOSED = ("done", "cancelled")

# The moves a person makes: close an open task, or open a closed one again.
TRANSITIONS = {"open": ("done", "cancelled"), "done": ("open",), "cancelled": ("open",)}

SYSTEM = "system"           # created_by of an automatic task
DEFAULT_FOLLOWUP_HOURS = 24
DEFAULT_RETENTION_DAYS = 180
RUN_EVERY = 3600            # seconds between automatic checks triggered by reads
DAY = 86400
DEFAULT_TIMEZONE = "Europe/Istanbul"
DONE_LIST_LIMIT = 200

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")

_COLUMNS = ("id", "practitioner_username", "type", "link_type", "link_id", "assignee", "due_on", "due_time",
            "priority", "status", "created_by", "created_at", "updated_by", "updated_at", "done_by", "done_at",
            "closed_at")
# Fixed SQL text: every value is a bound parameter.
_SELECT = ("SELECT id, practitioner_username, type, link_type, link_id, assignee, due_on, due_time, priority, "
           "status, created_by, created_at, updated_by, updated_at, done_by, done_at, closed_at FROM tasks")
_INSERT = ("INSERT INTO tasks (id, practitioner_username, type, link_type, link_id, assignee, due_on, due_time, "
           "priority, status, created_by, created_at, updated_by, updated_at, done_by, done_at, closed_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")

_last_run = 0.0


class TaskError(ValueError):
    """A request the task list refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


NOT_FOUND = "Task not found"
LINK_NOT_FOUND = "Linked item not found"


def _row(row) -> Optional[dict]:
    return {col: row[i] for i, col in enumerate(_COLUMNS)} if row else None


# ── Time ──────────────────────────────────────────────────────
def timezone() -> ZoneInfo:
    """The practice's timezone (VHV_TIMEZONE), as for the appointment book."""
    return ZoneInfo(os.getenv("VHV_TIMEZONE") or DEFAULT_TIMEZONE)


def now_local() -> datetime:
    return datetime.now(timezone())


def today() -> date:
    return now_local().date()


def is_overdue(task: dict, now: Optional[datetime] = None) -> bool:
    """Open, and its day has passed — or it is today and its time has."""
    if task.get("status") != "open":
        return False
    now = now or now_local()
    day = now.date().isoformat()
    if task["due_on"] < day:
        return True
    return task["due_on"] == day and bool(task.get("due_time")) and task["due_time"] < now.strftime("%H:%M")


def check_due(due_on, due_time) -> None:
    if not isinstance(due_on, str) or not _DATE.fullmatch(due_on):
        raise TaskError("Use YYYY-MM-DD")
    try:
        datetime.strptime(due_on, "%Y-%m-%d")
    except ValueError:
        raise TaskError("Use YYYY-MM-DD")
    if due_time is not None and (not isinstance(due_time, str) or not _TIME.fullmatch(due_time)):
        raise TaskError("Use HH:MM")


# ── Books and links ───────────────────────────────────────────
def can_see(user: dict, task: Optional[dict]) -> bool:
    """Is this task in a book the user runs? The links are read now."""
    return bool(task) and task["practitioner_username"] in appointment_book.books_for(user)


def _application_book(app: Optional[dict]) -> Optional[str]:
    if not app or app.get("purged_at") is not None:
        return None
    return app.get("assigned_practitioner") or app.get("preferred_practitioner")


def book_of_link(link_type: str, link_id: str) -> Optional[str]:
    """The book a linked item is in, or None when there is no such item."""
    if link_type == "client":
        return client_registry.owner_of(link_id)
    if link_type == "application":
        return _application_book(intake.get(link_id))
    if link_type == "appointment":
        a = appointment_book.get(link_id)
        return a["practitioner_username"] if a else None
    return None


def _check_link(book: str, link_type, link_id) -> None:
    if link_type is None and link_id is None:
        return
    if link_type not in LINK_TYPES or not link_id:
        raise TaskError("Give both the kind and the ID of the linked item")
    # Someone else's item gets the same answer as a missing one.
    if book_of_link(link_type, link_id) != book:
        raise TaskError(LINK_NOT_FOUND, status=404)


def can_be_assigned(book: str, username: Optional[str]) -> bool:
    """Nobody, the book's practitioner, or a secretary linked to the book."""
    return username is None or username == book or appointment_book.is_linked(username, book)


def _check_assignee(book: str, username) -> None:
    if not can_be_assigned(book, username):
        raise TaskError("This person does not work in this book")


def _client_name(patient_id: Optional[str]) -> Optional[str]:
    card = client_registry.get(patient_id) if patient_id else None
    return card["full_name"] if card else None


def link_info(task: dict) -> Optional[dict]:
    """What the linked item is, for the line the interface shows. `label` is
    None (and `available` False) when the item is gone or no longer in this
    task's book — an application given to someone else, say."""
    link_type, link_id = task.get("link_type"), task.get("link_id")
    if not link_type or not link_id:
        return None
    info = {"type": link_type, "id": link_id, "label": None, "starts_at": None, "patient_id": None,
            "available": False}
    book = task["practitioner_username"]
    if link_type == "client":
        card = client_registry.get(link_id)
        if card and card["practitioner_username"] == book:
            info.update(label=card["full_name"], patient_id=link_id, available=True)
    elif link_type == "application":
        app = intake.get(link_id)
        if _application_book(app) == book:
            client_id = app.get("client_id") if app.get("client_id") != intake._PENDING else None
            info.update(label=app.get("full_name") or _client_name(client_id), patient_id=client_id,
                        available=True)
    elif link_type == "appointment":
        a = appointment_book.get(link_id)
        if a and a["practitioner_username"] == book:
            info.update(label=_client_name(a["patient_id"]) or a["patient_id"], starts_at=a["starts_at"],
                        patient_id=a["patient_id"], available=True)
    return info


# ── Reading ───────────────────────────────────────────────────
def get(task_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (task_id,), fetch="one")
    return _row(rows[0]) if rows else None


def in_book(book: str) -> list:
    return [_row(r) for r in _run(_SELECT + " WHERE practitioner_username = ?", (book,), fetch="all")]


def _sort_open(task: dict) -> tuple:
    # Due day, then time (a task without a time counts as "by the end of the
    # day"), then high priority first.
    return task["due_on"], task["due_time"] or "24:00", 0 if task["priority"] == "high" else 1


def list_for(books: list, *, view: str = "today", assignee: Optional[str] = None, kind: Optional[str] = None,
             unassigned: bool = False) -> list:
    """The tasks of these books in one view, filtered and sorted."""
    day = today().isoformat()
    items = [t for book in books for t in in_book(book)]
    if view == "today":
        items = [t for t in items if t["status"] == "open" and t["due_on"] <= day]
    elif view == "upcoming":
        items = [t for t in items if t["status"] == "open" and t["due_on"] > day]
    else:
        items = [t for t in items if t["status"] in CLOSED]
    if unassigned:
        items = [t for t in items if t["assignee"] is None]
    elif assignee:
        items = [t for t in items if t["assignee"] == assignee]
    if kind:
        items = [t for t in items if t["type"] == kind]
    if view == "done":
        return sorted(items, key=lambda t: -(t["closed_at"] or 0))[:DONE_LIST_LIMIT]
    return sorted(items, key=_sort_open)


def counts_for(books: list) -> dict:
    """The menu badge and the dashboard card: open tasks due today or
    earlier, how many of those are overdue, and the upcoming ones."""
    day, now = today().isoformat(), now_local()
    result = {"today": 0, "overdue": 0, "upcoming": 0}
    for book in books:
        for t in in_book(book):
            if t["status"] != "open":
                continue
            if t["due_on"] <= day:
                result["today"] += 1
                result["overdue"] += 1 if is_overdue(t, now) else 0
            else:
                result["upcoming"] += 1
    return result


def center_counts() -> dict:
    """The operators' view: tasks per status for the whole center, nothing else."""
    result = {s: 0 for s in STATUSES}
    for status, n in _run("SELECT status, COUNT(*) FROM tasks GROUP BY status", fetch="all"):
        result[status] = result.get(status, 0) + n
    return result


def open_applications_of(book: str) -> list:
    """Open applications a task in this book may link to."""
    rows = _run("SELECT id FROM applications WHERE purged_at IS NULL AND status IN (?, ?, ?) AND "
                "(assigned_practitioner = ? OR (assigned_practitioner IS NULL AND preferred_practitioner = ?)) "
                "ORDER BY received_at", intake.OPEN + (book, book), fetch="all")
    return [intake.get(r[0]) for r in rows]


# ── Writing ───────────────────────────────────────────────────
def _audit(action: str, username: str, task: dict, **extra) -> None:
    # IDs and codes only: never a client's or applicant's name.
    from core.events.event_bus import SystemAuditEvent, event_bus
    from core.security import get_device_id
    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action=action, username=username, device_id=get_device_id(),
        extra={"task_id": task["id"], "practitioner": task["practitioner_username"], "type": task["type"],
               "link_type": task["link_type"], "link_id": task["link_id"], "status": task["status"], **extra}))


audit = _audit


def create(book: str, data: dict, *, by: str) -> dict:
    """A new open task in this book. `data` comes validated from the request
    schema; the checks against the book are made here."""
    kind, priority = data.get("type"), data.get("priority", "normal")
    if kind not in TYPES:
        raise TaskError("Unknown task type")
    if priority not in PRIORITIES:
        raise TaskError("Unknown priority")
    check_due(data.get("due_on"), data.get("due_time"))
    link_type, link_id = data.get("link_type"), data.get("link_id")
    _check_link(book, link_type, link_id)
    _check_assignee(book, data.get("assignee"))
    task_id = f"TSK-{uuid.uuid4().hex[:12].upper()}"
    values = {
        "id": task_id, "practitioner_username": book, "type": kind, "link_type": link_type, "link_id": link_id,
        "assignee": data.get("assignee"), "due_on": data["due_on"], "due_time": data.get("due_time"),
        "priority": priority, "status": "open", "created_by": by, "created_at": time.time(),
        "updated_by": None, "updated_at": None, "done_by": None, "done_at": None, "closed_at": None,
    }
    _run(_INSERT, tuple(values[c] for c in _COLUMNS))
    return get(task_id)


EDITABLE = ("type", "priority", "due_on", "due_time", "assignee", "link_type", "link_id")

# One fixed statement per change: the SQL text never changes, only the values.
_UPDATE_SQL = {
    "type": "UPDATE tasks SET type = ?, updated_by = ?, updated_at = ? WHERE id = ?",
    "priority": "UPDATE tasks SET priority = ?, updated_by = ?, updated_at = ? WHERE id = ?",
    "due_on": "UPDATE tasks SET due_on = ?, updated_by = ?, updated_at = ? WHERE id = ?",
    "due_time": "UPDATE tasks SET due_time = ?, updated_by = ?, updated_at = ? WHERE id = ?",
    "assignee": "UPDATE tasks SET assignee = ?, updated_by = ?, updated_at = ? WHERE id = ?",
}
_UPDATE_LINK = "UPDATE tasks SET link_type = ?, link_id = ?, updated_by = ?, updated_at = ? WHERE id = ?"


def update(task: dict, changes: dict, *, by: str) -> dict:
    """Change the fields in `changes` (only EDITABLE ones) of an open task.
    A link is changed as a pair (link_type and link_id together)."""
    if task["status"] != "open":
        raise TaskError("This task is closed", status=409)
    book = task["practitioner_username"]
    merged = {**task, **changes}
    if "type" in changes and changes["type"] not in TYPES:
        raise TaskError("Unknown task type")
    if "priority" in changes and changes["priority"] not in PRIORITIES:
        raise TaskError("Unknown priority")
    if "due_on" in changes or "due_time" in changes:
        check_due(merged["due_on"], merged["due_time"])
    relinked = "link_type" in changes or "link_id" in changes
    if relinked:
        _check_link(book, merged["link_type"], merged["link_id"])
    if "assignee" in changes:
        _check_assignee(book, changes["assignee"])
    now = time.time()
    for field, value in changes.items():
        if field in _UPDATE_SQL:
            _run(_UPDATE_SQL[field], (value, by, now, task["id"]))
    if relinked:
        _run(_UPDATE_LINK, (merged["link_type"], merged["link_id"], by, now, task["id"]))
    return get(task["id"])


def set_status(task: dict, status: str, *, by: str) -> dict:
    """done, cancelled, or open again. Checked against the stored status in
    the same statement, so two clicks cannot both close it."""
    if status not in STATUSES:
        raise TaskError("Unknown status")
    if status not in TRANSITIONS.get(task["status"], ()):
        raise TaskError(f"A task cannot go from {task['status']} to {status}", status=409)
    now = time.time()
    if status == "done":
        params = (status, by, now, now, by, now, task["id"], task["status"])
    elif status == "cancelled":
        params = (status, None, None, now, by, now, task["id"], task["status"])
    else:
        params = (status, None, None, None, by, now, task["id"], task["status"])
    changed = _run("UPDATE tasks SET status = ?, done_by = ?, done_at = ?, closed_at = ?, updated_by = ?, "
                   "updated_at = ? WHERE id = ? AND status = ?", params, fetch="rowcount")
    if changed != 1:
        raise TaskError(f"A task cannot go from {task['status']} to {status}", status=409)
    return get(task["id"])


# ── Automatic tasks ───────────────────────────────────────────
def _claim(key: str) -> bool:
    """True the first time a trigger is claimed, False ever after."""
    if _run("SELECT 1 FROM task_triggers WHERE trigger_key = ?", (key,), fetch="one")[0]:
        return False
    try:
        _run("INSERT INTO task_triggers (trigger_key, created_at) VALUES (?, ?)", (key, time.time()))
    except Exception:   # the primary key refused a second claim
        return False
    return True


def _create_automatic(book: str, kind: str, link_type: str, link_id: str) -> dict:
    task = create(book, {"type": kind, "link_type": link_type, "link_id": link_id,
                         "due_on": today().isoformat(), "priority": "normal"}, by=SYSTEM)
    _audit("TASK_CREATED", SYSTEM, task, automatic=True)
    return task


def on_client_created(card: Optional[dict]) -> Optional[dict]:
    """A new card without a KVKK signature date: one kvkk_forms task in its
    book. Returns the task, or None when there is nothing to do."""
    if not card or card.get("kvkk_signed_on"):
        return None
    if not _claim(f"kvkk:{card['patient_id']}"):
        return None
    return _create_automatic(card["practitioner_username"], "kvkk_forms", "client", card["patient_id"])


def on_kvkk_signed(patient_id: str, *, by: str) -> list:
    """The forms are signed: the open kvkk_forms tasks for this client are done."""
    rows = _run(_SELECT + " WHERE link_type = ? AND link_id = ? AND type = ? AND status = ?",
                ("client", patient_id, "kvkk_forms", "open"), fetch="all")
    closed = []
    for row in rows:
        try:
            task = set_status(_row(row), "done", by=by)
        except TaskError:
            continue
        _audit("TASK_DONE", by, task, automatic=True)
        closed.append(task["id"])
    return closed


def _positive_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def followup_hours() -> int:
    """VHV_TASK_FOLLOWUP_HOURS, or 24 when unset or not a positive number."""
    return _positive_env("VHV_TASK_FOLLOWUP_HOURS", DEFAULT_FOLLOWUP_HOURS)


def retention_days() -> int:
    """VHV_TASK_RETENTION_DAYS, or 180 when unset or not a positive number."""
    return _positive_env("VHV_TASK_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def create_followups(now: Optional[float] = None) -> list:
    """A follow_up_application task for every application still "new" more
    than followup_hours() after it came in, once each. Returns the task IDs."""
    now = now if now is not None else time.time()
    rows = _run("SELECT id, assigned_practitioner, preferred_practitioner FROM applications "
                "WHERE status = ? AND purged_at IS NULL AND received_at < ? ORDER BY received_at",
                ("new", now - followup_hours() * 3600), fetch="all")
    created = []
    for app_id, assigned, preferred in rows:
        book = assigned or preferred
        if not book or not _claim(f"followup:{app_id}"):
            continue
        created.append(_create_automatic(book, "follow_up_application", "application", app_id)["id"])
    return created


def purge_expired(now: Optional[float] = None) -> list:
    """Delete done and cancelled tasks closed more than retention_days() ago.
    Returns the IDs deleted."""
    now = now if now is not None else time.time()
    cutoff = now - retention_days() * DAY
    rows = _run("SELECT id FROM tasks WHERE status IN (?, ?) AND closed_at < ?", CLOSED + (cutoff,), fetch="all")
    purged = []
    for (task_id,) in rows:
        if _run("DELETE FROM tasks WHERE id = ? AND status IN (?, ?) AND closed_at < ?",
                (task_id,) + CLOSED + (cutoff,), fetch="rowcount"):
            purged.append(task_id)
    if purged:
        from core.events.event_bus import SystemAuditEvent, event_bus
        from core.security import get_device_id
        event_bus.publish(SystemAuditEvent(
            project_name="__system__", action="TASKS_PURGED", username=SYSTEM, device_id=get_device_id(),
            extra={"task_ids": purged, "count": len(purged), "retention_days": retention_days()}))
    return purged


def run_automatic(now: Optional[float] = None) -> list:
    """The follow-up check and the retention purge. Returns the follow-up
    tasks created."""
    global _last_run
    now = now if now is not None else time.time()
    _last_run = now
    created = create_followups(now)
    purge_expired(now)
    return created


def maybe_run() -> None:
    """run_automatic(), at most once every RUN_EVERY seconds."""
    if time.time() - _last_run >= RUN_EVERY:
        run_automatic()


# ── KVKK: erasure ─────────────────────────────────────────────
def remove_for_client(patient_id: str) -> int:
    """Erasure only (KVKK Art. 17): every task linked to the client's card, to
    one of their appointments, or to the application that became their card.
    Run before the appointments are deleted. Returns how many went."""
    removed = _run("DELETE FROM tasks WHERE (link_type = ? AND link_id = ?) "
                   "OR (link_type = ? AND link_id IN (SELECT id FROM appointments WHERE patient_id = ?)) "
                   "OR (link_type = ? AND link_id IN (SELECT id FROM applications WHERE client_id = ?))",
                   ("client", patient_id, "appointment", patient_id, "application", patient_id),
                   fetch="rowcount")
    _run("DELETE FROM task_triggers WHERE trigger_key = ?", (f"kvkk:{patient_id}",))
    return removed
