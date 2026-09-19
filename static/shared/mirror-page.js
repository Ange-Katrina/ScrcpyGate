/* Extracted from static/pages/mirror.html. */
document.addEventListener('DOMContentLoaded',function(){
      lucide.createIcons();
      var sidebar=document.getElementById('sidebar');
      var themeToggle=document.getElementById('themeToggle');
      if(themeToggle){
        themeToggle.addEventListener('click',function(){
          document.documentElement.classList.toggle('dark');
          try{ localStorage.setItem('scrcpygate-theme',document.documentElement.classList.contains('dark')?'dark':'light'); }catch(e){}
        });
      }
      try{ var savedTheme=localStorage.getItem('scrcpygate-theme'); if(savedTheme){ document.documentElement.classList.toggle('dark',savedTheme==='dark'); } }catch(e){}
      var toggle=document.getElementById('sidebarToggle');
      var app=document.querySelector('.app');
      var fullscreenActive=false;
      var fullscreenNative=false;
      var fullscreenOrientationTarget='';
      function syncKeyboardViewport(){
        if(!app) return;
        var vv=window.visualViewport;
        var inset=0;
        if(vv){
          var bottom=Number(vv.offsetTop||0)+Number(vv.height||window.innerHeight||0);
          inset=Math.max(0,Math.round(Number(window.innerHeight||0)-bottom));
        }
        app.style.setProperty('--sg-keyboard-inset',inset+'px');
      }
      if(window.visualViewport&&window.visualViewport.addEventListener){
        window.visualViewport.addEventListener('resize',syncKeyboardViewport);
        window.visualViewport.addEventListener('scroll',syncKeyboardViewport);
      }
      syncKeyboardViewport();
      var deviceList=document.getElementById('device-list');
      var deviceScrollbar=document.getElementById('device-scrollbar');
      if(deviceList){
        var sbTimer=null;
        function updateDeviceScrollbar(){
          if(!deviceScrollbar) return;
          var sh=deviceList.scrollHeight,ch=deviceList.clientHeight;
          var overflow=sh>ch+1;
          if(overflow){
            var trackH=ch;
            var thumbH=Math.max(24,Math.round(ch*ch/sh));
            deviceScrollbar.style.height=thumbH+'px';
            deviceScrollbar.style.top=Math.round(deviceList.scrollTop*(ch-thumbH)/(sh-ch))+4+'px';
            deviceScrollbar.classList.add('visible');
          }else{
            deviceScrollbar.classList.remove('visible','bright');
          }
        }
        function showDeviceScrollbar(bright){
          if(!deviceList.classList.contains('device-list-show')) return;
          updateDeviceScrollbar();
          deviceScrollbar.classList.toggle('bright',!!bright);
          window.clearTimeout(sbTimer);
          sbTimer=window.setTimeout(function(){ deviceScrollbar.classList.remove('visible','bright'); },1400);
        }
        deviceList.addEventListener('scroll',function(){ showDeviceScrollbar(true); },{passive:true});
        deviceList.addEventListener('mouseenter',function(){ showDeviceScrollbar(false); });
        deviceList.addEventListener('mouseleave',function(){ window.clearTimeout(sbTimer); deviceScrollbar.classList.remove('visible','bright'); });
        window.addEventListener('resize',updateDeviceScrollbar);
        updateDeviceScrollbar();
        deviceScrollbar.classList.remove('visible','bright');
      }
      requestAnimationFrame(function(){ if(deviceList){ deviceList.classList.add('device-list-show'); } });
      function setCollapsed(collapsed){
        sidebar.classList.add('is-transitioning');
        app.classList.add('sidebar-transitioning');
        if(deviceList){ deviceList.classList.remove('device-list-show'); }
        requestAnimationFrame(function(){
          sidebar.classList.toggle('is-collapsed',collapsed);
          app.classList.toggle('sidebar-collapsed',collapsed);
        });
        window.clearTimeout(sidebar._transitionTimer);
        sidebar._transitionTimer=window.setTimeout(function(){
          sidebar.classList.remove('is-transitioning');
          app.classList.remove('sidebar-transitioning');
          if(!collapsed&&deviceList){
            deviceList.classList.add('device-list-show');
          }
        },220);
        toggle.setAttribute('aria-expanded',String(!collapsed));
        toggle.setAttribute('aria-label',collapsed?'展开侧边栏':'收起侧边栏');
        toggle.title=collapsed?'展开侧边栏':'收起侧边栏';
        toggle.innerHTML='<i data-lucide="'+(collapsed?'panel-left-open':'panel-left-close')+'"></i>';
        lucide.createIcons();
      }
      toggle.addEventListener('click',function(){
        setCollapsed(!sidebar.classList.contains('is-collapsed'));
      });
      window.addEventListener('sg-mobile-nav-open',function(e){
        if(e.detail && e.detail.wasCollapsed) setCollapsed(false);
      });

      /* ---------- 通用工具 ---------- */
      var toast=document.getElementById('toast');
      var toastTimer=null;
      function tr(msg){ return window.ScrcpyGateI18n ? window.ScrcpyGateI18n.t(String(msg == null ? '' : msg)) : String(msg == null ? '' : msg); }
      function showToast(msg){
        if(!toast) return;
        toast.textContent=tr(msg);
        toast.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer=setTimeout(function(){ toast.classList.remove('show'); },2600);
      }
      var moreMenuReturnFocus=null;
      // 画质胶囊:配置值(qualityMetaBase)与客户端实测码率(measuredMbps)分开维护,
      // 这样切换设备或打开画质面板时都能保留实时读数。
      var qualityMetaBase='—';
      var measuredMbps=0;
      // 实测码率只在「投屏中」展示：投屏期间**始终**保留这一格（低到 0.0 也不隐藏），
      // 停止投屏才收起。以前 measuredMbps<=0 就把整格去掉，结果读数在低位时
      // 一会出现一会消失，状态条宽度跟着跳。
      var rateShown=false;
      function renderQualityMeta(){
        var el=document.getElementById('mirror-quality-meta');
        if(!el) return;
        var hasBase=!!qualityMetaBase&&qualityMetaBase!=='—';
        if(!(rateShown&&hasBase)){ el.textContent=qualityMetaBase; el.removeAttribute('title'); return; }
        // 配置码率/实测码率:实测值单独着色;移动端省略单位,避免状态栏尾部被挤出可视区。
        var compact=window.matchMedia&&window.matchMedia('(max-width:767px)').matches;
        el.textContent=qualityMetaBase+'/';
        el.title=tr('配置码率/实测码率');
        var rate=document.createElement('span');
        rate.className='pill-rate';
        rate.textContent=measuredMbps.toFixed(1)+(compact?'':' Mbps');
        el.appendChild(rate);
      }
      /* 实测帧率：紧挨分辨率显示（用户要求「分辨率旁边增加帧数」）。
         投屏中始终占位（0 也显示），停止后收起；明显掉帧时变色。 */
      var measuredFps=0;
      var fpsShown=false;
      function renderQualityFps(){
        var el=document.getElementById('mirror-fps');
        if(!el) return;
        if(!fpsShown){ el.hidden=true; el.textContent='—'; el.classList.remove('is-low'); return; }
        el.hidden=false;
        el.textContent=Math.round(Number(measuredFps)||0)+' fps';
        el.title=tr('实测帧率（客户端每秒收到的帧数）');
        el.classList.toggle('is-low',Number(measuredFps)>0&&Number(measuredFps)<20);
      }
      function closeMoreMenu(restoreFocus){
        var pop=document.getElementById('cb-pop');
        var btn=document.getElementById('cb-more-btn');
        var wasOpen=!!(pop&&pop.classList.contains('open'));
        var focusInside=!!(pop&&pop.contains(document.activeElement));
        if(pop) pop.classList.remove('open');
        if(btn) btn.setAttribute('aria-expanded','false');
        if(restoreFocus && wasOpen && (focusInside||moreMenuReturnFocus===btn)){
          if(btn && !btn.disabled && typeof btn.focus==='function') btn.focus();
          else if(focusInside && document.activeElement && typeof document.activeElement.blur==='function') document.activeElement.blur();
        }
        moreMenuReturnFocus=null;
        if(wasOpen) scheduleDockDensity();
      }
      function closeFixedPops(except){
        var morePop=document.getElementById('cb-pop');
        if(morePop&&morePop!==except) closeMoreMenu(false);
        var notifyPop=document.getElementById('notify-pop');
        if(notifyPop&&notifyPop!==except) notifyPop.classList.remove('open');
        var nb=document.getElementById('notify-btn');
        if(nb) nb.setAttribute('aria-expanded','false');
      }
      document.addEventListener('keydown',function(e){
        if(e.key==='Escape'){
          closeFixedPops(); vpClose(); apClose(); upClose(); takeoverClose(); alasFloatClose();
          if(fullscreenActive){
            e.preventDefault();
            exitFullscreen();
          }
        }
      });

      /* ---------- 侧栏菜单 ---------- */
      var menuItems=sidebar.querySelectorAll('.menu-item');
      menuItems.forEach(function(item){
        if(item.id==='acct-item'||item.id==='visual-item'||item.id==='alas-item') return;
        // 「投屏记录」的高亮不跟点击走：它必须反映「现在是否真的在记录」，
        // 否则打开过一次就永远蓝着（用户反馈）。点击只负责打开面板。
        if(item.id==='record-item') return;
        item.addEventListener('click',function(event){
          var isExternal=item.getAttribute('href')!=='#';
          if(!isExternal) event.preventDefault();
          menuItems.forEach(function(m){ m.classList.remove('active'); m.removeAttribute('aria-current'); });
          item.classList.add('active');
          item.setAttribute('aria-current','page');
          if(sidebar.classList.contains('is-collapsed')){
            setCollapsed(false);
          }else{
            setCollapsed(true);
          }
        });
      });

      /* ---------- 通知中心 ---------- */
      var mirrorNotifications = window.ScrcpyGateMirrorNotifications && window.ScrcpyGateMirrorNotifications.create
        ? window.ScrcpyGateMirrorNotifications.create({
          tr: tr,
          escHtml: function(value){ return String(value == null ? '' : value).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); },
          api: window.ScrcpyGateApi,
          dashboardLabels: window.ScrcpyGateDashboard || {},
          closeFixedPops: closeFixedPops,
          refreshIcons: function(){ if(window.lucide) lucide.createIcons(); }
        })
        : null;
      function loadNotifications(){ return mirrorNotifications ? mirrorNotifications.load() : Promise.resolve(null); }
      /* ---------- 真实设备与会话状态 ---------- */
      var videoBox=document.getElementById('video-box');
      var videoSurface=document.getElementById('video-surface');
      /* 显示状态层：画面方向（自动摆正）/ 源尺寸与可用空间（static/shared/display-control.js）。
         纯状态模块，DOM 相关的测量仍留在本文件里。 */
      var displayController=window.ScrcpyGateDisplay&&typeof window.ScrcpyGateDisplay.create==='function'
        ?window.ScrcpyGateDisplay.create():null;
      var mirrorPanel=document.getElementById('mirror-panel');
      var workspace=document.getElementById('workspace');
      var overlays=Array.prototype.slice.call(document.querySelectorAll('.conn-overlay'));
      var cdBlock=document.getElementById('cd-block');
      var cdSep=document.getElementById('cd-sep');
      var cdTime=document.getElementById('cd-time');
      var cdBanner=document.getElementById('countdown-banner');
      var cdBannerText=document.getElementById('cd-banner-text');
      var navBtns=['cb-back','cb-home','cb-tasks'].map(function(id){return document.getElementById(id);});
      var watchBtn=document.getElementById('cb-watch');
      var moreBtn=document.getElementById('cb-more-btn');
      var acquireBtn=document.getElementById('cb-acquire');
      var keyboardBtn=document.getElementById('cb-keyboard');
      var fullscreenBtn=document.getElementById('cb-fullscreen');
      var rotateBtn=document.getElementById('cb-rotate');
      var rotateState=document.getElementById('cb-rotate-state');
      var popShot=document.getElementById('cb-pop-shot');
      var popKeyboard=document.getElementById('cb-pop-keyboard');
      var popAutoControl=document.getElementById('cb-pop-auto-control');
      var ctrlDock=document.getElementById('ctrl-dock');
      var fullscreenDockToggle=document.getElementById('fullscreen-dock-toggle');
      var occupiedChip=document.getElementById('cb-occupied');
      var ctrlState=document.getElementById('ctrl-state');
      var takeoverLayer=document.getElementById('control-takeover-layer');
      var takeoverDialog=document.getElementById('control-takeover-dialog');
      var takeoverScrim=document.getElementById('control-takeover-scrim');
      var takeoverCloseBtn=document.getElementById('control-takeover-close');
      var takeoverCancelBtn=document.getElementById('control-takeover-cancel');
      var takeoverConfirmBtn=document.getElementById('control-takeover-confirm');
      var takeoverStatus=document.getElementById('control-takeover-status');
      var takeoverReturnFocus=null;
      var takeoverBusy=false;
      var controlBusy=false;
      var navBusy=false;
      var moreActionBusy=false;
      var keyboardOn=false;
      var dockHidden=false;
      var windowDockHidden=false;
      var controlState='unknown';
      // 控制权持有者（用户名）：状态栏「控制权」胶囊显示「我 / 某人 / 空闲」。
      var controlOwner='';
      var watchState='idle';
      var remaining=0;
      var cdTimer=null;
      var viewerStopWarning=false;
      var baseDocumentTitle=document.title;
      var selectedDevice=null;
      var deviceItems=[];
      var deviceLoadError=false;
      var deviceListStale=false;
      var deviceRenderSignature=null;
      var viewMode='single';
      var VIEW_MODE_KEY='scrcpygate-view-mode';
      function savedViewMode(){ try{ return localStorage.getItem(VIEW_MODE_KEY)==='grid'?'grid':'single'; }catch(e){ return 'single'; } }
      function persistViewMode(mode){ try{ localStorage.setItem(VIEW_MODE_KEY,mode); }catch(e){} }
      var gridView=null;
      var currentSession=null;
      var watchRequestGeneration=0;
      var mirrorRefreshTimer=null;
      var MIRROR_REFRESH_MS=60000;
      var notificationRefreshTimer=null;
      var apiErrorText=function(error){
        // 适配层的错误（控制通道未连接 / 控制请求超时 / 控制通道已断开 …）只有 message、
        // 没有 HTTP code，以前一律被 errorMessage() 归到通用「请求失败，请稍后重试」，
        // 用户看不到真正原因（「点获取控制还是请求失败」就是这么来的）。
        if(error&&!error.code&&typeof error.message==='string'&&error.message) return tr(error.message);
        return tr(window.ScrcpyGateApi ? window.ScrcpyGateApi.errorMessage(error) : '数据服务不可用');
      };
      function listPayload(payload){ return window.ScrcpyGateApi.list(payload); }
      function firstConfigured(names){
        for(var i=0;i<names.length;i++){ if(window.ScrcpyGateApi && window.ScrcpyGateApi.isConfigured(names[i])) return names[i]; }
        return '';
      }
      function configuredCall(names, options){
        var endpoint=Array.isArray(names)?firstConfigured(names):names;
        if(!endpoint){ return Promise.reject(new Error('接口尚未配置：'+(Array.isArray(names)?names[0]:names))); }
        return window.ScrcpyGateApi.configured(endpoint,options||{});
      }
      function escHtml(value){ return String(value == null ? '' : value).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];}); }
      var mirrorDeviceTools=window.ScrcpyGateMirrorDevices&&window.ScrcpyGateMirrorDevices.create
        ? window.ScrcpyGateMirrorDevices.create({
          tr:tr,
          escHtml:escHtml,
          getState:function(){ return { selectedDeviceId:selectedDevice&&selectedDevice.id }; },
          setState:function() {},
          onDeviceSelected:function(id){ if(typeof selectDevice==='function') selectDevice(id); },
          refreshIcons:function(){ if(window.lucide) lucide.createIcons(); }
        })
        : null;
      var mirrorControlTools=window.ScrcpyGateMirrorControls&&window.ScrcpyGateMirrorControls.create
        ? window.ScrcpyGateMirrorControls.create({
          getState:function(){ return { watchState:watchState, controlState:controlState, controlBusy:controlBusy, navBusy:navBusy, moreActionBusy:moreActionBusy, keyboardOn:keyboardOn, dockHidden:dockHidden }; },
          setState:function(next){
            if(!next) return;
            if(Object.prototype.hasOwnProperty.call(next,'keyboardOn')) keyboardOn=!!next.keyboardOn;
            if(Object.prototype.hasOwnProperty.call(next,'dockHidden')) dockHidden=!!next.dockHidden;
          },
          onControlAction:function() {}
        })
        : null;
      function normalizeMirrorStatus(d){
        if(mirrorDeviceTools) return mirrorDeviceTools.normalizeStatus(d);
        d=d||{};
        var raw=String(d.status||d.state||d.health||'').trim().toLowerCase();
        if(!raw&&typeof d.online==='boolean') return d.online?'online':'offline';
        if(['online','healthy','connected','running','ready','ok'].indexOf(raw)>=0) return 'online';
        if(['offline','disconnected','unreachable','error','failed'].indexOf(raw)>=0) return 'offline';
        return 'unknown';
      }
      function mirrorStatusText(d){ return d && d.online===true ? '在线' : (d && d.online===false ? '离线' : '未检查'); }
      function normalizeMirrorDevice(d){
        if(mirrorDeviceTools) return mirrorDeviceTools.normalize(d);
        var status=normalizeMirrorStatus(d);
        return { id:d.id, name:d.name || d.displayName || d.serial || d.id || '未命名设备', model:d.model || d.product || '—', status:status, online:status==='online' ? true : (status==='offline' ? false : null), permission:d.permission !== false && d.noPermission !== true, viewers:d.viewerCount == null ? (d.viewers || 0) : d.viewerCount, controller:d.controllerName || d.controller || null, alas:d.alasStatus || d.alas && d.alas.status || '未加载', quality:d.quality || null };
      }
      function deviceListSignature(items, loadError){
        if(mirrorDeviceTools) return mirrorDeviceTools.signature(items,loadError);
        return JSON.stringify({
          error:!!loadError,
          devices:(items || []).map(function(d){
            return [d.id,d.name,d.model,d.status,!!d.online,!!d.permission,d.viewers,d.controller || '',d.alas || '',d.quality && d.quality.name || '',d.quality && d.quality.meta || ''];
          })
        });
      }
      function deviceById(id){ return deviceItems.filter(function(d){ return d.id===id; })[0] || null; }
      function currentMirrorUsername(){
        var u=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null;
        return u&&u.username ? String(u.username) : '';
      }
      /* 服务端快照里的 control_lock 只有用户名，没有 client_id：
         「持有者是我」并不等于「这条连接持有」——同一账号在别的标签页/浏览器里的锁也会显示成我。
         所以只有在本地控制通道确实持有（controlOwnership）时才认 'self'；
         同账号的另一端也按 'other' 处理，接管前同样需要确认。 */
      function selfHoldsControl(){
        return !!(window.ScrcpyGateV2&&window.ScrcpyGateV2.state&&window.ScrcpyGateV2.state.controlOwnership===true);
      }
      function snapshotControlState(owner){
        if(selfHoldsControl()) return 'self';
        var name=owner?String(owner):'';
        if(!name) return 'free';
        return 'other';
      }
      /* 控制权的持有者用户名（状态栏要显示「谁在控制」）。 */
      function noteControlOwner(owner){
        var name=owner?String(owner):'';
        if(name) controlOwner=name;
        else if(controlState!=='other') controlOwner='';
        return controlOwner;
      }
      function deviceControlState(d){
        return snapshotControlState(d&&d.controller?String(d.controller):'');
      }
      function syncAdminOnly(){
        var u=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null;
        var admin=!!u&&(u.roleKey==='admin'||u.role==='管理员'||u.isAdmin===true);
        var list=document.getElementById('device-list');
        if(!list) return;
        list.querySelectorAll('[data-admin-only]').forEach(function(el){ el.style.display=admin?'':'none'; });
      }

      /* ---------- 宫格视图（管理员：一次显示全部设备） ---------- */
      function mirrorIsAdmin(){
        var u=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null;
        return !!u&&(u.roleKey==='admin'||u.role==='管理员'||u.isAdmin===true);
      }
      function ensureGridView(){
        if(gridView) return gridView;
        if(!window.ScrcpyGateMirrorGrid||typeof window.ScrcpyGateMirrorGrid.create!=='function') return null;
        var host=document.getElementById('mg-grid');
        if(!host) return null;
        gridView=window.ScrcpyGateMirrorGrid.create({
          tr:tr,
          escHtml:escHtml,
          refreshIcons:function(){ if(window.lucide) lucide.createIcons(); },
          maxLive:GRID_MAX_LIVE,
          slotCount:GRID_SLOT_COUNT,
          onOpenDevice:function(deviceId,options){ openDeviceFromGrid(deviceId,options); },
          onAddDevice:function(){ addAdbDevice(); },
          onNotice:function(message){ showToast(message); },
          // 卡片实际宽度变化时刷新缩放标签。
          onTileWidth:function(){ refreshGridZoomLabel(); }
        });
        gridView.mount(host,{
          startAll:document.getElementById('mg-start-all'),
          stopAll:document.getElementById('mg-stop-all'),
          refreshAll:document.getElementById('mg-refresh-streams')
        });
        gridView.setThumbnailFps(pendingGridFps||1);
        gridView.setMaxLive(GRID_MAX_LIVE);
        gridView.setOrientation(pendingGridOrientation||'portrait');
        return gridView;
      }
      function syncViewSwitch(){
        var wrap=document.getElementById('view-switch');
        if(!wrap) return;
        var admin=mirrorIsAdmin();
        wrap.hidden=!admin;
        if(!admin&&viewMode==='grid') setViewMode('single');
      }
      function setViewMode(mode){
        var next=mode==='grid'?'grid':'single';
        if(next===viewMode) return Promise.resolve();
        var app=document.querySelector('.app');
        var gridEl=document.getElementById('mirror-grid');
        var stopped=Promise.resolve();
        if(next==='grid'){
          var pending=watchState==='playing'||watchState==='connecting'||watchState==='waiting'||watchState==='websocket'||watchState==='hello'||watchState==='keyframe'||watchState==='resuming';
          if(pending) cancelCurrentWatch();
          viewMode='grid';
          if(app) app.classList.add('is-grid');
          if(gridEl) gridEl.hidden=false;
          var grid=ensureGridView();
          if(grid){ grid.setActive(true); grid.render(deviceItems); }
        }else{
          viewMode='single';
          if(app) app.classList.remove('is-grid');
          if(gridEl) gridEl.hidden=true;
          if(gridView) stopped=gridView.setActive(false);
        }
        document.querySelectorAll('#view-switch [data-view]').forEach(function(btn){
          var on=btn.getAttribute('data-view')===viewMode;
          btn.classList.toggle('active',on);
          btn.setAttribute('aria-pressed',on?'true':'false');
        });
        persistViewMode(viewMode);
        renderControl();
        if(window.lucide) lucide.createIcons();
        return stopped;
      }
      /* 深链：/mirror?device=<public id>（管理后台设备列表「打开」用的入口）。
         参数只消费一次并立刻从地址栏清掉，避免刷新/后退重复应用或重复起流；
         它优先于「上次视图」——否则管理员上次用过宫格就会被恢复成宫格（NAV 明确要求不进宫格）。 */
      function readRequestedDeviceRef(){
        try{
          var ref=new URLSearchParams(window.location.search).get('device');
          return ref?String(ref).trim():'';
        }catch(error){
          return '';
        }
      }
      function clearRequestedDeviceRef(){
        try{
          if(window.history&&window.history.replaceState){
            window.history.replaceState(null,'',window.location.pathname);
          }
        }catch(error){}
      }
      var requestedDeviceRef=readRequestedDeviceRef();
      var deviceDeepLink=!!requestedDeviceRef;
      function restoreViewMode(){
        // 刷新后保持管理员上次选择的视图（localStorage，按浏览器保存）。
        if(deviceDeepLink){
          if(viewMode==='grid') setViewMode('single');
          return;
        }
        if(savedViewMode()!=='grid') return;
        if(!mirrorIsAdmin()) return;
        setViewMode('grid');
      }
      function openDeviceFromGrid(deviceId,options){
        if(!deviceId) return;
        // 先切回单画面并拿到「停止宫格观看端」的 Promise（setViewMode 内部只停一次，
        // 避免第二次 stopTile 在 stop-self 请求返回前就断开 WebSocket 导致 404），
        // 等它们真正结束再开单画面：否则服务端会判定「仍有人在看」，
        // 单画面会继承宫格的 1fps 缩略档位而不是用户自己的画质。
        var stopped=setViewMode('single');
        selectDevice(deviceId);
        var begin=function(){
          startWatchFlow();
          if(options&&options.control){
            var tries=0;
            var timer=setInterval(function(){
              tries+=1;
              if(watchState==='playing'){
                clearInterval(timer);
                if(controlState!=='self'){ try{ acquireBtn.click(); }catch(e){} }
              }else if(tries>60){ clearInterval(timer); }
            },500);
          }
        };
        if(stopped&&typeof stopped.then==='function') stopped.catch(function(){}).then(begin);
        else begin();
      }
      /* 宫格底部「添加 ADB 设备」/ 空坑位：跳转设备管理并自动打开新增抽屉（?new=1）。 */
      function addAdbDevice(){
        if(!mirrorIsAdmin()){ showToast('需要管理员权限，请联系管理员添加设备'); return; }
        window.location.href='/devices?new=1';
      }
      function renderDeviceList(){
        var list=document.getElementById('device-list');
        if(!list) return;
        document.getElementById('device-count').textContent=deviceItems.length ? deviceItems.length + ' 台' : '—';
        if(!deviceItems.length){
          list.innerHTML=deviceLoadError
            ?'<div class="device-empty"><div class="device-empty-card error"><div class="dev-empty-icon"><i data-lucide="cloud-off"></i></div><b>设备数据加载失败</b><small>数据服务连接异常,请稍后重试</small><div class="dev-empty-actions"><button class="dev-empty-btn primary" type="button" data-dev-refresh>重试</button></div></div></div>'
            :'<div class="device-empty"><div class="device-empty-card"><div class="dev-empty-icon"><i data-lucide="smartphone"></i></div><b>暂无可用设备</b><small>添加设备并授权后即可在此观看</small><div class="dev-empty-actions"><a class="dev-empty-btn primary" data-admin-only href="/devices">前往设备管理</a><button class="dev-empty-btn" type="button" data-dev-refresh>刷新</button></div></div></div>';
          syncAdminOnly();
          list.querySelectorAll('[data-dev-refresh]').forEach(function(btn){ btn.addEventListener('click',function(){ var el=this; el.disabled=true; el.classList.add('loading'); var orig=el.innerHTML; el.innerHTML='<i data-lucide="loader-circle"></i><span>刷新中…</span>'; lucide.createIcons(); loadMirrorDevices().then(done,done); function done(){ el.disabled=false; el.classList.remove('loading'); el.innerHTML=orig; if(window.lucide) lucide.createIcons(); } }); });
          lucide.createIcons(); return;
        }
        if(mirrorDeviceTools){
          mirrorDeviceTools.render(deviceItems,selectedDevice&&selectedDevice.id);
          syncAdminOnly();
          return;
        }
        list.innerHTML=deviceItems.map(function(d){ var state=d.online===true?'online':(d.online===false?'offline':'unknown'); var meta=d.permission ? (d.controller ? '有控制权限' : '可获取控制') : '仅观看'; return '<div class="device-card' + (selectedDevice && selectedDevice.id===d.id ? ' selected' : '') + '" role="button" tabindex="0" data-device-id="'+escHtml(d.id)+'"><span class="dev-dot '+state+'" aria-hidden="true"></span><span class="dev-name">'+escHtml(d.name)+'</span><span class="dev-status '+state+'">'+mirrorStatusText(d)+'</span><span class="dev-meta">'+escHtml(meta)+'</span></div>'; }).join('');
        list.querySelectorAll('[data-device-id]').forEach(function(card){ card.addEventListener('click',function(){ selectDevice(card.getAttribute('data-device-id')); }); card.addEventListener('keydown',function(e){ if(e.key==='Enter' || e.key===' '){ e.preventDefault(); selectDevice(card.getAttribute('data-device-id')); } }); }); lucide.createIcons();
        syncViewSwitch();
        if(viewMode==='grid'&&gridView) gridView.render(deviceItems);
      }
      /* ---------- 顶部 ALAS 状态项（有绑定才显示） ---------- */
      var alasPillSeq=0;
      var alasPillConfig='';
      var alasPillState='';
      /* ALAS「异常」家族：运行出错、已断开、不可达、连接超时。顶部状态点、面板状态胶囊
         与底部工具栏按钮共用这一份判定，避免再次出现「上面点红、下面按钮灰」的不一致。 */
      var ALAS_FAULT_STATES=['error','disconnected','unreachable','timeout'];
      function alasStateIsFault(state){ return ALAS_FAULT_STATES.indexOf(String(state==null?'':state))>=0; }
      function alasPillLabel(state){
        var labels={ running:'运行中', error:'异常', stopped:'已停止', idle:'已停止', loading:'检查中', disabled:'已禁用', disconnected:'已断开', unreachable:'不可达', timeout:'连接超时', unconfigured:'未配置', unbound:'未绑定' };
        return labels[state]||'检查中';
      }
      function renderAlasPill(configName,state){
        var block=document.getElementById('mirror-alas-status-block');
        var sep=document.getElementById('mirror-alas-sep');
        if(!block||!sep) return;
        var show=workbenchAlasVisible!==false && !!configName;
        block.hidden=!show;
        sep.hidden=!show;
        if(!show) return;
        var raw=String(state||'');
        var text=document.getElementById('mirror-alas-status');
        if(text) text.textContent=alasPillLabel(raw);
        var dot=document.getElementById('mirror-alas-dot');
        if(dot){
          dot.classList.remove('off','err');
          if(alasStateIsFault(raw)) dot.classList.add('err');
          else if(raw!=='running') dot.classList.add('off');
        }
        var blockEl=document.getElementById('mirror-alas-status-block');
        if(blockEl) blockEl.title=configName?('ALAS · '+configName):'';
        normalizeStatusPill();
      }
      function loadAlasPill(device){
        var seq=++alasPillSeq;
        alasPillConfig='';
        alasPillState='';
        renderAlasPill('','');
        if(!device||!device.id||workbenchAlasVisible===false) return;
        var api=window.ScrcpyGateApi;
        if(!api||!api.isConfigured||!api.isConfigured('alas.configs')) return;
        api.configured('alas.configs',{ query:{ device_id:device.id } }).then(function(payload){
          if(seq!==alasPillSeq) return;
          var raw=((payload&&payload.configs)||[]);
          // 管理员没有单独绑定该设备时，后端会回退到 Runtime 配置目录并标 admin_fallback
          // （只有管理员会拿到这种条目）。同一份数据在下面「ALAS 面板」里本来就直接取用，
          // 顶部状态条以前把它过滤掉了，于是「面板能看、状态条不见」。
          // 现在管理员与面板一致地使用回退配置；普通用户仍然只有真正绑定才显示。
          var configs=raw.filter(function(c){ return !c||c.admin_fallback!==true||mirrorIsAdmin(); });
          if(!configs.length) return;
          var defaultName=String(payload&&payload.default_config||'');
          var chosen=null;
          for(var i=0;i<configs.length;i++){ if(configs[i].config_name===defaultName){ chosen=configs[i]; break; } }
          if(!chosen) chosen=configs[0];
          var configName=String(chosen.config_name||'');
          if(!configName) return;
          alasPillConfig=configName;
          alasPillState='loading';
          renderAlasPill(configName,'loading');
          // 回退配置不是绑定：状态条上标明来源，避免误以为这台设备已绑定 ALAS。
          if(chosen.admin_fallback===true){
            var pillBlock=document.getElementById('mirror-alas-status-block');
            if(pillBlock) pillBlock.title='ALAS · '+configName+'（运行时默认配置，未单独绑定该设备）';
          }
          if(!api.isConfigured('alas.status')) return;
          return api.configured('alas.status',{ query:{ config:configName, device_id:device.id } }).then(function(statusPayload){
            if(seq!==alasPillSeq) return;
            var d=statusPayload&&(statusPayload.data&&typeof statusPayload.data==='object'?statusPayload.data:statusPayload)||{};
            alasPillState=String(d.state||d.status||'');
            renderAlasPill(configName,alasPillState);
          });
        }).catch(function(){ if(seq===alasPillSeq){ alasPillConfig=''; renderAlasPill('',''); } });
      }
      function refreshAlasPill(){
        if(!selectedDevice||!alasPillConfig) return;
        var api=window.ScrcpyGateApi;
        if(!api||!api.isConfigured||!api.isConfigured('alas.status')) return;
        var seq=alasPillSeq;
        var configName=alasPillConfig;
        var deviceId=selectedDevice.id;
        api.configured('alas.status',{ query:{ config:configName, device_id:deviceId } }).then(function(statusPayload){
          if(seq!==alasPillSeq||configName!==alasPillConfig) return;
          var d=statusPayload&&(statusPayload.data&&typeof statusPayload.data==='object'?statusPayload.data:statusPayload)||{};
          alasPillState=String(d.state||d.status||'');
          renderAlasPill(configName,alasPillState);
        }).catch(function(){});
      }
      function selectDevice(id,options){
        options=options||{};
        var previousId=selectedDevice&&selectedDevice.id;
        var sameDevice=previousId!=null&&String(previousId)===String(id);
        if(!sameDevice) clearControlNotice();
        var preserveSession=options.preserveSession!==false&&sameDevice&&!!currentSession&&
          (watchState==='connecting'||watchState==='playing'||watchState==='websocket'||watchState==='hello'||watchState==='keyframe'||watchState==='resuming');
        selectedDevice=deviceById(id); renderDeviceList();
        var d=selectedDevice; if(!d) return;
        document.getElementById('mirror-device-name').textContent=d.name; document.getElementById('mirror-device-status').textContent=mirrorStatusText(d);
        var ddot=document.getElementById('mirror-device-dot');
        if(ddot){ ddot.classList.remove('off','err','unknown'); if(d.online===false) ddot.classList.add('off'); else if(d.online==null) ddot.classList.add('unknown'); }
        document.getElementById('start-watch').disabled=d.online!==true || !d.permission;
        document.getElementById('mirror-viewers').textContent=d.viewers == null ? '—' : d.viewers;
        loadAlasPill(d);
        document.getElementById('mirror-quality-name').textContent=d.quality && d.quality.name || '未加载'; qualityMetaBase=d.quality && d.quality.meta || '—'; renderQualityMeta();
        controlState=deviceControlState(d);
        if(preserveSession){
          renderControl();
        }else{
          setWatchState(d.online === true && d.permission ? 'idle' : (d.permission ? (d.online === false ? 'offline' : 'idle') : 'noperm'));
        }
        if(typeof vpUpdatePill==='function' && vpLoaded) vpUpdatePill();
        if(typeof apOnDeviceChange==='function') apOnDeviceChange();
      }
      function loadMirrorDevices(options){
        options=options || {};
        return window.ScrcpyGateApi.configured('devices.list',{query:{permission:'watch'}}).then(function(payload){
          var previousId=selectedDevice && selectedDevice.id;
          var nextItems=listPayload(payload).map(normalizeMirrorDevice).filter(function(d){return d.id;});
          var nextSignature=deviceListSignature(nextItems,false);
          var shouldRender=nextSignature!==deviceRenderSignature;
          deviceListStale=!!(payload && payload.stale);
          deviceLoadError=deviceListStale && !nextItems.length;
          deviceItems=nextItems;
          deviceRenderSignature=nextSignature;
          if(deviceListStale && !options.silent) showToast('设备列表可能已过期,将在下一次刷新时重试');
          if(deviceItems.length){
            /* 深链目标优先；解析不到（已删除/无权限/标识过期）时给出可理解提示并回落到默认设备。 */
            var requestedId=requestedDeviceRef && deviceById(requestedDeviceRef) ? requestedDeviceRef : '';
            if(requestedDeviceRef && !requestedId){
              showToast('未找到该设备，可能已被删除或你没有访问权限');
              requestedDeviceRef='';
              clearRequestedDeviceRef();
            }
            var nextId=requestedId ? requestedId : (deviceById(previousId) ? previousId : deviceItems[0].id);
            if(shouldRender) selectDevice(nextId);
            else selectedDevice=deviceById(nextId);
            if(requestedId){
              if(viewMode==='grid') setViewMode('single');
              requestedDeviceRef='';
              clearRequestedDeviceRef();
            }
          }else{
            selectedDevice=null;
            if(shouldRender) renderDeviceList();
            if(requestedDeviceRef){
              showToast('未找到该设备，可能已被删除或你没有访问权限');
              requestedDeviceRef='';
              clearRequestedDeviceRef();
            }
          }
          // 列表签名未变化时 selectDevice/renderDeviceList 不会执行，这里补一次视图同步。
          syncViewSwitch();
          if(viewMode==='grid'&&gridView) gridView.render(deviceItems);
        }).catch(function(error){
          deviceLoadError=true;
          if(deviceItems.length){
            if(!options.silent) showToast(apiErrorText(error));
            return;
          }
          var nextSignature=deviceListSignature([],true);
          var shouldRender=nextSignature!==deviceRenderSignature;
          deviceItems=[];
          selectedDevice=null;
          deviceRenderSignature=nextSignature;
          if(shouldRender) renderDeviceList();
          if(!options.silent || shouldRender) showToast(apiErrorText(error));
        });
      }
      function sessionAction(action, successText, extra){
        if(!currentSession){ showToast('请先开始观看会话'); return Promise.reject(new Error('no active session')); }
        var body=Object.assign({action:action, deviceId:selectedDevice && selectedDevice.id}, extra || {});
        return window.ScrcpyGateApi.configured('sessions.action',{params:{id:currentSession.id},method:'POST',body:body}).then(function(payload){
          currentSession=payload && (payload.session || payload.data && payload.data.session || currentSession) || currentSession;
          if(successText) showToast(successText);
          return payload;
        }).catch(function(error){ showToast(apiErrorText(error)); throw error; });
      }

      function fmtTime(s){
        var m=Math.floor(s/60), ss=s%60;
        return (m<10?'0':'')+m+':'+(ss<10?'0':'')+ss;
      }
      /* 投屏进行中（含连接/等关键帧）：旋转按钮的可用条件，也是「画框」在拿到真实
         尺寸前是否已经进入投屏布局的判断依据（避免开始投屏时才从 16:9 大框缩回画面大小）。 */
      var LIVE_WATCH_STATES=['connecting','waiting','websocket','hello','keyframe','resuming','playing'];
      function streamLive(){ return LIVE_WATCH_STATES.indexOf(watchState)>=0; }
      function renderControl(){
        var watching=watchState==='playing';
        var pending=watchState==='connecting'||watchState==='waiting'||watchState==='websocket'||watchState==='hello'||watchState==='keyframe'||watchState==='resuming';        var self=controlState==='self';
        var menuAllowed=mirrorControlTools ? mirrorControlTools.canUseMenu() : (watching&&!controlBusy&&!navBusy&&!moreActionBusy);        var controlMenuAllowed=mirrorControlTools ? mirrorControlTools.canUseControlMenu() : (menuAllowed&&self);
        // 方向状态对外可见（CSS/测试/排障都读它）：画面方向完全自动，这里只报当前角度。
        if(app) app.setAttribute('data-view-rotation',String(displayRotation()));
        var canStart=!!(selectedDevice&&selectedDevice.online===true&&selectedDevice.permission);
        var watchLabel=pending?'取消连接':(watching?'停止观看':'开始投屏');
        var watchIcon=pending?'x':(watching?'circle-stop':'play');
        watchBtn.disabled=!(pending||watching||canStart);
        watchBtn.classList.toggle('held',watching);
        watchBtn.title=watchLabel;
        watchBtn.setAttribute('aria-label',watchLabel);
        watchBtn.innerHTML='<i data-lucide="'+watchIcon+'"></i><span id="cb-watch-label">'+watchLabel+'</span>';
        var canControl=canControlNow();
        acquireBtn.disabled=!watching || controlBusy || !canControl;
        acquireBtn.setAttribute('aria-busy',controlBusy?'true':'false');
        var label=!canControl?'仅观看':(self?'释放控制':(controlState==='other'?'接管控制':'获取控制'));
        acquireBtn.classList.toggle('held',self&&canControl);
        acquireBtn.title=!canControl?'没有该设备的控制权限，请联系管理员授权':label;
        acquireBtn.setAttribute('aria-label',acquireBtn.title);
        acquireBtn.innerHTML='<i data-lucide="'+(!canControl?'eye':(self?'shield-check':'mouse-pointer-click'))+'"></i><span id="cb-acquire-label">'+label+'</span>';
        if(keyboardBtn){
          var keyboardLabel=keyboardOn?'关闭键盘':'键盘';
          keyboardBtn.disabled=!controlMenuAllowed;
          keyboardBtn.classList.toggle('held',keyboardOn);
          keyboardBtn.title=keyboardOn?'关闭键盘':'开启键盘输入';
          keyboardBtn.setAttribute('aria-label',keyboardOn?'关闭键盘':'开启键盘输入');
          setDockTogglePressed(keyboardBtn,keyboardOn);
          keyboardBtn.setAttribute('aria-busy',moreActionBusy?'true':'false');
          keyboardBtn.innerHTML='<i data-lucide="keyboard"></i><span id="cb-keyboard-label">'+keyboardLabel+'</span>';
        }
        occupiedChip.classList.toggle('show',controlState==='other'&&watching);
        if(!menuAllowed) closeMoreMenu(true);
        navBtns.forEach(function(b){ b.disabled=!controlMenuAllowed || navBusy; b.setAttribute('aria-busy',navBusy?'true':'false'); });
        moreBtn.disabled=!menuAllowed || moreActionBusy;
        moreBtn.setAttribute('aria-busy',moreActionBusy?'true':'false');
        // 全屏是纯显示层操作，不依赖投屏会话，始终可用。
        if(fullscreenBtn) fullscreenBtn.disabled=false;
        // 旋转也是纯显示层操作（不需要控制权），但要有画面可转：投屏中/连接中可用。
        if(rotateBtn){
          rotateBtn.disabled=!streamLive();
          rotateBtn.title=streamLive()?'顺时针旋转 90°':'开始投屏后可用';
          rotateBtn.setAttribute('aria-label',rotateBtn.title);
        }
        if(rotateState) rotateState.textContent=viewRotationDegrees()+'°';
        if(popShot) popShot.disabled=!menuAllowed;
        // 「全屏后默认获取控制」是本浏览器偏好，只要有菜单权限就能改。
        if(popAutoControl) popAutoControl.disabled=!menuAllowed;
        if(popKeyboard) popKeyboard.disabled=!controlMenuAllowed || moreActionBusy;
        var ctext=self?'我 · 控制中':(controlState==='other'?(controlOwner?(controlOwner+' 控制中'):'其他用户占用中'):(controlState==='free'?'空闲（可获取）':'未加载'));
        // 控制权胶囊（用户要求恢复，老项目里就有）：必须显示**谁**在控制，
        // 只写"其他用户占用中"没法判断是不是自己被别的端顶掉了。
        if(ctrlState){
          ctrlState.className='ctrl-state '+(self?'self':(controlState==='other'?'other':'free'));
          ctrlState.innerHTML='<i data-lucide="'+(self?'shield-check':(controlState==='other'?'lock':'shield'))+'"></i>'+escHtml(ctext);
        }
        if(window.lucide) lucide.createIcons();
        // 按钮文案/可见性改变后，重新分配单排工具栏和更多菜单。
        syncDockDensity();
        // 全屏里「获取控制」可能就藏在收起的把手后面，把手要跟着状态点亮。
        updateFullscreenDockToggle();
      }
      function stopCountdown(){
        clearInterval(cdTimer);
        cdTimer=null;
        cdBlock.hidden=true;
        cdSep.hidden=true;
        cdBanner.classList.remove('show');
        normalizeStatusPill();
      }
      function startCountdown(){
        if (!remaining) { cdBlock.hidden=true; cdSep.hidden=true; normalizeStatusPill(); return; }
        cdBlock.hidden=false;
        cdSep.hidden=false;
        normalizeStatusPill();
        clearInterval(cdTimer);
        cdTimer=setInterval(function(){
          remaining--;
          if(remaining<=0){ remaining=0; }
          cdTime.textContent=fmtTime(remaining);
          updateBanner();
          if(remaining<=0){
            clearInterval(cdTimer);
            cdTimer=null;
            endByTimeout();
          }
        },1000);
        cdTime.textContent=fmtTime(remaining);
        updateBanner();
      }
      function updateBanner(){
        if(viewerStopWarning){
          cdBannerText.textContent='将在 60 秒后停止观看';
          cdBanner.classList.add('show');
          return;
        }
        if(remaining<=0){
          // 没有「离开自动停止」警告、也没有会话时限倒计时：直接收起横幅。
          // 这里以前只改文案不收起，于是切回页面（适配层取消警告）后横幅还挂着，
          // 用户以为必须点「延长 10 分钟」才能继续看。
          cdBanner.classList.remove('show');
          return;
        }
        if(remaining>300){ cdBanner.classList.remove('show'); return; }
        cdBannerText.textContent=remaining>60?('将在 '+Math.ceil(remaining/60)+' 分钟后停止观看'):('将在 '+remaining+' 秒后停止观看');
        cdBanner.classList.add('show');
      }
      function endByTimeout(){
        showToast('观看时长已用完，当前观看会话已结束');
        if(fullscreenActive) exitFullscreen();
        if(currentSession){
          configuredCall('sessions.stop',{params:{id:currentSession.id,deviceId:selectedDevice&&selectedDevice.id},method:'POST'}).catch(function(){});
          currentSession=null;
        }
        setWatchState('expired');
      }
      function setWatchState(s){
        // 原始层：状态切换逐次留痕（「到底是哪一步卡住」靠它对齐）。
        if(watchState!==s) rawState('watch_state',{from:String(watchState||''),to:String(s||'')});
        watchState=s;
        if(s!=='playing' && viewerStopWarning){
          viewerStopWarning=false;
          document.title=baseDocumentTitle;
        }
        var overlayState=(s==='websocket'||s==='hello')?'waiting':s;
        overlays.forEach(function(o){ o.classList.toggle('show',o.getAttribute('data-cstate')===overlayState); });
        var watching=s==='playing';
        videoSurface.classList.toggle('show',watching);
        // 实测码率：投屏中（含连接/重连）一直占着这一格，停止投屏才收起并清零。
        var live=LIVE_WATCH_STATES.indexOf(s)>=0;
        if(!live) clearControlNotice();
        if(live!==rateShown){
          rateShown=live;
          if(!live){ measuredMbps=0; measuredFps=0; }
          renderQualityMeta();
        }
        if(live!==fpsShown){
          fpsShown=live;
          renderQualityFps();
        }
        if(watching){
          startCountdown();
        }else{
          stopCountdown();
        }
        renderControl();
      }
      function startWatchFlow(){
        if(!selectedDevice) { showToast('请先选择设备'); return; }
        // 新会话从正向开始，随后由自动摆正按设备方向调整（手动旋转基准一并解冻）。
        videoBox.classList.remove('rotated','rotated-180');
        manualRotationOffset=0;
        manualBaseRotation=null;
        if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.setVideoRotation==='function') window.ScrcpyGateV2.setVideoRotation(0);
        if(displayController) displayController.setRotation(0);
        // 先进入「连接中」再算布局：这样画框会按投屏布局（含 16:9 兜底）一次到位，
        // 而不是先撑成整宽的 16:9 大框、等第一帧到了再缩回画面大小。
        setWatchState('connecting');
        syncVideoOrientation();
        var requestGeneration=++watchRequestGeneration;
        var requestDeviceId=selectedDevice.id;
        window.ScrcpyGateApi.configured('sessions.create',{method:'POST',body:{deviceId:requestDeviceId}}).then(function(payload){
          if(requestGeneration!==watchRequestGeneration || !selectedDevice || String(selectedDevice.id)!==String(requestDeviceId)){
            return window.ScrcpyGateApi.configured('sessions.stop',{params:{deviceId:requestDeviceId},method:'POST'}).catch(function(){});
          }
          currentSession=payload && (payload.session || payload.data || payload);
          // controller 是「持有者用户名 / self / other / free」的混合口径：这里统一走
          // snapshotControlState + 持有者名，别把用户名直接塞进 controlState（否则
          // 状态栏会显示成用户名、按钮判定也会错）。
          if(currentSession&&currentSession.controller){
            var ctrlRef=String(currentSession.controller);
            if(ctrlRef==='self'){ controlState='self'; controlOwner=currentMirrorUsername()||'我'; }
            else if(ctrlRef==='other'){ controlState='other'; }
            else if(ctrlRef==='free'){ controlState='free'; controlOwner=''; }
            else { noteControlOwner(ctrlRef); controlState=snapshotControlState(ctrlRef); }
            renderControl();
          }
          if(currentSession && currentSession.remainingSeconds) { remaining=currentSession.remainingSeconds; startCountdown(); }
        }).catch(function(error){ if(requestGeneration===watchRequestGeneration){ setWatchState('fail'); showToast(apiErrorText(error)); } });
      }
      function cancelCurrentWatch(){
        watchRequestGeneration++;
        if(fullscreenActive) exitFullscreen();
        var deviceId=selectedDevice&&selectedDevice.id;
        var hadSession=!!currentSession;
        currentSession=null;
        controlState='free';
        setWatchState('idle');
        if(!deviceId||!hadSession) return;
        window.ScrcpyGateApi.configured('sessions.stop',{params:{deviceId:deviceId},method:'POST'}).catch(function(error){
          showToast(apiErrorText(error));
        });
      }

      document.getElementById('start-watch').addEventListener('click',startWatchFlow);
      watchBtn.addEventListener('click',function(){
        if(mirrorControlTools) mirrorControlTools.action('watch_toggle',{state:watchState});
        if(watchState==='playing'||watchState==='connecting'||watchState==='waiting'||watchState==='websocket'||watchState==='hello'||watchState==='keyframe'||watchState==='resuming'){
          cancelCurrentWatch();
          return;
        }
        startWatchFlow();
      });
      document.getElementById('cancel-watch').addEventListener('click',cancelCurrentWatch);
      /* 视图切换（管理员）与宫格刷新 */
      document.querySelectorAll('#view-switch [data-view]').forEach(function(btn){
        btn.addEventListener('click',function(){ setViewMode(btn.getAttribute('data-view')); });
      });
      var mgRefreshBtn=document.getElementById('mg-refresh');
      if(mgRefreshBtn){
        mgRefreshBtn.addEventListener('click',function(){
          var label=mgRefreshBtn.querySelector('span');
          mgRefreshBtn.disabled=true;
          if(label) label.textContent='刷新中…';
          loadMirrorDevices().catch(function(){}).then(function(){
            mgRefreshBtn.disabled=false;
            if(label) label.textContent='刷新设备';
            if(window.lucide) lucide.createIcons();
          });
        });
      }
      var mgStartAllBtn=document.getElementById('mg-start-all');
      if(mgStartAllBtn){
        mgStartAllBtn.addEventListener('click',function(){
          var grid=ensureGridView();
          if(grid) grid.startAll();
        });
      }
      var mgStopAllBtn=document.getElementById('mg-stop-all');
      if(mgStopAllBtn){
        mgStopAllBtn.addEventListener('click',function(){
          var grid=ensureGridView();
          if(grid) grid.stopAll();
        });
      }
      var mgRefreshStreamsBtn=document.getElementById('mg-refresh-streams');
      if(mgRefreshStreamsBtn){
        mgRefreshStreamsBtn.addEventListener('click',function(){
          var grid=ensureGridView();
          if(grid) grid.refreshAll();
        });
      }
      /* 宫格画面缩放：滑块按百分比调格子宽度（100% = 300px），列数仍由宽度自适应。
         范围 40%–150%（默认 80%）：最小 120px，保留两个完整的触控按钮。 */
      var GRID_ZOOM_KEY='scrcpygate-grid-zoom';
      var GRID_ZOOM_BASE=300;
      var GRID_ZOOM_MIN=40;
      var GRID_ZOOM_MAX=150;
      var GRID_ZOOM_STEP=5;
      var GRID_ZOOM_DEFAULT=80;
      var gridZoomRange=document.getElementById('mg-zoom-range');
      var gridZoomValue=document.getElementById('mg-zoom-value');
      function clampGridZoom(value){
        var pct=Number(value);
        if(!isFinite(pct)) pct=GRID_ZOOM_DEFAULT;
        pct=Math.round(pct/GRID_ZOOM_STEP)*GRID_ZOOM_STEP;
        return Math.min(GRID_ZOOM_MAX,Math.max(GRID_ZOOM_MIN,pct));
      }
      function gridZoomPx(pct){ return Math.round(GRID_ZOOM_BASE*clampGridZoom(pct)/100); }
      function savedGridZoom(){
        try{
          var saved=localStorage.getItem(GRID_ZOOM_KEY);
          if(saved!==null&&saved!==''){
            var savedNumber=Number(saved);
            // 旧版本存的是像素值（>200），按 300px=100% 换算后沿用。
            if(isFinite(savedNumber)) return clampGridZoom(savedNumber>GRID_ZOOM_MAX?savedNumber/GRID_ZOOM_BASE*100:savedNumber);
          }
        }catch(e){}
        return GRID_ZOOM_DEFAULT;
      }
      function applyGridZoom(pct){
        var value=clampGridZoom(pct);
        var host=document.getElementById('mg-grid');
        if(host) host.style.setProperty('--mg-tile-min',gridZoomPx(value)+'px');
        if(gridView) gridView.syncTileWidths();
        if(gridZoomRange&&Number(gridZoomRange.value)!==value) gridZoomRange.value=String(value);
        refreshGridZoomLabel();
        return value;
      }
      // 卡片仅在超过容器宽度时受限；较高的卡片通过宫格滚动查看。
      function refreshGridZoomLabel(){
        if(!gridZoomValue) return;
        var value=clampGridZoom(gridZoomRange?gridZoomRange.value:savedGridZoom());
        var info=gridView&&typeof gridView.tileWidth==='function'?gridView.tileWidth():null;
        var requested=gridZoomPx(value);
        var effective=info&&info.effective?Number(info.effective):requested;
        gridZoomValue.textContent=Math.abs(effective-requested)>1
          ?(value+'%'+tr('（实际 {0}px）').replace('{0}',effective))
          :(value+'%');
        if(gridZoomValue.title!==undefined){
          gridZoomValue.title=tr(effective<requested-1
            ?'当前卡片宽 {0}px（受可用宽度限制，滑块请求 {1}px）'
            :'当前卡片宽 {0}px').replace('{0}',effective).replace('{1}',requested);
        }
      }
      if(gridZoomRange){
        gridZoomRange.addEventListener('input',function(){ applyGridZoom(gridZoomRange.value); });
        gridZoomRange.addEventListener('change',function(){
          var size=applyGridZoom(gridZoomRange.value);
          try{ localStorage.setItem(GRID_ZOOM_KEY,String(size)); }catch(e){}
        });
      }
      applyGridZoom(savedGridZoom());
      /* 宫格截图设置：帧率（记在本浏览器）。同时显示数量固定 12 路、坑位固定 12 个，
         不再让管理员选择 —— 上限与服务端每用户视频连接上限一致（MAX_VIDEO_CONNECTIONS_PER_USER）。 */
      var GRID_FPS_KEY='scrcpygate-grid-fps';
      var GRID_FPS_VALUES=[1,2,5,10];
      var GRID_MAX_LIVE=12;
      var GRID_SLOT_COUNT=12;
      var pendingGridFps=1;
      var openGridMenu=null;
      function pickGridValue(options,value,fallback){
        var next=Number(value);
        for(var i=0;i<options.length;i++){ if(options[i]===next) return next; }
        return fallback;
      }
      function savedGridNumber(key,options,fallback){
        try{ return pickGridValue(options,localStorage.getItem(key),fallback); }catch(e){ return fallback; }
      }
      function updateGridHint(maxLive){
        var hint=document.getElementById('mg-hint');
        if(!hint) return;
        hint.textContent=String(tr('点击「开始截图」后按所选帧率抓取画面，最多同时预览 {0} 路')).replace('{0}',String(maxLive));
      }
      /* 自定义下拉菜单：原生 select 的弹出层由系统绘制（深色下是刺眼的白底），
         这里用按钮 + 菜单复刻 Apple 风格的浮层（圆角、发丝边、柔和阴影、勾选态）。 */
      function mgCreateMenu(config){
        var wrap=document.getElementById(config.wrap);
        var trigger=document.getElementById(config.trigger);
        var menu=document.getElementById(config.menu);
        var valueEl=document.getElementById(config.value);
        if(!wrap||!trigger||!menu) return null;
        var items=Array.prototype.slice.call(menu.querySelectorAll('[data-value]'));
        var api={
          value:null,
          set:function(value,notify){
            var next=String(value);
            api.value=next;
            var label=next;
            items.forEach(function(item){
              var on=item.getAttribute('data-value')===next;
              item.setAttribute('aria-checked',on?'true':'false');
              if(on) label=(item.querySelector('span')||item).textContent.trim();
            });
            if(valueEl) valueEl.textContent=label;
            if(notify&&typeof config.onChange==='function') config.onChange(Number(next),next);
            return Number(next);
          },
          close:function(){
            if(!menu.classList.contains('open')) return;
            menu.classList.remove('open');
            trigger.setAttribute('aria-expanded','false');
            if(openGridMenu===api) openGridMenu=null;
          },
          open:function(){
            if(openGridMenu&&openGridMenu!==api) openGridMenu.close();
            menu.classList.add('open');
            trigger.setAttribute('aria-expanded','true');
            openGridMenu=api;
            var checked=items.filter(function(item){ return item.getAttribute('aria-checked')==='true'; })[0];
            (checked||items[0]).focus();
          },
          toggle:function(){ if(menu.classList.contains('open')) api.close(); else api.open(); }
        };
        trigger.addEventListener('click',function(){ api.toggle(); });
        trigger.addEventListener('keydown',function(e){
          if(e.key==='ArrowDown'||e.key==='ArrowUp'){ e.preventDefault(); api.open(); }
        });
        menu.addEventListener('keydown',function(e){
          if(e.key==='Escape'){ e.preventDefault(); api.close(); trigger.focus(); return; }
          if(['ArrowDown','ArrowUp','Home','End'].indexOf(e.key)<0) return;
          e.preventDefault();
          var current=items.indexOf(document.activeElement);
          var next=current;
          if(e.key==='ArrowDown') next=current<0?0:(current+1)%items.length;
          else if(e.key==='ArrowUp') next=current<=0?items.length-1:current-1;
          else if(e.key==='Home') next=0;
          else if(e.key==='End') next=items.length-1;
          items[next].focus();
        });
        items.forEach(function(item){
          item.addEventListener('click',function(){
            api.set(item.getAttribute('data-value'),true);
            api.close();
            trigger.focus();
          });
        });
        api.set(config.initial,false);
        return api;
      }
      document.addEventListener('pointerdown',function(e){
        if(!openGridMenu) return;
        if(e.target&&e.target.closest&&e.target.closest('.mg-menu-wrap')) return;
        openGridMenu.close();
      });
      window.addEventListener('resize',function(){ if(openGridMenu) openGridMenu.close(); });
      var gridFpsMenu=mgCreateMenu({
        wrap:'mg-fps', trigger:'mg-fps-trigger', menu:'mg-fps-menu', value:'mg-fps-value',
        initial:savedGridNumber(GRID_FPS_KEY,GRID_FPS_VALUES,GRID_FPS_VALUES[0]),
        onChange:function(next){
          applyGridFps(next);
          try{ localStorage.setItem(GRID_FPS_KEY,String(next)); }catch(e){}
          if(gridView&&gridView.liveCount()>0){ gridView.refreshAll(); showToast('截图帧率已改为 '+next+' fps，正在重新开始截图'); }
        }
      });
      function applyGridFps(value){
        var next=pickGridValue(GRID_FPS_VALUES,value,GRID_FPS_VALUES[0]);
        pendingGridFps=next;
        if(gridView) gridView.setThumbnailFps(next);
        return next;
      }
      /* 宫格画面方向：统一竖屏（默认，所有格子 9:16）/ 跟随设备（横屏设备=横格）。 */
      var GRID_ORIENTATION_KEY='scrcpygate-grid-orientation';
      var GRID_ORIENTATION_VALUES=['portrait','device'];
      var pendingGridOrientation='portrait';
      function pickGridOrientation(value){
        var next=String(value==null?'':value);
        for(var i=0;i<GRID_ORIENTATION_VALUES.length;i++){ if(GRID_ORIENTATION_VALUES[i]===next) return next; }
        return GRID_ORIENTATION_VALUES[0];
      }
      function savedGridOrientation(){
        try{ return pickGridOrientation(localStorage.getItem(GRID_ORIENTATION_KEY)); }catch(e){ return GRID_ORIENTATION_VALUES[0]; }
      }
      function applyGridOrientation(value){
        var next=pickGridOrientation(value);
        pendingGridOrientation=next;
        if(gridView) gridView.setOrientation(next);
        return next;
      }
      var gridOrientationMenu=mgCreateMenu({
        wrap:'mg-orientation', trigger:'mg-orientation-trigger', menu:'mg-orientation-menu', value:'mg-orientation-value',
        initial:savedGridOrientation(),
        onChange:function(_numeric,raw){
          var next=applyGridOrientation(raw);
          try{ localStorage.setItem(GRID_ORIENTATION_KEY,next); }catch(e){}
        }
      });
      applyGridOrientation(gridOrientationMenu?gridOrientationMenu.value:GRID_ORIENTATION_VALUES[0]);
      applyGridFps(gridFpsMenu?gridFpsMenu.value:GRID_FPS_VALUES[0]);
      updateGridHint(GRID_MAX_LIVE);
      var addDeviceBtn=document.getElementById('mg-add-device');
      if(addDeviceBtn){
        addDeviceBtn.addEventListener('click',function(){ addAdbDevice(); });
      }
      if(window.lucide) lucide.createIcons();
      document.getElementById('cancel-watch2').addEventListener('click',cancelCurrentWatch);
      document.getElementById('resume-retry').addEventListener('click',startWatchFlow);
      document.getElementById('offline-retry').addEventListener('click',startWatchFlow);
      document.getElementById('offline-pick').addEventListener('click',function(){ showToast('请从左侧设备列表选择其他设备'); });
      document.getElementById('noperm-apply').addEventListener('click',function(){
        if(!selectedDevice){ showToast('请先选择设备'); return; }
        configuredCall(['permissions.request','devices.permission.request'],{method:'POST',body:{deviceId:selectedDevice.id,permission:'watch'}}).then(function(){ showToast('权限申请已提交'); }).catch(function(error){ showToast(apiErrorText(error)); });
      });
      document.getElementById('fail-retry').addEventListener('click',startWatchFlow);
      document.getElementById('fail-log').addEventListener('click',function(){
        var params=[];
        if(currentSession && currentSession.id) params.push('sessionId='+encodeURIComponent(currentSession.id));
        if(selectedDevice && selectedDevice.id) params.push('deviceId='+encodeURIComponent(selectedDevice.id));
        window.location.href='/logs'+(params.length?'?'+params.join('&'):'');
      });
      // 视频管道生命周期(adapter 派发):断线进入"正在恢复",重连成功回到观看,彻底失败进入失败态。
      ['scrcpygate:videodrop','scrcpygate:videoopen','scrcpygate:videohello','scrcpygate:videokeyframe','scrcpygate:videoplaying','scrcpygate:videofailed'].forEach(function(name){
        document.addEventListener(name,function(){
          if(name==='scrcpygate:videodrop'){ if(watchState==='playing'||watchState==='keyframe'||watchState==='hello') setWatchState('resuming'); }
          else if(name==='scrcpygate:videoopen'){ if(watchState==='connecting'||watchState==='resuming') setWatchState('websocket'); }
          else if(name==='scrcpygate:videohello'){ if(watchState==='websocket'||watchState==='connecting') setWatchState('hello'); syncVideoOrientation(); }
          else if(name==='scrcpygate:videokeyframe'){ if(watchState==='hello'||watchState==='websocket'||watchState==='resuming') setWatchState('keyframe'); }
          else if(name==='scrcpygate:videoplaying'){ if(watchState==='keyframe'||watchState==='hello'||watchState==='websocket'||watchState==='resuming') setWatchState('playing'); syncVideoOrientation(); renderControl(); autoAcquireControlOnFullscreen(); }
          else if(name==='scrcpygate:videofailed'){ if(watchState!=='idle'&&watchState!=='fail') setWatchState('fail'); }
        });
      });
      document.addEventListener('scrcpygate:quality',function(event){
        var detail=event&&event.detail||{};
        if(detail.meta) qualityMetaBase=String(detail.meta);
        var nameEl=document.getElementById('mirror-quality-name');
        if(nameEl&&detail.name) nameEl.textContent=String(detail.name);
        renderQualityMeta();
      });
      document.addEventListener('scrcpygate:videorate',function(event){
        var detail=event&&event.detail||{};
        measuredMbps=Math.max(0,Number(detail.mbps)||0);
        if(!rateShown&&streamLive()){ rateShown=true; }
        renderQualityMeta();
      });
      document.addEventListener('scrcpygate:videofps',function(event){
        var detail=event&&event.detail||{};
        measuredFps=Math.max(0,Number(detail.fps)||0);
        if(!fpsShown&&streamLive()){ fpsShown=true; }
        renderQualityFps();
      });
      document.addEventListener('scrcpygate:viewer-stop-warning',function(){
        viewerStopWarning=true;
        document.title='将在 60 秒后停止观看 · '+baseDocumentTitle;
        cdBannerText.textContent='将在 60 秒后停止观看';
        cdBanner.classList.add('show');
      });
      document.addEventListener('scrcpygate:viewer-stop-cancelled',function(){
        // 切回页面（可见 + 获得焦点）后适配层会取消「离开超时自动停止」并清零计时，
        // 这里同步收起横幅、恢复标题 —— 不需要用户再点任何按钮。
        viewerStopWarning=false;
        document.title=baseDocumentTitle;
        updateBanner();
      });
      document.addEventListener('scrcpygate:viewer-stopped',function(event){
        var detail=event&&event.detail||{};
        viewerStopWarning=false;
        document.title=baseDocumentTitle;
        remaining=0;
        currentSession=null;
        controlState='free';
        if(fullscreenActive) exitFullscreen();
        setWatchState(detail.reason==='auth_invalid'?'fail':'idle');
      });
      document.addEventListener('scrcpygate:account-expired',function(){
        // 到期账户仍处于登录态（可以浏览与续期），这里只说清投屏已停用。
        showToast(tr('账户已到期：投屏与 ALAS 已停用，请联系管理员续期'));
      });
      document.addEventListener('scrcpygate:auth-invalid',function(){
        viewerStopWarning=false;
        document.title=baseDocumentTitle;
        remaining=0;
        currentSession=null;
        controlState='free';
        if(fullscreenActive) exitFullscreen();
        setWatchState('fail');
      });
      document.addEventListener('scrcpygate:viewers',function(event){
        var detail=event&&event.detail||{};
        if(!selectedDevice || String(detail.deviceId||'')!==String(selectedDevice.id||'')) return;
        var count=Number(detail.count);
        document.getElementById('mirror-viewers').textContent=isFinite(count)?String(Math.max(0,count)):'—';
        var session=detail.session||{};
        var lock=session.control_lock||null;
        // 服务端快照是权威：快照里没有 lock（或 lock 是别人）就不能继续显示「我有控制权」。
        // 旧代码在「没有 lock」时保留 self，导致画面重连后按钮显示有控制权但指令全被丢弃。
        if(lock&&lock.username){
          noteControlOwner(lock.username);
          controlState=snapshotControlState(lock.username);
          rawState('control_lock',{owner:String(lock.username),acquired_at:lock.acquired_at||null,state:controlState});
        }else if(watchState==='playing'){
          controlOwner='';
          controlState='free';
        }
        renderControl();
      });
      document.addEventListener('scrcpygate:session-stopped',function(event){
        var detail=event&&event.detail||{};
        if(selectedDevice && detail.deviceId && String(detail.deviceId)!==String(selectedDevice.id)) return;
        if(fullscreenActive) exitFullscreen();
        currentSession=null;
        controlState='free';
        controlOwner='';
        setWatchState('idle');
        document.getElementById('mirror-viewers').textContent='0';
      });
      document.addEventListener('scrcpygate:logout-failed',function(){ showToast('退出失败，请重试'); });
      document.addEventListener('scrcpygate:controlerror',function(event){
        var detail=event&&event.detail||{};
        showToast(detail.message||'控制指令发送失败');
      });
      document.addEventListener('scrcpygate:controlstate',function(event){
        var active=!!(event&&event.detail&&event.detail.active);
        var owner=event&&event.detail&&event.detail.owner?String(event.detail.owner):'';
        if(active){ controlState='self'; controlOwner=currentMirrorUsername()||'我'; clearControlNotice(); }
        else if(controlState==='self') controlState=owner?'other':'free';
        if(owner) noteControlOwner(owner);
        else if(controlState==='free') controlOwner='';
        rawState('control_state',{active:active,owner:owner,state:controlState});
        renderControl();
      });
      function clearControlNotice(){
        var notice=document.getElementById('control-loss-notice');
        if(notice) notice.hidden=true;
      }
      document.addEventListener('scrcpygate:control-lost',function(event){
        var detail=event.detail||{};
        if(!selectedDevice||String(detail.deviceId)!==String(selectedDevice.id)||!streamLive()) return;
        var message=detail.owner===currentMirrorUsername()
          ?tr('控制权已被此账号的另一端接管，当前仍可观看。')
          :tr('控制权已被 {0} 接管，当前仍可观看。').replace('{0}',detail.owner||tr('其他用户'));
        var notice=document.getElementById('control-loss-notice');
        notice.hidden=false;
        document.getElementById('control-loss-text').textContent=message;
        setFullscreenDockHidden(false);
      });
      document.getElementById('control-loss-dismiss').addEventListener('click',clearControlNotice);
      document.addEventListener('scrcpygate:viewer-stopped',clearControlNotice);
      document.addEventListener('scrcpygate:session-stopped',clearControlNotice);
      document.getElementById('expired-renew').addEventListener('click',function(){
        configuredCall(['account.renewal.request','users.renewal.request'],{method:'POST',body:{deviceId:selectedDevice && selectedDevice.id,sessionId:currentSession && currentSession.id}}).then(function(){ showToast('续期申请已提交'); }).catch(function(error){ showToast(apiErrorText(error)); });
      });
      document.getElementById('extend-btn').addEventListener('click',function(){
        sessionAction('extend','观看时长已延长',{seconds:600,minutes:10}).then(function(payload){
          var d=payload && (payload.session || payload.data || payload);
          if(d && d.remainingSeconds){ remaining=d.remainingSeconds; startCountdown(); }
        }).catch(function(){});
      });

      function takeoverSetStatus(message,isError){
        if(!takeoverStatus) return;
        takeoverStatus.textContent=message ? tr(message) : '';
        takeoverStatus.hidden=!message;
        takeoverStatus.classList.toggle('error',!!isError);
      }
      function takeoverFocusables(){
        if(!takeoverDialog) return [];
        return Array.prototype.slice.call(takeoverDialog.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')).filter(function(el){ return !el.hidden && el.offsetParent!==null; });
      }
      function takeoverOpen(){
        if(!takeoverLayer||!takeoverDialog||takeoverBusy) return;
        takeoverReturnFocus=document.activeElement;
        closeFixedPops();
        vpClose();
        apClose();
        upClose();
        takeoverSetStatus('',false);
        if(takeoverConfirmBtn){ takeoverConfirmBtn.disabled=false; takeoverConfirmBtn.removeAttribute('aria-busy'); }
        takeoverLayer.classList.add('open');
        takeoverDialog.classList.add('open');
        takeoverLayer.setAttribute('aria-hidden','false');
        takeoverDialog.setAttribute('aria-hidden','false');
        takeoverDialog.removeAttribute('inert');
        document.body.classList.remove('sg-mobile-nav');
        var mobileScrim=document.getElementById('mobile-scrim');
        if(mobileScrim){ mobileScrim.classList.remove('open'); mobileScrim.setAttribute('aria-hidden','true'); }
        if(window.lucide) lucide.createIcons();
        if(takeoverCancelBtn) takeoverCancelBtn.focus();
      }
      function takeoverClose(){
        if(!takeoverLayer||!takeoverDialog||takeoverBusy) return;
        takeoverLayer.classList.remove('open');
        takeoverDialog.classList.remove('open');
        takeoverLayer.setAttribute('aria-hidden','true');
        takeoverDialog.setAttribute('aria-hidden','true');
        takeoverDialog.setAttribute('inert','');
        takeoverSetStatus('',false);
        if(takeoverConfirmBtn){ takeoverConfirmBtn.disabled=false; takeoverConfirmBtn.removeAttribute('aria-busy'); }
        var returnFocus=takeoverReturnFocus;
        takeoverReturnFocus=null;
        if(returnFocus && document.contains(returnFocus) && typeof returnFocus.focus==='function') returnFocus.focus();
      }
      function takeoverSubmit(){
        if(takeoverBusy) return;
        if(watchState!=='playing'||controlState!=='other'){ takeoverClose(); return; }
        takeoverBusy=true;
        if(takeoverConfirmBtn){ takeoverConfirmBtn.disabled=true; takeoverConfirmBtn.setAttribute('aria-busy','true'); }
        takeoverSetStatus('正在接管控制…',false);
        controlRequest('sessions.control.takeover').then(function(payload){
          var d=payload && (payload.session || payload.data || payload) || {};
          if(String(d.controller||'')!=='self') throw new Error('控制权接管失败');
          takeoverBusy=false;
          takeoverClose();
          showToast('控制权已接管');
        }).catch(function(error){
          var message=apiErrorText(error);
          takeoverBusy=false;
          if(takeoverConfirmBtn){ takeoverConfirmBtn.disabled=false; takeoverConfirmBtn.removeAttribute('aria-busy'); }
          takeoverSetStatus(message,true);
          showToast(message);
          if(takeoverConfirmBtn) takeoverConfirmBtn.focus();
        });
      }

      /* ---------- 控制权 ---------- */
      function applyControlResult(endpoint,payload){
        var d=payload && (payload.session || payload.data || payload) || {};
        if(endpoint.indexOf('release')>=0){
          controlState='free';
        }else{
          var controller=String(d.controller||'');
          controlState=controller==='self'?'self':(controller==='other'?'other':'free');
        }
        renderControl();
        return payload;
      }
      function controlRequest(endpoint){
        if(controlBusy) return Promise.resolve(null);
        if(!currentSession || watchState!=='playing') return Promise.reject(new Error('请先开始观看会话'));
        controlBusy=true;
        renderControl();
        return Promise.resolve().then(function(){
          return window.ScrcpyGateApi.configured(endpoint,{params:{id:currentSession && currentSession.id,deviceId:selectedDevice && selectedDevice.id},method:'POST'});
        }).then(function(payload){ return applyControlResult(endpoint,payload); }).then(function(payload){
          controlBusy=false;
          renderControl();
          return payload;
        },function(error){
          controlBusy=false;
          renderControl();
          throw error;
        });
      }
      acquireBtn.addEventListener('click',function(){
        if(watchState!=='playing') return;
        // 只有观看权限：控制通道一定会被服务端拒（4403），直接给明确原因。
        if(!canControlNow()){ showToast('没有该设备的控制权限，请联系管理员授权'); return; }
        if(mirrorControlTools) mirrorControlTools.action('control_toggle',{state:controlState});
        if(controlState==='other'){
          takeoverOpen();
          return;
        }
        controlRequest(controlState==='self'?'sessions.control.release':'sessions.control.acquire').catch(function(error){ showToast(apiErrorText(error)); });
      });
      if(takeoverCloseBtn) takeoverCloseBtn.addEventListener('click',takeoverClose);
      if(takeoverCancelBtn) takeoverCancelBtn.addEventListener('click',takeoverClose);
      if(takeoverScrim) takeoverScrim.addEventListener('click',takeoverClose);
      if(takeoverConfirmBtn) takeoverConfirmBtn.addEventListener('click',takeoverSubmit);
      if(takeoverDialog) takeoverDialog.addEventListener('keydown',function(e){
        if(e.key!=='Tab') return;
        var items=takeoverFocusables();
        if(!items.length){ e.preventDefault(); return; }
        var first=items[0];
        var last=items[items.length-1];
        if(e.shiftKey&&document.activeElement===first){ e.preventDefault(); last.focus(); }
        else if(!e.shiftKey&&document.activeElement===last){ e.preventDefault(); first.focus(); }
      });

      /* ---------- 安卓导航键 ---------- */
      document.addEventListener('scrcpygate:keyboard',function(event){
        var active=!!(event&&event.detail&&event.detail.active);
        keyboardOn=active;
        if(keyboardBtn){
          keyboardBtn.classList.toggle('held',active);
          setDockTogglePressed(keyboardBtn,active);
          keyboardBtn.setAttribute('aria-label',active?'关闭键盘':'开启键盘输入');
          keyboardBtn.title=active?'关闭键盘':'开启键盘输入';
          keyboardBtn.innerHTML='<i data-lucide="keyboard"></i><span id="cb-keyboard-label">'+(active?'关闭键盘':'键盘')+'</span>';
        }
        if(popKeyboard){
          popKeyboard.classList.toggle('held',active);
          popKeyboard.setAttribute('aria-checked',String(active));
          popKeyboard.setAttribute('aria-label',active?'重新打开备用键盘':'开启备用键盘');
        }
        var keyboardState=document.getElementById('cb-pop-keyboard-state');
        if(keyboardState) keyboardState.textContent=active?'已开启':'备用';
        syncKeyboardViewport();
        if(window.lucide) lucide.createIcons();
      });
      function sendNavigationAction(action){
        if(navBusy || !currentSession || watchState!=='playing' || controlState!=='self') return Promise.resolve(null);
        if(mirrorControlTools) mirrorControlTools.action('navigation',{action:action});
        navBusy=true;
        renderControl();
        return Promise.resolve().then(function(){
          return window.ScrcpyGateApi.configured('sessions.action',{params:{id:currentSession.id},method:'POST',body:{action:action}});
        }).then(function(payload){
          showToast('操作已发送');
          return payload;
        },function(error){
          showToast(apiErrorText(error));
          throw error;
        }).then(function(payload){
          navBusy=false;
          renderControl();
          return payload;
        },function(error){
          navBusy=false;
          renderControl();
          throw error;
        });
      }
      ['back','home','tasks'].forEach(function(action){ document.getElementById('cb-'+action).addEventListener('click',function(){ sendNavigationAction(action).catch(function(){}); }); });

      /* ---------- 更多菜单：全屏、旋转和键盘 ---------- */
      var cbPop=document.getElementById('cb-pop');
      if(cbPop && cbPop.parentElement) document.body.appendChild(cbPop);

      function fullscreenSourceSize(detail){
        var width=Number(detail&&detail.width)||0;
        var height=Number(detail&&detail.height)||0;
        if(detail&&detail.authoritative===false){ width=0; height=0; }
        var v2=window.ScrcpyGateV2;
        if(!(width>0&&height>0)&&v2&&v2.state){
          if(v2.state.videoDimensionsAuthoritative){
            width=Number(v2.state.videoWidth)||0;
            height=Number(v2.state.videoHeight)||0;
          }
          if(!(width>0&&height>0)&&v2.state.videoEl){
            width=Number(v2.state.videoEl.videoWidth)||0;
            height=Number(v2.state.videoEl.videoHeight)||0;
          }
        }
        return {width:width,height:height};
      }
      function refreshVideoLayout(){
        // 旋转后自适应画框会改变面板尺寸；必须让适配器按新容器重新计算画面尺寸，
        // 否则画面会停留在按旧容器算出的尺寸（黑屏中间一个小框）。
        var v2=window.ScrcpyGateV2;
        if(v2&&typeof v2.refreshVideoLayout==='function') v2.refreshVideoLayout();
      }
      function clearAdaptiveVideoFrame(){
        if(!mirrorPanel) return;
        mirrorPanel.classList.remove('video-frame-adaptive');
        mirrorPanel.style.removeProperty('width');
        mirrorPanel.style.removeProperty('height');
        mirrorPanel.style.removeProperty('max-width');
        mirrorPanel.style.removeProperty('max-height');
        mirrorPanel.style.removeProperty('flex');
        mirrorPanel.style.removeProperty('--mirror-frame-gutter');
        refreshVideoLayout();
      }
      /* 手机上「媒体查询」比窗口宽度更可靠：横竖屏切换时 innerWidth 已经变了。 */
      function narrowViewport(){
        try{
          if(window.matchMedia&&window.matchMedia('(max-width:640px)').matches) return true;
        }catch(e){}
        return (Number(window.innerWidth)||0)<=640;
      }
      function adaptiveVideoFrameBounds(){
        if(!mirrorPanel) return {width:0,height:0};
        // Measure the normal 16:9 layout as the available envelope before
        // applying a device-specific aspect ratio.
        clearAdaptiveVideoFrame();
        var rect=mirrorPanel.getBoundingClientRect ? mirrorPanel.getBoundingClientRect() : null;
        var width=Number(rect&&rect.width)||Number(mirrorPanel.clientWidth)||0;
        var height=Number(rect&&rect.height)||Number(mirrorPanel.clientHeight)||0;
        if((width<=0||height<=0)&&workspace){
          width=Number(workspace.clientWidth)||0;
          height=Number(workspace.clientHeight)||0;
        }
        // 竖向空间取「面板顶部 → 控制栏顶部」的真实距离，而不是面板自身的 16:9
        // 自然高度：后者会让竖屏画面在窗口变矮（例如打开开发者工具）时底部留出大块空白。
        if(workspace&&rect&&window.getComputedStyle){
          var wsRect=workspace.getBoundingClientRect ? workspace.getBoundingClientRect() : null;
          var wsStyle=window.getComputedStyle(workspace);
          var padLeft=parseFloat(wsStyle.paddingLeft)||0;
          var padRight=parseFloat(wsStyle.paddingRight)||0;
          var padBottom=parseFloat(wsStyle.paddingBottom)||0;
          var contentWidth=(Number(wsRect&&wsRect.width)||Number(workspace.clientWidth)||0)-padLeft-padRight;
          if(contentWidth>0) width=contentWidth;
          var contentBottom=(Number(wsRect&&wsRect.bottom)||0)-padBottom;
          // 控制栏预留量按「控制栏自身高度 + 固定间距」计算，**不读它的当前位置**：
          // 视口高度 ≤700px 时控制栏不再贴底（.ctrl-dock 的 margin-top 变成 8px），
          // 它紧跟在画面下方；若用它的 top 反推可用高度，画面一变矮控制栏就上移，
          // 可用高度随之更小 —— 自反馈会把画面越算越小（实测同一个 900×620 窗口
          // 会在 114px 与 352px 之间漂移）。用高度预留则与画面尺寸无关。
          var dockHeight=0;
          if(ctrlDock&&ctrlDock.getBoundingClientRect){
            dockHeight=Math.round(ctrlDock.getBoundingClientRect().height)||Number(ctrlDock.offsetHeight)||0;
          }
          var dockReserve=dockHeight>0?dockHeight+10:0;
          var bottom=contentBottom-dockReserve;
          var availHeight=bottom-(Number(rect.top)||0);
          if(availHeight>0) height=availHeight;
        }
        return {width:Math.max(0,width),height:Math.max(0,height)};
      }
      function applyAdaptiveVideoFrame(detail){
        if(!mirrorPanel) return;
        if(fullscreenActive){
          clearAdaptiveVideoFrame();
          return;
        }
        var size=fullscreenSourceSize(detail);
        if(!(size.width>0&&size.height>0)){
          // 还没拿到真实尺寸：投屏中（刚点开始/重连）用 16:9 兜底先按投屏布局落位，
          // 否则会退回 CSS 的整宽 16:9 大框，等第一帧到了再缩回去 —— 用户看到的就是
          // 「开始投屏的一瞬间边框变得很大再缩回画面大小」。
          if(mirrorPanel.classList.contains('video-frame-adaptive')) return; // 已有画框：保持不动
          if(!streamLive()){ clearAdaptiveVideoFrame(); return; }
          size={width:16,height:9};
        }
        var v2=window.ScrcpyGateV2;
        var rotated=viewRotationTransposed();
        var sourceWidth=rotated?size.height:size.width;
        var sourceHeight=rotated?size.width:size.height;
        var sourceAspect=sourceWidth/sourceHeight;
        if(!(sourceAspect>0&&isFinite(sourceAspect))) return;
        var bounds=adaptiveVideoFrameBounds();
        if(!(bounds.width>0&&bounds.height>0)) return;
        // 手机上把画框内边距收到 4px：边框每少 1px，旋转后的横屏画面就多 2px 宽
        // （390 宽的手机上 8px→4px 等于画面宽 354→362），视觉上就是「边框没那么大」。
        var gutter=narrowViewport()?4:8;
        var maxContentWidth=Math.max(1,bounds.width-gutter*2);
        var maxContentHeight=Math.max(1,bounds.height-gutter*2);
        var scale=Math.min(maxContentWidth/sourceWidth,maxContentHeight/sourceHeight);
        if(!(scale>0&&isFinite(scale))) return;
        var contentWidth=Math.max(1,Math.round(sourceWidth*scale));
        var contentHeight=Math.max(1,Math.round(sourceHeight*scale));
        var frameWidth=contentWidth+gutter*2;
        var frameHeight=contentHeight+gutter*2;
        mirrorPanel.classList.add('video-frame-adaptive');
        mirrorPanel.style.setProperty('--mirror-frame-gutter',gutter+'px');
        mirrorPanel.style.width=frameWidth+'px';
        mirrorPanel.style.height=frameHeight+'px';
        mirrorPanel.style.maxWidth='100%';
        mirrorPanel.style.maxHeight='100%';
        mirrorPanel.style.flex='0 0 auto';
        refreshVideoLayout();
      }
      /* ---------- 方向：自动摆正 + 手动顺时针叠加 ----------
         画面方向由「设备真实方向 + 当前可用空间」自动决定（display-control.js 的纯状态层，
         0/90 两档）；「旋转」按钮在此基础上叠加一个 0/90/180/270 的偏移，每点一次 +90°，
         点满四次 offset 归零、回到自动角度 —— 既满足「每次点击顺时针转 90°」，又不会
         让用户转进一个回不去的角度。偏移只作用于当前会话，重新开始投屏时清零。 */
      var manualRotationOffset=0;
      // 手动旋转期间的「自动基准」（冻结值）；null = 未冻结，按当前可用空间算。
      var manualBaseRotation=null;
      var lastDeviceLandscape=null;
      function displaySnapshot(){
        return displayController?displayController.snapshot():null;
      }
      function displayRotation(){
        var snapshot=displaySnapshot();
        if(snapshot) return snapshot.rotation;
        var v2=window.ScrcpyGateV2;
        var value=v2&&v2.state?Number(v2.state.videoRotation):0;
        if(!isFinite(value)) return 0;
        var normalized=((value%360)+360)%360;
        return normalized===90||normalized===180||normalized===270?normalized:0;
      }
      function viewRotationDegrees(){
        return displayRotation();
      }
      function viewRotationTransposed(){
        var deg=viewRotationDegrees();
        return deg===90||deg===270;
      }
      function setViewRotation(degrees){
        var value=Number(degrees)||0;
        var normalized=((value%360)+360)%360;
        if(normalized!==0&&normalized!==90&&normalized!==180&&normalized!==270) normalized=0;
        if(displayController) displayController.setRotation(normalized);
        if(videoBox){
          videoBox.classList.toggle('rotated',normalized===90||normalized===270);
          videoBox.classList.toggle('rotated-180',normalized===180);
        }
        if(app) app.setAttribute('data-view-rotation',String(normalized));
        if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.setVideoRotation==='function'){
          window.ScrcpyGateV2.setVideoRotation(normalized);
        }
      }
      /* ---------- 全屏方向锁：目标方向按「最终显示方向」算 ----------
         设备横屏 → 锁横屏；但用户在竖屏手机上手动把画面转成竖的（横屏源被转置）之后，
         再锁横屏就等于把「竖着的画面」塞进横屏，画面只能缩成中间一条窄带 ——
         这正是用户反馈的「旋转后边框很大画面很小」。所以目标方向必须跟着**当前显示的
         画面方向**走：显示是竖的就锁竖屏，显示是横的就锁横屏。 */
      function fullscreenOrientationFor(deviceLandscape){
        var displayedLandscape=viewRotationTransposed()?!deviceLandscape:deviceLandscape;
        return displayedLandscape?'landscape':'portrait';
      }
      function lockFullscreenOrientation(orientation){
        if(!fullscreenActive) return;
        var screenOrientation=window.screen&&window.screen.orientation;
        if(!screenOrientation||typeof screenOrientation.lock!=='function') return;
        if(fullscreenOrientationTarget===orientation) return;
        fullscreenOrientationTarget=orientation;
        try{
          var lockResult=screenOrientation.lock(orientation);
          if(lockResult&&typeof lockResult.catch==='function'){
            lockResult.catch(function(){ fullscreenOrientationTarget=''; });
          }
        }catch(e){ fullscreenOrientationTarget=''; }
      }
      /* 手动旋转之后立刻按新方向重新申请一次（手机上就是让屏幕跟着转，画面才能铺满）。 */
      function applyFullscreenOrientationLock(){
        if(!fullscreenActive) return;
        var size=fullscreenSourceSize(lastVideoSizeDetail);
        if(!(size.width>0&&size.height>0)) return;
        lockFullscreenOrientation(fullscreenOrientationFor(size.width>=size.height));
      }
      /* ---------- 跟随设备：画面方向跟着设备本身走 ----------
         默认不转：设备竖屏就竖着显示（宽屏窗口里也不再为了「面积更大」把竖屏画面掰横，
         ISSUE-157：那会让工作台默认画面看起来是横向的）。
         只在一种情况下转 90°：竖屏窗口（手机竖屏、桌面窄窗）里看横屏设备（竖屏游戏切成
         横屏等），此时横带会变成铺满高度；面积增益不足 1.2 倍仍不动，避免接近正方形时抖。
         方向本身由 display-control.js 的纯状态层算。 */
      function autoFitRotationDegrees(){
        if(!displayController) return null;
        // 把 DOM 里的实测值喂给状态层：源尺寸（解码后的流）与可用空间（工作区）。
        // 用「当前可用空间」而不是已经适配过的画框，否则会自反馈 —— 横屏画面已经把画框
        // 压成一条横带，于是永远觉得不用转。adaptiveVideoFrameBounds() 会先清掉自适应画框
        // 再量工作区，所以调用方拿到结果后要重新 applyAdaptiveVideoFrame()。
        var size=fullscreenSourceSize(lastVideoSizeDetail);
        if(!(size.width>0&&size.height>0)) return null;
        displayController.setSource(size.width,size.height);
        var bounds=adaptiveVideoFrameBounds();
        displayController.setViewport(Number(bounds.width)||0,Number(bounds.height)||0);
        // 全屏和窗口用同一套判断，**不做特例**：
        //  - 手机真的横过来了（screen.orientation.lock 生效，可用空间变横）→ 这里返回 0，
        //    画面正着铺满整块横屏；
        //  - 手机没能横过来（浏览器不支持/拒绝锁屏）→ 可用空间仍是竖的 → 返回 90°，
        //    画面铺满高度（横屏内容被转 90°，观感是「歪着但尽量大」，见
        //    ISSUE-159 的用户反馈：全屏后横屏设备画面变小）。
        return displayController.resolveAuto();
      }
      /* 按当前源尺寸 + 可用空间自动摆正，再叠加手动偏移；返回本次是否改变了方向。
         手动旋转期间自动基准会被**冻结**：否则全屏里旋转会让屏幕跟着转，屏幕一转可用空间
         就变了，自动摆正又算出另一个基准，画面会被「自动 + 偏移」连转两次（用户看到的
         是「转一下画面反而又转回去/缩成一条」）。冻结后：偏移 0→90→180→270→0 就是四次
         顺时针回原位，屏幕也跟着转回原方向。会话重新开始时解冻（见 reset 处）。 */
      function autoRotationWithOffset(){
        if(manualBaseRotation===null){
          var base=autoFitRotationDegrees();
          if(base===null) return null;
          if(!manualRotationOffset) return base;
          manualBaseRotation=base;
        }
        if(manualBaseRotation===null) return null;
        return ((manualBaseRotation+manualRotationOffset)%360+360)%360;
      }
      function applyAutoFitRotation(){
        var desired=autoRotationWithOffset();
        if(desired===null||desired===viewRotationDegrees()) return false;
        setViewRotation(desired);
        return true;
      }
      /* 顺时针 90°：偏移 +90 后重新按「自动角度 + 偏移」落位。 */
      function rotateViewClockwise(){
        manualRotationOffset=(manualRotationOffset+90)%360;
        var desired=autoRotationWithOffset();
        if(desired===null){
          // 还没有源尺寸（未投屏/刚连接）：纯顺时针转，等尺寸到位后由自动摆正接管。
          desired=((viewRotationDegrees()+90)%360+360)%360;
          if(displayController) displayController.setRotation(desired);
          manualRotationOffset=0;
        }
        setViewRotation(desired);
        applyAdaptiveVideoFrame(lastVideoSizeDetail);
        // 全屏里旋转会改变「最终显示方向」：立刻按新方向重新申请屏幕方向锁，
        // 让手机跟着转（否则竖着的画面被留在横屏里，只能缩成中间一条）。
        applyFullscreenOrientationLock();
        syncVideoOrientation(lastVideoSizeDetail);
        showToast('画面已顺时针旋转 90°（当前 '+viewRotationDegrees()+'°）');
        renderControl();
      }
      /**
       * 设备自己转了方向（横竖互换）= 重新按新方向自动摆正（不一定是正向）。
       */
      function applyAutoVideoRotation(){
        return applyAutoFitRotation();
      }
      function syncVideoOrientation(detail){
        if(!app) return;
        // 不带尺寸的调用（进/出全屏、窗口缩放）也要能用上一次的真实画面尺寸：
        // 否则刚进全屏时还没有新的一帧，方向锁这一段会被跳过（手机上就不会自动横过来）。
        if(!(detail&&Number(detail.width)>0&&Number(detail.height)>0)) detail=lastVideoSizeDetail;
        var size=fullscreenSourceSize(detail);
        // 只在事件真的带来尺寸时记住：resize/orientationchange 触发的空调用
        // 不会冲掉上一次的真实画面尺寸。
        if(detail&&Number(detail.width)>0&&Number(detail.height)>0) lastVideoSizeDetail=detail;
        applyAdaptiveVideoFrame(detail);
        if(!(size.width>0&&size.height>0)) return;
        var deviceLandscape=size.width>=size.height;
        if(lastDeviceLandscape===null) lastDeviceLandscape=deviceLandscape;
        var rotationChanged=false;
        if(deviceLandscape!==lastDeviceLandscape){
          lastDeviceLandscape=deviceLandscape;
          rotationChanged=applyAutoVideoRotation();
          if(rotationChanged){
            var autoDegrees=viewRotationDegrees();
            showToast('设备已切换为'+(deviceLandscape?'横屏':'竖屏')+' · '+(autoDegrees?'画面已自动旋转 '+autoDegrees+'°':'画面方向已自动复位'));
          }
        }else{
          // 屏幕方向变化（手机横竖切换、进出全屏）也重新算一次自动方向。
          rotationChanged=applyAutoFitRotation();
        }
        // autoFitRotationDegrees() 会清掉自适应画框来量「可用空间」，所以这里统一按（可能
        // 已经更新的）画面方向重算一次画框 —— 方向变了时这一步本来也是必须的。
        applyAdaptiveVideoFrame(lastVideoSizeDetail);
        // 屏幕方向锁按「画面内容方向」算，不按摆正后的视图算：横屏设备在竖屏手机上
        // 宁可让手机真的横过来（画面自然铺满），也不能算成 portrait 把手机锁在竖屏 ——
        // 那正是「手机端全屏下没有自动横向」（用户反馈）。手动旋转叠加后，画面显示方向
        // 可能已经反过来，此时目标方向也跟着反过来（否则旋转后画面缩成窄条）。
        var orientation=fullscreenOrientationFor(deviceLandscape);
        app.setAttribute('data-video-orientation',orientation);
        // 画面尺寸/方向变了，顶部留白也跟着变 —— 让位位移要重算。
        syncFullscreenDockLift();
        lockFullscreenOrientation(orientation);
      }
      /* ---------- 自适应画框：跟随窗口尺寸重算 ----------
         自适应画框是按「当时可用空间」算出的像素尺寸。窗口从小变大（小窗口起流后
         最大化浏览器、旋转屏幕、拖拽窗口）时必须重算，否则画面停在旧尺寸，周围留下
         很大一块空背景——这正是「最大化后投屏边缘变很大」的成因。
         已有的 resize 处理器在同一帧里就算过一次，但那一刻布局可能还没稳定；
         这里再补一次 rAF 收尾（帧内完成，不会闪烁）。 */
      var lastVideoSizeDetail=undefined;
      var adaptiveFrameRaf=0;
      var adaptiveFrameTimer=0;
      function adaptiveFrameWanted(){
        if(fullscreenActive) return false;
        if(lastVideoSizeDetail) return true;
        return !!(mirrorPanel&&mirrorPanel.classList.contains('video-frame-adaptive'));
      }
      function runAdaptiveFrameRefresh(){
        if(!adaptiveFrameWanted()) return;
        // detail 为空时 applyAdaptiveVideoFrame 会回退到适配器里的真实画面尺寸。
        applyAdaptiveVideoFrame(lastVideoSizeDetail);
        refreshVideoLayout();
      }
      function scheduleAdaptiveFrameRefresh(){
        if(!adaptiveFrameWanted()) return;
        if(adaptiveFrameTimer){ window.clearTimeout(adaptiveFrameTimer); adaptiveFrameTimer=0; }
        if(adaptiveFrameRaf) return;
        var raf=window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(fn){ return window.setTimeout(fn,16); };
        adaptiveFrameRaf=raf(function(){
          adaptiveFrameRaf=0;
          runAdaptiveFrameRefresh();
          // 再补一次：媒体查询切换 / 过渡动画结束后布局才最终稳定。
          adaptiveFrameTimer=window.setTimeout(function(){
            adaptiveFrameTimer=0;
            runAdaptiveFrameRefresh();
          },180);
        });
      }
      window.addEventListener('resize',scheduleAdaptiveFrameRefresh,{passive:true});
      window.addEventListener('orientationchange',scheduleAdaptiveFrameRefresh,{passive:true});
      if(window.visualViewport&&window.visualViewport.addEventListener){
        window.visualViewport.addEventListener('resize',scheduleAdaptiveFrameRefresh,{passive:true});
      }
      document.addEventListener('fullscreenchange',scheduleAdaptiveFrameRefresh);
      function updateFullscreenButton(){
        if(!fullscreenBtn) return;
        var label=fullscreenActive?'退出全屏':'全屏显示';
        var state=fullscreenActive?'全屏':'窗口';
        setDockTogglePressed(fullscreenBtn,fullscreenActive);
        fullscreenBtn.setAttribute('aria-label',label);
        fullscreenBtn.title=label;
        fullscreenBtn.classList.toggle('held',fullscreenActive);
        fullscreenBtn.innerHTML='<i data-lucide="'+(fullscreenActive?'minimize':'maximize')+'"></i><span>全屏</span><small id="cb-fullscreen-state">'+state+'</small>';
        if(window.lucide) lucide.createIcons();
      }
      function placeMoreMenuForFullscreen(active){
        if(!cbPop||!app) return;
        var target=active?app:document.body;
        if(cbPop.parentNode===target) return;
        target.appendChild(cbPop);
      }
      /* ---------- 全屏画质：进全屏切「全屏预设」，退出全屏恢复 ---------- */
      var fullscreenQualityBusy=false;
      var fullscreenQualityApplied=false;
      function setFullscreenQuality(enabled,silent){
        if(fullscreenQualityBusy) return Promise.resolve(null);
        // 没有正在播放的会话时不打扰后端：进全屏但还没开始观看时，画质切换没有意义。
        if(watchState!=='playing') return Promise.resolve(null);
        var api=window.ScrcpyGateApi;
        if(!api||!api.isConfigured||!api.isConfigured('mirror.fullscreen')) return Promise.resolve(null);
        var deviceId=(window.ScrcpyGateV2&&window.ScrcpyGateV2.state&&window.ScrcpyGateV2.state.deviceId)||'';
        if(!deviceId) return Promise.resolve(null);
        fullscreenQualityBusy=true;
        return api.configured('mirror.fullscreen',{method:'PUT',params:{deviceId:deviceId},body:{fullscreen:!!enabled,deviceId:deviceId}})
          .then(function(result){
            fullscreenQualityBusy=false;
            var d=result&&(result.data&&typeof result.data==='object'?result.data:result)||{};
            if(enabled) fullscreenQualityApplied=!!d.fullscreen_profile;
            else fullscreenQualityApplied=false;
            if(silent) return d;
            var effective=d.effective||{};
            var fps=Number(effective.max_fps)||0;
            var bitrate=Number(effective.video_bit_rate)||0;
            var bits=bitrate>0?((Math.round(bitrate/100000)/10)+'Mbps'):'';
            var quality=[bits,(fps?fps+'fps':'')].filter(Boolean).join(' · ');
            if(!enabled){
              showToast('已退出全屏 · 恢复原画质');
            }else if(d.deferred){
              showToast('有其他观看端，全屏画质将在你独享时切换');
            }else if(d.fullscreen_profile){
              showToast('全屏画质已提升'+(quality?(' · '+quality):''));
            }else{
              showToast('后台未配置可用的全屏画质，保持当前设置');
            }
            return d;
          },function(error){
            fullscreenQualityBusy=false;
            if(!silent) showToast('全屏画质切换失败 · '+apiErrorText(error));
            return null;
          });
      }
      function applyFullscreenState(active,nativeMode){
        if(!app) return;
        var next=!!active;
        var wasActive=fullscreenActive;
        if(!next&&fullscreenNative&&window.screen&&window.screen.orientation&&typeof window.screen.orientation.unlock==='function'){
          try{ window.screen.orientation.unlock(); }catch(e){}
        }
        fullscreenActive=next;
        fullscreenNative=next&&!!nativeMode;
        // 进/出全屏都重新尝试一次方向锁：进全屏那一刻调用 lock 会被浏览器拒绝（页面还没真正
        // 进入全屏），真正生效的是 fullscreenchange 之后这次；不复位目标就会因为「已经请求过
        // 同一个方向」而跳过，手机于是永远停在竖屏（用户反馈：全屏下没有自动横向）。
        fullscreenOrientationTarget='';
        if(!next){
          fullscreenOrientationTarget='';
          dockHidden=windowDockHidden;
        }
        // 全屏是沉浸式播放：控制栏默认收进边缘，只留一条细把手，点一下才展开
        // （需要手动点「获取控制」的情况由 autoAcquireControlOnFullscreen 兜底摊开）。
        if(next&&!wasActive){ windowDockHidden=dockHidden; dockHidden=true; }
        app.classList.toggle('is-fullscreen',next);
        app.classList.toggle('dock-menu-collapsed',dockHidden);
        app.setAttribute('data-fullscreen',String(next));
        if(ctrlDock){ ctrlDock.classList.toggle('dock-hidden',dockHidden); ctrlDock.inert=dockHidden; }
        placeMoreMenuForFullscreen(next);
        // 全屏里控制栏只留图标（见 CSS），宽度变了要重新分配更多菜单 —— 放在切换
        // is-fullscreen 之后，密度判定读到的才是切换后的真实宽度。
        syncDockDensity();
        // 全屏留白就是纯黑（#000）：不拉伸、不裁切，也不加任何背景层。
        // 全屏默认获取控制（可在「更多」里关掉）：进入全屏后自动 acquire，非强制接管。
        if(next&&!wasActive) autoAcquireControlOnFullscreen();
        if(next&&!dockHidden) scheduleDockAutoHide();
        if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.setFullscreenMode==='function'){
          window.ScrcpyGateV2.setFullscreenMode(next);
        }
        if(next!==wasActive){
          if(next){
            setFullscreenQuality(true);
          }else{
            if(fullscreenQualityApplied) setFullscreenQuality(false,true);
            fullscreenQualityApplied=false;
          }
        }
        updateFullscreenButton();
        updateFullscreenDockToggle();
        applyDockAnchor();
        syncKeyboardViewport();
        syncVideoOrientation();
        syncFullscreenDockLift();
      }

      function updateFullscreenDockToggle(){
        if(!fullscreenDockToggle) return;
        var visible=viewMode!=='grid';
        fullscreenDockToggle.setAttribute('aria-hidden',String(!visible));
        fullscreenDockToggle.tabIndex=visible?0:-1;
        fullscreenDockToggle.setAttribute('aria-expanded',String(!dockHidden));
        var attention=visible&&dockHidden&&controlAttentionNeeded();
        // 贴边的细条只用 CSS ::before 画一条亮线（见 mirror-page.css）：不再写图标/文字，
        // 可读名称与提示走 aria-label / title，状态用 .is-attention 点亮。
        var label=attention?'展开控制栏并获取控制':'展开控制栏';
        if(!dockHidden) label='收起控制栏';
        var hint=fullscreenActive?label+'（拖动可沿屏幕左右侧移动，双击或按 0 回到右侧中间）':label;
        fullscreenDockToggle.setAttribute('aria-label',tr(hint));
        fullscreenDockToggle.title=tr(hint);
        fullscreenDockToggle.classList.toggle('is-attention',attention);
        var toggleIcon=fullscreenActive?'':(dockHidden?'chevron-up':'chevron-down');
        if(fullscreenDockToggle.dataset.icon!==toggleIcon){
          fullscreenDockToggle.dataset.icon=toggleIcon;
          fullscreenDockToggle.innerHTML=toggleIcon?'<i data-lucide="'+toggleIcon+'" aria-hidden="true"></i>':'';
          if(window.lucide) lucide.createIcons();
        }
      }

      var DOCK_AUTO_HIDE_MS=4000;
      var dockAutoHideTimer=null;
      var dockLastActivityAt=0;
      /* 全屏后是否自动获取控制（本浏览器偏好，默认开）。
         开关在「更多」菜单里；开启时进全屏直接尝试 acquire（非强制接管），
         关掉后回到「手动点获取控制」。 */
      var FULLSCREEN_AUTO_CONTROL_KEY='scrcpygate-fullscreen-auto-control';
      var fullscreenAutoControl=true;
      function savedFullscreenAutoControl(){
        try{
          var raw=localStorage.getItem(FULLSCREEN_AUTO_CONTROL_KEY);
          if(raw==='0') return false;
          if(raw==='1') return true;
        }catch(e){}
        return true;
      }
      function renderAutoControlToggle(){
        if(!popAutoControl) return;
        var label=fullscreenAutoControl?'全屏后默认获取控制（已开启）':'全屏后默认获取控制（已关闭）';
        popAutoControl.setAttribute('aria-checked',String(fullscreenAutoControl));
        popAutoControl.classList.toggle('held',fullscreenAutoControl);
        popAutoControl.setAttribute('aria-label',label);
        popAutoControl.title=label;
        var state=document.getElementById('cb-pop-auto-control-state');
        if(state) state.textContent=fullscreenAutoControl?'开':'关';
      }
      function watchingNow(){ return watchState==='playing'; }
      /* 当前账号对该设备是否有控制权限（后端 can_control）。只有观看权限时不要让他点
         「获取控制」——控制通道会被服务端以 4403 拒掉，用户只会看到「连接失败」。 */
      function canControlNow(){ return !!(selectedDevice&&selectedDevice.canControl!==false); }
      /* 还在播放但控制权不在自己手上：底部「获取控制 / 接管控制」是此刻唯一该点的按钮。
         这种状态下控制栏不参与自动收起 —— 手机上 4s 就滑走会让人以为按钮点不动。
         只有观看权限时没有待办动作，仍然走沉浸式自动收起。 */
      function controlAttentionNeeded(){ return watchingNow()&&controlState!=='self'&&canControlNow(); }
      function autoAcquireControlOnFullscreen(){
        if(!fullscreenAutoControl||!fullscreenActive) return;
        if(!canControlNow()) return;
        if(!controlAttentionNeeded()) return;
        if(controlState==='other'){
          showToast('设备正被其他用户控制，可点「接管控制」');
          revealFullscreenDockForControl();
          return;
        }
        if(controlBusy||!acquireBtn||acquireBtn.disabled) return;
        // 等全屏切换稳定后再点，避免和布局/尺寸重算抢同一帧。
        window.setTimeout(function(){
          if(!fullscreenActive||!fullscreenAutoControl||!controlAttentionNeeded()) return;
          if(controlBusy||acquireBtn.disabled) return;
          try{ acquireBtn.click(); }catch(e){}
          // 自动获取失败（权限/占用/超时）时把控制栏摊开：全屏收起状态下
          // 「获取控制」是唯一该点的按钮，只剩一条把手会让人以为点不动。
          window.setTimeout(function(){
            if(fullscreenActive&&dockHidden&&controlAttentionNeeded()) revealFullscreenDockForControl();
          },1800);
        },350);
      }
      /* 全屏收起时把手是唯一入口：确认拿不到控制权就把控制栏摊开。 */
      function revealFullscreenDockForControl(){
        if(!fullscreenActive||!dockHidden) return;
        setFullscreenDockHidden(false);
      }
      function cancelDockAutoHide(){
        if(dockAutoHideTimer){ clearInterval(dockAutoHideTimer); dockAutoHideTimer=null; }
      }
      function noteDockActivity(){
        if(!fullscreenActive||dockHidden) return;
        dockLastActivityAt=Date.now();
      }
      function dockShouldStayOpen(){
        if(dockDrag) return true;
        // 正在发起的动作（旋转/键盘/导航/获取控制）期间不要把控制栏收走，
        // 否则点了按钮的 1s 内菜单自己滑没了，看起来像「点一下菜单就消失」。
        if(moreActionBusy||navBusy||controlBusy) return true;
        var active=document.activeElement;
        if(active&&active!==fullscreenDockToggle&&ctrlDock&&ctrlDock.contains&&ctrlDock.contains(active)) return true;
        if(active&&cbPop&&cbPop.contains&&cbPop.contains(active)) return true;
        return false;
      }
      function scheduleDockAutoHide(){
        cancelDockAutoHide();
        if(!fullscreenActive||dockHidden) return;
        dockLastActivityAt=Date.now();
        // 每秒看一眼「最近有没有动作」：鼠标移动/点击/滚轮/按键都会续期，
        // 只是把指针停在那里不动不算 —— 否则控制栏滑到光标下面时就再也收不回去了。
        dockAutoHideTimer=setInterval(function(){
          if(!fullscreenActive||dockHidden){ cancelDockAutoHide(); return; }
          if(dockShouldStayOpen()){ dockLastActivityAt=Date.now(); return; }
          if(controlAttentionNeeded()){ dockLastActivityAt=Date.now(); return; }
          if(Date.now()-dockLastActivityAt<DOCK_AUTO_HIDE_MS) return;
          setFullscreenDockHidden(true);
        },1000);
      }
      document.addEventListener('pointermove',noteDockActivity,true);
      document.addEventListener('pointerdown',noteDockActivity,true);
      document.addEventListener('keydown',noteDockActivity,true);
      document.addEventListener('wheel',noteDockActivity,{capture:true,passive:true});

      function setFullscreenDockHidden(hidden){
        if(hidden){
          closeMoreMenu(false);
          if(ctrlDock&&ctrlDock.contains(document.activeElement)&&fullscreenDockToggle) fullscreenDockToggle.focus({preventScroll:true});
        }
        if(mirrorControlTools) mirrorControlTools.setDockHidden(!!hidden); else dockHidden=!!hidden;
        if(!fullscreenActive) windowDockHidden=dockHidden;
        if(ctrlDock){ ctrlDock.classList.toggle('dock-hidden',dockHidden); ctrlDock.inert=dockHidden; }
        app.classList.toggle('dock-menu-collapsed',dockHidden);
        updateFullscreenDockToggle();
        applyDockAnchor();
        scheduleDockDensity();
        scheduleAdaptiveFrameRefresh();
        // 展开/收起会改变「画面该不该让位」，等布局稳定后再算位移（见下面的让位逻辑）。
        scheduleFullscreenDockLift();
        // 展开后空闲一段时间自动收回边缘；收起时不再计时。
        if(dockHidden) cancelDockAutoHide(); else scheduleDockAutoHide();
      }

      /* ---------- 全屏：展开控制栏时画面向上让位 ----------
         全屏里控制栏浮在画面之上，展开时会盖住画面底部。这里把画面整体上移
         「控制栏高度 + 它离屏幕底部的距离」，用顶部本来就空着的留白换出底部空间，
         收起后归位（纯 CSS transform + 过渡，见 mirror-page.css 的 .dock-lifted）。
         位移量取「需要让出的高度」与「画面顶部现有留白」的较小值：画面本来就铺满
         高度（例如横屏全屏）时留白为 0，于是不动 —— 绝不裁掉画面。
         只对停在屏幕下半部分的控制栏生效：拖到画面中间的锚点不该把画面顶走。
         行为由后台「投屏管理 → 投屏行为 → 全屏打开控制栏时画面上移」控制（默认开）。 */
      var FULLSCREEN_DOCK_LIFT_FEATURE='fullscreen_dock_lift';
      function fullscreenDockLiftEnabled(){
        if(!workbenchFeatures) return true;
        return workbenchFeatureEnabled(FULLSCREEN_DOCK_LIFT_FEATURE);
      }
      function dockLiftRoom(){
        if(!ctrlDock||!ctrlDock.getBoundingClientRect) return 0;
        var dockRect=ctrlDock.getBoundingClientRect();
        if(!(dockRect.height>0)) return 0;
        var viewportHeight=Number(window.innerHeight)||0;
        if(viewportHeight>0&&dockRect.top<viewportHeight/2) return 0;
        var below=viewportHeight>0?Math.max(0,viewportHeight-dockRect.bottom):0;
        return Math.round(dockRect.height+below);
      }
      function pictureTopSlack(){
        // 画面在面板里居中：顶部留白 =（面板高 - 画面高）/ 2。用高度差而不是位置差：
        // 位置会随本次让位改变，用位置反推会自反馈（位移越算越小）。
        if(!mirrorPanel||!mirrorPanel.getBoundingClientRect) return 0;
        var panelRect=mirrorPanel.getBoundingClientRect();
        var height=0;
        ['sg-raw-v2-video','raw-v2-surface'].forEach(function(id){
          var el=document.getElementById(id);
          if(!el||!el.getBoundingClientRect) return;
          var rect=el.getBoundingClientRect();
          if(Number(rect.height)>height) height=Number(rect.height);
        });
        if(!(height>0)) return 0;
        return Math.max(0,Math.round((panelRect.height-height)/2));
      }
      function syncFullscreenDockLift(){
        if(!app) return;
        var lift=0;
        if(fullscreenActive&&!dockHidden&&fullscreenDockLiftEnabled()){
          lift=Math.min(dockLiftRoom(),pictureTopSlack());
        }
        app.style.setProperty('--mirror-dock-lift',(lift>0?lift:0)+'px');
        app.classList.toggle('dock-lifted',lift>0);
      }
      var dockLiftRaf=0;
      var dockLiftTimer=0;
      function scheduleFullscreenDockLift(){
        if(!fullscreenActive){ syncFullscreenDockLift(); return; }
        if(dockLiftTimer){ window.clearTimeout(dockLiftTimer); dockLiftTimer=0; }
        if(dockLiftRaf) return;
        var raf=window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(fn){ return window.setTimeout(fn,16); };
        dockLiftRaf=raf(function(){
          dockLiftRaf=0;
          syncFullscreenDockLift();
          // 再补一次：媒体查询切换 / 控制栏过渡结束后布局才最终稳定。
          dockLiftTimer=window.setTimeout(function(){
            dockLiftTimer=0;
            syncFullscreenDockLift();
          },180);
        });
      }
      window.addEventListener('resize',scheduleFullscreenDockLift,{passive:true});
      window.addEventListener('orientationchange',scheduleFullscreenDockLift,{passive:true});
      if(window.visualViewport&&window.visualViewport.addEventListener){
        window.visualViewport.addEventListener('resize',scheduleFullscreenDockLift,{passive:true});
      }
      document.addEventListener('fullscreenchange',scheduleFullscreenDockLift);

      /* 单排工具栏：先收文字，仍放不下时将次要操作移入「更多」。 */
      function dockOverflow(){
        if(!ctrlDock) return 0;
        return Math.max(0,(Number(ctrlDock.scrollWidth)||0)-(Number(ctrlDock.clientWidth)||0));
      }
      function setDockTogglePressed(button,pressed){
        button.setAttribute(button.getAttribute('role')==='menuitemcheckbox'?'aria-checked':'aria-pressed',String(pressed));
      }
      function syncDockDensity(){
        if(!ctrlDock||dockHidden||!ctrlDock.offsetWidth||(cbPop&&cbPop.classList.contains('open'))) return;
        restoreDockOverflow();
        ctrlDock.classList.remove('dock-compact');
        if(dockOverflow()<=0) return;
        ctrlDock.classList.add('dock-compact');
        // Keep one row with full touch targets. Move the actual buttons so
        // handlers, permission flags and changing labels stay in sync.
        var candidates=['cb-rotate','cb-keyboard','cb-alas','cb-tasks','cb-back','cb-home','cb-fullscreen','cb-watch','cb-acquire'];
        Array.prototype.forEach.call(ctrlDock.querySelectorAll(':scope > button'),function(el){
          if(candidates.indexOf(el.id)<0) candidates.unshift(el.id);
        });
        candidates.some(function(id){
          if(dockOverflow()<=0) return true;
          var el=document.getElementById(id);
          if(!el||el.parentNode!==ctrlDock||!el.getBoundingClientRect().width) return false;
          var anchor=document.createComment('dock action');
          el.before(anchor);
          var toggle=el.hasAttribute('aria-pressed');
          dockOverflowItems.push({element:el,anchor:anchor,role:el.getAttribute('role'),toggle:toggle});
          el.setAttribute('role',toggle?'menuitemcheckbox':'menuitem');
          if(toggle){ el.setAttribute('aria-checked',el.getAttribute('aria-pressed')); el.removeAttribute('aria-pressed'); }
          cbPop.appendChild(el);
          var moreWrap=document.getElementById('cb-more');
          if(dockOverflowItems.length===1) dockMoreWasOff=moreWrap.hasAttribute('data-feature-off');
          moreWrap.removeAttribute('data-feature-off');
          moreBtn.disabled=false;
          return false;
        });
      }
      var dockOverflowItems=[];
      var dockMoreWasOff=false;
      function restoreDockOverflow(){
        if(dockOverflowItems&&dockOverflowItems.length&&dockMoreWasOff) document.getElementById('cb-more').setAttribute('data-feature-off','');
        (dockOverflowItems||[]).forEach(function(item){
          item.anchor.replaceWith(item.element);
          if(item.role) item.element.setAttribute('role',item.role); else item.element.removeAttribute('role');
          if(item.toggle){ item.element.setAttribute('aria-pressed',item.element.getAttribute('aria-checked')); item.element.removeAttribute('aria-checked'); }
        });
        dockOverflowItems=[];
      }
      var dockDensityRaf=0;
      function scheduleDockDensity(){
        if(dockDensityRaf) return;
        var raf=window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(fn){ return window.setTimeout(fn,16); };
        dockDensityRaf=raf(function(){ dockDensityRaf=0; syncDockDensity(); scheduleFullscreenDockLift(); });
      }
      window.addEventListener('resize',scheduleDockDensity,{passive:true});
      window.addEventListener('orientationchange',scheduleDockDensity,{passive:true});

      /* ---------- 全屏：控制栏可拖动（收起的把手与整条控制栏共用锚点） ----------
         需求：全屏后菜单不该被钉在底部，按钮和控制栏都要能拖。这里用视口比例存锚点
         （旋转/缩放窗口后位置仍然合理）。收起的「边缘把手」是贴着屏幕左侧/右侧的
         一条细条：它有自己的位置（哪一侧 + 竖直比例），拖它只沿边移动/换边，
         控制栏的位置仍由它自己的锚点决定（拖控制栏本体调整）。
         双击把手或按钮上的 Home/0 让把手回到右侧居中。 */
      var DOCK_POS_KEY='scrcpygate-fullscreen-dock-pos';
      var DOCK_HANDLE_KEY='scrcpygate-fullscreen-dock-handle';
      var dockAnchor={fx:0.5,fy:1};
      var dockHandle={side:'right',fy:0.5};
      var dockDrag=null;
      var dockDragEndedAt=0;
      var DOCK_EDGE=8;
      function clampNumber(value,min,max){ if(max<min) return min; return Math.min(max,Math.max(min,value)); }
      function dockGrip(){ return document.getElementById('dock-grip'); }
      function loadDockAnchor(){
        try{
          var raw=window.localStorage?window.localStorage.getItem(DOCK_POS_KEY):'';
          if(raw){
            var parsed=JSON.parse(raw);
            if(parsed&&isFinite(parsed.fx)&&isFinite(parsed.fy)){
              dockAnchor.fx=clampNumber(Number(parsed.fx),0,1);
              dockAnchor.fy=clampNumber(Number(parsed.fy),0,1);
            }
          }
          var rawHandle=window.localStorage?window.localStorage.getItem(DOCK_HANDLE_KEY):'';
          if(rawHandle){
            var parsedHandle=JSON.parse(rawHandle);
            if(parsedHandle&&parsedHandle.side==='left') dockHandle.side='left';
            if(parsedHandle&&isFinite(parsedHandle.fy)) dockHandle.fy=clampNumber(Number(parsedHandle.fy),0,1);
          }
        }catch(e){}
      }
      function saveDockAnchor(){
        try{
          if(!window.localStorage) return;
          window.localStorage.setItem(DOCK_POS_KEY,JSON.stringify({
            fx:Math.round(dockAnchor.fx*1000)/1000,
            fy:Math.round(dockAnchor.fy*1000)/1000
          }));
        }catch(e){}
      }
      function saveDockHandle(){
        try{
          if(!window.localStorage) return;
          window.localStorage.setItem(DOCK_HANDLE_KEY,JSON.stringify({
            side:dockHandle.side==='left'?'left':'right',
            fy:Math.round(dockHandle.fy*1000)/1000
          }));
        }catch(e){}
      }
      function dockMetrics(){
        var vw=window.innerWidth||document.documentElement.clientWidth||0;
        var vh=window.innerHeight||document.documentElement.clientHeight||0;
        var dockRect=ctrlDock&&ctrlDock.getBoundingClientRect?ctrlDock.getBoundingClientRect():null;
        var toggleRect=fullscreenDockToggle&&fullscreenDockToggle.getBoundingClientRect?fullscreenDockToggle.getBoundingClientRect():null;
        return {
          vw:vw,vh:vh,
          dockW:Math.round(Number(dockRect&&dockRect.width)||0)||240,
          dockH:Math.round(Number(dockRect&&dockRect.height)||0)||40,
          toggleW:Math.round(Number(toggleRect&&toggleRect.width)||0)||96,
          toggleH:Math.round(Number(toggleRect&&toggleRect.height)||0)||44
        };
      }
      function applyDockAnchor(){
        if(!ctrlDock||!app) return;
        if(!fullscreenActive){
          app.classList.remove('dock-dragging');
          ctrlDock.classList.remove('dock-placed');
          ctrlDock.style.removeProperty('left');
          ctrlDock.style.removeProperty('top');
          if(fullscreenDockToggle){
            fullscreenDockToggle.classList.remove('dock-placed');
            fullscreenDockToggle.classList.remove('dock-edge-left','dock-edge-right');
            fullscreenDockToggle.style.removeProperty('left');
            fullscreenDockToggle.style.removeProperty('top');
          }
          return;
        }
        var m=dockMetrics();
        var halfW=Math.max(m.dockW,m.toggleW)/2;
        var x=clampNumber(dockAnchor.fx*m.vw,halfW+DOCK_EDGE,Math.max(halfW+DOCK_EDGE,m.vw-halfW-DOCK_EDGE));
        var y=clampNumber(dockAnchor.fy*m.vh,m.dockH/2+DOCK_EDGE,Math.max(m.dockH/2+DOCK_EDGE,m.vh-m.dockH/2-DOCK_EDGE));
        ctrlDock.classList.add('dock-placed');
        ctrlDock.style.left=x+'px';
        ctrlDock.style.top=y+'px';
        if(fullscreenDockToggle){
          // 细条贴边：右侧 → 元素中心在 vw - w/2（右边缘正好压住屏幕边），左侧对称。
          var side=dockHandle.side==='left'?'left':'right';
          var handleW=Math.max(1,m.toggleW);
          var hx=side==='right'?(m.vw-handleW/2):(handleW/2);
          var hy=clampNumber(dockHandle.fy*m.vh,m.toggleH/2+DOCK_EDGE,Math.max(m.toggleH/2+DOCK_EDGE,m.vh-m.toggleH/2-DOCK_EDGE));
          fullscreenDockToggle.classList.add('dock-placed');
          fullscreenDockToggle.classList.toggle('dock-edge-left',side==='left');
          fullscreenDockToggle.classList.toggle('dock-edge-right',side==='right');
          fullscreenDockToggle.style.left=Math.round(hx)+'px';
          fullscreenDockToggle.style.top=Math.round(hy)+'px';
        }
        // 控制栏换位置后「画面要不要让位」可能变了（拖到上半屏就不让位）。
        scheduleFullscreenDockLift();
      }
      function resetDockAnchor(announce){
        dockAnchor={fx:0.5,fy:1};
        saveDockAnchor();
        applyDockAnchor();
        if(announce) showToast('菜单位置已复位');
      }
      function resetDockHandle(announce){
        dockHandle={side:'right',fy:0.5};
        saveDockHandle();
        applyDockAnchor();
        if(announce) showToast('把手已回到屏幕右侧中间');
      }
      function startDockDrag(event,target){
        if(!fullscreenActive||dockDrag) return;
        if(typeof event.button==='number'&&event.button!==0) return;
        var m=dockMetrics();
        dockDrag={
          id:event.pointerId,
          startX:event.clientX,
          startY:event.clientY,
          originX:dockAnchor.fx*m.vw,
          originY:dockAnchor.fy*m.vh,
          handle:target===fullscreenDockToggle,
          moved:false,
          target:target||null
        };
        app.classList.add('dock-dragging');
        noteDockActivity();
        if(target&&target.setPointerCapture&&event.pointerId!==undefined){
          try{ target.setPointerCapture(event.pointerId); }catch(e){}
        }
        // preventDefault 会阻止默认聚焦，而「焦点不在控制栏里」正是自动收起的前提；
        // 拖把手时手动把焦点放到把手上（收起逻辑把把手排除在外）。
        if(target===fullscreenDockToggle&&typeof target.focus==='function'){
          try{ target.focus({preventScroll:true}); }catch(e){ target.focus(); }
        }
        event.preventDefault();
      }
      function moveDockDrag(event){
        if(!dockDrag) return;
        if(dockDrag.id!==undefined&&event.pointerId!==undefined&&event.pointerId!==dockDrag.id) return;
        var m=dockMetrics();
        if(!m.vw||!m.vh) return;
        var dx=event.clientX-dockDrag.startX;
        var dy=event.clientY-dockDrag.startY;
        if(Math.abs(dx)+Math.abs(dy)>4) dockDrag.moved=true;
        if(dockDrag.handle){
          // 把手：竖直方向沿边移动（用指针绝对位置，避免原点比例与像素混算），
          // 水平方向过屏幕中线就换到另一侧。
          dockHandle.fy=clampNumber(event.clientY/m.vh,0,1);
          if(event.clientX<m.vw*0.5) dockHandle.side='left';
          else dockHandle.side='right';
        }else{
          dockAnchor.fx=clampNumber((dockDrag.originX+dx)/m.vw,0,1);
          dockAnchor.fy=clampNumber((dockDrag.originY+dy)/m.vh,0,1);
        }
        applyDockAnchor();
        noteDockActivity();
      }
      function endDockDrag(){
        if(!dockDrag) return;
        var moved=dockDrag.moved;
        var target=dockDrag.target;
        var wasHandle=dockDrag.handle;
        dockDrag=null;
        app.classList.remove('dock-dragging');
        if(target&&target.releasePointerCapture&&target.hasPointerCapture){
          try{ target.releasePointerCapture(); }catch(e){}
        }
        dockDragEndedAt=moved?Date.now():0;
        if(moved){ if(wasHandle) saveDockHandle(); else saveDockAnchor(); }
        noteDockActivity();
      }
      function nudgeDockAnchor(dx,dy){
        var m=dockMetrics();
        if(!m.vw||!m.vh) return;
        dockAnchor.fx=clampNumber(dockAnchor.fx+dx/m.vw,0,1);
        dockAnchor.fy=clampNumber(dockAnchor.fy+dy/m.vh,0,1);
        applyDockAnchor();
        saveDockAnchor();
        noteDockActivity();
      }
      function nudgeDockHandle(dx,dy){
        var m=dockMetrics();
        if(!m.vw||!m.vh) return;
        if(dx<0) dockHandle.side='left';
        else if(dx>0) dockHandle.side='right';
        if(dy) dockHandle.fy=clampNumber(dockHandle.fy+dy/m.vh,0,1);
        applyDockAnchor();
        saveDockHandle();
        noteDockActivity();
      }
      function bindDockDragTargets(){
        var grip=dockGrip();
        [fullscreenDockToggle,grip].forEach(function(el){
          if(!el) return;
          el.addEventListener('pointerdown',function(event){ startDockDrag(event,el); });
          // 把手双击 = 回到右侧居中；控制栏握把双击 = 控制栏复位到底部居中。
          el.addEventListener('dblclick',function(event){
            event.preventDefault();
            if(el===fullscreenDockToggle) resetDockHandle(true); else resetDockAnchor(true);
          });
        });
        if(ctrlDock){
          // 控制栏本体也能拖：只认空白处/分隔线，按钮与弹出菜单照旧各自响应。
          ctrlDock.addEventListener('pointerdown',function(event){
            if(!fullscreenActive) return;
            var target=event.target;
            if(target&&target.closest&&target.closest('button,.cb-pop,.dock-grip')) return;
            startDockDrag(event,grip||ctrlDock);
          });
        }
        window.addEventListener('pointermove',moveDockDrag,{passive:false});
        window.addEventListener('pointerup',endDockDrag);
        window.addEventListener('pointercancel',endDockDrag);
        if(fullscreenDockToggle){
          fullscreenDockToggle.addEventListener('keydown',function(event){
            if(!fullscreenActive) return;
            var step=event.shiftKey?8:32;
            // 把手只在边上：左右 = 换边，上下 = 沿边移动。
            if(event.key==='ArrowLeft'){ nudgeDockHandle(-1,0); }
            else if(event.key==='ArrowRight'){ nudgeDockHandle(1,0); }
            else if(event.key==='ArrowUp'){ nudgeDockHandle(0,-step); }
            else if(event.key==='ArrowDown'){ nudgeDockHandle(0,step); }
            else if(event.key==='Home'||event.key==='0'){ resetDockHandle(true); }
            else return;
            event.preventDefault();
          });
        }
      }
      loadDockAnchor();
      bindDockDragTargets();
      /* 当前的全屏元素：带 webkit 前缀的旧内核也要认（认不出来就会把「真全屏」当成
         「没进全屏」，屏幕方向锁也拿不到许可）。 */
      function nativeFullscreenElement(){
        return document.fullscreenElement||document.webkitFullscreenElement||null;
      }
      /* 真·全屏要多试几种写法：{navigationUI:'hide'} 是 Chrome 专有选项，个别内核会
         连整个请求一起拒掉；拒了就退回不带参数的调用，再退到 webkit 前缀。全都失败
         时保留纯 CSS 全屏（.app.is-fullscreen），但那样浏览器地址栏还在、屏幕方向锁
         也被浏览器拒绝——用户看到的「全屏后画面变小」正是这种情况。 */
      function requestNativeFullscreen(){
        var requests=[];
        if(typeof app.requestFullscreen==='function'){
          requests.push(function(){ return app.requestFullscreen({navigationUI:'hide'}); });
          requests.push(function(){ return app.requestFullscreen(); });
        }
        if(typeof app.webkitRequestFullscreen==='function'){
          requests.push(function(){ return app.webkitRequestFullscreen(); });
        }
        if(!requests.length) return;
        var index=0;
        function attempt(){
          if(index>=requests.length) return;
          var call=requests[index];
          index+=1;
          var result=null;
          try{ result=call(); }catch(e){ attempt(); return; }
          if(result&&typeof result.then==='function'){
            result.then(function(){
              if(nativeFullscreenElement()===app) applyFullscreenState(true,true);
            },function(){ attempt(); });
            return;
          }
          if(nativeFullscreenElement()===app) applyFullscreenState(true,true);
        }
        attempt();
      }
      function enterFullscreen(){
        if(fullscreenActive||!app) return;
        closeMoreMenu(true);
        applyFullscreenState(true,false);
        requestNativeFullscreen();
      }
      function exitFullscreen(){
        var nativeMode=nativeFullscreenElement()===app;
        if(nativeMode){
          try{
            var exit=typeof document.exitFullscreen==='function'
              ? document.exitFullscreen()
              : (typeof document.webkitExitFullscreen==='function'?document.webkitExitFullscreen():null);
            if(exit&&typeof exit.catch==='function') exit.catch(function(){});
          }catch(e){}
        }
        applyFullscreenState(false,false);
      }
      document.addEventListener('fullscreenchange',function(){
        var nativeMode=nativeFullscreenElement()===app;
        applyFullscreenState(nativeMode,nativeMode);
      });
      document.addEventListener('webkitfullscreenchange',function(){
        var nativeMode=nativeFullscreenElement()===app;
        applyFullscreenState(nativeMode,nativeMode);
      });
      document.addEventListener('scrcpygate:videosize',function(event){ syncVideoOrientation(event&&event.detail); });
      window.addEventListener('orientationchange',function(){ syncVideoOrientation(); syncKeyboardViewport(); applyDockAnchor(); });
      window.addEventListener('resize',function(){ syncVideoOrientation(); applyDockAnchor(); });

      function moreMenuItems(){
        return Array.prototype.slice.call(cbPop ? cbPop.querySelectorAll('[role^="menuitem"]') : []).filter(function(item){ return !item.disabled&&item.getBoundingClientRect().width>0; });
      }
      function openMoreMenu(){
        if(!cbPop || moreBtn.disabled) return;
        moreMenuReturnFocus=document.activeElement===moreBtn?moreBtn:null;
        closeFixedPops(cbPop);
        cbPop.classList.add('open');
        moreBtn.setAttribute('aria-expanded','true');
        var r=moreBtn.getBoundingClientRect();
        var w=cbPop.offsetWidth||172;
        var h=cbPop.offsetHeight||104;
        var viewportBottom=window.innerHeight;
        if(window.visualViewport){ viewportBottom=Math.min(viewportBottom,(Number(window.visualViewport.offsetTop)||0)+(Number(window.visualViewport.height)||viewportBottom)); }
        cbPop.style.left=Math.max(8,Math.min(r.right-w,window.innerWidth-w-8))+'px';
        var top=r.bottom+6;
        if(top+h>viewportBottom-8){ top=Math.max(8,r.top-h-6); }
        cbPop.style.top=top+'px';
        var first=moreMenuItems()[0];
        // preventScroll 必须带上：默认的 focus() 会把刚打开的面板滚进视野，
        // 触发 capture 阶段的 scroll 监听 → closeFixedPops() 立刻把它关掉，
        // 表现就是「第一次点更多没反应、第二次才出来」（手机上尤其明显）。
        if(first){ try{ first.focus({preventScroll:true}); }catch(e){ first.focus(); } }
      }
      moreBtn.addEventListener('click',function(e){
        e.stopPropagation();
        var was=cbPop.classList.contains('open');
        if(mirrorControlTools) mirrorControlTools.action('menu_toggle',{open:!was});
        if(was) closeMoreMenu(true);
        else openMoreMenu();
      });
      if(cbPop) cbPop.addEventListener('keydown',function(e){
        var items=moreMenuItems();
        if(e.key==='Escape'){
          e.preventDefault();
          e.stopPropagation();
          closeMoreMenu(true);
          return;
        }
        if(!items.length) return;
        var index=items.indexOf(document.activeElement);
        var next=-1;
        if(e.key==='ArrowDown'||e.key==='ArrowRight') next=(index+1+items.length)%items.length;
        else if(e.key==='ArrowUp'||e.key==='ArrowLeft') next=(index-1+items.length)%items.length;
        else if(e.key==='Home') next=0;
        else if(e.key==='End') next=items.length-1;
        if(next>=0){ e.preventDefault(); try{ items[next].focus({preventScroll:true}); }catch(err){ items[next].focus(); } }
      });
      if(fullscreenBtn) fullscreenBtn.addEventListener('click',function(){
        if(moreActionBusy) return;
        if(mirrorControlTools) mirrorControlTools.action('fullscreen_toggle',{active:!fullscreenActive});
        moreActionBusy=true;
        if(fullscreenActive) exitFullscreen(); else enterFullscreen();
        moreActionBusy=false;
        renderControl();
      });
      // 旋转：每次点击在自动摆正的基础上顺时针 +90°（点满四次回到自动角度）。
      if(rotateBtn) rotateBtn.addEventListener('click',function(){
        if(rotateBtn.disabled) return;
        rotateViewClockwise();
      });
      if(fullscreenDockToggle) fullscreenDockToggle.addEventListener('click',function(event){        // 刚拖过 / 双击复位：都不要当成「展开-收起」。
        if(Date.now()-dockDragEndedAt<250) return;
        if(event&&event.detail>1) return;
        setFullscreenDockHidden(!dockHidden);
      });

      function toggleKeyboard(source){
        if(moreActionBusy) return;
        // The menu entry is the recovery path: focus the proxy again even if
        // the previous attempt reported an active state without showing IME.
        var next=source==='menu'?true:!keyboardOn;
        moreActionBusy=true;
        closeMoreMenu(true);
        renderControl();
        var successText=source==='menu'
          ? (keyboardOn?'备用键盘已重新打开':'备用键盘已开启')
          : (next?'键盘已开启':'键盘已关闭');
        sessionAction('keyboard',successText,{enabled:next}).then(function(){
          if(mirrorControlTools) mirrorControlTools.setKeyboard(next); else keyboardOn=next;
        }).then(function(){ moreActionBusy=false; renderControl(); },function(error){
          moreActionBusy=false;
          renderControl();
          if(source==='direct') showToast('键盘未响应，请在更多菜单中重试');
          return null;
        });
      }
      if(keyboardBtn) keyboardBtn.addEventListener('click',function(){ toggleKeyboard('direct'); });
      if(popKeyboard) popKeyboard.addEventListener('click',function(){ toggleKeyboard('menu'); });
      if(popAutoControl) popAutoControl.addEventListener('click',function(){
        fullscreenAutoControl=!fullscreenAutoControl;
        try{ localStorage.setItem(FULLSCREEN_AUTO_CONTROL_KEY,fullscreenAutoControl?'1':'0'); }catch(e){}
        renderAutoControlToggle();
        showToast(fullscreenAutoControl?'全屏后默认获取控制已开启':'全屏后默认获取控制已关闭');
        if(fullscreenAutoControl) autoAcquireControlOnFullscreen();
      });
      /* 手动旋转按钮已按用户要求撤掉（ISSUE-156）：画面方向只由「设备方向 + 可用空间」
         自动决定，见 syncVideoOrientation()/applyAutoFitRotation()。 */

      /* ---------- 截图：复制到剪贴板，不支持时回退下载 ---------- */
      function screenshotCanvas(){
        var v2=window.ScrcpyGateV2;
        var source=v2&&v2.state&&v2.state.videoEl?v2.state.videoEl:null;
        var width=0,height=0;
        if(source&&Number(source.videoWidth)>0&&Number(source.videoHeight)>0){
          width=Number(source.videoWidth);
          height=Number(source.videoHeight);
        }else{
          var fallback=document.getElementById('raw-v2-surface');
          if(fallback&&Number(fallback.width)>0&&Number(fallback.height)>0){
            source=fallback;
            width=Number(fallback.width);
            height=Number(fallback.height);
          }
        }
        if(!source||!(width>0&&height>0)) return null;
        // 截图跟随当前显示方向：90/270 转置，180 只翻转。
        var rotation=viewRotationDegrees();
        var transposed=rotation===90||rotation===270;
        var canvas=document.createElement('canvas');
        canvas.width=transposed?height:width;
        canvas.height=transposed?width:height;
        var ctx=canvas.getContext('2d');
        if(!ctx) return null;
        try{
          if(rotation===0){
            ctx.drawImage(source,0,0,width,height);
          }else{
            ctx.translate(canvas.width/2,canvas.height/2);
            ctx.rotate(rotation*Math.PI/180);
            ctx.drawImage(source,-width/2,-height/2,width,height);
          }
        }catch(e){ return null; }
        return canvas;
      }
      function dataUrlToBlob(dataUrl){
        var parts=String(dataUrl||'').split(',');
        if(parts.length<2) return null;
        var mime=(parts[0].match(/:(.*?);/)||[])[1]||'image/png';
        try{
          var binary=atob(parts[1]);
          var bytes=new Uint8Array(binary.length);
          for(var i=0;i<binary.length;i+=1) bytes[i]=binary.charCodeAt(i);
          return new Blob([bytes],{type:mime});
        }catch(e){ return null; }
      }
      function screenshotFileName(){
        var name=selectedDevice&&selectedDevice.name?String(selectedDevice.name):'shot';
        name=name.replace(/[^\w\u4e00-\u9fa5-]+/g,'_').slice(0,40)||'shot';
        return 'scrcpygate-'+name+'-'+Date.now()+'.png';
      }
      function downloadScreenshot(blob,fileName){
        var url=URL.createObjectURL(blob);
        var link=document.createElement('a');
        link.href=url;
        link.download=fileName;
        link.rel='noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function(){ try{ URL.revokeObjectURL(url); }catch(e){} },4000);
      }
      function takeScreenshot(){
        var canvas=screenshotCanvas();
        if(!canvas){ showToast('当前没有可截取的画面'); return; }
        var blob=null;
        try{ blob=dataUrlToBlob(canvas.toDataURL('image/png')); }catch(e){ blob=null; }
        if(!blob){ showToast('截图失败，请重试'); return; }
        var fileName=screenshotFileName();
        var clipboard=navigator.clipboard;
        if(clipboard&&typeof clipboard.write==='function'&&typeof window.ClipboardItem==='function'){
          try{
            clipboard.write([new ClipboardItem({'image/png':blob})]).then(function(){
              showToast('截图已复制到剪贴板');
            },function(){
              downloadScreenshot(blob,fileName);
              showToast('浏览器未授权复制图片，已改为下载');
            });
            return;
          }catch(e){}
        }
        downloadScreenshot(blob,fileName);
        showToast('浏览器不支持复制图片，已改为下载');
      }
      if(popShot) popShot.addEventListener('click',function(){
        if(moreActionBusy) return;
        moreActionBusy=true;
        closeMoreMenu(true);
        try{ takeScreenshot(); }catch(e){ showToast('截图失败，请重试'); }
        moreActionBusy=false;
        renderControl();
      });

      /* ---------- 关闭弹层 ---------- */
      document.addEventListener('click',function(e){
        if(e.target.closest('#cb-more')||e.target.closest('#cb-pop')||e.target.closest('#notify-pop')||e.target.closest('#notify-btn')) return;
        closeFixedPops();
      });
      window.addEventListener('scroll',function(e){
        // 「更多」面板打开时浏览器会把聚焦项滚进视野，这会让控制栏本身滚一下；
        // 那不是用户滚动页面，不能因此把面板关掉（否则第一次点更多看着像没反应）。
        var target=e&&e.target;
        if(target&&target.closest&&(target.closest('#ctrl-dock')||target.closest('#cb-pop'))) return;
        closeFixedPops();
      },true);
      window.addEventListener('resize',function(){ closeFixedPops(); });

      /* ---------- 画面菜单（显示与画质 · Apple 风格,与后台"画质与传输"对齐） ---------- */
      var vpPanel=document.getElementById('vp-panel');
      var vpBackdrop=document.getElementById('vp-backdrop');
      var vpPresetsBox=document.getElementById('vp-presets');
      var vpFitSeg=document.getElementById('vp-fit');
      var vpModeSeg=document.getElementById('vp-mode');
      var vpStatus=document.getElementById('vp-status');
      var vpStatusDot=document.getElementById('vp-status-dot');
      var vpStatusText=document.getElementById('vp-status-text');
      var vpRes=document.getElementById('vp-res');
      var vpResW=document.getElementById('vp-resw');
      var vpResH=document.getElementById('vp-resh');
      var vpResCustom=document.getElementById('vp-res-custom');
      var vpFps=document.getElementById('vp-fps');
      var vpBitrate=document.getElementById('vp-bitrate');
      var vpFullscreen=document.getElementById('vp-fullscreen');
      var vpLoaded=false;
      var vpBusy=false;
      var vpPresets=[];
      var vpSelectedPresetId='';
      var vpParams={ resMode:'1280x720', customW:1280, customH:720, fps:24, bitrate:2.4 };
      var vpMode='rawV2';
      var vpModeOptions=['rawV2'];
      var vpFullscreenValue='';
      var vpAllowTuning=true;
      var vpFit='contain';
      var vpApplyTimer=null;
      function vpPresetById(id){ for(var i=0;i<vpPresets.length;i++){ if(vpPresets[i].id===id) return vpPresets[i]; } return null; }
      /* 输出尺寸档位与后台同源（resolution_cap_options），只列出不超过分辨率上限的档位。 */
      function vpRenderResolutionOptions(payload){
        if(!vpRes) return;
        var cap=Number(payload&&payload.max_size_limit)||1920;
        var options=Array.isArray(payload&&payload.resolution_cap_options)?payload.resolution_cap_options:[];
        var html='';
        options.forEach(function(o){
          var edge=Number(o.value)||0;
          if(!(edge>0)||edge>cap) return;
          var parts=String(o.description||'').split(' x ');
          if(parts.length!==2) return;
          var key=Number(parts[0])+'x'+Number(parts[1]);
          html+='<option value="'+escHtml(key)+'">'+escHtml(parts[0]+' × '+parts[1])+'</option>';
        });
        // 后端没给档位表时保留原来的静态两项，避免下拉为空。
        if(!html) html='<option value="854x480">854 × 480</option><option value="1280x720">1280 × 720</option><option value="1920x1080">1920 × 1080</option>';
        if(vpParams.resMode&&vpParams.resMode!=='custom'){
          var key=String(vpParams.resMode);
          if(html.indexOf('value="'+key+'"')<0){
            var parts=key.split('x');
            html='<option value="'+escHtml(key)+'">'+escHtml(parts[0]+' × '+parts[1])+'</option>'+html;
          }
        }
        vpRes.innerHTML=html+'<option value="custom">自定义</option>';
      }
      function vpParamsSpec(){
        var res=vpParams.resMode==='custom' ? (vpParams.customW+'×'+vpParams.customH) : String(vpParams.resMode).replace('x','×');
        return res+' · '+vpParams.fps+'fps · '+vpParams.bitrate+'Mbps';
      }
      function vpSyncSeg(seg){
        if(!seg) return;
        var thumb=seg.querySelector('.vp-seg-thumb');
        var active=seg.querySelector('button[aria-pressed="true"]');
        if(!thumb||!active) return;
        thumb.style.width=active.offsetWidth+'px';
        thumb.style.transform='translateX('+(active.offsetLeft-2)+'px)';
      }
      function vpSyncSegs(){ vpSyncSeg(vpFitSeg); vpSyncSeg(vpModeSeg); }
      var vpActiveSegment='presets';
      function vpShowSegment(name, moveFocus){
        if(name!=='tuning') name='presets';
        vpActiveSegment=name;
        var tabs=document.querySelectorAll('.vp-tab');
        tabs.forEach(function(tab){
          var on=tab.getAttribute('data-vp-seg')===name;
          tab.classList.toggle('active',on);
          tab.setAttribute('aria-selected',on?'true':'false');
          tab.tabIndex=on?0:-1;
          if(on&&moveFocus&&typeof tab.focus==='function') tab.focus();
        });
        document.querySelectorAll('[data-vp-panel]').forEach(function(panel){
          panel.hidden=panel.getAttribute('data-vp-panel')!==name;
        });
        if(window.lucide) lucide.createIcons();
      }
      document.querySelectorAll('.vp-tab').forEach(function(tab, index, all){
        tab.addEventListener('click',function(){ vpShowSegment(tab.getAttribute('data-vp-seg'),false); });
        tab.addEventListener('keydown',function(e){
          if(['ArrowRight','ArrowLeft','Home','End'].indexOf(e.key)<0) return;
          e.preventDefault();
          var next=index;
          if(e.key==='ArrowRight') next=(index+1)%all.length;
          else if(e.key==='ArrowLeft') next=(index-1+all.length)%all.length;
          else if(e.key==='Home') next=0;
          else if(e.key==='End') next=all.length-1;
          vpShowSegment(all[next].getAttribute('data-vp-seg'),true);
        });
      });
      function vpSetStatus(text,state){
        vpStatus.classList.remove('busy','error');
        if(state) vpStatus.classList.add(state);
        vpStatusDot.innerHTML=state==='busy' ? '<span class="vp-status-spinner" aria-hidden="true"></span>' : '<i data-lucide="check"></i>';
        vpStatusText.textContent=text;
        if(window.lucide) lucide.createIcons();
      }
      function vpRenderPresets(){
        if(!vpPresetsBox) return;
        // 预设列表只镜像后台配置的预设；「手动微调」不是预设，放在「微调」页签里。
        var html=vpPresets.map(function(p){
          var on=p.id===vpSelectedPresetId;
          return '<button class="vp-profile-card'+(on?' active':'')+'" type="button" role="radio" aria-checked="'+(on?'true':'false')+'" data-preset-id="'+escHtml(p.id)+'">'
            +'<span class="vp-p-main"><span class="vp-p-name">'+escHtml(p.name)+'</span><span class="vp-p-desc">'+(p.builtin?'内置预设':'自定义预设')+'</span></span>'
            +'<span class="vp-p-check" aria-hidden="true"><i data-lucide="check"></i></span></button>';
        }).join('');
        vpPresetsBox.innerHTML=html||'<p class="vp-note">'+escHtml(tr('管理员尚未启用任何画质预设'))+'</p>';
        vpPresetsBox.querySelectorAll('[data-preset-id]').forEach(function(card){
          card.addEventListener('click',function(){
            var id=card.getAttribute('data-preset-id');
            vpSelectedPresetId=id;
            var preset=vpPresetById(id);
            if(preset){
              vpParams={ resMode:preset.width+'x'+preset.height, customW:preset.width, customH:preset.height, fps:preset.fps, bitrate:preset.bitrate };
              vpSyncParamsFields();
            }
            vpRenderPresets();
            vpApply();
          });
        });
        var hint=document.getElementById('vp-custom-hint');
        if(hint) hint.hidden=!!vpSelectedPresetId;
        if(window.lucide) lucide.createIcons();
      }
      function vpSyncParamsFields(){
        var custom=vpParams.resMode==='custom';
        vpRes.value=custom?'custom':vpParams.resMode;
        vpResW.value=String(vpParams.customW);
        vpResH.value=String(vpParams.customH);
        vpResCustom.hidden=!custom;
        vpFps.value=String(vpParams.fps);
        vpBitrate.value=String(vpParams.bitrate);
      }
      function vpReadParams(){
        var custom=vpRes.value==='custom';
        return { resMode:custom?'custom':vpRes.value, customW:Number(vpResW.value)||1280, customH:Number(vpResH.value)||720, fps:Number(vpFps.value)||24, bitrate:Number(vpBitrate.value)||2.4 };
      }
      function vpRenderModes(){
        // 传输模式已从工作台移除(协议属于后台「传输链路」);保留状态以便保存时不丢失该字段。
        if(!vpModeSeg) return;
        var modes=vpModeOptions.length?vpModeOptions:['rawV2'];
        if(modes.indexOf(vpMode)<0) vpMode=modes[0];
        vpModeSeg.querySelectorAll('button').forEach(function(b){ b.remove(); });
        var thumb=vpModeSeg.querySelector('.vp-seg-thumb');
        modes.forEach(function(mode){
          var btn=document.createElement('button');
          btn.type='button';
          btn.setAttribute('data-mode',mode);
          btn.setAttribute('aria-pressed',String(mode===vpMode));
          btn.textContent=mode==='rawV2'?'Raw v2':(mode==='legacy'?'legacy · 诊断':mode);
          btn.addEventListener('click',function(){
            if(vpMode===mode) return;
            vpMode=mode;
            vpModeSeg.querySelectorAll('button').forEach(function(b){ b.setAttribute('aria-pressed',String(b===btn)); });
            vpSyncSeg(vpModeSeg);
            vpApply();
          });
          vpModeSeg.insertBefore(btn,thumb);
        });
        vpSyncSeg(vpModeSeg);
      }
      function vpRenderFullscreen(){
        var html='<option value="">跟随当前预设</option>';
        vpPresets.filter(function(p){ return p.fullscreenAllowed; }).forEach(function(p){
          html+='<option value="'+escHtml(p.id)+'"'+(vpFullscreenValue===p.id?' selected':'')+'>'+escHtml(p.name)+'</option>';
        });
        vpFullscreen.innerHTML=html;
      }
      function vpPayload(){
        return { deviceId:selectedDevice&&selectedDevice.id||'', presetId:vpSelectedPresetId||null, resMode:vpParams.resMode, customW:vpParams.customW, customH:vpParams.customH, fps:vpParams.fps, bitrate:vpParams.bitrate, defaultMode:vpMode, fullscreenQuality:vpFullscreenValue };
      }
      function vpUpdatePill(){
        var preset=vpPresetById(vpSelectedPresetId);
        var name=preset?preset.name:tr('手动微调');
        var el=document.getElementById('mirror-quality-name');
        if(el) el.textContent=name;
        qualityMetaBase=vpParamsSpec();
        renderQualityMeta();
        var summary=document.getElementById('vp-summary-text');
        if(summary) summary.textContent=name+' · '+vpParamsSpec();
      }
      function vpAdoptQualityResult(result){
        if(!result) return;
        var applied=result.applyResult||result.applied||result;
        var effective=applied&&applied.effective||applied&&applied.preferences;
        var config=result.config||result.settings||result.params;
        if(config){
          vpParams={
            resMode:String(config.resMode||vpParams.resMode),
            customW:Number(config.customW||vpParams.customW),
            customH:Number(config.customH||vpParams.customH),
            fps:Number(config.fps||vpParams.fps),
            bitrate:Number(config.bitrate||vpParams.bitrate)
          };
          if(config.defaultMode) vpMode=String(config.defaultMode);
        }
        var selected=String(result.selectedPresetId||result.presetId||'');
        if(selected && vpPresetById(selected)) vpSelectedPresetId=selected;
        else if(effective && effective.profile && vpPresetById(String(effective.profile))) vpSelectedPresetId=String(effective.profile);
        else if(effective && effective.profile==='custom') vpSelectedPresetId='';
        vpSyncParamsFields();
        vpRenderPresets();
        vpUpdatePill();
      }
      function vpApply(){
        if(vpBusy) return;
        vpBusy=true;
        vpSetStatus('正在应用…','busy');
        var payload=vpPayload();
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('quality.update'))
          ? window.ScrcpyGateApi.configured('quality.update',{method:'PUT',body:payload})
          : Promise.reject(new Error('画质服务未连接'));
        p.then(function(result){ vpBusy=false; vpAdoptQualityResult(result); vpSetStatus(result&&result.deferred?'已保存 · 当前有多个观看端，视频重启已延后':(result&&result.restarted?'已应用 · 视频流已重启':'已应用')); })
         .catch(function(error){ vpBusy=false; vpSetStatus('应用失败 · '+apiErrorText(error),'error'); });
      }
      function vpLoad(){
        vpLoaded=true;
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('quality.config'))
          ? window.ScrcpyGateApi.configured('quality.config',{})
          : Promise.resolve(null);
        p.then(function(payload){
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||null;
          if(d){
            var rawPresets=(d.presets&&d.presets.items)?d.presets.items:(d.presets||[]);
            vpPresets=(Array.isArray(rawPresets)?rawPresets:[]).map(function(p){
              return { id:String(p.id||p.presetId||''), name:String(p.name||p.displayName||'未命名预设'), width:Number(p.width||0), height:Number(p.height||0), fps:Number(p.fps||p.frameRate||24), bitrate:Number(p.bitrate||p.bitrateMbps||2.4), builtin:!!(p.builtin||p.system), enabled:p.enabled!==false, projectionAllowed:p.projectionAllowed!==false && p.projection_allowed!==false, fullscreenAllowed:p.fullscreenAllowed!==false && p.fullscreen_allowed!==false, fullscreenOnly:p.fullscreenOnly===true || p.fullscreen_only===true };
            }).filter(function(p){ return p.id; });
            var _presetSession=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null;
            var _presetAdmin=!!_presetSession&&(_presetSession.roleKey==='admin'||_presetSession.role==='管理员'||_presetSession.isAdmin===true);
            var _enabledPresets=Array.isArray(d.enabled_presets)?d.enabled_presets.map(function(id){ return String(id); }):[];
            var _selectedForFilter=String(d.selectedPresetId||d.presetId||'');
            // 后台预设卡片上的「工作台可选」开关对所有人生效（含管理员的工作台）：
            // 关掉的预设不在这里出现，但保留当前正在使用的那个，避免选中的预设消失。
            vpPresets=vpPresets.filter(function(p){
              if(p.projectionAllowed===false) return false;
              if(!_enabledPresets.length) return true;
              return _enabledPresets.indexOf(p.id)>=0||p.id===_selectedForFilter;
            });
            var params=d.config||d.settings||d.params||d.current||{};
            vpParams={ resMode:String(params.resMode||'1280x720'), customW:Number(params.customW||1280), customH:Number(params.customH||720), fps:Number(params.fps||24), bitrate:Number(params.bitrate||2.4) };
            vpRenderResolutionOptions(d);
            var cap=Number(d.max_size_limit)||1920;
            if(Number(vpParams.customW)>cap) vpParams.customW=cap;
            if(Number(vpParams.customH)>cap) vpParams.customH=Math.max(240,Math.round(cap*9/16));
            if(vpResW) vpResW.max=String(cap);
            if(vpResH) vpResH.max=String(cap);
            vpSelectedPresetId=String(d.selectedPresetId||d.presetId||'');
            var modes=[];
            if(d.rawV2===true||params.rawV2===true) modes.push('rawV2');
            if(d.protoAvail===true||params.protoAvail===true) modes.push('protocol');
            if(d.legacyAvail===true||params.legacyAvail===true) modes.push('legacy');
            if(!modes.length) modes.push('rawV2');
            vpModeOptions=modes;
            vpMode=String(d.defaultMode||params.defaultMode||'rawV2');
            vpFullscreenValue=String(d.fullscreenQuality||params.fullscreenQuality||'');
            // 画质控制: 普通用户微调开关与全屏档可用性(由后台"用户画质控制"决定)
            try{
              var _su=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null;
              var _isAdmin=!!_su&&(_su.roleKey==='admin'||_su.role==='管理员'||_su.isAdmin===true);
              var _allowTuning=d.allowCustomTuning!==false&&params.allowCustomTuning!==false;
              var _showTuning=_allowTuning||_isAdmin;
              vpAllowTuning=_showTuning;
              var _tuneTab=document.getElementById('vp-tab-tuning');
              if(_tuneTab) _tuneTab.hidden=!_showTuning;
              if(!_showTuning&&vpActiveSegment==='tuning') vpShowSegment('presets',false);
              // 当前配置是手动微调时，直接打开「微调」页签，避免预设列表看起来「没选中」。
              else if(_showTuning&&!vpSelectedPresetId&&vpActiveSegment==='presets') vpShowSegment('tuning',false);
              var _fsTitle=document.getElementById('vp-fullscreen-title');
              var _fsField=document.getElementById('vp-fullscreen-field');
              var _showFs=!!vpFullscreenValue;
              if(_fsTitle) _fsTitle.style.display=_showFs?'':'none';
              if(_fsField) _fsField.style.display=_showFs?'':'none';
            }catch(e){}
          }
          vpRenderPresets();
          vpSyncParamsFields();
          vpRenderModes();
          vpRenderFullscreen();
          vpSyncSegs();
          vpUpdatePill();
          vpSetStatus(d?'画质设置已同步':'画质服务未连接,使用内置默认值');
        }).catch(function(){
          vpRenderPresets();
          vpSyncParamsFields();
          vpRenderModes();
          vpRenderFullscreen();
          vpSyncSegs();
          vpSetStatus('画质数据不可用,当前为内置默认值','error');
        });
      }
      /* aria-hidden 不能被仍持有焦点的元素隐藏：先移出焦点再隐藏面板，否则
         浏览器会拒绝 aria-hidden 并输出 “Blocked aria-hidden … retained focus”。 */
      function releasePanelFocus(panel){
        if(!panel||!panel.contains) return;
        var active=document.activeElement;
        if(active&&active!==document.body&&panel.contains(active)&&typeof active.blur==='function') active.blur();
      }
      function vpOpen(){
        if(!vpLoaded) vpLoad();
        apClose();
        upClose();
        tlClose();
        vpPanel.classList.add('open');
        vpBackdrop.classList.add('open');
        vpPanel.setAttribute('aria-hidden','false');
        vpPanel.removeAttribute('inert');
        vpBackdrop.setAttribute('aria-hidden','false');
        document.body.classList.remove('sg-mobile-nav');
        var scrim=document.getElementById('mobile-scrim');
        if(scrim){ scrim.classList.remove('open'); scrim.setAttribute('aria-hidden','true'); }
        vpSyncSegs();
        var closeBtn=document.getElementById('vp-close');
        if(closeBtn) closeBtn.focus();
      }
      function vpClose(){
        vpPanel.classList.remove('open');
        vpBackdrop.classList.remove('open');
        releasePanelFocus(vpPanel);
        vpPanel.setAttribute('aria-hidden','true');
        vpPanel.setAttribute('inert','');
        vpBackdrop.setAttribute('aria-hidden','true');
      }
      document.getElementById('visual-item').addEventListener('click',function(e){
        e.preventDefault();
        vpOpen();
      });
      document.getElementById('vp-close').addEventListener('click',vpClose);
      vpBackdrop.addEventListener('click',vpClose);
      vpFitSeg.querySelectorAll('button').forEach(function(btn){
        btn.addEventListener('click',function(){
          vpFit=btn.getAttribute('data-fit');
          vpFitSeg.querySelectorAll('button').forEach(function(b){ b.setAttribute('aria-pressed',String(b===btn)); });
          vpSyncSeg(vpFitSeg);
          var box=document.getElementById('video-box');
          if(box) box.classList.toggle('fit-original',vpFit==='original');
          try{ localStorage.setItem('scrcpygate-fit',vpFit); }catch(e){}
        });
      });
      try{
        var savedFit=localStorage.getItem('scrcpygate-fit');
        if(savedFit==='original'){
          vpFit='original';
          var oBtn=vpFitSeg.querySelector('[data-fit="original"]');
          var cBtn=vpFitSeg.querySelector('[data-fit="contain"]');
          if(oBtn) oBtn.setAttribute('aria-pressed','true');
          if(cBtn) cBtn.setAttribute('aria-pressed','false');
          var fitBox=document.getElementById('video-box');
          if(fitBox) fitBox.classList.add('fit-original');
        }
      }catch(e){}
      function vpOnManualEdit(){
        vpParams=vpReadParams();
        vpSelectedPresetId='';
        vpRenderPresets();
        vpUpdatePill();
        clearTimeout(vpApplyTimer);
        vpApplyTimer=setTimeout(vpApply,450);
      }
      vpRes.addEventListener('change',function(){
        vpResCustom.hidden=vpRes.value!=='custom';
        vpOnManualEdit();
      });
      vpResW.addEventListener('input',vpOnManualEdit);
      vpResH.addEventListener('input',vpOnManualEdit);
      vpFps.addEventListener('input',vpOnManualEdit);
      vpBitrate.addEventListener('input',vpOnManualEdit);
      vpFullscreen.addEventListener('change',function(){
        vpFullscreenValue=vpFullscreen.value;
        vpApply();
      });
      window.addEventListener('resize',function(){ if(vpPanel.classList.contains('open')) vpSyncSegs(); });

      /* ---------- ALAS 菜单（与后台"ALAS 管理"对齐 · Apple 风格） ---------- */
      var apPanel=document.getElementById('ap-panel');
      var apBackdrop=document.getElementById('ap-backdrop');
      var apDeviceName=document.getElementById('ap-device-name');
      var apDeviceMeta=document.getElementById('ap-device-meta');
      var apConfigSingle=document.getElementById('ap-config-single');
      var apConfigName=document.getElementById('ap-config-name');
      var apConfigTask=document.getElementById('ap-config-task');
      var apConfigChip=document.getElementById('ap-config-chip');
      var apEmpty=document.getElementById('ap-empty');
      var apState=document.getElementById('ap-state');
      var apStateLabel=document.getElementById('ap-state-label');
      var apStateDetail=document.getElementById('ap-state-detail');
      var apStatus=document.getElementById('ap-status');
      var apStatusDot=document.getElementById('ap-status-dot');
      var apStatusText=document.getElementById('ap-status-text');
      var apToggle=document.getElementById('ap-toggle');
      var apToggleLabel=document.getElementById('ap-toggle-label');
      var apBottomToggle=document.getElementById('cb-alas');
      var apBottomLabel=document.getElementById('cb-alas-label');
      var apBottomState=document.getElementById('cb-alas-state');
      var apRefresh=document.getElementById('ap-refresh');
      var apOpenLink=document.getElementById('ap-open');
      var apExitGuard=document.getElementById('ap-exit-guard');
      var apExitGuardEnabled=false;
      var apExitGuardBusy=false;
      var alasFloatWindow=document.getElementById('alas-float-window');
      var alasFloatHead=document.getElementById('alas-float-head');
      var alasFloatFrame=document.getElementById('alas-float-frame');
      var alasFloatCloseButton=document.getElementById('alas-float-close');
      var alasFloatMaximizeButton=document.getElementById('alas-float-maximize');
      var alasFloatReturnFocus=null;
      var alasFloatGeometryReady=false;
      var alasFloatDrag=null;
      var alasFloatRestoreGeometry=null;
      var apLoaded=false;
      var apBusy=false;
      var apConfig=null;
      var apStateData={ state:'idle', label:'未运行', detail:'选择配置后查看运行状态', stopReason:null };
      var apSyncSeq=0;
      var apCache=Object.create(null);
      var AP_CACHE_TTL=120000;
      var workbenchAlasVisible=true;
      function syncWorkbenchAlasVisibility(visible){
        workbenchAlasVisible=visible!==false;
        var targets=[
          document.getElementById('alas-item'),
          document.getElementById('cb-alas'),
          document.getElementById('mirror-alas-dock-sep-before'),
          document.getElementById('mirror-alas-dock-sep-after')
        ];
        targets.forEach(function(el){
          if(!el) return;
          el.hidden=!workbenchAlasVisible;
          el.setAttribute('aria-hidden',workbenchAlasVisible?'false':'true');
          if(workbenchAlasVisible) el.removeAttribute('inert');
          else el.setAttribute('inert','');
        });
        if(!workbenchAlasVisible){
          apClose();
          alasFloatClose();
        }
        normalizeDockSeparators();
        renderAlasPill(alasPillConfig,alasPillState);
      }
      window.addEventListener('scrcpygate:workbench-alas-visibility',function(e){
        syncWorkbenchAlasVisibility(e.detail&&e.detail.visible);
      });
      if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.getWorkbenchAlasVisible==='function'){
        syncWorkbenchAlasVisibility(window.ScrcpyGateV2.getWorkbenchAlasVisible());
      }
      /* ---------- 投屏管理：按角色下发的工作台功能开关 + 底部菜单编排 ---------- */
      // 只控制显示与可用性；权限判定仍在服务端。缺省（未下发/未知）一律启用。
      // 状态条按**功能块**各一个开关（设备连接 / 观看端 / 控制权 / ALAS / 画质）。
      // 「剩余时长」按用户要求下线：改由「控制权」块显示谁在控制。
      var WORKBENCH_FEATURE_TARGETS={
        status:['status-pill'],
        status_device:['mirror-device-name','mirror-device-dot','mirror-device-status'],
        status_viewers:['mirror-viewers-label','mirror-viewers-block'],
        status_control:['mirror-control-label','ctrl-state'],
        status_alas:['mirror-alas-label','mirror-alas-dot','mirror-alas-status'],
        status_quality:['mirror-quality-label','mirror-quality-name','mirror-fps','mirror-quality-meta'],
        notifications:['notify-btn','notify-pop']
      };
      // 画面上的「开始观看」不属于控制栏，仍随 watch 开关一起隐藏。
      var WORKBENCH_EXTRA_TARGETS={ watch:['start-watch'] };
      // 状态条里每个分区（.pill-block）含哪些条目：分区内条目全部关闭时，
      // 分区本身也不再显示（否则会留下一个空的间距或孤立的分隔线）。
      var STATUS_PILL_GROUPS=[
        ['pill-device',['mirror-device-label','mirror-device-name','mirror-device-dot','mirror-device-status']],
        ['pill-viewers',['mirror-viewers-label','mirror-viewers-block']],
        ['pill-control',['mirror-control-label','ctrl-state']],
        ['mirror-alas-status-block',['mirror-alas-label','mirror-alas-dot','mirror-alas-status']],
        ['pill-quality',['mirror-quality-label','mirror-quality-name','mirror-fps','mirror-quality-meta']]
      ];
      var workbenchFeatures={};
      function workbenchFeatureEnabled(id){ return workbenchFeatures[id]!==false; }
      function dockButtonVisible(el){
        if(!el||el.hidden) return false;
        var node=el;
        while(node&&node!==document.body){
          if(node.hidden||(node.hasAttribute&&node.hasAttribute('data-feature-off'))) return false;
          node=node.parentElement;
        }
        return true;
      }
      // 按钮被隐藏后，夹在两组之间的分隔条会留下孤立的竖线：按「两侧还有可见按钮」
      // 重新计算 ctrl-dock 里每条 .ctrl-sep 的可见性。
      function normalizeDockSeparators(){
        var dock=document.getElementById('ctrl-dock');
        if(!dock) return;
        var children=Array.prototype.slice.call(dock.children);
        children.forEach(function(child,index){
          if(!child.classList||!child.classList.contains('ctrl-sep')) return;
          var visibleBefore=false,visibleAfter=false,node;
          for(node=index-1;node>=0;node-=1){
            if(children[node].classList&&children[node].classList.contains('ctrl-sep')) break;
            if(dockButtonVisible(children[node])){ visibleBefore=true; break; }
          }
          for(node=index+1;node<children.length;node+=1){
            if(children[node].classList&&children[node].classList.contains('ctrl-sep')) break;
            if(dockButtonVisible(children[node])){ visibleAfter=true; break; }
          }
          child.hidden=!(visibleBefore&&visibleAfter);
        });
      }
      // 状态条：分区内条目全关 → 分区隐藏；两侧分区都不可见 → 分隔线隐藏。
      // 这里统一用 data-feature-off（只会「加隐藏」，不会把倒计时/ALAS 逻辑
      // 自己要隐藏的分区显示出来），避免和它们互相覆盖。
      function statusNodeHidden(el){
        if(!el) return true;
        for(var node=el;node&&node!==document.body;node=node.parentElement){
          if(node.hidden||(node.hasAttribute&&node.hasAttribute('data-feature-off'))) return true;
        }
        return false;
      }
      function normalizeStatusPill(){
        var pill=document.getElementById('status-pill');
        if(!pill) return;
        STATUS_PILL_GROUPS.forEach(function(entry){
          var block=document.getElementById(entry[0]);
          if(!block) return;
          var allOff=entry[1].every(function(id){
            var el=document.getElementById(id);
            return !el||el.hidden||el.hasAttribute('data-feature-off');
          });
          if(allOff) block.setAttribute('data-feature-off','');
          else block.removeAttribute('data-feature-off');
        });
        var children=Array.prototype.slice.call(pill.children);
        children.forEach(function(child,index){
          if(!child.classList||!child.classList.contains('pill-sep')) return;
          var before=false,after=false,node;
          for(node=index-1;node>=0;node-=1){
            if(children[node].classList&&children[node].classList.contains('pill-sep')) break;
            if(!statusNodeHidden(children[node])){ before=true; break; }
          }
          for(node=index+1;node<children.length;node+=1){
            if(children[node].classList&&children[node].classList.contains('pill-sep')) break;
            if(!statusNodeHidden(children[node])){ after=true; break; }
          }
          if(before&&after) child.removeAttribute('data-feature-off');
          else child.setAttribute('data-feature-off','');
        });
      }

      /* ---------- 管理员：投屏实时记录（事件时间线） ----------
         记录由 v2-adapter 的 ScrcpyGateV2.videoRecord 采集（只存在本机内存），
         这里只负责展示/复制/导出。入口是侧边栏的 data-admin-only 菜单项，非管理员
         由 session.js 隐藏；这里再兜一层 mirrorIsAdmin() 防止被手动打开。 */
      var tlPanel=document.getElementById('tl-panel');
      var tlBackdrop=document.getElementById('tl-backdrop');
      var tlList=document.getElementById('tl-list');
      var tlStats=document.getElementById('tl-stats');
      var tlAuto=document.getElementById('tl-autoscroll');
      var tlUnsubscribe=null;
      var TL_KIND_LABEL={
        videoopen:'通道',videohello:'握手',videoplaying:'播放',videodrop:'中断',videofailed:'失败',
        keyframe:'关键帧',keyframe_wait:'等关键帧',gap:'跳号',rebuild:'重建',seek:'回跳',
        reset:'服务端重置',socket_close:'断线',rate:'码率',size:'尺寸',rotate:'旋转',
        control:'控制权',viewer_stop:'自动停播'
      };
      function timelineApi(){
        return (window.ScrcpyGateV2&&window.ScrcpyGateV2.videoRecord)||null;
      }
      function tlKindLabel(kind){ return TL_KIND_LABEL[kind]||kind; }
      function tlDuration(ms){
        var total=Math.max(0,Math.round(Number(ms)/1000));
        var minutes=Math.floor(total/60);
        var seconds=total%60;
        return minutes?minutes+'分'+seconds+'秒':seconds+'秒';
      }
      function tlDataText(data){
        var parts=[];
        Object.keys(data||{}).forEach(function(key){
          var value=data[key];
          if(value===undefined||value===null) return;
          // 空串也照原样显示：记录是排查用的，宁可多一行也不要漏掉字段。
          parts.push(key+'='+(value&&typeof value==='object'?JSON.stringify(value):String(value)));
        });
        return parts.join('  ');
      }
      function tlRenderStats(){
        if(!tlStats) return;
        var api=timelineApi();
        if(!api){ tlStats.innerHTML=''; return; }
        var stats=api.stats();
        var parts=['<span class="tl-stat">事件 <b>'+stats.total+'</b></span>'];
        if(stats.warn) parts.push('<span class="tl-stat warn">警告 <b>'+stats.warn+'</b></span>');
        if(stats.error) parts.push('<span class="tl-stat error">错误 <b>'+stats.error+'</b></span>');
        if(stats.duration_ms) parts.push('<span class="tl-stat">跨度 <b>'+tlDuration(stats.duration_ms)+'</b></span>');
        if(stats.last_rate_mbps) parts.push('<span class="tl-stat">码率 <b>'+Number(stats.last_rate_mbps).toFixed(1)+' Mbps</b></span>');
        var kinds=stats.kinds||{};
        ['keyframe_wait','gap','rebuild','seek','reset','socket_close'].forEach(function(kind){
          if(!kinds[kind]) return;
          var heavy=(kind==='gap'||kind==='rebuild'||kind==='reset'||kind==='socket_close');
          parts.push('<span class="tl-stat'+(heavy?' warn':'')+'">'+tlKindLabel(kind)+' <b>'+kinds[kind]+'</b></span>');
        });
        tlStats.innerHTML=parts.join('');
        tlRenderFloat();
      }
      /* 记录中悬浮窗：条数 / 警告 / 错误 / 参与端，点一下回面板。 */
      function tlRenderFloat(){
        renderRecordItemState();
        var box=document.getElementById('tl-float');
        if(!box) return;
        if(!recordInProgress()&&!localRecord.active){ box.hidden=true; return; }
        box.hidden=false;
        var active=recordInProgress();
        box.classList.toggle('is-stopped',!active);
        var api=timelineApi();
        var stats=(api&&typeof api.stats==='function')?api.stats():{total:0,warn:0,error:0};
        var state=recordState();
        var session=recordSession();
        var title=document.getElementById('tl-float-title');
        if(title){
          if(session&&recordIsInitiator()) title.textContent=session.stopped?'多端记录已停止':'多端记录中 · 主端';
          else if(session) title.textContent=session.stopped?'多端记录已停止':'多端记录中 · 参与端';
          else title.textContent=active?'本端记录中':'记录已停止';
        }
        var meta=document.getElementById('tl-float-meta');
        if(meta){
          var bits=[Number(stats.total||0)+' 条'];
          if(stats.warn) bits.push(Number(stats.warn)+' 警告');
          // 错误数一直显示（0 错误也要看得见），否则「记录中到底有没有出错」没有依据。
          bits.push(Number(stats.error||0)+' 错误');
          if(session){
            var participants=session.participants||[];
            var accepted=participants.filter(function(item){
              return item.state==='accepted'||item.state==='uploaded';
            }).length;
            bits.push('参与 '+accepted+'/'+participants.length);
            var bundles=(state&&state.bundles)||[];
            if(bundles.length) bits.push('已收到 '+bundles.length+' 份');
          }
          meta.textContent=bits.join(' · ');
        }
        var stopBtn=document.getElementById('tl-float-stop');
        if(stopBtn){
          var stoppable=localRecord.active||!!(session&&!session.stopped&&recordIsInitiator());
          stopBtn.hidden=!stoppable;
        }
      }
      function tlRow(entry){
        var row=document.createElement('li');
        row.className='tl-row'+(entry.level==='info'?'':' '+entry.level);
        // 悬停看完整原始记录（含未渲染的键、序号、级别）：排查时不会因为界面裁剪漏信息。
        try{ row.title=JSON.stringify(entry); }catch(e){}
        var time=document.createElement('span');
        time.className='tl-time';
        var date=new Date(entry.t);
        time.textContent=('0'+date.getHours()).slice(-2)+':'+('0'+date.getMinutes()).slice(-2)+':'+('0'+date.getSeconds()).slice(-2);
        // 序号也显示出来：跳号/丢帧要靠它对齐服务端日志。
        if(entry.seq!=null){
          var seq=document.createElement('span');
          seq.className='tl-seq';
          seq.textContent='#'+entry.seq;
          time.appendChild(document.createTextNode(' '));
          time.appendChild(seq);
        }
        var body=document.createElement('span');
        body.className='tl-body';
        var main=document.createElement('span');
        main.className='tl-main';
        var kind=document.createElement('span');
        kind.className='tl-kind';
        // 原始 kind 也留着（中文标签只作可读性补充）：未收录的事件类型照样看得出来。
        kind.textContent=tlKindLabel(entry.kind)+(entry.kind&&tlKindLabel(entry.kind)!==entry.kind?' / '+entry.kind:'');
        main.appendChild(kind);
        main.appendChild(document.createTextNode(entry.text||''));
        if(entry.level&&entry.level!=='info'){
          var lvl=document.createElement('span');
          lvl.className='tl-level tl-level-'+entry.level;
          lvl.textContent=entry.level;
          main.appendChild(document.createTextNode(' '));
          main.appendChild(lvl);
        }
        body.appendChild(main);
        var metaText=tlDataText(entry.data);
        if(metaText){
          var meta=document.createElement('span');
          meta.className='tl-meta';
          meta.textContent=metaText;
          body.appendChild(meta);
        }
        row.appendChild(time);
        row.appendChild(body);
        return row;
      }
      function tlClearList(target){
        var node=target||tlList;
        if(!node) return;
        node.innerHTML='';
      }
      function tlScrollToEnd(){
        if(tlList&&tlAuto&&tlAuto.checked) tlList.scrollTop=tlList.scrollHeight;
      }
      function tlAppend(entry){
        if(!tlList||!entry) return;
        if(tlList.firstElementChild&&tlList.firstElementChild.className==='tl-empty') tlClearList();
        tlList.appendChild(tlRow(entry));
        var api=timelineApi();
        var limit=(api&&api.limit)||400;
        while(tlList.childElementCount>limit) tlList.removeChild(tlList.firstElementChild);
        tlScrollToEnd();
        tlRenderStats();
      }
      function tlRenderAll(){
        if(!tlList) return;
        var api=timelineApi();
        var entries=(api&&api.entries())||[];
        tlClearList();
        if(!entries.length){
          var empty=document.createElement('li');
          empty.className='tl-empty';
          empty.textContent='还没有记录。开始投屏后，这里会按时间顺序显示连接、关键帧等待、跳号丢帧、解码器重建、延迟回跳等事件。';
          tlList.appendChild(empty);
          tlRenderStats();
          return;
        }
        entries.forEach(function(entry){ tlList.appendChild(tlRow(entry)); });
        tlScrollToEnd();
        tlRenderStats();
      }
      function recordApi(){
        return (window.ScrcpyGateV2&&window.ScrcpyGateV2.mirrorRecord)||null;
      }
      /* ---------- 原始数据层（面板「原始数据」块） ----------
         只渲染适配器已经采集到的原始项；采集开关关闭时适配器完全不占内存。 */
      var tlRawList=document.getElementById('tl-raw-list');
      var tlRawStats=document.getElementById('tl-raw-stats');
      var tlRawToggle=document.getElementById('tl-raw-toggle');
      var tlRawUnsubscribe=null;
      var TL_RAW_DOM_LIMIT=300;
      function rawApi(){
        return (window.ScrcpyGateV2&&window.ScrcpyGateV2.videoRecordRaw)||null;
      }
      function tlRawPayloadText(payload){
        if(payload===null||payload===undefined) return '';
        if(typeof payload==='string') return payload;
        try{ return JSON.stringify(payload); }catch(e){ return String(payload); }
      }
      function tlRawRow(entry){
        var row=document.createElement('li');
        row.className='tl-raw-row';
        var time=document.createElement('span');
        time.className='tl-time';
        var date=new Date(entry.t);
        time.textContent=('0'+date.getHours()).slice(-2)+':'+('0'+date.getMinutes()).slice(-2)+':'+('0'+date.getSeconds()).slice(-2)+'.'
          +('00'+date.getMilliseconds()).slice(-3);
        var chip=document.createElement('span');
        chip.className='tl-raw-ch '+String(entry.channel||'');
        chip.textContent=String(entry.channel||'')+(entry.kind?'/'+entry.kind:'');
        var body=document.createElement('span');
        body.className='tl-raw-body';
        // 原文照抄（不翻译、不裁剪），排查时以它为准。
        body.textContent=tlRawPayloadText(entry.payload);
        row.appendChild(time);
        row.appendChild(chip);
        row.appendChild(body);
        return row;
      }
      function tlRenderRawStats(){
        if(!tlRawStats) return;
        var api=rawApi();
        if(!api||typeof api.stats!=='function'){ tlRawStats.textContent='—'; return; }
        var stats=api.stats()||{};
        var parts=[Number(stats.total||0)+' 条'];
        if(stats.bytes) parts.push(Math.round(Number(stats.bytes)/1024)+' KB');
        if(stats.dropped) parts.push('已丢弃 '+stats.dropped);
        tlRawStats.textContent=parts.join(' · ')+((api.isEnabled&&api.isEnabled()===false)?'（采集已关闭）':'');
      }
      function tlRenderRaw(){
        if(!tlRawList) return;
        var api=rawApi();
        var entries=(api&&typeof api.entries==='function')?api.entries():[];
        tlRawList.innerHTML='';
        if(!entries.length){
          var empty=document.createElement('li');
          empty.className='tl-empty';
          empty.textContent='还没有原始数据。开始投屏后这里会逐条显示控制面 JSON 原文、发出的控制消息、Raw v2 包头、接口调用、状态切换与环境快照。';
          tlRawList.appendChild(empty);
          tlRenderRawStats();
          return;
        }
        var tail=entries.slice(Math.max(0,entries.length-TL_RAW_DOM_LIMIT));
        tail.forEach(function(entry){ tlRawList.appendChild(tlRawRow(entry)); });
        tlRawList.scrollTop=tlRawList.scrollHeight;
        tlRenderRawStats();
      }
      function tlAppendRaw(entry){
        if(!tlRawList||!entry) return;
        if(tlRawList.firstElementChild&&tlRawList.firstElementChild.className==='tl-empty') tlRawList.innerHTML='';
        tlRawList.appendChild(tlRawRow(entry));
        while(tlRawList.childElementCount>TL_RAW_DOM_LIMIT) tlRawList.removeChild(tlRawList.firstElementChild);
        tlRawList.scrollTop=tlRawList.scrollHeight;
        tlRenderRawStats();
      }
      function tlSubscribeRaw(){
        var api=rawApi();
        if(!api||typeof api.subscribe!=='function'||tlRawUnsubscribe) return;
        tlRawUnsubscribe=api.subscribe(function(entry){ tlAppendRaw(entry); });
      }
      function tlUnsubscribeRaw(){
        if(typeof tlRawUnsubscribe==='function'){ tlRawUnsubscribe(); tlRawUnsubscribe=null; }
      }
      function tlRawEnabledPref(){
        try{ return window.localStorage.getItem('scrcpygate-record-raw')!=='0'; }catch(e){ return true; }
      }
      function tlApplyRawEnabled(enabled){
        var api=rawApi();
        if(api&&typeof api.setEnabled==='function') api.setEnabled(!!enabled);
        try{ window.localStorage.setItem('scrcpygate-record-raw',enabled?'1':'0'); }catch(e){}
        if(tlRawToggle) tlRawToggle.checked=!!enabled;
        tlRenderRawStats();
      }
      // 页面侧状态也进原始层（watchState / 控制权 / 显示层操作 / 离开超时决策）。
      function rawState(kind,payload){
        var api=rawApi();
        if(api&&typeof api.push==='function') api.push('state',kind,payload||null);
      }
      function rawEnvironment(extra){
        var api=rawApi();
        if(api&&typeof api.environment==='function') api.environment(extra||null);
      }

      /* ---------- 旧记录（记录结束自动归档） ----------
         用户要求：记录一结束就把这次记录收进「旧记录」卡片，同时自动清空实时缓冲，
         下一次记录从干净状态开始。卡片放在面板「仅本端记录」下方：左侧信息
         （名称/记录时间/设备/时长/条数），右下角复制、导出、查看、删除。
         归档存 localStorage（关掉页面也还在），有单条原始数据预算与总容量上限，超了
         丢最旧的；原始数据被裁剪时会写进卡片信息里，不假装完整。 */
      var TL_ARCHIVE_KEY='scrcpygate-record-archive';
      var TL_ARCHIVE_MAX=8;
      var TL_ARCHIVE_RAW_BUDGET=512*1024;
      var TL_ARCHIVE_TOTAL_BUDGET=3.5*1024*1024;
      var tlArchive=[];
      var tlArchiveViewId='';
      var tlArchiveViewMode='events';
      var recordStartedAt=0;
      function tlArchiveBytes(value){ try{ return JSON.stringify(value).length; }catch(e){ return 0; } }
      function tlTimeText(ms){
        var date=new Date(Number(ms)||Date.now());
        return ('0'+date.getHours()).slice(-2)+':'+('0'+date.getMinutes()).slice(-2)+':'+('0'+date.getSeconds()).slice(-2);
      }
      function tlArchiveLoad(){
        try{
          var raw=window.localStorage.getItem(TL_ARCHIVE_KEY);
          var list=raw?JSON.parse(raw):[];
          return Array.isArray(list)?list:[];
        }catch(e){ return []; }
      }
      function tlArchivePersist(){
        // 配额超了就丢最旧的再存；再失败就只保留最近一条（不能让归档把记录功能搞挂）。
        var attempt=0;
        while(attempt<2){
          try{ window.localStorage.setItem(TL_ARCHIVE_KEY,JSON.stringify(tlArchive)); return true; }
          catch(e){
            attempt+=1;
            if(!tlArchive.length) return false;
            tlArchive.pop();
          }
        }
        return false;
      }
      function tlArchiveTrim(){
        while(tlArchive.length>TL_ARCHIVE_MAX) tlArchive.pop();
        while(tlArchive.length>1&&tlArchiveBytes(tlArchive)>TL_ARCHIVE_TOTAL_BUDGET) tlArchive.pop();
      }
      function tlArchiveStamp(date){
        var d=date||new Date();
        function p(n){ return (n<10?'0':'')+n; }
        return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());
      }
      function tlArchiveModeLabel(mode){
        if(mode==='multi') return '多端 · 主端';
        if(mode==='participant') return '多端 · 副端';
        return '单端';
      }
      function tlArchiveModeClass(mode){ return mode==='local'?'local':'multi'; }
      function tlArchiveDurationText(ms){
        var total=Math.max(0,Math.round(Number(ms)/1000));
        var minutes=Math.floor(total/60);
        var seconds=total%60;
        return minutes?minutes+'分'+seconds+'秒':seconds+'秒';
      }
      /* 把当前实时缓冲归档。mode: local / multi / participant。没有内容就不归档。 */
      function tlArchiveCurrent(mode,reason){
        var api=timelineApi();
        var rawApiRef=rawApi();
        var payload=(api&&typeof api.json==='function')?api.json():null;
        if(!payload) return null;
        var entries=payload.entries||[];
        var rawEntries=(payload.raw&&payload.raw.entries)||[];
        if(!entries.length&&!rawEntries.length) return null;
        // 原始数据按预算保留最新的（旧记录要长期留在浏览器里）。
        var keptRaw=[]; var used=0;
        for(var i=rawEntries.length-1;i>=0;i--){
          var size=tlArchiveBytes(rawEntries[i]);
          if(used+size>TL_ARCHIVE_RAW_BUDGET) break;
          used+=size;
          keptRaw.unshift(rawEntries[i]);
        }
        var rawTrimmed=rawEntries.length-keptRaw.length;
        payload.raw=Object.assign({},payload.raw||{},{entries:keptRaw,stats:Object.assign({},(payload.raw&&payload.raw.stats)||{},{total:keptRaw.length,stored:keptRaw.length,trimmed:rawTrimmed})});
        var ended=Date.now();
        var started=recordStartedAt||Number(payload.stats&&payload.stats.since)||ended;
        var duration=Number(payload.stats&&payload.stats.duration_ms)||(ended-started);
        var deviceName=(selectedDevice&&selectedDevice.name)||String(payload.device_id||'')||'未知设备';
        var name=tlArchiveModeLabel(mode)+' · '+deviceName+' · '+tlArchiveStamp(new Date(ended));
        var item={
          id:'rec-'+ended+'-'+Math.random().toString(16).slice(2,6),
          name:name,
          mode:String(mode||'local'),
          reason:String(reason||'stopped'),
          device:deviceName,
          device_id:String(payload.device_id||''),
          started_at:started,
          ended_at:ended,
          duration_ms:duration,
          raw_trimmed:rawTrimmed,
          payload:payload
        };
        tlArchive.unshift(item);
        tlArchiveTrim();
        var stored=tlArchivePersist();
        tlArchiveRender();
        showToast('已归档旧记录：'+name+(stored?'':'（浏览器存储已满，已丢弃最旧的）'));
        return item;
      }
      /* 记录结束：先归档，再清空实时缓冲（这就是「自动清理旧记录」）。 */
      function tlArchiveAndClearLive(mode,reason){
        var item=tlArchiveCurrent(mode,reason);
        var api=timelineApi();
        if(api&&typeof api.clear==='function') api.clear();
        var rawApiRef=rawApi();
        if(rawApiRef&&typeof rawApiRef.clear==='function') rawApiRef.clear();
        tlRenderAll();
        tlRenderRaw();
        recordStartedAt=0;
        return item;
      }
      function tlArchiveMetaText(item){
        return '记录时间 '+tlArchiveStamp(new Date(Number(item.ended_at)||Date.now()))
          +' · 设备 '+String(item.device||item.device_id||'未知设备')
          +' · 时长 '+tlArchiveDurationText(item.duration_ms);
      }
      function tlArchiveStatsText(item){
        var stats=(item.payload&&item.payload.stats)||{};
        var rawStats=(item.payload&&item.payload.raw&&item.payload.raw.stats)||{};
        var bits=[Number(stats.total||0)+' 条事件'];
        if(stats.warn) bits.push(Number(stats.warn)+' 警告');
        if(stats.error) bits.push(Number(stats.error)+' 错误');
        bits.push('原始 '+Number(rawStats.total||0)+' 条');
        if(Number(item.raw_trimmed||rawStats.trimmed||0)>0) bits.push('原始已裁剪 '+Number(item.raw_trimmed||rawStats.trimmed));
        if(item.payload&&item.payload.browser_id) bits.push('设备标识 '+String(item.payload.browser_id).slice(0,10));
        return bits.join(' · ');
      }
      /* 渲染一组旧记录卡片。面板「旧记录」与「记录方式」弹窗里的「历史记录」共用，
         卡片结构完全一致（左侧信息 + 右下四个操作）。 */
      function tlArchiveRenderInto(list){
        if(!list) return;
        list.innerHTML='';
        if(!tlArchive.length){
          var empty=document.createElement('li');
          empty.className='tl-archive-empty';
          empty.textContent='还没有旧记录。结束一次记录后，它会自动出现在这里。';
          list.appendChild(empty);
          return;
        }
        tlArchive.forEach(function(item){
          var li=document.createElement('li');
          li.className='tl-arch-card';
          li.setAttribute('data-arch-id',item.id);
          var info=document.createElement('div');
          info.className='tl-arch-info';
          var name=document.createElement('b');
          name.className='tl-arch-name';
          name.textContent=String(item.name||'未命名记录');
          var meta=document.createElement('span');
          meta.className='tl-arch-meta';
          var chip=document.createElement('span');
          chip.className='tl-arch-chip '+tlArchiveModeClass(item.mode);
          chip.textContent=tlArchiveModeLabel(item.mode);
          meta.appendChild(chip);
          meta.appendChild(document.createTextNode(tlArchiveMetaText(item)));
          var stats=document.createElement('span');
          stats.className='tl-arch-stats';
          stats.textContent=tlArchiveStatsText(item);
          info.appendChild(name);
          info.appendChild(meta);
          info.appendChild(stats);
          var actions=document.createElement('div');
          actions.className='tl-arch-actions';
          [['copy','复制'],['export','导出'],['view','查看'],['delete','删除']].forEach(function(pair){
            var btn=document.createElement('button');
            btn.type='button';
            btn.className='tl-arch-btn'+(pair[0]==='delete'?' danger':'');
            btn.setAttribute('data-arch-act',pair[0]);
            btn.textContent=pair[1];
            actions.appendChild(btn);
          });
          li.appendChild(info);
          li.appendChild(actions);
          list.appendChild(li);
        });
      }
      function tlArchiveRender(){
        var count=document.getElementById('tl-archive-count');
        var clearBtn=document.getElementById('tl-archive-clear');
        if(count) count.textContent=tlArchive.length+' 条';
        if(clearBtn) clearBtn.hidden=!tlArchive.length;
        var dialogCount=document.getElementById('tl-mode-history-count');
        if(dialogCount) dialogCount.textContent=tlArchive.length+' 条';
        // 两处列表（面板 + 记录方式弹窗的历史记录）保持同步。
        tlArchiveRenderInto(document.getElementById('tl-archive-list'));
        tlArchiveRenderInto(document.getElementById('tl-mode-history-list'));
      }
      /* 两处列表共用的点击处理（复制/导出/查看/删除）。 */
      function tlArchiveHandleClick(event){
        var btn=event.target&&event.target.closest?event.target.closest('[data-arch-act]'):null;
        if(!btn) return;
        var card=btn.closest('[data-arch-id]');
        var id=card?card.getAttribute('data-arch-id'):'';
        var act=btn.getAttribute('data-arch-act');
        if(!id) return;
        if(act==='copy') tlArchiveCopyItem(id);
        else if(act==='export') tlArchiveExportItem(id);
        else if(act==='view') tlArchiveOpen(id);
        else if(act==='delete') tlArchiveDeleteItem(id);
      }
      function tlArchiveFind(id){
        var target=String(id||'');
        for(var i=0;i<tlArchive.length;i++){ if(tlArchive[i].id===target) return tlArchive[i]; }
        return null;
      }
      function tlArchiveText(item){
        var p=(item&&item.payload)||{};
        var lines=[];
        lines.push('ScrcpyGate 投屏记录（旧记录）');
        lines.push('名称: '+String(item.name||''));
        lines.push('方式: '+tlArchiveModeLabel(item.mode)+'  设备: '+String(p.device_id||'')+'  通道: '+String(p.transport||''));
        lines.push('时间: '+tlArchiveStamp(new Date(Number(item.ended_at)||Date.now()))+'  时长: '+tlArchiveDurationText(item.duration_ms));
        var stats=p.stats||{};
        lines.push('事件: '+Number(stats.total||0)+'  警告: '+Number(stats.warn||0)+'  错误: '+Number(stats.error||0));
        lines.push('');
        lines.push('===== 事件 =====');
        (p.entries||[]).forEach(function(entry){
          var data=tlDataText(entry.data);
          lines.push(tlTimeText(entry.t)+' ['+(entry.level||'info')+'/'+(entry.kind||'event')+'] '+String(entry.text||'')+(data?'  '+data:''));
        });
        var rawEntries=(p.raw&&p.raw.entries)||[];
        if(rawEntries.length){
          lines.push('');
          lines.push('===== 原始数据（'+rawEntries.length+' 条） =====');
          rawEntries.forEach(function(entry){
            lines.push(tlTimeText(entry.t)+' ['+entry.channel+'/'+entry.kind+'] '+tlRawPayloadText(entry.payload));
          });
        }
        return lines.join('\n');
      }
      /* ---------- 旧记录查看器（只读；事件 / 原始数据两个页签） ---------- */
      var tlArchiveDialog=document.getElementById('tl-archive-dialog');
      var tlArchiveBackdrop=document.getElementById('tl-archive-backdrop');
      var tlArchiveViewList=document.getElementById('tl-archive-view-list');
      function tlArchiveOpen(id){
        var item=tlArchiveFind(id);
        if(!item||!tlArchiveDialog) return;
        tlArchiveViewId=item.id;
        tlArchiveViewMode='events';
        var title=document.getElementById('tl-archive-title');
        if(title) title.textContent=String(item.name||'旧记录');
        var meta=document.getElementById('tl-archive-meta');
        if(meta) meta.textContent=tlArchiveMetaText(item)+' · '+tlArchiveStatsText(item);
        tlArchiveRenderView();
        tlArchiveSyncTabs();
        dialogOpen(tlArchiveDialog,tlArchiveBackdrop);
      }
      function tlArchiveClose(){ dialogClose(tlArchiveDialog,tlArchiveBackdrop); }
      function tlArchiveSyncTabs(){
        var events=document.getElementById('tl-archive-view-events');
        var raw=document.getElementById('tl-archive-view-raw');
        if(events){ events.classList.toggle('active',tlArchiveViewMode==='events'); events.setAttribute('aria-pressed',String(tlArchiveViewMode==='events')); }
        if(raw){ raw.classList.toggle('active',tlArchiveViewMode==='raw'); raw.setAttribute('aria-pressed',String(tlArchiveViewMode==='raw')); }
      }
      function tlArchiveRenderView(){
        if(!tlArchiveViewList) return;
        var item=tlArchiveFind(tlArchiveViewId);
        tlArchiveViewList.innerHTML='';
        if(!item) return;
        var p=item.payload||{};
        if(tlArchiveViewMode==='raw'){
          var rawEntries=(p.raw&&p.raw.entries)||[];
          if(!rawEntries.length){
            tlArchiveViewList.innerHTML='<li class="tl-empty">这条旧记录没有原始数据（可能记录时关掉了采集，或已按预算裁剪）。</li>';
            return;
          }
          rawEntries.forEach(function(entry){ tlArchiveViewList.appendChild(tlRawRow(entry)); });
          return;
        }
        var entries=p.entries||[];
        if(!entries.length){
          tlArchiveViewList.innerHTML='<li class="tl-empty">这条旧记录没有事件。</li>';
          return;
        }
        entries.forEach(function(entry){ tlArchiveViewList.appendChild(tlRow(entry)); });
      }
      function tlArchiveCopyItem(id){
        var item=tlArchiveFind(id);
        if(!item) return;
        tlCopyText(tlArchiveText(item),'已复制旧记录：'+String(item.name||''));
      }
      function tlArchiveExportItem(id){
        var item=tlArchiveFind(id);
        if(!item) return;
        try{
          var blob=new Blob([JSON.stringify(item.payload,null,2)],{type:'application/json'});
          var url=URL.createObjectURL(blob);
          var link=document.createElement('a');
          var stamp=new Date(Number(item.ended_at)||Date.now()).toISOString().replace(/[:.]/g,'-');
          link.href=url;
          link.download='scrcpygate-mirror-record-'+String(item.device_id||'device')+'-'+stamp+'.json';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          window.setTimeout(function(){ URL.revokeObjectURL(url); },4000);
          showToast('已导出旧记录 JSON');
        }catch(err){
          showToast('导出失败：'+((err&&err.message)||'未知错误'));
        }
      }
      function tlArchiveDeleteItem(id){
        var item=tlArchiveFind(id);
        if(!item) return;
        if(!window.confirm('删除这条旧记录？删除后无法恢复。')) return;
        tlArchive=tlArchive.filter(function(entry){ return entry.id!==item.id; });
        tlArchivePersist();
        tlArchiveRender();
        if(tlArchiveViewId===item.id) tlArchiveClose();
        showToast('已删除旧记录：'+String(item.name||''));
      }
      /* 通用复制（旧记录卡片与查看器共用）。 */
      function tlCopyText(text,doneMessage){
        var value=String(text==null?'':text);
        var done=function(){ showToast(doneMessage||'已复制'); };
        var fallback=function(){
          try{
            var area=document.createElement('textarea');
            area.value=value;
            area.setAttribute('readonly','');
            area.style.position='fixed';
            area.style.left='-9999px';
            document.body.appendChild(area);
            area.select();
            var ok=document.execCommand&&document.execCommand('copy');
            document.body.removeChild(area);
            if(ok) done(); else showToast('复制失败，请改用「导出」');
          }catch(err){ showToast('复制失败，请改用「导出」'); }
        };
        if(navigator.clipboard&&navigator.clipboard.writeText){
          navigator.clipboard.writeText(value).then(done).catch(fallback);
        }else{
          fallback();
        }
      }
      function recordState(){
        var api=recordApi();
        return api&&typeof api.state==='function'?api.state():null;
      }
      // 参与者（普通用户）在邀请/参与期间也能打开面板看自己那份记录。
      function recordParticipantActive(){
        var s=recordState();
        return !!(s&&(s.armed||s.invite));
      }
      /* ---------- 记录会话的三种形态 ----------
         local  ：仅本端记录（纯前端的会话边界，不经服务端）；
         multi  ：多端记录，本端是**主端**（发起，能停止、能汇总）；
         participant：多端记录，本端是**副端**（被邀请参与，只能退出、不能停止）。 */
      var recordMode=null;                 // null | 'local' | 'multi'
      var localRecord={active:false,startedAt:0};
      // 宫格观看端：记录通知来自宫格自己的 WebSocket，适配器状态里没有会话／没有
      // is_initiator，这里留一份只读影子，让面板与悬浮窗在宫格模式下也能显示「参与中」。
      var gridRecord={session:null,clientId:''};
      function recordSession(){ var s=recordState(); return (s&&s.session)||gridRecord.session; }
      function recordIsInitiator(){ var s=recordState(); return !!(s&&s.is_initiator); }
      function recordIsParticipant(){
        var s=recordState();
        if(s&&s.session&&!s.is_initiator) return true;
        return !!(gridRecord.session&&!gridRecord.session.stopped);
      }
      function recordMyClientId(){
        var s=recordState();
        return String((s&&s.client_id)||gridRecord.clientId||'');
      }
      function recordInviteClientId(){
        var s=recordState();
        return String((s&&s.invite_client_id)||(s&&s.client_id)||gridRecord.clientId||'');
      }
      function recordInProgress(){
        if(localRecord.active) return true;
        if(recordParticipantActive()) return true;
        if(gridRecord.session&&!gridRecord.session.stopped) return true;
        var session=recordSession();
        return !!(session&&!session.stopped);
      }
      // 已经选过记录方式（哪怕会话已停止）时，再点「投屏记录」应当直接回到面板，
      // 而不是又弹一次「选择记录方式」。
      // 注意：**记录已经结束**（停止/上传完）之后要重新问一次记录方式 —— 用户反馈
      // 「结束后关闭窗口再点还是上一次的模式」。
      function recordModeChosen(){ return recordInProgress(); }
      /* 侧栏「投屏记录」高亮 + 红点：只反映「现在是否真的在记录」，
         与面板有没有打开无关（用户反馈：打开过就一直蓝着）。 */
      function renderRecordItemState(){
        var item=document.getElementById('record-item');
        if(!item) return;
        var active=recordInProgress();
        item.classList.toggle('active',active);
        if(active) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
        var dot=document.getElementById('record-dot');
        if(dot) dot.hidden=!active;
        var hint=document.getElementById('record-item-hint');
        if(hint){
          var s=recordState();
          var session=recordSession();
          var text='实时事件时间线';
          if(localRecord.active&&!session) text='本端记录中';
          else if(session) text=session.stopped?'记录已停止':'记录中（'+(session.participants||[]).length+' 个参与端）';
          hint.textContent=text;
        }
      }
      function startLocalRecord(){
        if(!localRecord.active){
          localRecord.active=true;
          localRecord.startedAt=Date.now();
          recordStartedAt=localRecord.startedAt;
          var api=timelineApi();
          // 纯前端的会话边界，直接在时间线上留一条，便于事后对照。
          if(api&&typeof api.push==='function'){
            api.push('record_start','info','开始仅本端投屏记录',{scope:'local'});
          }
          // 原始层：环境快照 + 会话边界（单端记录不经服务端，这两条就是它的「会话头」）。
          rawEnvironment({record_mode:'local'});
          rawState('record_start',{mode:'local'});
        }
        recordMode='local';
        tlRenderSession();
        tlRenderFloat();
      }
      function stopLocalRecord(){
        if(!localRecord.active) return;
        localRecord.active=false;
        var api=timelineApi();
        if(api&&typeof api.push==='function'){
          api.push('record_stop','info','停止仅本端投屏记录',{scope:'local'});
        }
        rawState('record_stop',{mode:'local',duration_ms:Date.now()-(localRecord.startedAt||Date.now())});
        // 结束即归档：这次记录进「旧记录」卡片，实时缓冲清空（自动清理）。
        tlArchiveAndClearLive('local','stopped');
        tlRenderSession();
        tlRenderFloat();
      }
      function tlOpen(){
        if(!mirrorIsAdmin()&&!recordParticipantActive()) return;
        if(!tlPanel) return;
        vpClose();
        apClose();
        upClose();
        tlRenderAll();
        tlRenderSession();
        tlRenderRaw();
        tlSubscribeRaw();
        tlPanel.classList.add('open');
        tlBackdrop.classList.add('open');
        tlPanel.setAttribute('aria-hidden','false');
        tlPanel.removeAttribute('inert');
        tlBackdrop.setAttribute('aria-hidden','false');
        var api=timelineApi();
        if(api&&typeof api.subscribe==='function'&&!tlUnsubscribe){
          tlUnsubscribe=api.subscribe(function(entry){ tlAppend(entry); });
        }
        var closeBtn=document.getElementById('tl-close');
        if(closeBtn) closeBtn.focus();
      }
      function tlClose(){
        if(!tlPanel) return;
        if(typeof tlUnsubscribe==='function'){ tlUnsubscribe(); tlUnsubscribe=null; }
        tlUnsubscribeRaw();
        tlPanel.classList.remove('open');
        tlBackdrop.classList.remove('open');
        releasePanelFocus(tlPanel);
        tlPanel.setAttribute('aria-hidden','true');
        tlPanel.setAttribute('inert','');
        tlBackdrop.setAttribute('aria-hidden','true');
      }
      function tlCopy(){
        var api=timelineApi();
        if(!api){ showToast('记录不可用'); return; }
        // 原始数据接在派生事件后面（同一份文本里都给到，排查不用来回切）。
        var raw="";
        var rawApiRef=rawApi();
        if(rawApiRef&&typeof rawApiRef.text==='function'&&(!rawApiRef.isEnabled||rawApiRef.isEnabled())){
          try{ raw='\n\n'+rawApiRef.text(); }catch(e){ raw=''; }
        }
        var text=api.text()+raw;
        var done=function(){ showToast('已复制 '+api.stats().total+' 条记录'+((raw?' + 原始数据':''))); };
        var fallback=function(){
          try{
            var area=document.createElement('textarea');
            area.value=text;
            area.setAttribute('readonly','');
            area.style.position='fixed';
            area.style.left='-9999px';
            document.body.appendChild(area);
            area.select();
            var ok=document.execCommand&&document.execCommand('copy');
            document.body.removeChild(area);
            if(ok) done(); else showToast('复制失败，请改用「导出 JSON」');
          }catch(err){ showToast('复制失败，请改用「导出 JSON」'); }
        };
        if(navigator.clipboard&&navigator.clipboard.writeText){
          navigator.clipboard.writeText(text).then(done).catch(fallback);
        }else{
          fallback();
        }
      }
      function tlExport(){
        var api=timelineApi();
        if(!api){ showToast('记录不可用'); return; }
        try{
          var blob=new Blob([JSON.stringify(api.json(),null,2)],{type:'application/json'});
          var url=URL.createObjectURL(blob);
          var link=document.createElement('a');
          var stamp=new Date().toISOString().replace(/[:.]/g,'-');
          link.href=url;
          link.download='scrcpygate-mirror-record-'+(api.stats().device_id||'device')+'-'+stamp+'.json';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          window.setTimeout(function(){ URL.revokeObjectURL(url); },4000);
          showToast('已导出记录 JSON');
        }catch(err){
          showToast('导出失败：'+(err&&err.message?err.message:'未知错误'));
        }
      }
      /* ---------- 多端记录：模式选择 / 参与邀请 / 参与端与汇总 ---------- */
      var tlModeDialog=document.getElementById('tl-mode-dialog');
      var tlModeBackdrop=document.getElementById('tl-mode-backdrop');
      var tlInviteDialog=document.getElementById('tl-invite-dialog');
      var tlInviteBackdrop=document.getElementById('tl-invite-backdrop');
      var RECORD_STATE_LABEL={invited:'待确认',accepted:'参与中',declined:'已拒绝',uploaded:'已上传',left:'已离开',unavailable:'不可用'};
      function dialogOpen(dialog,backdrop){
        if(!dialog) return;
        dialog.classList.add('open');
        if(backdrop) backdrop.classList.add('open');
        dialog.setAttribute('aria-hidden','false');
        dialog.removeAttribute('inert');
        if(backdrop) backdrop.setAttribute('aria-hidden','false');
      }
      function dialogClose(dialog,backdrop){
        if(!dialog) return;
        dialog.classList.remove('open');
        if(backdrop) backdrop.classList.remove('open');
        releasePanelFocus(dialog);
        dialog.setAttribute('aria-hidden','true');
        dialog.setAttribute('inert','');
        if(backdrop) backdrop.setAttribute('aria-hidden','true');
      }
      function openRecordModeDialog(){
        if(!mirrorIsAdmin()) return;
        // 打开时刷新历史记录（和面板里的旧记录同一份数据）。
        tlArchiveRender();
        dialogOpen(tlModeDialog,tlModeBackdrop);
        var first=document.getElementById('tl-mode-local');
        if(first) first.focus();
      }
      function closeRecordModeDialog(){ dialogClose(tlModeDialog,tlModeBackdrop); }
      function showRecordInvite(invite){
        if(!invite) return;
        lastInviteDetail=invite;
        var text=document.getElementById('tl-invite-text');
        if(text){
          text.textContent='管理员 '+String(invite.initiator||'')+' 发起了对这台设备的多端投屏记录。参与后本浏览器会记录画面管道事件，'
            +'并在管理员停止记录时把这份记录上传给他（约 '+Math.round(Number(invite.ttl_seconds||900)/60)+' 分钟内有效）。';
        }
        dialogOpen(tlInviteDialog,tlInviteBackdrop);
        var accept=document.getElementById('tl-invite-accept');
        if(accept) accept.focus();
      }
      function closeRecordInvite(){ dialogClose(tlInviteDialog,tlInviteBackdrop); }
      // 最近一次邀请的内容：宫格观看端的邀请不经过适配器状态，响应时要用它兜底。
      var lastInviteDetail=null;
      function answerRecordInvite(accept){
        var api=recordApi();
        var stateInvite=recordState()&&recordState().invite;
        var invite=stateInvite||lastInviteDetail;
        var sessionId=(invite&&invite.session)||'';
        // 宫格视图里一页可能有多个观看端，必须按「收到邀请的那个 client_id」响应。
        var inviteClientId=String((invite&&invite.client_id)||'');
        closeRecordInvite();
        if(!api||typeof api.respond!=='function'||!sessionId) return;
        api.respond(sessionId,accept,inviteClientId).then(function(){
          showToast(accept?'已参与记录，管理员停止后会收到你这份记录':'已拒绝参与记录');
          if(accept) recordMode='multi';
          tlRenderSession();
          tlRenderFloat();
        }).catch(function(error){
          showToast('响应失败：'+apiErrorText(error));
        });
      }
      function tlRenderSession(){
        var box=document.getElementById('tl-multi');
        if(!box) return;
        var state=recordState();
        var session=recordSession();
        var mine=recordIsInitiator();
        var participant=recordIsParticipant();
        var localOnly=!!localRecord.active&&!session;
        // 停止之后仍然保留这条会话框（显示「已停止」+ 记录方式入口），
        // 否则关掉面板就再也找不到「重新选择记录方式」了。
        if(!session&&!localOnly&&recordMode===null){
          box.hidden=true;
          tlRenderFloat();
          return;
        }
        box.hidden=false;
        var title=document.getElementById('tl-multi-title');
        if(title) title.textContent=session
          ? (mine?'多端记录 · 由我发起':'多端记录 · 参与中')
          : '仅本端记录';
        // 单端 / 多端徽标：面板里一眼看出这次记录是哪一种（用户反馈两者看不出区别）。
        var chip=document.getElementById('tl-mode-chip');
        if(chip){
          var chipText='未选择';
          var chipClass='is-none';
          if(session&&mine){ chipText='多端 · 主端'; chipClass='is-multi'; }
          else if(session&&participant){ chipText='多端 · 副端'; chipClass='is-multi'; }
          else if(session){ chipText='多端'; chipClass='is-multi'; }
          else if(localRecord.active){ chipText='单端记录中'; chipClass='is-local'; }
          else if(localRecord.startedAt){ chipText='单端（已停止）'; chipClass='is-local'; }
          chip.textContent=chipText;
          chip.className='tl-mode-chip '+chipClass;
        }
        // 主端 / 副端说明：副端明确不能停止，避免同账号的另一台设备（手机端）误停。
        var roleEl=document.getElementById('tl-multi-role');
        if(roleEl){
          if(session&&mine){
            roleEl.textContent='你是这次记录的主端：可以停止记录，各参与端的记录会自动汇总到这里。';
          }else if(session){
            roleEl.textContent='你是副端（管理员 '+String(session.initiator||'')+' 发起）：本端只负责记录并上传自己的画面管道事件，'
              +'停止由主端操作；你随时可以退出参与。';
          }else{
            roleEl.textContent='仅记录本浏览器，不经服务端；记录一直保存在本机内存里，可随时停止并导出。';
          }
          roleEl.hidden=false;
        }
        var stateEl=document.getElementById('tl-multi-state');
        var participants=(session&&session.participants)||[];
        var bundles=(state&&state.bundles)||[];
        var uploaded=participants.filter(function(item){return item.uploaded;}).length;
        if(stateEl){
          var api=timelineApi();
          var stats=(api&&typeof api.stats==='function')?api.stats():{total:0,error:0};
          var line=(session?(session.stopped?'已停止':'进行中'):(localRecord.active?'进行中':'已停止'))
            +' · '+Number(stats.total||0)+' 条'
            +' · '+Number(stats.error||0)+' 错误';
          if(session){
            line+=' · 参与 '+participants.filter(function(item){return item.state==='accepted'||item.state==='uploaded';}).length
              +'/'+participants.length
              +' · 已收到 '+uploaded+' 份';
          }
          stateEl.textContent=line;
        }
        var stopBtn=document.getElementById('tl-stop');
        if(stopBtn){
          var canStop=session?(!session.stopped&&mine):localRecord.active;
          stopBtn.disabled=!canStop;
          stopBtn.hidden=!canStop&&(!session||!mine);
          var stopLabel=stopBtn.querySelector('span');
          if(!stopLabel){
            // 文案节点在 HTML 里是纯文本，补一个 span 便于单独更新。
            stopBtn.textContent='';
            stopLabel=document.createElement('span');
            stopBtn.appendChild(stopLabel);
          }
          stopLabel.textContent=session?'停止记录并汇总':'停止本端记录';
        }
        var leaveBtn=document.getElementById('tl-leave');
        if(leaveBtn) leaveBtn.hidden=!(session&&participant&&!session.stopped);
        var restartBtn=document.getElementById('tl-restart');
        if(restartBtn) restartBtn.hidden=!(mirrorIsAdmin()&&!session);
        var list=document.getElementById('tl-participants');
        if(list){
          tlClearList(list);
          if(!session){
            var localHint=document.createElement('li');
            localHint.className='tl-participant empty';
            localHint.textContent='仅本端记录：不邀请其它观看端，也不会上传。';
            list.appendChild(localHint);
          }else if(!participants.length){
            var emptyPart=document.createElement('li');
            emptyPart.className='tl-participant empty';
            emptyPart.textContent='当前没有其他观看端，只有本端记录。';
            list.appendChild(emptyPart);
          }
          participants.forEach(function(item){
            var li=document.createElement('li');
            li.className='tl-participant '+String(item.state||'')+(item.away?' away':'');
            var who=document.createElement('b');
            who.textContent=String(item.username||'?');
            var badge=document.createElement('span');
            badge.className='tl-participant-state';
            badge.textContent=(RECORD_STATE_LABEL[item.state]||String(item.state||''))+(item.away?' · 暂离':'');
            var meta=document.createElement('small');
            // 显示稳定的浏览器设备标识（同一台设备进出记录不变），连接级 id 只作兜底。
            var stable=String(item.browser_id||'');
            meta.textContent=stable?stable.slice(0,10):String(item.client_id||'').slice(0,8);
            li.appendChild(who);
            li.appendChild(badge);
            li.appendChild(meta);
            list.appendChild(li);
          });
        }
        var bundlesEl=document.getElementById('tl-bundles');
        if(bundlesEl){
          tlClearList(bundlesEl);
          bundles.forEach(function(bundle,index){
            var li=document.createElement('li');
            li.className='tl-bundle';
            var from=(bundle&&bundle.from)||{};
            var entries=((bundle&&bundle.timeline&&bundle.timeline.entries)||[]).length;
            li.textContent='#'+(index+1)+' '+(from.username||'?')+' · '+entries+' 条 · '+Math.round(Number(bundle.bytes||0)/1024)+' KB · '
              +new Date(Number(bundle.received_at||0)*1000).toLocaleTimeString();
            bundlesEl.appendChild(li);
          });
          if(bundles.length){
            var hint=document.createElement('li');
            hint.className='tl-bundle hint';
            hint.textContent='上面是收到的参与端完整记录，点「复制汇总 / 导出汇总 JSON」可拿到本端 + 全部参与端的内容。';
            bundlesEl.appendChild(hint);
          }
        }
        tlRenderFloat();
      }
      function tlStopRecord(){
        // 仅本端记录：纯前端会话，停止即结束本端记录。
        if(localRecord.active&&!recordSession()){
          stopLocalRecord();
          showToast('已停止本端记录，时间线仍保留在本机，可继续复制/导出');
          return;
        }
        var api=recordApi();
        if(!api||typeof api.stop!=='function') return;
        // 副端不允许停止：服务端也会拒（record_not_initiator），这里先挡住并解释清楚。
        if(!recordIsInitiator()){
          showToast('这次记录由管理员 '+String((recordSession()||{}).initiator||'')+' 发起，副端不能停止；如需退出请点「退出参与」','error');
          tlRenderSession();
          return;
        }
        var btn=document.getElementById('tl-stop');
        if(btn) btn.disabled=true;
        api.stop().then(function(){
          showToast('已停止记录，正在等各参与端上传');
          tlRenderSession();
          tlRenderFloat();
          // 发起端**不会**收到 record_stopped 通知（服务端只通知参与端），所以这里自己收尾：
          // 归档成旧记录并清空实时缓冲（用户要求「结束记录后自动清理旧记录」）。
          tlArchiveAndClearLive('multi','stopped');
          tlRenderSession();
          tlRenderFloat();
        }).catch(function(error){
          showToast('停止失败：'+apiErrorText(error));
          tlRenderSession();
        });
      }
      // 副端退出参与：置为 declined 并通知主端；本端记录随之停止（不再上传）。
      function tlLeaveRecord(){
        var api=recordApi();
        var session=recordSession();
        if(!api||typeof api.respond!=='function'||!session) return;
        api.respond(session.session,false,recordInviteClientId()).then(function(){
          showToast('已退出这次多端记录');
          tlRenderSession();
          tlRenderFloat();
        }).catch(function(error){
          showToast('退出失败：'+apiErrorText(error));
        });
      }
      function tlBundleCopy(){
        var api=recordApi();
        if(!api||typeof api.bundleText!=='function') return;
        var text=api.bundleText();
        var fallback=function(){
          try{
            var area=document.createElement('textarea');
            area.value=text;
            area.setAttribute('readonly','');
            area.style.position='fixed';
            area.style.left='-9999px';
            document.body.appendChild(area);
            area.select();
            var ok=document.execCommand&&document.execCommand('copy');
            document.body.removeChild(area);
            showToast(ok?'已复制多端汇总':'复制失败，请改用「导出汇总 JSON」');
          }catch(err){ showToast('复制失败，请改用「导出汇总 JSON」'); }
        };
        if(navigator.clipboard&&navigator.clipboard.writeText){
          navigator.clipboard.writeText(text).then(function(){ showToast('已复制多端汇总'); }).catch(fallback);
        }else{
          fallback();
        }
      }
      function tlBundleExport(){
        var api=recordApi();
        if(!api||typeof api.bundleJson!=='function') return;
        try{
          var blob=new Blob([JSON.stringify(api.bundleJson(),null,2)],{type:'application/json'});
          var url=URL.createObjectURL(blob);
          var link=document.createElement('a');
          var stamp=new Date().toISOString().replace(/[:.]/g,'-');
          link.href=url;
          link.download='scrcpygate-mirror-record-bundle-'+stamp+'.json';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          window.setTimeout(function(){ URL.revokeObjectURL(url); },4000);
          showToast('已导出多端汇总 JSON');
        }catch(err){
          showToast('导出失败：'+(err&&err.message?err.message:'未知错误'));
        }
      }
      (function initTimelinePanel(){
        var trigger=document.getElementById('record-item');
        if(trigger) trigger.addEventListener('click',function(e){
          e.preventDefault();
          // 管理员先选记录方式；已经选过（或正在记录、或正在参与）时直接回到面板，
          // 而不是每次都重新弹「选择记录方式」。参与中的普通用户直接看自己那份记录。
          if(mirrorIsAdmin()&&!recordParticipantActive()&&!recordModeChosen()) openRecordModeDialog();
          else tlOpen();
        });
        var closeBtn=document.getElementById('tl-close');
        if(closeBtn) closeBtn.addEventListener('click',tlClose);
        if(tlBackdrop) tlBackdrop.addEventListener('click',tlClose);
        var copyBtn=document.getElementById('tl-copy');
        if(copyBtn) copyBtn.addEventListener('click',tlCopy);
        var exportBtn=document.getElementById('tl-export');
        if(exportBtn) exportBtn.addEventListener('click',tlExport);
        var clearBtn=document.getElementById('tl-clear');
        if(clearBtn) clearBtn.addEventListener('click',function(){
          var api=timelineApi();
          if(!api) return;
          api.clear();
          tlRenderAll();
          showToast('记录已清空');
        });
        // 记录方式选择
        var localBtn=document.getElementById('tl-mode-local');
        if(localBtn) localBtn.addEventListener('click',function(){
          closeRecordModeDialog();
          startLocalRecord();
          tlOpen();
          showToast('仅记录本端；需要多端协同时可在面板里重新选择记录方式');
        });
        var multiBtn=document.getElementById('tl-mode-multi');
        if(multiBtn) multiBtn.addEventListener('click',function(){
          var api=recordApi();
          closeRecordModeDialog();
          recordMode='multi';
          if(!api||typeof api.start!=='function'){ tlOpen(); return; }
          api.start().then(function(session){
            tlOpen();
            recordStartedAt=Date.now();
            var invited=session&&session.participants?session.participants.length:0;
            showToast(invited?('已邀请 '+invited+' 个观看端参与记录'):'当前没有其他观看端，仅记录本端');
            tlRenderSession();
            tlRenderFloat();
          }).catch(function(error){
            tlOpen();
            showToast('发起多端记录失败：'+apiErrorText(error));
          });
        });
        var modeCancel=document.getElementById('tl-mode-cancel');
        if(modeCancel) modeCancel.addEventListener('click',closeRecordModeDialog);
        if(tlModeBackdrop) tlModeBackdrop.addEventListener('click',closeRecordModeDialog);
        // 面板里的「重新选择记录方式」：只在本端记录（或没有会话）时出现。
        var restartBtn=document.getElementById('tl-restart');
        if(restartBtn) restartBtn.addEventListener('click',function(){
          stopLocalRecord();
          recordMode=null;
          tlRenderSession();
          tlRenderFloat();
          openRecordModeDialog();
        });
        // 副端退出参与
        var leaveBtn=document.getElementById('tl-leave');
        if(leaveBtn) leaveBtn.addEventListener('click',tlLeaveRecord);
        // 悬浮窗：点主体回面板，点停止直接停。
        var floatMain=document.getElementById('tl-float-main');
        if(floatMain) floatMain.addEventListener('click',function(){ tlOpen(); });
        var floatStop=document.getElementById('tl-float-stop');
        if(floatStop) floatStop.addEventListener('click',tlStopRecord);
        // 参与邀请
        var acceptBtn=document.getElementById('tl-invite-accept');
        if(acceptBtn) acceptBtn.addEventListener('click',function(){ answerRecordInvite(true); });
        var declineBtn=document.getElementById('tl-invite-decline');
        if(declineBtn) declineBtn.addEventListener('click',function(){ answerRecordInvite(false); });
        var stopBtn=document.getElementById('tl-stop');
        if(stopBtn) stopBtn.addEventListener('click',tlStopRecord);
        var bundleCopyBtn=document.getElementById('tl-bundle-copy');
        if(bundleCopyBtn) bundleCopyBtn.addEventListener('click',tlBundleCopy);
        var bundleExportBtn=document.getElementById('tl-bundle-export');
        if(bundleExportBtn) bundleExportBtn.addEventListener('click',tlBundleExport);
        // 原始数据层：采集开关（本浏览器偏好）+ 清空。
        if(tlRawToggle) tlRawToggle.addEventListener('change',function(){ tlApplyRawEnabled(this.checked); });
        var rawClearBtn=document.getElementById('tl-raw-clear');
        if(rawClearBtn) rawClearBtn.addEventListener('click',function(){
          var api=rawApi();
          if(!api||typeof api.clear!=='function') return;
          api.clear();
          tlRenderRaw();
          showToast('原始数据已清空');
        });
        tlApplyRawEnabled(tlRawEnabledPref());
        // 旧记录：卡片操作（复制/导出/查看/删除）、全部删除、查看器。
        // 面板「旧记录」与「记录方式」弹窗的「历史记录」是同一套卡片，共用同一处理。
        var archiveList=document.getElementById('tl-archive-list');
        if(archiveList) archiveList.addEventListener('click',tlArchiveHandleClick);
        var historyList=document.getElementById('tl-mode-history-list');
        if(historyList) historyList.addEventListener('click',tlArchiveHandleClick);
        var archiveClearBtn=document.getElementById('tl-archive-clear');
        if(archiveClearBtn) archiveClearBtn.addEventListener('click',function(){
          if(!tlArchive.length) return;
          if(!window.confirm('删除全部 '+tlArchive.length+' 条旧记录？删除后无法恢复。')) return;
          tlArchive=[];
          tlArchivePersist();
          tlArchiveRender();
          showToast('旧记录已全部删除');
        });
        var archiveCloseBtn=document.getElementById('tl-archive-close');
        if(archiveCloseBtn) archiveCloseBtn.addEventListener('click',tlArchiveClose);
        if(tlArchiveBackdrop) tlArchiveBackdrop.addEventListener('click',tlArchiveClose);
        var archiveCopyBtn=document.getElementById('tl-archive-copy');
        if(archiveCopyBtn) archiveCopyBtn.addEventListener('click',function(){ tlArchiveCopyItem(tlArchiveViewId); });
        var archiveExportBtn=document.getElementById('tl-archive-export');
        if(archiveExportBtn) archiveExportBtn.addEventListener('click',function(){ tlArchiveExportItem(tlArchiveViewId); });
        var archiveEventsBtn=document.getElementById('tl-archive-view-events');
        if(archiveEventsBtn) archiveEventsBtn.addEventListener('click',function(){ tlArchiveViewMode='events'; tlArchiveSyncTabs(); tlArchiveRenderView(); });
        var archiveRawBtn=document.getElementById('tl-archive-view-raw');
        if(archiveRawBtn) archiveRawBtn.addEventListener('click',function(){ tlArchiveViewMode='raw'; tlArchiveSyncTabs(); tlArchiveRenderView(); });
        tlArchive=tlArchiveLoad();
        tlArchiveTrim();
        tlArchiveRender();
        // 适配器派发的多端记录事件
        document.addEventListener('scrcpygate:record-invite',function(e){ showRecordInvite(e.detail); });
        document.addEventListener('scrcpygate:record-responded',function(e){
          // 普通用户同意参与后，把「投屏记录」入口露出来：他能看到自己正在分享什么。
          var detail=e&&e.detail||{};
          if(detail.accept){
            var item=document.getElementById('record-item');
            if(item) item.style.display='';
          }
          tlRenderSession();
        });
        document.addEventListener('scrcpygate:record-started',function(){
          var item=document.getElementById('record-item');
          if(item) item.style.display='';
          tlRenderSession();
        });
        document.addEventListener('scrcpygate:record-participants',function(){ tlRenderSession(); });
        document.addEventListener('scrcpygate:record-bundle',function(){ tlRenderSession(); });
        // 服务端在握手后对齐会话（晚到者补邀请、发起端刷新页面后拿回主导权）。
        document.addEventListener('scrcpygate:record-session',function(e){
          var detail=e&&e.detail||{};
          var role=String(detail.role||'');
          var item=document.getElementById('record-item');
          if(item) item.style.display='';
          if(detail.grid&&detail.session){
            // 宫格观看端：适配器没有这条会话，记进影子状态供面板/悬浮窗显示。
            gridRecord.session=detail.session;
            gridRecord.clientId=String(detail.client_id||'');
          }
          if(role==='initiator'||role==='participant'){
            // 本端已经在这条会话里：关掉可能刚弹出的邀请，直接进入记录视图。
            closeRecordInvite();
            recordMode='multi';
          }
          tlRenderSession();
          tlRenderFloat();
        });
        // 宫格观看端的上传请求：适配器的单画面通道会自己上传，这里只处理宫格转发的。
        document.addEventListener('scrcpygate:record-upload-request',function(e){
          var detail=e&&e.detail||{};
          var api=recordApi();
          if(!detail.grid||!api||typeof api.upload!=='function') return;
          var clientId=String(detail.client_id||'');
          if(!clientId) return;
          api.upload(detail.session,clientId).then(function(){
            if(gridRecord.session&&gridRecord.session.session===detail.session){
              gridRecord.session.stopped=true;
            }
            tlRenderSession();
            tlRenderFloat();
          }).catch(function(error){
            showToast('本端记录上传失败：'+apiErrorText(error));
          });
        });
        document.addEventListener('scrcpygate:record-stopped',function(e){
          var detail=e&&e.detail||{};
          if(detail.grid&&gridRecord.session) gridRecord.session.stopped=true;
          closeRecordInvite();
          // 记录结束：自动归档成旧记录卡片，并清空实时缓冲（用户要求）。
          tlArchiveAndClearLive(recordIsInitiator()?'multi':'participant','stopped');
          tlRenderSession();
          tlRenderFloat();
          // 记录已经结束：下次点「投屏记录」要重新问记录方式（用户反馈：结束后
          // 再点还是上一次的模式）。
          recordMode=null;
          renderRecordItemState();
        });
        // 画面停了但记录还在（服务端不再因为停流终止记录）：提示一次，
        // 免得用户以为记录跟着投屏一起没了。
        document.addEventListener('scrcpygate:record-stream-stopped',function(){
          if(!recordIsInitiator()&&!recordIsParticipant()) return;
          showToast('投屏已停止，但这次投屏记录仍在进行；重新开始投屏后会自动继续');
          tlRenderSession();
          tlRenderFloat();
        });
        document.addEventListener('scrcpygate:record-uploaded',function(e){
          var detail=e&&e.detail||{};
          showToast(detail.delivered===false?'记录未能送达发起端，本端仍保留':'已把本端记录上传给发起端');
          // 参与端上传完成 = 这次参与结束：同样归档并清空实时缓冲。
          if(!recordIsInitiator()){
            tlArchiveAndClearLive('participant','uploaded');
          }
          tlRenderSession();
          tlRenderFloat();
        });
        document.addEventListener('scrcpygate:record-upload-failed',function(){ tlRenderSession(); });
        document.addEventListener('keydown',function(e){
          if(e.key!=='Escape') return;
          if(tlArchiveDialog&&tlArchiveDialog.classList.contains('open')){ e.preventDefault(); tlArchiveClose(); return; }
          if(tlInviteDialog&&tlInviteDialog.classList.contains('open')){ e.preventDefault(); answerRecordInvite(false); return; }
          if(tlModeDialog&&tlModeDialog.classList.contains('open')){ e.preventDefault(); closeRecordModeDialog(); return; }
          if(!tlPanel||!tlPanel.classList.contains('open')) return;
          e.preventDefault();
          tlClose();
        });
      })();

      /* ---------- 底部菜单编排（一级 / 二级 + 顺序） ----------
         一级 = 画面下方控制栏，二级 = 「更多」弹出菜单。拖到哪一级就渲染到哪一级：
         元素本身不动代码、只在两个容器之间搬移，外观由所在容器决定（见 mirror-page.css
         里的 .ctrl-dock .cb-item / .cb-pop .cb-btn），因此不会出现"两份表示"不同步。 */
      var DOCK_MENU_IDS={
        watch:['cb-watch'],
        acquire:['cb-acquire'],
        keyboard:['cb-keyboard'],
        nav:['cb-back','cb-home','cb-tasks'],
        fullscreen:['cb-fullscreen'],
        rotate:['cb-rotate'],
        shot:['cb-pop-shot'],
        alt_keyboard:['cb-pop-keyboard'],
        more:['cb-more'],
        '@alas':['mirror-alas-dock-sep-before','cb-alas','mirror-alas-dock-sep-after']
      };
      var DOCK_MENU_GROUPS={
        watch:'session',
        acquire:'control',
        keyboard:'control',
        nav:'nav',
        fullscreen:'view',
        rotate:'view',
        shot:'more',
        alt_keyboard:'more',
        more:'more',
        '@alas':'alas'
      };
      // 与后端 workbench_features.default_layout() 保持一致：默认排布 = 现有工作台。
      // 画面方向默认自动摆正（跟随设备 + 可用空间），「旋转」按钮在其基础上按 90° 叠加，
      // 点满四次回到自动角度（offset 归零）。
      var DOCK_MENU_DEFAULT={
        level1:['watch','acquire','keyboard','@alas','nav','fullscreen','rotate','more'],
        level2:['shot','alt_keyboard']
      };
      var dockLayout=null;
      function dockMenuLayout(){
        var layout=dockLayout&&typeof dockLayout==='object'?dockLayout:null;
        var level1=layout&&layout.level1&&layout.level1.length?layout.level1.slice():DOCK_MENU_DEFAULT.level1.slice();
        var level2=layout&&layout.level2?layout.level2.slice():DOCK_MENU_DEFAULT.level2.slice();
        return {level1:level1,level2:level2};
      }
      function dockFeatureEnabled(id){
        // 锚点没有开关：@alas 的显隐由「工作台显示」控制，rotate 始终显示（只可调顺序）。
        return (id==='@alas'||id==='rotate')?true:workbenchFeatureEnabled(id);
      }
      function dockMenuRender(){
        var dock=document.getElementById('ctrl-dock');
        var pop=document.getElementById('cb-pop');
        if(!dock||!pop) return;
        restoreDockOverflow();
        var layout=dockMenuLayout();
        var level1=dockLayout?layout.level1.filter(dockFeatureEnabled):DOCK_MENU_DEFAULT.level1.slice();
        var level2=dockLayout?layout.level2.filter(dockFeatureEnabled):DOCK_MENU_DEFAULT.level2.slice();
        // 1) 没被编排到任何一级的功能一律隐藏（拖出到"未启用"区即停用）。
        Object.keys(DOCK_MENU_IDS).forEach(function(id){
          var level=level1.indexOf(id)>=0?1:(level2.indexOf(id)>=0?2:0);
          DOCK_MENU_IDS[id].forEach(function(elementId){
            var el=document.getElementById(elementId);
            if(!el) return;
            if(level) el.removeAttribute('data-feature-off');
            else el.setAttribute('data-feature-off','');
            el.hidden=false;
          });
        });
        // 2) 一级：按顺序搬进控制栏，并在分组变化处插入分隔线。
        Array.prototype.slice.call(dock.querySelectorAll('.ctrl-sep')).forEach(function(sep){
          if(sep.id==='mirror-alas-dock-sep-before'||sep.id==='mirror-alas-dock-sep-after') return;
          sep.remove();
        });
        var occupied=document.getElementById('cb-occupied');
        var previousGroup='';
        level1.forEach(function(id){
          var group=DOCK_MENU_GROUPS[id]||'';
          // ALAS 分组自带前后两条分隔线，不再额外插入（否则会出现双线）。
          if(previousGroup&&group&&previousGroup!==group&&previousGroup!=='alas'&&id!=='@alas'){
            var sep=document.createElement('span');
            sep.className='ctrl-sep dock-derived';
            dock.appendChild(sep);
          }
          DOCK_MENU_IDS[id].forEach(function(elementId){
            var el=document.getElementById(elementId);
            if(el) dock.appendChild(el);
          });
          previousGroup=group;
        });
        if(occupied) dock.appendChild(occupied);
        // 3) 二级：按顺序收进「更多」菜单。
        level2.forEach(function(id){
          DOCK_MENU_IDS[id].forEach(function(elementId){
            var el=document.getElementById(elementId);
            if(el) pop.appendChild(el);
          });
        });
        // 4) 「更多」按钮：菜单里有可见项时才出现。除了可编排的二级项，还有两个固定
        //    项（全屏后默认获取控制、逆时针旋转 90°），它们永远在菜单里，所以只要
        //    「更多」功能没被关掉，按钮就保留 —— 否则用户把二级项全拖走后这两个也进不去。
        var moreWrap=document.getElementById('cb-more');
        var staticPopupItems=pop.querySelectorAll ? pop.querySelectorAll('.cb-item[data-static]').length : 0;
        var hasPopupItems=level2.length>0||staticPopupItems>0;
        if(moreWrap){
          if(!dockFeatureEnabled('more')||!hasPopupItems) moreWrap.setAttribute('data-feature-off','');
          else moreWrap.removeAttribute('data-feature-off');
        }
        if(!hasPopupItems||!dockFeatureEnabled('more')) closeMoreMenu(false);
        if(window.lucide) lucide.createIcons();
        normalizeDockSeparators();
        // 编排变了，控制栏宽度也变了：重新判定密度。
        syncDockDensity();
      }
      function syncWorkbenchFeatures(features, layout){
        if(features&&typeof features==='object') workbenchFeatures=features;
        Object.keys(WORKBENCH_FEATURE_TARGETS).forEach(function(id){
          var hidden=!workbenchFeatureEnabled(id);
          WORKBENCH_FEATURE_TARGETS[id].forEach(function(elementId){
            var el=document.getElementById(elementId);
            if(!el) return;
            el.hidden=hidden;
            el.setAttribute('aria-hidden',hidden?'true':'false');
            if(hidden) el.setAttribute('inert',''); else el.removeAttribute('inert');
          });
        });
        Object.keys(WORKBENCH_EXTRA_TARGETS).forEach(function(id){
          var hidden=!workbenchFeatureEnabled(id);
          WORKBENCH_EXTRA_TARGETS[id].forEach(function(elementId){
            var el=document.getElementById(elementId);
            if(!el) return;
            el.hidden=hidden;
          });
        });
        if(layout&&typeof layout==='object') dockLayout=layout;
        dockMenuRender();
        if(!workbenchFeatureEnabled('notifications')){
          var notifyPop=document.getElementById('notify-pop');
          if(notifyPop) notifyPop.classList.remove('open');
          var notifyBtn=document.getElementById('notify-btn');
          if(notifyBtn) notifyBtn.setAttribute('aria-expanded','false');
        }
        if(!workbenchFeatureEnabled('fullscreen')&&fullscreenActive) exitFullscreen();
        // 「全屏打开控制栏时画面上移」开关可能刚被改过：立刻按新值收/放位移。
        syncFullscreenDockLift();
        if(!workbenchFeatureEnabled('keyboard')&&keyboardOn){
          // 关掉开关时把已经打开的键盘一并释放，避免隐藏后仍在发送按键。
          var releaseKeyboard=function(){
            if(!workbenchFeatureEnabled('keyboard')&&keyboardOn) toggleKeyboard('direct');
          };
          if(moreActionBusy) window.setTimeout(releaseKeyboard,600); else releaseKeyboard();
        }
        normalizeStatusPill();
      }
      window.addEventListener('scrcpygate:workbench-features',function(e){
        syncWorkbenchFeatures(e.detail&&e.detail.features,e.detail&&e.detail.layout);
      });
      window.addEventListener('scrcpygate:workbench-layout',function(e){
        syncWorkbenchFeatures(null,e.detail&&e.detail.layout);
      });
      if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.getWorkbenchFeatures==='function'){
        syncWorkbenchFeatures(
          window.ScrcpyGateV2.getWorkbenchFeatures(),
          typeof window.ScrcpyGateV2.getWorkbenchLayout==='function'?window.ScrcpyGateV2.getWorkbenchLayout():null
        );
      }
      dockMenuRender();
      function apDevice(){ return selectedDevice && selectedDevice.id || ''; }
      function apSelectedConfig(){ return apConfig; }
      function apSetStatus(text,state){
        apStatus.classList.remove('busy','error');
        if(state) apStatus.classList.add(state);
        apStatusDot.innerHTML=state==='busy' ? '<span class="vp-status-spinner" aria-hidden="true"></span>' : '<i data-lucide="check"></i>';
        apStatusText.textContent=text;
        if(window.lucide) lucide.createIcons();
      }
      function apRenderDevice(){
        var dev=selectedDevice;
        if(dev){
          apDeviceName.textContent=dev.name;
          apDeviceMeta.textContent=mirrorStatusText(dev)+' · ALAS '+(dev.alas||'未加载');
        }else{
          apDeviceName.textContent='未选择设备';
          apDeviceMeta.textContent='请先在左侧选择设备';
        }
      }
      function apStateText(state){
        var labels={ running:'运行中', error:'异常', unbound:'未绑定', stopped:'已停止', idle:'已停止', loading:'检查中', disabled:'已禁用', unconfigured:'未配置', disconnected:'已断开', unreachable:'不可达', timeout:'连接超时', unknown:'未检查' };
        return labels[state]||'未检查';
      }
      function apSyncBottomToggle(){
        if(!apBottomToggle) return;
        var cfg=apSelectedConfig();
        var state=String(apStateData&&apStateData.state||'idle');
        var blocked=['disabled','unconfigured','unbound','unreachable','timeout','disconnected'].indexOf(state)>=0;
        var failed=alasStateIsFault(state);
        var available=!!cfg && apDevice() && cfg.can_run!==false && !blocked;
        apBottomToggle.disabled=!available || apBusy;
        setDockTogglePressed(apBottomToggle,state==='running');
        apBottomToggle.classList.toggle('is-active',state==='running');
        // 异常状态用红色标出来：以前这里只有「运行中」有颜色，error/unreachable 落到默认灰。
        apBottomToggle.classList.toggle('is-error',failed);
        var label=state==='running'?'停止 ALAS':(state==='error'?'重启 ALAS':'启动 ALAS');
        if(!cfg) label='ALAS 未绑定';
        else if(blocked) label='ALAS '+apStateText(state);
        apBottomToggle.title=label;
        apBottomToggle.setAttribute('aria-label',label);
        if(apBottomLabel) apBottomLabel.textContent='ALAS';
        if(apBottomState) apBottomState.textContent=cfg ? apStateText(state) : '未绑定';
      }
      function apRenderConfig(){
        var c=apConfig;
        apConfigSingle.hidden=!c;
        apEmpty.hidden=!!c;
        if(!c){
          apConfigName.textContent='—';
          apConfigTask.textContent='—';
          apConfigChip.textContent='未绑定';
          apConfigChip.className='ap-config-state unbound';
          return;
        }
        apConfigName.textContent=c.name;
        apConfigTask.textContent=c.task||'自动化任务';
        var stateCls=c.state==='running'?'running':(alasStateIsFault(c.state)?'error':'stopped');
        var stateTxt=apStateText(c.state);
        apConfigChip.textContent=stateTxt;
        apConfigChip.className='ap-config-state '+stateCls;
      }
      function apRenderState(){
        var s=apStateData;
        var cfg=apSelectedConfig();
        apState.setAttribute('data-state',s.state);
        apStateLabel.textContent=s.label||'未运行';
        var detailFallback={ running:'配置正在运行', stopped:'当前未运行', idle:'当前未运行', loading:'正在获取当前设备状态', disabled:'ALAS 服务已禁用', unconfigured:'ALAS Runtime 未配置', disconnected:'ALAS Runtime 已断开', unreachable:'ALAS Runtime 不可达', timeout:'ALAS Runtime 连接超时', unbound:'该设备未绑定 ALAS 配置' };
        var stateDetail=s.detail||detailFallback[s.state]||'ALAS 状态已加载';
        apStateDetail.textContent=stateDetail+(s.stopReason?(' · '+s.stopReason):'');
        var blocked=['disabled','unconfigured'].indexOf(s.state)>=0;
        var disabled=!cfg||apBusy||!apDevice()||cfg.can_run===false||blocked;
        apToggle.disabled=disabled;
        apToggle.classList.toggle('ap-btn-danger',!!(cfg&&s.state==='running'));
        apToggleLabel.textContent=!cfg?'未绑定配置':(s.state==='disabled'?'ALAS 已禁用':(s.state==='unconfigured'?'ALAS 未配置':(s.state==='running'?'停止 ALAS':(s.state==='error'?'重启 ALAS':'启动 ALAS'))));
        var icon=apToggle.querySelector('[data-lucide]');
        if(icon){
          icon.setAttribute('data-lucide',s.state==='running'?'square':(s.state==='error'?'rotate-cw':'play'));
        }
        if(window.lucide) lucide.createIcons();
        if(cfg){
          apConfigChip.textContent=apStateText(s.state);
          apConfigChip.className='ap-config-state '+(s.state==='running'?'running':(alasStateIsFault(s.state)?'error':'stopped'));
        }
        apOpenLink.classList.toggle('disabled',!cfg);
        if(cfg && apDevice()) {
          apOpenLink.href='/alas/embed/?config='+encodeURIComponent(cfg.name)+'&device_id='+encodeURIComponent(apDevice());
          apOpenLink.setAttribute('aria-disabled','false');
        } else {
          apOpenLink.href='#';
          apOpenLink.setAttribute('aria-disabled','true');
        }
        apSyncBottomToggle();
        var pill=document.getElementById('mirror-alas-status');
        if(pill&&selectedDevice&&apDevice()===selectedDevice.id){
          pill.textContent=!cfg?'未绑定':apStateText(s.state);
          var adot=document.getElementById('mirror-alas-dot');
          if(adot){
            adot.classList.remove('off','err');
            if(!cfg||['unbound','stopped','idle','loading','disabled','unconfigured'].indexOf(s.state)>=0) adot.classList.add('off');
            else if(alasStateIsFault(s.state)) adot.classList.add('err');
          }
        }
      }
      function apApplyCache(deviceId, cached){
        if(deviceId!==apDevice() || !cached) return false;
        apConfig=cached.config||null;
        apStateData=cached.state||{ state:'unbound', label:'未绑定', detail:'该设备未绑定 ALAS 配置', stopReason:null };
        apRenderConfig();
        apRenderState();
        return true;
      }
      function apLoadConfigs(force){
        var deviceId=apDevice();
        if(!deviceId){
          apConfig=null;
          apRenderConfig();
          apStateData={ state:'idle', label:'未运行', detail:'请先在左侧选择设备', stopReason:null };
          apRenderState();
          return Promise.resolve();
        }
        var cached=apCache[deviceId];
        if(!force && cached && cached.promise) return cached.promise;
        if(!force && cached && Date.now()-cached.at<AP_CACHE_TTL){
          apApplyCache(deviceId,cached);
          return Promise.resolve(cached);
        }
        var seq=++apSyncSeq;
        apStateData={ state:'loading', label:'检查中', detail:'正在获取当前设备 ALAS 状态', stopReason:null };
        apRenderState();
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('alas.configs'))
          ? window.ScrcpyGateApi.configured('alas.configs',{ query:{ device_id:deviceId } })
          : Promise.resolve(null);
        var request=p.then(function(payload){
          if(seq!==apSyncSeq || deviceId!==apDevice()) return null;
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||null;
          var raw=d&&(d.configs||d.configurations||d.items||d.list)||(Array.isArray(d)?d:[]);
          var c=(Array.isArray(raw)&&raw[0])||null;
          apConfig=c?{
            id:String(c.id||c.config_id||c.configId||''),
            name:String(c.name||c.config_name||c.displayName||'未命名配置'),
            task:String(c.task||c.taskName||'自动化任务'),
            can_run:c.can_run!==false,
            state:String(c.state||c.status||(c.running?'running':'stopped')),
            openUrl:String(c.openUrl||c.url||'/alas/embed/')
          }:null;
          apRenderConfig();
          return apLoadStatus({deviceId:deviceId,seq:seq});
        }).then(function(){
          if(seq!==apSyncSeq || deviceId!==apDevice()) return null;
          apCache[deviceId]={ at:Date.now(), config:apConfig, state:apStateData };
          return apCache[deviceId];
        }).catch(function(error){
          if(seq===apSyncSeq && deviceId===apDevice()){
            apConfig=null;
            apStateData={ state:'unreachable', label:'不可达', detail:apiErrorText(error), stopReason:null };
            apRenderConfig();
            apRenderState();
            apSetStatus('ALAS 配置不可用 · '+apiErrorText(error),'error');
          }
          return null;
        });
        apCache[deviceId]={ at:0, config:null, state:apStateData, promise:request };
        request.then(function(result){
          var current=apCache[deviceId];
          if(current && current.promise===request){
            if(result) apCache[deviceId]=result;
            else delete apCache[deviceId];
          }
        });
        return request;
      }
      function apLoadStatus(options){
        options=options||{};
        var deviceId=options.deviceId||apDevice();
        var seq=options.seq||apSyncSeq;
        var cfg=options.config||apSelectedConfig();
        if(!cfg){
          if(deviceId===apDevice()){
            apStateData={ state:'unbound', label:'未绑定', detail:'该设备未绑定 ALAS 配置,请联系管理员在后台绑定', stopReason:null };
            apRenderState();
            apSetStatus('该设备未绑定 ALAS 配置');
          }
          return Promise.resolve();
        }
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('alas.status'))
          ? window.ScrcpyGateApi.configured('alas.status',{ query:{ config:cfg.id, config_name:cfg.name, device_id:deviceId } })
          : Promise.resolve(null);
        return p.then(function(payload){
          if(deviceId!==apDevice() || seq!==apSyncSeq) return payload;
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||null;
          if(d){
            apStateData={ state:String(d.state||d.status||'stopped'), label:String(d.label||''), detail:String(d.detail||d.message||''), stopReason:d.stopReason||d.stop_reason||null };
            if(!apStateData.label) apStateData.label=apStateData.state==='running'?'运行中':(apStateData.state==='error'?'异常':(apStateData.state==='unbound'?'未绑定':'已停止'));
          }else{
            apStateData={ state:cfg.state==='running'?'running':'stopped', label:cfg.state==='running'?'运行中':'已停止', detail:'ALAS 服务未连接,显示本地状态', stopReason:null };
          }
          apRenderState();
          apSetStatus(d?'ALAS 状态已同步':'ALAS 服务未连接');
          return payload;
        }).catch(function(error){
          if(deviceId===apDevice() && seq===apSyncSeq){
            apStateData={ state:'unreachable', label:'不可达', detail:apiErrorText(error), stopReason:null };
            apRenderState();
            apSetStatus('状态获取失败 · '+apiErrorText(error),'error');
          }
          return null;
        });
      }
      function apToggleRun(){
        var cfg=apSelectedConfig();
        if(!cfg||apBusy) return;
        if(cfg.can_run===false){
          apSetStatus('该配置仅可查看状态,无运行权限','error');
          return;
        }
        apBusy=true;
        apToggle.disabled=true;
        apSyncBottomToggle();
        apSetStatus('正在执行…','busy');
        var action=apStateData.state==='running'?'stop':(apStateData.state==='error'?'restart':'start');
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('alas.toggle'))
          ? window.ScrcpyGateApi.configured('alas.toggle',{ method:'POST', body:{ config_name:cfg.name, config_id:cfg.id, device_id:apDevice(), action:action } })
          : Promise.reject(new Error('ALAS 服务未连接'));
        p.then(function(){ apBusy=false; return apLoadStatus({force:true}); })
         .then(function(){ apSetStatus('操作已提交 · ALAS 状态已刷新'); refreshAlasPill(); })
         .catch(function(error){ apBusy=false; apSetStatus('操作失败 · '+apiErrorText(error),'error'); apRenderState(); });
      }
      function apOnDeviceChange(){
        apRenderDevice();
        apConfig=null;
        apStateData={ state:'loading', label:'检查中', detail:'正在获取当前设备 ALAS 状态', stopReason:null };
        apRenderConfig();
        apRenderState();
        apLoadConfigs(false).catch(function(){});
      }
      function apRenderExitGuard(enabled){
        if(!apExitGuard) return;
        var on=enabled===true;
        apExitGuard.classList.toggle('is-on',on);
        apExitGuard.setAttribute('aria-checked',on?'true':'false');
        apExitGuard.disabled=!!apExitGuardBusy;
      }
      function apLoadExitGuard(){
        if(!apExitGuard) return Promise.resolve();
        if(!(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('alas.exitGuard'))){ apRenderExitGuard(false); return Promise.resolve(); }
        return window.ScrcpyGateApi.configured('alas.exitGuard').then(function(payload){
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||{};
          apExitGuardEnabled=d.enabled===true;
          apRenderExitGuard(apExitGuardEnabled);
        }).catch(function(){ apRenderExitGuard(false); });
      }
      function apToggleExitGuard(){
        if(!apExitGuard||apExitGuardBusy) return;
        if(!(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('alas.exitGuard.update'))){ showToast('ALAS 服务未连接'); return; }
        var next=!apExitGuardEnabled;
        apExitGuardBusy=true;
        apExitGuardEnabled=next;
        apRenderExitGuard(next);
        apSetStatus(next?'正在开启退出后检测…':'正在关闭退出后检测…','busy');
        window.ScrcpyGateApi.configured('alas.exitGuard.update',{ method:'PUT', body:{ enabled:next } }).then(function(payload){
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||{};
          apExitGuardBusy=false;
          apExitGuardEnabled=d.enabled===true;
          apRenderExitGuard(apExitGuardEnabled);
          apSetStatus(apExitGuardEnabled?'退出后检测已开启 · 关闭浏览器后检测一次':'退出后检测已关闭');
        }).catch(function(error){
          apExitGuardBusy=false;
          apExitGuardEnabled=!next;
          apRenderExitGuard(apExitGuardEnabled);
          apSetStatus('设置失败 · '+apiErrorText(error),'error');
        });
      }
      function apOpen(){
        if(!workbenchAlasVisible) return;
        if(!apLoaded){ apLoaded=true; apLoadConfigs().catch(function(){}); apLoadExitGuard(); }
        else apLoadExitGuard();
        vpClose();
        upClose();
        tlClose();
        apPanel.classList.add('open');
        apBackdrop.classList.add('open');
        apPanel.setAttribute('aria-hidden','false');
        apPanel.removeAttribute('inert');
        apBackdrop.setAttribute('aria-hidden','false');
        document.body.classList.remove('sg-mobile-nav');
        var scrim=document.getElementById('mobile-scrim');
        if(scrim){ scrim.classList.remove('open'); scrim.setAttribute('aria-hidden','true'); }
        apRenderDevice();
        var closeBtn=document.getElementById('ap-close');
        if(closeBtn) closeBtn.focus();
      }
      function apClose(){
        apPanel.classList.remove('open');
        apBackdrop.classList.remove('open');
        releasePanelFocus(apPanel);
        apPanel.setAttribute('aria-hidden','true');
        apPanel.setAttribute('inert','');
        apBackdrop.setAttribute('aria-hidden','true');
      }
      function alasIsMobileViewport(){
        if(!window.matchMedia) return window.innerWidth<=640;
        return window.matchMedia('(max-width: 640px)').matches || window.matchMedia('(pointer: coarse)').matches;
      }
      function alasFloatSafeUrl(raw){
        var value=String(raw||'').trim();
        if(!value||value==='#') return '';
        try{
          var target=new URL(value,window.location.href);
          var path=target.pathname.replace(/\/+$/,'');
          if(target.origin!==window.location.origin || path!='/alas/embed') return '';
          return target.href;
        }catch(e){ return ''; }
      }
      function alasFloatClamp(){
        if(!alasFloatWindow||!alasFloatWindow.open||alasFloatWindow.classList.contains('is-maximized')) return;
        var rect=alasFloatWindow.getBoundingClientRect();
        var margin=12;
        var maxLeft=Math.max(margin,window.innerWidth-rect.width-margin);
        var maxTop=Math.max(margin,window.innerHeight-rect.height-margin);
        var left=parseFloat(alasFloatWindow.style.left);
        var top=parseFloat(alasFloatWindow.style.top);
        if(!Number.isFinite(left)) left=rect.left;
        if(!Number.isFinite(top)) top=rect.top;
        alasFloatWindow.style.left=Math.round(Math.min(maxLeft,Math.max(margin,left)))+'px';
        alasFloatWindow.style.top=Math.round(Math.min(maxTop,Math.max(margin,top)))+'px';
      }
      function alasFloatCenter(){
        if(!alasFloatWindow) return;
        var rect=alasFloatWindow.getBoundingClientRect();
        alasFloatWindow.style.transform='none';
        alasFloatWindow.style.left=Math.max(12,Math.round((window.innerWidth-rect.width)/2))+'px';
        alasFloatWindow.style.top=Math.max(12,Math.round((window.innerHeight-rect.height)/2))+'px';
        alasFloatGeometryReady=true;
      }
      function alasFloatSetMaximizeIcon(active){
        if(!alasFloatMaximizeButton) return;
        alasFloatMaximizeButton.setAttribute('aria-pressed',String(active));
        alasFloatMaximizeButton.setAttribute('aria-label',active?'还原 ALAS 窗口':'最大化 ALAS 窗口');
        alasFloatMaximizeButton.title=active?'还原':'最大化';
        alasFloatMaximizeButton.innerHTML='<i data-lucide="'+(active?'minimize-2':'maximize-2')+'"></i>';
        if(window.lucide) lucide.createIcons();
      }
      function alasFloatToggleMaximize(){
        if(!alasFloatWindow||!alasFloatWindow.open) return;
        var active=!alasFloatWindow.classList.contains('is-maximized');
        if(active){
          alasFloatRestoreGeometry={
            left:alasFloatWindow.style.left,
            top:alasFloatWindow.style.top,
            width:alasFloatWindow.style.width,
            height:alasFloatWindow.style.height,
            transform:alasFloatWindow.style.transform
          };
          alasFloatWindow.classList.add('is-maximized');
        }else{
          alasFloatWindow.classList.remove('is-maximized');
          if(alasFloatRestoreGeometry){
            alasFloatWindow.style.left=alasFloatRestoreGeometry.left;
            alasFloatWindow.style.top=alasFloatRestoreGeometry.top;
            alasFloatWindow.style.width=alasFloatRestoreGeometry.width;
            alasFloatWindow.style.height=alasFloatRestoreGeometry.height;
            alasFloatWindow.style.transform=alasFloatRestoreGeometry.transform;
          }
        }
        alasFloatSetMaximizeIcon(active);
        alasFloatClamp();
      }
      function alasFloatFinishClose(){
        if(!alasFloatWindow) return;
        alasFloatWindow.classList.remove('is-dragging');
        alasFloatWindow.setAttribute('aria-hidden','true');
        alasFloatWindow.setAttribute('inert','');
        if(alasFloatFrame) alasFloatFrame.removeAttribute('src');
        alasFloatDrag=null;
        var target=alasFloatReturnFocus;
        alasFloatReturnFocus=null;
        if(target&&target.isConnected&&!target.closest('[inert]')&&target.getAttribute('aria-hidden')!=='true'){
          target.focus();
        }
      }
      function alasFloatClose(){
        if(!alasFloatWindow||!alasFloatWindow.open) return;
        if(typeof alasFloatWindow.close==='function') alasFloatWindow.close();
        else alasFloatFinishClose();
      }
      function alasFloatOpen(url,trigger){
        if(!workbenchAlasVisible) return false;
        if(!alasFloatWindow||!alasFloatFrame) return false;
        if(typeof alasFloatWindow.show!=='function'){
          window.location.href=url;
          return true;
        }
        alasFloatReturnFocus=document.getElementById('alas-item')||trigger||null;
        vpClose();
        apClose();
        upClose();
        alasFloatFrame.src=url;
        alasFloatWindow.removeAttribute('inert');
        alasFloatWindow.setAttribute('aria-hidden','false');
        if(!alasFloatWindow.open) alasFloatWindow.show();
        if(!alasFloatGeometryReady) alasFloatCenter();
        alasFloatClamp();
        if(alasFloatCloseButton) alasFloatCloseButton.focus();
        return true;
      }
      if(alasFloatWindow){
        alasFloatWindow.addEventListener('close',alasFloatFinishClose);
        alasFloatWindow.addEventListener('cancel',function(e){ e.preventDefault(); alasFloatClose(); });
        alasFloatWindow.addEventListener('pointermove',function(e){
          if(!alasFloatDrag||e.pointerId!==alasFloatDrag.pointerId) return;
          e.preventDefault();
          alasFloatWindow.style.left=Math.round(e.clientX-alasFloatDrag.offsetX)+'px';
          alasFloatWindow.style.top=Math.round(e.clientY-alasFloatDrag.offsetY)+'px';
          alasFloatClamp();
        });
        alasFloatWindow.addEventListener('pointerup',function(e){
          if(!alasFloatDrag||e.pointerId!==alasFloatDrag.pointerId) return;
          alasFloatDrag=null;
          alasFloatWindow.classList.remove('is-dragging');
          try{ alasFloatWindow.releasePointerCapture(e.pointerId); }catch(err){}
        });
        alasFloatWindow.addEventListener('pointercancel',function(){
          alasFloatDrag=null;
          alasFloatWindow.classList.remove('is-dragging');
        });
      }
      if(alasFloatHead&&alasFloatWindow){
        alasFloatHead.addEventListener('pointerdown',function(e){
          if(!alasFloatWindow.open||alasFloatWindow.classList.contains('is-maximized')||(e.button!==undefined&&e.button!==0)) return;
          if(e.target.closest('button,a,input,select,textarea')) return;
          var rect=alasFloatWindow.getBoundingClientRect();
          alasFloatDrag={ pointerId:e.pointerId, offsetX:e.clientX-rect.left, offsetY:e.clientY-rect.top };
          alasFloatWindow.classList.add('is-dragging');
          try{ alasFloatWindow.setPointerCapture(e.pointerId); }catch(err){}
          e.preventDefault();
        });
      }
      if(alasFloatCloseButton) alasFloatCloseButton.addEventListener('click',alasFloatClose);
      if(alasFloatMaximizeButton) alasFloatMaximizeButton.addEventListener('click',alasFloatToggleMaximize);
      if(apOpenLink) apOpenLink.addEventListener('click',function(e){
        if(apOpenLink.getAttribute('aria-disabled')==='true'){
          e.preventDefault();
          return;
        }
        var url=alasFloatSafeUrl(apOpenLink.getAttribute('href'));
        if(!url){
          e.preventDefault();
          return;
        }
        if(alasIsMobileViewport()) return;
        e.preventDefault();
        alasFloatOpen(url,apOpenLink);
      });
      window.addEventListener('resize',alasFloatClamp);
      document.getElementById('alas-item').addEventListener('click',function(e){
        e.preventDefault();
        if(!workbenchAlasVisible) return;
        apOpen();
      });
      document.getElementById('ap-close').addEventListener('click',apClose);
      apBackdrop.addEventListener('click',apClose);
      apToggle.addEventListener('click',apToggleRun);
      if(apBottomToggle) apBottomToggle.addEventListener('click',function(){ apToggleRun(); });
      apRefresh.addEventListener('click',function(){ apLoadConfigs(true); });
      if(apExitGuard) apExitGuard.addEventListener('click',apToggleExitGuard);

      /* ---------- 账户菜单（资料、偏好与安全 · Apple 风格） ---------- */
      var upPanel=document.getElementById('up-panel');
      var upBackdrop=document.getElementById('up-backdrop');
      var upAvatar=document.getElementById('up-avatar');
      var upName=document.getElementById('up-name');
      var upUsername=document.getElementById('up-username');
      var upRole=document.getElementById('up-role');
      var upPermsBox=document.getElementById('up-perms');
      var upPermsEmpty=document.getElementById('up-perms-empty');
      var upCur=document.getElementById('up-cur');
      var upNew=document.getElementById('up-new');
      var upConfirm=document.getElementById('up-confirm');
      var upSave=document.getElementById('up-save');
      var upStatus=document.getElementById('up-status');
      var upStatusDot=document.getElementById('up-status-dot');
      var upStatusText=document.getElementById('up-status-text');
      var upLoaded=false;
      var upBusy=false;
      function upUser(){
        var u=null;
        try{ u=(window.ScrcpyGateSession&&window.ScrcpyGateSession.current&&window.ScrcpyGateSession.current())||null; }catch(e){}
        if(!u){
          var nm=document.querySelector('.user-meta strong');
          var sm=document.querySelector('.user-meta small');
          if(nm) u={ displayName:nm.textContent, role:sm?sm.textContent:'' };
        }
        return u;
      }
      function upRenderIdentity(){
        var u=upUser();
        var name=u&&(u.displayName||u.name||u.username)||'当前账户';
        var username=u&&(u.username||u.email)||'—';
        var role=u&&(u.roleLabel||u.role)||(u&&u.roleKey==='admin'?'管理员':(u?'普通用户':''));
        upAvatar.textContent=(name||'A').charAt(0).toUpperCase();
        upName.textContent=name;
        upUsername.textContent=username;
        upRole.textContent=role;
        upRole.hidden=!role;
        upRole.classList.toggle('user',!!role&&role.indexOf('管理员')<0);
      }
      function upRenderExpiry(){
        var u=upUser();
        var lastLogin=u&&u.lastLoginAt||'';
        var lastClient=u&&u.lastLoginClient||'';
        var lastSpan=document.getElementById('up-last-login');
        if(lastSpan) lastSpan.textContent=lastLogin?('上次登录 '+lastLogin+(lastClient?' · '+lastClient:'')):'上次登录 —';
        var expires=u&&u.expiresAt||'';
        var box=document.getElementById('up-expiry');
        var textEl=document.getElementById('up-expiry-text');
        var metaEl=document.getElementById('up-expiry-meta');
        var renewBtn=document.getElementById('up-renew');
        if(!expires){
          box.setAttribute('data-state','ok');
          textEl.textContent='长期有效';
          metaEl.textContent='无到期限制';
          renewBtn.hidden=true;
          renewBtn.disabled=false;
          renewBtn.textContent='申请续期';
          return;
        }
        var days=Math.ceil((new Date(expires+'T00:00:00')-Date.now())/86400000);
        var state=days<0?'expired':(days<=30?'warn':'ok');
        box.setAttribute('data-state',state);
        textEl.textContent=expires;
        metaEl.textContent=days<0?('已过期 '+Math.abs(days)+' 天'):('剩余 '+days+' 天');
        renewBtn.hidden=!(days<=30);
        renewBtn.disabled=false;
        renewBtn.textContent='申请续期';
      }
      function upRenew(){
        if(upBusy) return;
        upBusy=true;
        upSetStatus('正在提交续期申请…','busy');
        var p=(window.ScrcpyGateApi&&(window.ScrcpyGateApi.isConfigured('account.renewal.request')||window.ScrcpyGateApi.isConfigured('users.renewal.request')))
          ? window.ScrcpyGateApi.configured(window.ScrcpyGateApi.isConfigured('account.renewal.request')?'account.renewal.request':'users.renewal.request',{ method:'POST', body:{} })
          : Promise.reject(new Error('账户服务未连接'));
        p.then(function(){
          upBusy=false;
          var btn=document.getElementById('up-renew');
          btn.disabled=true;
          btn.textContent='申请已提交';
          upSetStatus('续期申请已提交,等待管理员处理');
        }).catch(function(error){
          upBusy=false;
          upSetStatus('提交失败 · '+apiErrorText(error),'error');
        });
      }
      function upRenderPerms(items){
        upPermsBox.innerHTML='';
        upPermsEmpty.hidden=!!(items&&items.length);
        (items||[]).forEach(function(p){
          var row=document.createElement('div');
          row.className='up-perm';
          var icon=document.createElement('i');
          icon.setAttribute('data-lucide',p.permission==='control'?'smartphone':'eye');
          var nm=document.createElement('span');
          nm.className='up-perm-name';
          nm.textContent=p.device||p.deviceName||p.name||p.deviceId||'—';
          var chip=document.createElement('span');
          chip.className='up-perm-chip'+(p.permission==='control'?' control':'');
          chip.textContent=p.permission==='control'?'控制':'观看';
          row.appendChild(icon);
          row.appendChild(nm);
          row.appendChild(chip);
          upPermsBox.appendChild(row);
        });
        if(window.lucide) lucide.createIcons();
      }
      function upLoadPerms(){
        // The admin permissions catalog is not available to regular users.
        // Use the filtered device catalog instead, which is also the source
        // for the workbench device list and therefore reflects this account's
        // actual grants.
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('devices.list'))
          ? window.ScrcpyGateApi.configured('devices.list',{ method:'GET', timeout:10000 })
          : Promise.resolve(null);
        return p.then(function(payload){
          var d=payload&&(payload.data&&typeof payload.data==='object'?payload.data:payload)||null;
          var devices=d&&(d.devices||d.items||d.list)||(Array.isArray(d)?d:null)||[];
          var items=devices.filter(function(device){
            return device && device.enabled !== false && device.can_view !== false && device.noPermission !== true;
          }).map(function(device){
            // 页面里的设备对象由适配层规范化过：控制权限是驼峰 canControl，蛇形
            // can_control 只存在于裸接口响应里。此前只看 can_control，导致每一行都
            // 落回「观看」（连管理员也一样）。只认显式 true —— 字段缺失时按仅观看
            // 显示，不虚报控制权。
            var canControl=device.canControl===true||device.can_control===true;
            return {
              device:device.name||device.display_name||device.displayName||device.id||device.device_id||'—',
              deviceId:device.id||device.device_id||'',
              permission:canControl?'control':'watch',
              canView:device.can_view!==false&&device.noPermission!==true,
              canControl:canControl
            };
          });
          upRenderPerms(items);
          return payload;
        }).catch(function(){
          upRenderPerms(null);
          var empty=upPermsEmpty.querySelector('span');
          if(empty) empty.textContent='权限信息不可用';
        });
      }
      function upSetStatus(text,state){
        upStatus.classList.remove('busy','error');
        if(state) upStatus.classList.add(state);
        upStatusDot.innerHTML=state==='busy' ? '<span class="vp-status-spinner" aria-hidden="true"></span>' : '<i data-lucide="check"></i>';
        upStatusText.textContent=text;
        if(window.lucide) lucide.createIcons();
      }
      function upChangePassword(){
        if(upBusy) return;
        var cur=upCur.value;
        var nw=upNew.value;
        var cf=upConfirm.value;
        if(!cur){ upSetStatus('请输入当前密码','error'); return; }
        if(!nw||nw.length<12){ upSetStatus('新密码至少 12 位','error'); return; }
        if(nw!==cf){ upSetStatus('两次输入的新密码不一致','error'); return; }
        upBusy=true;
        upSave.disabled=true;
        upSetStatus('正在保存…','busy');
        var p=(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('account.password'))
          ? window.ScrcpyGateApi.configured('account.password',{ method:'PUT', body:{ current:cur, password:nw, new_password:nw } })
          : Promise.reject(new Error('账户服务未连接'));
        p.then(function(){
          upBusy=false;
          upSave.disabled=false;
          upCur.value='';
          upNew.value='';
          upConfirm.value='';
          upSetStatus('密码已更新');
        }).catch(function(error){
          upBusy=false;
          upSave.disabled=false;
          upSetStatus('保存失败 · '+apiErrorText(error),'error');
        });
      }
      function upLogout(){
        var done=function(){ try{ window.location.href='/login'; }catch(e){} };
        if(window.ScrcpyGateApi&&window.ScrcpyGateApi.isConfigured('auth.logout')){
          window.ScrcpyGateApi.configured('auth.logout',{ method:'POST' }).then(done).catch(function(){ showToast('退出失败，请重试'); });
        }else{
          showToast('退出服务不可用，请重试');
        }
      }
      function upOpen(){
        if(!upLoaded){ upLoaded=true; upLoadPerms().catch(function(){}); }
        vpClose();
        apClose();
        tlClose();
        upPanel.classList.add('open');
        upBackdrop.classList.add('open');
        upPanel.setAttribute('aria-hidden','false');
        upPanel.removeAttribute('inert');
        upBackdrop.setAttribute('aria-hidden','false');
        document.body.classList.remove('sg-mobile-nav');
        var scrim=document.getElementById('mobile-scrim');
        if(scrim){ scrim.classList.remove('open'); scrim.setAttribute('aria-hidden','true'); }
        upRenderIdentity();
        upRenderExpiry();
        var closeBtn=document.getElementById('up-close');
        if(closeBtn) closeBtn.focus();
      }
      function upClose(){
        upPanel.classList.remove('open');
        upBackdrop.classList.remove('open');
        releasePanelFocus(upPanel);
        upPanel.setAttribute('aria-hidden','true');
        upPanel.setAttribute('inert','');
        upBackdrop.setAttribute('aria-hidden','true');
      }
      document.getElementById('acct-item').addEventListener('click',function(e){
        e.preventDefault();
        upOpen();
      });
      document.getElementById('up-close').addEventListener('click',upClose);
      upBackdrop.addEventListener('click',upClose);
      var upPasswordForm=document.getElementById('up-password-form');
      if(upPasswordForm){
        upPasswordForm.addEventListener('submit',function(e){ e.preventDefault(); upChangePassword(); });
      }
      document.getElementById('up-renew').addEventListener('click',upRenew);
      document.getElementById('up-logout').addEventListener('click',upLogout);

      /* 断点切换会同时改变面板的停靠方向与隐藏方向（>640px 为右侧抽屉、关闭时
         停在 translateX(105%)；≤640px 为底部抽屉、关闭时停在 translateY(105%)）。
         若保留 transform 过渡，关闭状态的面板会在矩阵插值下沿对角线扫过整个
         画面（实测 x=1220→0、y=0→937，约 300ms），因此切换瞬间临时禁用过渡。 */
      var panelLayoutQuery=window.matchMedia?window.matchMedia('(max-width:640px)'):null;
      function closePanelsOnLayoutChange(){
        var panels=[vpPanel,apPanel,upPanel,tlPanel];
        panels.forEach(function(panel){ if(panel&&panel.style) panel.style.transition='none'; });
        vpClose();
        apClose();
        upClose();
        tlClose();
        var restore=function(){
          panels.forEach(function(panel){ if(panel&&panel.style) panel.style.transition=''; });
        };
        if(window.requestAnimationFrame){
          window.requestAnimationFrame(function(){ window.requestAnimationFrame(restore); });
        }else{
          window.setTimeout(restore,32);
        }
      }
      if(panelLayoutQuery){
        if(typeof panelLayoutQuery.addEventListener==='function') panelLayoutQuery.addEventListener('change',closePanelsOnLayoutChange);
        else if(typeof panelLayoutQuery.addListener==='function') panelLayoutQuery.addListener(closePanelsOnLayoutChange);
      }

      /* ---------- 初始渲染 ---------- */
      setWatchState('idle');
      loadMirrorDevices();
      mirrorRefreshTimer=window.setInterval(function(){
        if(document.visibilityState==='visible' && !document.hidden) loadMirrorDevices({silent:true});
      },MIRROR_REFRESH_MS);
      /* 通知中心：进入页面先拉一次（未读红点不用展开就能看到），
         之后每分钟静默刷新一次，回到前台也补一次。 */
      loadNotifications();
      notificationRefreshTimer=window.setInterval(function(){
        if(document.visibilityState==='visible' && !document.hidden) loadNotifications();
      },MIRROR_REFRESH_MS);
      document.addEventListener('visibilitychange',function(){
        if(document.visibilityState==='visible') loadNotifications();
      });
      if(window.ScrcpyGateSession) window.ScrcpyGateSession.start().catch(function(){}).then(function(){
        // 会话就绪后再判定管理员视图开关（首次设备列表渲染时可能还没有会话信息）。
        syncViewSwitch();
        restoreViewMode();
        // 全屏自动获取控制：读取本浏览器偏好并同步开关外观。
        fullscreenAutoControl=savedFullscreenAutoControl();
        renderAutoControlToggle();
        loadNotifications();
      });
    });
