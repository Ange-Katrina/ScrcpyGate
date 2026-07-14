import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

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


class MainLifespanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-main-lifespan-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules(["app.main", "app.mirror", "app.storage", "app.security", "app.adb_monitor"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for key in ("WEB_SCRCPY_DATA_DIR", "ALLOWED_HOSTS", "SESSION_COOKIE_SECURE"):
            os.environ.pop(key, None)
        reset_app_modules(["app.main", "app.mirror", "app.storage", "app.security", "app.adb_monitor"])

    def test_testclient_runs_lifespan_without_deprecation_warnings(self):
        events = []

        async def monitor_start():
            events.append("monitor_start")

        async def monitor_stop():
            events.append("monitor_stop")

        async def mirror_stop_all():
            events.append("mirror_stop_all")

        async def autostop_loop():
            await asyncio.Event().wait()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            main = importlib.import_module("app.main")
            with (
                mock.patch.object(main.storage, "init_db", side_effect=lambda: events.append("init_db")),
                mock.patch.object(main.adb_monitor, "start", side_effect=monitor_start),
                mock.patch.object(main.adb_monitor, "stop", side_effect=monitor_stop),
                mock.patch.object(main.manager, "stop_all", side_effect=mirror_stop_all),
                mock.patch.object(main, "mirror_autostop_loop", side_effect=autostop_loop),
            ):
                with TestClient(main.app) as client:
                    response = client.get("/login")
                    task = main.mirror_autostop_task
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.headers["content-type"].startswith("text/html"))
                    self.assertIn("ScrcpyGate", response.text)
                    self.assertIsNotNone(task)
                    self.assertFalse(task.done())

                self.assertIsNone(main.mirror_autostop_task)
                self.assertTrue(task.cancelled())

        self.assertEqual(events, ["init_db", "monitor_start", "mirror_stop_all", "monitor_stop"])
        deprecations = [item for item in caught if issubclass(item.category, DeprecationWarning)]
        self.assertEqual(deprecations, [])


if __name__ == "__main__":
    unittest.main()
