import importlib
import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
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
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-alas-multi-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules([
            "app.main",
            "app.storage",
            "app.alas",
            "app.alas_embed",
            "app.security",
            "app.account_access",
        ])
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
        self.client.close()
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

    def test_admin_rejects_existing_owner_without_transfer_and_allows_reassignment(self):
        headers = self.login("admin", "admin")
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.storage.upsert_user("bob", "BobPassword1234", "user")
        self.bind("alice", "Exclusive", True, False)
        self.bind("bob", "BobExisting", False, True)

        conflict = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={
                "username": "bob",
                "config_name": "Exclusive",
                "enabled": True,
                "can_run": True,
                "can_edit": True,
                "is_default": False,
            },
        )
        legacy_conflict = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={
                "username": "bob",
                "config_name": "Exclusive",
                "enabled": True,
                "can_run": True,
                "can_edit": True,
            },
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(legacy_conflict.status_code, 409)
        self.assertEqual(
            conflict.json()["detail"],
            "配置“Exclusive”已归属用户“alice”，请先移除原归属再分配。",
        )
        self.assertEqual(self.storage.get_user_alas_binding("alice", "Exclusive")["username"], "alice")
        self.assertIsNone(self.storage.get_user_alas_binding("bob", "Exclusive"))
        self.assertEqual(self.storage.get_user_alas_config("bob")["config_name"], "BobExisting")

        deleted = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={"username": "alice", "config_name": "Exclusive", "enabled": False, "is_default": False},
        )
        reassigned = self.client.put(
            "/api/admin/alas/permissions",
            headers=headers,
            json={
                "username": "bob",
                "config_name": "Exclusive",
                "enabled": True,
                "can_run": True,
                "can_edit": False,
                "is_default": True,
            },
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(reassigned.status_code, 200)
        self.assertEqual(self.storage.get_user_alas_binding("bob", "Exclusive")["username"], "bob")

        self.assertEqual(
            self.client.put(
                "/api/admin/alas/permissions",
                headers=headers,
                json={"username": "alice", "config_name": "Foo", "enabled": True, "is_default": True},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(
                "/api/admin/alas/permissions",
                headers=headers,
                json={"username": "bob", "config_name": "foo", "enabled": True, "is_default": False},
            ).status_code,
            200,
        )

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
        self.main.alas.list_configs = lambda: {
            "ok": True,
            "configs": ["BoundConfig", "UnassignedConfig"],
        }

        response = self.client.get("/api/admin/alas/configs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime_configs"], ["BoundConfig", "UnassignedConfig"])
        self.assertEqual(response.json()["configs"], ["BoundConfig", "UnassignedConfig"])

    def test_admin_overview_lists_every_runtime_and_bound_config_status(self):
        self.login("admin", "admin")
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.bind("admin", "RuntimeA", True, True)
        self.bind("alice", "BoundOnly", True, False)
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.main.alas.list_configs = lambda: {"ok": True, "configs": ["RuntimeA", "RuntimeB"]}

        def status_for_config(config_name, include_configs=False):
            self.assertFalse(include_configs)
            values = {
                "RuntimeA": ("running", "MainTask"),
                "RuntimeB": ("stopped", ""),
                "BoundOnly": ("idle", "Waiting"),
            }
            status, task = values[config_name]
            return {"ok": True, "config": config_name, "status": status, "task": task, "configs": [config_name]}

        self.main.alas.status_for_config = status_for_config
        response = self.client.get("/api/admin/overview/alas")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["configs"], ["RuntimeA", "RuntimeB", "BoundOnly"])
        self.assertTrue(all(isinstance(item, str) for item in payload["configs"]))
        statuses = {item["config"]: item for item in payload["config_statuses"]}
        self.assertEqual(statuses["RuntimeA"]["username"], "admin")
        self.assertEqual(statuses["RuntimeA"]["status"], "running")
        self.assertEqual(statuses["RuntimeA"]["task"], "MainTask")
        self.assertEqual(statuses["RuntimeB"]["username"], "")
        self.assertEqual(statuses["BoundOnly"]["username"], "alice")
        self.assertEqual(payload["config_count"], 3)
        self.assertEqual(payload["running_count"], 1)
        self.assertEqual(payload["status"], "running")
        for legacy_field in ("ok", "settings", "token_set", "task", "config", "error"):
            self.assertIn(legacy_field, payload)

    def test_admin_overview_alas_disabled_and_missing_token_do_not_call_runtime(self):
        self.login("admin", "admin")
        self.bind("admin", "BoundConfig", True, True)
        calls = []
        self.main.alas.list_configs = lambda: calls.append("catalog") or {"ok": True, "configs": []}
        self.main.alas.status_for_config = lambda *args: calls.append("status") or {}

        disabled = self.client.get("/api/admin/overview/alas").json()
        self.assertEqual(disabled["status"], "disabled")
        self.assertTrue(disabled["ok"])
        self.assertEqual(disabled["config_statuses"][0]["config"], "BoundConfig")

        self.storage.set_setting("alas_enabled", "true")
        missing_token = self.client.get("/api/admin/overview/alas").json()
        self.assertEqual(missing_token["status"], "error")
        self.assertFalse(missing_token["ok"])
        self.assertIn("token", missing_token["error"].lower())
        self.assertEqual(calls, [])

    def test_admin_overview_runtime_outage_does_not_fan_out_per_config(self):
        self.login("admin", "admin")
        self.storage.upsert_user("alice", "AlicePassword123", "user")
        self.storage.upsert_user("bob", "BobPassword123", "user")
        self.bind("admin", "RuntimeA", True, True)
        self.bind("alice", "RuntimeB", True, False)
        self.bind("bob", "RuntimeC", True, False)
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.main.alas.list_configs = lambda: {"ok": False, "configs": [], "error": "ALAS API unreachable: refused"}
        calls = []

        def status_for_config(config_name, include_configs=False):
            calls.append(config_name)
            return {"ok": False, "config": config_name, "status": "disconnected", "task": "", "error": "ALAS API unreachable: refused"}

        self.main.alas.status_for_config = status_for_config
        response = self.client.get("/api/admin/overview/alas")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(calls, ["RuntimeA"])
        self.assertEqual(payload["config_count"], 3)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(all(not item["ok"] for item in payload["config_statuses"]))

    def test_admin_overview_does_not_invent_legacy_config_for_empty_catalog(self):
        self.login("admin", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.storage.set_setting("alas_current_config", "LegacyOnly")
        self.main.alas.list_configs = lambda: {"ok": True, "configs": []}
        self.main.alas.status_for_config = lambda config_name, include_configs=False: {
            "ok": True,
            "config": config_name,
            "status": "idle",
            "task": "",
        }

        payload = self.client.get("/api/admin/overview/alas").json()

        self.assertEqual(payload["configs"], [])
        self.assertEqual(payload["config_statuses"], [])
        self.assertEqual(payload["config_count"], 0)
        self.assertEqual(payload["config"], "")
        self.assertEqual(payload["status"], "unknown")

        calls = []
        self.main.alas.list_configs = lambda: {"ok": False, "configs": [], "error": "ALAS API unreachable: refused"}
        self.main.alas.status_for_config = lambda *args: calls.append(args) or {}
        unavailable = self.client.get("/api/admin/overview/alas").json()
        self.assertEqual(unavailable["configs"], [])
        self.assertEqual(unavailable["config_statuses"], [])
        self.assertEqual(unavailable["status"], "error")
        self.assertEqual(calls, [])

    def test_admin_overview_alas_status_concurrency_is_bounded(self):
        self.login("admin", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        runtime_configs = [f"Runtime{index}" for index in range(12)]
        self.main.alas.list_configs = lambda: {"ok": True, "configs": runtime_configs}
        lock = threading.Lock()
        active = 0
        peak = 0

        def status_for_config(config_name, include_configs=False):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return {"ok": True, "config": config_name, "status": "idle", "task": ""}

        self.main.alas.status_for_config = status_for_config
        response = self.client.get("/api/admin/overview/alas")
        repeated = self.client.get("/api/admin/overview/alas")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(repeated.json()["ok"])
        self.assertFalse(any(item["error"] for item in repeated.json()["config_statuses"]))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)

    def test_base_admin_overview_never_waits_for_runtime_status(self):
        self.login("admin", "admin")
        self.bind("admin", "RuntimeA", True, True)
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.main.alas.list_configs = lambda: (_ for _ in ()).throw(AssertionError("catalog must not be queried"))
        self.main.alas.status_for_config = lambda *args: (_ for _ in ()).throw(AssertionError("status must not be queried"))

        response = self.client.get("/api/admin/overview")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["alas"]
        self.assertEqual(payload["status"], "unknown")
        self.assertEqual(payload["configs"], ["RuntimeA"])
        self.assertEqual(payload["config_statuses"], [])
        for field in ("ok", "settings", "enabled", "token_set", "configured", "status", "task", "config", "configs", "error"):
            self.assertIn(field, payload)
        self.assertTrue(all(isinstance(item, str) for item in payload["configs"]))

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
