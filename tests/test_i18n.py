import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import i18n, main, video_options


ROOT = Path(__file__).resolve().parents[1]


class I18nCatalogTests(unittest.TestCase):
    def test_default_catalog_has_no_duplicate_object_keys(self):
        duplicates = []

        def collect_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    duplicates.append(key)
                result[key] = value
            return result

        catalog_path = ROOT / "static" / "i18n" / "zh-CN.json"
        json.loads(catalog_path.read_text(encoding="utf-8"), object_pairs_hook=collect_pairs)

        self.assertEqual(duplicates, [])

    def test_default_catalog_resolves_nested_messages(self):
        self.assertEqual(i18n.DEFAULT_LOCALE, "zh-CN")
        self.assertEqual(i18n.translate("login.username"), "用户名")
        self.assertEqual(i18n.resolve_message("common.theme.system"), "跟随系统")

    def test_missing_key_remains_visible(self):
        self.assertIsNone(i18n.resolve_message("missing.translation"))
        self.assertEqual(i18n.translate("missing.translation"), "missing.translation")

    def test_interpolation_preserves_unknown_placeholders(self):
        self.assertEqual(
            i18n.translate("alas.denied.auto_return", seconds=7),
            "7 秒后自动返回。",
        )
        self.assertEqual(
            i18n.translate("alas.denied.auto_return", unrelated="value"),
            "{seconds} 秒后自动返回。",
        )

    def test_interpolation_only_replaces_simple_identifiers(self):
        catalog = {
            "broken_open": "损坏 {",
            "conversion": "保留 {name!r}",
            "field_access": "保留 {name.value}",
            "valid": "替换 {name}",
        }
        with tempfile.TemporaryDirectory() as directory:
            locale_dir = Path(directory)
            (locale_dir / "zh-CN.json").write_text(
                json.dumps(catalog, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                with patch.object(i18n, "LOCALE_DIR", locale_dir):
                    i18n.clear_catalog_cache()
                    self.assertEqual(i18n.translate("broken_open", name="值"), "损坏 {")
                    self.assertEqual(i18n.translate("conversion", name="值"), "保留 {name!r}")
                    self.assertEqual(i18n.translate("field_access", name="值"), "保留 {name.value}")
                    self.assertEqual(i18n.translate("valid", name="值"), "替换 值")
            finally:
                i18n.clear_catalog_cache()

    def test_unsupported_locale_falls_back_to_default(self):
        self.assertEqual(i18n.normalize_locale("en-US"), i18n.DEFAULT_LOCALE)
        self.assertEqual(i18n.translate("login.password", "en-US"), "密码")
        self.assertEqual(i18n.browser_payload("en-US")["locale"], i18n.DEFAULT_LOCALE)

    def test_browser_payload_json_cannot_terminate_script_node(self):
        malicious = '</script><script>alert("owned")</script>&'
        with patch(
            "app.i18n.browser_payload",
            return_value={"locale": "zh-CN", "messages": {"unsafe": malicious}},
        ):
            encoded = i18n.browser_payload_json()

        self.assertNotIn("<", encoded)
        self.assertNotIn(">", encoded)
        self.assertNotIn("&", encoded)
        self.assertEqual(json.loads(encoded)["messages"]["unsafe"], malicious)

    def test_missing_or_invalid_catalog_degrades_to_visible_keys(self):
        cases = {
            "missing": None,
            "invalid-json": "{not-json",
            "invalid-root": "[]",
        }
        for name, content in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                locale_dir = Path(directory)
                if content is not None:
                    (locale_dir / "zh-CN.json").write_text(content, encoding="utf-8")
                try:
                    with patch.object(i18n, "LOCALE_DIR", locale_dir):
                        i18n.clear_catalog_cache()
                        self.assertEqual(i18n.load_catalog(), {})
                        self.assertEqual(i18n.translate("login.submit"), "login.submit")
                        self.assertEqual(i18n.browser_payload()["messages"], {})
                finally:
                    i18n.clear_catalog_cache()

    def test_all_static_translation_key_references_exist(self):
        source_paths = (
            "app/alas_embed.py",
            "app/devices.py",
            "app/main.py",
            "app/security.py",
            "app/video_options.py",
            "static/js/alas-shell.js",
            "static/js/admin.js",
            "static/js/login.js",
            "static/js/mirror.js",
            "static/js/ui-core.js",
            "templates/admin.html",
            "templates/index.html",
            "templates/login.html",
        )
        direct_call_pattern = re.compile(
            r"(?:i18n\.translate|t)\(\s*[\"']((?:common|login|alas|mirror|admin|server)(?:\.[a-z0-9_]+)+)[\"']",
            re.IGNORECASE,
        )
        quoted_key_pattern = re.compile(
            r"[\"']((?:common|login|alas|mirror|admin|server)(?:\.[a-z0-9_]+)+)[\"']",
            re.IGNORECASE,
        )
        references = set()
        for relative_path in source_paths:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            pattern = quoted_key_pattern if relative_path.startswith("static/js/") else direct_call_pattern
            references.update(pattern.findall(source))
        missing = sorted(key for key in references if i18n.resolve_message(key) is None)

        self.assertGreater(len(references), 20)
        self.maxDiff = None
        self.assertEqual(missing, [])

    def test_dynamic_alas_denial_message_keys_exist(self):
        keys = set(main.ALAS_EMBED_DENIED_MESSAGE_KEYS.values())
        keys.add(main.ALAS_EMBED_DENIED_DEFAULT_MESSAGE_KEY)

        self.assertGreater(len(keys), 8)
        self.assertEqual(sorted(key for key in keys if i18n.resolve_message(key) is None), [])

    def test_server_error_code_mappings_resolve_without_changing_codes(self):
        keys = set(main.SERVER_ERROR_MESSAGE_KEYS.values())
        self.assertEqual(sorted(key for key in keys if i18n.resolve_message(key) is None), [])
        self.assertEqual(main.server_error_message("invalid_username"), "用户名无效")
        self.assertEqual(
            main.server_error_message("Password must be at least 12 characters"),
            "密码至少需要 12 个字符",
        )
        self.assertEqual(main.server_error_message("third-party diagnostic"), "third-party diagnostic")

    def test_all_server_translation_key_references_exist(self):
        pattern = re.compile(r"[\"'](server(?:\.[a-z0-9_]+)+)[\"']", re.IGNORECASE)
        references = set()
        for relative_path in (
            "app/alas_embed.py",
            "app/devices.py",
            "app/main.py",
            "app/security.py",
            "app/video_options.py",
        ):
            references.update(pattern.findall((ROOT / relative_path).read_text(encoding="utf-8")))
        self.assertGreater(len(references), 40)
        self.assertEqual(sorted(key for key in references if i18n.resolve_message(key) is None), [])

    def test_video_option_errors_keep_internal_diagnostics_and_localize_at_boundary(self):
        with self.assertRaises(video_options.VideoOptionError) as raised:
            video_options.normalize_video_options({"max_size": "invalid"})
        self.assertEqual(str(raised.exception), "max_size must be integer")
        self.assertEqual(raised.exception.message_key, "server.video_error.must_be_integer")
        self.assertEqual(raised.exception.localized(), "max_size 必须是整数")

    def test_builtin_profile_labels_are_catalog_backed(self):
        source = (ROOT / "app" / "video_options.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[流畅稳定高清低延迟]")
        self.assertEqual(
            video_options.profile_label_payloads(),
            {"smooth": "流畅", "balanced": "稳定", "sharp": "高清", "low_latency": "低延迟"},
        )


class I18nBrowserRuntimeTests(unittest.TestCase):
    def test_browser_runtime_resolves_interpolates_and_exposes_missing_keys(self):
        source_path = ROOT / "static" / "js" / "i18n.js"
        payload = {
            "locale": "zh-CN",
            "messages": {
                "nested": {"message": "你好，{name}"},
                "object": {"not_a_message": {"value": "ignored"}},
            },
        }
        program = f"""
const fs = require("fs");
const vm = require("vm");
const payload = {json.dumps(payload, ensure_ascii=False)};
global.document = {{
  documentElement: {{}},
  getElementById(id) {{
    return id === "scrcpygate-i18n" ? {{ textContent: JSON.stringify(payload) }} : null;
  }}
}};
global.window = {{}};
vm.runInThisContext(fs.readFileSync({json.dumps(str(source_path))}, "utf8"));
process.stdout.write(JSON.stringify({{
  locale: window.ScrcpyGateI18n.locale,
  resolved: window.ScrcpyGateI18n.resolve("nested.message"),
  interpolated: window.ScrcpyGateI18n.t("nested.message", {{ name: "测试" }}),
  hasMessage: window.ScrcpyGateI18n.has("nested.message"),
  hasMissing: window.ScrcpyGateI18n.has("nested.missing"),
  missing: window.ScrcpyGateI18n.t("nested.missing"),
  object: window.ScrcpyGateI18n.resolve("object.not_a_message")
}}));
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["locale"], "zh-CN")
        self.assertEqual(result["resolved"], "你好，{name}")
        self.assertEqual(result["interpolated"], "你好，测试")
        self.assertTrue(result["hasMessage"])
        self.assertFalse(result["hasMissing"])
        self.assertEqual(result["missing"], "nested.missing")
        self.assertIsNone(result["object"])

    def test_browser_runtime_survives_missing_and_invalid_payloads(self):
        source_path = ROOT / "static" / "js" / "i18n.js"
        program = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
function execute(nodeText) {{
  const context = {{
    document: {{
      documentElement: {{}},
      getElementById() {{ return nodeText === null ? null : {{ textContent: nodeText }}; }}
    }},
    window: {{}}
  }};
  vm.runInNewContext(source, context);
  return {{
    locale: context.window.ScrcpyGateI18n.locale,
    translated: context.window.ScrcpyGateI18n.t("login.submit")
  }};
}}
process.stdout.write(JSON.stringify([execute(null), execute("{{not-json")]));
"""
        completed = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )

        self.assertEqual(
            json.loads(completed.stdout),
            [
                {"locale": "zh-CN", "translated": "login.submit"},
                {"locale": "zh-CN", "translated": "login.submit"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
