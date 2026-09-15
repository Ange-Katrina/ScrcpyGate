/* Extracted from static/pages/users.html. */
(function () {
      'use strict';
      if (window.lucide) { lucide.createIcons(); }
      var dashboard = window.ScrcpyGateDashboard || {};

      /* ---------- 数据模型：仅由真实接口填充 ---------- */
      var DEVICE_POOL = [];
      var ALAS_POOL = [];
      var USERS = [];
      var usersLoading = false;
      var PAGE_SIZE = 5;
      var state = { q: '', role: 'all', status: 'all', page: 1 };
      var EMPTY_ROW_HTML = '<tr id="users-empty"><td colspan="10"><div class="table-empty"><i data-lucide="user-x"></i><span>未找到匹配的用户</span><button class="empty-btn" type="button" id="clear-filters"><i data-lucide="rotate-ccw"></i>清除筛选</button></div></td></tr>';

      /* ---------- 工具 ---------- */
      function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
          return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
      }
      function userById(id) { for (var i = 0; i < USERS.length; i++) { if (USERS[i].id === id) return USERS[i]; } return null; }
      function deviceById(id) { for (var i = 0; i < DEVICE_POOL.length; i++) { if (DEVICE_POOL[i].id === id) return DEVICE_POOL[i]; } return null; }
      function configById(id) { for (var i = 0; i < ALAS_POOL.length; i++) { if (ALAS_POOL[i].id === id) return ALAS_POOL[i]; } return null; }
      function apiError(error) { return window.ScrcpyGateApi ? window.ScrcpyGateApi.errorMessage(error) : '数据服务不可用'; }
      function catalogStatus(value) {
        if (value && typeof value === 'object') {
          if (typeof value.online === 'boolean') return value.online ? 'online' : 'offline';
          value = value.status || value.state || value.health;
        }
        var state = String(value == null ? '' : value).trim().toLowerCase();
        if (['online', 'healthy', 'connected', 'running', 'ready', 'ok'].indexOf(state) >= 0) return 'online';
        if (['offline', 'unreachable', 'disconnected', 'error', 'disabled', 'failed'].indexOf(state) >= 0) return 'offline';
        return 'unknown';
      }
      function catalogStatusChip(state, alas) {
        var suffix = alas ? (state === 'online' ? 'alas-ok' : (state === 'offline' ? 'alas-err' : 'alas-none')) : state;
        var text = dashboard.statusLabel ? dashboard.statusLabel(state) : (state === 'online' ? '在线' : (state === 'offline' ? '离线' : '未检查'));
        return '<span class="status-chip ' + suffix + '"><span class="mini-dot"></span>' + text + '</span>';
      }
      function normalizeUser(u) {
        u = u || {};
        var rawWatchStats = u.watchStats || u.watch_stats || {};
        var rawWatchHistory = Array.isArray(u.watchHistory) ? u.watchHistory : (Array.isArray(u.watch_history) ? u.watch_history : []);
        return {
          id: u.id,
          username: u.username || u.userName || u.email || u.id || '—',
          name: u.name || u.displayName || u.username || '—',
          role: u.role || 'user',
          enabled: u.enabled !== false,
          status: u.enabled === false ? 'disabled' : (u.status === 'disabled' ? 'disabled' : (u.status || computeStatus(u.expiry))),
          expiry: u.expiry || u.expiresAt || '',
          remainingDays: u.remainingDays == null ? (u.remaining_seconds == null ? null : Math.ceil(Number(u.remaining_seconds) / 86400)) : Number(u.remainingDays),
          expirationState: u.expirationState || u.expiration_state || '',
          lastLoginAt: u.lastLoginAt || u.last_login_at || null,
          lastLoginIp: u.lastLoginIp || u.last_login_ip || '',
          watchStats: {
            sessionCount: Number(rawWatchStats.sessionCount != null ? rawWatchStats.sessionCount : rawWatchStats.session_count) || 0,
            totalDurationMs: Math.max(0, Number(rawWatchStats.totalDurationMs != null ? rawWatchStats.totalDurationMs : rawWatchStats.total_duration_ms) || 0),
            lastStartedAtMs: rawWatchStats.lastStartedAtMs != null ? rawWatchStats.lastStartedAtMs : (rawWatchStats.last_started_at_ms || null),
            lastEndedAtMs: rawWatchStats.lastEndedAtMs != null ? rawWatchStats.lastEndedAtMs : (rawWatchStats.last_ended_at_ms || null),
            lastWatchedAtMs: rawWatchStats.lastWatchedAtMs != null ? rawWatchStats.lastWatchedAtMs : (rawWatchStats.last_watched_at_ms || null),
            activeSessions: Number(rawWatchStats.activeSessions != null ? rawWatchStats.activeSessions : rawWatchStats.active_sessions) || 0
          },
          watchHistory: rawWatchHistory.map(function (item) {
            item = item || {};
            return {
              id: String(item.id || ''),
              deviceName: String(item.deviceName || item.device_name || '未知设备'),
              startedAtMs: item.startedAtMs != null ? item.startedAtMs : item.started_at_ms,
              endedAtMs: item.endedAtMs != null ? item.endedAtMs : item.ended_at_ms,
              durationMs: Math.max(0, Number(item.durationMs != null ? item.durationMs : item.duration_ms) || 0),
              endReason: String(item.endReason || item.end_reason || ''),
              active: item.active === true
            };
          }),
          deviceIds: u.role === 'admin' || u.deviceIds === null ? null : (u.deviceIds || (u.devices || []).map(function (d) { return d.id || d; })),
          devicePermissions: u.role === 'admin' ? null : (u.devicePermissions || []).map(function (permission) {
            return {
              deviceId: String(permission.deviceId || permission.device_id || ''),
              canView: permission.canView !== false && permission.can_view !== false,
              canControl: permission.canControl === true || permission.can_control === true
            };
          }).filter(function (permission) { return !!permission.deviceId; }),
          configIds: u.role === 'admin' || u.configIds === null ? null : (u.configIds || (u.configs || []).map(function (c) { return c.id || c; }))
        };
      }
      function loadUsers() {
        if (usersLoading) return usersLoading;
        usersLoading = window.ScrcpyGateApi.configured('users.list', { query: { include: 'devices,alas' } }).then(function (payload) {
          var data = window.ScrcpyGateApi.list(payload);
          USERS = data.map(normalizeUser).filter(function (u) { return !!u.id; });
          // 选择器必须来自完整目录，而不是只从已有授权记录反推。
          // 否则新设备/新配置永远无法授予尚无记录的用户。
          DEVICE_POOL = (Array.isArray(payload.devices) ? payload.devices : []).map(function (d) {
            var status = catalogStatus(d);
            return { id: String(d.id || d.public_id || d.device_id || ''), name: d.name || d.display_name || d.id, model: d.model || '—', status: status, online: status === 'online' ? true : (status === 'offline' ? false : null) };
          }).filter(function (d) { return !!d.id; });
          ALAS_POOL = (Array.isArray(payload.configs) ? payload.configs : []).map(function (c) {
            var id = typeof c === 'string' ? c : (c.id || c.config_name || c.name);
            var status = catalogStatus(c);
            return { id: String(id || ''), name: typeof c === 'string' ? c : (c.name || c.config_name || id), status: status, online: status === 'online' ? true : (status === 'offline' ? false : null) };
          }).filter(function (c) { return !!c.id; });
          render();
          return USERS;
        }).catch(function (error) {
          USERS = []; DEVICE_POOL = []; ALAS_POOL = [];
          render(); showToast(apiError(error), 'error'); throw error;
        }).finally(function () { usersLoading = false; });
        return usersLoading;
      }
      function avatarLetter(name) { var a = Array.from(String(name || '?')); return a.length ? a[0] : '?'; }
      function statusLabel(s) { return dashboard.statusLabel ? dashboard.statusLabel(s) : (s === 'expired' ? '已到期' : (s === 'expiring' ? '即将到期' : (s === 'disabled' ? '已停用' : '正常'))); }
      function deviceCountText(u) { return u.role === 'admin' ? '全部设备' : (u.deviceIds.length + ' 台设备'); }
      function configCountText(u) { return u.role === 'admin' ? '全部 ALAS' : (u.configIds.length + ' 个配置'); }
      function computeStatus(expiry) {
        var today = new Date(); today.setHours(0, 0, 0, 0);
        var d = new Date(String(expiry) + 'T00:00:00');
        if (isNaN(d.getTime())) return 'normal';
        var days = Math.round((d - today) / 86400000);
        if (days < 0) return 'expired';
        if (days <= 30) return 'expiring';
        return 'normal';
      }
      function formatLoginAt(value) {
        if (!value) return '从未登录';
        var raw = String(value).trim();
        var date;
        if (/^[+-]?\d+(?:\.\d+)?$/.test(raw)) {
          var numeric = Number(raw);
          date = new Date(Math.abs(numeric) >= 1000000000000 ? numeric : numeric * 1000);
        } else {
          date = new Date(raw.replace(' ', 'T'));
        }
        if (isNaN(date.getTime())) return String(value);
        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0') + ' ' + String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
      }
      function formatWatchDuration(value) {
        var seconds = Math.max(0, Math.floor(Number(value || 0) / 1000));
        if (seconds < 60) return seconds + ' 秒';
        var minutes = Math.floor(seconds / 60);
        var rest = seconds % 60;
        if (minutes < 60) return minutes + ' 分' + (rest ? ' ' + rest + ' 秒' : '');
        var hours = Math.floor(minutes / 60);
        var minuteRest = minutes % 60;
        return hours + ' 小时' + (minuteRest ? ' ' + minuteRest + ' 分' : '');
      }
      function formatWatchAt(value) {
        return value ? formatLoginAt(value) : '暂无记录';
      }
      function watchSummaryHtml(u) {
        var stats = u.watchStats || {};
        var count = Number(stats.sessionCount || 0);
        var active = Number(stats.activeSessions || 0);
        return '<span class="watch-summary"><strong>' + esc(formatWatchDuration(stats.totalDurationMs)) + '</strong>'
          + '<small>' + count + ' 次' + (active ? ' · ' + active + ' 进行中' : '') + '</small>'
          + '<em>最近 ' + esc(formatWatchAt(stats.lastWatchedAtMs)) + '</em></span>';
      }
      function watchReasonLabel(reason, active) {
        if (active) return '进行中';
        return ({
          client_disconnect: '主动断开',
          disconnect: '连接结束',
          permission_revoked: '权限撤销',
          session_revoked: '会话失效',
          stream_ended: '视频结束',
          stream_terminated: '视频停止',
          process_restart: '服务重启',
          error: '异常结束'
        })[reason] || '连接结束';
      }
      function remainingChip(u) {
        if (u.role === 'admin' || u.expirationState === 'permanent' || u.remainingDays === null || isNaN(u.remainingDays)) return '<span class="expiry-chip good">长期有效</span>';
        var days = Math.max(0, Math.floor(Number(u.remainingDays)));
        var cls = u.status === 'expired' || u.status === 'disabled' || days <= 7 ? 'danger' : (days <= 30 ? 'warn' : 'good');
        return '<span class="expiry-chip ' + cls + '">' + (u.status === 'expired' ? '已到期' : days + ' 天') + '</span>';
      }

      /* ---------- toast ---------- */
      function showToast(msg, type) {
        var root = document.getElementById('toast-root');
        if (!root) return;
        var icon = type === 'success' ? 'check-circle' : (type === 'error' ? 'alert-circle' : 'info');
        var t = document.createElement('div');
        t.className = 'toast ' + (type || 'info');
        t.innerHTML = '<i data-lucide="' + icon + '"></i><span></span>';
        t.querySelector('span').textContent = msg;
        root.appendChild(t);
        if (window.lucide) { lucide.createIcons(); }
        requestAnimationFrame(function () { t.classList.add('show'); });
        setTimeout(function () {
          t.classList.remove('show');
          setTimeout(function () { t.remove(); }, 240);
        }, 2600);
      }

      /* ---------- 表格渲染 ---------- */
      function filteredUsers() {
        var q = state.q.trim().toLowerCase();
        return USERS.filter(function (u) {
          if (state.role !== 'all' && u.role !== state.role) return false;
          if (state.status !== 'all' && u.status !== state.status) return false;
          if (q && u.username.toLowerCase().indexOf(q) === -1 && u.name.toLowerCase().indexOf(q) === -1) return false;
          return true;
        });
      }
      function rowHtml(u) {
        var st = u.status;
        var expiryCls = st === 'expired' || st === 'disabled' ? 'overdue' : (st === 'expiring' ? 'warned' : '');
        var delBtn = u.role === 'admin'
          ? '<button class="row-btn danger" type="button" data-act="del" disabled title="管理员账号不可删除"><i data-lucide="trash-2"></i>删除</button>'
          : '<button class="row-btn danger" type="button" data-act="del" title="删除用户"><i data-lucide="trash-2"></i>删除</button>';
        return '<tr class="user-row" data-id="' + esc(u.id) + '">' +
          '<td><div class="user-cell"><span class="user-avatar">' + esc(avatarLetter(u.name)) + '</span><div style="min-width:0"><div class="user-name">' + esc(u.name) + '</div><div class="user-handle">@' + esc(u.username) + '</div></div></div></td>' +
          '<td><span class="role-chip ' + (u.role === 'admin' ? 'admin' : 'user') + '">' + (u.role === 'admin' ? '管理员' : '普通用户') + '</span></td>' +
          '<td><span class="status-chip ' + st + '"><span class="mini-dot"></span>' + statusLabel(st) + '</span></td>' +
          '<td><span class="expiry-cell"><span class="expiry-date ' + expiryCls + '">' + esc(u.expiry || '长期有效') + '</span>' + remainingChip(u) + '</span></td>' +
          '<td><span class="count-cell">' + deviceCountText(u) + '</span></td>' +
          '<td><span class="count-cell">' + configCountText(u) + '</span></td>' +
          '<td>' + watchSummaryHtml(u) + '</td>' +
          '<td><span class="last-login">' + esc(formatLoginAt(u.lastLoginAt)) + '</span></td>' +
          '<td><span class="login-ip">' + esc(u.lastLoginIp || '—') + '</span></td>' +
          '<td><div class="row-actions">' +
          '<button class="row-btn neutral" type="button" data-act="edit" title="编辑用户"><i data-lucide="pencil"></i>编辑</button>' +
          '<button class="row-btn neutral" type="button" data-act="reset" title="重置密码"><i data-lucide="key"></i>重置密码</button>' +
          '<button class="row-btn open" type="button" data-act="perm" title="权限管理"><i data-lucide="shield"></i>权限</button>' +
          delBtn +
          '</div></td></tr>';
      }
      function renderPager(pages) {
        var pager = document.getElementById('users-pager');
        if (!pager) return;
        var html = '';
        html += '<button class="page-btn" type="button" data-page="prev"' + (state.page <= 1 ? ' disabled' : '') + ' title="上一页"><i data-lucide="chevron-left"></i></button>';
        for (var i = 1; i <= pages; i++) {
          html += '<button class="page-btn' + (i === state.page ? ' active' : '') + '" type="button" data-page="' + i + '">' + i + '</button>';
        }
        html += '<button class="page-btn" type="button" data-page="next"' + (state.page >= pages ? ' disabled' : '') + ' title="下一页"><i data-lucide="chevron-right"></i></button>';
        pager.innerHTML = html;
      }
      function render() {
        var list = filteredUsers();
        var pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
        if (state.page > pages) { state.page = pages; }
        var start = (state.page - 1) * PAGE_SIZE;
        var pageUsers = list.slice(start, start + PAGE_SIZE);
        var tbody = document.getElementById('users-tbody');
        if (!tbody) return;
        var html = '';
        pageUsers.forEach(function (u) { html += rowHtml(u); });
        tbody.innerHTML = html;
        if (pageUsers.length === 0) { tbody.insertAdjacentHTML('beforeend', EMPTY_ROW_HTML); }
        var count = document.getElementById('users-count');
        if (count) { count.textContent = '共 ' + list.length + ' 条 · 每页 ' + PAGE_SIZE + ' 条'; }
        var sub = document.getElementById('users-subtitle');
        if (sub) { sub.textContent = '共 ' + list.length + ' 人'; }
        renderPager(pages);
        if (window.lucide) { lucide.createIcons(); }
      }
      function clearFilters() {
        state.q = ''; state.role = 'all'; state.status = 'all'; state.page = 1;
        var s = document.getElementById('users-search'); if (s) { s.value = ''; }
        var r = document.getElementById('users-role-filter'); if (r) { r.value = 'all'; }
        var st = document.getElementById('users-status-filter'); if (st) { st.value = 'all'; }
        render();
      }

      /* ---------- 弹窗通用 ---------- */
      function openModal(id) { var m = document.getElementById(id); if (m) { m.classList.add('open'); } }
      function closeModal(id) { var m = document.getElementById(id); if (m) { m.classList.remove('open'); } }
      function setError(name, msg) {
        var w = document.getElementById('f-' + name);
        var er = document.getElementById('er-' + name);
        if (w) { w.classList.toggle('has-error', !!msg); }
        if (er) { er.textContent = msg || ''; }
      }
      function clearErrors(form) {
        if (!form) return;
        form.querySelectorAll('.field.has-error').forEach(function (f) { f.classList.remove('has-error'); });
        form.querySelectorAll('.field-error').forEach(function (f) { f.textContent = ''; });
      }

      /* ---------- 永久有效（不设置到期时间） ---------- */
      /* 服务端用 expires_at=NULL 表示永久；表单原来强制要求日期，于是永久账号
         （含唯一的内置管理员）在界面上根本改不了，新增也无法建永久账号。 */
      function expiryIsPermanent(inputId) {
        var box = document.getElementById(inputId === 'add-expiry' ? 'add-permanent' : 'edit-permanent');
        return !!(box && box.checked);
      }
      function expiryValue(inputId) {
        return expiryIsPermanent(inputId) ? '' : document.getElementById(inputId).value;
      }
      function syncExpiryEditor(inputId) {
        var field = document.getElementById(inputId);
        var permanent = expiryIsPermanent(inputId);
        var editor = document.getElementById(inputId === 'add-expiry' ? 'f-add-expiry' : 'f-edit-expiry');
        if (field) field.disabled = permanent;
        if (editor) {
          editor.querySelectorAll('[data-expiry-days], .expiry-custom-add, .expiry-custom-input').forEach(function (node) {
            node.disabled = permanent;
          });
        }
        if (permanent) {
          setError(inputId, '');
          setExpiryDeltaHtml(inputId, expiryChip('', '永久有效', ''));
        } else {
          updateExpiryDelta(inputId);
        }
      }
      ['add-expiry', 'edit-expiry'].forEach(function (inputId) {
        var box = document.getElementById(inputId === 'add-expiry' ? 'add-permanent' : 'edit-permanent');
        if (box) box.addEventListener('change', function () { syncExpiryEditor(inputId); });
      });
      function parseExpiryInput(value) {
        var text = String(value || '').trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
        var parts = text.split('-');
        var date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
        return isNaN(date.getTime()) ? null : date;
      }
      function setExpiryFromDays(inputId, days) {
        var input = document.getElementById(inputId);
        if (!input) return;
        /* backport: frontend repo — stack quick days on the current value */
        var date = parseExpiryInput(input.value) || new Date();
        date.setHours(0, 0, 0, 0);
        date.setDate(date.getDate() + Number(days || 0));
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, '0');
        var d = String(date.getDate()).padStart(2, '0');
        input.value = y + '-' + m + '-' + d;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        setError(inputId.replace('expiry', 'expiry'), '');
      }
      document.querySelectorAll('[data-expiry-days]').forEach(function (button) {
        button.addEventListener('click', function () {
          var inputId = button.closest('#f-add-expiry') ? 'add-expiry' : 'edit-expiry';
          setExpiryFromDays(inputId, button.getAttribute('data-expiry-days'));
        });
      });

      /* ---------- 到期时间差值提示（按月续费：在现有日期上叠加） ---------- */
      var editExpiryOriginal = '';
      function expiryDayIndex(value) {
        var date = parseExpiryInput(value);
        if (!date) return null;
        return Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
      }
      function todayDayIndex() {
        var now = new Date();
        return Math.round(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / 86400000);
      }
      /* Apple Copy 胶囊：label（小号 muted）+ value（加粗 tabular-nums） */
      function expiryChip(label, value, tone) {
        return '<span class="expiry-delta-chip' + (tone ? ' is-' + tone : '') + '">'
          + (label ? '<span class="expiry-delta-chip-label">' + label + ' </span>' : '')
          + '<span class="expiry-delta-chip-value">' + value + '</span>'
          + '</span>';
      }
      /* 0 天读作「今天到期」，负数读作「已过期 N 天」 */
      function expiryStateChip(prefix, days, positiveTone) {
        if (days === 0) return expiryChip('', '今天到期', '');
        if (days < 0) return expiryChip('已过期', Math.abs(days) + ' 天', 'down');
        return expiryChip(prefix, days + ' 天', positiveTone);
      }
      function expiryDeltaChip(days) {
        if (days > 0) return expiryChip('增加', days + ' 天', 'up');
        return expiryChip('减少', Math.abs(days) + ' 天', 'down');
      }
      function setExpiryDeltaHtml(inputId, html) {
        var label = document.getElementById(inputId + '-delta');
        if (!label) return;
        label.innerHTML = html;
      }
      function updateExpiryDelta(inputId) {
        var input = document.getElementById(inputId);
        if (!input) return;
        var current = expiryDayIndex(input.value);
        if (current === null) { setExpiryDeltaHtml(inputId, ''); return; }
        var today = todayDayIndex();
        var remaining = current - today;
        if (inputId === 'add-expiry') {
          setExpiryDeltaHtml(inputId, expiryStateChip('剩余', remaining, ''));
          return;
        }
        var base = expiryDayIndex(editExpiryOriginal);
        if (base === null) {
          setExpiryDeltaHtml(inputId, expiryStateChip('剩余', remaining, ''));
          return;
        }
        var added = current - base;
        var baseRemaining = base - today;
        var html = expiryStateChip('剩余', baseRemaining, '');
        if (added !== 0) {
          html += '<span class="expiry-delta-op">' + (added > 0 ? '+' : '−') + '</span>';
          html += expiryDeltaChip(added);
          html += '<span class="expiry-delta-op">=</span>';
          html += expiryStateChip('总计', remaining, 'result');
        }
        setExpiryDeltaHtml(inputId, html);
      }
      ['add-expiry', 'edit-expiry'].forEach(function (inputId) {
        var input = document.getElementById(inputId);
        if (input) input.addEventListener('input', function () { updateExpiryDelta(inputId); });
      });
      document.querySelectorAll('.expiry-custom-add').forEach(function (button) {
        var field = button.closest('.field');
        var box = field ? field.querySelector('.expiry-custom-input') : null;
        var dateInput = field ? field.querySelector('input[type="date"]') : null;
        function applyCustomDays() {
          if (!box || !dateInput) return;
          var days = parseInt(box.value, 10);
          if (!isFinite(days) || days <= 0) { box.focus(); return; }
          setExpiryFromDays(dateInput.id, Math.min(days, 9999));
          box.value = '';
        }
        button.addEventListener('click', applyCustomDays);
        if (box) box.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') { event.preventDefault(); applyCustomDays(); }
        });
      });

      /* ---------- 新增用户 ---------- */
      var addPermissionState = { devices: {} };
      function selectedInitialDevicePermissions() {
        return Object.keys(addPermissionState.devices).map(function (deviceId) {
          var permission = addPermissionState.devices[deviceId];
          return { deviceId: deviceId, canView: !!permission.canView, canControl: !!permission.canControl };
        }).filter(function (permission) { return permission.canView; });
      }
      function updateAddAlasOptions() {
        var configSelect = document.getElementById('add-alas-config');
        var deviceSelect = document.getElementById('add-alas-device');
        var previousConfig = configSelect.value;
        var previousDevice = deviceSelect.value;
        configSelect.innerHTML = '<option value="">不关联</option>' + ALAS_POOL.map(function (config) {
          return '<option value="' + esc(config.id) + '">' + esc(config.name) + '</option>';
        }).join('');
        if (ALAS_POOL.some(function (config) { return config.id === previousConfig; })) configSelect.value = previousConfig;
        var selected = selectedInitialDevicePermissions();
        deviceSelect.innerHTML = selected.length
          ? '<option value="">选择设备</option>' + selected.map(function (permission) {
            var device = deviceById(permission.deviceId);
            return '<option value="' + esc(permission.deviceId) + '">' + esc(device ? device.name : permission.deviceId) + '</option>';
          }).join('')
          : '<option value="">先选择可观看设备</option>';
        deviceSelect.disabled = !selected.length || !configSelect.value;
        if (selected.some(function (permission) { return permission.deviceId === previousDevice; })) deviceSelect.value = previousDevice;
        document.getElementById('add-alas-summary').textContent = configSelect.value ? '待关联' : '未关联';
      }
      function syncAddDeviceRow(row) {
        var deviceId = row.getAttribute('data-add-device');
        var permission = addPermissionState.devices[deviceId];
        var view = row.querySelector('[data-add-view]');
        var control = row.querySelector('[data-add-control]');
        permission.canView = !!view.checked;
        if (!permission.canView) control.checked = false;
        control.disabled = !permission.canView;
        permission.canControl = permission.canView && !!control.checked;
        row.classList.toggle('is-selected', permission.canView);
        row.querySelector('[data-add-device-status]').textContent = permission.canControl ? '观看与控制' : permission.canView ? '仅观看' : '未授权';
        document.getElementById('add-device-count').textContent = '已选择 ' + selectedInitialDevicePermissions().length + ' 台';
        updateAddAlasOptions();
      }
      function renderAddDevicePermissions() {
        var list = document.getElementById('add-device-list');
        list.innerHTML = DEVICE_POOL.length ? DEVICE_POOL.map(function (device) {
          var permission = addPermissionState.devices[device.id] || { canView: false, canControl: false };
          return '<div class="add-device-row' + (permission.canView ? ' is-selected' : '') + '" data-add-device="' + esc(device.id) + '">'
            + '<span class="perm-item-glyph"><i data-lucide="smartphone"></i></span>'
            + '<div class="add-device-copy"><strong>' + esc(device.name) + '</strong><small data-add-device-status>' + (permission.canControl ? '观看与控制' : permission.canView ? '仅观看' : '未授权') + '</small></div>'
            + '<div class="permission-switches">'
            + '<label class="permission-switch"><span>观看</span><input type="checkbox" class="sg-switch-input" data-add-view ' + (permission.canView ? 'checked' : '') + '></label>'
            + '<label class="permission-switch dependent"><span>控制</span><input type="checkbox" class="sg-switch-input" data-add-control ' + (permission.canControl ? 'checked' : '') + (!permission.canView ? ' disabled' : '') + '></label>'
            + '</div></div>';
        }).join('') : '<div class="add-permission-empty">系统暂无设备，可以先创建用户，稍后再添加权限。</div>';
        list.querySelectorAll('[data-add-view],[data-add-control]').forEach(function (input) {
          input.addEventListener('change', function () { syncAddDeviceRow(input.closest('[data-add-device]')); });
        });
        document.getElementById('add-device-count').textContent = '已选择 ' + selectedInitialDevicePermissions().length + ' 台';
        if (window.lucide) lucide.createIcons();
      }
      function updateAddPermissionRole() {
        var admin = document.getElementById('add-role').value === 'admin';
        document.getElementById('add-admin-permissions').hidden = !admin;
        document.getElementById('add-user-permissions').hidden = admin;
      }
      function resetAddPermissions() {
        addPermissionState.devices = {};
        DEVICE_POOL.forEach(function (device) { addPermissionState.devices[device.id] = { canView: false, canControl: false }; });
        document.getElementById('add-alas-config').value = '';
        document.getElementById('add-alas-device').value = '';
        document.getElementById('add-alas-details').open = false;
        document.getElementById('er-add-permissions').textContent = '';
        document.getElementById('er-add-permissions').style.display = 'none';
        renderAddDevicePermissions();
        updateAddAlasOptions();
        updateAddPermissionRole();
      }
      function saveInitialUserPermissions(username, devicePermissions, alasRelation) {
        var report = { failed: 0 };
        var deviceTasks = devicePermissions.map(function (permission) {
          return window.ScrcpyGateApi.configured('devices.permissions.update', { method: 'PUT', body: {
            username: username, deviceId: permission.deviceId, enabled: true,
            canView: true, canControl: permission.canControl
          }});
        });
        return Promise.allSettled(deviceTasks).then(function (results) {
          results.forEach(function (result) { if (result.status === 'rejected') report.failed++; });
          if (!alasRelation.configId) return report;
          var deviceIndex = devicePermissions.findIndex(function (permission) { return permission.deviceId === alasRelation.deviceId; });
          if (deviceIndex < 0 || results[deviceIndex].status === 'rejected') {
            report.failed++;
            return report;
          }
          return window.ScrcpyGateApi.configured('alas.relations.create', { method: 'POST', body: {
            userId: username, configId: alasRelation.configId, deviceId: alasRelation.deviceId,
            canRun: true, canEdit: true, isDefault: true, grantView: false
          }}).catch(function () { report.failed++; }).then(function () { return report; });
        });
      }
      function openAddModal() {
        var f = document.getElementById('add-form');
        if (f) { f.reset(); clearErrors(f); }
        syncExpiryEditor('add-expiry');
        resetAddPermissions();
        openModal('modal-add-user');
        var u = document.getElementById('add-username');
        if (u) { setTimeout(function () { u.focus(); }, 60); }
      }
      function submitAdd() {
        var uName = document.getElementById('add-username').value.trim();
        var uPass = document.getElementById('add-password').value;
        var uRole = document.getElementById('add-role').value;
        var uExpiry = expiryValue('add-expiry');
        var permanent = expiryIsPermanent('add-expiry');
        var devicePermissions = uRole === 'admin' ? [] : selectedInitialDevicePermissions();
        var alasRelation = uRole === 'admin' ? { configId: '', deviceId: '' } : {
          configId: document.getElementById('add-alas-config').value,
          deviceId: document.getElementById('add-alas-device').value
        };
        var ok = true;
        setError('add-username', uName ? '' : '请输入用户名');
        if (uName) {
          var dup = USERS.some(function (x) { return x.username.toLowerCase() === uName.toLowerCase(); });
          if (dup) { setError('add-username', '该用户名已存在'); ok = false; }
        }
        setError('add-password', uPass ? (uPass.length >= 12 ? '' : '密码至少 12 位') : '请输入初始密码');
        setError('add-expiry', uExpiry || permanent ? '' : '请选择到期时间或勾选「永久有效」');
        var permissionError = '';
        if (!!alasRelation.configId !== !!alasRelation.deviceId) permissionError = alasRelation.configId ? '请选择 ALAS 关联设备' : '请选择 Runtime 配置';
        document.getElementById('er-add-permissions').textContent = permissionError;
        document.getElementById('er-add-permissions').style.display = permissionError ? 'block' : 'none';
        if (!uName || !uPass || (!uExpiry && !permanent) || uPass.length < 12) { ok = false; }
        if (permissionError) { ok = false; document.getElementById('add-alas-details').open = true; }
        if (!ok) { showToast('保存失败，请检查表单填写', 'error'); return; }
        var btn = document.getElementById('add-save'); btn.disabled = true;
        var accountCreated = false;
        window.ScrcpyGateApi.configured('users.create', { method: 'POST', body: { username: uName, password: uPass, role: uRole, expiry: uExpiry, forcePasswordChange: document.getElementById('add-force').checked } }).then(function () {
          accountCreated = true;
          return saveInitialUserPermissions(uName, devicePermissions, alasRelation);
        }).then(function (report) {
          closeModal('modal-add-user');
          return loadUsers().then(function () { return report; });
        }).then(function (report) {
          showToast(report.failed ? '用户已创建，' + report.failed + ' 项初始权限未保存，请在权限管理中重试' : '用户及初始权限已创建', report.failed ? 'info' : 'success');
        }).catch(function (error) {
          showToast(accountCreated ? '用户已创建，但初始权限保存失败：' + apiError(error) : apiError(error), accountCreated ? 'info' : 'error');
          if (accountCreated) loadUsers().catch(function () {});
        }).finally(function () { btn.disabled = false; });
      }

      /* ---------- 编辑用户 ---------- */
      var editTarget = null;
      function openEditModal(u) {
        editTarget = u;
        document.getElementById('edit-username').textContent = u.username;
        document.getElementById('edit-role').value = u.role;
        document.getElementById('edit-expiry').value = u.expiry;
        editExpiryOriginal = u.expiry || '';
        var permanentBox = document.getElementById('edit-permanent');
        if (permanentBox) permanentBox.checked = u.expirationState === 'permanent' || !u.expiry;
        syncExpiryEditor('edit-expiry');
        document.getElementById('edit-enabled').checked = u.enabled !== false;
        document.getElementById('edit-enabled-label').textContent = u.enabled === false ? '已停用' : '已启用';
        updateRoleHint();
        clearErrors(document.getElementById('edit-form'));
        openModal('modal-edit-user');
      }
      function updateRoleHint() {
        var isAdmin = document.getElementById('edit-role').value === 'admin';
        var hint = document.getElementById('edit-role-hint');
        if (hint) { hint.style.display = isAdmin ? 'flex' : 'none'; }
      }
      function submitEdit() {
        var u = editTarget;
        if (!u) return;
        var r = document.getElementById('edit-role').value;
        var e = expiryValue('edit-expiry');
        var permanent = expiryIsPermanent('edit-expiry');
        var enabled = document.getElementById('edit-enabled').checked;
        var ok = true;
        setError('edit-expiry', e || permanent ? '' : '请选择到期时间或勾选「永久有效」');
        if (!e && !permanent) { ok = false; }
        if (!ok) { showToast('保存失败，请检查表单填写', 'error'); return; }
        var btn = document.getElementById('edit-save'); btn.disabled = true;
        window.ScrcpyGateApi.configured('users.update', { params: { id: u.id }, method: 'PUT', body: { role: r, expiry: e, enabled: enabled } }).then(function () {
          closeModal('modal-edit-user'); return loadUsers();
        }).then(function () { showToast('用户信息已更新', 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      }

      /* ---------- 删除用户 ---------- */
      var delTarget = null;
      function openDeleteModal(u) {
        delTarget = u;
        document.getElementById('del-user-name').textContent = u.name + '（@' + u.username + '）';
        openModal('modal-delete-user');
      }
      function confirmDelete() {
        var u = delTarget;
        if (!u) return;
        var btn = document.getElementById('del-confirm'); btn.disabled = true;
        window.ScrcpyGateApi.configured('users.delete', { params: { id: u.id }, method: 'DELETE' }).then(function () {
          closeModal('modal-delete-user'); return loadUsers();
        }).then(function () { showToast('用户已删除', 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      }

      /* ---------- 重置密码 ---------- */
      function openResetModal(u) {
        var f = document.getElementById('reset-form');
        if (f) { f.reset(); clearErrors(f); }
        document.getElementById('reset-form').setAttribute('data-username', u.username);
        openModal('modal-reset-password');
      }
      function submitReset() {
        var p = document.getElementById('reset-password').value;
        if (!p) { setError('reset-password', '请输入新密码'); showToast('保存失败，请检查表单填写', 'error'); return; }
        if (p.length < 12) { setError('reset-password', '密码至少 12 位'); showToast('保存失败，请检查表单填写', 'error'); return; }
        var force = document.getElementById('reset-force').checked;
        var uname = document.getElementById('reset-form').getAttribute('data-username') || '';
        var target = USERS.filter(function (u) { return u.username === uname; })[0];
        var btn = document.getElementById('reset-save'); btn.disabled = true;
        window.ScrcpyGateApi.configured('users.reset-password', { params: { id: target && target.id }, method: 'POST', body: { password: p, forcePasswordChange: force } }).then(function () { closeModal('modal-reset-password'); showToast('密码已重置', 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      }

      /* ---------- 权限管理抽屉 ---------- */
      var perm = { user: null, deviceIds: null, controlIds: null, configIds: null, devSnapshot: null, controlSnapshot: null, cfgSnapshot: null, changes: [], batch: false, batchSnapshot: null };
      function openPermDrawer(u) {
        perm.user = u;
        perm.changes = [];
        perm.batch = false;
        if (u.role === 'admin') {
          perm.deviceIds = null; perm.controlIds = null; perm.configIds = null;
          perm.devSnapshot = null; perm.controlSnapshot = null; perm.cfgSnapshot = null;
        } else {
          perm.deviceIds = new Set(u.deviceIds || []);
          perm.controlIds = new Set((u.devicePermissions || []).filter(function (permission) { return permission.canControl; }).map(function (permission) { return permission.deviceId; }));
          perm.configIds = new Set(u.configIds || []);
          perm.devSnapshot = new Set(u.deviceIds || []);
          perm.controlSnapshot = new Set(perm.controlIds);
          perm.cfgSnapshot = new Set(u.configIds || []);
        }
        renderPerm();
        document.getElementById('drawer-mask').classList.add('open');
        document.getElementById('perm-drawer').classList.add('open');
        var body = document.getElementById('perm-body');
        if (body) { body.scrollTop = 0; }
      }
      function closePermDrawer() {
        document.getElementById('drawer-mask').classList.remove('open');
        document.getElementById('perm-drawer').classList.remove('open');
      }
      function renderWatchHistory() {
        var u = perm.user;
        var stats = (u && u.watchStats) || {};
        var count = document.getElementById('watch-session-count');
        var total = document.getElementById('watch-total');
        var list = document.getElementById('watch-history-list');
        if (!count || !total || !list) return;
        count.textContent = Number(stats.sessionCount || 0);
        total.textContent = '总时长 ' + formatWatchDuration(stats.totalDurationMs);
        var history = (u && Array.isArray(u.watchHistory)) ? u.watchHistory : [];
        if (!history.length) {
          list.innerHTML = '<div class="watch-empty"><i data-lucide="clock-3"></i><span>暂无观看记录</span></div>';
          if (window.lucide) { lucide.createIcons(); }
          return;
        }
        list.innerHTML = history.map(function (item) {
          var range = formatWatchAt(item.startedAtMs) + ' - ' + (item.active ? '现在' : formatWatchAt(item.endedAtMs));
          return '<div class="watch-history-item">'
            + '<div class="watch-history-main"><strong>' + esc(item.deviceName || '未知设备') + '</strong><span>' + esc(range) + '</span></div>'
            + '<div class="watch-history-meta"><b>' + esc(formatWatchDuration(item.durationMs)) + '</b><small>' + esc(watchReasonLabel(item.endReason, item.active)) + '</small></div>'
            + '</div>';
        }).join('');
      }
      function renderPerm() {
        var u = perm.user;
        if (!u) return;
        document.getElementById('perm-avatar').textContent = avatarLetter(u.name);
        document.getElementById('perm-name').textContent = u.name;
        document.getElementById('perm-handle').textContent = '@' + u.username;
        var rc = document.getElementById('perm-role');
        rc.className = 'role-chip ' + (u.role === 'admin' ? 'admin' : 'user');
        rc.textContent = u.role === 'admin' ? '管理员' : '普通用户';
        renderPermDevices();
        renderPermConfigs();
        renderWatchHistory();
        renderPermChanges();
        var saveBtn = document.getElementById('perm-save');
        if (saveBtn) { saveBtn.classList.toggle('save-ready', perm.changes.length > 0); }
      }
      function renderPermDevices() {
        var u = perm.user;
        var actions = document.getElementById('perm-device-actions');
        var batchBar = document.getElementById('perm-device-batch');
        var box = document.getElementById('perm-devices');
        var count = document.getElementById('perm-device-count');
        if (u.role === 'admin') {
          if (actions) { actions.innerHTML = ''; }
          if (batchBar) { batchBar.innerHTML = ''; }
          box.innerHTML = '<div class="perm-note"><i data-lucide="shield"></i><span>管理员拥有全部设备权限，无需单独授权。</span></div>';
          count.textContent = '全部';
          if (window.lucide) { lucide.createIcons(); }
          return;
        }
        count.textContent = perm.deviceIds.size;
        if (perm.batch) {
          if (actions) { actions.innerHTML = ''; }
          batchBar.innerHTML = '<span>批量选择已授权设备</span><button class="batch-btn" type="button" id="batch-all">全选</button><button class="batch-btn" type="button" id="batch-invert">反选</button><button class="batch-btn apply" type="button" id="batch-apply">应用批量设置</button><button class="batch-btn" type="button" id="batch-cancel">取消</button>';
        } else {
          actions.innerHTML = '<button class="row-btn neutral" type="button" id="perm-batch-btn" title="批量设置已授权设备"><i data-lucide="check-square"></i>批量设置</button><button class="btn-primary" type="button" id="perm-add-device"><i data-lucide="plus"></i>添加设备权限</button>';
          batchBar.innerHTML = '';
        }
        var html = '';
        var ids = Array.from(perm.deviceIds);
        if (!ids.length) { html += '<div class="perm-empty">该用户暂未绑定设备</div>'; }
        ids.forEach(function (id) {
          var d = deviceById(id);
          if (!d) return;
          var st = catalogStatusChip(d.status, false);
          var permissions = perm.batch ? '' : '<div class="perm-item-permissions">'
            + '<label class="permission-switch"><span>观看</span><input type="checkbox" class="sg-switch-input" data-perm-view="' + esc(d.id) + '" checked></label>'
            + '<label class="permission-switch dependent"><span>控制</span><input type="checkbox" class="sg-switch-input" data-perm-control="' + esc(d.id) + '"' + (perm.controlIds.has(d.id) ? ' checked' : '') + '></label>'
            + '</div>';
          var action = perm.batch
            ? '<input class="perm-item-check" type="checkbox" data-batch-dev="' + esc(d.id) + '"' + (perm.deviceIds.has(d.id) ? ' checked' : '') + '>'
            : '<button class="perm-item-remove" type="button" data-remove-dev="' + esc(d.id) + '" title="移除设备权限"><i data-lucide="x"></i>移除</button>';
          html += '<div class="perm-item"><span class="perm-item-glyph"><i data-lucide="smartphone"></i></span><div class="perm-item-copy"><span class="perm-item-name">' + esc(d.name) + '</span><span class="perm-item-sub">' + (perm.controlIds.has(d.id) ? '观看与控制' : '仅观看') + '</span></div>' + st + permissions + action + '</div>';
        });
        box.innerHTML = html;
        if (window.lucide) { lucide.createIcons(); }
      }
      function renderPermConfigs() {
        var u = perm.user;
        var actions = document.getElementById('perm-cfg-actions');
        var box = document.getElementById('perm-configs');
        var count = document.getElementById('perm-cfg-count');
        if (u.role === 'admin') {
          if (actions) { actions.innerHTML = ''; }
          box.innerHTML = '<div class="perm-note"><i data-lucide="shield"></i><span>管理员拥有全部 ALAS 配置权限，无需单独授权。</span></div>';
          count.textContent = '全部';
          if (window.lucide) { lucide.createIcons(); }
          return;
        }
        count.textContent = perm.configIds.size;
        actions.innerHTML = '<button class="btn-primary" type="button" id="perm-add-config"><i data-lucide="plus"></i>添加 ALAS 配置权限</button>';
        var html = '';
        var ids = Array.from(perm.configIds);
        if (!ids.length) { html += '<div class="perm-empty">该用户暂未关联 ALAS 配置</div>'; }
        ids.forEach(function (id) {
          var c = configById(id);
          if (!c) return;
          var st = catalogStatusChip(c.status, true);
          html += '<div class="perm-item"><span class="perm-item-glyph"><i data-lucide="bot"></i></span><div class="perm-item-copy"><span class="perm-item-name">' + esc(c.name) + '</span><span class="perm-item-sub">ALAS 配置</span></div>' + st + '<button class="perm-item-remove" type="button" data-remove-cfg="' + esc(c.id) + '" title="移除配置权限"><i data-lucide="x"></i>移除</button></div>';
        });
        box.innerHTML = html;
        if (window.lucide) { lucide.createIcons(); }
      }
      function pushChange(op, kind, label) {
        perm.changes.push({ op: op, kind: kind, label: label });
        renderPermChanges();
        var saveBtn = document.getElementById('perm-save');
        if (saveBtn) { saveBtn.classList.toggle('save-ready', perm.changes.length > 0); }
      }
      function renderPermChanges() {
        var list = document.getElementById('perm-changes');
        var empty = document.getElementById('perm-changes-empty');
        if (!list || !empty) return;
        if (!perm.changes.length) {
          list.innerHTML = '';
          empty.style.display = 'block';
          return;
        }
        empty.style.display = 'none';
        var html = '';
        perm.changes.forEach(function (ch) {
          var cls = ch.op === '+' ? 'add' : 'remove';
          var sym = ch.op === '+' ? '+' : '−';
          html += '<div class="change-item ' + cls + '"><span class="op">' + sym + '</span><span class="txt">' + esc(ch.label) + '</span><span class="kind">' + (ch.kind === 'device' ? '设备' : '配置') + '</span></div>';
        });
        list.innerHTML = html;
      }
      function removeDevice(id) {
        if (perm.deviceIds.has(id)) {
          perm.deviceIds.delete(id);
          perm.controlIds.delete(id);
          pushChange('-', 'device', deviceById(id).name);
        }
        renderPermDevices();
      }
      function setDeviceControl(id, enabled) {
        if (!perm.deviceIds.has(id)) return;
        if (enabled) perm.controlIds.add(id); else perm.controlIds.delete(id);
        pushChange(enabled ? '+' : '-', 'device', deviceById(id).name + ' 控制权限');
        renderPermDevices();
      }
      function removeConfig(id) {
        if (perm.configIds.has(id)) {
          perm.configIds.delete(id);
          pushChange('-', 'config', configById(id).name);
        }
        renderPermConfigs();
      }
      function enterBatch() {
        perm.batch = true;
        perm.batchSnapshot = new Set(perm.deviceIds);
        renderPerm();
      }
      function exitBatch(apply) {
        if (!perm.batch) return;
        if (apply) {
          var prev = perm.batchSnapshot, cur = perm.deviceIds;
          prev.forEach(function (id) { if (!cur.has(id)) { pushChange('-', 'device', deviceById(id).name); } });
          cur.forEach(function (id) { if (!prev.has(id)) { pushChange('+', 'device', deviceById(id).name); } });
          Array.from(perm.controlIds).forEach(function (id) { if (!cur.has(id)) perm.controlIds.delete(id); });
        } else {
          perm.deviceIds = new Set(perm.batchSnapshot);
        }
        perm.batch = false;
        perm.batchSnapshot = null;
        renderPerm();
      }
      function batchSelectAll() { perm.deviceIds = new Set(perm.batchSnapshot); renderPermDevices(); }
      function batchInvert() {
        var next = new Set();
        perm.batchSnapshot.forEach(function (id) { if (!perm.deviceIds.has(id)) { next.add(id); } });
        perm.deviceIds = next;
        renderPermDevices();
      }
      function openDevicePicker() {
        var list = document.getElementById('device-pick-list');
        var html = '';
        var available = 0;
        DEVICE_POOL.forEach(function (d) {
          if (perm.deviceIds.has(d.id)) return;
          available++;
          var st = catalogStatusChip(d.status, false);
          html += '<label class="pick-item"><input type="checkbox" value="' + esc(d.id) + '" data-pick-dev><span class="perm-item-glyph"><i data-lucide="smartphone"></i></span><span class="perm-item-copy"><span class="perm-item-name">' + esc(d.name) + '</span><span class="perm-item-sub">' + esc(d.model) + '</span></span>' + st + '</label>';
        });
        list.innerHTML = html || (DEVICE_POOL.length
          ? '<div class="perm-empty">所有设备均已添加到该用户</div>'
          : '<div class="perm-empty">系统暂无设备，请先在设备管理中新增设备</div>');
        if (window.lucide) { lucide.createIcons(); }
        openModal('modal-device-picker');
      }
      function confirmDevicePicker() {
        var picked = [];
        document.querySelectorAll('#device-pick-list input:checked').forEach(function (inp) { picked.push(inp.value); });
        var added = 0;
        picked.forEach(function (id) {
          if (!perm.deviceIds.has(id)) {
            perm.deviceIds.add(id);
            perm.controlIds.delete(id);
            pushChange('+', 'device', deviceById(id).name);
            added++;
          }
        });
        closeModal('modal-device-picker');
        if (added) { renderPermDevices(); }
      }
      function openConfigPicker() {
        var list = document.getElementById('config-pick-list');
        var html = '';
        var available = 0;
        ALAS_POOL.forEach(function (c) {
          if (perm.configIds.has(c.id)) return;
          available++;
          var st = catalogStatusChip(c.status, true);
          html += '<label class="pick-item"><input type="checkbox" value="' + esc(c.id) + '" data-pick-cfg><span class="perm-item-glyph"><i data-lucide="bot"></i></span><span class="perm-item-copy"><span class="perm-item-name">' + esc(c.name) + '</span><span class="perm-item-sub">ALAS 配置</span></span>' + st + '</label>';
        });
        list.innerHTML = html || '<div class="perm-empty">没有可添加的配置</div>';
        if (window.lucide) { lucide.createIcons(); }
        openModal('modal-config-picker');
      }
      function confirmConfigPicker() {
        var picked = [];
        document.querySelectorAll('#config-pick-list input:checked').forEach(function (inp) { picked.push(inp.value); });
        var added = 0;
        picked.forEach(function (id) {
          if (!perm.configIds.has(id)) {
            perm.configIds.add(id);
            pushChange('+', 'config', configById(id).name);
            added++;
          }
        });
        closeModal('modal-config-picker');
        if (added) { renderPermConfigs(); }
      }
      function cancelPerm() {
        if (perm.user && perm.user.role !== 'admin' && perm.devSnapshot) {
          perm.deviceIds = new Set(perm.devSnapshot);
          perm.controlIds = new Set(perm.controlSnapshot);
          perm.configIds = new Set(perm.cfgSnapshot);
        }
        perm.changes = [];
        perm.batch = false;
        perm.batchSnapshot = null;
        closePermDrawer();
      }
      function savePerm() {
        var u = perm.user;
        if (!u) return;
        if (!perm.changes.length) { showToast('没有待保存的变更', 'info'); return; }
        var btn = document.getElementById('perm-save'); btn.disabled = true;
        window.ScrcpyGateApi.configured('users.permissions', { params: { id: u.id }, method: 'PUT', body: {
          devicePermissions: u.role === 'admin' ? null : Array.from(perm.deviceIds).map(function (deviceId) {
            return { deviceId: deviceId, canView: true, canControl: perm.controlIds.has(deviceId) };
          }),
          configIds: u.role === 'admin' ? null : Array.from(perm.configIds),
          /* 打开抽屉时的快照：适配层据此保留「编辑期间才出现」的设备/配置授权，
             不会因为它们不在这份快照里就被整体替换删掉。 */
          deviceIdsBaseline: u.role === 'admin' || !perm.devSnapshot ? null : Array.from(perm.devSnapshot),
          configIdsBaseline: u.role === 'admin' || !perm.cfgSnapshot ? null : Array.from(perm.cfgSnapshot)
        } }).then(function () { closePermDrawer(); return loadUsers(); }).then(function () { showToast('权限变更已保存', 'success'); }).catch(function (error) { showToast(apiError(error), 'error'); }).finally(function () { btn.disabled = false; });
      }

      /* ---------- 事件绑定 ---------- */
      document.getElementById('add-user-btn').addEventListener('click', openAddModal);
      document.getElementById('add-save').addEventListener('click', submitAdd);
      document.getElementById('add-role').addEventListener('change', updateAddPermissionRole);
      document.getElementById('add-alas-config').addEventListener('change', function () {
        updateAddAlasOptions();
        if (this.value) document.getElementById('add-alas-details').open = true;
      });
      document.getElementById('add-alas-device').addEventListener('change', function () {
        document.getElementById('add-alas-summary').textContent = this.value ? '已选择' : (document.getElementById('add-alas-config').value ? '待关联' : '未关联');
      });
      document.getElementById('edit-save').addEventListener('click', submitEdit);
      document.getElementById('edit-role').addEventListener('change', updateRoleHint);
      document.getElementById('edit-enabled').addEventListener('change', function () { document.getElementById('edit-enabled-label').textContent = this.checked ? '已启用' : '已停用'; });
      document.getElementById('del-confirm').addEventListener('click', confirmDelete);
      document.getElementById('reset-save').addEventListener('click', submitReset);
      document.getElementById('device-pick-confirm').addEventListener('click', confirmDevicePicker);
      document.getElementById('config-pick-confirm').addEventListener('click', confirmConfigPicker);
      document.getElementById('perm-save').addEventListener('click', savePerm);
      document.getElementById('perm-cancel').addEventListener('click', cancelPerm);
      document.getElementById('perm-close').addEventListener('click', cancelPerm);

      ['add-form', 'edit-form', 'reset-form'].forEach(function (id) {
        var f = document.getElementById(id);
        if (f) {
          f.addEventListener('submit', function (e) {
            e.preventDefault();
            if (id === 'add-form') { submitAdd(); }
            else if (id === 'edit-form') { submitEdit(); }
            else { submitReset(); }
          });
        }
      });

      document.querySelectorAll('[data-close]').forEach(function (b) {
        b.addEventListener('click', function () {
          var m = b.closest('.modal-mask');
          if (m) { closeModal(m.id); }
        });
      });
      document.querySelectorAll('.modal-mask').forEach(function (m) {
        m.addEventListener('click', function (e) { if (e.target === m) { closeModal(m.id); } });
      });
      document.getElementById('drawer-mask').addEventListener('click', function (e) { if (e.target === this) { cancelPerm(); } });

      document.getElementById('users-search').addEventListener('input', function () { state.q = this.value; state.page = 1; render(); });
      document.getElementById('users-role-filter').addEventListener('change', function () { state.role = this.value; state.page = 1; render(); });
      document.getElementById('users-status-filter').addEventListener('change', function () { state.status = this.value; state.page = 1; render(); });
      document.getElementById('users-pager').addEventListener('click', function (e) {
        var btn = e.target.closest('.page-btn');
        if (!btn || btn.disabled) return;
        var p = btn.getAttribute('data-page');
        if (p === 'prev') { state.page--; }
        else if (p === 'next') { state.page++; }
        else { state.page = parseInt(p, 10); }
        render();
      });
      document.getElementById('users-tbody').addEventListener('click', function (e) {
        var cf = e.target.closest('#clear-filters');
        if (cf) { clearFilters(); return; }
        var btn = e.target.closest('[data-act]');
        if (!btn || btn.disabled) return;
        var row = btn.closest('.user-row');
        if (!row) return;
        var u = userById(row.getAttribute('data-id'));
        if (!u) return;
        var act = btn.getAttribute('data-act');
        if (act === 'edit') { openEditModal(u); }
        else if (act === 'reset') { openResetModal(u); }
        else if (act === 'perm') { openPermDrawer(u); }
        else if (act === 'del') { openDeleteModal(u); }
      });
      document.getElementById('perm-body').addEventListener('click', function (e) {
        var rd = e.target.closest('[data-remove-dev]');
        if (rd) { removeDevice(rd.getAttribute('data-remove-dev')); return; }
        var rc = e.target.closest('[data-remove-cfg]');
        if (rc) { removeConfig(rc.getAttribute('data-remove-cfg')); return; }
        var btn = e.target.closest('button');
        if (!btn) return;
        var id = btn.id;
        if (id === 'perm-batch-btn') { enterBatch(); }
        else if (id === 'batch-all') { batchSelectAll(); }
        else if (id === 'batch-invert') { batchInvert(); }
        else if (id === 'batch-apply') { exitBatch(true); }
        else if (id === 'batch-cancel') { exitBatch(false); }
        else if (id === 'perm-add-device') { openDevicePicker(); }
        else if (id === 'perm-add-config') { openConfigPicker(); }
      });
      document.addEventListener('change', function (e) {
        var t = e.target;
        if (t.matches('[data-pick-dev],[data-pick-cfg]')) {
          var item = t.closest('.pick-item');
          if (item) { item.classList.toggle('checked', t.checked); }
        }
        if (t.matches('[data-batch-dev]')) {
          if (t.checked) { perm.deviceIds.add(t.value); } else { perm.deviceIds.delete(t.value); }
        }
        if (t.matches('[data-perm-view]') && !t.checked) {
          removeDevice(t.getAttribute('data-perm-view'));
        }
        if (t.matches('[data-perm-control]')) {
          setDeviceControl(t.getAttribute('data-perm-control'), t.checked);
        }
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          if (document.getElementById('perm-drawer').classList.contains('open')) { cancelPerm(); }
          else {
            var openM = document.querySelector('.modal-mask.open');
            if (openM) { closeModal(openM.id); }
          }
        }
      });

      /* ---------- 账户到期策略（提醒提前天数 / 到期自动停止 ALAS） ---------- */
      /* 这两个设置以前只有 API 没有入口，也没人消费：「提前天数」实际被硬编码成 7 天，
         「到期自动停止」从来不会停止任何配置。现在它们既生效也可在此处修改。 */
      var accountPolicy = { expiryReminderDays: 3, stopAlasOnExpiry: false };
      function renderAccountPolicy() {
        var days = document.getElementById('account-policy-reminder');
        var stop = document.getElementById('account-policy-stop');
        if (days) days.value = String(accountPolicy.expiryReminderDays);
        if (stop) stop.checked = !!accountPolicy.stopAlasOnExpiry;
        var state = document.getElementById('account-policy-state');
        if (state) {
          state.textContent = '提前 ' + accountPolicy.expiryReminderDays + ' 天提醒 · 到期自动停止 ALAS：'
            + (accountPolicy.stopAlasOnExpiry ? '已开启' : '已关闭');
        }
      }
      function loadAccountPolicy() {
        if (!window.ScrcpyGateApi || !window.ScrcpyGateApi.isConfigured('account.policy')) return Promise.resolve();
        return window.ScrcpyGateApi.configured('account.policy').then(function (payload) {
          if (payload) { accountPolicy = payload; }
          renderAccountPolicy();
        }).catch(function () {});
      }
      function saveAccountPolicy() {
        var daysInput = document.getElementById('account-policy-reminder');
        var stopInput = document.getElementById('account-policy-stop');
        var raw = daysInput ? Number(daysInput.value) : 0;
        if (!isFinite(raw) || raw < 0 || raw > 90) {
          showToast('到期提醒提前天数必须是 0-90 的整数', 'error');
          return;
        }
        var days = Math.round(raw);
        var stop = !!(stopInput && stopInput.checked);
        var btn = document.getElementById('account-policy-save');
        if (btn) btn.disabled = true;
        window.ScrcpyGateApi.configured('account.policy.update', {
          method: 'PUT',
          body: { expiryReminderDays: days, stopAlasOnExpiry: stop }
        }).then(function (payload) {
          if (payload) { accountPolicy = payload; }
          renderAccountPolicy();
          showToast('账户到期策略已保存', 'success');
          return loadUsers();
        }).catch(function (error) {
          showToast(apiError(error), 'error');
        }).finally(function () {
          if (btn) btn.disabled = false;
        });
      }
      (function wireAccountPolicy() {
        var btn = document.getElementById('account-policy-save');
        if (btn) btn.addEventListener('click', saveAccountPolicy);
      })();

      /* ---------- 首次渲染：接口返回后显示数据 ---------- */
      render();
      renderAccountPolicy();
      loadAccountPolicy();
      loadUsers().catch(function () {});
      if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
        window.ScrcpyGateI18n.on(function () { render(); });
      }
    })();
