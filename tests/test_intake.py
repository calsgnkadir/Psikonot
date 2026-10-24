"""
tests/test_intake.py — the center's intake inbox and waitlist
==============================================================
New clients ask for an appointment by phone, e-mail, in person or through a
referral. Before a client card exists, the request is an *application*
(core/services/intake.py, backend/routers/intake.py):

  * fixed codes for everything (channel, format, preferred days and times,
    priority, status, the reason an application was declined) and no
    free-text field at all: the table is not encrypted, so nothing clinical may
    be typed into it. A request with any other field is refused;
  * status: new -> contacted -> waitlisted -> scheduled, or declined /
    withdrawn. Anything else is refused;
  * who sees what: a secretary sees the unassigned applications and those
    assigned to a practitioner they are linked to; a practitioner sees the
    ones assigned to them, and unassigned ones that prefer them; operators
    (admin, security officer, auditor) see counts only, never a name;
  * the waitlist is ordered by priority, then oldest first, and filters by
    status, practitioner, format and preferred day / time;
  * converting creates exactly one client card, for the assigned
    practitioner, and drops the application's own copy of the personal data;
    the application becomes "scheduled" once the first appointment is booked:
    from the application in one step (POST /applications/{id}/book, which
    books and closes it in one transaction, once), or through the ordinary
    booking endpoints, which close the client's open application too;
  * KVKK: declined / withdrawn applications, and open ones nobody touched,
    lose their personal data after VHV_INTAKE_RETENTION_DAYS (90 by default);
  * every change is audited, with IDs only.
"""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, time as clock, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.events.event_bus import event_bus
from core.security import hash_password
from core.services import appointment_book, client_registry, intake, rooms
from database import sql_db
from database.sql_db import default_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Center@Intake2026!"
OPERATORS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}
DAY = 86400
PERSONAL = ("full_name", "phone", "email")


class IntakeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        self.users, self.cards, self.apps, self.appointments, self.rooms = [], [], [], [], []
        self.a, self.b, self.c = (self._user("practitioner") for _ in range(3))
        # The front desk works for A and B, not C.
        self.desk = self._user("secretary")
        appointment_book.link_staff(self.desk, self.a)
        appointment_book.link_staff(self.desk, self.b)
        self.desk_session = self._session(self.desk)

    def tearDown(self):
        for app_id in self.apps:
            run_sql("DELETE FROM applications WHERE id = ?", (app_id,))
        for appointment_id in self.appointments:
            run_sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for patient_id in self.cards:
            run_sql("DELETE FROM appointments WHERE patient_id = ?", (patient_id,))
            run_sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
        for room_id in self.rooms:
            run_sql("DELETE FROM appointments WHERE room_id = ?", (room_id,))
            run_sql("DELETE FROM rooms WHERE id = ?", (room_id,))
        for username in self.users:
            run_sql("DELETE FROM appointments WHERE practitioner_username = ?", (username,))
            run_sql("DELETE FROM users WHERE username = ?", (username,))
            run_sql("DELETE FROM practice_staff WHERE staff_username = ? OR practitioner_username = ?",
                    (username, username))

    # ── helpers ───────────────────────────────────────────────
    def _user(self, role):
        username = f"{role[:4]}.{uuid.uuid4().hex[:8]}"
        self.users.append(username)
        self.repo.save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=username,
            password_hash=hash_password(PASSWORD), role=role,
            full_name=f"Test {role} {username[-4:]}", account_status="ACTIVE_ENROLLED",
        ))
        return username

    def _session(self, username, password=PASSWORD):
        res = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _body(self, **over):
        body = {"channel": "phone", "full_name": "Deniz Aydın", "phone": "+90 532 000 11 22",
                "session_format": "either", "preferred_days": ["mon", "wed"],
                "preferred_times": ["evening"], "priority": "normal"}
        body.update(over)
        return body

    def _create(self, headers=None, **over):
        res = self.client.post("/api/v1/applications", headers=headers or self.desk_session, json=self._body(**over))
        if res.status_code == 200:
            self.apps.append(res.json()["application"]["id"])
        return res

    def _new(self, received_days_ago=0, **fields):
        """An application written straight through the service (backdated)."""
        data = {"channel": "phone", "full_name": "Test Başvuran", "phone": "+90 532 000 00 99",
                "email": None, "session_format": "either", "preferred_days": [], "preferred_times": [],
                "priority": "normal", "preferred_practitioner": None, "assigned_practitioner": None}
        data.update(fields)
        app_ = intake.create(data, by="test", received_at=time.time() - received_days_ago * DAY)
        self.apps.append(app_["id"])
        return app_["id"]

    def _list(self, headers, **params):
        res = self.client.get("/api/v1/applications", headers=headers, params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return [a["id"] for a in res.json()["applications"] if a["id"] in self.apps]

    def _status(self, headers, app_id, status, reason=None):
        body = {"status": status}
        if reason:
            body["reason"] = reason
        return self.client.post(f"/api/v1/applications/{app_id}/status", headers=headers, json=body)


class TestApplications(IntakeTestCase):
    def test_the_front_desk_records_an_application(self):
        res = self._create(email="deniz@example.com", preferred_practitioner=self.a)
        self.assertEqual(res.status_code, 200, res.text)
        a = res.json()["application"]
        self.assertEqual(a["status"], "new")
        self.assertEqual(a["channel"], "phone")
        self.assertEqual(a["full_name"], "Deniz Aydın")
        self.assertEqual(a["email"], "deniz@example.com")
        self.assertEqual(a["preferred_practitioner"], self.a)
        self.assertIsNone(a["assigned_practitioner"])
        self.assertEqual(a["preferred_days"], ["mon", "wed"])
        self.assertEqual(a["preferred_times"], ["evening"])
        self.assertEqual(a["contact_attempts"], 0)
        self.assertIsNone(a["client_id"])
        self.assertEqual(a["waiting_days"], 0)
        stored = intake.get(a["id"])
        self.assertEqual(stored["created_by"], self.desk)

    def test_fixed_codes_only(self):
        for field, value in (("channel", "fax"), ("session_format", "Hybrid"), ("priority", "urgent"),
                             ("preferred_days", ["monday"]), ("preferred_times", ["night"]),
                             ("preferred_days", "mon"), ("preferred_practitioner", "nobody.at.all"),
                             ("preferred_practitioner", self.desk)):
            with self.subTest(field=field, value=value):
                self.assertEqual(self._create(**{field: value}).status_code, 422)

    def test_no_free_text_clinical_field_is_accepted(self):
        """There is no note field: any field the schema does not know is
        refused, so nothing clinical can be typed into this table."""
        for field in ("note", "notes", "complaint", "reason_for_referral", "summary", "contact_note"):
            with self.subTest(field=field):
                res = self._create(**{field: "Panik atak yaşıyor, intihar düşüncesi var"})
                self.assertEqual(res.status_code, 422, res.text)
        app_id = self._create().json()["application"]["id"]
        res = self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session,
                                json={"note": "Depresyon"})
        self.assertEqual(res.status_code, 422)
        columns = {r[1] for r in run_sql("PRAGMA table_info(applications)", fetch="all")} \
            if not default_sql_db.is_postgres else set()
        for column in ("note", "notes", "complaint", "summary"):
            self.assertNotIn(column, columns)

    def test_the_name_is_a_name(self):
        for name in ("Ayşe (depresyon)", "Mehmet: panik", "Ali 2. seans", "x", "<b>Ali</b>"):
            with self.subTest(name=name):
                self.assertEqual(self._create(full_name=name).status_code, 422)
        for name in ("Ayşe Nur Yılmaz-Kaya", "Can O'Neil", "Şule Ç. Öztürk"):
            with self.subTest(name=name):
                self.assertEqual(self._create(full_name=name).status_code, 200)

    def test_a_way_to_reach_the_applicant_is_required(self):
        self.assertEqual(self._create(phone=None).status_code, 422)
        self.assertEqual(self._create(phone=None, email="a@example.com").status_code, 200)

    def test_status_flow(self):
        app_id = self._create().json()["application"]["id"]
        self.assertEqual(self._status(self.desk_session, app_id, "contacted").json()["application"]["status"],
                         "contacted")
        self.assertEqual(self._status(self.desk_session, app_id, "waitlisted").json()["application"]["status"],
                         "waitlisted")
        res = self._status(self.desk_session, app_id, "withdrawn")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["application"]["close_reason"], "applicant_withdrew")
        # A closed application stays closed.
        for status in ("new", "contacted", "waitlisted", "declined"):
            with self.subTest(status=status):
                self.assertEqual(self._status(self.desk_session, app_id, status, "no_capacity").status_code, 409)

    def test_invalid_transitions_are_refused(self):
        app_id = self._create().json()["application"]["id"]
        # "scheduled" comes only from booking the first appointment.
        self.assertEqual(self._status(self.desk_session, app_id, "scheduled").status_code, 409)
        self.assertEqual(self._status(self.desk_session, app_id, "new").status_code, 409)
        self.assertEqual(self._status(self.desk_session, app_id, "lost").status_code, 422)
        self._status(self.desk_session, app_id, "waitlisted")
        self.assertEqual(self._status(self.desk_session, app_id, "contacted").status_code, 409)

    def test_declining_needs_a_fixed_reason(self):
        app_id = self._create().json()["application"]["id"]
        self.assertEqual(self._status(self.desk_session, app_id, "declined").status_code, 422)
        self.assertEqual(self._status(self.desk_session, app_id, "declined", "too sad").status_code, 422)
        res = self._status(self.desk_session, app_id, "declined", "no_capacity")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["application"]["close_reason"], "no_capacity")

    def test_contact_attempts_are_counted(self):
        app_id = self._create().json()["application"]["id"]
        before = time.time()
        for n in (1, 2):
            res = self.client.post(f"/api/v1/applications/{app_id}/contact-attempt", headers=self.desk_session)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["application"]["contact_attempts"], n)
        self.assertGreaterEqual(intake.get(app_id)["last_contact_at"], before)
        self.assertIsNotNone(res.json()["application"]["last_contact_at"])

    def test_editing_an_application(self):
        app_id = self._create().json()["application"]["id"]
        res = self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session, json={
            "phone": "+90 532 000 33 44", "priority": "high", "assigned_practitioner": self.a,
            "preferred_times": ["morning", "evening"]})
        self.assertEqual(res.status_code, 200, res.text)
        a = res.json()["application"]
        self.assertEqual((a["phone"], a["priority"], a["assigned_practitioner"]), ("+90 532 000 33 44", "high", self.a))
        self.assertEqual(a["preferred_times"], ["morning", "evening"])
        self.assertEqual(intake.get(app_id)["updated_by"], self.desk)
        res = self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session,
                                json={"full_name": None})
        self.assertEqual(res.status_code, 422)

    def test_every_change_is_audited_with_ids_only(self):
        with mock.patch.object(event_bus, "publish") as publish:
            app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
            self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session, json={"priority": "high"})
            self.client.post(f"/api/v1/applications/{app_id}/contact-attempt", headers=self.desk_session)
            self._status(self.desk_session, app_id, "contacted")
            res = self.client.post(f"/api/v1/applications/{app_id}/convert", headers=self.desk_session)
            self.assertEqual(res.status_code, 200, res.text)
            self.cards.append(res.json()["client"]["patient_id"])
        events = [c.args[0] for c in publish.call_args_list if c.args[0].action.startswith("APPLICATION")]
        self.assertEqual([e.action for e in events], [
            "APPLICATION_CREATED", "APPLICATION_UPDATED", "APPLICATION_CONTACT_ATTEMPT",
            "APPLICATION_STATUS_CHANGED", "APPLICATION_CONVERTED"])
        for e in events:
            self.assertEqual(e.username, self.desk)
            self.assertEqual(e.extra["application_id"], app_id)
            text = repr(e.extra)
            for value in ("Deniz", "Aydın", "532", "example.com"):
                self.assertNotIn(value, text)
        self.assertEqual(events[-1].extra["patient_id"], res.json()["client"]["patient_id"])


class TestScoping(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.unassigned = self._new()
        self.for_a = self._new(assigned_practitioner=self.a)
        self.for_b = self._new(assigned_practitioner=self.b)
        self.for_c = self._new(assigned_practitioner=self.c)
        self.prefers_a = self._new(preferred_practitioner=self.a)
        self.prefers_c = self._new(preferred_practitioner=self.c)
        # Preferred A but given to C: it is C's now.
        self.prefers_a_given_c = self._new(preferred_practitioner=self.a, assigned_practitioner=self.c)

    def test_secretary_sees_unassigned_and_linked_books(self):
        seen = set(self._list(self.desk_session))
        self.assertEqual(seen, {self.unassigned, self.for_a, self.for_b, self.prefers_a, self.prefers_c})
        for app_id in (self.for_c, self.prefers_a_given_c):
            with self.subTest(app_id=app_id):
                res = self.client.get(f"/api/v1/applications/{app_id}", headers=self.desk_session)
                self.assertEqual(res.status_code, 404)
                self.assertEqual(self._status(self.desk_session, app_id, "contacted").status_code, 404)
                res = self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session,
                                        json={"priority": "high"})
                self.assertEqual(res.status_code, 404)

    def test_a_secretary_linked_to_nobody_sees_the_unassigned_inbox(self):
        lonely = self._session(self._user("secretary"))
        self.assertEqual(set(self._list(lonely)), {self.unassigned, self.prefers_a, self.prefers_c})

    def test_unlinking_hides_that_book_at_once(self):
        self.assertIn(self.for_a, self._list(self.desk_session))
        appointment_book.unlink_staff(self.desk, self.a)
        self.assertNotIn(self.for_a, self._list(self.desk_session))

    def test_practitioner_sees_assigned_and_unassigned_preferring_them(self):
        self.assertEqual(set(self._list(self._session(self.a))), {self.for_a, self.prefers_a})
        self.assertEqual(set(self._list(self._session(self.c))),
                         {self.for_c, self.prefers_c, self.prefers_a_given_c})
        res = self.client.get(f"/api/v1/applications/{self.unassigned}", headers=self._session(self.a))
        self.assertEqual(res.status_code, 404)
        res = self.client.get(f"/api/v1/applications/{self.for_b}", headers=self._session(self.a))
        self.assertEqual(res.status_code, 404)

    def test_a_secretary_assigns_only_to_a_book_they_run(self):
        res = self.client.patch(f"/api/v1/applications/{self.unassigned}", headers=self.desk_session,
                                json={"assigned_practitioner": self.c})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self._create(assigned_practitioner=self.c).status_code, 403)
        res = self.client.patch(f"/api/v1/applications/{self.unassigned}", headers=self.desk_session,
                                json={"assigned_practitioner": self.b})
        self.assertEqual(res.status_code, 200)

    def test_a_practitioner_assigns_only_to_themselves(self):
        session_a = self._session(self.a)
        res = self.client.patch(f"/api/v1/applications/{self.prefers_a}", headers=session_a,
                                json={"assigned_practitioner": self.b})
        self.assertEqual(res.status_code, 403)
        res = self.client.patch(f"/api/v1/applications/{self.prefers_a}", headers=session_a,
                                json={"assigned_practitioner": self.a})
        self.assertEqual(res.status_code, 200)
        # A practitioner's own new application is theirs unless they say otherwise.
        res = self._create(headers=session_a)
        self.assertEqual(res.json()["application"]["assigned_practitioner"], self.a)

    def test_operators_and_auditors_see_counts_only(self):
        auditor = self._session(self._user("auditor"))
        for name, headers in (("admin", self._session(*OPERATORS["admin"])),
                              ("officer", self._session(*OPERATORS["officer"])), ("auditor", auditor)):
            with self.subTest(operator=name):
                self.assertEqual(self.client.get("/api/v1/applications", headers=headers).status_code, 403)
                res = self.client.get(f"/api/v1/applications/{self.unassigned}", headers=headers)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(self.client.post("/api/v1/applications", headers=headers,
                                                  json=self._body()).status_code, 403)
                self.assertEqual(self._status(headers, self.unassigned, "contacted").status_code, 403)
                res = self.client.get("/api/v1/applications/summary", headers=headers)
                self.assertEqual(res.status_code, 200, res.text)
                counts = res.json()["counts"]
                self.assertGreaterEqual(counts["new"], 7)
                self.assertNotIn("Test Başvuran", res.text)
                self.assertNotIn("532", res.text)

    def test_summary_is_scoped_for_staff(self):
        res = self.client.get("/api/v1/applications/summary", headers=self._session(self.a))
        self.assertEqual(res.status_code, 200)
        own = len(intake.list_visible({"username": self.a, "role": "practitioner"}, status="new"))
        self.assertEqual(res.json()["counts"]["new"], own)
        self.assertEqual(own, 2)

    def test_practitioner_list_for_the_form(self):
        res = self.client.get("/api/v1/applications/practitioners", headers=self.desk_session)
        self.assertEqual(res.status_code, 200)
        names = {p["username"] for p in res.json()["practitioners"]}
        self.assertTrue({self.a, self.b, self.c} <= names)
        self.assertNotIn(self.desk, names)
        admin = self._session(*OPERATORS["admin"])
        self.assertEqual(self.client.get("/api/v1/applications/practitioners", headers=admin).status_code, 403)


class TestWaitlist(IntakeTestCase):
    def test_ordered_by_priority_then_oldest_first(self):
        old = self._new(received_days_ago=30, assigned_practitioner=self.a)
        newer = self._new(received_days_ago=2, assigned_practitioner=self.a)
        urgent_new = self._new(received_days_ago=1, priority="high", assigned_practitioner=self.a)
        urgent_old = self._new(received_days_ago=5, priority="high", assigned_practitioner=self.a)
        self.assertEqual(self._list(self.desk_session, practitioner=self.a),
                         [urgent_old, urgent_new, old, newer])
        res = self.client.get(f"/api/v1/applications/{old}", headers=self.desk_session)
        self.assertEqual(res.json()["application"]["waiting_days"], 30)

    def test_filters(self):
        online_eve = self._new(session_format="Online", preferred_days=["tue"], preferred_times=["evening"])
        inperson_morning = self._new(session_format="In-person", preferred_days=["mon", "fri"],
                                     preferred_times=["morning"])
        either_any = self._new(session_format="either")
        for_a = self._new(assigned_practitioner=self.a)
        prefers_b = self._new(preferred_practitioner=self.b)
        closed = self._new()
        intake.set_status(intake.get(closed), "declined", reason="no_capacity", by="test")

        self.assertEqual(set(self._list(self.desk_session, session_format="Online")),
                         {online_eve, either_any, for_a, prefers_b, closed} - {closed})
        self.assertEqual(set(self._list(self.desk_session, day="fri")),
                         {inperson_morning, either_any, for_a, prefers_b})
        self.assertEqual(set(self._list(self.desk_session, time_of_day="evening")),
                         {online_eve, either_any, for_a, prefers_b})
        self.assertEqual(set(self._list(self.desk_session, practitioner=self.a)), {for_a})
        self.assertEqual(set(self._list(self.desk_session, practitioner=self.b)), {prefers_b})
        self.assertEqual(self._list(self.desk_session, status="declined"), [closed])
        self.assertNotIn(closed, self._list(self.desk_session))          # open ones by default
        self.assertIn(closed, self._list(self.desk_session, status="all"))
        res = self.client.get("/api/v1/applications", headers=self.desk_session, params={"day": "someday"})
        self.assertEqual(res.status_code, 422)


class TestConvert(IntakeTestCase):
    def _convert(self, headers, app_id):
        res = self.client.post(f"/api/v1/applications/{app_id}/convert", headers=headers)
        if res.status_code == 200:
            self.cards.append(res.json()["client"]["patient_id"])
        return res

    def test_convert_creates_one_card_for_the_assigned_practitioner(self):
        app_id = self._create(assigned_practitioner=self.b, email="deniz@example.com").json()["application"]["id"]
        res = self._convert(self.desk_session, app_id)
        self.assertEqual(res.status_code, 200, res.text)
        card = client_registry.get(res.json()["client"]["patient_id"])
        self.assertEqual(card["practitioner_username"], self.b)
        self.assertEqual((card["full_name"], card["phone"], card["email"]),
                         ("Deniz Aydın", "+90 532 000 11 22", "deniz@example.com"))
        a = res.json()["application"]
        self.assertEqual(a["client_id"], card["patient_id"])
        self.assertEqual(a["full_name"], "Deniz Aydın")   # read from the card

        # The application keeps the link and the dates, not a second copy.
        stored = intake.get(app_id)
        for field in PERSONAL:
            self.assertIsNone(stored[field], field)

        again = self._convert(self.desk_session, app_id)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(len([c for c in client_registry.list_for(self.b) if c["full_name"] == "Deniz Aydın"]), 1)

    def test_convert_needs_an_assigned_practitioner(self):
        app_id = self._create().json()["application"]["id"]
        self.assertEqual(self._convert(self.desk_session, app_id).status_code, 422)

    def test_a_closed_application_is_not_converted(self):
        app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        self._status(self.desk_session, app_id, "withdrawn")
        self.assertEqual(self._convert(self.desk_session, app_id).status_code, 409)

    def test_concurrent_conversion_creates_one_card(self):
        app_id = self._new(assigned_practitioner=self.a)
        first = intake.convert(intake.get(app_id), by="test")
        self.cards.append(first[1]["patient_id"])
        with self.assertRaises(intake.IntakeError):
            intake.convert(dict(intake.get(app_id), client_id=None), by="test")

    def test_assigned_practitioner_is_fixed_after_conversion(self):
        app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        self._convert(self.desk_session, app_id)
        res = self.client.patch(f"/api/v1/applications/{app_id}", headers=self.desk_session,
                                json={"assigned_practitioner": self.b})
        self.assertEqual(res.status_code, 409)

    def test_an_appointment_already_in_the_book_can_close_the_application(self):
        """POST /scheduled stays for an application whose first appointment
        is already in the book (booked before booking closed applications, or
        through the service directly): checked against the book, never taken
        on trust."""
        app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        mark = lambda: self.client.post(f"/api/v1/applications/{app_id}/scheduled",  # noqa: E731
                                        headers=self.desk_session)
        self.assertEqual(mark().status_code, 409, "not converted yet")
        patient_id = self._convert(self.desk_session, app_id).json()["client"]["patient_id"]
        self.assertEqual(mark().status_code, 409, "no appointment yet")

        at = time.time() + 300 * DAY + uuid.uuid4().int % 500 * 3600
        appointment_book.book(practitioner=self.a, patient_id=patient_id, starts_at=at, duration_min=50,
                              session_format="Online", created_by="test")
        res = mark()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["application"]["status"], "scheduled")
        self.assertEqual(mark().status_code, 409)
        self.assertNotIn(app_id, self._list(self.desk_session))
        self.assertIn(app_id, self._list(self.desk_session, status="scheduled"))

    def test_a_practitioner_converts_their_own(self):
        app_id = self._new(assigned_practitioner=self.c)
        self.assertEqual(self._convert(self._session(self.a), app_id).status_code, 404)
        self.assertEqual(self._convert(self.desk_session, app_id).status_code, 404)
        res = self._convert(self._session(self.c), app_id)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(client_registry.owner_of(res.json()["client"]["patient_id"]), self.c)


def _future_day(days_min=300):
    """A random date far ahead, so runs never meet."""
    return (datetime.now(timezone.utc) + timedelta(days=days_min + uuid.uuid4().int % 2000)).date()


class TestBookingFromAnApplication(IntakeTestCase):
    """POST /applications/{id}/book: the first appointment and the
    application's "scheduled" in one transaction, once."""

    def setUp(self):
        super().setUp()
        self.day = _future_day()

    def _converted(self, practitioner=None):
        app_id = self._create(assigned_practitioner=practitioner or self.a).json()["application"]["id"]
        res = self.client.post(f"/api/v1/applications/{app_id}/convert", headers=self.desk_session)
        self.assertEqual(res.status_code, 200, res.text)
        patient_id = res.json()["client"]["patient_id"]
        self.cards.append(patient_id)
        return app_id, patient_id

    def _book(self, app_id, headers=None, **over):
        body = {"local_date": self.day.isoformat(), "local_time": "10:00", "duration_min": 50,
                "session_format": "Online"}
        body.update(over)
        body = {k: v for k, v in body.items() if v is not None}
        return self.client.post(f"/api/v1/applications/{app_id}/book", headers=headers or self.desk_session,
                                json=body)

    def _booked(self, patient_id):
        return run_sql("SELECT id, starts_at, room_id, status FROM appointments WHERE patient_id = ?",
                       (patient_id,), fetch="all")

    def _room(self):
        room = rooms.create(f"Oda B{uuid.uuid4().int % 100000}", by="admin")
        self.rooms.append(room["id"])
        return room["id"]

    def _other_client(self, practitioner):
        card = client_registry.add(practitioner=practitioner, full_name="Başka Danışan", phone=None, email=None,
                                   kvkk_signed_on=None, created_by="test")
        self.cards.append(card["patient_id"])
        return card["patient_id"]

    def _istanbul(self, hour, minute=0):
        return datetime.combine(self.day, clock(hour, minute), tzinfo=ZoneInfo("Europe/Istanbul")).timestamp()

    def test_one_step_and_only_once(self):
        app_id, patient_id = self._converted()
        res = self._book(app_id)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["application"]["status"], "scheduled")
        self.assertEqual(body["appointment"]["patient_id"], patient_id)
        self.assertEqual(intake.get(app_id)["status"], "scheduled")

        # Pressing "Randevu ver" again books nothing, whatever the time.
        again = self._book(app_id, local_time="15:00")
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(len(self._booked(patient_id)), 1)

    def test_the_time_is_read_in_the_server_timezone(self):
        app_id, patient_id = self._converted()
        with mock.patch.dict(os.environ, {"VHV_TIMEZONE": "Asia/Tokyo"}):
            res = self._book(app_id, local_time="09:30")
        self.assertEqual(res.status_code, 200, res.text)
        (row,) = self._booked(patient_id)
        local = datetime.fromtimestamp(row[1], tz=ZoneInfo("Asia/Tokyo"))
        self.assertEqual((local.date(), local.strftime("%H:%M")), (self.day, "09:30"))

    def test_a_refused_booking_leaves_the_application_open(self):
        app_id, patient_id = self._converted()
        # The practitioner is busy at 10:00 (another client of theirs).
        appointment_book.book(practitioner=self.a, patient_id=self._other_client(self.a),
                              starts_at=self._istanbul(10), duration_min=50, session_format="Online",
                              created_by="test")
        self.assertEqual(self._book(app_id).status_code, 409)
        self.assertEqual(self._book(app_id, local_date="2020-01-06").status_code, 422)
        self.assertEqual(self._book(app_id, local_time="12:00", duration_min=5).status_code, 422)
        self.assertEqual(self._book(app_id, local_time="12:00", session_format="either").status_code, 422)
        self.assertEqual(self._booked(patient_id), [])
        self.assertEqual(intake.get(app_id)["status"], "new")
        # Another time works, once.
        self.assertEqual(self._book(app_id, local_time="12:00").status_code, 200)

    def test_a_failure_inside_the_step_books_nothing(self):
        """The application is closed by someone else between the request's
        own check and the transaction: the claim inside the transaction fails
        and the appointment written in it is rolled back."""
        app_id, patient_id = self._converted()
        stale = intake.get(app_id)
        run_sql("UPDATE applications SET status = ? WHERE id = ?", ("withdrawn", app_id))
        with self.assertRaises(intake.IntakeError) as caught:
            intake.book_first(stale, starts_at=self._istanbul(10), duration_min=50, session_format="Online",
                              room_id=None, by="test")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self._booked(patient_id), [])
        self.assertEqual(intake.get(app_id)["status"], "withdrawn")

    def test_an_error_after_the_insert_rolls_it_back(self):
        app_id, patient_id = self._converted()
        with mock.patch.object(intake, "_claim_scheduled", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                intake.book_first(intake.get(app_id), starts_at=self._istanbul(10), duration_min=50,
                                  session_format="Online", room_id=None, by="test")
        self.assertEqual(self._booked(patient_id), [])
        self.assertEqual(intake.get(app_id)["status"], "new")

    def test_room_rules_apply(self):
        app_id, patient_id = self._converted()
        room_id = self._room()
        # Another practitioner's session holds the room from 09:30.
        stranger = self._user("practitioner")
        appointment_book.book(practitioner=stranger, patient_id=self._other_client(stranger),
                              starts_at=self._istanbul(9, 30), duration_min=50, session_format="In-person",
                              created_by="test", room_id=room_id)
        taken = self._book(app_id, session_format="In-person", room_id=room_id)
        self.assertEqual(taken.status_code, 409, taken.text)
        online = self._book(app_id, session_format="Online", room_id=room_id, local_time="14:00")
        self.assertEqual(online.status_code, 422, online.text)
        off = self._room()
        rooms.update(rooms.get(off), by="admin", active=False)
        closed = self._book(app_id, session_format="In-person", room_id=off, local_time="14:00")
        self.assertEqual(closed.status_code, 422, closed.text)
        self.assertEqual(self._booked(patient_id), [])
        self.assertEqual(intake.get(app_id)["status"], "new")

        res = self._book(app_id, session_format="In-person", room_id=room_id, local_time="14:00")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["appointment"]["room_id"], room_id)

    def test_only_a_converted_open_application_is_booked(self):
        app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        self.assertEqual(self._book(app_id).status_code, 409, "no client card yet")
        app_id, patient_id = self._converted()
        self._status(self.desk_session, app_id, "withdrawn")
        self.assertEqual(self._book(app_id).status_code, 409)
        self.assertEqual(self._booked(patient_id), [])

    def test_only_who_runs_that_book(self):
        app_id, _ = self._converted(self.a)
        self.assertEqual(self._book(app_id, headers=self._session(self.b)).status_code, 404)
        self.assertEqual(self._book(app_id, headers=self._session(self.c)).status_code, 404)
        res = self._book(app_id, headers=self._session(self.a))
        self.assertEqual(res.status_code, 200, res.text)

    def test_it_is_audited_with_ids_only(self):
        app_id, _ = self._converted()
        with mock.patch.object(event_bus, "publish") as publish:
            self.assertEqual(self._book(app_id).status_code, 200)
        actions = {e.action: e.extra for e in (c.args[0] for c in publish.call_args_list)}
        self.assertIn("APPOINTMENT_BOOKED", actions)
        self.assertEqual(actions["APPLICATION_STATUS_CHANGED"]["status"], "scheduled")
        self.assertNotIn("Deniz", str(actions))


class TestBookingElsewhereClosesTheApplication(IntakeTestCase):
    """A converted client's open application is closed by booking their
    first appointment through the ordinary endpoints, in the same
    transaction."""

    def _converted(self):
        app_id = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        _, card = intake.convert(intake.get(app_id), by="test")
        self.cards.append(card["patient_id"])
        return app_id, card["patient_id"]

    def _at(self, hour=10):
        return datetime.combine(_future_day(), clock(hour, 0), tzinfo=ZoneInfo("Europe/Istanbul"))

    def _post(self, path, **body):
        return self.client.post(path, headers=self.desk_session, params={"practitioner": self.a},
                                json={"starts_at": self._at().isoformat(), "duration_min": 50,
                                      "session_format": "Online", **body})

    def test_a_single_booking(self):
        app_id, patient_id = self._converted()
        other_app = self._create(assigned_practitioner=self.a).json()["application"]["id"]
        res = self._post("/api/v1/appointments", patient_id=patient_id)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(intake.get(app_id)["status"], "scheduled")
        self.assertIsNotNone(intake.get(app_id)["closed_at"])
        # Somebody else's application is left as it is.
        self.assertEqual(intake.get(other_app)["status"], "new")

    def test_a_series(self):
        app_id, patient_id = self._converted()
        res = self._post("/api/v1/appointments/series", patient_id=patient_id, frequency="weekly", count=3)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(intake.get(app_id)["status"], "scheduled")

    def test_a_refused_booking_leaves_it_open(self):
        app_id, patient_id = self._converted()
        past = datetime(2020, 1, 6, 10, tzinfo=timezone.utc)
        res = self._post("/api/v1/appointments", patient_id=patient_id, starts_at=past.isoformat())
        self.assertEqual(res.status_code, 422, res.text)
        series = self._post("/api/v1/appointments/series", patient_id=patient_id, frequency="weekly", count=2,
                            starts_at=past.isoformat())
        self.assertEqual(series.status_code, 409, series.text)
        self.assertEqual(intake.get(app_id)["status"], "new")

    def test_it_is_audited(self):
        app_id, patient_id = self._converted()
        with mock.patch.object(event_bus, "publish") as publish:
            self.assertEqual(self._post("/api/v1/appointments", patient_id=patient_id).status_code, 200)
        changed = [e.extra for e in (c.args[0] for c in publish.call_args_list)
                   if e.action == "APPLICATION_STATUS_CHANGED"]
        self.assertEqual(changed, [{"application_id": app_id, "status": "scheduled", "patient_id": patient_id}])


class TestPurge(IntakeTestCase):
    def _closed(self, status, days_ago):
        app_id = self._new()
        with mock.patch.object(intake.time, "time", return_value=time.time() - days_ago * DAY):
            intake.set_status(intake.get(app_id), status,
                              reason="no_answer" if status == "declined" else None, by="test")
        return app_id

    def test_closed_applications_lose_their_personal_data_after_the_period(self):
        old_declined = self._closed("declined", 91)
        old_withdrawn = self._closed("withdrawn", 120)
        recent = self._closed("declined", 10)
        with mock.patch.object(event_bus, "publish") as publish:
            purged = intake.purge_expired()
        self.assertTrue({old_declined, old_withdrawn} <= set(purged))
        self.assertNotIn(recent, purged)
        for app_id in (old_declined, old_withdrawn):
            row = intake.get(app_id)
            for field in PERSONAL:
                self.assertIsNone(row[field])
            self.assertIsNotNone(row["purged_at"])
            # What is left is anonymous and still counts.
            self.assertIn(row["status"], ("declined", "withdrawn"))
            self.assertIsNotNone(row["received_at"])
        self.assertEqual(intake.get(recent)["full_name"], "Test Başvuran")
        events = [c.args[0] for c in publish.call_args_list if c.args[0].action == "APPLICATIONS_PURGED"]
        self.assertEqual(len(events), 1)
        self.assertIn(old_declined, events[0].extra["application_ids"])
        self.assertNotIn("Test Başvuran", repr(events[0].extra))
        # Purged applications are not listed any more, and a second run does nothing new.
        self.assertNotIn(old_declined, self._list(self.desk_session, status="all"))
        self.assertNotIn(old_declined, intake.purge_expired())

    def test_inactive_open_applications_lapse(self):
        stale = self._new(received_days_ago=200)
        run_sql("UPDATE applications SET last_activity_at = ? WHERE id = ?", (time.time() - 100 * DAY, stale))
        touched = self._new(received_days_ago=200)
        run_sql("UPDATE applications SET last_activity_at = ? WHERE id = ?", (time.time() - 5 * DAY, touched))
        intake.purge_expired()
        self.assertEqual(intake.get(stale)["status"], "lapsed")
        self.assertIsNone(intake.get(stale)["full_name"])
        self.assertEqual(intake.get(touched)["full_name"], "Test Başvuran")

    def test_the_period_is_configurable(self):
        app_id = self._closed("declined", 20)
        with mock.patch.dict(os.environ, {"VHV_INTAKE_RETENTION_DAYS": "30"}):
            self.assertNotIn(app_id, intake.purge_expired())
        with mock.patch.dict(os.environ, {"VHV_INTAKE_RETENTION_DAYS": "14"}):
            self.assertIn(app_id, intake.purge_expired())
        for raw, days in (("", 90), ("abc", 90), ("0", 90), ("-5", 90), ("365", 365)):
            with mock.patch.dict(os.environ, {"VHV_INTAKE_RETENTION_DAYS": raw}):
                self.assertEqual(intake.retention_days(), days)

    def test_a_purge_runs_when_the_waitlist_is_read(self):
        app_id = self._closed("withdrawn", 100)
        with mock.patch.object(intake, "_last_purge", 0.0):
            self._list(self.desk_session, status="all")
        self.assertIsNone(intake.get(app_id)["full_name"])

    def test_converted_applications_are_left_alone(self):
        app_id = self._new(assigned_practitioner=self.a)
        _, card = intake.convert(intake.get(app_id), by="test")
        self.cards.append(card["patient_id"])
        run_sql("UPDATE applications SET last_activity_at = ? WHERE id = ?", (time.time() - 400 * DAY, app_id))
        intake.purge_expired()
        row = intake.get(app_id)
        self.assertEqual(row["client_id"], card["patient_id"])
        self.assertIsNone(row["purged_at"])
        self.assertNotEqual(row["status"], "lapsed")


class TestDemoData(unittest.TestCase):
    def test_demo_applications_follow_the_rules(self):
        from backend.demo_seed import _DEMO_APPLICATIONS
        from backend.schemas.requests import ApplicationReq
        for (name, phone, email, channel, _days, status, assigned, preferred, session_format, days, times,
             priority, _attempts, reason) in _DEMO_APPLICATIONS:
            with self.subTest(name=name):
                ApplicationReq(channel=channel, full_name=name, phone=phone, email=email,
                               preferred_practitioner=preferred, assigned_practitioner=assigned,
                               session_format=session_format, preferred_days=days, preferred_times=times,
                               priority=priority)
                self.assertIn(status, intake.STATUSES)
                self.assertTrue(reason is None or reason in intake.REASONS)


@unittest.skipIf(default_sql_db.is_postgres, "SQLite file migration")
class TestMigration(unittest.TestCase):
    def test_the_table_is_created_once_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", path):
                default_sql_db.init_db()
                conn = sqlite3.connect(path)
                conn.execute("INSERT INTO applications (id, received_at, channel, full_name, session_format, "
                             "priority, status, contact_attempts, created_by, created_at, last_activity_at) "
                             "VALUES ('APP-OLD', 1, 'phone', 'Eski Başvuru', 'either', 'normal', 'new', 0, "
                             "'x', 1, 1)")
                conn.commit()
                conn.close()
                default_sql_db.init_db()   # safe to run on every start
                default_sql_db.init_db()
            conn = sqlite3.connect(path)
            try:
                row = conn.execute("SELECT full_name, status FROM applications WHERE id = 'APP-OLD'").fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("Eski Başvuru", "new"))

    def test_demo_applications_are_seeded_once(self):
        from backend.demo_seed import _DEMO_APPLICATIONS, seed_demo_applications
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", os.path.join(tmp, "demo.db")):
                default_sql_db.init_db()
                self.assertTrue(seed_demo_applications())
                self.assertFalse(seed_demo_applications())
                rows = run_sql("SELECT status, COUNT(*) FROM applications GROUP BY status", fetch="all")
            self.assertEqual(sum(n for _, n in rows), len(_DEMO_APPLICATIONS))


if __name__ == "__main__":
    unittest.main()
