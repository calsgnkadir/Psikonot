# ADR 0002: Single-node deployment (in-memory coordination state)

## Status
Accepted — deliberate scope decision for this threat tier.

## Context
The vault serves a small number of protected individuals inside one institution's
private subnet (see `PRIVATE_VPC_DEPLOYMENT.md`, ADR-0001). Several pieces of
coordination state live **in process memory**, not in a shared store:

- **WebAuthn challenges** — `core.webauthn.challenge_store` is an in-memory,
  single-use, TTL map. A challenge issued by one process is unknown to another.
- **Unit-of-work / after-commit hooks** — `database.storage.active_txn` /
  `after_commit_hooks` are `contextvars` scoped to the running task in one process.
- **KMS provider** — `core.kms.registry` caches one provider instance per process.
- **Rate-limit fallbacks** — small in-process maps.

Because FastAPI runs sync endpoints in a threadpool, concurrency **within** one
process is safe (see `tests/test_concurrency.py`: writes to one chain serialize via
the unit of work and never fork). The limitation is **across** processes.

## Decision
Run the vault as a **single node, single worker**. Do **not** start it with
`uvicorn --workers >1`, gunicorn with multiple workers, or more than one replica:
the in-memory state above would desynchronise (a passkey challenge issued on worker
A rejected on worker B; after-commit hooks firing in the wrong task; inconsistent
JWT keys if two fresh workers generated their own).

For this tier — low request volume, one institution, one trust boundary — a single
node is the correct, simplest fit. It is a single point of failure, mitigated
operationally (hot standby + restore from backup, see `BACKUP_RESTORE.md` and
`KEY_MANAGEMENT.md`), not a
throughput bottleneck.

## Consequences
- **Positive:** no external coordination dependency (no Redis), simple to deploy and
  reason about, matches the air-gapped/private-VPC posture.
- **Negative:** not horizontally scalable as-is; a process restart drops in-flight
  WebAuthn challenges (the client simply retries).

## Path to horizontal scale (future, if ever needed)
Not planned for this tier, but the shape is known:
1. Move WebAuthn challenges and rate-limit counters to a shared store (Redis) with
   the same TTL/single-use semantics.
2. Make the unit-of-work / after-commit coordination process-independent (drive
   anchoring off a committed-write signal rather than in-process contextvars).
3. Use `KMS_PROVIDER=vault` (already externalised — ADR nothing to change) and inject
   `HEALTH_BLOCKCHAIN_KEY` / `PSEUDONYM_SECRET` from the secret manager so every
   worker derives the same keys.
4. Then run N workers behind the private load balancer.
