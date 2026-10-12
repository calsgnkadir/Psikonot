"""
tests/test_error_handling.py — failures are reported, internals are not
=======================================================================
  * logout that cannot revoke the token says so (503), instead of "Logged out"
    while the token keeps working until it expires;
  * a lost erasure tombstone is logged and reported, not silently dropped;
  * exception text (database host and user, file paths, crypto errors) is
    logged server side and never sent back in a response.
"""

import os
import sys
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db
from core.services.record_service import RecordService
from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
from infrastructure.cryptography.crypto_strategies import AESGCMStrategy

CLIENT_ID = "CL-001"
LEAK = "host=db.internal port=5432 user=vault password=hunter2"
ACCOUNTS = {
    "practitioner": ("psk.elif", "Practitioner@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}


class TestErrorHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)

    def _headers(self, actor, client=None):
        username, password = ACCOUNTS[actor]
        res = (client or self.client).post("/api/v1/auth/login",
                                           json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    # ── logout ──
    def test_logout_that_cannot_revoke_the_token_fails(self):
        headers = self._headers("practitioner")
        with mock.patch("database.storage.blacklist_token", side_effect=RuntimeError(LEAK)), \
                self.assertLogs("vhv.auth", level="ERROR"):
            res = self.client.post("/api/v1/auth/logout", headers=headers)
        self.assertEqual(res.status_code, 503, res.text)
        self.assertNotIn("hunter2", res.text)
        # The token was not revoked, and the server does not pretend otherwise.
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 200)

    def test_logout_revokes_the_token(self):
        headers = self._headers("practitioner")
        res = self.client.post("/api/v1/auth/logout", headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        fresh = TestClient(app)  # no session cookie, only the old bearer token
        self.assertEqual(fresh.get("/api/v1/auth/me", headers=headers).status_code, 401)

    def test_logout_with_an_invalid_token_still_succeeds(self):
        res = self.client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer not.a.jwt"})
        self.assertEqual(res.status_code, 200, res.text)

    # ── health ──
    def test_health_does_not_echo_the_database_error(self):
        with mock.patch.object(default_sql_db, "get_connection", side_effect=RuntimeError(LEAK)), \
                self.assertLogs("VIPHealthVault", level="ERROR"):
            res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "unhealthy")
        self.assertEqual(body["database"], "error")
        self.assertNotIn("hunter2", res.text)
        self.assertNotIn("db.internal", res.text)

    # ── records ──
    def _add_with_file(self, headers):
        res = self.client.post("/api/v1/records", headers=headers, json={
            "patient_id": CLIENT_ID, "record_type": "client_profile", "title": "Error test",
            "doctor_name": "Uzm. Psk. Elif Yilmaz", "institution": "Practice",
            "record_date": "2026-09-01", "is_confidential": False,
            "data": {"presenting_problem": "Sleep"}, "notes": "",
            "file_name": "note.txt", "file_type": "text/plain", "file_data": "aGVsbG8=",
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def test_download_does_not_echo_a_missing_attachment_error(self):
        headers = self._headers("practitioner")
        idx = self._add_with_file(headers)
        with mock.patch("core.services.attachment_store.AttachmentStore.get",
                        side_effect=FileNotFoundError(LEAK)), \
                self.assertLogs("vhv.records", level="ERROR"):
            res = self.client.post(f"/api/v1/records/offchain/download/{CLIENT_ID}/{idx}", headers=headers)
        self.assertEqual(res.status_code, 404, res.text)
        self.assertNotIn("hunter2", res.text)

    def test_download_does_not_echo_an_unexpected_error(self):
        headers = self._headers("practitioner")
        idx = self._add_with_file(headers)
        with mock.patch("backend.routers.records.decrypt_data", side_effect=ValueError(LEAK)), \
                self.assertLogs("vhv.records", level="ERROR"):
            res = self.client.post(f"/api/v1/records/offchain/download/{CLIENT_ID}/{idx}", headers=headers)
        self.assertEqual(res.status_code, 500, res.text)
        self.assertNotIn("hunter2", res.text)

    def test_record_decryption_error_is_not_returned(self):
        svc = RecordService(LMDBBlockRepository(), AESGCMStrategy())
        pwd = "Locked@Record2026!"
        patient = f"CL-7{uuid.uuid4().int % 1000:03d}"
        block = svc.add_record(patient, {"title": "Transcript"}, is_protected=True,
                               protection_password=pwd, username="psk.elif")
        real = svc.crypto_strategy.decrypt_data

        def failing_with_password(data, key, salt):
            if key == pwd:
                raise ValueError(LEAK)
            return real(data, key, salt)

        with mock.patch.object(svc.crypto_strategy, "decrypt_data", side_effect=failing_with_password), \
                self.assertLogs("vhv.recordservice", level="ERROR"):
            out = svc.get_block_data(patient, block.index, password=pwd)
        self.assertIsInstance(out, str)
        self.assertIn("ERROR", out)  # callers still recognise the failure
        self.assertNotIn("hunter2", out)

    # ── erasure ──
    def test_lost_erasure_tombstone_is_logged_and_reported(self):
        patient = f"CL-6{uuid.uuid4().int % 1000:03d}"
        admin = self._headers("admin")
        res = self.client.post("/api/v1/security/dual-control/request", headers=admin, json={
            "request_type": "ERASE_PATIENT", "target_patient_id": patient,
            "reason": "Data subject erasure request", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token_id = res.json()["token_id"]
        res = self.client.post("/api/v1/security/dual-control/co-sign", headers=self._headers("officer"),
                               json={"token_id": token_id})
        self.assertEqual(res.status_code, 200, res.text)

        with mock.patch("database.audit_storage.append_access_log", side_effect=RuntimeError(LEAK)), \
                self.assertLogs("vhv.erasure", level="ERROR"):
            res = self.client.post(f"/api/v1/erasure/{patient}",
                                   headers={**admin, "X-Dual-Control-Token": token_id})
        # The erasure itself is irreversible and done; only its audit entry failed.
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["erased"])
        self.assertFalse(res.json()["audit_logged"])
        self.assertNotIn("hunter2", res.text)


if __name__ == "__main__":
    unittest.main()
