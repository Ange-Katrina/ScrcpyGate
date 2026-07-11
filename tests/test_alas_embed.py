#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import io
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.alas_embed as alas_embed

from app.alas_embed import (
    PyWebIOSessionPolicy,
    PyWebIOTaskRegistration,
    WebSocketMessageAction,
    bound_config_query_items,
    build_upstream_url,
    denied_page_html,
    embed_shell_html,
    filter_user_json_payload,
    filter_user_websocket_downstream,
    filter_user_html,
    rewrite_location_header,
    proxy_decision,
    resolve_base_url,
    runtime_url_candidates,
    websocket_message_allowed,
    websocket_target_url,
)


class AlasEmbedUrlTests(unittest.TestCase):
    def test_websocket_target_url_converts_http_scheme_and_preserves_repeated_query(self):
        result = websocket_target_url(
            "http://alas.test:22267/base/",
            "ws/channel",
            [("config", "a"), ("config", "b"), ("x", "1")],
        )

        self.assertEqual(result, "ws://alas.test:22267/base/ws/channel?config=a&config=b&x=1")

    def test_websocket_target_url_converts_https_scheme_to_wss(self):
        result = websocket_target_url("https://alas.test:443", "pywebio", [])

        self.assertEqual(result, "wss://alas.test:443/pywebio")

    def test_build_upstream_url_preserves_base_path(self):
        result = build_upstream_url(
            "http://alas.test:22267/base/",
            "api/state",
            [],
        )

        self.assertEqual(result, "http://alas.test:22267/base/api/state")

    def test_rewrite_location_header_rewrites_relative_under_base_path(self):
        cases = {
            "../api/state": "/alas/embed/proxy/api/state",
            "./next": "/alas/embed/proxy/next",
            "child": "/alas/embed/proxy/child",
            "/base/root": "/alas/embed/proxy/root",
        }
        for location, expected in cases.items():
            with self.subTest(location=location):
                self.assertEqual(
                    rewrite_location_header(
                        location,
                        "http://alas.test:22267/base/current/page",
                    ),
                    expected,
                )

    def test_rewrite_location_header_does_not_expose_external_location(self):
        result = rewrite_location_header(
            "https://evil.test/login",
            "http://alas.test:22267/base/current/page",
        )

        self.assertNotIn("evil.test", result)
        self.assertEqual(result, "/alas/embed/proxy/")

    def test_rewrite_location_header_rejects_same_origin_outside_base_path(self):
        result = rewrite_location_header(
            "/other/root",
            "http://alas.test:22267/base/current/page",
        )

        self.assertNotEqual(result, "/alas/embed/proxy/other/root")
        self.assertNotIn("/other/root", result)

    def test_rewrite_location_header_rejects_relative_escape_from_base_path(self):
        result = rewrite_location_header(
            "../../outside",
            "http://alas.test:22267/base/current/page",
        )

        self.assertNotIn("outside", result)


class AlasEmbedTests(unittest.TestCase):
    def test_embed_shell_html_escapes_inputs_and_links_back(self):
        result = embed_shell_html(
            "ALAS <原页面>",
            '/alas/embed/proxy/?config="x"',
            "当前 <仅允许>",
        )

        self.assertIn("ALAS &lt;原页面&gt;", result)
        self.assertIn('/alas/embed/proxy/?config=&quot;x&quot;', result)
        self.assertIn("当前 &lt;仅允许&gt;", result)
        self.assertIn('href="/"', result)
        self.assertNotIn("ALAS <原页面>", result)

    def test_ip_without_port_uses_default_runtime_port(self):
        self.assertEqual(
            runtime_url_candidates("192.168.5.18"),
            ["http://192.168.5.18:22267"],
        )

    def test_domain_without_port_uses_common_fallback_ports(self):
        self.assertEqual(
            runtime_url_candidates("alas.example.test"),
            [
                "http://alas.example.test:80",
                "https://alas.example.test:443",
                "http://alas.example.test:22267",
            ],
        )

    def test_bound_config_query_items_appends_when_missing(self):
        decision = alas_embed.ProxyDecision(allowed=True, config_name="挂机-云", filtered=True)

        self.assertEqual(
            bound_config_query_items([("x", "1")], decision),
            [("x", "1"), ("config", "挂机-云")],
        )

    def test_bound_config_query_items_preserves_explicit_bound_config(self):
        decision = alas_embed.ProxyDecision(allowed=True, config_name="挂机-云", filtered=True)

        self.assertEqual(
            bound_config_query_items([("config", "挂机-云"), ("x", "1")], decision),
            [("config", "挂机-云"), ("x", "1")],
        )

    def test_explicit_https_domain_without_port_only_uses_https_candidates(self):
        self.assertEqual(
            runtime_url_candidates("https://alas.example.test"),
            [
                "https://alas.example.test:443",
                "https://alas.example.test:22267",
            ],
        )

    def test_explicit_http_domain_without_port_only_uses_http_candidates(self):
        self.assertEqual(
            runtime_url_candidates("http://alas.example.test"),
            [
                "http://alas.example.test:80",
                "http://alas.example.test:22267",
            ],
        )

    def test_full_url_with_port_normalizes_root_path(self):
        self.assertEqual(
            runtime_url_candidates("http://192.168.5.18:22267/"),
            ["http://192.168.5.18:22267"],
        )

    def test_probe_runtime_url_returns_true_for_http_200(self):
        """Runtime 探测在 HTTP 200 时返回可达。"""
        class FakeResponse:
            """模拟成功 HTTP 响应。"""

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def getcode(self):
                return 200

        class FakeOpener:
            """模拟 urllib opener 对象。"""

            def open(self, req, timeout=0):
                return FakeResponse()

        original = alas_embed.build_opener
        alas_embed.build_opener = lambda *handlers: FakeOpener()
        try:
            self.assertTrue(alas_embed.probe_runtime_url("http://alas.test:22267"))
        finally:
            alas_embed.build_opener = original

    def test_probe_runtime_url_returns_false_for_http_404(self):
        """Runtime 探测遇到 HTTP 404 时不应视为可达。"""
        class FakeOpener:
            """模拟返回 HTTPError 的 urllib opener 对象。"""

            def open(self, req, timeout=0):
                raise HTTPError(req.full_url, 404, "missing", {}, io.BytesIO(b"missing"))

        original = alas_embed.build_opener
        alas_embed.build_opener = lambda *handlers: FakeOpener()
        try:
            self.assertFalse(alas_embed.probe_runtime_url("http://alas.test:22267"))
        finally:
            alas_embed.build_opener = original

    def test_url_with_username_or_password_raises_value_error(self):
        with self.assertRaises(ValueError):
            runtime_url_candidates("http://user:pass@192.168.5.18:22267")

    def test_url_with_params_raises_value_error(self):
        with self.assertRaises(ValueError):
            runtime_url_candidates("http://example.com/;x")


class AlasEmbedResolveTests(unittest.TestCase):
    def test_resolve_base_url_returns_first_reachable_candidate(self):
        attempts = []

        def probe(url, timeout=2.0):
            attempts.append((url, timeout))
            return url == "http://alas.example.test:22267"

        self.assertEqual(
            resolve_base_url("alas.example.test", probe=probe),
            "http://alas.example.test:22267",
        )
        self.assertEqual(
            attempts,
            [
                ("http://alas.example.test:80", 2.0),
                ("https://alas.example.test:443", 2.0),
                ("http://alas.example.test:22267", 2.0),
            ],
        )

    def test_resolve_base_url_raises_when_all_candidates_fail(self):
        with self.assertRaisesRegex(ValueError, "ALAS Runtime unreachable"):
            resolve_base_url("alas.example.test", probe=lambda url, timeout=2.0: False)


class AlasEmbedPolicyTests(unittest.TestCase):
    def test_admin_policy_allows_management(self):
        decision = proxy_decision(
            {"role": "admin"},
            None,
            "manage",
            {},
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.filtered)

    def test_user_without_binding_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            None,
            "",
            {},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_with_empty_binding_config_name_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": ""},
            "",
            {},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_other_config_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "",
            {"config": "其它"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_other_config_path_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "config/其它",
            {},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_nested_other_config_path_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "config/挂机-云/nested/config/其它",
            {},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_repeated_query_with_other_config_denied(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "",
            {"config": ["挂机-云", "其它"]},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)

    def test_user_repeated_query_with_bound_config_allowed(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "",
            {"config": ["挂机-云", "挂机-云"]},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.config_name, "挂机-云")

    def test_user_can_run_false_denies_run_path(self):
        """普通用户 can_run=False 时拒绝明显运行类 HTTP 路径。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": False, "can_edit": True},
            "api/task/start",
            {"config": "挂机-云"},
            method="POST",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(decision.reason, "run permission denied")

    def test_user_can_edit_false_denies_edit_path(self):
        """普通用户 can_edit=False 时拒绝明显编辑类 HTTP 路径。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": False},
            "api/settings/save",
            {"config": "挂机-云"},
            method="PUT",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(decision.reason, "edit permission denied")

    def test_user_readonly_request_allowed_when_run_and_edit_denied(self):
        """普通用户只读状态查询不受 can_run/can_edit 限制。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": False, "can_edit": False},
            "api/status",
            {"config": "挂机-云"},
            method="GET",
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.filtered)

    def test_user_business_request_without_config_allowed_for_bound_user(self):
        """普通用户访问业务 API 时可由代理补齐绑定配置。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {},
            method="GET",
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.filtered)

    def test_user_static_request_without_config_allowed(self):
        """普通用户访问静态资源时允许不携带配置。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": False, "can_edit": False},
            "static/logo.png",
            {},
            method="GET",
        )

        self.assertTrue(decision.allowed)

    def test_user_body_other_config_denied(self):
        """普通用户非安全方法正文中的其它配置会被拒绝。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云"},
            method="POST",
            body={"config": "其它"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "config mismatch")

    def test_user_body_run_action_denied_when_can_run_false(self):
        """can_run=False 时正文中的运行操作会被拒绝。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": False, "can_edit": True},
            "api/state",
            {"config": "挂机-云"},
            method="POST",
            body={"action": "start"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "run permission denied")

    def test_user_body_edit_method_denied_when_can_edit_false(self):
        """can_edit=False 时正文中的编辑操作会被拒绝。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": False},
            "api/state",
            {"config": "挂机-云"},
            method="POST",
            body={"method": "settings.save"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "edit permission denied")

    def test_user_query_management_route_denied(self):
        """普通用户查询参数中的管理 route 会被拒绝。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云", "route": "admin"},
            method="GET",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "management path denied")

    def test_user_query_home_route_allowed(self):
        """普通用户允许访问 ALAS 原首页，否则 PyWebIO 初始加载可能被阻断。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云", "route": "home"},
            method="GET",
        )

        self.assertTrue(decision.allowed)

    def test_user_body_management_event_denied(self):
        """普通用户正文中的管理事件会被拒绝。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云"},
            method="POST",
            body={"event": "alas.config_list"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "management path denied")

    def test_admin_policy_allows_alas_settings(self):
        """管理员仍可完整访问 ALAS 原页面设置分组。"""
        decision = proxy_decision(
            {"role": "admin"},
            None,
            "api/state",
            {"menu": "Alas", "task": "Alas"},
            method="GET",
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.filtered)

    def test_user_query_alas_settings_denied(self):
        """普通用户不能打开 ALAS -> ALAS 设置页。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云", "menu": "Alas", "task": "Alas"},
            method="GET",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 403)
        self.assertEqual(decision.reason, "alas settings denied")

    def test_user_body_alas_emulator_setting_denied(self):
        """普通用户不能请求 ALAS 模拟器设置字段。"""
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云", "can_run": True, "can_edit": True},
            "api/state",
            {"config": "挂机-云"},
            method="POST",
            body={"key": "Alas.Emulator.Serial"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "alas settings denied")

    def test_user_bound_config_allowed_and_filtered(self):
        decision = proxy_decision(
            {"role": "user"},
            {"config_name": "挂机-云"},
            "",
            {"config": "挂机-云"},
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.filtered)
        self.assertEqual(decision.config_name, "挂机-云")

    def test_filter_user_html_filters_visible_text_without_mutating_scripts(self):
        result = filter_user_html(
            "<nav>挂机-云 其它配置 管理入口</nav><script>const label = '其它配置 管理入口';</script>",
            "挂机-云",
        )

        self.assertIn("挂机-云", result)
        self.assertNotIn("<nav>挂机-云 其它配置 管理入口</nav>", result)
        self.assertIn("<nav>挂机-云  入口</nav>", result)
        self.assertIn("<script>const label = '其它配置 管理入口';</script>", result)
        self.assertIn("data-scrcpygate-alas-bind", result)

    def test_filter_user_html_escapes_config_name_in_comment(self):
        result = filter_user_html("<main></main>", "x--> <script>")

        self.assertNotIn("x--> <script>", result)
        self.assertIn("x--&gt; &lt;script&gt;", result)
        self.assertNotIn("<!-- bound ALAS config: x--> <script> -->", result)

    def test_filter_user_html_escapes_angle_brackets_in_config_comment(self):
        result = filter_user_html("<div>empty</div>", "<bad>")

        self.assertNotIn("<bad>", result)
        self.assertIn("&lt;bad&gt;", result)

    def test_filter_user_html_injects_request_patch_and_click_blocker(self):
        result = filter_user_html("<html><body></body></html>", "3256475495")

        self.assertIn("data-scrcpygate-alas-bind", result)
        self.assertIn("patchUrl", result)
        self.assertIn("configKeys", result)
        self.assertIn("blocksSensitiveEvent", result)
        self.assertIn("maskSensitiveText", result)
        self.assertIn("closestActionable", result)
        self.assertNotIn("hideSensitiveNavigation", result)
        self.assertNotIn("textLooksLikeOtherConfig", result)
        self.assertNotIn("filterBoundConfigRail", result)
        self.assertNotIn("data-scrcpygate-filtered-config", result)
        self.assertNotIn("data-scrcpygate-hidden-config", result)
        self.assertNotIn("data-scrcpygate-hidden-alas-settings", result)
        self.assertNotIn("data-scrcpygate-hidden-sensitive-device", result)
        self.assertNotIn("filterConfigRail", result)
        self.assertNotIn("filterAlasSettings", result)

    def test_filter_user_html_proxies_alas_root_static_asset_paths(self):
        result = filter_user_html("<html><body></body></html>", "3256475495")

        self.assertIn('pathname.indexOf("/static/") === 0', result)
        self.assertIn('pathname.indexOf("/assets/") === 0', result)
        self.assertIn('pathname === "/favicon.ico"', result)
        self.assertIn("url.pathname = proxyPrefix +", result)

    def test_denied_page_html_redirects_back_to_bound_alas(self):
        result = denied_page_html("无权访问 ALAS 管理入口", "/alas/embed/proxy/?config=3256475495", seconds=3)

        self.assertIn("此入口不可访问", result)
        self.assertIn("无权访问 ALAS 管理入口", result)
        self.assertIn('content="3;url=/alas/embed/proxy/?config=3256475495"', result)
        self.assertIn("window.location.replace", result)

    def test_filter_user_html_masks_adb_endpoint(self):
        result = filter_user_html("<main>Serial 192.0.2.10:30100</main>", "挂机-云")

        self.assertNotIn("192.0.2.10:30100", result)
        self.assertIn("已隐藏", result)

    def test_filter_user_json_payload_keeps_only_bound_config_list_entries(self):
        payload = {"configs": ["挂机-云", "其它"], "nested": {"config_list": [{"name": "挂机-云"}, {"name": "其它"}]}}

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["configs"], ["挂机-云"])
        self.assertEqual(result["nested"]["config_list"], [{"name": "挂机-云"}])

    def test_filter_user_json_payload_removes_alas_settings_menu_entries(self):
        payload = {
            "menus": [
                {"name": "Alas", "page": "setting", "tasks": ["Alas", "General", "Restart"]},
                {"name": "Farm", "page": "setting", "tasks": ["Main"]},
            ]
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["menus"], [{"name": "Farm", "page": "setting", "tasks": ["Main"]}])

    def test_filter_user_json_payload_removes_alas_settings_label_entries(self):
        payload = {
            "buttons": [
                {"label": "Alas设置", "value": "Alas"},
                {"label": "任务设置", "value": "Task"},
            ]
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["buttons"], [{"label": "任务设置", "value": "Task"}])

    def test_filter_user_json_payload_removes_restricted_left_navigation(self):
        payload = {
            "menus": [
                {"label": "主页", "value": "home"},
                {"label": "配置", "value": "config"},
                {"label": "管理", "value": "Manage"},
                {"label": "工具", "value": "Tools"},
                {"label": "出击", "value": "Campaign"},
            ]
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(
            result["menus"],
            [{"label": "主页", "value": "home"}, {"label": "出击", "value": "Campaign"}],
        )

    def test_filter_user_json_payload_keeps_restricted_words_in_locale_dict(self):
        payload = {
            "locale": {
                "home": "主页",
                "config": "配置",
                "manage": "管理",
                "tools": "工具",
            },
            "ok": True,
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result, payload)

    def test_filter_user_json_payload_removes_pywebio_alas_settings_items(self):
        payload = {
            "command": "output",
            "spec": {
                "items": [
                    {"label": "Alas Settings", "value": "Alas"},
                    {"label": "Restart", "value": "Restart"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["spec"]["items"], [{"label": "Restart", "value": "Restart"}])

    def test_filter_user_json_payload_keeps_normal_alas_buttons(self):
        payload = {
            "command": "output",
            "scope": "Alas",
            "spec": {
                "items": [
                    {"label": "Restart", "value": "Restart"},
                    {"label": "任务总览", "value": "dashboard"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(
            result["spec"]["items"],
            [{"label": "Restart", "value": "Restart"}, {"label": "任务总览", "value": "dashboard"}],
        )

    def test_filter_user_json_payload_filters_pywebio_config_option_items(self):
        payload = {
            "command": "output",
            "spec": {
                "items": [
                    {"label": "3256475495", "value": "3256475495"},
                    {"label": "13361966861", "value": "13361966861"},
                    {"label": "alas", "value": "alas"},
                    {"label": "Restart", "value": "Restart"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "3256475495")

        self.assertEqual(
            result["spec"]["items"],
            [{"label": "3256475495", "value": "3256475495"}, {"label": "Restart", "value": "Restart"}],
        )

    def test_filter_user_json_payload_filters_pywebio_string_config_items(self):
        payload = {"command": "output", "spec": {"items": ["3256475495", "13361966861", "alas", "Restart"]}}

        result = filter_user_json_payload(payload, "3256475495")

        self.assertEqual(result["spec"]["items"], ["3256475495", "Restart"])

    def test_filter_user_json_payload_filters_named_config_options_in_bound_group(self):
        payload = {
            "command": "output",
            "spec": {
                "items": [
                    {"label": "user-a", "value": "user-a"},
                    {"label": "user-b", "value": "user-b"},
                    {"label": "Restart", "value": "Restart"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "user-a")

        self.assertEqual(
            result["spec"]["items"],
            [{"label": "user-a", "value": "user-a"}, {"label": "Restart", "value": "Restart"}],
        )

    def test_filter_user_json_payload_filters_config_keyed_dict(self):
        payload = {
            "data": {
                "3256475495": {"status": "running"},
                "13361966861": {"status": "idle"},
                "status": "ok",
            }
        }

        result = filter_user_json_payload(payload, "3256475495")

        self.assertEqual(result["data"], {"3256475495": {"status": "running"}, "status": "ok"})

    def test_filter_user_json_payload_keeps_short_numeric_dropdown_items(self):
        payload = {
            "command": "output",
            "spec": {
                "items": [
                    {"label": "3256475495", "value": "3256475495"},
                    {"label": "1-1", "value": "1-1"},
                    {"label": "普通", "value": "normal"},
                    {"label": "13361966861", "value": "13361966861"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "3256475495")

        self.assertEqual(
            result["spec"]["items"],
            [
                {"label": "3256475495", "value": "3256475495"},
                {"label": "1-1", "value": "1-1"},
                {"label": "普通", "value": "normal"},
            ],
        )

    def test_filter_user_json_payload_removes_update_notice_items(self):
        payload = {
            "command": "output",
            "spec": {
                "items": [
                    {"label": "有更新可用，点击这里进行更新", "value": "update"},
                    {"label": "任务总览", "value": "dashboard"},
                ]
            },
        }

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["spec"]["items"], [{"label": "任务总览", "value": "dashboard"}])

    def test_filter_user_json_payload_masks_nested_adb_endpoint(self):
        payload = {"settings": {"Serial": "192.0.2.10:30100"}}

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["settings"]["Serial"], "已隐藏")

    def test_filter_user_websocket_downstream_filters_config_list(self):
        message = '{"configs":["挂机-云","其它"],"status":"ok"}'

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(json.loads(result), {"configs": ["挂机-云"], "status": "ok"})

    def test_filter_user_websocket_downstream_drops_single_other_config_option(self):
        message = json.dumps({"label": "13361966861", "value": "13361966861"}, ensure_ascii=False)

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertIsNone(result)

    def test_filter_user_websocket_downstream_keeps_pywebio_pin_registration(self):
        message = json.dumps(
            {
                "command": "pin_onchange",
                "spec": {
                    "name": "Alas_Emulator_Serial",
                    "callback_id": "CB-put_queue-test",
                    "clear": False,
                },
                "task_id": "index-test",
            },
            ensure_ascii=False,
        )

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertEqual(json.loads(result), json.loads(message))

    def _alas_instance_sidebar_item(self, label, index=1):
        return {
            "type": "custom_widget",
            "data": {
                "contents": [
                    {
                        "type": "html",
                        "content": '<svg class="aside-icon icon-run"></svg>',
                        "scope": f"#pywebio-scope-alas-instance-{index}",
                    },
                    {
                        "type": "buttons",
                        "callback_id": f"callback-{index}",
                        "buttons": [
                            {
                                "label": label,
                                "value": 0,
                                "color": "aside",
                            }
                        ],
                        "scope": f"#pywebio-scope-alas-instance-{index}",
                        "style": f";z-index: 2; --aside-{label}--;",
                    },
                ]
            },
        }

    def _alas_instance_sidebar_message(self, *labels):
        return json.dumps(
            {
                "command": "output",
                "spec": {
                    "type": "custom_widget",
                    "data": {
                        "contents": [
                            self._alas_instance_sidebar_item(label, index)
                            for index, label in enumerate(labels, start=1)
                        ]
                    },
                },
            },
            ensure_ascii=False,
        )

    def test_filter_user_websocket_downstream_drops_other_alas_instance_button(self):
        message = self._alas_instance_sidebar_message("13361966861")

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertIsNotNone(result)
        self.assertEqual(json.loads(result)["spec"]["data"]["contents"], [])

    def test_filter_user_websocket_downstream_drops_default_alas_instance_button(self):
        message = self._alas_instance_sidebar_message("alas")

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertIsNotNone(result)
        self.assertEqual(json.loads(result)["spec"]["data"]["contents"], [])

    def test_filter_user_websocket_downstream_keeps_bound_alas_instance_button(self):
        message = self._alas_instance_sidebar_message("3256475495")

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertIsNotNone(result)
        self.assertEqual(json.loads(result), json.loads(message))

    def test_filter_user_websocket_downstream_filters_mixed_alas_instance_buttons(self):
        message = self._alas_instance_sidebar_message("3256475495", "13361966861", "alas")

        result = filter_user_websocket_downstream(message, "3256475495")

        self.assertIsNotNone(result)
        contents = json.loads(result)["spec"]["data"]["contents"]
        self.assertEqual(contents, [self._alas_instance_sidebar_item("3256475495", 1)])
        serialized = json.dumps(contents, ensure_ascii=False)
        self.assertNotIn("13361966861", serialized)
        self.assertNotIn("--aside-alas--", serialized)

    def test_filter_user_websocket_downstream_filters_alas_settings_payload(self):
        result = filter_user_websocket_downstream('{"menu":"Alas","task":"Alas"}', "挂机-云")

        self.assertEqual(json.loads(result), {})

    def test_filter_user_websocket_downstream_filters_mixed_alas_settings_menu(self):
        message = json.dumps(
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

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(
            json.loads(result),
            {"command": "output", "spec": {"items": [{"label": "Restart", "value": "Restart"}]}},
        )

    def test_filter_user_websocket_downstream_filters_restricted_left_navigation(self):
        message = json.dumps(
            {
                "command": "output",
                "spec": {
                    "items": [
                        {"label": "主页", "value": "home"},
                        {"label": "管理", "value": "Manage"},
                        {"label": "工具", "value": "Tools"},
                        {"label": "出击", "value": "Campaign"},
                    ]
                },
            },
            ensure_ascii=False,
        )

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(
            json.loads(result),
            {
                "command": "output",
                "spec": {"items": [{"label": "主页", "value": "home"}, {"label": "出击", "value": "Campaign"}]},
            },
        )

    def test_filter_user_websocket_downstream_drops_restricted_single_menu_buttons(self):
        for label, style in (
            ("更新器", ";--menu-Update--"),
            ("远程控制", ";--menu-Remote--"),
            ("工具", ";--menu-Utils--"),
        ):
            with self.subTest(label=label):
                message = json.dumps(
                    {
                        "command": "output",
                        "spec": {
                            "type": "buttons",
                            "callback_id": f"callback-{label}",
                            "buttons": [
                                {"label": label, "value": 0, "color": "menu", "disabled": False}
                            ],
                            "scope": "#pywebio-scope-menu",
                            "position": -1,
                            "style": style,
                        },
                    },
                    ensure_ascii=False,
                )

                self.assertIsNone(filter_user_websocket_downstream(message, "3256475495"))

    def test_filter_user_websocket_downstream_drops_restricted_custom_widget_group(self):
        message = json.dumps(
            {
                "command": "output",
                "spec": {
                    "type": "custom_widget",
                    "template": "<details><summary>{{title}}</summary>{{#contents}}{{& pywebio_output_parse}}{{/contents}}</details>",
                    "data": {
                        "title": "工具",
                        "contents": [
                            {
                                "type": "buttons",
                                "callback_id": "callback-daemon",
                                "buttons": [{"label": "半自动点击", "value": 0, "color": "menu"}],
                                "scope": "#pywebio-scope-menu",
                                "style": ";--menu-Daemon--",
                            },
                            {
                                "type": "buttons",
                                "callback_id": "callback-benchmark",
                                "buttons": [{"label": "性能测试", "value": 0, "color": "menu"}],
                                "scope": "#pywebio-scope-menu",
                                "style": ";--menu-Benchmark--",
                            },
                        ],
                    },
                    "scope": "#pywebio-scope-menu",
                },
            },
            ensure_ascii=False,
        )

        self.assertIsNone(filter_user_websocket_downstream(message, "3256475495"))

    def test_filter_user_websocket_downstream_keeps_locale_dict_with_config_label(self):
        message = json.dumps(
            {
                "command": "output",
                "spec": {
                    "locale": {
                        "home": "主页",
                        "config": "配置",
                        "manage": "管理",
                        "tools": "工具",
                    }
                },
            },
            ensure_ascii=False,
        )

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(json.loads(result), json.loads(message))

    def test_filter_user_websocket_downstream_keeps_pywebio_alas_scope_output(self):
        message = json.dumps(
            {
                "command": "output",
                "scope": "Alas",
                "spec": {"content": "任务总览", "config": "挂机-云"},
            },
            ensure_ascii=False,
        )

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(
            json.loads(result),
            {"command": "output", "scope": "Alas", "spec": {"content": "任务总览", "config": "挂机-云"}},
        )

    def test_filter_user_websocket_downstream_drops_update_notice(self):
        message = json.dumps(
            {
                "command": "toast",
                "content": "有更新可用，点击这里进行更新",
                "action": "update",
            },
            ensure_ascii=False,
        )

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertIsNone(result)

    def test_filter_user_websocket_downstream_rejects_alas_settings_text(self):
        result = filter_user_websocket_downstream("open Alas设置", "挂机-云")

        self.assertIsNone(result)

    def test_filter_user_websocket_downstream_masks_adb_endpoint(self):
        result = filter_user_websocket_downstream('{"serial":"192.0.2.10:30100"}', "挂机-云")

        self.assertEqual(json.loads(result), {"serial": "已隐藏"})

    def test_filter_user_websocket_downstream_rejects_other_config(self):
        result = filter_user_websocket_downstream('{"config":"其它"}', "挂机-云")

        self.assertIsNone(result)


class AlasEmbedPyWebIOSessionPolicyTests(unittest.TestCase):
    """验证每连接 PyWebIO 回调和输入任务授权状态。"""

    config_name = "3256475495"

    def observe(self, policy, payload):
        original = json.dumps(payload, ensure_ascii=False)
        filtered = filter_user_websocket_downstream(original, self.config_name)
        observation = policy.observe_downstream(original, filtered)
        return filtered, observation

    @staticmethod
    def evaluate(policy, payload):
        return policy.evaluate_upstream(json.dumps(payload, ensure_ascii=False))

    def register_input(self, policy, task_id="input-task", *fields):
        payload = {
            "command": "input_group",
            "task_id": task_id,
            "spec": {
                "inputs": [
                    {"name": field, "label": f"Field {index}"}
                    for index, field in enumerate(fields or ("ordinary_name",), start=1)
                ]
            },
        }
        return self.observe(policy, payload)

    def test_registered_input_event_treats_name_as_control_name(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        filtered, observation = self.register_input(policy, "input-task", "ordinary_name")

        decision = self.evaluate(
            policy,
            {
                "event": "input_event",
                "task_id": "input-task",
                "data": {
                    "event_name": "change",
                    "name": "ordinary_name",
                    "value": "13361966861",
                },
            },
        )

        self.assertIsNotNone(filtered)
        self.assertTrue(observation.forwarded)
        self.assertEqual(observation.reason, "forwarded")
        self.assertEqual(
            policy.tasks["input-task"],
            PyWebIOTaskRegistration(kind="input", fields=frozenset({"ordinary_name"})),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "input_event_allowed")

    def test_input_event_requires_edit_permission(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_edit=False)
        self.register_input(policy, "input-task", "ordinary_name")

        decision = self.evaluate(
            policy,
            {
                "event": "input_event",
                "task_id": "input-task",
                "data": {
                    "event_name": "blur",
                    "name": "ordinary_name",
                    "value": "safe",
                },
            },
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "edit_permission_denied")
        self.assertEqual(decision.permission, "edit")

    def test_from_submit_allows_only_registered_safe_fields(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        self.register_input(policy, "input-task", "ordinary_name", "region")

        allowed = self.evaluate(
            policy,
            {
                "event": "from_submit",
                "task_id": "input-task",
                "data": {"ordinary_name": "fleet", "region": "cn"},
            },
        )
        denied = self.evaluate(
            policy,
            {
                "event": "from_submit",
                "task_id": "input-task",
                "data": {"ordinary_name": "fleet", "unregistered": "value"},
            },
        )

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason, "submit_allowed")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "unknown_input_field")

    def test_explicit_config_selector_allows_bound_value_and_rejects_mismatch(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        self.register_input(policy, "config-task", "config")

        allowed = self.evaluate(
            policy,
            {
                "event": "from_submit",
                "task_id": "config-task",
                "data": {"config": self.config_name},
            },
        )
        denied = self.evaluate(
            policy,
            {
                "event": "from_submit",
                "task_id": "config-task",
                "data": {"config": "13361966861"},
            },
        )

        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "config_mismatch")

    def test_from_cancel_needs_registered_input_but_not_edit_permission(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_edit=False)
        self.register_input(policy, "input-task", "ordinary_name")

        allowed = self.evaluate(
            policy,
            {"event": "from_cancel", "task_id": "input-task", "data": None},
        )
        denied = self.evaluate(
            policy,
            {"event": "from_cancel", "task_id": "unknown-task", "data": None},
        )

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason, "cancel_allowed")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "unknown_task_id")

    def test_unknown_task_structured_data_closes_on_explicit_violation(self):
        policy = PyWebIOSessionPolicy(self.config_name)

        cases = (
            (
                {
                    "event": "from_submit",
                    "task_id": "unknown-config-task",
                    "data": {"config": "other-config"},
                },
                "config_mismatch",
            ),
            (
                {
                    "event": "from_cancel",
                    "task_id": "unknown-management-task",
                    "data": {"command": "management"},
                },
                "management_denied",
            ),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                decision = self.evaluate(policy, payload)
                self.assertEqual(decision.action, WebSocketMessageAction.CLOSE)
                self.assertTrue(decision.closes_connection)
                self.assertEqual(decision.reason, reason)

        scalar = self.evaluate(
            policy,
            {
                "event": "from_cancel",
                "task_id": "unknown-scalar-task",
                "data": "ordinary management and config business text",
            },
        )
        self.assertEqual(scalar.action, WebSocketMessageAction.DROP)
        self.assertEqual(scalar.reason, "unknown_task_id")

    def test_registered_js_yield_does_not_scan_business_result_text(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_run=False, can_edit=False)
        for command in ("pin_value", "pin_wait", "run_script"):
            task_id = f"yield-{command}"
            spec = {"name": "status_pin"} if command != "run_script" else {"code": "return 1"}
            self.observe(
                policy,
                {"command": command, "task_id": task_id, "spec": spec},
            )
            decision = self.evaluate(
                policy,
                {
                    "event": "js_yield",
                    "task_id": task_id,
                    "data": {
                        "message": "ordinary config and settings status text",
                        "config": "this is business text, not a selector",
                    },
                },
            )

            with self.subTest(command=command):
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.reason, "yield_allowed")

    def test_safe_callback_is_allowed_and_filtered_callback_is_denied(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        filtered, _observation = self.observe(
            policy,
            {
                "command": "output",
                "spec": {
                    "items": [
                        {"label": "主页", "value": "home", "callback_id": "safe-callback"},
                        {"label": "管理", "value": "Manage", "callback_id": "blocked-callback"},
                    ]
                },
            },
        )

        safe = self.evaluate(
            policy,
            {"event": "callback", "task_id": "safe-callback", "data": 0},
        )
        blocked = self.evaluate(
            policy,
            {"event": "callback", "task_id": "blocked-callback", "data": 0},
        )
        explicit_violation = self.evaluate(
            policy,
            {
                "event": "callback",
                "task_id": "safe-callback",
                "data": {"config": "13361966861"},
            },
        )

        self.assertNotIn("blocked-callback", filtered)
        self.assertTrue(safe.allowed)
        self.assertEqual(safe.permission, "navigation")
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "denied_callback_id")
        self.assertEqual(explicit_violation.action, WebSocketMessageAction.CLOSE)
        self.assertEqual(explicit_violation.reason, "config_mismatch")

    def test_denied_callback_id_wins_if_later_reused_by_safe_output(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {"items": [{"label": "管理", "value": "Manage", "callback_id": "reused-id"}]},
            },
        )
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {"items": [{"label": "主页", "value": "home", "callback_id": "reused-id"}]},
            },
        )

        decision = self.evaluate(
            policy,
            {"event": "callback", "task_id": "reused-id", "data": 0},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "denied_callback_id")

    def test_run_callback_obeys_run_permission_category(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_run=False)
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {
                    "type": "buttons",
                    "callback_id": "run-callback",
                    "buttons": [{"label": "Start", "value": "start"}],
                },
            },
        )

        decision = self.evaluate(
            policy,
            {"event": "callback", "task_id": "run-callback", "data": "start"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "run_permission_denied")
        self.assertEqual(decision.permission, "run")

    def test_edit_callback_obeys_edit_permission_category(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_edit=False)
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {
                    "type": "buttons",
                    "callback_id": "edit-callback",
                    "buttons": [{"label": "Save", "value": "save"}],
                },
            },
        )

        decision = self.evaluate(
            policy,
            {"event": "callback", "task_id": "edit-callback", "data": "save"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "edit_permission_denied")
        self.assertEqual(decision.permission, "edit")

    def test_safe_callback_structured_data_obeys_action_permissions(self):
        policy = PyWebIOSessionPolicy(self.config_name, can_run=False, can_edit=False)
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {
                    "type": "buttons",
                    "callback_id": "safe-callback",
                    "buttons": [{"label": "Home", "value": "home"}],
                },
            },
        )

        cases = (
            ({"action": "start"}, "run_permission_denied", "run"),
            ({"method": "settings.save"}, "edit_permission_denied", "edit"),
        )
        for data, reason, permission in cases:
            with self.subTest(reason=reason):
                decision = self.evaluate(
                    policy,
                    {"event": "callback", "task_id": "safe-callback", "data": data},
                )
                self.assertEqual(decision.action, WebSocketMessageAction.CLOSE)
                self.assertEqual(decision.reason, reason)
                self.assertEqual(decision.permission, permission)

    def test_safe_callback_scalar_sensitive_labels_are_closed(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        self.observe(
            policy,
            {
                "command": "output",
                "spec": {
                    "type": "buttons",
                    "callback_id": "safe-callback",
                    "buttons": [{"label": "Home", "value": "home"}],
                },
            },
        )

        cases = (
            ("Manage", "management_denied"),
            ("alas.config_list", "management_denied"),
            ("Remote", "restricted_entry_denied"),
            ("Alas", "alas_settings_denied"),
            ("Alas.Emulator.Serial", "alas_settings_denied"),
        )
        for data, reason in cases:
            with self.subTest(data=data):
                decision = self.evaluate(
                    policy,
                    {"event": "callback", "task_id": "safe-callback", "data": data},
                )
                self.assertEqual(decision.action, WebSocketMessageAction.CLOSE)
                self.assertEqual(decision.reason, reason)

        home = self.evaluate(
            policy,
            {"event": "callback", "task_id": "safe-callback", "data": "home"},
        )
        self.assertEqual(home.action, WebSocketMessageAction.FORWARD)
        self.assertEqual(home.reason, "callback_allowed")

    def test_sensitive_pin_registration_is_forwarded_but_callback_is_denied(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        filtered, observation = self.observe(
            policy,
            {
                "command": "pin_onchange",
                "task_id": "index-task",
                "spec": {
                    "name": "Alas_Emulator_Serial",
                    "callback_id": "sensitive-pin-callback",
                    "clear": False,
                },
            },
        )

        decision = self.evaluate(
            policy,
            {"event": "callback", "task_id": "sensitive-pin-callback", "data": "changed"},
        )

        self.assertIsNotNone(filtered)
        self.assertTrue(observation.forwarded)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "denied_callback_id")

    def test_unknown_protocol_identifiers_drop_but_malformed_frames_close(self):
        policy = PyWebIOSessionPolicy(self.config_name)
        self.register_input(policy, "input-task", "ordinary_name")

        cases = [
            (
                {"event": "callback", "task_id": "unknown-callback", "data": 0},
                "unknown_callback_id",
            ),
            (
                {"event": "future_event", "task_id": "future-task", "data": {}},
                "unknown_protocol_event",
            ),
            (
                {"event": "from_cancel", "task_id": "unknown-task", "data": None},
                "unknown_task_id",
            ),
            (
                {"event": "js_yield", "task_id": "input-task", "data": None},
                "task_kind_mismatch",
            ),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason):
                decision = self.evaluate(policy, payload)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.action, WebSocketMessageAction.DROP)
                self.assertFalse(decision.closes_connection)
                self.assertEqual(decision.reason, reason)

        malformed = policy.evaluate_upstream("{")
        invalid_binary = policy.evaluate_upstream(b"file-upload-data")
        missing_id = self.evaluate(policy, {"event": "callback", "data": 0})
        missing_event = self.evaluate(policy, {"event": "", "task_id": "future-task"})
        unknown_event_only = self.evaluate(policy, {"event": "future_event"})
        unknown_event_data_without_id = self.evaluate(
            policy,
            {"event": "future_event", "data": {"value": "safe"}},
        )
        protocol_fields_without_event = self.evaluate(
            policy,
            {"task_id": "future-task", "data": {}},
        )
        data_without_event = self.evaluate(policy, {"data": {}})
        legacy_dict = self.evaluate(policy, {"message": "legacy status"})
        for decision, reason in (
            (malformed, "invalid_json"),
            (invalid_binary, "invalid_binary"),
            (missing_id, "missing_task_id"),
            (missing_event, "missing_event"),
            (unknown_event_only, "missing_task_id"),
            (unknown_event_data_without_id, "missing_task_id"),
            (protocol_fields_without_event, "missing_event"),
            (data_without_event, "missing_event"),
        ):
            with self.subTest(reason=reason):
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.action, WebSocketMessageAction.CLOSE)
                self.assertTrue(decision.closes_connection)
                self.assertEqual(decision.reason, reason)
        self.assertEqual(protocol_fields_without_event.event, "unknown")
        self.assertEqual(data_without_event.event, "unknown")
        self.assertEqual(legacy_dict.action, WebSocketMessageAction.FORWARD)
        self.assertEqual(legacy_dict.reason, "structured_message_allowed")


class AlasEmbedWebSocketPolicyTests(unittest.TestCase):
    """验证 WebSocket 消息权限兜底。"""

    def test_message_with_run_command_denied_when_can_run_false(self):
        """can_run=False 时明显运行类 WebSocket 消息会被拒绝。"""
        self.assertFalse(
            websocket_message_allowed(
                '{"action":"start","config":"挂机-云"}',
                "挂机-云",
                can_run=False,
                can_edit=True,
            )
        )

    def test_message_with_edit_command_denied_when_can_edit_false(self):
        """can_edit=False 时明显编辑类 WebSocket 消息会被拒绝。"""
        self.assertFalse(
            websocket_message_allowed(
                '{"method":"settings.save","config":"挂机-云"}',
                "挂机-云",
                can_run=True,
                can_edit=False,
            )
        )

    def test_message_readonly_allowed_when_run_and_edit_denied(self):
        """can_run/can_edit 均为 False 时只读 WebSocket 消息允许。"""
        self.assertTrue(
            websocket_message_allowed(
                '{"event":"status","config":"挂机-云"}',
                "挂机-云",
                can_run=False,
                can_edit=False,
            )
        )

    def test_message_with_other_config_is_denied(self):
        """包含其它配置名的 WebSocket 文本消息会被拒绝。"""
        self.assertFalse(websocket_message_allowed('{"config":"其它"}', "挂机-云"))

    def test_nested_dict_message_with_other_config_is_denied(self):
        """嵌套字典中的其它配置名会被拒绝。"""
        self.assertFalse(
            websocket_message_allowed('{"params":{"config":"其它"}}', "挂机-云")
        )

    def test_nested_dict_message_with_bound_config_is_allowed(self):
        """嵌套字典中的绑定配置名会被允许。"""
        self.assertTrue(
            websocket_message_allowed('{"params":{"config":"挂机-云"}}', "挂机-云")
        )

    def test_list_message_with_other_config_is_denied(self):
        """列表内嵌字典中的其它配置名会被拒绝。"""
        self.assertFalse(
            websocket_message_allowed('[{"config":"挂机-云"},{"config":"其它"}]', "挂机-云")
        )

    def test_message_with_bound_config_is_allowed(self):
        """包含绑定配置名的 WebSocket 文本消息会被允许。"""
        self.assertTrue(websocket_message_allowed('{"config":"挂机-云"}', "挂机-云"))

    def test_message_with_management_operation_is_denied(self):
        """普通用户 WebSocket 文本消息尝试管理操作时会被拒绝。"""
        self.assertFalse(websocket_message_allowed('{"event":"alas.config_list"}', "挂机-云"))

    def test_message_with_management_operation_case_variant_is_denied(self):
        """管理操作字段和值的大小写变体会被拒绝。"""
        cases = [
            '{"Action":"Manage"}',
            '{"method":"ALAS.CONFIG_LIST"}',
        ]
        for message in cases:
            with self.subTest(message=message):
                self.assertFalse(websocket_message_allowed(message, "挂机-云"))

    def test_message_with_home_route_is_allowed(self):
        """普通用户 WebSocket 消息允许切到 ALAS 原首页。"""
        self.assertTrue(websocket_message_allowed('{"route":"home","config":"挂机-云"}', "挂机-云"))

    def test_message_with_alas_settings_task_is_denied(self):
        """普通用户 WebSocket 消息不能切到 ALAS -> ALAS 设置页。"""
        self.assertFalse(
            websocket_message_allowed(
                '{"menu":"Alas","task":"Alas","config":"挂机-云"}',
                "挂机-云",
            )
        )

    def test_message_with_alas_emulator_setting_is_denied(self):
        """普通用户 WebSocket 消息不能读取 ALAS 模拟器设置字段。"""
        self.assertFalse(
            websocket_message_allowed(
                '{"key":"Alas.Emulator.Serial","config":"挂机-云"}',
                "挂机-云",
            )
        )

    def test_message_with_manage_text_outside_command_fields_is_allowed(self):
        """普通文本字段包含管理字样时不应误拒绝。"""
        cases = [
            '{"message":"please manage my fleet"}',
            '{"message":"请帮我管理舰队"}',
        ]
        for message in cases:
            with self.subTest(message=message):
                self.assertTrue(websocket_message_allowed(message, "挂机-云"))

    def test_plain_text_management_command_is_denied(self):
        """非 JSON 文本包含明显管理路径或命令时会被拒绝。"""
        cases = ["GET /admin HTTP/1.1", "POST /manage", "alas.config_list"]
        for message in cases:
            with self.subTest(message=message):
                self.assertFalse(websocket_message_allowed(message, "挂机-云"))

    def test_plain_text_normal_content_is_allowed(self):
        """非 JSON 普通文本不应被误杀。"""
        self.assertTrue(websocket_message_allowed("ping", "挂机-云"))

    def test_plain_text_management_word_is_allowed(self):
        """非 JSON 普通文本仅包含管理字样时不应误杀。"""
        self.assertTrue(websocket_message_allowed("这是一条包含管理二字的普通文本", "挂机-云"))

    def test_bytes_bound_config_json_is_allowed(self):
        """UTF-8 JSON bytes 且仅访问绑定配置时允许。"""
        self.assertTrue(websocket_message_allowed('{"config":"挂机-云"}'.encode("utf-8"), "挂机-云"))

    def test_bytes_other_config_json_is_denied(self):
        """UTF-8 JSON bytes 访问其它配置时拒绝。"""
        self.assertFalse(websocket_message_allowed('{"config":"其它"}'.encode("utf-8"), "挂机-云"))

    def test_unparseable_bytes_message_is_denied(self):
        """无法安全解析的 bytes 消息按保守策略拒绝。"""
        self.assertFalse(websocket_message_allowed(b"\xff\xfe", "挂机-云"))


if __name__ == "__main__":
    unittest.main()
