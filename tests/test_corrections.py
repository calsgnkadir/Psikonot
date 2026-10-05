"""
tests/test_corrections.py — a client record is corrected, never overwritten
===========================================================================
A correction appends a new block that supersedes the original. Both remain on the
chain: the current view shows the corrected content, `?version=original` still
returns the superseded content, and the record is flagged with the correction's
provenance. Correcting requires the same access as reading.
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db


class TestCorrectionFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.token = self._login("psk.elif", "Practitioner@2026!")

    def _login(self, username, password):
        res = self.client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["access_token"]

    def _auth(self, token=None):
        return {"Authorization": f"Bearer {token or self.token}"}

    def _add_profile(self):
        res = self.client.post("/api/v1/records", headers=self._auth(), json={
            "patient_id": "CL-001", "record_type": "client_profile",
            "title": "Original profile", "doctor_name": "Psk. A",
            "institution": "Practice", "record_date": "2026-08-01",
            "is_confidential": False,
            "data": {"presenting_problem": "Panic on the commute"},
            "notes": "",
        })
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["block_index"]

    def _correct(self, idx, problem="Panic on the commute and at work", reason="Re-evaluated", token=None):
        return self.client.post(
            f"/api/v1/records/CL-001/{idx}/correct", headers=self._auth(token),
            json={"reason": reason, "corrected_data": {
                "title": "Corrected profile", "record_type": "client_profile",
                "doctor_name": "Psk. A", "institution": "Practice",
                "record_date": "2026-08-01",
                "data": {"presenting_problem": problem},
                "notes": "",
            }},
        )

    def test_correction_supersedes_but_keeps_the_original(self):
        idx = self._add_profile()
        res = self._correct(idx)
        self.assertEqual(res.status_code, 200, res.text)

        current = self.client.get(f"/api/v1/records/CL-001/{idx}?version=current",
                                  headers=self._auth()).json()["data"]
        original = self.client.get(f"/api/v1/records/CL-001/{idx}?version=original",
                                   headers=self._auth()).json()["data"]
        self.assertEqual(current["data"]["presenting_problem"], "Panic on the commute and at work")
        self.assertEqual(current["title"], "Corrected profile")
        # The original block is untouched and still readable.
        self.assertEqual(original["data"]["presenting_problem"], "Panic on the commute")
        self.assertEqual(original["title"], "Original profile")

    def test_corrected_record_is_flagged_with_provenance(self):
        idx = self._add_profile()
        self._correct(idx, reason="Problem mis-recorded")
        records = self.client.get("/api/v1/records/CL-001", headers=self._auth()).json()["records"]
        rec = next(r for r in records if r["block_index"] == idx)
        self.assertTrue(rec["is_corrected"])
        self.assertEqual(rec["correction"]["reason"], "Problem mis-recorded")
        self.assertEqual(rec["correction"]["corrected_by"], "psk.elif")

    def test_correction_requires_a_reason(self):
        idx = self._add_profile()
        res = self.client.post(
            f"/api/v1/records/CL-001/{idx}/correct", headers=self._auth(),
            json={"reason": "  ", "corrected_data": {"title": "x", "data": {}}},
        )
        self.assertEqual(res.status_code, 422)

    def test_chain_stays_valid_after_correction(self):
        # Verified on an isolated chain: the shared CL-001 store is mutated by
        # many other test classes, so its overall validity is not a clean signal.
        import database.storage as storage
        from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
        from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
        from core.services.record_service import RecordService
        from core.cqrs.commands import AddRecordCommand, AddCorrectionCommand, CommandHandler

        patient = "CL-CORRECT-ISO"
        block_repo = LMDBBlockRepository()
        service = RecordService(block_repo, AESGCMStrategy())
        handler = CommandHandler(service, None, block_repo)
        storage.reset_db(service._get_project_name(patient))

        block = handler.handle_add_record(AddRecordCommand(
            patient_id=patient,
            data={"record_type": "client_profile", "title": "Profile",
                  "data": {"presenting_problem": "Panic on the commute"}},
            is_protected=False, protection_password=None, username="dr.iso",
        ))
        self.assertTrue(service.is_chain_valid(patient))

        handler.handle_add_correction(AddCorrectionCommand(
            patient_id=patient, block_index=block.index,
            corrected_data={"record_type": "client_profile", "title": "Profile (corrected)",
                            "data": {"presenting_problem": "Panic on the commute and at work"}},
            username="dr.iso", reason="Re-graded",
        ))
        self.assertTrue(service.is_chain_valid(patient))
        storage.reset_db(service._get_project_name(patient))

    def test_nobody_but_the_practitioner_corrects(self):
        idx = self._add_profile()
        for username, password in (("secretary.ayse", "Secretary@2026!"), ("admin", "Admin@2026Secure!")):
            with self.subTest(username=username):
                res = self._correct(idx, token=self._login(username, password))
                self.assertEqual(res.status_code, 403)

    def test_audit_and_correction_blocks_cannot_be_corrected(self):
        # Block 0 is genesis; correcting a non-clinical block is rejected.
        res = self._correct(0)
        self.assertIn(res.status_code, (400, 404, 422))

    def test_correction_is_validated_like_a_new_record(self):
        # This used to be stored as-is; a profile without a problem must be refused.
        idx = self._add_profile()
        res = self.client.post(
            f"/api/v1/records/CL-001/{idx}/correct", headers=self._auth(),
            json={"reason": "Emptied", "corrected_data": {
                "record_type": "client_profile",
                "data": {"presenting_problem": ""},
            }},
        )
        self.assertEqual(res.status_code, 422, res.text)

    def test_correction_drops_fields_the_record_form_never_sends(self):
        idx = self._add_profile()
        res = self.client.post(
            f"/api/v1/records/CL-001/{idx}/correct", headers=self._auth(),
            json={"reason": "Re-scored", "corrected_data": {
                "title": "Corrected profile",
                "file_type": 'image/png" onerror="alert(1)',
                "smuggled_field": "<script>alert(1)</script>",
            }},
        )
        self.assertEqual(res.status_code, 200, res.text)
        current = self.client.get(f"/api/v1/records/CL-001/{idx}?version=current",
                                  headers=self._auth()).json()["data"]
        self.assertEqual(current["title"], "Corrected profile")
        self.assertNotIn("smuggled_field", current)
        self.assertNotIn("file_type", current)


if __name__ == "__main__":
    unittest.main()
