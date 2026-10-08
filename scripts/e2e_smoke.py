"""
scripts/e2e_smoke.py — end-to-end smoke test against a running demo.

Walks the access model through the real HTTP API, the way a browser would
(httpOnly cookie + CSRF double-submit token):

  1. the demo accounts are advertised by /config;
  2. the practitioner sees their clients with the tally, and reads CL-001's file;
  3. the secretary sees the same client list and the appointment book;
  4. the secretary is refused the file (403);
  5. the administrator is refused the file without a dual-control co-signature.

It changes nothing, and signs in three times (one session per account), within
the demo's limit of 5 sign-ins per IP per minute.

Usage (against the Docker demo on :8000):
    python scripts/e2e_smoke.py
    python scripts/e2e_smoke.py http://127.0.0.1:8093
"""

import sys

import httpx

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000") + "/api/v1"
CLIENT_ID = "CL-001"


def session(accounts: dict, role: str) -> httpx.Client:
    """A signed-in session for the demo account with this role."""
    account = accounts[role]
    s = httpx.Client(base_url=BASE_URL, timeout=120)
    s.get("/config")                                  # sets the csrf_token cookie
    s.headers["X-CSRF-Token"] = s.cookies.get("csrf_token")
    r = s.post("/auth/login", json={"username": account["username"], "password": account["password"]})
    check(r.status_code == 200, f"{role} signs in", r)
    return s


def check(ok: bool, what: str, response=None):
    if not ok:
        detail = f" -> {response.status_code} {response.text[:200]}" if response is not None else ""
        print(f"FAILED: {what}{detail}")
        sys.exit(1)
    print(f"ok  {what}")


def main():
    print(f"=== PsikoNot end-to-end smoke test against {BASE_URL} ===")

    r = httpx.get(f"{BASE_URL}/config", timeout=30)
    config = r.json()
    check(r.status_code == 200 and config.get("demo_mode") is True, "demo mode is on", r)
    accounts = {a["role"]: a for a in config.get("demo_accounts", [])}
    check({"PRACTITIONER", "SECRETARY", "ADMIN"} <= set(accounts), "demo practitioner, secretary and admin exist")

    prac = session(accounts, "PRACTITIONER")
    secretary = session(accounts, "SECRETARY")
    admin = session(accounts, "ADMIN")

    # The practitioner: their clients, and the file.
    r = prac.get("/clients")
    clients = {c["patient_id"]: c for c in r.json().get("clients", [])}
    check(CLIENT_ID in clients and "attended" in clients[CLIENT_ID], "the practitioner lists clients with the tally", r)
    r = prac.get(f"/records/{CLIENT_ID}")
    check(r.status_code == 200 and r.json().get("records"), "the practitioner reads the client's file", r)

    # The secretary: the same practice log, never the file.
    r = secretary.get("/clients")
    check(CLIENT_ID in {c["patient_id"] for c in r.json().get("clients", [])}, "the secretary sees the client card", r)
    r = secretary.get("/appointments")
    check(r.status_code == 200, "the secretary sees the appointment book", r)
    r = secretary.get(f"/records/{CLIENT_ID}")
    check(r.status_code == 403, "the secretary is refused the file", r)

    # The administrator: refused without a second person's co-signature.
    r = admin.get(f"/records/{CLIENT_ID}")
    check(r.status_code == 403, "the administrator is refused the file without dual control", r)

    for s in (prac, secretary, admin):
        s.post("/auth/logout")
    print("=== all end-to-end checks passed ===")


if __name__ == "__main__":
    main()
