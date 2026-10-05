"""
tests/test_record_schemas.py — the psychology record schemas
============================================================
The API checks the `data` of each record type against a schema
(backend/schemas/requests.py -> DATA_SCHEMAS). These tests pin the main rules,
and check that the bundled demo file passes the same rules the API enforces —
otherwise the demo could show data a real user could never enter.
"""

import os
import sys
import unittest

from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.schemas.requests import (
    DATA_SCHEMAS, RECORD_TYPES, ClientProfileSchema, SessionTranscriptSchema, SessionNoteSchema,
    HomeworkSchema,
    RecordCreate,
)
from backend.demo_seed import _demo_chart, _process_note, _session_transcript
from core.services.access_policy import PRACTITIONER_ONLY


class TestRecordSchemas(unittest.TestCase):
    def test_every_schema_belongs_to_a_known_type(self):
        for record_type in DATA_SCHEMAS:
            self.assertIn(record_type, RECORD_TYPES)

    def test_demo_file_passes_the_api_schemas(self):
        for record in _demo_chart() + [_process_note(), _session_transcript()]:
            schema = DATA_SCHEMAS.get(record["record_type"])
            if schema:
                schema(**record["data"])  # raises if the demo drifts from the rules
            # ...and, like every record the API writes, it is practitioner-only.
            self.assertEqual(record["access_level"], PRACTITIONER_ONLY)

    def test_scoring_is_gone(self):
        # Mahrem keeps no questionnaire scores: no "assessment" type, no score fields.
        self.assertNotIn("assessment", RECORD_TYPES)
        self.assertNotIn("assessment", DATA_SCHEMAS)

    def test_profile_needs_a_presenting_problem(self):
        with self.assertRaises(ValidationError):
            ClientProfileSchema(presenting_problem="  ")
        self.assertEqual(ClientProfileSchema(presenting_problem="Panic").characteristics, "")

    def test_transcript_needs_text_and_a_session(self):
        with self.assertRaises(ValidationError):
            SessionTranscriptSchema(session_number=1, transcript="")
        with self.assertRaises(ValidationError):
            SessionTranscriptSchema(session_number=0, transcript="T: ...")

    def test_unknown_session_format_is_rejected(self):
        with self.assertRaises(ValidationError):
            SessionNoteSchema(session_number=1, duration_min=50,
                              session_format="Phone", summary="x")

    def test_homework_due_date_may_be_in_the_future(self):
        HomeworkSchema(task="Thought record", due_date="2999-01-01")  # must not raise

    # The attachment fields end up inside <img src="data:TYPE;base64,DATA"> on the
    # client, so the server refuses anything that could break out of that attribute.
    def _record(self, **overrides):
        record = dict(patient_id="CL-001", record_type="document", title="t",
                      doctor_name="d", institution="i", record_date="2026-01-01", data={})
        record.update(overrides)
        return record

    def test_file_type_must_be_a_mime_type(self):
        with self.assertRaises(ValidationError):
            RecordCreate(**self._record(file_type='image/png" onerror="alert(1)'))

    def test_file_data_must_be_base64(self):
        with self.assertRaises(ValidationError):
            RecordCreate(**self._record(file_data='AAAA" onerror="alert(1)'))

    def test_valid_attachment_fields_pass(self):
        RecordCreate(**self._record(file_name="scan.png", file_type="image/png",
                                    file_data="iVBORw0KGgo="))  # must not raise


if __name__ == "__main__":
    unittest.main()
