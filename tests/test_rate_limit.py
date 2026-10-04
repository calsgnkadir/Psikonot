"""
tests/test_rate_limit.py — sign-in attempts are limited per IP
==============================================================
The limiter used to match "/api/auth/login", a path that does not exist (the
API lives under /api/v1), so it never applied. These tests switch the limit on
(it is off while TESTING is set) and check each sign-in entry point.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from backend.middleware.rate_limiter import RATE_LIMIT_MAX
from database.sql_db import default_sql_db


def _clear_attempts():
    conn = default_sql_db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM rate_limits")
        conn.commit()
    finally:
        cur.close()
        conn.close()


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        self._testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "false"
        _clear_attempts()
        self.client = TestClient(app)

    def tearDown(self):
        _clear_attempts()
        if self._testing is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = self._testing

    def _attempt(self, path, body):
        return self.client.post(path, json=body)

    def test_password_login_is_limited(self):
        body = {"username": "secretary.ayse", "password": "wrong-password"}
        for _ in range(RATE_LIMIT_MAX):
            self.assertNotEqual(self._attempt("/api/v1/auth/login", body).status_code, 429)
        self.assertEqual(self._attempt("/api/v1/auth/login", body).status_code, 429)

    def test_limit_also_blocks_the_right_password(self):
        # Otherwise a guesser simply keeps going until the limit lifts per guess.
        wrong = {"username": "secretary.ayse", "password": "wrong-password"}
        for _ in range(RATE_LIMIT_MAX):
            self._attempt("/api/v1/auth/login", wrong)
        right = {"username": "secretary.ayse", "password": "Secretary@2026!"}
        self.assertEqual(self._attempt("/api/v1/auth/login", right).status_code, 429)

    def test_invitation_codes_share_the_same_limit(self):
        # CSRF is enforced when TESTING is off, so send the double-submit token.
        self.client.get("/api/v1/config")
        csrf = self.client.cookies.get("csrf_token")
        body = {"enrollment_token": "not-a-real-code", "new_password": "Enrolled@2026Secure!"}
        for _ in range(RATE_LIMIT_MAX):
            res = self.client.post("/api/v1/onboarding/redeem", json=body,
                                   headers={"X-CSRF-Token": csrf})
            self.assertNotEqual(res.status_code, 429)
        res = self.client.post("/api/v1/onboarding/redeem", json=body,
                               headers={"X-CSRF-Token": csrf})
        self.assertEqual(res.status_code, 429)


if __name__ == "__main__":
    unittest.main()
