/* Shared navigation and account interactions for admin pages. */
(function () {
      'use strict';
      // Content disclosures default open on desktop. Action popovers/editors remain closed.
      // Only initialise once: resizing and refreshes must not override a user's choice.
      var compactSettings = window.matchMedia('(max-width:767px)').matches;
      document.querySelectorAll('details.admin-disclosure, details.sec-disclosure:not(.ban-editor), details.geo-help').forEach(function (details) {
        if (!details.closest('dialog')) details.open = !compactSettings;
      });
      if (window.lucide) { lucide.createIcons(); }

      function closeAllMenus() {
        document.querySelectorAll('.row-menu.open').forEach(function (m) { m.classList.remove('open'); });
      }
      function closeAccountMenu() {
        var a = document.getElementById('account-menu');
        if (a) { a.classList.remove('open'); a.querySelector('.menu-trigger').setAttribute('aria-expanded', 'false'); }
      }

      var adminThemeToggle=document.getElementById('adminThemeToggle');
      function syncAdminThemeToggle(){
        if(!adminThemeToggle) return;
        var isDark=document.documentElement.classList.contains('dark');
        var nextLabel=isDark?'切换到浅色模式':'切换到深色模式';
        adminThemeToggle.setAttribute('aria-label',nextLabel);
        adminThemeToggle.setAttribute('title',nextLabel);
        adminThemeToggle.setAttribute('aria-pressed',isDark?'true':'false');
      }
      if(adminThemeToggle){
        adminThemeToggle.addEventListener('click',function(){
          document.documentElement.classList.toggle('dark');
          try{ localStorage.setItem('scrcpygate-theme',document.documentElement.classList.contains('dark')?'dark':'light'); }catch(e){}
          syncAdminThemeToggle();
        });
      }
      try{ var adminSaved=localStorage.getItem('scrcpygate-theme'); if(adminSaved){ document.documentElement.classList.toggle('dark',adminSaved==='dark'); } }catch(e){}
      syncAdminThemeToggle();
      var sidebar = document.getElementById('admin-sidebar');
      var sToggle = document.getElementById('adminSidebarToggle');
      var railScrollbar = document.getElementById('admin-rail-scrollbar');
      if (sidebar) {
        var railSbTimer = null;
        function updateRailScrollbar() {
          if (!railScrollbar) return;
          var sh = sidebar.scrollHeight, ch = sidebar.clientHeight;
          if (sh > ch + 1) {
            var thumbH = Math.max(24, Math.round(ch * ch / sh));
            railScrollbar.style.height = thumbH + 'px';
            railScrollbar.style.top = Math.round(sidebar.scrollTop * (ch - thumbH) / (sh - ch)) + 4 + 'px';
            railScrollbar.classList.add('visible');
          } else {
            railScrollbar.classList.remove('visible', 'bright');
          }
        }
        function showRailScrollbar(bright) {
          updateRailScrollbar();
          railScrollbar.classList.toggle('bright', !!bright);
          window.clearTimeout(railSbTimer);
          railSbTimer = window.setTimeout(function () {
            railScrollbar.classList.remove('visible', 'bright');
          }, 1400);
        }
        sidebar.addEventListener('scroll', function () { showRailScrollbar(true); }, { passive: true });
        sidebar.addEventListener('mouseenter', function () { showRailScrollbar(false); });
        sidebar.addEventListener('mouseleave', function () {
          window.clearTimeout(railSbTimer);
          railScrollbar.classList.remove('visible', 'bright');
        });
        window.addEventListener('resize', updateRailScrollbar);
        updateRailScrollbar();
      }
      if (sidebar && sToggle) {
        var mobileNavigation = window.matchMedia('(max-width:1023px)');
        var mobileMenuBtn = document.getElementById('mobile-menu-btn');
        var desktopCollapsed = sidebar.classList.contains('is-collapsed');
        function setSidebarCollapsed(collapsed) {
          var mobile = mobileNavigation.matches;
          if (!mobile) desktopCollapsed = collapsed;
          if (mobile && collapsed && sidebar.contains(document.activeElement) && mobileMenuBtn) {
            mobileMenuBtn.focus();
          }
          sidebar.classList.add('is-transitioning');
          sidebar.classList.toggle('is-collapsed', collapsed);
          sidebar.inert = mobile && collapsed;
          if (mobile && collapsed) sidebar.setAttribute('aria-hidden', 'true');
          else sidebar.removeAttribute('aria-hidden');
          window.clearTimeout(sidebar._transitionTimer);
          sidebar._transitionTimer = window.setTimeout(function () {
            sidebar.classList.remove('is-transitioning');
          }, 220);
          sToggle.setAttribute('aria-expanded', String(!collapsed));
          sToggle.setAttribute('aria-label', collapsed ? '展开侧边栏' : '收起侧边栏');
          sToggle.title = collapsed ? '展开侧边栏' : '收起侧边栏';
          sToggle.innerHTML = '<i data-lucide="' + (collapsed ? 'panel-left-open' : 'panel-left-close') + '"></i>';
          if (window.lucide) { lucide.createIcons(); }
          var scrim = document.getElementById('mobile-scrim');
          if (scrim) { scrim.classList.toggle('open', mobile && !collapsed); scrim.setAttribute('aria-hidden', mobile && !collapsed ? 'false' : 'true'); }
          if (mobileMenuBtn) {
            mobileMenuBtn.setAttribute('aria-controls', sidebar.id);
            mobileMenuBtn.setAttribute('aria-expanded', String(mobile && !collapsed));
          }
          if (mobile && !collapsed) sToggle.focus();
        }
        sToggle.addEventListener('click', function () {
          setSidebarCollapsed(!sidebar.classList.contains('is-collapsed'));
        });
        if (mobileMenuBtn) {
          mobileMenuBtn.addEventListener('click', function () {
            setSidebarCollapsed(!sidebar.classList.contains('is-collapsed'));
          });
        }
        var mobileScrim = document.getElementById('mobile-scrim');
        if (mobileScrim) {
          mobileScrim.addEventListener('click', function () { setSidebarCollapsed(true); });
        }
        setSidebarCollapsed(mobileNavigation.matches ? true : desktopCollapsed);
        mobileNavigation.addEventListener('change', function () {
          setSidebarCollapsed(mobileNavigation.matches ? true : desktopCollapsed);
        });
        document.addEventListener('keydown', function (event) {
          if (event.key !== 'Escape' || event.isComposing || event.defaultPrevented) return;
          if (mobileNavigation.matches && !sidebar.classList.contains('is-collapsed')) {
            event.preventDefault();
            event.stopImmediatePropagation();
            setSidebarCollapsed(true);
          }
        });
        sidebar.querySelectorAll('.side-item').forEach(function (item) {
          item.addEventListener('click', function () {
            if (window.matchMedia('(max-width:1023px)').matches) {
              if (!sidebar.classList.contains('is-collapsed')) { setSidebarCollapsed(true); }
            } else if (sidebar.classList.contains('is-collapsed')) {
              setSidebarCollapsed(false);
            }
          });
        });
      }

      var acct = document.getElementById('account-menu');
      if (acct) {
        var trig = acct.querySelector('.menu-trigger');
        trig.addEventListener('click', function (e) {
          e.stopPropagation();
          var open = acct.classList.toggle('open');
          trig.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
      }

      document.addEventListener('click', function (e) {
        var btn = e.target.closest('.menu-open-btn');
        if (btn) {
          e.stopPropagation();
          closeAccountMenu();
          var menu = btn.closest('.row-menu');
          var pop = menu.querySelector('.menu-pop');
          var wasOpen = menu.classList.contains('open');
          closeAllMenus();
          if (!wasOpen) {
            var r = btn.getBoundingClientRect();
            var w = 176, estH = 200;
            var x = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8));
            var y = (r.bottom + estH + 8 > window.innerHeight) ? (r.top - estH - 8) : (r.bottom + 8);
            y = Math.max(8, y);
            pop.style.left = x + 'px';
            pop.style.top = y + 'px';
            menu.classList.add('open');
          }
          return;
        }
        if (!e.target.closest('.menu-panel') && !e.target.closest('#account-menu')) {
          closeAllMenus();
          closeAccountMenu();
        }
      });
      window.addEventListener('scroll', function () { closeAllMenus(); }, true);
      window.addEventListener('resize', function () { closeAllMenus(); });

      document.querySelectorAll('[data-copy-id]').forEach(function (item) {
        item.addEventListener('click', function () {
          var id = item.getAttribute('data-copy-id');
          var label = item.querySelector('span');
          var old = label.textContent;
          var done = function (ok) {
            label.textContent = ok ? '已复制' : '复制失败';
            setTimeout(function () { label.textContent = old; }, 1200);
          };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(id).then(function () { done(true); }, function () { done(false); });
          } else { done(false); }
        });
      });

      // Keep wide data tables discoverable and keyboard-scrollable without changing their data.
      document.querySelectorAll('[data-scroll-table]').forEach(function (region) {
        var note = document.createElement('p');
        note.className = 'table-scroll-note';
        var message = '横向滚动查看完整列与操作';
        note.textContent = window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(message) : message;
        region.insertAdjacentElement('afterend', note);
        function syncOverflow() { note.hidden = region.scrollWidth <= region.clientWidth + 1; }
        if (window.ResizeObserver) {
          var observer = new ResizeObserver(syncOverflow);
          observer.observe(region);
          if (region.firstElementChild) observer.observe(region.firstElementChild);
        }
        window.addEventListener('resize', syncOverflow);
        syncOverflow();
      });
    })();
