"""
tests/test_recurring_calendar.py — weekly series and the week calendar
======================================================================
Therapy sessions are usually weekly at the same time. A series books them in
one go:

  * weekly or every two weeks, the same wall-clock time in the server's
    timezone (VHV_TIMEZONE) — also across a clock change — ending after N
    sessions or on a date, never more than 52 sessions;
  * a preview lists every session with what stands in its way (the
    practitioner is busy, the room is busy, the time has passed) and writes
    nothing;
  * creating the series skips the dates the caller chose and writes the rest
    in one transaction, with the same checks as a single booking: if a session
    is no longer free, nothing is written and the answer names the date;
  * one session can be cancelled (or moved) on its own, or this one and every
    later scheduled one of the series at once.

The week view is the day view over seven days, Monday to Sunday in the
server's timezone, for every book the caller runs. The room week follows the
room day's rules: the caller's books in full, other books as busy times,
operators busy times only, everyone else refused.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, time as clock, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.domain.entities import User
from core.security import hash_password
from core.services import appointment_book, client_registry, rooms
from database import sql_db
from database.sql_db import default_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Center@Desk2026!"
ISTANBUL = ZoneInfo("Europe/Istanbul")
BUSY_FIELDS = {"starts_at", "ends_at", "mine"}


class SeriesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        self.users, self.cards, self.rooms = [], [], []
        self.doc = self._user("practitioner")
        self.card = self._card(self.doc)
        self.session = self._session(self.doc)
        # A random Monday years ahead, so runs never meet.
        monday = date(2031, 1, 6) + timedelta(weeks=uuid.uuid4().int % 400)
        self.monday = monday
        self.first = datetime.combine(monday + timedelta(days=1), clock(10, 0), tzinfo=ISTANBUL)

    def tearDown(self):
        for username in self.users:
            run_sql("DELETE FROM appointments WHERE practitioner_username = ?", (username,))
        for patient_id in self.cards:
            run_sql("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
        for room_id in self.rooms:
            run_sql("DELETE FROM appointments WHERE room_id = ?", (room_id,))
            run_sql("DELETE FROM rooms WHERE id = ?", (room_id,))
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

    def _card(self, practitioner, name=None):
        card = client_registry.add(practitioner=practitioner, full_name=name or f"Client of {practitioner}",
                                   phone=None, email=None, kvkk_signed_on=None, created_by=practitioner)
        self.cards.append(card["patient_id"])
        return card["patient_id"]

    def _session(self, username, password=PASSWORD):
        res = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _room(self):
        room = rooms.create(f"Oda S{uuid.uuid4().int % 100000}", by="admin")
        self.rooms.append(room["id"])
        return room["id"]

    def _body(self, **over):
        body = {"patient_id": self.card, "starts_at": self.first.isoformat(), "duration_min": 50,
                "session_format": "In-person", "frequency": "weekly", "count": 4}
        body.update(over)
        return {k: v for k, v in body.items() if v is not None}

    def _preview(self, headers=None, params=None, **over):
        return self.client.post("/api/v1/appointments/series/preview", headers=headers or self.session,
                                params=params or {}, json=self._body(**over))

    def _create(self, headers=None, params=None, **over):
        return self.client.post("/api/v1/appointments/series", headers=headers or self.session,
                                params=params or {}, json=self._body(**over))

    def _rows(self, practitioner=None):
        return run_sql("SELECT id, starts_at, status, series_id FROM appointments "
                       "WHERE practitioner_username = ? ORDER BY starts_at",
                       (practitioner or self.doc,), fetch="all")

    def _local(self, iso, tz=ISTANBUL):
        return datetime.fromisoformat(iso).astimezone(tz)

    def _single(self, practitioner, patient_id, at, room_id=None, minutes=50):
        return appointment_book.book(practitioner=practitioner, patient_id=patient_id,
                                     starts_at=at.timestamp(), duration_min=minutes,
                                     session_format="In-person", created_by="test", room_id=room_id)


class TestCreatingASeries(SeriesTestCase):
    def test_weekly_with_a_count(self):
        res = self._create()
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        items = body["appointments"]
        self.assertEqual(len(items), 4)
        self.assertTrue(body["series_id"])
        for k, item in enumerate(items):
            local = self._local(item["starts_at"])
            self.assertEqual(local.date(), self.first.date() + timedelta(weeks=k))
            self.assertEqual((local.hour, local.minute), (10, 0))
            self.assertEqual(item["series_id"], body["series_id"])
            self.assertEqual(item["status"], "scheduled")
            self.assertEqual(item["duration_min"], 50)
        self.assertEqual({r[3] for r in self._rows()}, {body["series_id"]})

    def test_every_two_weeks_until_a_date(self):
        until = self.first.date() + timedelta(weeks=6)
        res = self._create(frequency="biweekly", count=None, until=until.isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        dates = [self._local(a["starts_at"]).date() for a in res.json()["appointments"]]
        self.assertEqual(dates, [self.first.date() + timedelta(weeks=w) for w in (0, 2, 4, 6)])

    def test_until_is_inclusive_and_read_in_local_dates(self):
        until = self.first.date() + timedelta(weeks=2, days=-1)
        res = self._create(count=None, until=until.isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()["appointments"]), 2)

    def test_the_cap_is_52_sessions(self):
        self.assertEqual(self._preview(count=53).status_code, 422)
        res = self._create(count=53)
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn("52", res.json()["detail"])
        far = self.first.date() + timedelta(weeks=60)
        self.assertEqual(self._create(count=None, until=far.isoformat()).status_code, 422)
        self.assertEqual(self._rows(), [])
        res = self._preview(count=52)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()["occurrences"]), 52)

    def test_count_or_until_but_one_of_them(self):
        self.assertEqual(self._create(count=None).status_code, 422)
        until = (self.first.date() + timedelta(weeks=2)).isoformat()
        self.assertEqual(self._create(count=3, until=until).status_code, 422)
        self.assertEqual(self._create(count=0).status_code, 422)
        self.assertEqual(self._create(frequency="daily").status_code, 422)
        self.assertEqual(self._rows(), [])

    def test_only_this_books_clients(self):
        other = self._user("practitioner")
        res = self._create(patient_id=self._card(other))
        self.assertEqual(res.status_code, 404, res.text)

    def test_the_series_keeps_its_room_and_format(self):
        room_id = self._room()
        res = self._create(room_id=room_id)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual({a["room_id"] for a in res.json()["appointments"]}, {room_id})
        res = self._create(session_format="Online", room_id=room_id,
                           starts_at=(self.first + timedelta(hours=3)).isoformat())
        self.assertEqual(res.status_code, 422, res.text)

    def test_single_appointments_carry_no_series(self):
        a = self._single(self.doc, self.card, self.first)
        self.assertIsNone(a["series_id"])
        res = self.client.get("/api/v1/appointments", headers=self.session, params={
            "start": (self.first - timedelta(days=1)).isoformat(),
            "end": (self.first + timedelta(days=1)).isoformat()})
        (item,) = res.json()["appointments"]
        self.assertIsNone(item["series_id"])


class TestWallClock(SeriesTestCase):
    def test_the_time_stays_fixed_across_a_clock_change(self):
        """Berlin moves from UTC+1 to UTC+2 on the last Sunday of March 2032
        (28 March): 10:00 stays 10:00 local, so UTC moves from 09:00 to 08:00."""
        berlin = ZoneInfo("Europe/Berlin")
        first = datetime(2032, 3, 16, 10, 0, tzinfo=berlin)
        with mock.patch.dict(os.environ, {"VHV_TIMEZONE": "Europe/Berlin"}):
            preview = self._preview(starts_at=first.isoformat(), count=4)
            res = self._create(starts_at=first.isoformat(), count=4)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["timezone"], "Europe/Berlin")
        self.assertEqual(res.status_code, 200, res.text)
        starts = [datetime.fromisoformat(a["starts_at"]) for a in res.json()["appointments"]]
        self.assertEqual([s.astimezone(berlin).hour for s in starts], [10, 10, 10, 10])
        self.assertEqual([s.astimezone(timezone.utc).hour for s in starts], [9, 9, 8, 8])
        self.assertEqual([datetime.fromisoformat(o["starts_at"]) for o in preview.json()["occurrences"]], starts)

    def test_the_first_session_is_read_in_the_server_timezone(self):
        """A browser elsewhere sends 07:00 UTC; in Istanbul that is 10:00, and
        every session is 10:00 Istanbul."""
        res = self._create(starts_at=self.first.astimezone(timezone.utc).isoformat(), count=3)
        self.assertEqual(res.status_code, 200, res.text)
        for item in res.json()["appointments"]:
            self.assertEqual(self._local(item["starts_at"]).hour, 10)


class TestWallClockForms(SeriesTestCase):
    """The forms send the date and time the user picked (local_date,
    local_time); the server reads them in its own timezone, whatever the
    browser's. They win over starts_at when both are sent."""
    ZONE = "America/New_York"

    def setUp(self):
        super().setUp()
        self.zone = ZoneInfo(self.ZONE)
        self.day = self.first.date()
        self.env = mock.patch.dict(os.environ, {"VHV_TIMEZONE": self.ZONE})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _wall(self, iso):
        local = datetime.fromisoformat(iso).astimezone(self.zone)
        return local.date(), local.strftime("%H:%M")

    def _book(self, **body):
        return self.client.post("/api/v1/appointments", headers=self.session,
                                json={"patient_id": self.card, "duration_min": 50, "session_format": "In-person",
                                      **body})

    def test_a_single_booking(self):
        res = self._book(local_date=self.day.isoformat(), local_time="10:00")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._wall(res.json()["appointment"]["starts_at"]), (self.day, "10:00"))

    def test_the_wall_clock_wins_over_starts_at(self):
        res = self._book(local_date=self.day.isoformat(), local_time="11:15",
                         starts_at=self.first.astimezone(timezone.utc).isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._wall(res.json()["appointment"]["starts_at"]), (self.day, "11:15"))

    def test_starts_at_alone_still_works(self):
        res = self._book(starts_at=self.first.isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(datetime.fromisoformat(res.json()["appointment"]["starts_at"]), self.first)

    def test_incomplete_or_malformed_times_are_refused(self):
        for body in ({}, {"local_date": self.day.isoformat()}, {"local_time": "10:00"},
                     {"local_date": self.day.isoformat(), "local_time": "25:00"},
                     {"local_date": "next tuesday", "local_time": "10:00"}):
            with self.subTest(body=body):
                self.assertEqual(self._book(**body).status_code, 422)
        self.assertEqual(self._rows(), [])

    def test_a_series_and_its_preview(self):
        body = {"starts_at": None, "local_date": self.day.isoformat(), "local_time": "18:30", "count": 3}
        preview = self._preview(**body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["timezone"], self.ZONE)
        self.assertEqual([o["time"] for o in preview.json()["occurrences"]], ["18:30"] * 3)
        res = self._create(**body)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([self._wall(a["starts_at"]) for a in res.json()["appointments"]],
                         [(self.day + timedelta(weeks=k), "18:30") for k in range(3)])

    def test_moving_an_appointment(self):
        a = self._book(local_date=self.day.isoformat(), local_time="10:00").json()["appointment"]
        later = self.day + timedelta(days=1)
        res = self.client.patch(f"/api/v1/appointments/{a['id']}", headers=self.session,
                                json={"local_date": later.isoformat(), "local_time": "16:45"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._wall(res.json()["appointment"]["starts_at"]), (later, "16:45"))
        half = self.client.patch(f"/api/v1/appointments/{a['id']}", headers=self.session,
                                 json={"local_time": "17:00"})
        self.assertEqual(half.status_code, 422)

    def test_the_list_names_the_timezone(self):
        res = self.client.get("/api/v1/appointments", headers=self.session)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["timezone"], self.ZONE)


class TestMoveFollowing(SeriesTestCase):
    """Move this session of a series and every later scheduled one to another
    weekday / time and/or room: previewed first (nothing written), then
    applied whole or not at all."""

    def _series(self, count=5, **over):
        res = self._create(count=count, **over)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["appointments"]

    def _move(self, appointment_id, preview=False, headers=None, **body):
        path = f"/api/v1/appointments/{appointment_id}/move-following" + ("/preview" if preview else "")
        return self.client.post(path, headers=headers or self.session, json=body)

    def _starts(self):
        return [r[1] for r in self._rows()]

    def test_preview_lists_the_new_times_and_writes_nothing(self):
        items = self._series()
        before = self._starts()
        # From week 2 on: Thursday 14:00 instead of Tuesday 10:00.
        new_day = (self.first + timedelta(weeks=2, days=2)).date()
        res = self._move(items[2]["id"], preview=True, local_date=new_day.isoformat(), local_time="14:00")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["timezone"], "Europe/Istanbul")
        sessions = body["sessions"]
        self.assertEqual([s["id"] for s in sessions], [a["id"] for a in items[2:]])
        self.assertEqual([s["date"] for s in sessions],
                         [(new_day + timedelta(weeks=k)).isoformat() for k in range(3)])
        self.assertEqual({s["time"] for s in sessions}, {"14:00"})
        self.assertEqual([s["conflicts"] for s in sessions], [[], [], []])
        self.assertEqual(self._starts(), before)

    def test_apply_moves_this_and_every_later_one(self):
        items = self._series()
        self.client.patch(f"/api/v1/appointments/{items[4]['id']}", headers=self.session,
                          json={"status": "cancelled"})
        new_day = (self.first + timedelta(weeks=2, days=2)).date()
        res = self._move(items[2]["id"], local_date=new_day.isoformat(), local_time="14:00")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["moved"], 2)
        rows = {r[0]: r for r in self._rows()}
        for a in items[:2]:   # earlier ones stay
            self.assertEqual(self._local(a["starts_at"]), datetime.fromtimestamp(rows[a["id"]][1], ISTANBUL))
        for k, a in enumerate(items[2:4]):
            moved = datetime.fromtimestamp(rows[a["id"]][1], ISTANBUL)
            self.assertEqual((moved.date(), moved.hour), (new_day + timedelta(weeks=k), 14))
            self.assertEqual(rows[a["id"]][3], a["series_id"])
            self.assertEqual(appointment_book.get(a["id"])["duration_min"], 50)
        # The cancelled one stays where it was.
        self.assertEqual(rows[items[4]["id"]][2], "cancelled")
        self.assertEqual(self._local(items[4]["starts_at"]),
                         datetime.fromtimestamp(rows[items[4]["id"]][1], ISTANBUL))

    def test_biweekly_spacing_is_kept(self):
        items = self._series(count=3, frequency="biweekly")
        new_day = (self.first + timedelta(days=1)).date()
        res = self._move(items[0]["id"], local_date=new_day.isoformat(), local_time="09:00")
        self.assertEqual(res.status_code, 200, res.text)
        starts = [datetime.fromtimestamp(t, ISTANBUL) for t in self._starts()]
        self.assertEqual([s.date() for s in starts], [new_day + timedelta(weeks=2 * k) for k in range(3)])

    def test_a_conflict_refuses_the_whole_move(self):
        room_id = self._room()
        items = self._series(count=4, room_id=room_id)
        before = self._starts()
        new_day = (self.first + timedelta(days=1)).date()   # Wednesdays
        # Another practitioner holds the room on the third Wednesday.
        other = self._user("practitioner")
        self._single(other, self._card(other), datetime.combine(new_day + timedelta(weeks=2), clock(10, 0),
                                                                tzinfo=ISTANBUL), room_id=room_id)
        # The practitioner has another client on the fourth.
        self._single(self.doc, self._card(self.doc), datetime.combine(new_day + timedelta(weeks=3), clock(10, 20),
                                                                      tzinfo=ISTANBUL))
        preview = self._move(items[0]["id"], preview=True, local_date=new_day.isoformat(), local_time="10:00")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual([s["conflicts"] for s in preview.json()["sessions"]], [[], [], ["room"], ["practitioner"]])

        res = self._move(items[0]["id"], local_date=new_day.isoformat(), local_time="10:00")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn((new_day + timedelta(weeks=2)).isoformat(), res.json()["detail"])
        self.assertEqual([t for t in self._starts() if t in before], before)
        for a in items:
            self.assertEqual(appointment_book.get(a["id"])["starts_at"],
                             datetime.fromisoformat(a["starts_at"]).timestamp())

    def test_a_session_does_not_conflict_with_where_it_was(self):
        items = self._series(count=3)
        res = self._move(items[0]["id"], local_date=self.first.date().isoformat(), local_time="10:30")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual({datetime.fromtimestamp(t, ISTANBUL).strftime("%H:%M") for t in self._starts()}, {"10:30"})

    def test_a_new_room_only(self):
        room_id = self._room()
        items = self._series(count=3)
        res = self._move(items[1]["id"], room_id=room_id)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([appointment_book.get(a["id"])["room_id"] for a in items], [None, room_id, room_id])
        # Times are kept.
        self.assertEqual([appointment_book.get(a["id"])["starts_at"] for a in items],
                         [datetime.fromisoformat(a["starts_at"]).timestamp() for a in items])
        # room_id null takes them out of the room again.
        self.assertEqual(self._move(items[1]["id"], room_id=None).status_code, 200)
        self.assertEqual([appointment_book.get(a["id"])["room_id"] for a in items], [None, None, None])

    def test_room_rules_apply(self):
        off = self._room()
        rooms.update(rooms.get(off), by="admin", active=False)
        items = self._series(count=2)
        self.assertEqual(self._move(items[0]["id"], room_id=off).status_code, 422)
        online = self._series(count=2, session_format="Online",
                              starts_at=(self.first + timedelta(hours=3)).isoformat())
        self.assertEqual(self._move(online[0]["id"], room_id=self._room()).status_code, 422)

    def test_past_times_are_refused(self):
        items = self._series(count=2)
        preview = self._move(items[0]["id"], preview=True, local_date="2020-01-07", local_time="10:00")
        self.assertEqual([s["conflicts"] for s in preview.json()["sessions"]], [["past"], ["past"]])
        self.assertEqual(self._move(items[0]["id"], local_date="2020-01-07", local_time="10:00").status_code, 409)

    def test_needs_a_series_and_a_scheduled_session(self):
        single = self._single(self.doc, self.card, self.first + timedelta(hours=4))
        self.assertEqual(self._move(single["id"], local_time="11:00",
                                    local_date=self.first.date().isoformat()).status_code, 422)
        items = self._series(count=2)
        self.client.patch(f"/api/v1/appointments/{items[0]['id']}", headers=self.session,
                          json={"status": "cancelled"})
        self.assertEqual(self._move(items[0]["id"], local_time="11:00",
                                    local_date=self.first.date().isoformat()).status_code, 409)
        self.assertEqual(self._move(items[1]["id"]).status_code, 422, "nothing to change")

    def test_scoped_to_the_book(self):
        items = self._series(count=2)
        stranger = self._session(self._user("practitioner"))
        for preview in (True, False):
            res = self._move(items[0]["id"], preview=preview, headers=stranger, local_time="11:00",
                             local_date=self.first.date().isoformat())
            self.assertEqual(res.status_code, 404)


class TestPreview(SeriesTestCase):
    def test_conflicts_are_listed_and_nothing_is_written(self):
        room_id = self._room()
        other = self._user("practitioner")
        other_card = self._card(other)
        busy_doc = self._single(self.doc, self.card, self.first + timedelta(weeks=1, minutes=20))
        busy_room = self._single(other, other_card, self.first + timedelta(weeks=2), room_id=room_id)
        before = self._rows()
        res = self._preview(room_id=room_id)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["timezone"], "Europe/Istanbul")
        occurrences = body["occurrences"]
        self.assertEqual([o["conflicts"] for o in occurrences], [[], ["practitioner"], ["room"], []])
        self.assertEqual([o["date"] for o in occurrences],
                         [(self.first.date() + timedelta(weeks=k)).isoformat() for k in range(4)])
        self.assertEqual({o["time"] for o in occurrences}, {"10:00"})
        self.assertEqual(self._rows(), before)
        # The other book's appointment is not described, only "room".
        self.assertNotIn(other_card, res.text)
        self.assertNotIn(busy_room["id"], res.text)
        self.assertNotIn(busy_doc["id"], res.text)

    def test_past_sessions_are_marked(self):
        first = datetime.now(ISTANBUL).replace(second=0, microsecond=0) - timedelta(weeks=2) + timedelta(hours=1)
        res = self._preview(starts_at=first.isoformat(), count=4)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([o["conflicts"] for o in res.json()["occurrences"]], [["past"], ["past"], [], []])
        self.assertEqual(self._rows(), [])

    def test_a_switched_off_room_refuses_the_whole_preview(self):
        room_id = self._room()
        rooms.update(rooms.get(room_id), by="admin", active=False)
        self.assertEqual(self._preview(room_id=room_id).status_code, 422)


class TestSkipAndAtomicity(SeriesTestCase):
    def test_create_skips_the_chosen_dates(self):
        busy = self._single(self.doc, self.card, self.first + timedelta(weeks=1))
        skip = [(self.first.date() + timedelta(weeks=1)).isoformat()]
        res = self._create(skip=skip)
        self.assertEqual(res.status_code, 200, res.text)
        dates = [self._local(a["starts_at"]).date() for a in res.json()["appointments"]]
        self.assertEqual(dates, [self.first.date() + timedelta(weeks=w) for w in (0, 2, 3)])
        self.assertEqual(len(self._rows()), 4)   # three of the series, and the one in the way
        self.assertIsNone(appointment_book.get(busy["id"])["series_id"])

    def test_skipping_past_sessions(self):
        first = datetime.now(ISTANBUL).replace(second=0, microsecond=0) - timedelta(weeks=1) + timedelta(hours=1)
        skip = [first.date().isoformat()]
        res = self._create(starts_at=first.isoformat(), count=3, skip=skip)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()["appointments"]), 2)

    def test_an_unskipped_conflict_refuses_everything(self):
        self._single(self.doc, self.card, self.first + timedelta(weeks=2))
        before = self._rows()
        res = self._create()
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn((self.first.date() + timedelta(weeks=2)).isoformat(), res.json()["detail"])
        self.assertEqual(self._rows(), before)

    def test_a_conflict_after_the_preview_refuses_the_whole_series(self):
        room_id = self._room()
        preview = self._preview(room_id=room_id)
        self.assertEqual([o["conflicts"] for o in preview.json()["occurrences"]], [[], [], [], []])
        # Meanwhile, another practitioner takes the room in week 3.
        other = self._user("practitioner")
        taken = self._single(other, self._card(other), self.first + timedelta(weeks=3, minutes=-30),
                             room_id=room_id)
        res = self._create(room_id=room_id)
        self.assertEqual(res.status_code, 409, res.text)
        detail = res.json()["detail"]
        self.assertIn((self.first.date() + timedelta(weeks=3)).isoformat(), detail)
        self.assertIn("10:00", detail)
        self.assertNotIn(taken["id"], detail)
        self.assertEqual(self._rows(), [])

    def test_the_check_and_the_writes_share_one_transaction(self):
        """A booking that lands between the series' checks and its writes would
        double-book: the checks run inside the write transaction, so a second
        series for the same slots fails as a whole."""
        self.assertEqual(self._create().status_code, 200)
        res = self._create()
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(len(self._rows()), 4)

    def test_skipping_everything_books_nothing(self):
        skip = [(self.first.date() + timedelta(weeks=k)).isoformat() for k in range(4)]
        self.assertEqual(self._create(skip=skip).status_code, 422)
        self.assertEqual(self._rows(), [])


class TestEditingASeries(SeriesTestCase):
    def _series(self, count=5):
        res = self._create(count=count)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["appointments"]

    def _status(self):
        return [r[2] for r in self._rows()]

    def test_cancel_one_session(self):
        items = self._series()
        res = self.client.patch(f"/api/v1/appointments/{items[1]['id']}", headers=self.session,
                                json={"status": "cancelled"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._status(), ["scheduled", "cancelled", "scheduled", "scheduled", "scheduled"])

    def test_move_one_session(self):
        items = self._series()
        moved = self.first + timedelta(weeks=1, days=1)
        res = self.client.patch(f"/api/v1/appointments/{items[1]['id']}", headers=self.session,
                                json={"starts_at": moved.isoformat()})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["appointment"]["series_id"], items[1]["series_id"])

    def test_cancel_this_and_following(self):
        items = self._series()
        # One later session is already cancelled; it stays as it is.
        self.client.patch(f"/api/v1/appointments/{items[3]['id']}", headers=self.session,
                          json={"status": "cancelled"})
        res = self.client.post(f"/api/v1/appointments/{items[2]['id']}/cancel-following", headers=self.session)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["cancelled"], 2)
        self.assertEqual(self._status(), ["scheduled", "scheduled", "cancelled", "cancelled", "cancelled"])
        self.assertEqual({a["status"] for a in res.json()["appointments"]}, {"cancelled"})

    def test_cancel_following_leaves_other_series_and_singles_alone(self):
        items = self._series(count=3)
        single = self._single(self.doc, self.card, self.first + timedelta(weeks=1, hours=3))
        other = self._create(starts_at=(self.first + timedelta(hours=5)).isoformat(), count=3).json()
        self.client.post(f"/api/v1/appointments/{items[0]['id']}/cancel-following", headers=self.session)
        self.assertEqual(appointment_book.get(single["id"])["status"], "scheduled")
        for a in other["appointments"]:
            self.assertEqual(appointment_book.get(a["id"])["status"], "scheduled")

    def test_cancel_following_needs_a_series(self):
        single = self._single(self.doc, self.card, self.first)
        res = self.client.post(f"/api/v1/appointments/{single['id']}/cancel-following", headers=self.session)
        self.assertEqual(res.status_code, 422, res.text)

    def test_cancel_following_is_scoped_to_the_book(self):
        items = self._series(count=2)
        stranger = self._session(self._user("practitioner"))
        res = self.client.post(f"/api/v1/appointments/{items[0]['id']}/cancel-following", headers=stranger)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self._status(), ["scheduled", "scheduled"])

    def test_came_and_did_not_come_stay_per_session(self):
        first = datetime.now(ISTANBUL).replace(second=0, microsecond=0) - timedelta(weeks=1, hours=1)
        appointment_book.add_existing(practitioner=self.doc, patient_id=self.card,
                                      starts_at=first.timestamp(), duration_min=50,
                                      session_format="In-person", status="scheduled",
                                      created_by="test", series_id="SER-TEST")
        later = appointment_book.add_existing(practitioner=self.doc, patient_id=self.card,
                                              starts_at=(first + timedelta(weeks=2)).timestamp(), duration_min=50,
                                              session_format="In-person", status="scheduled",
                                              created_by="test", series_id="SER-TEST")
        past_id = self._rows()[0][0]
        res = self.client.patch(f"/api/v1/appointments/{past_id}", headers=self.session,
                                json={"status": "no_show"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(appointment_book.get(later)["status"], "scheduled")


class TestWeekView(SeriesTestCase):
    def _week(self, headers, start, **params):
        return self.client.get("/api/v1/appointments/week", headers=headers, params={"start": start, **params})

    def test_the_week_runs_monday_to_sunday_in_istanbul(self):
        sunday_late = datetime.combine(self.monday + timedelta(days=6), clock(23, 30), tzinfo=ISTANBUL)
        monday_early = datetime.combine(self.monday, clock(0, 0), tzinfo=ISTANBUL)
        next_monday = datetime.combine(self.monday + timedelta(days=7), clock(0, 10), tzinfo=ISTANBUL)
        ids = {}
        for name, at in (("first", monday_early), ("last", sunday_late), ("after", next_monday)):
            ids[appointment_book.add_existing(practitioner=self.doc, patient_id=self.card,
                                              starts_at=at.timestamp(), duration_min=30,
                                              session_format="Online", status="scheduled",
                                              created_by="test")] = name
        # Any day of the week gives that week.
        for start in (self.monday, self.monday + timedelta(days=3), self.monday + timedelta(days=6)):
            with self.subTest(start=start):
                res = self._week(self.session, start.isoformat())
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                self.assertEqual(body["start"], self.monday.isoformat())
                self.assertEqual(body["end"], (self.monday + timedelta(days=7)).isoformat())
                self.assertEqual(body["timezone"], "Europe/Istanbul")
                (book,) = body["practitioners"]
                self.assertEqual(book["username"], self.doc)
                self.assertEqual([ids[a["id"]] for a in book["appointments"]], ["first", "last"])

    def test_the_week_shows_series_and_the_day_view_fields(self):
        series = self._create(count=2).json()
        res = self._week(self.session, self.monday.isoformat())
        (item,) = res.json()["practitioners"][0]["appointments"]
        self.assertEqual(item["series_id"], series["series_id"])
        self.assertEqual(set(item), {"id", "patient_id", "client_name", "starts_at", "ends_at", "duration_min",
                                     "session_format", "room_id", "room_name", "status", "series_id"})

    def test_the_desk_sees_linked_books_only(self):
        second, third = self._user("practitioner"), self._user("practitioner")
        desk = self._user("secretary")
        appointment_book.link_staff(desk, self.doc)
        appointment_book.link_staff(desk, second)
        third_card = self._card(third)
        when = datetime.combine(self.monday + timedelta(days=2), clock(11, 0), tzinfo=ISTANBUL)
        for p, card in ((self.doc, self.card), (second, self._card(second)), (third, third_card)):
            appointment_book.add_existing(practitioner=p, patient_id=card, starts_at=when.timestamp(),
                                          duration_min=50, session_format="Online", status="scheduled",
                                          created_by="test")
        desk_session = self._session(desk)
        res = self._week(desk_session, self.monday.isoformat())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual({p["username"] for p in res.json()["practitioners"]}, {self.doc, second})
        self.assertNotIn(third_card, res.text)
        # One book only, when named; a book the desk does not run is refused.
        res = self._week(desk_session, self.monday.isoformat(), practitioner=second)
        self.assertEqual([p["username"] for p in res.json()["practitioners"]], [second])
        self.assertEqual(self._week(desk_session, self.monday.isoformat(), practitioner=third).status_code, 403)

    def test_a_practitioner_sees_only_their_own_week(self):
        other = self._user("practitioner")
        other_card = self._card(other)
        when = datetime.combine(self.monday, clock(9, 0), tzinfo=ISTANBUL)
        appointment_book.add_existing(practitioner=other, patient_id=other_card, starts_at=when.timestamp(),
                                      duration_min=50, session_format="Online", status="scheduled",
                                      created_by="test")
        res = self._week(self.session, self.monday.isoformat())
        self.assertEqual([p["username"] for p in res.json()["practitioners"]], [self.doc])
        self.assertNotIn(other_card, res.text)
        self.assertEqual(self._week(self.session, self.monday.isoformat(), practitioner=other).status_code, 403)

    def test_others_are_refused(self):
        unlinked = self._user("secretary")
        for headers in (self._session(self._user("auditor")), self._session(unlinked),
                        self._session("admin", "Admin@2026Secure!")):
            self.assertEqual(self._week(headers, self.monday.isoformat()).status_code, 403)

    def test_a_bad_start_is_refused(self):
        for bad in ("", "2031-13-01", "next week", "2031-03-10T00:00"):
            with self.subTest(start=bad):
                self.assertEqual(self._week(self.session, bad).status_code, 422)


class TestRoomWeek(SeriesTestCase):
    def _room_week(self, room_id, headers, start=None):
        return self.client.get(f"/api/v1/rooms/{room_id}/week", headers=headers,
                               params={"start": (start or self.monday).isoformat()})

    def test_own_books_in_full_other_books_busy_only(self):
        room_id = self._room()
        other = self._user("practitioner")
        other_card = self._card(other, name="Hidden Client Name")
        mine = self._single(self.doc, self.card, self.first, room_id=room_id)
        theirs = self._single(other, other_card, self.first + timedelta(days=2), room_id=room_id)
        res = self._room_week(room_id, self.session)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["start"], self.monday.isoformat())
        self.assertEqual(body["timezone"], "Europe/Istanbul")
        slots = body["slots"]
        self.assertEqual(len(slots), 2)
        own = [s for s in slots if s["mine"]]
        busy = [s for s in slots if not s["mine"]]
        self.assertEqual(own[0]["id"], mine["id"])
        self.assertEqual(set(busy[0]), BUSY_FIELDS)
        for secret in (theirs["id"], other_card, other, "Hidden Client Name"):
            self.assertNotIn(secret, res.text)

    def test_operators_see_busy_times_only(self):
        room_id = self._room()
        self._create(room_id=room_id, count=1)
        for who, password in (("admin", "Admin@2026Secure!"), ("sec.officer", "SecOfficer@2026!")):
            with self.subTest(who=who):
                res = self._room_week(room_id, self._session(who, password))
                self.assertEqual(res.status_code, 200, res.text)
                (slot,) = res.json()["slots"]
                self.assertEqual(set(slot), BUSY_FIELDS)
                for secret in (self.card, self.doc, "series_id", "client_name"):
                    self.assertNotIn(secret, res.text)

    def test_a_linked_desk_sees_its_books_in_full(self):
        room_id = self._room()
        desk = self._user("secretary")
        appointment_book.link_staff(desk, self.doc)
        self._single(self.doc, self.card, self.first, room_id=room_id)
        res = self._room_week(room_id, self._session(desk))
        (slot,) = res.json()["slots"]
        self.assertTrue(slot["mine"])
        self.assertEqual(slot["patient_id"], self.card)

    def test_auditor_and_unlinked_desk_are_refused(self):
        room_id = self._room()
        for role in ("auditor", "secretary"):
            with self.subTest(role=role):
                res = self._room_week(room_id, self._session(self._user(role)))
                self.assertEqual(res.status_code, 403, res.text)

    def test_unknown_room_and_bad_date(self):
        self.assertEqual(self._room_week("ROOM-NOPE", self.session).status_code, 404)
        room_id = self._room()
        res = self.client.get(f"/api/v1/rooms/{room_id}/week", headers=self.session, params={"start": "x"})
        self.assertEqual(res.status_code, 422)


@unittest.skipIf(default_sql_db.is_postgres, "SQLite file migration")
class TestSeriesMigration(unittest.TestCase):
    def test_old_appointments_get_a_null_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE appointments (
                id VARCHAR(40) PRIMARY KEY, practitioner_username VARCHAR(100) NOT NULL,
                patient_id VARCHAR(100) NOT NULL, starts_at REAL NOT NULL, duration_min INTEGER NOT NULL,
                session_format VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL,
                created_by VARCHAR(100) NOT NULL, created_at REAL NOT NULL,
                updated_by VARCHAR(100), updated_at REAL, room_id VARCHAR(40))""")
            conn.execute("INSERT INTO appointments VALUES ('APT-OLD', 'psk.elif', 'CL-001', 1, 50, "
                         "'In-person', 'completed', 'x', 1, NULL, NULL, NULL)")
            conn.commit()
            conn.close()

            with mock.patch.object(sql_db, "DEFAULT_SQLITE_PATH", path):
                default_sql_db.init_db()
                default_sql_db.init_db()   # safe to run on every start

            conn = sqlite3.connect(path)
            try:
                row = conn.execute("SELECT series_id, status FROM appointments WHERE id = 'APT-OLD'").fetchone()
            finally:
                conn.close()
            self.assertEqual(row, (None, "completed"))


if __name__ == "__main__":
    unittest.main()
