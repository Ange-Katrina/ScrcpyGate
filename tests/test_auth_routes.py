import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        reset_app_modules(["app.main", "app.mirror", "app.storage", "app.security"])
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
        reset_app_modules(["app.main", "app.mirror", "app.storage", "app.security"])

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
