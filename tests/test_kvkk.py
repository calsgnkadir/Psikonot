"""
tests/test_kvkk.py — a client's KVKK rights, exercised by their practitioner
============================================================================
Clients do not sign in, so the practitioner downloads a copy of the client's
data for them and files their erasure request. An operator can close the
request as done only once the client's key has really been destroyed.
"""

import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.services import client_registry
from database.sql_db import default_sql_db
from tests.test_appointments import _sql
from tests.test_practitioner_file_access import new_practitioner

CLIENT_ID = "CL-001"
PRACTITIONER = "psk.elif"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}


class TestKvkk(unittest.TestCase):
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
        cls.other_username, cls.headers["other"] = new_practitioner(cls.client, cls.headers["admin"])

    @classmethod
    def tearDownClass(cls):
        _sql("DELETE FROM users WHERE username = ?", (cls.other_username,))
        _sql("DELETE FROM enrollment_tokens WHERE username = ?", (cls.other_username,))

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.cards = []
        _sql("DELETE FROM erasure_requests WHERE patient_id = ?", (CLIENT_ID,))

    def tearDown(self):
        _sql("DELETE FROM erasure_requests WHERE patient_id = ?", (CLIENT_ID,))
        for patient_id in self.cards:
            _sql("DELETE FROM erasure_requests WHERE patient_id = ?", (patient_id,))
            _sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))

    def _request(self, patient_id=CLIENT_ID, actor="practitioner"):
        return self.client.post("/api/v1/kvkk/erasure-requests", headers=self.headers[actor],
                                json={"patient_id": patient_id})

    # ── data export ──
    def test_practitioner_exports_their_clients_data(self):
        res = self.client.post("/api/v1/records", headers=self.headers["practitioner"], json={
            "patient_id": CLIENT_ID, "record_type": "session_note", "title": "Exported note",
            "doctor_name": "Psk", "institution": "Practice", "record_date": "2026-09-01",
            "data": {"session_number": 1, "duration_min": 50, "session_format": "Online", "summary": "x"}})
        self.assertEqual(res.status_code, 200, res.text)

        res = self.client.get(f"/api/v1/kvkk/export/{CLIENT_ID}", headers=self.headers["practitioner"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("attachment", res.headers["content-disposition"])
        data = json.loads(res.content)
        self.assertEqual(data["client"]["patient_id"], CLIENT_ID)
        self.assertEqual(data["client"]["full_name"], "Ahmet Karataş")
        for key in ("records", "appointments", "access_log", "erasure_requests"):
            self.assertIn(key, data)
        self.assertIn("Exported note", [r["title"] for r in data["records"]])

    def test_nobody_else_exports(self):
        for actor in ("other", "secretary", "admin"):
            with self.subTest(actor=actor):
                self.assertEqual(self.client.get(f"/api/v1/kvkk/export/{CLIENT_ID}",
                                                 headers=self.headers[actor]).status_code, 403)

    # ── erasure requests ──
    def test_practitioner_files_one_request_per_client(self):
        res = self._request()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["request"]["status"], "open")
        self.assertEqual(res.json()["request"]["requested_by"], PRACTITIONER)
        self.assertEqual(self._request().status_code, 409)
        mine = self.client.get("/api/v1/kvkk/erasure-requests", headers=self.headers["practitioner"]).json()
        self.assertIn(CLIENT_ID, [r["patient_id"] for r in mine["requests"]])

    def test_only_the_clients_practitioner_files_a_request(self):
        for actor in ("other", "secretary", "admin"):
            with self.subTest(actor=actor):
                self.assertEqual(self._request(actor=actor).status_code, 403)

    def test_who_sees_erasure_requests(self):
        self._request()
        for actor in ("admin", "officer"):
            with self.subTest(actor=actor):
                listed = self.client.get("/api/v1/kvkk/erasure-requests", headers=self.headers[actor])
                self.assertIn(CLIENT_ID, [r["patient_id"] for r in listed.json()["requests"]])
        # Another practitioner sees only their own requests; the secretary none.
        listed = self.client.get("/api/v1/kvkk/erasure-requests", headers=self.headers["other"]).json()
        self.assertNotIn(CLIENT_ID, [r["patient_id"] for r in listed["requests"]])
        self.assertEqual(self.client.get("/api/v1/kvkk/erasure-requests",
                                         headers=self.headers["secretary"]).status_code, 403)

    def test_done_only_after_the_key_is_destroyed(self):
        request_id = self._request().json()["request"]["id"]
        # CL-001 still has its key: the request cannot be closed as done.
        res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/done", headers=self.headers["admin"])
        self.assertEqual(res.status_code, 409, res.text)
        # Rejecting (e.g. a legal duty to keep the records) is always possible.
        res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/rejected", headers=self.headers["officer"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["request"]["status"], "rejected")

    def test_done_when_there_is_nothing_left_to_erase(self):
        # A new client with no records has no key yet, so there is nothing to shred.
        card = client_registry.add(practitioner=PRACTITIONER, full_name="Erase Me", phone=None, email=None,
                                   kvkk_signed_on=None, created_by="test")
        self.cards.append(card["patient_id"])
        request_id = self._request(card["patient_id"]).json()["request"]["id"]
        res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/done", headers=self.headers["admin"])
        self.assertEqual(res.status_code, 200, res.text)

    def test_only_operators_close_requests(self):
        request_id = self._request().json()["request"]["id"]
        for actor in ("practitioner", "secretary"):
            with self.subTest(actor=actor):
                res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/rejected",
                                       headers=self.headers[actor])
                self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
