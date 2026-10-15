"""
tests/test_record_list_cost.py — the record list does the same work for 5 records or 500
========================================================================================
GET /records/{client} loaded the chain from LMDB five times, read every block's
password hash in its own read transaction, and opened a SQL connection per block
to fetch the client's erasure key. A file of 150 records cost 1505 hash reads and
150 SQL connections. The work is now done once per request: the chain is loaded
once, the hashes are read in one transaction and the erasure key is fetched once.

Nothing is cached across requests: once a client is erased, the next list can no
longer read their records.
"""

import os
import sys
import unittest
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
import database.storage as storage
from core.cqrs.queries import GetPatientRecordsQuery, QueryHandler
from core.services import client_registry
from core.services.erasure_service import ErasureKeyStore, get_erasure_key_store
from core.services.record_service import RecordService
from database.sql_db import default_sql_db
from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
from tests.test_appointments import _sql

PRACTITIONER = "psk.elif"
PASSWORD = "Practitioner@2026!"
LOCK = "LockedRecord@2026!"


class _Counter:
    """Counts calls to the expensive reads while one list request runs."""

    def __init__(self, patient_id):
        self.patient_id = patient_id
        self.chain_loads = 0
        self.hash_reads = 0
        self.key_fetches = 0

    def __enter__(self):
        load_all_blocks = LMDBBlockRepository.load_all_blocks
        load_hash = storage.load_block_pwd_hash
        load_hashes = getattr(storage, "load_block_pwd_hashes", None)
        get_key = ErasureKeyStore.get

        def spy_load_all_blocks(repo, project_name):
            self.chain_loads += 1
            return load_all_blocks(repo, project_name)

        def spy_load_hash(*args, **kwargs):
            self.hash_reads += 1
            return load_hash(*args, **kwargs)

        def spy_load_hashes(*args, **kwargs):
            self.hash_reads += 1
            return load_hashes(*args, **kwargs)

        def spy_get_key(store, patient_id):
            if patient_id == self.patient_id:
                self.key_fetches += 1
            return get_key(store, patient_id)

        self._patches = [
            mock.patch.object(LMDBBlockRepository, "load_all_blocks", spy_load_all_blocks),
            mock.patch.object(storage, "load_block_pwd_hash", spy_load_hash),
            mock.patch.object(ErasureKeyStore, "get", spy_get_key),
        ]
        if load_hashes is not None:
            self._patches.append(mock.patch.object(storage, "load_block_pwd_hashes", spy_load_hashes))
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestRecordListCost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        default_sql_db.seed_default_users()
        cls.client = TestClient(app)
        res = cls.client.post("/api/v1/auth/login", json={"username": PRACTITIONER, "password": PASSWORD})
        assert res.status_code == 200, res.text
        cls.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.cards = []
        self.svc = RecordService(LMDBBlockRepository(), AESGCMStrategy())

    def tearDown(self):
        for patient_id in self.cards:
            get_erasure_key_store().destroy(patient_id)
            storage.reset_db(self.svc._get_project_name(patient_id))
            _sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))

    def _new_client(self, records):
        """A client with `records` session notes: every third one locked with a
        password, one corrected, and one old private journal entry the
        practitioner may not see."""
        card = client_registry.add(practitioner=PRACTITIONER, full_name="List Cost", phone=None,
                                   email=None, kvkk_signed_on=None, created_by="test")
        patient_id = card["patient_id"]
        self.cards.append(patient_id)
        for n in range(records):
            data = {"record_type": "session_note", "title": f"Session {n}",
                    "access_level": "practitioner_only", "data": {"note": f"NOTE-{n}"}}
            if n % 3 == 2:
                self.svc.add_record(patient_id, data, is_protected=True,
                                    protection_password=LOCK, username=PRACTITIONER)
            else:
                self.svc.add_record(patient_id, data, username=PRACTITIONER)
        self.svc.add_correction_block(patient_id, 1, {
            "record_type": "session_note", "title": "Session 0 (corrected)",
            "access_level": "practitioner_only", "data": {"note": "NOTE-0-FIXED"}},
            username=PRACTITIONER, reason="typo")
        self.svc.add_record(patient_id, {"record_type": "journal", "title": "Private",
                                         "access_level": "private", "data": {"note": "PRIVATE"}},
                            username=PRACTITIONER)
        return patient_id

    def _list(self, patient_id):
        res = self.client.get(f"/api/v1/records/{patient_id}", headers=self.headers)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_list_does_a_fixed_amount_of_work_per_request(self):
        for records in (4, 12):
            with self.subTest(records=records):
                patient_id = self._new_client(records)
                with _Counter(patient_id) as cost:
                    self._list(patient_id)
                self.assertEqual(cost.chain_loads, 1, "the chain is loaded once")
                self.assertEqual(cost.hash_reads, 1, "password hashes are read in one transaction")
                self.assertEqual(cost.key_fetches, 1, "the erasure key is fetched once")

    def test_list_matches_the_record_by_record_reads(self):
        patient_id = self._new_client(6)
        body = self._list(patient_id)

        # The same answer the query and the chain checks give on their own.
        handler = QueryHandler(self.svc, self.svc.block_repo)
        expected = handler.handle_get_patient_records(
            GetPatientRecordsQuery(patient_id, PRACTITIONER, "practitioner"))
        expected.sort(key=lambda x: x["timestamp"], reverse=True)
        self.assertEqual(body["records"], expected)
        self.assertEqual(body["total_blocks"], len(self.svc.get_chain(patient_id)))
        self.assertTrue(body["chain_valid"])
        self.assertTrue(self.svc.is_chain_valid(patient_id))

        by_index = {r["block_index"]: r for r in body["records"]}
        titles = sorted(r["title"] for r in body["records"])
        # Six records, the private journal left out.
        self.assertEqual(len(body["records"]), 6)
        self.assertNotIn("Private", titles)
        # The correction is applied and its provenance shown.
        self.assertEqual(by_index[1]["title"], "Session 0 (corrected)")
        self.assertTrue(by_index[1]["is_corrected"])
        self.assertEqual(by_index[1]["correction"]["reason"], "typo")
        # Locked records stay locked.
        locked = [r for r in body["records"] if r["is_protected"]]
        self.assertEqual(len(locked), 2)
        for r in locked:
            self.assertEqual(r["title"], "ENCRYPTED RECORD")
            self.assertIsNone(r["data"])

    def test_erasure_is_seen_by_the_very_next_list(self):
        patient_id = self._new_client(3)
        self.assertIn("NOTE-1", str(self._list(patient_id)["records"]))
        get_erasure_key_store().destroy(patient_id)
        body = self._list(patient_id)
        self.assertNotIn("NOTE-", str(body["records"]))
        self.assertTrue(body["chain_valid"])


if __name__ == "__main__":
    unittest.main()
