import base64
import hashlib
import json
import os
import sys
import unittest

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from core.webauthn import b64url_encode
from database.sql_db import default_sql_db

ORIGIN = "http://127.0.0.1:8000"
RP_ID = "127.0.0.1"


def build_authenticator_data(rp_id: str = RP_ID, flags: int = 0x01, sign_count: int = 0) -> bytes:
    """rpIdHash (32 bytes) || flags (1 byte) || signCount (4 bytes)."""
    return (
        hashlib.sha256(rp_id.encode("utf-8")).digest()
        + bytes([flags])
        + sign_count.to_bytes(4, "big")
    )


def build_client_data(challenge: str, ceremony: str = "webauthn.get", origin: str = ORIGIN) -> bytes:
    return json.dumps(
        {"type": ceremony, "challenge": challenge, "origin": origin},
        separators=(",", ":"),
    ).encode("utf-8")


class TestWebAuthnPasskeys(unittest.TestCase):
    """
    Exercises the real FIDO2 ceremony: an in-test secp256r1 key pair stands in for
    the hardware authenticator, so a valid assertion is genuinely signed and every
    tampered variant must be rejected.
    """

    @classmethod
    def setUpClass(cls):
        # The demo accounts are normally seeded by the app startup event, which the
        # bare TestClient does not run.
        default_sql_db.seed_default_users()

    def setUp(self):
        os.environ["TESTING"] = "true"
        os.environ["VHV_WEBAUTHN_RP_ID"] = RP_ID
        os.environ["VHV_WEBAUTHN_ORIGINS"] = ORIGIN
        self.client = TestClient(app)
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.public_key_b64 = b64url_encode(
            self.private_key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        self.credential_id = b64url_encode(os.urandom(16))

    # ── helpers ────────────────────────────────────────────────
    def _challenge(self) -> str:
        res = self.client.get("/api/v1/auth/webauthn/challenge")
        self.assertEqual(res.status_code, 200)
        return res.json()["challenge"]

    def _auth_headers(self, username="secretary.ayse", password="Secretary@2026!"):
        res = self.client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    def _sign(self, authenticator_data: bytes, client_data: bytes) -> str:
        signature = self.private_key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return b64url_encode(signature)

    def _enroll(self):
        challenge = self._challenge()
        client_data = build_client_data(challenge, ceremony="webauthn.create")
        res = self.client.post(
            "/api/v1/auth/webauthn/register",
            headers=self._auth_headers(),
            json={
                "credential_id": self.credential_id,
                "public_key": self.public_key_b64,
                "client_data_json": b64url_encode(client_data),
            },
        )
        self.assertEqual(res.status_code, 200, res.text)

    def _assert_login(self, *, sign_count=1, flags=0x01, tamper_signature=False, challenge=None):
        challenge = challenge or self._challenge()
        client_data = build_client_data(challenge)
        auth_data = build_authenticator_data(flags=flags, sign_count=sign_count)
        signature = self._sign(auth_data, client_data)
        if tamper_signature:
            raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
            raw[-1] ^= 0xFF
            signature = b64url_encode(bytes(raw))
        return self.client.post(
            "/api/v1/auth/webauthn/login",
            json={
                "credential_id": self.credential_id,
                "signature": signature,
                "client_data_json": b64url_encode(client_data),
                "authenticator_data": b64url_encode(auth_data),
            },
        )

    # ── tests ──────────────────────────────────────────────────
    def test_challenge_is_single_use_and_random(self):
        first, second = self._challenge(), self._challenge()
        self.assertTrue(len(first) > 20)
        self.assertNotEqual(first, second)

    def test_no_default_passkey_is_seeded(self):
        """A pre-seeded credential would be a one-click authentication bypass."""
        res = self.client.post(
            "/api/v1/auth/webauthn/login",
            json={
                "credential_id": "passkey_default_demo",
                "signature": "sig_demo",
                "client_data_json": b64url_encode(build_client_data("x")),
                "authenticator_data": b64url_encode(build_authenticator_data()),
            },
        )
        self.assertEqual(res.status_code, 401)

    def test_valid_assertion_authenticates(self):
        self._enroll()
        res = self._assert_login(sign_count=1)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertIn("access_token", body)
        self.assertEqual(body["user"]["username"], "secretary.ayse")

    def test_response_never_leaks_credential_material(self):
        self._enroll()
        body = self._assert_login(sign_count=1).json()
        self.assertNotIn("password_hash", body["user"])
        self.assertNotIn("totp_secret", body["user"])

    def test_invalid_signature_is_rejected(self):
        self._enroll()
        res = self._assert_login(sign_count=1, tamper_signature=True)
        self.assertEqual(res.status_code, 401)

    def test_forged_signature_from_another_key_is_rejected(self):
        self._enroll()
        attacker_key = ec.generate_private_key(ec.SECP256R1())
        challenge = self._challenge()
        client_data = build_client_data(challenge)
        auth_data = build_authenticator_data(sign_count=1)
        forged = attacker_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        res = self.client.post(
            "/api/v1/auth/webauthn/login",
            json={
                "credential_id": self.credential_id,
                "signature": b64url_encode(forged),
                "client_data_json": b64url_encode(client_data),
                "authenticator_data": b64url_encode(auth_data),
            },
        )
        self.assertEqual(res.status_code, 401)

    def test_replayed_assertion_is_rejected(self):
        """The same challenge must not authenticate twice."""
        self._enroll()
        challenge = self._challenge()
        client_data = build_client_data(challenge)
        auth_data = build_authenticator_data(sign_count=1)
        payload = {
            "credential_id": self.credential_id,
            "signature": self._sign(auth_data, client_data),
            "client_data_json": b64url_encode(client_data),
            "authenticator_data": b64url_encode(auth_data),
        }
        self.assertEqual(self.client.post("/api/v1/auth/webauthn/login", json=payload).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/webauthn/login", json=payload).status_code, 401)

    def test_unknown_challenge_is_rejected(self):
        self._enroll()
        res = self._assert_login(sign_count=1, challenge=b64url_encode(os.urandom(32)))
        self.assertEqual(res.status_code, 401)

    def test_foreign_origin_is_rejected(self):
        self._enroll()
        challenge = self._challenge()
        client_data = build_client_data(challenge, origin="https://evil.example.com")
        auth_data = build_authenticator_data(sign_count=1)
        res = self.client.post(
            "/api/v1/auth/webauthn/login",
            json={
                "credential_id": self.credential_id,
                "signature": self._sign(auth_data, client_data),
                "client_data_json": b64url_encode(client_data),
                "authenticator_data": b64url_encode(auth_data),
            },
        )
        self.assertEqual(res.status_code, 401)

    def test_wrong_relying_party_is_rejected(self):
        self._enroll()
        challenge = self._challenge()
        client_data = build_client_data(challenge)
        auth_data = build_authenticator_data(rp_id="evil.example.com", sign_count=1)
        res = self.client.post(
            "/api/v1/auth/webauthn/login",
            json={
                "credential_id": self.credential_id,
                "signature": self._sign(auth_data, client_data),
                "client_data_json": b64url_encode(client_data),
                "authenticator_data": b64url_encode(auth_data),
            },
        )
        self.assertEqual(res.status_code, 401)

    def test_missing_user_presence_flag_is_rejected(self):
        self._enroll()
        res = self._assert_login(sign_count=1, flags=0x00)
        self.assertEqual(res.status_code, 401)

    def test_cloned_authenticator_counter_is_rejected(self):
        """A signature counter that fails to advance signals a cloned credential."""
        self._enroll()
        self.assertEqual(self._assert_login(sign_count=5).status_code, 200)
        self.assertEqual(self._assert_login(sign_count=3).status_code, 401)

    def test_registration_requires_a_valid_challenge(self):
        client_data = build_client_data(b64url_encode(os.urandom(32)), ceremony="webauthn.create")
        res = self.client.post(
            "/api/v1/auth/webauthn/register",
            headers=self._auth_headers(),
            json={
                "credential_id": self.credential_id,
                "public_key": self.public_key_b64,
                "client_data_json": b64url_encode(client_data),
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_registration_rejects_unusable_key(self):
        challenge = self._challenge()
        client_data = build_client_data(challenge, ceremony="webauthn.create")
        res = self.client.post(
            "/api/v1/auth/webauthn/register",
            headers=self._auth_headers(),
            json={
                "credential_id": self.credential_id,
                "public_key": b64url_encode(b"not-a-public-key"),
                "client_data_json": b64url_encode(client_data),
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_registration_requires_authentication(self):
        challenge = self._challenge()
        client_data = build_client_data(challenge, ceremony="webauthn.create")
        res = self.client.post(
            "/api/v1/auth/webauthn/register",
            json={
                "credential_id": self.credential_id,
                "public_key": self.public_key_b64,
                "client_data_json": b64url_encode(client_data),
            },
        )
        self.assertIn(res.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
