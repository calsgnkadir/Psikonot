"""
core/services/kvkk.py — the client's KVKK rights, in the product
================================================================
Three things a client can do without writing to the practice:

  * read the privacy notice (aydınlatma metni) and give explicit consent — the
    accepted version and the time are recorded;
  * download a copy of their own data (KVKK Art. 11);
  * ask for their data to be erased. The request does not erase anything by
    itself: an operator carries it out with the crypto-shred, which needs a
    dual-control co-signature, and only then can the request be closed as done.

The notice below is a template. A practice replaces it with its own text and
bumps NOTICE_VERSION, which asks every client to accept the new version.
"""

import time
import uuid
from typing import Optional

from database.sql_db import get_sql_db
from infrastructure.repositories.sql_repositories import _to_placeholder

NOTICE_VERSION = "2026-09"
NOTICE_TEXT = (
    "Data controller: the psychology practice that invited you to Mahrem.\n\n"
    "What we process: your identity and contact details, your appointments, and the "
    "records of your therapy — your profile, session notes and the documents you or your "
    "practitioner add. Records of your therapy are health data, a special category of personal "
    "data under KVKK Art. 6.\n\n"
    "Why: to provide and document psychological counselling, and to schedule sessions.\n\n"
    "How it is protected: records are encrypted, your practitioner sees only what you consent to, "
    "every access is logged and you can see who looked at your records.\n\n"
    "Who else sees it: nobody outside the practice. Operators of the system cannot read your records "
    "without a second person's approval, and the data is kept in Türkiye.\n\n"
    "Your rights (KVKK Art. 11): to know what is processed, to get a copy (\"Download my data\"), to "
    "have it corrected, and to have it erased (\"Request erasure\"), where the law does not require "
    "the practice to keep it.\n\n"
    "By accepting, you give your explicit consent to the processing of your health data for these purposes."
)

OPEN, DONE, REJECTED = "open", "done", "rejected"


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


# ── Privacy notice and explicit consent ───────────────────────
def accepted_at(username: str, version: str = NOTICE_VERSION) -> Optional[float]:
    rows = _run("SELECT accepted_at FROM kvkk_notice_acceptances WHERE username = ? AND notice_version = ?",
                (username, version), fetch="one")
    return rows[0][0] if rows and rows[0] else None


def accept(username: str, client_ip: Optional[str]) -> float:
    """Record the acceptance of the current notice (idempotent)."""
    existing = accepted_at(username)
    if existing:
        return existing
    now = time.time()
    _run("INSERT INTO kvkk_notice_acceptances (username, notice_version, accepted_at, client_ip) "
         "VALUES (?, ?, ?, ?)", (username, NOTICE_VERSION, now, client_ip))
    return now


# ── Erasure requests ──────────────────────────────────────────
_SELECT = ("SELECT id, patient_id, requested_by, requested_at, status, handled_by, handled_at "
           "FROM erasure_requests")
_COLUMNS = ("id", "patient_id", "requested_by", "requested_at", "status", "handled_by", "handled_at")


def _row(row) -> Optional[dict]:
    return {col: row[i] for i, col in enumerate(_COLUMNS)} if row else None


def open_request_for(patient_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE patient_id = ? AND status = ?", (patient_id, OPEN), fetch="one")
    return _row(rows[0]) if rows else None


def request_erasure(patient_id: str, requested_by: str) -> dict:
    if open_request_for(patient_id):
        raise ValueError("An erasure request is already open")
    request_id = f"ERQ-{uuid.uuid4().hex[:12].upper()}"
    _run("INSERT INTO erasure_requests (id, patient_id, requested_by, requested_at, status, handled_by, "
         "handled_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
         (request_id, patient_id, requested_by, time.time(), OPEN, None, None))
    return get_request(request_id)


def get_request(request_id: str) -> Optional[dict]:
    rows = _run(_SELECT + " WHERE id = ?", (request_id,), fetch="one")
    return _row(rows[0]) if rows else None


def list_requests(patient_id: Optional[str] = None) -> list:
    rows = _run(_SELECT + " WHERE (? IS NULL OR patient_id = ?) ORDER BY requested_at DESC",
                (patient_id, patient_id), fetch="all")
    return [_row(r) for r in rows]


def close_request(request_id: str, status: str, handled_by: str) -> dict:
    if status not in (DONE, REJECTED):
        raise ValueError("Status must be done or rejected")
    _run("UPDATE erasure_requests SET status = ?, handled_by = ?, handled_at = ? WHERE id = ?",
         (status, handled_by, time.time(), request_id))
    return get_request(request_id)
