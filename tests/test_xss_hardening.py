import os
import sys
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app

class TestXSSHardening(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)

    def test_http_security_headers_response(self):
        res = self.client.get("/api/v1/health")
        self.assertEqual(res.status_code, 200)
        self.assertIn("X-XSS-Protection", res.headers)
        self.assertEqual(res.headers["X-XSS-Protection"], "1; mode=block")
        self.assertIn("X-Content-Type-Options", res.headers)
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("X-Frame-Options", res.headers)
        self.assertEqual(res.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("Content-Security-Policy", res.headers)
        csp = res.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)

    def test_header_injection_xss_sanitization(self):
        headers = {
            "True-Client-IP": "127.0.0.1<script>alert('header_xss')</script>",
            "User-Agent": "<img src=x onerror=alert('agent')>"
        }
        res = self.client.get("/api/v1/health", headers=headers)
        self.assertEqual(res.status_code, 200)

if __name__ == "__main__":
    unittest.main()


class TestClinicalTextFidelity(unittest.TestCase):
    """
    A record is a clinical document: what was written must come back unchanged.

    Escaping used to happen on the way in - twice, in fact - so "Dr. Smith & Co"
    was stored as "Dr. Smith &amp;amp; Co" and displayed that way for ever in an
    append-only chain. Escaping now happens where the value is rendered.
    """

    @classmethod
    def setUpClass(cls):
        from database.sql_db import default_sql_db
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        res = self.client.post("/api/v1/auth/login",
                               json={"username": "psk.elif", "password": "Practitioner@2026!"})
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def test_special_characters_round_trip_unchanged(self):
        doctor = "Dr. Smith & Co"
        institution = "A<B Kliniği"
        task = "Rate anxiety <5 & note triggers"

        res = self.client.post("/api/v1/records", headers=self.headers, json={
            "patient_id": "CL-001",
            "record_type": "homework",
            "title": "Fidelity check",
            "doctor_name": doctor,
            "institution": institution,
            "record_date": "2026-08-18",
            "is_confidential": False,
            "data": {"task": task, "due_date": "2026-08-25"},
            "notes": "",
        })
        self.assertEqual(res.status_code, 200, res.text)

        records = self.client.get("/api/v1/records/CL-001", headers=self.headers).json()["records"]
        stored = next(r for r in records if r["title"] == "Fidelity check")
        self.assertEqual(stored["doctor_name"], doctor)
        self.assertEqual(stored["institution"], institution)
        self.assertEqual(stored["data"]["task"], task)

    def test_new_records_carry_no_entity_encoding(self):
        """
        Scoped to records written now: blocks committed under the old
        escape-on-input behaviour keep their mangled text, because the chain is
        append-only and rewriting history is the one thing it must not allow.
        """
        title = "Entity check"
        self.client.post("/api/v1/records", headers=self.headers, json={
            "patient_id": "CL-001",
            "record_type": "treatment_plan",
            "title": title,
            "doctor_name": "Prof. Müller & Sons",
            "institution": "Ünite <A>",
            "record_date": "2026-08-18",
            "is_confidential": False,
            "data": {"goals": "Sleep & mood", "approach": "CBT", "planned_sessions": 10},
            "notes": "",
        })

        records = self.client.get("/api/v1/records/CL-001", headers=self.headers).json()["records"]
        stored = str(next(r for r in records if r["title"] == title))
        self.assertNotIn("&amp;", stored)
        self.assertNotIn("&lt;", stored)
        self.assertIn("Müller & Sons", stored)
