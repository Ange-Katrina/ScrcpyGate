import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminWorkspaceUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
        cls.catalog = (ROOT / "static" / "i18n" / "zh-CN.json").read_text(encoding="utf-8")

    def test_workspace_uses_sidebar_and_full_width_domains(self):
        for token in (
            'id="adminNav"',
            'class="admin-workspace"',
            'class="admin-content"',
            'id="overviewUsers"',
            'id="overviewDevicesMeta"',
            'id="overviewUsersMeta"',
            'id="overviewMirrorMeta"',
            'id="overviewAlasMeta"',
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
        self.assertIn("height: var(--ui-visual-viewport-height, 100dvh)", self.styles)
        self.assertIn("padding: max(var(--ui-space-4), env(safe-area-inset-top))", self.styles)
        self.assertIn("nav.setAttribute('role','dialog')", self.script)
        self.assertIn("nav.setAttribute('aria-modal','true')", self.script)
        self.assertIn("element.closest('[hidden], [inert], [aria-hidden=\"true\"]')", self.script)
        self.assertIn("element.getClientRects().length", self.script)
        self.assertIn("panel.scrollIntoView({block:'start',behavior:'instant'})", self.script)

    def test_mobile_forms_and_actions_do_not_overflow_narrow_screens(self):
        self.assertIn("font-size: 16px", self.styles)
        self.assertIn("white-space: normal", self.styles)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", self.styles)
        self.assertIn(".device-card .actions .danger:last-child", self.styles)
        self.assertIn("@media (max-height: 520px) and (orientation: landscape)", self.styles)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", self.styles)
        landscape = self.styles.split("@media (max-height: 520px) and (orientation: landscape)", 1)[1].split("@media", 1)[0]
        self.assertIn("grid-template-rows: auto auto auto", landscape)
        self.assertIn("align-content: start", landscape)
        body_rule = self.styles.split(".admin-editor-drawer > .drawer-body {", 1)[1].split("}", 1)[0]
        form_rule = self.styles.split(".drawer-form {", 1)[1].split("}", 1)[0]
        actions_rule = self.styles.split(".drawer-actions {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y: auto", body_rule)
        self.assertIn("align-content: start", body_rule)
        self.assertIn("grid-auto-rows: max-content", form_rule)
        self.assertIn("position: static", actions_rule)

    def test_all_editor_drawers_use_fixed_header_scrollable_body_and_actions(self):
        drawer_ids = (
            "deviceDrawer",
            "userDrawer",
            "alasConnectionDrawer",
            "alasAssignmentDrawer",
            "alasConfigDrawer",
        )
        for drawer_id in drawer_ids:
            start = self.template.index(f'<aside id="{drawer_id}"')
            end = self.template.index("</aside>", start)
            drawer = self.template[start:end]
            self.assertEqual(drawer.count('class="drawer-body"'), 1, drawer_id)
            self.assertEqual(drawer.count('class="drawer-actions"'), 1, drawer_id)
            self.assertIn('\n  <div class="drawer-body">', drawer, drawer_id)
            self.assertIn('\n  <div class="drawer-actions">', drawer, drawer_id)
            self.assertLess(drawer.index('class="drawer-heading"'), drawer.index('class="drawer-body"'))
            self.assertLess(drawer.index('class="drawer-body"'), drawer.index('class="drawer-actions"'))

    def test_tables_and_danger_actions_use_responsive_accessible_patterns(self):
        self.assertGreaterEqual(self.template.count('class="responsive-table"'), 5)
        self.assertIn("applyTableLabels(rows)", self.script)
        self.assertIn("function tableActionCell(...buttons)", self.script)
        self.assertIn("cell.className='table-action-cell'", self.script)
        self.assertIn("actions.className='table-actions'", self.script)
        self.assertIn("td.table-action-cell", self.styles)
        self.assertIn(".table-actions", self.styles)
        self.assertNotIn(":not([data-label])::before", self.styles)
        mobile_cells = self.styles.split(".responsive-table td {", 1)[1].split("}", 1)[0]
        self.assertIn("width: 100%;", mobile_cells)
        custom_profiles = self.script.split("function renderCustomProfiles", 1)[1].split("function streamModeLabel", 1)[0]
        users = self.script.split("function renderUsers", 1)[1].split("function fillSelect", 1)[0]
        self.assertNotIn("actions.className='actions'", custom_profiles)
        self.assertNotIn("actions.className='actions'", users)
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

    def test_security_audit_workspace_has_filters_cursor_actions_and_accessible_detail(self):
        for token in (
            'id="auditSummaryDenied"',
            'id="auditSummaryFailed"',
            'id="auditSummaryHighRisk"',
            'id="auditFilters"',
            'id="auditFrom"',
            'id="auditTo"',
            'id="auditActor"',
            'id="auditAction"',
            'id="auditOutcome"',
            'id="auditSeverity"',
            'id="auditRequestId"',
            'id="auditLoadMore"',
            'id="auditExportCsv"',
            'id="auditExportJson"',
            'id="auditIntegrityCheck"',
            'id="auditDetailDialog"',
            'id="auditDetailSourceIp"',
            'id="auditDetailSchemaVersion"',
            'id="auditDetailIntegrity"',
            'aria-labelledby="auditDetailTitle"',
            'aria-describedby="auditDetailStatus"',
            'aria-hidden="true" inert hidden',
        ):
            self.assertIn(token, self.template)

        for token in (
            "api(auditRequestUrl(filters,before),{signal})",
            "before=append ? auditNextCursor : null",
            "renderAuditLogs(additions,true)",
            "api(`/api/admin/logs/${encodeURIComponent(eventId)}`)",
            "api('/api/admin/logs/export',{method:'POST'",
            "api('/api/admin/logs/integrity-check',{method:'POST'",
            "window.ScrcpyGateUI.openDialog(dialog,trigger)",
            "window.ScrcpyGateUI.closeDialog(dialog,'close')",
            "$('auditDetailDialog').addEventListener('cancel'",
            "anchor.download=download.filename",
            "function auditReasonText(reason)",
            "admin.audit_reason.http_status",
            "admin.logs.audit_reason_not_recorded",
            "admin.logs.audit_detail_not_recorded",
            "previous_hash:data.prev_hash || null",
        ):
            self.assertIn(token, self.script)
        self.assertNotIn("innerHTML", self.script)

        for token in (
            ".audit-summary",
            ".audit-filter-form",
            ".audit-log-table .responsive-table",
            ".audit-detail-dialog",
            ".audit-detail-grid",
            "grid-auto-rows: max-content",
            "min-height: max-content",
            "grid-template-columns: minmax(0, 1fr)",
        ):
            self.assertIn(token, self.styles)

    def test_runtime_log_viewer_has_safe_filters_counts_and_structured_rows(self):
        for token in (
            'id="runtimeLogFilters"',
            'id="runtimeSeverityFilter"',
            'id="runtimeLogSearch"',
            'id="runtimeLogWrap"',
            'id="runtimeRawToggle"',
            'id="runtimeLogReset"',
            'id="runtimeLogSummary"',
            'id="runtimeLogResultCount"',
            'id="runtimeLogs" class="runtime-logs" role="list"',
            'id="runtimeRawLogs" class="runtime-raw-logs"',
            'id="exportRuntimeLogs"',
            'id="reloadRuntimeLogs" class="btn" type="button"><svg',
            'data-busy-label',
        ):
            self.assertIn(token, self.template)

        for token in (
            "function normalizeRuntimeLogSeverity",
            "function runtimeLogEntryAttributes",
            "function isRuntimeLogPayload",
            "function splitRuntimeTextFields",
            "RUNTIME_LOG_TEXT_FIELDS_MARKER",
            "function parseRuntimeLogMessageFields",
            "function parseRuntimeLogFallback",
            "function isRuntimeLogContinuation",
            "entries.length && isRuntimeLogContinuation(continuation)",
            "if(!isAbortError(e)) show(e.message)",
            "function runtimeLogCategory",
            "function runtimeLogEventTitle",
            "function runtimeLogReasonText",
            "function runtimeLogTechnicalContext",
            "function createRuntimeLogEntry",
            "document.createDocumentFragment()",
            "attributes.textContent=JSON.stringify",
            "runtime-log-category--${category}",
            "params.set('min_severity',severity)",
            "function exportRuntimeLogs",
            "const rawMode=$('runtimeRawToggle').checked",
            "function syncRuntimeLogMode",
            "$('runtimeSeverityFilter').disabled=rawMode",
            "const label=el.querySelector('[data-busy-label]') || el",
            "? (state.runtimeLogs || []).join('\\n')",
            ": JSON.stringify(filteredRuntimeLogEntries(),null,2)",
            "requestAnimationFrame",
            "runtime-log-entry--${severity}",
        ):
            self.assertIn(token, self.script)
        self.assertNotIn("innerHTML", self.script)

        for token in (
            ".runtime-log-toolbar",
            ".runtime-log-summary",
            ".runtime-log-viewer",
            ".runtime-log-entry--warning",
            ".runtime-log-entry--error",
            ".runtime-log-level--critical",
            ".runtime-log-category--security",
            ".runtime-log-title",
            ".runtime-log-context-grid",
            ".runtime-log-detail-code",
            ".runtime-logs.is-nowrap",
            ".runtime-raw-logs.is-nowrap",
        ):
            self.assertIn(token, self.styles)

    def test_admin_script_avoids_runtime_inline_styles(self):
        self.assertNotIn(".style.", self.script)
        self.assertIn("className='stream-mode-toggle'", self.script)
        self.assertNotIn("letter-spacing: -", self.styles)
        self.assertNotIn("font-size: clamp(", self.styles)

    def test_device_cards_show_authoritative_adb_state(self):
        for token in (
            "const ADB_STATUS_META",
            "function deviceAdbMeta(device)",
            "function applyDeviceAdbResult(id,result={})",
            "deviceProbeRequests.has(id)",
            "applyDevices(result)",
            "loadedResources.add('devices')",
            "admin.adb.checking",
            "admin.adb.online",
            "admin.adb.offline",
            "admin.adb.network_unreachable",
            "admin.adb.unauthorized",
            "last_checked_at",
            "last_seen_at",
            "latency_ms",
            "function syncDeviceStatusPolling()",
            "(activeTab==='devices' || activeTab==='overview') && !document.hidden",
            "api('/api/admin/adb/status'",
            "api('/api/admin/devices'",
            "function applyOverviewDevices(data)",
        ):
            self.assertIn(token, self.script)
        self.assertIn('id="deviceId" type="hidden"', self.template)
        self.assertIn('id="deviceCards" class="device-grid"', self.template)
        self.assertNotIn('id="deviceCards" class="device-grid" aria-live=', self.template)
        self.assertNotIn("async function startDevice", self.script)
        self.assertNotIn("async function stopDevice", self.script)
        self.assertIn(".device-card__heading", self.styles)
        self.assertIn(".device-heartbeat", self.styles)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", self.styles)
        self.assertIn("Object.entries(statuses).forEach", self.script)
        self.assertNotIn("Object.entries(statuses).forEach(([id,status])=>applyDeviceAdbResult(id,status || {}));\n  renderOverview();", self.script)

    def test_overview_uses_independent_keyed_status_lists(self):
        for token in (
            "function updateOverviewRows(container,items,emptyMessage)",
            "function renderOverviewDevices()",
            "function renderOverviewUsers()",
            "function renderOverviewMirrors()",
            "function renderOverviewAlas()",
            "function alasOverviewErrorText(value)",
            "admin.overview.alas_unreachable",
            "config_statuses",
            "session.clients",
            "state.overview && Array.isArray(state.overview.users)",
            "loadOverviewAlas",
            "loadOverviewDevices",
        ):
            self.assertIn(token, self.script)
        self.assertIn('"alas_unreachable": "ALAS Runtime 不可达"', self.catalog)
        self.assertIn('role="region" aria-labelledby="overviewDevicesTitle" tabindex="0"', self.template)
        self.assertIn(".overview-list:focus-visible", self.styles)
        self.assertIn("overflow-wrap: anywhere", self.styles)


if __name__ == "__main__":
    unittest.main()
