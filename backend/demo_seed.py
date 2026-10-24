"""
backend/demo_seed.py — Demonstration file for the bundled demo client
====================================================================
A freshly cloned vault starts with an empty chain, so every screen renders as an
empty state — the running application looks broken rather than idle.

This module writes a small, clinically coherent therapy file for the demo client
(a CBT course for anxiety) and a few weeks of the appointment book, so a first
run shows the system doing its job. The content is in Turkish, as a center in
Türkiye would write it; the people and the center are made up. It only ever runs alongside the demo
accounts (development or VHV_DEMO_MODE), and only when the client has no records
yet, so it can never touch a real deployment or overwrite a real chain.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import List

DEMO_PATIENT_ID = "CL-001"
DEMO_DOCTOR = "psk.elif"

# Documented in the README and shown on the login screen's demo panel.
DEMO_RECORD_PASSWORD = "DemoRecord@2026!"

_DOCTOR_NAME = "Uzm. Psk. Elif Yılmaz"
_INSTITUTION = "Örnek Psikoloji Merkezi"


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _record(record_type: str, title: str, data: dict, days_ago: int, notes: str = "") -> dict:
    from backend.schemas.requests import RECORD_TYPES
    return {
        "record_type":       record_type,
        "record_type_label": RECORD_TYPES[record_type],
        "title":             title,
        "doctor_name":       _DOCTOR_NAME,
        "institution":       _INSTITUTION,
        "record_date":       _day(days_ago),
        "access_level":      "practitioner_only",
        "is_confidential":   False,
        "data":              data,
        "notes":             notes,
        "created_by":        DEMO_DOCTOR,
        "created_at":        datetime.now(timezone.utc).isoformat(),
        "patient_id":        DEMO_PATIENT_ID,
        "file_name":         None,
        "file_type":         None,
        "file_data":         None,
    }


def _demo_chart() -> List[dict]:
    """Five weeks of a CBT course for panic on the commute: the client's
    profile, the plan, the session notes and the homework."""
    return [
        _record("consent_form", "KVKK açık rıza ve terapi sözleşmesi", {
            "form_type": "KVKK açık rıza beyanı ve terapi sözleşmesi",
            "signed_date": _day(35),
        }, days_ago=35),

        _record("client_profile", "Danışan profili", {
            "presenting_problem": ("Sabah işe giderken otobüste panik atakları yaşıyor; "
                                   "son aylarda otobüse binmekten kaçınmaya başlamış."),
            "characteristics": ("34 yaşında, yazılım test uzmanı. Titiz ve kendini çok eleştiren biri; "
                                "iş günlerinden önceki gecelerde uykusu bozuluyor."),
            "background": "İlk atak iki yıl önce iş değişikliğinin ardından. Daha önce terapi almamış.",
        }, days_ago=35),

        _record("treatment_plan", "Panik ve yaygın kaygı için BDT planı", {
            "goals": "Panik ataklarının azalması; işe kaçınmadan, toplu taşımayla gidip gelebilmek",
            "approach": "Bilişsel Davranışçı Terapi (BDT)",
            "planned_sessions": "12",
        }, days_ago=34),

        _record("session_note", "1. seans — psikoeğitim", {
            "session_number": "1", "duration_min": "50", "session_format": "In-person",
            "summary": "Kaygı döngüsü anlatıldı; yavaş ve düzenli nefes egzersizi birlikte çalışıldı.",
        }, days_ago=28),

        _record("homework", "Günlük düşünce kaydı", {
            "task": "Her gün kaygı uyandıran 3 düşünceyi, lehine ve aleyhine kanıtlarıyla birlikte yazmak.",
            "due_date": _day(21),
        }, days_ago=28),

        _record("session_note", "2. seans — bilişsel yeniden yapılandırma", {
            "session_number": "2", "duration_min": "50", "session_format": "In-person",
            "summary": "Düşünce kaydı birlikte gözden geçirildi; felaketleştiren tahminler sorgulandı.",
        }, days_ago=21),

        _record("session_note", "3. seans — aşamalı maruz bırakma", {
            "session_number": "3", "duration_min": "50", "session_format": "Online",
            "summary": "İşe gidiş yolu için bir maruz bırakma basamakları listesi hazırlandı; ilk adımda anlaşıldı.",
        }, days_ago=14),

        _record("session_note", "4. seans — ara değerlendirme", {
            "session_number": "4", "duration_min": "50", "session_format": "In-person",
            "summary": "Otobüsle iki durak tek başına gitti; bu hafta panik atakları azaldı.",
        }, days_ago=1, notes="Terapiye iyi yanıt veriyor."),
    ]


def _process_note() -> dict:
    # A process note: the therapist's own reflections on the session.
    return _record("session_note", "Süreç notu — 3. seans", {
        "session_number": "3", "duration_min": "50", "session_format": "Online",
        "summary": "Aktarım üzerine kendi düşüncelerim; süpervizyonda konuşmaya değer.",
    }, days_ago=14)


def _session_transcript() -> dict:
    # What was said, word for word.
    # T: terapist, D: danışan.
    return _record("session_transcript", "Seans dökümü — 3. seans", {
        "session_number": "3",
        "transcript": ("T: Otobüs durağında beklerken aklınızdan neler geçiyor?\n"
                       "D: Bayılacağımı ve herkesin bana bakacağını düşünüyorum.\n"
                       "T: Daha önce hiç bayıldınız mı?\n"
                       "D: Hayır. Kalbim çok hızlı atıyor ama hiç bayılmadım."),
    }, days_ago=14)


def seed_demo_chart() -> bool:
    """Writes the demo chart once. Safe to call on every startup."""
    from infrastructure.repositories.lmdb_repositories import LMDBBlockRepository
    from infrastructure.cryptography.crypto_strategies import AESGCMStrategy
    from core.services.record_service import RecordService
    from core.cqrs.commands import AddRecordCommand, CommandHandler

    block_repo = LMDBBlockRepository()
    record_service = RecordService(block_repo, AESGCMStrategy())
    project_name = record_service._get_project_name(DEMO_PATIENT_ID)

    existing = [
        b for b in block_repo.load_all_blocks(project_name)
        if b.index > 0 and not (isinstance(b.data, dict) and b.data.get("type") == "audit")
    ]
    if existing:
        return False  # a chart is already present — never write over it

    handler = CommandHandler(record_service, None, block_repo)

    for record in _demo_chart() + [_process_note()]:
        handler.handle_add_record(AddRecordCommand(
            patient_id=DEMO_PATIENT_ID, data=record,
            is_protected=False, protection_password=None, username=DEMO_DOCTOR,
        ))
    # The transcript is locked with an extra password on top of the at-rest
    # encryption: even the practitioner's own session cannot open it alone.
    handler.handle_add_record(AddRecordCommand(
        patient_id=DEMO_PATIENT_ID, data=dict(_session_transcript(), is_confidential=True),
        is_protected=True, protection_password=DEMO_RECORD_PASSWORD, username=DEMO_DOCTOR,
    ))
    _seed_appointments()
    return True


def _seed_appointments() -> None:
    """A few weeks of the book, booked by the practice secretary: the demo
    client's weekly sessions (one missed), and two more clients so the tally
    has something to compare. CL-002 and CL-003 come every week at the same
    hour and were booked as a weekly series, so the calendar shows series
    markers. In-person sessions are in the centre's first room. Times are in
    Türkiye (UTC+3)."""
    import uuid

    from core.services import appointment_book as book
    from core.services import rooms

    existing = {r["name"]: r["id"] for r in rooms.list_rooms(active_only=False)}
    for name in ("Oda 1", "Oda 2"):
        if name not in existing:
            existing[name] = rooms.create(name, by="admin")["id"]

    series = {key: f"SER-{uuid.uuid4().hex[:12].upper()}" for key in ("CL-002", "CL-003")}
    tr = timezone(timedelta(hours=3))
    today = datetime.now(tr).replace(minute=0, second=0, microsecond=0)
    plan = [
        # client, days from today, hour, format, status, series
        ("CL-001", -28, 10, "In-person", "completed", None),
        ("CL-001", -21, 10, "In-person", "completed", None),
        ("CL-001", -14, 10, "Online", "completed", None),
        ("CL-001", -7, 10, "In-person", "no_show", None),
        ("CL-001", 2, 10, "In-person", "scheduled", None),
        ("CL-001", 9, 10, "Online", "scheduled", None),
        ("CL-002", -20, 14, "In-person", "completed", "CL-002"),
        ("CL-002", -13, 14, "In-person", "cancelled", "CL-002"),
        ("CL-002", -6, 14, "In-person", "completed", "CL-002"),
        ("CL-002", 1, 14, "In-person", "scheduled", "CL-002"),
        ("CL-002", 8, 14, "In-person", "scheduled", "CL-002"),
        ("CL-002", 15, 14, "In-person", "scheduled", "CL-002"),
        ("CL-003", -10, 16, "Online", "no_show", "CL-003"),
        ("CL-003", -3, 16, "Online", "completed", "CL-003"),
        ("CL-003", 4, 16, "Online", "scheduled", "CL-003"),
        ("CL-003", 11, 16, "Online", "scheduled", "CL-003"),
    ]
    for patient_id, days, hour, session_format, status, series_key in plan:
        book.add_existing(
            practitioner=DEMO_DOCTOR, patient_id=patient_id,
            starts_at=(today.replace(hour=hour) + timedelta(days=days)).timestamp(), duration_min=50,
            session_format=session_format, status=status, created_by="secretary.ayse",
            room_id=existing["Oda 1"] if session_format == "In-person" else None,
            series_id=series.get(series_key),
        )


# The center's intake inbox: made-up people asking for a first appointment.
# name, phone, e-mail, channel, days ago, status, assigned, preferred, format,
# days, times, priority, contact attempts, decline reason
_DEMO_APPLICATIONS = (
    ("Burak Çelik", "+90 532 000 10 01", None, "phone", 12, "waitlisted", DEMO_DOCTOR, DEMO_DOCTOR,
     "In-person", ["mon", "wed"], ["evening"], "high", 2, None),
    ("Gizem Yıldız", "+90 532 000 10 02", "gizem@example.com", "referral", 20, "contacted", DEMO_DOCTOR, None,
     "either", ["tue", "thu"], ["afternoon"], "normal", 1, None),
    ("Selin Aksoy", None, "selin@example.com", "email", 5, "new", None, DEMO_DOCTOR,
     "Online", [], ["morning", "evening"], "normal", 0, None),
    ("Emre Doğan", "+90 532 000 10 04", None, "walk_in", 2, "new", None, None,
     "either", ["sat"], [], "normal", 0, None),
    ("Kerem Şahin", "+90 532 000 10 05", None, "phone", 40, "declined", None, None,
     "In-person", ["fri"], ["morning"], "normal", 3, "no_answer"),
)


def seed_demo_applications() -> bool:
    """A few applications for the intake page, once: only while there are none."""
    from core.services import intake
    from database.sql_db import run_sql

    if run_sql("SELECT COUNT(*) FROM applications", fetch="one")[0][0]:
        return False
    now = datetime.now(timezone.utc).timestamp()
    for (name, phone, email, channel, days, status, assigned, preferred, session_format, pref_days,
         pref_times, priority, attempts, reason) in _DEMO_APPLICATIONS:
        intake.add_existing({
            "channel": channel, "full_name": name, "phone": phone, "email": email,
            "preferred_practitioner": preferred, "assigned_practitioner": assigned,
            "session_format": session_format, "preferred_days": pref_days, "preferred_times": pref_times,
            "priority": priority,
        }, by="secretary.ayse", received_at=now - days * 86400 - 3600, status=status,
            contact_attempts=attempts, close_reason=reason)
    return True


# ── Staff tasks ("İşler") ─────────────────────────────────────
# The demo book's to-do list: type, link, days from today, time, priority,
# assignee, status. A link is ("client", ID) or ("next_appointment", client
# ID): that client's next scheduled appointment, when there is one. The demo
# application left "new" (Selin Aksoy) gets its follow-up task from the
# automatic check, not from here.
_DEMO_TASKS = (
    ("call_back", ("client", "CL-002"), 0, "11:00", "high", "secretary.ayse", "open"),
    ("confirm_appointment", ("next_appointment", "CL-001"), 0, None, "normal", "secretary.ayse", "open"),
    ("request_document", ("client", "CL-003"), -2, None, "normal", None, "open"),
    ("prepare_room", ("next_appointment", "CL-002"), 1, "13:30", "normal", "secretary.ayse", "open"),
    ("call_back", ("client", "CL-001"), 3, None, "normal", DEMO_DOCTOR, "open"),
    ("kvkk_forms", ("client", "CL-003"), -6, None, "normal", "secretary.ayse", "done"),
)


def seed_demo_tasks() -> bool:
    """A few tasks for the İşler page, once: only while there are none."""
    import time

    from core.services import appointment_book as book
    from core.services import client_registry, tasks
    from database.sql_db import run_sql

    if run_sql("SELECT COUNT(*) FROM tasks", fetch="one")[0][0]:
        return False
    today = tasks.today()
    for kind, (link_kind, client_id), days, due_time, priority, assignee, status in _DEMO_TASKS:
        link_type, link_id = None, None
        if link_kind == "client" and client_registry.owner_of(client_id) == DEMO_DOCTOR:
            link_type, link_id = "client", client_id
        elif link_kind == "next_appointment":
            now = time.time()
            upcoming = [a for a in book.list_appointments(practitioner=DEMO_DOCTOR, patient_id=client_id,
                                                          start=now, end=now + 30 * 86400)
                        if a["status"] == "scheduled"]
            if upcoming:
                link_type, link_id = "appointment", upcoming[0]["id"]
        if not tasks.can_be_assigned(DEMO_DOCTOR, assignee):
            assignee = None
        task = tasks.create(DEMO_DOCTOR, {
            "type": kind, "link_type": link_type, "link_id": link_id, "assignee": assignee,
            "due_on": (today + timedelta(days=days)).isoformat(), "due_time": due_time, "priority": priority,
        }, by="secretary.ayse")
        if status != "open":
            tasks.set_status(task, status, by="secretary.ayse")
    return True


def seed_demo_chart_if_enabled() -> bool:
    """Seeds only in the same conditions that create the demo accounts."""
    env = os.environ.get("ENVIRONMENT", "production")
    demo_mode = os.getenv("VHV_DEMO_MODE", "false").lower() == "true"
    if env != "development" and not demo_mode:
        return False
    seeded = seed_demo_chart()
    seed_demo_applications()
    seed_demo_tasks()
    return seeded
