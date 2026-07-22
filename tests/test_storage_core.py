import importlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
        os.environ.pop("INITIAL_ADMIN_PASSWORD", None)

    def test_fresh_install_does_not_persist_plaintext_admin_password(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        self.assertEqual(storage.get_initial_admin_password_for_display(), "")
        self.assertFalse((self.tmp / "initial_admin_password.txt").exists())
        user = storage.get_user("admin")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")
        self.assertTrue(str(user["password_hash"]).startswith("pbkdf2_sha256$"))

    def test_settings_batch_is_committed_together(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        storage.set_settings({"video_fullscreen_profile": "balanced", "max_size": 1280, "max_fps": 30})

        settings = storage.get_settings(["video_fullscreen_profile", "max_size", "max_fps"])
        self.assertEqual(settings["video_fullscreen_profile"], "balanced")
        self.assertEqual(settings["max_size"], "1280")
        self.assertEqual(settings["max_fps"], "30")

    def test_settings_update_uses_a_locked_snapshot(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.set_settings({"video_fullscreen_profile": "sharp", "max_fps": 24})

        observed = {}

        def update(current):
            observed.update(current)
            return {"video_fullscreen_profile": "balanced", "max_fps": 30}

        saved = storage.update_settings(update)

        self.assertEqual(observed["video_fullscreen_profile"], "sharp")
        self.assertEqual(saved["video_fullscreen_profile"], "balanced")
        self.assertEqual(saved["max_fps"], "30")

    def test_initial_admin_password_env_is_used_and_displayed(self):
        os.environ["INITIAL_ADMIN_PASSWORD"] = "StrongInitialPwd123"
        storage = load_storage(self.tmp)
        admin_created = storage.init_db()

        self.assertTrue(admin_created)
        self.assertEqual(storage.get_initial_admin_password_for_display(admin_created), "StrongInitialPwd123")
        self.assertFalse((self.tmp / "initial_admin_password.txt").exists())
        self.assertIsNotNone(storage.authenticate("admin", "StrongInitialPwd123"))

    def test_existing_admin_password_is_never_displayed_again(self):
        os.environ["INITIAL_ADMIN_PASSWORD"] = "StrongInitialPwd123"
        storage = load_storage(self.tmp)
        first_created = storage.init_db()
        second_created = storage.init_db()

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(storage.get_initial_admin_password_for_display(second_created), "")

    def test_legacy_users_are_migrated_without_password_override(self):
        (self.tmp / "initial_admin_password.txt").write_text("stale-password\n", encoding="utf-8")
        (self.tmp / "users.json").write_text(json.dumps({
            "admin": {"password_hash": "not-a-current-generated-hash", "is_admin": True, "created_at": "2026-07-05 00:00:00"}
        }), encoding="utf-8")
        storage = load_storage(self.tmp)
        storage.init_db()

        users = storage.list_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "admin")
        self.assertEqual(storage.get_initial_admin_password_for_display(), "")
        self.assertFalse((self.tmp / "initial_admin_password.txt").exists())

    def test_empty_legacy_users_file_still_creates_an_admin(self):
        os.environ["INITIAL_ADMIN_PASSWORD"] = "StrongInitialPwd123"
        (self.tmp / "users.json").write_text("{}", encoding="utf-8")
        storage = load_storage(self.tmp)

        admin_created = storage.init_db()

        self.assertTrue(admin_created)
        self.assertEqual(storage.get_initial_admin_password_for_display(admin_created), "StrongInitialPwd123")
        self.assertIsNotNone(storage.authenticate("admin", "StrongInitialPwd123"))

    def test_known_bad_quality_presets_are_upgraded_without_touching_manual_custom_values(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("manual", "ManualQualityPassword123", "user")

        legacy_2mbps = {
            "smooth": (450000, 720, 20),
            "balanced": (650000, 720, 24),
            "sharp": (1100000, 720, 24),
            "low_latency": (750000, 720, 30),
        }
        for profile, (bit_rate, max_size, max_fps) in legacy_2mbps.items():
            storage.set_setting(f"video_preset_{profile}_video_bit_rate", str(bit_rate))
            storage.set_setting(f"video_preset_{profile}_max_size", str(max_size))
            storage.set_setting(f"video_preset_{profile}_max_fps", str(max_fps))
        storage.set_setting("video_profile", "sharp")
        storage.set_setting("video_bit_rate", "1100000")
        storage.set_setting("max_size", "720")
        storage.set_setting("max_fps", "24")
        storage.set_user_video_preference(
            "admin",
            {"profile": "sharp", "adaptive": False, "video_bit_rate": 1100000, "max_size": 720, "max_fps": 24, "scrcpy_stream_mode": "raw"},
        )
        storage.set_user_video_preference(
            "manual",
            {"profile": "custom", "adaptive": False, "video_bit_rate": 1100000, "max_size": 720, "max_fps": 24, "scrcpy_stream_mode": "raw"},
        )
        with storage.db_connect() as conn:
            conn.execute("DELETE FROM settings WHERE key='video_quality_migration_version'")

        storage.init_db()

        settings = storage.get_settings()
        expected = {
            "smooth": (800000, 854, 20),
            "balanced": (1000000, 960, 24),
            "sharp": (1300000, 1280, 24),
            "low_latency": (1000000, 854, 30),
        }
        for profile, values in expected.items():
            actual = tuple(int(settings[f"video_preset_{profile}_{field}"]) for field in ("video_bit_rate", "max_size", "max_fps"))
            self.assertEqual(actual, values)
        self.assertEqual((int(settings["video_bit_rate"]), int(settings["max_size"]), int(settings["max_fps"])), expected["sharp"])
        self.assertEqual(storage.get_user_video_preference("admin")["max_size"], 1280)
        manual = storage.get_user_video_preference("manual")
        self.assertEqual((manual["profile"], manual["video_bit_rate"], manual["max_size"]), ("custom", 1100000, 854))
        self.assertEqual(settings["video_quality_migration_version"], storage.VIDEO_QUALITY_MIGRATION_VERSION)

    def test_quality_migration_requires_a_complete_known_matrix_and_never_downgrades_marker(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        custom_json = '{"manual":{"label":"Manual","video_bit_rate":1234567,"max_size":777,"max_fps":23}}'
        storage.set_setting("video_preset_smooth_video_bit_rate", "450000")
        storage.set_setting("video_preset_smooth_max_size", "720")
        storage.set_setting("video_preset_smooth_max_fps", "20")
        storage.set_setting("video_custom_profiles", custom_json)
        with storage.db_connect() as conn:
            conn.execute("DELETE FROM settings WHERE key='video_quality_migration_version'")

        storage.init_db()

        settings = storage.get_settings()
        self.assertEqual(
            tuple(int(settings[f"video_preset_smooth_{field}"]) for field in ("video_bit_rate", "max_size", "max_fps")),
            (450000, 854, 20),
        )
        self.assertEqual(json.loads(settings["video_custom_profiles"])["manual"]["max_size"], 854)

        legacy = {
            "smooth": (450000, 720, 20),
            "balanced": (650000, 720, 24),
            "sharp": (1100000, 720, 24),
            "low_latency": (750000, 720, 30),
        }
        for profile, values in legacy.items():
            for field, value in zip(("video_bit_rate", "max_size", "max_fps"), values):
                storage.set_setting(f"video_preset_{profile}_{field}", str(value))
        storage.set_setting("video_quality_migration_version", "4")

        storage.init_db()

        settings = storage.get_settings()
        self.assertEqual(settings["video_quality_migration_version"], "4")
        self.assertEqual(int(settings["video_preset_sharp_video_bit_rate"]), 1100000)

    def test_legacy_env_video_values_are_normalized_after_every_import(self):
        (self.tmp / ".env").write_text(
            "VIDEO_BIT_RATE=650000\nMAX_SIZE=720\nMAX_FPS=24\n",
            encoding="utf-8",
        )
        storage = load_storage(self.tmp)

        storage.init_db()
        storage.init_db()

        settings = storage.get_settings()
        self.assertEqual((settings["video_bit_rate"], settings["max_size"], settings["max_fps"]), ("650000", "854", "24"))
        self.assertEqual(settings["video_quality_migration_version"], storage.VIDEO_QUALITY_MIGRATION_VERSION)

    def test_invalid_text_video_sizes_are_repaired_idempotently(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AliceInvalidSize123", "user")
        storage.set_user_video_preference(
            "alice",
            {"profile": "balanced", "adaptive": False, "video_bit_rate": 2400000, "max_size": 1280, "max_fps": 24, "scrcpy_stream_mode": "raw"},
        )
        storage.set_setting("max_size", "not-a-number")
        storage.set_setting("video_preset_smooth_max_size", "not-a-number")
        storage.set_setting(
            "video_custom_profiles",
            '{"office":{"label":"Office","video_bit_rate":1800000,"max_size":"not-a-number","max_fps":24}}',
        )
        with storage.db_connect() as conn:
            conn.execute("UPDATE user_video_preferences SET max_size='not-a-number' WHERE username='alice'")
            conn.commit()

        storage.init_db()
        storage.init_db()

        settings = storage.get_settings()
        self.assertEqual(settings["max_size"], "1280")
        self.assertEqual(settings["video_preset_smooth_max_size"], "854")
        self.assertEqual(json.loads(settings["video_custom_profiles"])["office"]["max_size"], 1280)
        self.assertEqual(storage.get_user_video_preference("alice")["max_size"], 1280)

    def test_v2_known_matrix_with_invalid_text_sizes_does_not_abort_migration(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AliceLegacySize123", "user")
        legacy = {
            "smooth": (450000, 720, 20),
            "balanced": (650000, 720, 24),
            "sharp": (1100000, 720, 24),
            "low_latency": (750000, 720, 30),
        }
        for profile, values in legacy.items():
            for field, value in zip(("video_bit_rate", "max_size", "max_fps"), values):
                storage.set_setting(f"video_preset_{profile}_{field}", str(value))
        storage.set_setting("video_profile", "sharp")
        storage.set_setting("video_bit_rate", "1100000")
        storage.set_setting("max_size", "not-a-number")
        storage.set_setting("max_fps", "24")
        storage.set_setting("video_quality_migration_version", "2")
        storage.set_user_video_preference(
            "alice",
            {"profile": "sharp", "adaptive": False, "video_bit_rate": 1100000, "max_size": 1280, "max_fps": 24, "scrcpy_stream_mode": "raw"},
        )
        with storage.db_connect() as conn:
            conn.execute("UPDATE user_video_preferences SET max_size='not-a-number' WHERE username='alice'")
            conn.commit()

        storage.init_db()

        settings = storage.get_settings()
        self.assertEqual(settings["video_quality_migration_version"], "3")
        self.assertEqual(settings["max_size"], "1280")
        self.assertEqual(settings["video_preset_sharp_max_size"], "1280")
        self.assertEqual(storage.get_user_video_preference("alice")["max_size"], 1280)

    def test_legacy_alas_video_mode_and_profile_are_normalized(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AliceVideoMode123", "user", "alas")
        storage.set_user_video_preference(
            "alice",
            {"profile": "alas_sharp", "adaptive": False, "video_bit_rate": 4000000, "max_size": 1280, "max_fps": 30, "scrcpy_stream_mode": "raw"},
        )
        with storage.db_connect() as conn:
            conn.execute("UPDATE users SET video_mode='alas' WHERE username='alice'")
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('video_preset_alas_sharp_max_size','1280')")
            conn.commit()

        storage.init_db()

        self.assertEqual(storage.get_user("alice")["video_mode"], "normal")
        self.assertEqual(next(user for user in storage.list_users() if user["username"] == "alice")["video_mode"], "normal")
        preference = storage.get_user_video_preference("alice")
        self.assertEqual((preference["profile"], preference["max_size"]), ("sharp", 1920))
        self.assertEqual(storage.get_setting("video_preset_alas_sharp_max_size"), "1280")

    def test_legacy_plaintext_password_is_hashed_before_migration(self):
        (self.tmp / "users.json").write_text(json.dumps({
            "alice": {"password": "AlicePassword123", "role": "user", "created_at": "2026-07-05 00:00:00"}
        }), encoding="utf-8")
        storage = load_storage(self.tmp)
        storage.init_db()

        user = storage.get_user("alice")
        self.assertIsNotNone(user)
        self.assertNotEqual(user["password_hash"], "AlicePassword123")
        self.assertTrue(user["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertIsNotNone(storage.authenticate("alice", "AlicePassword123"))

    def test_legacy_password_hash_is_upgraded_after_successful_login(self):
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac("sha256", b"AdminPassword123", salt, 100000)
        old_hash = salt.hex() + ":" + key.hex()
        (self.tmp / "users.json").write_text(json.dumps({
            "admin": {"password_hash": old_hash, "role": "admin", "created_at": "2026-07-05 00:00:00"}
        }), encoding="utf-8")
        storage = load_storage(self.tmp)
        storage.init_db()

        self.assertIsNotNone(storage.authenticate("admin", "AdminPassword123"))
        upgraded = storage.get_user("admin")["password_hash"]
        self.assertTrue(upgraded.startswith("pbkdf2_sha256$310000$"))

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
        self.assertTrue(storage.lock_owned_by("dev1", "admin", "client-admin"))
        self.assertFalse(storage.release_lock("dev1", "admin", client_id="stale-client"))
        self.assertTrue(storage.lock_owned_by("dev1", "admin", "client-admin"))
        self.assertTrue(storage.release_lock("dev1", "admin", client_id="client-admin"))
        self.assertFalse(storage.lock_owned_by("dev1", "admin", "client-admin"))

    def test_device_upsert_preserves_permissions_and_created_at(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_device("stable-device", "Old name", "192.0.2.10:30100", True)
        storage.set_permission("alice", "stable-device", True, False)
        before = dict(storage.get_device("stable-device"))

        storage.upsert_device("stable-device", "New name", "192.0.2.20:30100", False)

        after = dict(storage.get_device("stable-device"))
        self.assertEqual(after["id"], "stable-device")
        self.assertEqual(after["address"], "192.0.2.20:30100")
        self.assertEqual(after["created_at"], before["created_at"])
        permission = next(item for item in storage.list_permissions() if item["username"] == "alice")
        self.assertTrue(permission["can_view"])
        self.assertFalse(storage.user_can("alice", "stable-device", "view"))
        self.assertFalse(storage.user_can("alice", "stable-device", "control"))

    def test_generated_device_ids_are_opaque_and_unique(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        first = storage.generate_device_id()
        storage.upsert_device(first, "Device", "192.0.2.10:30100", True)
        second = storage.generate_device_id()

        self.assertRegex(first, r"^device_[0-9a-f]{16}$")
        self.assertNotEqual(first, second)
        self.assertNotIn("192.0.2.10", first)

    def test_create_device_retries_collision_without_overwriting_existing_device(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        existing_id = f"device_{'a' * 16}"
        storage.upsert_device(existing_id, "Existing", "192.0.2.10:30100", True)

        with patch.object(storage.secrets, "token_hex", side_effect=["a" * 16, "b" * 16]):
            created_id = storage.create_device("Created", "192.0.2.20:30100", True)

        self.assertEqual(created_id, f"device_{'b' * 16}")
        self.assertEqual(storage.get_device(existing_id)["name"], "Existing")
        self.assertEqual(storage.get_device(created_id)["name"], "Created")

    def test_update_device_never_creates_missing_id(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        self.assertFalse(storage.update_device("chosen-by-client", "Device", "192.0.2.30:30100", True))
        self.assertIsNone(storage.get_device("chosen-by-client"))

    def test_delete_device_clears_control_lock_without_cached_session(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_device("dev-delete", "Device", "192.0.2.40:30100", True)
        storage.acquire_lock("dev-delete", "admin", "http")

        self.assertTrue(storage.delete_device("dev-delete"))
        self.assertIsNone(storage.get_device("dev-delete"))
        self.assertIsNone(storage.get_lock("dev-delete"))

    def test_control_lock_reacquire_requires_same_user_and_client(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        clock = [1000]
        storage.now_ts = lambda: clock[0]
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")

        first = storage.acquire_lock("dev1", "alice", "client-a", ttl_seconds=90)
        self.assertTrue(first["ok"])
        self.assertEqual(first["expires_at"], 1090)

        clock[0] = 1010
        repeated = storage.acquire_lock("dev1", "alice", "client-a", ttl_seconds=90)
        self.assertTrue(repeated["ok"])
        self.assertEqual(repeated["expires_at"], 1100)
        self.assertEqual(storage.get_lock("dev1")["expires_at"], 1100)

        self.assertFalse(storage.acquire_lock("dev1", "alice", "client-b")["ok"])
        self.assertFalse(storage.acquire_lock("dev1", "bob", "client-a")["ok"])
        self.assertTrue(storage.lock_owned_by("dev1", "alice", "client-a"))
        self.assertFalse(storage.lock_owned_by("dev1", "alice", "client-b"))
        self.assertFalse(storage.lock_owned_by("dev1", "bob", "client-a"))

    def test_http_control_lock_can_be_upgraded_only_once(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")

        self.assertTrue(storage.acquire_lock("dev1", "alice", "http")["ok"])
        self.assertTrue(storage.acquire_lock("dev1", "alice", "client-a")["ok"])
        self.assertEqual(storage.get_lock("dev1")["client_id"], "client-a")
        self.assertFalse(storage.acquire_lock("dev1", "alice", "client-b")["ok"])
        self.assertTrue(storage.acquire_lock("dev1", "alice", "client-a")["ok"])

    def test_concurrent_http_control_lock_upgrade_has_one_winner(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        self.assertTrue(storage.acquire_lock("dev1", "alice", "http")["ok"])

        barrier = threading.Barrier(3)
        results = {}

        def upgrade(client_id):
            barrier.wait()
            results[client_id] = storage.acquire_lock("dev1", "alice", client_id)["ok"]

        threads = [
            threading.Thread(target=upgrade, args=("client-a",)),
            threading.Thread(target=upgrade, args=("client-b",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        winners = [client_id for client_id, ok in results.items() if ok]
        self.assertEqual(len(winners), 1)
        self.assertEqual(storage.get_lock("dev1")["client_id"], winners[0])

    def test_control_lock_force_takeover_and_exact_release(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")

        self.assertTrue(storage.acquire_lock("dev1", "alice", "client-a")["ok"])
        self.assertTrue(storage.acquire_lock("dev1", "admin", "client-admin", force=True)["ok"])
        self.assertFalse(storage.release_lock("dev1", "alice", client_id="client-a"))
        self.assertFalse(storage.release_lock("dev1", "admin", client_id="stale-client"))
        self.assertFalse(storage.release_lock("dev1", "admin"))
        self.assertTrue(storage.lock_owned_by("dev1", "admin", "client-admin"))
        self.assertTrue(storage.release_lock("dev1", "admin", client_id="client-admin"))

    def test_control_lock_renew_requires_active_exact_owner(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        clock = [1000]
        storage.now_ts = lambda: clock[0]
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")

        self.assertFalse(storage.renew_lock("missing", "alice", "client-a"))
        self.assertTrue(storage.acquire_lock("dev1", "alice", "client-a", ttl_seconds=10)["ok"])
        clock[0] = 1005
        self.assertFalse(storage.renew_lock("dev1", "alice", "client-b", ttl_seconds=20))
        self.assertFalse(storage.renew_lock("dev1", "bob", "client-a", ttl_seconds=20))
        self.assertEqual(storage.get_lock("dev1")["expires_at"], 1010)

        self.assertTrue(storage.renew_lock("dev1", "alice", "client-a", ttl_seconds=20))
        self.assertEqual(storage.get_lock("dev1")["expires_at"], 1025)
        clock[0] = 1026
        self.assertFalse(storage.renew_lock("dev1", "alice", "client-a", ttl_seconds=20))
        self.assertIsNone(storage.get_lock("dev1"))

    def test_expired_account_cannot_recreate_or_renew_control_lock(self):
        storage = load_storage(self.tmp)
        with patch.object(storage, "now_ts", return_value=1000):
            storage.init_db()
            storage.upsert_user("timed", "TimedPassword123", "user", expires_at=1100)
            storage.upsert_device("timed-device", "Timed device", "192.0.2.20:30100", True)
            self.assertTrue(storage.acquire_lock("timed-device", "timed", "client-a")["ok"])

        with patch.object(storage, "now_ts", return_value=1100):
            self.assertFalse(storage.renew_lock("timed-device", "timed", "client-a"))
            self.assertIsNone(storage.get_lock("timed-device"))
            denied = storage.acquire_lock("timed-device", "timed", "client-b")

        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"], "account_expired")
        self.assertIsNone(storage.get_lock("timed-device"))

    def test_expired_control_lock_can_be_replaced(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        clock = [1000]
        storage.now_ts = lambda: clock[0]
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")

        self.assertTrue(storage.acquire_lock("dev1", "alice", "client-a", ttl_seconds=5)["ok"])
        clock[0] = 1006
        self.assertTrue(storage.acquire_lock("dev1", "bob", "client-b")["ok"])
        self.assertFalse(storage.release_lock("dev1", "alice", client_id="client-a"))
        self.assertTrue(storage.lock_owned_by("dev1", "bob", "client-b"))

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

    def test_user_alas_multi_bindings_keep_independent_permissions_and_default(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")

        storage.upsert_user_alas_binding("alice", "AliceMain", True, False)
        storage.upsert_user_alas_binding("alice", "AliceArchive", False, True)

        alice = storage.list_user_alas_bindings("alice")
        self.assertEqual([item["config_name"] for item in alice], ["AliceMain", "AliceArchive"])
        self.assertTrue(alice[0]["is_default"])
        self.assertTrue(alice[0]["can_run"])
        self.assertFalse(alice[0]["can_edit"])
        self.assertFalse(alice[1]["can_run"])
        self.assertTrue(alice[1]["can_edit"])

        storage.set_default_user_alas_config("alice", "AliceArchive")
        self.assertEqual(storage.get_user_alas_config("alice")["config_name"], "AliceArchive")
        storage.delete_user_alas_binding("alice", "AliceArchive")
        promoted = storage.get_user_alas_config("alice")
        self.assertEqual(promoted["config_name"], "AliceMain")
        self.assertTrue(promoted["is_default"])

    def test_alas_config_has_one_owner_and_can_be_reassigned_after_deletion(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")
        storage.upsert_user_alas_binding("alice", "Shared", True, False)
        storage.upsert_user_alas_binding("bob", "BobExisting", False, True)

        with self.assertRaises(storage.AlasConfigOwnershipError) as conflict:
            storage.upsert_user_alas_binding("bob", "Shared", False, True)
        self.assertEqual(conflict.exception.config_name, "Shared")
        self.assertEqual(conflict.exception.owner, "alice")
        self.assertIsNone(storage.get_user_alas_binding("bob", "Shared"))

        with self.assertRaises(storage.AlasConfigOwnershipError):
            storage.set_user_alas_config("bob", "Shared", True, True)
        self.assertEqual(storage.get_user_alas_config("bob")["config_name"], "BobExisting")

        storage.delete_user_alas_binding("alice", "Shared")
        storage.upsert_user_alas_binding("bob", "Shared", True, True)
        self.assertEqual(storage.get_user_alas_binding("bob", "Shared")["username"], "bob")

        storage.upsert_user_alas_binding("alice", "Foo", True, False)
        storage.upsert_user_alas_binding("bob", "foo", False, True)
        self.assertEqual(storage.get_user_alas_binding("alice", "Foo")["username"], "alice")
        self.assertEqual(storage.get_user_alas_binding("bob", "foo")["username"], "bob")

    def test_concurrent_alas_assignment_has_exactly_one_owner(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")
        barrier = threading.Barrier(3)
        results = {}

        def assign(username):
            barrier.wait()
            try:
                storage.upsert_user_alas_binding(username, "RaceConfig", True, False)
                results[username] = "assigned"
            except Exception as exc:  # capture the cross-thread result for assertions
                results[username] = exc

        threads = [
            threading.Thread(target=assign, args=("alice",)),
            threading.Thread(target=assign, args=("bob",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        winners = [username for username, result in results.items() if result == "assigned"]
        conflicts = [result for result in results.values() if isinstance(result, storage.AlasConfigOwnershipError)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].owner, winners[0])
        bindings = [item for item in storage.list_user_alas_bindings() if item["config_name"] == "RaceConfig"]
        self.assertEqual([item["username"] for item in bindings], winners)

    def test_user_alas_legacy_table_migrates_once_and_preserves_binding(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("dirty", "DirtyPassword123", "user")
        with sqlite3.connect(storage.DB_PATH) as conn:
            conn.execute("DROP TABLE user_alas_configs")
            conn.execute(
                """
                CREATE TABLE user_alas_configs (
                    username TEXT PRIMARY KEY,
                    config_name TEXT NOT NULL,
                    can_run INTEGER NOT NULL DEFAULT 1,
                    can_edit INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO user_alas_configs(username,config_name,can_run,can_edit,updated_at) VALUES(?,?,?,?,?)",
                ("admin", "LegacyMain", 0, 1, 12345),
            )
            conn.execute(
                "INSERT INTO user_alas_configs(username,config_name,can_run,can_edit,updated_at) VALUES(?,?,?,?,?)",
                ("dirty", "DirtyMain", 2, -3, 23456),
            )

        storage.init_db()
        storage.init_db()

        binding = storage.get_user_alas_config("admin")
        self.assertEqual(binding["config_name"], "LegacyMain")
        self.assertFalse(binding["can_run"])
        self.assertTrue(binding["can_edit"])
        self.assertTrue(binding["is_default"])
        self.assertEqual(binding["updated_at"], 12345)
        dirty = storage.get_user_alas_config("dirty")
        self.assertTrue(dirty["can_run"])
        self.assertTrue(dirty["can_edit"])
        self.assertTrue(dirty["is_default"])
        with sqlite3.connect(storage.DB_PATH) as conn:
            columns = conn.execute("PRAGMA table_info(user_alas_configs)").fetchall()
            primary_key = [row[1] for row in sorted((row for row in columns if row[5]), key=lambda row: row[5])]
            self.assertEqual(primary_key, ["username", "config_name"])
            self.assertIn("is_default", {row[1] for row in columns})
            self.assertIsNone(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='user_alas_configs_new'"
                ).fetchone()
            )

    def test_existing_duplicate_alas_owners_migrate_deterministically_and_repair_defaults(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("alice", "AlicePassword123", "user")
        storage.upsert_user("bob", "BobPassword1234", "user")
        with sqlite3.connect(storage.DB_PATH) as conn:
            conn.execute("DROP INDEX ux_user_alas_configs_config_owner")
            conn.executemany(
                """
                INSERT INTO user_alas_configs(username,config_name,can_run,can_edit,is_default,updated_at)
                VALUES(?,?,?,?,?,?)
                """,
                [
                    ("alice", "SharedLegacy", 1, 0, 1, 100),
                    ("alice", "AliceOther", 0, 1, 0, 400),
                    ("alice", "TieConfig", 1, 0, 0, 300),
                    ("bob", "SharedLegacy", 0, 1, 1, 200),
                    ("bob", "BobOther", 1, 0, 0, 500),
                    ("bob", "TieConfig", 0, 1, 0, 300),
                ],
            )

        storage.init_db()
        storage.init_db()

        self.assertIsNone(storage.get_user_alas_binding("alice", "SharedLegacy"))
        self.assertEqual(storage.get_user_alas_binding("bob", "SharedLegacy")["username"], "bob")
        self.assertEqual(storage.get_user_alas_binding("alice", "TieConfig")["username"], "alice")
        self.assertIsNone(storage.get_user_alas_binding("bob", "TieConfig"))
        self.assertEqual(storage.get_user_alas_config("alice")["config_name"], "AliceOther")
        self.assertEqual(storage.get_user_alas_config("bob")["config_name"], "SharedLegacy")
        with sqlite3.connect(storage.DB_PATH) as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(user_alas_configs)")}
        self.assertIn("ux_user_alas_configs_config_owner", indexes)

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

    def test_user_expiration_migration_keeps_existing_accounts_permanent(self):
        db_path = self.tmp / "webscrcpy.db"
        self.tmp.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    video_mode TEXT NOT NULL DEFAULT 'normal'
                )
                """
            )
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES('admin','legacy','admin','2026-01-01 00:00:00')"
            )
            conn.commit()

        storage = load_storage(self.tmp)
        storage.init_db()

        self.assertIsNone(storage.get_user("admin")["expires_at"])
        listed = next(user for user in storage.list_users() if user["username"] == "admin")
        self.assertEqual(listed["expiration_state"], "permanent")
        self.assertTrue(listed["is_active"])
        with sqlite3.connect(storage.DB_PATH) as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(users)")}
        self.assertIn("idx_users_expires_at", indexes)

    def test_user_expiration_payload_has_stable_boundary_states(self):
        storage = load_storage(self.tmp)

        permanent = storage.user_expiration_payload({"expires_at": None}, now=1000)
        expired = storage.user_expiration_payload({"expires_at": 1000}, now=1000)
        expiring = storage.user_expiration_payload({"expires_at": 1001}, now=1000)
        active = storage.user_expiration_payload(
            {"expires_at": 1000 + storage.ACCOUNT_EXPIRING_WINDOW_SECONDS + 1},
            now=1000,
        )

        self.assertEqual(permanent, {
            "expires_at": None,
            "expiration_state": "permanent",
            "remaining_seconds": None,
            "is_active": True,
        })
        self.assertEqual(expired["expiration_state"], "expired")
        self.assertEqual(expired["remaining_seconds"], 0)
        self.assertFalse(expired["is_active"])
        self.assertEqual(expiring["expiration_state"], "expiring")
        self.assertEqual(active["expiration_state"], "active")

    def test_account_expiration_rejects_non_finite_and_fractional_values(self):
        storage = load_storage(self.tmp)

        for value in (float("inf"), float("-inf"), float("nan"), 1234.5, "1234.5"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_expires_at"):
                storage.normalize_expires_at(value)

    def test_user_upsert_distinguishes_omitted_expiration_from_explicit_clear(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        future = storage.now_ts() + 86400
        storage.upsert_user("alice", "AlicePassword123", "user", expires_at=future)

        storage.upsert_user("alice", None, "user")
        self.assertEqual(storage.get_user("alice")["expires_at"], future)

        storage.upsert_user("alice", None, "user", expires_at=None)
        self.assertIsNone(storage.get_user("alice")["expires_at"])

    def test_expired_account_is_denied_auth_session_and_device_access(self):
        storage = load_storage(self.tmp)
        with patch.object(storage, "now_ts", return_value=1000):
            storage.init_db()
            storage.upsert_user("alice", "AlicePassword123", "user", expires_at=1100)
            storage.upsert_device("dev1", "Device", "192.0.2.10:30100", True)
            storage.set_permission("alice", "dev1", True, True)
            session = storage.create_session("alice")

        with patch.object(storage, "now_ts", return_value=1100):
            self.assertIsNone(storage.authenticate("alice", "AlicePassword123"))
            self.assertFalse(storage.user_can("alice", "dev1", "view"))
            self.assertFalse(storage.user_can("alice", "dev1", "control"))
            self.assertIsNone(storage.get_session(session["sid"]))
            with self.assertRaisesRegex(ValueError, "account_expired_or_missing"):
                storage.create_session("alice")

        self.assertIsNotNone(storage.get_user("alice"))

    def test_expiration_and_recovery_revoke_sessions_and_control_locks(self):
        storage = load_storage(self.tmp)
        with patch.object(storage, "now_ts", return_value=1000):
            storage.init_db()
            storage.upsert_user("alice", "AlicePassword123", "user", expires_at=1100)
            storage.upsert_device("dev1", "Device", "192.0.2.10:30100", True)
            session = storage.create_session("alice")
            storage.acquire_lock("dev1", "alice", "http")

        with patch.object(storage, "now_ts", return_value=1200):
            updated = storage.set_user_expiration("alice", 2000)

        self.assertEqual(updated["expires_at"], 2000)
        self.assertIsNone(storage.get_session(session["sid"]))
        self.assertIsNone(storage.get_lock("dev1"))
        self.assertIsNotNone(storage.get_user("alice"))

    def test_extend_user_expiration_uses_current_deadline_or_now(self):
        storage = load_storage(self.tmp)
        with patch.object(storage, "now_ts", return_value=1000):
            storage.init_db()
            storage.upsert_user("alice", "AlicePassword123", "user", expires_at=2000)
            active = storage.extend_user_expiration("alice", 1)
        self.assertEqual(active["expires_at"], 2000 + 86400)

        with patch.object(storage, "now_ts", return_value=active["expires_at"] + 100):
            expired = storage.extend_user_expiration("alice", 1)
        self.assertEqual(expired["expires_at"], active["expires_at"] + 100 + 86400)

        storage.upsert_user("bob", "BobPassword1234", "user")
        with patch.object(storage, "now_ts", return_value=5000):
            limited = storage.extend_user_expiration("bob", 7)
        self.assertEqual(limited["expires_at"], 5000 + 7 * 86400)

    def test_revoke_expired_access_keeps_users_but_removes_credentials(self):
        storage = load_storage(self.tmp)
        with patch.object(storage, "now_ts", return_value=1000):
            storage.init_db()
            storage.upsert_user("alice", "AlicePassword123", "user", expires_at=1100)
            storage.upsert_user("bob", "BobPassword1234", "user", expires_at=2000)
            storage.upsert_device("dev1", "Device", "192.0.2.10:30100", True)
            alice_session = storage.create_session("alice")
            bob_session = storage.create_session("bob")
            storage.acquire_lock("dev1", "alice", "http")

        expired = storage.revoke_expired_access(now=1100)

        self.assertEqual(expired, {"alice"})
        with patch.object(storage, "now_ts", return_value=1100):
            self.assertIsNone(storage.get_session(alice_session["sid"]))
            self.assertIsNotNone(storage.get_session(bob_session["sid"]))
        self.assertIsNone(storage.get_lock("dev1"))
        self.assertIsNotNone(storage.get_user("alice"))

    def test_last_permanent_admin_cannot_be_limited(self):
        storage = load_storage(self.tmp)
        storage.init_db()

        with self.assertRaisesRegex(ValueError, "last_permanent_admin_required"):
            storage.set_user_expiration("admin", storage.now_ts() + 86400)

        storage.upsert_user("temporary-admin", "TemporaryAdmin123", "admin", expires_at=storage.now_ts() + 86400)
        with self.assertRaisesRegex(ValueError, "last_permanent_admin_required"):
            storage.set_user_expiration("admin", storage.now_ts() + 86400)

        storage.upsert_user("backup", "BackupPassword123", "admin")
        storage.set_user_expiration("admin", storage.now_ts() + 86400)
        self.assertIsNotNone(storage.get_user("admin")["expires_at"])
        self.assertEqual(storage.permanent_admin_count(), 1)

    def test_concurrent_admin_deletes_keep_one_permanent_admin(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user("backup", "BackupPassword123", "admin")
        barrier = threading.Barrier(3)
        errors = []

        def remove(username):
            barrier.wait()
            try:
                storage.delete_user(username)
            except ValueError as exc:
                errors.append(str(exc))

        threads = [
            threading.Thread(target=remove, args=("admin",)),
            threading.Thread(target=remove, args=("backup",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(storage.admin_count(), 1)
        self.assertEqual(storage.permanent_admin_count(), 1)
        self.assertEqual(errors, ["last_admin_required"])

    def test_expiring_admin_does_not_allow_last_permanent_admin_removal_or_demotion(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        storage.upsert_user(
            "temporary-admin",
            "TemporaryAdmin123",
            "admin",
            expires_at=storage.now_ts() + 86400,
        )

        with self.assertRaisesRegex(ValueError, "last_permanent_admin_required"):
            storage.upsert_user("admin", None, "user")
        with self.assertRaisesRegex(ValueError, "last_permanent_admin_required"):
            storage.delete_user("admin")

        storage.upsert_user("backup", "BackupPassword123", "admin")
        storage.upsert_user("admin", None, "user")
        self.assertEqual(storage.get_user("admin")["role"], "user")

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

    def test_upsert_password_revokes_existing_sessions(self):
        storage = load_storage(self.tmp)
        storage.init_db()
        session = storage.create_session("admin")

        storage.upsert_user("admin", "ReplacementAdminPwd123", "admin")

        self.assertIsNone(storage.get_session(session["sid"]))
        self.assertIsNotNone(storage.authenticate("admin", "ReplacementAdminPwd123"))

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
