"""The logic of scripts/check_https_stack.py, against a fake stack.

The script itself needs a running Caddy + app (docs/HTTPS_DEPLOYMENT.md); here
each check is fed the answers a good and a broken deployment would give.
"""

import os
import socket
import ssl
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import check_https_stack as chk  # noqa: E402
from scripts.check_https_stack import Reply  # noqa: E402

GOOD_HEADERS = [
    ("strict-transport-security", "max-age=31536000; includeSubDomains"),
    ("content-security-policy", "default-src 'self'"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "SAMEORIGIN"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
]
GOOD_COOKIE = "access_token=abc.def; HttpOnly; Max-Age=28800; Path=/; SameSite=strict; Secure"


class FakeClient:
    """Answers by (method, path, https); a list of replies is served in order."""

    domain = "psikonot.merkez.lan"
    http_port = 80
    https_port = 443

    def __init__(self, routes=None, open_ports=()):
        self.routes = routes or {}
        self.open_ports = set(open_ports)
        self.sent = []

    def request(self, method, path, *, https=True, headers=None, body=None):
        self.sent.append((method, path, https, dict(headers or {})))
        answer = self.routes[(method, path, https)]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, list):
            return answer.pop(0)
        return answer

    def port_open(self, host, port):
        return (host, port) in self.open_ports


def good_stack(login_replies=None):
    return FakeClient({
        ("GET", "/", False): Reply(308, [("Location", "https://psikonot.merkez.lan/")]),
        ("GET", chk.HEALTH_PATH, True): Reply(200, list(GOOD_HEADERS)),
        ("POST", chk.LOGIN_PATH, True): login_replies or Reply(200, [("set-cookie", GOOD_COOKIE)], b"{}"),
        ("GET", chk.ME_PATH, True): Reply(200, [], b"{}"),
    })


class CheckHttpsStackTests(unittest.TestCase):
    # ── cookies ─────────────────────────────────────────────────────────────────

    def test_cookie_attributes_reads_name_and_flags(self):
        name, attrs = chk.cookie_attributes(GOOD_COOKIE)
        assert name == "access_token"
        assert {"httponly", "secure"} <= set(attrs)
        assert attrs["samesite"] == "strict"


    def test_find_cookie_picks_the_named_one(self):
        reply = Reply(200, [("set-cookie", "csrf_token=x; Secure"), ("Set-Cookie", GOOD_COOKIE)])
        assert chk.find_cookie(reply, "access_token") == GOOD_COOKIE
        assert chk.find_cookie(reply, "missing") is None


    # ── redirect ────────────────────────────────────────────────────────────────

    def test_redirect_to_https_passes(self):
        assert chk.check_redirect(good_stack()).ok


    def test_redirect_to_another_host_or_plain_http_fails(self):
        for location in ("https://evil.example/", "http://psikonot.merkez.lan/", "https://psikonot.merkez.lan.evil/"):
            client = FakeClient({("GET", "/", False): Reply(308, [("Location", location)])})
            assert not chk.check_redirect(client).ok, location


    def test_no_redirect_fails(self):
        client = FakeClient({("GET", "/", False): Reply(200, [])})
        assert not chk.check_redirect(client).ok


    def test_closed_http_port_fails_with_a_reason(self):
        client = FakeClient({("GET", "/", False): ConnectionRefusedError("refused")})
        result = chk.check_redirect(client)
        assert not result.ok and "refused" in result.detail


    # ── TLS and headers ─────────────────────────────────────────────────────────

    def test_headers_once_each_pass(self):
        assert chk.check_tls_and_headers(good_stack()).ok


    def test_duplicated_csp_fails(self):
        client = good_stack()
        client.routes[("GET", chk.HEALTH_PATH, True)] = Reply(
            200, GOOD_HEADERS + [("Content-Security-Policy", "default-src *")])
        result = chk.check_tls_and_headers(client)
        assert not result.ok and "content-security-policy sent 2 times" in result.detail


    def test_missing_hsts_fails(self):
        client = good_stack()
        client.routes[("GET", chk.HEALTH_PATH, True)] = Reply(200, GOOD_HEADERS[1:])
        assert not chk.check_tls_and_headers(client).ok


    def test_server_header_fails(self):
        client = good_stack()
        client.routes[("GET", chk.HEALTH_PATH, True)] = Reply(200, GOOD_HEADERS + [("server", "uvicorn")])
        result = chk.check_tls_and_headers(client)
        assert not result.ok and "uvicorn" in result.detail


    def test_untrusted_certificate_says_what_to_do(self):
        error = ssl.SSLCertVerificationError("bad")
        error.verify_message = "self-signed certificate"
        client = good_stack()
        client.routes[("GET", chk.HEALTH_PATH, True)] = error
        result = chk.check_tls_and_headers(client)
        assert not result.ok and "--cafile" in result.detail


    # ── app port ────────────────────────────────────────────────────────────────

    def test_app_port_published_fails(self):
        client = FakeClient(open_ports={("10.0.0.5", 8000)})
        assert not chk.check_app_port_closed(client, "10.0.0.5", 8000).ok
        assert chk.check_app_port_closed(FakeClient(), "10.0.0.5", 8000).ok


    def test_port_open_on_real_sockets(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            assert chk.port_open("127.0.0.1", port)
        # The listener is closed now; nothing answers on that port.
        assert not chk.port_open("127.0.0.1", port, timeout=1)


    # ── sign-in ─────────────────────────────────────────────────────────────────

    def test_sign_in_with_strict_secure_cookie_passes(self):
        client = good_stack()
        result = chk.check_sign_in(client, "sec.officer", "pw")
        assert result.ok, result.detail
        # The session was then used, with only the token sent back.
        method, path, https, headers = client.sent[-1]
        assert (method, path, https) == ("GET", chk.ME_PATH, True)
        assert headers["Cookie"] == "access_token=abc.def"


    def test_cookie_without_secure_fails(self):
        weak = "access_token=abc; HttpOnly; Path=/; SameSite=strict"
        client = good_stack(Reply(200, [("set-cookie", weak)], b"{}"))
        result = chk.check_sign_in(client, "u", "pw")
        assert not result.ok and "secure" in result.detail


    def test_cookie_with_lax_samesite_fails(self):
        lax = "access_token=abc; HttpOnly; Secure; SameSite=lax"
        client = good_stack(Reply(200, [("set-cookie", lax)], b"{}"))
        assert not chk.check_sign_in(client, "u", "pw").ok


    def test_wrong_password_and_2fa_accounts_fail_clearly(self):
        client = good_stack(Reply(401, [], b'{"detail":"Incorrect username or password"}'))
        assert "401" in chk.check_sign_in(client, "u", "pw").detail
        client = good_stack(Reply(200, [], b'{"mfa_required": true}'))
        assert "2FA" in chk.check_sign_in(client, "u", "pw").detail


    def test_session_not_accepted_fails(self):
        client = good_stack()
        client.routes[("GET", chk.ME_PATH, True)] = Reply(401, [], b"")
        assert not chk.check_sign_in(client, "u", "pw").ok


    # ── X-Forwarded-For spoofing ────────────────────────────────────────────────

    def test_limit_reached_despite_changing_header_passes(self):
        replies = [Reply(401), Reply(401), Reply(401), Reply(401), Reply(429)]
        client = good_stack(replies)
        result = chk.check_spoofing_ignored(client)
        assert result.ok
        claimed = [h["X-Forwarded-For"] for m, p, s, h in client.sent]
        assert len(set(claimed)) == 5  # a different address every time


    def test_never_limited_means_the_header_is_trusted(self):
        client = good_stack([Reply(401) for _ in range(chk.SPOOF_ATTEMPTS)])
        result = chk.check_spoofing_ignored(client)
        assert not result.ok and "trusting the header" in result.detail
        assert len(client.sent) == chk.SPOOF_ATTEMPTS


    # ── putting it together ────────────────────────────────────────────────────

    def test_run_checks_skips_what_was_not_asked_for(self):
        names = [r.name for r in chk.run_checks(good_stack(), app_host=None, app_port=None)]
        assert len(names) == 2


    def test_run_checks_and_report_on_a_good_stack(self):
        client = good_stack([Reply(200, [("set-cookie", GOOD_COOKIE)], b"{}"), Reply(401), Reply(429)])
        results = chk.run_checks(client, app_host="10.0.0.5", app_port=8000,
                                 username="u", password="pw", spoof_check=True)
        lines = []
        assert chk.report(results, lines.append)
        assert len(results) == 5 and lines[-1] == "All checks passed."


    def test_report_flags_a_failure(self):
        lines = []
        assert not chk.report([chk.Result("a", True, ""), chk.Result("b", False, "why")], lines.append)
        assert "FAIL  b: why" in lines and lines[-1] == "Some checks FAILED."

    def test_main_exit_status(self):
        seen = {}

        def fake_run(client, **kwargs):
            seen.update(kwargs, domain=client.domain, connect=client.connect)
            return [chk.Result("x", True, "")]

        with mock.patch.object(chk, "run_checks", fake_run),                 mock.patch.dict(os.environ, {"PSIKONOT_CHECK_PASSWORD": "from-env"}),                 mock.patch("builtins.print"):
            self.assertEqual(chk.main(["--domain", "psikonot.merkez.lan", "--connect", "10.0.0.5",
                                       "--username", "u"]), 0)
        self.assertEqual((seen["connect"], seen["password"], seen["app_port"]), ("10.0.0.5", "from-env", 8000))

        failing = mock.patch.object(chk, "run_checks", lambda client, **kw: [chk.Result("x", False, "")])
        with failing, mock.patch("builtins.print"):
            self.assertEqual(chk.main(["--domain", "psikonot.merkez.lan", "--app-port", "0"]), 1)


if __name__ == "__main__":
    unittest.main()
