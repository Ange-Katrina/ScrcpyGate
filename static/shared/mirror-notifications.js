/* Mirror notification center.  The page supplies its display and API
 * dependencies so notification state does not become a second page store. */
(function (global) {
  'use strict';

  function create(options) {
    options = options || {};
    var tr = options.tr || function (value) { return String(value == null ? '' : value); };
    var escHtml = options.escHtml || function (value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character];
      });
    };
    var api = options.api || global.ScrcpyGateApi || null;
    var labels = options.dashboardLabels || global.ScrcpyGateDashboard || {};
    var closeFixedPops = options.closeFixedPops || function () {};
    var refreshIcons = options.refreshIcons || function () {
      if (global.lucide && typeof global.lucide.createIcons === 'function') global.lucide.createIcons();
    };
    var notifyBtn = document.getElementById('notify-btn');
    var notifyPop = document.getElementById('notify-pop');
    var notifyList = document.getElementById('notify-list');
    var notifyDot = document.getElementById('notify-dot');
    var notifyMarkAll = document.getElementById('notify-mark-all');
    var notifications = [];
    var loaded = false;
    var unreadFromServer = null;

    function currentLabels() {
      return global.ScrcpyGateDashboard || labels || {};
    }

    function unreadCount() {
      if (unreadFromServer !== null && isFinite(unreadFromServer)) {
        return Math.max(0, unreadFromServer);
      }
      return notifications.filter(function (item) { return item.read !== true; }).length;
    }

    function icon(type) {
      if (type === 'warn') return '<span class="notify-ic warn"><i data-lucide="triangle-alert"></i></span>';
      if (type === 'danger' || type === 'error') return '<span class="notify-ic danger"><i data-lucide="circle-alert"></i></span>';
      return '<span class="notify-ic info"><i data-lucide="info"></i></span>';
    }

    function time(value) {
      var display = currentLabels();
      var full = display.formatTime ? display.formatTime(value) : String(value == null ? '' : value);
      var relative = display.timeLabel ? display.timeLabel(value) : full;
      var iso = display.dateTime ? display.dateTime(value) : '';
      var datetime = iso ? ' datetime="' + escHtml(iso) + '"' : '';
      return '<time class="notify-time"' + datetime + ' title="' + escHtml(full) + '">' + escHtml(relative || '—') + '</time>';
    }

    function result(item) {
      /* 收件箱条目（带 kind）用图标表达语气，不再叠一个「成功/拒绝」状态胶囊：
         对「即将到期」这类提醒来说那个词毫无意义。 */
      if (item && item.kind) return '';
      var display = currentLabels();
      var value = display.displayResult
        ? display.displayResult(item)
        : String(item.result || item.outcome || item.status || 'unknown').trim().toLowerCase();
      var label = display.resultLabel ? display.resultLabel(item) : value;
      var tone = display.resultClass ? display.resultClass(item) : (
        value === 'success' || value === 'ok' || value === 'passed' ? 'success' :
          (value === 'denied' || value === 'blocked' || value === 'timeout' ? 'denied' : (value === 'unknown' ? 'unknown' : 'fail'))
      );
      return '<span class="notify-status ' + escHtml(tone) + '">' + escHtml(label || tr('未知')) + '</span>';
    }

    function render() {
      if (!notifyList) return;
      if (!notifications.length) {
        notifyList.innerHTML = '<div class="notify-empty"><i data-lucide="bell-off"></i><span>' + escHtml(tr('暂无通知')) + '</span><small>' + escHtml(tr('新的通知将在出现时显示')) + '</small></div>';
      } else {
        notifyList.innerHTML = notifications.map(function (item) {
          var title = String(item.title || item.message || '').trim();
          var details = [];
          var message = String(item.message || '').trim();
          if (message && message !== title) details.push(message);
          var target = String(item.deviceName || '').trim();
          if (target) details.push(tr('设备') + ' ' + target);
          var actor = String(item.operatorLabel || '').trim();
          if (actor && actor !== '系统' && actor !== 'System') details.push(tr('操作者') + ' ' + actor);
          var detail = details.join(' · ');
          var href = String(item.href || '').trim();
          return '<button class="notify-item' + (item.read !== true ? ' unread' : '') + '" type="button" data-notify-id="' + escHtml(item.id) + '"'
            + (href ? ' data-notify-href="' + escHtml(href) + '"' : '') + '>' +
            icon(item.type) + '<span class="notify-copy"><span class="notify-line"><span class="notify-text">' + escHtml(title) + '</span>' +
            result(item) + '</span>' + (detail ? '<span class="notify-detail">' + escHtml(detail) + '</span>' : '') + time(item.createdAt || item.time || '') + '</span></button>';
        }).join('');
        notifyList.querySelectorAll('[data-notify-id]').forEach(function (item) {
          item.addEventListener('click', function () {
            var href = item.getAttribute('data-notify-href');
            markRead(item.getAttribute('data-notify-id'));
            if (href) global.location.href = href;
          });
        });
      }
      var unread = unreadCount();
      if (notifyDot) notifyDot.hidden = unread === 0;
      if (notifyMarkAll) notifyMarkAll.disabled = unread === 0;
      if (notifyBtn) {
        var label = unread > 0 ? tr('通知（' + unread + '）') : tr('通知');
        notifyBtn.setAttribute('aria-label', label);
        notifyBtn.setAttribute('title', label);
      }
      refreshIcons();
    }

    function apply(payload) {
      var list = api && typeof api.list === 'function' ? api.list(payload) : [];
      var display = currentLabels();
      var adapter = global.ScrcpyGateV2 || {};
      unreadFromServer = payload && typeof payload === 'object' && payload.unread != null
        ? Number(payload.unread)
        : null;
      notifications = list.map(function (raw) {
        var item = raw || {};
        var severity = String(item.severity || item.level || '').trim().toLowerCase();
        var resultValue = display.displayResult ? display.displayResult(item) : (adapter.displayResult ? adapter.displayResult(item) : String(item.result || item.outcome || item.status || '').trim().toLowerCase());
        var alert = display.isAlertRecord ? display.isAlertRecord(item) : (adapter.auditIsAlertRecord ? adapter.auditIsAlertRecord(item) : !!(item.alertType || item.alert_type || item.isAlert || item.kind === 'alert') || severity === 'warn' || severity === 'warning' || severity === 'error' || severity === 'critical' || ['fail', 'failure', 'error', 'denied', 'blocked', 'timeout'].indexOf(resultValue) >= 0);
        var rawTitle = String(item.title || item.message || '').trim();
        var readableTitle = display.readableText ? display.readableText(rawTitle) : (adapter.auditReadableText ? adapter.auditReadableText(rawTitle) : rawTitle);
        var action = item.action || item.action_code || item.event || item.event_name || item.actionLabel || item.action_label || rawTitle;
        var mapped = Object.assign({}, item, { isAlert: alert, action: action, result: resultValue, severity: severity });
        var reasonLabel = item.reasonLabel || item.reason_label || (display.reasonLabel ? display.reasonLabel(mapped) : (adapter.auditReasonLabel ? adapter.auditReasonLabel(mapped) : ''));
        /* 收件箱条目（带 kind）的标题/正文由适配层按 kind 生成；
           审计标题映射是给日志记录用的，不能把它覆盖成「系统事件」。 */
        var isInboxItem = !!item.kind;
        var title;
        var message;
        if (isInboxItem) {
          title = String(item.title || '').trim() || rawTitle;
          message = String(item.message || '').trim() || title;
        } else {
          title = display.title ? display.title(mapped) : (adapter.auditRecordTitle ? adapter.auditRecordTitle(mapped) : rawTitle);
          if (!title || title === '系统操作' || title === 'System action') title = readableTitle || rawTitle;
          title = String(title || tr('系统事件')).replace(/(?:成功|通过|失败|错误|拒绝|阻止|超时)$/, '').replace(/\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out)$/i, '').trim() || tr('系统事件');
          message = String(item.message || '').trim();
          if (display.readableText) message = display.readableText(message) || reasonLabel || title;
          else if (adapter.auditReadableText) message = adapter.auditReadableText(message) || reasonLabel || title;
        }
        return {
          id: item.id,
          kind: item.kind || '',
          title: title,
          message: message,
          href: String(item.href || ''),
          reasonLabel: reasonLabel,
          actionLabel: title,
          severity: severity,
          result: resultValue,
          alertType: item.alertType || item.alert_type || '',
          target: item.target || item.targetId || item.target_id || '',
          targetType: item.targetType || item.target_type || '',
          deviceName: item.deviceName || item.device_name || '',
          operatorLabel: display.readableActor ? display.readableActor(item) : (item.operatorLabel || item.operator || item.username || ''),
          type: item.type || (severity === 'error' || severity === 'critical' || ['fail', 'failure', 'error', 'denied', 'blocked', 'timeout'].indexOf(resultValue) >= 0 ? 'error' : (alert ? 'warn' : 'info')),
          createdAt: item.createdAt || item.time,
          read: item.read === true || item.read === '1'
        };
      });
      loaded = true;
      render();
    }

    function load() {
      if (!api || typeof api.isConfigured !== 'function' || !api.isConfigured('notifications.list')) {
        notifications = [];
        loaded = false;
        render();
        if (notifyList) notifyList.innerHTML = '<div class="notify-empty"><i data-lucide="bell-off"></i><span>' + escHtml(tr('通知接口未配置')) + '</span><small>' + escHtml(tr('连接数据服务后将显示通知')) + '</small></div>';
        if (notifyDot) notifyDot.hidden = true;
        if (notifyMarkAll) notifyMarkAll.disabled = true;
        refreshIcons();
        return Promise.resolve(null);
      }
      return api.configured('notifications.list', { query: { limit: 20 } }).then(function (payload) {
        apply(payload);
        return payload;
      }).catch(function () {
        notifications = [];
        render();
        return null;
      });
    }

    function markRead(id) {
      var target = notifications.filter(function (item) { return item.id === id; })[0];
      if (target) target.read = true;
      unreadFromServer = null;
      render();
      return postRead({ ids: [id] });
    }

    function markAllRead() {
      notifications.forEach(function (item) { item.read = true; });
      unreadFromServer = null;
      render();
      return postRead({ all: true });
    }

    function postRead(body) {
      if (!api || typeof api.isConfigured !== 'function' || !api.isConfigured('notifications.markRead')) {
        return Promise.resolve(null);
      }
      return api.configured('notifications.markRead', { method: 'POST', body: body }).then(function (result) {
        // 服务端返回的未读数才是权威值：别让本地乐观更新和它长期不一致。
        if (result && result.unread != null) {
          unreadFromServer = Number(result.unread);
          render();
        }
        return result;
      }).catch(function () { return null; });
    }

    if (notifyBtn && notifyPop) {
      notifyBtn.addEventListener('click', function (event) {
        event.stopPropagation();
        var wasOpen = notifyPop.classList.contains('open');
        closeFixedPops();
        if (!wasOpen) {
          notifyPop.classList.add('open');
          var rect = notifyBtn.getBoundingClientRect();
          var width = notifyPop.offsetWidth || 320;
          notifyPop.style.left = Math.max(8, Math.min(rect.right - width, global.innerWidth - width - 8)) + 'px';
          notifyPop.style.top = Math.max(8, rect.bottom + 6) + 'px';
          notifyBtn.setAttribute('aria-expanded', 'true');
          if (!loaded) load();
        }
      });
    }
    if (notifyMarkAll) {
      notifyMarkAll.addEventListener('click', function () { markAllRead(); });
    }
    if (global.ScrcpyGateI18n && typeof global.ScrcpyGateI18n.on === 'function') {
      global.ScrcpyGateI18n.on(function () {
        labels = global.ScrcpyGateDashboard || labels;
        if (loaded) render();
      });
    }

    return Object.freeze({
      load: load,
      refresh: load,
      apply: apply,
      render: render,
      markRead: markRead,
      markAllRead: markAllRead,
      unreadCount: unreadCount,
      records: function () { return notifications.slice(); }
    });
  }

  global.ScrcpyGateMirrorNotifications = Object.freeze({ create: create });
}(window));
