"""
tests/test_shared_secretary.py — one front desk for a psychology center
=======================================================================
A secretary used to work for exactly one practitioner (practice_staff had the
secretary's username as its primary key). A center's front desk now serves
several practitioners:

  * a secretary may be linked to several practitioners. Only an operator
    (admin / security officer, the center manager) links a secretary to a book:
    a practitioner who could add any secretary to their own book would make
    that secretary "shared" without asking anyone, and take reset/disable away
    from the practitioner the secretary works for. A practitioner still invites
    a new secretary of their own and removes a secretary from their own book;
    an operator may link and unlink anyone. Every change is audited. Operators
    see every practitioner and secretary with their links (names and account
    status only);
  * everything the secretary sees — client cards, appointments, the tally, the
    list of books — is limited to the practitioners they are linked to. When
    they work for several, a request for one book names the practitioner, and
    the link is checked on every request: removing it ends access at once;
  * the day view lists a day's appointments for every book the caller may see,
    the day read in the server's timezone (Europe/Istanbul unless configured);
  * the secretary still never opens a record.
"""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.events.event_bus import event_bus
from core.security import hash_password
from core.services import appointment_book, client_registry
from database import sql_db
from database.sql_db import default_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Center@Desk2026!"
OPERATORS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}
# What the day view may say about an appointment. Nothing clinical; the room
# is a label the operators chose ("Oda 1"); series_id is an opaque id that
# ties the sessions of a weekly series together (null for a single one).
DAY_FIELDS = {"id", "patient_id", "client_name", "starts_at", "ends_at", "duration_min",
              "session_format", "room_id", "room_name", "status", "series_id"}


class CenterTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        self.users, self.cards, self.appointments = [], [], []
        # Three practitioners of one center, each with a client and an appointment.
        self.a, self.b, self.c = (self._user("practitioner") for _ in range(3))
        self.card = {p: self._card(p) for p in (self.a, self.b, self.c)}
        self.future = time.time() + 400 * 86400 + uuid.uuid4().int % 1000 * 3600
        self.appt = {p: self._appointment(p, self.card[p], self.future) for p in (self.a, self.b, self.c)}
        # The front desk works for A and B, not C.
        self.desk = self._user("secretary")
        appointment_book.link_staff(self.desk, self.a)
        appointment_book.link_staff(self.desk, self.b)
        self.desk_session = self._session(self.desk)

    def tearDown(self):
        for appointment_id in self.appointments:
            run_sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for patient_id in self.cards:
            run_sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
        for username in self.users:
            run_sql("DELETE FROM users WHERE username = ?", (username,))
            run_sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))
            run_sql("DELETE FROM practice_staff WHERE staff_username = ? OR practitioner_username = ?",
                    (username, username))

    # ── helpers ───────────────────────────────────────────────
    def _user(self, role, status="ACTIVE_ENROLLED"):
        username = f"{role[:4]}.{uuid.uuid4().hex[:8]}"
        self.users.append(username)
        self.repo.save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=username,
            password_hash=hash_password(PASSWORD), role=role,
            full_name=f"Test {role} {username[-4:]}", account_status=status,
        ))
        return username

    def _card(self, practitioner, name=None):
        card = client_registry.add(practitioner=practitioner, full_name=name or f"Client of {practitioner}",
                                   phone=None, email=None, kvkk_signed_on=None, created_by=practitioner)
        self.cards.append(card["patient_id"])
        return card["patient_id"]

    def _appointment(self, practitioner, patient_id, starts_at, status="scheduled", minutes=50):
        appointment_id = appointment_book.add_existing(
            practitioner=practitioner, patient_id=patient_id, starts_at=starts_at, duration_min=minutes,
            session_format="In-person", status=status, created_by="test")
        self.appointments.append(appointment_id)
        return appointment_id

    def _session(self, username, password=PASSWORD):
        res = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _operator(self, who):
        return self._session(*OPERATORS[who])

    def _range(self):
        return {"start": datetime.fromtimestamp(self.future - 86400, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(self.future + 86400, tz=timezone.utc).isoformat()}

    def _clients(self, headers, practitioner=None):
        params = {"practitioner": practitioner} if practitioner else {}
        return self.client.get("/api/v1/clients", headers=headers, params=params)

    def _appointments(self, headers, practitioner=None):
        params = self._range()
        if practitioner:
            params["practitioner"] = practitioner
        return self.client.get("/api/v1/appointments", headers=headers, params=params)

    def _book(self, headers, patient_id, practitioner=None, at=None):
        at = at or datetime.fromtimestamp(self.future + 7200, tz=timezone.utc)
        params = {"practitioner": practitioner} if practitioner else {}
        res = self.client.post("/api/v1/appointments", headers=headers, params=params, json={
            "patient_id": patient_id, "starts_at": at.isoformat(), "duration_min": 50,
            "session_format": "Online"})
        if res.status_code == 200:
            self.appointments.append(res.json()["appointment"]["id"])
        return res

    def _link(self, headers, username, practitioner=None):
        body = {"username": username}
        if practitioner:
            body["practitioner"] = practitioner
        return self.client.post("/api/v1/onboarding/staff/link", headers=headers, json=body)

    def _unlink(self, headers, username, practitioner=None):
        body = {"username": username}
        if practitioner:
            body["practitioner"] = practitioner
        return self.client.post("/api/v1/onboarding/staff/unlink", headers=headers, json=body)

    def _day(self, headers, date):
        return self.client.get("/api/v1/appointments/day", headers=headers, params={"date": date})


class TestScoping(CenterTestCase):
    def test_secretary_sees_both_linked_books_and_not_the_third(self):
        for practitioner in (self.a, self.b):
            with self.subTest(practitioner=practitioner):
                res = self._clients(self.desk_session, practitioner)
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual([c["patient_id"] for c in res.json()["clients"]], [self.card[practitioner]])
                res = self._appointments(self.desk_session, practitioner)
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual([a["id"] for a in res.json()["appointments"]], [self.appt[practitioner]])

        for res in (self._clients(self.desk_session, self.c), self._appointments(self.desk_session, self.c)):
            self.assertEqual(res.status_code, 403, res.text)
            self.assertNotIn(self.card[self.c], res.text)

    def test_no_such_practitioner_and_not_yours_get_the_same_answer(self):
        not_yours = self._clients(self.desk_session, self.c)
        no_such = self._clients(self.desk_session, "nobody.at.all")
        self.assertEqual(not_yours.status_code, 403)
        self.assertEqual((no_such.status_code, no_such.json()), (not_yours.status_code, not_yours.json()))

    def test_a_secretary_of_several_names_the_book(self):
        self.assertEqual(self._clients(self.desk_session).status_code, 422)
        self.assertEqual(self._appointments(self.desk_session).status_code, 422)
        self.assertEqual(self._book(self.desk_session, self.card[self.a]).status_code, 422)
        add = self.client.post("/api/v1/clients", headers=self.desk_session, json={"full_name": "Deniz Aydın"})
        self.assertEqual(add.status_code, 422)

    def test_secretary_books_and_adds_cards_in_the_named_book_only(self):
        res = self._book(self.desk_session, self.card[self.b], practitioner=self.b)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(res.json()["appointment"]["id"],
                      [a["id"] for a in self._appointments(self._session(self.b)).json()["appointments"]])
        # A's client cannot be booked into B's book, nor C's client anywhere.
        self.assertEqual(self._book(self.desk_session, self.card[self.a], practitioner=self.b).status_code, 404)
        self.assertEqual(self._book(self.desk_session, self.card[self.c], practitioner=self.c).status_code, 403)

        res = self.client.post("/api/v1/clients", headers=self.desk_session, params={"practitioner": self.a},
                               json={"full_name": "Deniz Aydın"})
        self.assertEqual(res.status_code, 200, res.text)
        self.cards.append(res.json()["client"]["patient_id"])
        self.assertEqual(client_registry.owner_of(res.json()["client"]["patient_id"]), self.a)
        res = self.client.post("/api/v1/clients", headers=self.desk_session, params={"practitioner": self.c},
                               json={"full_name": "Deniz Aydın"})
        self.assertEqual(res.status_code, 403)

    def test_secretary_changes_appointments_and_cards_of_linked_books_only(self):
        patch = lambda appointment_id: self.client.patch(  # noqa: E731
            f"/api/v1/appointments/{appointment_id}", headers=self.desk_session, json={"status": "cancelled"})
        self.assertEqual(patch(self.appt[self.c]).status_code, 404)
        self.assertEqual(patch(self.appt[self.a]).status_code, 200)
        self.assertEqual(patch(self.appt[self.b]).status_code, 200)

        edit = lambda patient_id: self.client.patch(  # noqa: E731
            f"/api/v1/clients/{patient_id}", headers=self.desk_session, json={"phone": "+90 532 000 00 42"})
        self.assertEqual(edit(self.card[self.c]).status_code, 404)
        self.assertEqual(edit(self.card[self.a]).status_code, 200)

    def test_unlinking_ends_access_to_that_book_at_once(self):
        session_a = self._session(self.a)
        self.assertEqual(self._clients(self.desk_session, self.a).status_code, 200)
        self.assertEqual(self._unlink(session_a, self.desk).status_code, 200)

        # The same session, the very next request.
        self.assertEqual(self._clients(self.desk_session, self.a).status_code, 403)
        self.assertEqual(self._appointments(self.desk_session, self.a).status_code, 403)
        res = self.client.patch(f"/api/v1/appointments/{self.appt[self.a]}", headers=self.desk_session,
                                json={"status": "cancelled"})
        self.assertEqual(res.status_code, 404)
        # B's book is untouched, and with one book left there is nothing to choose.
        self.assertEqual(self._clients(self.desk_session).status_code, 200)
        self.assertEqual(self._clients(self.desk_session).json()["clients"][0]["patient_id"], self.card[self.b])

    def test_a_secretary_linked_to_nobody_has_no_book(self):
        lonely = self._user("secretary")
        headers = self._session(lonely)
        self.assertEqual(self._clients(headers).status_code, 403)
        self.assertEqual(self._appointments(headers).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/appointments/practitioners", headers=headers).json(),
                         {"practitioners": []})

    def test_a_practitioner_still_sees_only_their_own_book(self):
        session_a = self._session(self.a)
        self.assertEqual(self._clients(session_a).status_code, 200)
        self.assertEqual(self._clients(session_a, self.a).status_code, 200)
        self.assertEqual(self._clients(session_a, self.b).status_code, 403)
        self.assertEqual(self._appointments(session_a, self.b).status_code, 403)
        self.assertEqual(self._book(session_a, self.card[self.b], practitioner=self.b).status_code, 403)

    def test_the_secretary_lists_their_books(self):
        res = self.client.get("/api/v1/appointments/practitioners", headers=self.desk_session)
        self.assertEqual(res.status_code, 200, res.text)
        books = {p["username"]: p for p in res.json()["practitioners"]}
        self.assertEqual(set(books), {self.a, self.b})
        self.assertEqual(books[self.a]["full_name"], f"Test practitioner {self.a[-4:]}")

    def test_secretary_is_refused_by_every_record_endpoint(self):
        for practitioner in (self.a, self.b, self.c):
            patient_id = self.card[practitioner]
            for path in (f"/api/v1/records/{patient_id}",
                         f"/api/v1/records/{patient_id}/1",
                         f"/api/v1/records/proof/{patient_id}/1",
                         f"/api/v1/blockchain/{patient_id}/status",
                         f"/api/v1/blockchain/{patient_id}/access-logs",
                         f"/api/v1/kvkk/export/{patient_id}"):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path, headers=self.desk_session).status_code, 403)
            res = self.client.post(f"/api/v1/records/{patient_id}/1/decrypt", headers=self.desk_session,
                                   json={"password": "anything-at-all"})
            self.assertEqual(res.status_code, 403)
            res = self.client.post("/api/v1/records", headers=self.desk_session, json={
                "patient_id": patient_id, "record_type": "other", "title": "Desk note",
                "doctor_name": "-", "institution": "-", "record_date": "2026-09-01", "data": {}})
            self.assertEqual(res.status_code, 403)


class TestLinking(CenterTestCase):
    def _all_links(self):
        return sorted(tuple(r) for r in run_sql(
            "SELECT staff_username, practitioner_username FROM practice_staff", fetch="all"))

    def test_a_practitioner_cannot_link_a_secretary(self):
        lonely = self._user("secretary")
        before = self._all_links()
        session_c = self._session(self.c)
        for username, practitioner in ((self.desk, None), (self.desk, self.c), (lonely, None),
                                       ("nobody.at.all", None)):
            with self.subTest(username=username, practitioner=practitioner):
                res = self._link(session_c, username, practitioner)
                self.assertEqual(res.status_code, 403, res.text)
        self.assertEqual(self._all_links(), before)
        self.assertEqual(self._clients(self.desk_session, self.c).status_code, 403)

    def test_an_operator_adds_an_existing_secretary_to_a_book(self):
        for who, practitioner in (("admin", self.c), ("officer", self._user("practitioner"))):
            with self.subTest(operator=who):
                res = self._link(self._operator(who), self.desk, practitioner=practitioner)
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(res.json()["practitioner"], practitioner)
                self.assertTrue(appointment_book.is_linked(self.desk, practitioner))
                self.assertEqual(self._clients(self.desk_session, practitioner).status_code, 200)
                again = self._link(self._operator(who), self.desk, practitioner=practitioner)
                self.assertEqual(again.status_code, 409, "already linked")
        # The practitioner sees the secretary on their list, marked shared.
        staff = {s["username"]: s for s in self.client.get(
            "/api/v1/onboarding/staff", headers=self._session(self.c)).json()["staff"]}
        self.assertTrue(staff[self.desk]["shared"])

    def test_a_practitioner_links_only_to_their_own_book(self):
        res = self._link(self._session(self.c), self.desk, practitioner=self.a)
        self.assertEqual(res.status_code, 403)
        res = self._unlink(self._session(self.c), self.desk, practitioner=self.a)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self._clients(self.desk_session, self.a).status_code, 200)

    def test_only_secretary_accounts_can_be_linked(self):
        admin = self._operator("admin")
        for username in (self.a, "admin", "nobody.at.all", self._user("auditor")):
            with self.subTest(username=username):
                self.assertEqual(self._link(admin, username, practitioner=self.c).status_code, 404)

    def test_removing_someone_who_is_not_on_the_book(self):
        self.assertEqual(self._unlink(self._session(self.c), self.desk).status_code, 404)

    def test_secretaries_and_auditors_cannot_link(self):
        auditor = self._session(self._user("auditor"))
        for headers in (self.desk_session, auditor):
            self.assertEqual(self._link(headers, self.desk, practitioner=self.c).status_code, 403)
            self.assertEqual(self._unlink(headers, self.desk, practitioner=self.a).status_code, 403)

    def test_an_operator_links_and_unlinks_for_any_practitioner(self):
        admin = self._operator("admin")
        self.assertEqual(self._link(admin, self.desk).status_code, 422, "an operator names the practitioner")
        self.assertEqual(self._link(admin, self.desk, practitioner="nobody.at.all").status_code, 404)
        self.assertEqual(self._link(admin, self.desk, practitioner=self.desk).status_code, 404)

        self.assertEqual(self._link(admin, self.desk, practitioner=self.c).status_code, 200)
        self.assertEqual(self._clients(self.desk_session, self.c).status_code, 200)
        officer = self._operator("officer")
        self.assertEqual(self._unlink(officer, self.desk, practitioner=self.a).status_code, 200)
        self.assertEqual(self._clients(self.desk_session, self.a).status_code, 403)

    def test_an_invited_secretary_can_be_added_by_another_practitioner(self):
        username = f"sec.{uuid.uuid4().hex[:8]}"
        self.users.append(username)
        res = self.client.post("/api/v1/onboarding/invite-secretary", headers=self._session(self.a),
                               json={"username": username, "full_name": "New Desk"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._link(self._operator("admin"), username, practitioner=self.b).status_code, 200)
        self.assertEqual(set(appointment_book.practitioners_of(username)), {self.a, self.b})

        redeemed = self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["invite_code"], "new_password": PASSWORD})
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        headers = self._session(username)
        self.assertEqual(self._clients(headers, self.a).status_code, 200)
        self.assertEqual(self._clients(headers, self.b).status_code, 200)

    def test_links_and_unlinks_are_audited(self):
        with mock.patch.object(event_bus, "publish") as publish:
            self.assertEqual(self._link(self._operator("officer"), self.desk, practitioner=self.c).status_code,
                             200)
            self.assertEqual(self._unlink(self._operator("admin"), self.desk, practitioner=self.a).status_code,
                             200)
        events = [call.args[0] for call in publish.call_args_list]
        linked = [e for e in events if e.action == "STAFF_LINKED"]
        unlinked = [e for e in events if e.action == "STAFF_UNLINKED"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0].username, "sec.officer")
        self.assertEqual(linked[0].extra, {"secretary": self.desk, "practitioner": self.c})
        self.assertEqual(len(unlinked), 1)
        self.assertEqual(unlinked[0].username, "admin")
        self.assertEqual(unlinked[0].extra, {"secretary": self.desk, "practitioner": self.a})

    def test_removing_a_pending_secretary_from_their_only_book_withdraws_the_invitation(self):
        username = f"sec.{uuid.uuid4().hex[:8]}"
        self.users.append(username)
        session_a = self._session(self.a)
        res = self.client.post("/api/v1/onboarding/invite-secretary", headers=session_a,
                               json={"username": username, "full_name": "New Desk"})
        self.assertEqual(self._unlink(session_a, username).status_code, 200)
        redeemed = self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["invite_code"], "new_password": PASSWORD})
        self.assertEqual(redeemed.status_code, 400, redeemed.text)


class TestOperatorLinkList(CenterTestCase):
    """GET /api/v1/onboarding/staff/links: every practitioner and secretary
    with their links, for the center manager's screen. Names and account status
    only: no client, appointment or record."""

    PATH = "/api/v1/onboarding/staff/links"
    ACCOUNT_FIELDS = {"username", "full_name", "role", "status"}

    def test_operators_see_every_link(self):
        lonely = self._user("secretary", status="PENDING_ONBOARDING")
        for who in ("admin", "officer"):
            with self.subTest(operator=who):
                res = self.client.get(self.PATH, headers=self._operator(who))
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                self.assertEqual(set(body), {"practitioners", "secretaries"})
                practitioners = {p["username"]: p for p in body["practitioners"]}
                secretaries = {s["username"]: s for s in body["secretaries"]}
                for p in (self.a, self.b, self.c):
                    self.assertEqual(set(practitioners[p]), self.ACCOUNT_FIELDS | {"secretaries"})
                    self.assertEqual(practitioners[p]["role"], "practitioner")
                    self.assertEqual(practitioners[p]["status"], "active")
                self.assertEqual(practitioners[self.a]["secretaries"], [self.desk])
                self.assertEqual(practitioners[self.c]["secretaries"], [])
                self.assertEqual(set(secretaries[self.desk]), self.ACCOUNT_FIELDS | {"practitioners"})
                self.assertEqual(secretaries[self.desk]["role"], "secretary")
                self.assertEqual(secretaries[self.desk]["practitioners"], sorted([self.a, self.b]))
                self.assertEqual(secretaries[self.desk]["full_name"], f"Test secretary {self.desk[-4:]}")
                self.assertEqual(secretaries[lonely]["practitioners"], [])
                self.assertEqual(secretaries[lonely]["status"], "pending")
                # Only practitioners and secretaries are listed.
                self.assertNotIn("admin", practitioners)
                self.assertNotIn("admin", secretaries)

    def test_the_list_holds_no_client_data(self):
        res = self.client.get(self.PATH, headers=self._operator("admin"))
        self.assertEqual(res.status_code, 200, res.text)
        for practitioner in (self.a, self.b, self.c):
            self.assertNotIn(self.card[practitioner], res.text)
            self.assertNotIn(f"Client of {practitioner}", res.text)
            self.assertNotIn(self.appt[practitioner], res.text)
        # Every entry carries the same few fields, whoever else is in the table.
        for entry in res.json()["practitioners"]:
            self.assertEqual(set(entry), self.ACCOUNT_FIELDS | {"secretaries"})
        for entry in res.json()["secretaries"]:
            self.assertEqual(set(entry), self.ACCOUNT_FIELDS | {"practitioners"})

    def test_everyone_else_is_refused(self):
        for headers in (self._session(self.a), self.desk_session, self._session(self._user("auditor"))):
            self.assertEqual(self.client.get(self.PATH, headers=headers).status_code, 403)
        self.assertIn(self.client.get(self.PATH).status_code, (401, 403))


class TestDayView(CenterTestCase):
    def _istanbul(self, y, m, d, hh, mm):
        # Istanbul has been UTC+3 all year since 2016.
        return datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=3))).timestamp()

    def test_the_desk_sees_the_day_of_every_linked_book(self):
        day = datetime.fromtimestamp(self.future, tz=timezone(timedelta(hours=3))).date().isoformat()
        res = self._day(self.desk_session, day)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["date"], day)
        self.assertEqual(body["timezone"], "Europe/Istanbul")
        books = {p["username"]: p for p in body["practitioners"]}
        self.assertEqual(set(books), {self.a, self.b})
        self.assertEqual(books[self.a]["full_name"], f"Test practitioner {self.a[-4:]}")
        (item,) = books[self.a]["appointments"]
        self.assertEqual(set(item), DAY_FIELDS)
        self.assertEqual(item["id"], self.appt[self.a])
        self.assertEqual(item["patient_id"], self.card[self.a])
        self.assertEqual(item["client_name"], f"Client of {self.a}")
        self.assertEqual(item["duration_min"], 50)
        self.assertEqual(item["session_format"], "In-person")
        self.assertEqual(item["status"], "scheduled")
        self.assertNotIn(self.card[self.c], res.text)

    def test_a_practitioner_sees_their_own_day_and_operators_none(self):
        day = datetime.fromtimestamp(self.future, tz=timezone(timedelta(hours=3))).date().isoformat()
        res = self._day(self._session(self.a), day)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([p["username"] for p in res.json()["practitioners"]], [self.a])
        self.assertEqual(self._day(self._operator("admin"), day).status_code, 403)

    def test_the_day_is_read_in_istanbul_time(self):
        # 23:30 on 10 March in Istanbul is 20:30 UTC; 00:10 on 11 March is 21:10 UTC on 10 March.
        late = self._appointment(self.a, self.card[self.a], self._istanbul(2031, 3, 10, 23, 30), minutes=30)
        early = self._appointment(self.a, self.card[self.a], self._istanbul(2031, 3, 11, 0, 10))
        first = self._appointment(self.a, self.card[self.a], self._istanbul(2031, 3, 10, 0, 0))
        session_a = self._session(self.a)

        def day(date):
            res = self._day(session_a, date)
            self.assertEqual(res.status_code, 200, res.text)
            return [a["id"] for a in res.json()["practitioners"][0]["appointments"]]

        self.assertEqual(day("2031-03-10"), [first, late])
        self.assertEqual(day("2031-03-11"), [early])
        self.assertEqual(day("2031-03-09"), [])

    def test_the_timezone_can_be_configured(self):
        late = self._appointment(self.a, self.card[self.a], self._istanbul(2031, 3, 10, 23, 30), minutes=30)
        early = self._appointment(self.a, self.card[self.a], self._istanbul(2031, 3, 11, 0, 10))
        with mock.patch.dict(os.environ, {"VHV_TIMEZONE": "UTC"}):
            res = self._day(self._session(self.a), "2031-03-10")
        self.assertEqual(res.json()["timezone"], "UTC")
        self.assertEqual([a["id"] for a in res.json()["practitioners"][0]["appointments"]], [late, early])

    def test_a_bad_date_is_refused(self):
        for date in ("2031-13-01", "tomorrow", "2031-03-10T00:00:00", ""):
            with self.subTest(date=date):
                self.assertEqual(self._day(self.desk_session, date).status_code, 422)

    def test_cancelled_and_closed_appointments_show_their_status(self):
        session_a = self._session(self.a)
        self.client.patch(f"/api/v1/appointments/{self.appt[self.a]}", headers=session_a,
                          json={"status": "cancelled"})
        day = datetime.fromtimestamp(self.future, tz=timezone(timedelta(hours=3))).date().isoformat()
        items = self._day(session_a, day).json()["practitioners"][0]["appointments"]
        self.assertEqual([a["status"] for a in items], ["cancelled"])


class TestStaffMigration(unittest.TestCase):
    """practice_staff used to have the secretary's username as its primary key.
    SQLite cannot change a primary key in place, so init_db() rebuilds the
    table: new table, copy the rows, swap. It runs on every start."""

    OLD_TABLE = """
        CREATE TABLE practice_staff (
            staff_username        VARCHAR(100) PRIMARY KEY,
            practitioner_username VARCHAR(100) NOT NULL,
            created_at            REAL NOT NULL
        )"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(os.path.join(self.dir.name, "old.db"))
        self.conn.execute(self.OLD_TABLE)
        self.conn.executemany(
            "INSERT INTO practice_staff (staff_username, practitioner_username, created_at) VALUES (?, ?, ?)",
            [("sec.one", "psk.one", 1.0), ("sec.two", "psk.two", 2.0)])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def _migrate(self):
        cur = self.conn.cursor()
        sql_db.migrate_practice_staff(cur, is_postgres=False)
        self.conn.commit()
        cur.close()

    def _primary_key(self):
        rows = self.conn.execute("PRAGMA table_info(practice_staff)").fetchall()
        return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5]]

    def _links(self):
        return sorted(self.conn.execute(
            "SELECT staff_username, practitioner_username, created_at FROM practice_staff").fetchall())

    def test_old_links_are_kept_and_the_key_becomes_the_pair(self):
        self._migrate()
        self.assertEqual(self._primary_key(), ["staff_username", "practitioner_username"])
        self.assertEqual(self._links(), [("sec.one", "psk.one", 1.0), ("sec.two", "psk.two", 2.0)])
        # A secretary can now work for a second practitioner, but not twice for one.
        self.conn.execute("INSERT INTO practice_staff VALUES ('sec.one', 'psk.two', 3.0)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO practice_staff VALUES ('sec.one', 'psk.two', 4.0)")

    def test_running_it_again_changes_nothing(self):
        self._migrate()
        self.conn.execute("INSERT INTO practice_staff VALUES ('sec.one', 'psk.two', 3.0)")
        self.conn.commit()
        self._migrate()
        self._migrate()
        self.assertEqual(self._primary_key(), ["staff_username", "practitioner_username"])
        self.assertEqual(len(self._links()), 3)
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        self.assertEqual(tables, ["practice_staff"])

    def test_a_half_done_rebuild_is_redone(self):
        # A crash between creating the new table and the swap leaves it behind.
        self.conn.execute("CREATE TABLE practice_staff_new (staff_username VARCHAR(100))")
        self.conn.commit()
        self._migrate()
        self.assertEqual(len(self._links()), 2)
        self.assertEqual(self._primary_key(), ["staff_username", "practitioner_username"])

    def test_start_up_keeps_the_seeded_link(self):
        default_sql_db.init_db()
        default_sql_db.init_db()
        default_sql_db.seed_default_users()
        self.assertEqual(appointment_book.practitioners_of("secretary.ayse"), ["psk.elif"])


if __name__ == "__main__":
    unittest.main()
