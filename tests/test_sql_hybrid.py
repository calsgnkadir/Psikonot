import os
import shutil
import unittest
import tempfile
import database.sql_db as sql_db
from database.sql_db import SQLDatabaseManager
from infrastructure.repositories.sql_repositories import SQLUserRepository
from core.domain.entities import User

class TestSQLHybrid(unittest.TestCase):
    def setUp(self):
        # Create a temp dir for database
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_vault.db")

        # Override SQLITE path and force re-initialization
        self.original_path = sql_db.DEFAULT_SQLITE_PATH
        sql_db.DEFAULT_SQLITE_PATH = self.db_path

        # Re-create manager for test environment
        self.db_manager = SQLDatabaseManager()

        # Patch the default_sql_db in sql_db and repositories modules
        self.original_manager = sql_db.default_sql_db
        sql_db.default_sql_db = self.db_manager

        self.user_repo = SQLUserRepository()

    def tearDown(self):
        # Restore original settings
        sql_db.DEFAULT_SQLITE_PATH = self.original_path
        sql_db.default_sql_db = self.original_manager

        # Clean up temp dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_database_init(self):
        # Verify the database file is created
        self.assertTrue(os.path.exists(self.db_path))

        # Verify default tables can be queried
        conn = self.db_manager.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        self.assertIn("users", tables)
        self.assertIn("clients", tables)
        self.assertIn("blacklisted_tokens", tables)

    def test_user_repository(self):
        user = User(
            id="USR-TEST-99",
            username="psk.sqltest",
            password_hash="hashed_password",
            role="practitioner",
            full_name="Dr. SQL Test",
            specialty="Pediatrics",
            institution="SQL Psychology Practice",
            totp_enabled=False
        )

        # 1. Save user
        self.user_repo.save_user(user)
        self.assertTrue(self.user_repo.user_exists("psk.sqltest"))

        # 2. Load user
        loaded = self.user_repo.load_user("psk.sqltest")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.full_name, "Dr. SQL Test")
        self.assertEqual(loaded.specialty, "Pediatrics")
        self.assertEqual(loaded.institution, "SQL Psychology Practice")
        self.assertFalse(loaded.totp_enabled)

        # 3. Update user
        loaded.totp_enabled = True
        loaded.specialty = "Clinical Psychology"
        self.user_repo.save_user(loaded)

        updated = self.user_repo.load_user("psk.sqltest")
        self.assertTrue(updated.totp_enabled)
        self.assertEqual(updated.specialty, "Clinical Psychology")

        # 4. Load all
        all_users = self.user_repo.load_all_users()
        self.assertEqual(len(all_users), 1)
        self.assertEqual(all_users[0].username, "psk.sqltest")

        # 5. Delete user
        deleted = self.user_repo.delete_user("psk.sqltest")
        self.assertTrue(deleted)
        self.assertFalse(self.user_repo.user_exists("psk.sqltest"))


if __name__ == '__main__':
    unittest.main()
