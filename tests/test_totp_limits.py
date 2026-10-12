"""
tests/test_totp_limits.py — 2FA codes are rate limited and single-use
=====================================================================
/2fa/enable and /2fa/disable were not rate limited, so a stolen session could
guess the 6-digit code (about 3 in a million per try with the ±1 step window),
switch 2FA off and bind the attacker's phone. A code was also accepted again
for its whole ~90-second window, so someone who watched the owner type it
could sign in right after them.
"""

import os
import sys
import time
import unittest
import uuid
from unittest import mock

import pyotp
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.totp
from backend.dependencies import create_token
from backend.main import app
from backend.middleware.rate_limiter import RATE_LIMIT_MAX
from core.domain.entities import User
from core.security import hash_password
from database.sql_db import default_sql_db
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Totp@Limits2026!"


def _clear_attempts(only_ip: bool = False):
    conn = default_sql_db.get_connection()
    cur = conn.cursor()
    try:
        if only_ip:
            # What an attacker gets by moving to a new address.
            cur.execute("DELETE FROM rate_limits WHERE ip NOT LIKE 'account:%'")
        else:
            cur.execute("DELETE FROM rate_limits")
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _make_user(enabled: bool) -> User:
    user = User(
        id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=f"totp.{uuid.uuid4().hex[:8]}",
        password_hash=hash_password(PASSWORD), role="practitioner",
        full_name="TOTP Test", account_status="ACTIVE_ENROLLED",
        totp_secret=pyotp.random_base32(), totp_enabled=enabled,
    )
    SQLUserRepository().save_user(user)
    return user


class TestTotpRateLimit(unittest.TestCase):
    def setUp(self):
        self._testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "false"   # switch the rate limit on
        _clear_attempts()
        self.client = TestClient(app)

    def tearDown(self):
        _clear_attempts()
        if self._testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = self._testing

    def _post(self, user, path, code):
        # CSRF is enforced when TESTING is off. The cookie is set by hand: it is
        # Secure and the test client speaks plain http.
        headers = {"Authorization": f"Bearer {create_token(user.to_dict())}",
                   "Cookie": "csrf_token=t0ken", "X-CSRF-Token": "t0ken"}
        return self.client.post(path, json={"code": code}, headers=headers).status_code

    def _assert_limited(self, user, path, rotate_ip=False):
        for _ in range(RATE_LIMIT_MAX):
            self.assertEqual(self._post(user, path, "000000"), 400)
            if rotate_ip:
                _clear_attempts(only_ip=True)
        self.assertEqual(self._post(user, path, "000000"), 429)

    def test_disable_is_limited(self):
        self._assert_limited(_make_user(enabled=True), "/api/v1/auth/2fa/disable")

    def test_enable_is_limited(self):
        self._assert_limited(_make_user(enabled=False), "/api/v1/auth/2fa/enable")

    def test_disable_is_limited_per_account_across_addresses(self):
        self._assert_limited(_make_user(enabled=True), "/api/v1/auth/2fa/disable", rotate_ip=True)

    def test_login_code_is_limited_per_account_across_addresses(self):
        user = _make_user(enabled=True)
        body = {"username": user.username, "password": PASSWORD, "code": "000000"}
        for _ in range(RATE_LIMIT_MAX):
            self.assertEqual(self.client.post("/api/v1/auth/login", json=body).status_code, 401)
            _clear_attempts(only_ip=True)
        self.assertEqual(self.client.post("/api/v1/auth/login", json=body).status_code, 429)


class TestTotpReplay(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        # The middle of the current 30-second step, so the test cannot cross a
        # step boundary while it runs.
        self.now = (int(time.time()) // 30) * 30 + 15
        clock = mock.Mock(time=mock.Mock(return_value=self.now))
        patcher = mock.patch.object(core.totp, "time", clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _code(self, user, steps=0):
        return pyotp.TOTP(user.totp_secret).at(self.now + 30 * steps)

    def _login(self, user, code):
        return self.client.post("/api/v1/auth/login",
                                json={"username": user.username, "password": PASSWORD, "code": code})

    def _post(self, user, path, code):
        headers = {"Authorization": f"Bearer {create_token(user.to_dict())}"}
        return self.client.post(path, json={"code": code}, headers=headers)

    def test_valid_code_signs_in(self):
        user = _make_user(enabled=True)
        self.assertEqual(self._login(user, self._code(user)).status_code, 200)

    def test_code_cannot_sign_in_twice(self):
        user = _make_user(enabled=True)
        code = self._code(user)
        self.assertEqual(self._login(user, code).status_code, 200)
        self.assertEqual(self._login(user, code).status_code, 401)
        # The next step's code is still good.
        self.assertEqual(self._login(user, self._code(user, 1)).status_code, 200)

    def test_older_step_is_refused_after_a_newer_one(self):
        user = _make_user(enabled=True)
        self.assertEqual(self._login(user, self._code(user)).status_code, 200)
        self.assertEqual(self._login(user, self._code(user, -1)).status_code, 401)

    def test_sign_in_code_cannot_disable_2fa(self):
        user = _make_user(enabled=True)
        code = self._code(user)
        self.assertEqual(self._login(user, code).status_code, 200)
        self.assertEqual(self._post(user, "/api/v1/auth/2fa/disable", code).status_code, 400)
        res = self._post(user, "/api/v1/auth/2fa/disable", self._code(user, 1))
        self.assertEqual(res.status_code, 200, res.text)

    def test_enable_code_cannot_sign_in(self):
        user = _make_user(enabled=False)
        code = self._code(user)
        self.assertEqual(self._post(user, "/api/v1/auth/2fa/enable", code).status_code, 200)
        self.assertEqual(self._login(user, code).status_code, 401)
        self.assertEqual(self._login(user, self._code(user, 1)).status_code, 200)


if __name__ == "__main__":
    unittest.main()
