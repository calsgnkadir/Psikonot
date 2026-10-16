"""
core/services/rooms.py — the centre's therapy rooms
===================================================
Practitioners share the rooms; an in-person appointment may say which one it is
in (core/services/appointment_book.py keeps a room from being double-booked).

A room's name is a short label such as "Oda 1" or "Grup Odası": letters,
digits, spaces and . - / only, at most 30 characters. That is a label, not a
place to write about a client, so nothing clinical can end up in this table.

Rooms are switched off, never deleted: past appointments keep their room, and
a room that is off cannot be chosen for a new or moved appointment.
"""

import re
import time
import uuid
from typing import Optional

from database.sql_db import run_sql as _run

MAX_NAME = 30
# Starts and ends with a letter or digit; \w is Unicode, so Turkish letters count.
_NAME = re.compile(r"[^\W_](?:[\w .\-/]*[^\W_])?")

_SELECT = "SELECT id, name, active, created_by, created_at, updated_by, updated_at FROM rooms"
_COLUMNS = ("id", "name", "active", "created_by", "created_at", "updated_by", "updated_at")


class RoomError(ValueError):
    """A change the room list refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def clean_name(name: str) -> str:
    """The name with spaces tidied, or ValueError if it is not a short label."""
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= MAX_NAME or "_" in name or not _NAME.fullmatch(name):
        raise ValueError(f"A room name is 1-{MAX_NAME} characters: letters, digits, spaces and . - /")
    return name


def _row(row) -> dict:
    room = {col: row[i] for i, col in enumerate(_COLUMNS)}
    room["active"] = bool(room["active"])
    return room


def get(room_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (room_id,), fetch="one")
    return _row(rows[0]) if rows and rows[0] else None


def list_rooms(*, active_only: bool) -> list:
    rows = _run(_SELECT + " WHERE (? = 0 OR active = ?) ORDER BY name",
                (1 if active_only else 0, True), fetch="all")
    return [_row(r) for r in rows]


def names() -> dict:
    """{room id: name} for every room, switched off or not."""
    return {r[0]: r[1] for r in _run("SELECT id, name FROM rooms", fetch="all")}


def _check_unique(name: str, ignore_id: Optional[str] = None) -> None:
    rows = _run("SELECT id FROM rooms WHERE LOWER(name) = LOWER(?)", (name,), fetch="all")
    if any(r[0] != ignore_id for r in rows):
        raise RoomError("A room with that name already exists", status=409)


def create(name: str, *, by: str) -> dict:
    name = clean_name(name)
    _check_unique(name)
    room_id = f"ROOM-{uuid.uuid4().hex[:8].upper()}"
    _run("INSERT INTO rooms (id, name, active, created_by, created_at, updated_by, updated_at) "
         "VALUES (?, ?, ?, ?, ?, ?, ?)",
         (room_id, name, True, by, time.time(), None, None))
    return get(room_id)


def update(room: dict, *, by: str, name: Optional[str] = None, active: Optional[bool] = None) -> dict:
    """Rename a room and/or switch it on or off."""
    if name is not None:
        name = clean_name(name)
        _check_unique(name, ignore_id=room["id"])
    _run("UPDATE rooms SET name = ?, active = ?, updated_by = ?, updated_at = ? WHERE id = ?",
         (name if name is not None else room["name"],
          active if active is not None else room["active"],
          by, time.time(), room["id"]))
    return get(room["id"])
