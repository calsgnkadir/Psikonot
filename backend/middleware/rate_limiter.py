import hashlib
import logging
import os
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.dependencies import _get_client_ip
from database.sql_db import default_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder

logger = logging.getLogger("vhv.ratelimiter")

RATE_LIMIT_WINDOW = 60   # seconds
RATE_LIMIT_MAX = 5       # max 5 sign-in attempts per IP per minute
TOO_MANY_ATTEMPTS = "Too many attempts. Please wait 1 minute."

# Every endpoint that accepts a secret without a session: the password and
# passkey logins, and redeeming an enrollment / invitation code. This used to
# match "/api/auth/login" only — a path that does not exist (the API lives under
# /api/v1) — so the limit never applied and passwords could be guessed freely.
# The 2FA enable/disable codes are guessed with a stolen session; disabling
# 2FA is how the authenticator is replaced.
RATE_LIMITED_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/webauthn/login",
    "/api/v1/onboarding/redeem",
    "/api/v1/auth/2fa/enable",
    "/api/v1/auth/2fa/disable",
}

def _is_testing() -> bool:
    return os.getenv("TESTING", "false").lower() == "true"

def _check_rate_limit(ip: str) -> bool:
    """Returns True if allowed, False if rate limit exceeded using SQL persistence."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    conn = default_sql_db.get_connection()
    cursor = conn.cursor()
    try:
        # General cleanup of expired rate limits to keep database size minimal
        cleanup_sql = _to_placeholder("DELETE FROM rate_limits WHERE timestamp < ?")
        cursor.execute(cleanup_sql, (cutoff,))

        # Count attempts for this client IP in the time window
        count_sql = _to_placeholder("SELECT COUNT(*) FROM rate_limits WHERE ip = ? AND timestamp >= ?")
        cursor.execute(count_sql, (ip, cutoff))
        count = cursor.fetchone()[0]

        if count >= RATE_LIMIT_MAX:
            conn.commit()
            return False

        # Log this attempt
        insert_sql = _to_placeholder("INSERT INTO rate_limits (ip, timestamp) VALUES (?, ?)")
        cursor.execute(insert_sql, (ip, now))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        # Fallback to True under failure conditions to avoid system lockouts
        logger.error(f"[RateLimiter Warning] Database rate limit check failed ({e}). Falling back to permissive mode.")
        return True
    finally:
        cursor.close()
        conn.close()


def account_attempt_allowed(username: str) -> bool:
    """The same limit per account, for every endpoint that checks a TOTP code.
    The per-IP limit alone lets an attacker rotate addresses. Called by the
    endpoint once it knows the account (after the password at sign-in, so only
    someone who has it can use up the owner's attempts)."""
    if _is_testing():
        return True
    # Hashed to fit the 100-character key column next to the prefix.
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:32]
    return _check_rate_limit(f"account:{digest}")


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # The test suite signs in hundreds of times from one address; like the
        # CSRF check, the limit is off while TESTING is set (tests/test_rate_limit.py
        # switches it back on).
        if (request.method == "POST" and request.url.path in RATE_LIMITED_PATHS
                and not _is_testing()):
            client_ip = _get_client_ip(request)
            if not _check_rate_limit(client_ip):
                return JSONResponse(
                    status_code=429,
                    content={"detail": TOO_MANY_ATTEMPTS}
                )
        return await call_next(request)
