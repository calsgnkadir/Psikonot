# Onboarding: how accounts come to exist

Nobody registers themselves in PsikoNot. Every account starts **pending**, with a
random password nobody knows, and becomes usable only when its holder redeems a
**single-use code** that reached them outside the system (in person, or by a
channel the practice trusts). Login is refused for any account that is not
`ACTIVE_ENROLLED`.

There are three ways an account is created. Clients have no accounts: each client
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

Practitioners and auditors are created by an administrator or security officer
(`POST /api/v1/onboarding/provision`) after their identity has been checked outside
the system. The same rules apply: a pending account, a single-use 72-hour code stored
as a hash, and no login until the code is redeemed.

Administrators and security officers are not created through the API. They co-sign
dual-control requests, and the operator who provisions an account holds its code:
one administrator could otherwise make their own second approver. For the same
reason an account never co-signs a request from the operator who provisioned it,
or the other way round (`users.provisioned_by`).

## 3. Administrators and security officers: on the server

They are created with a command run on the server itself, with the same database
settings as the service (`VHV_DATABASE_URL` or `VHV_SQLITE_PATH`):

```bash
python -m scripts.create_privileged_account --role security_officer \
    --username sec.officer2 --full-name "Second Officer"
```

The account starts pending like any other, and the command prints its single-use
enrollment code once (72 hours, stored as a hash). The holder redeems it on the
sign-in page under "Have an invitation code?". The provisioner is recorded as
`shell:<os user>`, which can never be a username.

**Shell access to the server is the second factor here.** Whoever has it already
holds the database and the key material, so the command adds no power they did
not have; what it removes is the ability of a single *API* operator to create a
co-signer. Keep shell access to the people who would be trusted with both halves
of dual control, and create the first two operators in the presence of both of
them. The command works in demo mode too: demo mode changes no rule here.

## Forgotten passwords and leavers

- **Change password** (Security page, `POST /api/v1/accounts/password`): needs the
  current password and the same password rules as redeeming a code. Every other
  session of the account ends; the one that made the change continues.
- **Reset code** (`POST /api/v1/accounts/{username}/reset-code`): a single-use code,
  valid for 24 hours, stored as a hash, delivered in person or as a link
  (`/#reset=…`), redeemed under "Forgot your password?"
  (`POST /api/v1/accounts/redeem-reset`). Redeeming it ends every session of the
  account. A practitioner resets their own secretary. Any other reset is issued by
  an administrator or security officer **with a co-signed `RESET_ACCOUNT` token for
  that username** from a second operator (not the account itself): whoever issues a
  code can redeem it and become the account, and a practitioner account opens client
  files. The issuer is recorded as the account's provisioner, so the two never
  co-sign each other's requests afterwards. A reset keeps the account's 2FA and
  passkeys, so a code that reached the wrong person is not enough to sign in where
  a second factor is set up.
- **Disable / enable** (`POST /api/v1/accounts/{username}/disable` and `/enable`):
  an operator disables any staff account but their own, a practitioner their own
  secretary. It takes effect on the next request; open codes for the account are
  voided. Re-enabling anyone but a secretary needs a co-signed `ENABLE_ACCOUNT`
  token. Old sessions do not come back with the account.

In demo mode the seeded demo accounts cannot have their published passwords
changed, be reset or be disabled.

## After sign-in: passkeys

Any account can then register a passkey (FIDO2 / WebAuthn). With
`MANDATORY_FIDO2=true`, for every role:

- an account that has a passkey **must sign in with it** — its password alone is
  refused (checked before the password, so the answer never confirms a guess);
- an account without one may sign in with its password and is taken straight to
  passkey enrolment. Refusing that login would lock a new account out for good,
  because a passkey can only be enrolled by someone who is signed in.

## Rate limit

Redeeming a code (invitation or reset) and changing a password share the sign-in
limit: 5 attempts per IP per minute. A password change is also limited per account.
