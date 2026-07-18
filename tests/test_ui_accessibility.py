import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiAccessibilityContractTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_product_pages_expose_language_and_semantic_page_context(self):
        for name in ("login.html", "index.html", "admin.html"):
            with self.subTest(name=name):
                source = self.read(f"templates/{name}")
                self.assertIn('<html lang="{{ current_locale() }}">', source)
                self.assertIn('data-ui-locale-select', source)
                self.assertIn('aria-label="{{ t(\'common.language.label\') }}"', source)
                self.assertNotRegex(source, r"\sstyle\s*=")

    def test_shared_controls_keep_focus_touch_and_motion_baselines(self):
        components = self.read("static/css/ui-components.css")
        tokens = self.read("static/css/ui-tokens.css")
        mirror = self.read("static/css/mirror.css")
        self.assertIn(":where(button, a, input, select, textarea, [tabindex]):focus-visible", components)
        self.assertIn("@media (prefers-reduced-motion: reduce)", components)
        self.assertIn(".ui-button,\n  .ui-icon-button,\n  .ui-theme-picker", components)
        self.assertIn(".ui-locale-picker { min-height: 44px; }", components)
        self.assertIn(".ui-theme-option { min-height: 44px; }", components)
        self.assertIn("--ui-color-primary-text: #8ab4ff", tokens)
        self.assertIn("color: var(--ui-color-primary-text)", mirror)

    def test_drawers_and_dialogs_have_keyboard_closure_and_inert_isolation(self):
        core = self.read("static/js/ui-core.js")
        mirror = self.read("static/js/mirror.js")
        self.assertIn('event.key === "Escape"', core)
        self.assertIn("'Escape'", mirror)
        for source in (core, mirror):
            self.assertIn("inert", source)
            self.assertIn('aria-hidden', source)
        self.assertIn("trapLayerFocus", core)
        self.assertIn("trapLayerFocus", mirror)

    def test_mobile_layouts_declare_responsive_overflow_rules(self):
        for name in ("static/css/mirror.css", "static/css/admin.css"):
            source = self.read(name)
            with self.subTest(name=name):
                self.assertRegex(source, r"@media\s*\(max-width:\s*(?:720|640)px\)")
                self.assertIn("overflow-x", source)
                self.assertRegex(source, r"min-width:\s*0")

    def test_browser_gate_configuration_is_opt_in_and_uses_axe(self):
        package = self.read("package.json")
        lockfile = self.read("package-lock.json")
        config = self.read("playwright.config.js")
        spec = self.read("tests/e2e/ui-accessibility.spec.js")
        workflow = self.read(".github/workflows/Builds.yml")
        self.assertIn('"@axe-core/playwright"', package)
        self.assertIn('"@playwright/test"', package)
        self.assertIn('"lockfileVersion": 3', lockfile)
        self.assertIn("SCRCPYGATE_E2E_BASE_URL", config)
        for viewport in ("1440, height: 900", "1280, height: 720", "768, height: 1024", "390, height: 844", "844, height: 390"):
            self.assertIn(viewport, config)
        self.assertIn("@axe-core/playwright", spec)
        self.assertIn("critical", spec)
        self.assertIn("horizontal overflow", spec)
        self.assertIn("npm ci", workflow)
        self.assertIn("playwright install --with-deps chromium", workflow)


if __name__ == "__main__":
    unittest.main()
