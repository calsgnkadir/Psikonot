"""
tests/test_key_derivation_reuse.py — a file opens with one key derivation
=========================================================================
Every block of one client's file is encrypted under the same key, derived with
600,000 PBKDF2 iterations. That derivation used to run again for every block,
so a file of 300 records took about 40 seconds to open. A request now derives
the key once and reuses it.
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cqrs.commands import AddRecordCommand, CommandHandler
from core.security import get_kms
from core.services.record_service import RecordService
from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository

RECORDS = 12


def _new_file(n=RECORDS):
    """A new client file with n records; returns its client ID."""
    patient_id = f"CL-K{uuid.uuid4().hex[:8]}"
    repo = LMDBBlockRepository()
    writer = CommandHandler(RecordService(repo, AESGCMStrategy()), None, repo)
    for i in range(n):
        writer.handle_add_record(AddRecordCommand(
            patient_id=patient_id, username="psk.test",
            data={"record_type": "session_note", "title": f"Note {i}", "data": {}}))
    return patient_id


class TestKeyDerivationReuse(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"

    def _read(self, patient_id):
        """Read the whole file as a new request would, counting key derivations."""
        reader = RecordService(LMDBBlockRepository(), AESGCMStrategy())
        kms = get_kms()
        with mock.patch.object(kms, "derive_key", wraps=kms.derive_key) as derive:
            data = reader.get_final_data(patient_id)
        return data, derive.call_count

    def test_reading_a_file_derives_the_key_once(self):
        data, derivations = self._read(_new_file())
        titles = [v["title"] for v in data.values() if isinstance(v, dict) and "title" in v]
        self.assertEqual(len(titles), RECORDS)
        self.assertEqual(derivations, 1)

    def test_each_request_derives_its_own(self):
        # Keys are not shared between requests: after an erasure no derived key
        # of that client may linger in memory.
        patient_id = _new_file(2)
        self.assertEqual(self._read(patient_id)[1], 1)
        self.assertEqual(self._read(patient_id)[1], 1)

    def test_a_wrong_password_still_fails(self):
        strategy = AESGCMStrategy()
        salt = os.urandom(32)
        ciphertext, _ = strategy.encrypt_data("session text", "right-secret", salt)
        self.assertEqual(strategy.decrypt_data(ciphertext, "right-secret", salt), "session text")
        with self.assertRaises(ValueError):
            strategy.decrypt_data(ciphertext, "wrong-secret", salt)
        with self.assertRaises(ValueError):
            strategy.decrypt_data(ciphertext, "right-secret", os.urandom(32))

    def test_old_ciphertext_still_opens(self):
        # The stored format did not change: what the KMS wrote before opens now.
        salt = os.urandom(32)
        ciphertext, _ = get_kms().encrypt("written before", "secret", salt)
        self.assertEqual(AESGCMStrategy().decrypt_data(ciphertext, "secret", salt), "written before")

    def test_every_write_gets_a_fresh_nonce(self):
        strategy = AESGCMStrategy()
        salt = os.urandom(32)
        first, _ = strategy.encrypt_data("same text", "secret", salt)
        second, _ = strategy.encrypt_data("same text", "secret", salt)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
