"""
backend/routers/pseudonymization.py — Pseudonymization API Router
===================================================================
Admin-only endpoints for managing patient identity pseudonymization.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from backend.dependencies import current_user
from core.pseudonymization.service import get_pseudonymization_service

router = APIRouter(prefix="/api/v1/pseudonym", tags=["pseudonymization"])


# ── Request / Response Schemas ──────────────────

class PseudonymizeRequest(BaseModel):
    patient_id: str


class PseudonymizeResponse(BaseModel):
    patient_id: str
    anon_id: str
    display_id: str


class DepseudonymizeRequest(BaseModel):
    anon_id: str


class DepseudonymizeResponse(BaseModel):
    anon_id: str
    patient_id: Optional[str] = None
    found: bool


class MappingEntry(BaseModel):
    patient_id: str
    anon_id: str


class AllMappingsResponse(BaseModel):
    count: int
    mappings: list


# ── Endpoints ───────────────────────────────────

@router.post("/generate", response_model=PseudonymizeResponse,
             summary="Generate Pseudonym for Patient")
def generate_pseudonym(
    req: PseudonymizeRequest,
    u: dict = Depends(current_user),
):
    """
    Generate or retrieve the anonymous identifier for a patient. Admins only.
    """
    if u["role"] != "admin":
        raise HTTPException(403, "Only admins can manage pseudonyms")

    svc = get_pseudonymization_service()
    anon_id = svc.pseudonymize(req.patient_id)
    display_id = svc.get_anon_id_for_display(req.patient_id)

    return PseudonymizeResponse(
        patient_id=req.patient_id,
        anon_id=anon_id,
        display_id=display_id,
    )


@router.post("/resolve", response_model=DepseudonymizeResponse,
             summary="Resolve Anonymous ID to Patient")
def resolve_pseudonym(
    req: DepseudonymizeRequest,
    u: dict = Depends(current_user),
):
    """
    Reverse-lookup: resolve an anonymous ID back to the real patient ID.
    Admin-only — this is a privileged operation.
    """
    if u["role"] != "admin":
        raise HTTPException(403, "Only admins can resolve pseudonyms")

    svc = get_pseudonymization_service()
    patient_id = svc.depseudonymize(req.anon_id)

    return DepseudonymizeResponse(
        anon_id=req.anon_id,
        patient_id=patient_id,
        found=patient_id is not None,
    )


@router.get("/mappings", response_model=AllMappingsResponse,
            summary="List All Pseudonym Mappings")
def list_mappings(u: dict = Depends(current_user)):
    """
    List all known pseudonym mappings.
    Admin-only endpoint for audit and compliance purposes.
    """
    if u["role"] != "admin":
        raise HTTPException(403, "Only admins can view pseudonym mappings")

    svc = get_pseudonymization_service()
    raw = svc.get_all_mappings()

    mappings = [
        {"patient_id": pid, "anon_id": aid}
        for pid, aid in raw.items()
    ]

    return AllMappingsResponse(count=len(mappings), mappings=mappings)
