from typing import Optional
from core.domain.entities import User
from core.ports.repositories import IUserRepository
from core.security import verify_password, get_device_id
import core.totp as totp
from core.events.event_bus import event_bus, SystemAuditEvent

class AuthService:
    def __init__(self, user_repo: IUserRepository):
        self.user_repo = user_repo

    def authenticate(self, username: str, password: str, client_ip: str) -> Optional[User]:
        user = self.user_repo.load_user(username)
        device_id = get_device_id()
        if not user or not verify_password(password, user.password_hash):
            if user:
                event_bus.publish(SystemAuditEvent(
                    project_name="__system__",
                    action="LOGIN_FAILED",
                    username=username,
                    device_id=device_id,
                    extra={"ip": client_ip}
                ))
            return None
        return user

    def login_success(self, user: User, client_ip: str) -> None:
        event_bus.publish(SystemAuditEvent(
            project_name="__system__",
            action="LOGIN_SUCCESS",
            username=user.username,
            device_id=get_device_id(),
            extra={"ip": client_ip, "role": user.role, "mfa": bool(user.totp_enabled)}
        ))

    def verify_totp_code(self, user: User, code: str) -> bool:
        """The only way to check a TOTP code: a code is accepted once, so one seen
        over the owner's shoulder cannot be used again within its ~90 seconds."""
        if not user.totp_secret or not code:
            return False
        step = totp.match_totp_step(user.totp_secret, code)
        return step is not None and self.user_repo.claim_totp_step(user.username, step)

    def setup_2fa(self, user: User) -> dict:
        secret = totp.generate_totp_secret()
        user.totp_secret = secret
        self.user_repo.save_user(user)

        uri = totp.get_totp_uri(user.username, secret)
        qr_code = totp.get_totp_qr_base64(uri)
        return {
            "secret": secret,
            "qr_code": qr_code
        }

    def enable_2fa(self, user: User, code: str) -> bool:
        secret = user.totp_secret
        if not secret:
            return False

        if self.verify_totp_code(user, code):
            user.totp_enabled = True
            self.user_repo.save_user(user)
            event_bus.publish(SystemAuditEvent(
                project_name="__system__",
                action="2FA_ENABLED",
                username=user.username,
                device_id=get_device_id()
            ))
            return True
        return False

    def disable_2fa(self, user: User, code: str) -> bool:
        if not user.totp_enabled:
            return False

        secret = user.totp_secret
        if not secret:
            return False

        if self.verify_totp_code(user, code):
            user.totp_enabled = False
            user.totp_secret = None
            self.user_repo.save_user(user)
            event_bus.publish(SystemAuditEvent(
                project_name="__system__",
                action="2FA_DISABLED",
                username=user.username,
                device_id=get_device_id()
            ))
            return True
        return False

