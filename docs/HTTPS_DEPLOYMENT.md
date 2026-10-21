# PsikoNot — HTTPS Deployment

In production (`ENVIRONMENT=production`) the session and CSRF cookies are
`Secure`: a browser only sends them over HTTPS, so **signing in works only over
HTTPS**. This guide puts PsikoNot behind [Caddy](https://caddyserver.com/), which
holds the certificate, redirects `http://` to `https://` and passes requests to
the app over an internal Docker network. Only Caddy is reachable; the app's own
port (8000) is not published on the server.

```
center's computers ──https :443──▶ caddy ──http, internal network──▶ psikonot :8000
                   ──http  :80 ──▶ caddy (redirect to https)
```

Files involved:

| File | What it is |
| :--- | :--- |
| `docker-compose.yml` | The app (base file; publishes no port, never used alone) |
| `docker-compose.prod.yml` | Production: adds Caddy and the networks, fixes the production settings |
| `docker-compose.demo.yml` | The public demo (plain HTTP on loopback, synthetic data) — unchanged |
| `deploy/caddy/Caddyfile` | Caddy's configuration; configured through `.env`, not edited by hand |
| `.env.production.example` | Template for the `.env` file production reads |
| `scripts/check_https_stack.py` | Checks a running deployment from a client computer |

The production file is an **overlay** on the base file rather than a separate,
self-contained file, so the app's definition (image, volumes, health check)
exists once and the demo keeps working exactly as before.

---

## 1. Before you start

- A Linux server inside the center's network with Docker Engine and Docker
  Compose **v2.24 or later** (`docker compose version`).
- Ports 80 and 443 free on the server.
- A name for the server that the center's computers will type, for example
  `psikonot.merkez.lan`. It must resolve to the server's address: an entry in
  the center's router/DNS, or a line in each computer's hosts file
  (`192.168.1.10  psikonot.merkez.lan`). Prefer a name over a bare IP address:
  passkeys are bound to it.
- The two keys, `HEALTH_BLOCKCHAIN_KEY` and `PSEUDONYM_SECRET`, generated once
  and stored in the center's secret store (see [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md)).

## 2. Pick a certificate mode

Set `PSIKONOT_TLS_MODE` in `.env`. Nothing else changes between the modes.

| Mode | When | What you do |
| :--- | :--- | :--- |
| `internal` *(default)* | The server is only reachable inside the center (the usual case). | Caddy runs its own small certificate authority. Install its root certificate on each of the center's computers once (step 5). |
| `acme` | The server has a **public** domain name and ports 80 and 443 are reachable from the internet. | Set `PSIKONOT_ACME_EMAIL`. Caddy gets a Let's Encrypt certificate (ZeroSSL as fallback) and renews it. Note that the name then appears in public certificate logs. |
| `files` | The center (or its IT provider) already has a certificate for the name, e.g. from a company CA. | Put the full chain as `cert.pem` and the key as `key.pem` in `deploy/caddy/certs/` (or the folder in `PSIKONOT_CERT_DIR`). It is mounted read-only. Make `key.pem` readable by root only. |

An unknown mode stops Caddy with `File to import not found: tls_<mode>`.

## 3. Fill in `.env`

```bash
cp .env.production.example .env
chmod 600 .env
nano .env
```

Set at least `PSIKONOT_DOMAIN`, `PSIKONOT_TLS_MODE` and `VHV_TIMEZONE`, and
narrow `ALLOWLISTED_NETWORKS` to the center's LAN (section 7). The keys:

- **Better:** leave them out of `.env` and have your secret store export them in
  the shell that runs `docker compose up` (a value in the shell wins over
  `.env`). For example with 1Password: `op run --env-file=keys.env -- docker compose up -d`.
- **Simpler:** put them in `.env`, which then must stay root-only and be backed
  up like the keys themselves.

Either way the app refuses to start without them. `.env` is in `.gitignore` and
`.dockerignore`, so it is never committed nor copied into the image.

The production file fixes `ENVIRONMENT=production`, `VHV_DEMO_MODE=false`,
`VHV_IP_ALLOWLIST_ENABLED=true`, `TRUST_PROXIES=true`, `TRUSTED_PROXIES` and the
passkey settings (`VHV_WEBAUTHN_RP_ID`, `VHV_WEBAUTHN_ORIGINS` from
`PSIKONOT_DOMAIN`); `.env` cannot change them.

## 4. Start

`.env.production.example` sets `COMPOSE_FILE`, so plain `docker compose`
commands use both files:

```bash
docker compose up -d --build
docker compose ps        # psikonot_app (healthy) and caddy, only caddy has ports
docker compose logs caddy | grep -i certificate
```

Without `COMPOSE_FILE`, name the files each time:
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.

Create the first accounts on the server, as in
[ONBOARDING_PROTOCOL.md](ONBOARDING_PROTOCOL.md):
`docker exec -it psikonot_app python -m scripts.create_privileged_account ...`.

## 5. Mode `internal`: install the root certificate on the center's computers

Copy the root certificate off the server (it is public; the private key never
leaves the `caddy_data` volume):

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./psikonot-root.crt
```

Bring `psikonot-root.crt` to each computer (USB stick, shared folder) and check
that it is the right file: compare its SHA-256 fingerprint with the one on the
server (`openssl x509 -in psikonot-root.crt -noout -fingerprint -sha256`).

- **Windows** (as administrator):
  `certutil -addstore -f Root psikonot-root.crt`
  — or double-click it → *Install Certificate* → *Local Machine* →
  *Trusted Root Certification Authorities*. Edge and Chrome use it at once.
- **macOS:**
  `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain psikonot-root.crt`
- **Linux (Debian/Ubuntu):**
  `sudo cp psikonot-root.crt /usr/local/share/ca-certificates/psikonot-root.crt && sudo update-ca-certificates`
- **Firefox** keeps its own list: *Settings → Privacy & Security → Certificates →
  View Certificates → Authorities → Import*, tick "trust this CA to identify
  websites". (On Windows and macOS, setting `security.enterprise_roots.enabled`
  to `true` in `about:config` makes Firefox use the system list instead.)
- **Phones and tablets:** install the file as a CA profile (iOS: also enable it
  under *Settings → General → About → Certificate Trust Settings*).

Restart the browser afterwards. The address bar must show a padlock without a
warning. Never tell staff to click through a certificate warning: that is
exactly what an attacker on the network would rely on.

The root is valid for about 10 years. Caddy issues the server certificate from
it and renews it by itself. **Back up the `caddy_data` volume**: if it is lost,
Caddy creates a new root and every computer needs the new one.

## 6. Verify

From one of the center's computers (Python 3.10+, no extra packages):

```bash
python scripts/check_https_stack.py --domain psikonot.merkez.lan \
    --cafile psikonot-root.crt --connect 192.168.1.10
```

`--cafile` is only needed while the root is not installed on that computer
(mode `internal`) — or for a `files` certificate from a private CA. It checks
that http redirects to https, the certificate is valid for the name, HSTS and
the app's security headers arrive exactly once, and port 8000 is closed. Add
`--username <account>` (an account without 2FA; the password is read from
`PSIKONOT_CHECK_PASSWORD` or prompted) to check that signing in sets a `Secure;
HttpOnly; SameSite=Strict` cookie and the session works. Add `--spoof-check` to
check that a client cannot pick its own address with `X-Forwarded-For` — it uses
up that computer's sign-in attempts for a minute, so run it last. Exit status 0
means every check passed.

By hand: open `http://psikonot.merkez.lan` — the browser must land on
`https://psikonot.merkez.lan` with a padlock, and signing in must keep you
signed in.

## 7. Client addresses, proxy trust and the IP allowlist

- Caddy sits at a **fixed address**, `172.31.254.2`, on the internal `backend`
  network (`172.31.254.0/24`). The app trusts `X-Forwarded-For` only from that
  one address (`TRUST_PROXIES=true`, `TRUSTED_PROXIES=172.31.254.2`).
- Caddy discards any `X-Forwarded-For` a client sends and writes the address it
  saw; the app reads the list from the right. So the sign-in rate limit, the
  audit log and the IP allowlist see each computer's real address, and a client
  cannot claim another one.
- `ALLOWLISTED_NETWORKS` (in `.env`) lists the client networks the app accepts,
  comma-separated CIDRs. Empty means the private ranges `10.0.0.0/8`,
  `172.16.0.0/12`, `192.168.0.0/16` and loopback. Narrow it to the center's LAN
  and VPN, e.g. `ALLOWLISTED_NETWORKS=192.168.1.0/24,10.8.0.0/24`. A refused
  computer gets `403 Network Security Violation` with the address the app saw
  (`client_ip`), which is also logged as an `UNAUTHORIZED_IP_ACCESS` alert.
- **Docker Desktop** (Windows/macOS) hides client addresses: every connection
  reaches Caddy from the Docker network's gateway (e.g. `172.25.0.1`). Run
  production on a Linux host, where Docker keeps the real source address.
- If `172.31.254.0/24` is already used in the center's network, change the
  subnet, `ip_range`, Caddy's `ipv4_address` and `TRUSTED_PROXIES` together in
  `docker-compose.prod.yml`.

The `backend` network is `internal`: the app has no route out of it. With
`KMS_PROVIDER=vault` and Vault on another host, add a network that reaches Vault
to the `psikonot` service in a local override file.

**Firewall note:** ports published by Docker bypass `ufw`/`firewalld` rules on
the host. To keep PsikoNot off other networks the server is attached to, set
`PSIKONOT_BIND_ADDRESS` to the server's LAN address.

## 8. Headers, limits and timeouts

- **Security headers** (CSP, HSTS, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy) are set by the app and passed through
  unchanged. Caddy only fills in HSTS if a response lacks it, adds HSTS to the
  error pages it writes itself (502 while the app restarts, 413), and removes
  the `Server` and `Via` headers.
- **HSTS** (`max-age=31536000; includeSubDomains`) is sent on HTTPS only; the
  port-80 answer is just the redirect. After the first visit the browser will
  not use plain HTTP for the name for a year.
- **`X-Forwarded-Proto: https`** is set by Caddy.
- **Request size:** at most 10 MB. The largest request the app takes is a
  record with a 2 MB attachment (about 2.7 MB as base64 in JSON); larger bodies
  get `413` from Caddy before reaching the app.
- **Timeouts:** 10 s to send headers, 60 s to send a request, 180 s to write a
  response, 2 min idle; 5 s to reach the app and 120 s for it to answer.
- **One worker:** Caddy forwards to a single app process (ADR-0002). Do not add
  `--workers`, replicas or a second app container behind it.
- HTTP/3 is off (it would need UDP 443 published as well).

## 9. Renewals and certificate changes

- **`acme`:** automatic, about 30 days before expiry. Ports 80/443 must stay
  reachable from the internet, and Caddy needs outbound internet access.
- **`internal`:** automatic and offline. Server certificates live 12 hours and
  are renewed in the background; the intermediate is renewed weekly; the root
  lasts about 10 years (reinstall it on the computers before then, or after
  losing `caddy_data`).
- **`files`:** replace `cert.pem`/`key.pem` before the old ones expire, then
  `docker compose restart caddy`. Caddy does not notice changed files on its
  own. Put the expiry date in the center's calendar.
- Changing the name or mode: edit `.env`, then `docker compose up -d`.

## 10. Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| Signing in "works" but you are signed out at once | The page was opened over `http://` or through a certificate warning; the browser drops `Secure` cookies. Use the https name with a trusted certificate. |
| Certificate warning | Mode `internal`: the root is not installed on that computer/browser (Firefox has its own list). Name mismatch: the address typed is not `PSIKONOT_DOMAIN`. |
| `403 Network Security Violation` | The computer's address (shown as `client_ip`) is outside `ALLOWLISTED_NETWORKS`. |
| `502 Bad Gateway` | The app is starting or has stopped: `docker compose logs psikonot` (missing keys stop it at start). |
| Caddy does not start | `docker compose logs caddy`: an unknown `PSIKONOT_TLS_MODE`, missing `cert.pem`/`key.pem` in mode `files`, or ports 80/443 already in use. |
| Passkey sign-in fails | Passkeys are bound to `PSIKONOT_DOMAIN`; ones enrolled under another name (or `localhost`) must be enrolled again. |
