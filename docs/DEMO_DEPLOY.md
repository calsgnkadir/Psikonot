# Public Demo Deploy (Fly.io)

> **DEMO ONLY — synthetic data.** The real vault is private-VPC only
> (see `PRIVATE_VPC_DEPLOYMENT.md`). This path runs the demo profile: demo mode,
> throwaway seeded accounts (passwords are public in the README), IP allowlist off,
> an auto-generated signing key, and the on-screen DEMO ribbon. Never put real
> records here. Config lives in `fly.toml`.

## One time

1. **Install flyctl** (Windows PowerShell):
   ```powershell
   iwr https://fly.io/install.ps1 -useb | iex
   ```
   (macOS/Linux: `curl -L https://fly.io/install.sh | sh`)

2. **Sign in / up:**
   ```bash
   fly auth signup   # or: fly auth login
   ```
   A card is required for verification; a single tiny auto-stopping machine costs
   little and often stays within Fly's small monthly usage.

## Deploy

3. From the repo root, launch (detects `fly.toml` + `Dockerfile`):
   ```bash
   fly launch
   ```
   - "Copy configuration to a new app?" → **Yes**.
   - It assigns a globally-unique app name (replaces the `CHANGE-ME` placeholder),
     or edit `app = "..."` in `fly.toml` first.
   - Postgres / Redis / Upstash → **No** (this app uses local LMDB + SQLite).

4. Ship it:
   ```bash
   fly deploy
   ```

5. Open it:
   ```bash
   fly open
   ```
   → `https://<your-app>.fly.dev` — TLS is automatic. You'll see the DEMO ribbon.
   Log in with a demo account (e.g. `psk.elif` / `Practitioner@2026!` or `secretary.ayse` / `Secretary@2026!`).

## Notes

- `VHV_BIND_HOST=0.0.0.0` and `PORT=8080` in `fly.toml` are **required** — the app
  defaults to loopback, which Fly's proxy cannot reach.
- Data is ephemeral (no volume mounted); a redeploy reseeds fresh demo data. That
  is fine for a demo. To persist, add a Fly volume and mount `backend/projects` +
  `database/` — not needed for a throwaway demo.
- Logs: `fly logs`. Status: `fly status`. Tear down: `fly apps destroy <name>`.
