#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""ALAS 嵌入路由测试。"""

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
    """验证 ALAS 嵌入 HTTP 路由。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-alas-embed-routes-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        for name in ["app.config", "app.main", "app.storage", "app.alas", "app.alas_embed", "app.security"]:
            sys.modules.pop(name, None)
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.main = importlib.import_module("app.main")
        self.current_user = None
        self.main.security.get_current_user = lambda request: self.current_user
        self.client = TestClient(self.main.app)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("WEB_SCRCPY_DATA_DIR", None)
        os.environ.pop("ALLOWED_HOSTS", None)
        os.environ.pop("SESSION_COOKIE_SECURE", None)

    def login(self, username="admin", password="password123456", role="admin"):
        """创建指定用户并设置当前测试用户。"""
        self.storage.upsert_user(username, password, role)
        user = self.storage.get_user(username)
        self.current_user = dict(user)

    def test_embed_requires_login(self):
        """未登录访问嵌入入口时重定向到登录页。"""
        res = self.client.get("/alas/embed/", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers.get("location", ""))

    def test_admin_embed_page_loads(self):
        """管理员可以打开完整 ALAS 嵌入入口。"""
        self.login("admin", "password123456", "admin")
        res = self.client.get("/alas/embed/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ALAS 原页面", res.text)
        self.assertIn("/alas/embed/proxy/", res.text)
        self.assertIn("管理员完整访问", res.text)

    def test_user_with_binding_embed_page_loads(self):
        """已绑定配置的普通用户可以打开绑定配置入口。"""
        self.login("alice", "password123456", "user")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        res = self.client.get("/alas/embed/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ALAS - 挂机-云", res.text)
        self.assertIn("/alas/embed/proxy/?config=%E6%8C%82%E6%9C%BA-%E4%BA%91", res.text)

    def test_user_binding_embed_page_encodes_config_query(self):
        """绑定配置名包含特殊字符时 iframe 查询参数会被 URL 编码。"""
        self.login("alice", "password123456", "user")
        self.storage.set_user_alas_config("alice", "挂机 A&B", True, True)
        res = self.client.get("/alas/embed/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("config=挂机 A&B", res.text)
        self.assertIn("config=%E6%8C%82%E6%9C%BA+A%26B", res.text)

    def test_user_without_binding_gets_403(self):
        """未绑定 ALAS 配置的普通用户访问入口时被拒绝。"""
        self.login("alice", "password123456", "user")
        res = self.client.get("/alas/embed/")
        self.assertEqual(res.status_code, 403)

    def test_proxy_denies_other_config_for_bound_user(self):
        """代理骨架拒绝普通用户访问非绑定配置。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        res = self.client.get("/alas/embed/proxy/?config=其它")
        self.assertEqual(res.status_code, 403)

    def test_proxy_denies_repeated_config_when_other_config_first(self):
        """代理骨架拒绝普通用户通过重复 config 混入非绑定配置。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        res = self.client.get("/alas/embed/proxy/?config=其它&config=挂机-云")
        self.assertEqual(res.status_code, 403)

    def test_proxy_denies_repeated_config_when_other_config_last(self):
        """代理骨架拒绝普通用户在重复 config 尾部混入非绑定配置。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        res = self.client.get("/alas/embed/proxy/?config=挂机-云&config=其它")
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
