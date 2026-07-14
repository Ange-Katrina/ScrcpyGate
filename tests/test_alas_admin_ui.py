import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AlasAdminUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    def test_workspace_separates_user_authorization_and_config_library(self):
        for token in (
            'id="alasUsersTab"',
            'id="alasConfigsTab"',
            'id="alasUsersView"',
            'id="alasConfigsView"',
            'id="alasUserList"',
            'id="alasConfigLibrary"',
            'role="listbox"',
            'aria-orientation="vertical"',
            'id="alasSelectedUser"',
            'id="alasCurrentConfigName"',
        ):
            self.assertIn(token, self.template)
        self.assertNotIn('>操作配置<', self.template)
        self.assertIn('当前配置', self.template)
        self.assertIn('配置库', self.template)

    def test_summary_and_primary_actions_are_explicit(self):
        for token in (
            'id="alasRuntimeSummary"',
            'id="alasConfigCount"',
            'id="alasUserCount"',
            'id="alasAssignmentCount"',
            'id="reloadAlas"',
            'id="alasOpenCurrent"',
            'id="openAlasConnection"',
        ):
            self.assertIn(token, self.template)

    def test_assignments_use_multi_config_payload_and_exact_deletion(self):
        self.assertIn("Array.isArray(state.alas.assignments)", self.script)
        self.assertIn("hasDetailedAssignments ? state.alas.assignments : alasBindings()", self.script)
        self.assertIn("config_name:binding.config_name, enabled:false, is_default:false", self.script)
        for token in (
            "enabled:true",
            "can_run:$('alasBindRun').value==='true'",
            "can_edit:$('alasBindEdit').value==='true'",
            "is_default:$('alasBindDefault').value==='true' ? true : null",
        ):
            self.assertIn(token, self.script)
        self.assertIn("editingAlasAssignment ? editingAlasAssignment.config_name", self.script)
        self.assertIn("await mutateAlasPermissions(payload)", self.script)
        self.assertIn("await mutateAlasPermissions({username:binding.username", self.script)
        remove_binding = self.script.split("async function removeAlasBinding", 1)[1].split("function renderAlas", 1)[0]
        save_binding = self.script.split("async function saveAlasBinding", 1)[1].split("function activateTab", 1)[0]
        self.assertNotIn("refreshDomains('overview','alas')", remove_binding)
        self.assertNotIn("refreshDomains('overview','alas')", save_binding)
        self.assertIn("refreshDomains('overview')", remove_binding)
        self.assertIn("refreshDomains('overview')", save_binding)

    def test_config_catalog_and_status_are_loaded_without_batch_status_requests(self):
        self.assertIn("api('/api/admin/alas/configs',{signal})", self.script)
        self.assertIn("/api/admin/alas?config=${encodeURIComponent(config)}", self.script)
        self.assertIn("requestResource('alasStatus'", self.script)
        self.assertIn("Promise.allSettled", self.script)
        self.assertNotIn("uniqueAlasConfigs().map(loadAlasStatusForConfig", self.script)
        load_alas = self.script.split("async function loadAlas(options={})", 1)[1].split("function loadAlasCatalog", 1)[0]
        self.assertNotIn("/api/admin/alas/configs", load_alas)
        self.assertIn("loadIfNeeded('alasCatalog',loadAlasCatalog)", self.script)

    def test_each_config_has_one_exclusive_owner(self):
        combined = self.template + self.script
        for forbidden in (
            "共享配置",
            "共享实例",
            "同一配置也可以授权给多人",
            "多人获授权",
            "位授权用户",
        ):
            self.assertNotIn(forbidden, combined)

        for token in (
            'id="alasAssignmentOwnerHint"',
            'aria-describedby="alasAssignmentDrawerContext alasAssignmentOwnerHint"',
            "function configOwnership(configName)",
            "return !ownership.owners.length",
            "otherOwners=configOwnership(configName).owners.filter(owner=>owner!==username)",
            "不能直接分配给",
            "ownership.owner?`归属 ${ownership.owner}`:'未分配'",
            "$('alasCurrentUserCount').textContent=ownership.conflict?'需处理':ownership.owner?'已分配':'未分配'",
            "$('alasBindDefault').value='keep'",
        ):
            self.assertIn(token, combined)
        self.assertIn('.alas-owner-note[data-state="error"]', self.styles)

    def test_config_identity_errors_and_focus_are_explicit(self):
        for token in (
            "return name.endsWith('.json') ? name.slice(0,-5) : name;",
            "function requireAlasSuccess",
            "let alasConfigDrawerTarget = ''",
            "new AbortController()",
            "request.sequence===alasConfigReadSequence",
            "sequence===alasStatusSequence",
            "if(!resourceRequests.has('alasStatus'))",
            "data=>applyAlasDetails(data,statusSequence)",
            "source:config, target:config",
            "function focusedAlasChoiceKey",
            "function restoreAlasChoiceFocus",
            "button.setAttribute('role','option')",
            "button.setAttribute('aria-selected',String(selected))",
        ):
            self.assertIn(token, self.script)

        status_loader = self.script.split("async function loadAlasStatusForConfig", 1)[1].split(
            "function openAlasConnectionDrawer", 1
        )[0]
        self.assertNotIn("renderAlasUserList()", status_loader)
        self.assertNotIn("renderAlasUserDetail()", status_loader)
        self.assertNotIn("renderAlasConfigList()", status_loader)

    def test_empty_listboxes_long_names_and_copy_use_accessible_contracts(self):
        for token in (
            "function emptyListboxOption(text)",
            "option.setAttribute('role','option')",
            "option.setAttribute('aria-disabled','true')",
            "list.appendChild(emptyListboxOption(query || filter!=='all'",
            "list.appendChild(emptyListboxOption(query ? '没有匹配的配置。'",
            "Runtime 与归属记录中都没有配置。",
        ):
            self.assertIn(token, self.script)
        self.assertNotIn("Runtime 与授权记录", self.script + self.template)
        strong_rule = self.styles.split(".alas-choice__main strong {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-wrap: anywhere", strong_rule)
        self.assertIn("white-space: normal", strong_rule)

    def test_assignment_identity_has_clear_visual_hierarchy(self):
        for token in (
            'id="alasAssignmentSummary" class="alas-assignment-summary" role="group" aria-label="配置归属摘要"',
            'id="alasAssignmentUserName"',
            'id="alasAssignmentUserMeta"',
            'id="alasAssignmentConfigName"',
            'id="alasAssignmentConfigMeta"',
            "function syncAlasAssignmentSummary()",
            "kind:'用户账号'",
            "kind:'Runtime 配置'",
            "syncAlasAssignmentSummary();",
        ):
            self.assertIn(token, self.template + self.script)
        for token in (
            ".alas-assignment-summary",
            ".alas-choice__kind",
            "font-size: 15px",
            "font-size: 16px",
            "box-shadow: inset 3px 0 0 var(--ui-color-primary)",
        ):
            self.assertIn(token, self.styles)

    def test_promise_driven_config_and_toggle_guards(self):
        harness = textwrap.dedent(
            r"""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync(process.argv[2], 'utf8');

            function sliceBetween(start, end) {
              const from = source.indexOf(start);
              const to = source.indexOf(end, from + start.length);
              assert(from >= 0, `missing start: ${start}`);
              assert(to > from, `missing end: ${end}`);
              return source.slice(from, to);
            }
            function deferred() {
              let resolve;
              let reject;
              const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
              return {promise, resolve, reject};
            }

            globalThis.__elements = {
              configEditor: {value: ''},
              alasConfigDrawer: {classList: {contains: name => name === 'is-open' && globalThis.__drawerOpen}},
            };
            globalThis.__drawerOpen = true;
            globalThis.__messages = [];
            globalThis.__statusCalls = [];
            globalThis.__refreshCalls = [];
            globalThis.__closed = [];
            globalThis.__selected = '';
            globalThis.__apiImpl = () => Promise.reject(new Error('api mock missing'));
            globalThis.requestAnimationFrame = callback => callback();

            const selectedSource = `
              var alasToggleSequence = 0;
              var alasStatusLoading = false;
              var alasStatusLoadingConfig = '';
              var alasStatusSequence = 0;
              var alasConfigDrawerTarget = '';
              var alasConfigReadSequence = 0;
              var alasConfigReadController = null;
              var state = {alas: {}};
              function $(id){ return globalThis.__elements[id]; }
              function api(url, options){ return globalThis.__apiImpl(url, options || {}); }
              function show(message){ globalThis.__messages.push(message); }
              function selectedAlasConfig(){ return globalThis.__selected; }
              function assignmentsForConfig(){ return globalThis.__ownershipBindings || []; }
              function requestResource(name, request, apply){ return Promise.resolve(request(new AbortController().signal)).then(data => { apply(data || {}); return data; }); }
              function alasCatalog(){ return {}; }
              function renderAlasConfigDetail(){}
              function renderAlasSummary(){}
              function renderAlasUserList(){}
              function renderAlasUserDetail(){}
              function isAbortError(error){ return error && error.name === 'AbortError'; }
              function refreshDomains(...names){ globalThis.__refreshCalls.push(names); return Promise.resolve(); }
              function closeEditorDrawer(name){ globalThis.__closed.push(name); globalThis.__drawerOpen=false; invalidateAlasConfigRead(true); }
              ${sliceBetween('function configKey', 'function uniqueAlasConfigs')}
              ${sliceBetween('function configOwnership', 'function selectedAlasConfig')}
              ${sliceBetween('function focusedAlasChoiceKey', 'function createAlasChoice')}
              ${sliceBetween('async function loadAlasStatusForConfig', 'function openAlasConnectionDrawer')}
              ${sliceBetween('function requireAlasSuccess', 'function openAlasConfigDrawer')}
              ${sliceBetween('async function toggleAlas', 'async function loadConfig')}
              ${sliceBetween('async function loadConfig', 'async function saveAlasBinding')}
            `;
            vm.runInThisContext(selectedSource, {filename: 'admin-alas-behavior.js'});

            (async () => {
              assert.deepStrictEqual(uniqueNames(['Foo', 'foo', 'Foo']), ['Foo', 'foo']);
              assert.strictEqual(configKey(' Foo '), 'Foo');
              assert.strictEqual(configKey(' Foo.json '), 'Foo');
              assert.strictEqual(configKey('Foo.JSON'), 'Foo.JSON');
              assert.deepStrictEqual(uniqueNames(['Foo', 'Foo.json', 'foo']), ['Foo', 'foo']);
              globalThis.__ownershipBindings = [{username: 'alice'}, {username: 'alice'}];
              assert.deepStrictEqual(configOwnership('Foo'), {
                bindings: globalThis.__ownershipBindings,
                owners: ['alice'],
                owner: 'alice',
                conflict: false,
              });
              globalThis.__ownershipBindings = [{username: 'alice'}, {username: 'bob'}];
              assert.strictEqual(configOwnership('Foo').conflict, true);
              assert.deepStrictEqual(configOwnership('Foo').owners, ['alice', 'bob']);

              const focusLog = [];
              const choices = ['Foo', 'foo'].map((key, index) => ({
                dataset: {choiceKey: key},
                classList: {contains: name => name === 'is-selected' && index === 0},
                focus: () => focusLog.push(key),
              }));
              restoreAlasChoiceFocus({querySelectorAll: () => choices}, 'foo');
              assert.deepStrictEqual(focusLog, ['foo']);

              const statusA1 = deferred();
              const statusB = deferred();
              const statusA2 = deferred();
              const statusQueue = [statusA1, statusB, statusA2];
              globalThis.__apiImpl = () => statusQueue.shift().promise;
              globalThis.__selected = 'Foo';
              const pendingStatusA1 = loadAlasStatusForConfig('Foo');
              globalThis.__selected = 'foo';
              const pendingStatusB = loadAlasStatusForConfig('foo');
              globalThis.__selected = 'Foo';
              const pendingStatusA2 = loadAlasStatusForConfig('Foo');
              statusA1.resolve({status: {config: 'Foo'}});
              await pendingStatusA1;
              assert.strictEqual(globalThis.alasStatusLoading, true);
              statusB.resolve({status: {config: 'foo'}});
              await pendingStatusB;
              assert.strictEqual(globalThis.alasStatusLoading, true);
              statusA2.resolve({status: {config: 'Foo'}});
              await pendingStatusA2;
              assert.strictEqual(globalThis.alasStatusLoading, false);

              const readA = deferred();
              const readB = deferred();
              const readQueue = [readA, readB];
              globalThis.__apiImpl = () => readQueue.shift().promise;
              globalThis.__drawerOpen = true;
              globalThis.alasConfigDrawerTarget = 'Foo';
              const pendingA = loadConfig();
              invalidateAlasConfigRead(true);
              globalThis.alasConfigDrawerTarget = 'foo';
              const pendingB = loadConfig();
              readA.resolve({ok: true, data: {config: 'Foo'}});
              await pendingA;
              assert.strictEqual(globalThis.__elements.configEditor.value, '');
              readB.resolve({ok: true, data: {config: 'foo'}});
              await pendingB;
              assert.strictEqual(globalThis.__elements.configEditor.value, JSON.stringify({config: 'foo'}, null, 2));

              const beforeFailure = globalThis.__elements.configEditor.value;
              globalThis.__apiImpl = async () => ({ok: false, error: 'read denied'});
              globalThis.__drawerOpen = true;
              globalThis.alasConfigDrawerTarget = 'foo';
              await assert.rejects(loadConfig(), /read denied/);
              assert.strictEqual(globalThis.__elements.configEditor.value, beforeFailure);

              let savedRequest = null;
              globalThis.__elements.configEditor.value = JSON.stringify({safe: true});
              globalThis.__drawerOpen = true;
              globalThis.alasConfigDrawerTarget = 'foo';
              globalThis.__apiImpl = async (url, options) => {
                savedRequest = {url, options};
                return {ok: true};
              };
              await saveConfig();
              assert.strictEqual(savedRequest.options.body.source, 'foo');
              assert.strictEqual(savedRequest.options.body.target, 'foo');
              assert.deepStrictEqual(savedRequest.options.body.data, {safe: true});
              assert.deepStrictEqual(globalThis.__closed, ['alasConfig']);

              const toggleA = deferred();
              globalThis.loadAlasStatusForConfig = config => { globalThis.__statusCalls.push(config); return Promise.resolve(); };
              globalThis.__messages.length = 0;
              globalThis.__statusCalls.length = 0;
              globalThis.__refreshCalls.length = 0;
              globalThis.__selected = 'Foo';
              globalThis.__apiImpl = () => toggleA.promise;
              const pendingToggle = toggleAlas();
              globalThis.__selected = 'foo';
              toggleA.resolve({ok: true});
              await pendingToggle;
              assert.deepStrictEqual(globalThis.__statusCalls, []);
              assert.deepStrictEqual(globalThis.__refreshCalls, [['overview']]);
              assert(globalThis.__messages.some(message => message.includes('Foo') && message.includes('foo')));

              globalThis.__messages.length = 0;
              globalThis.__statusCalls.length = 0;
              globalThis.__selected = 'Foo';
              globalThis.__apiImpl = async () => ({ok: false, error: 'toggle denied'});
              await assert.rejects(toggleAlas(), /toggle denied/);
              assert.deepStrictEqual(globalThis.__statusCalls, []);
              assert.deepStrictEqual(globalThis.__messages, []);
            })().catch(error => {
              console.error(error && error.stack || error);
              process.exitCode = 1;
            });
            """
        )
        with tempfile.TemporaryDirectory(prefix="scrcpygate-admin-ui-") as temp_dir:
            harness_path = Path(temp_dir) / "admin-alas-behavior.cjs"
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(harness_path), str(ROOT / "static" / "js" / "admin.js")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_permission_epoch_rejects_old_reads_and_reconciles_out_of_order_writes(self):
        harness = textwrap.dedent(
            r"""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync(process.argv[2], 'utf8');

            function sliceBetween(start, end) {
              const from = source.indexOf(start);
              const to = source.indexOf(end, from + start.length);
              assert(from >= 0, `missing start: ${start}`);
              assert(to > from, `missing end: ${end}`);
              return source.slice(from, to);
            }
            function deferred() {
              let resolve;
              let reject;
              const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
              return {promise, resolve, reject};
            }

            globalThis.__apiImpl = () => Promise.reject(new Error('api mock missing'));
            globalThis.__stale = [];
            globalThis.__errors = [];
            const selectedSource = `
              var alasPermissionsEpoch = 0;
              var alasPermissionsMutations = 0;
              var alasPermissionsNeedsRefresh = false;
              var state = {alas: {}};
              var loadedResources = new Set();
              function api(url, options){ return globalThis.__apiImpl(url, options || {}); }
              function markResourceStale(name){ globalThis.__stale.push(name); }
              function requestResource(name, request, apply){
                return Promise.resolve(request(new AbortController().signal)).then(data => {
                  apply(data || {});
                  return data;
                });
              }
              function renderAlas(){}
              function renderOverview(){}
              function reportRequestError(error, prefix){ globalThis.__errors.push([prefix, error && error.message]); }
              ${sliceBetween('function applyAlasPermissions', 'function applyAlasCatalog')}
              ${sliceBetween('function loadAlasPermissions', 'function loadAlasDetails')}
            `;
            vm.runInThisContext(selectedSource, {filename: 'admin-alas-permission-epoch.js'});

            (async () => {
              const oldRead = deferred();
              const firstWrite = deferred();
              const firstQueue = [oldRead, firstWrite];
              globalThis.__apiImpl = () => firstQueue.shift().promise;
              const pendingRead = loadAlasPermissions();
              const pendingWrite = mutateAlasPermissions({username: 'alice', config_name: 'Foo'});
              oldRead.resolve({assignments: [{username: 'stale', config_name: 'Foo'}]});
              await pendingRead;
              assert.deepStrictEqual(state.alas.assignments, undefined);
              firstWrite.resolve({assignments: [{username: 'alice', config_name: 'Foo'}]});
              await pendingWrite;
              assert.deepStrictEqual(state.alas.assignments, [{username: 'alice', config_name: 'Foo'}]);

              state.alas = {};
              const writeA = deferred();
              const writeB = deferred();
              const authoritativeRead = deferred();
              const secondQueue = [writeA, writeB, authoritativeRead];
              const calls = [];
              globalThis.__apiImpl = (url, options) => {
                calls.push({url, method: options.method || 'GET'});
                return secondQueue.shift().promise;
              };
              const pendingA = mutateAlasPermissions({username: 'alice', config_name: 'Foo'});
              const pendingB = mutateAlasPermissions({username: 'bob', config_name: 'foo'});
              writeB.resolve({assignments: [{username: 'bob', config_name: 'foo'}]});
              await pendingB;
              assert.deepStrictEqual(state.alas.assignments, [{username: 'bob', config_name: 'foo'}]);
              authoritativeRead.resolve({assignments: [
                {username: 'alice', config_name: 'Foo'},
                {username: 'bob', config_name: 'foo'},
              ]});
              writeA.resolve({assignments: [{username: 'alice', config_name: 'Foo'}]});
              await pendingA;
              assert.deepStrictEqual(state.alas.assignments, [
                {username: 'alice', config_name: 'Foo'},
                {username: 'bob', config_name: 'foo'},
              ]);
              assert.deepStrictEqual(calls.map(call => call.method), ['PUT', 'PUT', 'GET']);
              assert.strictEqual(alasPermissionsMutations, 0);
              assert.strictEqual(alasPermissionsNeedsRefresh, false);
              assert.deepStrictEqual(globalThis.__errors, []);
            })().catch(error => {
              console.error(error && error.stack || error);
              process.exitCode = 1;
            });
            """
        )
        with tempfile.TemporaryDirectory(prefix="scrcpygate-admin-permission-epoch-") as temp_dir:
            harness_path = Path(temp_dir) / "admin-alas-permission-epoch.cjs"
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(harness_path), str(ROOT / "static" / "js" / "admin.js")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_drawers_and_mobile_layout_keep_accessibility_contract(self):
        for token in (
            'id="alasConnectionDrawer"',
            'id="alasAssignmentDrawer"',
            'id="alasConfigDrawer"',
            'aria-hidden="true" inert',
            'role="dialog"',
            'aria-modal="true"',
        ):
            self.assertIn(token, self.template)
        self.assertIn("const EDITOR_DRAWERS", self.script)
        self.assertIn("openEditorDrawer('alasAssignment'", self.script)
        self.assertIn(".alas-master-detail", self.styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", self.styles)
        self.assertIn("min-height: 44px", self.styles)


if __name__ == "__main__":
    unittest.main()
