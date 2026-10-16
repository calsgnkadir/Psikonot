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

In a center one front desk serves several practitioners. Only the center
manager (an operator) adds a secretary to further books:

  staff/link   (operator)             →  an existing secretary joins a named book
  staff/unlink (operator)             →  the secretary leaves a named book
  staff/unlink (practitioner)         →  the secretary leaves their own book
  staff/links  (operator)             →  every practitioner and secretary, linked

A practitioner does not link. A secretary linked to a second book becomes
"shared", and then only an operator resets or disables them
(backend/routers/accounts.py): a practitioner able to add any secretary to
their book would take that away from the secretary's practitioner without
anyone's consent. Unlinking only narrows who sees the practitioner's own book,
so a practitioner may still remove a secretary from it.

Linking only opens the practitioner's own book and client cards to the
secretary, never a record. Every link and unlink is audited.

Clients have no accounts: they are cards in the practice's client list
(core/services/client_registry.py).
"""

import os
import secrets
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.dependencies import require_role
from backend.schemas.requests import (InviteSecretaryReq, ProvisionAccountReq, RedeemEnrollmentReq,
                                      StaffLinkReq)
from core.services import accounts, appointment_book
from core.domain.entities import User
from core.events.event_bus import event_bus, SystemAuditEvent
from core.security import get_device_id, hash_password, validate_password
from database.sql_db import DEMO_USERNAMES, get_sql_db
from infrastructure.repositories.sql_repositories import SQLUserRepository, _to_placeholder

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

# A practitioner may hold at most this many unredeemed invitations, so a stolen
# practitioner account cannot fill the user table with pending accounts.
MAX_OPEN_INVITATIONS = 20

# The codes themselves are made and stored in core/services/accounts.py, shared
# with password reset codes and the server-side CLI.
_hash_token = accounts.hash_code


def _issue_token(cur, username: str, created_by: str):
    """Store a new single-use enrollment code for `username`, and `created_by`
    as the account's provisioner; return (token, expires_at). The caller commits."""
    return accounts.issue_code(cur, username, created_by)


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
            "WHERE created_by = ? AND used = ? AND expires_at > ? AND purpose = ?"
        ),
        (practitioner, False, time.time(), accounts.ENROLL),
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
                "status": _STATUS.get(user.account_status, "pending"),
                # Also works for other practitioners: then only an operator
                # resets or disables the account (backend/routers/accounts.py).
                # Who the others are is not this practitioner's business.
                "shared": len(appointment_book.practitioners_of(username)) > 1,
            })
    return {"staff": staff}


OPERATORS = ("admin", "security_officer")
# Who may remove a secretary from a book: an operator from any, a practitioner
# from their own. Adding is for operators only (see the module docstring).
STAFF_MANAGERS = ("practitioner",) + OPERATORS

_STATUS = {"ACTIVE_ENROLLED": "active", "DISABLED": "disabled"}


def _account(user: User) -> dict:
    """What the link screen shows of an account: no contact detail, no client."""
    return {"username": user.username, "full_name": user.full_name, "role": user.role,
            "status": _STATUS.get(user.account_status, "pending")}


@router.get("/staff/links", summary="Every practitioner and secretary, with their links (operator)")
def list_staff_links(u: dict = Depends(require_role(*OPERATORS))):
    """For the center manager's screen: who runs whose book. Names, roles and
    account status only; never a client, an appointment or a record."""
    users = [x for x in SQLUserRepository().load_all_users() if x.role in ("practitioner", "secretary")]
    links = appointment_book.all_staff_links()
    practitioners = sorted((x for x in users if x.role == "practitioner"), key=lambda x: x.username)
    secretaries = sorted((x for x in users if x.role == "secretary"), key=lambda x: x.username)
    return {
        "practitioners": [
            {**_account(p), "secretaries": sorted(s for s, q in links if q == p.username)}
            for p in practitioners],
        "secretaries": [
            {**_account(s), "practitioners": sorted(q for t, q in links if t == s.username)}
            for s in secretaries],
    }


def _staff_link(req: StaffLinkReq, u: dict) -> tuple:
    """(secretary, practitioner) for a link or unlink by `u`, or an HTTP error.
    A practitioner acts on their own book only; an operator names the book."""
    repo = SQLUserRepository()
    if u["role"] == "practitioner":
        if req.practitioner not in (None, u["username"]):
            raise HTTPException(403, "You can only change who works on your own book.")
        practitioner = u["username"]
    else:
        if not req.practitioner:
            raise HTTPException(422, "Name the practitioner whose book this is.")
        account = repo.load_user(req.practitioner)
        if not account or account.role != "practitioner":
            raise HTTPException(404, "No practitioner account with this username.")
        practitioner = req.practitioner
    # The same answer for no such account and for an account of another role.
    secretary = repo.load_user(req.username)
    if not secretary or secretary.role != "secretary":
        raise HTTPException(404, "No secretary account with this username.")
    # In a public demo one visitor must not take the demo secretary away from
    # everyone else.
    if os.getenv("VHV_DEMO_MODE", "false").lower() == "true" and req.username in DEMO_USERNAMES:
        raise HTTPException(403, "Demo accounts cannot be changed in demo mode.")
    return secretary, practitioner


def _staff_audit(action: str, u: dict, secretary: str, practitioner: str) -> None:
    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action=action,
        username=u["username"],
        device_id=get_device_id(),
        extra={"secretary": secretary, "practitioner": practitioner},
    ))


@router.post("/staff/link", summary="Add an existing secretary to a book (operator)")
def link_secretary(req: StaffLinkReq, u: dict = Depends(require_role(*OPERATORS))):
    """The secretary can then run this practitioner's appointment book and
    client cards, and still no record. A secretary may work for several
    practitioners. Only an operator links: it makes the secretary shared, which
    changes who may reset or disable them."""
    secretary, practitioner = _staff_link(req, u)
    if appointment_book.is_linked(secretary.username, practitioner):
        raise HTTPException(409, "This secretary already works on this book.")
    appointment_book.link_staff(secretary.username, practitioner)
    _staff_audit("STAFF_LINKED", u, secretary.username, practitioner)
    return {"success": True, "username": secretary.username, "practitioner": practitioner}


@router.post("/staff/unlink", summary="Remove a secretary from a book")
def unlink_secretary(req: StaffLinkReq, u: dict = Depends(require_role(*STAFF_MANAGERS))):
    """Takes effect on the secretary's next request: the links are read every
    time. The account itself stays; it is disabled separately."""
    secretary, practitioner = _staff_link(req, u)
    if not appointment_book.unlink_staff(secretary.username, practitioner):
        raise HTTPException(404, "This secretary does not work on this book.")
    # An invitation to an account that now works for nobody is withdrawn.
    if (secretary.account_status == "PENDING_ONBOARDING"
            and not appointment_book.practitioners_of(secretary.username)):
        accounts.void_codes(secretary.username, accounts.ENROLL)
    _staff_audit("STAFF_UNLINKED", u, secretary.username, practitioner)
    return {"success": True, "username": secretary.username, "practitioner": practitioner}


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
                "SELECT username, expires_at, used, purpose FROM enrollment_tokens WHERE token_hash = ?"
            ),
            (token_hash,),
        )
        row = cur.fetchone()
        # A password reset code is redeemed at /api/v1/accounts/redeem-reset.
        # Here it would set the account active, even a disabled one.
        if not row or accounts.code_purpose(_col(row, "purpose", 3)) != accounts.ENROLL:
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
        # Disabling an account voids its codes; this also covers a code left
        # over from before that rule.
        if user.account_status == "DISABLED":
            raise HTTPException(400, "This account has been disabled")

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
