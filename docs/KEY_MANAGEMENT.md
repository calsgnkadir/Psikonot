# Key Management

> The single most important operational fact about this vault:
> **the signing key is the encryption key. Lose it and every encrypted record is gone.**

## What the key does

PsikoNot runs on one root secret, the *signing key*
(`SoftwareKMSProvider.get_signing_key()`). It has two jobs:

1. **Integrity** — it is the HMAC key that signs every block, so the hash-chain
   can detect tampering (`core/security.py`).
2. **Confidentiality** — it derives the AES-256-GCM at-rest key for each client
   via `derive_rest_secret("rest-v1:{patient_id}")` (`core/security.py:185`).

Because (2) is derived deterministically from the signing key, there is no
separate "backup copy" of the encryption key anywhere. **If the signing key is
lost, the ciphertext in `projects/` can never be decrypted again** — there is no
recovery path, by design (that is what makes a stolen backup useless without it).

## Where the key is stored

`_load_signing_key()` resolves the key from the first source that has one:

| # | Source | Recommended for |
| :-- | :-- | :-- |
| 1 | `HEALTH_BLOCKCHAIN_KEY` environment variable | **Production.** Inject from your secret manager / systemd credential / container secret. |
| 2 | OS keyring — DPAPI (Windows), Keychain (macOS), Secret Service (Linux) | Single-host deployments with a logged-in service account. Requires the `keyring` package (now a declared dependency). |
| 3 | `.private_key` plaintext file in the working directory | Legacy / fallback only. Auto-migrates into the keyring when one is available. |
| 4 | Freshly generated random key | First run only. **Refused in production** unless you opt in (below). |

### The threat model, stated honestly

The README says *"a stolen `projects/` backup without the signing key cannot be
read."* That holds **only when the key lives somewhere the backup does not** —
an environment variable or the OS keyring. If the key sits in the `.private_key`
file inside the same directory tree you back up, a stolen backup contains both
the ciphertext and its key. Keep the key out of the data backup.

## Production guardrails

The vault is in "production" whenever `ENVIRONMENT` is not `development` and
`VHV_DEMO_MODE` is not `true`. In that mode:

- **It will not silently generate a new key.** A key minted on boot would either
  land in a plaintext file or, worse, orphan every record already encrypted under
  a previous key. Startup fails with a clear message until you provide a key.
- **First-run exception:** a fresh install with no data yet can be allowed to mint
  its key by setting `VHV_ALLOW_GENERATED_KEY=true` for that first boot. Capture
  the generated key (from the keyring or `.private_key`), store it in your secret
  manager, then unset the flag.
- **Plaintext-on-disk warning:** if the key can only be kept in `.private_key`
  (no keyring available), a warning is logged on every startup.

## Backing up the key

1. Generate a strong key once:

   ```bash
   python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
   ```

2. Store it in your secret manager and inject it as `HEALTH_BLOCKCHAIN_KEY`.
3. Keep an offline, encrypted copy (e.g. a sealed envelope / hardware token) in a
   different location from the `projects/` backups.

## Rotation

Rotation is deliberately not automatic, because the at-rest key is derived from
the signing key: rotating the signing key changes the derived key for **every**
client, so all existing records must be re-encrypted under the new key in the
same operation. The supported procedure is:

1. Stand up the new key as `HEALTH_BLOCKCHAIN_KEY_NEXT` (out of band).
2. For each client chain, decrypt each block payload with the current key and
   re-encrypt with the next key, appending the re-encrypted blocks (the chain is
   append-only — originals stay, superseded).
3. Promote `HEALTH_BLOCKCHAIN_KEY_NEXT` to `HEALTH_BLOCKCHAIN_KEY`.
4. Destroy the old key only after verifying every chain re-validates.

> A migration script for step 2 is **planned, not yet shipped** — see the README
> Roadmap. Until then, treat the signing key as long-lived and protect it
> accordingly.
