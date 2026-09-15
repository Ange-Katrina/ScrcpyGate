(function (window, document) {
  'use strict';

  var state = {
    user: null,
    loaded: false,
    error: null,
    promise: null
  };

  function unwrap(payload) {
    if (!payload || typeof payload !== 'object') return null;
    return payload.user || (payload.data && payload.data.user) || payload.data || payload;
  }

  function value(user, keys, fallback) {
    if (!user) return fallback;
    for (var i = 0; i < keys.length; i++) {
      if (user[keys[i]] !== undefined && user[keys[i]] !== null && user[keys[i]] !== '') return String(user[keys[i]]);
    }
    return fallback;
  }

  function initials(label) {
    var text = String(label || '?').trim();
    return text ? text.substring(0, 1).toUpperCase() : '?';
  }

  function isAdmin(user) {
    return !!user && (user.roleKey === 'admin' || user.role === '管理员' || user.isAdmin === true);
  }

  function applyRoleUI(user) {
    // 管理员专属入口:普通用户隐藏
    document.querySelectorAll('[data-admin-only]').forEach(function (el) {
      el.style.display = isAdmin(user) ? '' : 'none';
    });
    // 管理页守卫:非管理员显示无权限遮罩
    var adminPage = document.querySelector('[data-admin-page]');
    if (adminPage && !isAdmin(user)) {
      if (!document.getElementById('sg-admin-guard')) {
        var guard = document.createElement('div');
        guard.id = 'sg-admin-guard';
        guard.setAttribute('role', 'alert');
        guard.innerHTML =
          '<style>#sg-admin-guard{position:fixed;inset:0;z-index:120;display:flex;align-items:center;justify-content:center;background:var(--carbon-ambient,var(--admin-bg,#f2f2f7));}' +
          '#sg-admin-guard .guard-box{display:flex;flex-direction:column;align-items:center;gap:10px;max-width:min(92vw,360px);padding:32px 28px;border:1px solid var(--admin-border,rgba(0,0,0,.08));border-radius:16px;background:var(--admin-surface,var(--c-surface,#fff));box-shadow:0 24px 64px -12px rgba(0,0,0,.12);text-align:center;}' +
          '#sg-admin-guard .guard-box [data-lucide]{width:34px;height:34px;color:var(--trend-warn,#ff9f0a);}' +
          '#sg-admin-guard .guard-box b{font-size:16px;font-weight:700;color:var(--admin-text,var(--c-text,#1d1d1f));}' +
          '#sg-admin-guard .guard-box span{font-size:12.5px;color:var(--admin-muted,#6e6e73);line-height:1.55;}' +
          '#sg-admin-guard .guard-box a{display:inline-flex;align-items:center;justify-content:center;height:36px;margin-top:6px;padding:0 18px;border-radius:999px;background:var(--admin-brand,#007aff);color:#fff;font-size:13px;font-weight:600;text-decoration:none;}' +
          '</style>' +
          '<div class="guard-box"><i data-lucide="shield-off"></i><b>无管理权限</b><span>当前账号为普通用户，无法访问管理后台。请联系管理员开通权限，或返回投屏工作台。</span><a href="/mirror">返回投屏工作台</a></div>';
        adminPage.style.display = 'none';
        adminPage.parentNode.insertBefore(guard, adminPage);
        if (window.lucide) window.lucide.createIcons();
      }
    }
  }

  function render(user) {
    var displayName = value(user, ['displayName', 'name', 'username'], '当前账户');
    var username = value(user, ['email', 'username', 'handle'], '当前账户');
    var role = value(user, ['roleLabel', 'role'], '');
    var avatar = initials(displayName);

    document.querySelectorAll('.user-copy b, .menu-head b').forEach(function (el) {
      el.textContent = displayName;
    });
    document.querySelectorAll('.user-copy span, .menu-head div span').forEach(function (el) {
      el.textContent = role ? username + ' · ' + role : username;
    });
    document.querySelectorAll('.admin-avatar, .user-avatar').forEach(function (el) {
      if (!el.hasAttribute('data-static-avatar')) el.textContent = avatar;
    });
    document.querySelectorAll('[data-session-display-name]').forEach(function (el) {
      el.textContent = displayName;
    });
    document.querySelectorAll('[data-session-username]').forEach(function (el) {
      el.textContent = username;
    });
    document.querySelectorAll('[data-session-role]').forEach(function (el) {
      el.textContent = role;
    });
    // 投屏工作台顶栏(.user-meta 结构)
    document.querySelectorAll('.user-meta strong').forEach(function (el) {
      el.textContent = displayName;
    });
    document.querySelectorAll('.user-meta small').forEach(function (el) {
      el.textContent = role || '';
    });
  }

  function load(options) {
    options = options || {};
    if (state.promise && !options.force) return state.promise;
    state.promise = window.ScrcpyGateApi.get('session.current', {
      timeout: options.timeout
    }).then(function (payload) {
      state.user = unwrap(payload);
      state.loaded = true;
      state.error = null;
      render(state.user);
      applyRoleUI(state.user);
      return state.user;
    }).catch(function (error) {
      state.loaded = false;
      state.error = error;
      render(null);
      applyRoleUI(null);
      throw error;
    });
    return state.promise;
  }

  function start(options) {
    if (!window.ScrcpyGateApi) return Promise.reject(new Error('API client unavailable'));
    return load(options);
  }

  window.ScrcpyGateSession = {
    state: state,
    load: load,
    start: start,
    render: render,
    current: function () { return state.user; }
  };

  document.addEventListener('DOMContentLoaded', function () {
    start().catch(function () { /* The page renders its own unavailable state. */ });
  });
})(window, document);
