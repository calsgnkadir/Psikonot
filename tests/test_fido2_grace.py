"""
tests/test_fido2_grace.py — MANDATORY_FIDO2 must not deadlock a fresh account
=============================================================================
Enforcing "must have a passkey" by refusing every password login would lock out a
fresh account: a passkey can only be enrolled after logging in. So an account with
no passkey gets a one-time enrolment grace — it logs in and the response flags that
a passkey must be enrolled now, instead of a 403 that can never be cleared.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db


class TestFido2EnrollmentGrace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self._saved = os.environ.get("MANDATORY_FIDO2")
        # Ensure the account has no passkey so the grace path is exercised.
        conn = default_sql_db.get_connection()
        cur = conn.cursor()
        ph = "%s" if default_sql_db.is_postgres else "?"
        try:
            cur.execute(f"DELETE FROM webauthn_credentials WHERE username = {ph}", ("secretary.ayse",))
            conn.commit()
        finally:
            cur.close()
            conn.close()

    def tearDown(self):
        # Never leave a fake passkey behind for the other tests.
        conn = default_sql_db.get_connection()
        cur = conn.cursor()
        ph = "%s" if default_sql_db.is_postgres else "?"
        try:
            cur.execute(f"DELETE FROM webauthn_credentials WHERE credential_id LIKE {ph}", ("test-cred-%",))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        if self._saved is None:
            os.environ.pop("MANDATORY_FIDO2", None)
        else:
            os.environ["MANDATORY_FIDO2"] = self._saved

    def _login(self):
        return self.client.post("/api/v1/auth/login",
                                json={"username": "secretary.ayse", "password": "Secretary@2026!"})

    def test_fresh_account_can_log_in_and_is_told_to_enrol(self):
        os.environ["MANDATORY_FIDO2"] = "true"
        res = self._login()
        self.assertEqual(res.status_code, 200, res.text)   # not a 403 deadlock
        body = res.json()
        self.assertIn("access_token", body)
        self.assertTrue(body.get("passkey_enrollment_required"))

    def test_flag_is_false_when_policy_is_off(self):
        os.environ["MANDATORY_FIDO2"] = "false"
        res = self._login()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json().get("passkey_enrollment_required"))

    def test_practitioners_are_covered_too(self):
        # The policy used to apply to admins and clients only.
        os.environ["MANDATORY_FIDO2"] = "true"
        res = self.client.post("/api/v1/auth/login",
                               json={"username": "psk.elif", "password": "Practitioner@2026!"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json().get("passkey_enrollment_required"))

    def test_account_with_a_passkey_cannot_sign_in_with_its_password(self):
        # This used to succeed: the policy only returned a flag nothing read.
        self._add_fake_passkey("secretary.ayse")
        os.environ["MANDATORY_FIDO2"] = "true"
        res = self._login()
        self.assertEqual(res.status_code, 403, res.text)
        self.assertNotIn("access_token", res.json())
        # The same refusal for a wrong password: the answer says nothing about it.
        wrong = self.client.post("/api/v1/auth/login",
                                 json={"username": "secretary.ayse", "password": "Wrong@Password2026!"})
        self.assertEqual(wrong.status_code, 403)

    def test_passkey_does_not_block_password_login_when_policy_is_off(self):
        self._add_fake_passkey("secretary.ayse")
        os.environ["MANDATORY_FIDO2"] = "false"
        self.assertEqual(self._login().status_code, 200)

    def _add_fake_passkey(self, username):
        conn = default_sql_db.get_connection()
        cur = conn.cursor()
        ph = "%s" if default_sql_db.is_postgres else "?"
        try:
            cur.execute(f"INSERT INTO webauthn_credentials (credential_id, username, public_key, sign_count, created_at) "
                        f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                        (f"test-cred-{username}", username, "not-a-real-key", 0, 0.0))
            conn.commit()
        finally:
            cur.close()
            conn.close()


if __name__ == "__main__":
    unittest.main()
