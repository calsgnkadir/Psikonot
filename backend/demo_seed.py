"""
backend/demo_seed.py — Demonstration file for the bundled demo client
====================================================================
A freshly cloned vault starts with an empty chain, so every screen renders as an
empty state — the running application looks broken rather than idle.

This module writes a small, clinically coherent therapy file for the demo client
(a CBT course for anxiety) and a few weeks of the appointment book, so a first
run shows the system doing its job. It only ever runs alongside the demo
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
_INSTITUTION = "Mahrem Psychology Practice"


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
        _record("consent_form", "KVKK explicit consent & therapy agreement", {
            "form_type": "KVKK explicit consent + therapy agreement",
            "signed_date": _day(35),
        }, days_ago=35),

        _record("client_profile", "Client profile", {
            "presenting_problem": "Panic attacks on the morning commute; has started avoiding the bus.",
            "characteristics": "34, software tester. Conscientious, self-critical; sleeps poorly before workdays.",
            "background": "First episode two years ago after a job change. No previous therapy.",
        }, days_ago=35),

        _record("treatment_plan", "CBT plan for panic and generalised anxiety", {
            "goals": "Fewer panic episodes; commute to work without avoidance",
            "approach": "Cognitive Behavioural Therapy (CBT)",
            "planned_sessions": "12",
        }, days_ago=34),

        _record("session_note", "Session 1 — psychoeducation", {
            "session_number": "1", "duration_min": "50", "session_format": "In-person",
            "summary": "Explained the anxiety cycle; practised paced breathing.",
        }, days_ago=28),

        _record("homework", "Daily thought record", {
            "task": "Write down 3 anxious thoughts a day and the evidence for and against each.",
            "due_date": _day(21),
        }, days_ago=28),

        _record("session_note", "Session 2 — cognitive restructuring", {
            "session_number": "2", "duration_min": "50", "session_format": "In-person",
            "summary": "Reviewed the thought record; challenged catastrophic predictions.",
        }, days_ago=21),

        _record("session_note", "Session 3 — graded exposure", {
            "session_number": "3", "duration_min": "50", "session_format": "Online",
            "summary": "Built an exposure ladder for the commute; first step agreed.",
        }, days_ago=14),

        _record("session_note", "Session 4 — review", {
            "session_number": "4", "duration_min": "50", "session_format": "In-person",
            "summary": "Took the bus two stops alone; fewer panic episodes this week.",
        }, days_ago=1, notes="Responding well to therapy."),
    ]


def _process_note() -> dict:
    # A process note: the therapist's own reflections on the session.
    return _record("session_note", "Process note — session 3", {
        "session_number": "3", "duration_min": "50", "session_format": "Online",
        "summary": "Own reflections on transference; worth raising in supervision.",
    }, days_ago=14)


def _session_transcript() -> dict:
    # What was said, word for word.
    return _record("session_transcript", "Transcript — session 3", {
        "session_number": "3",
        "transcript": ("T: What goes through your mind at the bus stop?\n"
                       "C: That I will faint and everyone will stare.\n"
                       "T: Has that happened before?\n"
                       "C: No. My heart races, but I have never fainted."),
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
    has something to compare. Times are in Türkiye (UTC+3)."""
    from core.services import appointment_book as book

    tr = timezone(timedelta(hours=3))
    today = datetime.now(tr).replace(minute=0, second=0, microsecond=0)
    plan = [
        # client, days from today, hour, format, status
        ("CL-001", -28, 10, "In-person", "completed"),
        ("CL-001", -21, 10, "In-person", "completed"),
        ("CL-001", -14, 10, "Online", "completed"),
        ("CL-001", -7, 10, "In-person", "no_show"),
        ("CL-001", 2, 10, "In-person", "scheduled"),
        ("CL-001", 9, 10, "Online", "scheduled"),
        ("CL-002", -20, 14, "In-person", "completed"),
        ("CL-002", -13, 14, "In-person", "cancelled"),
        ("CL-002", -6, 14, "In-person", "completed"),
        ("CL-002", 1, 14, "In-person", "scheduled"),
        ("CL-003", -10, 16, "Online", "no_show"),
        ("CL-003", -3, 16, "Online", "completed"),
        ("CL-003", 4, 16, "Online", "scheduled"),
    ]
    for patient_id, days, hour, session_format, status in plan:
        book.add_existing(
            practitioner=DEMO_DOCTOR, patient_id=patient_id,
            starts_at=(today.replace(hour=hour) + timedelta(days=days)).timestamp(), duration_min=50,
            session_format=session_format, status=status, created_by="secretary.ayse",
        )


def seed_demo_chart_if_enabled() -> bool:
    """Seeds only in the same conditions that create the demo accounts."""
    env = os.environ.get("ENVIRONMENT", "production")
    demo_mode = os.getenv("VHV_DEMO_MODE", "false").lower() == "true"
    if env != "development" and not demo_mode:
        return False
    return seed_demo_chart()
