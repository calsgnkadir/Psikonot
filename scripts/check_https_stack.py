"""
scripts/check_https_stack.py — check a running HTTPS deployment from outside
============================================================================
    python scripts/check_https_stack.py --domain psikonot.merkez.lan \
        --cafile psikonot-root.crt [--connect 192.168.1.10] \
        [--username sec.officer] [--spoof-check]

Run it from one of the center's computers (or the server) after
`docker compose up -d`, and after every certificate or proxy change. It talks
to the stack the way a browser does and checks:

  redirect       http:// answers with a redirect to https://<domain>/
  tls+headers    https:// serves a certificate valid for <domain> (against
                 --cafile, or the system's trusted roots), with HSTS and the
                 app's security headers, each exactly once
  app port       the app's own port (8000) is not reachable from here
  sign-in        (--username) the session cookie is Secure, HttpOnly and
                 SameSite=Strict, and the session works over HTTPS; the
                 password is read from PSIKONOT_CHECK_PASSWORD or prompted for
  spoofing       (--spoof-check) failed sign-ins that each claim a different
                 X-Forwarded-For are still counted against this computer, so
                 the app sees the real address, not the header. This uses up
                 this computer's sign-in attempts for one minute: run it last.

Standard library only, so it runs on any computer with Python 3.10+. Exit
status 0 when every check passed, 1 otherwise. It needs a running stack, so it
is not part of the test suite (tests/test_check_https_stack.py tests its logic).
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import socket
import ssl
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

LOGIN_PATH = "/api/v1/auth/login"
ME_PATH = "/api/v1/auth/me"
HEALTH_PATH = "/api/v1/health"
REDIRECT_CODES = (301, 302, 307, 308)
# Headers the app sets on every response; a proxy must neither drop nor repeat them.
SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
)
SPOOF_ATTEMPTS = 6  # the sign-in limit is 5 per address per minute


@dataclass
class Reply:
    status: int
    headers: list = field(default_factory=list)  # [(name, value)], names as sent
    body: bytes = b""

    def all(self, name: str) -> list:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def first(self, name: str) -> Optional[str]:
        values = self.all(name)
        return values[0] if values else None


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


class _ConnectTo(http.client.HTTPSConnection):
    """HTTPS to `address` while presenting and verifying `domain`, so the check
    works before DNS is set up (like curl --resolve)."""

    def __init__(self, address: str, domain: str, port: int, context: ssl.SSLContext, timeout: float):
        super().__init__(domain, port, context=context, timeout=timeout)
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Client:
    """Sends single requests (no redirects followed, no cookie jar)."""

    def __init__(self, domain: str, connect: str, http_port: int, https_port: int,
                 cafile: Optional[str] = None, timeout: float = 10.0):
        self.domain = domain
        self.connect = connect
        self.http_port = http_port
        self.https_port = https_port
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=cafile)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

    def request(self, method: str, path: str, *, https: bool = True,
                headers: Optional[dict] = None, body: Optional[bytes] = None) -> Reply:
        if https:
            conn = _ConnectTo(self.connect, self.domain, self.https_port, self.context, self.timeout)
        else:
            conn = http.client.HTTPConnection(self.connect, self.http_port, timeout=self.timeout)
        try:
            # The Host header carries the domain whichever address is dialled.
            conn.request(method, path, body=body, headers={"Host": self.domain, **(headers or {})})
            resp = conn.getresponse()
            return Reply(resp.status, resp.getheaders(), resp.read())
        finally:
            conn.close()

    def port_open(self, host: str, port: int) -> bool:
        return port_open(host, port, self.timeout)


def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def cookie_attributes(set_cookie: str) -> tuple:
    """(name, {attribute-lowercased: value}) of one Set-Cookie header."""
    parts = [p.strip() for p in set_cookie.split(";")]
    name = parts[0].split("=", 1)[0].strip()
    attrs = {}
    for part in parts[1:]:
        if not part:
            continue
        key, _, value = part.partition("=")
        attrs[key.strip().lower()] = value.strip()
    return name, attrs


def find_cookie(reply: Reply, name: str) -> Optional[str]:
    for header in reply.all("set-cookie"):
        if cookie_attributes(header)[0] == name:
            return header
    return None


# ── The checks ──────────────────────────────────────────────────────────────

def check_redirect(client) -> Result:
    name = "http redirects to https"
    try:
        reply = client.request("GET", "/", https=False)
    except OSError as e:
        return Result(name, False, f"no answer on http port {client.http_port}: {e}")
    location = reply.first("location") or ""
    expected = f"https://{client.domain}"
    ok = reply.status in REDIRECT_CODES and (location == expected or location.startswith(expected + "/")
                                             or location.startswith(expected + ":"))
    return Result(name, ok, f"{reply.status} -> {location or '(no Location)'}")


def check_tls_and_headers(client) -> Result:
    name = "https certificate and security headers"
    try:
        reply = client.request("GET", HEALTH_PATH)
    except ssl.SSLCertVerificationError as e:
        return Result(name, False, f"certificate not trusted for {client.domain}: {e.verify_message}"
                      " (install the root certificate or pass --cafile)")
    except (OSError, ssl.SSLError) as e:
        return Result(name, False, f"no TLS answer on port {client.https_port}: {e}")
    problems = []
    if reply.status != 200:
        problems.append(f"health answered {reply.status}")
    for header in SECURITY_HEADERS:
        count = len(reply.all(header))
        if count != 1:
            problems.append(f"{header} sent {count} times")
    hsts = reply.first("strict-transport-security") or ""
    if "max-age=" not in hsts:
        problems.append("HSTS has no max-age")
    if reply.first("server"):
        problems.append(f"Server header reveals {reply.first('server')!r}")
    detail = "; ".join(problems) if problems else f"200, HSTS {hsts!r}, headers once each"
    return Result(name, not problems, detail)


def check_app_port_closed(client, host: str, port: int) -> Result:
    name = f"app port {port} not reachable on {host}"
    if client.port_open(host, port):
        return Result(name, False, "a TCP connection succeeded: the app port is published")
    return Result(name, True, "connection refused or timed out")


def _login(client, username: str, password: str, extra_headers: Optional[dict] = None) -> Reply:
    body = json.dumps({"username": username, "password": password}).encode()
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    return client.request("POST", LOGIN_PATH, headers=headers, body=body)


def check_sign_in(client, username: str, password: str) -> Result:
    name = "sign-in over https sets a Secure, HttpOnly, SameSite=Strict session"
    reply = _login(client, username, password)
    if reply.status != 200:
        return Result(name, False, f"sign-in answered {reply.status}: {reply.body[:200]!r}")
    cookie = find_cookie(reply, "access_token")
    if cookie is None:
        try:
            if json.loads(reply.body).get("mfa_required"):
                return Result(name, False, "the account asks for a 2FA code; use one without 2FA for this check")
        except ValueError:
            pass
        return Result(name, False, "no access_token cookie in the answer")
    _, attrs = cookie_attributes(cookie)
    problems = [flag for flag in ("secure", "httponly") if flag not in attrs]
    if attrs.get("samesite", "").lower() != "strict":
        problems.append(f"samesite={attrs.get('samesite') or 'missing'}")
    if problems:
        return Result(name, False, "cookie lacks " + ", ".join(problems))
    token = cookie.split(";", 1)[0]
    me = client.request("GET", ME_PATH, headers={"Cookie": token})
    if me.status != 200:
        return Result(name, False, f"cookie set, but {ME_PATH} answered {me.status}")
    return Result(name, True, f"cookie flags {sorted(attrs)}; {ME_PATH} -> 200")


def check_spoofing_ignored(client, attempts: int = SPOOF_ATTEMPTS) -> Result:
    """Each failed sign-in claims a different client address. If the app took
    X-Forwarded-For from the client, every attempt would count against a new
    address and none would be limited; seeing 429 shows it counts the real one."""
    name = "X-Forwarded-For from the client is ignored"
    statuses = []
    for i in range(attempts):
        reply = _login(client, "no-such-user.https-check", "wrong-password",
                       {"X-Forwarded-For": f"203.0.113.{10 + i}"})
        statuses.append(reply.status)
        if reply.status == 429:
            return Result(name, True, f"limited after {i + 1} attempts with {i + 1} different "
                                      f"X-Forwarded-For values ({statuses})")
    return Result(name, False, f"never limited ({statuses}): the app may be trusting the header")


def run_checks(client, *, app_host: Optional[str], app_port: Optional[int],
               username: Optional[str] = None, password: Optional[str] = None,
               spoof_check: bool = False) -> list:
    results = [check_redirect(client), check_tls_and_headers(client)]
    if app_host and app_port:
        results.append(check_app_port_closed(client, app_host, app_port))
    if username:
        results.append(check_sign_in(client, username, password or ""))
    if spoof_check:
        results.append(check_spoofing_ignored(client))
    return results


def report(results: list, out: Optional[Callable[[str], None]] = None) -> bool:
    out = out or print
    for r in results:
        out(f"{'PASS' if r.ok else 'FAIL'}  {r.name}: {r.detail}")
    passed = all(r.ok for r in results)
    out("All checks passed." if passed else "Some checks FAILED.")
    return passed


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Check a running PsikoNot HTTPS deployment.")
    p.add_argument("--domain", required=True, help="the name in PSIKONOT_DOMAIN")
    p.add_argument("--connect", help="address to dial instead of resolving --domain (e.g. the server's LAN IP)")
    p.add_argument("--http-port", type=int, default=80)
    p.add_argument("--https-port", type=int, default=443)
    p.add_argument("--cafile", help="root certificate to trust (mode internal: Caddy's root.crt)")
    p.add_argument("--app-port", type=int, default=8000, help="the app's own port, which must be closed (0 skips)")
    p.add_argument("--username", help="an account to sign in with (without 2FA)")
    p.add_argument("--spoof-check", action="store_true",
                   help="check X-Forwarded-For spoofing (uses up this computer's sign-in attempts for a minute)")
    args = p.parse_args(argv)

    connect = args.connect or args.domain
    password = None
    if args.username:
        password = os.environ.get("PSIKONOT_CHECK_PASSWORD") or getpass.getpass(f"Password for {args.username}: ")
    client = Client(args.domain, connect, args.http_port, args.https_port, args.cafile)
    results = run_checks(client, app_host=connect, app_port=args.app_port or None,
                         username=args.username, password=password, spoof_check=args.spoof_check)
    return 0 if report(results) else 1


if __name__ == "__main__":
    sys.exit(main())
