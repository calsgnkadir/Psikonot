"""
tests/test_erasure.py — GDPR/KVKK Art. 17 crypto-shredding erasure
==================================================================
Erasure destroys the patient's per-patient erasure key. The append-only chain and
its signatures stay intact and valid, but every record encrypted under the now-gone
key becomes permanently undecryptable — read-back returns an "erased" marker, never
the plaintext. Erasure is privileged and Dual-Control-gated.
"""

import os
import sys
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db
from core.services.erasure_service import get_erasure_key_store
from core.services.record_service import RecordService
from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
from infrastructure.cryptography.crypto_strategies import AESGCMStrategy

_ACCOUNTS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
    "doctor": ("psk.elif", "Practitioner@2026!"),
}


class TestErasure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.svc = RecordService(LMDBBlockRepository(), AESGCMStrategy())
        # Unique numeric patient id per run so destructive erasure never touches
        # another test's chain.
        self.patient = f"CL-9{uuid.uuid4().int % 1000:03d}"

    def _headers(self, actor):
        username, password = _ACCOUNTS[actor]
        res = self.client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _dc_token(self):
        res = self.client.post("/api/v1/security/dual-control/request",
            headers=self._headers("admin"),
            json={"request_type": "ERASE_PATIENT", "target_patient_id": self.patient,
                  "reason": "Data subject erasure request", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token_id = res.json()["token_id"]
        cosign = self.client.post("/api/v1/security/dual-control/co-sign",
            headers=self._headers("officer"), json={"token_id": token_id})
        self.assertEqual(cosign.status_code, 200, cosign.text)
        return token_id

    def _write_two_records(self):
        for sev in ("Mild", "Severe"):
            self.svc.add_record(self.patient, {
                "record_type": "session_note", "title": "Session 1",
                "data": {"icd_code": "I10", "severity": sev, "note": "SENSITIVE-MARKER"},
            }, username="psk.elif")

    def _erase(self, actor="admin", token=None):
        headers = self._headers(actor)
        if token:
            headers["X-Dual-Control-Token"] = token
        return self.client.post(f"/api/v1/erasure/{self.patient}", headers=headers)

    # ── the guarantee ──────────────────────────────────────────
    def test_erasure_shreds_records_but_keeps_the_chain_valid(self):
        self._write_two_records()
        # Before: readable plaintext, key exists, chain valid.
        before = self.svc.get_final_data(self.patient)
        joined = " ".join(str(v) for v in before.values())
        self.assertIn("SENSITIVE-MARKER", joined)
        self.assertTrue(get_erasure_key_store().exists(self.patient))
        self.assertTrue(self.svc.is_chain_valid(self.patient))

        res = self._erase(token=self._dc_token())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["erasure_key_destroyed"])

        # After: key gone, plaintext unrecoverable (erased marker), chain still valid.
        self.assertFalse(get_erasure_key_store().exists(self.patient))
        after = self.svc.get_final_data(self.patient)
        joined_after = " ".join(str(v) for v in after.values())
        self.assertNotIn("SENSITIVE-MARKER", joined_after)
        self.assertIn("__erased__", joined_after)
        self.assertTrue(self.svc.is_chain_valid(self.patient))

    def test_erasure_shreds_password_protected_records(self):
        """A password-protected record was encrypted under its password alone,
        so destroying the erasure key left it readable to anyone holding the
        password. It now also sits under the erasure key."""
        pwd = "Locked@Record2026!"
        block = self.svc.add_record(self.patient, {"title": "Transcript", "note": "SENSITIVE-MARKER"},
                                    is_protected=True, protection_password=pwd, username="psk.elif")
        self.assertIn("SENSITIVE-MARKER", str(self.svc.get_block_data(self.patient, block.index, password=pwd)))

        get_erasure_key_store().destroy(self.patient)

        after = self.svc.get_block_data(self.patient, block.index, password=pwd)
        self.assertNotIn("SENSITIVE-MARKER", str(after))
        self.assertTrue(after.get("__erased__"))
        self.assertTrue(self.svc.is_chain_valid(self.patient))

    def test_erasure_shreds_password_protected_corrections(self):
        pwd = "Locked@Record2026!"
        block = self.svc.add_record(self.patient, {"title": "Transcript"},
                                    is_protected=True, protection_password=pwd, username="psk.elif")
        self.svc.add_correction_block(self.patient, block.index, {"title": "Fixed", "note": "SENSITIVE-MARKER"},
                                      encryption_password=pwd, username="psk.elif", reason="typo")
        self.assertIn("SENSITIVE-MARKER",
                      str(self.svc.get_final_block_data(self.patient, block.index, password=pwd)))

        get_erasure_key_store().destroy(self.patient)

        after = self.svc.get_final_block_data(self.patient, block.index, password=pwd)
        self.assertNotIn("SENSITIVE-MARKER", str(after))
        self.assertTrue(after.get("__erased__"))

    def test_erasure_is_idempotent(self):
        self._write_two_records()
        self.assertEqual(self._erase(token=self._dc_token()).status_code, 200)
        second = self._erase(token=self._dc_token())
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["was_already_erased"])

    def test_non_privileged_role_cannot_erase(self):
        self._write_two_records()
        res = self._erase(actor="doctor")
        self.assertEqual(res.status_code, 403)

    def test_privileged_without_dual_control_is_blocked(self):
        self._write_two_records()
        res = self._erase(actor="admin", token=None)
        self.assertEqual(res.status_code, 403)
        # And the data is still readable — nothing was shredded.
        joined = " ".join(str(v) for v in self.svc.get_final_data(self.patient).values())
        self.assertIn("SENSITIVE-MARKER", joined)


if __name__ == "__main__":
    unittest.main()
