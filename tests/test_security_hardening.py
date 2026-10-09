"""
tests/test_security_hardening.py — Tests for security hardening
===============================================================
Covering:
1. Network IP Allowlisting Middleware (IP restriction, block public IPs)
2. Real-Time Security Alert Service & Anomaly Engine
3. Dual-Control (M-of-N Approval) Engine & Anti-Self-Approval
"""

import unittest
from fastapi.testclient import TestClient
from backend.main import app
from core.services.alert_service import alert_service
from core.services.dual_control import dual_control_engine


class TestSecurityHardening(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_alert_service_raise_and_retrieve(self):
        alert_id = alert_service.raise_alert(
            alert_type="UNAUTHORIZED_ACCESS_SPIKE",
            severity="CRITICAL",
            title="Rapid Failed Login Spikes",
            description="5 failed attempts in 10 seconds",
            username="attacker_user",
            client_ip="198.51.100.4"
        )
        self.assertIsNotNone(alert_id)
        self.assertTrue(alert_id.startswith("alt_"))

        recent_alerts = alert_service.get_recent_alerts(limit=10, severity_filter="CRITICAL")
        found = any(a["alert_id"] == alert_id for a in recent_alerts)
        self.assertTrue(found)

        # Test acknowledgement
        ack_success = alert_service.acknowledge_alert(alert_id)
        self.assertTrue(ack_success)

    def test_dual_control_workflow_and_security(self):
        patient_id = "CL-SEC-999"

        # 1. Initiate Dual Control Request
        req_res = dual_control_engine.request_dual_control_access(
            request_type="DECRYPT_RAW_RECORD",
            target_patient_id=patient_id,
            requested_by="admin",
            reason="Court order inspection",
            validity_minutes=15
        )
        token_id = req_res["token_id"]
        self.assertEqual(req_res["status"], "PENDING_CO_APPROVAL")

        # 2. Prevent self-approval (Admin requesting cannot self-approve)
        with self.assertRaises(ValueError) as ctx:
            dual_control_engine.co_sign_request(token_id, co_signer_username="admin", co_signer_role="admin")
        self.assertIn("cannot self-approve", str(ctx.exception))

        # 3. Co-sign by a distinct Security Officer
        co_sign_res = dual_control_engine.co_sign_request(
            token_id=token_id,
            co_signer_username="sec_officer_1",
            co_signer_role="security_officer"
        )
        self.assertEqual(co_sign_res["status"], "APPROVED")
        self.assertEqual(co_sign_res["co_signed_by"], "sec_officer_1")

        # 4. Verify approval status
        is_valid = dual_control_engine.is_dual_control_approved(
            token_id, patient_id, request_type="DECRYPT_RAW_RECORD", username="admin")
        self.assertTrue(is_valid)

    def test_admin_record_access_requires_dual_control(self):
        from backend.dependencies import current_user
        from core.domain.entities import User

        admin_user = User(
            id="ADM-001",
            username="admin_test",
            password_hash="hash",
            role="admin",
            full_name="Admin Security Officer"
        )
        app.dependency_overrides[current_user] = lambda: admin_user.to_dict()

        patient_id = "CL-ENFORCE-100"

        # 1. Admin attempts to fetch records without Dual-Control token -> Blocked with 403 Forbidden
        res_blocked = self.client.get(f"/api/v1/records/{patient_id}")
        self.assertEqual(res_blocked.status_code, 403)
        self.assertIn("Dual-Control Policy Violation", res_blocked.json()["detail"])

        # 2. Request and co-sign a Dual-Control access token
        token_data = dual_control_engine.request_dual_control_access("DECRYPT_RAW_RECORD", patient_id, "admin_test", "Investigation")
        token_id = token_data["token_id"]
        dual_control_engine.co_sign_request(token_id, "sec_officer_2", "security_officer")

        # 3. Admin presents approved Dual-Control token -> Access granted
        headers = {"X-Dual-Control-Token": token_id}
        res_granted = self.client.get(f"/api/v1/records/{patient_id}", headers=headers)
        self.assertEqual(res_granted.status_code, 200)

        app.dependency_overrides.clear()

    def test_ip_spoofing_header_blocked_if_untrusted_peer(self):
        from backend.middleware.ip_allowlist import resolve_secure_client_ip
        from unittest.mock import MagicMock

        mock_req = MagicMock()
        mock_req.client.host = "203.0.113.195"  # Public attacker IP
        mock_req.headers = {"X-Forwarded-For": "127.0.0.1"}

        # Without TRUST_PROXIES enabled, X-Forwarded-For is ignored
        resolved_ip = resolve_secure_client_ip(mock_req)
        self.assertEqual(resolved_ip, "203.0.113.195")

    def test_webauthn_revoke_endpoint(self):
        from backend.dependencies import current_user
        from core.domain.entities import User
        from database.sql_db import get_sql_db
        import time

        db = get_sql_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO webauthn_credentials (credential_id, username, public_key, created_at) VALUES (?, ?, ?, ?)",
                ("cred_to_revoke_123", "revoke_test_user", "pubkey_test", time.time())
            )
            conn.commit()

        sec_user = User(
            id="SEC-001",
            username="sec_officer_test",
            password_hash="hash",
            role="security_officer",
            full_name="Security Officer"
        )
        app.dependency_overrides[current_user] = lambda: sec_user.to_dict()

        res = self.client.post("/api/v1/auth/webauthn/revoke", json={
            "username": "revoke_test_user",
            "credential_id": "cred_to_revoke_123"
        })
        self.assertEqual(res.status_code, 200)
        self.assertIn("revoked successfully", res.json()["message"])

        app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
