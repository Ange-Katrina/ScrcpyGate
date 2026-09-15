/* Extracted from static/pages/devices.html. */
(function () {
      'use strict';
      var dashboard = window.ScrcpyGateDashboard || {};

      /* ===== 数据模型：仅由真实接口填充 ===== */
      var DEVICES = [];
      var devicesLoaded = false;
      var devicesLoading = false;

      /* ===== 全局状态 ===== */
      var selectedId = null;
      var query = '';
      var filter = 'all';
      var mirrorFilter = 'all';
      var modalMode = 'add'; /* add / edit */
      var editingId = null;
      var lastSyncText = '从未';
      var connCheckText = '—';
      /* 仪表盘跳转定位：/devices?device=<public id 或内部 id>（与 /logs?event_id= 同一约定） */
      var requestedDeviceRef = '';
      // 宫格空坑位 / 「添加 ADB 设备」按钮用 ?new=1 直达新增抽屉。
      var requestedCreate = false;
      try {
        var bootParams = new URLSearchParams(window.location.search);
        requestedDeviceRef = bootParams.get('device') || '';
        requestedCreate = bootParams.get('new') === '1';
      } catch (e) { requestedDeviceRef = ''; requestedCreate = false; }
      var pendingHighlightId = null;

      /* ===== 工具函数 ===== */
      function $(id) { return document.getElementById(id); }
      function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      }
      function icon(name) { return '<i data-lucide="' + name + '"></i>'; }
      function tr(value) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(String(value == null ? '' : value)) : String(value == null ? '' : value); }
      function refreshIcons() { if (window.lucide) { lucide.createIcons(); } }
      function nowTime() {
        var d = new Date();
        function p(n) { return (n < 10 ? '0' : '') + n; }
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      }
      function devById(id) { for (var i = 0; i < DEVICES.length; i++) { if (DEVICES[i].id === id) { return DEVICES[i]; } } return null; }
      /* ===== 深层定位（仪表盘「打开」→ 该设备的详情） ===== */
      function deviceMatchesRef(d, ref) {
        if (!d || !ref) { return false; }
        return String(d.id) === ref || String(d.internalId) === ref;
      }
      function findDeviceByRef(ref) {
        for (var i = 0; i < DEVICES.length; i++) {
          if (deviceMatchesRef(DEVICES[i], ref)) { return DEVICES[i]; }
        }
        return null;
      }
      // 只消费一次：命中就选中该设备，未命中给出可读提示，两种情况都清掉 URL 参数，
      // 避免刷新页面时重复滚动/重复弹提示。
      function consumeRequestedDevice() {
        var ref = requestedDeviceRef;
        if (!ref) { return; }
        requestedDeviceRef = '';
        if (window.history && window.history.replaceState) {
          try { window.history.replaceState(null, '', window.location.pathname); } catch (e) { /* 忽略 */ }
        }
        var match = findDeviceByRef(ref);
        if (!match) { toast('未找到该设备，可能已被删除或你没有访问权限', 'error'); return; }
        selectedId = match.id;
        pendingHighlightId = match.id;
      }
      function consumeRequestedCreate() {
        if (!requestedCreate) { return; }
        requestedCreate = false;
        if (window.history && window.history.replaceState) {
          try { window.history.replaceState(null, '', window.location.pathname); } catch (e) { /* 忽略 */ }
        }
        openAddDrawer();
      }
      function listRowFor(id) {
        try { return document.querySelector('#devList .alas-list-button[data-did="' + String(id).replace(/"/g, '\\"') + '"]'); } catch (e) { return null; }
      }
      // 命中行可能被搜索/筛选挡住：先按当前筛选找，找不到就重置筛选再找一次。
      function revealRequestedDevice() {
        if (!pendingHighlightId) { return; }
        var id = pendingHighlightId;
        pendingHighlightId = null;
        var row = listRowFor(id);
        if (!row) {
          query = '';
          filter = 'all';
          mirrorFilter = 'all';
          if ($('devSearch')) { $('devSearch').value = ''; }
          if ($('devFilter')) { $('devFilter').value = 'all'; }
          if ($('devMirrorFilter')) { $('devMirrorFilter').value = 'all'; }
          renderList();
          row = listRowFor(id);
        }
        if (!row) { return; }
        row.classList.add('is-focused');
        if (row.focus) { try { row.focus({ preventScroll: true }); } catch (e) { row.focus(); } }
        if (row.scrollIntoView) { row.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
        window.setTimeout(function () { row.classList.remove('is-focused'); }, 2400);
      }
      function apiError(error) { return window.ScrcpyGateApi ? window.ScrcpyGateApi.errorMessage(error) : '数据服务不可用'; }
      function normalizeDeviceStatus(d) {
        d = d || {};
        if (d.enabled === false) return 'disabled';
        var raw = String(d.status || d.state || d.health || '').trim().toLowerCase();
        if (!raw && typeof d.online === 'boolean') return d.online ? 'online' : 'offline';
        if (['online', 'healthy', 'connected', 'running', 'ready', 'ok'].indexOf(raw) >= 0) return 'online';
        if (['offline', 'disconnected'].indexOf(raw) >= 0) return 'offline';
        if (['connecting', 'pending'].indexOf(raw) >= 0) return 'connecting';
        if (['check-fail', 'check_failed', 'error', 'failed', 'unreachable'].indexOf(raw) >= 0) return 'check-fail';
        return 'unknown';
      }
      function normalizeDevice(d) {
        d = d || {};
        var status = normalizeDeviceStatus(d);
        return {
          id: d.id,
          internalId: d.internalId || d.internal_id || d.real_device_id || d.id,
          name: d.name || d.displayName || d.serial || d.id || '未命名设备',
          adb: d.adb || d.address || d.endpoint || '',
          note: d.note || d.description || '',
          enabled: d.enabled !== false,
          status: status,
          latency: d.latency == null ? null : d.latency,
          heartbeat: d.heartbeat || d.lastSeen || '—',
          viewers: Number(d.viewers || d.viewerCount || 0),
          controller: d.controller || d.controllerName || null,
          streaming: d.streaming === true || d.mirroring === true,
          lastError: d.lastError || d.error || null,
          statusDetail: d.statusDetail || d.adbDetail || d.adb_detail || '',
          lastOfflineReason: d.lastOfflineReason || d.offlineReason || null,
          alasCfg: d.alasCfg || d.alasConfig || null,
          noPermission: d.noPermission === true || d.permission === false,
          sessions: Array.isArray(d.sessions) ? d.sessions : []
        };
      }
      function loadDevices() {
        if (devicesLoading) return devicesLoading;
        devicesLoading = window.ScrcpyGateApi.configured('devices.list', { query: { include: 'sessions,status' } }).then(function (payload) {
          DEVICES = window.ScrcpyGateApi.list(payload).map(normalizeDevice).filter(function (d) { return !!d.id; });
          devicesLoaded = true;
          $('devErrorBar').hidden = true;
          consumeRequestedDevice();
          if (!selectedId && DEVICES.length) selectedId = DEVICES[0].id;
          lastSyncText = nowTime();
          renderAll();
          revealRequestedDevice();
          consumeRequestedCreate();
          return DEVICES;
        }).catch(function (error) {
          devicesLoaded = false;
          $('devErrorBarText').textContent = apiError(error);
          $('devErrorBar').hidden = false;
          DEVICES = [];
          selectedId = null;
          renderAll();
          throw error;
        }).finally(function () { devicesLoading = false; });
        return devicesLoading;
      }
      function devInit(d) { var n = d.name || '?'; return esc(n.substring(0, 1).toUpperCase()); }
      function setLoading(btn, on, label) {
        if (!btn) { return; }
        if (on) {
          if (!btn.getAttribute('data-dev-orig')) { btn.setAttribute('data-dev-orig', btn.innerHTML); }
          btn.classList.add('loading');
          var l = (label === '' ? '' : (label ? esc(label) : '处理中…'));
          btn.innerHTML = '<span class="alas-spinner"></span>' + l;
        } else {
          btn.classList.remove('loading');
          var orig = btn.getAttribute('data-dev-orig');
          if (orig) { btn.innerHTML = orig; btn.removeAttribute('data-dev-orig'); }
        }
      }
      function toast(msg, type) {
        var wrap = $('devToastWrap');
        if (!wrap) { return; }
        var t = document.createElement('div');
        t.className = 'alas-toast ' + (type || 'info');
        t.innerHTML = icon(type === 'success' ? 'circle-check' : (type === 'error' ? 'circle-x' : 'info')) + '<span>' + esc(msg) + '</span>';
        wrap.appendChild(t);
        refreshIcons();
        requestAnimationFrame(function () { t.classList.add('show'); });
        setTimeout(function () {
          t.classList.add('out');
          setTimeout(function () { if (t.parentNode) { t.parentNode.removeChild(t); } }, 260);
        }, 3000);
      }

      /* ===== 抽屉控制（新增 / 编辑设备） ===== */
      function openDrawer() {
        var drawer = $('devDrawer'), mask = $('devDrawerMask');
        drawer.classList.add('open');
        mask.classList.add('open');
        drawer.setAttribute('aria-hidden', 'false');
        drawer.removeAttribute('inert');
        mask.hidden = false;
        refreshIcons();
        setTimeout(function () { $('devFormName').focus(); }, 60);
      }
      function closeDrawer() {
        var drawer = $('devDrawer'), mask = $('devDrawerMask');
        drawer.classList.remove('open');
        mask.classList.remove('open');
        drawer.setAttribute('aria-hidden', 'true');
        drawer.setAttribute('inert', '');
        setTimeout(function () { mask.hidden = true; }, 190);
      }

      var accessState = { permissions: [], permissionBaseline: {}, users: [], alas: [], configs: [], activeTab: 'users', grantContext: '' };
      function closeAccessDrawer() {
        var drawer = $('devAccessDrawer'), mask = $('devAccessMask');
        if (!drawer) return;
        drawer.classList.remove('open'); mask.classList.remove('open');
        drawer.setAttribute('aria-hidden', 'true'); drawer.setAttribute('inert', '');
        setTimeout(function () { mask.hidden = true; $('devAccessBtn').focus(); }, 190);
      }
      function showAccessError(message) {
        $('devAccessError').textContent = message || '保存失败';
        $('devAccessError').hidden = false;
      }
      function currentAccessDevice() { return selectedId ? devById(selectedId) : null; }
      function isCurrentAccessDevice(value) {
        var device = currentAccessDevice();
        value = String(value || '');
        return !!value && (
          value === String(selectedId || '') ||
          !!(device && value === String(device.internalId || ''))
        );
      }
      function accessPermissionFor(username) {
        var device = currentAccessDevice();
        return (accessState.permissions || []).filter(function (item) {
          return item.username === username && (
            String(item.public_device_id || '') === String(selectedId || '') ||
            String(item.device_id || '') === String(selectedId || '') ||
            String(item.device_id || '') === String(device && device.internalId || '')
          );
        })[0] || {};
      }
      function stageAccessPermission(username, canView, canControl) {
        var permission = accessPermissionFor(username);
        if (!permission.username) {
          permission = { username: username, public_device_id: selectedId, device_id: String(currentAccessDevice() && currentAccessDevice().internalId || ''), assigned: true };
          accessState.permissions.push(permission);
        }
        permission.can_view = !!canView || !!canControl;
        permission.can_control = !!canControl;
        permission.assigned = permission.can_view || permission.can_control;
        updateAccessCounts();
        updateAccessSaveState();
      }
      function snapshotAccessPermissions() {
        var baseline = {};
        (accessState.users || []).filter(function (user) { return user.role !== 'admin'; }).forEach(function (user) {
          var permission = accessPermissionFor(user.username);
          baseline[user.username] = { canView: !!permission.can_view, canControl: !!permission.can_control };
        });
        accessState.permissionBaseline = baseline;
      }
      function accessPermissionChanges() {
        return (accessState.users || []).filter(function (user) { return user.role !== 'admin'; }).map(function (user) {
          var permission = accessPermissionFor(user.username);
          var current = { canView: !!permission.can_view, canControl: !!permission.can_control };
          var baseline = accessState.permissionBaseline[user.username] || { canView: false, canControl: false };
          return current.canView !== baseline.canView || current.canControl !== baseline.canControl
            ? { username: user.username, canView: current.canView, canControl: current.canControl }
            : null;
        }).filter(Boolean);
      }
      function accessBindingsForDevice() {
        return (accessState.alas || []).filter(function (binding) { return isCurrentAccessDevice(binding.device_id); });
      }
      function updateAccessCounts() {
        var granted = (accessState.users || []).filter(function (user) {
          return user.role !== 'admin' && accessPermissionFor(user.username).can_view;
        }).length;
        var bindings = accessBindingsForDevice().length;
        $('devAccessUsersCount').textContent = String(granted);
        $('devAccessAlasCount').textContent = String(bindings);
        $('devAccessDeviceMeta').textContent = granted + ' 位用户可观看 · ' + bindings + ' 个 ALAS 关联';
      }
      function switchAccessTab(tab, focus) {
        accessState.activeTab = tab === 'alas' ? 'alas' : 'users';
        document.querySelectorAll('[data-access-tab]').forEach(function (button) {
          var active = button.getAttribute('data-access-tab') === accessState.activeTab;
          button.setAttribute('aria-selected', active ? 'true' : 'false');
          button.setAttribute('tabindex', active ? '0' : '-1');
          if (active && focus) button.focus({ preventScroll: true });
        });
        $('devAccessUsersPanel').hidden = accessState.activeTab !== 'users';
        $('devAccessAlasPanel').hidden = accessState.activeTab !== 'alas';
        $('devAccessSave').querySelector('span').textContent = accessState.activeTab === 'users' ? '保存用户访问' : '创建关联';
        updateAccessComposer();
      }
      /* 停用/已到期的账号即使写了权限也不生效（服务端 user_can 先看账户状态），
         所以在授权列表里直接标出来，避免管理员以为已经生效。 */
      function accountInactive(user) {
        if (!user) return false;
        if (user.enabled === false) return true;
        var state = String(user.expiration_state || user.expirationState || '').toLowerCase();
        return state === 'expired' || state === 'disabled';
      }
      function renderAccessUsers() {
        var query = String($('devAccessSearch').value || '').trim().toLowerCase();
        var rows = (accessState.users || []).filter(function (u) {
          return u.role !== 'admin' && (!query || String(u.username || '').toLowerCase().indexOf(query) >= 0);
        }).map(function (u) {
          var p = accessPermissionFor(u.username);
          var status = p.can_control ? '观看与控制' : p.can_view ? '仅观看' : '未授权';
          if (p.can_view && accountInactive(u)) status += ' · 账号未生效';
          return '<div class="dev-access-row" data-access-user="' + esc(u.username) + '">'
            + '<div class="dev-access-row-main"><span class="dev-access-avatar">' + esc(String(u.username || '?').charAt(0).toUpperCase()) + '</span><div><strong>' + esc(u.username) + '</strong><small>' + status + '</small></div></div>'
            + '<div class="dev-access-controls">'
            + '<label class="dev-access-toggle"><span>观看</span><input type="checkbox" data-access-view ' + (p.can_view ? 'checked' : '') + '><span class="dev-access-toggle-track"></span></label>'
            + '<label class="dev-access-toggle dependent"><span>控制</span><input type="checkbox" data-access-control ' + (p.can_control ? 'checked' : '') + (!p.can_view ? ' disabled' : '') + '><span class="dev-access-toggle-track"></span></label>'
            + '</div></div>';
        }).join('');
        $('devAccessUsers').innerHTML = rows || '<div class="dev-access-empty">' + (query ? '没有匹配的用户' : '暂无普通用户') + '</div>';
        $('devAccessUsers').querySelectorAll('[data-access-control]').forEach(function (control) {
          control.addEventListener('change', function () {
            var row = control.closest('[data-access-user]');
            var view = row.querySelector('[data-access-view]');
            if (control.checked) view.checked = true;
            stageAccessPermission(row.getAttribute('data-access-user'), view.checked, control.checked);
            row.querySelector('small').textContent = control.checked ? '观看与控制' : '仅观看';
          });
        });
        $('devAccessUsers').querySelectorAll('[data-access-view]').forEach(function (view) {
          view.addEventListener('change', function () {
            var row = view.closest('[data-access-user]');
            var control = row.querySelector('[data-access-control]');
            if (!view.checked) control.checked = false;
            control.disabled = !view.checked;
            stageAccessPermission(row.getAttribute('data-access-user'), view.checked, control.checked);
            row.querySelector('small').textContent = view.checked ? (control.checked ? '观看与控制' : '仅观看') : '未授权';
          });
        });
      }
      function renderAccessAlas() {
        var configs = accessState.configs || [];
        $('devAccessConfig').innerHTML = '<option value="">选择配置</option>' + configs.map(function (name) { return '<option value="' + esc(name) + '">' + esc(name) + '</option>'; }).join('');
        var users = (accessState.users || []).filter(function (u) { return u.role !== 'admin'; });
        $('devAccessUser').innerHTML = '<option value="">选择用户</option>' + users.map(function (u) { return '<option value="' + esc(u.username) + '">' + esc(u.username) + '</option>'; }).join('');
        var bindings = accessBindingsForDevice();
        $('devAccessBindings').innerHTML = bindings.length ? bindings.map(function (b) {
          var meta = esc(b.username) + ' · ' + (b.can_run ? '可运行' : '不可运行') + (b.can_edit ? ' · 可编辑' : '')
            + (b.effective === false ? ' · 账号未生效' : '');
          return '<div class="dev-access-row"><div class="dev-access-row-main"><span class="dev-access-avatar alas"><i data-lucide="bot"></i></span><div><strong>' + esc(b.config_name) + '</strong><small>' + meta + '</small></div></div><button class="dev-access-unbind" type="button" data-unbind-alas="' + esc(b.config_name) + '" data-unbind-user="' + esc(b.username) + '" aria-label="解除 ' + esc(b.config_name) + ' 关联" title="解除关联"><i data-lucide="link-2-off"></i></button></div>';
        }).join('') : '<div class="dev-access-empty">当前设备暂无 ALAS 绑定</div>';
        updateAccessCounts();
        updateAccessComposer();
        refreshIcons();
        $('devAccessBindings').querySelectorAll('[data-unbind-alas]').forEach(function (button) {
          button.addEventListener('click', function () {
            var req = { username: button.getAttribute('data-unbind-user'), configName: button.getAttribute('data-unbind-alas'), deviceId: selectedId, enabled: false };
            window.ScrcpyGateApi.configured('devices.alas.bind', { method: 'PUT', body: req }).then(loadAccessData).then(function () { toast('ALAS 绑定已解除', 'success'); }).catch(function (error) { showAccessError(apiError(error)); });
          });
        });
      }
      function updateAccessComposer() {
        var username = $('devAccessUser').value;
        var configName = $('devAccessConfig').value;
        var permission = username ? accessPermissionFor(username) : {};
        var needsView = !!username && !permission.can_view;
        var context = username + ':' + String(selectedId || '');
        if (context !== accessState.grantContext) {
          $('devAccessGrantView').checked = needsView;
          accessState.grantContext = context;
        }
        $('devAccessGrantNotice').hidden = !needsView;
        var conflict = configName ? (accessState.alas || []).filter(function (binding) {
          return binding.config_name === configName && (
            binding.username !== username || !isCurrentAccessDevice(binding.device_id)
          );
        })[0] : null;
        $('devAccessConflictNotice').hidden = !conflict;
        if (conflict) {
          $('devAccessConflictText').textContent = conflict.username !== username
            ? '该配置已关联给用户 ' + conflict.username + '，请先解除原关联。'
            : '该配置当前绑定到其他设备。保存时不会静默覆盖。';
        }
        updateAccessSaveState();
      }
      function updateAccessSaveState() {
        var disabled = accessState.activeTab === 'users'
          ? accessPermissionChanges().length === 0
          : (!$('devAccessUser').value || !$('devAccessConfig').value);
        $('devAccessSave').disabled = disabled;
      }
      function loadAccessData() {
        return Promise.all([
          window.ScrcpyGateApi.configured('permissions.catalog', { method: 'GET' }),
          window.ScrcpyGateApi.configured('alas.permissions.catalog', { method: 'GET' }),
          window.ScrcpyGateApi.configured('alas.catalog', { method: 'GET' })
        ]).then(function (items) {
          accessState.permissions = items[0].permissions || [];
          accessState.users = items[0].users || items[1].users || [];
          accessState.alas = items[1].assignments || items[1].bindings || [];
          accessState.configs = (items[2].configs || items[1].configs || []).map(function (item) { return typeof item === 'string' ? item : (item.config_name || item.name || ''); }).filter(Boolean);
          (accessState.alas || []).forEach(function (binding) {
            if (accessState.configs.indexOf(binding.config_name) < 0) accessState.configs.push(binding.config_name);
          });
          snapshotAccessPermissions();
          renderAccessUsers();
          renderAccessAlas();
          updateAccessCounts();
        });
      }
      function openAccessDrawer() {
        var device = currentAccessDevice();
        if (!device) { toast('请先选择设备', 'error'); return Promise.resolve(); }
        $('devAccessError').hidden = true;
        $('devAccessTitle').textContent = '权限与绑定';
        $('devAccessSubtitle').textContent = '分别管理用户访问和 ALAS 关联';
        $('devAccessDeviceName').textContent = device.name;
        $('devAccessDeviceMeta').textContent = '正在读取访问关系…';
        $('devAccessSearch').value = '';
        accessState.grantContext = '';
        switchAccessTab('users');
        var drawer = $('devAccessDrawer'), mask = $('devAccessMask');
        drawer.classList.add('open'); mask.classList.add('open'); mask.hidden = false; drawer.setAttribute('aria-hidden', 'false'); drawer.removeAttribute('inert');
        refreshIcons();
        setTimeout(function () { $('devAccessSearch').focus(); }, 60);
        return loadAccessData().catch(function (error) {
          accessState = { permissions: [], permissionBaseline: {}, users: [], alas: [], configs: [], activeTab: 'users', grantContext: '' };
          renderAccessUsers(); renderAccessAlas();
          showAccessError('权限数据加载失败：' + apiError(error));
        });
      }
      function saveAccessUsers() {
        var changes = accessPermissionChanges();
        if (!changes.length) return Promise.reject(new Error('没有需要保存的用户访问变更'));
        return Promise.allSettled(changes.map(function (change) {
          return window.ScrcpyGateApi.configured('devices.permissions.update', { method: 'PUT', body: {
            username: change.username, deviceId: selectedId,
            canView: change.canView,
            canControl: change.canControl,
            enabled: change.canView || change.canControl
          }});
        })).then(function (results) {
          var failed = results.filter(function (result) { return result.status === 'rejected'; });
          return loadAccessData().then(function () {
            if (failed.length) throw new Error(failed.length + ' 项用户访问保存失败，请检查后重试');
            return { changed: changes.length };
          });
        });
      }
      /* 改绑到本设备＝把配置从原设备移动过来：服务端先回 409（结构化 detail），
         这里弹出二次确认，确认后带 confirmMove 重试，不再静默搬家。 */
      function alasMoveConfirm(error) {
        var payload = error && error.detail && error.detail.payload;
        var detail = payload && payload.detail;
        if (detail && typeof detail === 'object' && detail.code === 'alas_binding_move_confirm') return detail;
        return null;
      }
      function saveAccessAlas(options) {
        options = options || {};
        var configName = $('devAccessConfig').value, username = $('devAccessUser').value;
        if (!username) return Promise.reject(new Error('请选择用户'));
        if (!configName) return Promise.reject(new Error('请选择 Runtime 配置'));
        var conflict = (accessState.alas || []).filter(function (binding) {
          return binding.config_name === configName && binding.username !== username;
        })[0];
        if (conflict) return Promise.reject(new Error('该配置已关联给用户 ' + conflict.username + '，请先解除原关联'));
        var body = {
          username: username, configName: configName, deviceId: selectedId,
          canRun: $('devAccessRun').checked, canEdit: $('devAccessEdit').checked,
          grantView: $('devAccessGrantView').checked, enabled: true
        };
        if (options.confirmMove) body.confirmMove = true;
        return window.ScrcpyGateApi.configured('devices.alas.bind', { method: 'PUT', body: body })
          .catch(function (error) {
            var move = alasMoveConfirm(error);
            if (!move || options.confirmMove) throw error;
            var accepted = window.confirm((move.message || '该配置当前绑定到其他设备，保存会把它移动过来。') + '\n\n确定移动并保存？');
            if (!accepted) throw error;
            return saveAccessAlas({ confirmMove: true });
          });
      }
      document.querySelectorAll('[data-close-drawer]').forEach(function (btn) {
        btn.addEventListener('click', function () { closeDrawer(); });
      });
      $('devDrawerMask').addEventListener('click', closeDrawer);
      $('devAccessClose').addEventListener('click', closeAccessDrawer);
      $('devAccessCancel').addEventListener('click', closeAccessDrawer);
      $('devAccessMask').addEventListener('click', closeAccessDrawer);
      var accessTabs = Array.prototype.slice.call(document.querySelectorAll('[data-access-tab]'));
      accessTabs.forEach(function (button, index) {
        button.addEventListener('click', function () { switchAccessTab(button.getAttribute('data-access-tab')); });
        button.addEventListener('keydown', function (event) {
          if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(event.key) < 0) return;
          event.preventDefault();
          var next = event.key === 'Home' ? 0 : event.key === 'End' ? accessTabs.length - 1
            : event.key === 'ArrowRight' ? (index + 1) % accessTabs.length
              : (index - 1 + accessTabs.length) % accessTabs.length;
          switchAccessTab(accessTabs[next].getAttribute('data-access-tab'), true);
        });
      });
      $('devAccessSearch').addEventListener('input', renderAccessUsers);
      $('devAccessUser').addEventListener('change', updateAccessComposer);
      $('devAccessConfig').addEventListener('change', updateAccessComposer);
      $('devAccessSave').addEventListener('click', function () {
        var btn = $('devAccessSave'); setLoading(btn, true, '保存中…'); $('devAccessError').hidden = true;
        var savingAlas = accessState.activeTab === 'alas';
        var task = savingAlas ? saveAccessAlas() : saveAccessUsers();
        task.then(function () {
          if (savingAlas) {
            $('devAccessUser').value = '';
            $('devAccessConfig').value = '';
            accessState.grantContext = '';
          }
          return savingAlas ? Promise.all([loadAccessData(), loadDevices()]) : loadDevices();
        }).then(function () {
          toast(savingAlas ? 'ALAS 关联已创建' : '用户访问已保存', 'success');
        }).catch(function (error) {
          showAccessError(apiError(error));
        }).finally(function () {
          setLoading(btn, false);
          updateAccessComposer();
        });
      });

      /* ===== 弹窗控制 ===== */
      function openModal(id) {
        var modal = $(id), mask = $(id + 'Mask');
        modal.classList.add('open');
        mask.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
        modal.hidden = false;
        mask.hidden = false;
        refreshIcons();
      }
      function closeModal(id) {
        var modal = $(id), mask = $(id + 'Mask');
        modal.classList.remove('open');
        mask.classList.remove('open');
        setTimeout(function () {
          modal.hidden = true;
          mask.hidden = true;
          modal.setAttribute('aria-hidden', 'true');
        }, 190);
      }
      document.querySelectorAll('[data-close-modal]').forEach(function (btn) {
        btn.addEventListener('click', function () { closeModal(btn.getAttribute('data-close-modal')); });
      });
      document.querySelectorAll('.alas-modal-mask').forEach(function (mask) {
        mask.addEventListener('click', function () {
          var id = mask.id.replace('Mask', '');
          closeModal(id);
        });
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          closeDrawer();
          closeModal('devDeleteModal'); closeModal('devDisableModal');
        }
      });

      /* ===== 状态模型 ===== */
      function statusMeta(d) {
        var st = d.status;
        var text = dashboard.statusLabel ? dashboard.statusLabel(st) : (
          st === 'disabled' ? '已禁用' : (st === 'online' ? '在线' : (st === 'offline' ? '离线' : (st === 'connecting' ? '连接中' : (st === 'check-fail' ? '检测失败' : '未检查'))))
        );
        if (st === 'disabled') { return { chip: 'dev-chip-disabled', text: text }; }
        if (st === 'online') { return { chip: 'dev-chip-online', text: text }; }
        if (st === 'offline') { return { chip: 'dev-chip-offline', text: text }; }
        if (st === 'connecting') { return { chip: 'dev-chip-connecting', text: text }; }
        if (st === 'check-fail') { return { chip: 'dev-chip-checkfail', text: text }; }
        return { chip: 'dev-chip-unknown', text: text };
      }

      /* ===== 统计 ===== */
      function computeStats() {
        var total = DEVICES.length, online = 0, offline = 0, disabled = 0, connecting = 0, checkFail = 0, unknown = 0, streaming = 0, viewers = 0, i;
        for (i = 0; i < DEVICES.length; i++) {
          var d = DEVICES[i];
          if (d.status === 'online') { online++; }
          else if (d.status === 'offline') { offline++; }
          else if (d.status === 'disabled') { disabled++; }
          else if (d.status === 'connecting') { connecting++; }
          else if (d.status === 'check-fail') { checkFail++; }
          else if (d.status === 'unknown') { unknown++; }
          if (d.streaming) { streaming++; }
          viewers += d.viewers || 0;
        }
        return { total: total, online: online, offline: offline, disabled: disabled, connecting: connecting, checkFail: checkFail, unknown: unknown, streaming: streaming, viewers: viewers };
      }
      function renderStats() {
        var s = computeStats();
        var od = $('devStatusOnlineDot');
        od.className = 'alas-status-dot ' + (s.online > 0 ? 'ok' : '');
        $('devStatusOnline').textContent = s.online + ' / ' + s.total + ' 台';
        $('devStatusStreaming').textContent = s.streaming + ' 台设备';
        $('devStatusViewers').textContent = s.viewers + ' 个客户端';
        var attention = s.offline + s.disabled + s.connecting + s.checkFail;
        $('devStatusAttention').textContent = attention + ' 台需处理' + (s.unknown ? ' · ' + s.unknown + ' 台未检查' : '');
        $('devStatusAttentionWrap').classList.toggle('warning', attention > 0);
        $('devStatusSync').textContent = lastSyncText === '从未' ? '从未' : '今天 ' + lastSyncText;
      }

      /* ===== 列表渲染 ===== */
      function listMeta(d) {
        var m = statusMeta(d);
        if (d.status === 'online' && d.streaming) { return '<span class="dev-status-chip dev-chip-online"><span class="mini-dot"></span>' + esc(tr('投屏中')) + '</span>'; }
        if (d.status === 'online') { return '<span class="dev-status-chip dev-chip-online"><span class="mini-dot"></span>' + esc(m.text) + '</span>'; }
        if (d.status === 'check-fail' && !d.adb) { return '<span class="dev-status-chip dev-chip-unconfigured"><span class="mini-dot"></span>' + esc(tr('未配置')) + '</span>'; }
        if (d.noPermission) { return '<span class="dev-status-chip dev-chip-noperm"><span class="mini-dot"></span>' + esc(tr('无权限')) + '</span>'; }
        return '<span class="dev-status-chip ' + m.chip + '"><span class="mini-dot"></span>' + m.text + '</span>';
      }
      function renderList() {
        var list = $('devList');
        var out = '';
        var q = query.toLowerCase();
        var i;
        for (i = 0; i < DEVICES.length; i++) {
          var d = DEVICES[i];
          var st = d.status;
          if (filter === 'online' && st !== 'online') { continue; }
          if (filter === 'offline' && st !== 'offline') { continue; }
          if (filter === 'disabled' && st !== 'disabled') { continue; }
          if (filter === 'connecting' && st !== 'connecting') { continue; }
          if (filter === 'check-fail' && st !== 'check-fail') { continue; }
          if (filter === 'unknown' && st !== 'unknown') { continue; }
          if (mirrorFilter === 'mirroring' && !d.streaming) { continue; }
          if (mirrorFilter === 'idle' && d.streaming) { continue; }
          if (q) {
            var hay = ((d.name || '') + ' ' + (d.adb || '') + ' ' + (d.note || '')).toLowerCase();
            if (hay.indexOf(q) === -1) { continue; }
          }
          var sub = (d.note || '未设置备注') + ' · ' + (d.adb || '未配置 ADB');
          var sel = selectedId === d.id ? ' active' : '';
          var metaExtra = (d.status === 'online' && d.latency != null) ? '<span class="ping">' + esc(d.latency) + ' ms</span>' : '';
          out += '<button class="alas-list-button' + sel + '" type="button" data-did="' + esc(d.id) + '">'
            + '<span class="alas-list-avatar">' + devInit(d) + '</span>'
            + '<span class="alas-list-main"><strong>' + esc(d.name) + '</strong><span>' + esc(sub) + '</span></span>'
            + '<span class="alas-list-meta">' + listMeta(d) + metaExtra + '</span>'
            + '</button>';
        }
        if (!out) {
          if (query || filter !== 'all' || mirrorFilter !== 'all') {
            out = '<div class="alas-list-empty">' + icon('search-x') + '<div>没有匹配的设备</div></div>';
          } else {
            out = '<div class="alas-list-empty">' + icon('smartphone') + '<div>暂未登记任何设备</div>'
              + '<button class="btn-primary dev-btn-sm" id="devEmptyAddBtn" type="button"><i data-lucide="plus"></i><span>新增设备</span></button>'
              + '</div>';
          }
        }
        list.innerHTML = out;
        var emptyAdd = $('devEmptyAddBtn');
        if (emptyAdd) {
          emptyAdd.addEventListener('click', function () { openAddDrawer(); });
        }
        refreshIcons();
      }

      /* ===== 详情渲染 ===== */
      function fact(id, val, cls) {
        var el = $(id);
        el.textContent = val;
        el.className = cls || '';
      }
      function renderDetail() {
        var d = selectedId ? devById(selectedId) : null;
        if (!d) {
          $('devDetailAvatar').textContent = '—';
          $('devDetailName').textContent = '请选择设备';
          $('devDetailMeta').textContent = '—';
          $('devDetailChips').innerHTML = '';
          $('devEditBtn').disabled = true;
          $('devToggleBtn').disabled = true;
          $('devCheckConnBtn').disabled = true;
          $('devRefreshOneBtn').disabled = true;
          $('devReleaseCtrlBtn').disabled = true;
          $('devStopStreamBtn').disabled = true;
          $('devDeleteBtn').disabled = true;
          ['devFactOnline','devFactLatency','devFactViewers','devFactController','devFactStreaming','devFactEnabled','devFactAlas','devFactError'].forEach(function (id) { $(id).textContent = '—'; });
          $('devWarningBand').hidden = true;
          $('devSessionSummary').textContent = '—';
          $('devSessionList').innerHTML = '<div class="dev-session-empty">' + icon('monitor-play') + '<div>请先在左侧选择设备</div></div>';
          $('devAlasLinkCopy').textContent = '当前未绑定 ALAS 配置';
          return;
        }
        var m = statusMeta(d);
        var chips = '';
        chips += '<span class="chip ' + (m.chip === 'dev-chip-online' ? 'ok' : (m.chip === 'dev-chip-offline' ? 'danger' : (m.chip === 'dev-chip-disabled' ? 'warn' : (m.chip === 'dev-chip-connecting' ? 'info' : (m.chip === 'dev-chip-checkfail' ? 'danger' : ''))))) + '">' + m.text + '</span>';
        if (d.streaming) { chips += '<span class="chip cyan"><span class="mini-dot"></span>投屏中</span>'; }
        else if (d.status === 'online') { chips += '<span class="chip">空闲</span>'; }
        if (d.noPermission) { chips += '<span class="chip">无权限</span>'; }
        if (d.status === 'check-fail' && !d.adb) { chips += '<span class="chip">未配置</span>'; }

        $('devDetailAvatar').textContent = devInit(d);
        $('devDetailName').textContent = d.name;
        $('devDetailMeta').textContent = 'ADB ' + (d.adb || '未配置') + ' · 最近心跳 ' + (d.heartbeat || '—');
        $('devDetailChips').innerHTML = chips;

        var isDisabled = d.status === 'disabled';
        var toggleBtn = $('devToggleBtn');
        toggleBtn.disabled = false;
        if (isDisabled) {
          toggleBtn.innerHTML = icon('power') + '<span>启用设备</span>';
          toggleBtn.classList.add('danger');
        } else {
          toggleBtn.innerHTML = icon('power') + '<span>停用设备</span>';
          toggleBtn.classList.remove('danger');
        }
        $('devEditBtn').disabled = false;
        $('devDeleteBtn').disabled = false;

        fact('devFactOnline', m.text, m.chip === 'dev-chip-online' ? 'ok' : (m.chip === 'dev-chip-offline' || m.chip === 'dev-chip-checkfail' ? 'err' : (m.chip === 'dev-chip-disabled' ? 'warn' : '')));
        fact('devFactLatency', d.latency != null ? d.latency + ' ms' : '--', d.latency != null ? 'ok' : '');
        fact('devFactViewers', d.viewers > 0 ? d.viewers + ' 个' : '无观看端');
        fact('devFactController', d.controller ? d.controller + ' 控制中' : '无人控制');
        fact('devFactStreaming', d.streaming ? '投屏中' : '空闲', d.streaming ? 'ok' : '');
        fact('devFactEnabled', d.enabled ? '已启用' : '已停用', d.enabled ? 'ok' : 'warn');
        fact('devFactAlas', d.alasCfg || '未关联');
        var errorLabel = $('devFactErrorLabel');
        var hasDiagnostic = !d.lastError && !!d.statusDetail;
        if (errorLabel) errorLabel.textContent = hasDiagnostic ? '连接诊断' : '最近错误';
        var errText = d.lastError || (hasDiagnostic ? '已连接 · ' + d.statusDetail : '无错误');
        fact('devFactError', errText, d.lastError ? 'err' : '');
        var fErrEl = $('devFactError');
        fErrEl.textContent = errText;
        fErrEl.className = d.lastError ? 'err' : '';

        /* 警告条：显示最近错误或离线原因 */
        var warn = $('devWarningBand');
        var warnText = d.lastError || (d.lastOfflineReason ? '设备离线：' + d.lastOfflineReason : '');
        if (warnText && d.status !== 'disabled') {
          warn.hidden = false;
          $('devWarningText').textContent = warnText;
        } else {
          warn.hidden = true;
        }

        /* 观看会话 */
        var sessions = d.sessions || [];
        $('devSessionSummary').textContent = sessions.length ? sessions.length + ' 个观看端 · ' + (d.controller ? d.controller + ' 拥有控制权' : '无人控制') : '暂无观看会话';
        if (!sessions.length) {
          $('devSessionList').innerHTML = '<div class="dev-session-empty">' + icon('monitor-play') + '<div>该设备暂无观看会话</div></div>';
        } else {
          var out = '';
          for (var i = 0; i < sessions.length; i++) {
            var s = sessions[i];
            var isControl = s.role === 'control';
            var roleCls = isControl ? 'info' : 'watch';
            var roleTxt = isControl ? '控制' : '观看';
            out += '<div class="dev-session-row">'
              + '<div class="dev-session-main"><strong>' + esc(s.name) + '</strong>'
              + '<span class="dev-session-client">' + esc((s.client || '观看中') + ' · ' + s.since + ' 开始') + '</span></div>'
              + '<div class="dev-session-side">'
              + '<span class="dev-session-latency">' + esc(s.latency) + ' ms</span>'
              + '<span class="dev-session-role ' + roleCls + '">' + roleTxt + '</span>'
              + '</div></div>';
          }
          $('devSessionList').innerHTML = out;
        }

        /* 关联入口 */
        $('devAlasLinkCopy').textContent = d.alasCfg ? '绑定 ' + d.alasCfg + ' 配置' : '当前未绑定 ALAS 配置';

        var canCheck = d.enabled && d.status !== 'disabled';
        $('devCheckConnBtn').disabled = !canCheck;
        $('devRefreshOneBtn').disabled = !canCheck;
        $('devReleaseCtrlBtn').disabled = !canCheck;
        $('devStopStreamBtn').disabled = !canCheck || !d.streaming;

        refreshIcons();
      }

      function renderAll() {
        renderStats();
        renderList();
        renderDetail();
        refreshIcons();
      }

      /* ===== 连接检测 ===== */
      function runConnCheck() {
        if (!selectedId) { return; }
        var d = devById(selectedId);
        if (!d || d.status === 'disabled' || !d.enabled) {
          toast('禁用设备不可检测连接', 'error');
          return;
        }
        if (!d.adb) {
          toast('该设备 ADB 地址未配置，无法检测', 'error');
          return;
        }
        var btn = $('devCheckConnBtn');
        setLoading(btn, true, '检测中…');
        window.ScrcpyGateApi.configured('devices.check', { params: { id: d.id }, method: 'POST', body: { address: d.adb } }).then(function (payload) {
          var result = payload && (payload.device || payload.data || payload);
          if (result && typeof result === 'object') Object.assign(d, normalizeDevice(Object.assign({}, d, result)));
          connCheckText = nowTime();
          lastSyncText = nowTime();
          renderAll();
          toast('连接检测完成', 'success');
        }).catch(function (error) { refreshError(apiError(error)); }).finally(function () { setLoading(btn, false); });
      }

      /* ===== 刷新（全部 / 单个） ===== */
      function refreshError(message) {
        $('devErrorBarText').textContent = message || '设备状态刷新失败，请稍后重试';
        $('devErrorBar').hidden = false;
        toast($('devErrorBarText').textContent, 'error');
      }
      function refreshAll() {
        var btns = [$('devRefreshAllBtn'), $('devMasterRefreshBtn')];
        btns.forEach(function (b) { setLoading(b, true, ''); });
        loadDevices().then(function () { $('devErrorBar').hidden = true; toast('设备状态已刷新', 'success'); }).catch(function () {}).finally(function () { btns.forEach(function (b) { setLoading(b, false); }); });
      }
      function refreshOne() {
        if (!selectedId) { return; }
        var d = devById(selectedId);
        if (!d || d.status === 'disabled' || !d.enabled) {
          toast('禁用设备不可刷新状态', 'error');
          return;
        }
        var btn = $('devRefreshOneBtn');
        setLoading(btn, true, '刷新中…');
        window.ScrcpyGateApi.configured('devices.check', { params: { id: d.id }, method: 'POST', body: { address: d.adb } }).then(function (payload) {
          var result = payload && (payload.device || payload.data || payload);
          if (result && typeof result === 'object') Object.assign(d, normalizeDevice(Object.assign({}, d, result)));
          lastSyncText = nowTime();
          renderAll();
          toast('设备状态已刷新', 'success');
        }).catch(function (error) { refreshError(apiError(error)); }).finally(function () { setLoading(btn, false); });
      }

      /* ===== 新增 / 编辑 ===== */
      function openAddDrawer() {
        modalMode = 'add';
        editingId = null;
        $('devDrawerTitle').textContent = '新增设备';
        $('devFormSaveBtn').innerHTML = icon('check') + '<span>保存设备</span>';
        $('devFormName').value = '';
        $('devFormAdb').value = '';
        $('devFormNote').value = '';
        $('devFormEnabled').checked = true;
        clearFormError();
        openDrawer();
      }
      function openEditDrawer() {
        if (!selectedId) { toast('请先选择设备', 'error'); return; }
        var d = devById(selectedId);
        if (!d) { return; }
        modalMode = 'edit';
        editingId = d.id;
        $('devDrawerTitle').textContent = '编辑设备';
        $('devFormSaveBtn').innerHTML = icon('check') + '<span>保存修改</span>';
        $('devFormName').value = d.name;
        $('devFormAdb').value = d.adb || '';
        $('devFormNote').value = d.note || '';
        $('devFormEnabled').checked = !!d.enabled;
        clearFormError();
        openDrawer();
      }
      function clearFormError() {
        $('devFormError').hidden = true;
        ['devFormName', 'devFormAdb'].forEach(function (id) { $(id).classList.remove('err'); });
      }
      function showFormError(msg) {
        $('devFormError').textContent = msg;
        $('devFormError').hidden = false;
      }
      function saveForm() {
        var name = $('devFormName').value.trim();
        var adb = $('devFormAdb').value.trim();
        var ok = true;
        if (!name) { $('devFormName').classList.add('err'); ok = false; }
        if (!adb) { $('devFormAdb').classList.add('err'); ok = false; }
        if (!ok) { showFormError(tr('带 * 的字段为必填项')); return; }
        // 网络 ADB 地址必须带端口：只有点号没有冒号通常是漏写端口。
        if (adb.indexOf(':') < 0 && adb.indexOf('.') >= 0) {
          $('devFormAdb').classList.add('err');
          showFormError(tr('ADB 地址需为 主机:端口 格式，例如 192.0.2.10:5555（USB 设备直接填序列号）'));
          return;
        }
        var btn = $('devFormSaveBtn');
        setLoading(btn, true, '保存中…');
        var body = { name: name, address: adb, note: $('devFormNote').value.trim(), enabled: $('devFormEnabled').checked };
        var request = modalMode === 'add'
          ? window.ScrcpyGateApi.configured('devices.create', { method: 'POST', body: body })
          : window.ScrcpyGateApi.configured('devices.update', { params: { id: editingId }, method: 'PUT', body: body });
        request.then(function (payload) {
          var result = payload && (payload.device || payload.data || payload);
          var normalized = normalizeDevice(result);
          if (modalMode === 'add' && normalized.id) { DEVICES.push(normalized); selectedId = normalized.id; }
          else { var existing = devById(editingId); if (existing && result) Object.assign(existing, normalized); }
          closeDrawer(); renderAll(); toast('设备信息已保存', 'success');
        }).catch(function (error) { showFormError(apiError(error)); toast('设备保存失败', 'error'); }).finally(function () { setLoading(btn, false); });
      }

      /* ===== 删除 / 停用 / 启用 ===== */
      function openDeleteModal() {
        if (!selectedId) { toast('请先选择设备', 'error'); return; }
        var d = devById(selectedId);
        if (!d) { return; }
        $('devDeleteBody').innerHTML = '确定删除设备「<strong>' + esc(d.name) + '</strong>」？删除后该设备将从列表中移除，且无法恢复。';
        openModal('devDeleteModal');
      }
      function confirmDelete() {
        if (!selectedId) { closeModal('devDeleteModal'); return; }
        var d = devById(selectedId);
        var btn = $('devDeleteConfirmBtn');
        setLoading(btn, true, '删除中…');
        window.ScrcpyGateApi.configured('devices.delete', { params: { id: selectedId }, method: 'DELETE' }).then(function () {
          var name = d ? d.name : '设备';
          DEVICES = DEVICES.filter(function (x) { return x.id !== selectedId; });
          selectedId = DEVICES.length ? DEVICES[0].id : null;
          closeModal('devDeleteModal'); renderAll(); toast('设备「' + name + '」已删除', 'success');
        }).catch(function (error) { toast(apiError(error), 'error'); }).finally(function () { setLoading(btn, false); });
      }
      function openDisableModal() {
        if (!selectedId) { toast('请先选择设备', 'error'); return; }
        var d = devById(selectedId);
        if (!d) { return; }
        if (d.status === 'disabled') {
          /* 停用设备的按钮为“启用”，直接启用 */
          window.ScrcpyGateApi.configured('devices.update', { params: { id: d.id }, method: 'PATCH', body: { enabled: true } }).then(function (payload) {
            Object.assign(d, normalizeDevice(Object.assign({}, d, payload && (payload.device || payload.data || payload)))); renderAll(); toast('设备已启用', 'success');
          }).catch(function (error) { toast(apiError(error), 'error'); });
          return;
        }
        $('devDisableBody').innerHTML = '确定停用设备「<strong>' + esc(d.name) + '</strong>」？停用后该设备将不可投屏，且无法进行连接检测与状态刷新。';
        openModal('devDisableModal');
      }
      function confirmDisable() {
        if (!selectedId) { closeModal('devDisableModal'); return; }
        var d = devById(selectedId);
        var btn = $('devDisableConfirmBtn');
        setLoading(btn, true, '停用中…');
        window.ScrcpyGateApi.configured('devices.update', { params: { id: selectedId }, method: 'PATCH', body: { enabled: false } }).then(function (payload) {
          if (d) Object.assign(d, normalizeDevice(Object.assign({}, d, payload && (payload.device || payload.data || payload))));
          closeModal('devDisableModal'); renderAll(); toast('设备已停用', 'success');
        }).catch(function (error) { toast(apiError(error), 'error'); }).finally(function () { setLoading(btn, false); });
      }

      /* ===== 事件绑定 ===== */
      $('devAddBtn').addEventListener('click', openAddDrawer);
      $('devEditBtn').addEventListener('click', openEditDrawer);
      $('devToggleBtn').addEventListener('click', openDisableModal);
      $('devDeleteBtn').addEventListener('click', openDeleteModal);
      $('devCheckConnBtn').addEventListener('click', runConnCheck);
      $('devRefreshOneBtn').addEventListener('click', refreshOne);
      $('devReleaseCtrlBtn').addEventListener('click', function () {
        if (!selectedId) { return; }
        var d = devById(selectedId);
        if (!d || !canOperate(d)) { return; }
        if (!d.controller) { toast('当前无人控制该设备', 'info'); return; }
        window.ScrcpyGateApi.configured('sessions.control.release', { params: { deviceId: d.id }, method: 'POST' }).then(function () { return loadDevices(); }).then(function () { toast('控制权已释放', 'success'); }).catch(function (error) { toast(apiError(error), 'error'); });
      });
      $('devStopStreamBtn').addEventListener('click', function () {
        if (!selectedId) { return; }
        var d = devById(selectedId);
        if (!d || !canOperate(d)) { return; }
        if (!d.streaming) { toast('该设备当前未投屏', 'info'); return; }
        window.ScrcpyGateApi.configured('sessions.stop', { params: { deviceId: d.id, admin: true }, method: 'POST' }).then(function () { return loadDevices(); }).then(function () { toast('投屏会话已停止', 'success'); }).catch(function (error) { toast(apiError(error), 'error'); });
      });
      $('devRefreshAllBtn').addEventListener('click', refreshAll);
      $('devMasterRefreshBtn').addEventListener('click', refreshAll);
      $('devErrorRetryBtn').addEventListener('click', refreshAll);
      $('devFormSaveBtn').addEventListener('click', saveForm);
      $('devDeleteConfirmBtn').addEventListener('click', confirmDelete);
      $('devDisableConfirmBtn').addEventListener('click', confirmDisable);
      $('devSearch').addEventListener('input', function () {
        query = this.value;
        renderList();
      });
      $('devFilter').addEventListener('change', function () {
        filter = this.value;
        renderList();
      });
      $('devMirrorFilter').addEventListener('change', function () {
        mirrorFilter = this.value;
        renderList();
      });
      function canOperate(d) {
        if (!d.enabled || d.status === 'disabled') { toast('禁用设备不可操作', 'error'); return false; }
        return true;
      }
      $('devList').addEventListener('click', function (e) {
        var btn = e.target.closest('.alas-list-button');
        if (!btn) { return; }
        var did = btn.getAttribute('data-did');
        selectedId = did;
        renderList();
        renderDetail();
      });
      $('devAlasLinkBtn').addEventListener('click', function (e) {
        e.preventDefault();
        openAccessDrawer();
      });
      $('devAccessBtn').addEventListener('click', openAccessDrawer);
      $('devUsersLinkBtn').addEventListener('click', function (e) {
        e.preventDefault();
        openAccessDrawer();
      });
      $('devLogsLinkBtn').addEventListener('click', function (e) {
        e.preventDefault();
        var d = selectedId ? devById(selectedId) : null;
        toast((d ? '正在查看设备「' + d.name + '」的相关日志' : '正在查看设备日志'), 'success');
        window.setTimeout(function () { window.location.href = '/logs'; }, 400);
      });

      /* ===== 初始化：等待真实设备接口 ===== */
      var listEl = $('devList');
      var sk = '';
      for (var si = 0; si < 4; si++) {
        sk += '<div class="dev-skeleton-item"><div class="dev-sk-avatar"></div><div class="dev-sk-line"><span class="alas-skeleton"></span><span class="alas-skeleton alas-skeleton-line"></span></div></div>';
      }
      listEl.innerHTML = '<div class="dev-skeleton-list">' + sk + '</div>';
      refreshIcons();
      renderAll();
      loadDevices().catch(function () {});
      if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
        window.ScrcpyGateI18n.on(function () { renderAll(); });
      }
    })();
