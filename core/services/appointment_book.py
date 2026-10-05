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
  * a secretary linked to that practitioner (table practice_staff) — they run
    the book but never see a record: the access policy knows no "secretary"
    role, so every record endpoint refuses them.
Clients do not sign in; the book is the practice's own.

Rules:
  * a practitioner cannot be double-booked (scheduled appointments of the same
    practitioner never overlap);
  * a new or moved appointment starts in the future and lasts 15-240 minutes;
  * only a scheduled appointment can be moved or cancelled, and it can only be
    marked completed / no-show once it has started.

Single node (ADR-0002): the overlap check and the insert run on one
connection, one after the other, which is enough without concurrent workers.
"""

import time
import uuid
from typing import Optional

from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder

FORMATS = ("In-person", "Online")
STATUSES = ("scheduled", "cancelled", "completed", "no_show")
MIN_DURATION, MAX_DURATION = 15, 240

_COLUMNS = ("id", "practitioner_username", "patient_id", "starts_at", "duration_min",
            "session_format", "status", "created_by", "created_at", "updated_by", "updated_at")

# Fixed SQL text: queries are never assembled from strings, every value is a
# bound parameter. Optional filters use "(? IS NULL OR column = ?)".
_SELECT = ("SELECT id, practitioner_username, patient_id, starts_at, duration_min, session_format, "
           "status, created_by, created_at, updated_by, updated_at FROM appointments")
_INSERT = ("INSERT INTO appointments (id, practitioner_username, patient_id, starts_at, duration_min, "
           "session_format, status, created_by, created_at, updated_by, updated_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


class BookingError(ValueError):
    """A request the book refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _run(sql: str, params: tuple = (), fetch: str = "") -> list:
    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(_to_placeholder(sql), params)
        rows = cur.fetchall() if fetch == "all" else ([cur.fetchone()] if fetch == "one" else [])
        conn.commit()
        return rows
    finally:
        cur.close()
        conn.close()


def _row_to_dict(row) -> dict:
    return {col: row[i] for i, col in enumerate(_COLUMNS)}


# ── Staff ─────────────────────────────────────────────────────
def practitioner_for(user: dict) -> Optional[str]:
    """Whose book this user runs: their own for a practitioner, their
    employer's for a secretary, none for anyone else."""
    if user.get("role") == "practitioner":
        return user["username"]
    if user.get("role") == "secretary":
        rows = _run("SELECT practitioner_username FROM practice_staff WHERE staff_username = ?",
                    (user["username"],), fetch="one")
        return rows[0][0] if rows and rows[0] else None
    return None


def link_staff(staff_username: str, practitioner_username: str) -> None:
    _run("INSERT INTO practice_staff (staff_username, practitioner_username, created_at) VALUES (?, ?, ?)",
         (staff_username, practitioner_username, time.time()))


def staff_of(practitioner_username: str) -> list:
    rows = _run("SELECT staff_username FROM practice_staff WHERE practitioner_username = ?",
                (practitioner_username,), fetch="all")
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


def get(appointment_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (appointment_id,), fetch="one")
    return _row_to_dict(rows[0]) if rows and rows[0] else None


# ── Writing ───────────────────────────────────────────────────
def _check_slot(practitioner: str, starts_at: float, duration_min: int, ignore_id: Optional[str] = None):
    if not MIN_DURATION <= duration_min <= MAX_DURATION:
        raise BookingError(f"Duration must be {MIN_DURATION}-{MAX_DURATION} minutes")
    if starts_at <= time.time():
        raise BookingError("An appointment must start in the future")
    ends_at = starts_at + duration_min * 60
    # Any scheduled appointment starting up to the longest possible duration
    # earlier could still be running at `starts_at`.
    rows = _run("SELECT id, starts_at, duration_min FROM appointments "
                "WHERE practitioner_username = ? AND status = ? AND starts_at < ? AND starts_at > ?",
                (practitioner, "scheduled", ends_at, starts_at - MAX_DURATION * 60), fetch="all")
    for other_id, other_start, other_duration in rows:
        if other_id != ignore_id and other_start + other_duration * 60 > starts_at:
            raise BookingError("The practitioner already has an appointment at that time", status=409)


def book(*, practitioner: str, patient_id: str, starts_at: float, duration_min: int,
         session_format: str, created_by: str) -> dict:
    if session_format not in FORMATS:
        raise BookingError(f"Format must be one of {FORMATS}")
    _check_slot(practitioner, starts_at, duration_min)
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"
    _run(_INSERT,
         (appointment_id, practitioner, patient_id, starts_at, duration_min, session_format,
          "scheduled", created_by, time.time(), None, None))
    return get(appointment_id)


def reschedule(appointment: dict, *, starts_at: float, duration_min: int, by: str) -> dict:
    if appointment["status"] != "scheduled":
        raise BookingError("Only a scheduled appointment can be moved", status=409)
    _check_slot(appointment["practitioner_username"], starts_at, duration_min, ignore_id=appointment["id"])
    _run("UPDATE appointments SET starts_at = ?, duration_min = ?, updated_by = ?, updated_at = ? WHERE id = ?",
         (starts_at, duration_min, by, time.time(), appointment["id"]))
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
                 session_format: str, status: str, created_by: str) -> str:
    """For the demo seed (and tests) only: write an appointment as given, past
    ones and their outcome included, without the future-only and overlap
    rules. Returns its id."""
    appointment_id = f"APT-{uuid.uuid4().hex[:12].upper()}"
    _run(_INSERT,
         (appointment_id, practitioner, patient_id, starts_at, duration_min,
          session_format, status, created_by, time.time(), None, None))
    return appointment_id
