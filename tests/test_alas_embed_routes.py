#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""ALAS 嵌入路由测试。"""

import asyncio
import gzip
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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


class AlasEmbedRouteTests(unittest.TestCase):
    """验证 ALAS 嵌入 HTTP 路由。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="webscrcpy-v2-alas-embed-routes-"))
        os.environ["WEB_SCRCPY_DATA_DIR"] = str(self.tmp)
        os.environ["ALLOWED_HOSTS"] = "testserver,alas.test:22267"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        reset_app_modules(["app.config", "app.main", "app.storage", "app.alas", "app.alas_embed", "app.security"])
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

    def test_index_page_contains_alas_embed_entry(self):
        """普通用户投屏页包含 ALAS 原页面入口。"""
        self.login("alice", "password123456", "user")

        res = self.client.get("/")

        self.assertEqual(res.status_code, 200)
        self.assertIn('href="/alas/embed/"', res.text)
        self.assertIn("打开 ALAS 页面", res.text)

    def test_admin_page_contains_alas_embed_entry(self):
        """管理员后台 ALAS 设置包含完整原页面入口。"""
        self.login("admin", "password123456", "admin")

        res = self.client.get("/admin")

        self.assertEqual(res.status_code, 200)
        self.assertIn('href="/alas/embed/"', res.text)
        self.assertIn("打开完整 ALAS 页面", res.text)
        self.assertIn("Runtime URL 可填写 IP、域名或完整 URL", res.text)

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

    def test_proxy_denial_logs_redact_config_names_and_endpoint_paths(self):
        """入口拒绝日志和审计只保留路径类别，不保存配置名或 endpoint。"""
        bound_config = "private-bound-config"
        other_config = "private-other-config"
        endpoint = "192.0.2.32:5555"
        self.login("alice", "password123456", "user")
        self.storage.set_user_alas_config("alice", bound_config, True, True)
        path = f"/alas/embed/proxy/config/{other_config}/serial/{endpoint}"

        with self.assertLogs("webscrcpy.main", level="WARNING") as captured_logs:
            response = self.client.get(path)

        application_logs = "\n".join(captured_logs.output)
        audit_logs = "\n".join(row["detail"] for row in self.storage.recent_audit(5))
        self.assertEqual(response.status_code, 403)
        self.assertIn("route=config", application_logs)
        self.assertIn("route=config", audit_logs)
        for secret in (bound_config, other_config, endpoint, path):
            self.assertNotIn(secret, application_logs)
            self.assertNotIn(secret, audit_logs)

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

    def test_proxy_denies_run_request_when_user_can_run_false(self):
        """普通用户 can_run=False 时 HTTP 代理拒绝绑定配置内运行类请求。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", False, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/task/start?config=挂机-云",
            content=b"{}",
            headers={"X-CSRF-Token": session["csrf_token"]},
        )
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权执行 ALAS 运行类操作")
        self.assertEqual(captured, [])
        self.assertIn("reason=run_permission_denied", logs[0]["detail"])

    def test_proxy_denies_edit_request_when_user_can_edit_false(self):
        """普通用户 can_edit=False 时 HTTP 代理拒绝绑定配置内编辑类请求。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, False)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.put(
            "/alas/embed/proxy/api/settings/save?config=挂机-云",
            content=b"{}",
            headers={"X-CSRF-Token": session["csrf_token"]},
        )
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权修改 ALAS 绑定配置设置")
        self.assertEqual(captured, [])
        self.assertIn("reason=edit_permission_denied", logs[0]["detail"])

    def test_proxy_allows_admin_run_and_edit_requests(self):
        """管理员 HTTP 代理运行和编辑类请求不受普通用户绑定权限限制。"""
        session = self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/task/start?config=其它",
            content=b"{}",
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["method"], "POST")

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
        self.assertFalse(any(log["action"] == "alas_embed_proxy" for log in self.storage.recent_audit(5)))

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

    def test_proxy_response_allows_same_site_iframe(self):
        """代理响应不能携带阻断本站 iframe 的安全头。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        self.install_fake_upstream(
            headers={"Content-Type": "text/html; charset=utf-8"},
            body="<main>挂机-云</main>".encode("utf-8"),
        )

        res = self.client.get("/alas/embed/proxy/?config=挂机-云")

        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.headers.get("x-frame-options"), "DENY")
        self.assertEqual(res.headers.get("content-security-policy"), "frame-ancestors 'self'")
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")

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

    def test_proxy_filters_gzip_html_body_for_bound_user(self):
        """带 Content-Encoding 的 HTML 响应会先解压再过滤，避免 ALAS 菜单泄漏。"""
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
        self.assertIn("挂机-云", res.text)
        self.assertNotIn("其它配置", res.text)
        self.assertNotIn("管理入口", res.text)
        self.assertIn("data-scrcpygate-alas-bind", res.text)
        self.assertIsNone(res.headers.get("content-encoding"))

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

    def test_user_without_binding_gets_clear_error_and_audit_log(self):
        """未绑定普通用户访问入口时返回清晰中文文案并写入审计。"""
        self.login("alice", "password123456", "user")

        res = self.client.get("/alas/embed/")
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "未绑定 ALAS 配置，请联系管理员绑定 ALAS 配置")
        self.assertEqual(logs[0]["username"], "alice")
        self.assertEqual(logs[0]["action"], "alas_embed_denied")
        self.assertIn("missing_binding", logs[0]["detail"])

    def test_proxy_denies_other_config_with_clear_error_and_audit_log(self):
        """普通用户越权访问其它配置时返回清晰中文文案并写入审计。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)

        res = self.client.get("/alas/embed/proxy/?config=其它")
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权访问其它 ALAS 配置")
        self.assertEqual(logs[0]["username"], "alice")
        self.assertEqual(logs[0]["action"], "alas_embed_denied")
        self.assertIn("reason=config_mismatch", logs[0]["detail"])
        self.assertNotIn("password", logs[0]["detail"].lower())

    def test_proxy_appends_bound_config_for_business_request_without_config(self):
        """绑定普通用户访问业务 API 时无 config 应自动补齐绑定配置。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        res = self.client.get("/alas/embed/proxy/api/state")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["url"], "http://alas.test:22267/api/state?config=%E6%8C%82%E6%9C%BA-%E4%BA%91")

    def test_proxy_allows_business_request_with_bound_config(self):
        """绑定普通用户访问业务 API 时带绑定 config 应允许。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        res = self.client.get("/alas/embed/proxy/api/state?config=挂机-云")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["url"], "http://alas.test:22267/api/state?config=%E6%8C%82%E6%9C%BA-%E4%BA%91")

    def test_proxy_filters_json_config_list_for_bound_user(self):
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        self.install_fake_upstream(
            headers={"Content-Type": "application/json"},
            body=json.dumps({"configs": ["挂机-云", "其它"], "ok": True}, ensure_ascii=False).encode("utf-8"),
        )

        res = self.client.get("/alas/embed/proxy/api/configs")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["configs"], ["挂机-云"])

    def test_proxy_denies_json_body_other_config(self):
        """普通用户 POST JSON body 中请求其它配置时应被拒绝。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            json={"config": "其它"},
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权访问其它 ALAS 配置")
        self.assertEqual(captured, [])

    def test_proxy_denies_json_body_run_action_when_can_run_false(self):
        """can_run=False 时普通用户 POST body 中 action=start 应被拒绝。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", False, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            json={"action": "start"},
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权执行 ALAS 运行类操作")
        self.assertEqual(captured, [])

    def test_proxy_denies_form_body_edit_method_when_can_edit_false(self):
        """can_edit=False 时普通用户 form body 中 method=settings.save 应被拒绝。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, False)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            data={"method": "settings.save"},
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权修改 ALAS 绑定配置设置")
        self.assertEqual(captured, [])

    def test_proxy_allows_admin_json_body_run_and_edit(self):
        """管理员 POST body 中运行与编辑命令应允许透传。"""
        session = self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state",
            json={"config": "其它", "action": "start", "method": "settings.save"},
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["data"], b'{"config":"\xe5\x85\xb6\xe5\xae\x83","action":"start","method":"settings.save"}')

    def test_proxy_denies_query_management_route(self):
        """普通用户 query route=admin 管理操作应被拒绝。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.get("/alas/embed/proxy/api/state?config=挂机-云&route=admin")

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权访问 ALAS 管理入口")
        self.assertEqual(captured, [])

    def test_proxy_allows_query_home_route(self):
        """普通用户 query route=home 原首页入口应允许通过。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        res = self.client.get("/alas/embed/proxy/api/state?config=挂机-云&route=home")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})
        self.assertEqual(
            captured[0]["url"],
            "http://alas.test:22267/api/state?config=%E6%8C%82%E6%9C%BA-%E4%BA%91&route=home",
        )

    def test_proxy_denies_query_management_route_with_friendly_html(self):
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.get(
            "/alas/embed/proxy/api/state?config=3256475495&route=admin",
            headers={"Accept": "text/html"},
        )

        self.assertEqual(res.status_code, 403)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("此入口不可访问", res.text)
        self.assertIn("无权访问 ALAS 管理入口", res.text)
        self.assertIn("/alas/embed/proxy/?config=3256475495", res.text)
        self.assertIn("window.location.replace", res.text)
        self.assertEqual(captured, [])

    def test_proxy_denies_user_alas_settings_query(self):
        """普通用户不能通过 HTTP 打开 ALAS -> ALAS 设置页。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        res = self.client.get("/alas/embed/proxy/api/state?config=挂机-云&menu=Alas&task=Alas")

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权访问 ALAS 设置页")
        self.assertEqual(captured, [])

    def test_proxy_denies_body_management_event(self):
        """普通用户 body event=alas.config_list 管理操作应被拒绝。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            json={"event": "alas.config_list"},
            headers={"X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "无权访问 ALAS 管理入口")
        self.assertEqual(captured, [])

    def test_proxy_denies_invalid_json_body_for_bound_user(self):
        """普通用户声明 JSON 但正文无法解析时应拒绝，避免绕过 body 策略。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            content=b'{"event":"alas.config_list"',
            headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "ALAS 请求正文无法安全解析")
        self.assertEqual(captured, [])

    def test_proxy_denies_oversized_json_body_for_bound_user(self):
        """普通用户 JSON body 超过策略解析上限时应拒绝，避免 fail-open 透传。"""
        session = self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_upstream(body=b"should not reach")

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state?config=挂机-云",
            content=b'{"payload":"' + (b"x" * 70000) + b'"}',
            headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "ALAS 请求正文无法安全解析")
        self.assertEqual(captured, [])

    def test_proxy_allows_admin_invalid_json_body(self):
        """管理员无绑定限制时，无法解析的 JSON body 仍按原样透传。"""
        session = self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_upstream(headers={"Content-Type": "application/json"}, body=b'{"ok": true}')

        self.client.cookies.set("wsid", session["sid"], domain="testserver.local")
        res = self.client.post(
            "/alas/embed/proxy/api/state",
            content=b'{"event":"alas.config_list"',
            headers={"Content-Type": "application/json", "X-CSRF-Token": session["csrf_token"]},
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured[0]["data"], b'{"event":"alas.config_list"')

    def test_proxy_disabled_returns_clear_error_and_audit_log(self):
        """ALAS 未启用时代理返回清晰中文文案并写入审计。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "false")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        res = self.client.get("/alas/embed/proxy/")
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "ALAS 控制未启用")
        self.assertEqual(logs[0]["action"], "alas_embed_denied")
        self.assertIn("reason=disabled", logs[0]["detail"])

    def test_proxy_unconfigured_returns_clear_error_and_audit_log(self):
        """ALAS Runtime 未配置时代理返回清晰中文文案并写入审计。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "")
        self.main.alas.public_settings = lambda: {
            "enabled": True,
            "base_url": "",
            "current_config": "alas",
            "token_set": False,
        }

        res = self.client.get("/alas/embed/proxy/")
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "ALAS Runtime 未配置，请先在后台填写 Runtime URL")
        self.assertEqual(logs[0]["action"], "alas_embed_denied")
        self.assertIn("reason=unconfigured", logs[0]["detail"])

    def test_proxy_upstream_unreachable_returns_clear_error_and_audit_log(self):
        """上游不可达时代理返回清晰中文文案并写入审计。"""
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
        logs = self.storage.recent_audit(5)

        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "ALAS Runtime 不可达，请确认服务已启动且 Runtime URL 可访问")
        self.assertTrue(any(log["action"] == "alas_embed_proxy_failed" for log in logs))
        failure_log = next(log for log in logs if log["action"] == "alas_embed_proxy_failed")
        self.assertIn("reason=upstream_unreachable", failure_log["detail"])

    def websocket_close_code(self, path):
        """连接 WebSocket 并返回服务端关闭码。"""
        try:
            with self.client.websocket_connect(path) as websocket:
                message = websocket.receive()
                return message.get("code")
        except Exception as exc:
            return getattr(exc, "code", None) or getattr(exc, "status_code", None)

    def install_fake_websocket_upstream(self, incoming=None, connect_error=None):
        """安装测试用上游 WebSocket 连接器并记录转发行为。"""
        captured = {"targets": [], "sent": [], "closed": []}
        incoming_messages = list(incoming or [])

        class FakeUpstream:
            """模拟 websockets 异步客户端连接。"""

            def __init__(self, target):
                self.target = target

            async def __aenter__(self):
                captured["targets"].append(self.target)
                if connect_error:
                    raise connect_error
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(0.05)
                if not incoming_messages:
                    raise StopAsyncIteration
                message = incoming_messages.pop(0)
                if isinstance(message, BaseException):
                    raise message
                return message

            async def send(self, message):
                captured["sent"].append(message)

            async def close(self, code=1000):
                captured["closed"].append(code)

        self.main.alas_embed.websocket_connect = lambda target, open_timeout=10.0: FakeUpstream(target)
        self.main.alas.public_settings = lambda: {
            "enabled": True,
            "base_url": "http://alas.test:22267/base",
            "current_config": "alas",
            "token_set": False,
        }
        return captured

    def test_websocket_denies_disallowed_origin_before_upstream_connect(self):
        """Origin 检查不通过时 WebSocket 代理拒绝连接且不连接上游。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        captured = self.install_fake_websocket_upstream(incoming=["should-not-reach"])
        original_origin_allowed = self.main.security.websocket_origin_allowed
        self.main.security.websocket_origin_allowed = lambda websocket: False
        try:
            with self.assertRaises(WebSocketDisconnect) as context:
                with self.client.websocket_connect("/alas/embed/proxy/"):
                    pass
        finally:
            self.main.security.websocket_origin_allowed = original_origin_allowed

        self.assertEqual(context.exception.code, 4403)
        self.assertEqual(captured["targets"], [])

    def test_websocket_forwards_text_bidirectionally_and_preserves_query(self):
        """WebSocket 代理转发文本消息并保留重复查询参数。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        captured = self.install_fake_websocket_upstream(incoming=["from-upstream"])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=a&config=b&x=1") as websocket:
            websocket.send_text("from-client")
            self.assertEqual(websocket.receive_text(), "from-upstream")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(captured["targets"], ["ws://alas.test:22267/base/ws?config=a&config=b&x=1"])
        self.assertEqual(captured["sent"], ["from-client"])

    def test_websocket_forwards_binary_bidirectionally(self):
        """WebSocket 代理转发二进制消息。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        captured = self.install_fake_websocket_upstream(incoming=[b"from-upstream"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_bytes(b"from-client")
            self.assertEqual(websocket.receive_bytes(), b"from-upstream")
            websocket.receive()

        self.assertEqual(captured["sent"], [b"from-client"])

    def test_websocket_closes_1008_when_user_can_run_false_sends_run_message(self):
        """普通用户 can_run=False 时 WebSocket 运行类消息会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", False, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"action":"start","config":"挂机-云"}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1008_when_user_can_edit_false_sends_edit_message(self):
        """普通用户 can_edit=False 时 WebSocket 编辑类消息会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, False)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"method":"settings.save","config_name":"挂机-云"}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_admin_forwards_run_and_edit_messages(self):
        """管理员 WebSocket 运行和编辑类消息不受普通用户绑定权限限制。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_websocket_upstream(incoming=["admin-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_text('{"action":"start","method":"settings.save"}')
            self.assertEqual(websocket.receive_text(), "admin-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], ['{"action":"start","method":"settings.save"}'])

    def test_websocket_closes_1008_when_user_message_switches_config(self):
        """普通用户 WebSocket 消息尝试切换配置时客户端和上游均以策略码关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"config":"其它"}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1008_when_user_message_switches_nested_config(self):
        """普通用户 WebSocket 嵌套消息尝试切换配置时连接会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"params":{"config":"其它"}}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1008_when_user_bytes_message_switches_config(self):
        """普通用户 WebSocket bytes JSON 尝试切换配置时连接会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_bytes('{"config":"其它"}'.encode("utf-8"))
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1008_when_user_sends_invalid_binary_message(self):
        """普通用户发送不可解码二进制消息时连接会被策略关闭且不转发。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_bytes(b"\xff\xfe")
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_forwards_admin_binary_message(self):
        """管理员发送二进制消息时仍然转发给上游。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        captured = self.install_fake_websocket_upstream(incoming=[b"admin-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_bytes(b"from-admin")
            self.assertEqual(websocket.receive_bytes(), b"admin-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], [b"from-admin"])

    def test_websocket_closes_1008_when_user_message_requests_management(self):
        """普通用户 WebSocket 消息尝试管理操作时连接会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"event":"alas.config_list"}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1008_when_user_message_requests_alas_settings(self):
        """普通用户 WebSocket 消息尝试打开 ALAS 设置页时连接会被策略关闭。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"menu":"Alas","task":"Alas","config":"挂机-云"}')
            message = websocket.receive()

        self.assertEqual(message["type"], "websocket.close")
        self.assertEqual(message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_closes_1011_when_upstream_connect_fails(self):
        """上游 WebSocket 连接失败时客户端以 1011 关闭。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267/base")
        self.install_fake_websocket_upstream(connect_error=OSError("boom"))

        self.assertEqual(self.websocket_close_code("/alas/embed/proxy/ws"), 1011)

    def test_websocket_denies_unbound_user(self):
        """未绑定普通用户连接 WebSocket 代理时被策略拒绝。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        self.assertEqual(self.websocket_close_code("/alas/embed/proxy/ws"), 1008)

    def test_websocket_forwards_bound_user_text(self):
        """已绑定普通用户可通过 WebSocket 权限检查并转发消息。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream(incoming=["bound-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            websocket.send_text('{"config":"挂机-云"}')
            self.assertEqual(websocket.receive_text(), "bound-ok")
            close_message = websocket.receive()
        logs = self.storage.recent_audit(5)

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], ['{"config":"挂机-云"}'])
        self.assertFalse(any(log["action"] == "alas_embed_ws" for log in logs))

    def test_websocket_filters_bound_user_alas_settings_menu_without_closing(self):
        """上游初始化菜单包含 ALAS 设置时只过滤敏感菜单项，不关闭普通用户连接。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        upstream_message = json.dumps(
            {
                "command": "output",
                "spec": {
                    "items": [
                        {"label": "Alas设置", "value": "Alas"},
                        {"label": "Restart", "value": "Restart"},
                    ]
                },
            },
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[upstream_message])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            filtered = json.loads(websocket.receive_text())
            close_message = websocket.receive()

        self.assertEqual(
            filtered,
            {"command": "output", "spec": {"items": [{"label": "Restart", "value": "Restart"}]}},
        )
        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["closed"], [1000])

    def test_websocket_registers_filtered_callbacks_and_forwards_safe_callback(self):
        """浏览器只能回调实际收到的安全按钮，且安全回调会转发到上游。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        downstream = json.dumps(
            {
                "command": "output",
                "spec": {
                    "items": [
                        {"label": "主页", "value": "home", "callback_id": "safe-callback"},
                        {"label": "管理", "value": "Manage", "callback_id": "blocked-callback"},
                    ]
                },
            },
            ensure_ascii=False,
        )
        callback = json.dumps(
            {"event": "callback", "task_id": "safe-callback", "data": 0},
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[downstream])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=3256475495") as websocket:
            filtered = websocket.receive_text()
            websocket.send_text(callback)
            close_message = websocket.receive()

        self.assertIn("safe-callback", filtered)
        self.assertNotIn("blocked-callback", filtered)
        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], [callback])

    def test_websocket_filtered_callback_closes_both_sides_with_1008(self):
        """被下行过滤的回调 ID 不得到达上游，并以 1008 关闭双方。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        downstream = json.dumps(
            {
                "command": "output",
                "spec": {
                    "items": [
                        {"label": "主页", "value": "home", "callback_id": "safe-callback"},
                        {"label": "管理", "value": "Manage", "callback_id": "blocked-callback"},
                    ]
                },
            },
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[downstream])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=3256475495") as websocket:
            websocket.receive_text()
            websocket.send_text(
                json.dumps({"event": "callback", "task_id": "blocked-callback", "data": 0})
            )
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_unknown_protocol_frames_drop_and_later_output_renders(self):
        """未知回调、任务和事件不转发、不关连接，后续安全输出仍可渲染。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        registration = json.dumps(
            {
                "command": "output",
                "spec": {
                    "type": "buttons",
                    "callback_id": "safe-callback",
                    "buttons": [{"label": "主页", "value": "home"}],
                },
            },
            ensure_ascii=False,
        )
        safe_output = json.dumps(
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览"}},
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[registration, safe_output])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=3256475495") as websocket:
            self.assertIn("safe-callback", websocket.receive_text())
            for frame in (
                {"event": "callback", "task_id": "unknown-callback", "data": 0},
                {"event": "from_cancel", "task_id": "unknown-task", "data": None},
                {"event": "future_event", "task_id": "future-task", "data": {}},
            ):
                websocket.send_text(json.dumps(frame))
            safe_callback = json.dumps(
                {"event": "callback", "task_id": "safe-callback", "data": "home"}
            )
            websocket.send_text(safe_callback)
            rendered = json.loads(websocket.receive_text())
            close_message = websocket.receive()

        self.assertEqual(rendered["spec"]["content"], "任务总览")
        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], [safe_callback])
        self.assertEqual(captured["closed"], [1000])

    def test_websocket_malformed_structured_text_closes_both_sides_with_1008(self):
        """看起来像 JSON 但无法解析的文本帧按畸形协议消息拒绝。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        captured = self.install_fake_websocket_upstream()

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=3256475495") as websocket:
            websocket.send_text("{")
            close_message = websocket.receive()

        self.assertEqual(close_message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertEqual(captured["closed"], [1008, 1008])

    def test_websocket_pin_onchange_registration_does_not_blank_user_stream(self):
        """敏感 pin 注册继续下发并遮罩 endpoint，后续正常输出仍可渲染。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "3256475495", True, True)
        pin_registration = json.dumps(
            {
                "command": "pin_onchange",
                "task_id": "index-task",
                "spec": {
                    "name": "Alas_Emulator_Serial",
                    "callback_id": "sensitive-pin-callback",
                    "serial": "192.0.2.32:5555",
                    "clear": False,
                },
            },
            ensure_ascii=False,
        )
        normal_output = json.dumps(
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览"}},
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[pin_registration, normal_output])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=3256475495") as websocket:
            pin = json.loads(websocket.receive_text())
            output = json.loads(websocket.receive_text())
            close_message = websocket.receive()

        self.assertEqual(pin["command"], "pin_onchange")
        self.assertEqual(pin["spec"]["callback_id"], "sensitive-pin-callback")
        self.assertEqual(pin["spec"]["serial"], "已隐藏")
        self.assertEqual(output["spec"]["content"], "任务总览")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["closed"], [1000])

    def test_websocket_policy_warning_log_hashes_id_and_omits_payload_secrets(self):
        """策略拒绝日志只记录短哈希和元数据，不记录载荷敏感值。"""
        username = "private-user-secret"
        bound_config = "private-bound-config"
        callback_id = "raw-callback-secret-id"
        other_config = "private-other-config"
        endpoint = "192.0.2.32:5555"
        token = "token-secret-value"
        event = "private-event-secret"
        command = "private-command-secret"
        self.login(username, "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config(username, bound_config, True, True)
        downstream = json.dumps({"command": command, "content": "safe"})
        captured = self.install_fake_websocket_upstream(incoming=[downstream])
        frame = json.dumps(
            {
                "event": event,
                "task_id": callback_id,
                "data": {
                    "command": command,
                    "config": other_config,
                    "serial": endpoint,
                    "token": token,
                },
            }
        )

        with self.assertLogs("webscrcpy.alas_embed", level="DEBUG") as captured_logs:
            with self.client.websocket_connect(
                f"/alas/embed/proxy/ws?config={bound_config}"
            ) as websocket:
                self.assertEqual(json.loads(websocket.receive_text()), json.loads(downstream))
                websocket.send_text(frame)
                close_message = websocket.receive()

        logs = "\n".join(captured_logs.output)
        task_hash = hashlib.sha256(callback_id.encode("utf-8")).hexdigest()[:12]
        self.assertEqual(close_message["code"], 1008)
        self.assertEqual(captured["sent"], [])
        self.assertIn("reason=config_mismatch", logs)
        self.assertIn(f"task={task_hash}", logs)
        self.assertIn("ALAS_WS_DOWNSTREAM", logs)
        self.assertIn("event=unknown", logs)
        for secret in (
            username,
            callback_id,
            bound_config,
            other_config,
            endpoint,
            token,
            event,
            command,
            frame,
        ):
            self.assertNotIn(secret, logs)
        for forbidden_field in (
            "action=",
            "direction=",
            "user=",
            "role=",
            "phase=",
            "code=",
            "filtered=",
            "forwarded=",
            "allowed_callbacks=",
            "denied_callbacks=",
            "allowed_tasks=",
            "denied_tasks=",
            "exception=",
        ):
            self.assertNotIn(forbidden_field, logs)

    def test_websocket_handshake_denial_uses_only_ws_metadata_log(self):
        username = "private-handshake-user"
        config = "private-handshake-config"
        token = "private-handshake-token"
        endpoint = "192.0.2.44:5555"
        self.login(username, "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        with self.assertLogs("webscrcpy.main", level="WARNING") as captured_logs:
            close_code = self.websocket_close_code(
                f"/alas/embed/proxy/ws?config={config}&token={token}&endpoint={endpoint}"
            )

        logs = "\n".join(captured_logs.output)
        self.assertEqual(close_code, 1008)
        self.assertIn("ALAS_WS_CLOSE", logs)
        self.assertNotIn("ALAS_EMBED_DENIED", logs)
        for secret in (username, config, token, endpoint):
            self.assertNotIn(secret, logs)
        for forbidden_field in (
            "direction=",
            "user=",
            "role=",
            "phase=",
            "code=",
            "filtered=",
            "exception=",
        ):
            self.assertNotIn(forbidden_field, logs)

    def test_websocket_admin_pywebio_callback_remains_unfiltered(self):
        """管理员仍然原样透传未登记的 PyWebIO 回调。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        callback = json.dumps(
            {"event": "callback", "task_id": "admin-unregistered", "data": "Manage"}
        )
        captured = self.install_fake_websocket_upstream(incoming=["admin-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_text(callback)
            self.assertEqual(websocket.receive_text(), "admin-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], [callback])

    def test_websocket_keeps_bound_user_pywebio_alas_scope_output(self):
        """ALAS/PyWebIO 正常输出包可包含 scope=Alas，不应被当成 ALAS 设置页而断开。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        upstream_message = json.dumps(
            {
                "command": "output",
                "scope": "Alas",
                "spec": {"content": "任务总览", "config": "挂机-云"},
            },
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[upstream_message])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            forwarded = json.loads(websocket.receive_text())
            close_message = websocket.receive()

        self.assertEqual(
            forwarded,
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览", "config": "挂机-云"}},
        )
        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["closed"], [1000])

    def test_websocket_skips_update_notice_without_closing_bound_user_stream(self):
        """上游维护更新提示会被丢弃，但不能关闭普通用户 ALAS 页面流。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        update_notice = json.dumps(
            {"command": "toast", "content": "有更新可用，点击这里进行更新", "action": "update"},
            ensure_ascii=False,
        )
        normal_output = json.dumps(
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览", "config": "挂机-云"}},
            ensure_ascii=False,
        )
        captured = self.install_fake_websocket_upstream(incoming=[update_notice, normal_output])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=挂机-云") as websocket:
            forwarded = json.loads(websocket.receive_text())
            close_message = websocket.receive()

        self.assertEqual(
            forwarded,
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览", "config": "挂机-云"}},
        )
        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["closed"], [1000])

    def test_websocket_appends_bound_config_when_query_missing(self):
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)
        captured = self.install_fake_websocket_upstream(incoming=["bound-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_text("ping")
            self.assertEqual(websocket.receive_text(), "bound-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["targets"], ["ws://alas.test:22267/base/ws?config=%E6%8C%82%E6%9C%BA-%E4%BA%91"])

    def test_websocket_denies_other_config_for_bound_user(self):
        """普通用户通过查询参数请求其它配置时 WebSocket 代理拒绝连接。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)

        self.assertEqual(self.websocket_close_code("/alas/embed/proxy/ws?config=其它"), 1008)

    def test_websocket_denies_other_config_path_for_bound_user(self):
        """普通用户通过路径请求其它配置时 WebSocket 代理拒绝连接。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)

        with self.assertRaises(WebSocketDisconnect) as context:
            with self.client.websocket_connect("/alas/embed/proxy/config/其它"):
                pass

        self.assertEqual(context.exception.code, 1008)

    def test_websocket_denies_nested_other_config_path_for_bound_user(self):
        """普通用户路径中后续配置切换时 WebSocket 代理拒绝连接。"""
        self.login("alice", "password123456", "user")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        self.storage.set_user_alas_config("alice", "挂机-云", True, True)

        with self.assertRaises(WebSocketDisconnect) as context:
            with self.client.websocket_connect("/alas/embed/proxy/config/挂机-云/nested/config/其它"):
                pass

        self.assertEqual(context.exception.code, 1008)

    def test_websocket_forwards_admin_text(self):
        """管理员可通过 WebSocket 权限检查并转发任意配置消息。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_websocket_upstream(incoming=["admin-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws?config=其它") as websocket:
            websocket.send_text('{"config":"其它"}')
            self.assertEqual(websocket.receive_text(), "admin-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], ['{"config":"其它"}'])

    def test_websocket_forwards_admin_management_text(self):
        """管理员 WebSocket 管理消息不受普通用户过滤影响并透传。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")
        captured = self.install_fake_websocket_upstream(incoming=["admin-ok"])

        with self.client.websocket_connect("/alas/embed/proxy/ws") as websocket:
            websocket.send_text('{"event":"Manage","method":"alas.config_list"}')
            self.assertEqual(websocket.receive_text(), "admin-ok")
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1000)
        self.assertEqual(captured["sent"], ['{"event":"Manage","method":"alas.config_list"}'])

    def test_websocket_denies_when_alas_disabled(self):
        """ALAS 未启用时 WebSocket 代理拒绝连接。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "false")
        self.storage.set_setting("alas_base_url", "http://alas.test:22267")

        self.assertEqual(self.websocket_close_code("/alas/embed/proxy/ws"), 1011)

    def test_websocket_denies_when_base_url_missing(self):
        """ALAS Runtime 未配置时 WebSocket 代理拒绝连接。"""
        self.login("admin", "password123456", "admin")
        self.storage.set_setting("alas_enabled", "true")
        self.storage.set_setting("alas_base_url", "")

        self.assertEqual(self.websocket_close_code("/alas/embed/proxy/ws"), 1011)


if __name__ == "__main__":
    unittest.main()
