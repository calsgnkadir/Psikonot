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
practitioner.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import current_user, require_role
from backend.routers.appointments import _names, _view, books_of
from backend.schemas.requests import RoomReq, UpdateRoomReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book as book
from core.services import rooms

router = APIRouter(prefix="/api/v1/rooms", tags=["rooms"])

MANAGERS = ("admin", "security_officer")
BOOKERS = ("practitioner", "secretary")
DAY = 86400
MAX_RANGE_DAYS = 7


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
    start: datetime,
    end: Optional[datetime] = None,
    u: dict = Depends(current_user),
):
    """Scheduled appointments in this room between `start` and `end` (default:
    one day). Every book the caller runs in full (a front desk runs several);
    any other book as busy times."""
    books = books_of(u)
    room = _found(room_id)
    t0 = start.timestamp()
    t1 = end.timestamp() if end else t0 + DAY
    if not 0 < t1 - t0 <= MAX_RANGE_DAYS * DAY:
        raise HTTPException(422, f"The range must be positive and at most {MAX_RANGE_DAYS} days")
    items = book.room_day(room_id, start=t0, end=t1)
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
    return {"room": _room_view(room), "slots": slots}
