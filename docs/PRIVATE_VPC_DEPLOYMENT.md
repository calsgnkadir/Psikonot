# PsikoNot — Private Network Deployment

> [!IMPORTANT]
> **Regulatory & Architecture Mandate (KVKK Art. 9 & Air-Gapped Network Isolation)**  
> Therapy records are special-category health data. They **must not** be hosted on a public PaaS. Run PsikoNot inside a private network: the practice's own server, a private cloud or a private VPC, reachable only over VPN / TLS (see [HTTPS_DEPLOYMENT.md](HTTPS_DEPLOYMENT.md)).

---

## 1. Network Topology & IP Isolation Architecture

```
[ Practice devices ]
            │
   (TLS, inside the practice's LAN or over its VPN)
            ▼
[ Institutional Firewall ] ──▶ [ Server in the private network ]
                                         │
                                         ▼
                          [ Caddy: TLS, http→https, ports 80/443 ]
                                         │  internal Docker network,
                                         │  Caddy at fixed 172.31.254.2
                                         ▼
                          [ PsikoNot container :8000, not published ]
                           ├── IPAllowlistMiddleware
                           ├── Persistent Storage Mounts
                           └── Hardware Passkey Auth
```

- **TLS is required.** In production the session and CSRF cookies are `Secure`,
  so signing in only works over HTTPS. `docker-compose.prod.yml` adds the Caddy
  reverse proxy; the steps, the three certificate modes (Let's Encrypt, Caddy's
  internal CA, the practice's own certificate) and the checks are in
  [HTTPS_DEPLOYMENT.md](HTTPS_DEPLOYMENT.md).
- **Only the proxy is reachable.** The app's port is not published on the host;
  the app is on an internal network with no route out.
- **Real client addresses.** The app trusts `X-Forwarded-For` only from Caddy's
  fixed address, so the IP allowlist and the sign-in rate limit see each
  device's own address. A device cannot claim another address with the header.
- **Allowed Subnets** (`ALLOWLISTED_NETWORKS`): by default `127.0.0.1/32`,
  `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`. Narrow it to the practice's
  LAN and VPN ranges.
- **Public Ingress Restrictions**: addresses outside the allowed subnets are
  refused (`403`) by `IPAllowlistMiddleware`; only the anonymous health check
  is exempt.

---

## 2. Persistent Storage Requirements

Public cloud free-tier PaaS environments feature ephemeral containers where local databases are destroyed upon redeployment. The private deployment architecture mandates persistent volume mounts:

| Storage Asset | Container Path | Docker volume | Purpose |
| :--- | :--- | :--- | :--- |
| **LMDB Encrypted Off-Chain Store** | `/app/backend/projects` | `vhv_lmdb_data` | AES-256-GCM encrypted record chains and attachments |
| **SQLite Vault Database** | `/app/data/vault.db` | `vhv_db_data` | User accounts, audit logs, access records, device id, erasure ledger |
| **Caddy certificates** | `/data` (caddy) | `caddy_data` | TLS certificates and, in mode `internal`, the practice's own CA |

All three belong in the backups ([BACKUP_RESTORE.md](BACKUP_RESTORE.md)).

---

## 3. Environment Variables & Configuration

Copy `.env.production.example` to `.env` and fill it in; it lists everything
production needs (domain and certificate mode, `ALLOWLISTED_NETWORKS`,
`VHV_TIMEZONE`, the keys from the secret store). `docker-compose.prod.yml`
fixes the settings that must not vary:

```env
ENVIRONMENT=production
VHV_DEMO_MODE=false
VHV_IP_ALLOWLIST_ENABLED=true
TRUST_PROXIES=true
TRUSTED_PROXIES=172.31.254.2        # Caddy's fixed address, nothing else
VHV_WEBAUTHN_RP_ID=<PSIKONOT_DOMAIN>
VHV_WEBAUTHN_ORIGINS=https://<PSIKONOT_DOMAIN>
```

`HEALTH_BLOCKCHAIN_KEY` and `PSEUDONYM_SECRET` come from the secret store; the
app refuses to boot without them ([KEY_MANAGEMENT.md](KEY_MANAGEMENT.md)). The
session-signing keys can be supplied as `VHV_JWT_PRIVATE_KEY` /
`VHV_JWT_PUBLIC_KEY`; otherwise they are generated inside the container and
everyone signs in again after it is recreated.

---

## 4. KVKK Article 9: keeping the data in the country

**KVKK Article 9** restricts transferring personal data abroad, and health data is special category data. Running PsikoNot on the practice's own server or in a private cloud located in Türkiye avoids a cross-border transfer in the first place. This is one part of compliance, not all of it: the practice's duties as data controller (section 5) still apply.

---

## 5. Before the first real client

Before the first real client is enrolled, the following must be in place:

1. **Data controller duties:** the practice, as data controller under KVKK, completes its registration and information notices, and a KVKK-compliant explicit consent is collected from each client.
2. **Keys off the app host:** move the signing key to a Hardware Security Module (HSM) or HashiCorp Vault Transit (`KMS_PROVIDER=vault`).
3. **A second person for dual control:** name who co-signs operator access (`security_officer` role) — for a small practice, for example, its IT provider.
