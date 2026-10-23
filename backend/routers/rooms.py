"""
backend/routers/rooms.py — the centre's therapy rooms
=====================================================
Operators (admin, security officer) keep the room list: they add rooms, rename
them and switch them off or back on. Rooms are never deleted, so past
appointments keep theirs. Whoever books appointments (a practitioner or a
secretary) lists the rooms in use and sees a room's day.

A room's day is shared by every practitioner who books into it, so it is
scoped like the book: the caller's own appointments are shown in full, every
other book's only as a busy time — no client, no appointment id, no
practitioner. Operators run no book: they see how a room is used, never who
uses it, so for them every appointment is a busy time.

The day runs from midnight to midnight in the server's timezone, like the
front desk's day view (appointments.day_bounds). A room's week is the same
over Monday to Sunday (appointments.week_bounds), with the same rules.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.dependencies import current_user, require_role
from backend.routers.appointments import _names, _view, books_of, day_bounds, week_bounds
from backend.schemas.requests import RoomReq, UpdateRoomReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book as book
from core.services import rooms

router = APIRouter(prefix="/api/v1/rooms", tags=["rooms"])

MANAGERS = ("admin", "security_officer")
BOOKERS = ("practitioner", "secretary")


def _room_view(room: dict) -> dict:
    return {"id": room["id"], "name": room["name"], "active": room["active"]}


def _audit(action: str, u: dict, room: dict) -> None:
    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action=action, username=u["username"],
        device_id=get_device_id(),
        extra={"room_id": room["id"], "name": room["name"], "active": room["active"]},
    ))


def _found(room_id: str) -> dict:
    room = rooms.get(room_id)
    if room is None:
        raise HTTPException(404, "Room not found")
    return room


@router.get("", summary="Therapy rooms")
def list_rooms(u: dict = Depends(current_user)):
    """Operators see every room, switched off or not; those who book see the
    rooms in use."""
    if u["role"] in MANAGERS:
        return {"rooms": [_room_view(r) for r in rooms.list_rooms(active_only=False)]}
    if u["role"] in BOOKERS:
        return {"rooms": [_room_view(r) for r in rooms.list_rooms(active_only=True)]}
    raise HTTPException(403, "Only those who book appointments, and operators, see the rooms.")


@router.post("", summary="Add a room")
def create_room(req: RoomReq, u: dict = Depends(require_role(*MANAGERS))):
    try:
        room = rooms.create(req.name, by=u["username"])
    except rooms.RoomError as e:
        raise HTTPException(e.status, str(e))
    _audit("ROOM_CREATED", u, room)
    return {"room": _room_view(room)}


@router.patch("/{room_id}", summary="Rename a room or switch it off / on")
def update_room(room_id: str, req: UpdateRoomReq, u: dict = Depends(require_role(*MANAGERS))):
    room = _found(room_id)
    if req.name is None and req.active is None:
        raise HTTPException(422, "Nothing to change")
    try:
        updated = rooms.update(room, by=u["username"], name=req.name, active=req.active)
    except rooms.RoomError as e:
        raise HTTPException(e.status, str(e))
    if req.name is not None and req.name != room["name"]:
        _audit("ROOM_RENAMED", u, updated)
    if req.active is not None and req.active != room["active"]:
        _audit("ROOM_ACTIVATED" if req.active else "ROOM_DEACTIVATED", u, updated)
    return {"room": _room_view(updated)}


@router.get("/{room_id}/schedule", summary="A room's day")
def room_schedule(
    room_id: str,
    day: str = Query(..., alias="date", description="YYYY-MM-DD in the practice's timezone"),
    u: dict = Depends(current_user),
):
    """Scheduled appointments in this room on one day. Every book the caller
    runs in full (a front desk runs several); any other book as busy times.
    Operators run no book, so they see busy times only; anyone else is
    refused."""
    books = _books(u)
    room = _found(room_id)
    t0, t1, tz = day_bounds(day)
    return {"room": _room_view(room), "date": day, "timezone": tz.key,
            "slots": _slots(room, books, t0, t1)}


@router.get("/{room_id}/week", summary="A room's week")
def room_week(
    room_id: str,
    start: str = Query(..., description="YYYY-MM-DD, any day of the week, in the practice's timezone"),
    u: dict = Depends(current_user),
):
    """The room's day over the Monday-to-Sunday week that holds `start`, with
    the same rules: the caller's books in full, any other book as busy times,
    operators busy times only, anyone else refused."""
    books = _books(u)
    room = _found(room_id)
    monday, t0, t1, tz = week_bounds(start)
    return {"room": _room_view(room), "start": monday.isoformat(),
            "end": (monday + timedelta(days=7)).isoformat(), "timezone": tz.key,
            "slots": _slots(room, books, t0, t1)}


def _books(u: dict) -> list:
    """The books whose appointments the caller sees in full: none for an
    operator, those they run for a practitioner or secretary; 403 otherwise."""
    return [] if u["role"] in MANAGERS else books_of(u)


def _slots(room: dict, books: list, t0: float, t1: float) -> list:
    """Scheduled appointments in the room in [t0, t1): an appointment of a book
    in `books` in full, any other only as its start and end."""
    items = book.room_day(room["id"], start=t0, end=t1)
    room_names = {room["id"]: room["name"]}
    names = {}
    for owner in books:
        names.update(_names(owner))
    slots = []
    for a in items:
        if a["practitioner_username"] in books:
            slots.append({**_view(a, names, room_names), "mine": True})
        else:
            view = _view(a, {}, room_names)
            slots.append({"starts_at": view["starts_at"], "ends_at": view["ends_at"], "mine": False})
    return slots
