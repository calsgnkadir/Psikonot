"""
backend/routers/onboarding.py — out-of-band account provisioning & enrollment
=============================================================================
No real account should exist that a person self-registered. Accounts are
*provisioned* by a privileged operator once identity has been vetted out of band,
and stay inactive until the holder redeems a single-use enrollment token that was
also delivered out of band (in person / sealed channel — the system never emails
it). Only then does the account become ``ACTIVE_ENROLLED`` and able to log in.

  provision (admin/security officer)  →  PENDING_ONBOARDING + one-time token
  redeem    (holder, token-gated)     →  sets password, ACTIVE_ENROLLED
  login                               →  refused unless ACTIVE_ENROLLED

A practitioner brings in their own secretary the same way:

  invite-secretary (practitioner)     →  PENDING secretary linked to them + code
  redeem           (secretary)        →  as above

Clients have no accounts: they are cards in the practice's client list
(core/services/client_registry.py).
"""

import hashlib
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.dependencies import require_role
from backend.schemas.requests import ProvisionAccountReq, RedeemEnrollmentReq, InviteSecretaryReq
from core.services import appointment_book
from core.domain.entities import User
from core.events.event_bus import event_bus, SystemAuditEvent
from core.security import get_device_id, hash_password, validate_password
from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import SQLUserRepository, _to_placeholder

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# How long a provisioned holder has to redeem their token (out-of-band delivery).
_TOKEN_TTL_SECONDS = 72 * 3600


# A practitioner may hold at most this many unredeemed invitations, so a stolen
# practitioner account cannot fill the user table with pending accounts.
MAX_OPEN_INVITATIONS = 20


def _hash_token(token: str) -> str:
    """Only the hash of the enrollment token is stored, never the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_token(cur, username: str, created_by: str):
    """Store a new single-use token for `username`, and `created_by` as the
    account's provisioner; return (token, expires_at). The caller commits."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + _TOKEN_TTL_SECONDS
    cur.execute(
        _to_placeholder(
            "INSERT INTO enrollment_tokens "
            "(token_hash, username, expires_at, used, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ),
        (_hash_token(token), username, expires_at, False, created_by, now),
    )
    # Whoever holds the code can become the account, so dual-control does not
    # count the two as different people (core/services/dual_control.py).
    cur.execute(
        _to_placeholder("UPDATE users SET provisioned_by = ? WHERE username = ?"),
        (created_by, username),
    )
    return token, expires_at


def _col(row, name: str, index: int):
    """Read a column from a SQLite row or a PostgreSQL tuple alike."""
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return row[index]


@router.post("/provision", summary="Provision a vetted account (out-of-band)")
def provision_account(
    req: ProvisionAccountReq,
    request: Request,
    u: dict = Depends(require_role("admin", "security_officer")),
):
    repo = SQLUserRepository()
    if repo.user_exists(req.username):
        raise HTTPException(409, "A user with this username already exists")

    # Locked account: a random password nobody holds. It is PENDING until the
    # holder redeems their token, and login is refused in the meantime.
    user = User(
        id=f"USR-{uuid.uuid4().hex[:12].upper()}",
        username=req.username,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=req.role,
        full_name=req.full_name,
        specialty=req.specialty,
        institution=req.institution,
        patient_id=None,
        clearance=req.clearance,
        totp_secret=None,
        totp_enabled=False,
        account_status="PENDING_ONBOARDING",
    )
    repo.save_user(user)

    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        token, expires_at = _issue_token(cur, req.username, u["username"])
        conn.commit()
    finally:
        cur.close()
        conn.close()

    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action="ACCOUNT_PROVISIONED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"provisioned": req.username, "role": req.role},
    ))

    return {
        "success": True,
        "username": req.username,
        "account_status": "PENDING_ONBOARDING",
        # Deliver this out of band. It is single-use, expires, and is stored only
        # as a hash — it cannot be recovered from the system afterwards.
        "enrollment_token": token,
        "expires_at": expires_at,
        "message": "Deliver the enrollment token to the account holder out of band.",
    }


# ── INVITATIONS ───────────────────────────────────────────
def _open_invitations(cur, practitioner: str) -> int:
    cur.execute(
        _to_placeholder(
            "SELECT COUNT(*) FROM enrollment_tokens "
            "WHERE created_by = ? AND used = ? AND expires_at > ?"
        ),
        (practitioner, False, time.time()),
    )
    return cur.fetchone()[0]


# ── PRACTICE STAFF (practitioner) ─────────────────────────────
@router.post("/invite-secretary", summary="Invite a secretary for my appointment book (practitioner)")
def invite_secretary(
    req: InviteSecretaryReq,
    u: dict = Depends(require_role("practitioner")),
):
    """Create a pending secretary account linked to this practitioner and return
    a single-use code. The secretary will run this practitioner's appointment
    book and nothing else: the "secretary" role has no access to records."""
    repo = SQLUserRepository()
    if repo.user_exists(req.username):
        raise HTTPException(409, "A user with this username already exists")
    practitioner = repo.load_user(u["username"])

    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        if _open_invitations(cur, u["username"]) >= MAX_OPEN_INVITATIONS:
            raise HTTPException(429, "Too many open invitations. Wait for some to be used or to expire.")
        repo.save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}",
            username=req.username,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="secretary",
            full_name=req.full_name,
            specialty=None,
            institution=practitioner.institution if practitioner else None,
            patient_id=None,
            clearance=None,
            totp_secret=None,
            totp_enabled=False,
            account_status="PENDING_ONBOARDING",
        ))
        token, expires_at = _issue_token(cur, req.username, u["username"])
        conn.commit()
    finally:
        cur.close()
        conn.close()
    appointment_book.link_staff(req.username, u["username"])

    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action="SECRETARY_INVITED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"secretary": req.username},
    ))
    return {"success": True, "username": req.username, "invite_code": token, "expires_at": expires_at}


@router.get("/staff", summary="My secretaries (practitioner)")
def list_staff(u: dict = Depends(require_role("practitioner"))):
    repo = SQLUserRepository()
    staff = []
    for username in appointment_book.staff_of(u["username"]):
        user = repo.load_user(username)
        if user:
            staff.append({
                "username": username,
                "full_name": user.full_name,
                "status": "active" if user.account_status == "ACTIVE_ENROLLED" else "pending",
            })
    return {"staff": staff}


@router.post("/redeem", summary="Redeem an enrollment token and activate the account")
def redeem_enrollment(req: RedeemEnrollmentReq, request: Request):
    valid, msg = validate_password(req.new_password)
    if not valid:
        raise HTTPException(422, msg)

    token_hash = _hash_token(req.enrollment_token)

    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            _to_placeholder(
                "SELECT username, expires_at, used FROM enrollment_tokens WHERE token_hash = ?"
            ),
            (token_hash,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Invalid or unknown enrollment token")
        username, expires_at, used = (
            _col(row, "username", 0), _col(row, "expires_at", 1), _col(row, "used", 2))

        if used:
            raise HTTPException(400, "This enrollment token has already been used")
        if time.time() > float(expires_at):
            raise HTTPException(400, "This enrollment token has expired")

        repo = SQLUserRepository()
        user = repo.load_user(username)
        if not user:
            raise HTTPException(400, "The account for this token no longer exists")

        user.password_hash = hash_password(req.new_password)
        user.account_status = "ACTIVE_ENROLLED"
        repo.save_user(user)

        cur.execute(
            _to_placeholder("UPDATE enrollment_tokens SET used = ? WHERE token_hash = ?"),
            (True, token_hash),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action="ACCOUNT_ENROLLED",
        username=username,
        device_id=get_device_id(),
        extra={},
    ))

    return {
        "success": True,
        "username": username,
        "account_status": "ACTIVE_ENROLLED",
        "message": "Account activated. You can now sign in and enrol your passkey.",
    }
