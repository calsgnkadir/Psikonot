"""
core/services/access_policy.py — who may see and write a client's records
=========================================================================
One policy, used by every endpoint that reads or writes a record. It used to be
re-implemented per endpoint, and the copies drifted apart: the record list hid
records the single-record endpoint still returned (see ADR-0003).

The rules, in plain words:

  File level   — a client's file belongs to the practitioner who keeps the
                 client's card (core/services/client_registry.py). No other
                 practitioner sees it: not its records, not its chain status,
                 not even whether it exists.
  Record level — everything in the file is the practitioner's own work, so the
                 owner sees all of it, except chain bookkeeping blocks and
                 records with an access level this policy does not know.
  Operators    — administrators, auditors and security officers may read a
                 client's records only with a dual-control co-signature,
                 enforced by the routers before this policy is consulted.
  Anyone else  — the practice secretary included — sees nothing. The policy
                 lists the roles that may see records and denies the rest.

Everything here is a pure function of its arguments, so the policy can be
tested without a database.
"""

from typing import Optional

PRACTITIONER_ONLY = "practitioner_only"

# Every record written now is practitioner-only. "doctor_shared" was written
# while clients still had accounts and shared records with their practitioner;
# such records stay readable for the practitioner. "private" was a client's own
# journal and stays closed, as does any level this policy does not know.
READABLE_LEVELS = {PRACTITIONER_ONLY, "doctor_shared"}

# Operators run the system. They may read a client's records only with a
# dual-control co-signature, which the routers check before this policy.
OPERATOR_ROLES = ("admin", "security_officer", "auditor")

# Every role that may be shown record content at all. Anything else — the
# practice secretary, or a role added later — sees nothing.
RECORD_ROLES = ("practitioner",) + OPERATOR_ROLES

# Blocks that keep the chain working but are not records a person wrote.
BOOKKEEPING_TYPES = ("genesis", "audit", "correction")


def can_open_file(role: str, username: str, owner: Optional[str]) -> bool:
    """May this user open the file of a client kept by `owner` (None if the
    client does not exist)? The answer is the same for a missing client and
    someone else's client, so client IDs cannot be probed."""
    if role == "practitioner":
        return owner is not None and owner == username
    return role in OPERATOR_ROLES


def can_view(role: str, record: Optional[dict]) -> bool:
    """May this user see this (decrypted) record? The caller has already
    checked the file level."""
    if not isinstance(record, dict) or role not in RECORD_ROLES:
        return False
    if role == "practitioner":
        return record.get("access_level", PRACTITIONER_ONLY) in READABLE_LEVELS
    return True


def can_view_stored(role: str, data) -> bool:
    """can_view() for a block as it is stored, before any password is given.

    A password-protected block is still a string here; its owner sees it listed
    as locked and opens it with the password they set. Bookkeeping blocks
    (genesis, audit, correction wrappers) say who did what and when — operators
    may see them, the practitioner works with the records themselves."""
    if role not in RECORD_ROLES:
        return False
    if isinstance(data, str):
        return True
    if isinstance(data, dict) and data.get("type") in BOOKKEEPING_TYPES:
        return role in OPERATOR_ROLES
    return can_view(role, data)
