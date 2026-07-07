#!/usr/bin/env python3
# -_- coding: utf-8 -_-

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


class AlasEmbedRouteTests(unittest.TestCase):
    def clear_app_modules(self):
        for module_name in (
            "app.main",
            "app.storage",
            "app.alas",
            "app.alas_embed",
            "app.security",
        ):
            sys.modules.pop(module_name, None)
        app_package = sys.modules.get("app")
        if app_package:
            for attribute_name in ("main", "storage", "alas", "alas_embed", "security"):
                if hasattr(app_package, attribute_name):
                    delattr(app_package, attribute_name)

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="scrcpygate-alas-embed-")
        os.environ["WEB_SCRCPY_DATA_DIR"] = self.data_dir
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        self.clear_app_modules()
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.main = importlib.import_module("app.main")
        self.client = TestClient(self.main.app)
        self.session_cookie = None

    def tearDown(self):
        self.client.close()
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
        os.environ.pop("ALLOWED_HOSTS", None)
        os.environ.pop("SESSION_COOKIE_SECURE", None)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        self.clear_app_modules()

    def login(self, username, password, role):
        self.storage.upsert_user(username, password, role)
        response = self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.session_cookie = response.cookies.get("wsid")
        self.assertTrue(self.session_cookie)
        return response

    def test_embed_requires_login(self):
        response = self.client.get("/alas/embed/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers.get("location", ""))

    def test_user_without_binding_gets_403(self):
        self.login("alice", "UserPassword123!@#", "user")

        response = self.client.get("/alas/embed/", follow_redirects=False)

        self.assertEqual(response.status_code, 403)

    def test_admin_embed_page_loads(self):
        self.login("admin", "AdminPassword123!", "admin")

        response = self.client.get("/alas/embed/", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("ALAS", response.text)


if __name__ == "__main__":
    unittest.main()
