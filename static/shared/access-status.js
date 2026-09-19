/* Shared, bounded diagnosis for opaque WebSocket handshake failures. */
(function (global) {
  'use strict';
  var pending = null;
  var lastResult = null;
  var checkedAt = 0;

  function tr(text) {
    return global.ScrcpyGateI18n ? global.ScrcpyGateI18n.t(text) : text;
  }

  function notify(result) {
    if (!global.document || !document.body) return;
    var banner = document.getElementById('access-connection-notice');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'access-connection-notice';
      banner.setAttribute('role', 'alert');
      banner.style.cssText = 'position:fixed;bottom:16px;left:16px;right:16px;z-index:100000;padding:16px;border-radius:12px;background:#262626;color:#fff;box-shadow:0 4px 24px #0004';
      var text = document.createElement('span');
      var retry = document.createElement('button');
      retry.type = 'button';
      retry.style.marginLeft = '12px';
      retry.textContent = tr('重新连接');
      retry.addEventListener('click', function () { global.location.reload(); });
      banner.appendChild(text);
      banner.appendChild(retry);
      document.body.appendChild(banner);
    }
    banner.firstChild.textContent = result.message || tr('连接已停止，请检查访问权限或稍后重试。');
  }

  function check() {
    if (pending) return pending;
    if (lastResult && Date.now() - checkedAt < 2000) return Promise.resolve(lastResult);
    var controller = new AbortController();
    var timer = global.setTimeout(function () { controller.abort(); }, 5000);
    pending = global.fetch('/api/access-status', {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal
    }).then(function (response) {
      if (response.ok) return { blocked: false };
      return response.json().catch(function () { return {}; }).then(function (data) {
        var blocked = [401, 403, 429, 503].indexOf(response.status) >= 0;
        return { blocked: blocked, code: data.code || 'access_denied', message: typeof data.detail === 'string' ? data.detail : '' };
      });
    }).catch(function () { return { blocked: false, networkError: true }; }).then(function (result) {
      global.clearTimeout(timer);
      pending = null;
      lastResult = result;
      checkedAt = Date.now();
      if (result.blocked) notify(result);
      return result;
    });
    return pending;
  }
  global.ScrcpyGateAccess = { check: check, notify: notify };
})(window);
