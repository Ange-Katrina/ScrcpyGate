/* 管理员「宫格」视图：一次显示全部设备，按需为每台设备建立观看流。
 *
 * 设计要点：
 * - 每个格子独立一条 `/ws/devices/{id}/video` 连接 + 独立 JMuxer/MSE 播放器，
 *   与单画面适配器（v2-adapter.js）互不干扰；进入宫格前页面会先结束单画面观看。
 * - 宫格固定 12 个坑位（slotCount）：有设备的坑位是设备卡片，没有设备的坑位是
 *   「空坑位」占位卡（点击即去添加 ADB 设备），因此每行形状稳定、不再随设备数量跳动。
 * - 默认全部格子是占位卡片，只有点击「开始截图」才真正起流，并受 maxLive 限制。
 * - 观看端只是接入已有会话（`mirror/start` 返回 viewer_token），不会抢占控制权。
 */
(function (global) {
  'use strict';

  var RawV2 = global.ScrcpyGateRawV2 || null;
  var DEFAULT_MAX_LIVE = 12;
  /* 宫格坑位数量：与页面工具栏「同时显示 12 路」一致，缺设备的坑位显示空坑位。 */
  var DEFAULT_SLOT_COUNT = 12;
  var RECONNECT_DELAYS = [1500, 3000, 6000];
  /* 统一竖屏的格子比例（9:16）。300px 宽格子 → 屏幕区 290×515。 */
  var UNIFORM_PORTRAIT_ASPECT = 9 / 16;
  /* 与设备卡写入 `--mg-stage-aspect` 时相同的取整方式，避免空坑位与设备卡差 1px。 */
  var PORTRAIT_ASPECT_CSS = Math.round(UNIFORM_PORTRAIT_ASPECT * 1000) / 1000;
  /* 画面旋转后与格子比例相差不超过这个相对值时按等比缩放显示（正好铺满，无边）；
     差得更多（例如 18:9 设备放进 9:16 格子）则改为铺满裁切，同样不出现黑边。 */
  var STAGE_CONTAIN_TOLERANCE = 0.02;

  function create(options) {
    options = options || {};
    var tr = options.tr || function (value) { return String(value == null ? '' : value); };
    var escHtml = options.escHtml || function (value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character];
      });
    };
    var refreshIcons = options.refreshIcons || function () {};
    var onOpenDevice = options.onOpenDevice || function () {};
    var onAddDevice = options.onAddDevice || function () {};
    var onNotice = options.onNotice || function () {};
    // 卡片实际宽度变化时回调（页面用它提示「滑块受高度/宽度限制」）。
    var onTileWidth = options.onTileWidth || null;
    var maxLive = Number(options.maxLive) > 0 ? Math.floor(Number(options.maxLive)) : DEFAULT_MAX_LIVE;
    var slotCount = Number(options.slotCount) > 0 ? Math.floor(Number(options.slotCount)) : DEFAULT_SLOT_COUNT;
    var thumbnailFps = 1;
    /* 画面方向：'portrait' 所有格子统一 9:16 竖框（默认，监控墙一行一个形状、便于扫一眼），
       'device' 跟随每台设备（竖屏设备=竖格，横屏设备=横格）。 */
    var orientation = options.orientation === 'device' ? 'device' : 'portrait';

    var host = null;
    var active = false;
    var tiles = Object.create(null);
    var order = [];
    var slots = [];
    var controls = null;
    var TILE_CHROME_HEIGHT = 62;   // 卡片头 + 操作条 + 间距（不随宽度变）
    var lastTileWidth = 0;          // 最近一次实际生效的卡片宽度
    var lastTileRequestedWidth = 0; // 滑块请求的宽度（可能被高度预算压小）
    var batchToken = 0;             // 批量操作令牌（一键全部投屏/停止/刷新）

    /* ---------------- 基础工具 ---------------- */

    function apiCall(name, body) {
      var api = global.ScrcpyGateApi;
      if (!api || typeof api.configured !== 'function') return Promise.reject(new Error('api unavailable'));
      return api.configured(name, { body: body || {} });
    }
    function wsBase() {
      var location = global.location || {};
      return (location.protocol === 'https:' ? 'wss://' : 'ws://') + (location.host || '');
    }
    function liveCount() {
      return order.filter(function (id) {
        var tile = tiles[id];
        return !!tile && (tile.state === 'starting' || tile.state === 'connecting' || tile.state === 'playing');
      }).length;
    }
    function isLive(tile) {
      return !!tile && (tile.state === 'starting' || tile.state === 'connecting' || tile.state === 'playing');
    }
    function tileList() {
      return order.map(function (id) { return tiles[id]; }).filter(function (tile) { return !!tile; });
    }
    function startableTiles() {
      return tileList().filter(function (tile) {
        return !isLive(tile) && tile.device && tile.device.online === true && tile.device.permission !== false;
      });
    }
    function restartableTiles() {
      return tileList().filter(function (tile) { return isLive(tile) || tile.state === 'error'; });
    }
    function syncToolbar() {
      if (!controls) return;
      var live = liveCount();
      if (controls.startAll) controls.startAll.disabled = !active || live >= maxLive || !startableTiles().length;
      if (controls.stopAll) controls.stopAll.disabled = !active || live === 0;
      if (controls.refreshAll) controls.refreshAll.disabled = !active || !restartableTiles().length;
    }
    function noticeText(template, values) {
      var text = tr(template);
      return String(text).replace(/\{(\d+)\}/g, function (match, index) {
        var value = values[Number(index)];
        return value === undefined || value === null ? match : String(value);
      });
    }
    function statusText(device) {
      if (!device) return '—';
      if (device.online === false) return tr('离线');
      if (device.online == null) return tr('未检查');
      var parts = [tr('在线')];
      if (Number(device.viewers) > 0) parts.push(Number(device.viewers) + tr(' 人观看'));
      if (device.controller) parts.push(tr('控制：') + String(device.controller));
      return parts.join(' · ');
    }

    /* ---------------- 渲染 ---------------- */

    function tileMarkup(device) {
      var name = escHtml(device.name || device.id);
      var offline = device.online === false;
      var noPermission = device.permission === false;
      var startLabel = escHtml(tr('开始截图'));
      var stopLabel = escHtml(tr('停止截图'));
      var openLabel = escHtml(tr('打开'));
      var controlLabel = escHtml(tr('控制'));
      return '' +
        '<article class="mg-tile" data-device-id="' + escHtml(device.id) + '" data-state="idle">' +
          '<header class="mg-head">' +
            '<span class="mg-dot ' + (offline ? 'off' : (device.online === true ? 'on' : 'unknown')) + '" aria-hidden="true"></span>' +
            '<span class="mg-name" title="' + name + '">' + name + '</span>' +
            '<span class="mg-meta"></span>' +
          '</header>' +
          '<div class="mg-stage">' +
            '<video class="mg-video" muted playsinline></video>' +
            '<div class="mg-placeholder"><i data-lucide="monitor-off" aria-hidden="true"></i><span class="mg-placeholder-text"></span></div>' +
            '<div class="mg-overlay" hidden><span class="mg-spinner" aria-hidden="true"></span><span class="mg-overlay-text"></span></div>' +
          '</div>' +
          '<footer class="mg-actions">' +
            '<span class="mg-actions-main">' +
              '<button class="mg-btn mg-btn-primary" type="button" data-action="start" title="' + startLabel + '" aria-label="' + startLabel + '"' + (offline || noPermission ? ' disabled' : '') + '><i data-lucide="play" aria-hidden="true"></i><span class="mg-btn-text">' + startLabel + '</span></button>' +
              '<button class="mg-btn mg-btn-stop" type="button" data-action="stop" title="' + stopLabel + '" aria-label="' + stopLabel + '" hidden><i data-lucide="square" aria-hidden="true"></i><span class="mg-btn-text">' + stopLabel + '</span></button>' +
            '</span>' +
            '<span class="mg-actions-end">' +
              '<button class="mg-btn" type="button" data-action="open" title="' + openLabel + '" aria-label="' + openLabel + '"><i data-lucide="monitor-up" aria-hidden="true"></i><span class="mg-btn-text">' + openLabel + '</span></button>' +
              '<button class="mg-btn" type="button" data-action="control" title="' + controlLabel + '" aria-label="' + controlLabel + '" hidden><i data-lucide="mouse-pointer-click" aria-hidden="true"></i><span class="mg-btn-text">' + controlLabel + '</span></button>' +
            '</span>' +
          '</footer>' +
        '</article>';
    }

    /* 空坑位：没有设备的坑位也要占住格子形状（布局稳定、行数不随设备数量跳动）。
       结构与设备卡一致（头 + 屏幕区 + 底部操作条），所以两种卡总高度相同、同一行齐平；
       整张卡可点击，点击即去「添加 ADB 设备」。 */
    function slotMarkup() {
      var label = escHtml(tr('空坑位'));
      var emptyText = escHtml(tr('未添加设备'));
      var addLabel = escHtml(tr('添加 ADB 设备'));
      return '' +
        '<article class="mg-tile mg-slot" data-state="empty" role="button" tabindex="0" aria-label="' + addLabel + '">' +
          '<header class="mg-head">' +
            '<span class="mg-dot unknown" aria-hidden="true"></span>' +
            '<span class="mg-name">' + label + '</span>' +
            '<span class="mg-meta">' + emptyText + '</span>' +
          '</header>' +
          '<div class="mg-stage">' +
            '<div class="mg-placeholder"><i data-lucide="plus" aria-hidden="true"></i><span class="mg-placeholder-text">' + emptyText + '</span></div>' +
          '</div>' +
          '<footer class="mg-actions">' +
            '<span class="mg-actions-main">' +
              '<button class="mg-btn mg-btn-primary mg-slot-add" type="button" title="' + addLabel + '" aria-label="' + addLabel + '"><i data-lucide="plus" aria-hidden="true"></i><span class="mg-btn-text">' + addLabel + '</span></button>' +
            '</span>' +
          '</footer>' +
        '</article>';
    }

    function ensureSlot(index) {
      if (slots[index]) return slots[index];
      if (!host) return null;
      var wrapper = document.createElement('div');
      wrapper.innerHTML = slotMarkup();
      var element = wrapper.firstChild;
      var slot = { element: element };
      // 整卡点击（含底部按钮）都走同一个动作：键盘 Enter/空格在按钮上同样冒泡到这里。
      element.addEventListener('click', function () { onAddDevice(); });
      element.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onAddDevice();
      });
      slots[index] = slot;
      return slot;
    }

    /* 坑位数量固定为 slotCount：设备更少时补空坑位，设备更多时用真实卡片顶掉空坑位。 */
    function syncSlots(deviceCount) {
      if (!host) return;
      var wanted = Math.max(0, slotCount - deviceCount);
      for (var index = 0; index < slotCount; index += 1) {
        var slot = ensureSlot(index);
        if (!slot) continue;
        slot.used = index < wanted;
        if (slot.used && !slot.element.parentNode) host.appendChild(slot.element);
        if (!slot.used && slot.element.parentNode) slot.element.parentNode.removeChild(slot.element);
        if (slot.used) {
          slot.element.style.setProperty('--mg-stage-aspect', String(PORTRAIT_ASPECT_CSS));
          applyElementWidth(slot.element, PORTRAIT_ASPECT_CSS);
        }
      }
      for (var extra = slotCount; extra < slots.length; extra += 1) {
        var stale = slots[extra];
        if (stale && stale.element && stale.element.parentNode) stale.element.parentNode.removeChild(stale.element);
      }
    }

    function ensureTile(device) {
      var tile = tiles[device.id];
      if (tile) return tile;
      if (!host) return null;
      var wrapper = document.createElement('div');
      wrapper.innerHTML = tileMarkup(device);
      var element = wrapper.firstChild;
      host.appendChild(element);
      tile = {
        deviceId: device.id,
        device: device,
        element: element,
        video: element.querySelector('.mg-video'),
        meta: element.querySelector('.mg-meta'),
        placeholder: element.querySelector('.mg-placeholder'),
        placeholderText: element.querySelector('.mg-placeholder-text'),
        overlay: element.querySelector('.mg-overlay'),
        overlayText: element.querySelector('.mg-overlay-text'),
        buttons: {
          start: element.querySelector('[data-action="start"]'),
          stop: element.querySelector('[data-action="stop"]'),
          open: element.querySelector('[data-action="open"]'),
          control: element.querySelector('[data-action="control"]')
        },
        state: 'idle',
        error: '',
        ws: null,
        jmuxer: null,
        viewerToken: '',
        clientId: '',
        transport: '',
        sequence: 0,
        sequenceSeen: false,
        lastFedSequence: 0,
        lastFedSequenceSeen: false,
        awaitingKeyframe: false,
        sourceAspect: 0,
        configGeneration: 0,
        configGenerationSeen: false,
        keyframeSeen: false,
        retryIndex: 0,
        retryTimer: null,
        frameSeen: false
      };
      tile.buttons.start.addEventListener('click', function () { startTile(tile); });
      tile.buttons.stop.addEventListener('click', function () { stopTile(tile); });
      tile.buttons.open.addEventListener('click', function () { onOpenDevice(tile.deviceId, { control: false }); });
      tile.buttons.control.addEventListener('click', function () { onOpenDevice(tile.deviceId, { control: true }); });
      tiles[device.id] = tile;
      // 屏幕区按真实画面比例自适应：竖屏手机不再被塞进 16:9 的黑框里。
      tile.video.addEventListener('loadedmetadata', function () { syncStageAspect(tile); });
      tile.video.addEventListener('resize', function () { syncStageAspect(tile); });
      syncTileWidth(tile);
      applyStageAspect(tile);
      return tile;
    }

    function syncStageAspect(tile) {
      var video = tile && tile.video;
      var width = Number(video && video.videoWidth) || 0;
      var height = Number(video && video.videoHeight) || 0;
      if (!width || !height || !tile.element) return;
      var ratio = width / height;
      if (!isFinite(ratio) || ratio <= 0) return;
      // 记住画面本身的宽高比，「统一横屏」切换回来时不必等下一帧元数据。
      tile.sourceAspect = Math.max(0.5, Math.min(2.2, ratio));
      applyStageAspect(tile);
    }

    /* 按当前方向模式写入格子比例：统一竖屏固定 9:16，跟随设备时用画面比例。
       同时写入倒数（1/比例）供旋转后的画面算宽度 —— CSS 里只用乘法，
       不用 `calc(100% / var(...))`（实测该写法在 Chrome 里不生效）。 */
    function applyStageAspect(tile) {
      if (!tile || !tile.element) return;
      var aspect = stageAspectFor(tile);
      tile.element.style.setProperty('--mg-stage-aspect', String(Math.round(aspect * 1000) / 1000));
      tile.element.style.setProperty('--mg-stage-aspect-inverse', String(Math.round((1 / aspect) * 1000) / 1000));
      applyStageRotation(tile, aspect);
      syncTileWidth(tile);
    }

    /* 画面方向与格子方向不一致时（横屏设备放进统一竖屏的格子）把画面旋转 90°，
       旋转后与格子比例仍然不合的改为铺满裁切 —— 两种情况都不留黑边。 */
    function applyStageRotation(tile, frameAspect) {
      if (!tile || !tile.element || !isFinite(frameAspect) || frameAspect <= 0) return;
      var source = Number(tile.sourceAspect) || 0;
      if (!isFinite(source) || source <= 0) {
        tile.element.classList.remove('is-rotated');
        tile.element.setAttribute('data-fit', 'contain');
        return;
      }
      var rotated = (source > 1) !== (frameAspect > 1);
      var effective = rotated ? 1 / source : source;
      var mismatch = Math.abs(effective - frameAspect) / frameAspect;
      tile.element.classList.toggle('is-rotated', rotated);
      tile.element.setAttribute('data-fit', mismatch <= STAGE_CONTAIN_TOLERANCE ? 'contain' : 'cover');
    }

    function stageAspectFor(tile) {
      if (orientation === 'device') {
        var ratio = Number(tile && tile.sourceAspect) || 0;
        return isFinite(ratio) && ratio > 0 ? ratio : UNIFORM_PORTRAIT_ASPECT;
      }
      return UNIFORM_PORTRAIT_ASPECT;
    }

    function applyStageAspects() {
      tileList().forEach(applyStageAspect);
    }

    /* 竖屏设备在放大档位下高度会失控（600px 宽 → 约 1060px 高）。这里按视口
       高度给格子宽度封顶：宽度取「滑块宽度」与「高度预算 × 画面比例」的较小值，
       画面仍然铺满格子，不会出现上下黑边。设备卡与空坑位共用同一套计算，
       同一档位下所有格子宽度一致（避免个别卡片突然比同屏其他卡片大）。 */
    function tileMinWidth() {
      if (!host) return 300;
      var px = parseFloat(host.style.getPropertyValue('--mg-tile-min') || '300px');
      return isFinite(px) && px > 0 ? px : 300;
    }

    /* 卡片除画面区之外的固定高度（卡片头 + 操作条 + 间距/内边距/边框）。
       手机端按钮是 44px，这个值会明显大于桌面，所以按真实 DOM 量。 */
    function tileChromeHeight() {
      var tile = tileList()[0];
      if (!tile || !tile.element) {
        var slot = null;
        for (var i = 0; i < slots.length; i++) { if (slots[i] && slots[i].used) { slot = slots[i]; break; } }
        tile = slot;
      }
      if (!tile || !tile.element) return TILE_CHROME_HEIGHT;
      var el = tile.element;
      var head = el.querySelector('.mg-head');
      var actions = el.querySelector('.mg-actions');
      var total = 0;
      if (head) total += head.getBoundingClientRect().height;
      if (actions) total += actions.getBoundingClientRect().height;
      var style = global.getComputedStyle ? global.getComputedStyle(el) : null;
      if (style) {
        var gap = parseFloat(style.rowGap || style.gap || '0') || 0;
        total += gap * 2;
        total += (parseFloat(style.paddingTop || '0') || 0) + (parseFloat(style.paddingBottom || '0') || 0);
        total += (parseFloat(style.borderTopWidth || '0') || 0) * 2;
      }
      return total > 24 ? total : TILE_CHROME_HEIGHT;
    }

    var budgetCache = null;
    var budgetCacheAt = 0;
    /* 可用高度预算：按**宫格可视区**高度减去卡片固定开销，保证一张卡完整可见。
       同一次渲染里所有格子必须用同一个预算（否则先渲染的格子会按旧布局算成别的宽度），
       所以这里做 250ms 缓存，resize/syncTileWidths 时强制重算。 */
    function stageHeightBudget(force) {
      var now = Date.now();
      if (!force && budgetCache !== null && now - budgetCacheAt < 250) return budgetCache;
      var measured = host && host.clientHeight ? Number(host.clientHeight) : 0;
      var viewport = Number(global.innerHeight) || 900;
      var available = measured > 0 ? measured : Math.max(220, viewport - 240);
      var budget = Math.max(60, available - tileChromeHeight());
      budgetCache = budget;
      budgetCacheAt = now;
      return budget;
    }

    function applyElementWidth(element, aspect, budget) {
      if (!element) return 0;
      var requested = tileMinWidth();
      var usable = isFinite(budget) && budget > 0 ? budget : stageHeightBudget();
      var width = requested;
      if (isFinite(aspect) && aspect > 0) {
        // aspect 是「宽/高」：可用高度 × 比例 = 该比例下不超高时的最大宽度。
        width = Math.min(requested, Math.round(usable * aspect));
      }
      width = Math.max(120, width);
      element.style.setProperty('--mg-tile-w', width + 'px');
      // 横屏手机这类「可视区只有一两百像素高」的场景：宽度已经压到下限 120px，
      // 单卡仍然放不下（120px 竖屏画面高约 213px）。再给画面区一个高度上限，
      // 保证一张卡完整可见（画面按比例缩进框内，格子里不会出现半张卡）。
      element.style.setProperty('--mg-stage-max-h', usable + 'px');
      lastTileWidth = width;
      lastTileRequestedWidth = requested;
      return width;
    }

    /* 当前实际生效的卡片宽度与滑块请求值：页面据此提示「为什么不能再大」。 */
    function tileWidthInfo() {
      return { effective: lastTileWidth, requested: lastTileRequestedWidth, budget: stageHeightBudget() };
    }

    /* 布局稳定后再同步一次宽度：切到宫格/刚渲染时 #mg-grid 的高度还没定下来，
       那一刻算出的预算是错的（实测手机上会算成下限 120px）。 */
    var widthSyncTimer = 0;
    var widthSyncRaf = 0;
    function scheduleWidthSync() {
      var raf = global.requestAnimationFrame ? global.requestAnimationFrame.bind(global) : function (fn) { return global.setTimeout(fn, 16); };
      if (widthSyncRaf) return;
      widthSyncRaf = raf(function () {
        widthSyncRaf = 0;
        syncTileWidths();
        if (widthSyncTimer) global.clearTimeout(widthSyncTimer);
        widthSyncTimer = global.setTimeout(function () { widthSyncTimer = 0; syncTileWidths(); }, 180);
      });
    }

    function syncTileWidth(tile) {
      if (!tile || !tile.element) return;
      var aspect = parseFloat(tile.element.style.getPropertyValue('--mg-stage-aspect') || '0');
      applyElementWidth(tile.element, aspect, stageHeightBudget());
    }

    function syncTileWidths() {
      // 先强制重算预算：同一次同步里所有格子用同一个值。
      var budget = stageHeightBudget(true);
      tileList().forEach(function (tile) { syncTileWidth(tile); });
      slots.forEach(function (slot) {
        if (slot && slot.used) applyElementWidth(slot.element, PORTRAIT_ASPECT_CSS, budget);
      });
      if (typeof onTileWidth === 'function') {
        try { onTileWidth(tileWidthInfo()); } catch (error) {}
      }
    }
    if (global.addEventListener) global.addEventListener('resize', function () { syncTileWidths(); scheduleWidthSync(); });
    // 宫格可视区大小变化（切视图、工具条换行、手机转屏）时重算一次。
    if (global.ResizeObserver) {
      try {
        var hostObserver = new global.ResizeObserver(function () { scheduleWidthSync(); });
        // host 在 mount() 里才赋值，这里用闭包读它。
        var observeHost = function () {
          if (!host) return false;
          hostObserver.observe(host);
          return true;
        };
        if (!observeHost()) {
          var observeTimer = global.setInterval(function () {
            if (observeHost()) global.clearInterval(observeTimer);
          }, 200);
        }
      } catch (error) {}
    }

    function setState(tile, state, message) {
      tile.state = state;
      if (message !== undefined) tile.error = message || '';
      var element = tile.element;
      element.setAttribute('data-state', state);
      var playing = state === 'playing';
      var busy = state === 'starting' || state === 'connecting';
      var offline = tile.device.online === false;
      var noPermission = tile.device.permission === false;
      element.classList.toggle('is-playing', playing);
      element.classList.toggle('is-busy', busy);
      tile.placeholder.hidden = playing || busy;
      tile.overlay.hidden = !busy && !tile.error;
      tile.buttons.start.hidden = isLive(tile) || state === 'starting';
      tile.buttons.stop.hidden = !isLive(tile) && state !== 'starting';
      tile.buttons.control.hidden = !playing;
      tile.buttons.start.disabled = offline || noPermission || busy;
      tile.buttons.stop.disabled = false;
      var placeholderText = offline ? tr('设备离线') : (noPermission ? tr('仅观看') : tr('未截图'));
      if (tile.error) placeholderText = tile.error;
      tile.placeholderText.textContent = placeholderText;
      tile.overlayText.textContent = state === 'starting' ? tr('正在开始截图…') : tr('正在连接…');
      tile.meta.textContent = statusText(tile.device);
      if (global.lucide) refreshIcons();
      syncToolbar();
    }

    /* ---------------- 播放器 ---------------- */

    function destroyPlayer(tile) {
      if (tile.jmuxer) {
        try { tile.jmuxer.destroy(); } catch (error) {}
        tile.jmuxer = null;
      }
      tile.frameSeen = false;
    }

    function ensurePlayer(tile) {
      if (tile.jmuxer) return true;
      var JMuxer = global.JMuxer;
      if (!JMuxer || !tile.video) return false;
      var configuredFps = Number(tile.device && tile.device.quality && tile.device.quality.fps);
      var fps = isFinite(configuredFps) && configuredFps > 0 ? Math.max(1, Math.min(60, Math.round(configuredFps))) : 24;
      try {
        tile.jmuxer = new JMuxer({
          node: tile.video,
          mode: 'video',
          flushingTime: 0,
          clearBuffer: true,
          maxDelay: 500,
          fps: fps,
          readFpsFromTrack: true,
          debug: false,
          onReady: function () {
            if (tile.video && typeof tile.video.play === 'function') {
              try { var playResult = tile.video.play(); if (playResult && playResult.catch) playResult.catch(function () {}); } catch (error) {}
            }
          },
          onError: function () {
            if (!isLive(tile)) return;
            destroyPlayer(tile);
            tile.keyframeSeen = false;
          }
        });
      } catch (error) {
        tile.jmuxer = null;
        return false;
      }
      return true;
    }

    /* ---------------- 数据包处理 ---------------- */

    function resetStreamState(tile) {
      tile.sequence = 0;
      tile.sequenceSeen = false;
      tile.lastFedSequence = 0;
      tile.lastFedSequenceSeen = false;
      tile.awaitingKeyframe = false;
      tile.configGeneration = 0;
      tile.configGenerationSeen = false;
      tile.keyframeSeen = false;
      destroyPlayer(tile);
    }

    function acceptPacket(tile, packet) {
      var sequence = Number(packet.sequence) >>> 0;
      var configGeneration = Number(packet.configGeneration) >>> 0;
      var newer = RawV2 ? RawV2.sequenceIsNewer : function (next, current) {
        var distance = (Number(next) - Number(current)) >>> 0;
        return distance !== 0 && distance < 0x80000000;
      };
      if (tile.sequenceSeen && !newer(sequence, tile.sequence)) return false;
      if (tile.configGenerationSeen && configGeneration !== tile.configGeneration && !newer(configGeneration, tile.configGeneration)) return false;
      var generationChanged = tile.configGenerationSeen && configGeneration !== tile.configGeneration;
      tile.sequence = sequence;
      tile.sequenceSeen = true;
      tile.configGeneration = configGeneration;
      tile.configGenerationSeen = true;
      if (packet.discontinuity || generationChanged) {
        // 只在配置真正换代（分辨率/编码参数变化）时重建播放器。
        // 服务端会把 SPS/PPS 前置到每个关键帧，因此 containsConfig 每帧都为真；
        // 若据此重建播放器，就会在每个关键帧黑屏一次（画面持续闪）。
        resetStreamState(tile);
        tile.sequence = sequence;
        tile.sequenceSeen = true;
        tile.configGeneration = configGeneration;
        tile.configGenerationSeen = true;
        if (!ensurePlayer(tile)) return false;
      }
      return true;
    }

    /* ---------------- 多端投屏记录：宫格**不参与** ----------------
       用户要求：宫格视图不参与投屏记录（宫格一路多端，混进单画面的时间线只会互相干扰）。
       做法是让服务端认得出这是宫格连接（URL 带 view=grid，见 openSocket），
       邀请/接入对齐都跳过它；这里也不再把宫格事件喂进记录时间线。 */

    function feedPacket(tile, data) {
      // 停止/错误后仍可能有在途帧到达，此时不要再重建播放器。
      if (tile.state === 'idle' || tile.state === 'error') return;
      var bytes = new Uint8Array(data);
      if (tile.transport === 'legacy-annexb') {
        if (!ensurePlayer(tile)) return;
        tile.keyframeSeen = true;
        markFrame(tile);
        try { tile.jmuxer.feed({ video: bytes, duration: 0 }); } catch (error) {}
        return;
      }
      if (!RawV2) return;
      var packet;
      try { packet = RawV2.parse(bytes); } catch (error) { return; }
      if (!acceptPacket(tile, packet)) return;
      if (!tile.keyframeSeen && !packet.keyframe) return;
      if (tile.keyframeSeen && !packet.keyframe) {
        // 观看端加入时服务端会先补发一个缓存关键帧，随后直接跳到当前进度，
        // 中间缺失的 P 帧会让解码器输出绿屏。检测到跳号就丢弃到下一个关键帧，
        // 期间保留上一帧画面（服务端因队列丢帧时也会走到这里，语义一致）。
        var expectedSequence = (tile.lastFedSequence + 1) >>> 0;
        if (tile.lastFedSequenceSeen && packet.sequence !== expectedSequence) {
          tile.awaitingKeyframe = true;
        }
      }
      if (tile.awaitingKeyframe && !packet.keyframe) return;
      if (!ensurePlayer(tile)) return;
      tile.keyframeSeen = true;
      tile.awaitingKeyframe = false;
      tile.lastFedSequence = packet.sequence;
      tile.lastFedSequenceSeen = true;
      markFrame(tile);
      try { tile.jmuxer.feed({ video: packet.payload, duration: 0, isLastVideoFrameComplete: true }); } catch (error) {}
    }

    function markFrame(tile) {
      if (tile.frameSeen) return;
      tile.frameSeen = true;
      setState(tile, 'playing');
    }

    /* ---------------- 连接生命周期 ---------------- */

    function clearRetry(tile) {
      if (tile.retryTimer) {
        global.clearTimeout(tile.retryTimer);
        tile.retryTimer = null;
      }
    }

    function closeSocket(tile) {
      if (!tile.ws) return;
      var socket = tile.ws;
      tile.ws = null;
      try { socket.close(); } catch (error) {}
    }

    function stopTile(tile, keepError) {
      clearRetry(tile);
      var token = tile.viewerToken;
      var clientId = tile.clientId;
      destroyPlayer(tile);
      tile.viewerToken = '';
      tile.clientId = '';
      tile.transport = '';
      tile.retryIndex = 0;
      resetStreamState(tile);
      if (keepError !== true) setState(tile, 'idle', '');
      if (token) {
        // 先让服务端结束本观看端（此时 WebSocket 仍在），再断开连接，
        // 否则 stop-self 会因为客户端已被移除而返回 404。
        return apiCall('sessions.viewer.stop', {
          deviceId: tile.deviceId,
          clientId: clientId,
          viewerToken: token
        }).catch(function () {}).then(function () {
          closeSocket(tile);
        });
      }
      closeSocket(tile);
      return Promise.resolve();
    }

    function startTile(tile) {
      if (!tile || isLive(tile)) return;
      if (tile.device.online !== true) { onNotice(tr('设备离线，无法投屏')); return; }
      if (tile.device.permission === false) { onNotice(tr('没有该设备的观看权限')); return; }
      if (liveCount() >= maxLive) { onNotice(tr('最多同时预览 ') + maxLive + tr(' 路，请先停止其他格子')); return; }
      setState(tile, 'starting', '');
      apiCall('sessions.viewer.start', { deviceId: tile.deviceId, thumbnail: true, thumbnailFps: thumbnailFps }).then(function (payload) {
        if (tile.state !== 'starting') return;
        var data = payload && payload.data && typeof payload.data === 'object' ? payload.data : (payload || {});
        if (data.ok === false) throw new Error(tr('服务端未启动投屏'));
        tile.viewerToken = String(data.viewer_token || '');
        openSocket(tile);
      }).catch(function (error) {
        setState(tile, 'error', (error && error.message) ? error.message : tr('开始截图失败'));
      });
    }

    function openSocket(tile) {
      setState(tile, 'connecting');
      var parts = [];
      if (tile.viewerToken) parts.push('viewer_token=' + encodeURIComponent(tile.viewerToken));
      // view=grid：服务端据此知道这是宫格观看端，投屏记录不会邀请它（宫格不参与记录）。
      parts.push('view=grid');
      if (global.ScrcpyGateV2 && typeof global.ScrcpyGateV2.browserDeviceId === 'function') {
        parts.push('browser_id=' + encodeURIComponent(global.ScrcpyGateV2.browserDeviceId()));
      }
      var url = wsBase() + '/ws/devices/' + encodeURIComponent(tile.deviceId) + '/video?' + parts.join('&');
      var socket;
      try { socket = new global.WebSocket(url); } catch (error) {
        setState(tile, 'error', tr('无法建立视频连接'));
        return;
      }
      socket.binaryType = 'arraybuffer';
      tile.ws = socket;
      socket.onopen = function () {
        if (tile.ws !== socket) return;
        tile.retryIndex = 0;
      };
      socket.onmessage = function (event) {
        if (tile.ws !== socket) return;
        if (typeof event.data === 'string') {
          var message;
          try { message = JSON.parse(event.data); } catch (error) { return; }
          if (message.type === 'hello') {
            tile.transport = String(message.video_transport || '');
            if (message.session) tile.device.quality = message.session.video || tile.device.quality;
            if (message.client_id) tile.clientId = String(message.client_id);
          } else if (message.type === 'stream_reset') {
            resetStreamState(tile);
            setState(tile, 'connecting');
          }
          // 多端投屏记录的邀请/上传请求等控制面通知在宫格里**一律忽略**（宫格不参与记录）。
          return;
        }
        feedPacket(tile, event.data);
      };
      socket.onerror = function () {
        if (tile.ws !== socket) return;
        try { socket.close(); } catch (error) {}
      };
      socket.onclose = function (event) {
        if (tile.ws !== socket) return;
        tile.ws = null;
        if (tile.state === 'idle') return;
        var code = event && Number(event.code);
        if (code === 4401 || code === 4403 || code === 4410 || code === 4411 || code === 4412) {
          setState(tile, 'error', tr('观看已结束，请重新开始投屏'));
          destroyPlayer(tile);
          tile.viewerToken = '';
          return;
        }
        if (tile.retryIndex < RECONNECT_DELAYS.length) {
          var delay = RECONNECT_DELAYS[tile.retryIndex];
          tile.retryIndex += 1;
          setState(tile, 'connecting');
          destroyPlayer(tile);
          clearRetry(tile);
          tile.retryTimer = global.setTimeout(function () {
            tile.retryTimer = null;
            if (tile.state === 'idle') return;
            openSocket(tile);
          }, delay);
          return;
        }
        setState(tile, 'error', tr('视频连接已断开'));
      };
    }

    /* ---------------- 对外接口 ---------------- */

    function mount(element, nextControls) {
      host = element || null;
      controls = nextControls || null;
      if (host) host.innerHTML = '';
      tiles = Object.create(null);
      order = [];
      slots = [];
      syncToolbar();
      scheduleWidthSync();
    }

    function render(devices) {
      if (!host) return;
      var next = Array.isArray(devices) ? devices : [];
      var seen = Object.create(null);
      next.forEach(function (device) {
        if (!device || !device.id) return;
        seen[device.id] = true;
        var tile = ensureTile(device);
        if (!tile) return;
        tile.device = device;
        if (!isLive(tile)) setState(tile, tile.state === 'error' ? 'error' : 'idle', tile.state === 'error' ? tile.error : '');
        else tile.meta.textContent = statusText(device);
      });
      order.forEach(function (id) {
        if (seen[id]) return;
        var tile = tiles[id];
        if (tile) { stopTile(tile); if (tile.element && tile.element.parentNode) tile.element.parentNode.removeChild(tile.element); }
        delete tiles[id];
      });
      order = next.filter(function (device) { return device && device.id && tiles[device.id]; }).map(function (device) { return device.id; });
      // 只在实际顺序变化时移动节点：把 <video> 在 DOM 里搬来搬去会打断解码/播放。
      order.forEach(function (id, index) {
        var tile = tiles[id];
        if (!tile || !tile.element) return;
        var current = host.children[index];
        if (current !== tile.element) host.insertBefore(tile.element, current || null);
      });
      // 空坑位永远排在设备卡之后：先按数量增删，再统一排到末尾。
      syncSlots(order.length);
      slots.forEach(function (slot) {
        if (slot && slot.used && slot.element) host.appendChild(slot.element);
      });
      if (global.lucide) refreshIcons();
      syncToolbar();
    }

    function setActive(value) {
      var next = value === true;
      if (active === next) return Promise.resolve();
      active = next;
      var stopped = Promise.resolve();
      if (!active) stopped = stopAll();
      syncToolbar();
      return stopped;
    }

    function stopAll() {
      batchToken += 1;
      var pending = [];
      order.forEach(function (id) {
        var tile = tiles[id];
        if (tile) pending.push(stopTile(tile));
      });
      syncToolbar();
      // 返回 Promise 让「打开单画面」能等所有观看端真正结束，避免缩略档位残留。
      return Promise.all(pending).catch(function () {});
    }

    /* 一键全部投屏：按列表顺序为在线且有权限的格子起流，受 maxLive 限制。 */
    function startAll() {
      if (!active) return 0;
      var room = maxLive - liveCount();
      if (room <= 0) {
        onNotice(tr('最多同时预览 ') + maxLive + tr(' 路，请先停止其他格子'));
        return 0;
      }
      var candidates = startableTiles();
      if (!candidates.length) {
        onNotice(tr('没有可投屏的设备'));
        return 0;
      }
      var chosen = candidates.slice(0, room);
      var token = ++batchToken;
      chosen.forEach(function (tile, index) {
        global.setTimeout(function () {
          if (!active || token !== batchToken || isLive(tile)) return;
          startTile(tile);
        }, index * 150);
      });
      var skipped = tileList().filter(function (tile) {
        return !isLive(tile) && !(tile.device && tile.device.online === true && tile.device.permission !== false);
      }).length;
      if (candidates.length > chosen.length) {
        onNotice(noticeText('已开始 {0} 台投屏，最多同时预览 {1} 路', [chosen.length, maxLive]));
      } else if (skipped > 0) {
        onNotice(noticeText('已开始 {0} 台投屏，{1} 台设备离线或不可用', [chosen.length, skipped]));
      }
      syncToolbar();
      return chosen.length;
    }

    /* 一键刷新画面：重连正在投屏或出错的格子，未投屏的格子保持不动。 */
    function refreshAll() {
      if (!active) return 0;
      var targets = restartableTiles();
      if (!targets.length) {
        onNotice(tr('没有正在投屏的格子'));
        return 0;
      }
      var token = ++batchToken;
      var ids = targets.map(function (tile) { return tile.deviceId; });
      targets.forEach(function (tile) { stopTile(tile); });
      global.setTimeout(function () {
        if (!active || token !== batchToken) return;
        ids.forEach(function (deviceId, index) {
          global.setTimeout(function () {
            if (!active || token !== batchToken) return;
            var tile = tiles[deviceId];
            if (tile && !isLive(tile)) startTile(tile);
          }, index * 150);
        });
      }, 450);
      syncToolbar();
      return ids.length;
    }

    function destroy() {
      batchToken += 1;
      stopAll();
      if (host) host.innerHTML = '';
      tiles = Object.create(null);
      order = [];
      slots = [];
      host = null;
      controls = null;
      active = false;
    }

    function setMaxLive(value) {
      var next = Number(value);
      if (!isFinite(next) || next < 1) return maxLive;
      maxLive = Math.floor(next);
      if (api) api.maxLive = maxLive;
      syncToolbar();
      return maxLive;
    }

    function setThumbnailFps(value) {
      var next = Number(value);
      if (!isFinite(next)) return thumbnailFps;
      thumbnailFps = Math.max(1, Math.min(30, Math.round(next)));
      return thumbnailFps;
    }

    function setOrientation(value) {
      orientation = value === 'device' ? 'device' : 'portrait';
      applyStageAspects();
      return orientation;
    }

    var api = {
      mount: mount,
      render: render,
      setActive: setActive,
      stopAll: stopAll,
      startAll: startAll,
      refreshAll: refreshAll,
      setMaxLive: setMaxLive,
      setThumbnailFps: setThumbnailFps,
      setOrientation: setOrientation,
      syncTileWidths: syncTileWidths,
      tileWidth: tileWidthInfo,
      destroy: destroy,
      liveCount: liveCount,
      isLive: function (deviceId) { return isLive(tiles[deviceId]); },
      maxLive: maxLive,
      slotCount: slotCount
    };
    return api;
  }

  global.ScrcpyGateMirrorGrid = { create: create, DEFAULT_MAX_LIVE: DEFAULT_MAX_LIVE, DEFAULT_SLOT_COUNT: DEFAULT_SLOT_COUNT };
})(typeof window !== 'undefined' ? window : this);
