# Backup and restore

A practice's session notes cannot be written again. This page is the procedure:
how to take a backup, where to keep it, how to restore it, and how to check
regularly that a restore really works.

There are **three separate things** to keep, in **three separate places**:

| What | Made by | Changes | Contains |
| :--- | :--- | :--- | :--- |
| **Data backup** | `python -m scripts.backup --out …` | every run | SQLite (accounts, client cards, appointments, per-client erasure keys) and every LMDB store (record chains, attachments, system and user stores) — encrypted |
| **Key export** | `python -m scripts.backup --keys-out …` | only when a key changes | `HEALTH_BLOCKCHAIN_KEY`, `PSEUDONYM_SECRET`, the device fingerprint |
| **Erasure ledger** | written by the app at every erasure | at every erasure | the pseudonym and time of every erased client — no names, no client IDs |

The backup passphrase (or key file) that encrypts the data backup is a fourth
secret: keep it in the practice's password manager, not with the backups.

## What a backup is

`python -m scripts.backup --out <dir>` writes one folder,
`psikonot-backup-<UTC time>`, while the app keeps running:

- **LMDB** — each environment (one per client chain, plus `attachments`,
  `__system__`, `__users__`) is copied with `env.copy(compact=True)`. LMDB does
  this inside one read transaction, so each copy is a consistent snapshot.
  Client chains are copied first, then attachments, then the system stores.
- **SQLite** — copied with the `sqlite3` backup API, never as a file, and last:
  everything the LMDB copies refer to (a client's erasure key, their mapping)
  was written to SQLite before the record that uses it.
- The stores are not one atomic snapshot together. A record written during the
  backup may be in one store and not yet in another; the copy order makes this
  harmless (at worst a card or key without its newest record).
- **`backup.json`** (plain): time, app version and commit, SHA-256 and size of
  the archive, how it is encrypted, counts. No personal data.
- **`data.enc`**: a tar archive, encrypted. Inside, `manifest.json` lists every
  file with its SHA-256 and size, the counts (users, client cards,
  appointments, chains, records, attachments), check values of the keys and
  the erasure ledger's position at backup time.

**Why it is encrypted.** Record contents are already ciphertext, but the SQLite
file holds client names, phone numbers, e-mail addresses, appointment times,
staff password hashes and TOTP secrets in the clear. The archive is AES-256-GCM
in 1 MiB chunks, each authenticated with its position and an end marker, so a
changed, reordered or cut-off archive is refused. The key comes from:

- a **passphrase** (at least 12 characters) through scrypt (n=2^17, r=8, p=1)
  with a random salt — from `PSIKONOT_BACKUP_PASSPHRASE`, `--passphrase-file`,
  or a prompt when run interactively; or
- a **key file** holding 32 random bytes in base64 (`--key-file` or
  `PSIKONOT_BACKUP_KEY_FILE`), through HKDF with a random salt. Make one with
  `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > backup.key`.

While the backup runs, the unencrypted copies sit for a moment in the system
temp folder (in Docker, the container's `/tmp`) and are deleted at the end.

**Keys are not in the data backup.** Without the key export, a data backup can
never be read — that is the point, and also why the key export must exist
somewhere else.

**PostgreSQL.** The scripts handle SQLite only and refuse to run when
`VHV_DATABASE_URL` is set. With PostgreSQL, back the database up with
`pg_dump --format=custom` at the same time as the LMDB stores (stop writes, or
accept the same ordering caveat as above), encrypt the dump, and restore it
with `pg_restore`. The erasure ledger must then be re-applied by hand: for
every ledger entry, delete the matching `patient_id` from
`patient_erasure_keys`, `patient_pseudonyms`, `clients` and `appointments`
(the matching id is the one whose HMAC-SHA256 under `PSEUDONYM_SECRET` equals
the entry's `anon_id`). This path is untested.

## The erasure rule (KVKK Art. 17)

Erasure destroys the client's erasure key, their pseudonym mapping, card and
appointments. **A backup taken before the erasure still holds all of them.**
Restored as it is, it would make an erased client's notes readable again.

So every erasure is also written to the **erasure ledger**
(`core/services/erasure_ledger.py`): one line with the client's pseudonym
(the anon id that names their chain store), the time, and a hash chained to
the line before. The line is written *before* the key is destroyed; if it
cannot be written, the erasure fails and nothing is destroyed.

`scripts.restore` then, before writing anything:

1. requires the ledger (refuses if it is missing, unless you pass
   `--confirm-no-erasures` and are sure);
2. checks its hash chain, and that it contains the ledger position recorded
   in the backup — a ledger older than the backup, or a different one, is
   refused;
3. erases again, in the restored copy, every client in the ledger (key,
   mapping, card, appointments), then checks that none of them can be read.

Where the ledger lives: `VHV_ERASURE_LEDGER` (default: next to the SQLite
file, i.e. `/app/data/erasure_ledger.jsonl` in Docker). Set
`VHV_ERASURE_LEDGER_MIRROR` to a second path (another disk or a network
share) and every line is written there as well. The ledger is deliberately
**not** inside the data backups, because a restore must never roll it back.

**Limits — read these.**

- The restore can prove the ledger is *not older than the backup*. It cannot
  prove it is *the newest copy*. If a client was erased after your last copy
  of the ledger and the host is then lost, the restore will bring that client
  back. Use `VHV_ERASURE_LEDGER_MIRROR` on a separate disk, and copy the
  ledger off the host after every erasure.
- Old backups still contain the erased client's key. Anyone with an old data
  backup, its passphrase and the key export can read that client. The erasure
  is complete only once every backup taken before it has expired: keep a fixed
  retention (below) and delete expired backups for real.
- The ledger names erased clients only by pseudonym and time. With
  `PSEUDONYM_SECRET` one can still test whether a given client ID is in it.
  It is the minimum needed to keep the erasure, and is kept as such.
- The ledger depends on `PSEUDONYM_SECRET`: it must be the same secret at
  restore time (the restore checks this).
- Erasures done before this ledger existed are not in it. If you erased
  anyone before upgrading, check old backups by hand or delete them.

## Docker: taking a backup

The container is `psikonot_app`; data is in the volumes mounted at `/app/data`
(SQLite, device fingerprint, erasure ledger) and `/app/backend/projects`
(record chains, attachments).

The `docker compose` commands below assume the production setup of
[HTTPS_DEPLOYMENT.md](HTTPS_DEPLOYMENT.md): run them in the folder with the
compose files and the `.env` whose `COMPOSE_FILE` names both
`docker-compose.yml` and `docker-compose.prod.yml`. Without it, add
`-f docker-compose.yml -f docker-compose.prod.yml` after `docker compose`.
(`docker exec` and `docker cp` name the container and need neither.)

```bash
# 1. Take the backup inside the container (prompts for the passphrase).
docker exec -it psikonot_app python -m scripts.backup --out /tmp/psikonot-backups

# Unattended (cron): pass the passphrase from a root-only file on the host.
docker exec -e PSIKONOT_BACKUP_PASSPHRASE="$(cat /root/.psikonot-backup-pass)" \
    psikonot_app python -m scripts.backup --out /tmp/psikonot-backups

# 2. Copy it off the container, then remove it there.
docker cp psikonot_app:/tmp/psikonot-backups/. /srv/psikonot-backups/
docker exec psikonot_app rm -rf /tmp/psikonot-backups

# 3. Copy the erasure ledger to its separate place too.
docker cp psikonot_app:/app/data/erasure_ledger.jsonl /srv/psikonot-ledger/
```

Then send `/srv/psikonot-backups/psikonot-backup-…` to the off-site copy (see
3-2-1 below).

**TLS certificates** — with `PSIKONOT_TLS_MODE=internal` the `caddy_data`
volume holds the center's own certificate authority. Losing it means
installing a new root certificate on every computer, so copy it once after
installation (and after a renewal of the root, years later). It contains the
CA's private key: keep the copy with the keys, not with the data backups.

```bash
docker compose cp caddy:/data/caddy /srv/psikonot-keys/caddy-data
```

To restore it, copy it back and restart Caddy:
`docker compose cp /srv/psikonot-keys/caddy-data/. caddy:/data/caddy`, then
`docker compose restart caddy`.

**Key export** — once at install, and again whenever a key changes:

```bash
docker exec psikonot_app python -m scripts.backup --keys-out /tmp/psikonot-keys
docker cp psikonot_app:/tmp/psikonot-keys/. ./psikonot-keys/
docker exec psikonot_app rm -rf /tmp/psikonot-keys
```

Move the `.env` file into the practice's password manager or onto an
encrypted USB stick kept in a safe, then delete the local copy. The tool
refuses to write it inside the data folders. In production the keys come from
your secret store already (`HEALTH_BLOCKCHAIN_KEY`, `PSEUDONYM_SECRET`); the
export is the copy you can restore from if that store is lost. With
`KMS_PROVIDER=vault` the signing key stays in Vault and is not exported —
back it up with Vault's own procedure.

Suggested (not applied) compose change, so the ledger is on its own volume:

```yaml
    environment:
      - VHV_ERASURE_LEDGER=/app/ledger/erasure_ledger.jsonl
      - VHV_ERASURE_LEDGER_MIRROR=/mnt/ledger-mirror/erasure_ledger.jsonl
    volumes:
      - vhv_ledger:/app/ledger
      - /mnt/nas/psikonot-ledger:/mnt/ledger-mirror
```

## Docker: restoring

Restore with the app **stopped**, in a one-off container that has the same
volumes and environment (keys) as the app:

```bash
docker compose stop psikonot
docker compose run --rm --no-deps \
    -v /srv/psikonot-backups:/backups:ro -v /srv/psikonot-ledger:/ledger:ro \
    psikonot python -m scripts.restore \
        --from /backups/psikonot-backup-20261015T020000Z \
        --erasure-ledger /ledger/erasure_ledger.jsonl
docker compose start psikonot
```

The restore refuses a target that already has data. To replace it, add
`--force`, which **deletes** the current SQLite file and the contents of the
projects folder — but only after every check has passed. Take a backup of the
current state first if there is anything in it worth keeping.

It prints a short report and exits non-zero on any failure:

```
Backup of 2026-10-15T02:00:00+00:00 (app 7.1.0, commit f05b389f15): archive and 41 file hashes verified.
Erasure ledger: 2 entries, 1 recorded after the backup, last erasure 2026-10-15T14:12:09+00:00.
Erasures re-applied: 1 client(s) found in the backup and erased again.
Verification: 37/37 chains valid, 36 client file(s) readable, 1 erased (unreadable as they should be), 0 chain(s) with no client mapping.
  counts: 35 client card(s), 812 record(s), 14 attachment(s), 4 account(s)
Restored into /app/data/vault.db and /app/backend/projects. All 37 chains valid.
```

What it checks before anything is written: the target is empty (or
`--force`); the ledger (above); the archive's SHA-256, every AES-GCM chunk and
every file's SHA-256 against the manifest; that `HEALTH_BLOCKCHAIN_KEY` and
`PSEUDONYM_SECRET` are the ones the backup was taken with; then, on the
restored copy, that every chain passes `is_chain_valid`, every record of a
client who is not erased decrypts, erased clients cannot be read, every
attachment matches its hash, and the counts match the manifest (less what the
re-applied erasures removed).

Not restored: the JWT signing keys (`backend/.jwt_*.pem` or
`VHV_JWT_*` env) — everyone signs in again, nothing else is lost; and the
legacy `backend/offchain_storage/` folder from before attachments moved into
LMDB, if an old install still has one.

## How often, and where (3-2-1)

- **Data backup:** every night, and before every upgrade. Keep 7 daily, 4
  weekly and 12 monthly copies, then delete. Write the retention into the
  practice's KVKK records: an erasure is complete only when the last backup
  taken before it has been deleted.
- **3 copies, 2 kinds of media, 1 off site:** the live data, a copy on a
  different disk or NAS in the practice, and an off-site copy (another
  building, or cloud storage — the archive is encrypted).
- **Keys apart from data:** the key export and the backup passphrase never go
  where the data backups go. Data backups on the NAS and in the cloud; keys in
  the password manager and in a sealed envelope in a safe. Someone who steals
  the backups must not find the keys next to them.
- **Ledger apart from data:** the erasure ledger goes with the keys or on its
  own mirror, never only inside the data volume.

## Test a restore regularly

A backup that has never been restored is a hope, not a backup. Once a month,
and after every upgrade, restore the newest backup into a scratch location
and read it:

```bash
docker compose run --rm --no-deps \
    -e VHV_SQLITE_PATH=/tmp/restore-test/vault.db \
    -e VHV_PROJECTS_DIR=/tmp/restore-test/projects \
    -v /srv/psikonot-backups:/backups:ro -v /srv/psikonot-ledger:/ledger:ro \
    psikonot python -m scripts.restore \
        --from /backups/psikonot-backup-<newest> --erasure-ledger /ledger/erasure_ledger.jsonl
```

This does not touch the live data (the `/tmp` of the one-off container is
thrown away). It must end with `All N chains valid` and the counts must look
right. Write the date and result in the practice's log. Do it once a year
with the keys taken from the key export rather than the running system, to
prove the export is complete.

The automated version of this test is `tests/test_backup_restore.py`.
