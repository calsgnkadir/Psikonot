# ADR 0003: One access policy for every record endpoint

## Status
Accepted. Amended in 7.0.0: clients no longer have accounts, so the file
belongs to the client's practitioner and consent grants are gone (see
"Amendment" below).

## Context
Who may see a client's record used to be decided separately in each endpoint:
the record list in `core/cqrs/queries.py`, and a copy of the rule in each
single-record endpoint in `backend/routers/records.py`. The copies drifted apart:

- `GET /records/{client}/{block}` checked only that a client stayed in their own
  file. **Any practitioner could read any client's unprotected records by
  walking block numbers** (an IDOR). The list hid those same records.
- A practitioner could add records to any client's file.
- Chain status was open to any practitioner.
- The Merkle proof endpoint checked nothing for a practitioner.

## Decision
All rules live in one module, `core/services/access_policy.py`, as pure
functions (`can_open_file`, `can_view`, `can_view_stored`). Every endpoint that
reads or writes a record calls it; no endpoint re-implements it.

**File level.** A client's file belongs to the practitioner who keeps the
client's card (`clients.practitioner_username`). Only that practitioner opens
it. Everyone else gets `403` from every endpoint — the same answer for a client
who does not exist, so client IDs cannot be probed.

**Record level.** Everything in the file is the practitioner's own work, and
every record written now is stored as `practitioner_only`, so the owner sees
all of it, except:

- chain bookkeeping blocks (genesis, audit, correction wrappers);
- records with a level the policy does not know (closed, never read as shared);
- a client's own `private` journal from before 7.0.0.

A record the user may not see gets `404`, as if it did not exist.

**Writing.** Only the client's practitioner adds or corrects records. A
correction keeps the original's access level.

**Operators.** Administrators, auditors and security officers may read a
client's records only with a dual-control co-signature, checked by the routers
before the policy is consulted. They never write records.

**Roles are denied by default.** The policy lists the roles that may see record
content at all — the practitioner and those operators (`RECORD_ROLES`) — and
refuses every other role. It first did the opposite (anything but client and
practitioner passed); adding a practice secretary showed that a new role would
have read every record.

## Amendment (7.0.0): ownership instead of consent
Until 6.4 clients had accounts. A client granted their practitioner consent per
record type and for a limited time, kept client-only records of their own, and
the file opened for a practitioner only while some consent was active. Records
had three levels (`doctor_shared`, `private`, `practitioner_only`).

In practice the client never used the app: the practice keeps the log and the
practitioner writes the records. Consent became a screen nobody used, and it
complicated every rule. The explicit consent KVKK asks for is now signed on
paper and its date is kept on the client's card.

So the file level became ownership, and the record levels collapsed into one.
The old values still load: `doctor_shared` stays readable for the practitioner,
`private` stays closed.

## Consequences
- **Positive:** one place to read, review and test the rules
  (`tests/test_access_policy.py` needs no database). The API tests in
  `tests/test_practitioner_file_access.py` cover the IDOR for every endpoint.
- **Positive:** the rule fits in one sentence — only the client's practitioner
  opens the file — which makes it easy to explain and to check.
- **Negative:** a client cannot see their own file in the app. They get a copy
  through their practitioner (KVKK Art. 11 export).
- **Negative:** a client cannot move to another practitioner; the card's owner
  never changes. A practice that needs this would add an explicit transfer.
