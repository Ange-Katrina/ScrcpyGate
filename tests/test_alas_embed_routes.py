#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""ALAS 嵌入路由测试。"""

import gzip
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
        os.environ["ALLOWED_HOSTS"] = "testserver,alas.test:22267"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        for name in ["app.config", "app.main", "app.storage", "app.alas", "app.alas_embed", "app.security"]:
            sys.modules.pop(name, None)
        self.storage = importlib.import_module("app.storage")
        self.storage.init_db()
        self.main = importlib.import_module("app.main")
        self.current_user = None
        self.main.security.get_current_user = lambda request: self.current_user
        self.main.security.allowed_hosts = lambda: {"testserver", "alas.test"}
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
        session = self.storage.create_session(username)
        self.main.security.get_current_session = lambda request: session
        return session

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

    def install_fake_upstream(self, status=200, headers=None, body=b""):
        """安装测试用上游 HTTP 客户端并记录转发请求。"""
        captured = []

        class FakeResponse:
            """模拟 urllib 响应对象。"""

            def __init__(self):
                self.headers = headers or {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return status

            def read(self):
                return body

        class FakeOpener:
            """模拟 urllib opener 对象。"""

            def open(self, req, timeout=0):
                captured.append(
                    {
                        "url": req.full_url,
                        "method": req.get_method(),
                        "data": req.data,
                        "headers": dict(req.header_items()),
                        "timeout": timeout,
                    }
                )
                return FakeResponse()

        self.main.alas_embed.build_opener = lambda *handlers: FakeOpener()
        self.main.alas.public_settings = lambda: {
            "enabled": True,
            "base_url": "http://alas.test:22267",
            "current_config": "alas",
            "token_set": False,
        }
        self.current_user = dict(self.current_user)
        return captured

    def test_proxy_forwards_get_to_upstream_url_with_query(self):
        """代理路由将 GET 请求按路径和重复查询参数转发到上游。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
        )

        res = self.client.get("/alas/embed/proxy/api/state?config=a&config=b&x=1")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'{"ok": true}')
        self.assertEqual(captured[0]["method"], "GET")
        self.assertEqual(captured[0]["url"], "http://alas.test:22267/api/state?config=a&config=b&x=1")

    def test_proxy_forwards_post_body_to_upstream(self):
        """代理路由将 POST 请求正文和 Content-Type 转发到上游。"""
        session = self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
        )

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/run?config=a",
            content=b'{"task":"start"}',
            headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["method"], "POST")
        self.assertEqual(captured[0]["url"], "http://alas.test:22267/api/run?config=a")
        self.assertEqual(captured[0]["data"], b'{"task":"start"}')
        self.assertEqual(captured[0]["headers"].get("Content-type"), "application/json")

    def test_proxy_rewrites_upstream_location_to_embed_proxy(self):
        """上游 Location 响应头会改写到嵌入代理路径下。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.install_fake_upstream(
            status=302,
            headers={"Location": "http://alas.test:22267/dashboard?x=1", "Content-Type": "text/plain"},
            body=b"redirect",
        )

        res = self.client.get("/alas/embed/proxy/login", follow_redirects=False)

        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers.get("location"), "/alas/embed/proxy/dashboard?x=1")

    def test_proxy_passes_through_upstream_error_status(self):
        """上游 HTTP 错误状态和正文会透传给客户端。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.install_fake_upstream(
            status=404,
            headers={"Content-Type": "text/plain"},
            body=b"missing",
        )

        res = self.client.get("/alas/embed/proxy/missing")

        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.text, "missing")

    def test_proxy_rejects_upgrade_requests(self):
        """HTTP 代理明确拒绝 Upgrade/WebSocket 请求。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.get(
            "/alas/embed/proxy/ws",
            headers={"Connection": "Upgrade", "Upgrade": "websocket"},
        )

        self.assertEqual(res.status_code, 501)
        self.assertEqual(captured, [])

    def test_proxy_rejects_absolute_url_path(self):
        """代理路径不能被绝对 URL 逃逸到非 ALAS 上游。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.get("/alas/embed/proxy/http://evil.test/path")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(captured, [])

    def test_proxy_filters_user_html_response(self):
        """普通用户访问 HTML 响应时会过滤管理入口。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        self.install_fake_upstream(
            headers={"Content-Type": "text/html; charset=utf-8"},
            body="<main>挂机-云 其它配置 管理入口</main>".encode("utf-8"),
        )

        res = self.client.get("/alas/embed/proxy/?config=挂机-云")

        self.assertEqual(res.status_code, 200)
        self.assertIn("挂机-云", res.text)
        self.assertNotIn("其它配置", res.text)
        self.assertNotIn("管理入口", res.text)

    def test_proxy_filters_connection_declared_hop_headers(self):
        """Connection 声明的扩展逐跳头不会转发或返回。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(
            headers={
                "Connection": "X-Upstream",
                "X-Upstream": "secret",
                "Content-Type": "text/plain",
            },
            body=b"ok",
        )

        res = self.client.get(
            "/alas/embed/proxy/api/state",
            headers={"Connection": "X-Secret", "X-Secret": "secret"},
        )

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("X-secret", captured[0]["headers"])
        self.assertIsNone(res.headers.get("x-upstream"))

    def test_proxy_strips_request_content_length(self):
        """请求 Content-Length 不会作为普通请求头转发。"""
        session = self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(
            headers={"Content-Type": "application/json"},
            body=b'{"ok": true}',
        )

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/run",
            content=b'{"task":"start"}',
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 200)
        self.assertNotIn("Content-length", captured[0]["headers"])

    def test_proxy_keeps_gzip_html_body_unfiltered(self):
        """带 Content-Encoding 的 HTML 响应不会被解码过滤而破坏 gzip 正文。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        gzip_body = gzip.compress("<main>挂机-云 其它配置 管理入口</main>".encode("utf-8"))
        self.install_fake_upstream(
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
            },
            body=gzip_body,
        )

        res = self.client.get("/alas/embed/proxy/?config=挂机-云")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, "<main>挂机-云 其它配置 管理入口</main>")
        self.assertEqual(res.headers.get("content-encoding"), "gzip")

    def test_proxy_requires_csrf_for_post(self):
        """非安全方法代理请求必须通过既有 CSRF 校验。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.post("/alas/embed/proxy/api/run", content=b"{}")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(captured, [])

    def test_proxy_does_not_filter_admin_html_response(self):
        """管理员访问 HTML 响应时保持上游内容不变。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.install_fake_upstream(
            headers={"Content-Type": "text/html; charset=utf-8"},
            body="<main>其它配置 管理入口</main>".encode("utf-8"),
        )

        res = self.client.get("/alas/embed/proxy/")

        self.assertEqual(res.status_code, 200)
        self.assertIn("其它配置", res.text)
        self.assertIn("管理入口", res.text)

    def test_proxy_passes_through_non_html_body_and_content_type(self):
        """非 HTML 响应透传正文和 Content-Type。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.install_fake_upstream(
            headers={"Content-Type": "image/png"},
            body=b"\x89PNG\r\n",
        )

        res = self.client.get("/alas/embed/proxy/static/logo.png")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "image/png")
        self.assertEqual(res.content, b"\x89PNG\r\n")

    def test_proxy_returns_502_when_upstream_unreachable(self):
        """上游不可达时代理路由返回 502。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        class BrokenOpener:
            """模拟不可达的 urllib opener 对象。"""

            def open(self, req, timeout=0):
                raise OSError("boom")

        self.main.alas_embed.build_opener = lambda *handlers: BrokenOpener()
        self.main.alas.public_settings = lambda: {
            "enabled": True,
            "base_url": "http://alas.test:22267",
            "current_config": "alas",
            "token_set": False,
        }
        self.current_user = dict(self.current_user)

        res = self.client.get("/alas/embed/proxy/")

        self.assertEqual(res.status_code, 502)


if __name__ == "__main__":
    unittest.main()
