import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminWorkspaceUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    def test_workspace_uses_sidebar_and_full_width_domains(self):
        for token in (
            'id="adminNav"',
            'class="admin-workspace"',
            'class="admin-content"',
            'id="overviewControl"',
            'id="runtimeLogsStatus"',
            'id="auditLogsStatus"',
        ):
            self.assertIn(token, self.template)
        self.assertIn("--admin-nav-width: 224px", self.styles)
        self.assertIn("grid-template-columns: var(--admin-nav-width) minmax(0, 1fr)", self.styles)

    def test_mobile_navigation_and_editors_are_accessible_layers(self):
        for token in (
            'id="adminNavToggle"',
            'aria-controls="adminNav"',
            'id="deviceDrawer"',
            'id="userDrawer"',
            'role="dialog"',
            'aria-modal="true"',
            'aria-hidden="true" inert',
        ):
            self.assertIn(token, self.template)
        for token in (
            "nav.setAttribute('aria-hidden','true')",
            "nav.setAttribute('inert','')",
            "setAdminWorkspaceHidden(true)",
            "trapAdminNavFocus",
            "event.key==='Escape'",
            "focusTarget.focus",
        ):
            self.assertIn(token, self.script)
        self.assertIn("@media (max-width: 920px)", self.styles)
        self.assertIn("min-height: 44px", self.styles)

    def test_tables_and_danger_actions_use_responsive_accessible_patterns(self):
        self.assertGreaterEqual(self.template.count('class="responsive-table"'), 5)
        self.assertIn("applyTableLabels(rows)", self.script)
        self.assertIn('id="confirmDialog"', self.template)
        self.assertIn("confirmDanger({", self.script)
        self.assertIn("pendingConfirmation", self.script)
        self.assertIn("restoreConfirmationFocus(pending.trigger)", self.script)

    def test_alas_help_and_logs_do_not_block_primary_overview(self):
        self.assertIn('class="help-details"', self.template)
        self.assertIn("loadOverview()", self.script)
        self.assertIn("reloadRuntimeLogs", self.script)
        self.assertIn("reloadAuditLogs", self.script)
        self.assertIn("Promise.allSettled", self.script)
        self.assertIn("loadedResources.has(name) || visible.has(name)", self.script)

    def test_admin_script_avoids_runtime_inline_styles(self):
        self.assertNotIn(".style.", self.script)
        self.assertIn("className='stream-mode-toggle'", self.script)
        self.assertNotIn("letter-spacing: -", self.styles)
        self.assertNotIn("font-size: clamp(", self.styles)


if __name__ == "__main__":
    unittest.main()
