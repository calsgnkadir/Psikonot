"""
tests/test_dual_control_access.py — M-of-N co-signature over raw record access
==============================================================================
Operator roles administer the vault but have no clinical relationship with the
patient, so none of them may read raw records on their own authority. Access is
unlocked only by a token that a *different* privileged principal co-signed, and
only for the patient that token names.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db

PATIENT_ID = "CL-001"

ACCOUNTS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
    "practitioner": ("psk.elif", "Practitioner@2026!"),
}


class TestDualControlAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)

    def _headers(self, actor: str):
        username, password = ACCOUNTS[actor]
        res = self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _request_token(self, actor: str = "admin", patient_id: str = PATIENT_ID):
        res = self.client.post(
            "/api/v1/security/dual-control/request",
            headers=self._headers(actor),
            json={
                "request_type": "DECRYPT_RAW_RECORD",
                "target_patient_id": patient_id,
                "reason": "Court order 2026/114",
                "validity_minutes": 30,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["token_id"]

    def _co_sign(self, token_id: str, actor: str = "officer"):
        return self.client.post(
            "/api/v1/security/dual-control/co-sign",
            headers=self._headers(actor),
            json={"token_id": token_id},
        )

    def _read_records(self, actor: str, token_id=None, patient_id: str = PATIENT_ID):
        headers = self._headers(actor)
        if token_id:
            headers["X-Dual-Control-Token"] = token_id
        return self.client.get(f"/api/v1/records/{patient_id}", headers=headers)

    # ── the lock ───────────────────────────────────────────────
    def test_admin_cannot_read_records_without_a_token(self):
        self.assertEqual(self._read_records("admin").status_code, 403)

    def test_security_officer_cannot_read_records_without_a_token(self):
        """A privileged operator role must not be a way around Dual-Control."""
        self.assertEqual(self._read_records("officer").status_code, 403)

    def test_pending_token_does_not_unlock_access(self):
        token_id = self._request_token()
        self.assertEqual(self._read_records("admin", token_id).status_code, 403)

    # ── the key ────────────────────────────────────────────────
    def test_co_signed_token_unlocks_access(self):
        token_id = self._request_token()
        self.assertEqual(self._co_sign(token_id).status_code, 200)
        self.assertEqual(self._read_records("admin", token_id).status_code, 200)

    def test_requester_cannot_self_approve(self):
        token_id = self._request_token()
        res = self._co_sign(token_id, actor="admin")
        self.assertEqual(res.status_code, 400)
        self.assertIn("cannot self-approve", res.json()["detail"])
        self.assertEqual(self._read_records("admin", token_id).status_code, 403)

    def test_token_is_bound_to_the_named_patient(self):
        token_id = self._request_token(patient_id=PATIENT_ID)
        self.assertEqual(self._co_sign(token_id).status_code, 200)
        self.assertEqual(
            self._read_records("admin", token_id, patient_id="CL-OTHER").status_code, 403
        )

    def test_co_signer_cannot_use_the_token(self):
        """The token belongs to whoever requested it. Before, anyone holding the
        id could use it — the co-signer included, so a single approval opened
        the file for both people."""
        token_id = self._request_token(actor="admin")
        self.assertEqual(self._co_sign(token_id, actor="officer").status_code, 200)
        self.assertEqual(self._read_records("officer", token_id).status_code, 403)
        self.assertEqual(self._read_records("admin", token_id).status_code, 200)

    def test_read_token_does_not_unlock_erasure(self):
        """Erasure is irreversible; it needs its own approval, not a read one."""
        token_id = self._request_token()
        self.assertEqual(self._co_sign(token_id).status_code, 200)
        headers = self._headers("admin")
        headers["X-Dual-Control-Token"] = token_id
        res = self.client.post(f"/api/v1/erasure/{PATIENT_ID}", headers=headers)
        self.assertEqual(res.status_code, 403)

    def test_validity_is_capped(self):
        res = self.client.post(
            "/api/v1/security/dual-control/request",
            headers=self._headers("admin"),
            json={"request_type": "DECRYPT_RAW_RECORD", "target_patient_id": PATIENT_ID,
                  "reason": "Court order 2026/114", "validity_minutes": 10_000_000},
        )
        self.assertEqual(res.status_code, 400)

    def test_unknown_request_type_is_rejected(self):
        res = self.client.post(
            "/api/v1/security/dual-control/request",
            headers=self._headers("admin"),
            json={"request_type": "ANYTHING", "target_patient_id": PATIENT_ID,
                  "reason": "Court order 2026/114", "validity_minutes": 30},
        )
        self.assertEqual(res.status_code, 400)

    def test_practitioner_cannot_request_a_token(self):
        res = self.client.post(
            "/api/v1/security/dual-control/request",
            headers=self._headers("practitioner"),
            json={"request_type": "DECRYPT_RAW_RECORD", "target_patient_id": PATIENT_ID,
                  "reason": "Court order 2026/114", "validity_minutes": 30},
        )
        self.assertEqual(res.status_code, 403)

    def test_unknown_token_is_rejected(self):
        self.assertEqual(self._read_records("admin", "dc_does_not_exist").status_code, 403)

    # ── status lookup ──────────────────────────────────────────
    def test_status_endpoint_reports_the_lifecycle(self):
        token_id = self._request_token()
        pending = self.client.get(
            f"/api/v1/security/dual-control/{token_id}", headers=self._headers("admin")
        ).json()
        self.assertEqual(pending["status"], "PENDING_CO_APPROVAL")

        self._co_sign(token_id)
        approved = self.client.get(
            f"/api/v1/security/dual-control/{token_id}", headers=self._headers("admin")
        ).json()
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["co_signed_by"], "sec.officer")

    def test_status_of_unknown_token_is_404(self):
        res = self.client.get(
            "/api/v1/security/dual-control/dc_nope", headers=self._headers("admin")
        )
        self.assertEqual(res.status_code, 404)

    def test_practitioner_access_is_unaffected_by_dual_control(self):
        """The rule targets operators; the record owner still reads their own chart."""
        self.assertEqual(self._read_records("practitioner").status_code, 200)


if __name__ == "__main__":
    unittest.main()
