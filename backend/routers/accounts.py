"""
backend/routers/accounts.py — change, reset, disable and re-enable accounts
===========================================================================
  POST /api/v1/accounts/password             anyone: change their own password
  POST /api/v1/accounts/{username}/reset-code  issue a single-use reset code
  POST /api/v1/accounts/redeem-reset         the code's holder sets a new password
  POST /api/v1/accounts/{username}/disable   switch an account off
  POST /api/v1/accounts/{username}/enable    switch it back on

Who may act on whom:

  practitioner          their own secretary: reset, disable, enable. A secretary
                        never sees a record, so no second person is needed.
                        Only a secretary who works for nobody else: a shared
                        front desk serves other practitioners too, and a reset
                        code would let one practitioner become an account that
                        sees the others' client cards. For a shared secretary
                        a practitioner can only remove them from their own
                        book (/api/v1/onboarding/staff/unlink); the account
                        itself is an operator's.
  admin / sec. officer  any staff account but their own. Disabling takes effect
                        at once. A reset code always needs a co-signed
                        RESET_ACCOUNT token for that username: the operator who
                        issues a code can redeem it and become the account, and
                        a practitioner account opens client files. Re-enabling
                        anyone but a secretary needs an ENABLE_ACCOUNT token.

A password change, a redeemed reset and disabling all end the account's open
sessions (core/services/accounts.py). Reset codes are stored only as a hash,
expire, work once, and count their issuer as the account's provisioner for
dual control. A reset keeps the account's 2FA and passkeys: whoever redeems a
code still needs them to sign in, so a code that went to the wrong person is not
enough on its own.
"""

import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.dependencies import TOKEN_HOURS, _get_client_ip, create_token, current_user
from backend.middleware.rate_limiter import TOO_MANY_ATTEMPTS, account_attempt_allowed
from backend.routers.auth import _set_session_cookie
from backend.schemas.requests import ChangePasswordReq, RedeemResetReq
from core.domain.entities import User
from core.events.event_bus import SystemAuditEvent, event_bus
from core.security import get_device_id, hash_password, validate_password, verify_password
from core.services import accounts, appointment_book
from core.services.alert_service import alert_service
from core.services.dual_control import dual_control_engine
from database.sql_db import DEMO_USERNAMES, LEGACY_DEMO_USERNAMES, get_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

# Roles that manage any staff account, and the accounts they manage. "client"
# accounts are switched off for good (clients no longer sign in).
MANAGER_ROLES = ("admin", "security_officer")
STAFF_ROLES = ("admin", "security_officer", "auditor", "practitioner", "secretary")
PRIVILEGED_ROLES = ("admin", "security_officer")

NOT_YOUR_SECRETARY = "You can only manage the secretaries who work for you."
SHARED_SECRETARY = ("This secretary also works for other practitioners. You can remove them from your "
                    "book; resetting or disabling the account is for an operator.")


def _demo_locked(username: str) -> bool:
    """In a public demo the seeded accounts' passwords are published: one
    visitor must not change them, reset them or switch them off for everyone."""
    return os.getenv("VHV_DEMO_MODE", "false").lower() == "true" and username in DEMO_USERNAMES


def _audit(action: str, actor: str, **extra) -> None:
    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action=action, username=actor,
        device_id=get_device_id(), extra=extra,
    ))


def _alert(alert_type: str, title: str, description: str, actor: str, request: Request,
           target: User) -> None:
    alert_service.raise_alert(
        alert_type=alert_type,
        severity="CRITICAL" if target.role in PRIVILEGED_ROLES else "HIGH",
        title=title, description=description, username=actor,
        client_ip=_get_client_ip(request),
        extra={"target": target.username, "target_role": target.role},
    )


def _target(u: dict, username: str, verb: str) -> User:
    """The account `u` may act on, or an HTTP error. Never the caller's own."""
    role = u["role"]
    if role not in MANAGER_ROLES and role != "practitioner":
        raise HTTPException(403, "Your role cannot manage accounts.")
    if username == u["username"]:
        raise HTTPException(400, f"You cannot {verb} your own account.")
    if _demo_locked(username):
        raise HTTPException(403, "Demo accounts cannot be changed in demo mode.")

    target = SQLUserRepository().load_user(username)
    if role == "practitioner":
        # The same answer for a stranger and for no such account.
        if (username not in appointment_book.staff_of(u["username"])
                or not target or target.role != "secretary"):
            raise HTTPException(403, NOT_YOUR_SECRETARY)
        # A shared front desk is not one practitioner's to switch off, and a
        # reset code would let its issuer become an account that sees the
        # other practitioners' client cards.
        if appointment_book.practitioners_of(username) != [u["username"]]:
            raise HTTPException(403, SHARED_SECRETARY)
        return target
    if not target or target.role not in STAFF_ROLES or username in LEGACY_DEMO_USERNAMES:
        raise HTTPException(404, "No staff account with this username.")
    return target


def _require_co_signature(request: Request, u: dict, target: User, request_type: str) -> None:
    """Spend a co-signed token for exactly this action on this account, or 403."""
    token_id = request.headers.get("X-Dual-Control-Token")
    if token_id and dual_control_engine.consume(token_id, target.username,
                                                request_type=request_type, username=u["username"]):
        return
    alert_service.raise_alert(
        alert_type="DUAL_CONTROL_VIOLATION_BLOCKED",
        severity="CRITICAL",
        title=f"Account change blocked for {target.username}",
        description=f"{u['username']} tried {request_type} on {target.username} without a co-signed token.",
        username=u["username"],
        client_ip=_get_client_ip(request),
        extra={"target": target.username, "request_type": request_type,
               "token_provided": bool(token_id)},
    )
    raise HTTPException(
        403,
        f"Dual-Control co-signature required: request {request_type} for {target.username} "
        "under Dual-Control Access and have a second operator approve it.")


def _set_status(username: str, status: str) -> None:
    run_sql("UPDATE users SET account_status = ? WHERE username = ?", (status, username))


def _set_password(username: str, password: str) -> None:
    run_sql("UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username))


# ── Own password ──────────────────────────────────────────────
@router.post("/password", summary="Change my password")
def change_password(req: ChangePasswordReq, request: Request, response: Response,
                    u: dict = Depends(current_user)):
    username = u["username"]
    # A stolen session must not become a way to guess the password.
    if not account_attempt_allowed(username):
        raise HTTPException(429, TOO_MANY_ATTEMPTS)
    if _demo_locked(username):
        raise HTTPException(403, "Demo accounts keep their published passwords in demo mode.")

    user = SQLUserRepository().load_user(username)
    if not verify_password(req.current_password, user.password_hash):
        _audit("PASSWORD_CHANGE_FAILED", username, ip=_get_client_ip(request))
        # Not 401: the session is fine, and the web client signs out on a 401.
        raise HTTPException(400, "The current password is not correct.")
    valid, msg = validate_password(req.new_password)
    if not valid:
        raise HTTPException(422, msg)
    if verify_password(req.new_password, user.password_hash):
        raise HTTPException(422, "The new password must differ from the current one.")

    _set_password(username, req.new_password)
    accounts.void_codes(username, accounts.RESET)
    accounts.end_sessions(username)
    # This session continues on a fresh token; every other one has ended.
    token = create_token(user.to_dict())
    _set_session_cookie(response, token)

    _audit("PASSWORD_CHANGED", username, ip=_get_client_ip(request))
    if user.role in PRIVILEGED_ROLES:
        _alert("PRIVILEGED_PASSWORD_CHANGED", f"Password changed for {username}",
               f"{username} changed their password; their other sessions were ended.",
               username, request, user)
    return {
        "success": True,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_HOURS * 3600,
        "message": "Password changed. Your other sessions have been signed out.",
    }


# ── Forgotten password ────────────────────────────────────────
@router.post("/{username}/reset-code", summary="Issue a single-use password reset code")
def issue_reset_code(username: str, request: Request, u: dict = Depends(current_user)):
    target = _target(u, username, "reset")
    if target.account_status != "ACTIVE_ENROLLED":
        raise HTTPException(
            409, "Only an active account can be reset: re-enable a disabled account first; "
                 "a pending account still has its invitation.")
    if u["role"] in MANAGER_ROLES:
        _require_co_signature(request, u, target, "RESET_ACCOUNT")

    accounts.void_codes(username, accounts.RESET)
    conn = get_sql_db().get_connection()
    cur = conn.cursor()
    try:
        code, expires_at = accounts.issue_code(cur, username, u["username"], accounts.RESET,
                                               accounts.RESET_TTL_SECONDS)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    _audit("ACCOUNT_RESET_CODE_ISSUED", u["username"], target=username, target_role=target.role)
    if u["role"] in MANAGER_ROLES:
        _alert("ACCOUNT_RESET_CODE_ISSUED", f"Password reset code issued for {username}",
               f"{u['username']} issued a password reset code for {username} ({target.role}).",
               u["username"], request, target)
    return {
        "success": True,
        "username": username,
        # Deliver it out of band. It works once, expires, and is stored only as
        # a hash: it cannot be read back from the system.
        "reset_code": code,
        "expires_at": expires_at,
        "message": "Give the reset code to the account holder in person.",
    }


@router.post("/redeem-reset", summary="Redeem a password reset code")
def redeem_reset_code(req: RedeemResetReq, request: Request):
    valid, msg = validate_password(req.new_password)
    if not valid:
        raise HTTPException(422, msg)

    token_hash = accounts.hash_code(req.reset_code)
    row = run_sql("SELECT username, expires_at, used, purpose FROM enrollment_tokens WHERE token_hash = ?",
                  (token_hash,), fetch="one")[0]
    # An invitation code is redeemed at /api/v1/onboarding/redeem.
    if not row or accounts.code_purpose(row[3]) != accounts.RESET:
        raise HTTPException(400, "Invalid or unknown reset code")
    username, expires_at, used = row[0], float(row[1]), row[2]
    if used:
        raise HTTPException(400, "This reset code has already been used")
    if time.time() > expires_at:
        raise HTTPException(400, "This reset code has expired")
    user = SQLUserRepository().load_user(username)
    if not user or user.account_status != "ACTIVE_ENROLLED":
        raise HTTPException(400, "This account cannot be reset")

    # One conditional UPDATE: of two requests with the same code, one wins.
    claimed = run_sql("UPDATE enrollment_tokens SET used = ? WHERE token_hash = ? AND used = ?",
                      (True, token_hash, False), fetch="rowcount")
    if claimed != 1:
        raise HTTPException(400, "This reset code has already been used")

    _set_password(username, req.new_password)
    accounts.end_sessions(username)

    _audit("ACCOUNT_PASSWORD_RESET", username, ip=_get_client_ip(request))
    if user.role != "secretary":
        _alert("ACCOUNT_PASSWORD_RESET", f"Password reset completed for {username}",
               f"A reset code for {username} ({user.role}) was redeemed; all their sessions were ended.",
               username, request, user)
    return {
        "success": True,
        "username": username,
        "message": "Password set. Sign in with your new password.",
    }


# ── Disable / re-enable ───────────────────────────────────────
@router.post("/{username}/disable", summary="Disable an account")
def disable_account(username: str, request: Request, u: dict = Depends(current_user)):
    target = _target(u, username, "disable")
    if target.account_status == "DISABLED":
        raise HTTPException(409, "This account is already disabled.")

    _set_status(username, "DISABLED")
    # Open codes would bring the account back (an invitation sets it active).
    accounts.void_codes(username)
    # Already refused on every request while disabled; this keeps old sessions
    # ended once it is enabled again.
    accounts.end_sessions(username)

    _audit("ACCOUNT_DISABLED", u["username"], target=username, target_role=target.role,
           previous_status=target.account_status)
    if u["role"] in MANAGER_ROLES:
        _alert("ACCOUNT_DISABLED", f"Account disabled: {username}",
               f"{u['username']} disabled {username} ({target.role}).", u["username"], request, target)
    return {"success": True, "username": username, "account_status": "DISABLED"}


@router.post("/{username}/enable", summary="Re-enable a disabled account")
def enable_account(username: str, request: Request, u: dict = Depends(current_user)):
    target = _target(u, username, "enable")
    if target.account_status != "DISABLED":
        raise HTTPException(409, "This account is not disabled.")
    # Bringing back anyone who can open records or co-sign is a second
    # person's decision. A secretary sees neither.
    if u["role"] in MANAGER_ROLES and target.role != "secretary":
        _require_co_signature(request, u, target, "ENABLE_ACCOUNT")

    _set_status(username, "ACTIVE_ENROLLED")

    _audit("ACCOUNT_ENABLED", u["username"], target=username, target_role=target.role)
    if u["role"] in MANAGER_ROLES:
        _alert("ACCOUNT_ENABLED", f"Account re-enabled: {username}",
               f"{u['username']} re-enabled {username} ({target.role}).", u["username"], request, target)
    return {
        "success": True,
        "username": username,
        "account_status": "ACTIVE_ENROLLED",
        "message": "Account enabled. If they no longer know their password, issue a reset code.",
    }
