"""
tests/test_access_policy.py — the access policy on its own
==========================================================
core/services/access_policy.py is a set of pure functions, so every rule can
be checked here without a database, a server or a login.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.access_policy import can_open_file, can_view, can_view_stored

ME = "psk.elif"
OTHER = "psk.other"


def record(level="practitioner_only", record_type="session_note"):
    return {"access_level": level, "record_type": record_type, "created_by": ME}


class TestCanOpenFile(unittest.TestCase):
    def test_a_practitioner_opens_only_their_own_clients_file(self):
        self.assertTrue(can_open_file("practitioner", ME, ME))
        self.assertFalse(can_open_file("practitioner", OTHER, ME))

    def test_a_missing_client_looks_like_someone_elses(self):
        self.assertFalse(can_open_file("practitioner", ME, None))

    def test_operators_pass_here_and_are_held_by_dual_control(self):
        for role in ("admin", "security_officer", "auditor"):
            with self.subTest(role=role):
                self.assertTrue(can_open_file(role, "x", ME))

    def test_every_other_role_is_denied(self):
        # Default deny: the secretary, an old client account, a made-up role.
        for role in ("secretary", "client", "receptionist", ""):
            with self.subTest(role=role):
                self.assertFalse(can_open_file(role, ME, ME))


class TestCanView(unittest.TestCase):
    def test_the_practitioner_sees_their_records(self):
        self.assertTrue(can_view("practitioner", record()))
        self.assertTrue(can_view("practitioner", record(record_type="session_transcript")))

    def test_a_record_without_a_level_is_practitioner_only(self):
        self.assertTrue(can_view("practitioner", {"record_type": "client_profile"}))

    def test_records_shared_in_the_old_model_stay_readable(self):
        # Written while clients still had accounts and shared records.
        self.assertTrue(can_view("practitioner", record("doctor_shared")))

    def test_a_clients_old_private_journal_stays_closed(self):
        self.assertFalse(can_view("practitioner", record("private")))

    def test_unknown_access_level_is_closed(self):
        self.assertFalse(can_view("practitioner", record("admin_only")))

    def test_operators_see_content_once_past_dual_control(self):
        for role in ("admin", "security_officer", "auditor"):
            self.assertTrue(can_view(role, record("private")))

    def test_every_other_role_sees_nothing(self):
        for role in ("secretary", "client", "receptionist"):
            with self.subTest(role=role):
                self.assertFalse(can_view(role, record()))

    def test_not_a_record(self):
        self.assertFalse(can_view("practitioner", None))
        self.assertFalse(can_view("practitioner", "a string"))


class TestCanViewStored(unittest.TestCase):
    LOCKED = "gAAAAB...ciphertext"

    def test_a_locked_block_is_listed_for_its_practitioner(self):
        self.assertTrue(can_view_stored("practitioner", self.LOCKED))

    def test_a_locked_block_is_not_shown_to_the_secretary(self):
        self.assertFalse(can_view_stored("secretary", self.LOCKED))

    def test_bookkeeping_blocks_are_for_operators(self):
        for block_type in ("genesis", "audit", "correction"):
            with self.subTest(block_type=block_type):
                self.assertFalse(can_view_stored("practitioner", {"type": block_type}))
                self.assertTrue(can_view_stored("auditor", {"type": block_type}))
                self.assertFalse(can_view_stored("secretary", {"type": block_type}))

    def test_a_stored_record_follows_can_view(self):
        self.assertTrue(can_view_stored("practitioner", record()))
        self.assertFalse(can_view_stored("practitioner", record("private")))
        self.assertFalse(can_view_stored("secretary", record()))


if __name__ == "__main__":
    unittest.main()
