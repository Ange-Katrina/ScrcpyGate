import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MirrorWorkspaceUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static/css/mirror.css").read_text(encoding="utf-8")
        cls.script = (ROOT / "static/js/mirror.js").read_text(encoding="utf-8")

    def test_workspace_exposes_search_filters_and_primary_control_state(self):
        for token in (
            'id="deviceSearch"',
            'id="deviceFilterAll"',
            'id="deviceFilterOnline"',
            'id="controlOwnership"',
            'id="statusDetails"',
            'aria-current',
            "function deviceMatchesFilters(device)",
            "function renderControlOwnership(device, session)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.template + self.script)

    def test_tools_use_one_accessible_workspace_drawer(self):
        for trigger in ("toolBtn", "alasBtn", "accountBtn"):
            self.assertRegex(
                self.template,
                rf'id="{trigger}"[^>]+aria-controls="workspaceDrawer"[^>]+aria-expanded="false"',
            )
        self.assertIn('id="workspaceDrawer"', self.template)
        self.assertIn('role="dialog"', self.template)
        self.assertIn('aria-modal="true"', self.template)
        self.assertIn("function openToolDrawer(id, trigger)", self.script)
        self.assertIn("function closeToolDrawer()", self.script)
        self.assertIn("trigger.focus({preventScroll:true})", self.script)
        self.assertIn("function trapLayerFocus(event, layer)", self.script)
        self.assertIn("if (app) app.setAttribute('inert','')", self.script)
        self.assertNotIn("ScrcpyGateUI.openDrawer", self.script)

    def test_sidebar_and_video_layout_have_stable_responsive_tracks(self):
        for token in (
            "--mirror-sidebar-width: 296px",
            "--mirror-sidebar-collapsed-width: 64px",
            "grid-template-rows: minmax(0, 1fr) calc(var(--mirror-navigation-row)",
            ".nav-btn",
            "width: 64px",
            "height: 44px",
            "@media (max-width: 960px)",
            "transform: translateY(calc(100% + 8px))",
            ".device-search,\n  .device-filters {\n    display: none",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.styles)
        self.assertIn("const MOBILE_SIDEBAR_QUERY = '(max-width: 960px)'", self.script)
        self.assertIn("scrcpygate:mirror:sidebar-collapsed", self.script)
        self.assertIn("sidebar.toggleAttribute('inert', !open)", self.script)
        self.assertIn("viewer.toggleAttribute('inert', open)", self.script)
        self.assertIn("node.getClientRects().length > 0", self.script)
        self.assertIn(".device-card {\n    min-height: 48px", self.styles)
        self.assertIn("selectedButton.scrollIntoView({block:'nearest'})", self.script)

    def test_async_commands_are_guarded_and_empty_states_are_structured(self):
        self.assertIn("const actionRequests = new Map()", self.script)
        self.assertIn("async function runBusyAction(key, element, label, action)", self.script)
        for action in ("mirror", "control", "refresh", "quality", "password", "alas"):
            self.assertIn(f"runBusyAction('{action}'", self.script)
        for state in (
            "loading",
            "no-devices",
            "no-permission",
            "filtered-empty",
            "starting",
            "connecting",
            "reconnecting",
            "disconnected",
            "error",
        ):
            with self.subTest(state=state):
                self.assertIn(f"'{state}'", self.script)
        self.assertIn("mirrorError:''", self.script)
        self.assertNotIn("uiError", self.script)
        self.assertIn("if (state.selectedDeviceId !== previous) state.mirrorError=''", self.script)
        self.assertIn("function deviceSelectable(device)", self.script)


if __name__ == "__main__":
    unittest.main()
