"""
tests/test_tasks.py — the staff task list ("İşler")
====================================================
The front desk and the practitioners keep a shared to-do list of practical
work: call someone back, get the KVKK forms signed, confirm an appointment
(core/services/tasks.py, backend/routers/tasks.py):

  * fixed codes for everything (type, priority, status) and no free-text
    field at all: the table is not encrypted, so nothing clinical may be typed
    into it. What a task is about comes from its type and the one item it is
    linked to (a client card, an application or an appointment);
  * a task belongs to one practitioner's book. The practitioner sees the
    tasks in their book, a secretary those in every book they are linked to
    (read on every request), operators only counts. A linked item must be in
    the same book; unknown and not-yours are the same 404;
  * views: today (due today or overdue), upcoming, done — sorted by due, then
    priority; open -> done / cancelled, and back with reopen;
  * automatic tasks: a client card without a KVKK date gets one kvkk_forms
    task, an application left "new" longer than VHV_TASK_FOLLOWUP_HOURS gets
    one follow_up_application task;
  * KVKK: an erased client's tasks go with the erasure, closed tasks are
    deleted after VHV_TASK_RETENTION_DAYS (180), every change is audited with
    IDs only.
"""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from datetime import date, datetime, timedelta
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.events.event_bus import event_bus
from core.security import hash_password
from core.services import appointment_book, client_registry, intake, tasks
from database import sql_db
from database.sql_db import default_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Center@Tasks2026!"
OPERATORS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}
DAY = 86400


def _today() -> date:
    return tasks.today()


def _on(days: int) -> str:
    return (_today() + timedelta(days=days)).isoformat()


class TasksTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        self.users, self.cards, self.apps, self.appointments, self.tasks = [], [], [], [], []
        # Other test files delete client cards directly (in the application
        # only erasure removes one, and it takes the tasks along), so a card ID
        # can come round again here. Drop what such cards left behind.
        run_sql("DELETE FROM tasks WHERE link_type = ? AND link_id NOT IN (SELECT patient_id FROM clients)",
                ("client",))
        run_sql("DELETE FROM task_triggers WHERE trigger_key LIKE ? AND "
                "SUBSTR(trigger_key, 6) NOT IN (SELECT patient_id FROM clients)", ("kvkk:%",))
        self.a, self.b = self._user("practitioner"), self._user("practitioner")
        self.desk = self._user("secretary")      # runs A's book only
        self.other_desk = self._user("secretary")  # runs B's book only
        appointment_book.link_staff(self.desk, self.a)
        appointment_book.link_staff(self.other_desk, self.b)
        self.sa, self.sb = self._session(self.a), self._session(self.b)
        self.sdesk = self._session(self.desk)
        self.card_a = self._card(self.a, "Deniz Aydın", kvkk="2026-01-01")
        self.card_b = self._card(self.b, "Mert Kaya", kvkk="2026-01-01")

    def tearDown(self):
        for task_id in self.tasks:
            run_sql("DELETE FROM tasks WHERE id = ?", (task_id,))
        for owner in (self.a, self.b):
            run_sql("DELETE FROM tasks WHERE practitioner_username = ?", (owner,))
        for app_id in self.apps:
            run_sql("DELETE FROM applications WHERE id = ?", (app_id,))
            run_sql("DELETE FROM task_triggers WHERE trigger_key = ?", (f"followup:{app_id}",))
        for appointment_id in self.appointments:
            run_sql("DELETE FROM appointments WHERE id = ?", (appointment_id,))
        for patient_id in self.cards:
            run_sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
            run_sql("DELETE FROM task_triggers WHERE trigger_key = ?", (f"kvkk:{patient_id}",))
        for username in self.users:
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

    def _card(self, owner, name, kvkk=None):
        card = client_registry.add(practitioner=owner, full_name=name, phone="+90 532 000 11 22", email=None,
                                   kvkk_signed_on=kvkk, created_by="test")
        self.cards.append(card["patient_id"])
        return card["patient_id"]

    def _appointment(self, owner, patient_id, days=2):
        apt = appointment_book.add_existing(practitioner=owner, patient_id=patient_id,
                                            starts_at=time.time() + days * DAY, duration_min=50,
                                            session_format="In-person", status="scheduled", created_by="test")
        self.appointments.append(apt)
        return apt

    def _application(self, received_hours_ago=0, **fields):
        data = {"channel": "phone", "full_name": "Selin Aksoy", "phone": "+90 532 000 00 99", "email": None,
                "session_format": "either", "preferred_days": [], "preferred_times": [], "priority": "normal",
                "preferred_practitioner": None, "assigned_practitioner": None}
        data.update(fields)
        app_ = intake.create(data, by="test", received_at=time.time() - received_hours_ago * 3600)
        self.apps.append(app_["id"])
        return app_["id"]

    def _body(self, **over):
        body = {"type": "call_back", "due_on": _on(0), "priority": "normal"}
        body.update(over)
        return body

    def _create(self, headers=None, practitioner=None, **over):
        params = {"practitioner": practitioner} if practitioner else {}
        res = self.client.post("/api/v1/tasks", headers=headers or self.sa, params=params, json=self._body(**over))
        if res.status_code == 200:
            self.tasks.append(res.json()["task"]["id"])
        return res

    def _id(self, headers=None, practitioner=None, **over):
        res = self._create(headers, practitioner, **over)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["task"]["id"]

    def _list(self, headers, **params):
        res = self.client.get("/api/v1/tasks", headers=headers, params=params)
        self.assertEqual(res.status_code, 200, res.text)
        return [t["id"] for t in res.json()["tasks"] if t["id"] in self.tasks or t["practitioner"] in (self.a, self.b)]

    def _action(self, headers, task_id, action):
        return self.client.post(f"/api/v1/tasks/{task_id}/{action}", headers=headers)


class TestTasks(TasksTestCase):
    def test_a_practitioner_creates_a_task_linked_to_their_client(self):
        res = self._create(type="call_back", link_type="client", link_id=self.card_a, due_time="14:30",
                           priority="high")
        self.assertEqual(res.status_code, 200, res.text)
        t = res.json()["task"]
        self.assertEqual(t["type"], "call_back")
        self.assertEqual(t["practitioner"], self.a)
        self.assertEqual(t["status"], "open")
        self.assertEqual(t["priority"], "high")
        self.assertEqual(t["due_on"], _on(0))
        self.assertEqual(t["due_time"], "14:30")
        self.assertIsNone(t["assignee"])
        self.assertEqual(t["link"]["type"], "client")
        self.assertEqual(t["link"]["id"], self.card_a)
        self.assertEqual(t["link"]["label"], "Deniz Aydın")
        stored = tasks.get(t["id"])
        self.assertEqual(stored["created_by"], self.a)
        self.assertIsNotNone(stored["created_at"])

    def test_links_to_an_application_and_an_appointment(self):
        apt = self._appointment(self.a, self.card_a)
        t = self.client.get(f"/api/v1/tasks/{self._id(type='confirm_appointment', link_type='appointment', link_id=apt)}",
                            headers=self.sa).json()["task"]
        self.assertEqual(t["link"]["label"], "Deniz Aydın")
        self.assertIsNotNone(t["link"]["starts_at"])
        app_id = self._application(assigned_practitioner=self.a)
        t = self.client.get(f"/api/v1/tasks/{self._id(type='follow_up_application', link_type='application', link_id=app_id)}",
                            headers=self.sa).json()["task"]
        self.assertEqual(t["link"]["label"], "Selin Aksoy")
        # An unassigned application that prefers A is in A's book too.
        prefers_a = self._application(preferred_practitioner=self.a)
        self.assertEqual(self._create(link_type="application", link_id=prefers_a).status_code, 200)

    def test_fixed_codes_only(self):
        for field, value in (("type", "therapy"), ("priority", "urgent"), ("link_type", "record"),
                             ("due_on", "2026-13-01"), ("due_on", "tomorrow"), ("due_time", "25:00"),
                             ("due_time", "9am")):
            with self.subTest(field=field, value=value):
                self.assertEqual(self._create(**{field: value}).status_code, 422)
        # A link is a type and an ID together.
        self.assertEqual(self._create(link_type="client").status_code, 422)
        self.assertEqual(self._create(link_id=self.card_a).status_code, 422)

    def test_no_free_text_field_is_accepted(self):
        """There is no title or note: any field the schema does not know is
        refused, so nothing clinical can be typed into this table."""
        for field in ("title", "label", "note", "notes", "description", "summary", "text"):
            with self.subTest(field=field):
                res = self._create(**{field: "Panik atak geçirdi, acil aranmalı"})
                self.assertEqual(res.status_code, 422, res.text)
        task_id = self._id()
        res = self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sa, json={"note": "Depresyon"})
        self.assertEqual(res.status_code, 422)
        if not default_sql_db.is_postgres:
            columns = {r[1] for r in run_sql("PRAGMA table_info(tasks)", fetch="all")}
            for column in ("title", "label", "note", "notes", "description", "summary"):
                self.assertNotIn(column, columns)

    def test_transitions(self):
        task_id = self._id()
        res = self._action(self.sa, task_id, "done")
        self.assertEqual(res.status_code, 200, res.text)
        t = res.json()["task"]
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["done_by"], self.a)
        self.assertIsNotNone(t["done_at"])
        self.assertEqual(self._action(self.sa, task_id, "done").status_code, 409)
        self.assertEqual(self._action(self.sa, task_id, "cancel").status_code, 409)
        t = self._action(self.sa, task_id, "reopen").json()["task"]
        self.assertEqual(t["status"], "open")
        self.assertIsNone(t["done_by"])
        self.assertEqual(self._action(self.sa, task_id, "reopen").status_code, 409)
        self.assertEqual(self._action(self.sa, task_id, "cancel").json()["task"]["status"], "cancelled")
        self.assertEqual(self._action(self.sa, task_id, "reopen").json()["task"]["status"], "open")

    def test_editing_a_task(self):
        task_id = self._id()
        res = self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sdesk,
                                json={"priority": "high", "due_on": _on(3), "due_time": "09:00",
                                      "assignee": self.desk, "type": "kvkk_forms",
                                      "link_type": "client", "link_id": self.card_a})
        self.assertEqual(res.status_code, 200, res.text)
        t = res.json()["task"]
        self.assertEqual((t["priority"], t["due_on"], t["due_time"], t["assignee"], t["type"]),
                         ("high", _on(3), "09:00", self.desk, "kvkk_forms"))
        self.assertEqual(tasks.get(task_id)["updated_by"], self.desk)
        # The link can be removed again, and a time cleared.
        t = self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sa,
                              json={"link_type": None, "link_id": None, "due_time": None}).json()["task"]
        self.assertIsNone(t["link"])
        self.assertIsNone(t["due_time"])
        # A closed task is not edited.
        self._action(self.sa, task_id, "done")
        res = self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sa, json={"priority": "normal"})
        self.assertEqual(res.status_code, 409)

    def test_assignee_works_in_the_book(self):
        self.assertEqual(self._create(assignee=self.a).status_code, 200)
        self.assertEqual(self._create(assignee=self.desk).status_code, 200)
        for nobody in (self.other_desk, self.b, "nobody.at.all"):
            with self.subTest(assignee=nobody):
                self.assertEqual(self._create(assignee=nobody).status_code, 422)

    def test_every_change_is_audited_with_ids_only(self):
        with mock.patch.object(event_bus, "publish") as publish:
            task_id = self._id(link_type="client", link_id=self.card_a)
            self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sa, json={"priority": "high"})
            self._action(self.sa, task_id, "done")
            self._action(self.sa, task_id, "reopen")
            self._action(self.sa, task_id, "cancel")
        events = [c.args[0] for c in publish.call_args_list if c.args[0].action.startswith("TASK")]
        self.assertEqual([e.action for e in events],
                         ["TASK_CREATED", "TASK_UPDATED", "TASK_DONE", "TASK_REOPENED", "TASK_CANCELLED"])
        for e in events:
            self.assertEqual(e.username, self.a)
            self.assertEqual(e.extra["task_id"], task_id)
            text = repr(e.extra)
            for value in ("Deniz", "Aydın", "532"):
                self.assertNotIn(value, text)
        self.assertEqual(events[0].extra["link_id"], self.card_a)


class TestSameBook(TasksTestCase):
    def test_a_link_must_be_in_the_same_book(self):
        apt_b = self._appointment(self.b, self.card_b)
        app_b = self._application(assigned_practitioner=self.b)
        app_none = self._application()   # the shared inbox: nobody's book yet
        for link_type, link_id in (("client", self.card_b), ("appointment", apt_b), ("application", app_b),
                                   ("application", app_none), ("client", "CL-999999"),
                                   ("appointment", "APT-NOPE"), ("application", "APP-NOPE")):
            with self.subTest(link=(link_type, link_id)):
                res = self._create(link_type=link_type, link_id=link_id)
                self.assertEqual(res.status_code, 404, res.text)
                self.assertNotIn("Mert", res.text)
        # Not on an edit either.
        task_id = self._id()
        res = self.client.patch(f"/api/v1/tasks/{task_id}", headers=self.sa,
                                json={"link_type": "client", "link_id": self.card_b})
        self.assertEqual(res.status_code, 404)

    def test_a_secretary_creates_in_a_book_they_run(self):
        res = self._create(headers=self.sdesk, link_type="client", link_id=self.card_a)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["task"]["practitioner"], self.a)
        self.assertEqual(self._create(headers=self.sdesk, practitioner=self.b).status_code, 403)


class TestScoping(TasksTestCase):
    def setUp(self):
        super().setUp()
        self.task_a = self._id()
        self.task_b = self._id(headers=self.sb)

    def test_practitioners_see_their_own_book(self):
        self.assertEqual(set(self._list(self.sa)), {self.task_a})
        self.assertEqual(set(self._list(self.sb)), {self.task_b})
        for action in ("done", "cancel", "reopen"):
            with self.subTest(action=action):
                self.assertEqual(self._action(self.sa, self.task_b, action).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/tasks/{self.task_b}", headers=self.sa).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/tasks/TSK-NOPE", headers=self.sa).status_code, 404)
        res = self.client.patch(f"/api/v1/tasks/{self.task_b}", headers=self.sa, json={"priority": "high"})
        self.assertEqual(res.status_code, 404)

    def test_a_secretary_sees_every_book_they_are_linked_to(self):
        self.assertEqual(set(self._list(self.sdesk)), {self.task_a})
        appointment_book.link_staff(self.desk, self.b)
        self.assertEqual(set(self._list(self.sdesk)), {self.task_a, self.task_b})
        self.assertEqual(set(self._list(self.sdesk, practitioner=self.b)), {self.task_b})
        # A book they do not run, or one that does not exist: refused alike.
        for book in ("nobody.at.all", self.a + "x"):
            res = self.client.get("/api/v1/tasks", headers=self.sb, params={"practitioner": book})
            self.assertEqual(res.status_code, 403)

    def test_unlinking_cuts_access_at_once(self):
        self.assertEqual(self.client.get(f"/api/v1/tasks/{self.task_a}", headers=self.sdesk).status_code, 200)
        appointment_book.unlink_staff(self.desk, self.a)
        self.assertEqual(self.client.get(f"/api/v1/tasks/{self.task_a}", headers=self.sdesk).status_code, 404)
        self.assertEqual(self._action(self.sdesk, self.task_a, "done").status_code, 404)
        # A secretary linked to nobody has no tasks at all.
        self.assertEqual(self.client.get("/api/v1/tasks", headers=self.sdesk).status_code, 403)

    def test_operators_and_auditors_see_counts_only(self):
        auditor = self._session(self._user("auditor"))
        for name, headers in (("admin", self._session(*OPERATORS["admin"])),
                              ("officer", self._session(*OPERATORS["officer"])), ("auditor", auditor)):
            with self.subTest(operator=name):
                self.assertEqual(self.client.get("/api/v1/tasks", headers=headers).status_code, 403)
                self.assertEqual(self.client.get(f"/api/v1/tasks/{self.task_a}", headers=headers).status_code, 403)
                self.assertEqual(self.client.post("/api/v1/tasks", headers=headers,
                                                  json=self._body()).status_code, 403)
                self.assertEqual(self._action(headers, self.task_a, "done").status_code, 403)
                self.assertEqual(self.client.get("/api/v1/tasks/options", headers=headers).status_code, 403)
                res = self.client.get("/api/v1/tasks/summary", headers=headers)
                self.assertEqual(res.status_code, 200, res.text)
                counts = res.json()["counts"]
                self.assertGreaterEqual(counts["open"], 2)
                self.assertNotIn(self.task_a, res.text)
                self.assertNotIn(self.a, res.text)

    def test_summary_is_scoped_for_staff(self):
        self._id(due_on=_on(-2))           # overdue
        self._id(due_on=_on(5))            # upcoming
        counts = self.client.get("/api/v1/tasks/summary", headers=self.sa).json()["counts"]
        self.assertEqual(counts, {"today": 2, "overdue": 1, "upcoming": 1})
        self.assertEqual(self.client.get("/api/v1/tasks/summary", headers=self.sb).json()["counts"]["today"], 1)

    def test_the_options_are_the_books_own(self):
        apt = self._appointment(self.a, self.card_a)
        app_a = self._application(assigned_practitioner=self.a)
        self._application(assigned_practitioner=self.b, full_name="Başka Biri")
        res = self.client.get("/api/v1/tasks/options", headers=self.sdesk)
        self.assertEqual(res.status_code, 200, res.text)
        d = res.json()
        self.assertIn(self.card_a, [c["id"] for c in d["clients"]])
        self.assertNotIn(self.card_b, [c["id"] for c in d["clients"]])
        self.assertIn(apt, [c["id"] for c in d["appointments"]])
        self.assertIn(app_a, [c["id"] for c in d["applications"]])
        self.assertNotIn("Başka Biri", res.text)
        self.assertEqual({p["username"] for p in d["assignees"]}, {self.a, self.desk})


class TestViews(TasksTestCase):
    def test_today_upcoming_and_done(self):
        overdue = self._id(due_on=_on(-1))
        today = self._id(due_on=_on(0))
        later = self._id(due_on=_on(4))
        done = self._id(due_on=_on(0))
        cancelled = self._id(due_on=_on(1))
        self._action(self.sa, done, "done")
        self._action(self.sa, cancelled, "cancel")
        self.assertEqual(self._list(self.sa), [overdue, today])          # the default view
        self.assertEqual(self._list(self.sa, view="upcoming"), [later])
        self.assertEqual(set(self._list(self.sa, view="done")), {done, cancelled})
        self.assertEqual(self.client.get("/api/v1/tasks", headers=self.sa, params={"view": "all"}).status_code, 422)

    def test_overdue_is_marked(self):
        overdue = self._id(due_on=_on(-1))
        later = self._id(due_on=_on(2))
        items = {t["id"]: t for t in self.client.get("/api/v1/tasks", headers=self.sa,
                                                     params={"view": "today"}).json()["tasks"]}
        self.assertTrue(items[overdue]["overdue"])
        up = {t["id"]: t for t in self.client.get("/api/v1/tasks", headers=self.sa,
                                                  params={"view": "upcoming"}).json()["tasks"]}
        self.assertFalse(up[later]["overdue"])
        # Today with a time already passed is overdue too.
        self.assertTrue(tasks.is_overdue({"status": "open", "due_on": _on(0), "due_time": "00:00"},
                                         now=datetime.combine(_today(), datetime.min.time()).replace(hour=12)))
        self.assertFalse(tasks.is_overdue({"status": "open", "due_on": _on(0), "due_time": None},
                                          now=datetime.combine(_today(), datetime.min.time()).replace(hour=12)))

    def test_sorted_by_due_then_priority(self):
        late_normal = self._id(due_on=_on(3), due_time="16:00")
        early_normal = self._id(due_on=_on(2))
        early_high = self._id(due_on=_on(2), priority="high")
        late_early = self._id(due_on=_on(3), due_time="09:00")
        self.assertEqual(self._list(self.sa, view="upcoming"), [early_high, early_normal, late_early, late_normal])

    def test_filters(self):
        apt = self._appointment(self.a, self.card_a)
        mine = self._id(assignee=self.desk, type="confirm_appointment", link_type="appointment", link_id=apt)
        theirs = self._id(assignee=self.a)
        nobody = self._id()
        self.assertEqual(self._list(self.sdesk, assignee="me"), [mine])
        self.assertEqual(self._list(self.sdesk, assignee=self.a), [theirs])
        self.assertEqual(self._list(self.sdesk, assignee="unassigned"), [nobody])
        self.assertEqual(self._list(self.sdesk, type="confirm_appointment"), [mine])
        self.assertEqual(self.client.get("/api/v1/tasks", headers=self.sa,
                                         params={"type": "therapy"}).status_code, 422)


class TestAutomaticTasks(TasksTestCase):
    def _auto(self, kind, owner=None):
        rows = run_sql("SELECT id, practitioner_username, link_id FROM tasks WHERE type = ? AND created_by = ?",
                       (kind, tasks.SYSTEM), fetch="all")
        return [r for r in rows if owner is None or r[1] == owner]

    def test_a_card_without_kvkk_forms_gets_one_task(self):
        res = self.client.post("/api/v1/clients", headers=self.sdesk, json={"full_name": "Ece Demir"})
        self.assertEqual(res.status_code, 200, res.text)
        patient_id = res.json()["client"]["patient_id"]
        self.cards.append(patient_id)
        found = [r for r in self._auto("kvkk_forms", self.a) if r[2] == patient_id]
        self.assertEqual(len(found), 1)
        self.tasks.append(found[0][0])
        listed = [t for t in self.client.get("/api/v1/tasks", headers=self.sdesk).json()["tasks"]
                  if t["id"] == found[0][0]]
        self.assertEqual(listed[0]["link"]["label"], "Ece Demir")
        self.assertTrue(listed[0]["automatic"])
        # Once: asking again creates nothing.
        self.assertIsNone(tasks.on_client_created(client_registry.get(patient_id)))
        self.assertEqual(len([r for r in self._auto("kvkk_forms") if r[2] == patient_id]), 1)
        # A card with the date gets none.
        signed = self._card(self.a, "İmzalı Danışan", kvkk="2026-02-02")
        self.assertEqual([r for r in self._auto("kvkk_forms") if r[2] == signed], [])

    def test_signing_the_forms_closes_the_task(self):
        patient_id = self._card(self.a, "Ece Demir")
        (task_id, _, _), = [r for r in self._auto("kvkk_forms") if r[2] == patient_id]
        res = self.client.patch(f"/api/v1/clients/{patient_id}", headers=self.sdesk,
                                json={"kvkk_signed_on": "2026-10-01"})
        self.assertEqual(res.status_code, 200, res.text)
        t = tasks.get(task_id)
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["done_by"], self.desk)

    def test_an_application_left_new_gets_one_follow_up(self):
        old = self._application(received_hours_ago=30, assigned_practitioner=self.a)
        fresh = self._application(received_hours_ago=2, assigned_practitioner=self.a)
        prefers_b = self._application(received_hours_ago=30, preferred_practitioner=self.b)
        nobodys = self._application(received_hours_ago=30)
        contacted = self._application(received_hours_ago=30, assigned_practitioner=self.a)
        intake.set_status(intake.get(contacted), "contacted", by="test")
        created = tasks.run_automatic()
        linked = {r[2]: r[1] for r in self._auto("follow_up_application")}
        self.assertEqual(linked.get(old), self.a)
        self.assertEqual(linked.get(prefers_b), self.b)
        for app_id in (fresh, nobodys, contacted):
            self.assertNotIn(app_id, linked)
        self.assertGreaterEqual(len(created), 2)
        # Once: the next run, and a run after the task is done, add nothing.
        (task_id,) = [r[0] for r in self._auto("follow_up_application") if r[2] == old]
        tasks.set_status(tasks.get(task_id), "done", by="test")
        tasks.run_automatic()
        self.assertEqual(len([r for r in self._auto("follow_up_application") if r[2] == old]), 1)
        # The fresh one, a day later (time patched).
        later = time.time() + 2 * DAY
        with mock.patch("core.services.tasks.time.time", return_value=later):
            tasks.run_automatic()
        self.assertEqual(len([r for r in self._auto("follow_up_application") if r[2] == fresh]), 1)

    def test_the_threshold_is_configurable(self):
        app_id = self._application(received_hours_ago=5, assigned_practitioner=self.a)
        with mock.patch.dict(os.environ, {"VHV_TASK_FOLLOWUP_HOURS": "4"}):
            self.assertEqual(tasks.followup_hours(), 4)
            tasks.run_automatic()
        self.assertEqual(len([r for r in self._auto("follow_up_application") if r[2] == app_id]), 1)
        for raw in ("", "x", "0", "-3"):
            with mock.patch.dict(os.environ, {"VHV_TASK_FOLLOWUP_HOURS": raw}):
                self.assertEqual(tasks.followup_hours(), 24)

    def test_the_check_runs_when_the_list_is_read(self):
        app_id = self._application(received_hours_ago=30, assigned_practitioner=self.a)
        with mock.patch.object(tasks, "_last_run", 0.0):
            self.client.get("/api/v1/tasks", headers=self.sa)
        self.assertEqual(len([r for r in self._auto("follow_up_application") if r[2] == app_id]), 1)


class TestKvkk(TasksTestCase):
    def test_erasure_removes_the_clients_tasks(self):
        apt = self._appointment(self.a, self.card_a)
        app_id = self._application(assigned_practitioner=self.a)
        run_sql("UPDATE applications SET client_id = ? WHERE id = ?", (self.card_a, app_id))
        on_card = self._id(link_type="client", link_id=self.card_a)
        on_apt = self._id(link_type="appointment", link_id=apt)
        on_app = self._id(link_type="application", link_id=app_id)
        other = self._id()
        removed = tasks.remove_for_client(self.card_a)
        self.assertEqual(removed, 3)
        for task_id in (on_card, on_apt, on_app):
            self.assertIsNone(tasks.get(task_id))
        self.assertIsNotNone(tasks.get(other))

    def test_the_erasure_endpoint_removes_them(self):
        """Through POST /api/v1/erasure, with its dual-control co-signature.
        The appointment-linked task shows the tasks go before the appointments."""
        patient = f"CL-8{uuid.uuid4().int % 100000:05d}"
        client_registry.add(practitioner=self.a, full_name="Silinecek Danışan", phone=None, email=None,
                            kvkk_signed_on="2026-01-01", created_by="test", patient_id=patient)
        self.cards.append(patient)
        apt = self._appointment(self.a, patient)
        on_card = self._id(link_type="client", link_id=patient)
        on_apt = self._id(link_type="appointment", link_id=apt)
        admin, officer = self._session(*OPERATORS["admin"]), self._session(*OPERATORS["officer"])
        res = self.client.post("/api/v1/security/dual-control/request", headers=admin,
                               json={"request_type": "ERASE_PATIENT", "target_patient_id": patient,
                                     "reason": "Data subject erasure request", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token = res.json()["token_id"]
        res = self.client.post("/api/v1/security/dual-control/co-sign", headers=officer, json={"token_id": token})
        self.assertEqual(res.status_code, 200, res.text)
        res = self.client.post(f"/api/v1/erasure/{patient}", headers={**admin, "X-Dual-Control-Token": token})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(tasks.get(on_card))
        self.assertIsNone(tasks.get(on_apt))

    def test_closed_tasks_are_deleted_after_the_period(self):
        done = self._id()
        cancelled = self._id()
        recent = self._id()
        still_open = self._id(due_on=_on(-300))
        tasks.set_status(tasks.get(done), "done", by="test")
        tasks.set_status(tasks.get(cancelled), "cancelled", by="test")
        tasks.set_status(tasks.get(recent), "done", by="test")
        old = time.time() - 181 * DAY
        run_sql("UPDATE tasks SET closed_at = ? WHERE id IN (?, ?)", (old, done, cancelled))
        with mock.patch.object(event_bus, "publish") as publish:
            purged = tasks.purge_expired()
        self.assertEqual(set(purged) & {done, cancelled, recent, still_open}, {done, cancelled})
        self.assertIsNone(tasks.get(done))
        self.assertIsNotNone(tasks.get(recent))
        self.assertIsNotNone(tasks.get(still_open))
        events = [c.args[0] for c in publish.call_args_list if c.args[0].action == "TASKS_PURGED"]
        self.assertEqual(len(events), 1)
        self.assertIn(done, events[0].extra["task_ids"])

    def test_the_period_is_configurable(self):
        task_id = self._id()
        tasks.set_status(tasks.get(task_id), "done", by="test")
        run_sql("UPDATE tasks SET closed_at = ? WHERE id = ?", (time.time() - 11 * DAY, task_id))
        self.assertNotIn(task_id, tasks.purge_expired())
        with mock.patch.dict(os.environ, {"VHV_TASK_RETENTION_DAYS": "10"}):
            self.assertEqual(tasks.retention_days(), 10)
            self.assertIn(task_id, tasks.purge_expired())
        with mock.patch.dict(os.environ, {"VHV_TASK_RETENTION_DAYS": "zero"}):
            self.assertEqual(tasks.retention_days(), 180)


class TestDemoData(unittest.TestCase):
    def test_demo_tasks_follow_the_rules(self):
        from backend.demo_seed import _DEMO_TASKS
        from backend.schemas.tasks import TaskReq
        for kind, link, days, due_time, priority, assignee, status in _DEMO_TASKS:
            with self.subTest(kind=kind, link=link):
                TaskReq(type=kind, due_on=_on(days), due_time=due_time, priority=priority)
                self.assertIn(status, tasks.STATUSES)


@unittest.skipIf(default_sql_db.is_postgres, "SQLite file migration")
class TestMigration(unittest.TestCase):
    def test_the_table_is_created_once_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", path):
                default_sql_db.init_db()
                conn = sqlite3.connect(path)
                conn.execute("INSERT INTO tasks (id, practitioner_username, type, due_on, priority, status, "
                             "created_by, created_at) VALUES ('TSK-OLD', 'psk.x', 'call_back', '2026-01-01', "
                             "'normal', 'open', 'x', 1)")
                conn.commit()
                conn.close()
                default_sql_db.init_db()   # safe to run on every start
                default_sql_db.init_db()
            conn = sqlite3.connect(path)
            try:
                row = conn.execute("SELECT type, status FROM tasks WHERE id = 'TSK-OLD'").fetchone()
                triggers = conn.execute("SELECT COUNT(*) FROM task_triggers").fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("call_back", "open"))
            self.assertEqual(triggers, (0,))

    def test_demo_tasks_are_seeded_once(self):
        from backend.demo_seed import _DEMO_TASKS, seed_demo_tasks
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", os.path.join(tmp, "demo.db")):
                default_sql_db.init_db()
                default_sql_db.seed_default_users()
                self.assertTrue(seed_demo_tasks())
                self.assertFalse(seed_demo_tasks())
                rows = run_sql("SELECT COUNT(*) FROM tasks", fetch="one")
            self.assertEqual(rows[0][0], len(_DEMO_TASKS))


if __name__ == "__main__":
    unittest.main()
