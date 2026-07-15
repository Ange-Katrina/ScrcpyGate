import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def reset_app_modules(names):
    package = sys.modules.get("app")
    for name in names:
        sys.modules.pop(name, None)
        if package is not None and name.startswith("app."):
            attr = name.rsplit(".", 1)[1]
            if hasattr(package, attr):
                delattr(package, attr)


class AuthRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-auth-routes-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        os.environ["LOGIN_RATE_LIMIT_MAX"] = "2"
        os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "60"
        os.environ["LOGIN_LOCKOUT_SECONDS"] = "30"
        os.environ.pop("ALLOW_NULL_ORIGIN", None)
        reset_app_modules(["app.main", "app.mirror", "app.devices", "app.storage", "app.security"])
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.storage.upsert_user("admin", "AdminPassword123", "admin")
        self.main = importlib.import_module("app.main")
        self.client = TestClient(self.main.app)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key in (
            "WEB_SCRCPY_DATA_DIR",
            "ALLOWED_HOSTS",
            "SESSION_COOKIE_SECURE",
            "LOGIN_RATE_LIMIT_MAX",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
            "LOGIN_LOCKOUT_SECONDS",
            "ENABLE_API_DOCS",
            "ALLOW_NULL_ORIGIN",
        ):
            os.environ.pop(key, None)
        reset_app_modules(["app.main", "app.mirror", "app.devices", "app.storage", "app.security"])

    def test_login_route_rate_limits_repeated_failures(self):
        payload = {"username": "admin", "password": "wrong-password"}

        self.assertEqual(self.client.post("/login", data=payload).status_code, 401)
        self.assertEqual(self.client.post("/login", data=payload).status_code, 401)
        limited = self.client.post("/login", data=payload)
        self.assertEqual(limited.status_code, 429)
        self.assertGreater(int(limited.headers.get("retry-after", "0")), 0)

        rows = self.storage.recent_audit(20)
        self.assertIn("login_rate_limited", [row["action"] for row in rows])

    def test_failed_login_uses_chinese_error_and_preserves_only_username(self):
        response = self.client.post(
            "/login",
            data={"username": "remember-me", "password": "never-render-this-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("用户名或密码错误。", response.text)
        self.assertIn('value="remember-me"', response.text)
        self.assertNotIn("never-render-this-password", response.text)
        self.assertIn('id="loginError"', response.text)
        self.assertIn('role="alert"', response.text)

    def test_login_rejects_null_origin_by_default(self):
        response = self.client.post(
            "/login",
            headers={"origin": "null"},
            data={"username": "admin", "password": "AdminPassword123"},
        )

        self.assertEqual(response.status_code, 403)

    def test_api_docs_are_disabled_by_default(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_unauthenticated_pages_and_get_apis_are_blocked(self):
        checks = {
            "/": {302},
            "/admin": {302},
            "/alas/embed": {302},
            "/alas/embed/": {302},
            "/alas/embed/proxy/": {401},
            "/api/me": {401},
            "/api/devices": {401},
            "/api/video/preferences": {401},
            "/api/alas/status": {401},
            "/api/alas/configs": {401},
            "/api/admin/overview": {401},
            "/api/admin/users": {401},
            "/api/admin/devices": {401},
            "/api/admin/adb/status": {401},
            "/api/admin/permissions": {401},
            "/api/admin/video": {401},
            "/api/admin/alas": {401},
            "/api/admin/alas/config": {401},
            "/api/admin/alas/configs": {401},
            "/api/admin/alas/permissions": {401},
            "/api/admin/logs": {401},
            "/api/admin/runtime-logs": {401},
        }
        for path, expected in checks.items():
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertIn(response.status_code, expected)

    def test_unauthenticated_mutating_apis_are_blocked(self):
        checks = [
            ("post", "/logout"),
            ("put", "/api/video/preferences"),
            ("put", "/api/account/password"),
            ("post", "/api/devices/dev_1/mirror/start"),
            ("put", "/api/devices/dev_1/mirror/settings"),
            ("post", "/api/devices/dev_1/mirror/stop"),
            ("post", "/api/devices/dev_1/mirror/idle-stop"),
            ("post", "/api/devices/dev_1/control/acquire"),
            ("post", "/api/devices/dev_1/control/release"),
            ("post", "/api/alas/toggle"),
            ("post", "/alas/embed/proxy/api/state"),
            ("put", "/api/admin/users"),
            ("delete", "/api/admin/users/alice"),
            ("put", "/api/admin/devices"),
            ("post", "/api/admin/devices/dev_1/adb/test"),
            ("post", "/api/admin/devices/dev_1/adb/reconnect"),
            ("delete", "/api/admin/devices/dev_1"),
            ("put", "/api/admin/permissions"),
            ("put", "/api/admin/video"),
            ("put", "/api/admin/alas"),
            ("post", "/api/admin/alas/toggle"),
            ("put", "/api/admin/alas/config"),
            ("put", "/api/admin/alas/permissions"),
        ]
        for method, path in checks:
            with self.subTest(method=method, path=path):
                response = self.client.request(method.upper(), path, json={})
                self.assertGreaterEqual(response.status_code, 400)

    def test_unauthenticated_websockets_are_blocked(self):
        checks = [
            ("/ws/events", 4401),
            ("/ws/devices/dev_1/video", 4401),
            ("/ws/devices/dev_1/control", 4401),
            ("/alas/embed/proxy/ws", 1008),
        ]
        for path, expected_code in checks:
            with self.subTest(path=path):
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with self.client.websocket_connect(path):
                        pass
                self.assertEqual(ctx.exception.code, expected_code)

    def test_normal_user_cannot_open_admin_interfaces(self):
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        session = self.storage.create_session("alice")
        self.client.cookies.set("wsid", session["sid"])

        self.assertEqual(self.client.get("/admin", follow_redirects=False).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/devices").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/logs").status_code, 403)

        response = self.client.put(
            "/api/admin/users",
            headers={"x-csrf-token": session["csrf_token"]},
            json={"username": "bob", "password": "BobPassword123", "role": "user"},
        )
        self.assertEqual(response.status_code, 403)

    def test_all_users_receive_the_same_four_profiles_and_legacy_mode_is_ignored(self):
        admin_session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", admin_session["sid"])
        created = self.client.put(
            "/api/admin/users",
            headers={"x-csrf-token": admin_session["csrf_token"]},
            json={"username": "alice", "password": "AlicePassword123", "role": "user", "video_mode": "alas"},
        )

        self.assertEqual(created.status_code, 200)
        alice_row = next(user for user in created.json()["users"] if user["username"] == "alice")
        self.assertEqual(alice_row["video_mode"], "normal")

        alice_session = self.storage.create_session("alice")
        self.client.cookies.clear()
        self.client.cookies.set("wsid", alice_session["sid"])
        preferences_response = self.client.get("/api/video/preferences")
        self.assertEqual(preferences_response.status_code, 200)
        data = preferences_response.json()
        self.assertEqual(data["video_mode"], "normal")
        self.assertEqual(set(data["profiles"]), {"smooth", "balanced", "sharp", "low_latency"})
        self.assertEqual(data["profiles"]["smooth"]["max_size"], 854)
        self.assertEqual(data["profiles"]["sharp"]["max_size"], 1920)

        selected = self.client.put(
            "/api/video/preferences",
            headers={"x-csrf-token": alice_session["csrf_token"]},
            json={"profile": "alas_sharp"},
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["effective"]["profile"], "sharp")
        self.assertEqual(selected.json()["effective"]["max_size"], 1920)

        self.storage.set_setting(
            "video_custom_profiles",
            '{"office":{"label":"Office","video_bit_rate":1800000,"max_size":960,"max_fps":24}}',
        )
        normal_data = self.client.get("/api/video/preferences").json()
        self.assertEqual(normal_data["video_mode"], "normal")
        self.assertIn("office", normal_data["profiles"])
        self.assertFalse(any(name.startswith("alas_") for name in normal_data["profiles"]))

    def test_admin_user_route_ignores_obsolete_video_mode_values(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        response = self.client.put(
            "/api/admin/users",
            headers={"x-csrf-token": session["csrf_token"]},
            json={"username": "alice", "password": "AlicePassword123", "role": "user", "video_mode": "invalid"},
        )

        self.assertEqual(response.status_code, 200)
        alice = next(user for user in response.json()["users"] if user["username"] == "alice")
        self.assertEqual(alice["video_mode"], "normal")

    def test_admin_video_rejects_output_sizes_outside_480p_to_1080p(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        response = self.client.put(
            "/api/admin/video",
            headers={"x-csrf-token": session["csrf_token"]},
            json={
                "presets": {
                    "sharp": {
                        "video_bit_rate": 6000000,
                        "max_size": 2560,
                        "max_fps": 30,
                    }
                }
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("max_size", response.json()["detail"])

    def test_mirror_settings_restarts_only_when_effective_quality_changes(self):
        self.storage.upsert_device("dev_1", "Device", "192.0.2.10:5555")
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])
        current = self.main.user_video_options("admin", {"profile": "balanced"})
        snapshot = {"dev_1": {"device_id": "dev_1", "running": True, "video": current}}

        with (
            patch.object(self.main.manager, "snapshot", new=AsyncMock(return_value=snapshot)),
            patch.object(self.main.manager, "start", new=AsyncMock(return_value=True)) as start,
        ):
            unchanged = self.client.put(
                "/api/devices/dev_1/mirror/settings",
                headers={"x-csrf-token": session["csrf_token"]},
                json=current,
            )

        self.assertEqual(unchanged.status_code, 200)
        self.assertFalse(unchanged.json()["restarted"])
        start.assert_awaited_once_with("dev_1", current, force_restart=False)

        changed = self.main.user_video_options("admin", {"profile": "smooth"})
        with (
            patch.object(self.main.manager, "snapshot", new=AsyncMock(return_value=snapshot)),
            patch.object(self.main.manager, "start", new=AsyncMock(return_value=True)) as start,
        ):
            restarted = self.client.put(
                "/api/devices/dev_1/mirror/settings",
                headers={"x-csrf-token": session["csrf_token"]},
                json=changed,
            )

        self.assertEqual(restarted.status_code, 200)
        self.assertTrue(restarted.json()["restarted"])
        start.assert_awaited_once_with("dev_1", changed, force_restart=True)

    def test_admin_device_save_returns_fresh_adb_status(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        async def record_online(device_id):
            return self.main.adb_monitor._set_status(
                device_id,
                "online",
                ok=True,
                detail="device",
                address="192.0.2.10:30100",
            )

        try:
            with patch.object(self.main.adb_monitor, "reconnect_device", side_effect=record_online) as reconnect:
                response = self.client.put(
                    "/api/admin/devices",
                    headers={"x-csrf-token": session["csrf_token"]},
                    json={
                        "device_id": "new-device",
                        "name": "New device",
                        "address": "192.0.2.10:30100",
                        "enabled": True,
                    },
                )

            self.assertEqual(response.status_code, 200)
            reconnect.assert_awaited_once_with("new-device")
            saved = next(item for item in response.json()["devices"] if item["id"] == "new-device")
            self.assertEqual(saved["adb_state"], "online")
            self.assertTrue(saved["adb_ok"])
        finally:
            self.main.adb_monitor._statuses.pop("new-device", None)

    def test_normal_user_cannot_open_unassigned_device_websockets(self):
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.storage.upsert_device("dev_1", "Device 1", "127.0.0.1:5555", True)
        session = self.storage.create_session("alice")
        self.client.cookies.set("wsid", session["sid"])

        checks = [
            "/ws/devices/dev_1/video",
            "/ws/devices/dev_1/control",
        ]
        for path in checks:
            with self.subTest(path=path):
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with self.client.websocket_connect(path):
                        pass
                self.assertEqual(ctx.exception.code, 4403)

    def test_logout_requires_valid_session_csrf_and_clears_session(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        blocked = self.client.post("/logout", data={})
        self.assertEqual(blocked.status_code, 400)
        self.assertIsNotNone(self.storage.get_session(session["sid"]))

        response = self.client.post("/logout", data={"csrf_token": session["csrf_token"]}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("location", ""))
        self.assertIsNone(self.storage.get_session(session["sid"]))

    def test_logout_allows_null_origin_when_csrf_is_valid(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        response = self.client.post(
            "/logout",
            headers={"origin": "null"},
            data={"csrf_token": session["csrf_token"]},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("location", ""))
        self.assertIsNone(self.storage.get_session(session["sid"]))

    def test_logout_rejects_null_origin_when_csrf_is_invalid(self):
        session = self.storage.create_session("admin")
        self.client.cookies.set("wsid", session["sid"])

        response = self.client.post(
            "/logout",
            headers={"origin": "null"},
            data={"csrf_token": "wrong-token"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(self.storage.get_session(session["sid"]))


if __name__ == "__main__":
    unittest.main()
