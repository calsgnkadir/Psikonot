"""
scripts/create_privileged_account.py — create an admin or security officer
==========================================================================
    python -m scripts.create_privileged_account --role security_officer \
        --username sec.officer2 --full-name "Second Officer"

The API does not create the roles that co-sign dual-control requests: the
operator who provisions an account holds its code, so one operator could make
their own second approver. These accounts are created here instead, on the
server, by someone with shell access to it. That access is the second factor:
whoever has it already holds the database and the key material.

The account is created PENDING_ONBOARDING with a random password nobody knows,
and the single-use enrollment code is printed once. Deliver it out of band; the
holder redeems it on the sign-in page ("Have an invitation code?").

Uses the same database settings as the server (VHV_DATABASE_URL or
VHV_SQLITE_PATH). Exit status 0 on success, 1 when the account is refused.
"""

import argparse
import getpass
import re
import secrets
import sys
import time
import uuid

ROLES = ("admin", "security_officer")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,50}$")


def _operator() -> str:
    """Recorded as the provisioner. The colon cannot appear in a username, so
    it never matches a real account in the dual-control provisioner check."""
    try:
        return f"shell:{getpass.getuser()}"
    except Exception:
        return "shell:unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.create_privileged_account",
        description="Create a pending admin or security officer account and print its enrollment code.")
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--username", required=True)
    parser.add_argument("--full-name", default=None, help="defaults to the role name")
    args = parser.parse_args(argv)

    if not USERNAME.match(args.username):
        print("Username must be 3-50 characters: letters, digits, . _ -", file=sys.stderr)
        return 1
    full_name = (args.full_name or args.role.replace("_", " ").title()).strip()
    if not 2 <= len(full_name) <= 100:
        print("Full name must be 2-100 characters", file=sys.stderr)
        return 1

    from core.domain.entities import User
    from core.events.event_bus import SystemAuditEvent, event_bus
    from core.security import get_device_id, hash_password
    from core.services import accounts
    from database.sql_db import get_sql_db
    from infrastructure.repositories.sql_repositories import SQLUserRepository

    repo = SQLUserRepository()
    if repo.user_exists(args.username):
        print(f"A user named {args.username} already exists.", file=sys.stderr)
        return 1

    operator = _operator()
    repo.save_user(User(
        id=f"USR-{uuid.uuid4().hex[:12].upper()}",
        username=args.username,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=args.role,
        full_name=full_name,
        account_status="PENDING_ONBOARDING",
    ))
    conn = get_sql_db().get_connection()
    cur = conn.cursor()
    try:
        code, expires_at = accounts.issue_code(cur, args.username, operator)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    event_bus.publish(SystemAuditEvent(
        project_name="__system__", action="ACCOUNT_PROVISIONED", username=operator,
        device_id=get_device_id(),
        extra={"provisioned": args.username, "role": args.role, "via": "server-cli"},
    ))

    print(f"Created {args.role} account {args.username} (pending until the code is redeemed).")
    print(f"Enrollment code: {code}")
    print(f"Expires: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(expires_at))}")
    print("Deliver the code in person. It works once and cannot be shown again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
