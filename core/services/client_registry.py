"""
core/services/client_registry.py — the practice's client cards
==============================================================
Clients do not use PsikoNot. Each client is a card that belongs to one
practitioner: a client ID (CL-###), a name, how to reach them, and the date the
KVKK forms (privacy notice and explicit consent) were signed on paper.

The card is what the practitioner and their secretary need to run the
appointment book and call a client. Nothing clinical is on it: what the client
talks about — their profile, session notes, transcripts — goes into the
encrypted record chain, which only the practitioner who owns the card can open
(core/services/access_policy.py).

Single node (ADR-0002): picking the next free ID and inserting it run one after
the other on one connection; the primary key refuses a duplicate anyway.
"""

import re
import time
from typing import Optional

import database.storage as storage
from core.pseudonymization.service import project_name_for
from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder

_COLUMNS = ("patient_id", "practitioner_username", "full_name", "phone", "email",
            "kvkk_signed_on", "created_by", "created_at", "updated_by", "updated_at")
_SELECT = ("SELECT patient_id, practitioner_username, full_name, phone, email, kvkk_signed_on, "
           "created_by, created_at, updated_by, updated_at FROM clients")

# The fields a card may change after it is created. The owner never changes.
EDITABLE = ("full_name", "phone", "email", "kvkk_signed_on")


def _run(sql: str, params: tuple = (), fetch: str = "") -> list:
    db = get_sql_db()
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(_to_placeholder(sql), params)
        rows = cur.fetchall() if fetch == "all" else ([cur.fetchone()] if fetch == "one" else [])
        conn.commit()
        return rows
    finally:
        cur.close()
        conn.close()


def _row(row) -> Optional[dict]:
    return {col: row[i] for i, col in enumerate(_COLUMNS)} if row else None


def _next_client_id() -> str:
    """The next free CL-### number. A number is free only if no card and no old
    client account has it AND no record chain exists for it: a chain left behind
    by an old or erased client must never be handed to a new person."""
    taken = {r[0] for r in _run("SELECT patient_id FROM clients", fetch="all")}
    taken |= {r[0] for r in _run("SELECT patient_id FROM users WHERE patient_id IS NOT NULL", fetch="all")}
    numbers = [int(pid[3:]) for pid in taken if pid and re.fullmatch(r"CL-[0-9]{3,}", pid)]
    n = max(numbers, default=0) + 1
    while True:
        patient_id = f"CL-{n:03d}"
        if patient_id not in taken and not storage.project_exists(project_name_for(patient_id)):
            return patient_id
        n += 1


def add(*, practitioner: str, full_name: str, phone: Optional[str], email: Optional[str],
        kvkk_signed_on: Optional[str], created_by: str, patient_id: Optional[str] = None) -> dict:
    """Create a card for this practitioner. `patient_id` is for the demo seed
    only; everyone else gets the next free ID."""
    patient_id = patient_id or _next_client_id()
    _run("INSERT INTO clients (patient_id, practitioner_username, full_name, phone, email, kvkk_signed_on, "
         "created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
         (patient_id, practitioner, full_name, phone, email, kvkk_signed_on, created_by, time.time(), None, None))
    return get(patient_id)


def get(patient_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE patient_id = ?", (patient_id,), fetch="one")
    return _row(rows[0]) if rows else None


def owner_of(patient_id: str) -> Optional[str]:
    """The practitioner who keeps this client's file, or None."""
    card = get(patient_id)
    return card["practitioner_username"] if card else None


def list_for(practitioner: str) -> list:
    rows = _run(_SELECT + " WHERE practitioner_username = ? ORDER BY full_name", (practitioner,), fetch="all")
    return [_row(r) for r in rows]


def remove(patient_id: str) -> bool:
    """Erasure only (KVKK Art. 17): the card goes. The client ID is never handed
    out again, because the client's (now undecryptable) chain still exists."""
    existed = get(patient_id) is not None
    _run("DELETE FROM clients WHERE patient_id = ?", (patient_id,))
    return existed


def update(patient_id: str, changes: dict, by: str) -> dict:
    """Change the fields in `changes` (only EDITABLE ones), one statement each:
    the SQL text stays fixed and every value is a bound parameter."""
    statements = {
        "full_name": "UPDATE clients SET full_name = ?, updated_by = ?, updated_at = ? WHERE patient_id = ?",
        "phone": "UPDATE clients SET phone = ?, updated_by = ?, updated_at = ? WHERE patient_id = ?",
        "email": "UPDATE clients SET email = ?, updated_by = ?, updated_at = ? WHERE patient_id = ?",
        "kvkk_signed_on": "UPDATE clients SET kvkk_signed_on = ?, updated_by = ?, updated_at = ? WHERE patient_id = ?",
    }
    now = time.time()
    for field, value in changes.items():
        if field in statements:
            _run(statements[field], (value, by, now, patient_id))
    return get(patient_id)
