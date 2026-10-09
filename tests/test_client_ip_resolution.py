"""
tests/test_client_ip_resolution.py — the client IP behind a trusted proxy
=========================================================================
A proxy APPENDS the address it saw to X-Forwarded-For, so only the right end of
the list is written by our own proxies; the left end is whatever the client sent.
The resolver used to take the left-most value. Behind a trusted proxy a client
could then pick its own IP: a private one to pass the IP allowlist, and a new
one on every sign-in attempt to never hit the rate limit.
"""

import os
import sys
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from backend.middleware.ip_allowlist import resolve_secure_client_ip
from backend.middleware.rate_limiter import RATE_LIMIT_MAX
from database.sql_db import default_sql_db

PROXY_ENV = {"TRUST_PROXIES": "true", "TRUSTED_PROXIES": "10.0.0.1,172.28.0.0/24"}


def _request(peer: str, *forwarded: str, real_ip: str = None) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    if real_ip:
        headers.append((b"x-real-ip", real_ip.encode()))
    return Request({"type": "http", "method": "GET", "path": "/",
                    "client": (peer, 1234), "headers": headers})


class TestClientIpResolution(unittest.TestCase):
    def _resolve(self, peer, *forwarded, real_ip=None, env=PROXY_ENV):
        with mock.patch.dict(os.environ, env):
            return resolve_secure_client_ip(_request(peer, *forwarded, real_ip=real_ip))

    def test_spoofed_leftmost_value_is_ignored(self):
        self.assertEqual(self._resolve("10.0.0.1", "10.1.2.3, 203.0.113.9"), "203.0.113.9")

    def test_single_hop(self):
        self.assertEqual(self._resolve("10.0.0.1", "203.0.113.9"), "203.0.113.9")

    def test_multiple_xff_headers_are_read_together(self):
        self.assertEqual(self._resolve("10.0.0.1", "10.1.2.3", "203.0.113.9"), "203.0.113.9")

    def test_trusted_hops_on_the_right_are_skipped(self):
        # Two proxies of our own (CIDR entry): the client is the hop before them.
        self.assertEqual(self._resolve("172.28.0.5", "198.51.100.7, 172.28.0.9"), "198.51.100.7")

    def test_garbage_on_the_left_is_never_reached(self):
        self.assertEqual(self._resolve("10.0.0.1", "garbage, 203.0.113.9"), "203.0.113.9")

    def test_garbage_on_the_right_falls_back_to_the_peer(self):
        # A broken chain: never skip past it to values the client wrote.
        self.assertEqual(self._resolve("10.0.0.1", "203.0.113.9, garbage"), "10.0.0.1")

    def test_x_real_ip_is_ignored(self):
        # A proxy that does not set X-Real-IP passes on whatever the client sent.
        self.assertEqual(self._resolve("10.0.0.1", real_ip="10.9.9.9"), "10.0.0.1")

    def test_all_hops_trusted_returns_the_peer(self):
        self.assertEqual(self._resolve("10.0.0.1", "127.0.0.1, 10.0.0.1"), "10.0.0.1")

    def test_untrusted_peer_ignores_the_header(self):
        self.assertEqual(self._resolve("203.0.113.195", "127.0.0.1"), "203.0.113.195")

    def test_testclient_name_counts_as_loopback(self):
        self.assertEqual(self._resolve("testclient", "10.1.2.3, 192.168.1.20"), "192.168.1.20")

    def test_proxies_are_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRUST_PROXIES", None)
            self.assertEqual(resolve_secure_client_ip(_request("10.0.0.1", "10.1.2.3")), "10.0.0.1")

    def test_invalid_trusted_proxies_entry_is_ignored(self):
        env = {"TRUST_PROXIES": "true", "TRUSTED_PROXIES": "nope,10.0.0.1"}
        self.assertEqual(self._resolve("10.0.0.1", "203.0.113.9", env=env), "203.0.113.9")


def _clear_attempts():
    conn = default_sql_db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM rate_limits")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _behind_proxy(peer: str):
    """The whole app as reached through a proxy at `peer`."""
    async def wrapped(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(peer, 1234))
        await app(scope, receive, send)
    return wrapped


class TestSpoofingThroughTheApp(unittest.TestCase):
    """The same rule, end to end: the rate limit and the IP allowlist."""

    def setUp(self):
        self._testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "false"   # switch the rate limit on
        _clear_attempts()
        self.env = mock.patch.dict(os.environ, PROXY_ENV)
        self.env.start()
        self.client = TestClient(_behind_proxy("10.0.0.1"))

    def tearDown(self):
        self.env.stop()
        _clear_attempts()
        if self._testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = self._testing

    def test_rotating_spoofed_values_cannot_bypass_the_login_limit(self):
        body = {"username": "secretary.ayse", "password": "wrong-password"}
        codes = []
        for i in range(RATE_LIMIT_MAX + 1):
            # A new invented address each time; the proxy appends the real one.
            headers = {"X-Forwarded-For": f"10.66.0.{i + 1}, 192.168.1.20"}
            codes.append(self.client.post("/api/v1/auth/login", json=body, headers=headers).status_code)
        self.assertNotIn(429, codes[:RATE_LIMIT_MAX])
        self.assertEqual(codes[-1], 429, codes)

    def test_spoofed_private_address_does_not_pass_the_allowlist(self):
        res = self.client.get("/api/v1/config", headers={"X-Forwarded-For": "10.1.2.3, 203.0.113.9"})
        self.assertEqual(res.status_code, 403, res.text)


if __name__ == "__main__":
    unittest.main()
