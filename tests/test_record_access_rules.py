"""
tests/test_record_access_rules.py — one access rule for every record endpoint
=============================================================================
The record list (core/cqrs/queries.py) and the endpoints that touch a single
record apply the same policy. These tests pin the single-record endpoints:

  * an attachment is given to the client's own practitioner and nobody else;
  * a correction cannot change a record's access level, i.e. who may see it;
  * a client's journal from the old model (when clients had accounts) stays
    closed to the practitioner: not listed, not downloadable, not correctable,
    and not opened by its password either.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db
from tests.test_appointments import _sql
from tests.test_practitioner_file_access import new_practitioner

CLIENT_ID = "CL-001"
PRACTITIONER = "psk.elif"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
}
PROFILE_DATA = {"presenting_problem": "Panic on the commute"}
JOURNAL_PASSWORD = "ClientOnly@2026!"


def _old_client_journal(password=None):
    """Write a record as the old model did: a client's own "private" journal.
    The API no longer writes such records, so it goes straight to the chain."""
    from core.cqrs.commands import AddRecordCommand, CommandHandler
    from core.services.record_service import RecordService
    from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
    from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository

    block_repo = LMDBBlockRepository()
    handler = CommandHandler(RecordService(block_repo, AESGCMStrategy()), None, block_repo)
    data = {"record_type": "other", "record_type_label": "Other", "title": "My journal",
            "doctor_name": "", "institution": "", "record_date": "2026-09-01",
            "access_level": "private", "is_confidential": bool(password), "data": {},
            "notes": "Only for me.", "created_by": "client001",
            "created_at": datetime.now(timezone.utc).isoformat(), "patient_id": CLIENT_ID,
            "file_name": "journal.txt", "file_type": "text/plain", "file_data": None}
    block = handler.handle_add_record(AddRecordCommand(
        patient_id=CLIENT_ID, data=data, is_protected=bool(password),
        protection_password=password, username="client001"))
    return block.index


class TestRecordAccessRules(unittest.TestCase):
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

    def _add(self, record_type="client_profile", data=PROFILE_DATA, with_file=False, password=None):
        body = {
            "patient_id": CLIENT_ID, "record_type": record_type, "title": "Access rule test",
            "doctor_name": "Uzm. Psk. Elif Yilmaz", "institution": "Mahrem",
            "record_date": "2026-09-01",
            "is_confidential": bool(password), "confidential_password": password,
            "data": data, "notes": "",
        }
        if with_file:
            body.update(file_name="note.txt", file_type="text/plain", file_data="aGVsbG8=")
        res = self.client.post("/api/v1/records", headers=self.headers["practitioner"], json=body)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def _download(self, idx, actor="practitioner"):
        return self.client.get(f"/api/v1/records/offchain/download/{CLIENT_ID}/{idx}",
                               headers=self.headers[actor])

    # ── attachments ──
    def test_attachment_is_for_the_clients_practitioner_only(self):
        idx = self._add(with_file=True)
        res = self._download(idx)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, b"hello")
        for actor in ("other", "secretary"):
            with self.subTest(actor=actor):
                self.assertEqual(self._download(idx, actor=actor).status_code, 403)

    # ── locked records ──
    def test_a_locked_record_opens_with_its_password_only(self):
        idx = self._add(password="Locked@2026Note!")
        listed = {r["block_index"]: r for r in self.client.get(
            f"/api/v1/records/{CLIENT_ID}", headers=self.headers["practitioner"]).json()["records"]}
        self.assertTrue(listed[idx]["is_protected"])
        self.assertIsNone(listed[idx]["data"])
        wrong = self.client.post(f"/api/v1/records/{CLIENT_ID}/{idx}/decrypt",
                                 headers=self.headers["practitioner"], json={"password": "Wrong@2026Pass!"})
        self.assertEqual(wrong.status_code, 403)
        right = self.client.post(f"/api/v1/records/{CLIENT_ID}/{idx}/decrypt",
                                 headers=self.headers["practitioner"], json={"password": "Locked@2026Note!"})
        self.assertEqual(right.status_code, 200, right.text)
        self.assertEqual(right.json()["data"]["data"], PROFILE_DATA)
        # The password alone is not enough for another practitioner.
        other = self.client.post(f"/api/v1/records/{CLIENT_ID}/{idx}/decrypt",
                                 headers=self.headers["other"], json={"password": "Locked@2026Note!"})
        self.assertEqual(other.status_code, 403)

    # ── corrections ──
    def test_a_correction_cannot_change_who_sees_the_record(self):
        idx = self._add()
        res = self.client.post(
            f"/api/v1/records/{CLIENT_ID}/{idx}/correct", headers=self.headers["practitioner"],
            json={"reason": "Typo", "corrected_data": {
                "title": "Fixed title", "access_level": "doctor_shared"}})
        self.assertEqual(res.status_code, 200, res.text)
        current = self.client.get(f"/api/v1/records/{CLIENT_ID}/{idx}?version=current",
                                  headers=self.headers["practitioner"]).json()["data"]
        self.assertEqual(current["title"], "Fixed title")
        self.assertEqual(current["access_level"], "practitioner_only")

    # ── a client's journal from the old model ──
    def test_an_old_client_journal_stays_closed(self):
        idx = _old_client_journal()
        listed = [r["block_index"] for r in self.client.get(
            f"/api/v1/records/{CLIENT_ID}", headers=self.headers["practitioner"]).json()["records"]]
        self.assertNotIn(idx, listed)
        self.assertEqual(self.client.get(f"/api/v1/records/{CLIENT_ID}/{idx}",
                                         headers=self.headers["practitioner"]).status_code, 404)
        self.assertEqual(self._download(idx).status_code, 403)
        res = self.client.post(
            f"/api/v1/records/{CLIENT_ID}/{idx}/correct", headers=self.headers["practitioner"],
            json={"reason": "Tidy up", "corrected_data": {"title": "Overwritten"}})
        self.assertEqual(res.status_code, 404, res.text)

    def test_its_password_does_not_open_an_old_client_journal(self):
        idx = _old_client_journal(password=JOURNAL_PASSWORD)
        res = self.client.post(f"/api/v1/records/{CLIENT_ID}/{idx}/decrypt",
                               headers=self.headers["practitioner"], json={"password": JOURNAL_PASSWORD})
        self.assertEqual(res.status_code, 403, res.text)


if __name__ == "__main__":
    unittest.main()
