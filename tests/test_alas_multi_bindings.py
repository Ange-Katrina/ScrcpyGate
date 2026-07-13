import importlib
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.responses import JSONResponse
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


class AlasMultiBindingRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-alas-multi-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules(["app.main", "app.storage", "app.alas", "app.alas_embed", "app.security"])
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.main = importlib.import_module("app.main")
        self.current_user = None
        self.current_session = None
        self.main.security.get_current_user = lambda request: self.current_user
        self.main.security.get_current_session = lambda request: self.current_session
        self.main.security.allowed_hosts = lambda: {"testserver", "localhost"}
        self.client = TestClient(self.main.app)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
        os.environ.pop("SESSION_COOKIE_SECURE", None)

    def login(self, username: str, role: str = "user"):
        self.storage.upsert_user(username, f"{username.title()}Password123", role)
        self.current_user = dict(self.storage.get_user(username))
        self.current_session = self.storage.create_session(username)
        return {"X-CSRF-Token": self.current_session["csrf_token"]}

    def bind(self, username: str, config: str, can_run=True, can_edit=False, is_default=None):
        self.storage.upsert_user_alas_binding(username, config, can_run, can_edit, is_default)

    def test_user_config_list_and_status_only_expose_authorized_bindings(self):
        self.login("alice")
        self.bind("alice", "AliceMain", True, False)
        self.bind("alice", "AliceArchive", False, True)
        calls = []

        def status_for_config(config_name, include_configs=False):
            calls.append((config_name, include_configs))
            return {"ok": True, "configured": True, "status": "idle", "config": config_name, "configs": ["HiddenRuntime"]}

        self.main.alas.status_for_config = status_for_config

        configs = self.client.get("/api/alas/configs")
        default_status = self.client.get("/api/alas/status")
        archive_status = self.client.get("/api/alas/status?config=AliceArchive")
        denied = self.client.get("/api/alas/status?config=HiddenRuntime")

        self.assertEqual(configs.status_code, 200)
        self.assertEqual(configs.json()["default_config"], "AliceMain")
        self.assertEqual(
            {item["config_name"] for item in configs.json()["configs"]},
            {"AliceMain", "AliceArchive"},
        )
        self.assertNotIn("HiddenRuntime", configs.text)
        self.assertEqual(default_status.json()["config"], "AliceMain")
        self.assertTrue(default_status.json()["can_run"])
        self.assertEqual(archive_status.json()["config"], "AliceArchive")
        self.assertFalse(archive_status.json()["can_run"])
        self.assertTrue(archive_status.json()["can_edit"])
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn(("HiddenRuntime", False), calls)

    def test_toggle_uses_selected_binding_permissions(self):
        headers = self.login("alice")
        self.bind("alice", "AliceMain", True, False)
        self.bind("alice", "AliceReadOnly", False, False)
        calls = []

        def control_for_config(action, config_name):
            calls.append((action, config_name))
            return {"ok": True, "action": "restart", "config": config_name, "alas": {"status": "running", "config": config_name}}

        self.main.alas.control_for_config = control_for_config

        allowed = self.client.post(
            "/api/alas/toggle",
            headers=headers,
            json={"config_name": "AliceMain"},
        )
        denied = self.client.post(
            "/api/alas/toggle",
            headers=headers,
            json={"config_name": "AliceReadOnly"},
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["config"], "AliceMain")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(calls, [("toggle", "AliceMain")])

    def test_embed_and_proxy_can_select_authorized_non_default_config(self):
        self.login("alice")
        self.bind("alice", "AliceMain", True, False)
        self.bind("alice", "AliceArchive", True, True)
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        async def fake_proxy(request, base_url, path, decision, body=b""):
            return JSONResponse({"config": decision.config_name, "can_edit": decision.can_edit})

        self.main.alas_embed.proxy_http_request = fake_proxy

        embed = self.client.get("/alas/embed/?config=AliceArchive")
        proxy = self.client.get("/alas/embed/proxy/api/state?config=AliceArchive")
        uppercase_proxy = self.client.get("/alas/embed/proxy/api/state?Config=AliceArchive")
        conflicting_proxy = self.client.get(
            "/alas/embed/proxy/api/state?config=AliceArchive&CONFIG_NAME=AliceMain"
        )
        denied = self.client.get("/alas/embed/proxy/api/state?config=Other")

        self.assertEqual(embed.status_code, 200)
        self.assertIn("config=AliceArchive", embed.text)
        self.assertEqual(proxy.status_code, 200)
        self.assertEqual(proxy.json(), {"config": "AliceArchive", "can_edit": True})
        self.assertEqual(uppercase_proxy.status_code, 200)
        self.assertEqual(uppercase_proxy.json(), {"config": "AliceArchive", "can_edit": True})
        self.assertEqual(conflicting_proxy.status_code, 403)
        self.assertEqual(denied.status_code, 403)

    def test_websocket_handshake_is_pinned_to_selected_authorized_config(self):
        self.login("alice")
        self.bind("alice", "AliceMain", True, False)
        self.bind("alice", "AliceArchive", True, True)
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.main.security.websocket_origin_allowed = lambda websocket: True
        captured = {}

        async def fake_websocket(websocket, base_url, path, decision, **kwargs):
            captured["config"] = decision.config_name
            captured["can_edit"] = decision.can_edit
            captured["authorization_check"] = kwargs.get("authorization_check")
            await websocket.accept()
            await websocket.send_json({"config": decision.config_name})
            await websocket.close()

        self.main.alas_embed.proxy_websocket = fake_websocket

        with self.client.websocket_connect("/alas/embed/proxy/ws?CONFIG_NAME=AliceArchive") as websocket:
            self.assertEqual(websocket.receive_json(), {"config": "AliceArchive"})
        self.assertEqual(captured["config"], "AliceArchive")
        self.assertTrue(captured["can_edit"])
        current = asyncio.run(captured["authorization_check"]())
        self.assertEqual(current["config_name"], "AliceArchive")
        with self.assertRaises(WebSocketDisconnect) as conflicting:
            with self.client.websocket_connect(
                "/alas/embed/proxy/ws?config=AliceArchive&Config=AliceMain"
            ) as websocket:
                websocket.receive_text()
        self.assertEqual(conflicting.exception.code, 1008)
        with self.assertRaises(WebSocketDisconnect) as denied:
            with self.client.websocket_connect("/alas/embed/proxy/ws?config=Other") as websocket:
                websocket.receive_text()
        self.assertEqual(denied.exception.code, 1008)

    def test_admin_can_add_update_default_and_delete_one_assignment(self):
        headers = self.login("admin", "admin")
        self.storage.upsert_user("alice", "AlicePassword123", "user")

        first = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={"username": "alice", "config_name": "AliceMain", "enabled": True, "can_run": True, "can_edit": False},
        )
        second = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={"username": "alice", "config_name": "AliceArchive", "enabled": True, "can_run": False, "can_edit": True, "is_default": True},
        )
        deleted = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={
                "username": "alice",
                "config_name": "AliceMain",
                "enabled": False,
                "is_default": False,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        assignments = [item for item in second.json()["assignments"] if item["username"] == "alice"]
        self.assertEqual(len(assignments), 2)
        self.assertEqual(next(item for item in assignments if item["is_default"])["config_name"], "AliceArchive")
        self.assertEqual(deleted.status_code, 200)
        remaining = [item for item in deleted.json()["assignments"] if item["username"] == "alice"]
        self.assertEqual([item["config_name"] for item in remaining], ["AliceArchive"])

    def test_admin_legacy_permission_update_replaces_single_binding(self):
        headers = self.login("admin", "admin")
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.bind("alice", "AliceMain", True, False)

        response = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={
                "username": "alice",
                "config_name": "AliceReplacement",
                "enabled": True,
                "can_run": False,
                "can_edit": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        assignments = [item for item in response.json()["assignments"] if item["username"] == "alice"]
        self.assertEqual([item["config_name"] for item in assignments], ["AliceReplacement"])
        self.assertTrue(assignments[0]["is_default"])
        self.assertFalse(assignments[0]["can_run"])
        self.assertTrue(assignments[0]["can_edit"])

    def test_admin_runtime_catalog_merges_unassigned_configs(self):
        self.login("admin", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.storage.upsert_user_alas_binding("admin", "BoundConfig", True, True)
        self.main.alas.status_for_config = lambda config_name, include_configs=False: {
            "ok": True,
            "config": config_name,
            "configs": ["BoundConfig", "UnassignedConfig"],
        }

        response = self.client.get("/api/admin/alas/configs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime_configs"], ["BoundConfig", "UnassignedConfig"])
        self.assertEqual(response.json()["configs"], ["BoundConfig", "UnassignedConfig"])

    def test_case_distinct_configs_survive_admin_catalog_and_authorization(self):
        self.login("alice")
        self.bind("alice", "Foo", True, False, True)
        self.bind("alice", "foo", False, True, False)
        calls = []

        def status_for_config(config_name, include_configs=False):
            calls.append((config_name, include_configs))
            return {"ok": True, "configured": True, "status": "idle", "config": config_name}

        self.main.alas.status_for_config = status_for_config

        upper = self.client.get("/api/alas/status?config=Foo")
        lower = self.client.get("/api/alas/status?config=foo")
        wrong_case = self.client.get("/api/alas/status?config=FOO")

        self.assertEqual(upper.status_code, 200)
        self.assertEqual(upper.json()["config"], "Foo")
        self.assertTrue(upper.json()["can_run"])
        self.assertFalse(upper.json()["can_edit"])
        self.assertEqual(lower.status_code, 200)
        self.assertEqual(lower.json()["config"], "foo")
        self.assertFalse(lower.json()["can_run"])
        self.assertTrue(lower.json()["can_edit"])
        self.assertEqual(wrong_case.status_code, 403)
        self.assertEqual(calls, [("Foo", False), ("foo", False)])

        self.login("admin", "admin")
        catalog = self.client.get("/api/admin/alas/configs")

        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["bound_configs"], ["Foo", "foo"])
        self.assertEqual(catalog.json()["configs"], ["Foo", "foo"])


if __name__ == "__main__":
    unittest.main()
