"""
tests/test_appointments.py — the appointment book and the secretary role
========================================================================
A practitioner and their secretary run one appointment book for the
practitioner's clients. The secretary sees names, IDs and times — and is
refused by every record endpoint.
"""

import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import default_sql_db

CLIENT_ID = "CL-001"
PRACTITIONER = "psk.elif"
STRONG = "Enrolled@2026Secure!"
ACCOUNTS = {
    "practitioner": (PRACTITIONER, "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
}


def _sql(statement, params=()):
    conn = default_sql_db.get_connection()
    cur = conn.cursor()
    ph = "%s" if default_sql_db.is_postgres else "?"
    try:
        cur.execute(statement.replace("?", ph), params)
        conn.commit()
    finally:
        cur.close()
        conn.close()


class TestAppointments(unittest.TestCase):
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

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.booked, self.users = [], []
        # A random future day, so runs never collide with each other.
        self.base = (datetime.now(timezone.utc) + timedelta(days=random.randint(30, 3000))).replace(
            hour=9, minute=0, second=0, microsecond=0)

    def tearDown(self):
        for appointment_id in self.booked:
            _sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for username in self.users:
            _sql("DELETE FROM users WHERE username = ?", (username,))
            _sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))
            _sql("DELETE FROM practice_staff WHERE staff_username = ?", (username,))

    # ── helpers ──
    def _book(self, actor="practitioner", at=None, minutes=50, patient_id=CLIENT_ID, headers=None):
        at = at or self.base
        res = self.client.post("/api/v1/appointments", headers=headers or self.headers[actor], json={
            "patient_id": patient_id, "starts_at": at.isoformat(),
            "duration_min": minutes, "session_format": "Online"})
        if res.status_code == 200:
            self.booked.append(res.json()["appointment"]["id"])
        return res

    def _list(self, actor, headers=None):
        start = (self.base - timedelta(days=1)).isoformat()
        end = (self.base + timedelta(days=2)).isoformat()
        res = self.client.get("/api/v1/appointments", headers=headers or self.headers[actor],
                              params={"start": start, "end": end})
        self.assertEqual(res.status_code, 200, res.text)
        return {a["id"]: a for a in res.json()["appointments"]}

    def _patch(self, actor, appointment_id, headers=None, **body):
        return self.client.patch(f"/api/v1/appointments/{appointment_id}",
                                 headers=headers or self.headers[actor], json=body)

    def _new_account(self, username, role):
        """Provision and activate an account; returns its auth headers."""
        self.users.append(username)
        res = self.client.post("/api/v1/onboarding/provision", headers=self.headers["admin"], json={
            "username": username, "full_name": "Test " + role, "role": role})
        self.assertEqual(res.status_code, 200, res.text)
        self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["enrollment_token"], "new_password": STRONG})
        login = self.client.post("/api/v1/auth/login", json={"username": username, "password": STRONG})
        self.assertEqual(login.status_code, 200, login.text)
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    # ── booking ──
    def test_practitioner_books_and_secretary_sees_it(self):
        res = self._book()
        self.assertEqual(res.status_code, 200, res.text)
        appointment = res.json()["appointment"]
        self.assertEqual(appointment["status"], "scheduled")
        self.assertEqual(appointment["client_name"], "Ahmet Karataş")
        self.assertIn(appointment["id"], self._list("secretary"))

    def test_secretary_can_book_for_the_practitioner(self):
        res = self._book("secretary")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(res.json()["appointment"]["id"], self._list("practitioner"))

    def test_no_double_booking(self):
        self.assertEqual(self._book().status_code, 200)
        self.assertEqual(self._book(at=self.base + timedelta(minutes=30)).status_code, 409)
        # Back to back is fine.
        self.assertEqual(self._book(at=self.base + timedelta(minutes=50)).status_code, 200)

    def test_cancelling_frees_the_slot(self):
        appointment_id = self._book().json()["appointment"]["id"]
        self.assertEqual(self._patch("practitioner", appointment_id, status="cancelled").status_code, 200)
        self.assertEqual(self._book().status_code, 200)

    def test_bad_requests(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(self._book(at=past).status_code, 422)
        self.assertEqual(self._book(minutes=5).status_code, 422)
        naive = self.client.post("/api/v1/appointments", headers=self.headers["practitioner"], json={
            "patient_id": CLIENT_ID, "starts_at": "2030-01-01T10:00:00", "duration_min": 50})
        self.assertEqual(naive.status_code, 422)

    def test_only_the_practitioners_clients(self):
        self.assertEqual(self._book(patient_id="CL-9999").status_code, 404)

    def test_reschedule_and_status_rules(self):
        appointment_id = self._book().json()["appointment"]["id"]
        moved = self._patch("secretary", appointment_id, starts_at=(self.base + timedelta(hours=3)).isoformat())
        self.assertEqual(moved.status_code, 200, moved.text)
        # It has not started yet, so it cannot be marked completed.
        self.assertEqual(self._patch("practitioner", appointment_id, status="completed").status_code, 409)

    def test_attended_and_no_show(self):
        past = datetime.now(timezone.utc) - timedelta(days=random.randint(2, 300))
        from core.services import appointment_book
        appointment_id = appointment_book.add_existing(
            practitioner=PRACTITIONER, patient_id=CLIENT_ID, starts_at=past.timestamp(), duration_min=50,
            session_format="In-person", status="scheduled", created_by="test")
        self.booked.append(appointment_id)
        self.assertEqual(self._patch("secretary", appointment_id, status="no_show").status_code, 200)
        # Once closed, it stays closed.
        self.assertEqual(self._patch("practitioner", appointment_id, status="completed").status_code, 409)

    # ── the secretary sees no records ──
    def test_secretary_is_refused_by_every_record_endpoint(self):
        for path in (f"/api/v1/records/{CLIENT_ID}",
                     f"/api/v1/records/{CLIENT_ID}/1",
                     f"/api/v1/records/proof/{CLIENT_ID}/1",
                     f"/api/v1/records/offchain/download/{CLIENT_ID}/1",
                     f"/api/v1/blockchain/{CLIENT_ID}/status",
                     f"/api/v1/blockchain/{CLIENT_ID}/access-logs",
                     f"/api/v1/kvkk/export/{CLIENT_ID}",
                     "/api/v1/kvkk/erasure-requests"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.headers["secretary"]).status_code, 403)
        res = self.client.post(f"/api/v1/records/{CLIENT_ID}/1/decrypt", headers=self.headers["secretary"],
                               json={"password": "anything-at-all"})
        self.assertEqual(res.status_code, 403)
        res = self.client.post("/api/v1/records", headers=self.headers["secretary"], json={
            "patient_id": CLIENT_ID, "record_type": "other", "title": "Secretary note",
            "doctor_name": "-", "institution": "-", "record_date": "2026-09-01", "data": {}})
        self.assertEqual(res.status_code, 403)

    # ── other books stay closed ──
    def test_another_practitioner_cannot_see_or_touch_the_book(self):
        appointment_id = self._book().json()["appointment"]["id"]
        other = self._new_account(f"psk.other{random.randint(1000, 9999)}", "practitioner")
        self.assertNotIn(appointment_id, self._list("x", headers=other))
        self.assertEqual(self._patch("x", appointment_id, headers=other, status="cancelled").status_code, 404)
        # ...nor book one of this practitioner's clients.
        self.assertEqual(self._book(headers=other).status_code, 404)

    def test_operators_have_no_book(self):
        res = self.client.get("/api/v1/appointments", headers=self.headers["admin"])
        self.assertEqual(res.status_code, 403)

    # ── inviting a secretary ──
    def test_practitioner_invites_a_secretary_who_runs_their_book(self):
        username = f"sec.test{random.randint(1000, 9999)}"
        self.users.append(username)
        res = self.client.post("/api/v1/onboarding/invite-secretary", headers=self.headers["practitioner"],
                               json={"username": username, "full_name": "New Secretary"})
        self.assertEqual(res.status_code, 200, res.text)
        staff = {s["username"]: s for s in self.client.get(
            "/api/v1/onboarding/staff", headers=self.headers["practitioner"]).json()["staff"]}
        self.assertEqual(staff[username]["status"], "pending")

        self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["invite_code"], "new_password": STRONG})
        login = self.client.post("/api/v1/auth/login", json={"username": username, "password": STRONG})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["user"]["role"], "secretary")
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        appointment_id = self._book().json()["appointment"]["id"]
        self.assertIn(appointment_id, self._list("x", headers=headers))
        self.assertEqual(self.client.get(f"/api/v1/records/{CLIENT_ID}", headers=headers).status_code, 403)

    def test_only_practitioners_invite_secretaries(self):
        for actor in ("secretary", "admin"):
            res = self.client.post("/api/v1/onboarding/invite-secretary", headers=self.headers[actor],
                                   json={"username": "sec.nobody", "full_name": "Nobody"})
            with self.subTest(actor=actor):
                self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
