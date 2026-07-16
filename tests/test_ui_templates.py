import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape


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
                        self.assertEqual(script.get("id"), "scrcpygate-bootstrap")
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
            },
            "index.html": {
                "sidebar", "roleChip", "adminLink", "toolBtn", "alasBtn", "accountBtn",
                "sidebarCollapseBtn", "deviceSearch", "deviceFilterAll", "deviceFilterOnline",
                "refreshBtn", "deviceSummary", "devices", "startBtn", "controlBtn", "stopBtn", "tools",
                "qualityProfiles", "qualityStreamMode", "qualityStatus", "alasTools",
                "alasPanelStatus", "alasToggleRun", "alasReload", "accountTools", "accountInfo",
                "currentPassword", "newPassword", "confirmPassword", "changePasswordBtn",
                "sidebarBackdrop", "menuBtn", "selectedTitle", "selectedMeta", "topStatus",
                "statusDetails", "controlOwnership", "notice", "stage", "screenArea", "videoWrap",
                "phoneVideo", "empty", "emptyTitle", "emptyDescription", "backBtn", "homeBtn",
                "recentBtn", "toolDrawerBackdrop", "workspaceDrawer", "workspaceDrawerTitle",
                "toolDrawerCloseBtn", "scrcpygate-bootstrap",
            },
            "admin.html": {
                "notice", "summaryMirror", "summaryAlas", "overview", "overviewDevices",
                "overviewMirror", "overviewAlas", "reloadAll", "devices", "deviceCards",
                "deviceId", "deviceName", "deviceAddress", "deviceEnabled", "saveDevice",
                "clearDeviceForm", "video", "videoProfile", "videoStreamMode", "videoAutoStop",
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
                "scrcpygate-bootstrap",
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
        self.assertIn("打开 ALAS 页面", index)
        self.assertIn('href="/alas/embed/"', admin)
        self.assertIn("打开完整 ALAS 页面", admin)
        self.assertIn("Runtime URL 可填写 IP、域名或完整 URL", admin)

    def test_json_bootstrap_escapes_untrusted_user_data(self):
        environment = Environment(
            loader=FileSystemLoader(TEMPLATES),
            autoescape=select_autoescape(["html"]),
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
        self.assertIn(":focus-visible", components)
        self.assertIn("prefers-reduced-motion: reduce", components)
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
        ):
            self.assertIn(token, core)
        for selector in (".ui-theme-trigger", ".ui-theme-menu", ".ui-theme-option", '.ui-theme-option[aria-checked="true"]', ".compact-theme-picker .ui-theme-trigger"):
            self.assertIn(selector, components)
        self.assertIn(".compact-theme-picker { width: 44px; }", components)

    def test_scripts_avoid_template_code_and_unsafe_html_sinks(self):
        for name in ("mirror.js", "admin.js", "login.js", "ui-core.js", "theme-init.js"):
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
