# PsikoNot — Threat Model

> [!NOTE]
> **Purpose**: who might attack a psychologist's client records, how, and which control stops them.
> Written first for *VIP Health Vault*; updated for PsikoNot, called PsikoNot in 6.x (see the [CHANGELOG](../CHANGELOG.md)).

---

## 1. System Boundary & Primary Assets

### Primary Assets
1. **Therapy records:** the client profile, session notes, session transcripts, treatment plans, homework, attachments, and the practitioner's own process notes.
2. **The fact of being a client:** the pseudonym mapping that connects a client ID to its `anon_id`, and metadata such as how large a client's file is.
3. **Master Cryptographic Keys:** KMS key material, AES-256-GCM record encryption keys.
4. **Audit Trail Integrity:** Immutable access and decryption audit logs (`access_logs`, `RECORD_DECRYPTED` events).

---

## 2. Adversary Profiles & Threat Vectors

### Threat Actor 1: External Cyber Attacker (Remote Internet Breach)
- **Vector:** Attempts network scanning, credential brute-forcing, IP header spoofing (`X-Forwarded-For`), and API exploitation over public networks.
- **Countermeasures:**
  - `IPAllowlistMiddleware` & `resolve_secure_client_ip`: Blocks untrusted socket IPs attempting header spoofing.
  - Rate Limiting (`RateLimiterMiddleware`): 5 sign-in attempts per IP per minute on password login, passkey
    login, invitation and reset-code redemption, password changes and the 2FA enable/disable codes. It used to match `/api/auth/login`, a
    path that does not exist (the API is under `/api/v1`), so it never applied; `tests/test_rate_limit.py` now
    pins it. Every TOTP check (sign-in, enable, disable) is also limited per account, so a stolen session cannot
    guess codes by rotating addresses (`tests/test_totp_limits.py`).
  - XSS Protection & Strict Security Headers (`XSSProtectionMiddleware`).

### Threat Actor 2: Compromised Administrator (Rogue Insider)
- **Vector:** An administrator with DB access attempts to read client records without authorization.
- **Countermeasures:**
  - **Dual-Control Engine (`core.services.dual_control.DualControlEngine`):** Raw record access by administrators is blocked (`403 Forbidden`) unless co-signed by an independent Security Officer (`security_officer` role).
  - **Pseudonymization Engine (`core.pseudonymization.engine.PseudonymizationEngine`):** Real identity remains masked behind dynamic HMAC-SHA256 pseudonyms.
  - **Immutable Decryption Audit Logging (`storage.append_access_log`):** Every record decryption generates a permanent `RECORD_DECRYPTED` log entry.
  - **No account takeover by reset:** whoever issues a password reset code can redeem it and become the account. An operator issues one only with a co-signed `RESET_ACCOUNT` token for that username (the target cannot approve it), and becomes the account's recorded provisioner, so the two never co-sign each other afterwards (`backend/routers/accounts.py`).

### Threat Actor 3: Stolen / Lost Hardware Credential
- **Vector:** An attacker steals a user's hardware security key / FIDO2 passkey or mobile device.
- **Countermeasures:**
  - **Passkey Revocation API (`POST /api/v1/auth/webauthn/revoke`):** Hardware credentials can be revoked out-of-band by Security Officers.
  - **2FA/TOTP verification** (each code is accepted once: the last used time step is stored per account), and `MANDATORY_FIDO2` so a stolen password alone does not open an account that has a passkey.

### Threat Actor 4: Curious Practitioner (another practitioner's clients)
- **Vector:** A practitioner of the same practice tries to read a client who is not theirs: by guessing client
  IDs, walking block numbers, downloading attachments, asking for proofs or chain status, or writing into the
  file.
- **Countermeasures:**
  - **One access policy for every record endpoint** (`core/services/access_policy.py`, ADR-0003): a client's
    file opens only for the practitioner who keeps the client's card — on the list, single record,
    decryption, corrections, attachment downloads, Merkle proofs, chain status and the access ledger. The
    single-record endpoint used to check nothing for a practitioner — any practitioner could read any
    client's unprotected records by walking block numbers (an IDOR).
  - **Not yours, not there:** someone else's client gets the same `403` as a client who does not exist, so
    client IDs cannot be probed. The client list and the appointment book answer `404` for them.
  - **A record password is not a key to someone else's file:** decryption checks the file first.
  - **Corrections cannot change the access level** — who may see a record is not content.
  - **No emergency override.** Break-glass was removed: a private practice has no emergency-access need that
    would justify a path around the owner rule.

### Threat Actor 5: Curious Secretary (the appointment book as a way in)
- **Vector:** A practice secretary, who legitimately sees the appointment book and the client cards, tries to
  read a client's records or notes — or another practitioner's book.
- **Countermeasures:**
  - **Default deny for roles** (`access_policy.RECORD_ROLES`): the access policy lists the roles that may see
    record content and refuses every other role. It used to allow every role except client and practitioner,
    so a newly added role would have read everything; tests show six record endpoints answered a secretary
    before the fix.
  - **Only linked books:** a secretary is linked to the practitioners they work for (`practice_staff`, one
    row per link — a center's front desk may serve several). Every appointment and client-card request is
    scoped to one of those books, read again on each request, so removing a link ends access at once. A
    request for a book the secretary does not run answers `403`, the same for a practitioner who does not
    exist; another book's appointment or card answers `404`, like a missing one.
  - **No account takeover through a shared desk:** a practitioner resets, disables or re-enables only a
    secretary who works for nobody else. A reset code lets its issuer become the account, and a shared
    secretary sees other practitioners' client cards; for a shared secretary a practitioner can only
    remove them from their own book, and the account goes to an operator under dual control. Only an
    operator links a secretary to a book (`staff/link` answers a practitioner `403`), so a practitioner
    cannot make someone else's secretary shared and take the account out of that practitioner's hands.
  - **No clinical text in the book or on the cards:** appointments hold only who, with whom, when and how;
    a card holds a name, phone, e-mail and a date. Neither has a free-text field that could carry clinical
    content into the unencrypted tables.
  - **Shared rooms show times, not clients:** the one view that crosses books, a room's day
    (`GET /api/v1/rooms/{id}/schedule`), shows another practitioner's appointments in that room only as a
    busy start and end time — no client ID or name, no appointment ID, no practitioner. Room names are short
    labels (letters, digits, spaces, `. - /`), so they cannot carry clinical text either.

---

## 3. Summary Mapping Matrix

| Adversary Profile | Threat Vector | System Countermeasure | Enforcing Code / Class |
| :--- | :--- | :--- | :--- |
| External Attacker | `X-Forwarded-For` IP Spoofing | IP Peer Host Verification | `backend.middleware.ip_allowlist.resolve_secure_client_ip` |
| Rogue Administrator | Unauthorized PHI Query | Dual-Control Co-Signature | `core.services.dual_control.DualControlEngine` |
| Stolen Hardware Passkey | Stolen YubiKey Credential | Hardware Passkey Revocation API | `POST /api/v1/auth/webauthn/revoke` |
| Curious Practitioner | Reading another practitioner's client | One access policy (owner + record level) on every record endpoint | `core.services.access_policy` |
| Curious Secretary | Reading records via the appointment book | Default-deny role list; only the books of linked practitioners | `access_policy.RECORD_ROLES`, `core.services.appointment_book` |

---

## 4. Trust Assumptions & Residual Risk

### What we assume about the attacker

We do **not** rely on hiding the system. The source code is public, so we assume the
attacker knows exactly how it works and can reach the service. A private network
(private VPC) is only one extra layer, not the main defense. Security must come from
keys and access rules, not from secrecy.

### What still protects data under these assumptions

| Attacker | Control that still works |
| :--- | :--- |
| One stolen admin account | Dual-control: reading a record needs a second, different approver. Self-approval is rejected. |
| Stolen disk or backup | Records are AES-256-GCM ciphertext, stored under HMAC pseudonyms — no real client ID on disk. |
| Password guessing or theft | Argon2id hashing, login rate-limiting, and optional mandatory passkeys (a passkey-holding account cannot use its password alone). |
| Changing the history | Signed, append-only hash-chain: any edit is detected when the chain is verified. |
| "Delete my data" / subpoena | Crypto-shred: destroying a per-client key makes that client's data unreadable for good. |

### Residual risk (chosen limits for this tier — not defects)

This is a single-institution, single-node build. The items below are **known limits**,
not bugs. Each one names what a real production system would add.

| Residual risk | Why it is accepted here | What production would add |
| :--- | :--- | :--- |
| Encryption/signing keys and `PSEUDONYM_SECRET` can live on the app host, so taking over the host can mean taking the keys. | Keeps the demo self-contained; the KMS layer is abstracted but defaults to software. | Move keys to an HSM / Vault Transit so the host never holds raw key material. |
| Tampering is **detected, not blocked**. | Tamper-evidence is the design goal; blocking needs more infrastructure. | Real-time monitoring, alerting, and incident response. |
| No high availability (single node = single point of failure). | This tier optimizes confidentiality and integrity, not uptime. | Replication behind a load balancer; DoS protection at the edge. |
| No independent penetration test or audit. | Solo portfolio project. | Third-party pentest and code audit before handling real client data. |
| If passkeys are not enforced, a stolen password exposes what that account can see. Even with `MANDATORY_FIDO2=true`, an account that has not enrolled a passkey yet can still sign in with its password (to enrol one). | Passkey enforcement is opt-in, and enrolment needs a signed-in session. | Enforce passkeys from day one, enrolling them in person during onboarding. |
| An operator who issues a co-signed reset code still sees the code and could redeem it before the account holder does. | The co-signer approves the reset but cannot keep the code from the issuer. The reset is alerted, the holder notices a code that no longer works, and a reset keeps the account's 2FA and passkeys. | Deliver reset codes through a channel the issuer cannot read, or split the code between issuer and co-signer. |

### Scope

This system is built for one private psychology practice on its own private network
(see [ADR-0001](adr/0001-offchain-storage-onchain-anchoring.md),
[ADR-0002](adr/0002-single-node-deployment.md)). It is **not** a multi-tenant clinic
platform and does not claim to be. The legal and operational work needed for real use is
tracked in [GDPR_KVKK_COMPLIANCE.md](GDPR_KVKK_COMPLIANCE.md) and [DPIA.md](DPIA.md).
