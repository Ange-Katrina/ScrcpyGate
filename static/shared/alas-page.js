/* Extracted from static/pages/alas.html. */
(function () {
      'use strict';
      var dashboard = window.ScrcpyGateDashboard || {};

      /* ===== 数据模型 ===== */
      var DEVICES = [];
      var USERS = [];
      var CONFIGS = [];
      var RELS = [];
      var FEATURE_GROUPS = [];
      var featuresLoaded = false;

      /* ===== 全局状态 ===== */
      var serviceEnabled = false;
      var serviceKnown = false;
      var connState = 'unchecked'; /* unchecked / connected / timeout / unreachable / token-missing */
      var lastCheckText = '—';
      var tokenConfigured = false;
      var tokenKnown = false;
      var tokenUpdatedAt = '—';
      // 服务端 ALAS Token 加密密钥状态（{configured, valid, source}）；
      // 旧服务端不带该字段时为 null，页面不做提示。
      var tokenKeyState = null;
      // 隐藏清单（写死在服务端）的列表顺序，与后端 RULE_KEYS 一致
      var RULE_ORDER = ['labels', 'route_markers', 'field_prefixes', 'settings_markers', 'settings_tasks'];
      var workbenchAlasVisible = true;
      var syncState = 'loading'; /* loading / empty / error / ready */
      var syncTimeText = '—';
      var dataState = 'loading';
      var dataErrorText = '';
      var alasExternalUrl = '';
      var currentView = 'users';
      var selectedUserId = null;
      var selectedCfgId = null;
      var userFilter = 'all';
      var userQuery = '';
      var cfgQuery = '';
      var linkMode = 'add'; /* add / edit */
      var linkRelId = null;
      var linkPresetUser = null;
      var linkPresetCfg = null;
      var linkGrantContext = '';

      /* ===== 工具函数 ===== */
      function $(id) { return document.getElementById(id); }
      function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      }
      function icon(name) { return '<i data-lucide="' + name + '"></i>'; }
      function refreshIcons() { if (window.lucide) { lucide.createIcons(); } }
      function nowTime() {
        var d = new Date();
        function p(n) { return (n < 10 ? '0' : '') + n; }
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      }
      function daysUntil(expiryStr) {
        var t = new Date(expiryStr + 'T00:00:00').getTime();
        var now = new Date(); now.setHours(0, 0, 0, 0);
        return Math.round((t - now.getTime()) / 86400000);
      }
      function userById(id) { for (var i = 0; i < USERS.length; i++) { if (USERS[i].id === id) return USERS[i]; } return null; }
      function cfgById(id) { for (var i = 0; i < CONFIGS.length; i++) { if (CONFIGS[i].id === id) return CONFIGS[i]; } return null; }
      function devById(id) { for (var i = 0; i < DEVICES.length; i++) { if (DEVICES[i].id === id) return DEVICES[i]; } return null; }
      function cfgRels(cfgId) { return RELS.filter(function (r) { return r.cfgId === cfgId; }); }
      function userRels(userId) { return RELS.filter(function (r) { return r.userId === userId; }); }
      function resourceStatus(value) {
        if (value && typeof value === 'object') {
          if (typeof value.online === 'boolean') return value.online ? 'online' : 'offline';
          value = value.status || value.state || value.health;
        }
        var state = String(value == null ? '' : value).trim().toLowerCase();
        if (['online', 'healthy', 'connected', 'running', 'ready', 'ok'].indexOf(state) >= 0) return 'online';
        if (['offline', 'unreachable', 'disconnected', 'error', 'disabled', 'failed'].indexOf(state) >= 0) return 'offline';
        return 'unknown';
      }
      function userInit(u) { return esc(u.name.substring(0, 1).toUpperCase()); }
      function tr(s) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(String(s == null ? '' : s)) : String(s == null ? '' : s); }
      function statusText(value, fallbackZh, fallbackEn) {
        return dashboard.statusLabel ? dashboard.statusLabel(value, fallbackZh, fallbackEn) : tr(fallbackZh || value || '未检查');
      }
      function allowedExternalOrigins() {
        var cfg = window.ScrcpyGateConfig || {};
        var out = [];
        ['allowedAlasOrigins', 'allowedExternalOrigins'].forEach(function (key) {
          var value = cfg[key];
          if (typeof value === 'string') { value = [value]; }
          if (Array.isArray(value)) { out = out.concat(value); }
        });
        return out.map(function (origin) {
          try { return new URL(String(origin), window.location.origin).origin; } catch (e) { return ''; }
        }).filter(Boolean);
      }
      function safeExternalUrl(raw) {
        var value = String(raw == null ? '' : raw).trim();
        if (!value || value === '#') return '';
        try {
          var url = new URL(value, window.location.origin);
          if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
          if (url.origin === window.location.origin) return url.pathname + url.search + url.hash;
          return allowedExternalOrigins().indexOf(url.origin) >= 0 ? url.href : '';
        } catch (e) {
          return '';
        }
      }
      function setExternalLink(el, raw) {
        if (!el) return;
        var href = safeExternalUrl(raw);
        el.href = href || '#';
        el.setAttribute('aria-disabled', href ? 'false' : 'true');
        el.classList.toggle('disabled', !href);
      }
      function setLoading(btn, on, label) {
        if (!btn) return;
        if (on) {
          if (!btn.getAttribute('data-alas-orig')) { btn.setAttribute('data-alas-orig', btn.innerHTML); }
          btn.classList.add('loading');
          btn.innerHTML = '<span class="alas-spinner"></span>' + (label ? esc(tr(label)) : esc(tr('处理中…')));
        } else {
          btn.classList.remove('loading');
          var orig = btn.getAttribute('data-alas-orig');
          if (orig) { btn.innerHTML = orig; btn.removeAttribute('data-alas-orig'); }
        }
      }
      function toast(msg, type) {
        var wrap = $('alasToastWrap');
        if (!wrap) return;
        var t = document.createElement('div');
        t.className = 'alas-toast ' + (type || 'info');
        t.innerHTML = icon(type === 'success' ? 'circle-check' : (type === 'error' ? 'circle-x' : 'info')) + '<span>' + esc(tr(msg)) + '</span>';
        wrap.appendChild(t);
        refreshIcons();
        requestAnimationFrame(function () { t.classList.add('show'); });
        setTimeout(function () {
          t.classList.add('out');
          setTimeout(function () { if (t.parentNode) { t.parentNode.removeChild(t); } }, 260);
        }, 3000);
      }
      function confirmModal(opts) {
        var modal = $('alasConfirmModal'), mask = $('alasModalMask'), okBtn = $('alasConfirmOk'), cancelBtn = $('alasConfirmCancel');
        $('alasConfirmTitle').textContent = tr(opts.title || '确认操作');
        var body = $('alasConfirmBody');
        body.textContent = '';
        if (opts.bodyNode) {
          body.appendChild(opts.bodyNode);
        } else {
          body.textContent = tr(opts.body || '');
        }
        okBtn.className = 'btn-primary' + (opts.danger ? ' danger' : '');
        okBtn.innerHTML = icon('check') + '<span>' + esc(tr(opts.okText || '确认')) + '</span>';
        okBtn.removeAttribute('data-alas-orig');
        okBtn.onclick = function () {
          if (opts.onConfirm) { opts.onConfirm(function () { closeModal(); }, function (on) { setLoading(okBtn, on, '处理中…'); }); }
          else { closeModal(); }
        };
        cancelBtn.onclick = closeModal;
        modal.classList.add('open');
        mask.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
        modal.hidden = false;
        mask.hidden = false;
        refreshIcons();
      }
      function closeModal() {
        var modal = $('alasConfirmModal'), mask = $('alasModalMask');
        modal.classList.remove('open');
        mask.classList.remove('open');
        setTimeout(function () {
          modal.hidden = true;
          mask.hidden = true;
          modal.setAttribute('aria-hidden', 'true');
        }, 190);
      }
      var drawerOpeners = {};
      function openDrawer(id) {
        var maskId = id === 'alasConnDrawer' ? 'alasConnMask' : 'alasLinkMask';
        if (!$(id).classList.contains('open')) drawerOpeners[id] = document.activeElement;
        $(id).classList.add('open');
        $(maskId).classList.add('open');
        $(id).setAttribute('aria-hidden', 'false');
        $(id).removeAttribute('inert');
        $(maskId).hidden = false;
        var first = $(id).querySelector('button');
        if (first) first.focus({ preventScroll: true });
      }
      function closeDrawer(id) {
        var maskId = id === 'alasConnDrawer' ? 'alasConnMask' : 'alasLinkMask';
        if ($(id).contains(document.activeElement)) {
          var opener = drawerOpeners[id];
          if (opener && opener.isConnected && !opener.disabled && !opener.closest('[inert]')) opener.focus({ preventScroll: true });
          if ($(id).contains(document.activeElement)) document.activeElement.blur();
        }
        $(id).classList.remove('open');
        $(maskId).classList.remove('open');
        $(id).setAttribute('aria-hidden', 'true');
        $(id).setAttribute('inert', '');
        setTimeout(function () { $(maskId).hidden = true; }, 190);
      }
       function apiError(error) { return tr(window.ScrcpyGateApi ? window.ScrcpyGateApi.errorMessage(error) : '数据服务不可用'); }
       /* 改绑到另一台设备＝移动：服务端先回 409（结构化 detail），
          取出来交给二次确认，确认后带 confirmMove 重试。 */
       function alasMoveConfirm(error) {
         var payload = error && error.detail && error.detail.payload;
         var detail = payload && payload.detail;
         if (detail && typeof detail === 'object' && detail.code === 'alas_binding_move_confirm') return detail;
         return null;
       }
       function apiList(payload) { return window.ScrcpyGateApi ? window.ScrcpyGateApi.list(payload) : []; }
       function unwrapData(payload) {
         if (!payload || typeof payload !== 'object') { return {}; }
         return payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data) ? payload.data : payload;
       }
       function guardService(endpoint) {
         if (!serviceEnabled) { toast('ALAS 服务未启用', 'error'); return false; }
         if (endpoint && !window.ScrcpyGateApi.isConfigured(endpoint)) { toast('接口尚未配置：' + endpoint, 'error'); return false; }
         return true;
       }

       function normalizeAlasUser(u) {
         u = u || {};
         return { id: String(u.id || u.userId || u.username || ''), name: String(u.name || u.displayName || u.username || u.email || u.id || '未命名用户'), role: String(u.role || 'user'), status: String(u.status || (u.disabled ? 'disabled' : 'active')), expiry: u.expiry || u.expiresAt || '', deviceIds: u.deviceIds === null ? null : (Array.isArray(u.deviceIds) ? u.deviceIds.map(String) : []) };
       }
       function normalizeAlasDevice(d) {
         d = d || {};
         var status = resourceStatus(d);
         return { id: String(d.id || d.deviceId || d.serial || ''), name: String(d.name || d.displayName || d.model || d.serial || d.id || '未命名设备'), model: String(d.model || d.product || '—'), status: status, online: status === 'online' ? true : (status === 'offline' ? false : null) };
       }
       function normalizeAlasConfig(c) {
         c = c || {};
         return { id: String(c.id || c.configId || c.name || ''), name: String(c.name || c.displayName || c.id || '未命名配置'), task: String(c.task || c.taskName || '—'), inRuntime: c.inRuntime !== false, device: c.device || c.deviceId || null, bound: c.bound !== false, runtime: String(c.runtime || c.status || 'unknown').trim().toLowerCase(), stopReason: c.stopReason || null, updatedAt: c.updatedAt || c.updated_at || '—', openUrl: c.openUrl || c.url || '' };
       }
       function normalizeAlasRel(r) {
         r = r || {};
         return { id: String(r.id || r.relationId || ''), userId: String(r.userId || r.user_id || ''), cfgId: String(r.cfgId || r.configId || r.config_id || ''), device: r.device || r.deviceId || null, def: !!(r.def || r.default || r.isDefault), run: r.run !== false && r.canRun !== false, edit: !!(r.edit || r.canEdit), updatedAt: r.updatedAt || r.updated_at || '—' };
       }
       function applyOverview(payload) {
         var d = unwrapData(payload);
         var users = d.users || d.accounts || apiList(d.userList);
         var devices = d.devices || apiList(d.deviceList);
         var configs = d.configs || d.configurations || apiList(d.configList);
         var relations = d.relations || d.rels || d.assignments || apiList(d.relationList);
         USERS = (Array.isArray(users) ? users : []).map(normalizeAlasUser).filter(function (u) { return u.id; });
         DEVICES = (Array.isArray(devices) ? devices : []).map(normalizeAlasDevice).filter(function (x) { return x.id; });
         CONFIGS = (Array.isArray(configs) ? configs : []).map(normalizeAlasConfig).filter(function (c) { return c.id; });
         RELS = (Array.isArray(relations) ? relations : []).map(normalizeAlasRel).filter(function (r) { return r.id && r.userId && r.cfgId; });
         var serviceValue = d.serviceEnabled !== undefined ? d.serviceEnabled : d.enabled;
         serviceKnown = typeof serviceValue === 'boolean';
         serviceEnabled = serviceKnown && serviceValue;
         connState = d.connection && (d.connection.state || d.connection.status) || (d.runtimeConnected === true ? 'connected' : 'unchecked');
          var tokenValue = d.tokenConfigured !== undefined ? d.tokenConfigured : d.hasToken;
          tokenKnown = typeof tokenValue === 'boolean';
          tokenConfigured = tokenKnown && tokenValue;
          workbenchAlasVisible = d.workbenchAlasVisible !== false && d.workbench_alas_visible !== false;
          tokenKeyState = d.tokenKey || d.token_key || null;
         tokenUpdatedAt = d.tokenUpdatedAt || d.tokenUpdatedAtText || '—';
         lastCheckText = d.lastCheckAt || d.lastCheckText || '—';
         syncTimeText = d.syncedAt || d.syncTime || '—';
         alasExternalUrl = safeExternalUrl(d.externalUrl || d.alasUrl || d.openUrl || '');
          $('alasServiceEnabled').checked = serviceEnabled;
          $('alasWorkbenchVisible').checked = workbenchAlasVisible;
         $('alasRuntimeUrl').value = d.runtimeUrl || d.runtimeURL || '';
         dataState = 'ready';
         syncState = CONFIGS.length ? 'ready' : 'empty';
         if (selectedUserId && !userById(selectedUserId)) { selectedUserId = null; }
         if (selectedCfgId && !cfgById(selectedCfgId)) { selectedCfgId = null; }
         if (!selectedUserId && USERS.length) { selectedUserId = USERS[0].id; }
         if (!selectedCfgId && CONFIGS.length) { selectedCfgId = CONFIGS[0].id; }
         setExternalLink($('alasOpenPageBtn'), alasExternalUrl);
         $('alasCfgOpenAlas').href = '#';
         renderAll();
       }
       var overviewRequest = null;
       var alasLastRefreshAt = 0;
       /* 打开 ALAS 管理页时扫一次：把 ALAS 配置里模拟器 ADB 地址对应的设备补成缺失的管理员关联。
          只跑一次（幂等），Runtime 不可用或没配 Token 时静默跳过；有新建或需要人工确认才提示。 */
       var autoBindState = 'idle';
       function maybeAutoBind() {
         if (autoBindState !== 'idle') { return; }
         if (!window.ScrcpyGateApi.isConfigured('alas.autoBind')) { return; }
         if (!serviceEnabled || !tokenConfigured) { return; }
         autoBindState = 'running';
         window.ScrcpyGateApi.configured('alas.autoBind', { method: 'POST', body: {} }).then(function (payload) {
           var d = unwrapData(payload) || {};
           autoBindState = 'done';
           if (!d.ok) { return; }
           var created = d.created || [];
           var skipped = d.skipped || [];
           if (created.length) {
             toast('已按 ALAS 配置里的 ADB 地址自动绑定 ' + created.length + ' 个配置', 'success');
             // 等本轮 overview 请求收尾（overviewRequest 在 finally 里才清空）再重新拉，否则会被去重。
             window.setTimeout(function () { loadOverview(false).catch(function () {}); }, 0);
             return;
           }
           var conflicts = skipped.filter(function (item) {
             return item && (item.reason === 'owned_by_other' || item.reason === 'ambiguous');
           });
           if (conflicts.length) {
             toast('有 ' + conflicts.length + ' 个配置需要人工确认（已被其它用户占用或匹配不唯一）', 'info');
           }
         }).catch(function () { autoBindState = 'done'; });
       }
       function loadOverview(showMessage, autoRefresh) {
         var background = dataState === 'ready' && (USERS.length || DEVICES.length || CONFIGS.length || RELS.length);
         if (overviewRequest) return overviewRequest;
         if (!background) {
           dataState = 'loading'; syncState = 'loading'; renderAll();
         }
         alasLastRefreshAt = Date.now();
         overviewRequest = window.ScrcpyGateApi.configured('alas.overview', { query: { include: 'users,devices,configs,relations', refresh: !autoRefresh }, background: !!(autoRefresh && background) }).then(function (payload) {
           applyOverview(payload);
           maybeAutoBind();
           if (showMessage) { toast('ALAS 数据已刷新', 'success'); }
           return payload;
         }).catch(function (error) {
           dataErrorText = apiError(error);
           if (background) {
             // 后台心跳失败不清空当前数据，也不切换骨架屏；保留最后一次可用状态。
             dataState = 'ready';
             syncState = CONFIGS.length ? 'ready' : 'error';
             if (showMessage) toast(dataErrorText, 'error');
             return Promise.reject(error);
           }
           dataState = 'error'; syncState = 'error';
           USERS = []; DEVICES = []; CONFIGS = []; RELS = []; selectedUserId = null; selectedCfgId = null; serviceEnabled = false; serviceKnown = false; tokenConfigured = false; tokenKnown = false; tokenKeyState = null; alasExternalUrl = '';
           renderAll();
           toast(dataErrorText, 'error');
           throw error;
         }).finally(function () { overviewRequest = null; });
         return overviewRequest;
       }

      /* ===== 统计与渲染 ===== */
      function computeStats() {
        var userIds = {}, cfgIds = {}, conflictIds = {}, runtimeCount = 0, i;
        for (i = 0; i < RELS.length; i++) { userIds[RELS[i].userId] = 1; cfgIds[RELS[i].cfgId] = 1; }
        for (i = 0; i < CONFIGS.length; i++) {
          if (CONFIGS[i].inRuntime) { runtimeCount++; }
          if (cfgRels(CONFIGS[i].id).length > 1) { conflictIds[CONFIGS[i].id] = 1; }
        }
        return {
          runtime: runtimeCount,
          users: Object.keys(userIds).length,
          allocated: Object.keys(cfgIds).length,
          conflicts: Object.keys(conflictIds).length,
          conflictsList: CONFIGS.filter(function (c) { return cfgRels(c.id).length > 1; })
        };
      }
       function connChipState() {
         var map = {
           unchecked: { cls: 'alas-none', text: statusText('unknown') },
           connected: { cls: 'online', text: statusText('connected') },
           timeout: { cls: 'offline', text: statusText('timeout') },
           unreachable: { cls: 'offline', text: statusText('unreachable') },
           'token-missing': { cls: 'offline', text: statusText('token_missing') },
           token_missing: { cls: 'offline', text: statusText('token_missing') },
           token_invalid: { cls: 'offline', text: statusText('token_invalid') },
           token_error: { cls: 'offline', text: tr('Token 解密失败') },
           invalid_url: { cls: 'alas-none', text: tr('地址无效') },
           http_error: { cls: 'offline', text: statusText('http_error') }
         };
        return map[connState] || map.unchecked;
      }
       function renderTokenKeyWarning() {
         var box = $('alasTokenKeyWarning');
         if (!box) return;
         box.hidden = !(tokenKeyState && tokenKeyState.valid === false);
       }
       function renderStats() {
         var s = computeStats();
         var c = connChipState();
        /* 状态条 */
        var dot = $('alasStatusDot');
        dot.className = 'alas-status-dot ' + (c.cls === 'online' ? 'ok' : (c.cls === 'alas-none' ? '' : 'bad'));
         $('alasStatusRuntime').textContent = dataState === 'error' ? statusText('unreachable', '不可用', 'Unavailable') : (dataState === 'loading' ? tr('加载中…') : c.text);
         $('alasStatusService').textContent = dataState === 'ready' ? (serviceKnown ? (serviceEnabled ? tr('已启用') : statusText('disabled')) : statusText('unknown')) : tr('未加载');
        $('alasStatusConfigs').textContent = CONFIGS.length + ' 个配置';
        $('alasStatusConflict').textContent = s.conflicts + ' 个归属冲突';
        $('alasStatusConflictWrap').classList.toggle('warning', s.conflicts > 0);
         $('alasStatusSync').textContent = syncTimeText === '从未' ? tr('从未') : syncTimeText;
        /* 视图徽标 */
        $('alasTabUsersBadge').textContent = USERS.length;
        $('alasTabConfigsBadge').textContent = CONFIGS.length;
         $('alasUsersTotal').textContent = dataState === 'error' ? tr('数据服务不可用') : (dataState === 'loading' ? tr('正在加载…') : USERS.length + ' ' + tr('个可授权账户'));
        /* 冲突警告条 */
        var banner = $('alasConflictBanner');
        banner.hidden = s.conflicts === 0;
        $('alasConflictBannerText').textContent = tr('发现 ') + s.conflicts + ' ' + tr('条配置归属冲突');
        /* 连接抽屉内状态 */
        var tokenChip = $('alasTokenChip');
        if (!tokenKnown) {
          tokenChip.className = 'inline-chip alas-none';
           tokenChip.textContent = statusText('unknown');
        } else if (tokenConfigured) {
          tokenChip.className = 'inline-chip online';
           tokenChip.textContent = tr('已配置 · 更新于 ' + tokenUpdatedAt);
        } else {
          tokenChip.className = 'inline-chip alas-none';
           tokenChip.textContent = tr('缺失');
        }
         $('alasLastCheckText').textContent = lastCheckText;
         renderTokenKeyWarning();
        var chipEl = $('alasConnResultChip');
        chipEl.className = 'inline-chip ' + (c.cls === 'online' ? 'online' : (c.cls === 'alas-none' ? 'alas-none' : 'offline'));
        chipEl.textContent = c.text;
        var svcChip = $('alasServiceChip');
         if (svcChip) {
           svcChip.className = 'chip ' + (!serviceKnown ? 'info' : (serviceEnabled ? 'ok' : 'warn'));
          svcChip.textContent = !serviceKnown ? statusText('unknown') : (serviceEnabled ? tr('当前已启用') : statusText('disabled'));
         }
         var workbenchChip = $('alasWorkbenchChip');
         if (workbenchChip) {
           workbenchChip.className = 'chip ' + (workbenchAlasVisible ? 'ok' : 'warn');
           workbenchChip.textContent = workbenchAlasVisible ? tr('工作台显示') : tr('工作台已隐藏');
         }
         $('alasSyncBtn').disabled = !serviceEnabled || !window.ScrcpyGateApi.isConfigured('alas.overview');
         $('alasCheckConnBtn').disabled = !window.ScrcpyGateApi.isConfigured('alas.connection.check');
         $('alasRefreshAllBtn').disabled = !window.ScrcpyGateApi.isConfigured('alas.overview');
         $('alasAddRelBtn').disabled = !window.ScrcpyGateApi.isConfigured('alas.relations.create');
         $('alasSavePermBtn').disabled = !serviceEnabled || !window.ScrcpyGateApi.isConfigured('alas.relations.update');
         $('alasCfgRefreshState').disabled = !serviceEnabled || !window.ScrcpyGateApi.isConfigured('alas.config.status');
         $('alasCfgPrimaryBtn').disabled = !serviceEnabled || !window.ScrcpyGateApi.isConfigured('alas.config.action');
       }
       function userStatusChip(u) {
          if (u.status === 'disabled') { return { cls: 'danger', text: statusText('disabled') }; }
          if (u.status === 'expired') { return { cls: 'danger', text: statusText('expired') }; }
          if (!u.expiry) { return { cls: 'ok', text: statusText('normal') }; }
          var d = daysUntil(u.expiry);
         if (d < 0) { return { cls: 'danger', text: statusText('expired') }; }
         if (d <= 30) { return { cls: 'warn', text: tr('剩 ' + d + ' 天') }; }
         return { cls: 'ok', text: statusText('normal') };
       }
       function renderUsers() {
         var list = $('alasUserList');
         if (dataState === 'loading') { list.innerHTML = '<div class="alas-list-empty">正在加载用户数据…</div>'; return; }
         if (dataState === 'error') { list.innerHTML = '<div class="alas-list-empty">' + esc(dataErrorText || '数据服务不可用') + '</div>'; return; }
        var out = '', i;
        var q = userQuery.toLowerCase();
        var conflictIds = computeStats().conflictsList.map(function (c) { return c.id; });
        for (i = 0; i < USERS.length; i++) {
          var u = USERS[i];
          var rels = userRels(u.id);
          var linked = rels.length > 0;
          if (userFilter === 'linked' && !linked) { continue; }
          if (userFilter === 'unlinked' && linked) { continue; }
          if (userFilter === 'conflict') {
            var inConflict = false;
            for (var k = 0; k < rels.length; k++) { if (conflictIds.indexOf(rels[k].cfgId) >= 0) { inConflict = true; break; } }
            if (!inConflict) { continue; }
          }
          if (q && (u.name.toLowerCase().indexOf(q) === -1) && (u.role.indexOf(q) === -1)) { continue; }
          var st = userStatusChip(u);
          var sel = selectedUserId === u.id ? ' active' : '';
          var roleTxt = u.role === 'admin' ? '管理员' : '普通用户';
           var expiryTxt = u.role === 'admin' ? '长期有效' : (u.expiry ? (st.text === '正常' || st.text === '已停用' ? '到期 ' + u.expiry : st.text + ' ' + u.expiry) : st.text);
          out += '<button class="alas-list-button' + sel + '" type="button" aria-pressed="' + (selectedUserId === u.id) + '" aria-controls="alasUserDetail" data-uid="' + esc(u.id) + '">'
            + '<span class="alas-list-avatar">' + userInit(u) + '</span>'
            + '<span class="alas-list-main"><strong data-i18n-skip>' + esc(u.name) + '</strong><span>' + esc(roleTxt + ' · ' + expiryTxt) + '</span></span>'
            + '<span class="alas-list-meta">' + (rels.length ? rels.length + ' 个配置' : '未关联') + '</span>'
            + '</button>';
        }
         if (!out) {
           out = '<div class="alas-list-empty">' + icon('search-x') + '<div>' + (USERS.length ? '没有匹配的用户' : '暂无用户数据') + '</div></div>';
        }
        list.innerHTML = out;
        refreshIcons();
      }
      function restoreListFocus(listId, attribute, value) {
        var buttons = $(listId).querySelectorAll('.alas-list-button');
        for (var i = 0; i < buttons.length; i++) {
          if (buttons[i].getAttribute(attribute) === value) {
            buttons[i].focus({ preventScroll: true });
            return;
          }
        }
      }
      function runtimeText(value) {
        var state = String(value || '').toLowerCase();
        if (state === 'running') { return statusText('running'); }
        if (state === 'connected' || state === 'online') { return statusText('connected'); }
        if (state === 'stopped' || state === 'idle') { return statusText('stopped'); }
        if (state === 'error') { return statusText('error'); }
        if (state === 'unreachable' || state === 'disconnected') { return statusText('unreachable'); }
        if (state === 'disabled' || state === 'unconfigured') { return statusText(state); }
        return statusText('unknown');
      }
      function renderRelList() {
        if (!selectedUserId) { return; }
        var list = $('alasRelList');
        var u = userById(selectedUserId);
        var rels = userRels(selectedUserId);
        // 管理员自动获得全部 Runtime 配置权限。自动绑定（按 ALAS 配置里的 ADB 地址）落下的
        // 关联是真实绑定行：先列出它们（可编辑/可解除），剩下的 Runtime 配置仍按「自动授权」展示。
        if (u && u.role === 'admin') {
          var autoCfg = CONFIGS.filter(function (c) { return c.inRuntime; });
          var boundIds = {};
          rels.forEach(function (r) { boundIds[r.cfgId] = true; });
          var remaining = autoCfg.filter(function (c) { return !boundIds[c.id]; });
          $('alasRelCount').textContent = rels.length + ' 个已绑定 · ' + remaining.length + ' 个自动授权';
          var out = '';
          for (var i = 0; i < rels.length; i++) { out += relRowHtml(rels[i]); }
          out += remaining.map(function (c) {
            return '<div class="alas-assignment-row">'
              + '<div class="alas-assignment-main"><strong data-i18n-skip>' + esc(c.name) + '</strong><span>自动授权 · 无需关联</span></div>'
              + '<div class="alas-assignment-main"><strong>' + runtimeText(c.runtime) + '</strong><span>Runtime 状态</span></div>'
              + '<div class="alas-permissions"><span class="chip info">管理员权限</span></div>'
              + '</div>';
          }).join('');
          list.innerHTML = out || '<div class="alas-rel-empty">Runtime 配置目录为空</div>';
          refreshIcons();
          return;
        }
        $('alasRelCount').textContent = rels.length + ' 个配置';
        if (!rels.length) {
          list.innerHTML = '<div class="alas-rel-empty">该用户暂无关联的 ALAS 配置</div>';
          return;
        }
        var html = '';
        for (var j = 0; j < rels.length; j++) { html += relRowHtml(rels[j]); }
        list.innerHTML = html;
        refreshIcons();
      }
      /* 单条关联行：管理员与普通用户共用（管理员行因此也能编辑/解除）。 */
      function relRowHtml(r) {
        var cfg = cfgById(r.cfgId);
        if (!cfg) { return ''; }
        var dev = devById(r.device);
        var conflictIds = computeStats().conflictsList.map(function (c) { return c.id; });
        var isConflict = conflictIds.indexOf(r.cfgId) >= 0;
        var subTxt = (r.def ? '默认配置' : '普通配置') + (isConflict ? ' · 归属冲突' : '');
        return '<div class="alas-assignment-row" data-rid="' + esc(r.id) + '">'
          + '<div class="alas-assignment-main"><strong data-i18n-skip>' + esc(cfg.name) + '</strong><span>' + esc(subTxt) + '</span></div>'
          + '<div class="alas-assignment-main"><strong data-i18n-skip>' + esc(dev ? dev.name : '设备未绑定') + '</strong><span>绑定设备</span></div>'
          + '<div class="alas-assignment-main"><strong>' + runtimeText(cfg.runtime) + '</strong><span>Runtime 状态</span></div>'
          + '<div class="alas-permissions">'
          + '<span class="chip ' + (r.run ? 'ok' : '') + '">' + (r.run ? '允许运行' : '仅查看') + '</span>'
          + '<span class="chip ' + (r.edit ? 'info' : '') + '">' + (r.edit ? '允许编辑' : '不可编辑') + '</span>'
          + '</div>'
          + '<div class="alas-row-actions">'
          + '<button class="alas-row-action" type="button" data-rel-edit-btn="' + esc(r.id) + '" title="编辑关联" aria-label="编辑关联">' + icon('pencil') + '</button>'
          + '<button class="alas-row-action danger" type="button" data-rel-del-btn="' + esc(r.id) + '" title="解除关联" aria-label="解除关联">' + icon('trash-2') + '</button>'
          + '</div></div>';
      }
       function renderUserDetail() {
         $('alasExpiredNote').hidden = true;
         if (!selectedUserId || !userById(selectedUserId)) {
           $('alasUserDetailAvatar').textContent = '—'; $('alasUserDetailName').textContent = tr('请选择用户'); $('alasUserDetailMeta').textContent = dataState === 'error' ? tr('数据服务不可用') : tr('暂无可显示的用户'); $('alasRelList').innerHTML = '<div class="alas-rel-empty">' + esc(tr('暂无配置关联')) + '</div>'; $('alasRelCount').textContent = '0 ' + tr('个配置'); $('alasAddRelBtn').disabled = true; return;
         }
        var u = userById(selectedUserId);
        $('alasUserDetailAvatar').textContent = userInit(u);
        $('alasUserDetailName').textContent = u.name;
        var st = userStatusChip(u);
        var rels = userRels(u.id);
        var roleTxt = u.role === 'admin' ? '管理员' : '普通用户';
         var expiryTxt = u.role === 'admin' ? '长期有效' : (u.expiry ? (st.text === '正常' || st.text === '已停用' ? '到期 ' + u.expiry : st.text + ' · ' + u.expiry) : st.text);
        $('alasUserDetailMeta').textContent = roleTxt + ' · ' + expiryTxt + ' · ' + rels.length + ' 个配置';
        $('alasAdminNote').hidden = u.role !== 'admin';
        $('alasAddRelBtn').hidden = u.role === 'admin';
        var showExpiryNotice = u.role !== 'admin' && u.status === 'expired' && rels.length > 0;
        $('alasExpiredNote').hidden = !showExpiryNotice;
        $('alasExpiredNoteText').textContent = showExpiryNotice ? tr('账户已到期，请检查已关联的 ALAS 配置运行状态') : '';
        var conflictIds = computeStats().conflictsList.map(function (c) { return c.id; });
        var hasConflict = false, conflictCfgName = '';
        for (var i = 0; i < rels.length; i++) {
          if (conflictIds.indexOf(rels[i].cfgId) >= 0) {
            hasConflict = true;
            var c0 = cfgById(rels[i].cfgId);
            conflictCfgName = c0 ? c0.name : '';
            break;
          }
        }
        $('alasUserAlert').hidden = !hasConflict;
        if (hasConflict) {
          $('alasUserAlertText').textContent = '「' + conflictCfgName + '」存在历史归属冲突';
        }
        renderRelList();
        refreshIcons();
      }
      function cfgDefaultCfg(cfg) {
        var rels = cfgRels(cfg.id);
        for (var i = 0; i < rels.length; i++) { if (rels[i].def) { return rels[i]; } }
        return null;
      }
       function renderConfigs() {
         var list = $('alasCfgList');
        var out = '', i;
        var q = cfgQuery.toLowerCase();
        if (syncState === 'loading') {
          var sk = '';
          for (i = 0; i < 3; i++) {
            sk += '<div class="alas-list-empty" style="padding:18px 12px"><span class="alas-skeleton alas-skeleton-line" style="display:inline-block;width:70%"></span></div>';
          }
          list.innerHTML = sk;
          return;
        }
         if (syncState === 'error') {
           list.innerHTML = '<div class="alas-error-bar">' + icon('alert-triangle') + '<span>' + esc(dataErrorText || '配置目录同步失败，请重试') + '</span><button class="row-btn" type="button" id="alasCfgRetryBtn">' + icon('rotate-cw') + '<span>重试</span></button></div>';
          refreshIcons();
          var rb = $('alasCfgRetryBtn');
          if (rb) { rb.onclick = function () { if (!guardService('alas.overview')) { return; } runSync(); }; }
          return;
        }
        if (syncState === 'empty') {
          list.innerHTML = '<div class="alas-sync-state">' + icon('folder-open') + '<div>Runtime 配置目录为空</div><small>点击右上角刷新目录重试</small></div>';
          refreshIcons();
          return;
        }
        var shown = 0;
        for (i = 0; i < CONFIGS.length; i++) {
          var cfg = CONFIGS[i];
          if (q && cfg.name.toLowerCase().indexOf(q) === -1) { continue; }
          shown++;
          var rels = cfgRels(cfg.id);
          var sel = selectedCfgId === cfg.id ? ' active' : '';
          var ownerTxt = rels.length ? (rels.length > 1 ? '多用户归属' : (userById(rels[0].userId) ? userById(rels[0].userId).name : '—')) : '未分配';
          var devTxt = devById(cfg.device) ? devById(cfg.device).name : '设备未绑定';
          var runCls = cfg.runtime === 'running' ? 'ok' : (cfg.runtime === 'error' || cfg.runtime === 'unreachable' ? 'warn' : (cfg.runtime === 'unknown' ? 'unknown' : ''));
          var runTxt = runtimeText(cfg.runtime);
          var linkedOnly = !cfg.inRuntime && rels.length > 0;
          out += '<button class="alas-list-button' + sel + '" type="button" aria-pressed="' + (selectedCfgId === cfg.id) + '" aria-controls="alasCfgDetail" data-cid="' + esc(cfg.id) + '">'
            + '<span class="alas-list-main"><strong><span data-i18n-skip>' + esc(cfg.name) + '</span>' + (linkedOnly ? ' <small style="color:var(--admin-faint);font-weight:400">· 仅关联记录</small>' : '') + '</strong>'
            + '<span>' + esc(ownerTxt + ' · ' + devTxt) + '</span></span>'
            + '<span class="chip ' + runCls + '">' + runTxt + '</span>'
            + '</button>';
        }
        if (!shown) {
           out = '<div class="alas-list-empty">' + icon('search-x') + '<div>' + (CONFIGS.length ? '没有匹配的配置' : '暂无配置数据') + '</div></div>';
        }
        list.innerHTML = out;
        refreshIcons();
      }
      function stopReasonText(cfg) {
        if (!cfg.stopReason) { return ''; }
        if (cfg.stopReason === 'expired') { return '停止原因：账户到期，ALAS 自动停止'; }
        if (cfg.stopReason === 'permission') { return '停止原因：权限不足'; }
        if (cfg.stopReason === 'device') { return '停止原因：绑定设备失效'; }
        return String(cfg.stopReason);
      }
       function renderCfgDetail() {
         if (!selectedCfgId || !cfgById(selectedCfgId)) { $('alasCfgDetailName').textContent = tr('请选择配置'); $('alasCfgDetailChips').innerHTML = ''; ['alasCfgOwner','alasCfgDevice','alasCfgDefault','alasCfgRun','alasCfgEdit','alasCfgError','alasCfgRunTask'].forEach(function (id) { $(id).textContent = '—'; }); $('alasCfgOpenAlas').href = '#'; $('alasCfgPrimaryBtn').disabled = true; return; }
        var cfg = cfgById(selectedCfgId);
        $('alasCfgDetailName').textContent = cfg.name;
        var rels = cfgRels(cfg.id);
        var u0 = rels.length ? userById(rels[0].userId) : null;
        var defRel = cfgDefaultCfg(cfg);
        var dev = devById(cfg.device);
        var permRel = defRel || rels[0] || null;
        var ownerText = rels.length ? (rels.length > 1 ? '多用户归属' : (u0 ? u0.name : '—')) : '未分配';
        $('alasCfgOwner').textContent = ownerText;
        $('alasCfgDevice').textContent = dev ? dev.name : '未绑定';
        $('alasCfgDefault').textContent = defRel ? '是' : '否';
        $('alasCfgRun').textContent = permRel ? (permRel.run ? '允许' : '不允许') : '未设置';
        $('alasCfgEdit').textContent = permRel ? (permRel.edit ? '允许' : '不允许') : '未设置';
        var errText = stopReasonText(cfg);
        var errEl = $('alasCfgError');
        errEl.textContent = errText || '无错误';
        errEl.classList.toggle('err', !!errText);
        /* 头部 chips：运行状态 + Runtime 状态 */
        var runCls = cfg.runtime === 'running' ? 'ok' : (cfg.runtime === 'error' ? 'warn' : (cfg.runtime === 'unknown' ? 'unknown' : ''));
        var runTxt = runtimeText(cfg.runtime);
        $('alasCfgDetailChips').innerHTML =
          '<span class="chip ' + runCls + '">' + runTxt + '</span>'
          + '<span class="chip ' + (cfg.inRuntime ? 'info' : 'cyan') + '">' + (cfg.inRuntime ? '已进入 Runtime' : '仅关联记录') + '</span>'
          + (rels.length > 1 ? '<span class="chip warn">多用户归属</span>' : '');
         $('alasCfgRunTask').textContent = cfg.task || '—';
        var assignBtn = $('alasCfgAssignBtn');
        if (assignBtn) { assignBtn.hidden = rels.length > 0; }
        setExternalLink($('alasCfgOpenAlas'), safeExternalUrl(cfg.openUrl) || alasExternalUrl);
        var btn = $('alasCfgPrimaryBtn');
        if (cfg.runtime === 'running') {
          btn.className = 'btn-primary danger';
          btn.innerHTML = icon('square') + '<span>停止配置</span>';
        } else if (cfg.runtime === 'error') {
          btn.className = 'btn-primary danger';
          btn.innerHTML = icon('rotate-cw') + '<span>重启异常配置</span>';
        } else {
          btn.className = 'btn-primary';
          btn.innerHTML = icon('play') + '<span>启动配置</span>';
        }
        btn.removeAttribute('data-alas-orig');
        refreshIcons();
      }
      function renderAll() {
        renderStats();
        renderUsers();
        renderUserDetail();
        renderConfigs();
        if (selectedCfgId) { renderCfgDetail(); }
      }
      /* ===== 视图切换 ===== */
      function switchView(v) {
        currentView = v;
        var tabs = document.querySelectorAll('.alas-tab');
        for (var i = 0; i < tabs.length; i++) {
          var on = tabs[i].getAttribute('data-view') === v;
          tabs[i].classList.toggle('active', on);
          tabs[i].setAttribute('aria-selected', on ? 'true' : 'false');
        }
        $('alasViewUsers').hidden = v !== 'users';
        $('alasViewConfigs').hidden = v !== 'configs';
        var featuresView = $('alasViewFeatures');
        if (featuresView) { featuresView.hidden = v !== 'features'; }
        if (v === 'users') { renderUsers(); renderUserDetail(); }
        if (v === 'configs') { renderConfigs(); if (selectedCfgId) { renderCfgDetail(); } }
        if (v === 'features') { loadFeatures(); renderFeatures(); loadVisibility(); }
      }
      /* ===== 隐藏范围（只读）=================================================
         隐藏清单已写死在服务端（app/alas_visibility.py 的 HIDDEN_ALAS_ENTRY_RULES 与
         app/alas.py 的 HIDDEN_ALAS_TASKS，ALAS 2.0 适配时再改），后台只做只读展示：
         没有开关、没有保存、也没有可编辑的规则文本域。 */
      function syncFeatureGroup(groupEl) {
        if (!groupEl) { return; }
        var items = groupEl.querySelectorAll('[data-feature-state]');
        var on = 0;
        for (var i = 0; i < items.length; i++) {
          if (items[i].getAttribute('data-visible') === 'true') { on++; }
        }
        var count = groupEl.querySelector('[data-feature-group-count]');
        if (count) { count.textContent = on + ' / ' + items.length; }
        groupEl.setAttribute('data-state', on === 0 ? 'none' : (on === items.length ? 'all' : 'partial'));
      }
      function syncFeatureGroups() {
        var groups = document.querySelectorAll('#alasFeatureList [data-feature-group]');
        for (var i = 0; i < groups.length; i++) { syncFeatureGroup(groups[i]); }
        var total = 0; var on = 0;
        var all = document.querySelectorAll('#alasFeatureList [data-feature-state]');
        for (var j = 0; j < all.length; j++) {
          total++;
          if (all[j].getAttribute('data-visible') === 'true') { on++; }
        }
        var count = $('alasFeatureCount');
        if (count) { count.textContent = on + ' / ' + total + ' ' + tr('项对普通用户可见'); }
        var badge = $('alasTabFeaturesBadge');
        if (badge) { badge.textContent = String(total ? total - on : 0); }
      }
      function renderFeatures() {
        var list = $('alasFeatureList');
        if (!list) { return; }
        if (!FEATURE_GROUPS.length) {
          list.innerHTML = '<div class="alas-empty-hint"><i data-lucide="toggle-left"></i><span>' + esc(tr('开关目录为空')) + '</span></div>';
        } else {
          var html = '';
          for (var i = 0; i < FEATURE_GROUPS.length; i++) {
            var group = FEATURE_GROUPS[i];
            var items = group.items || [];
            var on = 0;
            var itemHtml = '';
            for (var j = 0; j < items.length; j++) {
              var item = items[j];
              if (item.enabled) { on++; }
              var sharedHint = tr('与其它分组同名；按任务标记可精确区分，文字回退时会一起隐藏');
              itemHtml += '<div class="alas-feature-item" data-feature-id="' + esc(item.id || '') + '" data-state="' + (item.enabled ? 'visible' : 'hidden') + '">' +
                '<span class="alas-feature-label" data-i18n-skip>' + esc(item.label || '') +
                (item.shared ? '<i class="alas-feature-shared" data-lucide="info" title="' + esc(sharedHint) + '" aria-label="' + esc(sharedHint) + '"></i>' : '') +
                '</span>' +
                '<span class="alas-feature-state" data-feature-state data-visible="' + (item.enabled ? 'true' : 'false') + '">' + esc(item.enabled ? tr('可见') : tr('已隐藏')) + '</span>' +
                '</div>';
            }
            var state = on === 0 ? 'none' : (on === items.length ? 'all' : 'partial');
            // 默认全部收起：9 组 68 项一眼看不完；计数胶囊显示「可见 / 总数」。
            html += '<details class="alas-feature-group" data-feature-group data-state="' + state + '">' +
              '<summary class="alas-feature-group-head">' +
                '<i data-lucide="chevron-right" class="alas-feature-caret" aria-hidden="true"></i>' +
                '<span class="alas-feature-group-label" data-i18n-skip>' + esc(group.label || '') + '</span>' +
                '<span class="alas-feature-group-count" data-feature-group-count>' + on + ' / ' + items.length + '</span>' +
              '</summary>' +
              '<div class="alas-feature-items">' + itemHtml + '</div>' +
            '</details>';
          }
          list.innerHTML = html;
        }
        syncFeatureGroups();
        refreshIcons();
      }
      function loadFeatures() {
        if (featuresLoaded || !window.ScrcpyGateApi.isConfigured('alas.features')) { return; }
        featuresLoaded = true;
        window.ScrcpyGateApi.configured('alas.features').then(function (payload) {
          applyFeatures(payload);
        }).catch(function (error) {
          featuresLoaded = false;
          toast(apiError(error), 'error');
        });
      }
      function applyFeatures(payload) {
        var d = unwrapData(payload) || {};
        FEATURE_GROUPS = (d.groups || []).map(function (group) {
          return {
            id: group.id || '',
            label: group.label || '',
            partial: group.partial === true,
            items: (group.items || []).map(function (item) {
              return { id: item.id || '', label: item.label || '', shared: item.shared === true, enabled: item.enabled !== false };
            })
          };
        });
        renderFeatures();
      }
      /* ===== 管理员工具与后台页（写死的隐藏清单，只读展示） ===== */
      var visibilityLoaded = false;
      var visibilityCatalog = null;
      var visibilityRules = null;
      /* 与后端 alas_visibility._compact 一致：只留字母/数字（含中文），转小写。 */
      function visibilityCompact(value) {
        return String(value == null ? '' : value).replace(/[^\p{L}\p{N}]/gu, '').toLowerCase();
      }
      function visibilityEntryTokens(entry, key) {
        return ((entry.tokens || {})[key] || []).map(visibilityCompact);
      }
      function visibilityEntryState(entry) {
        var total = 0;
        var on = 0;
        RULE_ORDER.forEach(function (key) {
          var tokens = visibilityEntryTokens(entry, key);
          if (!tokens.length) { return; }
          var current = ((visibilityRules || {})[key] || []).map(visibilityCompact);
          total += tokens.length;
          tokens.forEach(function (token) { if (current.indexOf(token) >= 0) { on += 1; } });
        });
        if (!total) { return 'none'; }
        if (on === total) { return 'all'; }
        return on === 0 ? 'none' : 'partial';
      }
      function renderVisibility(snapshot) {
        if (!snapshot) { return; }
        visibilityCatalog = { groups: snapshot.entry_groups || [], entries: snapshot.entries || [] };
        visibilityRules = snapshot.rules || {};
        renderVisibilityEntries();
      }
      function renderVisibilityEntries() {
        var host = $('alasVisibilityEntries');
        if (!host || !visibilityCatalog) { return; }
        var groups = visibilityCatalog.groups || [];
        var entries = visibilityCatalog.entries || [];
        if (!entries.length) { host.innerHTML = ''; return; }
        var html = '';
        groups.forEach(function (group) {
          var members = entries.filter(function (entry) { return entry.group === group.id; });
          if (!members.length) { return; }
          html += '<div class="alas-visibility-group" data-visibility-group="' + esc(group.id) + '">' +
            '<div class="alas-visibility-group-head"><b>' + esc(tr(group.label)) + '</b>' +
            '<small>' + esc(tr(group.hint)) + '</small>' +
            '<span class="alas-visibility-group-count" data-visibility-group-count></span></div>';
          members.forEach(function (entry) {
            html += '<div class="alas-visibility-entry" data-visibility-entry="' + esc(entry.id) + '">' +
              '<span class="alas-visibility-entry-copy"><b>' + esc(tr(entry.label)) + '</b>' +
              '<small>' + esc(tr(entry.hint)) + '</small></span>' +
              '<span class="alas-feature-state" data-visibility-state></span>' +
              '</div>';
          });
          html += '</div>';
        });
        host.innerHTML = html;
        syncVisibilityEntryStates();
      }
      function syncVisibilityEntryStates() {
        var host = $('alasVisibilityEntries');
        if (!host || !visibilityCatalog) { return; }
        var entries = visibilityCatalog.entries || [];
        entries.forEach(function (entry) {
          var row = host.querySelector('[data-visibility-entry="' + entry.id + '"]');
          if (!row) { return; }
          var state = visibilityEntryState(entry);
          var chip = row.querySelector('[data-visibility-state]');
          if (chip) { chip.textContent = state === 'none' ? tr('可见') : tr('受限'); }
          row.setAttribute('data-state', state);
        });
        (visibilityCatalog.groups || []).forEach(function (group) {
          var card = host.querySelector('[data-visibility-group="' + group.id + '"]');
          if (!card) { return; }
          var members = entries.filter(function (entry) { return entry.group === group.id; });
          var restricted = members.filter(function (entry) { return visibilityEntryState(entry) !== 'none'; }).length;
          var count = card.querySelector('[data-visibility-group-count]');
          if (count) { count.textContent = restricted + ' / ' + members.length; }
          card.setAttribute('data-state', restricted === 0 ? 'none' : (restricted === members.length ? 'all' : 'partial'));
        });
      }
      function loadVisibility() {
        if (visibilityLoaded || !window.ScrcpyGateApi.isConfigured('alas.visibility')) { return; }
        visibilityLoaded = true;
        window.ScrcpyGateApi.configured('alas.visibility').then(function (payload) {
          renderVisibility(unwrapData(payload) || null);
        }).catch(function (error) {
          visibilityLoaded = false;
          toast(apiError(error), 'error');
        });
      }

      /* ===== 关联抽屉 ===== */
      function fillSelect(sel, opts, val) {
        var html = '';
        for (var i = 0; i < opts.length; i++) {
          html += '<option value="' + esc(opts[i].value) + '"' + (opts[i].value === val ? ' selected' : '') + '>' + esc(opts[i].label) + '</option>';
        }
        sel.innerHTML = html;
      }
      function userHasDeviceView(userId, deviceId) {
        var user = userById(userId);
        if (!user || !deviceId) return false;
        if (user.role === 'admin' || user.deviceIds === null) return true;
        return (user.deviceIds || []).indexOf(String(deviceId)) >= 0;
      }
      function linkOwnershipConflict(configId, userId) {
        return cfgRels(configId).filter(function (relation) {
          return relation.id !== linkRelId && relation.userId !== userId;
        })[0] || null;
      }
      function syncLinkForm() {
        var userId = $('alasLinkUser').value;
        var deviceId = $('alasLinkDevice').value;
        var configId = $('alasLinkConfig').value;
        var user = userById(userId);
        var device = devById(deviceId);
        var config = cfgById(configId);
        var complete = !!(user && device && config);
        $('alasLinkSummary').hidden = !complete;
        if (complete) {
          $('alasLinkSummaryText').textContent = user.name + ' 将通过 ' + device.name + ' 使用 ' + config.name;
        }
        var needsView = !!(user && device) && !userHasDeviceView(userId, deviceId);
        var grantContext = userId + ':' + deviceId;
        if (grantContext !== linkGrantContext) {
          $('alasLinkGrantView').checked = needsView;
          linkGrantContext = grantContext;
        }
        $('alasLinkViewNotice').hidden = !needsView;
        if (needsView) $('alasLinkViewText').textContent = user.name + ' 尚不能观看 ' + device.name + '，建议随关联一并授予。';
        var ownershipConflict = config ? linkOwnershipConflict(config.id, userId) : null;
        var deviceConflict = !!(config && config.device && String(config.device) !== String(deviceId));
        $('alasLinkDeviceWarn').hidden = !ownershipConflict && !deviceConflict;
        if (ownershipConflict) {
          var owner = userById(ownershipConflict.userId);
          $('alasLinkDeviceWarnText').textContent = '该配置已关联给用户 ' + (owner ? owner.name : ownershipConflict.userId) + '，请先解除原关联。';
        } else if (deviceConflict) {
          var occupied = devById(String(config.device));
          $('alasLinkDeviceWarnText').textContent = '该配置当前绑定到 ' + (occupied ? occupied.name : String(config.device)) + '。保存时不会静默覆盖。';
        }
        var permissions = [];
        $('alasLinkEdit').checked = $('alasLinkRun').checked;
        if ($('alasLinkRun').checked) permissions.push('运行与编辑');
        if ($('alasLinkDefault').checked) permissions.push('默认配置');
        $('alasLinkPermissionSummary').textContent = permissions.length ? permissions.join(' · ') : '无操作权限';
        $('alasLinkSaveBtn').disabled = !complete || !!ownershipConflict;
      }
       function openLinkDrawer(mode, relId, presetUser, presetCfg) {
        linkMode = mode;
        linkRelId = relId;
        linkPresetUser = presetUser || null;
        linkPresetCfg = presetCfg || null;
        $('alasLinkTitle').textContent = mode === 'edit' ? '编辑关联' : '新增关联';
        $('alasLinkSubtitle').textContent = mode === 'edit' ? '调整设备和访问权限' : '为用户连接设备与 ALAS 配置';
        $('alasLinkSaveBtn').querySelector('span').textContent = mode === 'edit' ? '保存更改' : '创建关联';
        var rel = null, u = null, cfg = null;
        if (mode === 'edit' && relId) { rel = RELS.filter(function (r) { return r.id === relId; })[0] || null; }
        if (rel) {
          u = userById(rel.userId);
          cfg = cfgById(rel.cfgId);
        } else if (presetUser) {
          u = userById(presetUser);
        } else if (presetCfg) {
          cfg = cfgById(presetCfg);
        }
        // 新建只允许普通用户；编辑时把该关联自己的用户也放进来 —— 自动绑定给管理员落下的
        // 关联行同样要能改设备（否则下拉框里没有管理员，保存会报「请选择关联用户」）。
        var userOpts = [{ value: '', label: '选择用户' }].concat(USERS.filter(function (item) {
          return item.role !== 'admin' || (mode === 'edit' && rel && item.id === rel.userId);
        }).map(function (item) { return { value: item.id, label: item.name }; }));
        var devOpts = [{ value: '', label: '选择设备' }].concat(DEVICES.map(function (item) { return { value: item.id, label: item.name + (item.online === true ? '' : (item.online === false ? '（离线）' : '（未检查）')) }; }));
        var cfgOpts = [{ value: '', label: '选择 Runtime 配置' }].concat(CONFIGS.filter(function (item) { return item.inRuntime; }).map(function (item) { return { value: item.id, label: item.name + (item.task && item.task !== '—' ? ' · ' + item.task : '') }; }));
        fillSelect($('alasLinkUser'), userOpts, u ? u.id : '');
        fillSelect($('alasLinkDevice'), devOpts, (rel && rel.device) || (cfg && cfg.device) || '');
        var cfgVal = '';
        if (rel && cfg && cfg.inRuntime) { cfgVal = cfg.id; }
        else if (cfg && cfg.inRuntime) { cfgVal = cfg.id; }
        fillSelect($('alasLinkConfig'), cfgOpts, cfgVal);
        $('alasLinkRun').checked = rel ? (rel.run && rel.edit) : true;
        $('alasLinkEdit').checked = $('alasLinkRun').checked;
        $('alasLinkDefault').checked = rel ? rel.def : !!(u && userRels(u.id).length === 0);
        $('alasLinkGrantView').checked = false;
        linkGrantContext = '';
        $('alasLinkUser').disabled = mode === 'edit' || !!presetUser;
        $('alasLinkConfig').disabled = mode === 'edit';
        $('alasLinkAdvanced').open = mode === 'edit';
        syncLinkForm();
        openDrawer('alasLinkDrawer');
        refreshIcons();
        setTimeout(function () {
          var target = !$('alasLinkUser').disabled && !$('alasLinkUser').value ? $('alasLinkUser') : !$('alasLinkDevice').value ? $('alasLinkDevice') : $('alasLinkConfig');
          target.focus();
        }, 60);
      }
       function saveLinkRel(done) {
         var userId = $('alasLinkUser').value;
         var device = $('alasLinkDevice').value;
         var cfgSel = $('alasLinkConfig').value;
         if (!userId) { toast('请选择关联用户', 'error'); return false; }
         if (!device) { toast('请选择关联设备', 'error'); return false; }
         if (!cfgSel) { toast('请选择 Runtime 配置', 'error'); return false; }
         var cfg = cfgById(cfgSel);
         if (!cfg) { toast('请选择有效的配置', 'error'); return false; }
         var cfgId = cfg.id;
         var ownershipConflict = linkOwnershipConflict(cfgId, userId);
         if (ownershipConflict) {
           var owner = userById(ownershipConflict.userId);
           toast('该配置已关联给用户 ' + (owner ? owner.name : ownershipConflict.userId) + '，请先解除原关联', 'error');
           return false;
         }
         var endpoint = linkMode === 'edit' ? 'alas.relations.update' : 'alas.relations.create';
         if (!guardService(endpoint)) { return false; }
         var fullAccess = $('alasLinkRun').checked;
         var body = { userId: userId, configId: cfgId, deviceId: device || null, canRun: fullAccess, canEdit: fullAccess, isDefault: $('alasLinkDefault').checked, grantView: $('alasLinkGrantView').checked };
         var options = { method: linkMode === 'edit' ? 'PATCH' : 'POST', body: body };
         if (linkMode === 'edit') { options.params = { id: linkRelId }; }
         function retryWithMove(loading, close) {
           var retryBody = {};
           Object.keys(body).forEach(function (key) { retryBody[key] = body[key]; });
           retryBody.confirmMove = true;
           var retryOptions = { method: options.method, body: retryBody };
           if (options.params) { retryOptions.params = options.params; }
           window.ScrcpyGateApi.configured(endpoint, retryOptions).then(function () {
             closeDrawer('alasLinkDrawer');
             return loadOverview(false);
           }).then(function () {
             loading(false); close();
             toast('配置关联已移动到新设备', 'success');
             if (done) { done(); }
           }).catch(function (error) {
             loading(false);
             toast(apiError(error), 'error');
             if (done) { done(error); }
           });
         }
         window.ScrcpyGateApi.configured(endpoint, options).then(function () { closeDrawer('alasLinkDrawer'); return loadOverview(false); }).then(function () { toast('配置关联已保存', 'success'); if (done) { done(); } }).catch(function (error) {
           var move = alasMoveConfirm(error);
           if (move) {
             confirmModal({
               title: '移动配置绑定',
               body: move.message || '该配置当前绑定到其他设备，保存会把它移动过来。',
               okText: '移动并保存',
               danger: true,
               onConfirm: function (close, loading) { loading(true); retryWithMove(loading, close); }
             });
             if (done) { done(error); }
             return;
           }
           toast(apiError(error), 'error');
           if (done) { done(error); }
         });
         return true;
       }
       function performConfigAction(action, successText) {
         if (!selectedCfgId || !guardService('alas.config.action')) { return; }
         var cfg = cfgById(selectedCfgId);
        confirmModal({ title: action === 'stop' ? '停止配置' : (action === 'restart' ? '重启异常配置' : '启动配置'), body: '确定对配置「' + (cfg ? cfg.name : '') + '」执行此操作？', danger: action !== 'start', okText: action === 'stop' ? '停止' : (action === 'restart' ? '重启' : '启动'), onConfirm: function (close, loading) {
           loading(true);
           window.ScrcpyGateApi.configured('alas.config.action', { method: 'POST', params: { id: selectedCfgId }, body: { action: action, configId: selectedCfgId, deviceId: cfg && (cfg.deviceId || cfg.device) || '' } }).then(function () { return loadOverview(false); }).then(function () { loading(false); close(); toast(successText, 'success'); }).catch(function (error) { loading(false); toast(apiError(error), 'error'); });
         } });
       }
       function runPrimaryCfg() {
         var cfg = cfgById(selectedCfgId);
         if (!cfg) { return; }
         performConfigAction(cfg.runtime === 'running' ? 'stop' : (cfg.runtime === 'error' ? 'restart' : 'start'), cfg.runtime === 'running' ? '配置已停止' : (cfg.runtime === 'error' ? '配置已重启' : '配置已启动'));
       }
       function deleteRelation(relId) {
         if (!guardService('alas.relations.delete')) { return; }
         window.ScrcpyGateApi.configured('alas.relations.delete', { method: 'DELETE', params: { id: relId }, body: { relationId: relId } }).then(function () { return loadOverview(false); }).then(function () { toast('关联已删除', 'success'); }).catch(function (error) { toast(apiError(error), 'error'); });
       }
       function checkConnection(done) {
         // 连接检查允许在服务未启用时执行(先探地址、再启用保存),只要求接口已配置。
         if (!window.ScrcpyGateApi.isConfigured('alas.connection.check')) { toast('接口尚未配置：alas.connection.check', 'error'); return; }
         var btn = $('alasCheckConnBtn'); setLoading(btn, true, '检查中…'); btn.disabled = true;
         window.ScrcpyGateApi.configured('alas.connection.check', { method: 'POST', body: { runtimeUrl: $('alasRuntimeUrl').value.trim() } }).then(function (payload) {
           var d = unwrapData(payload); connState = d.state || d.status || (d.connected ? 'connected' : 'unreachable'); lastCheckText = d.checkedAt || nowTime(); if (d.tokenConfigured !== undefined) { tokenConfigured = !!d.tokenConfigured; tokenKnown = true; } renderStats(); if (done) { done(connState); }
         }).catch(function (error) { connState = error.code === 'TIMEOUT' ? 'timeout' : 'unreachable'; lastCheckText = nowTime(); renderStats(); toast(apiError(error), 'error'); if (done) { done(connState); } }).finally(function () { setLoading(btn, false); renderStats(); });
       }
       function runSync(done) {
         if (!guardService('alas.overview')) { return; }
         var btn = $('alasSyncBtn'); setLoading(btn, true, '同步中…');
         loadOverview(false).then(function () { if (done) { done(syncState); } toast(CONFIGS.length ? '配置目录已同步' : 'Runtime 配置目录为空', CONFIGS.length ? 'success' : 'info'); }).catch(function () { if (done) { done('error'); } }).finally(function () { setLoading(btn, false); renderAll(); });
       }
       function refreshAll() {
         var btn = $('alasRefreshAllBtn'); if (!window.ScrcpyGateApi.isConfigured('alas.overview')) { toast('接口尚未配置：alas.overview', 'error'); return; }
         setLoading(btn, true, '刷新中…'); loadOverview(true).finally(function () { setLoading(btn, false); renderAll(); });
       }
      /* ===== 事件绑定 ===== */
      var i;
      document.querySelectorAll('.alas-tab').forEach(function (tab) {
        tab.addEventListener('click', function () { switchView(tab.getAttribute('data-view')); });
      });
      $('alasConnBtn').addEventListener('click', function () { openDrawer('alasConnDrawer'); });
      document.querySelectorAll('[data-close-drawer]').forEach(function (btn) {
        btn.addEventListener('click', function () { closeDrawer(btn.getAttribute('data-close-drawer')); });
      });
      document.querySelectorAll('.alas-drawer-mask').forEach(function (mask) {
        mask.addEventListener('click', function () {
          var drawerId = mask.id === 'alasConnMask' ? 'alasConnDrawer' : 'alasLinkDrawer';
          closeDrawer(drawerId);
        });
      });
      $('alasModalMask').addEventListener('click', closeModal);
      $('alasModalClose').addEventListener('click', closeModal);
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          if (!$('alasConfirmModal').hidden) { closeModal(); }
          closeDrawer('alasConnDrawer');
          closeDrawer('alasLinkDrawer');
        }
      });

      $('alasRefreshAllBtn').addEventListener('click', refreshAll);
       $('alasSyncBtn').addEventListener('click', function () {
         if (!guardService('alas.overview')) { return; }
         runSync();
       });

       $('alasServiceEnabled').addEventListener('change', function () {
         if (!window.ScrcpyGateApi.isConfigured('alas.settings.update')) { this.checked = serviceEnabled; toast('接口尚未配置：alas.settings.update', 'error'); return; }
         var input = this; input.disabled = true;
         window.ScrcpyGateApi.configured('alas.settings.update', { method: 'PATCH', body: { enabled: input.checked, runtimeUrl: $('alasRuntimeUrl').value.trim() } }).then(function () { return loadOverview(false); }).then(function () { toast(input.checked ? 'ALAS 服务已启用' : 'ALAS 服务已停用', 'success'); }).catch(function (error) { input.checked = serviceEnabled; toast(apiError(error), 'error'); }).finally(function () { input.disabled = false; renderStats(); });
       });
      $('alasCheckConnBtn').addEventListener('click', function () {
        checkConnection(function (st) {
          if (st === 'connected') { toast('连接检查成功', 'success'); }
          else if (st === 'token-missing' || st === 'token_missing') { toast('Runtime 可达，但尚未配置 API Token', 'error'); }
          else if (st === 'token_invalid') { toast('Runtime 可达，但 Token 无效或已过期', 'error'); }
          else if (st === 'token_error') { toast('Token 解密失败，请检查加密密钥配置', 'error'); }
          else if (st === 'invalid_url') { toast('Runtime 地址无效，请检查后重试', 'error'); }
          else if (st === 'http_error') { toast('Runtime 可达，但接口响应异常', 'error'); }
          else { toast(st === 'timeout' ? '连接超时' : '服务不可达', 'error'); }
        });
      });
      $('alasSaveConnBtn').addEventListener('click', function () {
        saveConnectionSettings(true);
      });
       function saveConnectionSettings(closeAfter) {
         var btn = $('alasSaveConnBtn');
         if (!window.ScrcpyGateApi.isConfigured('alas.settings.update')) { toast('接口尚未配置：alas.settings.update', 'error'); return; }
         var body = {
           enabled: $('alasServiceEnabled').checked,
           runtimeUrl: $('alasRuntimeUrl').value.trim(),
           workbenchAlasVisible: $('alasWorkbenchVisible').checked
         };
         var token = $('alasTokenInput').value.trim(); if (token) { body.token = token; }
         setLoading(btn, true, '保存中…');
         window.ScrcpyGateApi.configured('alas.settings.update', { method: 'PATCH', body: body }).then(function () { return loadOverview(false); }).then(function () {
           if (closeAfter) { closeDrawer('alasConnDrawer'); }
           $('alasTokenInput').value = '';
           toast(token ? '连接设置与 API Token 已保存' : '连接设置已保存', 'success');
         }).catch(function (error) { toast(apiError(error), 'error'); }).finally(function () { setLoading(btn, false); renderStats(); });
       }
      $('alasTokenClearBtn').addEventListener('click', function () {
        confirmModal({
          title: '清除 API Token',
          body: '确定清除已配置的 ALAS API Token？清除后将无法连接 ALAS Runtime。',
          danger: true,
          okText: '清除',
           onConfirm: function (close, loading) {
             if (!window.ScrcpyGateApi.isConfigured('alas.settings.update')) { toast('接口尚未配置：alas.settings.update', 'error'); return; }
             loading(true);
             window.ScrcpyGateApi.configured('alas.settings.update', { method: 'PATCH', body: { clearToken: true } }).then(function () { return loadOverview(false); }).then(function () { $('alasTokenInput').value = ''; loading(false); close(); toast('API Token 已清除', 'success'); }).catch(function (error) { loading(false); toast(apiError(error), 'error'); });
           }
         });
       });

      $('alasUserSearch').addEventListener('input', function () {
        userQuery = this.value;
        renderUsers();
      });
      $('alasUserFilter').addEventListener('change', function () {
        userFilter = this.value;
        renderUsers();
      });
      $('alasUserList').addEventListener('click', function (e) {
        var btn = e.target.closest('.alas-list-button');
        if (!btn) { return; }
        var uid = btn.getAttribute('data-uid');
        selectedUserId = uid;
        renderUsers();
        renderUserDetail();
        restoreListFocus('alasUserList', 'data-uid', uid);
      });
      $('alasRelList').addEventListener('click', function (e) {
        var btn = e.target.closest('[data-rel-edit-btn]');
        if (btn) { openLinkDrawer('edit', btn.getAttribute('data-rel-edit-btn')); return; }
        var del = e.target.closest('[data-rel-del-btn]');
        if (del) {
          var rid = del.getAttribute('data-rel-del-btn');
          var rel = RELS.filter(function (r) { return r.id === rid; })[0];
          if (!rel) { return; }
          var cfg = cfgById(rel.cfgId);
          var u = userById(rel.userId);
          confirmModal({
            title: '删除配置关联',
            body: '确定删除「' + (u ? u.name : '') + '」对配置「' + (cfg ? cfg.name : '') + '」的关联？',
            danger: true,
            okText: '删除',
            onConfirm: function (close, loading) {
              loading(true);
               deleteRelation(rid);
               close();
               loading(false);
            }
          });
        }
      });
      $('alasRelList').addEventListener('change', function (e) {
        var target = e.target;
        if (target.hasAttribute('data-rel-run')) {
          var rid1 = target.getAttribute('data-rel-run');
          for (var i = 0; i < RELS.length; i++) { if (RELS[i].id === rid1) { RELS[i].run = target.checked; } }
          toast('已更新运行权限，点击“保存用户 ALAS 权限”生效', 'info');
        }
        if (target.hasAttribute('data-rel-edit')) {
          var rid2 = target.getAttribute('data-rel-edit');
          for (var j = 0; j < RELS.length; j++) { if (RELS[j].id === rid2) { RELS[j].edit = target.checked; } }
          toast('已更新编辑权限，点击“保存用户 ALAS 权限”生效', 'info');
        }
      });
      $('alasAddRelBtn').addEventListener('click', function () {
        if (!selectedUserId) { toast('请先在左侧选择用户', 'error'); return; }
        var u = userById(selectedUserId);
        if (u && u.role === 'admin') { toast('管理员无需单独建立配置关联', 'info'); return; }
        if (!serviceEnabled) { toast('尚未配置 ALAS，请先在“连接设置”中填写 Runtime 地址和 Token 并启用服务', 'info'); return; }
        if (!CONFIGS.length) { toast('尚未发现 ALAS 配置，请先检查连接并刷新配置目录', 'info'); return; }
        openLinkDrawer('add', null, selectedUserId, null);
      });
      $('alasSavePermBtn').addEventListener('click', function () {
        if (!selectedUserId) { toast('请先选择用户', 'error'); return; }
        var btn = this;
         if (!window.ScrcpyGateApi.isConfigured('alas.relations.bulkUpdate')) { toast('接口尚未配置：alas.relations.bulkUpdate', 'error'); return; }
         setLoading(btn, true, '保存中…');
         window.ScrcpyGateApi.configured('alas.relations.bulkUpdate', { method: 'PATCH', body: { userId: selectedUserId, relations: userRels(selectedUserId).map(function (r) { return { id: r.id, canRun: !!r.run, canEdit: !!r.edit, isDefault: !!r.def }; }) } }).then(function () { return loadOverview(false); }).then(function () { toast('用户 ALAS 权限已保存', 'success'); }).catch(function (error) { toast(apiError(error), 'error'); }).finally(function () { setLoading(btn, false); });
      });

      $('alasCfgSearch').addEventListener('input', function () {
        cfgQuery = this.value;
        renderConfigs();
      });
      $('alasCfgList').addEventListener('click', function (e) {
        var btn = e.target.closest('.alas-list-button');
        if (!btn) { return; }
        var cid = btn.getAttribute('data-cid');
        selectedCfgId = cid;
        renderConfigs();
        renderCfgDetail();
        restoreListFocus('alasCfgList', 'data-cid', cid);
      });
      $('alasCfgPrimaryBtn').addEventListener('click', runPrimaryCfg);
      $('alasCfgAssignBtn').addEventListener('click', function () {
        if (!selectedCfgId) { toast('请先选择配置', 'error'); return; }
        openLinkDrawer('add', null, null, selectedCfgId);
      });
      $('alasCfgRefreshState').addEventListener('click', function () {
        if (!selectedCfgId) { return; }
         if (!guardService('alas.config.status')) { return; }
        var btn = this;
        setLoading(btn, true, '刷新中…');
         loadOverview(false).then(function () {
           var cfg = cfgById(selectedCfgId);
           toast(cfg && cfg.stopReason ? cfg.stopReason : '状态已刷新', cfg && cfg.stopReason ? 'error' : 'success');
         }).catch(function (error) { toast(apiError(error), 'error'); }).finally(function () { setLoading(btn, false); });
      });
      $('alasCfgOpenAlas').addEventListener('click', function (e) {
         if (!this.href || this.getAttribute('aria-disabled') === 'true' || this.getAttribute('href') === '#') { e.preventDefault(); toast('ALAS 页面地址尚未配置', 'error'); }
      });

      function filterConflictUsers() {
        if (currentView !== 'users') { switchView('users'); }
        userFilter = 'conflict';
        var sel = $('alasUserFilter');
        if (sel) { sel.value = 'conflict'; }
        renderUsers();
        toast('已筛选归属冲突相关的用户', 'info');
      }
      $('alasConflictBannerBtn').addEventListener('click', filterConflictUsers);
      $('alasStatusConflictWrap').addEventListener('click', filterConflictUsers);
      $('alasUserAlertBtn').addEventListener('click', filterConflictUsers);

      $('alasLinkUser').addEventListener('change', syncLinkForm);
      $('alasLinkDevice').addEventListener('change', syncLinkForm);
      $('alasLinkConfig').addEventListener('change', function () {
        var config = cfgById($('alasLinkConfig').value);
        if (config && config.device && !$('alasLinkDevice').value) $('alasLinkDevice').value = String(config.device);
        syncLinkForm();
      });
      ['alasLinkRun', 'alasLinkDefault'].forEach(function (id) {
        $(id).addEventListener('change', syncLinkForm);
      });
      $('alasLinkSaveBtn').addEventListener('click', function () {
        var btn = this;
         setLoading(btn, true, '保存中…');
         var ok = saveLinkRel(function () { setLoading(btn, false); });
         if (!ok) { setLoading(btn, false); }
      });

      /* ===== 隐藏范围（只读）已随写死移除全部交互：卡片默认展开、分组用原生
         <details> 折叠、条目只显示状态胶囊。 */

      /* ===== ALAS 心跳:周期同步配置状态,页面常驻时保持数据新鲜 ===== */
      var ALAS_HEARTBEAT_MS = 120000;
      var ALAS_VISIBLE_REFRESH_MIN_MS = 60000;
      var alasHeartbeatTimer = null;
      function startAlasHeartbeat() {
        stopAlasHeartbeat();
        alasHeartbeatTimer = window.setInterval(function () {
          if (document.visibilityState !== 'visible') { return; }
          if (dataState === 'loading') { return; }
          loadOverview(false, true).catch(function () {});
        }, ALAS_HEARTBEAT_MS);
      }
      function stopAlasHeartbeat() {
        if (alasHeartbeatTimer) { window.clearInterval(alasHeartbeatTimer); alasHeartbeatTimer = null; }
      }
      document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' && Date.now() - alasLastRefreshAt >= ALAS_VISIBLE_REFRESH_MIN_MS) {
          loadOverview(false, true).catch(function () {});
        }
      });

      /* ===== 初始化 ===== */
       renderStats();
       renderAll();
       loadOverview(false).catch(function () {});
       startAlasHeartbeat();
       if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
         window.ScrcpyGateI18n.on(function () { renderAll(); });
       }
     })();
