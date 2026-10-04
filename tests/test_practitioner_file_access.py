"""
tests/test_practitioner_file_access.py — only the client's own practitioner
===========================================================================
The API side of core/services/access_policy.py (ADR-0003). A client's file
belongs to the practitioner who keeps the client's card:

  * GET /records/{client}/{block} used to check nothing for a practitioner, so
    any practitioner could read any client's records by walking block numbers
    (an IDOR). Another practitioner now gets 403, for the list, a single
    record, the chain status and the Merkle proof alike;
  * nobody else writes into the file — not another practitioner, not the
    secretary, not an operator;
  * a missing client and someone else's client get the same answer.
"""

import os
import random
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db
from tests.test_appointments import _sql

CLIENT_ID = "CL-001"
UNKNOWN_CLIENT_ID = "CL-9999"
PRACTITIONER = "psk.elif"
STRONG = "Enrolled@2026Secure!"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
}
SESSION_DATA = {"session_number": 4, "duration_min": 50, "session_format": "Online",
                "summary": "Reviewed the exposure ladder."}


def new_practitioner(client, admin_headers):
    """A second, real practitioner account (provisioned and enrolled)."""
    username = f"psk.other{random.randint(1000, 9999)}"
    res = client.post("/api/v1/onboarding/provision", headers=admin_headers, json={
        "username": username, "full_name": "Other Practitioner", "role": "practitioner"})
    assert res.status_code == 200, res.text
    client.post("/api/v1/onboarding/redeem", json={
        "enrollment_token": res.json()["enrollment_token"], "new_password": STRONG})
    token = client.post("/api/v1/auth/login", json={"username": username, "password": STRONG}).json()["access_token"]
    return username, {"Authorization": f"Bearer {token}"}


class TestPractitionerFileAccess(unittest.TestCase):
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

    def _add(self, actor="practitioner", record_type="session_note", data=SESSION_DATA, patient_id=CLIENT_ID):
        return self.client.post("/api/v1/records", headers=self.headers[actor], json={
            "patient_id": patient_id, "record_type": record_type, "title": "File access test",
            "doctor_name": "Uzm. Psk. Elif Yilmaz", "institution": "Mahrem",
            "record_date": "2026-09-01", "data": data, "notes": "",
        })

    def _added(self, *args, **kwargs):
        res = self._add(*args, **kwargs)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def _get(self, path, actor="practitioner"):
        return self.client.get(path, headers=self.headers[actor])

    def _paths(self, idx):
        return (f"/api/v1/records/{CLIENT_ID}",
                f"/api/v1/records/{CLIENT_ID}/{idx}",
                f"/api/v1/blockchain/{CLIENT_ID}/status",
                f"/api/v1/records/proof/{CLIENT_ID}/{idx}",
                f"/api/v1/blockchain/{CLIENT_ID}/access-logs")

    # ── the owner ──────────────────────────────────────────────
    def test_the_practitioner_opens_their_clients_file(self):
        idx = self._added()
        for path in self._paths(idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path).status_code, 200)
        listed = [r["block_index"] for r in self._get(f"/api/v1/records/{CLIENT_ID}").json()["records"]]
        self.assertIn(idx, listed)

    def test_every_new_record_is_practitioner_only(self):
        idx = self._added()
        data = self._get(f"/api/v1/records/{CLIENT_ID}/{idx}").json()["data"]
        self.assertEqual(data["access_level"], "practitioner_only")

    def test_bookkeeping_blocks_are_hidden(self):
        self.assertEqual(self._get(f"/api/v1/records/{CLIENT_ID}/0").status_code, 404)

    # ── the IDOR: another practitioner ─────────────────────────
    def test_another_practitioner_is_refused_everywhere(self):
        idx = self._added()
        for path in self._paths(idx):
            with self.subTest(path=path):
                self.assertEqual(self._get(path, actor="other").status_code, 403)

    def test_another_practitioner_cannot_write_or_correct(self):
        idx = self._added()
        self.assertEqual(self._add("other").status_code, 403)
        res = self.client.post(
            f"/api/v1/records/{CLIENT_ID}/{idx}/correct", headers=self.headers["other"],
            json={"reason": "Curious", "corrected_data": {"title": "Overwritten"}})
        self.assertEqual(res.status_code, 403, res.text)

    def test_unknown_client_looks_the_same_as_someone_elses(self):
        theirs = self._get(f"/api/v1/records/{CLIENT_ID}", actor="other")
        unknown = self._get(f"/api/v1/records/{UNKNOWN_CLIENT_ID}", actor="other")
        self.assertEqual(theirs.status_code, unknown.status_code)
        self.assertEqual(theirs.json(), unknown.json())

    def test_cannot_write_into_a_client_that_does_not_exist(self):
        self.assertEqual(self._add(patient_id=UNKNOWN_CLIENT_ID).status_code, 403)

    # ── nobody else writes ─────────────────────────────────────
    def test_only_the_practitioner_writes(self):
        for actor in ("secretary", "admin"):
            with self.subTest(actor=actor):
                self.assertEqual(self._add(actor).status_code, 403)

    def test_a_transcript_needs_its_text(self):
        self.assertEqual(self._add(record_type="session_transcript", data={"session_number": 3}).status_code, 422)
        self._added(record_type="session_transcript", data={"session_number": 3, "transcript": "T: ...\nC: ..."})


if __name__ == "__main__":
    unittest.main()
