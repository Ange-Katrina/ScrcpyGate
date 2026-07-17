import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AdminAccessUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")

    def test_access_workspace_has_compact_accessible_contract(self):
        for token in (
            'id="accessAccountsTab"',
            'id="accessPermissionsTab"',
            'id="accessPermissionDraftSummary"',
            'role="tablist"',
            'aria-controls="accessAccountsView"',
            'aria-controls="accessPermissionsView"',
            'id="accessAccountsView"',
            'role="tabpanel"',
            'aria-labelledby="accessAccountsTab"',
            'id="accessPermissionsView"',
            'aria-labelledby="accessPermissionsTab"',
            'id="userSearch"',
            'id="userRoleFilter"',
            'id="userExpiryFilter"',
            'id="userResultCount"',
            'id="userPagePrevious"',
            'id="userPageNext"',
            'id="userPageStatus"',
            'id="permissionUserSearch"',
            'id="permissionUserList"',
            'role="listbox"',
            'aria-orientation="vertical"',
            'id="permUser"',
            'aria-controls="permissionRows"',
            'id="permissionLoadState"',
            'id="permissionRetry"',
            'id="permissionAdminNotice"',
            'id="permissionDeviceSearch"',
            'id="permissionDeviceFilter"',
            'value="none"',
            'id="permissionRows"',
            'id="savePermission"',
            'id="userExpiryMode"',
            'id="userExpiresAtField"',
            'id="userExpiresAt"',
            'id="userExpiryShortcuts"',
            'id="userExpiryPreview"',
            'data-days="1"',
            'data-days="7"',
            'data-days="30"',
            'data-days="90"',
        ):
            self.assertIn(token, self.template)
        for removed in ('id="permDevice"', 'id="permView"', 'id="permControl"'):
            self.assertNotIn(removed, self.template)
        self.assertNotIn('aria-controls="permissionDeviceRows"', self.template)

    def test_access_lists_are_bounded_on_desktop_and_mobile(self):
        for token in (
            ".access-account-table-wrap",
            ".permission-user-list",
            ".permission-device-table-wrap",
            "grid-template-columns: minmax(220px, 240px) minmax(0, 1fr)",
            "max-height: min(520px, 58vh)",
            "max-height: 500px",
            "max-height: 480px",
            ".permission-user-pane__mobile",
            ".permission-user-pane__desktop",
            "max-height: 58dvh",
            ".permission-toggle",
            "min-height: 44px",
            ".access-toolbar input",
            ".user-expiry",
            ".user-expiry-shortcuts",
            ".user-expiry-preview",
            ".permission-user-pane__mobile select",
            ".permission-load-state[data-state=\"error\"]",
            ".access-view-tabs button.has-drafts small",
            ".permission-user-choice.has-drafts",
        ):
            self.assertIn(token, self.styles)
        generic_mobile = self.styles.index(".table-wrap {\n    overflow: visible;")
        permission_override = self.styles.rindex(".permission-device-table-wrap.table-wrap")
        self.assertGreater(permission_override, generic_mobile)

    def test_script_uses_single_user_devices_drafts_and_keyboard_navigation(self):
        for token in (
            "const USER_PAGE_SIZE = 20",
            "filtered.slice((userAccountPage-1)*USER_PAGE_SIZE,userAccountPage*USER_PAGE_SIZE)",
            "function effectivePermissionFor(user,deviceId)",
            "const permissionIndex = new Map()",
            "function rebuildPermissionIndex(permissions=state.permissions)",
            "return permissionIndex.get(permissionDraftKey(username,deviceId)) || null",
            "function permissionsAreReady()",
            "permissionsLoadPhase==='ready' && loadedResources.has('permissions')",
            "if(user && user.role==='admin') return {can_view:true,can_control:true}",
            "return state.devices.filter(device=>",
            "input.type='checkbox'",
            "label.className='permission-toggle'",
            "input.setAttribute('aria-label'",
            "function disabledPermissionOption(message)",
            "option.setAttribute('role','option')",
            "option.setAttribute('aria-disabled','true')",
            "option.setAttribute('aria-selected',String(selected))",
            "event.key==='ArrowDown'",
            "event.key==='Home'",
            "event.key==='End'",
            "event.key==='Enter'",
            "function syncPermissionDraftIndicators()",
            "function handlePermissionBeforeUnload(event)",
            "window.addEventListener('beforeunload',handlePermissionBeforeUnload)",
            "function userExpirationState(user, now=",
            "function epochToLocalInput(value)",
            "function localInputToEpoch(value)",
            "function extendUserExpiry(days)",
            "const USER_EXPIRY_REFRESH_INTERVAL = 30000",
            "function refreshUserExpirationStatuses()",
            "setInterval(refreshUserExpirationStatuses,USER_EXPIRY_REFRESH_INTERVAL)",
            "let editingUsername = ''",
            "$('newUsername').readOnly=true",
            "expires_at:expiresAt",
            "last_permanent_admin_required:'admin.api_error.last_permanent_admin_required'",
        ):
            self.assertIn(token, self.script)
        render_permissions = self.script.split("function renderPermissions", 1)[1].split("function alasBindings", 1)[0]
        self.assertNotIn("state.permissions.forEach", render_permissions)
        checkbox_change = self.script.split("input.onchange=()=>{", 1)[1].split("};", 1)[0]
        self.assertIn("syncPermissionDraftIndicators()", checkbox_change)
        self.assertIn("renderPermissionDetail", checkbox_change)
        self.assertNotIn("renderPermissionUserList", checkbox_change)
        self.assertIn("const permissionKnown=!!user && (isAdmin || ready)", self.script)
        self.assertIn("saveButton.disabled=!user || isAdmin || !ready", self.script)

    def test_permission_batch_mutation_has_independent_authoritative_refresh(self):
        for token in (
            "let permissionsEpoch = 0",
            "let permissionsMutations = 0",
            "let permissionsNeedsRefresh = false",
            "let alasPermissionsEpoch = 0",
            "function beginPermissionsMutation()",
            "permissionsEpoch+=1",
            "markResourceStale('permissions')",
            "if(permissionsMutations || epoch!==permissionsEpoch) return",
            "Promise.allSettled(changes.map(change=>api('/api/admin/permissions'",
            "await finishPermissionsMutation(epoch)",
            "await loadPermissions({force:true})",
            "permissionsLoadPhase='loading'",
            "permissionsLoadPhase='ready'",
            "permissionsLoadPhase='error'",
            "if(!isAbortError(error)",
        ):
            self.assertIn(token, self.script)
        save = self.script.split("async function savePermission", 1)[1].split("function collectVideoPresets", 1)[0]
        self.assertNotIn("applyPermissions(result", save)
        self.assertNotIn("refreshDomains('permissions')", save)

    def test_filters_effective_permissions_and_stale_read_reconciliation(self):
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
            async function ticks(count=3) {
              while(count--) await Promise.resolve();
            }

            const selectedSource = `
              var __elements = {
                userSearch: {value: ''},
                userRoleFilter: {value: 'all'},
                userExpiryFilter: {value: 'all'},
                permissionUserSearch: {value: ''},
                permissionDeviceSearch: {value: ''},
                permissionDeviceFilter: {value: 'all'},
              };
              var state = {users: [], devices: [], permissions: []};
              var selectedPermissionUsername = '';
              var permissionDrafts = new Map();
              var permissionIndex = new Map();
              var permissionsEpoch = 0;
              var permissionsMutations = 0;
              var permissionsNeedsRefresh = false;
              var permissionsLoadPhase = 'idle';
              var permissionsLoadError = '';
              var loadedResources = new Set(['users', 'devices', 'permissions']);
              var __calls = [];
              var __stale = [];
              var __messages = [];
              var __apiImpl = () => Promise.reject(new Error('api mock missing'));
              function $(id){ return __elements[id]; }
              function getDeviceId(device){ return String((device && (device.device_id || device.id || device.address)) || '').trim(); }
              function api(url, options){ return __apiImpl(url, options || {}); }
              function markResourceStale(name){ __stale.push(name); loadedResources.delete(name); }
              function requestResource(name, request, apply){
                return Promise.resolve(request(new AbortController().signal)).then(data => {
                  apply(data || {});
                  loadedResources.add(name);
                  return data;
                });
              }
              function isAbortError(error){ return error && error.name === 'AbortError'; }
              function show(message){ __messages.push(message); }
              function renderUsers(){}
              function renderDevices(){}
              function renderOverview(){}
              ${sliceBetween('function epochToLocalInput', 'function appendTableEmpty')}
              ${sliceBetween('function filteredAccountUsers', 'function renderUsers')}
              ${sliceBetween('function permissionDraftKey', 'function alasBindings')}
              ${sliceBetween('function applyPermissions', 'function applyVideo')}
              ${sliceBetween('function loadPermissions', 'function loadVideo')}
              ${sliceBetween('async function savePermission', 'function collectVideoPresets')}
              renderPermissionUserList = function(){};
              renderPermissionDetail = function(){};
              renderPermissions = function(){ reconcilePermissionDrafts(); };
            `;
            vm.runInThisContext(selectedSource, {filename: 'admin-access-behavior.js'});

            (async () => {
              const sampleEpoch = Math.floor(Date.now() / 1000);
              const roundTripEpoch = localInputToEpoch(epochToLocalInput(sampleEpoch));
              assert(Math.abs(roundTripEpoch - sampleEpoch) < 60);
              assert.strictEqual(userExpirationState({expires_at: null}, 1000), 'permanent');
              assert.strictEqual(userExpirationState({expires_at: 1000}, 1000), 'expired');
              assert.strictEqual(userExpirationState({expires_at: 1001}, 1000), 'expiring');
              assert.strictEqual(userExpirationState({expires_at: 1000 + 7 * 86400 + 1}, 1000), 'active');

              state.users = Array.from({length: 45}, (_, index) => ({
                username: index === 0 ? 'Admin' : `user-${String(index).padStart(2, '0')}`,
                role: index % 10 === 0 ? 'admin' : 'user',
              }));
              __elements.userSearch.value = 'USER-1';
              assert.strictEqual(filteredAccountUsers().length, 10);
              __elements.userSearch.value = '';
              __elements.userRoleFilter.value = 'admin';
              assert.strictEqual(filteredAccountUsers().length, 5);
              __elements.userRoleFilter.value = 'all';
              __elements.userExpiryFilter.value = 'expired';
              state.users[1].expires_at = Math.floor(Date.now() / 1000) - 1;
              assert.deepStrictEqual(filteredAccountUsers().map(user => user.username), ['user-01']);
              __elements.userExpiryFilter.value = 'all';

              const alice = {username: 'alice', role: 'user'};
              const admin = {username: 'admin', role: 'admin'};
              state.users = [alice, admin, {username: 'bob', role: 'user'}];
              state.devices = Array.from({length: 20}, (_, index) => ({id: `dev-${index}`, name: `Device ${index}`, enabled: true}));
              state.permissions = [
                {username: 'alice', device_id: 'dev-0', can_view: 1, can_control: 0},
                ...state.devices.map(device => ({username: 'bob', device_id: device.id, can_view: 1, can_control: 1})),
                {username: 'admin', device_id: 'dev-0', can_view: 0, can_control: 0},
              ];
              rebuildPermissionIndex();
              assert.strictEqual(permissionsAreReady(), false);
              permissionsLoadPhase = 'ready';
              assert.strictEqual(permissionsAreReady(), true);
              assert.deepStrictEqual(basePermissionFor(alice, 'dev-1'), {can_view: false, can_control: false});
              assert.deepStrictEqual(effectivePermissionFor(admin, 'dev-0'), {can_view: true, can_control: true});
              __elements.permissionDeviceFilter.value = 'all';
              assert.strictEqual(permissionDevicesFor(alice).length, 20);
              __elements.permissionDeviceFilter.value = 'none';
              assert.strictEqual(permissionDevicesFor(alice).length, 19);
              updatePermissionDraft(alice, 'dev-1', 'can_control', true);
              __elements.permissionDeviceFilter.value = 'control';
              assert.deepStrictEqual(permissionDevicesFor(alice).map(device => device.id), ['dev-1']);

              state.users = [alice];
              state.devices = [{id: 'dev-1'}, {id: 'dev-2'}];
              state.permissions = [];
              rebuildPermissionIndex();
              permissionsLoadPhase = 'ready';
              selectedPermissionUsername = 'alice';
              permissionDrafts.clear();
              permissionDrafts.set(permissionDraftKey('alice', 'dev-1'), {username: 'alice', device_id: 'dev-1', can_view: true, can_control: false});
              permissionDrafts.set(permissionDraftKey('alice', 'dev-2'), {username: 'alice', device_id: 'dev-2', can_view: true, can_control: true});

              const oldRead = deferred();
              const putOne = deferred();
              const putTwo = deferred();
              const authority = deferred();
              let getCount = 0;
              __calls.length = 0;
              __stale.length = 0;
              __apiImpl = (url, options) => {
                const method = options.method || 'GET';
                if(method === 'GET') {
                  getCount += 1;
                  __calls.push('GET');
                  return getCount === 1 ? oldRead.promise : authority.promise;
                }
                __calls.push(`PUT:${options.body.device_id}`);
                return (options.body.device_id === 'dev-1' ? putOne.promise : putTwo.promise)
                  .then(() => ({permissions: [{username: 'poison', device_id: 'ignored', can_view: 1, can_control: 1}]}));
              };

              const pendingOld = loadPermissions();
              await ticks();
              assert.deepStrictEqual(__calls, ['GET']);
              permissionsLoadPhase = 'ready';
              loadedResources.add('permissions');
              const pendingSave = savePermission();
              assert.deepStrictEqual(__calls, ['GET', 'PUT:dev-1', 'PUT:dev-2']);
              oldRead.resolve({permissions: [{username: 'stale', device_id: 'stale', can_view: 1, can_control: 1}]});
              await pendingOld;
              assert.deepStrictEqual(state.permissions, []);
              putTwo.resolve({ok: true});
              await ticks();
              assert.strictEqual(getCount, 1);
              putOne.resolve({ok: true});
              await ticks(6);
              assert.strictEqual(getCount, 2);
              authority.resolve({permissions: [
                {username: 'alice', device_id: 'dev-1', can_view: 1, can_control: 0},
                {username: 'alice', device_id: 'dev-2', can_view: 1, can_control: 1},
              ]});
              await pendingSave;
              assert.deepStrictEqual(__calls, ['GET', 'PUT:dev-1', 'PUT:dev-2', 'GET']);
              assert.strictEqual(permissionsMutations, 0);
              assert.strictEqual(permissionDrafts.size, 0);
              assert.deepStrictEqual(state.permissions.map(item => item.device_id), ['dev-1', 'dev-2']);
              assert.strictEqual(__stale.filter(name => name === 'permissions').length, 2);

              permissionDrafts.set(permissionDraftKey('alice', 'dev-1'), {username: 'alice', device_id: 'dev-1', can_view: false, can_control: false});
              __calls.length = 0;
              __apiImpl = (url, options) => {
                const method = options.method || 'GET';
                __calls.push(method);
                if(method === 'PUT') return Promise.reject(new Error('write failed'));
                return Promise.resolve({permissions: state.permissions});
              };
              await assert.rejects(savePermission(), /1 .*保存失败/);
              assert.deepStrictEqual(__calls, ['PUT', 'GET']);
              assert.strictEqual(permissionsMutations, 0);

              const abortError = new Error('aborted');
              abortError.name = 'AbortError';
              permissionsLoadPhase = 'idle';
              __apiImpl = () => Promise.reject(abortError);
              await assert.rejects(loadPermissions(), /aborted/);
              assert.notStrictEqual(permissionsLoadPhase, 'error');
              __apiImpl = () => Promise.reject(new Error('network down'));
              await assert.rejects(loadPermissions(), /network down/);
              assert.strictEqual(permissionsLoadPhase, 'error');
              assert.strictEqual(permissionsLoadError, 'network down');
            })().catch(error => {
              console.error(error && error.stack || error);
              process.exitCode = 1;
            });
            """
        )
        with tempfile.TemporaryDirectory(prefix="scrcpygate-admin-access-") as temp_dir:
            harness_path = Path(temp_dir) / "admin-access-behavior.cjs"
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


if __name__ == "__main__":
    unittest.main()
