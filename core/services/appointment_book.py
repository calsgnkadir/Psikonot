"""
core/services/appointment_book.py — a practitioner's appointment book
=====================================================================
Scheduling facts only: who (client ID), with whom (practitioner), when, for
how long, in person or online, and whether it happened. There is deliberately
no free-text field, so clinical content cannot end up in this unencrypted
table by accident. That a client sees a psychologist at all is still
sensitive, so every read is scoped to one practitioner's book or one client.

Who may use a book:
  * the practitioner who owns it;
  * a secretary linked to that practitioner (table practice_staff, one row per
    link: a center's front desk may serve several practitioners) — they run
    the book but never see a record: the access policy knows no "secretary"
    role, so every record endpoint refuses them. The links are read on every
    request, so removing one ends access to that book at once.
Clients do not sign in; the book is the practice's own.

Rooms (core/services/rooms.py): an in-person appointment may name the
therapy room it is in; an online one never does. The room is optional, so
books kept before rooms existed, and practices with a single room, work as
before.

Rules:
  * a practitioner cannot be double-booked (scheduled appointments of the same
    practitioner never overlap);
  * neither can a room: scheduled appointments in the same room never overlap,
    whoever's book they are in. Cancelled and no-show appointments free it;
  * a room that is switched off cannot be chosen for a new or moved
    appointment, but the appointments already in it keep it;
  * a new or moved appointment starts in the future and lasts 15-240 minutes;
  * only a scheduled appointment can be moved or cancelled, and it can only be
    marked completed / no-show once it has started.

Single node (ADR-0002): the overlap checks (practitioner and room) and the
insert or update run on one connection in one transaction. On SQLite that
transaction is opened with BEGIN IMMEDIATE, which takes the database's write
lock before the checks read anything, so a second booking for the same slot
waits and then sees the first one.
"""

import time
import uuid
from typing import Optional

from database.sql_db import default_sql_db, to_placeholder
from database.sql_db import run_sql as _run

FORMATS = ("In-person", "Online")
STATUSES = ("scheduled", "cancelled", "completed", "no_show")
MIN_DURATION, MAX_DURATION = 15, 240

_COLUMNS = ("id", "practitioner_username", "patient_id", "starts_at", "duration_min",
            "session_format", "status", "created_by", "created_at", "updated_by", "updated_at", "room_id")

# Fixed SQL text: queries are never assembled from strings, every value is a
# bound parameter. Optional filters use "(? IS NULL OR column = ?)".
_SELECT = ("SELECT id, practitioner_username, patient_id, starts_at, duration_min, session_format, "
           "status, created_by, created_at, updated_by, updated_at, room_id FROM appointments")
_INSERT = ("INSERT INTO appointments (id, practitioner_username, patient_id, starts_at, duration_min, "
           "session_format, status, created_by, created_at, updated_by, updated_at, room_id) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


class BookingError(ValueError):
    """A request the book refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _row_to_dict(row) -> dict:
    return {col: row[i] for i, col in enumerate(_COLUMNS)}


# ── Staff ─────────────────────────────────────────────────────
def books_for(user: dict) -> list:
    """Whose books this user runs: their own for a practitioner, every
    practitioner they are linked to for a secretary, none for anyone else."""
    if user.get("role") == "practitioner":
        return [user["username"]]
    if user.get("role") == "secretary":
        return practitioners_of(user["username"])
    return []


def practitioner_for(user: dict) -> Optional[str]:
    """The one book this user runs, or None when they run none or several."""
    books = books_for(user)
    return books[0] if len(books) == 1 else None


def practitioners_of(staff_username: str) -> list:
    """The practitioners a secretary is linked to, by username."""
    rows = _run("SELECT practitioner_username FROM practice_staff WHERE staff_username = ? "
                "ORDER BY practitioner_username", (staff_username,), fetch="all")
    return [r[0] for r in rows]


def is_linked(staff_username: str, practitioner_username: str) -> bool:
    rows = _run("SELECT 1 FROM practice_staff WHERE staff_username = ? AND practitioner_username = ?",
                (staff_username, practitioner_username), fetch="one")
    return bool(rows and rows[0])


def link_staff(staff_username: str, practitioner_username: str) -> None:
    _run("INSERT INTO practice_staff (staff_username, practitioner_username, created_at) VALUES (?, ?, ?)",
         (staff_username, practitioner_username, time.time()))


def unlink_staff(staff_username: str, practitioner_username: str) -> bool:
    """Remove one link; False if there was none."""
    removed = _run("DELETE FROM practice_staff WHERE staff_username = ? AND practitioner_username = ?",
                   (staff_username, practitioner_username), fetch="rowcount")
    return removed > 0


def all_staff_links() -> list:
    """Every link as (secretary, practitioner), for the operators' screen."""
    rows = _run("SELECT staff_username, practitioner_username FROM practice_staff "
                "ORDER BY staff_username, practitioner_username", (), fetch="all")
    return [(r[0], r[1]) for r in rows]


def staff_of(practitioner_username: str) -> list:
    rows = _run("SELECT staff_username FROM practice_staff WHERE practitioner_username = ? "
                "ORDER BY staff_username", (practitioner_username,), fetch="all")
    return [r[0] for r in rows]


# ── Reading ───────────────────────────────────────────────────
def tally(practitioner: str) -> dict:
    """Per client of this book: how many sessions they came to, missed or
    cancelled, when they last came, and their next appointment.
    {patient_id: {"completed", "no_show", "cancelled", "last_seen_at", "next_at"}}"""
    counts = {}

    def entry(patient_id):
        return counts.setdefault(patient_id, {"completed": 0, "no_show": 0, "cancelled": 0,
                                              "last_seen_at": None, "next_at": None})

    rows = _run("SELECT patient_id, status, COUNT(*), MAX(starts_at) FROM appointments "
                "WHERE practitioner_username = ? GROUP BY patient_id, status",
                (practitioner,), fetch="all")
    for patient_id, status, n, latest in rows:
        if status in ("completed", "no_show", "cancelled"):
            entry(patient_id)[status] = n
        if status == "completed":
            entry(patient_id)["last_seen_at"] = latest
    rows = _run("SELECT patient_id, MIN(starts_at) FROM appointments "
                "WHERE practitioner_username = ? AND status = ? AND starts_at > ? GROUP BY patient_id",
                (practitioner, "scheduled", time.time()), fetch="all")
    for patient_id, next_at in rows:
        entry(patient_id)["next_at"] = next_at
    return counts


def list_appointments(*, practitioner: Optional[str] = None, patient_id: Optional[str] = None,
                      start: float, end: float) -> list:
    """Appointments that start in [start, end), for one practitioner's book or
    one client — never both unscoped."""
    if not practitioner and not patient_id:
        raise ValueError("an appointment list must be scoped to a practitioner or a client")
    rows = _run(_SELECT + " WHERE starts_at >= ? AND starts_at < ?"
                " AND (? IS NULL OR practitioner_username = ?) AND (? IS NULL OR patient_id = ?)"
                " ORDER BY starts_at",
                (start, end, practitioner, practitioner, patient_id, patient_id), fetch="all")
    return [_row_to_dict(r) for r in rows]


def room_day(room_id: str, *, start: float, end: float) -> list:
    """Scheduled appointments in one room that overlap [start, end), from
    every book. The caller shows another book's ones as "busy" only."""
    rows = _run(_SELECT + " WHERE room_id = ? AND status = ? AND starts_at > ? AND starts_at < ?"
                " ORDER BY starts_at",
                (room_id, "scheduled", start - MAX_DURATION * 60, end), fetch="all")
    items = [_row_to_dict(r) for r in rows]
    return [a for a in items if a["starts_at"] + a["duration_min"] * 60 > start]


def get(appointment_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (appointment_id,), fetch="one")
    return _row_to_dict(rows[0]) if rows and rows[0] else None


# ── Writing ───────────────────────────────────────────────────
def _write_checked(check, statement: str, params: tuple) -> None:
    """Run `check(cursor)` and then `statement` on one connection, in one
    transaction: nothing can be booked into the slot between the two."""
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        if not default_sql_db.is_postgres:
            cursor.execute("BEGIN IMMEDIATE")
        check(cursor)
        cursor.execute(to_placeholder(statement), params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# Fixed text per kind of overlap; only the bound values change.
_OVERLAP_SQL = {
    "practitioner": "SELECT id, starts_at, duration_min FROM appointments "
                    "WHERE practitioner_username = ? AND status = ? AND starts_at < ? AND starts_at > ?",
    "room": "SELECT id, starts_at, duration_min FROM appointments "
            "WHERE room_id = ? AND status = ? AND starts_at < ? AND starts_at > ?",
}


def _overlaps(cursor, kind: str, value: str, starts_at: float, ends_at: float,
              ignore_id: Optional[str]) -> bool:
    """Whether a scheduled appointment of this practitioner / in this room
    overlaps [starts_at, ends_at). Any one starting up to the longest possible
    duration earlier could still be running at `starts_at`."""
    cursor.execute(to_placeholder(_OVERLAP_SQL[kind]),
                   (value, "scheduled", ends_at, starts_at - MAX_DURATION * 60))
    return any(other_id != ignore_id and other_start + other_duration * 60 > starts_at
               for other_id, other_start, other_duration in cursor.fetchall())


def _check_slot(cursor, practitioner: str, starts_at: float, duration_min: int, *,
                session_format: str, room_id: Optional[str] = None, ignore_id: Optional[str] = None):
    """The book's rules for a new or moved appointment, read on the caller's
    connection (see _write_checked)."""
    if not MIN_DURATION <= duration_min <= MAX_DURATION:
        raise BookingError(f"Duration must be {MIN_DURATION}-{MAX_DURATION} minutes")
    if starts_at <= time.time():
        raise BookingError("An appointment must start in the future")
    room_name = None
    if room_id is not None:
        if session_format == "Online":
            raise BookingError("An online appointment takes no room")
        cursor.execute(to_placeholder("SELECT name, active FROM rooms WHERE id = ?"), (room_id,))
        room = cursor.fetchone()
        if room is None:
            raise BookingError("No such room")
        room_name = room[0]
        if not room[1]:
            if ignore_id is not None:   # a move: say how to get out of the room
                raise BookingError(f"The room {room_name} is no longer in use: move the appointment to "
                                   "another room, or choose No room to take it out of the room")
            raise BookingError(f"The room {room_name} is no longer in use: choose another room, or No room")
    ends_at = starts_at + duration_min * 60
    if _overlaps(cursor, "practitioner", practitioner, starts_at, ends_at, ignore_id):
        raise BookingError("The practitioner already has an appointment at that time", status=409)
    if room_id is not None and _overlaps(cursor, "room", room_id, starts_at, ends_at, ignore_id):
        raise BookingError(f"The room {room_name} is already booked at that time", status=409)


_KEEP = object()   # reschedule(): leave the room as it is


def book(*, practitioner: str, patient_id: str, starts_at: float, duration_min: int,
         session_format: str, created_by: str, room_id: Optional[str] = None) -> dict:
    if session_format not in FORMATS:
        raise BookingError(f"Format must be one of {FORMATS}")
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"
    _write_checked(
        lambda cursor: _check_slot(cursor, practitioner, starts_at, duration_min,
                                   session_format=session_format, room_id=room_id),
        _INSERT,
        (appointment_id, practitioner, patient_id, starts_at, duration_min, session_format,
         "scheduled", created_by, time.time(), None, None, room_id))
    return get(appointment_id)


def reschedule(appointment: dict, *, starts_at: float, duration_min: int, by: str,
               room_id=_KEEP) -> dict:
    """Move an appointment to another time and/or room. `room_id=None` takes
    it out of its room; leaving `room_id` out keeps the room it is in (which
    must still be in use)."""
    if appointment["status"] != "scheduled":
        raise BookingError("Only a scheduled appointment can be moved", status=409)
    if room_id is _KEEP:
        room_id = appointment.get("room_id")
    _write_checked(
        lambda cursor: _check_slot(cursor, appointment["practitioner_username"], starts_at, duration_min,
                                   session_format=appointment["session_format"], room_id=room_id,
                                   ignore_id=appointment["id"]),
        "UPDATE appointments SET starts_at = ?, duration_min = ?, room_id = ?, updated_by = ?, updated_at = ? "
        "WHERE id = ?",
        (starts_at, duration_min, room_id, by, time.time(), appointment["id"]))
    return get(appointment["id"])


def set_status(appointment: dict, status: str, *, by: str) -> dict:
    if status not in STATUSES or status == "scheduled":
        raise BookingError("Status must be cancelled, completed or no_show")
    if appointment["status"] != "scheduled":
        raise BookingError(f"This appointment is already {appointment['status']}", status=409)
    if status in ("completed", "no_show") and appointment["starts_at"] > time.time():
        raise BookingError("An appointment can be marked completed or no-show only after it starts", status=409)
    _run("UPDATE appointments SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?",
         (status, by, time.time(), appointment["id"]))
    return get(appointment["id"])


def remove_for_client(patient_id: str) -> None:
    """Erasure only (KVKK Art. 17): a client's appointments go with them."""
    _run("DELETE FROM appointments WHERE patient_id = ?", (patient_id,))


def add_existing(*, practitioner: str, patient_id: str, starts_at: float, duration_min: int,
                 session_format: str, status: str, created_by: str, room_id: Optional[str] = None) -> str:
    """For the demo seed (and tests) only: write an appointment as given, past
    ones and their outcome included, without the future-only and overlap
    rules. Returns its id."""
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"
    _run(_INSERT,
         (appointment_id, practitioner, patient_id, starts_at, duration_min,
          session_format, status, created_by, time.time(), None, None, room_id))
    return appointment_id
