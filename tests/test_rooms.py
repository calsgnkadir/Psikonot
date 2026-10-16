"""
tests/test_rooms.py — therapy rooms in the appointment book
==========================================================
Several practitioners share the centre's rooms. An in-person appointment may
carry a room; two scheduled appointments in the same room never overlap,
whoever's book they are in. Operators manage the room list (create, rename,
deactivate — never delete); everyone who books may list the rooms in use.
A room's day shows the caller's own appointments in full and other books'
appointments only as "busy".
"""

import os
import random
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.services import client_registry
from database import sql_db
from database.sql_db import default_sql_db

CLIENT_ID = "CL-001"
STRONG = "Enrolled@2026Secure!"
ACCOUNTS = {
    "practitioner": ("psk.elif", "Practitioner@2026!"),
    "secretary": ("secretary.ayse", "Secretary@2026!"),
    "admin": ("admin", "Admin@2026Secure!"),
    "security_officer": ("sec.officer", "SecOfficer@2026!"),
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


class TestRooms(unittest.TestCase):
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
        self.booked, self.users, self.rooms, self.clients = [], [], [], []
        self.base = (datetime.now(timezone.utc) + timedelta(days=random.randint(30, 3000))).replace(
            hour=9, minute=0, second=0, microsecond=0)

    def tearDown(self):
        for appointment_id in self.booked:
            _sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for room_id in self.rooms:
            _sql("DELETE FROM rooms WHERE id = ?", (room_id,))
        for patient_id in self.clients:
            _sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
        for username in self.users:
            _sql("DELETE FROM users WHERE username = ?", (username,))
            _sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))

    # ── helpers ──
    def _room(self, name=None, actor="admin"):
        name = f"Oda T{random.randint(10000, 99999)}" if name is None else name
        res = self.client.post("/api/v1/rooms", headers=self.headers[actor], json={"name": name})
        if res.status_code == 200:
            self.rooms.append(res.json()["room"]["id"])
        return res

    def _room_id(self):
        res = self._room()
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["room"]["id"]

    def _book(self, room_id=None, at=None, minutes=50, patient_id=CLIENT_ID, headers=None,
              session_format="In-person"):
        body = {"patient_id": patient_id, "starts_at": (at or self.base).isoformat(),
                "duration_min": minutes, "session_format": session_format}
        if room_id is not None:
            body["room_id"] = room_id
        res = self.client.post("/api/v1/appointments", headers=headers or self.headers["practitioner"],
                               json=body)
        if res.status_code == 200:
            self.booked.append(res.json()["appointment"]["id"])
        return res

    def _patch(self, appointment_id, headers=None, **body):
        return self.client.patch(f"/api/v1/appointments/{appointment_id}",
                                 headers=headers or self.headers["practitioner"], json=body)

    def _other_practitioner(self):
        """A second practitioner with one client of their own: (headers, client id)."""
        username = f"psk.room{random.randint(10000, 99999)}"
        self.users.append(username)
        res = self.client.post("/api/v1/onboarding/provision", headers=self.headers["admin"], json={
            "username": username, "full_name": "Test Practitioner", "role": "practitioner"})
        self.assertEqual(res.status_code, 200, res.text)
        self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["enrollment_token"], "new_password": STRONG})
        login = self.client.post("/api/v1/auth/login", json={"username": username, "password": STRONG})
        self.assertEqual(login.status_code, 200, login.text)
        card = client_registry.add(practitioner=username, full_name="Other Client", phone=None, email=None,
                                   kvkk_signed_on=None, created_by=username)
        self.clients.append(card["patient_id"])
        return {"Authorization": f"Bearer {login.json()['access_token']}"}, card["patient_id"]

    # ── managing rooms ──
    def test_operators_create_rename_and_deactivate(self):
        for actor in ("admin", "security_officer"):
            with self.subTest(actor=actor):
                res = self._room(actor=actor)
                self.assertEqual(res.status_code, 200, res.text)
                room = res.json()["room"]
                self.assertTrue(room["active"])
                new_name = f"Oda R{random.randint(10000, 99999)}"
                res = self.client.patch(f"/api/v1/rooms/{room['id']}", headers=self.headers[actor],
                                        json={"name": new_name})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(res.json()["room"]["name"], new_name)
                res = self.client.patch(f"/api/v1/rooms/{room['id']}", headers=self.headers[actor],
                                        json={"active": False})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertFalse(res.json()["room"]["active"])

    def test_bookers_list_rooms_but_cannot_manage_them(self):
        room_id = self._room_id()
        for actor in ("practitioner", "secretary"):
            with self.subTest(actor=actor):
                res = self.client.get("/api/v1/rooms", headers=self.headers[actor])
                self.assertEqual(res.status_code, 200, res.text)
                self.assertIn(room_id, {r["id"] for r in res.json()["rooms"]})
                res = self.client.post("/api/v1/rooms", headers=self.headers[actor], json={"name": "Oda X"})
                self.assertEqual(res.status_code, 403)
                res = self.client.patch(f"/api/v1/rooms/{room_id}", headers=self.headers[actor],
                                        json={"name": "Oda Y"})
                self.assertEqual(res.status_code, 403)
                res = self.client.patch(f"/api/v1/rooms/{room_id}", headers=self.headers[actor],
                                        json={"active": False})
                self.assertEqual(res.status_code, 403)

    def test_bookers_see_only_active_rooms(self):
        room_id = self._room_id()
        self.client.patch(f"/api/v1/rooms/{room_id}", headers=self.headers["admin"], json={"active": False})
        listed = self.client.get("/api/v1/rooms", headers=self.headers["secretary"]).json()["rooms"]
        self.assertNotIn(room_id, {r["id"] for r in listed})
        # The operators' list keeps it, so it can be switched back on.
        listed = self.client.get("/api/v1/rooms", headers=self.headers["admin"]).json()["rooms"]
        self.assertIn(room_id, {r["id"] for r in listed})

    def test_room_names_are_short_labels(self):
        for name in ("", "   ", "x" * 41, "<script>alert(1)</script>", "Client cried; panic attack\n"):
            with self.subTest(name=name):
                self.assertEqual(self._room(name=name).status_code, 422)
        self.assertEqual(self._room(name=f"Oda Ü{random.randint(1000, 9999)}").status_code, 200)

    def test_duplicate_names_are_refused(self):
        name = f"Oda D{random.randint(10000, 99999)}"
        self.assertEqual(self._room(name=name).status_code, 200)
        self.assertEqual(self._room(name=name.lower()).status_code, 409)

    # ── booking into rooms ──
    def test_appointment_carries_its_room(self):
        room_id = self._room_id()
        res = self._book(room_id)
        self.assertEqual(res.status_code, 200, res.text)
        appointment = res.json()["appointment"]
        self.assertEqual(appointment["room_id"], room_id)
        self.assertTrue(appointment["room_name"].startswith("Oda T"))

    def test_room_is_optional(self):
        res = self._book()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["appointment"]["room_id"])

    def test_same_room_cannot_be_double_booked_across_practitioners(self):
        room_id = self._room_id()
        other, other_client = self._other_practitioner()
        self.assertEqual(self._book(room_id).status_code, 200)
        res = self._book(room_id, at=self.base + timedelta(minutes=30), patient_id=other_client, headers=other)
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("room", res.json().get("detail", res.text).lower())

    def test_same_practitioner_in_another_room_is_still_double_booked(self):
        first, second = self._room_id(), self._room_id()
        self.assertEqual(self._book(first).status_code, 200)
        self.assertEqual(self._book(second, at=self.base + timedelta(minutes=20)).status_code, 409)

    def test_back_to_back_in_one_room(self):
        room_id = self._room_id()
        other, other_client = self._other_practitioner()
        self.assertEqual(self._book(room_id).status_code, 200)
        res = self._book(room_id, at=self.base + timedelta(minutes=50), patient_id=other_client, headers=other)
        self.assertEqual(res.status_code, 200, res.text)

    def test_cancelling_frees_the_room(self):
        room_id = self._room_id()
        other, other_client = self._other_practitioner()
        appointment_id = self._book(room_id).json()["appointment"]["id"]
        self.assertEqual(self._patch(appointment_id, status="cancelled").status_code, 200)
        res = self._book(room_id, patient_id=other_client, headers=other)
        self.assertEqual(res.status_code, 200, res.text)

    def test_moving_into_a_busy_room(self):
        busy, free = self._room_id(), self._room_id()
        other, other_client = self._other_practitioner()
        self.assertEqual(self._book(busy).status_code, 200)
        # Another practitioner's appointment in another room...
        theirs = self._book(free, patient_id=other_client, headers=other).json()["appointment"]["id"]
        # ...cannot move into the busy one,
        self.assertEqual(self._patch(theirs, headers=other, room_id=busy).status_code, 409)
        # nor can a later one in the busy room move onto the busy slot.
        later = self._book(busy, at=self.base + timedelta(hours=2), patient_id=other_client,
                           headers=other).json()["appointment"]["id"]
        res = self._patch(later, headers=other, starts_at=(self.base + timedelta(minutes=10)).isoformat())
        self.assertEqual(res.status_code, 409, res.text)
        # Moving to the free room at a free time works, and the room sticks.
        res = self._patch(theirs, headers=other, room_id=free,
                          starts_at=(self.base + timedelta(hours=4)).isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["appointment"]["room_id"], free)

    def test_room_can_be_cleared_on_move(self):
        room_id = self._room_id()
        appointment_id = self._book(room_id).json()["appointment"]["id"]
        res = self._patch(appointment_id, room_id=None)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["appointment"]["room_id"])

    def test_deactivated_room_is_refused_but_kept_on_old_appointments(self):
        room_id = self._room_id()
        kept = self._book(room_id).json()["appointment"]["id"]
        self.client.patch(f"/api/v1/rooms/{room_id}", headers=self.headers["admin"], json={"active": False})
        self.assertEqual(self._book(room_id, at=self.base + timedelta(hours=3)).status_code, 422)
        start = (self.base - timedelta(days=1)).isoformat()
        end = (self.base + timedelta(days=1)).isoformat()
        listed = {a["id"]: a for a in self.client.get(
            "/api/v1/appointments", headers=self.headers["secretary"],
            params={"start": start, "end": end}).json()["appointments"]}
        self.assertEqual(listed[kept]["room_id"], room_id)
        self.assertTrue(listed[kept]["room_name"])

    def test_unknown_room_is_refused(self):
        self.assertEqual(self._book("ROOM-NOPE").status_code, 422)

    def test_online_appointment_takes_no_room(self):
        room_id = self._room_id()
        self.assertEqual(self._book(room_id, session_format="Online").status_code, 422)
        online = self._book(session_format="Online").json()["appointment"]["id"]
        self.assertEqual(self._patch(online, room_id=room_id).status_code, 422)

    # ── a room's day ──
    def test_room_day_hides_other_books(self):
        room_id = self._room_id()
        other, other_client = self._other_practitioner()
        theirs = self._book(room_id, patient_id=other_client, headers=other).json()["appointment"]["id"]
        mine = self._book(room_id, at=self.base + timedelta(hours=1)).json()["appointment"]["id"]
        res = self.client.get(f"/api/v1/rooms/{room_id}/schedule", headers=self.headers["secretary"], params={
            "start": self.base.replace(hour=0).isoformat(),
            "end": (self.base.replace(hour=0) + timedelta(days=1)).isoformat()})
        self.assertEqual(res.status_code, 200, res.text)
        slots = res.json()["slots"]
        self.assertEqual(len(slots), 2)
        self.assertNotIn(other_client, res.text)
        self.assertNotIn(theirs, res.text)
        busy = [s for s in slots if not s["mine"]]
        self.assertEqual(len(busy), 1)
        self.assertEqual(set(busy[0]), {"starts_at", "ends_at", "mine"})
        own = [s for s in slots if s["mine"]]
        self.assertEqual(own[0]["id"], mine)
        self.assertEqual(own[0]["patient_id"], CLIENT_ID)

    def test_front_desk_sees_every_book_it_runs_in_a_room_day(self):
        """A secretary linked to two practitioners sees both books' appointments
        in the room in full, and a third practitioner's only as busy."""
        from core.services import appointment_book
        room_id = self._room_id()
        second, second_client = self._other_practitioner()
        second_name = self.users[-1]
        third, third_client = self._other_practitioner()
        appointment_book.link_staff("secretary.ayse", second_name)
        self.addCleanup(_sql, "DELETE FROM practice_staff WHERE staff_username = ? AND practitioner_username = ?",
                        ("secretary.ayse", second_name))
        elif_appt = self._book(room_id).json()["appointment"]["id"]
        second_appt = self._book(room_id, at=self.base + timedelta(hours=1), patient_id=second_client,
                                 headers=second).json()["appointment"]["id"]
        third_appt = self._book(room_id, at=self.base + timedelta(hours=2), patient_id=third_client,
                                headers=third).json()["appointment"]["id"]
        res = self.client.get(f"/api/v1/rooms/{room_id}/schedule", headers=self.headers["secretary"], params={
            "start": self.base.replace(hour=0).isoformat(),
            "end": (self.base.replace(hour=0) + timedelta(days=1)).isoformat()})
        self.assertEqual(res.status_code, 200, res.text)
        own = {s["id"] for s in res.json()["slots"] if s["mine"]}
        self.assertEqual(own, {elif_appt, second_appt})
        self.assertNotIn(third_appt, res.text)
        self.assertNotIn(third_client, res.text)
        self.assertEqual(len([s for s in res.json()["slots"] if not s["mine"]]), 1)

    def test_room_day_needs_a_book(self):
        room_id = self._room_id()
        res = self.client.get(f"/api/v1/rooms/{room_id}/schedule", headers=self.headers["admin"],
                              params={"start": self.base.isoformat()})
        self.assertEqual(res.status_code, 403)


@unittest.skipIf(default_sql_db.is_postgres, "SQLite file migration")
class TestRoomMigration(unittest.TestCase):
    def test_old_appointments_keep_a_null_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE appointments (
                id VARCHAR(40) PRIMARY KEY, practitioner_username VARCHAR(100) NOT NULL,
                patient_id VARCHAR(100) NOT NULL, starts_at REAL NOT NULL, duration_min INTEGER NOT NULL,
                session_format VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL,
                created_by VARCHAR(100) NOT NULL, created_at REAL NOT NULL,
                updated_by VARCHAR(100), updated_at REAL)""")
            conn.execute("INSERT INTO appointments VALUES ('APT-OLD', 'psk.elif', 'CL-001', 1, 50, "
                         "'In-person', 'completed', 'x', 1, NULL, NULL)")
            conn.commit()
            conn.close()

            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", path):
                default_sql_db.init_db()
                default_sql_db.init_db()   # safe to run on every start

            conn = sqlite3.connect(path)
            try:
                row = conn.execute("SELECT room_id, status FROM appointments WHERE id = 'APT-OLD'").fetchone()
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            finally:
                conn.close()
            self.assertEqual(row, (None, "completed"))
            self.assertIn("rooms", tables)


if __name__ == "__main__":
    unittest.main()
