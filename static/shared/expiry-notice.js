/*
 * 账户到期提醒弹窗。
 *
 * 服务端在 /api/me（以及登录响应）里已经给出 expiration_state / expires_at /
 * remaining_seconds，其中「即将到期」的窗口就是后台设置「到期提醒提前天数」；
 * 这里只负责在用户自己的页面上把它变成一次可见的提醒：每个到期日每天最多弹一次
 * （点「知道了」记在 localStorage），主动改密流程（must_change_password）不再叠加弹窗。
 *
 * 仅在已登录页面注入（/login 不加载），所有页面共用同一份脚本与样式。
 */
(function (window, document) {
  'use strict';

  if (!window || !document) return;
  if (window.ScrcpyGateExpiryNotice) return;

  var STORAGE_PREFIX = 'scrcpygate-expiry-notice:';
  var NOTIFY_STATES = ['expiring', 'expired'];

  function text(value) {
    return value === undefined || value === null ? '' : String(value);
  }

  function pad2(value) {
    return (value < 10 ? '0' : '') + value;
  }

  function localDay(now) {
    var date = new Date(now || Date.now());
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function formatDate(epochSeconds) {
    var numeric = Number(epochSeconds);
    if (!isFinite(numeric) || numeric <= 0) return '';
    var date = new Date(numeric * 1000);
    if (isNaN(date.getTime())) return '';
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function daysLeft(user, now) {
    var remaining = Number(user && user.remaining_seconds);
    if (!isFinite(remaining)) remaining = 0;
    if (remaining > 0) return Math.max(1, Math.ceil(remaining / 86400));
    var expiresAt = Number(user && user.expires_at);
    if (!isFinite(expiresAt) || expiresAt <= 0) return 0;
    var diff = expiresAt - Math.floor((now || Date.now()) / 1000);
    return diff > 0 ? Math.max(1, Math.ceil(diff / 86400)) : 0;
  }

  function dismissKey(user, now) {
    return STORAGE_PREFIX + text(user && user.expires_at) + ':' + localDay(now);
  }

  function readStore(store) {
    try {
      if (!store) return null;
      var probe = '__scrcpygate_probe__';
      store.setItem(probe, '1');
      store.removeItem(probe);
      return store;
    } catch (error) {
      return null;
    }
  }

  function storage() {
    return readStore(window.localStorage);
  }

  function decision(user, now, store) {
    var stamp = now || Date.now();
    if (!user || !text(user.username)) return { notify: false, reason: 'no-user' };
    var state = text(user.expiration_state).toLowerCase();
    if (NOTIFY_STATES.indexOf(state) < 0) {
      return { notify: false, reason: 'not-expiring', state: state };
    }
    if (state === 'expiring' && !(Number(user.remaining_seconds) > 0)) {
      return { notify: false, reason: 'no-remaining', state: state };
    }
    if (user.must_change_password) return { notify: false, reason: 'password-change-pending', state: state };
    var key = dismissKey(user, stamp);
    if (store && store.getItem(key)) return { notify: false, reason: 'dismissed', state: state, key: key };
    return {
      notify: true,
      state: state,
      days: daysLeft(user, stamp),
      expiresAt: formatDate(user.expires_at),
      key: key
    };
  }

  function noticeText(plan) {
    var days = Number(plan.days) || 0;
    var date = text(plan.expiresAt);
    if (plan.state === 'expired') {
      return {
        title: '账户已到期',
        body: (date ? '你的账户已于 ' + date + ' 到期。' : '你的账户已到期。')
          + '设备投屏与 ALAS 权限已失效，请联系管理员续期。'
      };
    }
    return {
      title: '账户即将到期',
      body: (date ? '你的账户将于 ' + date + ' 到期' : '你的账户即将到期')
        + (days > 0 ? '，还剩 ' + days + ' 天。' : '。')
        + '到期后设备投屏与 ALAS 权限会立即失效，请联系管理员续期。'
    };
  }

  var activeMask = null;

  function dismiss(user) {
    var store = storage();
    var plan = decision(user || currentUser(), Date.now(), null);
    var key = plan.key || dismissKey(user || {}, Date.now());
    if (store) {
      try { store.setItem(key, '1'); } catch (error) { /* 隐私模式：只在本次会话内记住 */ }
    }
    close();
    return key;
  }

  function close() {
    var mask = activeMask;
    activeMask = null;
    if (mask && mask.parentNode) mask.parentNode.removeChild(mask);
  }

  function buildElement(tag, className, content) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function mount(user, plan) {
    if (activeMask || !document.body) return null;
    var copy = noticeText(plan);
    var mask = buildElement('div', 'expiry-notice-mask');
    mask.setAttribute('data-expiry-notice-state', plan.state);
    var card = buildElement('div', 'expiry-notice-card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-labelledby', 'expiry-notice-title');
    card.setAttribute('aria-describedby', 'expiry-notice-body');
    var head = buildElement('div', 'expiry-notice-head');
    var icon = buildElement('span', 'expiry-notice-icon', '!');
    icon.setAttribute('aria-hidden', 'true');
    var title = buildElement('h2', 'expiry-notice-title', copy.title);
    title.id = 'expiry-notice-title';
    head.appendChild(icon);
    head.appendChild(title);
    var body = buildElement('p', 'expiry-notice-body', copy.body);
    body.id = 'expiry-notice-body';
    var actions = buildElement('div', 'expiry-notice-actions');
    var confirm = buildElement('button', 'expiry-notice-btn', '知道了（今天不再提醒）');
    confirm.type = 'button';
    confirm.id = 'expiry-notice-dismiss';
    confirm.addEventListener('click', function () { dismiss(user); });
    actions.appendChild(confirm);
    card.appendChild(head);
    card.appendChild(body);
    card.appendChild(actions);
    mask.appendChild(card);
    document.body.appendChild(mask);
    activeMask = mask;
    try { confirm.focus(); } catch (error) { /* 无头/测试环境没有焦点 */ }
    return mask;
  }

  function currentUser() {
    var session = window.ScrcpyGateSession;
    return session && typeof session.current === 'function' ? session.current() : null;
  }

  function whenReady() {
    if (document.readyState === 'loading') {
      return new Promise(function (resolve) {
        document.addEventListener('DOMContentLoaded', resolve);
      });
    }
    return Promise.resolve();
  }

  function userPromise() {
    return whenReady().then(function () {
      var existing = currentUser();
      if (existing) return existing;
      var session = window.ScrcpyGateSession;
      var pending = session && session.state && session.state.promise;
      if (pending && typeof pending.then === 'function') {
        return pending.then(function (user) { return user || null; }, function () { return null; });
      }
      return null;
    });
  }

  function run(now) {
    return userPromise().then(function (user) {
      var plan = decision(user, now || Date.now(), storage());
      if (plan.notify) {
        plan.mounted = !!mount(user, plan);
        plan.user = user;
      }
      return plan;
    });
  }

  document.addEventListener('keydown', function (event) {
    if (!activeMask) return;
    if (event && (event.key === 'Escape' || event.key === 'Esc')) {
      event.preventDefault();
      dismiss();
    }
  });

  window.ScrcpyGateExpiryNotice = {
    decision: decision,
    dismiss: dismiss,
    run: run,
    close: close,
    currentUser: currentUser,
    _dismissKey: dismissKey,
    _daysLeft: daysLeft,
    _formatDate: formatDate,
    _noticeText: noticeText
  };

  whenReady().then(function () { run(); });
})(window, document);
