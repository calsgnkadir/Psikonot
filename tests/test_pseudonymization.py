"""
tests/test_pseudonymization.py — Pseudonymization Engine Unit Tests
=====================================================================
Tests for the identity decoupling layer.
"""

import os
import sys
import unittest
import tempfile

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pseudonymization.engine import PseudonymizationEngine, PseudonymMapping
from core.pseudonymization.service import (
    PseudonymizationService,
    get_pseudonymization_service,
    reset_pseudonymization_service,
)


class TestPseudonymizationEngine(unittest.TestCase):
    """Test the core pseudonymization engine."""

    def setUp(self):
        self.engine = PseudonymizationEngine(secret="test-secret-key")

    def test_anon_id_is_64_hex_chars(self):
        anon = self.engine.generate_anon_id("CL-001")
        self.assertEqual(len(anon), 64)
        # Verify it's valid hex
        int(anon, 16)

    def test_deterministic_same_patient(self):
        """Same patient_id + same secret → same anon_id."""
        a1 = self.engine.generate_anon_id("CL-001")
        a2 = self.engine.generate_anon_id("CL-001")
        self.assertEqual(a1, a2)

    def test_different_patients_get_different_ids(self):
        a1 = self.engine.generate_anon_id("CL-001")
        a2 = self.engine.generate_anon_id("CL-002")
        self.assertNotEqual(a1, a2)

    def test_different_secrets_produce_different_ids(self):
        e1 = PseudonymizationEngine(secret="secret-A")
        e2 = PseudonymizationEngine(secret="secret-B")
        a1 = e1.generate_anon_id("CL-001")
        a2 = e2.generate_anon_id("CL-001")
        self.assertNotEqual(a1, a2)

    def test_anon_id_does_not_contain_patient_id(self):
        """The anonymous ID must not leak the original patient ID."""
        anon = self.engine.generate_anon_id("CL-001")
        self.assertNotIn("CL-001", anon)
        self.assertNotIn("001", anon[:10])  # first 10 chars shouldn't contain "001"

    def test_verify_mapping_correct(self):
        anon = self.engine.generate_anon_id("CL-001")
        self.assertTrue(self.engine.verify_mapping("CL-001", anon))

    def test_verify_mapping_wrong_patient(self):
        anon = self.engine.generate_anon_id("CL-001")
        self.assertFalse(self.engine.verify_mapping("CL-002", anon))

    def test_prefixed_anon_id(self):
        display = self.engine.generate_anon_id_with_prefix("CL-001")
        self.assertTrue(display.startswith("ANON-"))
        self.assertEqual(len(display), 5 + 16)  # "ANON-" + 16 hex chars

    def test_session_pseudonym_is_unique(self):
        p1 = self.engine.generate_session_pseudonym()
        p2 = self.engine.generate_session_pseudonym()
        self.assertNotEqual(p1, p2)
        self.assertTrue(p1.startswith("EPHEMERAL-"))

    def test_hash_field_deterministic(self):
        h1 = self.engine.hash_field("John Doe")
        h2 = self.engine.hash_field("John Doe")
        self.assertEqual(h1, h2)

    def test_hash_field_different_values(self):
        h1 = self.engine.hash_field("John Doe")
        h2 = self.engine.hash_field("Jane Doe")
        self.assertNotEqual(h1, h2)


class TestPseudonymMapping(unittest.TestCase):
    """Test the in-memory mapping cache."""

    def setUp(self):
        self.engine = PseudonymizationEngine(secret="mapping-test")
        self.mapping = PseudonymMapping(self.engine)

    def test_get_or_create_returns_consistent_id(self):
        a1 = self.mapping.get_or_create_anon_id("CL-001")
        a2 = self.mapping.get_or_create_anon_id("CL-001")
        self.assertEqual(a1, a2)

    def test_reverse_lookup(self):
        anon = self.mapping.get_or_create_anon_id("CL-001")
        result = self.mapping.resolve_patient_id(anon)
        self.assertEqual(result, "CL-001")

    def test_reverse_lookup_unknown(self):
        result = self.mapping.resolve_patient_id("nonexistent-anon-id")
        self.assertIsNone(result)

    def test_register_mapping(self):
        self.mapping.register_mapping("CL-999", "custom-anon-id")
        self.assertEqual(self.mapping.resolve_patient_id("custom-anon-id"), "CL-999")

    def test_get_all_mappings(self):
        self.mapping.get_or_create_anon_id("CL-001")
        self.mapping.get_or_create_anon_id("CL-002")
        all_m = self.mapping.get_all_mappings()
        self.assertEqual(len(all_m), 2)
        self.assertIn("CL-001", all_m)
        self.assertIn("CL-002", all_m)

    def test_clear_cache(self):
        self.mapping.get_or_create_anon_id("CL-001")
        self.mapping.clear_cache()
        self.assertEqual(len(self.mapping.get_all_mappings()), 0)


class TestPseudonymizationService(unittest.TestCase):
    """Test the high-level pseudonymization service."""

    def setUp(self):
        reset_pseudonymization_service()
        self.engine = PseudonymizationEngine(secret="service-test-key")
        self.svc = PseudonymizationService(engine=self.engine)

    def test_pseudonymize_returns_anon_id(self):
        anon = self.svc.pseudonymize("CL-001")
        self.assertIsInstance(anon, str)
        self.assertEqual(len(anon), 64)

    def test_pseudonymize_deterministic(self):
        a1 = self.svc.pseudonymize("CL-001")
        a2 = self.svc.pseudonymize("CL-001")
        self.assertEqual(a1, a2)

    def test_depseudonymize_roundtrip(self):
        anon = self.svc.pseudonymize("CL-001")
        real = self.svc.depseudonymize(anon)
        self.assertEqual(real, "CL-001")

    def test_depseudonymize_unknown(self):
        result = self.svc.depseudonymize("unknown-anon-id")
        self.assertIsNone(result)

    def test_display_id_format(self):
        display = self.svc.get_anon_id_for_display("CL-001")
        self.assertTrue(display.startswith("ANON-"))

    def test_hash_pii_field(self):
        h1 = self.svc.hash_pii_field("Minister of Health")
        h2 = self.svc.hash_pii_field("Minister of Health")
        self.assertEqual(h1, h2)

    def test_multiple_patients(self):
        """Multiple patients get distinct pseudonyms."""
        ids = ["CL-001", "CL-002", "CL-003", "CL-PRES-001"]
        anons = [self.svc.pseudonymize(pid) for pid in ids]
        self.assertEqual(len(set(anons)), 4)  # All unique

    def test_get_all_mappings(self):
        # NOTE: the service loads previously-persisted mappings from the shared
        # patient_pseudonyms table on first use, so get_all_mappings() is not
        # guaranteed to be empty at the start of the test. Assert the contract we
        # actually care about — both new mappings are present and correct — rather
        # than an exact global count that depends on cross-test SQL state.
        a1 = self.svc.pseudonymize("CL-001")
        a2 = self.svc.pseudonymize("CL-002")
        mappings = self.svc.get_all_mappings()
        self.assertEqual(mappings.get("CL-001"), a1)
        self.assertEqual(mappings.get("CL-002"), a2)


class TestPseudonymizationServiceSingleton(unittest.TestCase):
    """Test singleton behavior."""

    def setUp(self):
        reset_pseudonymization_service()

    def tearDown(self):
        reset_pseudonymization_service()

    def test_singleton_returns_same_instance(self):
        s1 = get_pseudonymization_service()
        s2 = get_pseudonymization_service()
        self.assertIs(s1, s2)

    def test_reset_creates_new_instance(self):
        s1 = get_pseudonymization_service()
        reset_pseudonymization_service()
        s2 = get_pseudonymization_service()
        self.assertIsNot(s1, s2)


class TestPseudonymizationAPI(unittest.TestCase):
    """Test the FastAPI pseudonymization endpoints."""

    @classmethod
    def setUpClass(cls):
        """Set up test environment."""
        cls._tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(cls._tmpdir, "test_pseudo_vault.db")

        os.environ["TESTING"] = "true"
        os.environ["ENVIRONMENT"] = "test"
        os.environ["VHV_DEMO_MODE"] = "true"

        reset_pseudonymization_service()

        import database.sql_db as _sql_mod
        _sql_mod.DEFAULT_SQLITE_PATH = db_path
        from database.sql_db import SQLDatabaseManager
        _bootstrap = SQLDatabaseManager()
        _bootstrap.seed_default_users()
        _sql_mod.default_sql_db = _bootstrap
        import infrastructure.repositories.sql_repositories as _sql_repo_mod
        _sql_repo_mod.default_sql_db = _bootstrap

        from backend.main import app
        from fastapi.testclient import TestClient
        cls.client = TestClient(app, raise_server_exceptions=False)

        # Login as admin
        resp = cls.client.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "Admin@2026Secure!"
        })
        cls.admin_token = resp.json().get("access_token", "") if resp.status_code == 200 else ""

        # Login as the demo practitioner: pseudonyms are for admins only
        resp = cls.client.post("/api/v1/auth/login", json={
            "username": "psk.elif",
            "password": "Practitioner@2026!"
        })
        cls.client_token = resp.json().get("access_token", "") if resp.status_code == 200 else ""

    def _admin_headers(self):
        return {"Authorization": f"Bearer {self.admin_token}"}

    def _client_headers(self):
        return {"Authorization": f"Bearer {self.client_token}"}

    def test_generate_pseudonym_as_admin(self):
        resp = self.client.post(
            "/api/v1/pseudonym/generate",
            json={"patient_id": "CL-001"},
            headers=self._admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("anon_id", data)
        self.assertIn("display_id", data)
        self.assertEqual(len(data["anon_id"]), 64)
        self.assertTrue(data["display_id"].startswith("ANON-"))

    def test_generate_pseudonym_denied_for_practitioner(self):
        resp = self.client.post(
            "/api/v1/pseudonym/generate",
            json={"patient_id": "CL-001"},
            headers=self._client_headers(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_resolve_pseudonym_admin_only(self):
        # First generate
        gen_resp = self.client.post(
            "/api/v1/pseudonym/generate",
            json={"patient_id": "CL-001"},
            headers=self._admin_headers(),
        )
        anon_id = gen_resp.json()["anon_id"]

        # Resolve as admin
        resp = self.client.post(
            "/api/v1/pseudonym/resolve",
            json={"anon_id": anon_id},
            headers=self._admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["patient_id"], "CL-001")

    def test_resolve_pseudonym_denied_for_practitioner(self):
        resp = self.client.post(
            "/api/v1/pseudonym/resolve",
            json={"anon_id": "some-anon-id"},
            headers=self._client_headers(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_list_mappings_admin_only(self):
        resp = self.client.get(
            "/api/v1/pseudonym/mappings",
            headers=self._admin_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("count", data)
        self.assertIn("mappings", data)

    def test_list_mappings_denied_for_practitioner(self):
        resp = self.client.get(
            "/api/v1/pseudonym/mappings",
            headers=self._client_headers(),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
