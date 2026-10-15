"""
tests/test_privileged_account_cli.py — creating an admin or security officer
============================================================================
The API does not create the roles that co-sign dual-control requests (one
operator could make their own second approver). They are created on the server
with `python -m scripts.create_privileged_account`: shell access to the server
is the second factor. The account is pending until its single-use code is
redeemed.
"""

import contextlib
import io
import os
import sys
import unittest
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from database.sql_db import run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository
from scripts import create_privileged_account as cli

STRONG = "Privileged@2026Secure!"


class TestCreatePrivilegedAccount(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.username = f"op.{uuid.uuid4().hex[:10]}"

    def tearDown(self):
        run_sql("DELETE FROM users WHERE username = ?", (self.username,))
        run_sql("DELETE FROM enrollment_tokens WHERE username = ?", (self.username,))

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as e:  # argparse refuses bad arguments this way
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def _code_from(self, output):
        lines = [line.split(":", 1)[1].strip() for line in output.splitlines()
                 if line.startswith("Enrollment code:")]
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def _redeem(self, code):
        return self.client.post("/api/v1/onboarding/redeem",
                                json={"enrollment_token": code, "new_password": STRONG})

    def test_creates_a_pending_security_officer_with_a_single_use_code(self):
        status, out, err = self._run("--role", "security_officer", "--username", self.username,
                                     "--full-name", "Second Officer")
        self.assertEqual(status, 0, err)
        user = SQLUserRepository().load_user(self.username)
        self.assertEqual(user.role, "security_officer")
        self.assertEqual(user.account_status, "PENDING_ONBOARDING")

        code = self._code_from(out)
        self.assertEqual(self._redeem(code).status_code, 200)
        self.assertEqual(self._redeem(code).status_code, 400, "the code works once")
        res = self.client.post("/api/v1/auth/login", json={"username": self.username, "password": STRONG})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["user"]["role"], "security_officer")

    def test_creates_an_admin(self):
        status, out, err = self._run("--role", "admin", "--username", self.username)
        self.assertEqual(status, 0, err)
        self.assertEqual(SQLUserRepository().load_user(self.username).role, "admin")

    def test_only_privileged_roles(self):
        status, _, _ = self._run("--role", "practitioner", "--username", self.username)
        self.assertNotEqual(status, 0)
        self.assertIsNone(SQLUserRepository().load_user(self.username))

    def test_refuses_an_existing_username(self):
        status, _, err = self._run("--role", "admin", "--username", "sec.officer")
        self.assertEqual(status, 1)
        self.assertIn("already exists", err)
        self.assertEqual(SQLUserRepository().load_user("sec.officer").role, "security_officer")

    def test_refuses_a_malformed_username(self):
        self.username = "bad name!"
        status, _, _ = self._run("--role", "admin", "--username", self.username)
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
