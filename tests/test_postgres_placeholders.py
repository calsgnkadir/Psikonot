"""
SQL must run on PostgreSQL as well as SQLite.

psycopg2 takes %s placeholders, not SQLite's ?. Several modules sent raw ?
SQL, so on PostgreSQL every admin record access, erasure and passkey sign-in
failed with a 500. No PostgreSQL server is needed to catch that: the
behavioural tests below run the real code against SQLite through a connection
that behaves like psycopg2 for placeholders (it refuses any SQL containing ?
and accepts %s) and that records whether every connection was closed. The
static test is a second line for code paths the behavioural tests do not reach.
"""

import ast
import hashlib
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.sql_db as sql_db
from backend.main import app
from core.webauthn import b64url_encode
from tests.test_webauthn_passkeys import build_authenticator_data, build_client_data

ROOT = Path(__file__).resolve().parent.parent


class _PsycopgStyleCursor:
    def __init__(self, cursor, violations):
        self._cursor = cursor
        self._violations = violations

    def execute(self, sql, params=()):
        if "?" in sql:
            # Recorded as well as raised, so code that swallows the error
            # still fails the test.
            self._violations.append(" ".join(sql.split()))
            raise sql_db.sqlite3.ProgrammingError("syntax error at or near \"?\" (psycopg2 takes %s)")
        return self._cursor.execute(sql.replace("%s", "?"), params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _PsycopgStyleConnection:
    def __init__(self, conn, violations):
        self._conn = conn
        self._violations = violations
        self.closed = False

    def cursor(self):
        return _PsycopgStyleCursor(self._conn.cursor(), self._violations)

    def close(self):
        self.closed = True
        self._conn.close()

    # `with conn:` ends the transaction and leaves the connection open, as
    # in sqlite3 and psycopg2.
    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class PostgresPlaceholderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sql_db.default_sql_db.seed_default_users()

    def setUp(self):
        db = sql_db.get_sql_db()
        self.violations = []
        self.connections = []

        def connect():
            # The SQLite database, opened the way get_connection() opens it;
            # is_postgres is patched to True below.
            raw = sql_db.sqlite3.connect(sql_db.DEFAULT_SQLITE_PATH)
            raw.row_factory = sql_db.sqlite3.Row
            conn = _PsycopgStyleConnection(raw, self.violations)
            self.connections.append(conn)
            return conn

        for patcher in (mock.patch.object(db, "is_postgres", True),
                        mock.patch.object(db, "get_connection", connect)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        app.dependency_overrides.clear()

    def assert_postgres_safe(self):
        self.assertEqual(self.violations, [], "SQL with ? placeholders would fail on PostgreSQL")
        left_open = [c for c in self.connections if not c.closed]
        self.assertEqual(left_open, [], "every connection must be closed")

    def test_dual_control_request_co_sign_and_check(self):
        from core.services.dual_control import dual_control_engine as engine
        req = engine.request_dual_control_access(
            "DECRYPT_RAW_RECORD", "CL-PG-1", "admin", "placeholder test")
        token = req["token_id"]
        self.assertEqual(engine.get_request(token)["status"], "PENDING_CO_APPROVAL")
        self.assertFalse(engine.is_dual_control_approved(
            token, "CL-PG-1", request_type="DECRYPT_RAW_RECORD", username="admin"))

        with self.assertRaises(ValueError):
            engine.co_sign_request(token, "admin", "admin")   # self-approval
        engine.co_sign_request(token, "sec.officer", "security_officer")
        self.assertTrue(engine.is_dual_control_approved(
            token, "CL-PG-1", request_type="DECRYPT_RAW_RECORD", username="admin"))
        self.assertIsNone(engine.get_request("dc_unknown"))

        expired = engine.request_dual_control_access(
            "ERASE_PATIENT", "CL-PG-1", "admin", "placeholder test")["token_id"]
        with mock.patch("core.services.dual_control.time.time", return_value=time.time() + 3 * 3600):
            with self.assertRaises(ValueError):
                engine.co_sign_request(expired, "sec.officer", "security_officer")
        self.assertEqual(engine.get_request(expired)["status"], "EXPIRED")
        self.assert_postgres_safe()

    def test_security_alerts(self):
        from core.services.alert_service import alert_service
        alert_id = alert_service.raise_alert("PG_TEST", "HIGH", "Placeholder test", "raised on PostgreSQL")
        self.assertIn(alert_id, [a["alert_id"] for a in alert_service.get_recent_alerts(limit=500)])
        self.assertIn(alert_id, [a["alert_id"] for a in alert_service.get_recent_alerts(500, "high")])
        self.assertTrue(alert_service.acknowledge_alert(alert_id))
        self.assertFalse(alert_service.acknowledge_alert("alt_unknown"))
        self.assert_postgres_safe()

    def test_passkey_register_login_and_revoke(self):
        from backend.dependencies import current_user
        os.environ["VHV_WEBAUTHN_RP_ID"] = "127.0.0.1"
        os.environ["VHV_WEBAUTHN_ORIGINS"] = "http://127.0.0.1:8000"

        client = TestClient(app)
        key = ec.generate_private_key(ec.SECP256R1())
        public_key = b64url_encode(key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
        credential_id = b64url_encode(os.urandom(16))

        app.dependency_overrides[current_user] = lambda: {"username": "secretary.ayse", "role": "secretary"}
        challenge = client.get("/api/v1/auth/webauthn/challenge").json()["challenge"]
        res = client.post("/api/v1/auth/webauthn/register", json={
            "credential_id": credential_id,
            "public_key": public_key,
            "client_data_json": b64url_encode(build_client_data(challenge, ceremony="webauthn.create")),
        })
        self.assertEqual(res.status_code, 200, res.text)
        app.dependency_overrides.clear()

        challenge = client.get("/api/v1/auth/webauthn/challenge").json()["challenge"]
        client_data = build_client_data(challenge)
        auth_data = build_authenticator_data(sign_count=1)
        signature = key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        res = client.post("/api/v1/auth/webauthn/login", json={
            "credential_id": credential_id,
            "signature": b64url_encode(signature),
            "client_data_json": b64url_encode(client_data),
            "authenticator_data": b64url_encode(auth_data),
        })
        self.assertEqual(res.status_code, 200, res.text)

        app.dependency_overrides[current_user] = lambda: {"username": "sec.officer", "role": "security_officer"}
        res = client.post("/api/v1/auth/webauthn/revoke",
                          json={"username": "secretary.ayse", "credential_id": credential_id})
        self.assertEqual(res.status_code, 200, res.text)
        self.assert_postgres_safe()

    def test_appointment_client_and_kvkk_helpers(self):
        from core.services import appointment_book, client_registry, kvkk
        card = client_registry.add(practitioner="psk.elif", full_name="PG Test", phone=None, email=None,
                                   kvkk_signed_on=None, created_by="psk.elif")
        patient_id = card["patient_id"]
        client_registry.update(patient_id, {"phone": "+90 532 000 00 99"}, by="psk.elif")
        self.assertEqual(client_registry.owner_of(patient_id), "psk.elif")
        self.assertIn(patient_id, [c["patient_id"] for c in client_registry.list_for("psk.elif")])

        start = time.time() + 400 * 86400
        appt = appointment_book.book(practitioner="psk.elif", patient_id=patient_id, starts_at=start,
                                     duration_min=50, session_format="Online", created_by="psk.elif")
        appointment_book.set_status(appt, "cancelled", by="psk.elif")
        self.assertEqual(appointment_book.practitioner_for({"role": "secretary", "username": "secretary.ayse"}),
                         "psk.elif")
        appointment_book.link_staff("pg.desk", "psk.elif")
        self.assertTrue(appointment_book.is_linked("pg.desk", "psk.elif"))
        self.assertEqual(appointment_book.practitioners_of("pg.desk"), ["psk.elif"])
        self.assertTrue(appointment_book.unlink_staff("pg.desk", "psk.elif"))
        self.assertEqual(appointment_book.books_for({"role": "secretary", "username": "pg.desk"}), [])
        self.assertEqual(len(appointment_book.list_appointments(patient_id=patient_id, start=start - 1,
                                                                end=start + 1)), 1)
        appointment_book.tally("psk.elif")

        request = kvkk.request_erasure(patient_id, "psk.elif")
        self.assertEqual(kvkk.list_requests(patient_id=patient_id)[0]["id"], request["id"])
        kvkk.close_request(request["id"], kvkk.REJECTED, "admin")

        appointment_book.remove_for_client(patient_id)
        self.assertTrue(client_registry.remove(patient_id))
        self.assert_postgres_safe()


class NoRawQuestionMarkSQLTest(unittest.TestCase):
    """Static guard: an execute() whose SQL is a literal with ? must pass it
    through to_placeholder() (or use run_sql, which does)."""

    DIRS = ("backend", "core", "database", "infrastructure")

    def test_no_execute_with_raw_placeholders(self):
        offenders = []
        for top in self.DIRS:
            for path in (ROOT / top).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr in ("execute", "executemany") and node.args):
                        continue
                    sql = node.args[0]
                    literal = isinstance(sql, ast.Constant) and isinstance(sql.value, str)
                    if (literal or isinstance(sql, ast.JoinedStr)) and "?" in ast.unparse(sql):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [], "wrap the SQL with to_placeholder() or use run_sql()")


if __name__ == "__main__":
    unittest.main()
