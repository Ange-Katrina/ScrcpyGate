/* IP 封禁面板（BAN）：/security 页上的「谁被挡在门外」视图。
   - 新建封禁支持预设（15 分钟/1 小时/24 小时/7 天/永久）与自定义分钟数（上限 365 天）。
   - 列表默认只显示生效中的封禁，可勾选显示已解除/已过期。
   - 「事件」展开该地址的封禁/改期/解封/自然到期记录。
   - 提交前确认目标、时长与恢复方式；服务端独立校验自封禁确认标志。
   端点：ban.list / ban.create / ban.lift / ban.events。 */
(function () {
  var root = document.querySelector('.ban-card');
  if (!root) return;

  var els = {
    summary: document.getElementById('ban-summary'),
    status: document.getElementById('ban-status'),
    counters: document.getElementById('ban-counters'),
    ip: document.getElementById('ban-ip'),
    preset: document.getElementById('ban-preset'),
    customWrap: document.getElementById('ban-custom-wrap'),
    custom: document.getElementById('ban-custom'),
    reason: document.getElementById('ban-reason'),
    submit: document.getElementById('ban-submit'),
    refresh: document.getElementById('ban-refresh'),
    prev: document.getElementById('ban-prev'),
    next: document.getElementById('ban-next'),
    page: document.getElementById('ban-page'),
    includeInactive: document.getElementById('ban-include-inactive'),
    rows: document.getElementById('ban-rows'),
    selfWarning: document.getElementById('ban-self-warning'),
    events: document.getElementById('ban-events'),
    eventsTitle: document.getElementById('ban-events-title'),
    eventsList: document.getElementById('ban-events-list'),
    eventsClose: document.getElementById('ban-events-close')
  };

  var PAGE_SIZE = 50;
  var MAX_CUSTOM_MINUTES = 365 * 24 * 60;
  var dialog = document.getElementById('ban-dialog');
  var dialogStatus = document.getElementById('ban-dialog-status');
  var dialogWarning = document.getElementById('ban-dialog-warning');
  var dialogOrigin = null;
  var fromAccess = false;
  var state = { offset: 0, items: [], hasMore: false, counters: {}, loading: false, seq: 0 };

  function api() { return window.ScrcpyGateApi; }
  function tr(text) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(text) : text; }

  function escapeText(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function num(value) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : 0;
  }

  function fmtTime(seconds) {
    var value = num(seconds);
    if (!value) return '—';
    try { return new Date(value * 1000).toLocaleString(); } catch (error) { return '—'; }
  }

  function fmtRemaining(row) {
    if (!row.active) return row.revokedTs ? '已解除' : '已到期';
    if (row.permanent) return '永久';
    var seconds = num(row.remainingSeconds);
    if (seconds <= 0) return '已到期';
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days) return days + ' 天 ' + hours + ' 小时';
    if (hours) return hours + ' 小时 ' + minutes + ' 分钟';
    return Math.max(1, minutes) + ' 分钟';
  }

  function setStatus(text, tone) {
    if (!els.status) return;
    els.status.textContent = text;
    if (dialogStatus && dialog.open) { dialogStatus.textContent = text; dialogStatus.setAttribute("data-tone", tone || ""); }
    els.status.setAttribute('data-tone', tone || '');
  }

  function setWarning(text) {
    if (!els.selfWarning) return;
    els.selfWarning.hidden = !text;
    els.selfWarning.textContent = text || '';
    if (dialogWarning && dialog.open) { dialogWarning.hidden = !text; dialogWarning.textContent = text || ''; }
  }

  function renderCounters() {
    if (!els.counters) return;
    var counters = state.counters || {};
    var items = [
      ['生效中', num(counters.active)],
      ['永久', num(counters.permanent)],
      ['今日新增', num(counters.created_today)],
      ['历史累计', num(counters.total)],
      ['事件条数', num(counters.events)]
    ];
    els.counters.innerHTML = items.map(function (pair) {
      return '<li class="vis-stat"><b>' + escapeText(Number(pair[1]).toLocaleString()) + '</b><span>' + escapeText(pair[0]) + '</span></li>';
    }).join('');
    if (els.summary) {
      els.summary.textContent = num(counters.active) + ' 个生效封禁'
        + (num(counters.permanent) ? '（含 ' + num(counters.permanent) + ' 个永久）' : '');
    }
  }

  function renderRows() {
    if (!els.rows) return;
    if (!state.items.length) {
      els.rows.innerHTML = '<tr><td class="table-empty" colspan="6"><i data-lucide="shield-check"></i><span>当前没有封禁</span></td></tr>';
    } else {
      els.rows.innerHTML = state.items.map(function (row) {
        var action = row.active
          ? '<button type="button" class="guard-preset ban-lift" data-ip="' + escapeText(row.ip) + '">解除</button>'
          : '<span class="vis-ua">已失效</span>';
        return '<tr class="ban-row" data-ip="' + escapeText(row.ip) + '">'
          + '<td class="vis-ip">' + escapeText(row.ip) + '</td>'
          + '<td>' + (row.permanent ? '永久' : escapeText(fmtTime(row.expiresTs))) + '</td>'
          + '<td' + (row.active ? '' : ' class="vis-ua"') + '>' + escapeText(fmtRemaining(row)) + '</td>'
          + '<td class="vis-path" title="' + escapeText(row.reason) + '">' + escapeText(row.reason || '—') + '</td>'
          + '<td>' + escapeText(row.actor || '—') + '<br><small>' + escapeText(fmtTime(row.createdTs)) + '<br>' + escapeText(fmtTime(row.updatedTs)) + '</small></td>'
          + '<td class="ban-actions">' + action
          + ' <button type="button" class="guard-preset ban-edit" data-ip="' + escapeText(row.ip) + '">改期</button>'
          + ' <button type="button" class="guard-preset ban-events-btn" data-ip="' + escapeText(row.ip) + '">事件</button>'
          + '</td></tr>';
      }).join('');
    }
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function load() {
    if (!api()) return;
    if (dialog && dialog.open) return;
    var seq = (state.seq += 1);
    var query = {
      limit: PAGE_SIZE,
      offset: state.offset,
      include_inactive: els.includeInactive && els.includeInactive.checked ? 1 : 0
    };
    api().configured('ban.list', { query: query, force: true })
      .then(function (payload) {
        if (seq !== state.seq) return;
        state.items = payload.items || [];
        state.hasMore = payload.hasMore === true;
        state.counters = payload.counters || {};
        renderCounters();
        var focused = document.activeElement;
        var focusedIp = focused && focused.getAttribute('data-ip');
        var focusedAction = focused && (focused.classList.contains('ban-edit') ? 'ban-edit' : focused.classList.contains('ban-lift') ? 'ban-lift' : '');
        if (!dialog.open) renderRows();
        if (focusedIp && focusedAction && !dialog.open) {
          var replacement = Array.prototype.find.call(els.rows.querySelectorAll('.' + focusedAction), function (node) { return node.getAttribute('data-ip') === focusedIp; });
          (replacement || document.getElementById('ban-new')).focus({ preventScroll: true });
        }
        if (els.prev) els.prev.disabled = state.offset === 0;
        if (els.next) els.next.disabled = !state.hasMore;
        if (els.page) els.page.textContent = String(Math.floor(state.offset / PAGE_SIZE) + 1);
      })
      .catch(function (error) {
        if (seq !== state.seq || dialog.open) return;
        if (els.rows) {
          els.rows.innerHTML = '<tr><td class="table-empty" colspan="6"><i data-lucide="alert-triangle"></i><span>'
            + escapeText((error && error.message) || '加载失败') + '</span></td></tr>';
        }
        setStatus('加载失败', 'error');
      });
  }

  var editorRevision = 0;
  [els.ip, els.reason, els.preset, els.custom].forEach(function (node) {
    if (node) node.addEventListener('input', function () { editorRevision += 1; if (dialog.open && !els.submit.disabled) { setStatus('', ''); setWarning(''); } });
  });
  async function submitBan() {
    if (!api()) return;
    var ip = (els.ip && els.ip.value || '').trim();
    if (!ip) {
      setStatus('请先填写来源 IP', 'warn');
      if (els.ip) els.ip.focus();
      return;
    }
    var preset = (els.preset && els.preset.value) || '1h';
    var body = { ip: ip };
    if (preset === 'custom') {
      var minutes = num(els.custom && els.custom.value);
      if (!(minutes >= 1)) {
        setStatus('自定义时长必须大于 0 分钟', 'warn');
        return;
      }
      if (minutes > MAX_CUSTOM_MINUTES) {
        setStatus('自定义时长最多 ' + MAX_CUSTOM_MINUTES + ' 分钟（365 天）', 'warn');
        return;
      }
      body.seconds = Math.round(minutes * 60);
    } else {
      body.preset = preset;
    }
    var reason = (els.reason && els.reason.value || '').trim();
    if (reason) body.reason = reason;
    var durationLabel = preset === 'custom' ? String(minutes) + ' 分钟' : String(els.preset.options[els.preset.selectedIndex].text);
    if (els.submit && els.submit.disabled) return;
    var question = '确认封禁 ' + ip + '，时长：' + durationLabel + '？';
    var detail = '现有连接将断开。如果这是你当前的来源，你也会失去访问权限；可在服务器执行 ./deploy.sh --unban ' + ip + ' 恢复。';
    if (els.submit) els.submit.disabled = true;
    var accepted = dialog && dialog.open ? true : window.ScrcpyGateSecurity
      ? await window.ScrcpyGateSecurity.confirm({ title: '确认 IP 封禁', message: question, detail: detail, accept: '确认并封禁' })
      : window.confirm(tr(question + detail));
    if (!accepted) { if (els.submit) els.submit.disabled = false; return; }
    body.confirmSelfBan = true;
    var submittedRevision = editorRevision;
    if (els.submit) els.submit.disabled = true;
    setStatus('提交中…', '');
    api().configured('ban.create', { method: 'POST', body: body })
      .then(function (result) {
        var closed = num(result.closedConnections);
        setStatus('已封禁 ' + ip + (closed ? '，并断开已有连接 ' + closed + ' 条' : ''), 'ok');
        setWarning(result.selfBan
          ? '注意：你封禁的是当前自己的来源地址 ' + ip + '，本页面马上也会被拒绝。请用服务器上的 ./deploy.sh --unban ' + ip + ' 解除。'
          : '');
        if (submittedRevision === editorRevision) {
          if (els.ip) els.ip.value = '';
          if (els.reason) els.reason.value = '';
          closeEditor();
        }
        document.dispatchEvent(new CustomEvent('scrcpygate:ban-changed', { detail: { ip: ip, selfBan: result.selfBan === true } }));
        if (!result.selfBan) load();
      })
      .catch(function (error) {
        var detail = (error && error.message) || '封禁失败';
        setStatus('封禁失败：' + detail, 'error');
        // 保护地址的判定在服务端（HTTP 400 + X-Ban-Error: protected_ip）。这里按文案兜底匹配：
        // 中英文两种提示都要认，否则「被拒绝但不说为什么」对管理员毫无帮助。
        setWarning(/protected|环回|本机|可信代理|禁止封禁|loopback|trusted proxy|cannot be banned/i.test(detail)
          ? '该地址是环回、本机或可信代理地址，封禁它会连健康探针或反向代理一起挡掉，因此被拒绝。'
          : '');
      })
      .then(function () { if (els.submit) els.submit.disabled = false; });
  }

  function lift(ip) {
    if (!api()) return;
    setStatus('解除中…', '');
    api().configured('ban.lift', { method: 'DELETE', body: { ip: ip } })
      .then(function () { setStatus('已解除 ' + ip, 'ok'); load(); })
      .catch(function (error) { setStatus('解除失败：' + ((error && error.message) || '未知错误'), 'error'); });
  }

  function showEvents(ip) {
    if (!api()) return;
    api().configured('ban.events', { query: { ip: ip, limit: 50 }, force: true })
      .then(function (payload) {
        if (els.eventsTitle) els.eventsTitle.textContent = '事件 · ' + ip;
        if (els.eventsList) {
          var items = payload.items || [];
          els.eventsList.innerHTML = items.length
            ? items.map(function (item) {
              return '<li class="guard-locked-row"><span class="guard-locked-ip">' + escapeText(fmtTime(item.ts)) + '</span>'
                + '<span class="guard-locked-meta">' + escapeText(item.action)
                + (item.actor ? ' · ' + escapeText(item.actor) : '') + '</span></li>';
            }).join('')
            : '<li class="table-empty"><i data-lucide="inbox"></i><span>没有事件</span></li>';
        }
        if (els.events) els.events.hidden = false;
        if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
      })
      .catch(function (error) { setStatus('事件加载失败：' + ((error && error.message) || '未知错误'), 'error'); });
  }

  function openEditor(ip, options) {
    if (!dialog || dialog.open) return;
    editorRevision += 1;
    fromAccess = !!(options && options.fromAccess);
    dialogOrigin = document.activeElement;
    var editor = document.getElementById('ban-editor');
    if (editor) editor.open = true;
    if (els.ip) { els.ip.value = ip; els.ip.readOnly = fromAccess; }
    var row = options && options.row || state.items.find(function (item) { return item.ip === ip; });
    if (els.submit) { els.submit.disabled = false; els.submit.textContent = '确认并封禁'; }
    document.getElementById('ban-dialog-title').textContent = row && row.active ? '调整封禁期限' : '封禁 IP';
    if (els.reason) els.reason.value = row ? row.reason || '' : '';
    if (els.preset) els.preset.value = row && row.permanent ? 'permanent' : row && row.active ? 'custom' : '1h';
    if (els.customWrap) els.customWrap.hidden = els.preset.value !== 'custom';
    if (els.custom) els.custom.value = String(row && row.active && !row.permanent ? Math.max(1, Math.ceil(row.remainingSeconds / 60)) : 60);
    dialogStatus.textContent = '';
    dialogWarning.hidden = true;
    dialog.showModal();
    (fromAccess ? els.preset : els.ip).focus();
  }
  function closeEditor() {
    if (dialog && dialog.open) dialog.close();
  }
  if (dialog) {
    dialog.addEventListener('cancel', function (event) { if (els.submit.disabled) event.preventDefault(); });
    dialog.addEventListener('close', function () {
      document.getElementById('ban-editor').open = false;
      var origin = dialogOrigin;
      if (origin && !origin.isConnected) {
        var targetIp = origin.getAttribute('data-ip');
        var action = fromAccess ? '.vis-ban-btn' : '.ban-edit';
        origin = Array.prototype.find.call(document.querySelectorAll(action), function (node) { return node.getAttribute('data-ip') === targetIp; });
      }
      (origin || document.getElementById(fromAccess ? 'vis-refresh' : 'ban-new')).focus({ preventScroll: true });
      fromAccess = false;
    });
  }
  var newButton = document.getElementById('ban-new');
  var cancelButton = document.getElementById('ban-cancel');
  var editorNode = document.getElementById('ban-editor');
  if (editorNode) editorNode.addEventListener('toggle', function () {
    if (newButton) newButton.setAttribute('aria-expanded', String(editorNode.open));
  });
  if (newButton) newButton.addEventListener('click', function () {
    openEditor('');
    if (els.preset) els.preset.value = '1h';
    if (els.customWrap) els.customWrap.hidden = true;
  });
  if (cancelButton) cancelButton.addEventListener('click', function () { if (!els.submit.disabled) closeEditor(); });
  var accessOpenSeq = 0;
  document.addEventListener('scrcpygate:ban-edit', function (event) {
    var ip = String(event.detail && event.detail.ip || '');
    var seq = ++accessOpenSeq;
    // Load the canonical row: access summaries intentionally omit the ban reason.
    api().configured('ban.list', { query: { ip: ip, include_inactive: 1 }, force: true }).then(function (payload) {
      if (seq !== accessOpenSeq) return;
      openEditor(ip, { fromAccess: true, row: (payload.items || [])[0] || null });
    }).catch(function () {
      document.dispatchEvent(new CustomEvent('scrcpygate:ban-open-error'));
    });
  });
  if (els.prev) els.prev.addEventListener('click', function () { state.offset = Math.max(0, state.offset - PAGE_SIZE); load(); });
  if (els.next) els.next.addEventListener('click', function () { if (state.hasMore) { state.offset += PAGE_SIZE; load(); } });

  if (els.submit) els.submit.addEventListener('click', submitBan);
  if (els.ip) els.ip.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); submitBan(); }
  });
  if (els.preset) els.preset.addEventListener('change', function () {
    if (els.customWrap) els.customWrap.hidden = els.preset.value !== 'custom';
  });
  if (els.refresh) els.refresh.addEventListener('click', function () { state.offset = 0; load(); });
  if (els.includeInactive) els.includeInactive.addEventListener('change', function () { state.offset = 0; load(); });
  if (els.rows) els.rows.addEventListener('click', async function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var liftBtn = target.closest('.ban-lift');
    if (liftBtn) {
      var ip = liftBtn.getAttribute('data-ip') || '';
      if (ip) {
        var message = '确认解除 ' + ip + ' 的封禁？该地址将立即可以访问。';
        var accepted = window.ScrcpyGateSecurity
          ? await window.ScrcpyGateSecurity.confirm({ title: '解除 IP 封禁', message: message, accept: '解除封禁' })
          : window.confirm(tr(message));
        if (accepted) lift(ip);
      }
      return;
    }
    var editBtn = target.closest('.ban-edit');
    if (editBtn) { openEditor(editBtn.getAttribute('data-ip') || ''); return; }
    var eventsBtn = target.closest('.ban-events-btn');
    if (eventsBtn) showEvents(eventsBtn.getAttribute('data-ip') || '');
  });
  if (els.eventsClose) els.eventsClose.addEventListener('click', function () { if (els.events) els.events.hidden = true; });

  load();
  // 封禁状态会随时间变化（到期/别人封禁），30 秒安静刷新一次，不动输入框。
  window.setInterval(function () {
    if (document.hidden) return;
    load();
  }, 30000);
})();
