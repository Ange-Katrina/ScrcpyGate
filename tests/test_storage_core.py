import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_storage(data_dir: Path):
    os.environ["WEB_SCRCPY_DATA_DIR"] = str(data_dir)
    for name in ["app.storage"]:
        sys.modules.pop(name, None)
    return importlib.import_module("app.storage")


class StorageCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
        os.environ.pop("INITIAL_ADMIN_PASSWORD", None)

    def test_fresh_install_generates_displayable_admin_password(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        password = storage.get_initial_admin_password_for_display()
        self.assertTrue(password)
        self.assertTrue((self.tmp / "initial_admin_password.txt").exists())
        user = storage.authenticate("admin", password)
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")

    def test_initial_admin_password_env_is_used_and_displayed(self):
        os.environ["INITIAL_ADMIN_PASSWORD"] = "StrongInitialPwd123"
        storage = load_storage(self.tmp)
        storage.init_db()

        self.assertEqual(storage.get_initial_admin_password_for_display(), "StrongInitialPwd123")
        self.assertIsNotNone(storage.authenticate("admin", "StrongInitialPwd123"))

    def test_legacy_users_are_migrated_without_password_override(self):
        (self.tmp / "initial_admin_password.txt").write_text("stale-password\n", encoding="utf-8")
        (self.tmp / "users.json").write_text(json.dumps({
            "admin": {"password_hash": "not-a-v2-generated-hash", "is_admin": True, "created_at": "2026-07-05 00:00:00"}
        }), encoding="utf-8")
        storage = load_storage(self.tmp)
        storage.init_db()

        users = storage.list_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "admin")
        self.assertEqual(storage.get_initial_admin_password_for_display(), "")

    def test_user_permissions_and_control_lock(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_device("dev1", "Device 1", "192.0.2.10:30100", True)
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.set_permission("alice", "dev1", True, False)

        self.assertTrue(storage.user_can("alice", "dev1", "view"))
        self.assertFalse(storage.user_can("alice", "dev1", "control"))
        storage.set_permission("alice", "dev1", True, True)
        self.assertTrue(storage.user_can("alice", "dev1", "control"))

        first = storage.acquire_lock("dev1", "alice", "client-a")
        self.assertTrue(first["ok"])
        blocked = storage.acquire_lock("dev1", "admin", "client-admin", force=False)
        self.assertFalse(blocked["ok"])
        forced = storage.acquire_lock("dev1", "admin", "client-admin", force=True)
        self.assertTrue(forced["ok"])
        self.assertTrue(storage.lock_owned_by("dev1", "admin"))
        self.assertFalse(storage.release_lock("dev1", "admin", client_id="stale-client"))
        self.assertTrue(storage.lock_owned_by("dev1", "admin"))
        self.assertTrue(storage.release_lock("dev1", "admin", client_id="client-admin"))
        self.assertFalse(storage.lock_owned_by("dev1", "admin"))

    def test_user_video_preference_and_public_device_id(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_device("192.0.2.10:30100", "Device 1", "192.0.2.10:30100", True)
        storage.set_user_video_preference(
            "admin",
            {"profile": "custom", "adaptive": True, "video_bit_rate": 2500000, "max_size": 960, "max_fps": 24, "scrcpy_stream_mode": "protocol"},
        )

        pref = storage.get_user_video_preference("admin")
        self.assertEqual(pref["video_bit_rate"], 2500000)
        self.assertEqual(pref["max_size"], 960)
        self.assertEqual(pref["max_fps"], 24)
        self.assertTrue(pref["adaptive"])
        self.assertEqual(pref["scrcpy_stream_mode"], "protocol")

        public_id = storage.public_device_id("192.0.2.10:30100")
        self.assertTrue(public_id.startswith("dev_"))
        self.assertNotIn("192.0.2.10", public_id)
        self.assertEqual(storage.resolve_device_ref(public_id), "192.0.2.10:30100")
        self.assertEqual(storage.resolve_device_ref("192.0.2.10:30100"), "192.0.2.10:30100")

    def test_user_alas_config_binding(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")

        self.assertIsNone(storage.get_user_alas_config("alice"))
        storage.set_user_alas_config("alice", "AliceMain", True, False)
        binding = storage.get_user_alas_config("alice")
        self.assertEqual(binding["config_name"], "AliceMain")
        self.assertTrue(binding["can_run"])
        self.assertFalse(binding["can_edit"])

        rows = {row["username"]: row for row in storage.list_user_alas_configs()}
        self.assertIn("alice", rows)
        self.assertEqual(rows["alice"]["config_name"], "AliceMain")

        storage.set_user_alas_config("alice", "AliceEdit", False, True)
        binding = storage.get_user_alas_config("alice")
        self.assertEqual(binding["config_name"], "AliceEdit")
        self.assertFalse(binding["can_run"])
        self.assertTrue(binding["can_edit"])

        storage.delete_user_alas_config("alice")
        self.assertIsNone(storage.get_user_alas_config("alice"))

    def test_password_policy_is_enforced(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        with self.assertRaises(ValueError):
            storage.upsert_user("bob", "short", "user")
        with self.assertRaises(ValueError):
            storage.upsert_user("bad user", "StrongPassword123", "user")

    def test_last_admin_cannot_be_removed_or_demoted(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        with self.assertRaisesRegex(ValueError, "last_admin_required"):
            storage.upsert_user("admin", None, "user")
        with self.assertRaisesRegex(ValueError, "last_admin_required"):
            storage.delete_user("admin")

        storage.upsert_user("backup", "BackupPassword123", "admin")
        storage.upsert_user("admin", None, "user")
        self.assertEqual(storage.get_user("admin")["role"], "user")
        storage.delete_user("admin")
        self.assertIsNone(storage.get_user("admin"))

    def test_set_permission_validates_user_and_device(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_device("dev1", "Device 1", "192.0.2.10:30100", True)

        with self.assertRaisesRegex(ValueError, "invalid_username"):
            storage.set_permission("missing", "dev1", True, False)
        with self.assertRaisesRegex(ValueError, "invalid_device"):
            storage.set_permission("alice", "missing", True, False)

        storage.set_permission("alice", "dev1", True, False)
        self.assertTrue(storage.user_can("alice", "dev1", "view"))
        self.assertFalse(storage.user_can("alice", "dev1", "control"))

    def test_change_user_password_requires_current_password_and_clears_other_sessions(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        current = storage.create_session("alice")
        other = storage.create_session("alice")

        with self.assertRaises(ValueError):
            storage.change_user_password("alice", "wrong-password", "AliceNewPassword123")
        with self.assertRaises(ValueError):
            storage.change_user_password("alice", "AlicePassword123", "short")

        storage.change_user_password("alice", "AlicePassword123", "AliceNewPassword123")
        self.assertIsNone(storage.authenticate("alice", "AlicePassword123"))
        self.assertIsNotNone(storage.authenticate("alice", "AliceNewPassword123"))

        removed = storage.delete_other_sessions("alice", current["sid"])
        self.assertEqual(removed, 1)
        self.assertIsNotNone(storage.get_session(current["sid"]))
        self.assertIsNone(storage.get_session(other["sid"]))

    def test_recent_audit_returns_recent_rows_oldest_to_newest(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        for idx in range(12):
            storage.audit(f"user{idx}", "login_success", f"ip=10.0.0.{idx}")

        rows = storage.recent_audit(10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["username"], "user2")
        self.assertEqual(rows[-1]["username"], "user11")
        self.assertEqual(rows[-1]["action"], "login_success")


if __name__ == "__main__":
    unittest.main()
