"""
tests/test_storage_and_metadata_safety.py — chain-store and metadata boundaries
===============================================================================
Two properties that must hold regardless of how a record is written:

  * a patient identifier can never place a chain store outside the projects
    directory (path-traversal confinement at the connection manager), and clinical
    text is stored as ciphertext on disk yet returned verbatim (escaping is a
    render-time concern);
  * chain metadata and the liveness probe do not leak across patient boundaries.

(These moved here when the LIS webhook gateway was removed; they were never
LIS-specific — the gateway was merely the write path they happened to use.)
"""

import os
import shutil
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.connection import LMDBConnectionManager
from core.pseudonymization.service import project_name_for


class TestChainStorePathSafety(unittest.TestCase):
    """No caller may place a chain store outside the projects directory."""

    def setUp(self):
        self.base = os.path.join(os.path.dirname(__file__), "test_path_projects")
        self.manager = LMDBConnectionManager(self.base)

    def tearDown(self):
        self.manager.close_all()
        shutil.rmtree(self.base, ignore_errors=True)

    def test_traversing_project_names_are_refused(self):
        manager = self.manager
        for name in ("../escape", "../../escape", "patient/../..", "a/b", "..", ""):
            with self.assertRaises(ValueError, msg=name):
                manager.get_project_path(name)

    def test_ordinary_project_names_still_resolve(self):
        manager = self.manager
        path = manager.get_project_path("patient_CL_001")
        self.assertTrue(path.endswith("patient_CL_001"))


class TestAtRestStorage(unittest.TestCase):
    """Clinical text is ciphertext on disk, but returned verbatim to the service."""

    @classmethod
    def setUpClass(cls):
        from database.sql_db import default_sql_db
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        res = self.client.post("/api/v1/auth/login",
                               json={"username": "psk.elif", "password": "Practitioner@2026!"})
        self.assertEqual(res.status_code, 200, res.text)
        self.token = res.json()["access_token"]

    def _add(self, summary):
        return self.client.post("/api/v1/records",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "patient_id": "CL-001", "record_type": "session_note",
                "title": "Session note", "doctor_name": "Psk. A",
                "institution": "Practice", "record_date": "2026-08-01",
                "is_confidential": False,
                "data": {"session_number": 1, "duration_min": 50,
                         "session_format": "In-person", "summary": summary},
                "notes": "",
            })

    def test_submitted_text_is_stored_verbatim(self):
        """Clinical text is preserved as written; escaping happens at render."""
        res = self._add("Anxiety score fell to <5 & sleep improved")
        self.assertEqual(res.status_code, 200, res.text)

        from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
        from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
        from core.services.record_service import RecordService

        service = RecordService(LMDBBlockRepository(), AESGCMStrategy())
        revealed = " ".join(str(v) for v in service.get_final_data("CL-001").values())
        self.assertIn("<5 & sleep", revealed)
        self.assertNotIn("&lt;5", revealed)

    def test_records_are_ciphertext_on_disk(self):
        """The raw chain store must not hold plaintext clinical text."""
        res = self._add("SECRET-MARKER-XYZ")
        self.assertEqual(res.status_code, 200, res.text)

        from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
        blocks = LMDBBlockRepository().load_all_blocks(project_name_for("CL-001"))
        on_disk = " ".join(str(b.data) for b in blocks)
        self.assertNotIn("SECRET-MARKER-XYZ", on_disk)


class TestMetadataDisclosure(unittest.TestCase):
    """Chain metadata and the liveness probe must not leak across boundaries."""

    @classmethod
    def setUpClass(cls):
        from database.sql_db import default_sql_db
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)

    def _token(self, username, password):
        res = self.client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["access_token"]

    def test_practitioner_cannot_read_another_clients_chain_status(self):
        token = self._token("psk.elif", "Practitioner@2026!")
        res = self.client.get("/api/v1/blockchain/CL-999/status",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 403)

    def test_practitioner_can_read_their_clients_chain_status(self):
        token = self._token("psk.elif", "Practitioner@2026!")
        res = self.client.get("/api/v1/blockchain/CL-001/status",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 200)

    def test_health_probe_does_not_disclose_the_device_fingerprint(self):
        body = self.client.get("/api/v1/health").json()
        self.assertEqual(body["status"], "healthy")
        self.assertNotIn("device_id", body)


if __name__ == "__main__":
    unittest.main()
