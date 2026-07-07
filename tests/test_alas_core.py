import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_modules(data_dir: Path):
    os.environ["WEB_SCRCPY_DATA_DIR"] = str(data_dir)
    for name in ["app.alas", "app.storage"]:
        sys.modules.pop(name, None)
    storage = importlib.import_module("app.storage")
    storage.init_db()
    alas = importlib.import_module("app.alas")
    return storage, alas


class AlasCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-alas-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)

    def test_config_name_rejects_path_traversal(self):
        _, alas = load_modules(self.tmp)
        with self.assertRaises(ValueError):
            alas.sanitize_config_name("../Current")
        with self.assertRaises(ValueError):
            alas.sanitize_config_name("bad/name")
        self.assertEqual(alas.sanitize_config_name("Current config_1"), "Current config_1")

    def test_save_config_rejects_running_runtime(self):
        _, alas = load_modules(self.tmp)
        alas.status_for_config = lambda config_name, include_configs=False: {"status": "running"}
        result = alas.save_config("a", "a", {"x": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 409)

    def test_toggle_error_uses_restart_then_fallback_start(self):
        storage, alas = load_modules(self.tmp)
        storage.set_setting("alas_current_config", "Current")
        calls = []

        def fake_request_api(path, method="GET", params=None, body=None, timeout=3.0):
            calls.append((path, method, params))
            if path == "restart":
                return None, 404, "HTTP 404"
            if path == "stop":
                return {"ok": True}, 200, ""
            if path == "start":
                return {"status": "running"}, 200, ""
            if path == "status":
                return {"status": "error", "config": "Current"}, 200, ""
            return {}, 200, ""

        alas.request_api = fake_request_api
        result = alas.control("toggle")
        self.assertTrue(result["ok"])
        self.assertIn(("restart", "POST", {"config": "Current"}), calls)
        self.assertIn(("stop", "POST", {"config": "Current"}), calls)
        self.assertIn(("start", "POST", {"config": "Current"}), calls)

    def test_control_for_config_uses_explicit_config(self):
        _, alas = load_modules(self.tmp)
        calls = []

        def fake_request_api(path, method="GET", params=None, body=None, timeout=3.0):
            calls.append((path, method, params))
            if path == "status":
                return {"status": "idle", "config": params["config"]}, 200, ""
            if path == "restart":
                return {"status": "running"}, 200, ""
            return {}, 200, ""

        alas.request_api = fake_request_api
        result = alas.control_for_config("toggle", "Alice")
        self.assertTrue(result["ok"])
        self.assertEqual(result["config"], "Alice")
        self.assertIn(("restart", "POST", {"config": "Alice"}), calls)

    def test_user_save_does_not_update_global_current_config(self):
        storage, alas = load_modules(self.tmp)
        storage.set_setting("alas_current_config", "Global")
        alas.status_for_config = lambda config_name, include_configs=False: {"status": "idle", "config": config_name}

        def fake_request_api(path, method="GET", params=None, body=None, timeout=3.0):
            if path == "config" and method == "PUT":
                return {"ok": True, "target": params["target"]}, 200, ""
            return {"status": "idle", "config": params["config"]}, 200, ""

        alas.request_api = fake_request_api
        result = alas.save_config("Alice", "Alice", {"x": 1}, update_current=False)
        self.assertTrue(result["ok"])
        self.assertEqual(storage.get_setting("alas_current_config"), "Global")

    def test_save_settings_resolves_base_url_without_port(self):
        storage, alas = load_modules(self.tmp)
        alas.resolve_base_url = lambda raw: "http://alas.example.test:22267"

        alas.save_settings({"base_url": "alas.example.test"})

        self.assertEqual(
            storage.get_setting("alas_base_url"),
            "http://alas.example.test:22267",
        )


if __name__ == "__main__":
    unittest.main()
