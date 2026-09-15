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
      function renderQualityMeta(){
        var el=document.getElementById('mirror-quality-meta');
        if(!el) return;
        var hasBase=!!qualityMetaBase&&qualityMetaBase!=='—';
        if(!(measuredMbps>0&&hasBase)){ el.textContent=qualityMetaBase; el.removeAttribute('title'); return; }
        // 配置码率/实测码率:实测值单独着色;移动端省略单位,避免状态栏尾部被挤出可视区。
        var compact=window.matchMedia&&window.matchMedia('(max-width:767px)').matches;
        el.textContent=qualityMetaBase+'/';
        el.title=tr('配置码率/实测码率');
        var rate=document.createElement('span');
        rate.className='pill-rate';
        rate.textContent=measuredMbps.toFixed(1)+(compact?'':' Mbps');
        el.appendChild(rate);
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
      var controlState='unknown';
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
         同账号但非本连接按 'free' 处理，让按钮显示「获取控制」并能真正拿到（见 v2-adapter 的
         「同账号陈旧锁先释放再重试」）。 */
      function selfHoldsControl(){
        return !!(window.ScrcpyGateV2&&window.ScrcpyGateV2.state&&window.ScrcpyGateV2.state.controlOwnership===true);
      }
      function snapshotControlState(owner){
        if(selfHoldsControl()) return 'self';
        var username=currentMirrorUsername();
        var name=owner?String(owner):'';
        if(!name) return 'free';
        return username&&name===username?'free':'other';
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
          onNotice:function(message){ showToast(message); }
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
      function restoreViewMode(){
        // 刷新后保持管理员上次选择的视图（localStorage，按浏览器保存）。
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
          // 管理员没有绑定时后端会回退到 Runtime 配置目录（admin_fallback），
          // 那不是绑定，按需求「未绑定不显示」要过滤掉。
          var configs=((payload&&payload.configs)||[]).filter(function(c){ return !c||c.admin_fallback!==true; });
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
            var nextId=deviceById(previousId) ? previousId : deviceItems[0].id;
            if(shouldRender) selectDevice(nextId);
            else selectedDevice=deviceById(nextId);
          }else{
            selectedDevice=null;
            if(shouldRender) renderDeviceList();
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
      function renderControl(){
        var watching=watchState==='playing';
        var pending=watchState==='connecting'||watchState==='waiting'||watchState==='websocket'||watchState==='hello'||watchState==='keyframe'||watchState==='resuming';
        var self=controlState==='self';
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
          keyboardBtn.setAttribute('aria-pressed',String(keyboardOn));
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
        if(popShot) popShot.disabled=!menuAllowed;
        // 「全屏后默认获取控制」是本浏览器偏好，只要有菜单权限就能改。
        if(popAutoControl) popAutoControl.disabled=!menuAllowed;
        if(popKeyboard) popKeyboard.disabled=!controlMenuAllowed || moreActionBusy;
        var ctext=self?'我 · 控制中':(controlState==='other'?'其他用户占用中':(controlState==='free'?'空闲':'未加载'));
        // 控制权胶囊已移除(底部「获取控制」按钮与占用 chip 已表达同一状态),保留写入以便旧结构复用。
        if(ctrlState){
          ctrlState.className='ctrl-state '+(self?'self':(controlState==='other'?'other':'free'));
          ctrlState.innerHTML='<i data-lucide="'+(self?'shield-check':(controlState==='other'?'lock':'shield'))+'"></i>'+ctext;
        }
        if(window.lucide) lucide.createIcons();
        // 按钮文案/可见性变了，控制栏宽度也会变：重新判定是否需要收文字/换行。
        syncDockDensity();
        // 全屏里「获取控制」可能就藏在收起的把手后面，把手要跟着状态点亮。
        if(fullscreenActive) updateFullscreenDockToggle();
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
        if(remaining>300){ cdBanner.classList.remove('show'); return; }
        if(remaining<=0){ cdBannerText.textContent='观看时长已用完，正在结束…'; return; }
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
        watchState=s;
        if(s!=='playing' && viewerStopWarning){
          viewerStopWarning=false;
          document.title=baseDocumentTitle;
        }
        var overlayState=(s==='websocket'||s==='hello')?'waiting':s;
        overlays.forEach(function(o){ o.classList.toggle('show',o.getAttribute('data-cstate')===overlayState); });
        var watching=s==='playing';
        videoSurface.classList.toggle('show',watching);
        if(watching){
          startCountdown();
        }else{
          stopCountdown();
        }
        renderControl();
      }
      function startWatchFlow(){
        if(!selectedDevice) { showToast('请先选择设备'); return; }
        // 新会话从正向开始，随后由自动摆正按设备方向调整。
        videoBox.classList.remove('rotated','rotated-180');
        if(window.ScrcpyGateV2&&typeof window.ScrcpyGateV2.setVideoRotation==='function') window.ScrcpyGateV2.setVideoRotation(0);
        if(displayController) displayController.setRotation(0);
        syncVideoOrientation();
        var requestGeneration=++watchRequestGeneration;
        var requestDeviceId=selectedDevice.id;
        setWatchState('connecting');
        window.ScrcpyGateApi.configured('sessions.create',{method:'POST',body:{deviceId:requestDeviceId}}).then(function(payload){
          if(requestGeneration!==watchRequestGeneration || !selectedDevice || String(selectedDevice.id)!==String(requestDeviceId)){
            return window.ScrcpyGateApi.configured('sessions.stop',{params:{deviceId:requestDeviceId},method:'POST'}).catch(function(){});
          }
          currentSession=payload && (payload.session || payload.data || payload);
          if(currentSession&&currentSession.controller){ controlState=String(currentSession.controller); }
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
         范围 25%–150%（默认 80%）：再大单格子会顶满视口高度，再小就看不清缩略图。 */
      var GRID_ZOOM_KEY='scrcpygate-grid-zoom';
      var GRID_ZOOM_BASE=300;
      var GRID_ZOOM_MIN=25;
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
        if(gridZoomValue) gridZoomValue.textContent=value+'%';
        return value;
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
        renderQualityMeta();
      });
      document.addEventListener('scrcpygate:viewer-stop-warning',function(){
        viewerStopWarning=true;
        document.title='将在 60 秒后停止观看 · '+baseDocumentTitle;
        cdBannerText.textContent='将在 60 秒后停止观看';
        cdBanner.classList.add('show');
      });
      document.addEventListener('scrcpygate:viewer-stop-cancelled',function(){
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
          controlState=snapshotControlState(lock.username);
        }else if(watchState==='playing'){
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
        if(active) controlState='self';
        else if(controlState==='self') controlState=(owner&&owner!==currentMirrorUsername())?'other':'free';
        renderControl();
      });
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
          keyboardBtn.setAttribute('aria-pressed',String(active));
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
          clearAdaptiveVideoFrame();
          return;
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
      /* ---------- 方向：完全自动（跟随设备 + 按可用空间摆正） ----------
         手动旋转（自动 / ↺ / ↻ 三个按钮）在 ISSUE-152/154/155 做过，用户实测「不好用、
         太挤太乱」，ISSUE-156 按要求整套撤掉：画面方向只由「设备真实方向 + 当前可用空间」
         决定。角度仍走 display-control.js 的纯状态层（0/90 两档自动摆正）。 */
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
      /* 按当前源尺寸 + 可用空间自动摆正；返回本次是否改变了方向。 */
      function applyAutoFitRotation(){
        var desired=autoFitRotationDegrees();
        if(desired===null||desired===viewRotationDegrees()) return false;
        setViewRotation(desired);
        return true;
      }
      /**
       * 设备自己转了方向（横竖互换）= 重新按新方向自动摆正（不一定是正向）。
       */
      function applyAutoVideoRotation(){
        return applyAutoFitRotation();
      }
      function syncVideoOrientation(detail){
        if(!app) return;
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
        // 那正是「手机端全屏下没有自动横向」（用户反馈）。
        var orientation=deviceLandscape?'landscape':'portrait';
        app.setAttribute('data-video-orientation',orientation);
        // 画面尺寸/方向变了，顶部留白也跟着变 —— 让位位移要重算。
        syncFullscreenDockLift();
        // 全屏（原生或纯 CSS 全屏）都尝试锁定屏幕方向；桌面浏览器不支持时静默失败。
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
        fullscreenBtn.setAttribute('aria-pressed',String(fullscreenActive));
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
          dockHidden=false;
        }
        // 全屏是沉浸式播放：控制栏默认收进边缘，只留一条细把手，点一下才展开
        // （需要手动点「获取控制」的情况由 autoAcquireControlOnFullscreen 兜底摊开）。
        if(next&&!wasActive) dockHidden=true;
        app.classList.toggle('is-fullscreen',next);
        app.classList.toggle('dock-menu-collapsed',next&&dockHidden);
        app.setAttribute('data-fullscreen',String(next));
        if(ctrlDock) ctrlDock.classList.toggle('dock-hidden',next&&dockHidden);
        placeMoreMenuForFullscreen(next);
        // 全屏里控制栏只留图标（见 CSS），宽度变了要重新判定是否需要换行 —— 放在切换
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
        var visible=fullscreenActive;
        fullscreenDockToggle.setAttribute('aria-hidden',String(!visible));
        fullscreenDockToggle.tabIndex=visible?0:-1;
        fullscreenDockToggle.setAttribute('aria-expanded',String(!dockHidden));
        var attention=visible&&dockHidden&&controlAttentionNeeded();
        // 贴边的细条只用 CSS ::before 画一条亮线（见 mirror-page.css）：不再写图标/文字，
        // 可读名称与提示走 aria-label / title，状态用 .is-attention 点亮。
        var label=attention?'展开控制栏并获取控制':'展开控制栏';
        if(!dockHidden) label='收起控制栏';
        var hint=label+'（拖动可沿屏幕左右侧移动，双击或按 0 回到右侧中间）';
        fullscreenDockToggle.setAttribute('aria-label',hint);
        fullscreenDockToggle.title=hint;
        fullscreenDockToggle.classList.toggle('is-attention',attention);
        if(fullscreenDockToggle.firstChild) fullscreenDockToggle.textContent='';
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
        if(!fullscreenActive) return;
        if(mirrorControlTools) mirrorControlTools.setDockHidden(!!hidden); else dockHidden=!!hidden;
        if(ctrlDock) ctrlDock.classList.toggle('dock-hidden',dockHidden);
        app.classList.toggle('dock-menu-collapsed',dockHidden);
        if(dockHidden) closeMoreMenu(true);
        updateFullscreenDockToggle();
        applyDockAnchor();
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

      /* ---------- 控制栏密度：先收文字，再换行，绝不让按钮被裁掉 ----------
         窄屏上控制栏原来是「固定一条 + 横向滚动」，于是最左边/最右边的按钮会被裁一半，
         而且「获取控制」拿到焦点时浏览器会把容器滚过去，把最左边的「开始投屏」推出可视区。
         这里按实际宽度分两档降级：① 隐藏文字标签、按钮变方形（图标 + aria-label）；
         ② 仍然放不下才换行。宽度够时保持原来的「图标 + 文字」胶囊样式。 */
      function dockOverflow(){
        if(!ctrlDock) return 0;
        return Math.max(0,(Number(ctrlDock.scrollWidth)||0)-(Number(ctrlDock.clientWidth)||0));
      }
      function syncDockDensity(){
        if(!ctrlDock) return;
        ctrlDock.classList.remove('dock-compact','dock-wrap');
        if(dockOverflow()<=0) return;
        ctrlDock.classList.add('dock-compact');
        if(dockOverflow()>0) ctrlDock.classList.add('dock-wrap');
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
        return Array.prototype.slice.call(cbPop ? cbPop.querySelectorAll('[role^="menuitem"]') : []).filter(function(item){ return !item.disabled; });
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
      if(fullscreenDockToggle) fullscreenDockToggle.addEventListener('click',function(event){
        // 刚拖过 / 双击复位：都不要当成「展开-收起」。
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
      // 状态条按**功能块**各一个开关（设备连接 / 观看端 / 剩余时长 / ALAS / 画质）。
      var WORKBENCH_FEATURE_TARGETS={
        status:['status-pill'],
        status_device:['mirror-device-name','mirror-device-dot','mirror-device-status'],
        status_viewers:['mirror-viewers-label','mirror-viewers-block'],
        status_countdown:['cd-label','cd-time'],
        status_alas:['mirror-alas-label','mirror-alas-dot','mirror-alas-status'],
        status_quality:['mirror-quality-label','mirror-quality-name','mirror-quality-meta'],
        notifications:['notify-btn','notify-pop']
      };
      // 画面上的「开始观看」不属于控制栏，仍随 watch 开关一起隐藏。
      var WORKBENCH_EXTRA_TARGETS={ watch:['start-watch'] };
      // 状态条里每个分区（.pill-block）含哪些条目：分区内条目全部关闭时，
      // 分区本身也不再显示（否则会留下一个空的间距或孤立的分隔线）。
      var STATUS_PILL_GROUPS=[
        ['pill-device',['mirror-device-label','mirror-device-name','mirror-device-dot','mirror-device-status']],
        ['pill-viewers',['mirror-viewers-label','mirror-viewers-block']],
        ['cd-block',['cd-label','cd-time']],
        ['mirror-alas-status-block',['mirror-alas-label','mirror-alas-dot','mirror-alas-status']],
        ['pill-quality',['mirror-quality-label','mirror-quality-name','mirror-quality-meta']]
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
          if(value===undefined||value===null||value==='') return;
          parts.push(key+'='+String(value));
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
      }
      function tlRow(entry){
        var row=document.createElement('li');
        row.className='tl-row'+(entry.level==='info'?'':' '+entry.level);
        var time=document.createElement('span');
        time.className='tl-time';
        var date=new Date(entry.t);
        time.textContent=('0'+date.getHours()).slice(-2)+':'+('0'+date.getMinutes()).slice(-2)+':'+('0'+date.getSeconds()).slice(-2);
        var body=document.createElement('span');
        body.className='tl-body';
        var main=document.createElement('span');
        main.className='tl-main';
        var kind=document.createElement('span');
        kind.className='tl-kind';
        kind.textContent=tlKindLabel(entry.kind);
        main.appendChild(kind);
        main.appendChild(document.createTextNode(entry.text||''));
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
      function recordState(){
        var api=recordApi();
        return api&&typeof api.state==='function'?api.state():null;
      }
      // 参与者（普通用户）在邀请/参与期间也能打开面板看自己那份记录。
      function recordParticipantActive(){
        var s=recordState();
        return !!(s&&(s.armed||s.invite));
      }
      function tlOpen(){
        if(!mirrorIsAdmin()&&!recordParticipantActive()) return;
        if(!tlPanel) return;
        vpClose();
        apClose();
        upClose();
        tlRenderAll();
        tlRenderMulti();
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
        var text=api.text();
        var done=function(){ showToast('已复制 '+api.stats().total+' 条记录'); };
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
        dialogOpen(tlModeDialog,tlModeBackdrop);
        var first=document.getElementById('tl-mode-local');
        if(first) first.focus();
      }
      function closeRecordModeDialog(){ dialogClose(tlModeDialog,tlModeBackdrop); }
      function showRecordInvite(invite){
        if(!invite) return;
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
      function answerRecordInvite(accept){
        var api=recordApi();
        var invite=recordState()&&recordState().invite;
        var sessionId=(invite&&invite.session)||'';
        closeRecordInvite();
        if(!api||typeof api.respond!=='function'||!sessionId) return;
        api.respond(sessionId,accept).then(function(){
          showToast(accept?'已参与记录，管理员停止后会收到你这份记录':'已拒绝参与记录');
          tlRenderMulti();
        }).catch(function(error){
          showToast('响应失败：'+apiErrorText(error));
        });
      }
      function tlRenderMulti(){
        var box=document.getElementById('tl-multi');
        if(!box) return;
        var state=recordState();
        var session=state&&state.session;
        var mine=mirrorIsAdmin();
        if(!session||(!mine&&!recordParticipantActive())){
          box.hidden=true;
          return;
        }
        box.hidden=false;
        var title=document.getElementById('tl-multi-title');
        if(title) title.textContent=mine?'多端记录 · 由我发起':'多端记录 · 参与中（管理员 '+String(session.initiator||'')+' 发起）';
        var stateEl=document.getElementById('tl-multi-state');
        var participants=session.participants||[];
        var bundles=(state&&state.bundles)||[];
        var uploaded=participants.filter(function(item){return item.uploaded;}).length;
        if(stateEl){
          stateEl.textContent=(session.stopped?'已停止':'进行中')
            +' · 参与 '+participants.filter(function(item){return item.state==='accepted'||item.state==='uploaded';}).length+'/'+participants.length
            +' · 已收到 '+uploaded+' 份';
        }
        var stopBtn=document.getElementById('tl-stop');
        if(stopBtn) stopBtn.disabled=!mine||!!session.stopped;
        var list=document.getElementById('tl-participants');
        if(list){
          tlClearList(list);
          if(!participants.length){
            var emptyPart=document.createElement('li');
            emptyPart.className='tl-participant empty';
            emptyPart.textContent='当前没有其他观看端，只有本端记录。';
            list.appendChild(emptyPart);
          }
          participants.forEach(function(item){
            var li=document.createElement('li');
            li.className='tl-participant '+String(item.state||'');
            var who=document.createElement('b');
            who.textContent=String(item.username||'?');
            var badge=document.createElement('span');
            badge.className='tl-participant-state';
            badge.textContent=RECORD_STATE_LABEL[item.state]||String(item.state||'');
            var meta=document.createElement('small');
            meta.textContent=String(item.client_id||'').slice(0,8);
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
      }
      function tlStopRecord(){
        var api=recordApi();
        if(!api||typeof api.stop!=='function') return;
        var btn=document.getElementById('tl-stop');
        if(btn) btn.disabled=true;
        api.stop().then(function(){
          showToast('已停止记录，正在等各参与端上传');
          tlRenderMulti();
        }).catch(function(error){
          showToast('停止失败：'+apiErrorText(error));
          tlRenderMulti();
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
          // 管理员先选记录方式；参与中/被邀请的普通用户直接看自己那份记录。
          if(mirrorIsAdmin()&&!recordParticipantActive()) openRecordModeDialog();
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
          tlOpen();
          showToast('仅记录本端；需要多端协同时再点一次「投屏记录」');
        });
        var multiBtn=document.getElementById('tl-mode-multi');
        if(multiBtn) multiBtn.addEventListener('click',function(){
          var api=recordApi();
          closeRecordModeDialog();
          if(!api||typeof api.start!=='function'){ tlOpen(); return; }
          api.start().then(function(session){
            tlOpen();
            var invited=session&&session.participants?session.participants.length:0;
            showToast(invited?('已邀请 '+invited+' 个观看端参与记录'):'当前没有其他观看端，仅记录本端');
            tlRenderMulti();
          }).catch(function(error){
            tlOpen();
            showToast('发起多端记录失败：'+apiErrorText(error));
          });
        });
        var modeCancel=document.getElementById('tl-mode-cancel');
        if(modeCancel) modeCancel.addEventListener('click',closeRecordModeDialog);
        if(tlModeBackdrop) tlModeBackdrop.addEventListener('click',closeRecordModeDialog);
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
        // 适配器派发的多端记录事件
        document.addEventListener('scrcpygate:record-invite',function(e){ showRecordInvite(e.detail); });
        document.addEventListener('scrcpygate:record-responded',function(e){
          // 普通用户同意参与后，把「投屏记录」入口露出来：他能看到自己正在分享什么。
          var detail=e&&e.detail||{};
          if(detail.accept){
            var item=document.getElementById('record-item');
            if(item) item.style.display='';
          }
          tlRenderMulti();
        });
        document.addEventListener('scrcpygate:record-started',function(){
          var item=document.getElementById('record-item');
          if(item) item.style.display='';
          tlRenderMulti();
        });
        document.addEventListener('scrcpygate:record-participants',function(){ tlRenderMulti(); });
        document.addEventListener('scrcpygate:record-bundle',function(){ tlRenderMulti(); });
        document.addEventListener('scrcpygate:record-stopped',function(){
          closeRecordInvite();
          tlRenderMulti();
        });
        document.addEventListener('scrcpygate:record-uploaded',function(e){
          var detail=e&&e.detail||{};
          showToast(detail.delivered===false?'记录未能送达发起端，本端仍保留':'已把本端记录上传给发起端');
          tlRenderMulti();
        });
        document.addEventListener('scrcpygate:record-upload-failed',function(){ tlRenderMulti(); });
        document.addEventListener('keydown',function(e){
          if(e.key!=='Escape') return;
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
        shot:'more',
        alt_keyboard:'more',
        more:'more',
        '@alas':'alas'
      };
      // 与后端 workbench_features.default_layout() 保持一致：默认排布 = 现有工作台。
      // 画面方向已经完全自动（ISSUE-156 撤掉了手动旋转按钮），控制栏不再有方向按钮。
      var DOCK_MENU_DEFAULT={
        level1:['watch','acquire','keyboard','@alas','nav','fullscreen','more'],
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
        return id==='@alas'?true:workbenchFeatureEnabled(id);
      }
      function dockMenuRender(){
        var dock=document.getElementById('ctrl-dock');
        var pop=document.getElementById('cb-pop');
        if(!dock||!pop) return;
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
        apBottomToggle.setAttribute('aria-pressed',state==='running'?'true':'false');
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
            return device && device.enabled !== false && device.can_view !== false;
          }).map(function(device){
            return {
              device:device.name||device.display_name||device.displayName||device.id||device.device_id||'—',
              deviceId:device.id||device.device_id||'',
              permission:device.can_control===true?'control':'watch',
              canView:device.can_view!==false,
              canControl:device.can_control===true
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
