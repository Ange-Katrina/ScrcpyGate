(function () {
  'use strict';
  if (window.lucide) { lucide.createIcons(); }

    function escDash(value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) { return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]; });
    }
    function icon(name) { return '<i data-lucide="' + name + '" aria-hidden="true"></i>'; }
    function dashList(payload) { return window.ScrcpyGateApi.list(payload); }
    function dashNormalizeEntry(item) {
      var row = Object.assign({}, item || {});
      row.eventId = row.eventId || row.event_id || row.alertId || row.alert_id;
      row.action = row.action || row.action_code || row.event || row.event_name || row.action_label || row.actionLabel || '';
      row.actionLabel = row.actionLabel || row.action_label || row.action || '审计事件';
      row.operatorLabel = row.operatorLabel || row.username || row.user_name || row.operator || '未认证用户';
      row.actorName = row.actorName || row.username || row.user_name || row.operator || '';
      row.username = row.username || row.operatorLabel || '';
      row.deviceName = row.deviceName || row.device_name || '';
      row.target = row.target || row.target_id || row.targetId || '';
      row.targetId = row.targetId || row.target_id || row.target || '';
      row.targetType = row.targetType || row.target_type || '';
      row.createdAt = row.createdAt || row.created_at || (row.ts ? fmtDashTime(row.ts) : '—');
      row.result = row.result || row.outcome || row.status || '';
      row.outcome = row.outcome || row.result || '';
      row.resultLabel = row.resultLabel || row.result_label || row.outcomeLabel || row.outcome_label || row.outcome || '—';
      row.reason = row.reason || row.error_code || row.failure_reason || '';
      row.requestId = row.requestId || row.request_id || '';
      row.sourceIp = row.sourceIp || row.source_ip || '';
      row.summaryRaw = row.summary || row.detail || row.message || '';
      row.displaySummary = dashDashboard.readableText
        ? (dashDashboard.readableText(row.summaryRaw) || dashReasonLabel(row) || dashAuditTitle(row))
        : (row.summaryRaw || dashReasonLabel(row) || dashAuditTitle(row));
      row.displayDetail = dashDashboard.readableText
        ? (dashDashboard.readableText(row.detail || row.message || '') || row.displaySummary)
        : (row.detail || row.message || row.displaySummary);
      var severity = String(row.severity || row.level || '').trim().toLowerCase();
      var result = dashAuditResult(row);
      row.isAlert = dashIsAlert(row) ||
        severity === 'warn' || severity === 'warning' || severity === 'error' || severity === 'critical' ||
        ['fail', 'failure', 'error', 'denied', 'blocked', 'timeout'].indexOf(String(result || '').toLowerCase()) >= 0;
      // Normalize legacy rows once at the boundary. This keeps the activity
      // and alert renderers small and prevents machine labels from leaking
      // into the dashboard detail drawer.
      // Always pass the action through the shared mapper.  Older producers
      // append the outcome to the action label (for example
      // "开始投屏成功"); retaining that value makes the separate result chip
      // look duplicated and lets machine labels leak into the activity list.
      row.actionLabel = dashAuditTitle(row) || '审计事件';
      row.rawResult = result || row.result || 'unknown';
      row.result = dashDisplayResult(row) || row.rawResult;
      row.outcome = row.outcome || row.result;
      row.resultLabel = dashResultLabel(row);
      row.reasonLabel = dashReasonLabel(row);
      row.severityLabel = dashSeverityLabel(row);
      return row;
    }
    function dashAuditList(payload) {
      var data = payload && payload.data && typeof payload.data === 'object' ? payload.data : payload;
      if (!data) return [];
      if (Array.isArray(data)) return data.map(dashNormalizeEntry);
      if (Array.isArray(data.recent_activities)) {
        return data.recent_activities.map(dashNormalizeEntry);
      }
      if (Array.isArray(data.audits)) return data.audits.map(dashNormalizeEntry);
      if (Array.isArray(data.logs)) return data.logs.map(dashNormalizeEntry);
      if (Array.isArray(data.items)) return data.items.map(dashNormalizeEntry);
      return [];
    }
    // Dashboard labels live in a shared asset so dynamic activity rows can
    // use the same Chinese/English vocabulary as the audit page.
    var dashDashboard = window.ScrcpyGateDashboard || {};
    function dashAuditResult(a) { return dashDashboard.result ? dashDashboard.result(a) : String(a && (a.result || a.outcome || a.status || '')).trim().toLowerCase(); }
    function dashDisplayResult(a) { return dashDashboard.displayResult ? dashDashboard.displayResult(a) : dashAuditResult(a); }
    function dashResultLabel(a) { return dashDashboard.resultLabel ? dashDashboard.resultLabel(a) : '未知'; }
    function dashResultClass(a) { return dashDashboard.resultClass ? dashDashboard.resultClass(a) : 'unknown'; }
    function dashAuditSeverity(a) { return dashDashboard.severity ? dashDashboard.severity(a) : String(a && (a.level || a.severity || '')).trim().toLowerCase(); }
    function dashIsAlert(a) {
      if (dashDashboard.isAlertRecord) return dashDashboard.isAlertRecord(a);
      var severity = dashAuditSeverity(a), result = dashAuditResult(a);
      return !!(a && (a.alertType || a.alert_type || a.isAlert || a.kind === 'alert')) ||
        ['warn', 'warning', 'error', 'critical', 'high', 'fatal'].indexOf(severity) >= 0 ||
        ['fail', 'failure', 'error', 'denied', 'blocked', 'timeout'].indexOf(result) >= 0;
    }
    function dashReadableActor(a) {
      var value = dashDashboard.readableActor ? dashDashboard.readableActor(a) : (a && (a.operatorLabel || a.actorName || a.actor || a.operator || a.username));
      var key = String(value == null ? '' : value).trim().toLowerCase();
      if (!key || ['-', '—', 'none', 'null', 'anonymous', '未认证用户', '未登录用户'].indexOf(key) >= 0) return dashLocal('未认证用户', 'Unauthenticated user');
      if (key === '系统' || key === 'system') return dashLocal('系统', 'System');
      return String(value);
    }
    function dashReasonLabel(a) { return dashDashboard.reasonLabel ? dashDashboard.reasonLabel(a) : ''; }
    function dashTargetLabel(a) { return dashDashboard.targetLabel ? dashDashboard.targetLabel(a) : ''; }
    function dashAuditTitle(a) { return dashDashboard.title ? dashDashboard.title(a) : '审计事件'; }
    function dashAuditMeta(a) { return dashDashboard.meta ? dashDashboard.meta(a) : '无附加信息'; }
    function dashLocal(zh, en) {
      return dashDashboard.isEnglish && dashDashboard.isEnglish() ? (en || zh) : zh;
    }
    function dashStatusKey(value) {
      return dashDashboard.statusKey ? dashDashboard.statusKey(value) : String(value == null ? 'unknown' : value).trim().toLowerCase();
    }
    function dashStatusLabel(value, fallbackZh, fallbackEn) {
      return dashDashboard.statusLabel ? dashDashboard.statusLabel(value, fallbackZh, fallbackEn) : (value || dashLocal(fallbackZh || '未检查', fallbackEn || 'Not checked'));
    }
    function dashFormatCount(value) {
      return dashDashboard.formatCount ? dashDashboard.formatCount(value) : String(Math.max(0, Math.floor(Number(value) || 0)));
    }
    function dashTimeAttr(value) {
      var iso = dashDashboard.dateTime ? dashDashboard.dateTime(value) : '';
      return iso ? ' datetime="' + escDash(iso) + '"' : '';
    }
    function dashViewerLabel(value) {
      if (value === null || value === undefined || value === '') return '—';
      var number = Number(value);
      if (!isFinite(number) || number < 0) return '—';
      return dashFormatCount(number);
    }
    function dashControllerLabel(value, role, username) {
      var raw = value;
      if ((raw === null || raw === undefined || raw === '') && role === 'control') raw = username || '';
      var key = String(raw == null ? '' : raw).trim().toLowerCase();
      if (!key || ['-', '—', 'none', 'null', 'no controller', '无人控制'].indexOf(key) >= 0) return dashLocal('无人控制', 'No controller');
      if (key === '已分配控制权' || key === 'control assigned') return dashLocal('已分配控制权', 'Control assigned');
      return String(raw);
    }
    function dashSessionDuration(session) {
      session = session || {};
      var connectedAt = session.connectedAt || session.connected_at;
      if (connectedAt) return dashLocal('自 ', 'Since ') + fmtDashTime(connectedAt);
      var raw = String(session.duration == null ? '' : session.duration).trim();
      if (!raw || raw === '—') return '—';
      if (raw === '已连接' || raw.toLowerCase() === 'connected') return dashLocal('已连接', 'Connected');
      if (raw.indexOf('自 ') === 0) return dashLocal('自 ', 'Since ') + raw.slice(2);
      if (raw.indexOf('Since ') === 0) return dashLocal('自 ', 'Since ') + raw.slice(6);
      return raw;
    }
    function dashRefreshMessage(state, timestamp) {
      if (state === 'loading') return dashLocal('正在刷新…', 'Refreshing…');
      if (state === 'ready') return dashLocal('更新于 ', 'Updated ') + fmtDashTime(timestamp || Date.now());
      if (state === 'stale') return dashLocal('数据可能已过期', 'Data may be stale');
      if (state === 'error') return dashLocal('刷新失败，保留上次数据', 'Refresh failed; showing last data');
      return dashLocal('等待数据', 'Waiting for data');
    }
    var dashAlertItems = [];
    var dashAlertPendingCount = 0;
    var dashAttentionItems = [];
    var dashAlertView = 'attention';
    var dashActivityItems = [];
    var dashDetailCurrent = null;
    var dashDetailPreviousFocus = null;
    var dashDetailNative = false;
    var dashAnnounceTimer = null;
    var dashLastAlertCount = null;
    var dashAlertCountChanged = false;
    var dashDashboardRequest = null;
    var dashLastSnapshot = null;
    var dashDashboardHasData = false;
    var dashDashboardTimer = null;
    var dashDashboardRefreshPending = false;
    var DASHBOARD_REFRESH_MS = 30000;
    function dashAlertClass(a) {
      var severity = dashAuditSeverity(a), result = dashAuditResult(a);
      return severity === 'error' || severity === 'critical' || result === 'fail' || result === 'failure' || result === 'error' ? 'red' : 'orange';
    }
    function dashAlertIcon(tone) { return icon(tone === 'red' ? 'circle-alert' : 'triangle-alert'); }
    function dashAlertStateIcon(handled) { return icon(handled ? 'check' : 'clock-3'); }
    function dashResultIcon(resultClass) {
      return icon(({ success: 'check', fail: 'circle-x', denied: 'shield-x', pending: 'loader-circle', unknown: 'circle-help' })[resultClass] || 'circle-help');
    }
    function dashAlertPayload(payload) {
      var data = payload && payload.data && typeof payload.data === 'object' ? payload.data : (payload || {});
      var source = Array.isArray(data.alerts) ? data.alerts : (Array.isArray(data.items) ? data.items : []);
      var rawCount = data.pendingCount != null ? data.pendingCount : (data.pending_count != null ? data.pending_count : source.length);
      var pendingCount = Number(rawCount);
      if (!isFinite(pendingCount) || pendingCount < 0) pendingCount = source.length;
      return { items: source, pendingCount: Math.floor(pendingCount) };
    }
    function dashSeverityLabel(entry) {
      return dashDashboard.severityLabel ? dashDashboard.severityLabel(entry) : dashLocal('需关注', 'Needs attention');
    }
    function dashTimeLabel(value) {
      // Use the shared mapper so dashboard, mirror notifications and the log
      // page agree on past/future direction and locale formatting.
      if (dashDashboard.timeLabel) return dashDashboard.timeLabel(value);
      if (!value) return '—';
      var raw = String(value).trim();
      var numeric = /^[+-]?\d+(?:\.\d+)?$/.test(raw) ? Number(raw) : NaN;
      var millis = isFinite(numeric) ? (Math.abs(numeric) >= 1000000000000 ? numeric : numeric * 1000) : NaN;
      if (isFinite(millis)) {
        var age = Date.now() - millis;
        if (age >= 0 && age < 60000) return dashLocal('刚刚', 'Just now');
        if (age >= 60000 && age < 3600000) return Math.floor(age / 60000) + dashLocal(' 分钟前', ' min ago');
        if (age >= 3600000 && age < 86400000) return Math.floor(age / 3600000) + dashLocal(' 小时前', ' h ago');
        if (age >= 86400000 && age < 604800000) return Math.floor(age / 86400000) + dashLocal(' 天前', ' d ago');
      }
      return fmtDashTime(value);
    }
    function dashEntryTime(entry) { return entry.ts || entry.created_at || entry.createdAt || entry.time || ''; }
    function dashDetailValue(value) { return value === null || value === undefined || value === '' ? '—' : String(value); }
    function dashDetailIsOpen() {
      var mask = document.getElementById('dash-detail-mask');
      return !!(mask && (mask.classList.contains('open') || (dashDetailNative && mask.open)));
    }
    function dashDetailFocusable() {
      var mask = document.getElementById('dash-detail-mask');
      if (!mask) return [];
      return Array.prototype.slice.call(mask.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')).filter(function (element) {
        return !element.hidden && element.getAttribute('aria-hidden') !== 'true';
      });
    }
    function trapDashDetailFocus(event) {
      if (dashDetailNative || !dashDetailIsOpen() || event.key !== 'Tab') return;
      var focusable = dashDetailFocusable();
      if (!focusable.length) { event.preventDefault(); return; }
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    function finishDashDetailClose() {
      var mask = document.getElementById('dash-detail-mask');
      if (mask) {
        mask.classList.remove('open');
        mask.setAttribute('aria-hidden', 'true');
        if (!dashDetailNative) mask.setAttribute('inert', '');
      }
      dashDetailCurrent = null;
      var previous = dashDetailPreviousFocus;
      dashDetailPreviousFocus = null;
      if (previous && typeof previous.focus === 'function' && document.contains(previous)) previous.focus();
    }
    function openDashDetail(entry, alert) {
      dashDetailPreviousFocus = document.activeElement;
      dashDetailCurrent = { entry: entry, alert: !!alert };
      var grid = document.getElementById('dash-detail-grid');
      var title = document.getElementById('dash-detail-title');
      var resolve = document.getElementById('dash-detail-resolve');
      var logLink = document.getElementById('dash-detail-log');
      var eventId = entry.eventId || entry.event_id || entry.alertId || '';
      var logUrl = entry.logUrl || entry.log_url || (eventId ? '/logs?event_id=' + encodeURIComponent(eventId) : '/logs');
      if (title) title.textContent = alert ? dashLocal('告警详情', 'Alert details') : dashLocal('活动详情', 'Activity details');
      if (grid) {
        var fields = [
          [dashLocal('动作', 'Action'), dashAuditTitle(entry)], [dashLocal('操作者', 'Actor'), dashReadableActor(entry)],
          [dashLocal('设备', 'Device'), entry.deviceName || entry.device_name || entry.target || entry.targetId || '—'], [dashLocal('时间', 'Time'), entry.createdAt || entry.time || '—'],
          [dashLocal('结果', 'Result'), dashResultLabel(entry)], [dashLocal('严重度', 'Severity'), dashSeverityLabel(entry)],
          [dashLocal('事件 ID', 'Event ID'), eventId || '—'], [dashLocal('请求 ID', 'Request ID'), entry.requestId || entry.request_id || '—'],
          [dashLocal('来源 IP', 'Source IP'), entry.sourceIp || entry.source_ip || '—'], [dashLocal('处理状态', 'State'), entry.handled ? (dashLocal('已处理', 'Handled') + (entry.handledBy ? ' · ' + entry.handledBy : '')) : dashLocal('待处理', 'Pending')],
          [dashLocal('原因', 'Reason'), dashReasonLabel(entry) || '—'], [dashLocal('目标', 'Target'), dashTargetLabel(entry) || '—'], [dashLocal('原始原因', 'Raw reason'), entry.reason || '—'],
          [dashLocal('摘要', 'Summary'), entry.displaySummary || entry.summary || entry.shortSummary || '—'], [dashLocal('详情', 'Details'), entry.displayDetail || entry.detail || entry.message || '—'], [dashLocal('原始动作', 'Raw action'), entry.action || '—']
        ];
        grid.innerHTML = fields.map(function (field) { var full = field[0] === '详情' || field[0] === '原因'; return '<div class="dash-detail-field' + (full ? ' full' : '') + '"><small>' + escDash(field[0]) + '</small><b>' + escDash(dashDetailValue(field[1])) + '</b></div>'; }).join('');
      }
      if (logLink) {
        logLink.href = logUrl;
        logLink.hidden = !eventId;
      }
      if (resolve) { resolve.hidden = !alert || !!entry.handled; resolve.disabled = false; }
      var mask = document.getElementById('dash-detail-mask');
      if (mask) {
        mask.classList.add('open');
        mask.setAttribute('aria-hidden', 'false');
        mask.removeAttribute('inert');
        if (dashDetailNative && !mask.open) {
          try {
            mask.showModal();
          } catch (error) {
            // A legacy browser or a stale close request can leave the native
            // dialog unavailable. Keep the existing overlay as a fallback.
            dashDetailNative = false;
            mask.classList.remove('dash-detail-native');
            mask.classList.add('dash-detail-legacy');
          }
        }
      }
      if (window.lucide) lucide.createIcons();
      var closeButton = document.getElementById('dash-detail-close');
      if (closeButton && typeof closeButton.focus === 'function') {
        window.setTimeout(function () { closeButton.focus(); }, 0);
      }
    }
    function closeDashDetail() {
      var mask = document.getElementById('dash-detail-mask');
      if (dashDetailNative && mask && mask.open) {
        mask.close();
        return;
      }
      finishDashDetailClose();
    }
    function bindDashDetailList(id) {
      var list = document.getElementById(id);
      if (!list || list.getAttribute('data-detail-bound') === 'true') return;
      list.setAttribute('data-detail-bound', 'true');
      list.addEventListener('click', function (event) {
        var el = event.target.closest('[data-dash-detail]');
        if (!el || !list.contains(el)) return;
        var idx = Number(el.getAttribute('data-dash-detail'));
        var rows = el.getAttribute('data-dash-kind') === 'alert' ? dashAlertItems : dashActivityItems;
        if (rows[idx]) openDashDetail(rows[idx], el.getAttribute('data-dash-kind') === 'alert');
      });
    }
    function dashAttentionText(item) {
      var kind = String(item && item.kind || '');
      var target = String(item && item.target || '').trim();
      var days = Number(item && item.days);
      var hasDays = isFinite(days) && days > 0;
      if (kind === 'account_expired') {
        return { title: dashLocal('账户已过期', 'Account expired'), detail: (target || '—') + (hasDays ? dashLocal(' · 已过期 ', ' · expired ') + dashFormatCount(days) + dashLocal(' 天', ' d') : '') };
      }
      if (kind === 'account_expiring') {
        return { title: dashLocal('账户即将到期', 'Account expiring'), detail: (target || '—') + (hasDays ? dashLocal(' · 剩余 ', ' · ') + dashFormatCount(days) + dashLocal(' 天', ' d') : '') };
      }
      if (kind === 'device_offline') {
        return { title: dashLocal('设备离线', 'Device offline'), detail: target || '—' };
      }
      if (kind === 'alas_error') {
        return { title: dashLocal('ALAS 异常', 'ALAS issue'), detail: dashLocal('连接不可用或未配置 Token', 'Unreachable or token missing') };
      }
      return { title: dashLocal('需要处理', 'Needs attention'), detail: target || '—' };
    }
    function dashAttentionNote(count) {
      if (!count) {
        return dashAlertPendingCount
          ? dashLocal('业务项正常 · 服务异常 ', 'No business issues · ') + dashFormatCount(dashAlertPendingCount) + dashLocal(' 条待处理', ' pending')
          : dashLocal('当前没有需要处理的事项', 'Nothing needs attention');
      }
      var kinds = {};
      dashAttentionItems.forEach(function (item) { var kind = String(item && item.kind || ''); kinds[kind] = (kinds[kind] || 0) + 1; });
      var parts = [];
      if (kinds.account_expired) parts.push(dashFormatCount(kinds.account_expired) + dashLocal(' 个账号已过期', ' accounts expired'));
      if (kinds.account_expiring) parts.push(dashFormatCount(kinds.account_expiring) + dashLocal(' 个账号即将到期', ' accounts expiring soon'));
      if (kinds.device_offline) parts.push(dashFormatCount(kinds.device_offline) + dashLocal(' 台设备离线', ' devices offline'));
      if (kinds.alas_error) parts.push(dashFormatCount(kinds.alas_error) + dashLocal(' 个 ALAS 异常', ' ALAS issues'));
      return parts.length ? parts.join(' · ') : dashLocal('按状态实时统计', 'Live state');
    }
    function renderDashAttention(items) {
      dashAttentionItems = (Array.isArray(items) ? items : []).filter(function (item) { return item && item.kind; });
      var box = document.getElementById('dash-attention-list');
      var count = dashAttentionItems.length;
      setDash('dash-attention-group-count', dashFormatCount(count));
      setDash('dash-alert-badge', dashFormatCount(count));
      setDash('dash-attention-value', dashFormatCount(count));
      setDash('dash-attention-unit', dashLocal('项待处理', 'pending'));
      setDash('dash-attention-note', dashAttentionNote(count));
      if (box) {
        box.innerHTML = count ? dashAttentionItems.slice(0, 5).map(function (item) {
          var text = dashAttentionText(item);
          var tone = String(item.severity || '') === 'error' ? 'red' : 'orange';
          var label = text.title + '，' + text.detail;
          return '<li><a class="attention-item" href="' + escDash(item.href || '/') + '" aria-label="' + escDash(label) + '" title="' + escDash(label) + '"><span class="alert-dot ' + tone + '" aria-hidden="true"></span><span class="alert-copy"><span class="alert-title">' + escDash(text.title) + '</span><span class="alert-meta">' + escDash(text.detail) + '</span></span><span class="attention-go" aria-hidden="true">' + icon('chevron-right') + '</span></a></li>';
        }).join('') : '<li class="table-empty">' + icon('shield-check') + '<span>' + dashLocal('当前没有需要处理的业务项', 'Nothing needs attention') + '</span></li>';
      }
      if (window.lucide) lucide.createIcons();
    }
    // 待处理事项：业务待处理 / 服务异常两组用分段控件切换，一次只显示一组。
    // 未手动切换前跟随数据（有业务项先看业务组），手动选过之后保持用户的选择。
    var dashAlertViewPinned = false;
    // 视图 id 与面板 id 不是同一套命名：服务组沿用既有的 dash-alert-* 前缀。
    var DASH_ALERT_VIEW_PANELS = { attention: 'dash-attention-panel', service: 'dash-alert-panel' };
    function dashAlertViewNodes(view) {
      return {
        tab: document.getElementById('dash-tab-' + view),
        panel: document.getElementById(DASH_ALERT_VIEW_PANELS[view] || '')
      };
    }
    function setDashAlertView(view) {
      ['attention', 'service'].forEach(function (name) {
        var nodes = dashAlertViewNodes(name);
        var active = name === view;
        if (nodes.tab) {
          nodes.tab.classList.toggle('active', active);
          nodes.tab.setAttribute('aria-selected', active ? 'true' : 'false');
          nodes.tab.setAttribute('tabindex', active ? '0' : '-1');
        }
        if (nodes.panel) nodes.panel.hidden = !active;
      });
      dashAlertView = view;
    }
    function syncDashAlertView() {
      if (dashAlertViewPinned) return;
      // 业务项随状态恢复会自动消失，所以默认视图每次渲染都跟着数据走。
      var next = dashAttentionItems.length ? 'attention' : 'service';
      if (next !== dashAlertView) setDashAlertView(next);
    }
    function renderDashAlerts(payload) {
      var normalized = dashAlertPayload(payload);
      var alerts = normalized.items.map(dashNormalizeEntry);
      dashAlertItems = alerts;
      var alertBox = document.getElementById('dash-alert-list');
      var count = normalized.pendingCount;
      dashAlertPendingCount = count;
      dashAlertCountChanged = dashDashboardHasData && dashLastAlertCount !== null && dashLastAlertCount !== count;
      if (dashAlertCountChanged) {
        dashAnnounce(count ? (count + dashLocal(' 条服务异常待处理', ' service issues pending')) : dashLocal('服务异常已清空', 'No service issues'));
      }
      dashLastAlertCount = count;
      // The badge and the 待处理事项 card follow the business items; the service
      // group reports its own durable pending count.
      setDash('dash-service-count', dashFormatCount(count));
      setDash('dash-alert-count', dashLocal('服务异常 ', 'Service issues ') + dashFormatCount(count) + dashLocal(' 条待处理', ' pending'));
      setDash('dash-attention-note', dashAttentionNote(dashAttentionItems.length));
      if (alertBox) {
        alertBox.innerHTML = dashAlertItems.length ? dashAlertItems.slice(0, 4).map(function (a, index) {
          var stamp = dashEntryTime(a);
          var tone = dashAlertClass(a);
          var state = a.handled ? dashLocal('已处理', 'Handled') : dashLocal('待处理', 'Pending');
          var stateClass = a.handled ? 'handled' : 'pending';
          var fullLabel = dashAuditTitle(a) + '，' + dashSeverityLabel(a) + '，' + dashAuditMeta(a) + '，' + state;
          return '<li><button class="alert-item" type="button" data-dash-detail="' + index + '" data-dash-kind="alert" aria-label="' + escDash(fullLabel) + '" title="' + escDash(fullLabel) + '"><span class="alert-dot ' + tone + '" aria-hidden="true"></span><span class="alert-copy"><span class="alert-title-line"><span class="alert-title">' + escDash(dashAuditTitle(a)) + '</span><span class="alert-flags"><span class="alert-severity ' + tone + '">' + dashAlertIcon(tone) + escDash(dashSeverityLabel(a)) + '</span><span class="alert-state ' + stateClass + '">' + dashAlertStateIcon(!!a.handled) + state + '</span></span></span><span class="alert-meta">' + escDash(dashAuditMeta(a)) + '</span></span><time class="alert-time"' + dashTimeAttr(stamp) + ' title="' + escDash(a.createdAt || a.time || stamp || '—') + '">' + escDash(dashTimeLabel(stamp)) + '</time></button></li>';
        }).join('') : '<li class="table-empty">' + icon('shield-check') + '<span>' + dashLocal('暂无待处理告警', 'No pending alerts') + '</span></li>';
      }
    }
    function renderDashActivities(payload) {
      dashActivityItems = dashAuditList(payload);
      var activityBox = document.getElementById('dash-activity-list');
      if (activityBox) {
        activityBox.innerHTML = dashActivityItems.length ? dashActivityItems.slice(0, 6).map(function (a, index) {
          var stamp = dashEntryTime(a);
          var resultClass = dashResultClass(a);
          var fullLabel = dashAuditTitle(a) + '，' + dashResultLabel(a) + '，' + dashAuditMeta(a);
          return '<li><button class="timeline-item" type="button" data-dash-detail="' + index + '" data-dash-kind="activity" aria-label="' + escDash(fullLabel) + '" title="' + escDash(fullLabel) + '"><span class="timeline-dot ' + dashDot(a) + '" aria-hidden="true"></span><span class="timeline-copy"><span class="timeline-title-line"><strong>' + escDash(dashAuditTitle(a)) + '</strong><span class="timeline-result ' + resultClass + '">' + dashResultIcon(resultClass) + escDash(dashResultLabel(a)) + '</span></span><span class="timeline-meta">' + escDash(dashAuditMeta(a)) + '</span></span><time class="timeline-time"' + dashTimeAttr(stamp) + ' title="' + escDash(a.createdAt || a.time || stamp || '—') + '">' + escDash(dashTimeLabel(stamp)) + '</time></button></li>';
        }).join('') : '<li class="table-empty">' + icon('history') + '<span>' + dashLocal('暂无最近活动', 'No recent activity') + '</span></li>';
      }
    }
    function renderDashAuditFailure(error) {
      var message = window.ScrcpyGateApi && window.ScrcpyGateApi.errorMessage ? window.ScrcpyGateApi.errorMessage(error) : '审计服务暂不可用';
      var alertBox = document.getElementById('dash-alert-list');
      var attentionBox = document.getElementById('dash-attention-list');
      var activityBox = document.getElementById('dash-activity-list');
      if (alertBox) alertBox.innerHTML = '<li class="table-empty">' + icon('triangle-alert') + '<span>' + escDash(message) + '</span></li>';
      if (attentionBox) attentionBox.innerHTML = '<li class="table-empty">' + icon('triangle-alert') + '<span>' + dashLocal('概览数据加载失败', 'Overview failed to load') + '</span></li>';
      if (activityBox) activityBox.innerHTML = '<li class="table-empty">' + icon('triangle-alert') + '<span>' + dashLocal('最近活动加载失败', 'Recent activity failed to load') + '</span></li>';
      setDash('dash-alert-badge', '—'); setDash('dash-alert-count', dashLocal('加载失败', 'Load failed')); setDash('dash-service-count', '—'); setDash('dash-attention-group-count', '—'); setDash('dash-attention-value', '—'); setDash('dash-attention-unit', dashLocal('暂不可用', 'Unavailable')); setDash('dash-attention-note', dashLocal('请检查概览与审计服务后重试', 'Check the overview and audit services and retry'));
      // 失败信息渲染在服务组里：没手动切换过时把视图挪过去，否则用户看不到错误。
      if (!dashAlertViewPinned) setDashAlertView('service');
    }
    function dashDot(a) {
      return dashDashboard.dotClass ? dashDashboard.dotClass(a) : 'blue';
    }
    function setDash(id, value) { var el = document.getElementById(id); if (el) el.textContent = value; }
    function dashAnnounce(message) {
      var announcer = document.getElementById('dash-live-announcer');
      if (!announcer || !message) return;
      window.clearTimeout(dashAnnounceTimer);
      announcer.textContent = '';
      dashAnnounceTimer = window.setTimeout(function () { announcer.textContent = String(message); }, 60);
    }
    function setDashboardBusy(busy) {
      ['dash-alert-list', 'dash-activity-list', 'dash-devices-tbody', 'dash-sessions-tbody'].forEach(function (id) {
        var element = document.getElementById(id);
        if (element) element.setAttribute('aria-busy', busy ? 'true' : 'false');
      });
    }
    function setDashRefreshState(state, message) {
      var button = document.getElementById('dash-refresh-button');
      var status = document.getElementById('dash-refresh-status');
      var loading = state === 'loading';
      if (button) {
        button.disabled = loading;
        button.classList.toggle('is-loading', loading);
        button.setAttribute('aria-busy', loading ? 'true' : 'false');
      }
      if (status && message !== undefined) status.textContent = message;
    }
    function renderDashLoggingHealth(payload) {
      var health = payload && (payload.logging_health || payload.loggingHealth) || {};
      var target = document.getElementById('dash-logging-health');
      if (!target) return;
      var configured = health.configured !== false && (health.listener_alive !== undefined || health.queue_capacity);
      if (!configured) {
        target.textContent = dashLocal('日志管道未启用', 'Log pipeline disabled');
        target.setAttribute('data-state', 'warning');
        return;
      }
      var depth = Number(health.queue_depth || 0);
      var capacity = Number(health.queue_capacity || 0);
      var dropped = Number(health.dropped_records || 0);
      if (!isFinite(depth) || depth < 0) depth = 0;
      if (!isFinite(capacity) || capacity < 0) capacity = 0;
      if (!isFinite(dropped) || dropped < 0) dropped = 0;
      depth = Math.floor(depth); capacity = Math.floor(capacity); dropped = Math.floor(dropped);
      var listenerAlive = health.listener_alive !== false;
      if (!listenerAlive) {
        target.textContent = dashLocal('日志监听器已停止', 'Log listener stopped');
        target.setAttribute('data-state', 'danger');
        return;
      }
      var utilization = capacity > 0 ? depth / capacity : 0;
      var state = dropped > 0 || utilization >= 0.8 ? 'warning' : 'ok';
      target.setAttribute('data-state', state);
      target.textContent = dashLocal('日志队列 ', 'Log queue ') + dashFormatCount(depth) + '/' + (capacity ? dashFormatCount(capacity) : '—') + dashLocal(' · 丢弃 ', ' · Dropped ') + dashFormatCount(dropped);
    }
    function dashDeviceStatus(d) {
      d = d || {};
      if (typeof d.online === 'boolean') return d.online ? 'online' : 'offline';
      var raw = String(d.status || d.state || d.health || '').trim().toLowerCase();
      if (['online', 'healthy', 'connected', 'running', 'ready', 'ok'].indexOf(raw) >= 0) return 'online';
      if (['offline', 'disconnected', 'unreachable', 'error', 'failed', 'disabled'].indexOf(raw) >= 0) return 'offline';
      return 'unknown';
    }
    function dashAlasState(d) {
      d = d || {};
      if (d.alasStatusCode || d.alas_status_code) return dashStatusKey(d.alasStatusCode || d.alas_status_code);
      if (d.alasStatus) return dashStatusKey(d.alasStatus);
      if (!d.alas) return 'unconfigured';
      if (typeof d.alas === 'string') return dashStatusKey(d.alas);
      if (d.alas.status || d.alas.state || d.alas.status_code || d.alas.statusCode) {
        return dashStatusKey(d.alas.status_code || d.alas.statusCode || d.alas.status || d.alas.state);
      }
      if (typeof d.alas.online === 'boolean') return d.alas.online ? 'running' : 'unreachable';
      return 'unknown';
    }
    function dashAlasValue(d) {
      return dashStatusLabel(dashAlasState(d), '未检查', 'Not checked');
    }
    function dashDeviceRow(d) {
      var deviceState = dashDeviceStatus(d);
      var online = deviceState === 'online';
      var streaming = d.streaming === true || d.mirroring === true;
      var name = d.name || d.displayName || d.id || '—';
      // 名称下的副行显示 ADB 地址与延迟：仪表盘的设备载荷里没有型号字段（实测
      // 只有 id/name/enabled/can_*/adb_*/latency_ms/address/last_error 等），
      // 原来固定渲染 model/product 只会得到一排「—」。
      var address = String(d.address || d.adb_address || '').trim();
      var rawLatency = d.latency == null ? d.latency_ms : d.latency;
      var latency = (rawLatency == null || rawLatency === '') ? NaN : Math.round(Number(rawLatency));
      var metaParts = [];
      if (address) metaParts.push(address);
      if (isFinite(latency)) metaParts.push(latency + ' ms');
      var meta = metaParts.length ? metaParts.join(' · ') : (d.model || d.product || '—');
      var tags = '';
      if (d.enabled === false) tags += '<span class="device-tag off">' + dashLocal('已停用', 'Disabled') + '</span>';
      if (d.viewOnly === true || d.can_control === false) tags += '<span class="device-tag view">' + dashLocal('仅观看', 'View only') + '</span>';
      var viewers = dashViewerLabel(d.viewers == null ? d.viewerCount : d.viewers);
      var controller = dashControllerLabel(d.controller || d.controllerName, d.role, d.username || d.user);
      var alas = dashAlasValue(d);
      var alasTone = dashAlasToneForDevice(d);
      var alasCls = alasTone === 'ok' ? 'alas-ok' : (alasTone === 'error' ? 'alas-err' : (alasTone === 'unbound' ? 'alas-unbound' : 'alas-none'));
      var status = streaming ? '<span class="status-chip mirroring"><span class="mini-dot" aria-hidden="true"></span>' + dashLocal('投屏中', 'Casting') + '</span>' : (online ? '<span class="status-chip idle"><span class="mini-dot" aria-hidden="true"></span>' + dashLocal('空闲', 'Idle') + '</span>' : (deviceState === 'offline' ? '<span class="status-chip offline"><span class="mini-dot" aria-hidden="true"></span>' + dashLocal('离线', 'Offline') + '</span>' : '<span class="status-chip unknown"><span class="mini-dot" aria-hidden="true"></span>' + dashLocal('未检查', 'Not checked') + '</span>'));
      /* backport: frontend repo — the offline tab reads data-offline */
      // 非在线设备把 ADB 给出的原因放进状态列，否则离线只剩一个红点、看不出为什么。
      var reason = '';
      if (!online) {
        var rawDetail = deviceState === 'offline'
          ? (d.lastError || d.last_error || d.adbDetail || d.adb_detail)
          : (d.adbDetail || d.adb_detail || d.lastError || d.last_error);
        var detail = String(rawDetail || '').trim();
        if (detail) reason = '<div class="device-note" title="' + escDash(detail) + '">' + escDash(detail) + '</div>';
      }
      var deviceLink = '/devices?device=' + encodeURIComponent(String(d.public_id || d.publicId || d.id || ''));
      return '<tr class="device-row" data-device-id="' + escDash(String(d.id || '')) + '" data-online="' + (online ? '1' : (deviceState === 'offline' ? '0' : 'unknown')) + '" data-offline="' + (deviceState === 'offline' ? '1' : '0') + '" data-unknown="' + (deviceState === 'unknown' ? '1' : '0') + '" data-mirroring="' + (streaming ? '1' : '0') + '" data-alas-err="' + (alasTone === 'error' ? '1' : '0') + '">' +
        '<td><div class="device-cell"><span class="device-glyph">' + icon('smartphone') + '</span><div style="min-width:0"><div class="device-name-row"><div class="device-name">' + escDash(name) + '</div>' + tags + '</div><div class="device-model" title="' + escDash(meta) + '">' + escDash(meta) + '</div></div></div></td>' +
        '<td><div class="device-status">' + status + reason + '</div></td><td class="tabular-nums">' + escDash(viewers) + '</td><td>' + escDash(controller) + '</td>' +
        '<td><span class="status-chip ' + alasCls + '"><span class="mini-dot" aria-hidden="true"></span>' + escDash(alas) + '</span></td>' +
        '<td class="tabular-nums">' + escDash(d.heartbeat || d.lastSeen || '—') + '</td><td><div class="row-actions"><a class="row-btn open" href="' + escDash(deviceLink) + '" aria-label="' + escDash(dashLocal('打开该设备的详情页', 'Open this device in device management')) + '"><i data-lucide="monitor-up" aria-hidden="true"></i><span class="btn-text">' + dashLocal('打开', 'Open') + '</span></a>' + dashDeviceActionsHtml(d) + '</div></td></tr>';
    }
    function dashAlasTone(value) {
      var state = dashStatusKey(value);
      if (state === 'running' || state === 'connected') return 'ok';
      if (state === 'error' || state === 'partial_error' || state === 'unreachable' || state === 'timeout' || state === 'token_missing' || state === 'token_invalid' || state === 'http_error') return 'error';
      return 'none';
    }
    function dashAlasToneForDevice(device) {
      device = device || {};
      var state = dashAlasState(device);
      var stateTone = dashAlasTone(state);
      if (stateTone !== 'none') return stateTone;
      // 未关联（没有绑定任何 ALAS 配置）按界面要求标红；未配置/未检查等中性态仍是灰色。
      // 注意这里只影响配色，不改变 data-alas-err 与「ALAS 异常」筛选的口径。
      if (state === 'unbound') return 'unbound';
      var supplied = String(device.alasStatusTone || device.alas_status_tone || '').trim().toLowerCase();
      return supplied === 'ok' || supplied === 'error' ? supplied : 'none';
    }
    /* ===== 设备行快捷操作：强行关闭投屏 / 停用·启用 / 启停 ALAS ===== */
    // 三个动作都命中已有的管理员接口（mirror/stop、devices update、alas toggle），
    // 这里只做「什么时候能点 + 点之前确认 + 点之后就地刷新」。
    var dashRowActionBusy = null;
    var dashDialogPreviousFocus = null;

    function dashDeviceActionsHtml(d) {
      d = d || {};
      var configs = dashAlasConfigsFor(d);
      var streaming = d.streaming === true || d.mirroring === true;
      var off = d.enabled === false;
      var html = '';
      html += '<button class="menu-item" type="button" role="menuitem" data-dash-action="stop-cast"'
        + (streaming ? '' : ' disabled title="' + escDash(dashLocal('当前未投屏', 'Not casting right now')) + '"') + '>'
        + icon('square') + '<span>' + dashLocal('强行关闭投屏', 'Force-stop casting') + '</span></button>';
      html += '<button class="menu-item' + (off ? '' : ' is-danger') + '" type="button" role="menuitem" data-dash-action="toggle-enabled">'
        + icon(off ? 'circle-check' : 'ban') + '<span>'
        + (off ? dashLocal('启用设备', 'Enable device') : dashLocal('停用设备', 'Disable device')) + '</span></button>';
      if (!configs.length) {
        html += '<button class="menu-item" type="button" role="menuitem" data-dash-action="start-alas" disabled title="'
          + escDash(dashLocal('该设备未绑定 ALAS 配置', 'No ALAS config is bound to this device')) + '">'
          + icon('bot') + '<span>' + dashLocal('启动 ALAS', 'Start ALAS') + '</span></button>';
        html += '<a class="menu-item" role="menuitem" href="/alas">' + icon('settings')
          + '<span>' + dashLocal('去 ALAS 管理关联配置', 'Bind a config in ALAS') + '</span></a>';
      } else if (configs.length === 1) {
        var single = dashAlasActionForState(d.alasStatusCode);
        html += '<button class="menu-item" type="button" role="menuitem" data-dash-action="toggle-alas">'
          + icon('bot') + '<span>' + (single === 'stop' ? dashLocal('停止 ALAS', 'Stop ALAS') : dashLocal('启动 ALAS', 'Start ALAS'))
          + '</span></button>';
      } else {
        html += '<button class="menu-item" type="button" role="menuitem" data-dash-action="pick-alas">'
          + icon('bot') + '<span>' + dashLocal('启停 ALAS 配置…', 'Start / stop an ALAS config…') + '</span></button>';
      }
      return '<div class="row-menu">'
        + '<button class="row-btn icon menu-open-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="'
        + escDash(dashLocal('更多操作：', 'More actions: ') + (d.name || d.id || '')) + '">' + icon('ellipsis-vertical') + '</button>'
        + '<div class="menu-pop" role="menu" aria-label="' + escDash(dashLocal('设备操作', 'Device actions')) + '">' + html + '</div>'
        + '</div>';
    }
    function dashAlasConfigsFor(device) {
      var list = device && device.alasConfigs;
      if (Array.isArray(list)) {
        var clean = list.map(function (name) { return String(name || '').trim(); }).filter(Boolean);
        if (clean.length) return clean;
      }
      var single = String((device && (device.alasConfig || device.alasCfg)) || '').trim();
      return single ? [single] : [];
    }
    function dashAlasActionForState(state) {
      var key = dashStatusKey(state);
      return key === 'running' || key === 'connected' ? 'stop' : 'start';
    }
    function dashAlasStateForConfig(name) {
      var alas = (dashLastSnapshot && dashLastSnapshot.alas) || {};
      var items = Array.isArray(alas.config_statuses) ? alas.config_statuses : [];
      for (var i = 0; i < items.length; i += 1) {
        var item = items[i] || {};
        var config = String(item.config || item.config_name || item.configName || '').trim();
        if (config && config === String(name)) {
          return dashStatusKey(item.status || item.state || item.status_label || (item.online ? 'running' : 'unknown'));
        }
      }
      return 'unknown';
    }
    function dashDeviceById(id) {
      var list = (dashLastSnapshot && dashLastSnapshot.devices) || [];
      for (var i = 0; i < list.length; i += 1) {
        if (String(list[i].id) === String(id)) return list[i];
      }
      return null;
    }
    function dashAlasChip(state) {
      var tone = dashAlasTone(state);
      var cls = tone === 'ok' ? 'alas-ok' : (tone === 'error' ? 'alas-err' : 'alas-none');
      return '<span class="status-chip ' + cls + '"><span class="mini-dot" aria-hidden="true"></span>'
        + escDash(dashStatusLabel(state, '未检查', 'Not checked')) + '</span>';
    }

    function dashDialogNative(dialog) {
      return !!(dialog && typeof dialog.showModal === 'function' && typeof dialog.close === 'function');
    }
    function dashOpenDialog(dialog, focusId) {
      if (!dialog) return;
      dashDialogPreviousFocus = document.activeElement;
      var native = dashDialogNative(dialog);
      dialog.classList.add('open');
      dialog.classList.add(native ? 'dash-detail-native' : 'dash-detail-legacy');
      dialog.setAttribute('aria-hidden', 'false');
      dialog.removeAttribute('inert');
      if (native && !dialog.open) {
        try { dialog.showModal(); } catch (e) { dialog.classList.remove('dash-detail-native'); dialog.classList.add('dash-detail-legacy'); }
      }
      if (window.lucide) lucide.createIcons();
      var target = focusId ? document.getElementById(focusId) : null;
      if (target && target.focus) window.setTimeout(function () { target.focus(); }, 0);
    }
    function dashFinishCloseDialog(dialog) {
      if (!dialog) return;
      dialog.classList.remove('open');
      dialog.setAttribute('aria-hidden', 'true');
      dialog.setAttribute('inert', '');
      var previous = dashDialogPreviousFocus;
      dashDialogPreviousFocus = null;
      if (previous && typeof previous.focus === 'function' && document.contains(previous)) previous.focus();
    }
    function dashCloseDialog(dialog) {
      if (!dialog) return;
      if (dashDialogNative(dialog) && dialog.open) { dialog.close(); return; }
      dashFinishCloseDialog(dialog);
    }
    function dashBindDialog(id) {
      var dialog = document.getElementById(id);
      if (!dialog) return null;
      dialog.addEventListener('click', function (event) { if (event.target === dialog) dashCloseDialog(dialog); });
      dialog.addEventListener('cancel', function (event) { event.preventDefault(); dashCloseDialog(dialog); });
      dialog.addEventListener('close', function () { dashFinishCloseDialog(dialog); });
      return dialog;
    }

    // 确认弹窗：返回 Promise<boolean>，取消/关闭/点背景都算 false。
    function dashConfirm(options) {
      options = options || {};
      var dialog = document.getElementById('dash-confirm-mask');
      var resolve = function () {};
      var promise = new Promise(function (done) { resolve = done; });
      if (!dialog) { resolve(window.confirm(String(options.body || '').replace(/<[^>]+>/g, ''))); return promise; }
      var title = document.getElementById('dash-confirm-title');
      var text = document.getElementById('dash-confirm-text');
      var ok = document.getElementById('dash-confirm-ok');
      if (title) title.textContent = options.title || dashLocal('确认操作', 'Confirm');
      if (text) text.innerHTML = options.body || '';
      if (ok) {
        ok.textContent = options.confirmText || dashLocal('确定', 'Confirm');
        ok.classList.toggle('is-danger', options.danger === true);
      }
      var settled = false;
      var finish = function (value) {
        if (settled) return;
        settled = true;
        dialog.removeEventListener('close', onClose);
        if (ok) ok.removeEventListener('click', onOk);
        var cancel = document.getElementById('dash-confirm-cancel');
        if (cancel) cancel.removeEventListener('click', onCancel);
        dashCloseDialog(dialog);
        resolve(value);
      };
      var onOk = function () { finish(true); };
      var onCancel = function () { finish(false); };
      var onClose = function () { finish(false); };
      if (ok) ok.addEventListener('click', onOk);
      var cancelBtn = document.getElementById('dash-confirm-cancel');
      if (cancelBtn) cancelBtn.addEventListener('click', onCancel);
      dialog.addEventListener('close', onClose);
      dashOpenDialog(dialog, 'dash-confirm-ok');
      return promise;
    }

    function dashRunRowAction(key, request, doneText) {
      if (dashRowActionBusy) return Promise.resolve(false);
      dashRowActionBusy = key;
      document.body.classList.add('dash-row-busy');
      return Promise.resolve()
        .then(request)
        .then(function () {
          if (window.ScrcpyGateUi && window.ScrcpyGateUi.toast) window.ScrcpyGateUi.toast(doneText, 'success');
          return loadDashboard({ force: true, runtime: true, manual: true });
        })
        .catch(function (error) {
          var message = window.ScrcpyGateApi && window.ScrcpyGateApi.errorMessage
            ? window.ScrcpyGateApi.errorMessage(error) : String((error && error.message) || error);
          if (window.ScrcpyGateUi && window.ScrcpyGateUi.toast) window.ScrcpyGateUi.toast(message, 'error');
          return false;
        })
        .finally(function () {
          dashRowActionBusy = null;
          document.body.classList.remove('dash-row-busy');
        });
    }

    function dashSetDeviceEnabled(device, enabled) {
      var label = enabled ? dashLocal('设备已启用', 'Device enabled') : dashLocal('设备已停用', 'Device disabled');
      return dashRunRowAction(enabled ? 'enable' : 'disable', function () {
        return window.ScrcpyGateApi.configured('devices.update', {
          params: { id: device.id },
          body: { enabled: enabled },
        });
      }, label);
    }

    function dashToggleAlasConfig(device, configName, action) {
      var label = action === 'stop'
        ? dashLocal('已停止 ALAS 配置：', 'ALAS config stopped: ') + configName
        : dashLocal('已启动 ALAS 配置：', 'ALAS config started: ') + configName;
      return dashRunRowAction('alas', function () {
        return window.ScrcpyGateApi.configured('alas.config.action', {
          body: { config_name: configName, device_id: device.id, action: action },
        });
      }, label);
    }

    function dashOpenAlasPicker(device) {
      var dialog = document.getElementById('dash-alas-mask');
      var configs = dashAlasConfigsFor(device);
      if (!dialog || !configs.length) return;
      var subtitle = document.getElementById('dash-alas-subtitle');
      if (subtitle) {
        subtitle.innerHTML = '<b>' + escDash(device.name || device.id) + '</b> '
          + dashLocal('绑定了多个 ALAS 配置，选择要启动或停止的一个。', ' has several bound ALAS configs — choose one to start or stop.');
      }
      var list = document.getElementById('dash-alas-list');
      if (list) {
        list.innerHTML = configs.map(function (name) {
          var state = dashAlasStateForConfig(name);
          var action = dashAlasActionForState(state);
          return '<li class="dash-alas-row"><span class="dash-alas-copy"><b>' + escDash(name) + '</b>'
            + dashAlasChip(state) + '</span>'
            + '<button class="dash-log-btn" type="button" data-dash-alas="' + escDash(name) + '" data-dash-alas-action="' + action + '">'
            + (action === 'stop' ? dashLocal('停止', 'Stop') : dashLocal('启动', 'Start')) + '</button></li>';
        }).join('');
      }
      dashOpenDialog(dialog, 'dash-alas-close');
    }

    function dashConfirmStopCast(device) {
      var viewers = Number(device.viewers == null ? device.viewerCount : device.viewers) || 0;
      var body = '<b>' + escDash(device.name || device.id) + '</b> ' + (viewers > 0
        ? dashLocal('当前有 ' + viewers + ' 个观看端，关闭会立即中断他们的画面。', ' has ' + viewers + ' viewer(s); they lose the picture immediately.')
        : dashLocal('会立即结束该设备的投屏会话。', ' ends this casting session immediately.'));
      return dashConfirm({
        title: dashLocal('强行关闭投屏', 'Force-stop casting'),
        body: body,
        confirmText: dashLocal('强行关闭', 'Force stop'),
        danger: true,
      }).then(function (ok) {
        if (!ok) return false;
        return dashRunRowAction('stop-cast', function () {
          return window.ScrcpyGateApi.configured('sessions.stop', { params: { deviceId: device.id }, method: 'POST' });
        }, dashLocal('已关闭投屏', 'Casting stopped'));
      });
    }

    function dashConfirmDisable(device) {
      var viewers = Number(device.viewers == null ? device.viewerCount : device.viewers) || 0;
      var body = '<b>' + escDash(device.name || device.id) + '</b> '
        + dashLocal('停用后该设备不可投屏，也无法做连接检测与状态刷新。', 'Once disabled the device cannot cast, and status checks stop.')
        + (viewers > 0 ? ' ' + dashLocal('正在观看的 ' + viewers + ' 个观看端会立即断开。', 'The ' + viewers + ' connected viewer(s) will be disconnected.') : '');
      return dashConfirm({
        title: dashLocal('停用设备', 'Disable device'),
        body: body,
        confirmText: dashLocal('停用', 'Disable'),
        danger: true,
      }).then(function (ok) { return ok ? dashSetDeviceEnabled(device, false) : false; });
    }

    function dashHandleRowAction(action, device) {
      if (!device) return false;
      if (action === 'stop-cast') return dashConfirmStopCast(device);
      if (action === 'toggle-enabled') {
        return device.enabled === false ? dashSetDeviceEnabled(device, true) : dashConfirmDisable(device);
      }
      if (action === 'toggle-alas') {
        var configs = dashAlasConfigsFor(device);
        if (configs.length !== 1) return false;
        return dashToggleAlasConfig(device, configs[0], dashAlasActionForState(device.alasStatusCode));
      }
      if (action === 'pick-alas') { dashOpenAlasPicker(device); return true; }
      return false;
    }

    function renderDashDevices(items) {
      var body = document.getElementById('dash-devices-tbody'); if (!body) return;
      items = Array.isArray(items) ? items : [];
      body.innerHTML = items.length ? items.map(dashDeviceRow).join('') : '<tr><td colspan="7"><div class="table-empty">' + icon('smartphone') + '<span>' + dashLocal('暂无设备数据', 'No device data') + '</span></div></td></tr>';
      var counts = { all: items.length, online: 0, offline: 0, unknown: 0, mirroring: 0, 'alas-err': 0 };
      items.forEach(function (d) { var state = dashDeviceStatus(d); if (state === 'online') counts.online++; else if (state === 'offline') counts.offline++; else counts.unknown++; if (d.streaming || d.mirroring) counts.mirroring++; if (dashAlasToneForDevice(d) === 'error') counts['alas-err']++; });
      Object.keys(counts).forEach(function (key) { var el = document.querySelector('[data-count-filter="' + key + '"]'); if (el) el.textContent = counts[key]; });
      setDash('dash-devices-count', dashLocal('共 ', '') + dashFormatCount(items.length) + dashLocal(' 台', ' devices'));
      setDash('dash-devices-value', dashFormatCount(counts.online) + ' / ' + dashFormatCount(counts.all)); setDash('dash-devices-unit', dashLocal('在线', 'online')); setDash('dash-devices-note', dashFormatCount(counts.offline) + dashLocal(' 台离线', ' offline') + (counts.unknown ? ' · ' + dashFormatCount(counts.unknown) + dashLocal(' 台未检查', ' not checked') : '') + dashLocal(' · 数据来自设备服务', ' · Source: device service'));
    }
    function renderDashSessions(items) {
      var body = document.getElementById('dash-sessions-tbody'); if (!body) return;
      items = Array.isArray(items) ? items : [];
      body.innerHTML = items.length ? items.slice(0, 8).map(function (s) {
        var device = s.device && typeof s.device === 'object' ? (s.device.name || s.device.displayName || s.device.id) : (s.deviceName || s.device || '');
        if (!device || device === '-') return '';
        var viewerCount = dashViewerLabel(s.viewerCount);
        var controller = dashControllerLabel(s.controllerName || s.controller, s.role, s.username || s.user);
        return '<tr><td><div class="device-cell"><span class="device-glyph">' + icon('smartphone') + '</span><div style="min-width:0"><div class="device-name">' + escDash(device) + '</div><div class="device-model">' + escDash(s.deviceModel || '—') + '</div></div></div></td><td class="tabular-nums">' + escDash(viewerCount === '—' ? viewerCount : viewerCount + dashLocal(' 个观看端', ' viewers')) + '</td><td>' + escDash(controller) + '</td><td class="tabular-nums">' + escDash(dashSessionDuration(s)) + '</td><td><a class="row-btn open" href="/mirror" aria-label="' + escDash(dashLocal('打开投屏工作台', 'Open casting workspace')) + '"><i data-lucide="monitor-up" aria-hidden="true"></i><span class="btn-text">' + dashLocal('打开', 'Open') + '</span></a></td></tr>';
      }).join('') : '<tr><td colspan="5"><div class="table-empty">' + icon('radio') + '<span>' + dashLocal('暂无活跃会话', 'No active sessions') + '</span></div></td></tr>';
      setDash('dash-sessions-count', dashFormatCount(items.length) + dashLocal(' 个会话', ' sessions')); setDash('dash-sessions-value', dashFormatCount(items.length)); setDash('dash-sessions-unit', dashLocal('路投屏', 'casts')); setDash('dash-sessions-note', dashLocal('实时会话数据来自服务端', 'Live data from the service'));
    }
    function dashSessionsFromActive(active) {
      var groups = {};
      (active || []).forEach(function (row) {
        var clientId = String(row.client_id || row.clientId || '');
        var key = String(row.device_id || row.deviceId || '');
        if (!clientId || !key || key === '-') return;
        if (!groups[key]) {
          groups[key] = {
            deviceName: row.device_name || row.deviceName || '',
            deviceId: row.device_id || row.deviceId || key,
            viewerCount: 0,
            controllerName: '',
            connectedAt: row.connected_at || null,
            queueBytes: 0,
            drops: 0
          };
        }
        var group = groups[key];
        if (!group.deviceName || group.deviceName === '-') group.deviceName = row.device_name || row.deviceName || '';
        group.viewerCount += 1;
        if (row.has_control && !group.controllerName) group.controllerName = row.username || '';
        if (!group.connectedAt || (row.connected_at && row.connected_at < group.connectedAt)) group.connectedAt = row.connected_at;
        group.queueBytes += Number(row.queue_bytes || 0);
        group.drops += Number(row.drops || 0);
      });
      return Object.keys(groups).map(function (key) {
        var group = groups[key];
        if (!group.deviceName || group.deviceName === '-') return null;
        return {
          deviceName: group.deviceName,
          deviceId: group.deviceId,
          viewerCount: group.viewerCount,
          controllerName: group.controllerName || '',
          connectedAt: group.connectedAt || null,
          duration: '',
          queueBytes: group.queueBytes,
          drops: group.drops
        };
      }).filter(function (item) { return !!item; });
    }
    function fmtDashTime(value) {
      if (!value) return '—';
      var raw = String(value).trim();
      var date;
      if (/^[+-]?\d+(?:\.\d+)?$/.test(raw)) {
        var numeric = Number(raw);
        date = new Date(Math.abs(numeric) >= 1000000000000 ? numeric : numeric * 1000);
      } else {
        date = new Date(raw.replace(' ', 'T'));
      }
      return isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    function dashSessionsFromDevices(items) {
      return items.filter(function (d) { return d.streaming === true || d.mirroring === true; }).map(function (d) {
        return {
          deviceName: d.name || d.displayName || d.id,
          viewerCount: d.viewerCount == null ? (d.viewers || 0) : d.viewerCount,
          controllerName: d.controllerName || d.controller || '',
          connectedAt: d.connectedAt || d.connected_at || null,
          duration: d.duration || '—'
        };
      });
    }
    function renderDashAlasStatus(payload) {
      var d = payload && (payload.data || payload) || {};
      var state = d.status || d.state || 'unknown';
      var stateKey = String(d.dashboard_status || state).trim().toLowerCase();
      stateKey = dashStatusKey(stateKey);
      var supplied = d.dashboard_label || d.label || '';
      var readable = dashDashboard.readableText ? dashDashboard.readableText(supplied) : supplied;
      var errorStates = ['error', 'partial_error', 'unreachable', 'disconnected', 'timeout', 'token_missing', 'token_invalid', 'http_error'];
      var healthyStates = ['running', 'healthy', 'connected', 'online'];
      var value = (errorStates.indexOf(stateKey) >= 0 || healthyStates.indexOf(stateKey) >= 0) ? dashStatusLabel(stateKey) : (readable || dashStatusLabel(stateKey));
      var tone = String(d.dashboard_tone || d.tone || '').toLowerCase();
      // A stale producer tone must not paint a known error green. State is
      // the stronger signal; an explicit tone is only used for unknown
      // states or when it agrees with the normalized state.
      if (errorStates.indexOf(stateKey) >= 0) tone = 'error';
      else if (healthyStates.indexOf(stateKey) >= 0) tone = 'ok';
      else if (tone !== 'ok' && tone !== 'error') tone = 'none';
      var valueEl = document.getElementById('dash-alas-value');
      if (valueEl) {
        valueEl.textContent = value;
        valueEl.setAttribute('data-tone', tone);
      }
      /* backport: frontend repo — the stat icon tone must follow the state */
      var iconEl = document.getElementById('dash-alas-icon');
      if (iconEl) {
        iconEl.classList.remove('green', 'red', 'neutral');
        iconEl.classList.add(tone === 'error' ? 'red' : (tone === 'ok' ? 'green' : 'neutral'));
      }
      var count = Number(d.config_count != null ? d.config_count : (d.configs || []).length);
      if (!isFinite(count) || count < 0) count = 0;
      count = Math.floor(count);
      var checked = d.checked_at || d.checkedAt || d.last_check || d.lastCheck;
      setDash('dash-alas-note', dashFormatCount(count) + dashLocal(' 个配置 · ', ' configs · ') + (checked ? dashLocal('最近检查 ', 'Last checked ') + fmtDashTime(checked) : dashLocal('状态来自 ALAS 连接服务', 'Status from ALAS service')));
    }
    function rerenderDashSnapshot() {
      if (!dashLastSnapshot) return;
      renderDashDevices(dashLastSnapshot.devices || []);
      renderDashSessions(dashLastSnapshot.sessions || []);
      renderDashAlasStatus(dashLastSnapshot.alas || {});
      renderDashActivities({ recent_activities: dashLastSnapshot.activities || [] });
      renderDashAttention(dashLastSnapshot.attention || []);
      renderDashAlerts(dashLastSnapshot.alerts || { items: [], pending_count: 0 });
      syncDashAlertView();
      renderDashLoggingHealth(dashLastSnapshot.logging || {});
      setDashRefreshState(dashLastSnapshot.refreshState || 'ready', dashRefreshMessage(dashLastSnapshot.refreshState || 'ready', dashLastSnapshot.updatedAt));
      if (window.lucide) lucide.createIcons();
    }
    function scheduleDashboardRefresh() {
      window.clearTimeout(dashDashboardTimer);
      dashDashboardTimer = null;
      if (document.visibilityState && document.visibilityState !== 'visible') return;
      dashDashboardTimer = window.setTimeout(function () {
        dashDashboardTimer = null;
        loadDashboard({ force: true, runtime: true });
      }, DASHBOARD_REFRESH_MS);
    }
    /* ---------- 系统更新（只读）：只显示当前/最新版本与宿主机更新命令 ----------
       应用本身跑在只读镜像里、没有 Docker 访问权，所以这里刻意不提供下载或
       「一键更新」按钮：检查走 /api/admin/update-check，应用由宿主机
       `deploy.sh --update` 完成（先备份、只换镜像、失败回滚）。 */
    var updateCheckInFlight = false;
    function updateCommandText(info) {
      return (info && info.hostCommand) || '—';
    }
    function renderUpdateCheck(info) {
      if (!info) return;
      setDash('update-current', info.currentVersion || '—');
      setDash('update-latest', info.latestVersion || '—');
      var state = document.getElementById('update-state');
      var text;
      var highlighted = false;
      if (!info.ok) {
        text = dashLocal('无法检查（离线或上游不可达）', 'Check unavailable (offline)');
      } else if (info.noRelease) {
        text = dashLocal('暂无已发布版本', 'No published release yet');
      } else if (info.updateAvailable === true) {
        text = dashLocal('有可用更新', 'Update available');
        highlighted = true;
      } else if (info.updateAvailable === false) {
        text = dashLocal('已是最新', 'Up to date');
      } else {
        text = dashLocal('无法按版本号比较，请核对镜像引用', 'Versions cannot be compared; check the image reference');
      }
      if (state) {
        state.textContent = text;
        state.classList.toggle('is-new', highlighted);
      }
      setDash('update-command', updateCommandText(info));
      var copyButton = document.getElementById('update-copy');
      if (copyButton) copyButton.disabled = !info.hostCommand;
      var hint = document.getElementById('update-hint');
      if (hint) {
        if (!info.ok) {
          hint.textContent = dashLocal('检查失败：', 'Check failed: ') + (info.error || '')
            + dashLocal('。可稍后点「检查更新」重试；离线部署出现这一行属正常。',
              '. Retry with “Check for updates” later; this is expected for an offline deployment.');
        } else if (info.noRelease) {
          hint.textContent = dashLocal('镜像仓库可达，但尚无稳定版本、latest 或 edge 镜像。',
            'The registry is reachable, but has no stable version, latest, or edge image.');
        } else if (info.updateAvailable === true) {
          hint.textContent = dashLocal('新版本 ', 'New release ')
            + (info.latestVersion || '') + dashLocal('。在服务器上执行下面的命令应用更新；',
              '. Apply it on the server with the command below; ')
            + dashLocal('更新前备份数据库、配套密钥与 .env；失败时尝试恢复原镜像。数据库迁移不会自动回退。',
              'it backs up the database, matching key, and .env, then attempts image rollback on failure. Database migrations are not rolled back automatically.');
        } else {
          hint.textContent = dashLocal('请在对应 bridge 部署目录执行命令。浮动标签需拉取后比较镜像；镜像回滚不会恢复数据库迁移。',
            'Run the command in the matching bridge deployment directory. Floating tags require pulling to compare images; image rollback does not reverse database migrations.');
        }
      }
      if (window.lucide) { window.lucide.createIcons(); }
    }
    function loadUpdateCheck(refresh) {
      if (updateCheckInFlight) return Promise.resolve();
      var api = window.ScrcpyGateApi;
      if (!api || typeof api.isConfigured !== 'function' || !api.isConfigured('system.update')) {
        setDash('update-state', dashLocal('接口未配置', 'Endpoint not configured'));
        return Promise.resolve();
      }
      updateCheckInFlight = true;
      setDash('update-state', dashLocal('检查中…', 'Checking…'));
      return api.configured('system.update', refresh ? { refresh: 1 } : {})
        .then(function (payload) { renderUpdateCheck(payload); })
        .catch(function (error) {
          setDash('update-state', dashLocal('无法检查', 'Check unavailable'));
          var message = api.errorMessage ? api.errorMessage(error) : '';
          setDash('update-hint', message || dashLocal('检查更新失败，请稍后重试。', 'Update check failed; retry later.'));
        })
        .finally(function () { updateCheckInFlight = false; });
    }
    function copyUpdateCommand() {
      var code = document.getElementById('update-command');
      var button = document.getElementById('update-copy');
      if (!code) return;
      var done = function () {
        var label = button ? button.querySelector('span') : null;
        if (!label) return;
        var original = label.textContent;
        label.textContent = dashLocal('已复制', 'Copied');
        window.setTimeout(function () { label.textContent = original; }, 1600);
      };
      var text = code.textContent || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () {});
        return;
      }
      // 没有剪贴板权限时退化成选中文本，用户可自行复制（只读展示不受影响）。
      try {
        var range = document.createRange();
        range.selectNodeContents(code);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      } catch (error) {
        /* ignore */
      }
    }
    (function wireUpdateCheck() {
      var button = document.getElementById('update-check-btn');
      if (button) button.addEventListener('click', function () { loadUpdateCheck(true); });
      var copy = document.getElementById('update-copy');
      if (copy) copy.addEventListener('click', copyUpdateCommand);
    })();

    function loadDashboard(options) {
      options = options || {};
      if (document.visibilityState && document.visibilityState !== 'visible') {
        dashDashboardRefreshPending = true;
        return Promise.resolve(false);
      }
      if (dashDashboardRequest) return dashDashboardRequest;
      // Runtime probing is bounded server-side; keep per-config ALAS state
      // current unless a caller explicitly requests a lightweight snapshot.
      var query = { runtime: options.runtime === false ? '0' : '1' };
      setDashRefreshState('loading', dashRefreshMessage('loading'));
      setDashboardBusy(true);
      var overviewRequest = window.ScrcpyGateApi.configured('dashboard.overview', {
        query: query,
        force: options.force === true,
        cache: false
      }).then(function (p) {
        var devices = p.devices || dashList(p);
        var activeSessions = p.activeSessions || p.active_sessions || [];
        var liveSessions = dashSessionsFromActive(activeSessions);
        if (liveSessions.length) {
          var liveByDevice = {};
          liveSessions.forEach(function (session) { liveByDevice[String(session.deviceId || session.deviceName)] = session; });
          devices = devices.map(function (device) {
            var key = String(device.id || device.device_id || device.name || '');
            var live = liveByDevice[key] || liveByDevice[String(device.name || '')];
            if (!live) return device;
            return Object.assign({}, device, { viewerCount: live.viewerCount, viewers: live.viewerCount, controllerName: live.controllerName, controller: live.controllerName || null, streaming: true, mirroring: true });
          });
        }
        var sessionRows = liveSessions.length ? liveSessions : (p.sessions || dashSessionsFromDevices(devices));
        var alasPayload = p.alas || {};
        var activityRows = p.recent_activities || [];
        var alertPayload = p.alerts || { items: [], pending_count: 0 };
        renderDashDevices(devices);
        renderDashSessions(sessionRows);
        renderDashAlasStatus(alasPayload);
        renderDashActivities({ recent_activities: activityRows });
        renderDashAttention(p.attention || []);
        renderDashAlerts(alertPayload);
        syncDashAlertView();
        renderDashLoggingHealth(p);
        dashDashboardHasData = true;
        dashDashboardRefreshPending = false;
        var updatedAt = p.updated_at || Date.now();
        dashLastSnapshot = {
          devices: devices,
          sessions: sessionRows,
          alas: alasPayload,
          activities: activityRows,
          alerts: alertPayload,
          attention: Array.isArray(p.attention) ? p.attention : [],
          logging: p,
          refreshState: 'ready',
          updatedAt: updatedAt
        };
        setDashRefreshState('ready', dashRefreshMessage('ready', updatedAt));
        if (options.manual === true && !dashAlertCountChanged) dashAnnounce(dashLocal('仪表盘已更新', 'Dashboard updated'));
        dashAlertCountChanged = false;
        if (window.lucide) lucide.createIcons();
        return p;
      }).catch(function (error) {
        if (dashDashboardHasData) {
          setDashRefreshState('error', dashRefreshMessage('error'));
          if (dashLastSnapshot) dashLastSnapshot.refreshState = 'error';
          if (options.manual === true) dashAnnounce(dashLocal('仪表盘刷新失败，已保留上次数据', 'Dashboard refresh failed; showing last data'));
        } else {
          renderDashAuditFailure(error);
          setDashRefreshState('error', dashLocal('加载失败', 'Load failed'));
          dashAnnounce(dashLocal('仪表盘加载失败，请稍后重试', 'Dashboard failed to load; retry later'));
        }
        return null;
      }).finally(function () {
        setDashboardBusy(false);
        dashDashboardRequest = null;
        scheduleDashboardRefresh();
      });
      dashDashboardRequest = overviewRequest;
      return dashDashboardRequest;
    }

    var dashDetailClose = document.getElementById('dash-detail-close');
    var dashDetailMask = document.getElementById('dash-detail-mask');
    if (dashDetailClose) dashDetailClose.addEventListener('click', closeDashDetail);
    if (dashDetailMask) {
      dashDetailNative = typeof dashDetailMask.showModal === 'function' && typeof dashDetailMask.close === 'function';
      dashDetailMask.classList.add(dashDetailNative ? 'dash-detail-native' : 'dash-detail-legacy');
      dashDetailMask.addEventListener('click', function (event) { if (event.target === dashDetailMask) closeDashDetail(); });
      dashDetailMask.addEventListener('keydown', trapDashDetailFocus);
      if (dashDetailNative) {
        dashDetailMask.addEventListener('cancel', function (event) {
          event.preventDefault();
          closeDashDetail();
        });
        dashDetailMask.addEventListener('close', finishDashDetailClose);
      }
    }
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && dashDetailIsOpen()) {
        event.preventDefault();
        closeDashDetail();
      }
    });
    /* ===== 设备行快捷操作的接线 ===== */
    var dashConfirmMask = dashBindDialog('dash-confirm-mask');
    var dashAlasMask = dashBindDialog('dash-alas-mask');
    var dashConfirmCancel = document.getElementById('dash-confirm-cancel');
    if (dashConfirmCancel) dashConfirmCancel.addEventListener('click', function () { if (dashConfirmMask) dashCloseDialog(dashConfirmMask); });
    ['dash-confirm-close', 'dash-alas-close', 'dash-alas-done'].forEach(function (id) {
      var btn = document.getElementById(id);
      var dialog = id === 'dash-confirm-close' ? dashConfirmMask : dashAlasMask;
      if (btn && dialog) btn.addEventListener('click', function () { dashCloseDialog(dialog); });
    });
    var dashAlasList = document.getElementById('dash-alas-list');
    if (dashAlasList) {
      dashAlasList.addEventListener('click', function (event) {
        var btn = event.target.closest ? event.target.closest('[data-dash-alas]') : null;
        if (!btn) return;
        var device = dashAlasMask && dashAlasMask.getAttribute('data-device-id');
        var target = dashDeviceById(device);
        if (!target) return;
        dashToggleAlasConfig(target, btn.getAttribute('data-dash-alas'), btn.getAttribute('data-dash-alas-action')).then(function () {
          if (dashAlasMask) dashCloseDialog(dashAlasMask);
        });
      });
    }
    function dashMenuItems(pop) {
      return Array.prototype.filter.call(pop.querySelectorAll('.menu-item'), function (item) {
        return !item.disabled && item.getAttribute('aria-disabled') !== 'true';
      });
    }
    document.addEventListener('click', function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      // 打开菜单后把焦点送进第一项（shell 的委托逻辑负责定位与开合，这里只补可达性）
      var trigger = target.closest('#dash-devices-tbody .menu-open-btn');
      if (trigger) {
        var menu = trigger.closest('.row-menu');
        var pop = menu ? menu.querySelector('.menu-pop') : null;
        var opened = !!(menu && menu.classList.contains('open'));
        trigger.setAttribute('aria-expanded', opened ? 'true' : 'false');
        if (opened && pop) {
          var items = dashMenuItems(pop);
          if (items.length) window.setTimeout(function () { items[0].focus(); }, 0);
        }
      } else {
        Array.prototype.slice.call(document.querySelectorAll('#dash-devices-tbody .row-menu')).forEach(function (row) {
          var btn = row.querySelector('.menu-open-btn');
          if (btn) btn.setAttribute('aria-expanded', row.classList.contains('open') ? 'true' : 'false');
        });
      }
      var actionBtn = target.closest('#dash-devices-tbody [data-dash-action]');
      if (!actionBtn || actionBtn.disabled) return;
      event.preventDefault();
      var row = actionBtn.closest('tr.device-row');
      var device = dashDeviceById(row ? row.getAttribute('data-device-id') : '');
      var menuWrap = actionBtn.closest('.row-menu');
      if (menuWrap) menuWrap.classList.remove('open');
      if (actionBtn.getAttribute('data-dash-action') === 'pick-alas' && dashAlasMask) {
        dashAlasMask.setAttribute('data-device-id', String(device ? device.id : ''));
      }
      dashHandleRowAction(actionBtn.getAttribute('data-dash-action'), device);
    });
    document.addEventListener('keydown', function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      var pop = target.closest('#dash-devices-tbody .menu-pop');
      if (!pop) return;
      if (event.key === 'Escape') {
        var menu = pop.closest('.row-menu');
        if (menu) menu.classList.remove('open');
        var trigger = menu ? menu.querySelector('.menu-open-btn') : null;
        if (trigger) { trigger.setAttribute('aria-expanded', 'false'); trigger.focus(); }
        event.preventDefault();
        return;
      }
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      var items = dashMenuItems(pop);
      if (!items.length) return;
      var index = items.indexOf(document.activeElement);
      var step = event.key === 'ArrowDown' ? 1 : -1;
      var next = items[(index + step + items.length) % items.length];
      event.preventDefault();
      next.focus();
    });
    var dashResolve = document.getElementById('dash-detail-resolve');
    if (dashResolve) dashResolve.addEventListener('click', function () {
      if (!dashDetailCurrent || !dashDetailCurrent.alert) return;
      var entry = dashDetailCurrent.entry || {};
      var id = entry.alertId || entry.eventId || entry.event_id;
      if (!id) return;
      dashResolve.disabled = true;
      window.ScrcpyGateApi.configured('alerts.resolve', { params: { id: id }, method: 'POST', body: {} }).then(function () {
        closeDashDetail();
        // Resolving an alert changes the dashboard read model. Reuse the
        // guarded refresh path so devices, activity and log health stay in
        // sync with the updated pending count.
        return loadDashboard({ force: true, runtime: true, manual: true });
      }).catch(function (error) {
        dashResolve.disabled = false;
        var message = window.ScrcpyGateApi.errorMessage ? window.ScrcpyGateApi.errorMessage(error) : '告警处理失败';
        if (window.ScrcpyGateUi && window.ScrcpyGateUi.toast) window.ScrcpyGateUi.toast(message, 'error');
      });
    });

    var tabs = document.querySelectorAll('.filter-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (t) {
          t.classList.remove('active');
          t.setAttribute('aria-selected', 'false');
        });
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        var f = tab.getAttribute('data-filter');
        var rows = document.querySelectorAll('#devices tbody tr.device-row');
        var shown = 0;
        rows.forEach(function (row) {
          var ok = (f === 'all') || row.getAttribute('data-' + f) === '1';
          row.classList.toggle('is-hidden', !ok);
          if (ok) { shown++; }
        });
        /* backport: frontend repo — the empty row id must match the rendered table */
        var empty = document.getElementById('dash-devices-empty');
        if (!empty) {
          var deviceBody = document.getElementById('dash-devices-tbody');
          if (deviceBody) {
            empty = document.createElement('tr');
            empty.id = 'dash-devices-empty';
            empty.innerHTML = '<td colspan="7"><div class="table-empty">' + icon('smartphone') + '<span>' + dashLocal('暂无设备数据', 'No device data') + '</span></div></td>';
            deviceBody.appendChild(empty);
          }
        }
        if (empty) { empty.classList.toggle('is-hidden', shown > 0); }
      });
    });
    bindDashDetailList('dash-alert-list');
    bindDashDetailList('dash-activity-list');

    // 分段控件：点击切换，左右方向键在两组之间移动焦点并切换（roving tabindex）。
    var dashAlertSeg = document.getElementById('dash-alert-seg');
    if (dashAlertSeg) {
      var dashAlertTabs = Array.prototype.slice.call(dashAlertSeg.querySelectorAll('[data-alert-view]'));
      var selectDashAlertView = function (tab, focus) {
        var view = tab.getAttribute('data-alert-view');
        dashAlertViewPinned = true;
        setDashAlertView(view);
        if (focus) tab.focus();
      };
      dashAlertTabs.forEach(function (tab) {
        tab.addEventListener('click', function () { selectDashAlertView(tab, false); });
        tab.addEventListener('keydown', function (event) {
          var step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
            : (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0);
          if (!step) return;
          event.preventDefault();
          var index = dashAlertTabs.indexOf(tab);
          var next = dashAlertTabs[(index + step + dashAlertTabs.length) % dashAlertTabs.length];
          if (next) selectDashAlertView(next, true);
        });
      });
    }
    if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
      window.ScrcpyGateI18n.on(function () {
        // Re-render every data-backed region from the last snapshot.  The
        // shared i18n observer can translate static markup, but it cannot
        // reconstruct labels embedded in rows that were rendered earlier.
        rerenderDashSnapshot();
      });
    }
    if (dashDetailMask) {
      dashDetailMask.setAttribute('aria-hidden', 'true');
      dashDetailMask.setAttribute('inert', '');
    }
    var dashRefreshButton = document.getElementById('dash-refresh-button');
    if (dashRefreshButton) dashRefreshButton.addEventListener('click', function () {
      loadDashboard({ force: true, runtime: true, manual: true });
    });
    function handleDashboardVisibility(visible) {
      if (!visible) {
        window.clearTimeout(dashDashboardTimer);
        dashDashboardTimer = null;
        dashDashboardRefreshPending = true;
        return;
      }
      if (dashDashboardRefreshPending) {
        dashDashboardRefreshPending = false;
        loadDashboard({ force: true, runtime: true });
      } else {
        scheduleDashboardRefresh();
      }
    }
    if (window.ScrcpyGateUi && typeof window.ScrcpyGateUi.bindVisibility === 'function') {
      window.ScrcpyGateUi.bindVisibility(handleDashboardVisibility);
    } else {
      document.addEventListener('visibilitychange', function () {
        handleDashboardVisibility(document.visibilityState === 'visible');
      }, { passive: true });
    }
    renderDashDevices([]); renderDashSessions([]); loadDashboard({ force: true, runtime: true });
    loadUpdateCheck(false);
    if (window.ScrcpyGateSession) { window.ScrcpyGateSession.start().catch(function () {}); }
  })();
