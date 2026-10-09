"""
tests/test_totp_setup.py — 2FA setup cannot replace an active secret
=====================================================================
/2fa/setup used to overwrite the TOTP secret of an account that already had
2FA on, without asking for a code. A stolen session could bind 2FA to the
attacker's phone and lock the owner out. Changing the device now goes through
/2fa/disable, which needs a current code.
"""

import os
import sys
import unittest
import uuid

import pyotp
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.security import hash_password
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Totp@Setup2026!"


class TestTotpSetup(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        # Its own account: enabling 2FA on a demo account would break every
        # other test that signs in with it.
        self.username = f"totp.{uuid.uuid4().hex[:8]}"
        SQLUserRepository().save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=self.username,
            password_hash=hash_password(PASSWORD), role="practitioner",
            full_name="TOTP Test", account_status="ACTIVE_ENROLLED",
        ))

    def _login(self, code=None):
        return self.client.post("/api/v1/auth/login",
                                json={"username": self.username, "password": PASSWORD, "code": code})

    def _post(self, path, json=None):
        # Bearer header, as the other tests: the session cookie is Secure and
        # the test client speaks plain http.
        return self.client.post(path, json=json, headers=self.headers)

    def _enable_2fa(self) -> str:
        res = self._login()
        self.assertEqual(res.status_code, 200, res.text)
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
        secret = self._post("/api/v1/auth/2fa/setup").json()["secret"]
        res = self._post("/api/v1/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
        self.assertEqual(res.status_code, 200, res.text)
        return secret

    def test_setup_is_refused_while_2fa_is_enabled(self):
        secret = self._enable_2fa()

        res = self._post("/api/v1/auth/2fa/setup")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertNotIn("secret", res.json())

        # The owner's authenticator still works: the secret was not replaced.
        self.assertEqual(self._login(code=pyotp.TOTP(secret).now()).status_code, 200)

    def test_setup_works_again_after_disabling(self):
        secret = self._enable_2fa()
        res = self._post("/api/v1/auth/2fa/disable", json={"code": pyotp.TOTP(secret).now()})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._post("/api/v1/auth/2fa/setup").status_code, 200)


if __name__ == "__main__":
    unittest.main()
