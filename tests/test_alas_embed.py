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

    def test_filter_user_html_hides_other_config_and_management_text(self):
        result = filter_user_html(
            "<nav>挂机-云 其它配置 管理入口</nav>",
            "挂机-云",
        )

        self.assertIn("挂机-云", result)
        self.assertNotIn("其它配置", result)
        self.assertNotIn("管理入口", result)

    def test_filter_user_html_escapes_config_name_in_comment(self):
        result = filter_user_html("<main></main>", "x--> <script>")

        self.assertNotIn("x--> <script>", result)
        self.assertIn("x--&gt; &lt;script&gt;", result)
        self.assertNotIn("<!-- bound ALAS config: x--> <script> -->", result)

    def test_filter_user_html_escapes_angle_brackets_in_config_comment(self):
        result = filter_user_html("<div>empty</div>", "<bad>")

        self.assertNotIn("<bad>", result)
        self.assertIn("&lt;bad&gt;", result)

    def test_filter_user_html_injects_config_rail_filter(self):
        result = filter_user_html("<html><body></body></html>", "3256475495")

        self.assertIn("data-scrcpygate-alas-bind", result)
        self.assertIn("data-scrcpygate-hidden-config", result)
        self.assertIn("data-scrcpygate-hidden-alas-settings", result)
        self.assertIn("data-scrcpygate-hidden-sensitive-device", result)
        self.assertIn("filterConfigRail", result)
        self.assertIn("filterAlasSettings", result)

    def test_denied_page_html_redirects_back_to_bound_alas(self):
        result = denied_page_html("无权访问 ALAS 管理入口", "/alas/embed/proxy/?config=3256475495", seconds=3)

        self.assertIn("此入口不可访问", result)
        self.assertIn("无权访问 ALAS 管理入口", result)
        self.assertIn('content="3;url=/alas/embed/proxy/?config=3256475495"', result)
        self.assertIn("window.location.replace", result)

    def test_filter_user_html_masks_adb_endpoint(self):
        result = filter_user_html("<main>Serial 10.0.1.30:30100</main>", "挂机-云")

        self.assertNotIn("10.0.1.30:30100", result)
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

    def test_filter_user_json_payload_masks_nested_adb_endpoint(self):
        payload = {"settings": {"Serial": "10.0.1.30:30100"}}

        result = filter_user_json_payload(payload, "挂机-云")

        self.assertEqual(result["settings"]["Serial"], "已隐藏")

    def test_filter_user_websocket_downstream_filters_config_list(self):
        message = '{"configs":["挂机-云","其它"],"status":"ok"}'

        result = filter_user_websocket_downstream(message, "挂机-云")

        self.assertEqual(json.loads(result), {"configs": ["挂机-云"], "status": "ok"})

    def test_filter_user_websocket_downstream_rejects_alas_settings_payload(self):
        result = filter_user_websocket_downstream('{"menu":"Alas","task":"Alas"}', "挂机-云")

        self.assertIsNone(result)

    def test_filter_user_websocket_downstream_rejects_alas_settings_text(self):
        result = filter_user_websocket_downstream("open Alas设置", "挂机-云")

        self.assertIsNone(result)

    def test_filter_user_websocket_downstream_masks_adb_endpoint(self):
        result = filter_user_websocket_downstream('{"serial":"10.0.1.30:30100"}', "挂机-云")

        self.assertEqual(json.loads(result), {"serial": "已隐藏"})

    def test_filter_user_websocket_downstream_rejects_other_config(self):
        result = filter_user_websocket_downstream('{"config":"其它"}', "挂机-云")

        self.assertIsNone(result)


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
