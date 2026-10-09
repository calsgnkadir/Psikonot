"""
tests/test_disabled_account_session.py — disabling an account ends its sessions
================================================================================
The account status used to be checked only at sign-in. A token issued before
the account was disabled kept working until it expired (8 hours): the client
list, the records, even 2FA setup. current_user now checks it on every request.
"""

import os
import sys
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.security import hash_password
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Disabled@Session2026!"


class TestDisabledAccountSession(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        # Its own account, so no demo account other tests sign in with is touched.
        self.username = f"off.{uuid.uuid4().hex[:8]}"
        self.repo.save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=self.username,
            password_hash=hash_password(PASSWORD), role="practitioner",
            full_name="Offboarded", account_status="ACTIVE_ENROLLED",
        ))
        res = self.client.post("/api/v1/auth/login",
                               json={"username": self.username, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        # Bearer header: the session cookie is Secure and the test client speaks http.
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _set_status(self, status: str) -> None:
        user = self.repo.load_user(self.username)
        user.account_status = status
        self.repo.save_user(user)

    def _get(self, path: str):
        return self.client.get(path, headers=self.headers)

    def test_disabled_account_token_is_rejected(self):
        self.assertEqual(self._get("/api/v1/auth/me").status_code, 200)
        self._set_status("DISABLED")
        self.assertEqual(self._get("/api/v1/auth/me").status_code, 401)
        self.assertEqual(self._get("/api/v1/clients").status_code, 401)

    def test_pending_account_token_is_rejected(self):
        self._set_status("PENDING_ONBOARDING")
        self.assertEqual(self._get("/api/v1/auth/me").status_code, 401)

    def test_sign_in_to_a_disabled_account_still_answers_403(self):
        """Unchanged at sign-in: the password was right, the account is closed."""
        self._set_status("DISABLED")
        res = self.client.post("/api/v1/auth/login",
                               json={"username": self.username, "password": PASSWORD})
        self.assertEqual(res.status_code, 403, res.text)


if __name__ == "__main__":
    unittest.main()
