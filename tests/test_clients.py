"""
tests/test_clients.py — client cards and the tally
==================================================
Clients do not sign in: each one is a card owned by a practitioner, with the
contact details the practice needs. The practitioner and their secretary both
keep the cards; nobody else sees them. The list also carries the tally — how
many sessions each client came to, missed or cancelled.
"""

import os
import random
import sys
import time
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.services import appointment_book
from database.sql_db import default_sql_db
from tests.test_appointments import _sql
from tests.test_practitioner_file_access import new_practitioner

PRACTITIONER = "psk.elif"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
}


class TestClients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TESTING"] = "true"
        default_sql_db.seed_default_users()
        cls.client = TestClient(app)
        cls.headers = {}
        for actor, (username, password) in ACCOUNTS.items():
            res = cls.client.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert res.status_code == 200, res.text
            cls.headers[actor] = {"Authorization": f"Bearer {res.json()['access_token']}"}
        cls.other_username, cls.headers["other"] = new_practitioner(cls.client, cls.headers["admin"])

    @classmethod
    def tearDownClass(cls):
        _sql("DELETE FROM users WHERE username = ?", (cls.other_username,))
        _sql("DELETE FROM enrollment_tokens WHERE username = ?", (cls.other_username,))

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.cards, self.appointments = [], []

    def tearDown(self):
        for appointment_id in self.appointments:
            _sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for patient_id in self.cards:
            _sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))

    def _add(self, actor="practitioner", **body):
        body.setdefault("full_name", "Deniz Aydın")
        res = self.client.post("/api/v1/clients", headers=self.headers[actor], json=body)
        if res.status_code == 200:
            self.cards.append(res.json()["client"]["patient_id"])
        return res

    def _list(self, actor="practitioner"):
        res = self.client.get("/api/v1/clients", headers=self.headers[actor])
        self.assertEqual(res.status_code, 200, res.text)
        return {c["patient_id"]: c for c in res.json()["clients"]}

    # ── cards ──
    def test_practitioner_adds_a_client_with_the_next_free_id(self):
        res = self._add(phone="+90 532 111 22 33", email="deniz@example.com", kvkk_signed_on="2026-09-01")
        self.assertEqual(res.status_code, 200, res.text)
        card = res.json()["client"]
        self.assertRegex(card["patient_id"], r"^CL-\d{3,}$")
        self.assertNotIn(card["patient_id"], ("CL-001", "CL-002", "CL-003"))
        self.assertEqual((card["phone"], card["email"], card["kvkk_signed_on"]),
                         ("+90 532 111 22 33", "deniz@example.com", "2026-09-01"))
        self.assertIn(card["patient_id"], self._list())

    def test_an_id_with_a_chain_is_never_handed_out_again(self):
        # An erased client's card is gone, but their (undecryptable) chain is
        # still there: that number must not go to a new person.
        from unittest import mock
        from core.pseudonymization.service import project_name_for
        from core.services import client_registry
        free = client_registry._next_client_id()
        left_behind = {project_name_for(free)}
        real = client_registry.storage.project_exists
        with mock.patch.object(client_registry.storage, "project_exists",
                               side_effect=lambda name: name in left_behind or real(name)):
            self.assertNotEqual(client_registry._next_client_id(), free)

    def test_secretary_adds_and_updates_cards_for_the_practitioner(self):
        res = self._add("secretary", phone="0212 555 00 00")
        self.assertEqual(res.status_code, 200, res.text)
        patient_id = res.json()["client"]["patient_id"]
        self.assertIn(patient_id, self._list("practitioner"))
        res = self.client.patch(f"/api/v1/clients/{patient_id}", headers=self.headers["secretary"],
                                json={"phone": "0212 555 00 01"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["client"]["phone"], "0212 555 00 01")
        self.assertEqual(res.json()["client"]["full_name"], "Deniz Aydın")   # untouched

    def test_the_secretary_sees_contact_details_only(self):
        card = self._list("secretary")["CL-001"]
        self.assertEqual(card["full_name"], "Ahmet Karataş")
        self.assertIn("phone", card)
        for clinical in ("records", "presenting_problem", "notes", "summary"):
            self.assertNotIn(clinical, card)

    def test_bad_input(self):
        for body in ({"full_name": "x"}, {"phone": "call me"}, {"email": "not-an-email"},
                     {"kvkk_signed_on": "01.09.2026"}, {"kvkk_signed_on": "2999-01-01"}):
            with self.subTest(body=body):
                self.assertEqual(self._add(**body).status_code, 422)
        res = self.client.patch("/api/v1/clients/CL-001", headers=self.headers["practitioner"],
                                json={"full_name": None})
        self.assertEqual(res.status_code, 422)

    # ── nobody else ──
    def test_another_practitioner_sees_none_of_them(self):
        patient_id = self._add().json()["client"]["patient_id"]
        self.assertNotIn(patient_id, self._list("other"))
        self.assertNotIn("CL-001", self._list("other"))
        res = self.client.patch(f"/api/v1/clients/{patient_id}", headers=self.headers["other"],
                                json={"phone": "0212 000 00 00"})
        self.assertEqual(res.status_code, 404)

    def test_operators_have_no_client_list(self):
        self.assertEqual(self.client.get("/api/v1/clients", headers=self.headers["admin"]).status_code, 403)
        self.assertEqual(self._add("admin").status_code, 403)

    # ── the tally ──
    def test_the_list_counts_who_came_and_who_did_not(self):
        patient_id = self._add().json()["client"]["patient_id"]
        day = 86400
        start = time.time() - day * random.randint(30, 300)
        for offset, status in ((0, "completed"), (7, "completed"), (14, "no_show"), (21, "cancelled")):
            self.appointments.append(appointment_book.add_existing(
                practitioner=PRACTITIONER, patient_id=patient_id, starts_at=start + offset * day,
                duration_min=50, session_format="In-person", status=status, created_by="test"))
        self.appointments.append(appointment_book.add_existing(
            practitioner=PRACTITIONER, patient_id=patient_id, starts_at=time.time() + 3 * day,
            duration_min=50, session_format="Online", status="scheduled", created_by="test"))

        card = self._list()[patient_id]
        self.assertEqual((card["attended"], card["no_show"], card["cancelled"]), (2, 1, 1))
        self.assertIsNotNone(card["last_seen_at"])
        self.assertIsNotNone(card["next_appointment_at"])

    def test_a_new_client_has_an_empty_tally(self):
        card = self._add().json()["client"]
        self.assertEqual((card["attended"], card["no_show"], card["cancelled"]), (0, 0, 0))
        self.assertIsNone(card["next_appointment_at"])


if __name__ == "__main__":
    unittest.main()
