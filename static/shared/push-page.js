(function () {
  'use strict';
  var api = window.ScrcpyGateApi;
  var english = String((window.ScrcpyGateConfig || {}).locale || '').toLowerCase().startsWith('en');
  function t(zh, en) { return english ? en : zh; }
  if (english) {
    document.documentElement.lang = 'en';
    document.querySelectorAll('[data-en]').forEach(function (node) { node.textContent = node.getAttribute('data-en'); });
    document.title = 'Notifications - ScrcpyGate';
    document.querySelectorAll('.sidebar-group-label').forEach(function (node) {
      node.textContent = ({ '工作台': 'Workspace', '资源管理': 'Resources', '系统': 'System' })[node.textContent] || node.textContent;
    });
    var labels = {
      '仪表盘': 'Dashboard', '概览统计': 'Overview', '设备管理': 'Devices', '投屏与状态': 'Mirroring and status',
      '用户与权限': 'Users and access', '角色与到期': 'Roles and expiry', '投屏管理': 'Mirror settings',
      '菜单与控件': 'Menus and controls', 'ALAS 管理': 'ALAS', '配置与绑定': 'Profiles and bindings',
      '画质与传输': 'Video quality', '分辨率与码率': 'Resolution and bitrate', '安全机制': 'Security',
      '登录防护': 'Login protection', '日志审计': 'Audit logs', '记录与追踪': 'Events and traces',
      '返回投屏工作台': 'Back to workbench', '前往画面与控制': 'View and control',
      '首页': 'Home', '管理后台': 'Administration', '退出登录': 'Sign out'
    };
    document.querySelectorAll('.admin-rail .menu-text b, .admin-rail .menu-text small, .breadcrumb a, .menu-item span').forEach(function (node) {
      node.textContent = labels[node.textContent] || node.textContent;
    });
    document.getElementById('push-url').placeholder = 'https://example.com/notify';
    document.getElementById('push-token').placeholder = 'Leave blank to keep the saved token';
    var refresh = document.getElementById('push-deliveries-refresh');
    refresh.setAttribute('aria-label', 'Refresh delivery records');
    refresh.title = 'Refresh delivery records';
  }
  var form = document.getElementById('push-form');
  var status = document.getElementById('push-status');
  var badge = document.getElementById('push-badge');
  var provider = document.getElementById('push-provider');
  var template = document.getElementById('push-template');
  var configured = false;
  function refreshDeliveries() {
    return api.configured('push.deliveries').then(function (result) {
      var list = document.getElementById('push-deliveries');
      list.replaceChildren();
      (result.items || []).forEach(function (item) {
        var row = document.createElement('li');
        var who = document.createElement('span');
        var detail = document.createElement('small');
        var state = document.createElement('span');
        var names = { device_offline: t('设备离线', 'Device offline'), account_expiring: t('即将到期', 'Expiring'), account_expired: t('已到期', 'Expired') };
        var states = { sent: t('已发送', 'Sent'), pending: t('待重试', 'Retrying'), failed: t('失败', 'Failed'), skipped: t('已跳过', 'Skipped') };
        who.textContent = item.username || '';
        detail.textContent = (names[item.event] || item.event) + ' · ' + new Date(Number(item.created_at) * 1000).toLocaleString();
        state.className = 'push-delivery-state';
        state.dataset.state = item.state || '';
        state.textContent = states[item.state] || item.state || '';
        row.append(who, detail, state);
        list.appendChild(row);
      });
      if (!list.children.length) {
        var empty = document.createElement('li');
        empty.textContent = t('暂无投递记录', 'No delivery records');
        list.appendChild(empty);
      }
    });
  }
  function message(text, tone) {
    status.textContent = text;
    status.dataset.tone = tone || '';
  }
  function errorText(error) { return (error && (error.detail || error.message)) || t('请求失败', 'Request failed'); }
  function syncProvider() {
    var custom = provider.value === 'custom';
    document.getElementById('push-template-field').hidden = !custom;
    document.getElementById('push-method').disabled = !custom;
  }
  provider.addEventListener('change', syncProvider);
  function load() {
    return api.configured('push.config').then(function (data) {
      configured = !!data.configured;
      provider.value = data.provider || 'custom';
      document.getElementById('push-method').value = data.method || 'POST';
      document.getElementById('push-enabled').checked = !!data.enabled;
      template.value = configured ? '' : JSON.stringify({ user_id: '{qq}', title: '{title}', content: '{message}', event: '{event}' }, null, 2);
      template.placeholder = configured ? t('留空保留已保存的正文模板', 'Leave blank to keep the saved body template') : '';
      document.querySelectorAll('input[name="event"]').forEach(function (box) {
        box.checked = (data.events || []).indexOf(box.value) >= 0;
      });
      badge.dataset.enabled = String(!!data.enabled);
      badge.textContent = data.enabled ? t('已启用', 'Enabled') : (configured ? t('已停用', 'Disabled') : t('未配置', 'Not configured'));
      document.getElementById('push-url').placeholder = data.endpoint_host ? data.endpoint_host + ' · ' + t('留空保留', 'leave blank to keep') : 'https://example.com/notify';
      message(configured ? t('当前配置已加载，敏感字段不回显。', 'Configuration loaded. Secrets are hidden.') : t('尚未配置推送。', 'No notification destination configured.'));
      syncProvider();
    }).catch(function (error) { message(errorText(error), 'error'); });
  }
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var headers, body;
    try {
      headers = document.getElementById('push-headers').value.trim();
      headers = headers ? JSON.parse(headers) : {};
      body = template.value.trim() ? JSON.parse(template.value) : null;
      if (!headers || Array.isArray(headers) || typeof headers !== 'object' || (body && (Array.isArray(body) || typeof body !== 'object'))) throw new Error('JSON object required');
    } catch (error) { message(t('请求头和正文必须是 JSON 对象。', 'Headers and body must be JSON objects.'), 'error'); return; }
    var save = document.getElementById('push-save');
    save.disabled = true;
    message(t('正在保存…', 'Saving…'));
    api.configured('push.save', { body: {
      provider: provider.value,
      url: document.getElementById('push-url').value.trim(),
      method: document.getElementById('push-method').value,
      token: document.getElementById('push-token').value,
      headers: headers,
      clear: (document.getElementById('push-clear-token').checked ? ['token'] : []).concat(document.getElementById('push-clear-headers').checked ? ['headers'] : []),
      template: body,
      events: Array.from(document.querySelectorAll('input[name="event"]:checked')).map(function (box) { return box.value; }),
      enabled: document.getElementById('push-enabled').checked
    } }).then(function () {
      document.getElementById('push-url').value = '';
      document.getElementById('push-token').value = '';
      document.getElementById('push-headers').value = '';
      document.getElementById('push-clear-token').checked = false;
      document.getElementById('push-clear-headers').checked = false;
      return load();
    }).then(function () { message(t('配置已保存。', 'Configuration saved.'), 'success'); })
      .catch(function (error) { message(errorText(error), 'error'); })
      .finally(function () { save.disabled = false; });
  });
  document.getElementById('push-test').addEventListener('click', function () {
    var qq = document.getElementById('push-test-qq').value.trim();
    if (!/^[0-9]{5,15}$/.test(qq)) { message(t('请输入有效的测试 QQ 号。', 'Enter a valid QQ number.'), 'error'); return; }
    var button = this;
    button.disabled = true;
    message(t('正在发送测试…', 'Sending test…'));
    api.configured('push.test', { body: { qq: qq } })
      .then(function () { message(t('目标接口已接受测试请求。', 'The destination accepted the test request.'), 'success'); })
      .catch(function (error) { message(errorText(error), 'error'); })
      .finally(function () { button.disabled = false; });
  });
  document.getElementById('push-deliveries-refresh').addEventListener('click', function () {
    refreshDeliveries().catch(function (error) { message(errorText(error), 'error'); });
  });
  var alasSource = document.getElementById('push-alas-source');
  Promise.all([api.configured('push.alasTemplate'), api.configured('push.alasConfigs')]).then(function (results) {
    var selected = results[0] || {};
    (results[1].configs || []).forEach(function (name) {
      var option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      alasSource.appendChild(option);
    });
    alasSource.value = selected.config_name || '';
    document.getElementById('push-alas-field').value = selected.recipient_field || 'data.user_id';
  }).catch(function (error) { message(errorText(error), 'error'); });
  document.getElementById('push-alas-form').addEventListener('submit', function (event) {
    event.preventDefault();
    var button = document.getElementById('push-alas-save');
    button.disabled = true;
    api.configured('push.alasTemplate.save', { body: {
      config_name: alasSource.value,
      recipient_field: document.getElementById('push-alas-field').value.trim()
    } }).then(function () { message(t('ALAS 模板已保存。', 'ALAS template saved.'), 'success'); })
      .catch(function (error) { message(errorText(error), 'error'); })
      .finally(function () { button.disabled = false; });
  });
  load();
  refreshDeliveries().catch(function (error) { message(errorText(error), 'error'); });
})();
