"""
core/services/alert_service.py — Real-Time Security Alert & Anomaly Engine
==========================================================================
Captures critical security events (dual-control violations, rapid failed auth,
unauthorized IP attempts) and records them into an immutable alert queue.
"""

import logging
import time
import json
import secrets
from typing import List, Dict, Optional
from database.sql_db import get_sql_db, run_sql

logger = logging.getLogger("vhv.alertservice")

# An alert's title and description are read by the center's operators, so they
# are written in Turkish; alert_type and severity stay English machine codes.
# These name the codes an alert's text mentions.
ROLE_TR = {
    "admin": "yönetici",
    "practitioner": "psikolog",
    "secretary": "sekreter",
    "security_officer": "güvenlik sorumlusu",
    "auditor": "denetçi",
    "client": "danışan",
}

REQUEST_TYPE_TR = {
    "DECRYPT_RAW_RECORD": "kayıt okuma",
    "ERASE_PATIENT": "danışan verisini silme",
    "REVOKE_PASSKEY": "geçiş anahtarını kaldırma",
    "RESET_ACCOUNT": "şifre sıfırlama",
    "ENABLE_ACCOUNT": "hesabı yeniden etkinleştirme",
}


def role_tr(role: Optional[str]) -> str:
    """A role code in Turkish, for alert text; an unknown code as it is."""
    return ROLE_TR.get(role or "", role or "?")


def request_type_tr(request_type: Optional[str]) -> str:
    """A dual-control request type in Turkish, for alert text."""
    return REQUEST_TYPE_TR.get(request_type or "", request_type or "?")


class AlertService:
    """
    Centralized Security Alert Manager & Anomaly Detection Pipeline.
    """

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        # REAL is a 4-byte float in PostgreSQL: too coarse for epoch seconds.
        double_type = "DOUBLE PRECISION" if get_sql_db().is_postgres else "REAL"
        run_sql(f"""
            CREATE TABLE IF NOT EXISTS security_alerts (
                alert_id TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                username TEXT,
                client_ip TEXT,
                created_at {double_type} NOT NULL,
                acknowledged INTEGER DEFAULT 0,
                metadata_json TEXT
            )
        """)

    def raise_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        description: str,
        username: Optional[str] = None,
        client_ip: Optional[str] = None,
        extra: Optional[dict] = None
    ) -> str:
        self._ensure_table()
        alert_id = f"alt_{secrets.token_hex(10)}"
        now = time.time()
        meta_json = json.dumps(extra or {})

        run_sql(
            """
            INSERT INTO security_alerts (alert_id, alert_type, severity, title, description, username, client_ip, created_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (alert_id, alert_type, severity, title, description, username, client_ip, now, meta_json)
        )

        # The log stays English: the machine code, not the Turkish display text.
        logger.info(f"[SECURITY ALERT - {severity}] {alert_type} {alert_id} (User: {username}, IP: {client_ip})")
        return alert_id

    def get_recent_alerts(self, limit: int = 50, severity_filter: Optional[str] = None) -> List[Dict]:
        if severity_filter:
            rows = run_sql(
                "SELECT alert_id, alert_type, severity, title, description, username, client_ip, created_at, acknowledged, metadata_json FROM security_alerts WHERE severity = ? ORDER BY created_at DESC LIMIT ?",
                (severity_filter.upper(), limit), fetch="all"
            )
        else:
            rows = run_sql(
                "SELECT alert_id, alert_type, severity, title, description, username, client_ip, created_at, acknowledged, metadata_json FROM security_alerts ORDER BY created_at DESC LIMIT ?",
                (limit,), fetch="all"
            )

        alerts = []
        for r in rows:
            alerts.append({
                "alert_id": r[0],
                "alert_type": r[1],
                "severity": r[2],
                "title": r[3],
                "description": r[4],
                "username": r[5],
                "client_ip": r[6],
                "created_at": r[7],
                "acknowledged": bool(r[8]),
                "metadata": json.loads(r[9]) if r[9] else {}
            })
        return alerts

    def acknowledge_alert(self, alert_id: str) -> bool:
        return run_sql("UPDATE security_alerts SET acknowledged = 1 WHERE alert_id = ?",
                       (alert_id,), fetch="rowcount") > 0


# Global singleton instance
alert_service = AlertService()
