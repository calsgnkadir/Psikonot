"""
tests/test_secrets_in_urls.py — no secret travels in a URL
==========================================================
A URL is written to the uvicorn and proxy access logs, the browser history and
Referer headers. So:

  * a confidential record's password reaches the attachment download in the
    request body, never in the query string;
  * a dual-control token is read from the X-Dual-Control-Token header only;
  * the security alerts every operator reads never show a token id.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db

CLIENT_ID = "CL-001"
PASSWORD = "Locked@2026File!"
ACCOUNTS = {
    "practitioner": ("psk.elif", "Practitioner@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}


class TestSecretsInUrls(unittest.TestCase):
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

    def setUp(self):
        os.environ["TESTING"] = "true"

    def _add_with_file(self, password=None):
        res = self.client.post("/api/v1/records", headers=self.headers["practitioner"], json={
            "patient_id": CLIENT_ID, "record_type": "client_profile", "title": "Attachment test",
            "doctor_name": "Uzm. Psk. Elif Yilmaz", "institution": "Practice",
            "record_date": "2026-09-01",
            "is_confidential": bool(password), "confidential_password": password,
            "data": {"presenting_problem": "Sleep"}, "notes": "",
            "file_name": "note.txt", "file_type": "text/plain", "file_data": "aGVsbG8=",
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def _download_url(self, idx):
        return f"/api/v1/records/offchain/download/{CLIENT_ID}/{idx}"

    def _approved_token(self, request_type="DECRYPT_RAW_RECORD", target=CLIENT_ID):
        res = self.client.post("/api/v1/security/dual-control/request", headers=self.headers["admin"], json={
            "request_type": request_type, "target_patient_id": target,
            "reason": "Court order 2026/114", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token_id = res.json()["token_id"]
        res = self.client.post("/api/v1/security/dual-control/co-sign", headers=self.headers["officer"],
                               json={"token_id": token_id})
        self.assertEqual(res.status_code, 200, res.text)
        return token_id

    # ── the record password ──
    def test_password_in_the_query_string_does_not_download(self):
        idx = self._add_with_file(password=PASSWORD)
        res = self.client.get(f"{self._download_url(idx)}?password={PASSWORD}",
                              headers=self.headers["practitioner"])
        self.assertNotEqual(res.status_code, 200)
        self.assertNotEqual(res.content, b"hello")

    def test_password_in_the_body_downloads_for_the_owner(self):
        idx = self._add_with_file(password=PASSWORD)
        res = self.client.post(self._download_url(idx), headers=self.headers["practitioner"],
                               json={"password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, b"hello")
        self.assertIn('filename="note.txt"', res.headers["content-disposition"])

    def test_wrong_password_in_the_body_does_not_download(self):
        idx = self._add_with_file(password=PASSWORD)
        res = self.client.post(self._download_url(idx), headers=self.headers["practitioner"],
                               json={"password": "Wrong@2026Pass!"})
        self.assertNotEqual(res.status_code, 200)
        self.assertNotEqual(res.content, b"hello")

    def test_unprotected_file_downloads_without_a_body(self):
        idx = self._add_with_file()
        res = self.client.post(self._download_url(idx), headers=self.headers["practitioner"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, b"hello")

    # ── the dual-control token ──
    def test_dual_control_token_in_the_query_string_is_ignored(self):
        token_id = self._approved_token()
        res = self.client.get(f"/api/v1/records/{CLIENT_ID}?dual_control_token={token_id}",
                              headers=self.headers["admin"])
        self.assertEqual(res.status_code, 403, res.text)
        res = self.client.get(f"/api/v1/records/{CLIENT_ID}",
                              headers={**self.headers["admin"], "X-Dual-Control-Token": token_id})
        self.assertEqual(res.status_code, 200, res.text)

    def test_passkey_revoke_ignores_a_token_in_the_query_string(self):
        token_id = self._approved_token(request_type="REVOKE_PASSKEY", target="psk.elif")
        body = {"username": "psk.elif", "credential_id": "no-such-credential"}
        res = self.client.post(f"/api/v1/auth/webauthn/revoke?dual_control_token={token_id}",
                               headers=self.headers["admin"], json=body)
        self.assertEqual(res.status_code, 403, res.text)
        # With the header the co-signature is accepted; the credential is unknown.
        res = self.client.post("/api/v1/auth/webauthn/revoke",
                               headers={**self.headers["admin"], "X-Dual-Control-Token": token_id}, json=body)
        self.assertEqual(res.status_code, 404, res.text)

    # ── the alerts ──
    def test_alerts_show_no_token_id(self):
        token_id = self._approved_token()
        res = self.client.get("/api/v1/security/alerts?limit=200", headers=self.headers["officer"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn(token_id, res.text)
        for alert in res.json()["alerts"]:
            self.assertNotIn("token_id", alert["metadata"])
        # The operator still sees what was asked, for whom, and who approved it.
        approved = next(a for a in res.json()["alerts"] if a["alert_type"] == "DUAL_CONTROL_APPROVED")
        self.assertEqual(approved["metadata"]["target_patient_id"], CLIENT_ID)
        self.assertEqual(approved["metadata"]["co_signed_by"], "sec.officer")


if __name__ == "__main__":
    unittest.main()
