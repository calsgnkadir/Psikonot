import os
import shutil
import unittest
import sys

# Setup project root import path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import LMDBConnectionManager
from infrastructure.repositories.lmdb_repositories import LMDBUserRepository
from core.services.auth_service import AuthService
from core.domain.entities import User
from core.security import hash_password

class TestAuthService(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(os.path.dirname(__file__), "test_projects")
        os.makedirs(self.test_dir, exist_ok=True)
        self.db_manager = LMDBConnectionManager(self.test_dir)
        self.user_repo = LMDBUserRepository(self.db_manager)
        self.auth_service = AuthService(self.user_repo)

    def tearDown(self):
        self.db_manager.close_all()
        if os.path.exists(self.test_dir):
            try:
                shutil.rmtree(self.test_dir)
            except Exception:
                pass

    def test_save_and_load_user(self):
        user = User(
            id="USR-001",
            username="test_doc",
            password_hash=hash_password("DocSecurePassword123!"),
            role="practitioner",
            full_name="Dr. Test Case",
        )
        self.user_repo.save_user(user)
        loaded = self.user_repo.load_user("test_doc")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.full_name, "Dr. Test Case")

    def test_authenticate_success(self):
        user = User(
            id="USR-002",
            username="test_secretary",
            password_hash=hash_password("SecretarySecurePassword123!"),
            role="secretary",
            full_name="Test Secretary"
        )
        self.user_repo.save_user(user)
        authenticated = self.auth_service.authenticate("test_secretary", "SecretarySecurePassword123!", "127.0.0.1")
        self.assertIsNotNone(authenticated)
        self.assertEqual(authenticated.username, "test_secretary")

    def test_authenticate_failure(self):
        authenticated = self.auth_service.authenticate("unknown_user", "SomePassword123!", "127.0.0.1")
        self.assertIsNone(authenticated)

    def test_blacklist_token(self):
        import time
        import database.storage as storage
        jti = "test-jti-123"
        exp = time.time() + 60
        storage.blacklist_token(jti, exp, self.db_manager)
        self.assertTrue(storage.is_token_blacklisted(jti, self.db_manager))

        # Check non-existent
        self.assertFalse(storage.is_token_blacklisted("non-existent-jti", self.db_manager))

        # Check expired jti
        expired_jti = "expired-jti"
        storage.blacklist_token(expired_jti, time.time() - 10, self.db_manager)
        self.assertFalse(storage.is_token_blacklisted(expired_jti, self.db_manager))

    def test_clean_expired_blacklisted_tokens(self):
        import time
        import database.storage as storage
        storage.blacklist_token("jti-1", time.time() - 10, self.db_manager)
        storage.blacklist_token("jti-2", time.time() + 60, self.db_manager)

        storage.clean_expired_blacklisted_tokens(self.db_manager)
        self.assertFalse(storage.is_token_blacklisted("jti-1", self.db_manager))
        self.assertTrue(storage.is_token_blacklisted("jti-2", self.db_manager))

    def test_logout_blacklists_token(self):
        import jwt
        from backend.dependencies import create_token, ALGORITHM, JWT_PUBLIC_KEY
        import database.storage as storage

        user_dict = {
            "id": "USR-002",
            "username": "test_secretary",
            "role": "secretary",
            "full_name": "Test Secretary"
        }
        token = create_token(user_dict)
        payload = jwt.decode(token, JWT_PUBLIC_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        self.assertIsNotNone(jti)

        storage.blacklist_token(jti, exp, self.db_manager)
        self.assertTrue(storage.is_token_blacklisted(jti, self.db_manager))

    def test_blacklisted_token_denied(self):
        import jwt
        from fastapi import HTTPException
        from backend.dependencies import create_token, current_user, ALGORITHM, JWT_PUBLIC_KEY
        import database.storage as storage

        # Save user first so user_repo can load it
        from core.domain.entities import User
        from core.security import hash_password
        user = User(
            id="USR-002",
            username="test_secretary",
            password_hash=hash_password("SecretarySecurePassword123!"),
            role="secretary",
            full_name="Test Secretary"
        )
        self.user_repo.save_user(user)

        user_dict = user.to_dict()
        token = create_token(user_dict)
        payload = jwt.decode(token, JWT_PUBLIC_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")

        storage.blacklist_token(jti, exp, self.db_manager)

        with self.assertRaises(HTTPException) as ctx:
            current_user(access_token=token, creds=None, user_repo=self.user_repo)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Token has been revoked")
