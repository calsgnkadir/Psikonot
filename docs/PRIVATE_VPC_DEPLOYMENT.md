# PsikoNot — Private Network Deployment

> [!IMPORTANT]
> **Regulatory & Architecture Mandate (KVKK Art. 9 & Air-Gapped Network Isolation)**  
> Therapy records are special-category health data. They **must not** be hosted on a public PaaS. Run PsikoNot inside a private network: the practice's own server, a private cloud or a private VPC, reachable only over VPN / TLS.

---

## 1. Network Topology & IP Isolation Architecture

```
[ Practice devices ]
            │
   (Encrypted VPN / TLS)
            ▼
[ Institutional Firewall / WAF ] ──▶ [ Private Subnet (10.0.0.0/8) ]
                                                │
                                                ▼
                                    [ PsikoNot container ]
                                     ├── IPAllowlistMiddleware
                                     ├── Persistent Storage Mounts
                                     └── Hardware Passkey Auth
```

- **Allowed Subnets**: `127.0.0.1/32`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.
- **Public Ingress Restrictions**: Public internet CIDRs (`0.0.0.0/0`) are blocked by `IPAllowlistMiddleware`.

---

## 2. Persistent Storage Requirements

Public cloud free-tier PaaS environments feature ephemeral containers where local databases are destroyed upon redeployment. The private deployment architecture mandates persistent volume mounts:

| Storage Asset | Container Path | Host Volume Mount Path | Purpose |
| :--- | :--- | :--- | :--- |
| **LMDB Encrypted Off-Chain Store** | `/app/lmdb_data` | `/var/lib/vhv/lmdb_data` | AES-256-GCM Encrypted Record Payloads |
| **SQLite Vault Database** | `/app/database/vault.db` | `/var/lib/vhv/database/vault.db` | User Accounts, Audit Logs & Access Records |

---

## 3. Environment Variables & Configuration

```env
ENVIRONMENT=production
VHV_DEMO_MODE=false
VHV_IP_ALLOWLIST_ENABLED=true
TRUST_PROXIES=true
TRUSTED_PROXIES=10.0.0.1,10.0.0.2
JWT_PRIVATE_KEY_PATH=/etc/vhv/keys/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/etc/vhv/keys/jwt_public.pem
```

---

## 4. KVKK Article 9: keeping the data in the country

**KVKK Article 9** restricts transferring personal data abroad, and health data is special category data. Running PsikoNot on the practice's own server or in a private cloud located in Türkiye avoids a cross-border transfer in the first place. This is one part of compliance, not all of it: the practice's duties as data controller (section 5) still apply.

---

## 5. Before the first real client

Before the first real client is enrolled, the following must be in place:

1. **Data controller duties:** the practice, as data controller under KVKK, completes its registration and information notices, and a KVKK-compliant explicit consent is collected from each client.
2. **Keys off the app host:** move the signing key to a Hardware Security Module (HSM) or HashiCorp Vault Transit (`KMS_PROVIDER=vault`).
3. **A second person for dual control:** name who co-signs operator access (`security_officer` role) — for a small practice, for example, its IT provider.
