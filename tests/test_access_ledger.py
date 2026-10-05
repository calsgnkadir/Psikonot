"""
tests/test_access_ledger.py — the access trail is tamper-evident
===============================================================
Who read (or attempted to read) a client's record is the claim this vault exists to
defend. The access log is therefore a hash-linked ledger, not a flat table:
deleting or altering any entry breaks the chain. The client's practitioner can read
the trail of their client's file and its integrity verdict.
"""

import json
import os
import shutil
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.connection import LMDBConnectionManager, active_txn, active_project
import database.audit_storage as audit_storage
from database.sql_db import default_sql_db

PROJECT = "patient_ACCESS_LEDGER_TEST"


class TestAccessLedgerIntegrity(unittest.TestCase):
    def setUp(self):
        # Isolate from any transaction context a preceding test may have left set,
        # and use a private store so the shared default chain cannot interfere.
        active_txn.set(None)
        active_project.set(None)
        self.base = os.path.join(os.path.dirname(__file__), "test_access_projects")
        shutil.rmtree(self.base, ignore_errors=True)
        self.mgr = LMDBConnectionManager(self.base)

    def tearDown(self):
        self.mgr.close_all()
        shutil.rmtree(self.base, ignore_errors=True)

    def _append(self, n):
        for i in range(n):
            audit_storage.append_access_log(
                PROJECT, f"dr.user{i}", "RECORD_DECRYPTED", "dev",
                {"block_index": i}, db_manager=self.mgr,
            )

    def _entries(self):
        env = self.mgr.open_db(PROJECT)
        rows = []
        with env.begin(write=False) as txn:
            prefix = f"access_log_{PROJECT}_".encode("utf-8")
            for key, value in txn.cursor():
                if key.startswith(prefix):
                    rows.append((key, json.loads(value.decode("utf-8"))))
        rows.sort(key=lambda kv: kv[1].get("seq", 0))
        return rows

    def test_fresh_ledger_is_hash_linked_and_valid(self):
        self._append(5)
        v = audit_storage.verify_access_log_integrity(PROJECT, db_manager=self.mgr)
        self.assertTrue(v["valid"])
        self.assertEqual(v["count"], 5)

        entries = [e for _, e in self._entries()]
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3, 4, 5])
        self.assertEqual(entries[0]["prev_hash"], "")
        for prev, cur in zip(entries, entries[1:]):
            self.assertEqual(cur["prev_hash"], prev["hash"])

    def test_altering_an_entry_is_detected(self):
        self._append(4)
        env = self.mgr.open_db(PROJECT)
        with env.begin(write=True) as txn:
            key, entry = self._entries()[1]
            entry["username"] = "attacker"
            txn.put(key, json.dumps(entry, ensure_ascii=False).encode("utf-8"))

        v = audit_storage.verify_access_log_integrity(PROJECT, db_manager=self.mgr)
        self.assertFalse(v["valid"])
        self.assertEqual(v["broken_at"], 2)

    def test_deleting_an_entry_is_detected(self):
        self._append(4)
        env = self.mgr.open_db(PROJECT)
        with env.begin(write=True) as txn:
            key, _ = self._entries()[2]
            txn.delete(key)

        v = audit_storage.verify_access_log_integrity(PROJECT, db_manager=self.mgr)
        self.assertFalse(v["valid"])

    def test_empty_ledger_is_trivially_valid(self):
        v = audit_storage.verify_access_log_integrity("patient_CL_NONE", db_manager=self.mgr)
        self.assertTrue(v["valid"])
        self.assertEqual(v["count"], 0)


class TestAccessLedgerEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)

    def _token(self, username, password):
        res = self.client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["access_token"]

    def test_practitioner_reads_their_clients_access_log_with_integrity(self):
        token = self._token("psk.elif", "Practitioner@2026!")
        res = self.client.get("/api/v1/blockchain/CL-001/access-logs",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        # The endpoint must always report an integrity verdict for the trail. The
        # strict valid/tamper behaviour is covered by the isolated unit tests
        # above; CL-001's ledger is shared across the API test classes here.
        self.assertIn("integrity", body)
        for field in ("valid", "count", "broken_at"):
            self.assertIn(field, body["integrity"])
        self.assertIsInstance(body["integrity"]["valid"], bool)

    def test_practitioner_cannot_read_another_clients_access_log(self):
        token = self._token("psk.elif", "Practitioner@2026!")
        res = self.client.get("/api/v1/blockchain/CL-777/access-logs",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 403)

    def test_the_secretary_reads_no_access_log(self):
        token = self._token("secretary.ayse", "Secretary@2026!")
        res = self.client.get("/api/v1/blockchain/CL-001/access-logs",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 403)

    def test_every_read_of_the_file_is_recorded(self):
        doctor = self._token("psk.elif", "Practitioner@2026!")
        self.client.get("/api/v1/records/CL-001", headers={"Authorization": f"Bearer {doctor}"})
        logs = self.client.get("/api/v1/blockchain/CL-001/access-logs",
                               headers={"Authorization": f"Bearer {doctor}"}).json()["logs"]
        actions = [(entry.get("username"), entry.get("action")) for entry in logs]
        self.assertIn(("psk.elif", "RECORDS_VIEWED"), actions)


if __name__ == "__main__":
    unittest.main()
