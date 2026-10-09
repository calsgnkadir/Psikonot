"""
tests/test_confidential_first_record.py — a new client's first confidential record
================================================================================
The first write for a client mints its encryption salt inside the unit of work.
A reader that opens its own transaction cannot see that uncommitted salt, so the
salt used to be minted twice and the record could never be opened again.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.storage as storage
from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
from core.services.record_service import RecordService
from core.cqrs.commands import AddRecordCommand, CommandHandler

PATIENT_ID = "CL-CONF-FIRST-1"
PASSWORD = "SuperSecret123!@#"


class TestConfidentialFirstRecord(unittest.TestCase):
    def setUp(self):
        # LMDBUnitOfWork transacts against the default connection manager.
        self.block_repo = LMDBBlockRepository()
        self.record_service = RecordService(self.block_repo, AESGCMStrategy())
        self.handler = CommandHandler(self.record_service, None, self.block_repo)
        self.project = self.record_service._get_project_name(PATIENT_ID)
        storage.reset_db(self.project)

    def tearDown(self):
        storage.reset_db(self.project)

    def _add(self, data):
        return self.handler.handle_add_record(AddRecordCommand(
            patient_id=PATIENT_ID,
            data=data,
            is_protected=True,
            protection_password=PASSWORD,
            username="dr.conf",
        ))

    def test_first_confidential_record_opens_with_its_password(self):
        data = {"record_type": "session_note", "title": "First session", "data": {"note": "private"}}
        block = self._add(data)
        result = self.record_service.get_block_data(PATIENT_ID, block.index, PASSWORD, "dr.conf")
        self.assertEqual(result, data)

    def test_second_confidential_record_still_opens(self):
        self._add({"record_type": "session_note", "title": "First", "data": {"note": "a"}})
        data = {"record_type": "session_note", "title": "Second", "data": {"note": "b"}}
        block = self._add(data)
        result = self.record_service.get_block_data(PATIENT_ID, block.index, PASSWORD, "dr.conf")
        self.assertEqual(result, data)


if __name__ == "__main__":
    unittest.main()
