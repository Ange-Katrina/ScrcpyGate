/* Pure device projection helpers for the mirror page. */
(function (global) {
  'use strict';

  function normalizeStatus(device) {
    var value = device || {};
    var raw = String(value.status || value.state || value.health || '').trim().toLowerCase();
    if (!raw && typeof value.online === 'boolean') return value.online ? 'online' : 'offline';
    if (['online', 'healthy', 'connected', 'running', 'ready', 'ok'].indexOf(raw) >= 0) return 'online';
    if (['offline', 'disconnected', 'unreachable', 'error', 'failed'].indexOf(raw) >= 0) return 'offline';
    return 'unknown';
  }

  function normalize(device) {
    var value = device || {};
    var status = normalizeStatus(value);
    return {
      id: value.id,
      name: value.name || value.displayName || value.serial || value.id || '未知设备',
      model: value.model || value.product || '—',
      status: status,
      online: status === 'online' ? true : (status === 'offline' ? false : null),
      permission: value.permission !== false && value.noPermission !== true,
      // 控制权限要单独保留：只有 can_view 的用户能看点不动，「获取控制」按钮要据此置灰。
      canControl: value.canControl !== false && value.can_control !== false,
      viewers: value.viewerCount == null ? (value.viewers || 0) : value.viewerCount,
      controller: value.controllerName || value.controller || null,
      alas: value.alasStatus || value.alas && value.alas.status || '未加载',
      quality: value.quality || null
    };
  }

  function signature(items, loadError) {
    return JSON.stringify({
      error: !!loadError,
      devices: (items || []).map(function (device) {
        return [device.id, device.name, device.model, device.status, !!device.online, !!device.permission,
          device.viewers, device.controller || '', device.alas || '', device.quality && device.quality.name || '',
          device.quality && device.quality.meta || ''];
      })
    });
  }

  function create(options) {
    options = options || {};
    var tr = options.tr || function (value) { return String(value == null ? '' : value); };
    var escHtml = options.escHtml || function (value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character];
      });
    };
    var refreshIcons = options.refreshIcons || function () {};
    var getState = options.getState || function () { return {}; };
    var setState = options.setState || function () {};
    var onDeviceSelected = options.onDeviceSelected || function () {};

    function render(items, selectedId) {
      var list = document.getElementById('device-list');
      if (!list) return;
      var devices = items || [];
      var count = document.getElementById('device-count');
      if (count) count.textContent = devices.length ? devices.length + ' 台' : '—';
      list.innerHTML = devices.map(function (device) {
        var state = device.online === true ? 'online' : (device.online === false ? 'offline' : 'unknown');
        var selected = selectedId && String(selectedId) === String(device.id) ? ' selected' : '';
        var meta = device.permission ? (device.controller ? '有控制权限' : '可获取控制') : '仅观看';
        return '<div class="device-card' + selected + '" role="button" tabindex="0" data-device-id="' + escHtml(device.id) + '">' +
          '<span class="dev-dot ' + state + '" aria-hidden="true"></span><span class="dev-name">' + escHtml(device.name) + '</span>' +
          '<span class="dev-status ' + state + '">' + escHtml(tr(device.online === true ? '在线' : (device.online === false ? '离线' : '未检查'))) + '</span>' +
          '<span class="dev-meta">' + escHtml(tr(meta)) + '</span></div>';
      }).join('');
      list.querySelectorAll('[data-device-id]').forEach(function (card) {
        function choose() {
          var id = card.getAttribute('data-device-id');
          var state = getState() || {};
          setState(Object.assign({}, state, { selectedDeviceId: id }));
          onDeviceSelected(id);
        }
        card.addEventListener('click', choose);
        card.addEventListener('keydown', function (event) {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          choose();
        });
      });
      refreshIcons();
    }

    return Object.freeze({
      normalizeStatus: normalizeStatus,
      normalize: normalize,
      signature: signature,
      render: render
    });
  }

  global.ScrcpyGateMirrorDevices = Object.freeze({ create: create, normalizeStatus: normalizeStatus, normalize: normalize, signature: signature });
}(window));
