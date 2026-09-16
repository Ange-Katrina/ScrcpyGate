/* 安全机制页面控制器：登录保护 / 人机验证 / 阈值时长可配置（保存后立即生效），
   并管理当前按 IP 的封禁与失败计数。用不到的部署可整体关闭。
   通过 login.guard / login.guard.update / login.guard.unlock 端点工作。 */
(function () {
  var panel = document.querySelector('.sec-layout');
  if (!panel) return;
  var list = document.getElementById('security-locked-list');
  var summary = document.getElementById('security-guard-summary');
  var count = document.getElementById('security-locked-count');
  var statusNode = document.getElementById('security-guard-status');
  var saveBtn = document.getElementById('security-guard-save');
  var resetBtn = document.getElementById('security-guard-reset');

  var FIELDS = [
    { name: 'enabled', kind: 'bool', id: 'security-guard-enabled' },
    { name: 'captcha_enabled', kind: 'bool', id: 'security-guard-captcha' },
    { name: 'captcha_after_failures', kind: 'int', id: 'security-guard-captcha-after' },
    { name: 'lockout_threshold', kind: 'int', id: 'security-guard-lockout-threshold' },
    { name: 'failure_window_seconds', kind: 'minutes', id: 'security-guard-window-minutes' },
    { name: 'lockout_seconds', kind: 'minutes', id: 'security-guard-lockout-minutes' },
    { name: 'captcha_ttl_seconds', kind: 'int', id: 'security-guard-captcha-ttl' },
    { name: 'captcha_issue_interval_seconds', kind: 'int', id: 'security-guard-issue-interval' }
  ];
  var PRESETS = {
    off: { enabled: false },
    lenient: { enabled: true, captcha_enabled: true, captcha_after_failures: 3, lockout_threshold: 10, failure_window_seconds: 1800, lockout_seconds: 300 },
    standard: { enabled: true, captcha_enabled: true, captcha_after_failures: 1, lockout_threshold: 3, failure_window_seconds: 900, lockout_seconds: 300 },
    strict: { enabled: true, captcha_enabled: true, captcha_after_failures: 1, lockout_threshold: 2, failure_window_seconds: 3600, lockout_seconds: 900 }
  };
  var snapshot = { config: {}, defaults: {}, locked: [], active: [] };

  function escapeText(value) {
    return String(value || '').replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  function fmtCountdown(seconds) {
    var total = Math.max(0, Math.round(seconds));
    return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
  }
  function control(field) { return document.getElementById(field.id); }
  function setStatus(text, tone) {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.setAttribute('data-tone', tone || '');
  }

  function readControl(field) {
    var node = control(field);
    if (!node) return undefined;
    if (field.kind === 'bool') return node.getAttribute('aria-checked') === 'true';
    var value = Number(node.value);
    if (!isFinite(value)) return undefined;
    return field.kind === 'minutes' ? Math.round(value * 60) : Math.round(value);
  }

  function writeControl(field, value) {
    var node = control(field);
    if (!node || value === undefined || value === null) return;
    if (field.kind === 'bool') {
      node.setAttribute('aria-checked', value ? 'true' : 'false');
      return;
    }
    if (field.kind === 'minutes') {
      // 秒转分钟展示：不足 1 分钟的非零值向上取整为 1，避免显示成 0。
      var minutes = Math.round(Number(value) / 60);
      node.value = String(Number(value) > 0 ? Math.max(1, minutes) : 0);
      return;
    }
    node.value = String(value);
  }

  function applyDisabledState() {
    // 控件的可用性跟随当前控件取值（预置按钮只改控件，不改服务端快照）。
    var enabled = readControl(FIELDS[0]) !== false;
    var captcha = enabled && readControl(FIELDS[1]) !== false;
    panel.classList.toggle('is-guard-off', !enabled);
    FIELDS.forEach(function (field) {
      var node = control(field);
      if (!node) return;
      if (field.name === 'enabled') return;
      var off = !enabled || (field.name !== 'captcha_enabled' && !captcha);
      node.disabled = off;
      var row = panel.querySelector('.guard-row[data-field="' + field.name + '"]');
      if (row) row.classList.toggle('is-muted', off);
    });
  }

  function serverValue(field) {
    if (field.kind === 'bool') return snapshot.config[field.name] !== false;
    return snapshot.config[field.name];
  }

  function matchesServer(field) {
    // 秒字段以分钟展示，比较时按展示精度归一化，避免加载后立即被判为「已修改」。
    var value = readControl(field);
    if (value === undefined) return true;
    var server = serverValue(field);
    if (field.kind === 'minutes') {
      var displayed = Number(server) > 0 ? Math.max(1, Math.round(Number(server) / 60)) * 60 : 0;
      return value === displayed;
    }
    return value === server;
  }

  function dirty() {
    return FIELDS.some(function (field) { return !matchesServer(field); });
  }

  function syncPresetState() {
    // 预置是「当前取值属于哪一档」的显示，不单独存状态：手动改数值后高亮立即跟随。
    panel.querySelectorAll('.guard-preset').forEach(function (button) {
      var preset = PRESETS[button.getAttribute('data-preset')];
      var active = false;
      if (preset) {
        active = Object.keys(preset).every(function (name) {
          var field = FIELDS.filter(function (item) { return item.name === name; })[0];
          if (!field) return true;
          return matchesPresetValue(field, preset[name]);
        });
      }
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function matchesPresetValue(field, expected) {
    var value = readControl(field);
    if (value === undefined) return false;
    if (field.kind === 'minutes') {
      return value === (Number(expected) > 0 ? Math.max(1, Math.round(Number(expected) / 60)) * 60 : 0);
    }
    return value === expected;
  }

  function syncSaveState() {
    var changed = dirty();
    if (saveBtn) saveBtn.disabled = !changed;
    setStatus(changed ? '有未保存的修改' : '未修改', changed ? 'warn' : '');
    syncPresetState();
  }

  function renderConfig() {
    FIELDS.forEach(function (field) { writeControl(field, snapshot.config[field.name]); });
    applyDisabledState();
    syncPresetState();
    if (saveBtn) saveBtn.disabled = !dirty();
  }

  function renderLocked() {
    var rows = snapshot.locked || [];
    var active = snapshot.active || [];
    list.innerHTML = '';
    if (!rows.length && !active.length) {
      var empty = document.createElement('li');
      empty.className = 'table-empty';
      empty.innerHTML = '<i data-lucide="shield-check"></i><span>当前没有封禁或失败计数</span>';
      list.appendChild(empty);
    } else {
      rows.forEach(function (entry) {
        var li = document.createElement('li');
        li.className = 'guard-locked-row';
        li.innerHTML = '<span class="guard-locked-ip">' + escapeText(entry.ip) + '</span>'
          + '<span class="guard-locked-meta">剩余 ' + fmtCountdown(entry.retry_after) + '</span>'
          + '<button type="button" class="guard-unlock" data-key="' + escapeText(entry.key) + '">解除</button>';
        list.appendChild(li);
      });
      active.forEach(function (entry) {
        var li = document.createElement('li');
        li.className = 'guard-locked-row';
        li.innerHTML = '<span class="guard-locked-ip">' + escapeText(entry.ip) + '</span>'
          + '<span class="guard-locked-meta">窗口内 ' + Number(entry.failures || 0) + ' 次失败</span>'
          + '<button type="button" class="guard-unlock" data-key="' + escapeText(entry.key) + '">清除</button>';
        list.appendChild(li);
      });
    }
    if (count) count.textContent = rows.length + ' 个封禁 · ' + active.length + ' 个失败计数';
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function renderSummary() {
    var config = snapshot.config || {};
    if (!summary) return;
    if (config.enabled === false) {
      summary.textContent = '已关闭';
    } else if (config.captcha_enabled === false) {
      summary.textContent = '仅封禁';
    } else {
      summary.textContent = '人机验证已启用';
    }
  }

  function render(payload) {
    snapshot = payload || { config: {}, defaults: {}, locked: [], active: [] };
    snapshot.config = snapshot.config || {};
    renderSummary();
    renderConfig();
    renderLocked();
    setStatus('未修改', '');
  }

  function load() {
    if (!window.ScrcpyGateApi) return;
    window.ScrcpyGateApi.configured('login.guard')
      .then(render)
      .catch(function () { if (count) count.textContent = '加载失败'; });
  }

  function save(body, doneText) {
    if (!window.ScrcpyGateApi) return;
    if (saveBtn) saveBtn.disabled = true;
    setStatus('保存中…', '');
    window.ScrcpyGateApi.configured('login.guard.update', { method: 'PUT', body: body })
      .then(function (payload) {
        render(payload);
        setStatus(doneText, 'ok');
      })
      .catch(function (error) {
        setStatus('保存失败：' + (error && error.message ? error.message : '未知错误'), 'error');
        if (saveBtn) saveBtn.disabled = false;
      });
  }

  FIELDS.forEach(function (field) {
    var node = control(field);
    if (!node) return;
    if (field.kind === 'bool') {
      node.addEventListener('click', function () {
        if (node.disabled) return;
        var next = node.getAttribute('aria-checked') !== 'true';
        // 只改控件：snapshot.config 保持服务端真值，脏状态才能被正确判定。
        node.setAttribute('aria-checked', next ? 'true' : 'false');
        applyDisabledState();
        syncSaveState();
      });
      node.addEventListener('keydown', function (event) {
        if (event.key === ' ' || event.key === 'Enter') {
          event.preventDefault();
          node.click();
        }
      });
    } else {
      node.addEventListener('input', syncSaveState);
      node.addEventListener('change', syncSaveState);
    }
  });

  panel.querySelectorAll('.guard-preset').forEach(function (button) {
    button.addEventListener('click', function () {
      var preset = PRESETS[button.getAttribute('data-preset')];
      if (!preset) return;
      Object.keys(preset).forEach(function (name) {
        var field = FIELDS.filter(function (item) { return item.name === name; })[0];
        if (field) writeControl(field, preset[name]);
      });
      applyDisabledState();
      syncSaveState();
    });
  });

  if (saveBtn) saveBtn.addEventListener('click', function () {
    // 只提交被修改过的字段：未触碰的项保持服务端当前值，避免把按秒存储的
    // 配置按分钟取整后回写。
    var body = {};
    var pending = 0;
    FIELDS.forEach(function (field) {
      if (matchesServer(field)) return;
      var value = readControl(field);
      if (value === undefined) return;
      body[field.name] = value;
      pending += 1;
    });
    if (!pending) {
      setStatus('未修改', '');
      if (saveBtn) saveBtn.disabled = true;
      return;
    }
    save(body, '已保存并立即生效');
  });
  if (resetBtn) resetBtn.addEventListener('click', function () {
    save(snapshot.defaults || {}, '已恢复为环境变量默认值');
  });

  list.addEventListener('click', function (event) {
    var button = event.target && event.target.closest ? event.target.closest('.guard-unlock') : null;
    if (!button) return;
    var key = button.getAttribute('data-key');
    if (!key || !window.ScrcpyGateApi) return;
    window.ScrcpyGateApi.configured('login.guard.unlock', { method: 'POST', body: { key: key } })
      .then(function (payload) { render(payload && payload.snapshot); })
      .catch(function () { if (count) count.textContent = '解除失败'; });
  });

  // 每 60 秒刷新一次封禁列表，但不覆盖正在编辑的控件（有未保存修改时跳过）。
  window.setInterval(function () {
    if (!window.ScrcpyGateApi || dirty()) return;
    window.ScrcpyGateApi.configured('login.guard').then(render).catch(function () {});
  }, 60000);

  // ---- 登录会话：谁登录了系统，可一键踢出（login.sessions / login.sessions.revoke） ----
  var sessionList = document.getElementById('login-sessions-list');
  var sessionCount = document.getElementById('login-sessions-count');
  var sessionRefresh = document.getElementById('login-sessions-refresh');

  function fmtSessionTime(seconds) {
    var value = Number(seconds || 0);
    if (!value) return '—';
    try {
      return new Date(value * 1000).toLocaleString();
    } catch (err) {
      return '—';
    }
  }

  function describeClient(item) {
    var agent = String(item.user_agent || '');
    var label = '未知客户端';
    if (/Windows/i.test(agent)) label = 'Windows';
    else if (/Android/i.test(agent)) label = 'Android';
    else if (/iPhone|iPad|iOS/i.test(agent)) label = 'iOS';
    else if (/Macintosh|Mac OS X/i.test(agent)) label = 'macOS';
    else if (/Linux/i.test(agent)) label = 'Linux';
    var ip = String(item.client_ip || '—');
    return label + ' · ' + ip;
  }

  /* 同一台设备（同一浏览器）会开多条会话：用服务端下发的浏览器设备标识归并，
     老服务端没有该字段时退回「IP + User-Agent」，效果一样。 */
  function sessionDeviceKey(item) {
    var device = String(item.device_id || item.deviceId || '').trim();
    if (device) return 'dev:' + device;
    return 'ua:' + String(item.client_ip || '') + '|' + String(item.user_agent || '').slice(0, 160);
  }

  function renderLoginSessions(payload) {
    if (!sessionList) return;
    var rows = (payload && payload.sessions) || [];
    sessionList.innerHTML = '';
    var groups = [];
    var index = {};
    rows.forEach(function (item) {
      var key = sessionDeviceKey(item);
      var group = index[key];
      if (!group) {
        group = { key: key, deviceId: String(item.device_id || item.deviceId || ''), items: [] };
        index[key] = group;
        groups.push(group);
      }
      group.items.push(item);
    });
    if (!groups.length) {
      var empty = document.createElement('li');
      empty.className = 'table-empty';
      empty.innerHTML = '<i data-lucide="users"></i><span>当前没有登录会话</span>';
      sessionList.appendChild(empty);
    } else {
      groups.forEach(function (group) {
        var items = group.items;
        var currentItem = items.filter(function (item) { return item.current; })[0] || null;
        var ips = [];
        items.forEach(function (item) {
          var ip = String(item.client_ip || '');
          if (ip && ips.indexOf(ip) < 0) ips.push(ip);
        });
        var firstLogin = Math.min.apply(null, items.map(function (item) { return Number(item.created_at) || 0; }).filter(Boolean));
        var lastSeen = Math.max.apply(null, items.map(function (item) { return Number(item.last_seen_at) || 0; }).filter(Boolean));
        var li = document.createElement('li');
        li.className = 'guard-locked-row';
        var who = escapeText(items[0].username) + (items[0].role === 'admin' ? '（管理员）' : '');
        var metaLines = [
          escapeText(describeClient(items[0])),
          '登录于 ' + escapeText(fmtSessionTime(firstLogin)) + ' · 最近活动 ' + escapeText(fmtSessionTime(lastSeen))
        ];
        if (group.deviceId) metaLines.push('设备标识 ' + escapeText(group.deviceId.slice(0, 12)));
        if (ips.length > 1) metaLines.push('来源 IP ' + escapeText(ips.join(' / ')));
        // 同一设备多条会话合成一行：数量写出来，操作合并成「踢出该设备」。
        var revokeIds = items.filter(function (item) { return !item.current; }).map(function (item) { return String(item.id); });
        var action;
        if (!revokeIds.length) {
          action = '<span class="guard-locked-meta">当前会话</span>';
        } else {
          var label = items.length > 1 ? ('踢出该设备（' + revokeIds.length + '）') : '踢出';
          action = '<button type="button" class="guard-unlock guard-revoke" data-sessions="' + escapeText(revokeIds.join(',')) + '">' + label + '</button>';
        }
        li.innerHTML = '<span class="guard-locked-ip">' + who
          + (items.length > 1 ? '<br><small class="guard-locked-meta">同一台设备 ' + items.length + ' 个会话</small>' : '')
          + '</span>'
          + '<span class="guard-locked-meta">' + metaLines.join('<br>') + '</span>'
          + action;
        sessionList.appendChild(li);
      });
    }
    if (sessionCount) {
      var max = Number((payload && payload.max_per_user) || 0);
      var deviceText = groups.length !== rows.length ? ('（归并自 ' + rows.length + ' 条会话 · ' + groups.length + ' 台设备）') : '';
      sessionCount.textContent = rows.length + ' 个登录会话' + deviceText + (max > 0 ? '（每账户上限 ' + max + '）' : '');
    }
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
  }

  function loadLoginSessions() {
    if (!sessionList || !window.ScrcpyGateApi) return;
    window.ScrcpyGateApi.configured('login.sessions')
      .then(renderLoginSessions)
      .catch(function () { if (sessionCount) sessionCount.textContent = '加载失败'; });
  }

  if (sessionList) {
    sessionList.addEventListener('click', function (event) {
      var button = event.target && event.target.closest ? event.target.closest('.guard-revoke') : null;
      if (!button || !window.ScrcpyGateApi) return;
      // 一行可能代表同一台设备的多条会话（data-sessions 是逗号分隔的会话 id）。
      var ids = String(button.getAttribute('data-sessions') || button.getAttribute('data-session') || '')
        .split(',').map(function (value) { return value.trim(); }).filter(Boolean);
      if (!ids.length) return;
      var question = ids.length > 1
        ? ('确认结束这 ' + ids.length + ' 个登录会话（同一台设备）？该设备需要重新登录。')
        : '确认结束该登录会话？对方需要重新登录。';
      if (!window.confirm(question)) return;
      button.disabled = true;
      Promise.all(ids.map(function (sessionId) {
        return window.ScrcpyGateApi.configured('login.sessions.revoke', { params: { sessionId: sessionId }, method: 'DELETE' })
          .catch(function () { return null; });
      })).then(function () { loadLoginSessions(); })
        .catch(function () {
          button.disabled = false;
          if (sessionCount) sessionCount.textContent = '踢出失败';
        });
    });
    if (sessionRefresh) sessionRefresh.addEventListener('click', loadLoginSessions);
    loadLoginSessions();
    // 列表只用来看一眼，30 秒刷一次；刷新时不动任何输入控件。
    window.setInterval(loadLoginSessions, 30000);
  }

  load();
})();
