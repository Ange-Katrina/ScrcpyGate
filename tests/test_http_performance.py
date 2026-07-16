import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from fastapi import Request
from fastapi.testclient import TestClient

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


class HttpPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="scrcpygate-http-performance-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules(
            [
                "app.main",
                "app.mirror",
                "app.storage",
                "app.security",
                "app.alas",
                "app.alas_embed",
                "app.adb_monitor",
            ]
        )
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.storage.upsert_user("admin", "AdminPassword123", "admin")
        self.session = self.storage.create_session("admin")
        self.main = importlib.import_module("app.main")
        self.client = TestClient(self.main.app)
        self.client.cookies.set("wsid", self.session["sid"])

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key in ("WEB_SCRCPY_DATA_DIR", "ALLOWED_HOSTS", "SESSION_COOKIE_SECURE"):
            os.environ.pop(key, None)
        reset_app_modules(
            [
                "app.main",
                "app.mirror",
                "app.storage",
                "app.security",
                "app.alas",
                "app.alas_embed",
                "app.adb_monitor",
            ]
        )

    def test_large_static_asset_is_gzipped(self):
        response = self.client.get(
            "/static/js/input.js?v=dad3e85fe2fa",
            headers={"accept-encoding": "gzip"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.headers.get("vary", ""))
        self.assertGreater(len(response.content), 500)

    def test_static_cache_headers_require_one_recognized_version(self):
        versioned = self.client.get("/static/js/input.js?v=dad3e85fe2fa")
        unversioned = self.client.get("/static/js/input.js")
        invalid = self.client.get("/static/js/input.js?v=short")
        repeated = self.client.get("/static/js/input.js?v=dad3e85fe2fa&v=70381d825b1a")

        self.assertEqual(
            versioned.headers.get("cache-control"),
            "public, max-age=31536000, immutable",
        )
        for response in (unversioned, invalid, repeated):
            self.assertEqual(
                response.headers.get("cache-control"),
                "public, max-age=0, must-revalidate",
            )
            self.assertTrue(response.headers.get("etag"))

    def test_version_query_does_not_make_html_or_api_immutable(self):
        login = self.client.get("/login?v=20260712")
        health = self.client.get("/healthz?v=20260712")

        self.assertEqual(login.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertNotIn("immutable", login.headers.get("cache-control", ""))
        self.assertNotIn("immutable", health.headers.get("cache-control", ""))

    def test_alas_status_keeps_event_loop_responsive_and_uses_worker_thread(self):
        started = threading.Event()
        release = threading.Event()
        worker_threads = []
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/alas/status",
                "query_string": b"",
                "headers": [],
            }
        )
        user = {"username": "alice", "role": "user"}
        binding = {
            "username": "alice",
            "config_name": "Alpha",
            "can_run": True,
            "can_edit": False,
        }

        def blocking_status(config_name, include_configs=False):
            worker_threads.append(threading.get_ident())
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release ALAS worker")
            return {
                "ok": True,
                "configured": True,
                "status": "idle",
                "task": "",
                "config": config_name,
                "settings": {},
                "configs": [config_name],
            }

        async def exercise():
            loop_thread = threading.get_ident()
            with (
                mock.patch.object(self.main.security, "require_user", return_value=user),
                mock.patch.object(self.main, "alas_binding_for_user", return_value=dict(binding)),
                mock.patch.object(self.main.alas, "status_for_config", side_effect=blocking_status),
            ):
                task = asyncio.create_task(self.main.api_alas_status(request))
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(started.is_set())
                ticks = 0
                for _ in range(5):
                    await asyncio.sleep(0.01)
                    ticks += 1
                self.assertFalse(task.done())
                release.set()
                result = await asyncio.wait_for(task, timeout=1)
            return loop_thread, ticks, result

        try:
            loop_thread, ticks, result = asyncio.run(exercise())
        finally:
            release.set()

        self.assertEqual(ticks, 5)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["config"], "Alpha")
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], loop_thread)

    def test_alas_overview_pool_stays_bounded_after_cancellation_across_loops(self):
        lock = threading.Lock()
        first_batch_started = threading.Event()
        release = threading.Event()
        active = 0
        peak = 0
        started = 0
        errors = []

        def blocking_status(name):
            nonlocal active, peak, started
            with lock:
                active += 1
                started += 1
                peak = max(peak, active)
                if started >= self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY:
                    first_batch_started.set()
            if not release.wait(timeout=3):
                raise TimeoutError(f"worker {name} was not released")
            with lock:
                active -= 1
            return {"ok": True, "config": name}

        async def cancel_first_batch():
            tasks = [
                asyncio.create_task(self.main._admin_alas_call(blocking_status, f"first-{index}"))
                for index in range(self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)
            ]
            while not first_batch_started.is_set():
                await asyncio.sleep(0.001)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        def run(coro):
            try:
                asyncio.run(coro)
            except Exception as exc:
                errors.append(exc)

        first_loop = threading.Thread(target=run, args=(cancel_first_batch(),))
        second_loop = None
        try:
            first_loop.start()
            self.assertTrue(first_batch_started.wait(timeout=1))
            first_loop.join(timeout=1)
            self.assertFalse(first_loop.is_alive())

            async def run_second_batch():
                return await asyncio.gather(*(
                    self.main._admin_alas_call(blocking_status, f"second-{index}")
                    for index in range(self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)
                ))

            second_loop = threading.Thread(target=run, args=(run_second_batch(),))
            second_loop.start()
            threading.Event().wait(0.1)
            with lock:
                self.assertEqual(started, self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)
                self.assertEqual(peak, self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)
        finally:
            release.set()
            first_loop.join(timeout=2)
            if second_loop is not None:
                second_loop.join(timeout=2)

        self.assertFalse(errors)
        self.assertIsNotNone(second_loop)
        self.assertFalse(second_loop.is_alive())
        self.assertEqual(started, self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY * 2)
        self.assertLessEqual(peak, self.main.ADMIN_ALAS_OVERVIEW_CONCURRENCY)

    def test_all_alas_network_routes_offload_blocking_helpers(self):
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_token", "secret")
        self.storage.set_setting("alas_current_config", "Alpha")
        request_threads = []
        worker_calls = []
        original_require_user = self.main.security.require_user
        status_payload = {
            "ok": True,
            "configured": True,
            "status": "idle",
            "task": "",
            "config": "Alpha",
            "configs": ["Alpha"],
            "settings": {"enabled": True, "token_set": True},
        }
        admin_payload = {
            "settings": {"enabled": True, "token_set": True},
            "status": dict(status_payload),
            "bindings": [],
            "bound_configs": [],
        }

        def tracked_require_user(request):
            request_threads.append(threading.get_ident())
            return original_require_user(request)

        def worker_result(name, value):
            def call(*args, **kwargs):
                worker_calls.append((name, threading.get_ident()))
                return value

            return call

        csrf_headers = {"x-csrf-token": self.session["csrf_token"]}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.main.security, "require_user", side_effect=tracked_require_user))
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "status_for_config",
                    side_effect=worker_result("status", dict(status_payload)),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "list_configs",
                    side_effect=worker_result("list_configs", {"ok": True, "configs": ["Alpha"]}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "control_for_config",
                    side_effect=worker_result(
                        "control",
                        {"ok": True, "action": "restart", "config": "Alpha", "alas": dict(status_payload)},
                    ),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main,
                    "admin_alas_payload",
                    side_effect=worker_result("admin_payload", dict(admin_payload)),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "get_config",
                    side_effect=worker_result("get_config", {"ok": True, "config": "Alpha", "data": {}}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "save_config",
                    side_effect=worker_result("save_config", {"ok": True, "config": "Alpha"}),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    self.main.alas,
                    "save_settings",
                    side_effect=worker_result("save_settings", None),
                )
            )

            responses = [
                self.client.get("/api/alas/status"),
                self.client.post("/api/alas/toggle", headers=csrf_headers),
                self.client.get("/api/admin/overview"),
                self.client.get("/api/admin/overview/alas"),
                self.client.get("/api/admin/alas?config=Alpha"),
                self.client.put("/api/admin/alas", headers=csrf_headers, json={"enabled": True}),
                self.client.post(
                    "/api/admin/alas/toggle",
                    headers=csrf_headers,
                    json={"config": "Alpha"},
                ),
                self.client.get("/api/admin/alas/config?config=Alpha"),
                self.client.put(
                    "/api/admin/alas/config",
                    headers=csrf_headers,
                    json={"source": "Alpha", "target": "Alpha", "data": {}},
                ),
            ]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(request_threads)
        self.assertEqual(
            {name for name, _ in worker_calls},
            {"status", "list_configs", "control", "admin_payload", "get_config", "save_config", "save_settings"},
        )
        self.assertTrue(all(thread_id not in request_threads for _, thread_id in worker_calls))


if __name__ == "__main__":
    unittest.main()
