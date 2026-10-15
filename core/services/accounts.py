"""
core/services/accounts.py — one-time account codes and session lifetime
=======================================================================
Two pieces shared by onboarding, account management and the server-side CLI.

One-time codes. An enrollment code opens a provisioned account; a reset code
replaces a forgotten password. Both are random, single-use, expiring, and stored
only as a SHA-256 hash, so the database never holds a usable code. Each code
has a purpose and is redeemed only at the endpoint for that purpose. Whoever
issues a code can redeem it and become the account, so issuing one records the
issuer as the account's provisioner: dual control then refuses a co-signature
between the two (core/services/dual_control.py).

Session lifetime. A session token is a JWT valid for hours, and a blacklist
only covers tokens someone logged out of. Each account therefore has a session
epoch: every token carries the epoch it was issued under, and a request whose
token has an older epoch is refused (backend/dependencies.py). Raising the
epoch ends every open session of the account at once: on a password change or
reset, and when the account is disabled (so re-enabling it does not bring old
sessions back).
"""

import hashlib
import secrets
import time

from database.sql_db import run_sql, to_placeholder

ENROLL = "ENROLL"
RESET = "RESET"

# How long a holder has to redeem a code delivered out of band.
ENROLL_TTL_SECONDS = 72 * 3600
# Shorter: a reset is asked for by someone already waiting for it.
RESET_TTL_SECONDS = 24 * 3600


def hash_code(code: str) -> str:
    """Only the hash of a code is stored, never the code itself."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_code(cur, username: str, created_by: str, purpose: str = ENROLL,
               ttl_seconds: int = ENROLL_TTL_SECONDS):
    """Store a new single-use code for `username` and record `created_by` as
    the account's provisioner; return (code, expires_at). Runs on the caller's
    cursor; the caller commits."""
    code = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + ttl_seconds
    cur.execute(
        to_placeholder(
            "INSERT INTO enrollment_tokens "
            "(token_hash, username, expires_at, used, created_by, created_at, purpose) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        ),
        (hash_code(code), username, expires_at, False, created_by, now, purpose),
    )
    # Whoever holds the code can become the account, so dual-control does not
    # count the two as different people (core/services/dual_control.py).
    cur.execute(
        to_placeholder("UPDATE users SET provisioned_by = ? WHERE username = ?"),
        (created_by, username),
    )
    return code, expires_at


def void_codes(username: str, purpose: str = None) -> None:
    """Mark the account's unused codes (of one purpose, or all) as used."""
    if purpose:
        run_sql("UPDATE enrollment_tokens SET used = ? WHERE username = ? AND used = ? AND purpose = ?",
                (True, username, False, purpose))
    else:
        run_sql("UPDATE enrollment_tokens SET used = ? WHERE username = ? AND used = ?",
                (True, username, False))


def code_purpose(row_purpose) -> str:
    """Rows from before the purpose column are enrollment codes."""
    return row_purpose or ENROLL


def session_epoch(username: str) -> int:
    """The account's current session epoch; 0 for an unknown account."""
    row = run_sql("SELECT session_epoch FROM users WHERE username = ?", (username,), fetch="one")[0]
    return int(row[0] or 0) if row else 0


def end_sessions(username: str) -> None:
    """Every token issued to the account so far stops working."""
    run_sql("UPDATE users SET session_epoch = COALESCE(session_epoch, 0) + 1 WHERE username = ?",
            (username,))
