"""
backend/routers/alerts.py — Security Alerts & Dual-Control API Router
======================================================================
Endpoints for viewing real-time security alerts and executing Dual-Control
co-approvals for vault management.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from backend.dependencies import current_user
from core.services import access_policy
from core.services.alert_service import alert_service
from core.services.dual_control import dual_control_engine
from pydantic import BaseModel


router = APIRouter(prefix="/api/v1/security", tags=["security"])


class DualControlReq(BaseModel):
    request_type: str
    target_patient_id: str
    reason: str
    validity_minutes: Optional[int] = 30


class CoSignReq(BaseModel):
    token_id: str


@router.get("/alerts", summary="Get Real-Time Security Alerts Dashboard")
def get_security_alerts(
    limit: int = 50,
    severity: Optional[str] = None,
    u: dict = Depends(current_user)
):
    if u["role"] not in ("admin", "security_officer"):
        raise HTTPException(403, "Access restricted to Security Officers and System Administrators.")
    return {
        "alerts": alert_service.get_recent_alerts(limit=limit, severity_filter=severity)
    }


@router.post("/alerts/acknowledge/{alert_id}", summary="Acknowledge Security Alert")
def acknowledge_security_alert(
    alert_id: str,
    u: dict = Depends(current_user)
):
    if u["role"] not in ("admin", "security_officer"):
        raise HTTPException(403, "Access restricted to Security Officers and System Administrators.")
    success = alert_service.acknowledge_alert(alert_id)
    if not success:
        raise HTTPException(404, "Alert ID not found.")
    return {"success": True, "message": f"Alert {alert_id} acknowledged by {u['username']}."}


@router.post("/dual-control/request", summary="Create Dual-Control Access Request")
def create_dual_control_request(
    req: DualControlReq,
    u: dict = Depends(current_user)
):
    # Only the operators that a token is for may ask for one. A practitioner
    # opens their own clients' files without one, and could not use a token on
    # anyone else's: the token is bound to the person who requested it.
    if u["role"] not in access_policy.OPERATOR_ROLES:
        raise HTTPException(403, "Only operators can initiate dual-control requests.")

    try:
        result = dual_control_engine.request_dual_control_access(
            request_type=req.request_type,
            target_patient_id=req.target_patient_id,
            requested_by=u["username"],
            reason=req.reason,
            validity_minutes=req.validity_minutes or 30
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    alert_service.raise_alert(
        alert_type="DUAL_CONTROL_REQUESTED",
        severity="HIGH",
        title=f"Dual-Control Request for {req.target_patient_id}",
        description=f"User {u['username']} requested {req.request_type} for client {req.target_patient_id}. Reason: {req.reason}",
        username=u["username"],
        extra=result
    )

    return result


@router.get("/dual-control/{token_id}", summary="Dual-Control Request Status")
def get_dual_control_status(
    token_id: str,
    u: dict = Depends(current_user)
):
    if u["role"] not in ("admin", "security_officer", "auditor", "practitioner"):
        raise HTTPException(403, "Only privileged operators can inspect dual-control requests.")

    info = dual_control_engine.get_request(token_id)
    if not info:
        raise HTTPException(404, "Dual-control request token not found.")

    # The justification text can name a patient and a legal matter; only the two
    # parties to the request need to read it back.
    if u["username"] not in (info["requested_by"], info.get("co_signed_by")):
        info.pop("reason", None)

    return info


@router.post("/dual-control/co-sign", summary="Co-Sign Dual-Control Request (Security Officer)")
def co_sign_dual_control_request(
    req: CoSignReq,
    u: dict = Depends(current_user)
):
    if u["role"] not in ("admin", "security_officer"):
        raise HTTPException(403, "Co-signature requires Security Officer or Administrator privileges.")

    try:
        result = dual_control_engine.co_sign_request(
            token_id=req.token_id,
            co_signer_username=u["username"],
            co_signer_role=u["role"]
        )

        alert_service.raise_alert(
            alert_type="DUAL_CONTROL_APPROVED",
            severity="CRITICAL",
            title=f"Dual-Control Co-Signed: {req.token_id}",
            description=f"Security Officer {u['username']} co-signed dual-control token {req.token_id}.",
            username=u["username"],
            extra=result
        )

        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
