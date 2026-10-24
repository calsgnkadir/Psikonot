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

Series: weekly sessions are booked in one go (book_series). Every session of
a series is an ordinary appointment that carries the series' id, so it is
cancelled, moved or marked came / did not come on its own; "this and every
later one" cancels the series' scheduled sessions from one onward. A series:
  * repeats weekly or every two weeks at the same wall-clock time in the
    practice's timezone, across a clock change too (occurrences());
  * ends after a number of sessions or on a date, and never has more than
    MAX_SERIES sessions;
  * is written whole or not at all: every session passes the same checks as a
    single booking, in the same transaction as the writes. The caller first
    previews it (preview_series, which writes nothing), chooses the dates to
    leave out, and if a session it kept is no longer free when the series is
    written, nothing is written (SeriesConflict).

The intake (core/services/intake.py) closes a client's application when
their first appointment is booked: book() and book_series() run a caller's
`within(cursor)` after the inserts, in the same transaction.

Single node (ADR-0002): the overlap checks (practitioner and room) and the
insert or update run on one connection in one transaction. On SQLite that
transaction is opened with BEGIN IMMEDIATE, which takes the database's write
lock before the checks read anything, so a second booking for the same slot
waits and then sees the first one.
"""

import time
import uuid
from datetime import date, datetime, timedelta, tzinfo
from typing import Callable, Optional

from database.sql_db import default_sql_db, to_placeholder
from database.sql_db import run_sql as _run

FORMATS = ("In-person", "Online")
STATUSES = ("scheduled", "cancelled", "completed", "no_show")
MIN_DURATION, MAX_DURATION = 15, 240
# A series: weeks between two sessions, and the most sessions it may have.
FREQUENCIES = {"weekly": 1, "biweekly": 2}
MAX_SERIES = 52

_COLUMNS = ("id", "practitioner_username", "patient_id", "starts_at", "duration_min",
            "session_format", "status", "created_by", "created_at", "updated_by", "updated_at", "room_id",
            "series_id")

# Fixed SQL text: queries are never assembled from strings, every value is a
# bound parameter. Optional filters use "(? IS NULL OR column = ?)".
_SELECT = ("SELECT id, practitioner_username, patient_id, starts_at, duration_min, session_format, "
           "status, created_by, created_at, updated_by, updated_at, room_id, series_id FROM appointments")
_INSERT = ("INSERT INTO appointments (id, practitioner_username, patient_id, starts_at, duration_min, "
           "session_format, status, created_by, created_at, updated_by, updated_at, room_id, series_id) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


class BookingError(ValueError):
    """A request the book refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class SeriesConflict(BookingError):
    """Sessions of a series that are no longer free (or have passed) when it
    is written; `starts` holds their start times. Nothing was written."""

    def __init__(self, starts: list):
        super().__init__("Some sessions of the series are no longer free", status=409)
        self.starts = starts


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
def _in_transaction(work, *, write: bool = True):
    """Run `work(cursor)` on one connection, in one transaction, and return
    what it returns. A write transaction takes the write lock first (BEGIN
    IMMEDIATE on SQLite), so nothing can be booked into a slot between the
    checks and the writes."""
    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        if write and not default_sql_db.is_postgres:
            cursor.execute("BEGIN IMMEDIATE")
        result = work(cursor)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _write_checked(check, statement: str, params: tuple) -> None:
    """Run `check(cursor)` and then `statement` on one connection, in one
    transaction: nothing can be booked into the slot between the two."""
    def work(cursor):
        check(cursor)
        cursor.execute(to_placeholder(statement), params)
    _in_transaction(work)


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


def _check_duration(duration_min: int) -> None:
    if not MIN_DURATION <= duration_min <= MAX_DURATION:
        raise BookingError(f"Duration must be {MIN_DURATION}-{MAX_DURATION} minutes")


def _check_room(cursor, room_id: Optional[str], session_format: str, *, moving: bool = False) -> Optional[str]:
    """The room's rules, whatever the time; returns the room's name."""
    if room_id is None:
        return None
    if session_format == "Online":
        raise BookingError("An online appointment takes no room")
    cursor.execute(to_placeholder("SELECT name, active FROM rooms WHERE id = ?"), (room_id,))
    room = cursor.fetchone()
    if room is None:
        raise BookingError("No such room")
    room_name = room[0]
    if not room[1]:
        if moving:   # say how to get out of the room
            raise BookingError(f"The room {room_name} is no longer in use: move the appointment to "
                               "another room, or choose No room to take it out of the room")
        raise BookingError(f"The room {room_name} is no longer in use: choose another room, or No room")
    return room_name


def _check_slot(cursor, practitioner: str, starts_at: float, duration_min: int, *,
                session_format: str, room_id: Optional[str] = None, ignore_id: Optional[str] = None):
    """The book's rules for a new or moved appointment, read on the caller's
    connection (see _write_checked)."""
    _check_duration(duration_min)
    if starts_at <= time.time():
        raise BookingError("An appointment must start in the future")
    room_name = _check_room(cursor, room_id, session_format, moving=ignore_id is not None)
    ends_at = starts_at + duration_min * 60
    if _overlaps(cursor, "practitioner", practitioner, starts_at, ends_at, ignore_id):
        raise BookingError("The practitioner already has an appointment at that time", status=409)
    if room_id is not None and _overlaps(cursor, "room", room_id, starts_at, ends_at, ignore_id):
        raise BookingError(f"The room {room_name} is already booked at that time", status=409)


def _conflicts(cursor, practitioner: str, starts_at: float, duration_min: int,
               room_id: Optional[str]) -> list:
    """What stands in the way of one session of a series, as a list of
    "past", "practitioner", "room" (empty when it is free). The same overlap
    checks as _check_slot."""
    if starts_at <= time.time():
        return ["past"]
    ends_at = starts_at + duration_min * 60
    found = []
    if _overlaps(cursor, "practitioner", practitioner, starts_at, ends_at, None):
        found.append("practitioner")
    if room_id is not None and _overlaps(cursor, "room", room_id, starts_at, ends_at, None):
        found.append("room")
    return found


_KEEP = object()   # reschedule(): leave the room as it is


def book(*, practitioner: str, patient_id: str, starts_at: float, duration_min: int,
         session_format: str, created_by: str, room_id: Optional[str] = None,
         within: Optional[Callable] = None) -> dict:
    """Book one appointment. `within(cursor)`, when given, runs after the
    insert in the same transaction (the intake closes the client's
    application with it); if it raises, nothing is booked."""
    if session_format not in FORMATS:
        raise BookingError(f"Format must be one of {FORMATS}")
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"

    def work(cursor):
        _check_slot(cursor, practitioner, starts_at, duration_min, session_format=session_format, room_id=room_id)
        cursor.execute(to_placeholder(_INSERT),
                       (appointment_id, practitioner, patient_id, starts_at, duration_min, session_format,
                        "scheduled", created_by, time.time(), None, None, room_id, None))
        if within is not None:
            within(cursor)
    _in_transaction(work)
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


# ── Series ────────────────────────────────────────────────────
def occurrences(first: datetime, tz: tzinfo, *, frequency: str, count: Optional[int] = None,
                until: Optional[date] = None) -> list:
    """The start of every session of a series, as aware datetimes in `tz`.

    The first session's wall-clock time in `tz` is kept for every session:
    each one is that date plus whole weeks, at that time, with the offset `tz`
    has on that day, so a session at 10:00 stays at 10:00 across a clock
    change. (On a night the clock skips that hour, the time is read with the
    offset from before the change.) `until` is a date in `tz`, inclusive.
    Exactly one of `count` and `until` is given."""
    if frequency not in FREQUENCIES:
        raise BookingError("Frequency must be weekly or biweekly")
    if (count is None) == (until is None):
        raise BookingError("Give either the number of sessions or an end date")
    local = first.astimezone(tz)
    day, wall = local.date(), local.time().replace(tzinfo=None, fold=0)
    step = timedelta(weeks=FREQUENCIES[frequency])
    if until is not None:
        count = (until - day) // step + 1 if until >= day else 0
    if not 1 <= count <= MAX_SERIES:
        raise BookingError(f"A series has 1-{MAX_SERIES} sessions")
    return [datetime.combine(day + k * step, wall, tzinfo=tz) for k in range(count)]


def preview_series(*, practitioner: str, starts: list, duration_min: int, session_format: str,
                   room_id: Optional[str] = None) -> list:
    """For each start time, what stands in its way ([] when free). Writes
    nothing. A rule that holds for the whole series (the duration, the
    format, the room) is refused as for a single booking."""
    if session_format not in FORMATS:
        raise BookingError(f"Format must be one of {FORMATS}")
    _check_duration(duration_min)

    def work(cursor):
        _check_room(cursor, room_id, session_format)
        return [_conflicts(cursor, practitioner, t, duration_min, room_id) for t in starts]
    return _in_transaction(work, write=False)


def book_series(*, practitioner: str, patient_id: str, starts: list, duration_min: int,
                session_format: str, created_by: str, room_id: Optional[str] = None,
                within: Optional[Callable] = None) -> tuple:
    """Book every start time as one series, in one transaction: the checks of
    a single booking for each session, then every insert, then
    `within(cursor)` if given (as for book()). If any session is not free,
    nothing is written (SeriesConflict names them).
    Returns (series_id, [appointment, ...])."""
    if session_format not in FORMATS:
        raise BookingError(f"Format must be one of {FORMATS}")
    _check_duration(duration_min)
    if not starts:
        raise BookingError("Every session of the series was left out: nothing to book")
    if len(starts) > MAX_SERIES:
        raise BookingError(f"A series has 1-{MAX_SERIES} sessions")
    series_id = f"SER-{uuid.uuid4().hex[:12].upper()}"
    ids = [f"APT-{uuid.uuid4().hex[:12].upper()}" for _ in starts]

    def work(cursor):
        _check_room(cursor, room_id, session_format)
        taken = [t for t in starts if _conflicts(cursor, practitioner, t, duration_min, room_id)]
        if taken:
            raise SeriesConflict(taken)
        now = time.time()
        for appointment_id, t in zip(ids, starts):
            cursor.execute(to_placeholder(_INSERT),
                           (appointment_id, practitioner, patient_id, t, duration_min, session_format,
                            "scheduled", created_by, now, None, None, room_id, series_id))
        if within is not None:
            within(cursor)
    _in_transaction(work)
    return series_id, [get(appointment_id) for appointment_id in ids]


_FOLLOWING_SQL = ("SELECT id FROM appointments WHERE series_id = ? AND practitioner_username = ? "
                  "AND status = ? AND starts_at >= ?")
_CANCEL_SQL = "UPDATE appointments SET status = ?, updated_by = ?, updated_at = ? WHERE id = ?"


def cancel_following(appointment: dict, *, by: str) -> list:
    """Cancel this session of a series and every later one that is still
    scheduled; sessions already cancelled, held or missed stay as they are.
    Returns the cancelled appointments."""
    series_id = appointment.get("series_id")
    if not series_id:
        raise BookingError("This appointment is not part of a series")

    def work(cursor):
        cursor.execute(to_placeholder(_FOLLOWING_SQL),
                       (series_id, appointment["practitioner_username"], "scheduled", appointment["starts_at"]))
        found = [r[0] for r in cursor.fetchall()]
        now = time.time()
        for appointment_id in found:
            cursor.execute(to_placeholder(_CANCEL_SQL), ("cancelled", by, now, appointment_id))
        return found
    return [get(appointment_id) for appointment_id in _in_transaction(work)]


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
                 session_format: str, status: str, created_by: str, room_id: Optional[str] = None,
                 series_id: Optional[str] = None) -> str:
    """For the demo seed (and tests) only: write an appointment as given, past
    ones and their outcome included, without the future-only and overlap
    rules. Returns its id."""
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"
    _run(_INSERT,
         (appointment_id, practitioner, patient_id, starts_at, duration_min,
          session_format, status, created_by, time.time(), None, None, room_id, series_id))
    return appointment_id
