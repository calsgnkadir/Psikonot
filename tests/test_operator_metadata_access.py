"""
tests/test_operator_metadata_access.py — operators need a co-signature for metadata too
=======================================================================================
That a person is a client here, and how large their file is, is an asset of its
own (THREAT_MODEL.md, section 1). The record endpoints held operators to dual
control, but the chain status, the audit trail, the access ledger and the Merkle
proof let them through on the file-level rule alone, which operators always
pass. An admin could walk CL-001…CL-999 and read who is a client and how big
their file is, and the access ledger carried the practitioner's free-text
correction reason.

A read of a client that does not exist also created the client's chain on disk,
and the next new client then skipped that number.
"""

import os
import random
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.pseudonymization.service import project_name_for
from database.sql_db import default_sql_db
import database.storage as storage
from tests.test_appointments import _sql

CLIENT_ID = "CL-001"
PRACTITIONER = "psk.elif"
STRONG = "Enrolled@2026Secure!"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}
SESSION_DATA = {"session_number": 2, "duration_min": 50, "session_format": "In-person",
                "summary": "Metadata access test."}
REASON = "client disclosed suicidal ideation MARKER-7Q2"


class TestOperatorMetadataAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        default_sql_db.seed_default_users()
        cls.client = TestClient(app)
        cls.headers = {}
        for actor, (username, password) in ACCOUNTS.items():
            res = cls.client.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert res.status_code == 200, res.text
            cls.headers[actor] = {"Authorization": f"Bearer {res.json()['access_token']}"}

        # An auditor account, provisioned and enrolled like a real one.
        cls.auditor = f"aud.meta{random.randint(1000, 9999)}"
        res = cls.client.post("/api/v1/onboarding/provision", headers=cls.headers["admin"], json={
            "username": cls.auditor, "full_name": "Metadata Auditor", "role": "auditor"})
        assert res.status_code == 200, res.text
        cls.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["enrollment_token"], "new_password": STRONG})
        res = cls.client.post("/api/v1/auth/login", json={"username": cls.auditor, "password": STRONG})
        assert res.status_code == 200, res.text
        cls.headers["auditor"] = {"Authorization": f"Bearer {res.json()['access_token']}"}

    @classmethod
    def tearDownClass(cls):
        _sql("DELETE FROM users WHERE username = ?", (cls.auditor,))
        _sql("DELETE FROM enrollment_tokens WHERE username = ?", (cls.auditor,))

    def setUp(self):
        os.environ["TESTING"] = "true"

    def _added(self):
        res = self.client.post("/api/v1/records", headers=self.headers["practitioner"], json={
            "patient_id": CLIENT_ID, "record_type": "session_note", "title": "Metadata test",
            "doctor_name": "Uzm. Psk. Elif Yilmaz", "institution": "Practice",
            "record_date": "2026-09-01", "data": SESSION_DATA, "notes": "",
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def _paths(self, patient_id, idx):
        return (f"/api/v1/blockchain/{patient_id}/status",
                f"/api/v1/blockchain/{patient_id}/audit",
                f"/api/v1/blockchain/{patient_id}/access-logs",
                f"/api/v1/records/proof/{patient_id}/{idx}")

    def _approved_token(self, actor, patient_id=CLIENT_ID, request_type="DECRYPT_RAW_RECORD"):
        res = self.client.post("/api/v1/security/dual-control/request", headers=self.headers[actor], json={
            "request_type": request_type, "target_patient_id": patient_id,
            "reason": "Annual access review", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token_id = res.json()["token_id"]
        res = self.client.post("/api/v1/security/dual-control/co-sign", headers=self.headers["officer"],
                               json={"token_id": token_id})
        self.assertEqual(res.status_code, 200, res.text)
        return token_id

    def _get(self, path, actor, token_id=None):
        headers = dict(self.headers[actor])
        if token_id:
            headers["X-Dual-Control-Token"] = token_id
        return self.client.get(path, headers=headers)

    # ── the lock ───────────────────────────────────────────────
    def test_admin_without_a_token_is_refused_everywhere(self):
        idx = self._added()
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "admin").status_code, 403)

    def test_auditor_without_a_token_is_refused(self):
        idx = self._added()
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "auditor").status_code, 403)

    def test_security_officer_without_a_token_is_refused(self):
        idx = self._added()
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "officer").status_code, 403)

    def test_an_erasure_token_does_not_open_metadata(self):
        """Reading needs a read approval, as for the records themselves."""
        idx = self._added()
        token_id = self._approved_token("admin", request_type="ERASE_PATIENT")
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "admin", token_id).status_code, 403)

    def test_a_token_for_another_client_does_not_open_this_one(self):
        idx = self._added()
        token_id = self._approved_token("admin", patient_id="CL-002")
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "admin", token_id).status_code, 403)

    # ── the key ────────────────────────────────────────────────
    def test_admin_with_a_co_signed_token_gets_in(self):
        idx = self._added()
        token_id = self._approved_token("admin")
        for path in self._paths(CLIENT_ID, idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "admin", token_id).status_code, 200)

    def test_auditor_with_a_co_signed_token_reads_the_trail(self):
        self._added()
        token_id = self._approved_token("auditor")
        for path in (f"/api/v1/blockchain/{CLIENT_ID}/audit",
                     f"/api/v1/blockchain/{CLIENT_ID}/access-logs"):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "auditor", token_id).status_code, 200)

    # ── the owner ──────────────────────────────────────────────
    def test_the_practitioner_still_reads_their_clients_metadata(self):
        idx = self._added()
        for path in (f"/api/v1/blockchain/{CLIENT_ID}/status",
                     f"/api/v1/blockchain/{CLIENT_ID}/access-logs",
                     f"/api/v1/records/proof/{CLIENT_ID}/{idx}"):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, "practitioner").status_code, 200)

    # ── no clinical text in the access ledger ──────────────────
    def test_correction_reason_is_not_in_the_access_ledger(self):
        idx = self._added()
        res = self.client.post(f"/api/v1/records/{CLIENT_ID}/{idx}/correct",
                               headers=self.headers["practitioner"],
                               json={"reason": REASON, "corrected_data": {"title": "Corrected"}})
        self.assertEqual(res.status_code, 200, res.text)

        res = self._get(f"/api/v1/blockchain/{CLIENT_ID}/access-logs?limit=1000", "practitioner")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("MARKER-7Q2", res.text)
        corrected = [e for e in res.json()["logs"] if e.get("action") == "RECORD_CORRECTED"
                     and e.get("block_index") == idx]
        self.assertTrue(corrected)
        self.assertIn("correction_index", corrected[0])

        stored = storage.load_access_logs(project_name_for(CLIENT_ID), limit=100000)
        self.assertFalse([e for e in stored if "MARKER-7Q2" in str(e)])

    # ── reading creates nothing ────────────────────────────────
    def test_reading_a_missing_client_creates_no_chain(self):
        missing = f"CL-{random.randint(70000, 79999)}"
        project = project_name_for(missing)
        self.assertFalse(storage.project_exists(project))
        for actor in ("admin", "practitioner"):
            for path in self._paths(missing, 1):
                with self.subTest(actor=actor, path=path):
                    self._get(path, actor)
        token_id = self._approved_token("admin", patient_id=missing)
        for path in self._paths(missing, 1):
            with self.subTest(path=path, token=True):
                self.assertIn(self._get(path, "admin", token_id).status_code, (200, 404))
        self.assertFalse(storage.project_exists(project))

    def test_missing_client_looks_like_someone_elses(self):
        missing = f"CL-{random.randint(80000, 89999)}"
        for actor in ("admin", "practitioner"):
            for path in self._paths(missing, 1):
                with self.subTest(actor=actor, path=path):
                    self.assertEqual(self._get(path, actor).status_code, 403)


if __name__ == "__main__":
    unittest.main()
