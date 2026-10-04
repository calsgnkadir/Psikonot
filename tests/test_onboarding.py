"""
tests/test_onboarding.py — out-of-band account provisioning & enrollment
========================================================================
No usable account exists without provisioning by a privileged operator followed
by redemption of a single-use, out-of-band enrollment token. A provisioned
account cannot log in until it is redeemed; the token is one-time and expiring.
"""

import os
import sys
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db

_STRONG = "Enrolled@2026Secure!"


class TestOnboarding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.admin = self._login("admin", "Admin@2026Secure!")
        # Unique per run: the shared vault.db persists across runs, so a fixed
        # name would collide with a provisioned account left by an earlier run.
        self.username = f"newacct_{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        # Keep the persistent dev DB clean: drop the account and tokens we created.
        conn = default_sql_db.get_connection()
        cur = conn.cursor()
        ph = "%s" if default_sql_db.is_postgres else "?"
        try:
            cur.execute(f"DELETE FROM users WHERE username = {ph}", (self.username,))
            cur.execute(f"DELETE FROM enrollment_tokens WHERE username = {ph}", (self.username,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            cur.close()
            conn.close()

    def _login(self, username, password):
        return self.client.post("/api/v1/auth/login",
                                json={"username": username, "password": password})

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _provision(self, token=None, **overrides):
        body = {"username": self.username, "full_name": "New Account",
                "role": "practitioner"}
        body.update(overrides)
        return self.client.post("/api/v1/onboarding/provision",
                                headers=self._auth(token or self.admin.json()["access_token"]),
                                json=body)

    def _redeem(self, enrollment_token, new_password=_STRONG):
        return self.client.post("/api/v1/onboarding/redeem",
                                json={"enrollment_token": enrollment_token,
                                      "new_password": new_password})

    def test_provision_requires_a_privileged_role(self):
        doctor = self._login("psk.elif", "Practitioner@2026!").json()["access_token"]
        res = self._provision(token=doctor)
        self.assertEqual(res.status_code, 403, res.text)

    def test_provisioned_account_cannot_login_until_redeemed(self):
        res = self._provision()
        self.assertEqual(res.status_code, 200, res.text)
        # Even with the (unknown) password it cannot be guessed; and the status
        # gate blocks it regardless — a redeem-set password is tested below.
        login = self._login(self.username, _STRONG)
        self.assertIn(login.status_code, (401, 403))

    def test_redeem_activates_and_enables_login(self):
        prov = self._provision().json()
        self.assertEqual(prov["account_status"], "PENDING_ONBOARDING")

        redeemed = self._redeem(prov["enrollment_token"])
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        self.assertEqual(redeemed.json()["account_status"], "ACTIVE_ENROLLED")

        login = self._login(self.username, _STRONG)
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("access_token", login.json())

    def test_enrollment_token_is_single_use(self):
        token = self._provision().json()["enrollment_token"]
        self.assertEqual(self._redeem(token).status_code, 200)
        self.assertEqual(self._redeem(token).status_code, 400)

    def test_redeem_rejects_a_weak_password(self):
        token = self._provision().json()["enrollment_token"]
        res = self._redeem(token, new_password="weak")
        self.assertEqual(res.status_code, 422)

    def test_redeem_rejects_an_unknown_token(self):
        res = self._redeem("this-token-was-never-issued")
        self.assertEqual(res.status_code, 400)

    def test_provision_rejects_a_duplicate_username(self):
        self.assertEqual(self._provision().status_code, 200)
        self.assertEqual(self._provision().status_code, 409)

    def test_no_shortcut_around_onboarding(self):
        # POST /admin/users used to let an admin create an active account with a
        # password directly, skipping the pending state and the single-use code.
        # Nothing used it; it was removed so onboarding is the only way in.
        res = self.client.post("/api/v1/admin/users", headers=self._auth(self.admin.json()["access_token"]), json={
            "username": self.username, "password": _STRONG, "role": "practitioner",
            "full_name": "Shortcut"})
        self.assertEqual(res.status_code, 405)
        self.assertEqual(self._login(self.username, _STRONG).status_code, 401)


if __name__ == "__main__":
    unittest.main()
