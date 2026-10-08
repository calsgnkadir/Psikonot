# Onboarding: how accounts come to exist

Nobody registers themselves in PsikoNot. Every account starts **pending**, with a
random password nobody knows, and becomes usable only when its holder redeems a
**single-use code** that reached them outside the system (in person, or by a
channel the practice trusts). Login is refused for any account that is not
`ACTIVE_ENROLLED`.

There are two ways an account is created. Clients have no accounts: each client
is a card the practitioner or their secretary adds on the **Clients** page
(`POST /api/v1/clients`). The system gives the card the next free client ID
(`CL-###`); an ID whose record chain still exists — for example, from an erased
client — is never handed out again, so a new client can never inherit someone
else's records.

## 1. A practitioner invites a secretary

A practitioner can invite the secretary who will run their appointment book
(`POST /api/v1/onboarding/invite-secretary`, "Clients" page). The account
starts pending with a single-use code, valid for 72 hours and stored only as its
SHA-256 hash, and is linked to that practitioner. The practitioner gives it in
person, or as a link with the code in the URL fragment (`/#invite=…`): browsers
do not send the fragment to the server, so the code does not land in access logs.
A secretary sees that practitioner's appointments and client cards — names,
contact details, times — and no record or note. A practitioner may hold at most
20 open invitations.

## 2. An operator provisions a staff account

Practitioners, administrators, auditors and security officers are created by an
administrator or security officer (`POST /api/v1/onboarding/provision`) after their
identity has been checked outside the system. The same rules apply: a pending
account, a single-use 72-hour code stored as a hash, and no login until the code is
redeemed.

## After sign-in: passkeys

Any account can then register a passkey (FIDO2 / WebAuthn). With
`MANDATORY_FIDO2=true`, for every role:

- an account that has a passkey **must sign in with it** — its password alone is
  refused (checked before the password, so the answer never confirms a guess);
- an account without one may sign in with its password and is taken straight to
  passkey enrolment. Refusing that login would lock a new account out for good,
  because a passkey can only be enrolled by someone who is signed in.

## Rate limit

Redeeming a code shares the sign-in limit: 5 attempts per IP per minute.
