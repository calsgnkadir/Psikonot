"""
tests/test_backup_restore.py — backup, restore, and erasures that stay erased
=============================================================================
`python -m scripts.backup` takes an encrypted snapshot of a running vault and
`python -m scripts.restore` puts it back into an empty one and checks it. The
tests build a vault of their own in a temp folder (users, client cards,
appointments, a plain record, a password-protected one and one with an
attachment), back it up, and restore it into a second, empty vault:

- everything reads back the same and every chain is valid;
- a backup with one changed byte, a non-empty target, or the wrong keys are
  refused before anything is written;
- the signing key and the pseudonym secret are never inside the data backup,
  and the client names are not readable in it;
- a client erased AFTER the backup stays erased when that backup is restored:
  their records stay unreadable and their card and mapping stay gone. Restore
  refuses an erasure ledger that is missing or older than the backup.

Each vault runs in its own child process (tests/backup_fixture.py), because
the app reads its paths from the environment once, at import time.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tests.backup_fixture import CLIENTS  # noqa: E402

SIGNING_KEY = "backup-test-signing-key-6f1c2a9e0b7d4c3f8a5e"
PSEUDONYM_SECRET = "backup-test-pseudonym-secret-2b8d7e1f4a9c"
PASSPHRASE = "a long backup passphrase for the tests"


class BackupRestoreTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Short paths: LMDB on Windows hits the 260-character limit otherwise.
        cls.root = tempfile.mkdtemp(prefix="pb")
        cls.ledger = os.path.join(cls.root, "ledger", "erasures.jsonl")
        cls.expected = cls._fixture("src", "populate")
        cls.backup = cls._backup("backups")
        # The ledger as it was when the backup was taken (empty: nothing erased yet).
        cls.ledger_at_backup = os.path.join(cls.root, "ledger-at-backup.jsonl")
        shutil.copyfile(cls.ledger, cls.ledger_at_backup)
        cls.keys_dir = os.path.join(cls.root, "keys")
        res = cls._run(["scripts.backup", "--keys-out", cls.keys_dir], "src")
        assert res.returncode == 0, res.stdout + res.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────
    @classmethod
    def _env(cls, vault, **overrides):
        env = dict(os.environ)
        env.pop("VHV_DATABASE_URL", None)
        env.update({
            "TESTING": "true", "ENVIRONMENT": "development", "VHV_DEMO_MODE": "true",
            "KMS_PROVIDER": "software",
            "HEALTH_BLOCKCHAIN_KEY": SIGNING_KEY,
            "PSEUDONYM_SECRET": PSEUDONYM_SECRET,
            "VHV_KEY_FILE": os.path.join(cls.root, ".private_key"),
            "VHV_DEVICE_FINGERPRINT_FILE": os.path.join(cls.root, ".device_fingerprint"),
            "VHV_SQLITE_PATH": os.path.join(cls.root, vault, "vault.db"),
            "VHV_PROJECTS_DIR": os.path.join(cls.root, vault, "projects"),
            "VHV_ERASURE_LEDGER": cls.ledger,
            "PSIKONOT_BACKUP_PASSPHRASE": PASSPHRASE,
            "PYTHONIOENCODING": "utf-8",
        })
        env.update(overrides)
        return env

    @classmethod
    def _run(cls, args, vault, **overrides):
        return subprocess.run(
            [sys.executable, "-m", *args], cwd=REPO, env=cls._env(vault, **overrides),
            capture_output=True, text=True, encoding="utf-8", timeout=600,
        )

    @classmethod
    def _fixture(cls, vault, *args):
        res = cls._run(["tests.backup_fixture", *args], vault)
        assert res.returncode == 0, res.stdout + res.stderr
        return json.loads(res.stdout.split("@@JSON@@", 1)[1])

    @classmethod
    def _backup(cls, name):
        out = os.path.join(cls.root, name)
        res = cls._run(["scripts.backup", "--out", out], "src")
        assert res.returncode == 0, res.stdout + res.stderr
        made = os.listdir(out)
        assert len(made) == 1, made
        return os.path.join(out, made[0])

    def _restore(self, vault, backup=None, *extra, ledger=None, **overrides):
        args = ["scripts.restore", "--from", backup or self.backup,
                "--erasure-ledger", ledger or self.ledger_at_backup, *extra]
        return self._run(args, vault, **overrides)

    def _read(self, vault):
        return self._fixture(vault, "read", self.expected["attachment"]["ref"])

    def _assert_empty(self, vault):
        self.assertFalse(os.path.exists(os.path.join(self.root, vault, "vault.db")))
        projects = os.path.join(self.root, vault, "projects")
        self.assertFalse(os.path.isdir(projects) and os.listdir(projects))

    def _backup_files(self, backup):
        for dirpath, _, names in os.walk(backup):
            for name in names:
                with open(os.path.join(dirpath, name), "rb") as f:
                    yield name, f.read()

    # ── round trip ─────────────────────────────────────────────
    def test_restore_reads_back_everything_and_chains_are_valid(self):
        res = self._restore("t1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        got = self._read("t1")
        for pid, (name, phone, email) in CLIENTS.items():
            client = got["clients"][pid]
            self.assertTrue(client["chain_valid"], pid)
            self.assertTrue(client["erasure_key"], pid)
            self.assertTrue(client["mapping"], pid)
            self.assertEqual(client["card"]["full_name"], name)
            self.assertEqual(client["card"]["phone"], phone)
            self.assertEqual(client["appointments"], 1)
            for index, data in self.expected["plain"][pid].items():
                self.assertEqual(client["records"][index], data)
        self.assertEqual(got["protected_read"], self.expected["protected"]["data"])
        self.assertEqual(got["attachment_blob"], self.expected["attachment"]["blob"])
        self.assertIn("chains valid", res.stdout)

    def test_backup_manifest_lists_hashes_and_counts(self):
        with open(os.path.join(self.backup, "backup.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertIn("created_at", meta)
        self.assertEqual(len(meta["archive_sha256"]), 64)
        self.assertGreaterEqual(meta["counts"]["clients"], len(CLIENTS))
        self.assertGreaterEqual(meta["counts"]["records"], 5)
        self.assertGreaterEqual(meta["counts"]["attachments"], 1)

    # ── refusals ───────────────────────────────────────────────
    def test_tampered_backup_is_refused_before_anything_is_written(self):
        tampered = os.path.join(self.root, "tampered")
        shutil.copytree(self.backup, tampered)
        archive = os.path.join(tampered, "data.enc")
        with open(archive, "r+b") as f:
            f.seek(os.path.getsize(archive) // 2)
            byte = f.read(1)
            f.seek(-1, os.SEEK_CUR)
            f.write(bytes([byte[0] ^ 0x01]))
        res = self._restore("t2", tampered)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("changed", res.stderr)
        self._assert_empty("t2")

        # Someone who also fixes the hash in backup.json is stopped by AES-GCM.
        from scripts.backup_format import sha256_file
        outer_path = os.path.join(tampered, "backup.json")
        with open(outer_path, encoding="utf-8") as f:
            outer = json.load(f)
        outer["archive_sha256"] = sha256_file(archive)
        with open(outer_path, "w", encoding="utf-8") as f:
            json.dump(outer, f)
        res = self._restore("t2", tampered)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("does not decrypt", res.stderr)
        self._assert_empty("t2")

    def test_non_empty_target_is_refused_without_force(self):
        target = os.path.join(self.root, "t3")
        os.makedirs(target)
        with open(os.path.join(target, "vault.db"), "wb") as f:
            f.write(b"existing data")
        res = self._restore("t3")
        self.assertNotEqual(res.returncode, 0)
        with open(os.path.join(target, "vault.db"), "rb") as f:
            self.assertEqual(f.read(), b"existing data")

        res = self._restore("t3", None, "--force")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(self._read("t3")["clients"]["CL-101"]["chain_valid"])

    def test_restore_with_the_wrong_keys_is_refused(self):
        res = self._restore("t7", HEALTH_BLOCKCHAIN_KEY="some-other-signing-key-entirely")
        self.assertNotEqual(res.returncode, 0)
        self._assert_empty("t7")
        res = self._restore("t8", PSEUDONYM_SECRET="some-other-pseudonym-secret")
        self.assertNotEqual(res.returncode, 0)
        self._assert_empty("t8")

    # ── keys are kept apart ────────────────────────────────────
    def test_keys_are_exported_separately_and_never_in_the_data_backup(self):
        exported = os.listdir(self.keys_dir)
        self.assertEqual(len(exported), 1)
        with open(os.path.join(self.keys_dir, exported[0]), encoding="utf-8") as f:
            keys = f.read()
        self.assertIn(SIGNING_KEY, keys)
        self.assertIn(PSEUDONYM_SECRET, keys)

        secrets_and_pii = [SIGNING_KEY, PSEUDONYM_SECRET, "Deniz Backupoğlu", "deniz@example.com"]
        for name, content in self._backup_files(self.backup):
            for value in secrets_and_pii:
                self.assertNotIn(value.encode("utf-8"), content, f"{value!r} in {name}")

        # Inside the encrypted archive: the data, but still no keys.
        from scripts.backup_format import decrypt_archive
        plain = os.path.join(self.root, "decrypted")
        decrypt_archive(self.backup, plain, passphrase=PASSPHRASE)
        found_name = False
        for name, content in self._backup_files(plain):
            self.assertNotIn(SIGNING_KEY.encode(), content, name)
            self.assertNotIn(PSEUDONYM_SECRET.encode(), content, name)
            found_name = found_name or "Deniz Backupoğlu".encode("utf-8") in content
        self.assertTrue(found_name, "the decrypted archive should hold the client cards")

    def test_key_export_is_a_separate_mode_and_kept_away_from_data(self):
        res = self._run(["scripts.backup", "--out", os.path.join(self.root, "x1"),
                         "--keys-out", os.path.join(self.root, "x2")], "src")
        self.assertNotEqual(res.returncode, 0)
        res = self._run(["scripts.backup", "--keys-out", os.path.join(self.root, "src", "projects", "k")], "src")
        self.assertNotEqual(res.returncode, 0)

    # ── the erasure trap ───────────────────────────────────────
    def test_client_erased_after_the_backup_stays_erased_after_restore(self):
        erased = "CL-103"
        self._fixture("src", "erase", erased)

        with open(self.ledger, encoding="utf-8") as f:
            ledger_text = f.read()
        self.assertNotIn(erased, ledger_text)          # pseudonym only, no client id
        self.assertNotIn("Erased Person", ledger_text)
        self.assertEqual(len(ledger_text.strip().splitlines()), 1)

        res = self._restore("t4", ledger=self.ledger)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        got = self._read("t4")["clients"]
        gone = got[erased]
        self.assertTrue(gone["chain_valid"])
        self.assertFalse(gone["erasure_key"])
        self.assertFalse(gone["mapping"])
        self.assertIsNone(gone["card"])
        self.assertEqual(gone["appointments"], 0)
        self.assertTrue(gone["records"])
        for value in gone["records"].values():
            if isinstance(value, dict) and value.get("type") in ("genesis", "audit"):
                continue
            self.assertNotIn("NOTE-CL-103", json.dumps(value))
        self.assertNotIn("NOTE-CL-103", json.dumps(gone))
        # Everyone else is untouched.
        self.assertEqual(got["CL-101"]["card"]["full_name"], CLIENTS["CL-101"][0])
        self.assertIn("NOTE-CL-101", json.dumps(got["CL-101"]["records"]))

        # A ledger older than the backup, or no ledger at all, is refused.
        later = self._backup("backups2")
        res = self._restore("t5", later)
        self.assertNotEqual(res.returncode, 0)
        self._assert_empty("t5")
        res = self._restore("t6", None, ledger=os.path.join(self.root, "no-such-ledger.jsonl"))
        self.assertNotEqual(res.returncode, 0)
        self._assert_empty("t6")


class ErasureLedgerTest(unittest.TestCase):
    """The ledger line is written before the key goes; no line, no erasure."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pl")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self._old = os.environ.get("VHV_ERASURE_LEDGER")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old is None:
            os.environ.pop("VHV_ERASURE_LEDGER", None)
        else:
            os.environ["VHV_ERASURE_LEDGER"] = self._old

    def test_erasure_is_not_done_when_the_ledger_cannot_be_written(self):
        from core.services.erasure_service import get_erasure_key_store
        store = get_erasure_key_store()
        patient = "CL-9871"
        store.get_or_create(patient)
        os.environ["VHV_ERASURE_LEDGER"] = self.dir   # a folder: cannot be appended to
        with self.assertRaises(OSError):
            store.destroy(patient)
        self.assertTrue(store.exists(patient))

        os.environ["VHV_ERASURE_LEDGER"] = os.path.join(self.dir, "ledger.jsonl")
        self.assertTrue(store.destroy(patient))
        self.assertFalse(store.exists(patient))
        store.destroy(patient)   # erasing again adds no second line

        from core.services import erasure_ledger
        entries = erasure_ledger.read_entries(os.environ["VHV_ERASURE_LEDGER"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0]["anon_id"]), 64)

    def test_a_changed_ledger_line_is_detected(self):
        from core.services import erasure_ledger
        path = os.path.join(self.dir, "ledger.jsonl")
        os.environ["VHV_ERASURE_LEDGER"] = path
        erasure_ledger.record("a" * 64)
        erasure_ledger.record("b" * 64)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace("a" * 64, "c" * 64))
        with self.assertRaises(erasure_ledger.LedgerError):
            erasure_ledger.read_entries(path)


if __name__ == "__main__":
    unittest.main()
