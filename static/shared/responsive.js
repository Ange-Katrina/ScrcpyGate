/**
 * ScrcpyGate 响应式交互
 * - 投屏工作台：移动端侧边栏抽屉（点击侧边栏切换按钮开合，点击遮罩关闭）
 * - 切换桌面宽度时自动清理移动端状态
 */
(function () {
  'use strict';

  var MOBILE_QUERY = '(max-width: 767px), (max-height: 500px) and (pointer: coarse)';

  function isMobile() {
    try { return window.matchMedia(MOBILE_QUERY).matches; } catch (e) { return false; }
  }

  function initDrawer() {
    var sidebar = document.getElementById('sidebar');
    // Admin pages own #admin-sidebar and its scrim in admin-shell.js.
    if (!sidebar) return;
    var toggle = document.getElementById('sidebarToggle');
    var mobileMenuBtn = document.getElementById('mobile-menu-btn');
    var mobileScrim = document.getElementById('mobile-scrim');

    function openMobileNav() {
      if (!sidebar) return;
      // 记录打开前的折叠状态：页面据此决定是否需要恢复完整展开（设备列表等）
      var wasCollapsed = sidebar.classList.contains('is-collapsed');
      // 撤销桌面折叠状态：设计稿脚本会异步（rAF / 定时器）添加 is-collapsed，
      // 且不同环境 rAF 时序不稳定，因此同步 + 多个时点兜底清除，保证抽屉内完整展开
      sidebar.classList.remove('is-collapsed');
      var deltas = [0, 60, 260];
      for (var i = 0; i < deltas.length; i++) {
        (function (d) {
          setTimeout(function () { sidebar.classList.remove('is-collapsed'); }, d);
        })(deltas[i]);
      }
      document.body.classList.add('sg-mobile-nav');
      if (mobileScrim) { mobileScrim.classList.add('open'); mobileScrim.setAttribute('aria-hidden', 'false'); }
      // 通知页面抽屉已打开（携带打开前是否折叠，供恢复设备列表等展开状态）
      try { window.dispatchEvent(new CustomEvent('sg-mobile-nav-open', { detail: { wasCollapsed: wasCollapsed } })); } catch (e) {}
    }

    function closeMobileNav() {
      document.body.classList.remove('sg-mobile-nav');
      if (mobileScrim) { mobileScrim.classList.remove('open'); mobileScrim.setAttribute('aria-hidden', 'true'); }
    }

    if (toggle) {
      toggle.addEventListener('click', function () {
        if (!isMobile()) return;
        if (document.body.classList.contains('sg-mobile-nav')) {
          closeMobileNav();
        } else {
          openMobileNav();
        }
      });
    }
    if (mobileMenuBtn) {
      mobileMenuBtn.addEventListener('click', function () {
        if (!isMobile()) return;
        if (document.body.classList.contains('sg-mobile-nav')) {
          closeMobileNav();
        } else {
          openMobileNav();
        }
      });
    }
    if (mobileScrim) {
      mobileScrim.addEventListener('click', function () { closeMobileNav(); });
    }

    // 点击遮罩 / 非侧边栏区域关闭
    document.addEventListener('click', function (e) {
      if (!isMobile() || !document.body.classList.contains('sg-mobile-nav')) return;
      if (sidebar && sidebar.contains(e.target)) return;
      if (toggle && toggle.contains(e.target)) return;
      if (mobileMenuBtn && mobileMenuBtn.contains(e.target)) return;
      closeMobileNav();
    });

    // Esc 关闭
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeMobileNav();
    });
  }

  // 回到桌面宽度时清理移动端状态
  window.addEventListener('resize', function () {
    if (!document.getElementById('sidebar')) return;
    if (!isMobile()) {
      document.body.classList.remove('sg-mobile-nav');
      var scrim = document.getElementById('mobile-scrim');
      if (scrim) { scrim.classList.remove('open'); scrim.setAttribute('aria-hidden', 'true'); }
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDrawer);
  } else {
    initDrawer();
  }
})();
