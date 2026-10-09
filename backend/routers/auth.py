import time
import os
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.responses import JSONResponse
from backend.dependencies import (
    get_auth_service, create_token, current_user, require_role,
    get_device_id, _get_client_ip, TOKEN_HOURS,
    security_bearer, get_db_manager, ensure_active_account
)
from backend.schemas.requests import (
    LoginReq, Verify2FAReq, WebAuthnRegisterReq, WebAuthnLoginReq, RevokePasskeyReq
)
from core.services.auth_service import AuthService
import core.totp as totp
from infrastructure.repositories.sql_repositories import _to_placeholder

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _public_user(user: dict) -> dict:
    """Client-safe projection of a user record — never exposes credential material."""
    return {
        "id":           user["id"],
        "username":     user["username"],
        "role":         user["role"],
        "full_name":    user["full_name"],
        "patient_id":   user.get("patient_id"),
        "totp_enabled": bool(user.get("totp_enabled")),
    }

def _set_session_cookie(response: Response, token: str) -> None:
    """The web client authenticates with this httpOnly cookie only, so every
    sign-in route must set it — password and passkey alike."""
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=os.environ.get("ENVIRONMENT", "production") == "production",
        samesite="strict",
        max_age=TOKEN_HOURS * 3600,
    )


def _has_passkey(username: str) -> bool:
    db = get_sql_db()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_to_placeholder("SELECT COUNT(*) FROM webauthn_credentials WHERE username = ?"),
                       (username,))
        return cursor.fetchone()[0] > 0


@router.post("/login", summary="User Login")
def login(
    req: LoginReq,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service)
):
    client_ip = _get_client_ip(request)

    # Mandatory passkeys (MANDATORY_FIDO2=true), for every role.
    # This used to only return a flag nothing read, for admins and clients only:
    # every password login still succeeded, so the setting enforced nothing.
    #  - An account that has a passkey must sign in with it. Its password alone no
    #    longer opens a session, so a stolen password is not enough. Checked before
    #    the password, so this answer never tells a guesser the password was right.
    #  - An account without one may sign in with its password, because a passkey can
    #    only be enrolled by someone signed in; the response tells the UI to go
    #    straight to enrolment.
    mandatory_fido2 = os.getenv("MANDATORY_FIDO2", "false").lower() in ("true", "1", "yes")
    if mandatory_fido2 and _has_passkey(req.username):
        raise HTTPException(403, "This account signs in with its passkey.")

    user_entity = auth_service.authenticate(req.username, req.password, client_ip)
    if not user_entity:
        raise HTTPException(401, "Incorrect username or password")

    ensure_active_account(user_entity, status_code=403)

    passkey_enrollment_required = mandatory_fido2

    if user_entity.totp_enabled:
        if not req.code:
            return JSONResponse(
                status_code=200,
                content={"mfa_required": True}
            )
        if not user_entity.totp_secret or not totp.verify_totp(user_entity.totp_secret, req.code):
            from core.events.event_bus import event_bus, SystemAuditEvent
            event_bus.publish(SystemAuditEvent(
                project_name="__system__",
                action="LOGIN_MFA_FAILED",
                username=req.username,
                device_id=get_device_id(),
                extra={"ip": client_ip},
            ))
            raise HTTPException(401, "Invalid 2FA verification code")

    auth_service.login_success(user_entity, client_ip)
    user = user_entity.to_dict()
    token = create_token(user)
    _set_session_cookie(response, token)

    return {
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   TOKEN_HOURS * 3600,
        "user":         _public_user(user),
        "passkey_enrollment_required": passkey_enrollment_required,
    }

@router.get("/me", summary="Current User Info")
def me(u: dict = Depends(current_user)):
    return _public_user(u)

@router.post("/logout", summary="User Logout")
def logout(
    response: Response,
    request: Request,
    creds = Depends(security_bearer),
    db_manager = Depends(get_db_manager)
):
    import jwt
    import database.storage as storage
    from backend.dependencies import JWT_PUBLIC_KEY, ALGORITHM

    token = request.cookies.get("access_token")
    if not token and creds:
        token = creds.credentials

    if token:
        try:
            payload = jwt.decode(token, JWT_PUBLIC_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                storage.blacklist_token(jti, exp, db_manager)
                storage.clean_expired_blacklisted_tokens(db_manager)
        except Exception:
            pass

    response.delete_cookie("access_token")
    return {"success": True, "message": "Logged out successfully"}

@router.post("/2fa/setup", summary="Setup 2FA TOTP")
def setup_2fa(
    u: dict = Depends(current_user),
    auth_service: AuthService = Depends(get_auth_service)
):
    from core.domain.entities import User
    user_entity = User.from_dict(u)
    # Setup would replace the secret without asking for a code, so a stolen
    # session could move 2FA to the attacker's phone. Changing the device goes
    # through /2fa/disable, which needs a code from the current one.
    if user_entity.totp_enabled:
        raise HTTPException(409, "2FA is already enabled. Disable it with a current code first.")
    res = auth_service.setup_2fa(user_entity)
    return res

@router.post("/2fa/enable", summary="Verify and Enable 2FA")
def enable_2fa(
    req: Verify2FAReq,
    u: dict = Depends(current_user),
    auth_service: AuthService = Depends(get_auth_service)
):
    from core.domain.entities import User
    user_entity = User.from_dict(u)
    if not user_entity.totp_secret:
        raise HTTPException(400, "2FA setup has not been initiated. Call /api/v1/auth/2fa/setup first.")

    if auth_service.enable_2fa(user_entity, req.code):
        return {"success": True, "message": "Two-factor authentication enabled successfully"}
    else:
        raise HTTPException(400, "Invalid verification code. Please check your app and try again.")

@router.post("/2fa/disable", summary="Disable 2FA")
def disable_2fa(
    req: Verify2FAReq,
    u: dict = Depends(current_user),
    auth_service: AuthService = Depends(get_auth_service)
):
    from core.domain.entities import User
    user_entity = User.from_dict(u)
    if not user_entity.totp_enabled:
        raise HTTPException(400, "2FA is not enabled for this account.")

    if not user_entity.totp_secret:
        raise HTTPException(500, "Database inconsistency: enabled 2FA but missing secret.")

    if auth_service.disable_2fa(user_entity, req.code):
        return {"success": True, "message": "Two-factor authentication disabled successfully"}
    else:
        raise HTTPException(400, "Invalid verification code. Disable failed.")


# ── WEBAUTHN / PASSKEY HARDWARE AUTHENTICATION ─────────────────
from database.sql_db import get_sql_db
from core.webauthn import (
    WebAuthnError, challenge_store, verify_assertion, verify_registration
)

@router.get("/webauthn/challenge", summary="Generate WebAuthn Challenge")
def get_webauthn_challenge():
    """Issues a single-use, TTL-bound challenge for a registration or login ceremony."""
    return {"challenge": challenge_store.issue()}

@router.post("/webauthn/register", summary="Register Passkey / WebAuthn Hardware Credential")
def register_webauthn_credential(
    req: WebAuthnRegisterReq,
    u: dict = Depends(current_user)
):
    try:
        verify_registration(req.public_key, req.client_data_json)
    except WebAuthnError as e:
        raise HTTPException(400, f"Passkey registration rejected: {e}")

    db = get_sql_db()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username FROM webauthn_credentials WHERE credential_id = ?",
            (req.credential_id,)
        )
        if cursor.fetchone():
            raise HTTPException(409, "This passkey credential is already registered.")
        cursor.execute(
            "INSERT INTO webauthn_credentials (credential_id, username, public_key, created_at) VALUES (?, ?, ?, ?)",
            (req.credential_id, u["username"], req.public_key, time.time())
        )
        conn.commit()

    from core.events.event_bus import event_bus, SystemAuditEvent
    event_bus.publish(SystemAuditEvent(
        project_name="__system__",
        action="PASSKEY_REGISTERED",
        username=u["username"],
        device_id=get_device_id(),
        extra={"credential_id": req.credential_id[:24]},
    ))

    return {"success": True, "message": "Passkey / WebAuthn hardware credential registered successfully!"}

@router.post("/webauthn/login", summary="Login with Passkey / WebAuthn Credential")
def login_webauthn_credential(
    req: WebAuthnLoginReq,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service)
):
    client_ip = _get_client_ip(request)

    db = get_sql_db()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, public_key, sign_count FROM webauthn_credentials WHERE credential_id = ?",
            (req.credential_id,)
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(401, "Invalid or unregistered Passkey credential.")
    username, public_key, sign_count = row[0], row[1], row[2]

    # Cryptographically verify the assertion BEFORE issuing any session token.
    try:
        new_sign_count = verify_assertion(
            public_key_spki_b64=public_key,
            client_data_json_b64=req.client_data_json,
            authenticator_data_b64=req.authenticator_data,
            signature_b64=req.signature,
            stored_sign_count=sign_count or 0,
        )
    except WebAuthnError as e:
        from core.events.event_bus import event_bus, SystemAuditEvent
        event_bus.publish(SystemAuditEvent(
            project_name="__system__",
            action="PASSKEY_LOGIN_FAILED",
            username=username,
            device_id=get_device_id(),
            extra={"ip": client_ip, "reason": str(e)},
        ))
        raise HTTPException(401, f"Passkey authentication failed: {e}")

    user_entity = auth_service.user_repo.load_user(username)
    if not user_entity:
        raise HTTPException(404, "User account not found.")

    # Same gate as password login: no session for an inactive account, even
    # with a valid passkey.
    ensure_active_account(user_entity, status_code=403)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE webauthn_credentials SET sign_count = ? WHERE credential_id = ?",
            (new_sign_count, req.credential_id)
        )
        conn.commit()

    auth_service.login_success(user_entity, client_ip)
    user = user_entity.to_dict()
    token = create_token(user)
    _set_session_cookie(response, token)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _public_user(user),
        "message": f"Successfully authenticated via Passkey for {username}"
    }

@router.post("/webauthn/revoke", summary="Revoke Stolen / Lost Passkey Hardware Credential")
def revoke_webauthn_credential(
    req: RevokePasskeyReq,
    request: Request,
    u: dict = Depends(require_role("admin", "security_officer"))
):
    from core.services.dual_control import dual_control_engine
    if u.get("role") == "admin":
        dc_token = request.headers.get("X-Dual-Control-Token") or request.query_params.get("dual_control_token")
        if not dc_token or not dual_control_engine.is_dual_control_approved(
                dc_token, req.username, request_type="REVOKE_PASSKEY", username=u["username"]):
            raise HTTPException(403, "Dual-Control Co-Signature Required to Revoke Passkey Credential.")

    db = get_sql_db()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM webauthn_credentials WHERE credential_id = ? AND username = ?", (req.credential_id, req.username))
        deleted = cursor.rowcount > 0
        conn.commit()

    if not deleted:
        raise HTTPException(404, f"Passkey credential {req.credential_id} for user {req.username} not found.")

    from core.services.alert_service import alert_service
    alert_service.raise_alert(
        alert_type="PASSKEY_REVOKED",
        severity="HIGH",
        title="Hardware Passkey Revoked",
        description=f"Passkey credential {req.credential_id} for user {req.username} was revoked by {u.get('username')}.",
        username=u.get("username"),
        client_ip=_get_client_ip(request)
    )

    return {"success": True, "message": f"Passkey credential {req.credential_id} revoked successfully for user {req.username}."}
