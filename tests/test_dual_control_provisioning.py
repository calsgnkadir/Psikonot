"""
tests/test_dual_control_provisioning.py — no second person made up by the first
===============================================================================
Dual-control needs two independent people. An operator who provisions an
account receives its enrollment code, so they can sign in as it: that account
is not a second person. One admin could provision a security officer, redeem
its code, and co-sign their own request to read a client's notes or erase a
client.

  * the onboarding API no longer creates the roles that can co-sign;
  * an account never approves a request from the operator who provisioned it,
    nor the other way round — this also covers accounts provisioned before the
    upgrade.
"""

import os
import sys
import time
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.security import hash_password
from database.sql_db import default_sql_db

PATIENT_ID = "CL-001"
STRONG = "Enrolled@2026Secure!"

ACCOUNTS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}


def run_sql(sql, params=()):
    db = default_sql_db
    if db.is_postgres:
        sql = sql.replace("?", "%s")
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
    finally:
        cur.close()
        conn.close()


class TestDualControlProvisioning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.users = []

    def tearDown(self):
        for username in self.users:
            run_sql("DELETE FROM users WHERE username = ?", (username,))
            run_sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))

    def _login(self, username, password):
        res = self.client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _headers(self, actor):
        return self._login(*ACCOUNTS[actor])

    def _provision(self, role, by="admin"):
        username = f"{role[:6]}_{uuid.uuid4().hex[:10]}"
        self.users.append(username)
        res = self.client.post("/api/v1/onboarding/provision", headers=self._headers(by), json={
            "username": username, "full_name": "Provisioned " + role, "role": role})
        return username, res

    def _provision_and_redeem(self, role, by="admin"):
        username, res = self._provision(role, by)
        self.assertEqual(res.status_code, 200, res.text)
        redeemed = self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["enrollment_token"], "new_password": STRONG})
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        return username, self._login(username, STRONG)

    def _legacy_provisioned(self, role, by):
        """An account an operator provisioned through the API before this fix:
        a user row and the enrollment token that operator created for it."""
        username = f"legacy_{uuid.uuid4().hex[:10]}"
        self.users.append(username)
        run_sql(
            "INSERT INTO users (id, username, password_hash, role, full_name, account_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"USR-{uuid.uuid4().hex[:12].upper()}", username, hash_password(STRONG), role,
             "Legacy " + role, "ACTIVE_ENROLLED"),
        )
        run_sql(
            "INSERT INTO enrollment_tokens (token_hash, username, expires_at, used, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, username, time.time() + 3600, True, by, time.time()),
        )
        # The upgrade runs on the next start.
        default_sql_db.init_db()
        return username, self._login(username, STRONG)

    def _request(self, headers, request_type="DECRYPT_RAW_RECORD", target=PATIENT_ID):
        res = self.client.post("/api/v1/security/dual-control/request", headers=headers, json={
            "request_type": request_type, "target_patient_id": target,
            "reason": "Court order 2026/114", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["token_id"]

    def _co_sign(self, token_id, headers):
        return self.client.post("/api/v1/security/dual-control/co-sign",
                                headers=headers, json={"token_id": token_id})

    def _read_records(self, headers, token_id):
        return self.client.get(f"/api/v1/records/{PATIENT_ID}",
                               headers={**headers, "X-Dual-Control-Token": token_id})

    # ── the API does not create co-signers ─────────────────────
    def test_admin_cannot_provision_a_security_officer(self):
        """The reported bypass, step 1: before the fix this answered 200 with the
        new officer's enrollment code."""
        _, res = self._provision("security_officer")
        self.assertEqual(res.status_code, 422, res.text)
        self.assertNotIn("enrollment_token", res.text)

    def test_admin_cannot_provision_another_admin(self):
        _, res = self._provision("admin")
        self.assertEqual(res.status_code, 422, res.text)

    def test_security_officer_cannot_provision_an_admin(self):
        _, res = self._provision("admin", by="officer")
        self.assertEqual(res.status_code, 422, res.text)

    def test_practitioners_and_auditors_are_still_provisioned(self):
        for role in ("practitioner", "auditor"):
            username, headers = self._provision_and_redeem(role)
            me = self.client.get("/api/v1/auth/me", headers=headers)
            self.assertEqual(me.status_code, 200, me.text)

    # ── a provisioned account is not a second person ───────────
    def test_officer_provisioned_by_the_admin_cannot_unlock_records(self):
        """The reported bypass end to end, for an officer provisioned before the
        upgrade: the admin asks, their own officer co-signs, the admin reads."""
        _, officer = self._legacy_provisioned("security_officer", by="admin")
        admin = self._headers("admin")
        token_id = self._request(admin)
        res = self._co_sign(token_id, officer)
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIn("provisioned", res.json()["detail"])
        self.assertEqual(self._read_records(admin, token_id).status_code, 403)

    def test_officer_provisioned_by_the_admin_cannot_approve_any_request_type(self):
        _, officer = self._legacy_provisioned("security_officer", by="admin")
        admin = self._headers("admin")
        for request_type, target in (("ERASE_PATIENT", PATIENT_ID), ("REVOKE_PASSKEY", "psk.elif")):
            token_id = self._request(admin, request_type, target)
            self.assertEqual(self._co_sign(token_id, officer).status_code, 400, request_type)

    def test_admin_cannot_approve_for_an_account_they_provisioned(self):
        """The other direction: the admin provisions an auditor, asks as the
        auditor, and co-signs as themselves."""
        _, auditor = self._provision_and_redeem("auditor")
        token_id = self._request(auditor)
        res = self._co_sign(token_id, self._headers("admin"))
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self._read_records(auditor, token_id).status_code, 403)

    def test_an_independent_officer_still_approves(self):
        """The rule is about who provisioned whom, not about provisioned accounts."""
        _, auditor = self._provision_and_redeem("auditor")
        token_id = self._request(auditor)
        self.assertEqual(self._co_sign(token_id, self._headers("officer")).status_code, 200)
        self.assertEqual(self._read_records(auditor, token_id).status_code, 200)

    def test_seeded_accounts_still_approve_each_other(self):
        """Accounts with no recorded provisioner (seeded, or older installs) keep
        working."""
        admin = self._headers("admin")
        token_id = self._request(admin)
        self.assertEqual(self._co_sign(token_id, self._headers("officer")).status_code, 200)
        self.assertEqual(self._read_records(admin, token_id).status_code, 200)


if __name__ == "__main__":
    unittest.main()
