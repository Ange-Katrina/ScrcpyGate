import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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


class AuthRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-auth-routes-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        os.environ["LOGIN_RATE_LIMIT_MAX"] = "2"
        os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "60"
        os.environ["LOGIN_LOCKOUT_SECONDS"] = "30"
        reset_app_modules(["app.main", "app.storage", "app.security"])
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
        ):
            os.environ.pop(key, None)
        reset_app_modules(["app.main", "app.storage", "app.security"])

    def test_login_route_rate_limits_repeated_failures(self):
        payload = {"username": "admin", "password": "wrong-password"}

        self.assertEqual(self.client.post("/login", data=payload).status_code, 401)
        self.assertEqual(self.client.post("/login", data=payload).status_code, 401)
        limited = self.client.post("/login", data=payload)
        self.assertEqual(limited.status_code, 429)
        self.assertGreater(int(limited.headers.get("retry-after", "0")), 0)

        rows = self.storage.recent_audit(20)
        self.assertIn("login_rate_limited", [row["action"] for row in rows])


if __name__ == "__main__":
    unittest.main()
