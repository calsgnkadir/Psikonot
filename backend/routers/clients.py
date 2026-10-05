"""
backend/routers/clients.py — the practice's client list
=======================================================
Each client is a card owned by one practitioner (core/services/client_registry.py).
The practitioner and their secretary both work with the cards: add a new
client, keep the phone number up to date, see at a glance who came and who did
not. Nothing here opens a client's file — that stays with the practitioner,
through the record endpoints and the access policy (ADR-0003).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import current_user
from backend.routers.appointments import book_owner
from backend.schemas.requests import ClientCardReq, ClientCardUpdateReq
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id
from core.services import appointment_book, client_registry

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])

NO_VISITS = {"completed": 0, "no_show": 0, "cancelled": 0, "last_seen_at": None, "next_at": None}


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _view(card: dict, visits: dict = None) -> dict:
    visits = visits or NO_VISITS
    return {
        "patient_id": card["patient_id"],
        "full_name": card["full_name"],
        "phone": card["phone"],
        "email": card["email"],
        "kvkk_signed_on": card["kvkk_signed_on"],
        # The tally: how often the client came, missed or cancelled.
        "attended": visits["completed"],
        "no_show": visits["no_show"],
        "cancelled": visits["cancelled"],
        "last_seen_at": _iso(visits["last_seen_at"]),
        "next_appointment_at": _iso(visits["next_at"]),
    }


def _audit(action: str, u: dict, patient_id: str) -> None:
    # The audit trail names the client by ID only, never by name or phone.
    event_bus.publish(SystemAuditEvent(project_name="__system__", action=action, username=u["username"],
                                       device_id=get_device_id(), extra={"patient_id": patient_id}))


@router.get("", summary="My clients, with their contact details and tally")
def list_clients(u: dict = Depends(current_user)):
    owner = book_owner(u)
    visits = appointment_book.tally(owner)
    return {"clients": [_view(c, visits.get(c["patient_id"])) for c in client_registry.list_for(owner)]}


@router.post("", summary="Add a client card")
def add_client(req: ClientCardReq, u: dict = Depends(current_user)):
    owner = book_owner(u)
    card = client_registry.add(practitioner=owner, full_name=req.full_name, phone=req.phone,
                               email=req.email, kvkk_signed_on=req.kvkk_signed_on, created_by=u["username"])
    _audit("CLIENT_ADDED", u, card["patient_id"])
    return {"client": _view(card)}


@router.patch("/{patient_id}", summary="Update a client card")
def update_client(patient_id: str, req: ClientCardUpdateReq, u: dict = Depends(current_user)):
    owner = book_owner(u)
    # Someone else's client gets the same answer as a missing one.
    if client_registry.owner_of(patient_id) != owner:
        raise HTTPException(404, "Client not found")
    changes = req.model_dump(exclude_unset=True)
    if "full_name" in changes and changes["full_name"] is None:
        raise HTTPException(422, "The name cannot be empty")
    card = client_registry.update(patient_id, changes, by=u["username"])
    _audit("CLIENT_UPDATED", u, patient_id)
    return {"client": _view(card, appointment_book.tally(owner).get(patient_id))}
