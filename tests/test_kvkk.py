"""
tests/test_kvkk.py — the client's KVKK rights as screens
========================================================
Privacy notice with explicit consent, a copy of one's own data, and erasure
requests that an operator can close as done only once the client's key has
really been destroyed.
"""

import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from backend.schemas.requests import RECORD_TYPES
from core.services import kvkk
from database.sql_db import default_sql_db
from tests.test_appointments import _sql

CLIENT_ID = "CL-001"
PRACTITIONER = "psk.elif"
STRONG = "Enrolled@2026Secure!"
ACCOUNTS = {
    "client": ("client001", "Client@2026Secure!"),
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

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.users = []
        self._clean()

    def tearDown(self):
        self._clean()
        for username in self.users:
            _sql("DELETE FROM users WHERE username = ?", (username,))
            _sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))
            _sql("DELETE FROM kvkk_notice_acceptances WHERE username = ?", (username,))
        for record_type in ["all", *RECORD_TYPES]:
            self.client.delete(f"/api/v1/consent/{CLIENT_ID}/{PRACTITIONER}/{record_type}",
                               headers=self.headers["client"])

    def _clean(self):
        _sql("DELETE FROM erasure_requests WHERE patient_id = ?", (CLIENT_ID,))
        _sql("DELETE FROM kvkk_notice_acceptances WHERE username = ?", ("client001",))

    # ── privacy notice ──
    def test_client_reads_and_accepts_the_notice(self):
        notice = self.client.get("/api/v1/kvkk/notice", headers=self.headers["client"]).json()
        self.assertEqual(notice["version"], kvkk.NOTICE_VERSION)
        self.assertIn("explicit consent", notice["text"])
        self.assertIsNone(notice["accepted_at"])

        first = self.client.post("/api/v1/kvkk/notice/accept", headers=self.headers["client"])
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.post("/api/v1/kvkk/notice/accept", headers=self.headers["client"])
        self.assertEqual(first.json()["accepted_at"], again.json()["accepted_at"])   # recorded once
        self.assertIsNotNone(self.client.get("/api/v1/kvkk/notice",
                                             headers=self.headers["client"]).json()["accepted_at"])

    def test_only_clients_accept_the_notice(self):
        for actor in ("practitioner", "secretary", "admin"):
            with self.subTest(actor=actor):
                self.assertEqual(self.client.post("/api/v1/kvkk/notice/accept",
                                                  headers=self.headers[actor]).status_code, 403)

    # ── data export ──
    def test_export_holds_the_clients_own_data_only(self):
        # A practitioner-only note must not appear in the client's copy.
        self.client.post("/api/v1/consent", headers=self.headers["client"], json={
            "patient_id": CLIENT_ID, "doctor_username": PRACTITIONER,
            "record_type": "all", "duration_days": 1})
        res = self.client.post("/api/v1/records", headers=self.headers["practitioner"], json={
            "patient_id": CLIENT_ID, "record_type": "session_note", "title": "Hidden process note",
            "doctor_name": "Psk", "institution": "Practice", "record_date": "2026-09-01",
            "access_level": "practitioner_only",
            "data": {"session_number": 1, "duration_min": 50, "session_format": "Online", "summary": "x"}})
        self.assertEqual(res.status_code, 200, res.text)

        res = self.client.get("/api/v1/kvkk/export", headers=self.headers["client"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("attachment", res.headers["content-disposition"])
        data = json.loads(res.content)
        self.assertEqual(data["account"]["client_id"], CLIENT_ID)
        for key in ("records", "appointments", "consents_given", "access_log"):
            self.assertIn(key, data)
        self.assertNotIn("Hidden process note", [r["title"] for r in data["records"]])
        self.assertTrue(all(r["access_level"] != "practitioner_only" for r in data["records"]))

    def test_only_a_client_exports_their_own_data(self):
        for actor in ("practitioner", "secretary", "admin"):
            with self.subTest(actor=actor):
                self.assertEqual(self.client.get("/api/v1/kvkk/export",
                                                 headers=self.headers[actor]).status_code, 403)

    # ── erasure requests ──
    def test_client_requests_erasure_once(self):
        res = self.client.post("/api/v1/kvkk/erasure-requests", headers=self.headers["client"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["request"]["status"], "open")
        self.assertEqual(self.client.post("/api/v1/kvkk/erasure-requests",
                                          headers=self.headers["client"]).status_code, 409)
        mine = self.client.get("/api/v1/kvkk/erasure-requests", headers=self.headers["client"]).json()["requests"]
        self.assertEqual([r["patient_id"] for r in mine], [CLIENT_ID])

    def test_who_sees_erasure_requests(self):
        self.client.post("/api/v1/kvkk/erasure-requests", headers=self.headers["client"])
        for actor in ("admin", "officer"):
            with self.subTest(actor=actor):
                listed = self.client.get("/api/v1/kvkk/erasure-requests", headers=self.headers[actor])
                self.assertIn(CLIENT_ID, [r["patient_id"] for r in listed.json()["requests"]])
        for actor in ("practitioner", "secretary"):
            with self.subTest(actor=actor):
                self.assertEqual(self.client.get("/api/v1/kvkk/erasure-requests",
                                                 headers=self.headers[actor]).status_code, 403)

    def test_done_only_after_the_key_is_destroyed(self):
        request_id = self.client.post("/api/v1/kvkk/erasure-requests",
                                      headers=self.headers["client"]).json()["request"]["id"]
        # CL-001 still has its key: the request cannot be closed as done.
        res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/done", headers=self.headers["admin"])
        self.assertEqual(res.status_code, 409, res.text)
        # Rejecting (e.g. a legal duty to keep the records) is always possible.
        res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/rejected", headers=self.headers["officer"])
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["request"]["status"], "rejected")

    def test_done_when_there_is_nothing_left_to_erase(self):
        # A new client with no records has no key yet, so there is nothing to shred.
        invite = self.client.post("/api/v1/onboarding/invite-client", headers=self.headers["practitioner"],
                                  json={"full_name": "Erase Me"}).json()
        self.users.append(invite["username"])
        self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": invite["invite_code"], "new_password": STRONG})
        new_client = {"Authorization": "Bearer " + self.client.post("/api/v1/auth/login", json={
            "username": invite["username"], "password": STRONG}).json()["access_token"]}
        request_id = self.client.post("/api/v1/kvkk/erasure-requests",
                                      headers=new_client).json()["request"]["id"]
        try:
            res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/done", headers=self.headers["admin"])
            self.assertEqual(res.status_code, 200, res.text)
        finally:
            _sql("DELETE FROM erasure_requests WHERE id = ?", (request_id,))

    def test_only_operators_close_requests(self):
        request_id = self.client.post("/api/v1/kvkk/erasure-requests",
                                      headers=self.headers["client"]).json()["request"]["id"]
        for actor in ("client", "practitioner", "secretary"):
            with self.subTest(actor=actor):
                res = self.client.post(f"/api/v1/kvkk/erasure-requests/{request_id}/rejected",
                                       headers=self.headers[actor])
                self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
