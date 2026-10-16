"""
tests/test_account_management.py — change, reset, disable and re-enable accounts
================================================================================
There was no way to change a password, to recover a forgotten one, or to switch
an account off short of editing the database. The rules now:

  * anyone changes their own password with the current one; every other open
    session of the account stops working;
  * a forgotten password is replaced with a single-use reset code. A
    practitioner resets their own secretary, as long as that secretary works
    for nobody else. An operator resets anyone else only
    with a co-signed RESET_ACCOUNT token for that username: whoever issues the
    code can redeem it and become the account, and a practitioner account opens
    client files;
  * an operator disables any staff account but their own, a practitioner their
    own secretary (again, only one who works for nobody else); it takes effect
    on the next request. Bringing back anyone but a secretary needs a co-signed
    ENABLE_ACCOUNT token.
  * a secretary shared by several practitioners of a center belongs to none of
    them alone: each practitioner can only remove them from their own book.
"""

import os
import sys
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from backend.middleware.rate_limiter import RATE_LIMIT_MAX
from core.domain.entities import User
from core.security import hash_password
from core.services import appointment_book
from database.sql_db import default_sql_db, run_sql
from infrastructure.repositories.sql_repositories import SQLUserRepository

PASSWORD = "Account@Mgmt2026!"
NEW_PASSWORD = "Changed@Mgmt2026!"

OPERATORS = {
    "admin": ("admin", "Admin@2026Secure!"),
    "officer": ("sec.officer", "SecOfficer@2026!"),
}


class AccountTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        self.client = TestClient(app)
        self.repo = SQLUserRepository()
        self.created = []

    def tearDown(self):
        for username in self.created:
            run_sql("DELETE FROM users WHERE username = ?", (username,))
            run_sql("DELETE FROM enrollment_tokens WHERE username = ?", (username,))
            run_sql("DELETE FROM practice_staff WHERE staff_username = ?", (username,))

    # ── helpers ───────────────────────────────────────────────
    def _user(self, role, practitioner=None, status="ACTIVE_ENROLLED"):
        username = f"{role[:4]}.{uuid.uuid4().hex[:8]}"
        self.created.append(username)
        self.repo.save_user(User(
            id=f"USR-{uuid.uuid4().hex[:12].upper()}", username=username,
            password_hash=hash_password(PASSWORD), role=role,
            full_name=f"Test {role}", account_status=status,
        ))
        if practitioner:
            appointment_book.link_staff(username, practitioner)
        return username

    def _login(self, username, password=PASSWORD):
        return self.client.post("/api/v1/auth/login",
                                json={"username": username, "password": password})

    def _session(self, username, password=PASSWORD):
        res = self._login(username, password)
        self.assertEqual(res.status_code, 200, res.text)
        # Bearer header: the session cookie is Secure and the test client speaks http.
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _operator(self, who):
        return self._session(*OPERATORS[who])

    def _me(self, headers):
        return self.client.get("/api/v1/auth/me", headers=headers).status_code

    def _dual_control(self, request_type, target, requester="admin", co_signer="officer"):
        """An approved token held by `requester`; returns (token_id, headers)."""
        headers = self._operator(requester)
        res = self.client.post("/api/v1/security/dual-control/request", headers=headers, json={
            "request_type": request_type, "target_patient_id": target,
            "reason": "Forgot password, identity checked in person", "validity_minutes": 30})
        self.assertEqual(res.status_code, 200, res.text)
        token_id = res.json()["token_id"]
        signed = self.client.post("/api/v1/security/dual-control/co-sign",
                                  headers=self._operator(co_signer), json={"token_id": token_id})
        self.assertEqual(signed.status_code, 200, signed.text)
        return token_id, {**headers, "X-Dual-Control-Token": token_id}

    def _reset_code(self, username, headers):
        return self.client.post(f"/api/v1/accounts/{username}/reset-code", headers=headers)

    def _redeem_reset(self, code, password=NEW_PASSWORD):
        return self.client.post("/api/v1/accounts/redeem-reset",
                                json={"reset_code": code, "new_password": password})

    def _disable(self, username, headers):
        return self.client.post(f"/api/v1/accounts/{username}/disable", headers=headers)

    def _enable(self, username, headers):
        return self.client.post(f"/api/v1/accounts/{username}/enable", headers=headers)

    def _status(self, username):
        return self.repo.load_user(username).account_status


class TestChangePassword(AccountTestCase):
    def _change(self, headers, current=PASSWORD, new=NEW_PASSWORD):
        return self.client.post("/api/v1/accounts/password", headers=headers,
                                json={"current_password": current, "new_password": new})

    def test_wrong_current_password_is_refused(self):
        username = self._user("practitioner")
        res = self._change(self._session(username), current="Wrong@Password2026!")
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self._login(username).status_code, 200)

    def test_weak_password_is_refused(self):
        username = self._user("secretary")
        res = self._change(self._session(username), new="short")
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(self._login(username).status_code, 200)

    def test_same_password_is_refused(self):
        username = self._user("secretary")
        self.assertEqual(self._change(self._session(username), new=PASSWORD).status_code, 422)

    def test_other_sessions_end_and_the_new_password_works(self):
        username = self._user("practitioner")
        this_tab = self._session(username)
        other_device = self._session(username)
        res = self._change(this_tab)
        self.assertEqual(res.status_code, 200, res.text)

        self.assertEqual(self._me(other_device), 401)
        self.assertEqual(self._me(this_tab), 401, "the old token of this tab is replaced too")
        fresh = {"Authorization": f"Bearer {res.json()['access_token']}"}
        self.assertEqual(self._me(fresh), 200, "the session that changed it continues")

        self.assertEqual(self._login(username).status_code, 401)
        self.assertEqual(self._login(username, NEW_PASSWORD).status_code, 200)

    def test_needs_a_session(self):
        res = self.client.post("/api/v1/accounts/password",
                               json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        self.assertEqual(res.status_code, 401)

    def test_demo_accounts_keep_their_published_passwords(self):
        """In a public demo one visitor must not lock everyone else out."""
        headers = self._operator("officer")
        with mock.patch.dict(os.environ, {"VHV_DEMO_MODE": "true"}):
            res = self._change(headers, current="SecOfficer@2026!")
        self.assertEqual(res.status_code, 403, res.text)
        self.assertEqual(self._login("sec.officer", "SecOfficer@2026!").status_code, 200)


class TestChangePasswordRateLimit(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.username = self._user("practitioner")
        self.headers = self._session(self.username)
        self._testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "false"
        run_sql("DELETE FROM rate_limits")

    def tearDown(self):
        run_sql("DELETE FROM rate_limits")
        os.environ["TESTING"] = self._testing or "true"
        super().tearDown()

    def test_current_password_guesses_are_limited(self):
        """A stolen session must not become a way to guess the password."""
        # CSRF is enforced when TESTING is off: a matching double-submit pair.
        # Sent by hand, since the cookie is Secure outside development.
        csrf = "csrf-test-value"
        headers = {**self.headers, "X-CSRF-Token": csrf, "Cookie": f"csrf_token={csrf}"}
        body = {"current_password": "Wrong@Password2026!", "new_password": NEW_PASSWORD}
        for _ in range(RATE_LIMIT_MAX):
            res = self.client.post("/api/v1/accounts/password", headers=headers, json=body)
            self.assertEqual(res.status_code, 400, res.text)
        res = self.client.post("/api/v1/accounts/password", headers=headers,
                               json={**body, "current_password": PASSWORD})
        self.assertEqual(res.status_code, 429, res.text)


class TestPasswordReset(AccountTestCase):
    def test_practitioner_resets_their_own_secretary(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        secretary_session = self._session(secretary)

        res = self._reset_code(secretary, self._session(practitioner))
        self.assertEqual(res.status_code, 200, res.text)
        code = res.json()["reset_code"]
        # Issuing alone changes nothing: the secretary may still remember it.
        self.assertEqual(self._me(secretary_session), 200)

        redeemed = self._redeem_reset(code)
        self.assertEqual(redeemed.status_code, 200, redeemed.text)
        self.assertEqual(self._me(secretary_session), 401)
        self.assertEqual(self._login(secretary).status_code, 401)
        self.assertEqual(self._login(secretary, NEW_PASSWORD).status_code, 200)
        self.assertEqual(self._redeem_reset(code, "Again@Mgmt2026!!").status_code, 400,
                         "a reset code works once")

    def test_practitioner_cannot_reset_another_practitioners_secretary(self):
        owner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=owner)
        other = self._user("practitioner")
        self.assertEqual(self._reset_code(secretary, self._session(other)).status_code, 403)

    def test_practitioner_cannot_reset_a_colleague(self):
        other = self._user("practitioner")
        res = self._reset_code(other, self._session(self._user("practitioner")))
        self.assertEqual(res.status_code, 403)

    def test_secretary_cannot_issue_reset_codes(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        self.assertEqual(self._reset_code(practitioner, self._session(secretary)).status_code, 403)

    def test_admin_cannot_reset_a_practitioner_alone(self):
        """The issuer holds the code. Alone, an admin could reset a practitioner,
        redeem the code and open that practitioner's client files."""
        practitioner = self._user("practitioner")
        res = self._reset_code(practitioner, self._operator("admin"))
        self.assertEqual(res.status_code, 403, res.text)
        self.assertNotIn("reset_code", res.text)

    def test_officer_cannot_reset_a_practitioner_alone(self):
        practitioner = self._user("practitioner")
        self.assertEqual(self._reset_code(practitioner, self._operator("officer")).status_code, 403)

    def test_admin_resets_a_practitioner_with_a_co_signed_token(self):
        practitioner = self._user("practitioner")
        old_session = self._session(practitioner)
        _, headers = self._dual_control("RESET_ACCOUNT", practitioner)
        res = self._reset_code(practitioner, headers)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._redeem_reset(res.json()["reset_code"]).status_code, 200)
        self.assertEqual(self._me(old_session), 401)
        self.assertEqual(self._login(practitioner, NEW_PASSWORD).status_code, 200)

    def test_the_issuer_counts_as_the_accounts_provisioner(self):
        """The issuer held a code to the account, so dual control does not
        count the two as different people afterwards."""
        auditor = self._user("auditor")
        _, headers = self._dual_control("RESET_ACCOUNT", auditor)
        self.assertEqual(self._reset_code(auditor, headers).status_code, 200)
        row = run_sql("SELECT provisioned_by FROM users WHERE username = ?", (auditor,), fetch="one")[0]
        self.assertEqual(row[0], "admin")

    def test_a_token_is_used_once(self):
        practitioner = self._user("practitioner")
        _, headers = self._dual_control("RESET_ACCOUNT", practitioner)
        self.assertEqual(self._reset_code(practitioner, headers).status_code, 200)
        self.assertEqual(self._reset_code(practitioner, headers).status_code, 403)

    def test_token_for_one_user_does_not_reset_another(self):
        user_a = self._user("practitioner")
        user_b = self._user("practitioner")
        _, headers = self._dual_control("RESET_ACCOUNT", user_a)
        self.assertEqual(self._reset_code(user_b, headers).status_code, 403)

    def test_token_of_another_type_does_not_reset(self):
        practitioner = self._user("practitioner")
        _, headers = self._dual_control("ENABLE_ACCOUNT", practitioner)
        self.assertEqual(self._reset_code(practitioner, headers).status_code, 403)

    def test_issuer_cannot_co_sign_their_own_reset(self):
        practitioner = self._user("practitioner")
        admin = self._operator("admin")
        res = self.client.post("/api/v1/security/dual-control/request", headers=admin, json={
            "request_type": "RESET_ACCOUNT", "target_patient_id": practitioner,
            "reason": "Forgot password", "validity_minutes": 30})
        token_id = res.json()["token_id"]
        signed = self.client.post("/api/v1/security/dual-control/co-sign", headers=admin,
                                  json={"token_id": token_id})
        self.assertEqual(signed.status_code, 400, signed.text)
        res = self._reset_code(practitioner, {**admin, "X-Dual-Control-Token": token_id})
        self.assertEqual(res.status_code, 403)

    def test_the_target_cannot_approve_their_own_reset(self):
        """An admin asking to take over the security officer's account must
        find someone other than that officer to agree."""
        officer = self._user("security_officer")
        admin = self._operator("admin")
        res = self.client.post("/api/v1/security/dual-control/request", headers=admin, json={
            "request_type": "RESET_ACCOUNT", "target_patient_id": officer,
            "reason": "Forgot password", "validity_minutes": 30})
        token_id = res.json()["token_id"]
        signed = self.client.post("/api/v1/security/dual-control/co-sign",
                                  headers=self._session(officer), json={"token_id": token_id})
        self.assertEqual(signed.status_code, 400, signed.text)

    def test_cannot_reset_yourself(self):
        self.assertEqual(self._reset_code("admin", self._operator("admin")).status_code, 400)

    def test_reset_code_does_not_open_an_invitation(self):
        """The onboarding redeem sets an account active; a reset code must not
        be usable there (it would re-open a disabled account)."""
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        code = self._reset_code(secretary, self._session(practitioner)).json()["reset_code"]
        res = self.client.post("/api/v1/onboarding/redeem",
                               json={"enrollment_token": code, "new_password": NEW_PASSWORD})
        self.assertEqual(res.status_code, 400, res.text)

    def test_an_invitation_is_not_a_reset_code(self):
        practitioner = self._user("practitioner")
        res = self.client.post("/api/v1/onboarding/invite-secretary", headers=self._session(practitioner),
                               json={"username": f"sec.{uuid.uuid4().hex[:8]}", "full_name": "New Secretary"})
        self.created.append(res.json()["username"])
        self.assertEqual(self._redeem_reset(res.json()["invite_code"]).status_code, 400)

    def test_disabled_account_cannot_be_reset(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        headers = self._session(practitioner)
        code = self._reset_code(secretary, headers).json()["reset_code"]
        self.assertEqual(self._disable(secretary, headers).status_code, 200)
        self.assertEqual(self._redeem_reset(code).status_code, 400, "disabling voids open codes")
        self.assertEqual(self._reset_code(secretary, headers).status_code, 409)
        self.assertEqual(self._status(secretary), "DISABLED")

    def test_a_new_code_replaces_the_previous_one(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        headers = self._session(practitioner)
        first = self._reset_code(secretary, headers).json()["reset_code"]
        second = self._reset_code(secretary, headers).json()["reset_code"]
        self.assertEqual(self._redeem_reset(first).status_code, 400)
        self.assertEqual(self._redeem_reset(second).status_code, 200)

    def test_redeem_needs_a_strong_password(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        code = self._reset_code(secretary, self._session(practitioner)).json()["reset_code"]
        self.assertEqual(self._redeem_reset(code, "weak").status_code, 422)
        self.assertEqual(self._redeem_reset(code).status_code, 200, "a refused attempt does not use the code")


class TestDisableAccount(AccountTestCase):
    def test_disabling_ends_an_open_session_at_once(self):
        practitioner = self._user("practitioner")
        session = self._session(practitioner)
        res = self._disable(practitioner, self._operator("admin"))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._me(session), 401)
        self.assertEqual(self._login(practitioner).status_code, 403)

    def test_cannot_disable_yourself(self):
        for who in ("admin", "officer"):
            username = OPERATORS[who][0]
            self.assertEqual(self._disable(username, self._operator(who)).status_code, 400)
            self.assertEqual(self._status(username), "ACTIVE_ENROLLED")

    def test_officer_disables_an_admin_account(self):
        other_admin = self._user("admin")
        self.assertEqual(self._disable(other_admin, self._operator("officer")).status_code, 200)
        self.assertEqual(self._status(other_admin), "DISABLED")

    def test_practitioner_disables_and_re_enables_their_secretary(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        secretary_session = self._session(secretary)
        headers = self._session(practitioner)

        self.assertEqual(self._disable(secretary, headers).status_code, 200)
        self.assertEqual(self._me(secretary_session), 401)
        staff = self.client.get("/api/v1/onboarding/staff", headers=headers).json()["staff"]
        self.assertEqual(staff[0]["status"], "disabled")

        self.assertEqual(self._enable(secretary, headers).status_code, 200)
        self.assertEqual(self._me(secretary_session), 401, "old sessions stay ended")
        self.assertEqual(self._login(secretary).status_code, 200)

    def test_practitioner_cannot_disable_another_practitioners_secretary(self):
        secretary = self._user("secretary", practitioner=self._user("practitioner"))
        other = self._session(self._user("practitioner"))
        self.assertEqual(self._disable(secretary, other).status_code, 403)
        self.assertEqual(self._status(secretary), "ACTIVE_ENROLLED")

    def test_practitioner_cannot_disable_other_staff(self):
        headers = self._session(self._user("practitioner"))
        for target in (self._user("practitioner"), "admin"):
            self.assertEqual(self._disable(target, headers).status_code, 403)

    def test_secretary_and_auditor_cannot_disable(self):
        practitioner = self._user("practitioner")
        secretary = self._user("secretary", practitioner=practitioner)
        auditor = self._user("auditor")
        target = self._user("practitioner")
        for actor in (secretary, auditor):
            self.assertEqual(self._disable(target, self._session(actor)).status_code, 403)

    def test_re_enabling_a_practitioner_needs_a_co_signature(self):
        practitioner = self._user("practitioner")
        self.assertEqual(self._disable(practitioner, self._operator("admin")).status_code, 200)
        self.assertEqual(self._enable(practitioner, self._operator("admin")).status_code, 403)
        self.assertEqual(self._status(practitioner), "DISABLED")

        _, headers = self._dual_control("ENABLE_ACCOUNT", practitioner)
        self.assertEqual(self._enable(practitioner, headers).status_code, 200)
        self.assertEqual(self._login(practitioner).status_code, 200)

    def test_re_enabling_an_admin_needs_a_co_signature(self):
        other_admin = self._user("admin")
        self.assertEqual(self._disable(other_admin, self._operator("officer")).status_code, 200)
        self.assertEqual(self._enable(other_admin, self._operator("officer")).status_code, 403)
        _, headers = self._dual_control("ENABLE_ACCOUNT", other_admin, requester="officer", co_signer="admin")
        self.assertEqual(self._enable(other_admin, headers).status_code, 200)

    def test_operator_re_enables_a_secretary_without_co_signature(self):
        secretary = self._user("secretary", practitioner=self._user("practitioner"))
        self.assertEqual(self._disable(secretary, self._operator("admin")).status_code, 200)
        self.assertEqual(self._enable(secretary, self._operator("admin")).status_code, 200)

    def test_disabling_a_pending_secretary_withdraws_the_invitation(self):
        practitioner = self._user("practitioner")
        headers = self._session(practitioner)
        res = self.client.post("/api/v1/onboarding/invite-secretary", headers=headers,
                               json={"username": f"sec.{uuid.uuid4().hex[:8]}", "full_name": "New Secretary"})
        secretary = res.json()["username"]
        self.created.append(secretary)
        self.assertEqual(self._disable(secretary, headers).status_code, 200)
        redeemed = self.client.post("/api/v1/onboarding/redeem", json={
            "enrollment_token": res.json()["invite_code"], "new_password": NEW_PASSWORD})
        self.assertEqual(redeemed.status_code, 400, redeemed.text)
        self.assertEqual(self._status(secretary), "DISABLED")

    def test_unknown_account(self):
        self.assertEqual(self._disable("nobody.here", self._operator("admin")).status_code, 404)

    def test_demo_accounts_cannot_be_disabled_in_demo_mode(self):
        with mock.patch.dict(os.environ, {"VHV_DEMO_MODE": "true"}):
            res = self._disable("psk.elif", self._operator("admin"))
        self.assertEqual(res.status_code, 403, res.text)
        self.assertEqual(self._status("psk.elif"), "ACTIVE_ENROLLED")

    def test_enabling_an_active_account_is_refused(self):
        secretary = self._user("secretary", practitioner=self._user("practitioner"))
        self.assertEqual(self._enable(secretary, self._operator("admin")).status_code, 409)



class TestSharedSecretary(AccountTestCase):
    """One practitioner must not switch off, or take over, the front desk of
    the others: a reset code lets its issuer become an account that sees the
    other practitioners' client cards."""

    def _shared(self):
        first, second = self._user("practitioner"), self._user("practitioner")
        secretary = self._user("secretary", practitioner=first)
        appointment_book.link_staff(secretary, second)
        return first, second, secretary

    def test_a_practitioner_cannot_reset_disable_or_enable_a_shared_secretary(self):
        first, second, secretary = self._shared()
        for practitioner in (first, second):
            headers = self._session(practitioner)
            with self.subTest(practitioner=practitioner):
                res = self._reset_code(secretary, headers)
                self.assertEqual(res.status_code, 403, res.text)
                self.assertNotIn("reset_code", res.text)
                self.assertIn("operator", res.json()["detail"])
                self.assertEqual(self._disable(secretary, headers).status_code, 403)
        self.assertEqual(self._status(secretary), "ACTIVE_ENROLLED")

        self.assertEqual(self._disable(secretary, self._operator("admin")).status_code, 200)
        self.assertEqual(self._enable(secretary, self._session(first)).status_code, 403)
        self.assertEqual(self._status(secretary), "DISABLED")

    def test_an_operator_handles_a_shared_secretary_under_the_usual_rules(self):
        _, _, secretary = self._shared()
        self.assertEqual(self._reset_code(secretary, self._operator("admin")).status_code, 403,
                         "a reset still needs a co-signature")
        _, headers = self._dual_control("RESET_ACCOUNT", secretary)
        self.assertEqual(self._reset_code(secretary, headers).status_code, 200)
        self.assertEqual(self._disable(secretary, self._operator("officer")).status_code, 200)
        self.assertEqual(self._enable(secretary, self._operator("officer")).status_code, 200)

    def test_once_removed_from_the_other_book_the_practitioner_manages_them_again(self):
        first, second, secretary = self._shared()
        res = self.client.post("/api/v1/onboarding/staff/unlink", headers=self._session(second),
                               json={"username": secretary})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self._reset_code(secretary, self._session(first)).status_code, 200)
        # ...and the practitioner who removed them no longer manages them at all.
        self.assertEqual(self._disable(secretary, self._session(second)).status_code, 403)

    def test_the_staff_list_says_who_is_shared(self):
        first, _, shared = self._shared()
        own = self._user("secretary", practitioner=first)
        staff = {s["username"]: s for s in self.client.get(
            "/api/v1/onboarding/staff", headers=self._session(first)).json()["staff"]}
        self.assertTrue(staff[shared]["shared"])
        self.assertFalse(staff[own]["shared"])


if __name__ == "__main__":
    unittest.main()
