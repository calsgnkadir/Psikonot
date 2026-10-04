"""
tests/test_role_migration.py — an old database keeps working after the rename
=============================================================================
Mahrem renamed two roles (doctor -> practitioner, vip_patient -> client) and
replaced the demo accounts. A database created before that still holds the old
role ids and the old demo accounts:

  * init_db() rewrites the old role ids, or every role check would reject
    those users;
  * seed_default_users() must still add the new demo accounts, even though the
    old demo doctor sits on the id the new one used to have, and must switch
    the old demo accounts off — their passwords are public.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.sql_db import default_sql_db

LEGACY_ROLE_USERS = {"legacy.doc": "doctor", "legacy.client": "vip_patient"}


def run_sql(sql, params=()):
    """Runs one statement on the test database; returns rows for a SELECT."""
    db = default_sql_db
    if db.is_postgres:
        sql = sql.replace("?", "%s")
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall() if sql.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return rows
    finally:
        cur.close()
        conn.close()


def insert_user(user_id, username, role, password_hash="x"):
    run_sql("DELETE FROM users WHERE username = ? OR id = ?", (username, user_id))
    run_sql(
        "INSERT INTO users (id, username, password_hash, role, full_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, username, password_hash, role, "Legacy User"),
    )


class TestRoleMigration(unittest.TestCase):
    def setUp(self):
        default_sql_db.init_db()
        for username, old_role in LEGACY_ROLE_USERS.items():
            insert_user(f"USR-{username}", username, old_role)

    def tearDown(self):
        for username in LEGACY_ROLE_USERS:
            run_sql("DELETE FROM users WHERE username = ?", (username,))

    def _roles(self):
        return dict(run_sql(
            "SELECT username, role FROM users WHERE username IN (?, ?)",
            tuple(LEGACY_ROLE_USERS),
        ))

    def test_old_role_ids_are_renamed_on_start(self):
        default_sql_db.init_db()
        roles = self._roles()
        self.assertEqual(roles["legacy.doc"], "practitioner")
        self.assertEqual(roles["legacy.client"], "client")

    def test_old_client_accounts_are_switched_off(self):
        # Clients no longer sign in; an account left from before is disabled.
        default_sql_db.init_db()
        status = dict(run_sql("SELECT username, account_status FROM users WHERE username = ?",
                              ("legacy.client",)))
        self.assertEqual(status["legacy.client"], "DISABLED")

    def test_migration_is_safe_to_run_twice(self):
        default_sql_db.init_db()
        default_sql_db.init_db()  # must not fail or change anything further
        self.assertEqual(set(self._roles().values()), {"practitioner", "client"})


class TestLegacyDemoAccounts(unittest.TestCase):
    def test_old_demo_account_is_disabled_and_new_ones_are_seeded(self):
        from core.security import hash_password
        from fastapi.testclient import TestClient
        from backend.main import app

        os.environ["TESTING"] = "true"
        default_sql_db.init_db()
        # The pre-Mahrem demo doctor, on the id the new practitioner used to have.
        insert_user("USR-DOC-001", "dr.smith", "practitioner",
                    hash_password("Doctor@2026Secure!"))

        default_sql_db.seed_default_users()

        status = dict(run_sql(
            "SELECT username, account_status FROM users WHERE username IN (?, ?, ?)",
            ("dr.smith", "psk.elif", "secretary.ayse"),
        ))
        self.assertEqual(status["dr.smith"], "DISABLED")
        self.assertIn("psk.elif", status)
        self.assertIn("secretary.ayse", status)

        # The public password must no longer open a session.
        res = TestClient(app).post("/api/v1/auth/login", json={
            "username": "dr.smith", "password": "Doctor@2026Secure!"})
        self.assertEqual(res.status_code, 403, res.text)
        self.assertIn("disabled", res.text)


if __name__ == "__main__":
    unittest.main()
