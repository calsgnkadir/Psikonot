# Changelog — PsikoNot (formerly Mahrem, and before that VIP Health Vault)

Entries before 7.1.0 use the name the project had at the time.

## [7.1.0] - 2026-10-05

### 🏷️ Renamed — Mahrem is now PsikoNot

- The app is called **PsikoNot** ("psychologist's notes"): the page title, sign-in
  screen, menus, API title, TOTP issuer, passkey display name, the data-export file
  name (`psikonot-export-….json`), the Docker service (`psikonot`, container
  `psikonot_app`), CI and every document.
- The demo practice is now "Yılmaz Psychology Practice": a practice's name is not the
  app's name.
- Nothing a running install depends on changed: the database, the record chains, the
  environment variables (`VHV_…`) and the stored keys keep their names, so existing
  data, passwords, passkeys and authenticator codes keep working. An authenticator app
  shows the old label until the account re-enrols TOTP.

## [7.0.1] - 2026-10-05

### ⚡ Fixed — opening a long file was slow

- Every block of one client's file is encrypted under the same key (the same secret
  and the same per-client salt), but the 600,000-iteration PBKDF2 derivation ran again
  for every block. Opening a file took about 0.13 s per record: 30 records 4 s, a file
  of 1,151 blocks about 2 minutes.
- `AESGCMStrategy` now keeps each derived key for the lifetime of the object, and one
  object lives for one request, so a request derives a client's key once. No key
  outlives its request, so nothing is left in memory after a client is erased.
- The KMS provider splits AES-GCM from key derivation (`encrypt_with_key`,
  `decrypt_with_key`); the stored format is unchanged and old records still open.
- Measured: 30 records 3.98 s → 0.15 s; 300 records 0.32 s; 1,151 blocks under 1 s.
  The test suite runs in about 47 s instead of 100.
- `tests/test_key_derivation_reuse.py` counts the derivations (12 records → 1); it fails
  on the old code with 12.

## [7.0.0] - 2026-10-05

Mahrem becomes the practice's own log: clients no longer sign in. The security
engineering now sits where it matters — what was said in the sessions.

### 💥 Changed — clients are cards, the file belongs to the practitioner

- **Client cards.** Each client is a card owned by one practitioner: client ID,
  name, phone, e-mail and the date the KVKK forms were signed on paper
  (`core/services/client_registry.py`, `/api/v1/clients`). The practitioner and their
  secretary add and edit cards.
- **The tally.** The client list shows, per client, the sessions attended, missed
  and cancelled, the last visit and the next appointment. The appointment book now
  says "Came" / "Did not come".
- **Ownership instead of consent.** A client's file opens only for the practitioner
  who keeps the card (`access_policy.can_open_file`); every new record is
  practitioner-only, so the access-level choice is gone from the form. Another
  practitioner gets the same `403` as for a client who does not exist. Operators
  still need dual control; the secretary still sees no record (default deny).
- **KVKK through the practitioner.** The practitioner downloads a copy of a client's
  data (Art. 11) and files their erasure request (Art. 17); operators carry it out
  with dual control. An erasure now also deletes the client's card and appointments.
- **The access ledger** is now the practitioner's view: who opened their client's file.

### 🗑️ Removed

- **Invoicing** (6.3.0): invoices are issued by the accountant or through the GİB
  portal; Mahrem is not a billing tool.
- Client accounts and everything that served them: consent grants and the consent
  page, client invitations, the privacy-notice gate, My Data, client notifications,
  client-only records and the protected-record audience metadata. Existing client
  accounts are disabled on start.
- `docs/CONSENT_FLOW.md`.

### 🔧 Other

- `VHV_PROJECTS_DIR` moves the record store, so tests can run on a fresh one (old
  local test data made long files — and the test suite — slow).
- The end-to-end smoke test and the screenshot scripts follow the new model.
- 266 tests.

## [6.4.0] - 2026-09-27

### ✨ Added — KVKK screens

- **Privacy notice and explicit consent**: a client reads the notice (aydınlatma metni) and
  gives explicit consent before using the app; the accepted version, time and IP are
  recorded, and a new notice version asks again. The text is a template for the practice
  to replace.
- **My Data**: a client downloads a copy of their data (KVKK Art. 11) — the records they
  can see (locked ones stay locked), appointments, invoices, consents and who accessed
  their records — and can request erasure.
- **Erasure requests** (admin, KVKK officer): the request erases nothing by itself. The
  operator erases the client with the existing crypto-shred, which needs dual control, and
  can close the request as done only once the key is really gone; otherwise reject it
  (e.g. a legal duty to keep records).
- **Security alerts** (admin, KVKK officer): the alerts the system already raised — such
  as an operator trying to read records without dual control — are now on a screen, with
  acknowledge.
- **README**: new screenshots (appointment book, invoice, a transcript only the practitioner
  sees) and an 11-scene walkthrough covering the practitioner, the secretary, a client's
  first sign-in with the privacy notice, and an erasure request that needs dual control.

## [6.3.0] - 2026-09-27

### ✨ Added — invoices for completed sessions

- A practitioner or their secretary invoices a completed session from the appointment
  book: net amount and VAT rate (0, 1, 10 or 20%); the invoice gets the next number for
  that practitioner and year (`2026-0001`, ...). One invoice per session; only a
  completed session can be invoiced.
- Nothing clinical on an invoice: the service line is a fixed text and there is no
  free-text field. Names are copied in when it is issued, so it never changes afterwards.
  Amounts are integer kuruş, never floats. No payment tracking, by design.
- A client sees and prints their own invoices. A printable view prints only the invoice
  ("Print / save as PDF").
- Demo: two of the completed demo sessions are invoiced.

## [6.2.0] - 2026-09-27

### Record types: what a practitioner actually keeps

- **Removed scoring**: the "assessment" record type (questionnaire scores such as
  GAD-7), its schema and the dashboard progress chart. Mahrem keeps what is said and
  written, not scores. Existing assessment records still load and display.
- **Added "Client Profile"**: who the client is and why they came — presenting problem,
  characteristics, background.
- **Added "Session Transcript"**: what was said, written down. **Always Practitioner
  Only**, enforced by the server (`access_policy.ALWAYS_PRACTITIONER_ONLY`), not just
  pre-selected in the form: a shared transcript is refused with 422 and a client cannot
  write one. The form locks the access level and suggests a password lock on top.
- No messaging between client and practitioner, by design.

## [6.1.0] - 2026-09-27

### 🔒 Fixed — a new role would have read every record

- The access policy allowed every role other than client and practitioner, on the
  assumption that the rest were operators stopped by dual control; the record endpoints
  and the consent list followed the same pattern. Adding a secretary role exposed it:
  against the old policy, six endpoints answered a secretary with record data or consent
  lists. The policy now lists the roles that may see records (`RECORD_ROLES`) and denies
  every other role (ADR-0003).

### ✨ Added — appointment book and secretary role

- **Appointments** (`/api/v1/appointments`): a practitioner and their secretary book,
  move and cancel sessions and mark them completed or missed; a client sees their own and
  may cancel. No double booking; no free-text field, so clinical content cannot reach the
  unencrypted table.
- **Secretary role**: invited by a practitioner (single-use code), linked to that
  practitioner's book only, and refused by every record endpoint. Demo account
  `secretary.ayse`.
- UI: an Appointments page for all three roles, the next appointments on the
  practitioner dashboard, and a secretary card on My Clients.

## [6.0.2] - 2026-09-26

### 🔒 Removed a way around onboarding

- **`POST /api/v1/admin/users` let an admin create an active account with a password
  directly**, skipping the pending state and the single-use code that README, SECURITY
  and the onboarding doc describe as the only way in. Nothing in the UI used it. The
  endpoint, its `UserCreate` schema (which also still allowed a `nurse` role) and the
  command/service/factory behind it are removed; `tests/test_onboarding.py` pins that
  the route is gone.

### 🧹 Removed what the old vault left behind

- **CSS**: 45 rules for views that no longer exist (AI triage, appointments, vaccines,
  medications, wearables, ECG status, old sidebar categories) — 296 lines.
- **`start_web.ps1`** showed the removed demo accounts and bound the demo to `0.0.0.0`,
  exposing accounts with public passwords to the local network, and ran from the
  `backend/` folder, so it minted its own keys there. It now matches the README:
  loopback only, port 8000, current demo accounts.
- **CI**: the `VHV_LIS_API_KEY` setting for the laboratory gateway removed long ago.
- **Docs**: `PRIVATE_VPC_DEPLOYMENT.md` claimed that hosting in Türkiye "guarantees
  100% compliance" with KVKK; it now says what it actually covers (no cross-border
  transfer) and what it does not. The KVKK data inventory lists therapy records instead
  of vital signs, prescriptions and lab results.
- **Wording**: theme names ("Stealth Dark", "Command Center"), code comments and test
  sample data (prescriptions, lab results, cardiology) now fit a psychology practice.

## [6.0.1] - 2026-09-25

### 🧹 Leftovers of the old name

- **`scripts/e2e_smoke.py` had been broken**: it signed in as the removed `VIP`
  account and called SIWE and DID/VC endpoints that no longer exist. Rewritten for
  Mahrem: the client gives consent, the practitioner reads the file, the client
  revokes it, the practitioner's access closes — then the demo is restored. It signs
  in only twice, within the sign-in rate limit.
- **Docker**: the service is `mahrem` and the container `mahrem_app`; the obsolete
  compose `version` key (a warning on every command) is gone. Volumes keep their
  names, so existing demo data carries over.
- **`VHV_IP_ALLOWLIST_ENABLED`** replaces `VIP_IP_ALLOWLIST_ENABLED`, like the other
  `VHV_` settings. The old name is still read, so deployments keep their setting.
- `isVip` → `isClient` in the web client; `test_vip_security_hardening.py` →
  `test_security_hardening.py`; the Fly app placeholder is `CHANGE-ME-mahrem-demo`.

## [6.0.0] - 2026-09-24

### 🔄 Pivot: VIP Health Vault → Mahrem

The vault became a client-records system for independent psychologists. The
security core (encryption at rest, signed chain, access ledger, dual control,
crypto-shred) stayed; the domain changed around it.

- **Roles**: `doctor` → `practitioner`, `vip_patient` → `client` (migrated in
  place); client IDs are `CL-###`. Old demo accounts are disabled.
- **Record types** for therapy: session notes, assessments (GAD-7, PHQ-9, …),
  treatment plans, homework, consent forms. Medical views (vitals, allergies,
  prescriptions) were removed.
- **Break-glass removed.** A private practice has no emergency-access need that
  would justify a path around consent.

### 🛡️ Fixed — access control

- **IDOR on `GET /records/{client}/{block}`**: any practitioner could read any
  client's unprotected records by walking block numbers, with no consent. The
  rules had been copied into each endpoint and drifted apart; they now live in
  one module, `core/services/access_policy.py` (ADR-0003), called by every
  record endpoint.
- **No consent, no file**: a practitioner without consent gets the same `403`
  for a client's records, chain status and proofs as for a client who does not
  exist. Writing a record needs consent too; notifications are client-only.
- **The sign-in rate limit never applied**: it matched `/api/auth/login`, not the
  real `/api/v1/auth/login`. It now covers password login, passkey login and
  invitation codes.
- **CI had skipped the tests since 21 August**: a lint error failed the first
  step. Fixed; lint, Bandit and the suite pass.

### ✨ Added

- **Practitioner-only notes** (process notes): visible to their author, never to
  the client.
- **Client invitations**: a practitioner invites a client by name; the system
  picks the next free `CL-###` (never one that still has a chain) and returns a
  single-use code. The invitation grants no access.
- **Practitioner dashboard**: the practitioner's own clients (consented or
  invited), and a progress chart of outcome measures over time.
- **Role-aware wording** from one table (`ROLE_TEXTS`).

## [5.10.0] - 2026-08-23

### 🩺 Fixed — clinical detail leaked into a plaintext notification

A general review (buttons, routes, encryption) turned up one real privacy gap.

- **A new-prescription notification embedded the medication name** in its message,
  and notifications live in the SQL store in plaintext. So a medication the chain
  had AES-encrypted (e.g. "Ramipril" → hypertension) was readable by anyone with the
  SQL database but not the chain key — partially defeating encryption at rest for
  that data point. The notification now points the patient to their records instead
  of repeating the clinical content; notifications carry no PHI.

Reviewed and confirmed sound in the same pass: every `data-action` button (48)
maps 1:1 to a registered handler (no dead handlers, no broken buttons across
click/submit/change/input); every frontend API call resolves to a real route; and
the block/encryption path is correct — AES-256-GCM with a fresh random 96-bit nonce
per write (no nonce reuse), the block `data` is always ciphertext (server-key or
server-blind password), and the chain verifies own-hash, `previous_hash` linkage,
timestamp/nonce, and the HMAC signature on every block.

## [5.9.0] - 2026-08-23

### 🍪 Session token off JavaScript-readable storage

- **The auth token is now only an httpOnly, SameSite=Strict cookie.** The client
  used to keep it in `localStorage` (`vhv_token`) and send it as a bearer header,
  so any script that ran on the page could read it. The server already issued and
  accepted the cookie, and CSRF is already enforced (double-submit
  `csrf_token`), so the client now relies on the cookie alone — no token in JS
  storage, no bearer header. Verified in a browser: after login the session
  works, `localStorage.vhv_token` is `null`, and `document.cookie` cannot see the
  session token (only the CSRF token, by design). The short-lived Dual-Control
  token, which must be JS-readable to be sent as a header, moved from
  `localStorage` to `sessionStorage` (never written to disk, cleared on tab close).

### 🚫 Demo accounts can never be seeded in production

- Starting with `ENVIRONMENT=production` **and** `VHV_DEMO_MODE=true` now refuses
  to seed the default demo accounts (whose passwords are in the README) and logs a
  CRITICAL warning, instead of silently standing up `admin` with a public password.

### 🧹 Repository hygiene

- Deleted **`render.yaml`** — a one-click deploy config for Render's public cloud,
  which directly contradicts the private-VPC-only mission (and, with the new
  signing-key / pseudonym-secret guards, could not boot there anyway).
- Deleted the duplicate **`backend/requirements.txt`** (out of sync with the root
  file — it was missing `keyring`, the package that stops the signing key falling
  back to a plaintext file). The root `requirements.txt` is the single source of
  truth used by the Dockerfile and CI.
- Deleted **`test_architecture_upgrades.py`** (superseded: it asserted the old
  "sanitise on input" XSS behaviour that was deliberately replaced with
  store-verbatim / escape-on-render) and moved **`test_e2e_api.py`** — a live-server
  smoke script, not a unit test — to `scripts/e2e_smoke.py`, out of the test
  discovery path. Removed the personal `workspace.code-workspace` and gitignored
  `*.code-workspace`.

## [5.8.0] - 2026-08-21

### 🔒 Secret-guard hardening & auth-bypass audit

A focused pass for "free login" / default-secret holes. Two real gaps closed; the
rest of the auth stack was reviewed and confirmed sound.

- **Pseudonymisation secret now fails closed in production.** `anon_id =
  HMAC(secret, patient_id)`, and the engine silently fell back to the shipped
  default `VHV_PSEUDONYM_SECRET_CHANGE_IN_PRODUCTION` when `PSEUDONYM_SECRET` was
  unset — so anyone with the code could recompute a patient's pseudonym, defeating
  the decoupling and erasure story. Production now refuses to boot on the default
  (dev/test/demo still use it). Documented in `.env.example`.
- **Passkey login now honours the onboarding gate.** `/auth/webauthn/login`
  verified the assertion but did not check `account_status`, so a non-activated
  account with a passkey could get a session that password login would refuse. It
  now applies the same `ACTIVE_ENROLLED` gate.

Reviewed and confirmed **not** vulnerable: JWT is RS256 with a locally-generated,
persisted key (no hardcoded secret, no `alg:none`), and `current_user` re-loads the
role from the database rather than trusting token claims — so a forged or tampered
token cannot escalate privilege. Passkey login is cryptographically verified before
any token is issued (single-use challenge, origin/rpId binding, clone detection).
Default demo accounts are seeded only in dev/demo, never in production. No `eval` /
`exec` / `pickle` / shell execution, and all SQL is parameterised.

## [5.7.0] - 2026-08-21

### 🧨 Right to be forgotten — crypto-shredding erasure (GDPR/KVKK Art. 17)

Erasure on an append-only, tamper-evident chain cannot delete blocks without
breaking the integrity the vault exists to prove. It is done by crypto-shredding
instead.

- **The at-rest key now depends on a per-patient erasure secret.** It is derived
  from the KMS root AND a random per-patient secret (`patient_erasure_keys`,
  created lazily on first write). Both are required to decrypt. *(Breaking: the
  at-rest derivation changed, so a store written before this must be re-seeded.)*
- **`POST /api/v1/erasure/{patient_id}` destroys that secret.** Every record
  encrypted under it becomes permanently undecryptable — read-back returns an
  `__erased__` marker, never plaintext — while the chain blocks and their
  signatures remain intact and `is_chain_valid` still passes. The identity↔pseudonym
  mapping is dropped and a tombstone is written to the tamper-evident access ledger.
- **Privileged + Dual-Control gated.** Only an admin/security officer with a
  co-signed Dual-Control token can erase; the operation is irreversible and
  idempotent. Verified end-to-end: sensitive text is readable before and gone
  after, with the chain still valid.

## [5.6.0] - 2026-08-21

### 🪪 Out-of-band account onboarding (implemented for real)

The README claimed the vault "satisfies out-of-band identity onboarding
requirements," but nothing enforced it — every account was effectively active the
moment it existed, and `account_status` was an entity field the database never
even stored. That claim is now true.

- **`account_status` is a real, persisted column** (`ACTIVE_ENROLLED` /
  `PENDING_ONBOARDING`), added to the users table and the repository mapping.
  Existing and seeded accounts default to `ACTIVE_ENROLLED`.
- **Provisioning is operator-only and out of band.** `POST
  /api/v1/onboarding/provision` (admin or security officer) creates a vetted
  account in `PENDING_ONBOARDING` with a locked random password and issues a
  single-use enrollment token; the operator delivers that token out of band. Only
  a hash of the token is stored — it cannot be recovered from the system.
- **Redemption activates the account.** `POST /api/v1/onboarding/redeem` takes the
  token and a new password (subject to the password policy), sets the credential,
  flips the account to `ACTIVE_ENROLLED`, and burns the token. Tokens are
  single-use and expire (72h delivery window).
- **Login is gated.** A non-`ACTIVE_ENROLLED` account is refused at `/auth/login`
  with a 403, so a provisioned-but-unredeemed account can never be used.

## [5.5.0] - 2026-08-21

### 🔐 Externally-held signing key (HSM / Vault-ready)

Closes the "a rogue admin has both the database and the key" gap.

- **The KMS interface moved from "hand over the key" to "sign for me."** Every
  key use in the vault — block signatures, the notarizer anchor, and the at-rest
  key derivation — is a MAC over some context, so they now all go through a single
  `KMSProvider.mac()` primitive. Raw key material no longer flows through
  application code. For the default software provider the output is byte-for-byte
  identical to before, so existing signatures and encrypted data remain valid — no
  migration.
- **HashiCorp Vault Transit provider.** With `KMS_PROVIDER=vault` the MAC is
  computed by Vault's `/transit/hmac` endpoint under a key held in Vault; the key
  never enters this process, so an operator with the host and the `projects/` store
  still cannot forge a signature or derive an at-rest key. It fails closed — a
  broken Vault stops signing rather than silently falling back to a local key.
  `get_signing_key()` is refused. Tested end-to-end against a fake Vault endpoint
  (sign/verify roundtrip, right URL/token, no local key, fail-closed).
- Config documented in `.env.example` (`KMS_PROVIDER`, `VAULT_ADDR`,
  `VAULT_TOKEN`, `VHV_KMS_TRANSIT_KEY`); README feature matrix and roadmap updated.

## [5.4.0] - 2026-08-21

### ✂️ Cut list — remove scope drift

Three residual features that did not fit a single-tenant, single-subnet vault for
a few individuals were removed outright.

- **`wallet_address` field deleted.** A dead column carried through the `User`
  entity, the SQL schema and the repository, never read by any logic — a leftover
  from an abandoned wallet-identity idea. Removed from the entity, `CREATE TABLE`,
  the migration, `_USER_COLS`, and every INSERT/UPDATE/SELECT mapping. Because it
  was the last column, existing rows read back cleanly (the trailing value is
  simply ignored).
- **LIS laboratory webhook gateway deleted.** `POST /api/v1/webhooks/lis`, its
  authentication, the `LisWebhookPayload` schema and `VHV_LIS_API_KEY` are gone;
  there is no real lab-integration contract, so the endpoint was only attack
  surface. The genuinely general tests that lived in its test file
  (path-traversal confinement, at-rest ciphertext, cross-patient metadata) moved
  to `tests/test_storage_and_metadata_safety.py`, rewritten to use the normal
  record path.
- **IPFS attachment storage folded into the encrypted LMDB store.** A distributed
  content network was never the fit for one private subnet. Attachments (already
  AES-encrypted) now live in the same LMDB store as the chain, content-addressed
  by the SHA-256 of the ciphertext (`core.services.attachment_store`). The IPFS
  client, its simulation-mode fallback, its network egress path and
  `VHV_IPFS_API_URL` are removed. Verified end-to-end: an imaging file uploads,
  downloads byte-for-byte, and the plaintext is not recoverable from the blob on
  disk.

## [5.3.0] - 2026-08-21

### 🎯 Scope realignment — closing the gap between claimed and actual behavior

A line-by-line audit of the mission ("single-tenant, private, tamper-evident
vault for a few protected individuals") flagged three things the README claimed
but the code did not do. Two are now fixed.

- **Identity pseudonymization is wired into the write path** (was: an isolated
  admin API that no record ever touched). The clinical chain store is now keyed by
  a deterministic `anon_id` (HMAC-SHA256 of the patient id under `PSEUDONYM_SECRET`),
  never the raw identifier — so the block store on disk holds only opaque
  pseudonyms. All read/write paths derive the store name from one canonical
  `project_name_for()`, so they cannot drift apart; the write path persists the
  reverse mapping for authorized resolution. Verified end-to-end: a record for
  `VIP-042` lands under `patient_18ce198d…`, the raw id names nothing on disk, and
  the server still decrypts and resolves it. **Breaking:** existing chains keyed by
  the raw id are not found under the new naming and must be re-seeded/migrated.
- **The notarizer anchor is a real signature, not a random number.** It previously
  generated `anchor_tx = f"0x{secrets.token_hex(32)}"` — a random token dressed up
  as a blockchain transaction hash — and stored it via `save_simulated_merkle_root`.
  The anchor is now an **HMAC-SHA256 signature of the Merkle root** under the KMS
  key; verification recomputes and checks it in constant time, so a tampered block
  fails as "Anchor signature invalid." The status API and UI drop the misleading
  `on_chain_tx_hash` / "Simulated Anchor" / "On-Chain Verified" wording for honest
  `anchor_signature` / "Local Signed Anchor" labels.

Every code-side item from that audit is now shipped: the `wallet_address`,
LIS-gateway and IPFS cut-list items in 5.4.0; the externally-held signing key in
5.5.0; out-of-band onboarding in 5.6.0; key-destruction erasure in 5.7.0. What
remains is non-code (institutional partner, legal counsel).

## [5.2.0] - 2026-08-21

### 🔑 Key-management hardening & honest documentation

The signing key both signs blocks and derives the at-rest encryption key, so its
handling is now treated as a first-class operational concern rather than an
implementation detail.

- **`keyring` is now a declared dependency** — the code advertised OS-keyring
  storage (DPAPI / Keychain / Secret Service) but never listed the package, so a
  clean install silently fell back to a plaintext `.private_key` file next to the
  encrypted chain store. `keyring==25.2.1` is now in `requirements.txt`.
- **Production refuses to auto-generate a signing key** — a key minted on boot
  would either land in plaintext on disk or orphan every record already encrypted
  under a previous key. Startup now fails with clear guidance unless a key is
  configured, with a `VHV_ALLOW_GENERATED_KEY=true` opt-in for a first-run install
  that has no data. Plaintext-on-disk storage logs a warning on every startup.
- **Honest threat model** — the README no longer implies the ciphertext is safe
  unconditionally; it is safe only while the key is kept out of the data backup.
  New [docs/KEY_MANAGEMENT.md](docs/KEY_MANAGEMENT.md) documents key sources,
  backup, and the rotation procedure.

### 🐞 Fixed — silent loss of access-log entries (audit trail)

- **The access ledger keyed entries on a wall-clock timestamp** (`time.time_ns()`),
  whose resolution on Windows is ~15.6 ms. Two access events in the same tick
  produced the **same LMDB key**, so the second silently overwrote the first — the
  audit trail could lose reads under any real load. Entries are now keyed on the
  monotonic, collision-free sequence number, so every access is stored and the
  hash-chain verifies deterministically. Confirmed: four back-to-back appends
  within one clock tick now store four rows (previously one). This also removes a
  timing-dependent flake in `tests/test_access_ledger.py`.

### 🧹 Removed misleading configuration

- **`.env.example` no longer advertises a public-chain integration that does not
  exist** — the `VHV_RPC_URL` (Polygon/Alchemy), `VHV_PRIVATE_KEY` and
  `VHV_CONTRACT_ADDRESS` lines contradicted the README's "Zero Web3/RPC
  dependencies" and were removed. The file now documents the real signing-key and
  file-attachment settings instead.
- **File attachments documented honestly** — the IPFS client is a real, working
  code path that AES-encrypts attachments and, with no daemon configured, stores
  them locally without any network call. It now appears in the feature matrix
  rather than being an undocumented surface.

## [5.1.0] - 2026-08-18

### 🔐 Security Audit — Findings and Remediation

Each item below was reproduced against a running instance before it was fixed, and
is covered by a regression test.

#### Authentication
- **Passkey login accepted any known credential id** — `/api/v1/auth/webauthn/login` issued a session token without checking the challenge, signature, origin, relying party or counter, and `init_db` seeded `passkey_default_demo → vip001` in every environment. Clicking "Sign in with Passkey" was enough to become the VIP patient. Assertions are now verified in `core/webauthn.py` (single-use TTL challenge, origin and rpId binding, User Present flag, ES256 signature, clone detection); the seeded credential is deleted on startup.
- **Credential material returned to the client** — the same endpoint answered with the full user record including the argon2 password hash, and fell back to the first account in the database when the credential owner could not be resolved.
- **Passkeys could not be enrolled** — no UI called the registration endpoint, so only the seeded credential ever worked. Enrolment lives in Security settings.
- **Session expiry was invisible** — a rejected token produced 401s while the UI still looked signed in. `apiFetch` now ends the session, and the stored token is validated on startup.

#### Authorization
- **Practitioners could grant themselves consent** — grant and revoke only rejected the `vip_patient` role, so a doctor could revoke a patient's decision and authorise themselves in its place, bypassing Break-Glass auditing. Consent is now a patient-only operation.
- **Dual-Control covered administrators only** — auditor and security officer roles could read raw records unchecked. Every non-clinical operator role now needs an M-of-N co-signature, and the flow is drivable from the new Dual-Control Access screen with a seeded Security Officer as the second principal.

#### Integrity
- **The laboratory gateway required no credentials** — `/api/v1/webhooks/lis` appended blocks to a patient's chain for anyone who could reach the API, and skipped the write path's sanitiser. It now requires `VHV_LIS_API_KEY` and fails closed.
- **Path traversal through the gateway** — its patient identifier became a directory name, so `../../../escaped_chain` created an LMDB store outside the projects directory. The identifier is validated and the store is confined at the connection manager.
- **The Merkle anchor was always one write behind** — anchoring ran inside the unit of work and read a pre-commit snapshot, so every chain reported "Merkle root mismatch". It now runs after commit.
- **The first record on a new patient chain failed with 500** — seeding the genesis block dropped the LMDB environment underneath the caller's open transaction.

#### Disclosure
- **Cross-patient metadata** — any signed-in patient could read another patient's chain length, Merkle root and anchoring transaction.
- **Device fingerprint** — the unauthenticated liveness probe returned it in full.

#### XSS
- **Inline event handlers forced a permissive CSP** — 76 `onclick`/`onchange`/`oninput`/`onsubmit` attributes meant `script-src` had to allow `'unsafe-inline'`, which is the directive that would have stopped an injected handler from running. All of them are replaced by `data-action` declarations resolved through a delegated dispatcher, and the policy is now `script-src 'self'`. Attachment bytes and record passwords, previously interpolated into `onclick` attributes, are held in a client-side stash and referenced by key.
- **Escaping happened in the wrong layer** — input was HTML-escaped on the way in (twice), which corrupted clinical text permanently in an append-only chain ("Dr. Smith & Co" → "Dr. Smith &amp;amp; Co", a dose of "<5 mg" → "&lt;5 mg") while leaving 31 unescaped render sinks exploitable by any write path that skipped the sanitiser. Text is now stored verbatim and escaped where it is rendered; `unsafe-eval` is gone from the CSP.

### 🩺 Application
- **Fixed**: the record view read fields from a shape the API does not return, so plain records rendered without type, doctor, institution or clinical data, filters and search matched nothing, and the Vaccine Passport and Medications pages threw TypeErrors.
- **Fixed**: Break-Glass could not be triggered — the panel was hidden by inline CSS nothing ever cleared.
- **Fixed**: the ledger activity chart padded quiet days with `Math.random()` values.
- **Fixed**: Chart.js was blocked by the application's own CSP; it is now vendored locally.
- **Added**: Merkle inclusion proof verification from the record view, sidebar entries for Vaccine Passport and Medications, and a demo chart so a fresh clone opens on a working vault.
- **Removed**: the unreachable Sign-In-With-Ethereum path, which called two endpoints that do not exist, and the AI-generated mockups that stood in for screenshots.

### 🧪 Tests
- 101 → 149, covering WebAuthn ceremonies, consent authorisation, Dual-Control, anchoring, the laboratory gateway and clinical text fidelity.

## [5.0.1] - 2026-07-30

### 🛡️ Security Audit Remediation & Architecture Alignment

#### Fixed & Security Hardened
- **Secured OpenAPI Schema & Docs**: Restricted `/api/v1/openapi.json` and OpenAPI docs endpoints behind `IPAllowlistMiddleware` to eliminate public API schema leakage.
- **X-Forwarded-For IP Spoofing Protection**: Hardened `resolve_secure_client_ip` in `backend/middleware/ip_allowlist.py` to only trust proxy headers if `TRUST_PROXIES=true` AND direct socket peer host is loopback or a trusted proxy.
- **Security Officer Role Authorization**: Fixed `Role` validation in `backend/schemas/requests.py` and `backend/routers/alerts.py` to include `security_officer`.
- **Immutable Record Decryption Audit Logging**: Added `RECORD_DECRYPTED` access log emission in `backend/routers/records.py` (`storage.append_access_log`).
- **Passkey Hardware Revocation API**: Implemented `POST /api/v1/auth/webauthn/revoke` endpoint backed by Dual-Control authorization for revoking lost/stolen hardware credentials.
- **Purged Dead Frontend Surface**: Completely purged dead FHIR export buttons, SIWE Web3 wallet login functions, Dead-Man's Switch, and ZKP modals from `index.html`, `app.js`, `auth.js`, and `records.js`.

#### Added
- **`THREAT_MODEL.md`**: Created formal threat model document mapping adversary vectors (external breach, rogue admin, passkey theft, coerced insider) to specific enforcing code.
- **`PRIVATE_VPC_DEPLOYMENT.md`**: Created institutional private VPC/VPN deployment specification documenting air-gapped network topology, persistent storage mounts (`/var/lib/vhv/data`), KVKK Art. 9 compliance, and the Institutional Deployment Gate.
- **`docker-compose.yml`**: Added containerized private VPC deployment configuration with persistent volume mounts (`vhv_db_data`, `vhv_lmdb_data`).

#### Documentation Alignment
- **ADR-0001 Alignment**: Updated `docs/adr/0001-offchain-storage-onchain-anchoring.md` replacing Ethereum `AnchorStore.sol` references with the actual local signed Merkle hash-chain notarization engine (`notarizer.py`).
- **Exact Python Symbol Paths**: Verified 100% exact module and class references (`core.services.notarizer.BlockchainNotarizer`, `core.pseudonymization.engine.PseudonymizationEngine`, `core.kms.provider.KMSProvider`).

---

## [5.0.0] - 2026-07-28

### 🚀 Major Release: Stealth VIP Health Privacy Vault & Hardened Security Architecture

#### Added
- **Dual-Control M-of-N Enforcement**: Hard lock on record decryption and viewing endpoints. System Administrators cannot decrypt raw VIP records without an active Security Officer co-signed token.
- **Network Level Isolation (`IPAllowlistMiddleware`)**: Enforces IP allowlisting restricting API access to internal subnets / private VPNs.
- **Real-Time Security Alert Engine (`alert_service.py`, `backend/routers/alerts.py`)**: Real-time event logging for Break-Glass access, rapid failed login spikes, and dual-control violations.
- **Mandatory FIDO2 Hardware Key Policy**: Policy enforcement option (`MANDATORY_FIDO2=true`) requiring WebAuthn/Passkey hardware keys for privileged personas.
- **Time-Bound Consent Engine (Hours/Days)**: Support for exact hourly consent windows with automatic expiration and `CONSENT_EXPIRED` audit logs.
- **Pseudonymization Engine**: Cryptographic identity decoupling isolating patient PII from medical records via `anon_id`.

#### Removed
- Removed SIWE / Web3 MetaMask wallet login endpoints (`/nonce`, `/wallet-login`).
- Removed Public Sepolia / Web3 contract deployer scripts (`contracts/`, `scripts/deploy_contract.py`).
- Removed ZKP / Pedersen commitment module (`core/zkp/`, `backend/routers/zkp.py`).
- Removed FHIR R4 export bundling (`backend/schemas/fhir.py`).
- Removed Social Recovery / Guardian endpoints (`/auth/recovery/*`).
- Removed Dead-Man's Switch (`backend/routers/deadman.py`) and Emergency QR (`backend/routers/emergency.py`) routers.
- Removed dead dependencies (`web3`, `keyring`) from `requirements.txt`.

#### Fixed & Hardened
- Aligned documentation (`README.md`, `GDPR_KVKK_COMPLIANCE.md`, `DPIA.md`, `CONSENT_FLOW.md`) with codebase reality.
- Enforced 100% test suite pass rate across 77+ unit and integration tests.
