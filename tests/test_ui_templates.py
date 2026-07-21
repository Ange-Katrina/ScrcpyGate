import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import i18n


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


class TemplateParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.scripts = []
        self.stylesheets = []
        self.forms = []
        self.static_assets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "script":
            self.scripts.append(values)
            if values.get("src", "").startswith("/static/"):
                self.static_assets.append(values["src"])
        elif tag == "link" and "stylesheet" in (values.get("rel") or "").split():
            self.stylesheets.append(values)
            if values.get("href", "").startswith("/static/"):
                self.static_assets.append(values["href"])
        elif tag == "use" and values.get("href", "").startswith("/static/"):
            self.static_assets.append(values["href"])
        elif tag == "form":
            self.forms.append(values)


class UiTemplateContractTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def parse_template(self, name):
        parser = TemplateParser()
        parser.feed(self.read(f"templates/{name}"))
        return parser

    def assert_content_version(self, url):
        parsed = urlsplit(url)
        versions = parse_qs(parsed.query).get("v", [])
        self.assertEqual(len(versions), 1, url)
        asset = ROOT / parsed.path.lstrip("/")
        expected = hashlib.sha256(asset.read_bytes()).hexdigest()[:12]
        self.assertEqual(versions[0], expected, url)

    def test_pages_use_self_hosted_design_system_without_inline_styles(self):
        expected_page_css = {
            "login.html": "/static/css/login.css",
            "index.html": "/static/css/mirror.css",
            "admin.html": "/static/css/admin.css",
        }
        for name, page_css in expected_page_css.items():
            with self.subTest(name=name):
                source = self.read(f"templates/{name}")
                parser = self.parse_template(name)
                hrefs = {urlsplit(item.get("href") or "").path for item in parser.stylesheets}
                self.assertNotIn("<style", source.lower())
                self.assertNotRegex(source, r"\sstyle\s*=")
                self.assertIn("/static/css/ui-tokens.css", hrefs)
                self.assertIn("/static/css/ui-components.css", hrefs)
                self.assertIn(page_css, hrefs)
                self.assertTrue(all((item.get("href") or "").startswith("/static/") for item in parser.stylesheets))
                for script in parser.scripts:
                    if script.get("src"):
                        self.assertTrue(script["src"].startswith("/static/"))
                    else:
                        self.assertEqual(script.get("type"), "application/json")
                        self.assertIn(script.get("id"), {"scrcpygate-bootstrap", "scrcpygate-i18n"})
                for asset_url in parser.static_assets:
                    self.assert_content_version(asset_url)

    def test_runtime_icon_sprite_url_uses_its_content_hash(self):
        core = self.read("static/js/ui-core.js")
        match = re.search(r'const iconUrl = "([^"]+)#";', core)
        self.assertIsNotNone(match)
        self.assert_content_version(match.group(1))

    def test_template_ids_remain_unique_and_core_bindings_exist(self):
        required = {
            "login.html": {
                "loginTitle", "loginError", "loginForm", "username", "password",
                "passwordToggle", "loginSubmit", "loginSubmitLabel", "loginSubmitStatus",
                "scrcpygate-i18n",
            },
            "index.html": {
                "sidebar", "roleChip", "adminLink", "toolBtn", "alasBtn", "accountBtn",
                "sidebarCollapseBtn", "deviceSearch", "deviceFilterAll", "deviceFilterOnline",
                "refreshBtn", "deviceSummary", "devices", "startBtn", "controlBtn", "stopBtn", "tools",
                "qualityProfiles", "qualityStreamMode", "qualityStatus", "alasTools",
                "alasPanelStatus", "alasToggleRun", "alasReload", "accountTools", "accountInfo",
                "currentPassword", "newPassword", "confirmPassword", "changePasswordBtn",
                "sidebarBackdrop", "menuBtn", "selectedTitle", "selectedMeta", "topStatus",
                "statusDetails", "controlOwnership", "fullscreenBtn", "notice", "stage", "screenArea", "videoWrap",
                "phoneVideo", "empty", "emptyTitle", "emptyDescription", "backBtn", "homeBtn",
                "recentBtn", "keyboardBtn", "immersiveRail", "immersiveRailToggle", "immersiveControls",
                "immersiveControlBtn", "immersiveAlasBtn", "immersiveMirrorBtn", "exitFullscreenBtn", "toolDrawerBackdrop", "workspaceDrawer", "workspaceDrawerTitle",
                "toolDrawerCloseBtn", "scrcpygate-i18n", "scrcpygate-bootstrap",
            },
            "admin.html": {
                "notice", "summaryMirror", "summaryAlas", "overview", "overviewDevices",
                "overviewUsers", "overviewMirror", "overviewAlas", "overviewDevicesMeta",
                "overviewUsersMeta", "overviewMirrorMeta", "overviewAlasMeta", "reloadAll", "devices", "deviceCards",
                "deviceId", "deviceName", "deviceAddress", "deviceEnabled", "saveDevice",
                "clearDeviceForm", "video", "videoProfile", "videoFullscreenProfile", "videoFullscreenHint", "videoStreamMode", "videoAutoStop",
                "saveVideo", "streamModeToggles", "bandwidthPresetActions", "videoPresetRows",
                "customProfileId", "customProfileLabel", "customProfileBitrate", "customProfileSizeSelect",
                "customProfileSizeCustomWrap", "customProfileWidth", "customProfileHeight", "customProfileSizeReference",
                "customProfileFps", "addCustomProfile", "customProfileRows", "users", "userRows",
                "newUsername", "newPassword", "newRole", "saveUser", "accessAccountsTab",
                "accessPermissionsTab", "accessPermissionDraftSummary", "accessAccountsView", "accessPermissionsView", "userSearch",
                "userRoleFilter", "userExpiryFilter", "userExpiryMode", "userExpiresAtField", "userExpiresAt",
                "userExpiryShortcuts", "userExpiryPreview", "userResultCount", "userPagePrevious", "userPageNext", "userPageStatus",
                "permissionUserSearch", "permissionUserList", "permissionUserResultCount", "permUser",
                "permissionSelectedUser", "permissionSelectedUserMeta", "permissionUserRole",
                "permissionDirtyCount", "permissionLoadState", "permissionRetry", "permissionAdminNotice", "permissionDeviceSearch",
                "permissionDeviceFilter", "permissionDeviceResultCount", "permissionSaveStatus",
                "savePermission", "permissionRows", "alas",
                "alasBaseUrl", "alasToken", "saveAlas", "alasOperateConfig", "alasStatus",
                "toggleAlas", "reloadAlas", "alasBindUser", "alasBindConfig", "alasBindConfigCustomField", "alasBindConfigCustom", "alasBindRun",
                "alasBindEnabled", "saveAlasBinding", "alasBindRows", "configSource", "configTarget",
                "alasAssignmentSummary", "alasAssignmentUserName", "alasAssignmentUserMeta",
                "alasAssignmentConfigSummary", "alasAssignmentConfigName", "alasAssignmentConfigMeta",
                "loadConfig", "configEditor", "saveConfig", "logs", "runtimeLogs", "logRows",
                "runtimeLogFilters", "runtimeSeverityFilter", "runtimeLogSearch", "runtimeLogWrap",
                "runtimeRawToggle", "runtimeLogReset", "runtimeLogSummary", "runtimeLogResultCount",
                "runtimeRawLogs", "exportRuntimeLogs", "runtimeLogMeta", "runtimeLogMetaTitle",
                "runtimeLogMetaSummary", "runtimeLogMetaHealth", "runtimeLogMetaIssues",
                "scrcpygate-i18n", "scrcpygate-bootstrap",
            },
        }
        for name, expected in required.items():
            with self.subTest(name=name):
                parser = self.parse_template(name)
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                self.assertFalse(expected.difference(parser.ids))

    def test_logout_and_alas_links_keep_existing_contracts(self):
        index = self.read("templates/index.html")
        admin = self.read("templates/admin.html")
        for source in (index, admin):
            self.assertRegex(source, r'<form\s+method="post"\s+action="/logout"')
            self.assertIn('name="csrf_token"', source)
        self.assertIn('href="/alas/embed/"', index)
        self.assertIn("t('mirror.ui.open_alas')", index)
        self.assertIn('href="/alas/embed/"', admin)
        self.assertIn("t('admin.ui.alas.open_full_page')", admin)
        self.assertIn("t('admin.ui.drawers.alas_connection.note')", admin)

    def test_json_bootstrap_escapes_untrusted_user_data(self):
        environment = Environment(
            loader=FileSystemLoader(TEMPLATES),
            autoescape=select_autoescape(["html"]),
        )
        environment.globals.update(
            t=i18n.translate,
            i18n_payload=i18n.browser_payload,
            default_locale=i18n.DEFAULT_LOCALE,
            current_locale=i18n.current_locale,
        )
        malicious = '</script><script>alert("owned")</script>'
        user = {"username": malicious, "role": "admin", "password_hash": "must-not-leak"}
        pattern = re.compile(
            r'<script type="application/json" id="scrcpygate-bootstrap">(.*?)</script>',
            re.DOTALL,
        )
        for name in ("index.html", "admin.html"):
            with self.subTest(name=name):
                rendered = environment.get_template(name).render(user=user, csrf_token=malicious)
                match = pattern.search(rendered)
                self.assertIsNotNone(match)
                payload = json.loads(match.group(1))
                self.assertEqual(payload["user"]["username"], malicious)
                self.assertEqual(payload["csrf_token"], malicious)
                self.assertNotIn(malicious, match.group(1))
                self.assertNotIn("password_hash", match.group(1))

    def test_pages_load_one_i18n_catalog_before_shared_ui_runtime(self):
        for name in ("login.html", "index.html", "admin.html"):
            with self.subTest(name=name):
                source = self.read(f"templates/{name}")
                parser = self.parse_template(name)
                self.assertEqual(parser.ids.count("scrcpygate-i18n"), 1)
                self.assertIn('/static/js/i18n.js?', source)
                self.assertLess(source.index('/static/js/i18n.js?'), source.index('/static/js/ui-core.js?'))
                self.assertIn('{{ i18n_payload() | tojson }}', source)

    def test_i18n_catalog_is_valid_and_runtime_has_key_fallback(self):
        catalog = json.loads(self.read("static/i18n/zh-CN.json"))
        runtime = self.read("static/js/i18n.js")
        self.assertEqual(catalog["meta"]["language_name"], "简体中文")
        self.assertEqual(catalog["login"]["submit"], "登录")
        self.assertIn("function resolve(key)", runtime)
        self.assertIn('return String(key || "")', runtime)
        self.assertIn("Object.prototype.hasOwnProperty.call(values, name)", runtime)

    def test_runtime_event_titles_cover_emitted_failure_events_in_both_catalogs(self):
        expected = {
            "alas_config_catalog_failed",
            "alas_overview_catalog_failed",
            "alas_overview_status_failed",
            "audit_queue_stop_timeout",
            "mirror_reconfigure_stop_failed",
            "mirror_remove_snapshot_failed",
            "mirror_remove_stop_failed",
            "video_client_terminate_failed",
            "video_protocol_keyflag_without_idr",
        }
        for locale in ("zh-CN", "en-US"):
            with self.subTest(locale=locale):
                catalog = json.loads(self.read(f"static/i18n/{locale}.json"))
                events = catalog["admin"]["logs"]["runtime_events"]
                self.assertTrue(expected.issubset(events.keys()))
                for key in expected:
                    self.assertTrue(events[key].strip(), key)

    def test_theme_runtime_and_accessibility_tokens_are_present(self):
        init = self.read("static/js/theme-init.js")
        core = self.read("static/js/ui-core.js")
        tokens = self.read("static/css/ui-tokens.css")
        components = self.read("static/css/ui-components.css")
        self.assertIn('scrcpygate:theme', init)
        self.assertIn('theme = "dark"', init)
        for value in ("system", "dark", "light"):
            self.assertIn(value, init)
        self.assertIn('addEventListener("storage"', core)
        self.assertIn("prefers-color-scheme: light", core)
        for color in ("#111315", "#191c20", "#20242a", "#363c44", "#f1f3f5", "#b0b7c0", "#2563eb"):
            self.assertIn(color, tokens)
        self.assertIn(':root[data-theme="light"]', tokens)
        self.assertIn(':root[data-theme="system"]', tokens)
        self.assertIn("--ui-color-control-border: #68717d", tokens)
        self.assertIn("--ui-visual-viewport-height: 100dvh", tokens)
        self.assertIn(":focus-visible", components)
        self.assertIn("prefers-reduced-motion: reduce", components)
        self.assertIn("prefers-reduced-transparency: reduce", components)
        self.assertIn("prefers-contrast: more", components)
        self.assertIn("function syncVisualViewportMetrics()", core)
        self.assertIn('window.visualViewport.addEventListener("resize"', core)
        self.assertIn('window.visualViewport.addEventListener("scroll"', core)
        for primitive in (".ui-button", ".ui-toast", ".ui-drawer", ".ui-dialog", ".ui-empty", ".ui-skeleton"):
            self.assertIn(primitive, components)

    def test_theme_picker_uses_accessible_self_hosted_popup(self):
        core = self.read("static/js/ui-core.js")
        components = self.read("static/css/ui-components.css")
        for name in ("login.html", "index.html", "admin.html"):
            with self.subTest(name=name):
                template = self.read(f"templates/{name}")
                self.assertIn('class="ui-theme-picker', template)
                self.assertIn("data-ui-theme-select", template)
                self.assertNotIn('<label class="ui-theme-picker', template)
        for token in (
            'trigger.setAttribute("aria-haspopup", "menu")',
            'menu.setAttribute("role", "menu")',
            'item.setAttribute("role", "menuitemradio")',
            'item.setAttribute("aria-checked", "false")',
            '"ArrowDown"',
            '"ArrowUp"',
            '"Enter"',
            '"Escape"',
            "closeThemeMenus(false)",
            "event.stopPropagation()",
            "event.stopImmediatePropagation()",
            "function positionPickerMenu(menu)",
            "repositionOpenPickerMenus()",
            "control.close(event.detail === 0)",
        ):
            self.assertIn(token, core)
        for selector in (".ui-theme-trigger", ".ui-theme-menu", ".ui-theme-option", '.ui-theme-option[aria-checked="true"]', ".compact-theme-picker .ui-theme-trigger"):
            self.assertIn(selector, components)
        self.assertEqual(core.count("if (leadingIcon) trigger.appendChild(leadingIcon);"), 2)
        self.assertEqual(core.count("control.close(event.detail === 0);"), 2)
        self.assertIn("right: var(--ui-picker-inline-offset, 0px);", components)
        self.assertIn("outline: 2px solid var(--ui-color-info);", components)
        self.assertNotIn(".ui-theme-picker:focus-within", components)
        self.assertNotIn('body[data-ui-page="admin"] .tabs button', components)
        fine_pointer = components.split("@media (hover: hover) and (pointer: fine)", 1)[1].split("@media", 1)[0]
        self.assertIn(".ui-theme-picker:hover", fine_pointer)
        self.assertIn(".ui-locale-picker:hover", fine_pointer)
        self.assertIn(".admin-nav__footer .ui-locale-picker", self.read("static/css/admin.css"))

    def test_locale_picker_matches_theme_picker_and_uses_self_hosted_popup(self):
        core = self.read("static/js/ui-core.js")
        components = self.read("static/css/ui-components.css")
        sprite = self.read("static/icons/lucide.svg")
        for name in ("login.html", "index.html", "admin.html"):
            with self.subTest(name=name):
                template = self.read(f"templates/{name}")
                self.assertIn('class="ui-locale-picker', template)
                self.assertIn("data-ui-locale-select", template)
                self.assertIn("#languages", template)
                self.assertNotIn("A/文", template)
                self.assertNotIn('<label class="ui-locale-picker', template)
        for token in (
            'trigger.setAttribute("aria-haspopup", "menu")',
            'menu.setAttribute("role", "menu")',
            'item.setAttribute("role", "menuitemradio")',
            'item.setAttribute("aria-checked", String(option.value === select.value))',
            'trigger.setAttribute("aria-expanded", "true")',
            'trigger.setAttribute("aria-expanded", "false")',
            '"ArrowDown"',
            '"ArrowUp"',
            '"Enter"',
            '"Escape"',
            "closeLocaleMenus(false)",
        ):
            self.assertIn(token, core)
        for selector in (
            ".ui-locale-trigger",
            ".ui-locale-menu",
            ".ui-locale-option",
            '.ui-locale-option[aria-checked="true"]',
            ".compact-locale-picker .ui-locale-trigger",
            "@keyframes ui-picker-enter",
        ):
            self.assertIn(selector, components)
        self.assertIn('<symbol id="languages"', sprite)
        self.assertIn(".compact-theme-picker { width: 44px; }", components)
        self.assertNotIn(".ui-locale-picker:focus-within", components)
        login_toolbar = self.read("static/css/login.css").split(".login-toolbar {", 1)[1].split("}", 1)[0]
        self.assertIn("gap: var(--ui-space-2);", login_toolbar)

    def test_scripts_avoid_template_code_and_unsafe_html_sinks(self):
        for name in ("mirror.js", "admin.js", "login.js", "ui-core.js", "theme-init.js", "i18n.js", "alas-shell.js"):
            with self.subTest(name=name):
                source = self.read(f"static/js/{name}")
                self.assertNotIn("{{", source)
                self.assertNotIn("{%", source)
                self.assertNotIn(".innerHTML", source)
                self.assertNotIn("insertAdjacentHTML", source)
                self.assertNotIn("document.write", source)

    def test_login_page_has_accessible_password_and_submission_states(self):
        template = self.read("templates/login.html")
        script = self.read("static/js/login.js")
        self.assertIn('aria-controls="password"', template)
        self.assertIn('aria-pressed="false"', template)
        self.assertIn('role="alert"', template)
        self.assertIn('aria-live="polite"', template)
        self.assertIn('value="{{ username', template)
        self.assertIn('password.type = visible ? "text" : "password"', script)
        self.assertIn('form.addEventListener("submit"', script)
        self.assertIn("ScrcpyGateUI.setBusy", script)
        self.assertIn('addEventListener("pageshow"', script)
        self.assertNotIn("autofocus", template)
        self.assertIn('(hover: hover) and (pointer: fine)', script)
        self.assertIn("const passwordHadFocus = document.activeElement === password", script)
        self.assertIn("if (passwordHadFocus)", script)

    def test_lucide_sprite_and_attribution_are_complete(self):
        sprite_path = ROOT / "static/icons/lucide.svg"
        root = ET.parse(sprite_path).getroot()
        symbols = root.findall("{http://www.w3.org/2000/svg}symbol")
        ids = [symbol.attrib.get("id") for symbol in symbols]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(ids))
        self.assertTrue(all(symbol.attrib.get("viewBox") == "0 0 24 24" for symbol in symbols))
        for required in ("menu", "refresh-cw", "settings", "user-round", "log-out", "sun", "moon", "monitor", "eye", "eye-off", "x", "check", "info", "external-link", "loader-circle"):
            self.assertIn(required, ids)
        third_party = self.read("THIRD_PARTY.md")
        self.assertIn("Lucide Icons", third_party)
        self.assertIn("static/icons/lucide.svg", third_party)
        self.assertIn("ISC", third_party)
        self.assertTrue((ROOT / "static/icons/LUCIDE_LICENSE").is_file())
        self.assertFalse((ROOT / "static/css/local-ui.css").exists())

    def test_bundled_video_assets_have_versioned_provenance(self):
        third_party = self.read("THIRD_PARTY.md")
        assets = {
            "scrcpy-server": "v3.1",
            "static/js/jmuxer.min.js": "v2.0.7",
            "adb/linux/adb": "36.0.0",
        }
        for relative_path, version in assets.items():
            asset_path = ROOT / relative_path
            self.assertTrue(asset_path.is_file())
            digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
            self.assertIn(digest, third_party)
            self.assertIn(version, third_party)

        jmuxer_license = ROOT / "static/js/JMUXER_LICENSE"
        self.assertTrue(jmuxer_license.is_file())
        license_text = jmuxer_license.read_text(encoding="utf-8")
        self.assertIn("The MIT License", license_text)
        self.assertIn("Copyright (c) 2018 Samir Das", license_text)


if __name__ == "__main__":
    unittest.main()
