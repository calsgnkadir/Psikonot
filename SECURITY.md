# Security

PsikoNot holds therapy records, so it is built in layers: if one layer fails, the next
one still protects the data. This page is a short map; the threat model has the detail
([docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)).

## Layers

### 1. Who may see what
- **One access policy** for every record endpoint ([ADR-0003](docs/adr/0003-one-access-policy.md)):
  a client's file opens only for the practitioner who keeps the client's card. Another
  practitioner gets the same answer as for a client who does not exist.
- **Default deny for roles:** the policy lists the roles that may see records; any
  other role — the practice secretary, who runs the appointment book and the client
  cards — sees none.
- **Clients do not sign in.** There is no client account to take over; the practice
  log (cards, appointments) holds no clinical text.
- **Dual control:** administrators, auditors and security officers cannot read a
  client's records on their own. They need a token co-signed by a second privileged
  person, bound to one client and short-lived.

### 2. Encryption
- **At rest:** every record is encrypted with AES-256-GCM (random 96-bit nonce,
  128-bit tag) under a per-client key derived from the KMS root. The record store
  holds only ciphertext.
- **Optional record password:** a record can also be locked with a password. The
  server derives a key from it (PBKDF2, 600,000 iterations) and does not store it, so
  the stored record cannot be read without the password. This is *not* end-to-end
  encryption: the password reaches the server when the record is written or opened.
- **Crypto-shredding:** destroying a client's key makes their records permanently
  unreadable while the chain stays valid (KVKK/GDPR Art. 17).

### 3. Integrity and audit
- **Signed append-only chain:** each block links to the previous hash and carries an
  HMAC-SHA256 signature and a Merkle root ([ADR-0001](docs/adr/0001-offchain-storage-onchain-anchoring.md)).
  Records are corrected by appending, never overwritten.
- **Access ledger:** every read is a hash-linked entry; deleting or changing one
  breaks the chain, and the client can see the ledger and its integrity verdict.

### 4. Sign-in
- Argon2id password hashing; WebAuthn/FIDO2 passkeys (ES256 signature verified on the
  server); optional TOTP. With `MANDATORY_FIDO2=true`, an account that has a passkey
  cannot sign in with its password alone.
- 5 sign-in attempts per IP per minute on password login, passkey login and
  invitation-code redemption.
- No self-registration: accounts start from a single-use, expiring code
  ([docs/ONBOARDING_PROTOCOL.md](docs/ONBOARDING_PROTOCOL.md)).

### 5. Browser and network
- Output encoding at every HTML sink, and a CSP of `script-src 'self'` (no inline
  script, no `eval`), so an injected string cannot run even if encoding is missed
  somewhere ([docs/DOM_XSS_SELF_AUDIT.md](docs/DOM_XSS_SELF_AUDIT.md)).
- The session token is an httpOnly, SameSite=Strict cookie; state-changing requests
  need a CSRF double-submit token.
- `IPAllowlistMiddleware` accepts requests only from configured private networks.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**). Do not open a public issue for a security
problem.
