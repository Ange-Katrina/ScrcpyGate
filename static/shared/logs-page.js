(function () {
  'use strict';
  if (window.lucide) window.lucide.createIcons();

  var PAGE_SIZE = 40;
  var LOGS = [];
  var AUDITS = [];
  var integrityData = { issues: [] };
  var autoTimer = null;
  var logsLoadSeq = 0;
  var auditLoadSeq = 0;
  var filterTimers = {};
  var currentLogIndex = 0;
  var auditDetailSequence = 0;
  var requestedEventId = '';
  var requestedEventOpened = false;
  try { requestedEventId = new URLSearchParams(window.location.search).get('event_id') || ''; } catch (_) {}
  var state = {
    activeTab: 'audit', viewMode: 'structured',
    kw: '', time: '24h', dateFrom: '', dateTo: '',
    severity: 'all', source: 'all', device: 'all', user: 'all',
    auditKw: '', auditTime: '24h', auditDateFrom: '', auditDateTo: '',
    eventType: 'all', result: 'all', actor: 'all', auditDevice: 'all', ip: '', target: '',
    rawWrap: true, autoOn: false
  };
  var page = {
    logs: { total: null, hasMore: false },
    audit: { total: null, hasMore: false, nextCursor: null }
  };
  var meta = { logsTotal: null, auditTotal: null, abnormal: null, auditSummary: {}, lastUpdated: '' };

  var SEV_LABEL = { debug:'调试', info:'信息', warn:'警告', warning:'警告', error:'错误', critical:'严重' };
  var SRC_LABEL = { auth:'认证', account:'账户', device:'设备', quality:'画质传输', alas:'ALAS', system:'系统' };
  var SRC_ICON = { auth:'key', account:'user', device:'smartphone', quality:'sliders-horizontal', alas:'bot', system:'settings' };
  var RES_LABEL = { success:'成功', fail:'失败', denied:'拒绝' };
  var CAT_LABEL = {
    login:'登录与退出审计', authfail:'认证失败', acct:'账户状态变更', perm:'用户权限调整',
    dev:'设备操作', quality:'画质与传输配置修改', alas:'ALAS 配置与运行操作', admin:'管理员操作'
  };
  // The dashboard and the audit page intentionally share one vocabulary. The
  // raw action/reason values remain on each row for filtering and diagnostics.
  var DASH = window.ScrcpyGateDashboard || {};
  var ADAPTER = window.ScrcpyGateV2 || {};
  function dashAction(item) {
    if (DASH.action) return DASH.action(item);
    var raw = item && (item.action || item.action_code || item.event || item.event_name || '');
    return DASH.canonicalAction ? DASH.canonicalAction(raw) : String(raw).trim().toLowerCase();
  }
  function dashResult(item) { return DASH.result ? DASH.result(item) : String(item && (item.result || item.outcome || item.status || 'unknown')).trim().toLowerCase(); }
  function dashDisplayResult(item) {
    return DASH.displayResult ? DASH.displayResult(item) : dashResult(item);
  }
  function dashResultLabel(item) {
    return DASH.resultLabel ? DASH.resultLabel(item) : (RES_LABEL[dashResult(item)] || dashResult(item) || tr('未知'));
  }
  function dashResultClass(item) { return DASH.resultClass ? DASH.resultClass(item) : (dashResult(item) === 'success' ? 'success' : (dashResult(item) === 'denied' ? 'denied' : 'fail')); }
  function dashSeverityLabel(item) { return DASH.severityLabel ? DASH.severityLabel(item) : (SEV_LABEL[String(item && (item.severity || item.level) || '').toLowerCase()] || '需关注'); }
  function dashTitle(item) {
    if (DASH.title) return DASH.title(item);
    if (ADAPTER.auditRecordTitle) return ADAPTER.auditRecordTitle(item);
    return item && (item.actionLabel || item.action || item.event) || '审计事件';
  }
  function dashMeta(item) { return DASH.meta ? DASH.meta(item) : ''; }
  function dashTarget(item) { return DASH.targetLabel ? DASH.targetLabel(item) : ''; }
  function dashCategoryLabel(item) {
    if (DASH.categoryLabel) return DASH.categoryLabel(item);
    if (ADAPTER.auditCategoryLabel) return ADAPTER.auditCategoryLabel(item);
    var key = String(item && (item.cat || item.category || item.type || '') || '').toLowerCase().replace(/[\s./:-]+/g, '_');
    var aliases = { auth:'authfail', authentication:'authfail', auth_failure:'authfail', security:'authfail', account:'acct', user:'acct', permission:'perm', device:'dev', mirror:'dev', video:'quality', transport:'quality', alas_runtime:'alas' };
    key = aliases[key] || key;
    return CAT_LABEL[key] || tr('管理员操作');
  }
  function dashReason(item) {
    if (DASH.reasonLabel) return DASH.reasonLabel(item);
    return ADAPTER.auditReasonLabel ? ADAPTER.auditReasonLabel(item) : '';
  }
  function dashReadable(value) {
    var text = String(value == null ? '' : value).trim();
    if (!text) return '';
    if (DASH.readableText) return DASH.readableText(text);
    if (ADAPTER.auditReadableText) return ADAPTER.auditReadableText(text);
    return opaqueAuditLabel(text) ? '' : text;
  }
  function dashRuntimeEventLabel(value) {
    if (DASH.runtimeEventLabel) return DASH.runtimeEventLabel(value);
    if (ADAPTER.runtimeEventLabel) return ADAPTER.runtimeEventLabel(value);
    return '';
  }
  function timeParts(value) {
    var source = value === null || value === undefined || value === '' ? '' : value;
    var full = DASH.formatTime ? DASH.formatTime(source) : (source ? String(source) : '—');
    var relative = DASH.timeLabel ? DASH.timeLabel(source) : full;
    var iso = DASH.dateTime ? DASH.dateTime(source) : '';
    return { value: source, full: full || '—', relative: relative || '—', iso: iso || '' };
  }
  function timeMarkup(row, className) {
    row = row || {};
    var parts = row.timeIso !== undefined ? {
      iso: row.timeIso || '',
      full: row.timeFull || row.t || '—',
      relative: row.timeLabel || row.t || '—'
    } : timeParts(row.timeValue !== undefined ? row.timeValue : row.t);
    var datetime = parts.iso ? ' datetime="' + esc(parts.iso) + '"' : '';
    return '<time class="' + esc(className || 'log-time') + '"' + datetime + ' title="' + esc(parts.full) + '">' + esc(parts.relative) + '</time>';
  }
  function opaqueAuditLabel(value) {
    var text = String(value == null ? '' : value).trim();
    return /^[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:成功|失败|错误|拒绝|阻止|超时)?$/i.test(text) ||
      /^[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:\s+(?:successfully|success|failed|failure|error|denied|blocked|timed\s+out))$/i.test(text);
  }
  function normalizedResultLabel(item, display) {
    var supplied = item.resultLabel || item.result_label || item.outcomeLabel || item.outcome_label || '';
    var mapped = dashResultLabel(display);
    // Prefer the stable result code mapping. Only fall back to a supplied
    // label when the producer did not provide a usable code (for example an
    // older endpoint that sent a localized label without ``outcome``).
    if (dashResult(display) !== 'unknown' && dashResult(display) !== '') return mapped;
    return supplied && !opaqueAuditLabel(supplied) ? supplied : mapped;
  }

  function $(id) { return document.getElementById(id); }
  function tr(s) { return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(String(s == null ? '' : s)) : String(s == null ? '' : s); }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  function fmt(n) { return n == null || n === '' || isNaN(Number(n)) ? '—' : Number(n).toLocaleString('en-US'); }
  function icons() { if (window.lucide) window.lucide.createIcons(); }
  function toggle(el, on) { if (el) el.classList.toggle('is-hidden', !on); }
  function dataObject(payload) { return payload && payload.data && typeof payload.data === 'object' && !Array.isArray(payload.data) ? payload.data : (payload || {}); }
  function payloadList(payload, key) {
    var d = dataObject(payload);
    if (Array.isArray(d)) return d;
    if (key && Array.isArray(d[key])) return d[key];
    if (Array.isArray(d.items)) return d.items;
    if (d.data && Array.isArray(d.data)) return d.data;
    if (d.data && key && Array.isArray(d.data[key])) return d.data[key];
    if (d.data && Array.isArray(d.data.items)) return d.data.items;
    return [];
  }
  function apiError(error) { return window.ScrcpyGateApi ? tr(window.ScrcpyGateApi.errorMessage(error)) : tr('数据服务不可用'); }
  function isConfigured(name) { return !!(window.ScrcpyGateApi && window.ScrcpyGateApi.isConfigured(name)); }
  function safeDownloadUrl(url) {
    return window.ScrcpyGateApi && window.ScrcpyGateApi.safeDownloadUrl ? window.ScrcpyGateApi.safeDownloadUrl(url) : '';
  }
  function totalOf(d, list) {
    var p = d.meta || d.pagination || {};
    var n = d.total;
    if (n == null) n = d.totalCount;
    if (n == null) n = d.count;
    if (n == null) n = p.total;
    if (n == null) n = p.totalCount;
    return n == null ? list.length : Number(n);
  }
  function hasMoreOf(d, offset, list, total) {
    var p = d.meta || d.pagination || {};
    if (d.hasMore != null) return !!d.hasMore;
    if (p.hasMore != null) return !!p.hasMore;
    return total != null && offset + list.length < total;
  }
  function objectValue(obj, keys) {
    if (!obj || typeof obj !== 'object') return '';
    for (var i = 0; i < keys.length; i++) {
      if (obj[keys[i]] !== undefined && obj[keys[i]] !== null && obj[keys[i]] !== '') return String(obj[keys[i]]);
    }
    return '';
  }
  function noneValue(v) { return v === undefined || v === null || v === '' || v === 'None' ? 'none' : String(v); }
  function noneLabel(v, label) { return v === undefined || v === null || v === '' || v === 'None' ? label : String(v); }

  function showToast(msg, type) {
    var root = $('toast-root');
    if (!root) return;
    var icon = type === 'success' ? 'check-circle' : (type === 'error' ? 'alert-circle' : 'info');
    var el = document.createElement('div');
    el.className = 'toast ' + (type || 'info');
    el.innerHTML = '<i data-lucide="' + icon + '"></i><span></span>';
    el.querySelector('span').textContent = tr(msg);
    root.appendChild(el);
    icons();
    requestAnimationFrame(function () { el.classList.add('show'); });
    setTimeout(function () { el.classList.remove('show'); setTimeout(function () { el.remove(); }, 240); }, 2600);
  }

  function copyText(txt, btn) {
    var done = function (ok) {
      var old = btn.innerHTML;
      btn.innerHTML = '<i data-lucide="' + (ok ? 'check' : 'copy') + '"></i>' + (ok ? tr('已复制') : tr('复制失败'));
      icons();
      setTimeout(function () { btn.innerHTML = old; icons(); }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { done(false); });
    } else {
      var ta = document.createElement('textarea');
      ta.value = txt;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(true); } catch (e) { done(false); }
      document.body.removeChild(ta);
    }
  }

  function normalizeLog(item) {
    item = item || {};
    var devObj = item.device && typeof item.device === 'object' ? item.device : null;
    var userObj = item.user && typeof item.user === 'object' ? item.user : null;
    var deviceLabel = item.deviceName || item.deviceLabel || objectValue(devObj, ['name','displayName','serial','id']) || (typeof item.device === 'string' ? item.device : '');
    var userLabel = item.username || item.userName || item.userLabel || objectValue(userObj, ['username','name','displayName','id']) || (typeof item.user === 'string' ? item.user : '');
    var source = String(item.source || item.module || item.category || 'system');
    var summary = item.summary || item.message || item.event || item.description || '';
    var eventName = item.event_name || item.eventName || item.event || '';
    var raw = item.raw || item.line || item.text || summary;
    var readableSummary = dashReadable(summary);
    var eventLabel = eventName ? dashRuntimeEventLabel(eventName) : '';
    // Runtime entries often carry only a machine event name (for example
    // ``http.request``). Show a stable human label in the table while keeping
    // the original line available to the detail view and raw tab.
    var displaySummary = readableSummary || eventLabel || summary;
    var timestamp = item.timeValue !== undefined ? item.timeValue : (item.time || item.createdAt || item.timestamp || item.datetime || '');
    var displayTime = timeParts(timestamp);
    return {
      t: displayTime.relative,
      timeValue: displayTime.value,
      timeLabel: displayTime.relative,
      timeFull: displayTime.full,
      timeIso: displayTime.iso,
      level: String(item.level || item.severity || 'info'),
      sourceLabel: item.sourceLabel || SRC_LABEL[source] || source,
      sourceIcon: item.sourceIcon || SRC_ICON[source] || 'file-text',
      device: noneValue(item.deviceId || objectValue(devObj, ['id']) || deviceLabel),
      deviceLabel: noneLabel(deviceLabel, '无设备'),
      user: noneValue(item.userId || objectValue(userObj, ['id']) || userLabel),
      userLabel: noneLabel(userLabel, '无用户'),
      ip: item.ip || item.ipAddress || item.remoteAddr || '—',
      sessionId: item.sessionId || item.session || '—',
      eventId: item.eventId || item.event_id || item.id || '—',
      summary: displaySummary || raw || '—',
      summaryRaw: summary || raw || '—',
      eventName: eventName,
      eventLabel: eventLabel,
      raw: raw || '—'
    };
  }
  function normalizeAudit(item) {
    item = item || {};
    var actorObj = item.actor && typeof item.actor === 'object' ? item.actor : null;
    var actorLabel = item.username || item.user_name || item.operator || item.operatorLabel || item.actorName || item.actorLabel || objectValue(actorObj, ['username','name','displayName','id']) || (typeof item.actor === 'string' ? item.actor : '');
    var actionValue = item.action || item.action_code || item.event || item.event_name || item.actionLabel || item.action_label || '';
    var catValue = item.cat || item.category || item.type || item.eventType || '';
    var rawResult = item.result || item.status || item.outcome || item.result_code || item.outcome_code || '';
    var desc = item.desc || item.description || item.message || item.summary || '';
    var raw = item.raw || item.line || item.text || desc;
    var severity = String(item.severity || item.level || 'info').toLowerCase();
    if (severity === 'warning') severity = 'warn';
    // Alert projections from older servers occasionally omit outcome. An
    // error-level row must never be presented as a successful activity.
    var result = rawResult || ((severity === 'warn' || severity === 'error' || severity === 'critical') ? 'failure' : 'unknown');
    var display = Object.assign({}, item, {
      action: actionValue,
      action_code: actionValue,
      result: result,
      outcome: result,
      severity: severity,
      level: severity,
      operator: actorLabel,
      actorName: actorLabel,
      username: actorLabel,
      target: item.target || item.targetId || item.target_id || item.object || item.resource || '',
      category: catValue,
      isAlert: (DASH.isAlertRecord ? DASH.isAlertRecord(item) : !!(item.alertType || item.alert_type || item.isAlert || item.kind === 'alert')) ||
        severity === 'warn' || severity === 'error' || severity === 'critical' ||
        ['fail', 'failure', 'error', 'denied', 'blocked', 'timeout'].indexOf(String(result).toLowerCase()) >= 0
    });
    var isAlert = display.isAlert === true;
    var actionCode = dashAction(display);
    var mappedTitle = dashTitle(display);
    // Alert rows must use the problem/reason title from the shared mapper;
    // otherwise a legacy ``actionLabel`` such as "开始投屏" hides the actual
    // incident (for example "设备离线"). Activity rows use the mapped action
    // title, with the result rendered separately below.
    // The mapper also strips a legacy outcome suffix from activity labels, so
    // the result chip remains the single source of truth for success/failure.
    var normalizedTitle = mappedTitle || '审计事件';
    var mappedResult = dashDisplayResult(display);
    var mappedSeverity = DASH.severity ? DASH.severity(display) : severity;
    var reasonLabel = dashReason(display);
    var visibleDesc = desc && !opaqueAuditLabel(desc) ? desc : (reasonLabel || mappedTitle);
    var categoryLabel = dashCategoryLabel(display);
    var eventId = item.eventId || item.event_id || item.id || '—';
    var timestamp = item.timeValue !== undefined ? item.timeValue : (item.ts != null ? item.ts : (item.time || item.createdAt || item.timestamp || item.datetime || ''));
    var displayTime = timeParts(timestamp);
    return {
      t: displayTime.relative,
      timeValue: displayTime.value,
      timeLabel: displayTime.relative,
      timeFull: displayTime.full,
      timeIso: displayTime.iso,
      ts: item.ts != null ? item.ts : item.timestamp || '',
      operator: noneValue(item.operatorId || item.operator_id || objectValue(actorObj, ['id']) || actorLabel),
      operatorLabel: noneLabel(actorLabel, '未认证用户'),
      actorRole: item.actorRole || item.actor_role || '',
      ip: item.sourceIp || item.ip || item.ipAddress || item.remoteAddr || '—',
      cat: DASH.category ? DASH.category(display) : (catValue || 'admin'),
      catLabel: categoryLabel,
      action: actionValue,
      actionCode: actionCode,
      actionLabel: normalizedTitle || mappedTitle || ADAPTER.auditRecordTitle && ADAPTER.auditRecordTitle(display) || '审计事件',
      target: item.target || item.targetId || item.object || item.resource || '—',
      targetType: item.targetType || item.target_type || '',
      targetId: item.targetId || item.target_id || item.target || '',
      result: mappedResult || result,
      rawResult: result,
      resultLabel: normalizedResultLabel(item, display),
      severity: mappedSeverity || severity,
      severityLabel: item.severityLabel || dashSeverityLabel(display),
      requestId: item.requestId || item.request_id || item.sessionId || item.session || '—',
      sessionId: item.requestId || item.request_id || item.sessionId || item.session || '—',
      eventId: eventId,
      reason: item.reason || '',
      reasonLabel: reasonLabel,
      desc: visibleDesc || raw || '—',
      userAgent: item.userAgent || item.user_agent || '',
      schemaVersion: item.schemaVersion || item.schema_version || '',
      metadata: item.metadata && typeof item.metadata === 'object' ? item.metadata : {},
      sequenceId: item.sequenceId || item.sequence_id || '',
      previousHash: item.previousHash || item.previous_hash || '',
      eventHash: item.eventHash || item.event_hash || '',
      dedupeKey: item.dedupeKey || item.dedupe_key || null,
      displayMeta: dashMeta(display),
      displayTarget: dashTarget(display),
      summaryRaw: desc || '',
      displaySummary: visibleDesc || '—',
      displayDetail: visibleDesc || '—',
      raw: raw || '—',
      rawAction: actionValue || '',
      rawTimestamp: timestamp,
      isAlert: isAlert
    };
  }

  function optionList(raw, valueKey, labelKey) {
    return (Array.isArray(raw) ? raw : []).map(function (x) {
      if (x && typeof x === 'object') return { value:x[valueKey] || x.id || x.value || x.key || x.name || x.label, label:x[labelKey] || x.label || x.name || x.displayName || x.value || x.key || x.id };
      return { value:x, label:x };
    }).filter(function (x) { return x.value !== undefined && x.value !== null && x.value !== ''; });
  }
  function uniqueOptions(rows, valueKey, labelKey) {
    var seen = {};
    var out = [];
    rows.forEach(function (row) {
      var v = row[valueKey];
      if (!v || v === 'none' || seen[v]) return;
      seen[v] = true;
      out.push({ value:v, label:row[labelKey] || v });
    });
    return out;
  }
  function setSelectOptions(id, allLabel, noneText, options) {
    var sel = $(id);
    if (!sel) return;
    var current = sel.value || 'all';
    var currentLabel = sel.selectedOptions.length ? sel.selectedOptions[0].textContent : current;
    var seen = {};
    var html = '<option value="all">' + esc(tr(allLabel)) + '</option>';
    if (noneText) html += '<option value="none">' + esc(tr(noneText)) + '</option>';
    options.forEach(function (opt) {
      var value = noneValue(opt.value);
      if (value === 'all' || value === 'none' || seen[value]) return;
      seen[value] = true;
      html += '<option value="' + esc(value) + '">' + esc(opt.label || value) + '</option>';
    });
    if (current !== 'all' && current !== 'none' && !seen[current]) {
      html += '<option value="' + esc(current) + '">' + esc(currentLabel) + '</option>';
    }
    sel.innerHTML = html;
    sel.value = Array.prototype.some.call(sel.options, function (o) { return o.value === current; }) ? current : 'all';
  }
  function updateFacets(d) {
    var facets = d.facets || d.filters || {};
    if (facets.actors) {
      setSelectOptions('logs-actor', '全部操作者', '未认证用户', optionList(facets.actors, 'id', 'username').concat(uniqueOptions(AUDITS, 'operator', 'operatorLabel')));
      setSelectOptions('audit-device', '全部设备', '', optionList(facets.devices, 'id', 'name'));
      return;
    }
    setSelectOptions('logs-device', '全部设备', '无设备', optionList(facets.devices || d.devices, 'id', 'name').concat(uniqueOptions(LOGS, 'device', 'deviceLabel')));
    setSelectOptions('logs-user', '全部用户', '无用户', optionList(facets.users || d.users, 'id', 'username').concat(uniqueOptions(LOGS, 'user', 'userLabel')));
  }

  function putIf(query, key, value) {
    if (value !== undefined && value !== null && value !== '' && value !== 'all') query[key] = value;
  }
  function logQuery(offset) {
    var query = { limit:PAGE_SIZE, offset:offset || 0 };
    putIf(query, 'q', state.kw.trim());
    putIf(query, 'timeRange', state.time);
    putIf(query, 'from', state.dateFrom);
    putIf(query, 'to', state.dateTo);
    putIf(query, 'severity', state.severity);
    putIf(query, 'source', state.source);
    putIf(query, 'device', state.device);
    putIf(query, 'user', state.user);
    return query;
  }
  function auditQuery(cursor) {
    var query = { limit:PAGE_SIZE };
    if (cursor !== undefined && cursor !== null && cursor !== '') query.before = cursor;
    putIf(query, 'q', state.auditKw.trim());
    putIf(query, 'timeRange', state.auditTime);
    putIf(query, 'from', state.auditDateFrom);
    putIf(query, 'to', state.auditDateTo);
    putIf(query, 'eventType', state.eventType);
    putIf(query, 'result', state.result);
    putIf(query, 'actor', state.actor);
    putIf(query, 'device', state.auditDevice);
    putIf(query, 'ip', state.ip.trim());
    putIf(query, 'target', state.target.trim());
    return query;
  }

  function setLoading(on) {
    var btn = $('refresh-btn');
    if (btn) {
      btn.classList.toggle('loading', !!on);
      btn.disabled = !!on;
    }
  }
  function setUnavailable(kind, message) {
    var msg = tr(message);
    if (kind === 'logs') {
      LOGS = [];
      $('logs-tbody').innerHTML = '';
      $('logs-empty').querySelector('span').textContent = msg;
      toggle($('logs-empty'), true);
    } else {
      AUDITS = [];
      $('audit-tbody').innerHTML = '';
      $('audit-raw-list').innerHTML = '';
      $('audit-empty').querySelector('span').textContent = msg;
      toggle($('audit-empty'), true);
    }
    $('logs-down-banner').classList.remove('is-hidden');
    $('logs-down-banner').querySelector('.banner-msg').textContent = msg;
    renderStats();
    renderFooter();
    icons();
  }

  function loadLogs(options) {
    options = options || {};
    var requestSeq = ++logsLoadSeq;
    if (!isConfigured('logs.list')) {
      setUnavailable('logs', '接口尚未配置：logs.list');
      return Promise.reject(new Error('logs.list unavailable'));
    }
    var offset = options.append ? LOGS.length : 0;
    setLoading(!options.silent);
    return window.ScrcpyGateApi.configured('logs.list', { method:'GET', query:logQuery(offset) }).then(function (payload) {
      if (requestSeq !== logsLoadSeq) return payload;
      var d = dataObject(payload);
      var list = payloadList(payload, 'logs').map(normalizeLog);
      LOGS = options.append ? LOGS.concat(list) : list;
      page.logs.total = totalOf(d, LOGS);
      page.logs.hasMore = hasMoreOf(d, offset, list, page.logs.total);
      meta.logsTotal = d.totalAll || d.totalRecords || d.logsTotal || page.logs.total;
      meta.lastUpdated = d.updatedAt || d.lastUpdated || new Date().toLocaleString();
      updateFacets(d);
      $('logs-fail-banner').classList.add('is-hidden');
      $('logs-down-banner').classList.add('is-hidden');
      renderAll();
      if (options.show) showToast('日志已刷新', 'success');
      return payload;
    }).catch(function (error) {
      if (requestSeq !== logsLoadSeq) return;
      if (!options.silent) {
        $('logs-fail-banner').classList.remove('is-hidden');
        $('logs-fail-banner').querySelector('.banner-msg').textContent = apiError(error);
        showToast(apiError(error), 'error');
      }
      throw error;
    }).finally(function () { if (requestSeq === logsLoadSeq) setLoading(false); });
  }
  function loadAudits(options) {
    options = options || {};
    var requestSeq = ++auditLoadSeq;
    if (!isConfigured('logs.audit')) {
      setUnavailable('audit', '接口尚未配置：logs.audit');
      return Promise.reject(new Error('logs.audit unavailable'));
    }
    var cursor = options.append ? page.audit.nextCursor : null;
    setLoading(!options.silent);
    return window.ScrcpyGateApi.configured('logs.audit', { method:'GET', query:auditQuery(cursor) }).then(function (payload) {
      if (requestSeq !== auditLoadSeq) return payload;
      var d = dataObject(payload);
      var list = payloadList(payload, 'audits').map(normalizeAudit);
      AUDITS = options.append ? AUDITS.concat(list) : list;
      page.audit.total = AUDITS.length;
      page.audit.hasMore = !!d.hasMore;
      page.audit.nextCursor = d.nextCursor == null ? null : d.nextCursor;
      meta.auditTotal = d.totalAll || d.totalRecords || d.auditTotal || page.audit.total;
      meta.auditSummary = d.summary || meta.auditSummary || {};
      meta.abnormal = d.abnormalCount == null ? Number(meta.auditSummary.high_risk || 0) : Number(d.abnormalCount);
      meta.lastUpdated = d.updatedAt || d.lastUpdated || meta.lastUpdated || new Date().toLocaleString();
      updateFacets(d);
      renderAll();
      openRequestedEvent();
      if (options.show) showToast('审计记录已刷新', 'success');
      return payload;
    }).catch(function (error) {
      if (requestSeq !== auditLoadSeq) return;
      if (!options.silent) {
        $('logs-fail-banner').classList.remove('is-hidden');
        $('logs-fail-banner').querySelector('.banner-msg').textContent = apiError(error);
        showToast(apiError(error), 'error');
      }
      throw error;
    }).finally(function () { if (requestSeq === auditLoadSeq) setLoading(false); });
  }
  function debounceFilter(key, fn) {
    if (filterTimers[key]) window.clearTimeout(filterTimers[key]);
    filterTimers[key] = window.setTimeout(function () { filterTimers[key] = null; fn(); }, 350);
  }
  function loadActive(options) { return state.activeTab === 'logs' ? loadLogs(options) : loadAudits(options); }

  function timeLabel() {
    var v = state.activeTab === 'logs' ? state.time : state.auditTime;
    return ({ '24h':'最近 24 小时', today:'今天', '7d':'近 7 天', '30d':'近 30 天', all:'全部时间', custom:'自定义' })[v] || v;
  }
  function renderStats() {
    var summary = meta.auditSummary || {};
    var outcomes = summary.by_outcome || {};
    $('stat-total-count').textContent = fmt(summary.total == null ? meta.auditTotal : summary.total);
    $('stat-denied-count').textContent = fmt(outcomes.denied || 0);
    $('stat-failed-count').textContent = fmt(Number(outcomes.failure || 0) + Number(outcomes.error || 0));
    $('stat-high-risk-count').textContent = fmt(summary.high_risk == null ? meta.abnormal : summary.high_risk);
    $('tab-logs-count').textContent = fmt(page.logs.total == null ? LOGS.length : page.logs.total);
    $('tab-audit-count').textContent = fmt(page.audit.total == null ? AUDITS.length : page.audit.total);
    var highRisk = summary.high_risk == null ? meta.abnormal : Number(summary.high_risk);
    var auditBanner = $('logs-audit-banner');
    auditBanner.classList.toggle('is-safe', highRisk === 0);
    // 整句一次性交给 i18n 引擎：片段拼接（tr(A) + n + tr(B)）在英文界面只会翻译
    // 单词条，剩下的中文片段会留在页面上（见 ISSUE-127）。
    $('logs-audit-banner-text').textContent = highRisk == null ? tr('未检查') : (highRisk > 0 ? tr('最近 24 小时发现 ' + highRisk + ' 条高风险事件，请优先核查') : tr('最近 24 小时未发现高风险事件'));
    $('refresh-meta-text').textContent = meta.lastUpdated ? tr('最后更新') + ' ' + meta.lastUpdated + (state.autoOn ? '' : ' · ' + tr('自动刷新已关闭')) : tr('等待服务端数据');
  }
  function renderLogs() {
    $('logs-tbody').innerHTML = LOGS.map(function (l, i) {
      return '<tr data-idx="' + i + '" tabindex="0">'
        + '<td>' + timeMarkup(l) + '</td>'
        + '<td><span class="level-chip ' + esc(l.level) + '"><span class="mini-dot"></span>' + esc(tr(SEV_LABEL[l.level] || l.level)) + '</span></td>'
        + '<td><span class="source-chip"><i data-lucide="' + esc(l.sourceIcon) + '"></i>' + esc(tr(l.sourceLabel)) + '</span></td>'
        + '<td>' + esc(l.deviceLabel) + '</td><td>' + esc(l.userLabel) + '</td>'
        + '<td class="summary-cell">' + esc(l.summary) + '</td>'
        + '<td style="text-align:right"><button class="row-btn view" type="button" data-idx="' + i + '"><i data-lucide="eye"></i>' + esc(tr('查看')) + '</button></td></tr>';
    }).join('');
    toggle($('logs-empty'), LOGS.length === 0);
    icons();
  }
  function renderAudits() {
    $('audit-tbody').innerHTML = AUDITS.map(function (a, i) {
      var resultClass = dashResultClass(a);
      var target = a.displayTarget || dashTarget(a) || ((a.targetType ? a.targetType + ' · ' : '') + (a.target || '—'));
      return '<tr data-idx="' + i + '" tabindex="0">'
        + '<td>' + timeMarkup(a) + '</td>'
        + '<td><div class="audit-event"><strong>' + esc(a.actionLabel || dashTitle(a)) + '</strong><span>' + esc(a.catLabel || dashCategoryLabel(a)) + '</span></div></td>'
        + '<td><div class="audit-subject"><strong>' + esc(a.operatorLabel) + '</strong><span>' + esc(target) + '</span></div></td>'
        + '<td><span class="result-chip ' + esc(resultClass) + '"><span class="mini-dot"></span>' + esc(a.resultLabel || dashResultLabel(a)) + '</span></td>'
        + '<td><span class="level-chip ' + esc(a.severity) + '"><span class="mini-dot"></span>' + esc(a.severityLabel || dashSeverityLabel(a)) + '</span></td>'
        + '<td><span class="audit-request-id" title="' + esc(a.requestId) + '">' + esc(a.requestId) + '</span></td>'
        + '<td style="text-align:right"><button class="row-btn view audit-view-btn" type="button" data-idx="' + i + '" aria-label="查看事件详情"><i data-lucide="chevron-right"></i></button></td></tr>';
    }).join('');
    $('audit-raw-list').innerHTML = AUDITS.map(function (a, i) {
      return '<div class="raw-line" tabindex="0" data-idx="' + i + '"><code>' + esc(a.raw) + '</code><button class="raw-copy" type="button" data-idx="' + i + '"><i data-lucide="copy"></i>' + esc(tr('复制')) + '</button></div>';
    }).join('');
    $('audit-raw-list').classList.toggle('wrap', state.rawWrap);
    $('raw-count').textContent = AUDITS.length + ' ' + tr('条记录');
    toggle($('audit-empty'), AUDITS.length === 0);
    toggle($('audit-structured'), state.viewMode === 'structured');
    toggle($('audit-raw'), state.viewMode === 'raw');
    icons();
  }
  function renderContent() {
    var isLogs = state.activeTab === 'logs';
    toggle($('logs-run-view'), isLogs);
    toggle($('audit-view'), !isLogs);
    if (isLogs) renderLogs(); else renderAudits();
  }
  function renderFooter() {
    var p = state.activeTab === 'logs' ? page.logs : page.audit;
    var len = state.activeTab === 'logs' ? LOGS.length : AUDITS.length;
    var total = p.total == null ? len : p.total;
    // 同上：页码信息整句翻译（「共 N 条 · 显示 1-N」/「已显示 N 条 · 还有更早记录」）
    $('logs-footer-info').textContent = len ? (state.activeTab === 'audit' && p.hasMore ? tr('已显示 ' + len + ' 条 · 还有更早记录') : tr('共 ' + fmt(total) + ' 条 · 显示 1-' + len)) : tr('暂无记录');
    $('load-more-btn').hidden = !p.hasMore;
  }
  function renderAll() { renderStats(); renderContent(); renderFooter(); }

  function openLogDetail(i) {
    var l = LOGS[i]; if (!l) return;
    currentLogIndex = i;
    $('log-drawer-meta').textContent = l.timeFull + ' · ' + l.sourceLabel + ' · ' + l.eventId;
    $('log-drawer-raw').textContent = l.raw;
    $('ld-time').textContent = l.timeFull || l.t;
    $('ld-level').textContent = SEV_LABEL[l.level] || l.level;
    $('ld-source').textContent = l.sourceLabel;
    $('ld-device').textContent = l.deviceLabel;
    $('ld-user').textContent = l.userLabel;
    $('ld-session').textContent = l.sessionId;
    $('ld-event').textContent = l.eventId;
    $('log-drawer-prev').disabled = i <= 0;
    $('log-drawer-next').disabled = i >= LOGS.length - 1;
    $('log-drawer').classList.add('open');
    $('drawer-mask').classList.add('open');
  }
  function stepLogDetail(delta) { openLogDetail(currentLogIndex + delta); }
  function closeLogDrawer() { $('log-drawer').classList.remove('open'); $('drawer-mask').classList.remove('open'); }
  function auditJson(value) {
    try { return JSON.stringify(value == null ? {} : value, null, 2); } catch (e) { return '{}'; }
  }
  function renderAuditDetail(a) {
    a = a || {};
    var resultClass = dashResultClass(a);
    $('am-time').textContent = a.timeFull || a.t || '—';
    $('am-event').textContent = a.eventId || '—';
    $('am-request').textContent = a.requestId || '—';
    $('am-actor').textContent = (a.operatorLabel || '未认证用户') + (a.actorRole ? ' · ' + a.actorRole : '');
    $('am-action').textContent = a.actionLabel || dashTitle(a) || a.action || '—';
    $('am-action').title = a.action || '';
    $('am-target').textContent = a.displayTarget || dashTarget(a) || ((a.targetType ? a.targetType + ' · ' : '') + (a.target || '—'));
    $('am-result').textContent = a.resultLabel || dashResultLabel(a) || '—';
    $('am-result').className = 'v ' + resultClass;
    $('am-severity').textContent = a.severityLabel || dashSeverityLabel(a) || '—';
    $('am-ip').textContent = a.ip || '—';
    $('am-schema').textContent = a.schemaVersion || '—';
    $('am-reason').textContent = a.reasonLabel || dashReason(a) || (a.reason && !opaqueAuditLabel(a.reason) ? a.reason : tr('未记录'));
    $('am-reason').title = a.reason || '';
    $('am-desc').textContent = a.desc || '未记录';
    $('am-user-agent').textContent = a.userAgent || '未记录';
    $('am-metadata').textContent = auditJson(a.metadata);
    $('am-integrity').textContent = auditJson({ sequence_id:a.sequenceId || null, previous_hash:a.previousHash || null, event_hash:a.eventHash || null, dedupe_key:a.dedupeKey || null });
    $('audit-modal-copy').dataset.raw = a.raw || auditJson(a);
  }
  function auditDetailError(error) {
    // 审计日志有保留上限，被裁剪掉的事件再点开就会 404 —— 给出明确原因，
    // 而不是把它显示成普通的"资源不存在"。
    var status = error && error.detail && error.detail.status;
    if (status === 404) return tr('该事件已被日志保留策略裁剪，可按事件 ID 在审计链里核对');
    return apiError(error);
  }
  function openAuditDetail(i) {
    var a = AUDITS[i]; if (!a) return;
    var sequence = ++auditDetailSequence;
    renderAuditDetail(a);
    $('audit-modal-status').textContent = tr('正在加载完整事件详情…');
    $('audit-modal-status').className = 'detail-load-status loading';
    $('audit-modal-mask').classList.add('open');
    if (!isConfigured('logs.audit.detail')) {
      $('audit-modal-status').textContent = tr('当前显示列表中的事件详情');
      $('audit-modal-status').className = 'detail-load-status';
      return;
    }
    window.ScrcpyGateApi.configured('logs.audit.detail', { method:'GET', params:{ id:a.eventId } }).then(function (payload) {
      if (sequence !== auditDetailSequence) return;
      var d = dataObject(payload);
      renderAuditDetail(normalizeAudit(d.event || d));
      $('audit-modal-status').textContent = tr('事件详情已加载');
      $('audit-modal-status').className = 'detail-load-status ready';
    }).catch(function (error) {
      if (sequence !== auditDetailSequence) return;
      $('audit-modal-status').textContent = auditDetailError(error);
      $('audit-modal-status').className = 'detail-load-status error';
    });
  }
  function closeAuditModal() { auditDetailSequence += 1; $('audit-modal-mask').classList.remove('open'); }
  function openRequestedEvent() {
    if (!requestedEventId || requestedEventOpened) return;
    state.activeTab = 'audit';
    syncTabUI();
    var index = AUDITS.findIndex(function (item) { return String(item.eventId || '') === String(requestedEventId); });
    if (index >= 0) {
      requestedEventOpened = true;
      openAuditDetail(index);
      return;
    }
    if (!isConfigured('logs.audit.detail')) return;
    requestedEventOpened = true;
    window.ScrcpyGateApi.configured('logs.audit.detail', { method:'GET', params:{ id:requestedEventId } }).then(function (payload) {
      var d = dataObject(payload);
      var event = normalizeAudit(d.event || d);
      renderAuditDetail(event);
      $('audit-modal-status').textContent = tr('事件详情已加载');
      $('audit-modal-status').className = 'detail-load-status ready';
      $('audit-modal-mask').classList.add('open');
      icons();
    }).catch(function (error) {
      requestedEventOpened = false;
      showToast(auditDetailError(error), 'error');
    });
  }

  function syncTabUI() {
    var isLogs = state.activeTab === 'logs';
    $('tab-logs').classList.toggle('active', isLogs);
    $('tab-audit').classList.toggle('active', !isLogs);
    $('tab-logs').setAttribute('aria-selected', isLogs ? 'true' : 'false');
    $('tab-audit').setAttribute('aria-selected', isLogs ? 'false' : 'true');
    toggle($('logs-filter-bar'), isLogs);
    toggle($('audit-filter-bar'), !isLogs);
    toggle($('view-seg'), !isLogs);
    toggle($('integrity-check-btn'), !isLogs);
    renderAll();
  }
  function syncViewSeg() {
    $('view-structured').classList.toggle('active', state.viewMode === 'structured');
    $('view-raw').classList.toggle('active', state.viewMode === 'raw');
  }
  function toggleCustomRange() { toggle($('logs-date-range'), $('logs-time').value === 'custom'); }
  function toggleAuditRange() { toggle($('audit-date-range'), $('audit-time').value === 'custom'); }

  function removeChip(k) {
    if (k === 'kw') { state.kw = ''; $('logs-search').value = ''; }
    else if (k === 'time') { state.time = '24h'; $('logs-time').value = '24h'; toggleCustomRange(); }
    else if (k === 'severity') { state.severity = 'all'; $('logs-severity').value = 'all'; }
    else if (k === 'source') { state.source = 'all'; $('logs-source').value = 'all'; }
    else if (k === 'device') { state.device = 'all'; $('logs-device').value = 'all'; }
    else if (k === 'user') { state.user = 'all'; $('logs-user').value = 'all'; }
    else if (k === 'auditKw') { state.auditKw = ''; $('audit-search').value = ''; }
    else if (k === 'auditTime') { state.auditTime = '24h'; $('audit-time').value = '24h'; toggleAuditRange(); }
    else if (k === 'eventType') { state.eventType = 'all'; $('logs-event-type').value = 'all'; }
    else if (k === 'result') { state.result = 'all'; $('logs-result').value = 'all'; }
    else if (k === 'actor') { state.actor = 'all'; $('logs-actor').value = 'all'; }
    else if (k === 'target') { state.target = ''; $('logs-target').value = ''; }
    syncExtraFilterCounts();
    loadActive({ show:false }).catch(function () {});
  }
  function clearAllFilters() {
    state.kw = ''; $('logs-search').value = '';
    state.time = '24h'; $('logs-time').value = '24h';
    state.dateFrom = ''; state.dateTo = ''; $('logs-date-from').value = ''; $('logs-date-to').value = '';
    state.auditKw = ''; $('audit-search').value = '';
    state.auditTime = '24h'; $('audit-time').value = '24h';
    state.auditDateFrom = ''; state.auditDateTo = ''; $('audit-date-from').value = ''; $('audit-date-to').value = '';
    state.severity = 'all'; $('logs-severity').value = 'all';
    state.source = 'all'; $('logs-source').value = 'all';
    state.device = 'all'; $('logs-device').value = 'all';
    state.user = 'all'; $('logs-user').value = 'all';
    state.eventType = 'all'; $('logs-event-type').value = 'all';
    state.result = 'all'; $('logs-result').value = 'all';
    state.actor = 'all'; $('logs-actor').value = 'all';
    state.auditDevice = 'all'; $('audit-device').value = 'all';
    state.target = ''; $('logs-target').value = '';
    toggleCustomRange(); toggleAuditRange();
    syncExtraFilterCounts();
    loadActive({ show:false }).catch(function () {});
  }

  function syncExtraFilterCounts() {
    document.querySelectorAll('.filter-disclosure').forEach(function (details) {
      var count = Array.from(details.querySelectorAll('select, input')).filter(function (field) {
        return field.value && field.value !== 'all';
      }).length;
      var badge = details.querySelector('.filter-active-count');
      badge.textContent = count ? '(' + count + ')' : '';
      badge.hidden = !count;
    });
  }

  function updateRefreshUI() {
    var running = state.autoOn;
    $('auto-switch').checked = running;
    var dot = $('refresh-meta-dot');
    if (dot) {
      dot.classList.toggle('on', running);
      dot.title = running ? tr('自动刷新已开启') : tr('自动刷新已关闭');
    }
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
    if (running) autoTimer = setInterval(function () {
      if (document.visibilityState === 'visible') loadActive({ silent:true }).catch(function () {});
    }, 60000);
    renderStats();
  }

  function closeExportPop() { $('export-pop').classList.remove('open'); }
  function showExportPop() {
    var btn = $('export-btn');
    var pop = $('export-pop');
    var count = state.activeTab === 'logs' ? LOGS.length : AUDITS.length;
    $('export-desc').textContent = tr('将导出当前筛选视图中的记录：' + count + ' 条');
    var r = btn.getBoundingClientRect();
    var w = 210, h = 178;
    pop.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
    pop.style.top = Math.max(8, (r.bottom + h + 8 > window.innerHeight) ? (r.top - h - 8) : (r.bottom + 8)) + 'px';
    pop.classList.add('open');
  }
  function triggerDownload(payload, format) {
    var d = dataObject(payload);
    var url = d.url || d.downloadUrl || d.href;
    if (url) {
      var safeUrl = safeDownloadUrl(url);
      if (!safeUrl) {
        return 'blocked-url';
      }
      var a = document.createElement('a');
      a.href = safeUrl;
      a.download = d.filename || '';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return true;
    }
    var content = typeof d.content === 'string' ? d.content : (typeof d.data === 'string' ? d.data : '');
    if (!content && !(d.blob instanceof Blob)) return false;
    var blob = d.blob instanceof Blob ? d.blob : new Blob([content], { type: format === 'json' ? 'application/json' : 'text/plain' });
    var blobUrl = URL.createObjectURL(blob);
    var link = document.createElement('a');
    link.href = blobUrl;
    link.download = d.filename || ('scrcpygate-logs.' + (format === 'text' ? 'txt' : format));
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 60000);
    return true;
  }
  function doExport(format) {
    closeExportPop();
    if (!isConfigured('logs.export')) { showToast('接口尚未配置：logs.export', 'error'); return; }
    var body = { type:state.activeTab, format:format, filters:state.activeTab === 'logs' ? logQuery(0) : auditQuery(null) };
    delete body.filters.limit;
    delete body.filters.offset;
    window.ScrcpyGateApi.configured('logs.export', { method:'POST', body:body }).then(function (payload) {
      var downloadResult = triggerDownload(payload, format);
      if (downloadResult === true) {
        showToast('导出任务已提交', 'success');
      } else if (downloadResult === 'blocked-url') {
        showToast('导出下载链接来源未被允许', 'error');
      } else {
        showToast('导出内容不可用', 'error');
      }
    }).catch(function (error) {
      var status = error && error.detail && error.detail.status;
      if (status === 401 || status === 403) $('logs-permission-banner').classList.remove('is-hidden');
      else $('logs-export-fail-banner').classList.remove('is-hidden');
      showToast(apiError(error), 'error');
    });
  }

  async function exportFullLogs() {
    var button = $('export-all-btn');
    if (button.disabled) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.querySelector('span').textContent = tr('正在导出…');
    showToast('正在整理全部保留的日志，请稍候', 'info');
    try {
      var payload = await window.ScrcpyGateApi.configured('logs.export.full', { method: 'POST' });
      if (triggerDownload(payload, 'zip') !== true) throw new Error('export_content_unavailable');
      showToast('完整日志已生成，开始下载 ZIP', 'success');
    } catch (error) {
      var status = error && error.detail && error.detail.status;
      if (status === 401 || status === 403) $('logs-permission-banner').classList.remove('is-hidden');
      else $('logs-export-fail-banner').classList.remove('is-hidden');
      showToast(apiError(error), 'error');
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.querySelector('span').textContent = tr('导出完整日志');
    }
  }

  function renderIntegrity(payload) {
    var d = dataObject(payload);
    integrityData = d || { issues: [] };
    var ok = d.ok === true || d.result === 'passed' || d.status === 'passed';
    var done = d.status || d.state || '已完成';
    $('integrity-status').className = 'result-chip ' + (ok ? 'success' : 'fail');
    $('integrity-status').innerHTML = '<span class="mini-dot"></span>' + esc(tr(done));
    $('integrity-result').className = 'result-chip ' + (ok ? 'success' : 'fail');
    $('integrity-result').innerHTML = '<span class="mini-dot"></span>' + esc(tr(d.resultLabel || (ok ? '通过' : '未通过')));
    $('integrity-time').textContent = d.checkedAt || d.updatedAt || d.time || '—';
    $('integrity-range').textContent = d.range || d.scope || tr('未检查');
    $('integrity-verified').textContent = d.verifiedCount == null ? '—' : fmt(d.verifiedCount) + ' ' + tr('条');
    $('integrity-failed').textContent = d.failedCount == null ? '—' : fmt(d.failedCount) + ' ' + tr('条');
    icons();
  }
  function runIntegrityCheck(showMessage) {
    if (!isConfigured('logs.integrity')) { showToast('接口尚未配置：logs.integrity', 'error'); return Promise.reject(new Error('logs.integrity unavailable')); }
    var btn = $('integrity-check-btn');
    btn.classList.add('loading');
    btn.disabled = true;
    btn.querySelector('span').textContent = tr('检查中…');
    return window.ScrcpyGateApi.configured('logs.integrity', { method:'POST', body:{ range:state.auditTime, from:state.auditDateFrom, to:state.auditDateTo } }).then(function (payload) {
      renderIntegrity(payload);
      if (showMessage) showToast('日志完整性检查已完成', 'success');
      return payload;
    }).catch(function (error) {
      showToast(apiError(error), 'error');
      throw error;
    }).finally(function () {
      btn.classList.remove('loading');
      btn.disabled = false;
      btn.querySelector('span').textContent = tr('检查完整性');
    });
  }
  function openIntegrityIssues() {
    var issues = payloadList(integrityData, 'issues').map(normalizeAudit);
    $('integrity-issue-empty').classList.toggle('is-hidden', issues.length > 0);
    $('integrity-issue-list').innerHTML = issues.map(function (a) {
      var resultClass = dashResultClass(a);
      var title = a.actionLabel || dashTitle(a);
      return '<div class="issue-row">' + timeMarkup(a) + '<span class="issue-actor">' + esc(a.operatorLabel) + '</span><span class="issue-desc"><strong>' + esc(title) + '</strong> · ' + esc(a.desc) + '</span><span class="result-chip ' + esc(resultClass) + '"><span class="mini-dot"></span>' + esc(a.resultLabel || dashResultLabel(a)) + '</span></div>';
    }).join('');
    icons();
    $('integrity-modal-mask').classList.add('open');
  }

  function bindFilters() {
    $('logs-search').addEventListener('input', function () { state.kw = this.value; debounceFilter('logs-search', function () { loadLogs({ silent:true }).catch(function () {}); }); });
    $('logs-time').addEventListener('change', function () { state.time = this.value; toggleCustomRange(); loadLogs({ show:false }).catch(function () {}); });
    $('logs-date-from').addEventListener('change', function () { state.dateFrom = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('logs-date-to').addEventListener('change', function () { state.dateTo = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('logs-severity').addEventListener('change', function () { state.severity = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('logs-source').addEventListener('change', function () { state.source = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('logs-device').addEventListener('change', function () { state.device = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('logs-user').addEventListener('change', function () { state.user = this.value; loadLogs({ show:false }).catch(function () {}); });
    $('audit-search').addEventListener('input', function () { state.auditKw = this.value; debounceFilter('audit-search', function () { loadAudits({ silent:true }).catch(function () {}); }); });
    $('audit-time').addEventListener('change', function () { state.auditTime = this.value; toggleAuditRange(); loadAudits({ show:false }).catch(function () {}); });
    $('audit-date-from').addEventListener('change', function () { state.auditDateFrom = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('audit-date-to').addEventListener('change', function () { state.auditDateTo = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('logs-event-type').addEventListener('change', function () { state.eventType = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('logs-result').addEventListener('change', function () { state.result = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('logs-actor').addEventListener('change', function () { state.actor = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('audit-device').addEventListener('change', function () { state.auditDevice = this.value; loadAudits({ show:false }).catch(function () {}); });
    $('logs-target').addEventListener('input', function () { state.target = this.value; debounceFilter('logs-target', function () { loadAudits({ silent:true }).catch(function () {}); }); });
  }

  $('tab-logs').addEventListener('click', function () { if (state.activeTab !== 'logs') { state.activeTab = 'logs'; syncTabUI(); loadLogs({ show:false }).catch(function () {}); } });
  $('tab-audit').addEventListener('click', function () { if (state.activeTab !== 'audit') { state.activeTab = 'audit'; syncTabUI(); loadAudits({ show:false }).catch(function () {}); } });
  $('view-structured').addEventListener('click', function () { state.viewMode = 'structured'; syncViewSeg(); renderContent(); renderFooter(); });
  $('view-raw').addEventListener('click', function () { state.viewMode = 'raw'; syncViewSeg(); renderContent(); renderFooter(); });
  $('auto-switch').addEventListener('change', function () { state.autoOn = this.checked; updateRefreshUI(); showToast(state.autoOn ? '自动刷新已开启' : '自动刷新已关闭', 'info'); });
  $('refresh-btn').addEventListener('click', function () { loadActive({ show:true }).catch(function () {}); });
  $('logs-fail-retry').addEventListener('click', function () { loadActive({ show:true }).catch(function () {}); });
  $('logs-down-close').addEventListener('click', function () { $('logs-down-banner').classList.add('is-hidden'); });
  $('export-btn').addEventListener('click', function (e) { e.stopPropagation(); showExportPop(); });
  $('export-all-btn').addEventListener('click', exportFullLogs);
  $('export-csv').addEventListener('click', function () { doExport('csv'); });
  $('export-json').addEventListener('click', function () { doExport('json'); });
  $('export-text').addEventListener('click', function () { doExport('text'); });
  $('logs-permission-close').addEventListener('click', function () { $('logs-permission-banner').classList.add('is-hidden'); });
  $('logs-export-fail-close').addEventListener('click', function () { $('logs-export-fail-banner').classList.add('is-hidden'); });
  $('raw-wrap-btn').addEventListener('click', function () {
    state.rawWrap = !state.rawWrap;
    this.setAttribute('aria-pressed', state.rawWrap ? 'true' : 'false');
    var span = this.querySelector('span');
    if (span) span.textContent = state.rawWrap ? tr('自动换行') : tr('单行显示');
    $('audit-raw-list').classList.toggle('wrap', state.rawWrap);
  });
  $('log-drawer-prev').addEventListener('click', function () { stepLogDetail(-1); });
  $('log-drawer-next').addEventListener('click', function () { stepLogDetail(1); });
  $('load-more-btn').addEventListener('click', function () { loadActive({ append:true }).catch(function () {}); });
  $('log-drawer-close').addEventListener('click', closeLogDrawer);
  $('drawer-mask').addEventListener('click', function (e) { if (e.target === this) closeLogDrawer(); });
  $('log-drawer-copy').addEventListener('click', function () { copyText($('log-drawer-raw').textContent, this); });
  $('audit-modal-close').addEventListener('click', closeAuditModal);
  $('audit-modal-mask').addEventListener('click', function (e) { if (e.target === this) closeAuditModal(); });
  $('audit-modal-copy').addEventListener('click', function () { copyText(this.dataset.raw || '{}', this); });
  $('integrity-check-btn').addEventListener('click', function () { runIntegrityCheck(true).catch(function () {}); });
  $('integrity-details-btn').addEventListener('click', openIntegrityIssues);
  $('integrity-modal-close').addEventListener('click', function () { $('integrity-modal-mask').classList.remove('open'); });
  $('integrity-modal-ok').addEventListener('click', function () { $('integrity-modal-mask').classList.remove('open'); });
  $('integrity-modal-mask').addEventListener('click', function (e) { if (e.target === this) this.classList.remove('open'); });
  $('logs-empty-clear').addEventListener('click', clearAllFilters);
  $('audit-empty-clear').addEventListener('click', clearAllFilters);
  document.querySelectorAll('[data-clear-log-filters]').forEach(function (button) {
    button.addEventListener('click', clearAllFilters);
  });
  document.querySelectorAll('.filter-extra').forEach(function (filters) {
    filters.addEventListener('input', syncExtraFilterCounts);
    filters.addEventListener('change', syncExtraFilterCounts);
  });

  document.addEventListener('click', function (e) {
    var viewBtn = e.target.closest ? e.target.closest('.logs-table .row-btn.view') : null;
    if (viewBtn) { openLogDetail(parseInt(viewBtn.getAttribute('data-idx'), 10)); return; }
    var logRow = e.target.closest ? e.target.closest('#logs-tbody tr[data-idx]') : null;
    if (logRow) { openLogDetail(parseInt(logRow.getAttribute('data-idx'), 10)); return; }
    var rawCopy = e.target.closest ? e.target.closest('.raw-copy') : null;
    if (rawCopy) {
      var item = AUDITS[parseInt(rawCopy.getAttribute('data-idx'), 10)];
      if (item) copyText(item.raw, rawCopy);
      return;
    }
    var rawLine = e.target.closest ? e.target.closest('#audit-raw-list .raw-line') : null;
    if (rawLine) { openAuditDetail(parseInt(rawLine.getAttribute('data-idx'), 10)); return; }
    var auditRow = e.target.closest ? e.target.closest('#audit-tbody tr[data-idx]') : null;
    if (auditRow) { openAuditDetail(parseInt(auditRow.getAttribute('data-idx'), 10)); return; }
    var chip = e.target.closest ? e.target.closest('[data-chip]') : null;
    if (chip) { removeChip(chip.getAttribute('data-chip')); return; }
    if (!e.target.closest('#export-pop') && !e.target.closest('#export-btn')) closeExportPop();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeLogDrawer(); closeAuditModal(); closeExportPop(); $('integrity-modal-mask').classList.remove('open'); }
    if (e.key === 'Enter' || e.key === ' ') {
      var logRow = e.target.closest ? e.target.closest('#logs-tbody tr[data-idx]') : null;
      if (logRow) { e.preventDefault(); openLogDetail(parseInt(logRow.getAttribute('data-idx'), 10)); return; }
      var auditRow = e.target.closest ? e.target.closest('#audit-tbody tr[data-idx]') : null;
      if (auditRow) { e.preventDefault(); openAuditDetail(parseInt(auditRow.getAttribute('data-idx'), 10)); return; }
      var rawLine = e.target.closest ? e.target.closest('#audit-raw-list .raw-line') : null;
      if (rawLine) { e.preventDefault(); openAuditDetail(parseInt(rawLine.getAttribute('data-idx'), 10)); }
    }
  });

  /* ---------- 日志保存时长（审计日志 + 运行日志轮转段；固定档位，0 = 不清理） ---------- */
  var LOG_RETENTION_OPTIONS = [0, 1, 3, 7, 15, 30];
  var logRetention = { logRetentionDays: 30 };

  function logRetentionDaysOf(payload) {
    var settings = (payload && payload.settings) || payload || {};
    var days = Number(settings.logRetentionDays);
    if (LOG_RETENTION_OPTIONS.indexOf(days) >= 0) return days;
    // 存量值不在档位内（更早的任意天数）：按「不超过它的最大档位」显示，和后台维护逻辑一致。
    if (!isFinite(days) || days <= 0) return 0;
    var allowed = LOG_RETENTION_OPTIONS.filter(function (option) { return option > 0 && option <= days; });
    return allowed.length ? allowed[allowed.length - 1] : LOG_RETENTION_OPTIONS[LOG_RETENTION_OPTIONS.length - 1];
  }
  function renderLogRetention() {
    var input = $('log-retention-days');
    if (input) input.value = String(logRetention.logRetentionDays);
    var state = $('log-retention-state');
    if (state) {
      state.textContent = logRetention.logRetentionDays > 0
        ? tr('保留最近') + ' ' + logRetention.logRetentionDays + ' ' + tr('天')
        : tr('不清理');
    }
  }
  function loadLogRetention() {
    if (!isConfigured('logs.retention')) return Promise.resolve();
    return window.ScrcpyGateApi.configured('logs.retention', { method: 'GET' }).then(function (payload) {
      logRetention.logRetentionDays = logRetentionDaysOf(payload);
      renderLogRetention();
    }).catch(function () {});
  }
  function saveLogRetention() {
    var input = $('log-retention-days');
    var days = input ? Number(input.value) : NaN;
    if (LOG_RETENTION_OPTIONS.indexOf(days) < 0) {
      showToast('日志保存时长只能是：不清理 / 1 / 3 / 7 / 15 / 30 天', 'error');
      return;
    }
    var btn = $('log-retention-save');
    if (btn) btn.disabled = true;
    window.ScrcpyGateApi.configured('logs.retention.update', {
      method: 'PUT',
      body: { logRetentionDays: days }
    }).then(function (payload) {
      logRetention.logRetentionDays = logRetentionDaysOf(payload);
      renderLogRetention();
      showToast('日志保存时长已保存', 'success');
    }).catch(function (error) {
      showToast(apiError(error), 'error');
    }).finally(function () {
      if (btn) btn.disabled = false;
    });
  }
  (function wireLogRetention() {
    var btn = $('log-retention-save');
    if (btn) btn.addEventListener('click', saveLogRetention);
  })();

  bindFilters();
  state.autoOn = $('auto-switch').checked;
  syncViewSeg();
  syncTabUI();
  updateRefreshUI();
  if (window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.on === 'function') {
    window.ScrcpyGateI18n.on(function () {
      // Re-run normalization so labels derived from the current locale do not
      // remain in the language used when the audit response first arrived.
      LOGS = LOGS.map(normalizeLog);
      AUDITS = AUDITS.map(normalizeAudit);
      renderAll();
      if (window.lucide) window.lucide.createIcons();
    });
  }
  loadAudits({ show:false }).catch(function () {});
  loadLogRetention();
})();
