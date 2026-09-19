/* 访问记录面板（VIS）：/security 页上的「谁访问过本站」视图。
   - 采集开关与保留时长直接改设置（access_log_enabled / access_retention_days）。
   - 列表是「按来源 IP 汇总」，点明细展开该地址的逐条记录（keyset 分页）。
   - 导出只取 JSON 分支，再由客户端合成 CSV/JSON 文件（见 v2-adapter 的说明）。
   - 采样丢记录时明确显示「记录不完整」，绝不把采样值当作总量。
   端点：access.summary / access.records / access.status / access.export / admin.settings.update。 */
(function () {
  var root = document.querySelector('.vis-card');
  if (!root) return;

  var els = {
    health: document.getElementById('vis-health'),
    status: document.getElementById('vis-status'),
    note: document.getElementById('vis-note'),
    enabled: document.getElementById('vis-enabled'),
    retention: document.getElementById('vis-retention'),
    stats: document.getElementById('vis-stats'),
    hints: document.getElementById('vis-hints'),
    hintsList: document.getElementById('vis-hints-list'),
    hintsNote: document.getElementById('vis-hints-note'),
    filterIp: document.getElementById('vis-filter-ip'),
    filterCountry: document.getElementById('vis-filter-country'),
    filterCode: document.getElementById('vis-filter-code'),
    filterDecision: document.getElementById('vis-filter-decision'),
    filterBan: document.getElementById('vis-filter-ban'),
    filterStatus: document.getElementById('vis-filter-status'),
    filterIdentity: document.getElementById('vis-filter-identity'),
    filterKind: document.getElementById('vis-filter-kind'),
    filterWindow: document.getElementById('vis-filter-window'),
    filterAdminPoll: document.getElementById('vis-filter-admin-poll'),
    apply: document.getElementById('vis-apply'),
    refresh: document.getElementById('vis-refresh'),
    exportCsv: document.getElementById('vis-export-csv'),
    exportJson: document.getElementById('vis-export-json'),
    rows: document.getElementById('vis-ip-rows'),
    more: document.getElementById('vis-more'),
    detail: document.getElementById('vis-detail'),
    detailTitle: document.getElementById('vis-detail-title'),
    detailRows: document.getElementById('vis-detail-rows'),
    detailMore: document.getElementById('vis-detail-more'),
    detailClose: document.getElementById('vis-detail-close')
  };

  var PAGE_SIZE = 25;
  var DETAIL_PAGE_SIZE = 50;
  var state = {
    summary: null,
    filterSnapshot: null,
    offset: 0,
    records: [],
    nextBeforeId: null,
    detailIp: '',
    detailHasMore: false,
    loading: false,
    // 连点筛选/开关会并发发出请求：只让最后一次请求的结果落到界面上，
    // 否则先发后到的旧响应会把新筛选的结果覆盖掉。
    summarySeq: 0,
    detailSeq: 0
  };

  function api() { return window.ScrcpyGateApi; }

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

  function fmtDuration(ms) {
    var value = num(ms);
    if (value <= 0) return '—';
    return value >= 1000 ? (value / 1000).toFixed(1) + ' s' : Math.round(value) + ' ms';
  }

  function readFilters() {
    var seconds = num(els.filterWindow && els.filterWindow.value) || 86400;
    var now = Math.floor(Date.now() / 1000);
    return {
      country: (els.filterCountry && els.filterCountry.value || '').trim().toUpperCase(),
      status: (els.filterCode && els.filterCode.value || '').trim(),
      decision: (els.filterDecision && els.filterDecision.value) || '',
      ban_state: (els.filterBan && els.filterBan.value) || '',
      from_ts: now - seconds,
      to_ts: now,
      source_ip: (els.filterIp && els.filterIp.value || '').trim(),
      status_class: (els.filterStatus && els.filterStatus.value) || '',
      include_admin_poll: els.filterAdminPoll && els.filterAdminPoll.checked ? 1 : 0
    };
  }

  /* 身份与类型只有逐条明细能过滤（按 IP 汇总表里没有这两个维度），
     因此它们放在明细区，跟着明细请求一起发。 */
  function readDetailFilters() {
    return {
      identity: (els.filterIdentity && els.filterIdentity.value) || '',
      kind: (els.filterKind && els.filterKind.value) || ''
    };
  }

  function exportFilters() {
    var filters = state.filterSnapshot || readFilters();
    var detail = readDetailFilters();
    var body = { from_ts: filters.from_ts, to_ts: filters.to_ts };
    ['source_ip', 'country', 'status', 'decision', 'ban_state', 'status_class', 'identity', 'kind'].forEach(function (key) {
      var value = filters[key] || detail[key];
      if (value) body[key] = value;
    });
    if (filters.include_admin_poll) body.include_admin_poll = 1;
    return body;
  }

  function dataOf(payload) {
    if (payload && payload.data && typeof payload.data === 'object') return payload.data;
    return payload || {};
  }

  function setNote(text, tone) {
    if (!els.note) return;
    els.note.textContent = text;
    els.note.setAttribute('data-tone', tone || '');
  }

  /* 操作反馈走标题栏的状态文字（和登录防护卡片一致）；观察说明文字
     （#vis-note）留给「采样/脱敏」这类长期信息，保存成功不会被下一次刷新覆盖。 */
  function setStatus(text, tone) {
    if (!els.status) return;
    els.status.textContent = text;
    els.status.setAttribute('data-tone', tone || '');
  }

  function renderHealth() {
    if (!els.health) return;
    var observation = (state.summary && state.summary.observation) || {};
    var settings = (state.summary && state.summary.settings) || {};
    var enabled = observation.enabled !== undefined ? observation.enabled : settings.accessLogEnabled !== false;
    var sampled = observation.sampled === true;
    var pieces = [enabled ? '记录中' : '已停止记录'];
    if (observation.retention_days === 0) pieces.push('不清理');
    else if (observation.retention_days) pieces.push('保留 ' + observation.retention_days + ' 天');
    if (observation.row_cap) pieces.push('上限 ' + Number(observation.row_cap).toLocaleString() + ' 行');
    if (sampled) pieces.push('记录不完整');
    els.health.textContent = pieces.join(' · ');
    els.health.setAttribute('data-tone', sampled ? 'warn' : '');

    var notes = ['记录在写入前已脱敏：不含查询参数、正文、Cookie 与 Referer；路径中的长随机串会被折叠。'];
    if (sampled) {
      notes.push('队列压力过大时停止了部分记录（累计丢弃 ' + num(observation.dropped_total).toLocaleString()
        + ' 条），下面的统计只代表已记录的部分，不是完整总量。');
    }
    if (observation.last_drop_ts) notes.push('最近一次丢弃：' + fmtTime(observation.last_drop_ts) + '。');
    setNote(notes.join(' '), sampled ? 'warn' : '');
  }

  function renderSettings() {
    var settings = (state.summary && state.summary.settings) || {};
    var observation = (state.summary && state.summary.observation) || {};
    if (els.enabled) {
      els.enabled.setAttribute('aria-checked', settings.accessLogEnabled === false ? 'false' : 'true');
    }
    if (els.retention) {
      var days = settings.accessRetentionDays;
      if (days === undefined || days === null) days = observation.retention_days;
      els.retention.value = String(days === undefined || days === null ? 7 : days);
    }
  }

  function renderStats() {
    if (!els.stats) return;
    var stats = (state.summary && state.summary.stats) || {};
    var items = [
      ['已保留请求', num(stats.requests)],
      ['独立来源', num(stats.distinct_ips)],
      ['4xx', num(stats.errors_4xx)],
      ['5xx', num(stats.errors_5xx)],
      ['被拒绝', num(stats.denied_ban) + num(stats.denied_geo)],
      ['健康检查（不计入访客）', num(stats.health_skipped)]
    ];
    els.stats.innerHTML = items.map(function (pair) {
      return '<li class="vis-stat"><b>' + escapeText(Number(pair[1]).toLocaleString()) + '</b><span>' + escapeText(pair[0]) + '</span></li>';
    }).join('');
  }

  function renderHints() {
    var hints = (state.summary && state.summary.hints) || null;
    if (!els.hints || !els.hintsList) return;
    var items = (hints && Array.isArray(hints.hints)) ? hints.hints : [];
    if (!items.length) {
      els.hints.hidden = true;
      els.hintsList.innerHTML = '';
      return;
    }
    els.hints.hidden = false;
    if (els.hintsNote) els.hintsNote.textContent = String(hints.note || '');
    els.hintsList.innerHTML = items.map(function (item) {
      var detail = item.detail || item.summary || '';
      return '<li class="vis-hint"><b>' + escapeText(item.source_ip || item.ip || '') + '</b>'
        + '<span>' + escapeText(item.label || item.kind || '') + '</span>'
        + '<small>' + escapeText(detail) + '</small></li>';
    }).join('');
  }

  function renderRows(append) {
    if (!els.rows) return;
    var items = (state.summary && state.summary.items) || [];
    if (!append) els.rows.innerHTML = '';
    if (!items.length && !append) {
      els.rows.innerHTML = '<tr><td class="table-empty" colspan="9"><i data-lucide="inbox"></i><span>该时间范围内没有访问记录</span></td></tr>';
      if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
      return;
    }
    var html = items.map(function (item) {
      var ip = String(item.source_ip || '');
      return '<tr class="vis-ip-row" data-ip="' + escapeText(ip) + '">'
        + '<td class="vis-ip">' + escapeText(ip) + '</td>'
        + '<td>' + escapeText(item.current_country || (item.geo_lookup_status === 'private' ? '内网地址'
          : item.geo_lookup_status === 'unavailable' ? '地区库不可用'
            : item.geo_lookup_status ? '无法定位' : item.country || '—')) + '</td>'
        + '<td>' + num(item.requests).toLocaleString() + '</td>'
        + '<td' + (num(item.errors_4xx) ? ' class="vis-bad"' : '') + '>' + num(item.errors_4xx).toLocaleString() + '</td>'
        + '<td' + (num(item.errors_5xx) ? ' class="vis-bad"' : '') + '>' + num(item.errors_5xx).toLocaleString() + '</td>'
        + '<td>' + (num(item.denied_ban) + num(item.denied_geo)).toLocaleString() + '</td>'
        + '<td>' + escapeText(fmtTime(item.first_seen_ts)) + '</td>'
        + '<td>' + escapeText(fmtTime(item.last_ts || item.last_seen_ts)) + '</td>'
        + '<td class="vis-operations"><span class="vis-ban-state">' + escapeText(item.ban_active ? (item.ban_permanent ? '永久封禁' : '封禁至 ' + fmtTime(item.ban_expires_ts)) : '未封禁')
        + '</span><div class="vis-row-actions"><button type="button" class="guard-preset vis-detail-btn" aria-controls="vis-detail" aria-expanded="false" data-ip="' + escapeText(ip) + '"><i data-lucide="list" aria-hidden="true"></i>明细</button>'
        + '<button type="button" class="guard-preset vis-ban-btn" data-ip="' + escapeText(ip) + '">' + '<i data-lucide="shield-ban" aria-hidden="true"></i>' + (item.ban_active ? '改期' : '封禁') + '</button></div></td>'
        + '</tr>';
    }).join('');
    els.rows.insertAdjacentHTML(append ? 'beforeend' : 'afterbegin', html);
    if (!append) {
      // insertAdjacentHTML('afterbegin') 会把新行插到占位行之前，这里清掉占位。
      var placeholder = els.rows.querySelector('.table-empty');
      if (placeholder && els.rows.rows.length > 1) placeholder.closest('tr').remove();
    }
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function renderMore() {
    var page = (state.summary && state.summary.page) || {};
    if (!els.more) return;
    var hasMore = page.has_more === true || page.hasMore === true;
    els.more.hidden = !hasMore;
  }

  function loadSummary(reset) {
    if (!api()) return;
    if (reset !== false) {
      state.detailSeq += 1;
      state.offset = 0;
      state.filterSnapshot = readFilters();
      updateFilterFeedback(false);
      if (els.detail) els.detail.hidden = true;
    els.rows.querySelectorAll('.vis-detail-btn').forEach(function (node) { node.setAttribute('aria-expanded', 'false'); });
      state.detailIp = '';
      state.records = [];
      state.nextBeforeId = null;
    }
    state.loading = true;
    var seq = (state.summarySeq += 1);
    var filters = state.filterSnapshot || readFilters();
    var query = {
      from_ts: filters.from_ts,
      to_ts: filters.to_ts,
      limit: PAGE_SIZE,
      offset: state.offset,
      include_admin_poll: filters.include_admin_poll
    };
    ['source_ip', 'country', 'status', 'decision', 'ban_state'].forEach(function (key) { if (filters[key]) query[key] = filters[key]; });
    if (filters.status_class) query.status_class = filters.status_class;
    api().configured('access.summary', { query: query, force: true })
      .then(function (payload) {
        if (seq !== state.summarySeq) return;
        var data = dataOf(payload);
        var items = Array.isArray(data.items) ? data.items : [];
        state.summary = {
          items: reset === false ? ((state.summary && state.summary.items) || []).concat(items) : items,
          page: data.page || {},
          window: data.window || {},
          stats: data.stats || {},
          hints: data.hints || null,
          observation: data.observation || {},
          settings: data.settings || {}
        };
        renderHealth();
        renderSettings();
        renderStats();
        renderHints();
        var focused = document.activeElement;
        var focusedIp = focused && focused.getAttribute('data-ip');
        var focusedAction = focused && (focused.classList.contains('vis-ban-btn') ? 'vis-ban-btn' : focused.classList.contains('vis-detail-btn') ? 'vis-detail-btn' : '');
        renderRows(false);
        if (focusedIp && focusedAction) {
          var replacement = Array.prototype.find.call(els.rows.querySelectorAll('.' + focusedAction), function (node) { return node.getAttribute('data-ip') === focusedIp; });
          (replacement || els.refresh).focus({ preventScroll: true });
        }
        renderMore();
      })
      .catch(function (error) {
        if (seq !== state.summarySeq) return;
        if (els.rows) {
          els.rows.innerHTML = '<tr><td class="table-empty" colspan="9"><i data-lucide="alert-triangle"></i><span>'
            + escapeText((error && error.message) || '加载失败') + '</span></td></tr>';
        }
        setNote('加载访问记录失败：' + ((error && error.message) || '未知错误'), 'error');
      })
      .then(function () { state.loading = false; });
  }

  /* 身份由服务端会话确认，共三种取值（access_log.IDENTITY_*）：
     管理员 / 已登录账户 / 未登录。界面必须把管理员单独显示，
     否则「管理员」记录看起来会像未登录。 */
  function identityLabel(item) {
    var identity = String(item.identity || '');
    var account = String(item.account || '');
    if (identity === 'admin') return account ? ('管理员 · ' + account) : '管理员';
    if (identity === 'account') return account || '已登录账户';
    return '未登录';
  }

  function renderDetail(append) {
    if (!els.detailRows) return;
    if (!append) els.detailRows.innerHTML = '';
    if (!state.records.length && !append) {
      els.detailRows.innerHTML = '<tr><td class="table-empty" colspan="7"><i data-lucide="inbox"></i><span>没有明细记录</span></td></tr>';
    } else {
      var html = state.records.map(function (item) {
        return '<tr>'
          + '<td>' + escapeText(fmtTime(item.ts)) + '</td>'
          + '<td>' + escapeText(item.method || '') + '</td>'
          + '<td class="vis-path" title="' + escapeText(item.route_template || item.path_sample || '') + '">'
          + escapeText(item.route_template || item.path_sample || '') + '</td>'
          + '<td' + (num(item.status) >= 400 ? ' class="vis-bad"' : '') + '>' + escapeText(item.status) + '</td>'
          + '<td>' + escapeText(identityLabel(item)) + '</td>'
          + '<td>' + escapeText(fmtDuration(item.duration_ms)) + '</td>'
          + '<td class="vis-ua" title="' + escapeText(item.user_agent || '') + '">' + escapeText(item.user_agent || '—') + '</td>'
          + '</tr>';
      }).join('');
      els.detailRows.insertAdjacentHTML(append ? 'beforeend' : 'afterbegin', html);
      if (!append) {
        var placeholder = els.detailRows.querySelector('.table-empty');
        if (placeholder && els.detailRows.rows.length > 1) placeholder.closest('tr').remove();
      }
    }
    if (els.detailMore) els.detailMore.hidden = !state.detailHasMore;
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function loadDetail(ip, append) {
    if (!api()) return;
    if (!append) {
      state.records = [];
      state.nextBeforeId = null;
    }
    var seq = (state.detailSeq += 1);
    var filters = state.filterSnapshot || readFilters();
    var detail = readDetailFilters();
    var query = {
      from_ts: filters.from_ts,
      to_ts: filters.to_ts,
      limit: DETAIL_PAGE_SIZE,
      source_ip: ip,
      include_admin_poll: filters.include_admin_poll
    };
    ['country', 'status', 'decision', 'ban_state', 'status_class'].forEach(function (key) { if (filters[key]) query[key] = filters[key]; });
    if (detail.identity) query.identity = detail.identity;
    if (detail.kind) query.kind = detail.kind;
    if (state.nextBeforeId) query.before_id = state.nextBeforeId;
    api().configured('access.records', { query: query, force: true })
      .then(function (payload) {
        if (seq !== state.detailSeq) return;
        var data = dataOf(payload);
        var items = Array.isArray(data.items) ? data.items : [];
        state.records = append ? state.records.concat(items) : items;
        var page = data.page || {};
        var nextBefore = page.next_before_id !== undefined ? page.next_before_id : page.nextBeforeId;
        state.nextBeforeId = nextBefore === undefined ? null : nextBefore;
        state.detailHasMore = page.has_more === true || page.hasMore === true;
        if (els.detailTitle) els.detailTitle.textContent = '明细 · ' + ip + '（' + state.records.length + ' 条已加载）';
        if (els.detail) els.detail.hidden = false;
        renderDetail(false);
      })
      .catch(function (error) {
        if (seq !== state.detailSeq) return;
        if (els.detailRows) {
          els.detailRows.innerHTML = '<tr><td class="table-empty" colspan="7"><i data-lucide="alert-triangle"></i><span>'
            + escapeText((error && error.message) || '加载失败') + '</span></td></tr>';
        }
      });
  }

  /* 设置走 access.settings.update（内部 PUT /api/admin/settings），字段名是 camelCase；
     写成下划线会被服务端当成未知字段而整条请求失败。保存后用服务端回显刷新卡片，
     不乐观地假设设置已经生效。 */
  function saveSettings(body, doneText) {
    if (!api()) return;
    api().configured('access.settings.update', { method: 'PUT', body: body })
      .then(function () { setStatus(doneText, 'ok'); loadSummary(true); })
      .catch(function (error) { setStatus('保存失败：' + ((error && error.message) || '未知错误'), 'error'); });
  }

  /* 下载：content/filename 由适配器给出，用 Blob 触发，不依赖服务端附件响应。 */
  function download(payload, fallbackName) {
    var data = dataOf(payload);
    var content = typeof data.content === 'string' ? data.content : '';
    if (!content) return false;
    var blob = new Blob([content], { type: /\.json$/.test(data.filename || '') ? 'application/json' : 'text/csv' });
    var url = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = url;
    link.download = data.filename || fallbackName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    if (data.truncated) setNote('导出的记录达到上限，只包含最新的部分；缩小时间范围可导出全部。', 'warn');
    return true;
  }

  function doExport(format) {
    if (!api()) return;
    var body = exportFilters();
    body.format = format;
    api().configured('access.export', { method: 'POST', body: body })
      .then(function (payload) {
        var ok = download(payload, format === 'json' ? 'scrcpygate-access.json' : 'scrcpygate-access.csv');
        if (ok) setStatus('已导出当前筛选范围内的访问记录。', 'ok');
        else setStatus('导出内容为空，没有可下载的记录。', 'warn');
      })
      .catch(function (error) { setStatus('导出失败：' + ((error && error.message) || '未知错误'), 'error'); });
  }

  if (els.enabled) {
    els.enabled.addEventListener('click', function () {
      if (els.enabled.disabled) return;
      var next = els.enabled.getAttribute('aria-checked') !== 'true';
      els.enabled.setAttribute('aria-checked', next ? 'true' : 'false');
      saveSettings({ accessLogEnabled: next }, next ? '已开始记录访问。' : '已停止记录访问（已有记录保留到清理）。');
    });
    els.enabled.addEventListener('keydown', function (event) {
      if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); els.enabled.click(); }
    });
  }
  if (els.retention) {
    els.retention.addEventListener('change', function () {
      var days = num(els.retention.value);
      saveSettings({ accessRetentionDays: days }, days === 0 ? '已改为不按天清理记录。' : ('访问记录保留 ' + days + ' 天。'));
    });
  }
  if (els.rows) {
    els.rows.addEventListener('click', function (event) {
      var button = event.target && event.target.closest ? event.target.closest('.vis-detail-btn') : null;
      if (!button) return;
      var ip = button.getAttribute('data-ip') || '';
      if (!ip) return;
      state.detailIp = ip;
      if (els.detail) {
        els.detail.hidden = false;
        els.detailRows.innerHTML = '<tr><td class="table-empty" colspan="7">加载中…</td></tr>';
        els.detailTitle.textContent = '明细 · ' + ip;
        els.rows.querySelectorAll('.vis-detail-btn').forEach(function (node) { node.setAttribute('aria-expanded', String(node === button)); });
        var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!reduced && els.detail.animate) els.detail.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 220, easing: 'ease-out' });
        els.detailTitle.focus({ preventScroll: true });
        els.detail.scrollIntoView({ block: 'start', behavior: reduced ? 'instant' : 'smooth' });
      }
      loadDetail(ip, false);
    });
  }
  if (els.more) els.more.addEventListener('click', function () {
    if (state.loading) return;
    state.offset += PAGE_SIZE;
    loadSummary(false);
  });
  if (els.detailMore) els.detailMore.addEventListener('click', function () {
    if (state.detailIp) loadDetail(state.detailIp, true);
  });
  if (els.detailClose) els.detailClose.addEventListener('click', function () {
    state.detailSeq += 1;
    if (els.detail) els.detail.hidden = true;
    els.rows.querySelectorAll('.vis-detail-btn').forEach(function (node) { node.setAttribute('aria-expanded', 'false'); });
    if (els.rows) {
      var origin = Array.prototype.find.call(els.rows.querySelectorAll('.vis-detail-btn'), function (button) {
        return button.getAttribute('data-ip') === state.detailIp;
      });
      if (origin) origin.focus();
    }
  });
  var filterReset = document.getElementById('vis-reset-filters');
  var filterFeedback = document.getElementById('vis-filter-feedback');
  var allFilters = [els.filterIp, els.filterStatus, els.filterCountry, els.filterCode, els.filterDecision, els.filterBan, els.filterWindow, els.filterAdminPoll];
  function updateFilterFeedback(pending) {
    var active = allFilters.some(function (node) {
      return node && (node === els.filterAdminPoll ? node.checked : node === els.filterWindow ? node.value !== '86400' : !!node.value);
    });
    if (filterReset) filterReset.hidden = !active;
    if (filterFeedback) {
      filterFeedback.hidden = !active && !pending;
      filterFeedback.textContent = pending ? '筛选已修改，点击「应用筛选」更新结果。' : active ? '当前结果已应用筛选。' : '';
    }
  }
  allFilters.forEach(function (node) {
    if (!node) return;
    node.addEventListener('input', function () { updateFilterFeedback(true); });
    node.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); loadSummary(true); }
    });
  });
  if (filterReset) filterReset.addEventListener('click', function () {
    allFilters.forEach(function (node) {
      if (!node) return;
      if (node === els.filterAdminPoll) node.checked = false;
      else node.value = node === els.filterWindow ? '86400' : '';
    });
    loadSummary(true);
  });
  if (els.apply) els.apply.addEventListener('click', function () { loadSummary(true); });
  var detailApply = document.getElementById('vis-detail-apply');
  if (detailApply) detailApply.addEventListener('click', function () {
    if (state.detailIp) loadDetail(state.detailIp, false);
  });
  [els.filterIdentity, els.filterKind].forEach(function (node) {
    if (node) node.addEventListener('change', function () {
      if (state.detailIp) loadDetail(state.detailIp, false);
    });
  });
  if (els.refresh) els.refresh.addEventListener('click', function () {
    loadSummary(true);
    loadStatus();
  });
  if (els.exportCsv) els.exportCsv.addEventListener('click', function () { doExport('csv'); });
  if (els.exportJson) els.exportJson.addEventListener('click', function () { doExport('json'); });

  /* 观测健康：只在「写库失败」这类汇总视图覆盖不到的情况下改写说明文字。
     采样丢弃的详细说明由 renderHealth() 写在同一个节点上，这里再写一次会把它
     覆盖成更短的一句（两个请求谁后到谁赢），因此采样情况直接跳过。 */
  function loadStatus() {
    if (!api()) return;
    api().configured('access.status', { force: true }).then(function (payload) {
      var data = dataOf(payload);
      if (data.degraded !== true) return;
      var writer = data.writer || {};
      if (data.dropped && data.dropped.sampled) return;
      if (num(writer.failures)) {
        setNote('观测处于降级状态：写库失败 ' + num(writer.failures) + ' 次，请检查数据库与磁盘。', 'warn');
      }
    }).catch(function () { /* 状态接口失败不影响主视图 */ });
  }

  document.addEventListener('scrcpygate:ban-open-error', function () { setStatus('无法读取封禁状态，请重试。', 'error'); });
  document.addEventListener('scrcpygate:ban-changed', function (event) {
    if (event.detail && event.detail.selfBan) {
      setStatus('已封禁当前来源。请在服务器执行 ./deploy.sh --unban IP 恢复访问。', 'warn');
    } else {
      setStatus('封禁已生效，访问记录已刷新。', 'ok');
      loadSummary(true);
    }
  });
  window.addEventListener('scrcpygate:geo-updated', function () { loadSummary(true); });
  loadSummary(true);
  loadStatus();
  if (els.rows) els.rows.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('.vis-ban-btn');
    if (button) {
      var ip = button.getAttribute('data-ip');
      var item = ((state.summary && state.summary.items) || []).find(function (row) { return row.source_ip === ip; });
      var row = item && { active: item.ban_active, permanent: item.ban_permanent, remainingSeconds: Math.max(60, Number(item.ban_expires_ts || 0) - Date.now() / 1000) };
      document.dispatchEvent(new CustomEvent('scrcpygate:ban-edit', { detail: { ip: ip, row: row } }));
    }
  });
})();
