/**
 * ScrcpyGate v2 数据适配层(数据边界)
 *
 * 职责:
 * - 拦截 window.ScrcpyGateApi.configured/get/post/put/patch/del,把 A 仓库页面的
 *   resource.action 契约桥接到 v2 FastAPI 端点(ENDPOINTS_MAP 之外的动态端点在此定义);
 * - 归一化响应形状(devices/users/sessions/quality/alas/logs/audit);
 * - 投屏工作台管道: /ws/devices/{id}/video(Raw v2 H.264 → JMuxer)与
 *   /ws/devices/{id}/control(控制协议 + 30s keepalive);
 * - 全局退出登录委托(.menu-item.logout / .menu-item.danger)。
 *
 * 本文件属于 FastAPI 项目侧(static/shared),不在前端仓库维护。
 */
(function (window, document) {
  'use strict';

  var Api = window.ScrcpyGateApi;
  if (!Api || !window.ScrcpyGateConfig) return;

  var BUILTIN_PRESETS = ['smooth', 'balanced', 'sharp', 'low_latency'];
  var RESOLUTION_CAPS = [
    { value: 640, label: '360p', description: '640 x 360' },
    { value: 854, label: '480p', description: '854 x 480' },
    { value: 960, label: '540p', description: '960 x 540' },
    { value: 1280, label: '720p', description: '1280 x 720' },
    { value: 1600, label: '900p', description: '1600 x 900' },
    { value: 1920, label: '1080p', description: '1920 x 1080' }
  ];

  var state = {
    session: null,          // 当前投屏会话(适配后)
    deviceId: null,         // 当前设备公共 id
    videoSocketDevice: '',  // 视频通道当前绑定的设备公共 id(换设备时据此重置控制)
    viewerToken: null,
    videoSocket: null,
    controlSocket: null,
    controlSocketDevice: '',// 控制通道当前绑定的设备公共 id(绝不复用其它设备的控制通道)
    controlOwnership: false,
    controlKeepaliveTimer: null,
    controlCurrentUser: '',
    scrcpyInput: null,
    keyboardOn: false,
    jmuxer: null,
    videoEl: null,
    videoRotation: 0,
    fullscreenMode: false,
    videoWidth: 0,
    videoHeight: 0,
    videoDimensionsAuthoritative: false,
    videoSurfaceReady: false,
    videoFrameReady: false,
    videoPlaying: false,
    deviceNameMap: {},      // 设备名 -> { publicId, internalId, address }
    deviceRefMap: {},       // 公共/内部设备 ID -> { publicId, internalId, address, name }
    adminDeviceCache: [],
    publicDevices: [],
    publicDevicesStale: false,
    publicDevicesStaleAt: 0,
    userRole: '',
    workbenchAlasVisible: true,
    workbenchFeatures: null,   // 投屏管理下发的功能开关；null = 未知（全部启用）
    workbenchLayout: null,     // 投屏管理下发的底部菜单编排；null = 未知（默认排布）
    alasRelationsMap: {},   // relationId -> { username, configName }
    pendingControl: null,   // { resolve, reject, timer }
    watchActive: false,     // 观看意图:断线后据此自动重连
    videoRetryTimer: null,
    videoRetryCount: 0,
    rawV2Sequence: 0,
    rawV2SequenceSeen: false,
    rawV2KeyframeSeen: false,
    // 关键帧门控：新解码器（刚重建/刚连接）或序列跳号之后，必须先收到关键帧才能
    // 继续喂包。把缺少参考帧的 P 帧喂进解码器会直接输出黑/绿屏（宫格
    // mirror-grid.js 有同样的门控，这里是同一语义）。
    rawV2AwaitingKeyframe: false,
    rawV2LastFedSequence: 0,
    rawV2LastFedSequenceSeen: false,
    rawV2KeyframeRequestAt: 0,
    // 投屏实时记录：事件环形缓冲 + 订阅者（管理员面板实时渲染）。
    videoRecord: [],
    videoRecordSeq: 0,
    videoRecordSubs: [],
    videoRecordFirstAt: 0,
    videoRecordLastRateAt: 0,
    videoRecordLastRateMbps: null,
    videoRecordLastSize: '',
    // 多端投屏记录（管理员发起、邀请同设备的其他观看端；只经内存中继，不落盘）。
    recordArmed: false,      // 本端是否在记录（管理员，或已同意参与的观看端）
    recordInvite: null,      // 待响应的邀请
    recordInviteClientId: '', // 收到邀请的那个观看端（宫格视图一页多端时必须按它响应）
    recordSession: null,     // 当前多端记录会话（发起端与参与端都会拿到）
    recordRole: '',          // 'initiator' = 主端（可停止）/ 'participant' = 副端（只能参与或退出）
    recordBundles: [],       // 收到的其他端完整记录
    rawV2ConfigGeneration: 0,
    rawV2ConfigGenerationSeen: false,
    rawV2Transport: '',
    rawV2PacketUnit: '',
    rawV2ProtocolVersion: 0,
    videoRateBytes: 0,      // 当前统计窗口内收到的视频字节数
    videoRateFrames: 0,     // 当前统计窗口内收到的帧数（算实测帧率）
    videoFps: 0,            // 平滑后的实测帧率
    browserDeviceId: '',    // 本浏览器的稳定设备标识（localStorage）
    videoRaw: [],           // 原始数据层（ws-in / ws-out / packet / api / state / env）
    videoRawSeq: 0,
    videoRawBytes: 0,
    videoRawDropped: 0,
    videoRawFirstAt: 0,
    videoRawSubs: null,
    videoRateMbps: 0,       // 最近一次实测码率(Mbps, 一位小数)
    videoRateTimer: null,
    playerRecoveryTimer: null,
    firstFrameTimer: null,
    firstFrameRecoveryCount: 0,
    videoFrameRevealTimer: null,
    videoFrameCallbackId: null,
    videoRevealBaseline: null,
    videoRevealFallbackTicks: 0,
    videoRevealHoldTimer: null,
    videoRevealFirstFrameAt: null,
    videoRevealMaxWaitTimer: null,
    staleVideoEl: null,
    staleVideoRetireTimer: null,
    videoOrientation: '',
    pendingControlMove: null,
    controlMoveRetryTimer: null,
    videoLatencyTimer: null,
    videoLatencyLastSeekAt: 0,
    videoLatencyRecovering: false,
    viewerStopSettings: {
      viewer_hidden_stop_enabled: true,
      viewer_hidden_stop_minutes: 5,
      viewer_blur_stop_enabled: false,
      viewer_blur_stop_minutes: 5,
      viewer_stop_settings_synced: false
    },
    viewerStopMode: null,
    viewerStopStartedAt: 0,
    viewerStopWarningAt: 0,
    viewerStopDeadlineAt: 0,
    viewerStopTimer: null,
    viewerStopWarningShown: false,
    viewerStopTerminal: false,
    viewerStopSettingsLoaded: false,
    authInvalid: false,
    windowFocused: typeof document.hasFocus === 'function' ? document.hasFocus() : true
  };

  var VIEWER_STOP_WARNING_MS = 60 * 1000;
  var VIEWER_STOP_DEFAULTS = {
    viewer_hidden_stop_enabled: true,
    viewer_hidden_stop_minutes: 5,
    viewer_blur_stop_enabled: false,
    viewer_blur_stop_minutes: 5,
    viewer_stop_settings_synced: false
  };
  // Keep MSE catch-up from turning normal append jitter into a seek loop.
  // The lower threshold clears recovery hysteresis after the playhead is near
  // the live tail; the upper threshold is the only point at which a seek is
  // allowed.  Values are deliberately bounded and protocol-independent.
  //
  // 这些播放端旋钮按**预设档位**取值：默认档严格保持历史数值（回归风险为零），
  // 只有明确选了 low_latency 的会话才把 MSE 尾巴与追赶阈值一起收紧 —— 用「网络抖动时
  // 更容易出现轻微卡顿」换更低的端到端延迟。这正是「低延迟」这个名字应该代表的东西：
  // 服务端的 i-frame-interval=1 / raw 传输 / tcp_nodelay 已经全档位拉满、没有剩余空间，
  // 播放缓冲是唯一还有实际收益的旋钮。
  var DEFAULT_LATENCY_TUNING = {
    maxDelayMs: 500,
    seekThresholdSeconds: 0.5,
    recoverySeconds: 0.12,
    seekCooldownMs: 1000
  };
  var PROFILE_LATENCY_TUNING = {
    low_latency: { maxDelayMs: 250, seekThresholdSeconds: 0.25, recoverySeconds: 0.06, seekCooldownMs: 800 }
  };
  function latencyTuningFor(profile) {
    var override = PROFILE_LATENCY_TUNING[String(profile || '').trim().toLowerCase()];
    if (!override) return DEFAULT_LATENCY_TUNING;
    return {
      maxDelayMs: override.maxDelayMs,
      seekThresholdSeconds: override.seekThresholdSeconds,
      recoverySeconds: override.recoverySeconds,
      seekCooldownMs: override.seekCooldownMs
    };
  }
  // 档位可能在会话存续期间改变（换画质会重建会话），所以按需重算并缓存。
  function currentLatencyTuning() {
    var profile = state.session && state.session.video ? state.session.video.profile : '';
    var key = String(profile || '').trim().toLowerCase();
    if (state.latencyTuning && state.latencyTuningKey === key) return state.latencyTuning;
    state.latencyTuningKey = key;
    state.latencyTuning = latencyTuningFor(key);
    return state.latencyTuning;
  }
  // 稳定性优先：解码器刚启动（或刚重建）时可能先输出一帧绿色/残缺画面。
  // 关键帧到达后至少再等这么多帧，并额外保持一小段时间，才把画面显示出来。
  var VIDEO_REVEAL_MIN_FRAMES = 2;
  var VIDEO_REVEAL_HOLD_MS = 120;
  // 静态画面可能长时间不产生新帧；超过这个等待上限就照常显示，避免卡在等待态。
  var VIDEO_REVEAL_MAX_WAIT_MS = 600;
  // 设备端转屏会让画面尺寸/朝向突变；用过渡把它变成平滑的旋转动画。
  var VIDEO_LAYOUT_ANIMATION_MS = 300;
  var VIDEO_LAYOUT_TRANSITION = 'width ' + VIDEO_LAYOUT_ANIMATION_MS + 'ms cubic-bezier(.22,1,.36,1),'
    + 'height ' + VIDEO_LAYOUT_ANIMATION_MS + 'ms cubic-bezier(.22,1,.36,1),'
    + 'transform ' + VIDEO_LAYOUT_ANIMATION_MS + 'ms cubic-bezier(.22,1,.36,1),opacity 80ms linear';
  // 重建解码器期间保留上一帧，避免黑屏断开。
  var VIDEO_STALE_FADE_MS = 180;
  // 保留的上一帧最迟保留这么久：新解码器迟迟拿不到关键帧时，宁可露出黑屏也不能
  // 让一张旧画面无限期停在最上层（用户会以为画面卡死）。
  var VIDEO_STALE_MAX_WAIT_MS = 8000;
  // 需要新关键帧时向服务端发 player_reset 的最小间隔（服务端 VIDEO_RESET_COOLDOWN
  // 是 0.75s，这里略大一点，避免把无谓的控制消息打到设备上）。
  var VIDEO_KEYFRAME_REQUEST_INTERVAL_MS = 900;
  // 投屏诊断时间线保存在浏览器内存中；同意参与多端记录后可上传给发起端汇总。
  // 保留最近 800 条事件；超过上限时移除最早的事件。
  var VIDEO_RECORD_LIMIT = 800;
  var VIDEO_RECORD_RATE_REPORT_MS = 60000;   // 码率样本最短间隔
  var VIDEO_RECORD_RATE_DELTA_MBPS = 1;      // 码率变化超过这个值也记一条

  // Control packets are tiny, but a slow mobile uplink can briefly build a
  // browser WebSocket buffer. Keep only the newest move while preserving
  // key/text/press/release packets, then retry without waiting for a repaint.
  var CONTROL_MOVE_BUFFER_LIMIT = 16384;
  var CONTROL_MOVE_RETRY_MS = 8;

  /* ---------------- 基础工具 ---------------- */

  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function timestampDate(value) {
    if (value === null || value === undefined || value === '') return null;
    var raw = String(value).trim();
    if (/^[+-]?\d+(?:\.\d+)?$/.test(raw)) {
      var numeric = Number(raw);
      if (!isFinite(numeric)) return null;
      // API timestamps are normally epoch seconds; accept epoch milliseconds
      // as well so mixed proxy/database payloads never render year 58600.
      return new Date(Math.abs(numeric) >= 1000000000000 ? numeric : numeric * 1000);
    }
    var date = new Date(raw.replace(' ', 'T'));
    return isNaN(date.getTime()) ? null : date;
  }
  function fmtTs(ts) {
    if (ts === null || ts === undefined || ts === '') return '—';
    var d = timestampDate(ts);
    if (!d) return String(ts);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }
  function dateStr(ts) {
    if (ts === null || ts === undefined || ts === '') return '';
    var d = timestampDate(ts);
    if (!d) return '';
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }
  function toEpoch(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'number' || /^[+-]?\d+(?:\.\d+)?$/.test(String(value).trim())) {
      var numeric = Number(value);
      if (!isFinite(numeric)) return null;
      return Math.floor(Math.abs(numeric) >= 1000000000000 ? numeric / 1000 : numeric);
    }
    var m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return Math.floor(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() / 1000);
    var d = new Date(String(value).replace(' ', 'T'));
    return isNaN(d.getTime()) ? null : Math.floor(d.getTime() / 1000);
  }
  /* 账户到期用的是「日历日」语义：选 2026-03-10 表示保到当天结束。
     服务端判定 expires_at <= now，若按本地零点发送，当天 00:00 就失效、
     界面还写着「今天到期」。这里按本地 23:59:59 发送（其它输入仍走 toEpoch）。 */
  function expiryEpoch(value) {
    var m = String(value == null ? '' : value).trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return toEpoch(value);
    return Math.floor(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59).getTime() / 1000);
  }
  function nowTs() { return Math.floor(Date.now() / 1000); }
  /* 「剩余天数」必须按日历日算，不能拿时长去除 86400：
     到期时间按本地 23:59:59 存（见 expiryEpoch），时长里总带一个不足一天的零头，
     Math.ceil 会把 30 天读成 31 天（编辑弹窗按日历日算，于是列表比弹窗多一天）。
     取本地年月日做差，跨 DST 与「按零点存储的旧数据」都稳定。 */
  function localDayIndex(date) {
    return Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
  }
  function calendarDaysUntil(value) {
    var date = timestampDate(value);
    return date ? localDayIndex(date) - localDayIndex(new Date()) : null;
  }
  function timeRangeBounds(timeRange, from, to) {
    var out = { fromTs: null, toTs: null };
    var toTs = to ? toEpoch(to) + 86399 : null;
    if (timeRange === '24h') { out.fromTs = nowTs() - 86400; out.toTs = toTs; }
    else if (timeRange === 'today') { out.fromTs = toEpoch(dateStr(nowTs())); out.toTs = toTs; }
    else if (timeRange === '7d') { out.fromTs = nowTs() - 7 * 86400; out.toTs = toTs; }
    else if (timeRange === '30d') { out.fromTs = nowTs() - 30 * 86400; out.toTs = toTs; }
    else if (timeRange === 'custom') { out.fromTs = from ? toEpoch(from) : null; out.toTs = to ? toEpoch(to) + 86399 : null; }
    return out;
  }
  function withinTimeBounds(ts, bounds) {
    if (!bounds || (bounds.fromTs === null && bounds.toTs === null)) return true;
    var t = toEpoch(ts);
    if (t === null) return true;
    if (bounds.fromTs !== null && t < bounds.fromTs) return false;
    if (bounds.toTs !== null && t > bounds.toTs) return false;
    return true;
  }
  function normalizeLogLevel(level) {
    var v = String(level || 'info').toLowerCase();
    if (v === 'warning') return 'warn';
    if (v === 'critical' || v === 'fatal') return 'error';
    return v;
  }
  function hasText(hay, needle) {
    if (!needle) return true;
    return String(hay || '').toLowerCase().indexOf(String(needle).toLowerCase()) !== -1;
  }
  function updateCsrfMeta(token) {
    if (!token) return;
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) meta.setAttribute('content', token);
  }
  function wsBase() {
    var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    return proto + window.location.host;
  }
  function b64encode(bytes) {
    var bin = '';
    var chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
    }
    return window.btoa(bin);
  }

  function apiRequest(path, opts) {
    opts = opts || {};
    var startedAt = Date.now();
    var method = String(opts.method || 'GET').toUpperCase();
    var body = opts.body;
    // Credential forms must never enter optional mirroring diagnostic exports.
    var recordedBody = /^\/api\/admin\/geo\/(credentials|downloads)(?:\?|$)/.test(path)
      ? null : body === undefined ? null : body;
    return Api.request(path, {
      method: method,
      query: opts.query,
      body: body,
      timeout: opts.timeout || 20000,
      force: opts.force === true,
      cache: opts.cache,
      coalesce: opts.coalesce
    }).then(function (payload) {
      rawRecord('api', method + ' ' + path, {
        method: method,
        url: path,
        query: opts.query || null,
        body: recordedBody,
        ok: true,
        status: Number(payload && payload.status_code) || 200,
        ms: Date.now() - startedAt,
        // 响应原文只对投屏/记录相关接口保留（设备列表之类的整页数据太占地方）。
        response: apiRawResponseWanted(path) ? truncateRaw(payload) : null
      });
      return payload;
    }).catch(function (error) {
      rawRecord('api', method + ' ' + path + ' FAILED', {
        method: method,
        url: path,
        query: opts.query || null,
        body: recordedBody,
        ok: false,
        status: httpStatus(error),
        ms: Date.now() - startedAt,
        error: String((error && (error.message || error.detail)) || error)
      });
      throw error;
    });
  }

  function apiRawResponseWanted(path) {
    var text = String(path || '');
    return text.indexOf('/mirror/') >= 0 || text.indexOf('/record') >= 0 || text.indexOf('/ws') >= 0;
  }

  function truncateRaw(value, limit) {
    var max = limit || 4000;
    try {
      var text = typeof value === 'string' ? value : JSON.stringify(value);
      if (text == null) return null;
      return text.length > max ? (text.slice(0, max) + '…(truncated ' + text.length + ')') : text;
    } catch (e) {
      return null;
    }
  }

  function apiGet(path, query) { return apiRequest(path, { query: query }); }
  function apiPost(path, body) { return apiRequest(path, { method: 'POST', body: body }); }
  function apiPut(path, body) { return apiRequest(path, { method: 'PUT', body: body }); }
  function apiDelete(path) { return apiRequest(path, { method: 'DELETE' }); }

  function httpStatus(error) {
    return Number(error && error.detail && error.detail.status) || 0;
  }
  function requireSuccessful(payload, fallback) {
    if (!payload || payload.ok !== false) return payload;
    var detail = payload.error || payload.detail || fallback || '请求未完成';
    var err = new Error(detail);
    err.code = 'HTTP_ERROR';
    err.name = 'ScrcpyGateApiError';
    err.detail = { status: Number(payload.status_code) || 502, payload: { detail: detail } };
    throw err;
  }

  /* ---------------- 审计/日志工具 ---------------- */

  // Audit labels live in one browser owner. The adapter keeps these wrappers so
  // legacy callers and the public ScrcpyGateV2 surface do not change.
  function auditMappingOwner() {
    return window.ScrcpyGateAuditMapping || window.ScrcpyGateDashboard || {};
  }
  function auditRecord(value, field) {
    if (value && typeof value === 'object') return value;
    var record = {};
    record[field || 'action'] = value;
    return record;
  }
  function auditIsEnglish() {
    var owner = auditMappingOwner();
    if (typeof owner.isEnglish === 'function') return !!owner.isEnglish();
    return !!(window.ScrcpyGateI18n && typeof window.ScrcpyGateI18n.getLang === 'function' &&
      window.ScrcpyGateI18n.getLang() === 'en-US');
  }
  function auditPick(zh, en) { return auditIsEnglish() ? (en || zh) : zh; }
  function auditRecordAction(record) {
    var value = record && typeof record === 'object' ? record : {};
    return value.action || value.action_code || value.event || value.event_name ||
      value.actionLabel || value.action_label || '';
  }
  function auditActionKey(action) {
    var owner = auditMappingOwner();
    var record = auditRecord(action, 'action');
    if (typeof owner.actionKey === 'function') return owner.actionKey(record) || '';
    if (typeof owner.canonicalAction === 'function') return owner.canonicalAction(auditRecordAction(record)) || '';
    return String(auditRecordAction(record) || '').trim().toLowerCase()
      .replace(/[\s./:-]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  }
  function reverseAuditActionLabel(value) {
    var owner = auditMappingOwner();
    if (typeof owner.reverseActionLabel === 'function') return owner.reverseActionLabel(value) || '';
    return '';
  }
  function auditIsAlertRecord(record) {
    var owner = auditMappingOwner();
    var value = auditRecord(record, 'action');
    if (typeof owner.isAlert === 'function') return !!owner.isAlert(value);
    if (typeof owner.isAlertRecord === 'function') return !!owner.isAlertRecord(value);
    var severity = String(value.severity || value.level || '').trim().toLowerCase();
    var outcome = String(value.result || value.outcome || value.status || '').trim().toLowerCase();
    return ['warning', 'warn', 'error', 'critical', 'high', 'fatal', 'fail', 'failed', 'failure',
      'denied', 'blocked', 'timeout', 'timed_out'].indexOf(severity || outcome) >= 0;
  }
  function auditActionLabel(action) {
    var owner = auditMappingOwner();
    var value = auditRecord(action, 'action');
    if (typeof owner.title === 'function') return owner.title(value) || auditPick('审计事件', 'Audit event');
    return auditPick('审计事件', 'Audit event');
  }
  function auditCategory(value) {
    var owner = auditMappingOwner();
    var record = auditRecord(value, 'action');
    if (typeof owner.category === 'function') return owner.category(record) || 'admin';
    return 'admin';
  }
  function auditCategoryLabel(record) {
    var owner = auditMappingOwner();
    var value = auditRecord(record, 'action');
    if (typeof owner.categoryLabel === 'function') {
      return owner.categoryLabel(value) || auditPick('管理员操作', 'Administrator actions');
    }
    return auditPick('管理员操作', 'Administrator actions');
  }
  function auditReasonLabel(record) {
    var owner = auditMappingOwner();
    var value = auditRecord(record, 'reason');
    if (typeof owner.reason === 'function') return owner.reason(value) || '';
    if (typeof owner.reasonLabel === 'function') return owner.reasonLabel(value) || '';
    return '';
  }
  function auditReadableText(value) {
    var owner = auditMappingOwner();
    if (typeof owner.readableText === 'function') return owner.readableText(value) || '';
    var text = String(value == null ? '' : value).trim();
    return /^[a-z0-9]+(?:[_.:-][a-z0-9]+)*(?:成功|失败|错误|拒绝|阻止|超时)?$/i.test(text) ? '' : text;
  }
  function auditRecordTitle(record) {
    var owner = auditMappingOwner();
    var value = auditRecord(record, 'action');
    if (typeof owner.title === 'function') return owner.title(value) || auditPick('系统操作', 'System action');
    return auditActionLabel(auditRecordAction(value));
  }
  function normOutcome(outcome, action, severity) {
    var owner = auditMappingOwner();
    var record = { action: action, outcome: outcome, result: outcome, severity: severity, level: severity };
    var mapped = '';
    if (typeof owner.displayResult === 'function') mapped = owner.displayResult(record);
    else if (typeof owner.result === 'function') mapped = owner.result(record);
    else mapped = outcome;
    var key = String(mapped == null ? '' : mapped).trim().toLowerCase();
    if (['success', 'successful', 'successfully', 'ok', 'passed', '通过', '成功'].indexOf(key) >= 0) return 'success';
    if (['failure', 'failed', 'error', 'abnormal', '异常', '不正常', '失败', '错误'].indexOf(key) >= 0) return 'fail';
    if (key === 'timed_out') return 'timeout';
    if (['denied', 'blocked', 'timeout', 'pending', 'cancelled', 'canceled', 'unknown'].indexOf(key) >= 0) return key;
    return key || 'unknown';
  }
  var RES_LABEL = {
    success: '成功', fail: '失败', failure: '失败', denied: '拒绝', blocked: '阻止',
    timeout: '超时', pending: '处理中', cancelled: '已取消', canceled: '已取消', unknown: '未知'
  };
  var SRC_LABEL = { auth: '认证', account: '账户', device: '设备', quality: '画质传输', alas: 'ALAS', system: '系统' };
  var SRC_ICON = { auth: 'key', account: 'user', device: 'smartphone', quality: 'sliders-horizontal', alas: 'bot', system: 'settings' };
  function runtimeEventLabel(value) {
    var owner = auditMappingOwner();
    if (typeof owner.runtimeEventLabel === 'function') {
      return owner.runtimeEventLabel(value) || auditPick('系统运行事件', 'Runtime event');
    }
    return auditPick('系统运行事件', 'Runtime event');
  }
  function sourceOfEntry(entry) {
    var value = entry || {};
    var hay = String((value.logger || '') + ' ' + (value.event_name || '') + ' ' + (value.message || '')).toLowerCase();
    if (hay.indexOf('alas') >= 0) return 'alas';
    if (hay.indexOf('video') >= 0 || hay.indexOf('mirror') >= 0 || hay.indexOf('h264') >= 0 || hay.indexOf('stream') >= 0) return 'quality';
    if (hay.indexOf('device') >= 0 || hay.indexOf('adb') >= 0) return 'device';
    if (hay.indexOf('auth') >= 0 || hay.indexOf('login') >= 0 || hay.indexOf('security') >= 0 || hay.indexOf('csrf') >= 0) return 'auth';
    if (hay.indexOf('user') >= 0 || hay.indexOf('account') >= 0 || hay.indexOf('audit') >= 0) return 'account';
    return 'system';
  }

  function adaptAuditRecord(rec) {
    rec = rec && typeof rec === 'object' ? rec : {};
    var action = auditRecordAction(rec);
    var timestamp = rec.ts != null ? rec.ts : (rec.timestamp != null ? rec.timestamp : (rec.created_at != null ? rec.created_at : rec.createdAt));
    var username = rec.username || rec.user_name || rec.operator || rec.actor_name || rec.actorName || '';
    var reason = rec.reason || rec.error_code || rec.failure_reason || '';
    var outcome = normOutcome(rec.outcome || rec.result || rec.status || rec.outcome_code || rec.result_code, action, rec.severity || rec.level);
    var displayRecord = Object.assign({}, rec, {
      action: action,
      outcome: outcome,
      result: outcome,
      reason: reason,
      username: username,
      isAlert: auditIsAlertRecord(rec)
    });
    var cat = auditCategory(displayRecord);
    var actionLabel = auditRecordTitle(displayRecord);
    var categoryLabel = auditCategoryLabel(displayRecord);
    var reasonLabel = auditReasonLabel(displayRecord);
    var detail = rec.detail || rec.summary || rec.message || '';
    var displayDetail = auditReadableText(detail) || reasonLabel || actionLabel;
    var message = displayDetail;
    var id = rec.id || rec.event_id || rec.eventId || '';
    var raw = '';
    try { raw = JSON.stringify(rec); } catch (e) { raw = String(id || ''); }
    return {
      id: id,
      eventId: rec.event_id || rec.eventId || id,
      time: fmtTs(timestamp),
      ts: timestamp,
      createdAt: fmtTs(timestamp),
      operator: username,
      operatorId: rec.operator_id || username,
      operatorLabel: username || '未认证用户',
      actor: username,
      actorName: username,
      actorRole: rec.actor_role || rec.actorRole || '',
      ip: rec.source_ip || rec.sourceIp || rec.ip || '',
      sourceIp: rec.source_ip || rec.sourceIp || rec.ip || '',
      category: cat,
      cat: cat,
      eventType: cat,
      categoryLabel: categoryLabel,
      catLabel: categoryLabel,
      action: action,
      actionCode: action,
      actionLabel: actionLabel,
      event: rec.event || rec.event_name || action,
      target: rec.target_id || rec.targetId || rec.target || rec.object || rec.resource || '',
      targetId: rec.target_id || rec.targetId || rec.target || '',
      targetType: rec.target_type || rec.targetType || '',
      result: outcome,
      outcome: outcome,
      resultLabel: window.ScrcpyGateDashboard && typeof window.ScrcpyGateDashboard.resultLabel === 'function'
        ? window.ScrcpyGateDashboard.resultLabel(displayRecord)
        : (RES_LABEL[outcome] || outcome),
      level: normalizeLogLevel(rec.severity || rec.level),
      severity: rec.severity || rec.level || 'info',
      reason: reason,
      reasonLabel: reasonLabel,
      detail: rec.detail || '',
      displayDetail: displayDetail,
      message: message || actionLabel,
      desc: message || actionLabel,
      sessionId: rec.request_id || rec.requestId || rec.session_id || rec.sessionId || '',
      requestId: rec.request_id || rec.requestId || '',
      userAgent: rec.user_agent || rec.userAgent || '',
      schemaVersion: rec.schema_version || rec.schemaVersion,
      metadata: rec.metadata && typeof rec.metadata === 'object' ? rec.metadata : {},
      sequenceId: rec.sequence_id || rec.sequenceId,
      previousHash: rec.previous_hash || rec.previousHash || '',
      eventHash: rec.event_hash || rec.eventHash || '',
      dedupeKey: rec.dedupe_key || rec.dedupeKey || '',
      raw: raw,
      isAlert: auditIsAlertRecord(displayRecord)
    };
  }

  function adaptRuntimeEntry(e) {
    var attrs = e.attributes || {};
    var source = sourceOfEntry(e);
    var message = auditReadableText(e.message || '');
    var eventLabel = runtimeEventLabel(e.event_name || e.event || '');
    var summary = message || eventLabel;
    var raw = e.raw || e.message || '';
    var deviceVal = attrs.device_id || attrs.device || attrs.deviceId || '';
    var userVal = attrs.username || attrs.user || attrs.user_id || '';
    return {
      time: fmtTs(e.timestamp),
      timestamp: e.timestamp,
      level: normalizeLogLevel(e.severity),
      severity: e.severity,
      source: source,
      sourceLabel: SRC_LABEL[source] || source,
      sourceIcon: SRC_ICON[source] || 'file-text',
      device: deviceVal ? String(deviceVal) : 'none',
      deviceId: deviceVal ? String(deviceVal) : 'none',
      deviceLabel: deviceVal ? String(deviceVal) : '无设备',
      user: userVal ? String(userVal) : 'none',
      userId: userVal ? String(userVal) : 'none',
      userLabel: userVal ? String(userVal) : '无用户',
      ip: attrs.ip || attrs.client_ip || '—',
      sessionId: attrs.session || attrs.session_id || '—',
      eventId: e.request_id || e.event_name || '—',
      summary: summary || '—',
      message: message || summary,
      eventLabel: eventLabel,
      event: e.event_name,
      raw: raw || '—'
    };
  }

  function adaptAlertRecord(rec) {
    rec = rec || {};
    var adapted = adaptAuditRecord(rec);
    adapted.handled = !!(rec.handled || rec.handled_at);
    adapted.handledAt = rec.handled_at ? fmtTs(rec.handled_at) : '';
    adapted.handledBy = rec.handled_by || '';
    adapted.pending = !adapted.handled;
    adapted.alertId = rec.event_id || rec.eventId || rec.id || '';
    adapted.alertType = rec.alert_type || rec.alertType || (adapted.isAlert ? 'service_failure' : '');
    // Do not synthesize an alert title from the triggering action. Legacy
    // records often carry "开始投屏" while the actual incident is in
    // reason/alert_type (for example "设备离线").
    adapted.title = auditRecordTitle(Object.assign({}, rec, { title: rec.title || '', action: rec.action || rec.action_code || rec.event_name || adapted.action }));
    adapted.displaySummary = auditReadableText(rec.summary || rec.detail || '') || adapted.reasonLabel || adapted.title || auditPick('请查看日志了解详情', 'See the logs for details');
    adapted.summary = adapted.displaySummary;
    adapted.logUrl = rec.log_url || rec.logUrl || (adapted.alertId ? '/logs?event_id=' + encodeURIComponent(adapted.alertId) : '/logs');
    adapted.dashboardVisible = rec.dashboard_visible !== false;
    adapted.shortSummary = adapted.summary;
    return adapted;
  }

  /* ---------------- 画质 ---------------- */

  function bpsToMbps(bps) { return Math.round((Number(bps) || 0) / 100000) / 10; }
  function profileWidth(maxSize) { return Number(maxSize) || 1280; }
  function profileHeight(maxSize) { return Math.round(profileWidth(maxSize) * 9 / 16); }
  // max_size 限制的是最长边,画质标签按短边命名(1280 -> 720p),与画质设置页的档位名称一致。
  function profileLabel(maxSize) { var height = profileHeight(maxSize); return height > 0 ? height + 'p' : ''; }
  // 预设帧率由服务端限制在 15..240(与画质页三个输入框的 min/max 一致),所以 0 只可能
  // 来自字段缺失或早期数据。这里只在"没有帧率"时兜底为 24,不再把 0 显示成一个帧率。
  function presetFps(value) { var fps = Number(value); return isFinite(fps) && fps > 0 ? fps : 24; }
  function profileDisplayName(profile, details) {
    var key = String(profile || '').trim();
    var labels = { smooth: '流畅', balanced: '均衡', sharp: '高清', low_latency: '低延迟' };
    if (details && details.label) return String(details.label);
    if (labels[key]) return labels[key];
    if (!key || key === 'custom') return '自定义';
    if (key.indexOf('custom_') === 0) {
      var spec = details || {};
      var size = Number(spec.max_size || 0);
      var fps = Number(spec.max_fps || 0);
      if (size && fps) return '自定义 ' + profileLabel(size) + ' · ' + fps + 'fps';
      return '自定义预设';
    }
    return key;
  }
  function resModeOf(maxSize) {
    var w = profileWidth(maxSize);
    return w + 'x' + Math.round(w * 9 / 16);
  }
  function adaptQuality(video) {
    if (!video) return null;
    return {
      name: profileDisplayName(video.profile, video),
      id: video.profile || '',
      meta: profileLabel(video.max_size) + ' · ' + bpsToMbps(video.video_bit_rate) + ' Mbps'
    };
  }
  function streamModeToPage(mode) {
    if (mode === 'protocol') return 'protocol';
    if (mode === 'legacy') return 'legacy';
    return 'rawV2';
  }
  function pageModeToServer(mode) {
    if (mode === 'protocol') return 'protocol';
    if (mode === 'legacy') return 'legacy';
    return 'raw';
  }
  function bandwidthTierDefaults() {
    return [
      { key: '2mbps', label: '2 Mbps', description: '低带宽 · 流畅优先' },
      { key: '5mbps', label: '5 Mbps', description: '均衡 · 推荐' },
      { key: '10mbps', label: '10 Mbps', description: '高清 · 更高码率' },
      { key: '20mbps', label: '20 Mbps', description: '超清 · 最高画质' }
    ];
  }

  function presetItems(profiles, labels, customProfiles, catalog) {
    if (Array.isArray(catalog) && catalog.length) {
      var catalogSeen = {};
      return catalog.reduce(function (items, row) {
        row = row || {};
        var id = String(row.id || row.preset_id || row.presetId || '');
        if (!id || catalogSeen[id]) return items;
        catalogSeen[id] = true;
        var builtin = row.builtin === true || row.kind === 'builtin' || row.readonly === true;
        items.push({
          id: id,
          presetId: id,
          name: String(row.display_name || row.name || id),
          displayName: String(row.display_name || row.name || id),
          width: Number(row.width) || profileWidth(row.max_size),
          height: Number(row.height) || profileHeight(row.max_size),
          fps: presetFps(row.fps || row.max_fps),
          bitrate: row.bitrate_mbps != null ? Number(row.bitrate_mbps) : bpsToMbps(row.video_bit_rate),
          builtin: builtin,
          readonly: row.readonly === true,
          system: builtin,
          editable: row.editable !== false,
          fullscreenOnly: row.fullscreen_only === true,
          projectionAllowed: row.projection_allowed !== false,
          fullscreenAllowed: row.fullscreen_allowed !== false,
          enabled: row.enabled !== false,
          maxRes: 'none'
        });
        return items;
      }, []);
    }
    var items = [];
    var seen = {};
    var customFingerprints = {};
    Object.keys(profiles || {}).forEach(function (key) {
      var p = profiles[key] || {};
      var builtin = BUILTIN_PRESETS.indexOf(key) >= 0;
      var fingerprint = [p.max_size || '', p.max_fps || '', p.video_bit_rate || '', p.label || ''].join('|');
      if (!builtin && customFingerprints[fingerprint]) return;
      if (!builtin) customFingerprints[fingerprint] = true;
      var label = (labels || {})[key] || key;
      seen[key] = true;
      items.push({
        id: key,
        presetId: key,
        name: label,
        displayName: label,
        width: profileWidth(p.max_size),
        height: profileHeight(p.max_size),
        fps: presetFps(p.max_fps),
        bitrate: bpsToMbps(p.video_bit_rate),
        builtin: builtin,
        readonly: builtin,
        system: builtin,
        maxRes: 'none'
      });
    });
    Object.keys(customProfiles || {}).forEach(function (key) {
      // /api/video/preferences already includes custom profiles in `profiles`.
      // Admin responses also expose them under `custom_profiles`; only append
      // entries that were not present in the primary profile map.
      if (seen[key]) return;
      var p = customProfiles[key] || {};
      items.push({
        id: key,
        presetId: key,
        name: p.label || (labels || {})[key] || key,
        displayName: p.label || (labels || {})[key] || key,
        width: profileWidth(p.max_size),
        height: profileHeight(p.max_size),
        fps: presetFps(p.max_fps),
        bitrate: bpsToMbps(p.video_bit_rate),
        builtin: false,
        readonly: false,
        system: false,
        maxRes: 'none'
      });
    });
    return items;
  }

  function buildQualityPayload(prefs, admin) {
    var defaults = prefs.defaults || prefs.effective || {};
    var effective = prefs.effective || prefs.preferences || defaults;
    var emergencyPreset = prefs.emergency_preset || prefs.emergencyPreset || (admin && (admin.emergency_preset || admin.emergencyPreset)) || null;
    var usingEmergency = prefs.using_emergency_preset === true
      || prefs.usingEmergencyPreset === true
      || String(defaults.profile || '').toLowerCase() === 'emergency'
      || String(effective.profile || '').toLowerCase() === 'emergency';
    var profiles = prefs.profiles || {};
    var labels = prefs.profile_labels || {};
    var custom = (admin && admin.custom_profiles) || {};
    var adminSettings = (admin && admin.settings) || {};
    var viewerStop = normalizedViewerStopSettings(Object.assign({}, adminSettings, prefs));
    adoptViewerStopSettings(viewerStop);
    var defaultPreset = prefs.default_preset_id || prefs.default_preset || (admin && (admin.default_preset_id || admin.default_preset)) || '';
    var maxSize = Number(effective.max_size) || 1280;
    var fps = Number(effective.max_fps) || 24;
    var enabledModes = prefs.enabled_stream_modes || (admin && admin.enabled_stream_modes) || ['raw'];
    if (!Array.isArray(enabledModes)) {
      enabledModes = String(enabledModes || 'raw').split(',').map(function (mode) { return mode.trim(); }).filter(Boolean);
    }
    var rawV2Enabled = enabledModes.indexOf('raw') >= 0;
    var protocolEnabled = enabledModes.indexOf('protocol') >= 0;
    var legacyEnabled = enabledModes.indexOf('legacy') >= 0;
    var selected = effective.profile || '';
    // Emergency is a runtime-only fallback and must never look like a
    // selectable preset in the management editor.
    if (selected === 'custom' || selected === 'auto' || String(selected).toLowerCase() === 'emergency') selected = '';
    var idleMinutes = parseInt(adminSettings.auto_stop_minutes, 10);
    if (isNaN(idleMinutes) || idleMinutes < 0) idleMinutes = 15;
    var sessionMinutes = parseInt(adminSettings.max_session_minutes, 10);
    if (isNaN(sessionMinutes) || sessionMinutes < 0) sessionMinutes = 0;
    // 显示单位：整小时用小时档，其余用分钟档，保证回显与保存往返一致。
    var sessionValue = sessionMinutes;
    var sessionUnit = 'min';
    if (sessionMinutes > 0 && sessionMinutes % 60 === 0) {
      sessionValue = sessionMinutes / 60;
      sessionUnit = 'h';
    }
    var config = {
      resMode: resModeOf(maxSize),
      customW: profileWidth(maxSize),
      customH: profileHeight(maxSize),
      fps: fps,
      bitrate: bpsToMbps(effective.video_bit_rate),
      rawV2: rawV2Enabled,
      protoAvail: protocolEnabled,
      legacyAvail: legacyEnabled,
      defaultMode: streamModeToPage(effective.scrcpy_stream_mode || 'raw'),
      fullscreenQuality: prefs.fullscreen_profile || '',
      defaultPreset: defaultPreset,
      allowCustomTuning: prefs.allow_custom_tuning !== false,
      idleStopMin: String(idleMinutes),
      idleStopUnit: 'min',
      sessionLimitH: String(sessionValue),
      sessionLimitUnit: sessionUnit,
      viewerHiddenStopEnabled: viewerStop.viewer_hidden_stop_enabled,
      viewerHiddenStopMin: viewerStop.viewer_hidden_stop_minutes,
      viewerBlurStopEnabled: viewerStop.viewer_blur_stop_enabled,
      viewerBlurStopMin: viewerStop.viewer_blur_stop_minutes,
      viewerStopSettingsSynced: viewerStop.viewer_stop_settings_synced
    };
    var catalog = prefs.preset_catalog || (admin && admin.preset_catalog) || null;
    var items = presetItems(profiles, labels, custom, catalog);
    var configuredSelected = prefs.selected_preset_id || prefs.selectedPresetId || (admin && admin.selected_preset_id) || '';
    if (configuredSelected && items.some(function (item) { return item.id === String(configuredSelected); })) selected = String(configuredSelected);
    var selectedItem = items.filter(function (item) { return item.id === selected; })[0];
    if (selectedItem) {
      config.resMode = String(selectedItem.width) + 'x' + String(selectedItem.height);
      config.customW = selectedItem.width;
      config.customH = selectedItem.height;
      config.fps = selectedItem.fps;
      config.bitrate = selectedItem.bitrate;
    }
    return {
      presets: { items: items },
      builtinPresets: items.filter(function (p) { return p.builtin; }),
      customPresets: items.filter(function (p) { return !p.builtin; }),
      config: config,
      settings: config,
      params: config,
      selectedPresetId: selected,
      presetId: selected,
      rawV2: rawV2Enabled,
      protoAvail: protocolEnabled,
      legacyAvail: legacyEnabled,
      defaultMode: streamModeToPage(effective.scrcpy_stream_mode || 'raw'),
      fullscreenQuality: prefs.fullscreen_profile || '',
      defaultPreset: defaultPreset,
      default_preset: defaultPreset,
      default_preset_id: defaultPreset,
      allowCustomTuning: prefs.allow_custom_tuning !== false,
      enabled_presets: Array.isArray(prefs.enabled_presets) ? prefs.enabled_presets.slice() : BUILTIN_PRESETS.slice(),
      bandwidth_preset: prefs.bandwidth_preset || '',
      bandwidth_profile: prefs.bandwidth_profile || null,
      bandwidth_profile_id: prefs.bandwidth_profile_id || '',
      max_size_limit: Number(prefs.max_size_limit || 1920),
      user_custom_tuning: prefs.allow_custom_tuning === true,
      viewer_hidden_stop_enabled: viewerStop.viewer_hidden_stop_enabled,
      viewer_hidden_stop_minutes: viewerStop.viewer_hidden_stop_minutes,
      viewer_blur_stop_enabled: viewerStop.viewer_blur_stop_enabled,
      viewer_blur_stop_minutes: viewerStop.viewer_blur_stop_minutes,
      viewer_stop_settings_synced: viewerStop.viewer_stop_settings_synced,
      bandwidth_presets: (admin && Array.isArray(admin.bandwidth_presets)) ? admin.bandwidth_presets : bandwidthTierDefaults(),
      // 带宽档位对应的内置预设目标值(后端 BANDWIDTH_RECOMMENDATIONS),供保存前的确认框预览。
      bandwidth_recommendations: (admin && admin.bandwidth_recommendations) ? admin.bandwidth_recommendations : {},
      resolution_cap_options: (admin && Array.isArray(admin.resolution_cap_options)) ? admin.resolution_cap_options : RESOLUTION_CAPS,
      using_emergency_preset: usingEmergency,
      usingEmergencyPreset: usingEmergency,
      emergency_preset: emergencyPreset,
      emergencyPreset: emergencyPreset,
      status: { ok: true, status: 'healthy', mode: effective.scrcpy_stream_mode || 'raw', message: '' }
    };
  }

  /* ---------------- 设备/会话 ---------------- */

  function deviceStatusOf(d) {
    if (d.enabled === false) return 'disabled';
    var s = String(d.adb_state || d.status_label || 'unknown').toLowerCase();
    if (s === 'connected' || s === 'online' || s === 'ready') return 'online';
    if (s === 'checking' || s === 'connecting') return 'connecting';
    if (s === 'disconnected' || s === 'offline' || s === 'missing') return 'offline';
    return 'check-fail';
  }
  function lockUsername(sess) {
    var lock = sess && sess.control_lock;
    return lock && lock.username ? lock.username : '';
  }
  function adaptDevice(d, adminById, liveViewerMap, alasByDevice) {
    var sess = d.session || null;
    var admin = adminById ? (adminById[d.id] || adminById[d.device_id] || null) : null;
    var lockUser = lockUsername(sess);
    var latency = d.latency_ms;
    var adb = (admin && (admin.address || admin.adb)) || d.address || d.adb || '';
    var publicId = d.public_id || d.id || d.device_id;
    var alasBinding = alasByDevice && (alasByDevice[publicId] || alasByDevice[d.id] || alasByDevice[d.device_id]);
    var video = sess && sess.video ? sess.video : null;
    var quality = adaptQuality(video);
    var watchSessions = [];
    var liveViewers = liveViewerMap && (liveViewerMap[d.id] || liveViewerMap[d.device_id]) || [];
    if (liveViewers.length) {
      watchSessions = liveViewers.map(function (viewer) {
        return {
          role: viewer.has_control ? 'control' : 'watch',
          name: viewer.username || '—',
          client: viewer.client_id || '—',
          since: fmtTs(viewer.connected_at),
          latency: latency,
          drops: Number(viewer.drops || 0)
        };
      });
    }
    if (sess && !liveViewers.length) {
      var clients = Number(sess.clients) || 0;
      if (lockUser) {
        watchSessions.push({ role: 'control', name: lockUser, client: '—', since: fmtTs(sess.control_lock.acquired_at), latency: latency });
      }
      var watchers = Math.max(0, clients - (lockUser ? 1 : 0));
      for (var i = 0; i < watchers; i++) {
        watchSessions.push({ role: 'watch', name: '观看端 ' + (i + 1), client: '—', since: '—', latency: latency });
      }
    }
    var out = {
      id: d.id,
      device_id: d.device_id || d.id,
      name: d.name || d.display_name || 'Device',
      displayName: d.display_name || d.name,
      enabled: d.enabled !== false,
      status: deviceStatusOf(d),
      online: deviceStatusOf(d) === 'online',
      adb: adb,
      address: adb,
      latency: latency,
      heartbeat: fmtTs(d.last_seen_at || d.last_checked_at),
      viewers: sess ? (Number(sess.clients) || 0) : 0,
      viewerCount: sess ? (Number(sess.clients) || 0) : 0,
      controller: lockUser || null,
      controllerName: lockUser || null,
      streaming: !!(sess && sess.running),
      mirroring: !!(sess && sess.running),
      permission: d.can_view !== false,
      noPermission: d.can_view === false,
      sessions: watchSessions,
      quality: quality,
      alasCfg: alasBinding ? alasBinding.config_name : null,
      alasConfig: alasBinding ? alasBinding.config_name : null,
      alasStatus: alasBinding ? '已关联' : '未加载',
      lastError: admin ? (admin.last_error || '') : String(d.last_error || ''),
      lastOfflineReason: null,
      // 控制权限与 ADB 细节在无 admin 映射时也带上（仪表盘的设备行要用）：
      // 只读设备与离线原因原本在快照路径上会被丢掉。
      canControl: d.can_control !== false,
      viewOnly: d.can_control === false,
      adbDetail: admin ? (admin.adb_detail || '') : String(d.adb_detail || '')
    };
    if (admin) {
      out.internalId = admin.id;
    }
    return out;
  }

  function mergeDevicePayload(payload, adminResults) {
      payload = payload || {};
      adminResults = adminResults || [];
      var hasSnakeVisibility = Object.prototype.hasOwnProperty.call(payload, 'workbench_alas_visible');
      var hasCamelVisibility = Object.prototype.hasOwnProperty.call(payload, 'workbenchAlasVisible');
      if (hasSnakeVisibility || hasCamelVisibility) {
        var rawVisibility = hasSnakeVisibility ? payload.workbench_alas_visible : payload.workbenchAlasVisible;
        var nextVisibility = settingBoolean(rawVisibility, true);
        if (nextVisibility !== state.workbenchAlasVisible) {
          state.workbenchAlasVisible = nextVisibility;
          try {
            window.dispatchEvent(new CustomEvent('scrcpygate:workbench-alas-visibility', {
              detail: { visible: state.workbenchAlasVisible }
            }));
          } catch (error) {}
        }
      }
      var devices = payload.devices || [];
      var sessions = payload.sessions || {};
      var hasSnakeFeatures = Object.prototype.hasOwnProperty.call(payload, 'workbench_features');
      var hasCamelFeatures = Object.prototype.hasOwnProperty.call(payload, 'workbenchFeatures');
      var hasSnakeLayout = Object.prototype.hasOwnProperty.call(payload, 'workbench_layout');
      var hasCamelLayout = Object.prototype.hasOwnProperty.call(payload, 'workbenchLayout');
      if (hasSnakeFeatures || hasCamelFeatures || hasSnakeLayout || hasCamelLayout) {
        var nextFeatures = state.workbenchFeatures;
        if (hasSnakeFeatures || hasCamelFeatures) {
          var rawFeatures = hasSnakeFeatures ? payload.workbench_features : payload.workbenchFeatures;
          nextFeatures = workbenchFeatureFlags(rawFeatures);
        }
        var nextLayout = state.workbenchLayout;
        if (hasSnakeLayout || hasCamelLayout) {
          var rawLayout = hasSnakeLayout ? payload.workbench_layout : payload.workbenchLayout;
          nextLayout = workbenchMenuLayout(rawLayout);
        }
        if (!sameFeatureFlags(nextFeatures, state.workbenchFeatures) || !sameWorkbenchLayout(nextLayout, state.workbenchLayout)) {
          state.workbenchFeatures = nextFeatures;
          state.workbenchLayout = nextLayout;
          try {
            window.dispatchEvent(new CustomEvent('scrcpygate:workbench-features', {
              detail: { features: state.workbenchFeatures, layout: state.workbenchLayout }
            }));
          } catch (error) {}
        }
      }
      var adminList = (adminResults[0] && adminResults[0].devices) || [];
      var alasAssignments = (adminResults[2] && (adminResults[2].assignments || adminResults[2].bindings)) || [];
      var liveViewerMap = {};
      ((adminResults[1] && (adminResults[1].sessions || adminResults[1].items)) || []).forEach(function (viewer) {
        var key = String(viewer.device_id || '');
        if (!key) return;
        if (!liveViewerMap[key]) liveViewerMap[key] = [];
        liveViewerMap[key].push(viewer);
      });
      var adminById = {};
      var refMap = {};
      adminList.forEach(function (ad) {
        var internalId = ad.id || ad.device_id;
        var publicId = ad.public_id || internalId;
        adminById[internalId] = ad;
        adminById[publicId] = ad;
        var ref = {
          publicId: publicId,
          internalId: internalId,
          address: ad.address || ad.adb || '',
          name: ad.name || ad.display_name || ''
        };
        refMap[internalId] = ref;
        refMap[publicId] = ref;
      });
      var alasByDevice = {};
      alasAssignments.forEach(function (binding) {
        var key = String(binding.device_id || '');
        if (!key || alasByDevice[key]) return;
        alasByDevice[key] = binding;
      });
      state.adminDeviceCache = adminList;
      var list = devices.map(function (d) {
        var merged = Object.assign({}, d, { session: sessions[d.id] || sessions[d.device_id] || null });
        return adaptDevice(merged, adminById, liveViewerMap, alasByDevice);
      });
      var nameMap = {};
      list.forEach(function (adapted) {
        var ref = refMap[adapted.id] || refMap[adapted.device_id];
        if (!adapted.name || !ref || nameMap[adapted.name]) return;
        nameMap[adapted.name] = ref;
      });
      state.deviceNameMap = nameMap;
      state.deviceRefMap = refMap;
      state.publicDevices = list;
      state.publicDevicesStale = false;
      state.publicDevicesStaleAt = 0;
      return list;
  }

  function fetchDevicesEnriched(baseOnly) {
    // One snapshot is the normal path. The old three-request enrichment remains
    // a compatibility fallback only for older deployments that return 404/405.
    var current = window.ScrcpyGateSession && window.ScrcpyGateSession.current && window.ScrcpyGateSession.current();
    var knownRole = state.userRole || (current && (current.roleKey === 'admin' || current.role === '管理员' || current.isAdmin === true ? 'admin' : 'user'));
    var snapshot = apiGet('/api/workbench/snapshot').then(function (payload) {
      // The snapshot is authoritative on a cold load. Reading the session
      // cache before it resolves used to hide the admin supplement on the
      // first render and then force a second refresh to recover it.
      var snapshotUser = payload && payload.user || {};
      var snapshotRole = snapshotUser.role === 'admin' || snapshotUser.is_admin === true ? 'admin' : 'user';
      state.userRole = snapshotRole;
      var adminResults = snapshotRole === 'admin' ? [
        { devices: payload.admin_devices || [] },
        { sessions: payload.admin_sessions || [] },
        { assignments: payload.alas_assignments || [] }
      ] : [{ devices: [] }, { sessions: [] }, { assignments: [] }];
      return mergeDevicePayload(payload, adminResults);
    });
    return snapshot.catch(function (error) {
      var status = httpStatus(error);
      if (status === 429 || status === 444 || (error && (error.code === 'EDGE_CHALLENGE' || error.code === 'WAF_BLOCKED'))) {
        // A gateway rejection must not fan out into more requests. Keep the
        // last-shaped public device list and let the next bounded refresh retry.
        state.publicDevicesStale = true;
        state.publicDevicesStaleAt = Date.now();
        return Promise.resolve(state.publicDevices.slice());
      }
      if (status && status !== 404 && status !== 405) throw error;
      var emptyAdminSupplement = function () { return [{ devices: [] }, { sessions: [] }, { assignments: [] }]; };
      var loadAdminSupplement = function () {
        return Promise.all([
          apiGet('/api/admin/devices').catch(function () { return { devices: [] }; }),
          apiGet('/api/admin/sessions').catch(function () { return { sessions: [] }; }),
          apiGet('/api/admin/alas/permissions').catch(function () { return { assignments: [] }; })
        ]);
      };
      var adminSupplement = function () {
        if (baseOnly || knownRole === 'user') return Promise.resolve(emptyAdminSupplement());
        if (knownRole === 'admin') return loadAdminSupplement();
        return apiGet('/api/me').then(function (me) {
          var user = me && me.user || {};
          state.userRole = user.role === 'admin' ? 'admin' : 'user';
          return state.userRole === 'admin' ? loadAdminSupplement() : emptyAdminSupplement();
        }).catch(function () { return emptyAdminSupplement(); });
      };
      return Promise.all([apiGet('/api/devices'), adminSupplement()]).then(function (results) {
        return mergeDevicePayload(results[0], results[1]);
      });
    });
  }

  /* ---------------- 用户 ---------------- */

  function userStatusOf(u) {
    // 先按服务端 expiration_state 判定：到期账户的 is_active 也是 false，
    // 若先看 is_active，已到期会被误标成「已停用」（剩余天数胶囊也会从「已到期」变成「0 天」）。
    if (u.expiration_state === 'disabled') return 'disabled';
    if (u.expiration_state === 'expired') return 'expired';
    if (u.expiration_state === 'expiring') return 'expiring';
    if (u.is_active === false) return 'disabled';
    return 'normal';
  }

  function fetchUsersEnriched() {
    return Promise.all([
      apiGet('/api/admin/users'),
      apiGet('/api/admin/permissions'),
      apiGet('/api/admin/alas/permissions'),
      apiGet('/api/devices'),
      apiGet('/api/admin/alas/configs')
    ]).then(function (results) {
      var usersPayload = results[0];
      var permPayload = results[1];
      var alasPayload = results[2];
      var devPayload = results[3];
      var perms = permPayload.permissions || [];
      var permDevices = permPayload.devices || [];
      var bindings = alasPayload.assignments || [];
      var publicDevices = devPayload.devices || [];
      var configCatalogPayload = results[4] || {};
      var nameMap = {};
      var refMap = {};
      permDevices.forEach(function (ad) {
        var internalId = ad.id || ad.device_id;
        var publicId = ad.public_id || internalId;
        var name = ad.name || ad.display_name || String(publicId);
        nameMap[name] = { internalId: internalId, publicId: publicId };
        var ref = { internalId: internalId, publicId: publicId, address: ad.address || '', name: name };
        refMap[internalId] = ref;
        refMap[publicId] = ref;
      });
      // Public device payloads intentionally hide the internal ID. Do not join
      // them by display name: duplicate device names are valid and must never
      // change which ADB target a management action affects.
      state.deviceNameMap = nameMap;
      state.deviceRefMap = refMap;
      var deviceCatalog = permDevices.map(function (d) {
        var internalId = d.id || d.device_id;
        var publicId = d.public_id || (refMap[internalId] && refMap[internalId].publicId) || internalId;
        return {
          id: publicId,
          name: d.name || d.display_name || String(publicId),
          model: '—',
          online: deviceStatusOf(d) === 'online'
        };
      });
      var configNames = Array.isArray(configCatalogPayload.configs) ? configCatalogPayload.configs.slice() : [];
      bindings.forEach(function (binding) {
        if (configNames.indexOf(binding.config_name) < 0) configNames.push(binding.config_name);
      });
      var configCatalog = configNames.map(function (name) {
        return { id: String(name), name: String(name), online: true };
      });
      var users = (usersPayload.users || []).map(function (u) {
        var deviceIds = null;
        var devices = [];
        var devicePermissions = null;
        if (u.role !== 'admin') {
          deviceIds = [];
          devicePermissions = [];
          perms.forEach(function (p) {
            if (p.username !== u.username || !p.can_view) return;
            var ref = refMap[p.device_id] || refMap[p.public_device_id] || null;
            var publicId = p.public_device_id || (ref && ref.publicId) || p.device_id;
            var matchedName = ref && ref.name;
            if (deviceIds.indexOf(publicId) < 0) deviceIds.push(publicId);
            devices.push({ id: publicId, name: matchedName || String(p.device_id), model: '—', online: true });
            devicePermissions.push({ deviceId: publicId, canView: true, canControl: !!p.can_control });
          });
        }
        var configIds = null;
        var configs = [];
        if (u.role !== 'admin') {
          configIds = [];
          bindings.forEach(function (b) {
            if (b.username !== u.username) return;
            if (configIds.indexOf(b.config_name) < 0) configIds.push(b.config_name);
            configs.push({
              id: b.config_name,
              name: b.config_name,
              online: b.can_run !== false,
              // 用户页「编辑」里的 ALAS 关联要能回读当前绑的设备与权限。
              deviceId: String(b.device_id || ''),
              canRun: b.can_run !== false,
              canEdit: !!b.can_edit,
              isDefault: !!b.is_default
            });
          });
        }
        return {
          id: u.username,
          username: u.username,
          name: u.username,
          displayName: u.username,
          role: u.role,
          enabled: u.enabled !== false,
          /* 服务端下发的是 0/1（或字符串 "0"/"false"），统一折算成布尔再给页面，
             否则 `0 !== false` 会把「隐藏 ALAS」当成开启。 */
          alasVisible: settingBoolean(
            u.alas_visible !== undefined ? u.alas_visible : u.alasVisible,
            true
          ),
          status: userStatusOf(u),
          expiry: dateStr(u.expires_at),
          expiresAt: dateStr(u.expires_at),
          expirationState: u.expiration_state || 'permanent',
          remainingSeconds: u.remaining_seconds == null ? null : Number(u.remaining_seconds),
          /* 与 users-page.js 的编辑弹窗（expiryDayIndex - todayDayIndex）同一套日历日口径 */
          remainingDays: u.expires_at == null ? null : calendarDaysUntil(u.expires_at),
          lastLoginAt: u.last_login_at || null,
          lastLoginIp: u.last_login_ip || '',
          watchStats: u.watch_stats || u.watchStats || {
            session_count: 0,
            total_duration_ms: 0,
            last_started_at_ms: null,
            last_ended_at_ms: null,
            last_watched_at_ms: null,
            active_sessions: 0
          },
          watchHistory: Array.isArray(u.watch_history) ? u.watch_history.slice() : (Array.isArray(u.watchHistory) ? u.watchHistory.slice() : []),
          deviceIds: deviceIds,
          devicePermissions: devicePermissions,
          configIds: configIds,
          devices: devices,
          configs: configs
        };
      });
      return { items: users, users: users, devices: deviceCatalog, configs: configCatalog, total: users.length };
    });
  }

  /* ---------------- 日志 ---------------- */

  function adaptAuditList(payload, query) {
    // 兼容后端按时间正序返回单页的历史契约；界面与游标追加统一保持最新事件优先。
    payload = payload && typeof payload === 'object' ? payload : {};
    var serverLogs = (payload.logs || []).slice().reverse();
    if (!serverLogs.length && Array.isArray(payload.audits)) serverLogs = payload.audits.slice().reverse();
    if (!serverLogs.length && Array.isArray(payload.items)) serverLogs = payload.items.slice().reverse();
    var pageInfo = payload.page || payload.pagination || {};
    var summary = payload.summary || {};
    var bounds = timeRangeBounds(query.timeRange, query.from, query.to);
    var filtered = serverLogs
      .map(adaptAuditRecord)
      .filter(function (a) {
        if (!withinTimeBounds(a.ts, bounds)) return false;
        if (query.q && !hasText(a.operator + ' ' + a.action + ' ' + a.message + ' ' + a.target + ' ' + a.ip, query.q)) return false;
        if (query.eventType && query.eventType !== 'all' && a.eventType !== query.eventType) return false;
        if (query.result && query.result !== 'all' && a.result !== query.result) return false;
        if (query.actor && a.operator !== query.actor) return false;
        if (query.ip && a.ip.indexOf(query.ip) < 0) return false;
        if (query.target && a.target.indexOf(query.target) < 0) return false;
        return true;
      });
    var actors = {};
    filtered.forEach(function (a) {
      if (a.operator && !actors[a.operator]) actors[a.operator] = { id: a.operator, username: a.operator };
    });
    return {
      audits: filtered,
      logs: filtered,
      total: filtered.length,
      totalAll: summary.total != null ? summary.total : filtered.length,
      auditTotal: summary.total != null ? summary.total : filtered.length,
      abnormalCount: summary.high_risk != null ? summary.high_risk : undefined,
      summary: summary,
      byOutcome: summary.by_outcome || {},
      bySeverity: summary.by_severity || {},
      hasMore: !!pageInfo.has_more,
      nextCursor: pageInfo.next_cursor,
      facets: { actors: Object.keys(actors).map(function (k) { return actors[k]; }) },
      updatedAt: new Date().toLocaleString()
    };
  }

  function adaptRuntimeLogs(payload, query, offset, limit) {
    var entries = payload.entries || [];
    var meta = payload.meta || {};
    var bounds = timeRangeBounds(query.timeRange, query.from, query.to);
    var filtered = entries
      .map(adaptRuntimeEntry)
      .filter(function (e) {
        if (!withinTimeBounds(e.timestamp, bounds)) return false;
        if (query.q && !hasText(e.summary + ' ' + e.raw + ' ' + e.source, query.q)) return false;
        if (query.severity && query.severity !== 'all' && e.level !== normalizeLogLevel(query.severity)) return false;
        if (query.source && query.source !== 'all' && e.source !== query.source) return false;
        if (query.device && query.device !== 'all' && String(e.device) !== String(query.device)) return false;
        if (query.user && query.user !== 'all' && String(e.user) !== String(query.user)) return false;
        return true;
      });
    var slice = filtered.slice(offset || 0, (offset || 0) + (limit || 40));
    var devices = {};
    var users = {};
    filtered.forEach(function (e) {
      if (e.deviceId && e.deviceId !== 'none' && !devices[e.deviceId]) devices[e.deviceId] = { id: e.deviceId, name: e.deviceLabel };
      if (e.userId && e.userId !== 'none' && !users[e.userId]) users[e.userId] = { id: e.userId, username: e.userLabel };
    });
    var serverFacets = meta.facets || payload.facets || {};
    function mergeFacet(local, remote, labelKey) {
      var merged = {};
      (remote || []).forEach(function (item) {
        if (!item || item.id == null) return;
        var id = String(item.id);
        merged[id] = { id: id, count: Number(item.count) || 0 };
        merged[id][labelKey] = item[labelKey] || item.name || item.username || id;
      });
      (local || []).forEach(function (item) {
        if (!item || item.id == null) return;
        var id = String(item.id);
        if (!merged[id]) merged[id] = item;
      });
      return Object.keys(merged).map(function (key) { return merged[key]; });
    }
    return {
      logs: slice,
      total: filtered.length,
      totalAll: meta.returned_entries != null ? meta.returned_entries : filtered.length,
      logsTotal: meta.returned_entries != null ? meta.returned_entries : filtered.length,
      hasMore: (offset || 0) + slice.length < filtered.length,
      facets: {
        devices: mergeFacet(Object.keys(devices).map(function (k) { return devices[k]; }), serverFacets.devices, 'name'),
        users: mergeFacet(Object.keys(users).map(function (k) { return users[k]; }), serverFacets.users, 'username'),
        loggers: serverFacets.loggers || [],
        events: serverFacets.events || []
      },
      updatedAt: new Date().toLocaleString()
    };
  }

  /* ---------------- ALAS ---------------- */

  function settingBoolean(value, fallback) {
    if (value === undefined || value === null) return fallback !== false;
    if (typeof value === 'string') {
      var normalized = value.trim().toLowerCase();
      if (!normalized) return false;
      return ['false', '0', 'off', 'no', 'disabled'].indexOf(normalized) < 0;
    }
    return value !== false && value !== 0;
  }

  // 投屏管理下发的工作台功能开关：只接受布尔值，未知键忽略，
  // 缺失键一律视为启用（后端同样是「缺省即启用」）。
  function workbenchFeatureFlags(raw) {
    if (!raw || typeof raw !== 'object') return {};
    var flags = {};
    Object.keys(raw).forEach(function (key) {
      var value = raw[key];
      if (typeof value === 'boolean') flags[key] = value;
      else if (typeof value === 'string' || typeof value === 'number') flags[key] = settingBoolean(value, true);
    });
    return flags;
  }

  function sameFeatureFlags(a, b) {
    var left = a || {};
    var right = b || {};
    var leftKeys = Object.keys(left);
    var rightKeys = Object.keys(right);
    if (leftKeys.length !== rightKeys.length) return false;
    for (var i = 0; i < leftKeys.length; i += 1) {
      if (left[leftKeys[i]] !== right[leftKeys[i]]) return false;
    }
    return true;
  }

  // 底部菜单编排（一级 / 二级 + 顺序）：只接受字符串数组，未知形状视为未下发。
  function workbenchMenuLayout(raw) {
    if (!raw || typeof raw !== 'object') return null;
    var toList = function (value) {
      if (!Array.isArray(value)) return [];
      return value.map(function (item) { return String(item == null ? '' : item).trim(); })
        .filter(function (item) { return !!item; });
    };
    return { level1: toList(raw.level1), level2: toList(raw.level2) };
  }

  function sameWorkbenchLayout(a, b) {
    if (!a || !b) return a === b;
    return JSON.stringify(a.level1 || []) === JSON.stringify(b.level1 || [])
      && JSON.stringify(a.level2 || []) === JSON.stringify(b.level2 || []);
  }

  function alasConnState(status) {
    var s = String(status || '').toLowerCase();
    if (s === 'disconnected' || s === 'unreachable' || s === 'timeout') return 'unreachable';
    if (s === 'error') return 'error';
    if (s === 'running' || s === 'stopped' || s === 'idle') return 'connected';
    return 'unchecked';
  }

  function adaptAlasOverview() {
    return Promise.all([
      apiGet('/api/admin/alas/permissions'),
      apiGet('/api/admin/alas'),
      apiGet('/api/admin/permissions')
    ]).then(function (results) {
      var perm = results[0];
      var admin = results[1] || {};
      var devicePermissions = (results[2] && results[2].permissions) || [];
      var settings = admin.settings || {};
      var status = admin.status || {};
      var assignments = perm.assignments || admin.assignments || [];
      var users = (perm.users || []).map(function (u) {
        var deviceIds = u.role === 'admin' ? null : devicePermissions.filter(function (row) {
          return row.username === u.username && row.can_view;
        }).map(function (row) {
          return String(row.public_device_id || row.device_id || '');
        }).filter(Boolean);
        return {
          id: u.username,
          userId: u.username,
          username: u.username,
          name: u.username,
          displayName: u.username,
          role: u.role,
          // 与用户页共用同一套判定：到期账户的 is_active 也是 false，
          // 只看 is_active 会把「已到期」显示成「已停用」。
          status: userStatusOf(u),
          expiry: dateStr(u.expires_at),
          expiresAt: dateStr(u.expires_at),
          expirationState: u.expiration_state || 'permanent',
          remainingSeconds: u.remaining_seconds == null ? null : Number(u.remaining_seconds),
          deviceIds: deviceIds
        };
      });
      var devices = (perm.devices || []).map(function (d) {
        return { id: d.device_id, deviceId: d.device_id, name: d.name, online: d.enabled !== false };
      });
      var runtimeConfigs = (status.runtime_configs || []).map(function (name) { return String(name); });
      function openUrl(name, deviceId) {
        var query = ['config=' + encodeURIComponent(name)];
        if (deviceId) query.push('device_id=' + encodeURIComponent(deviceId));
        return '/alas/embed/?' + query.join('&');
      }
      // 逐配置状态:每行显示各自状态,而不是共享同一个总体状态。
      var statusByConfig = {};
      (status.config_statuses || []).forEach(function (s) {
        if (s && s.config) statusByConfig[s.config] = s;
      });
      function configStatusOf(name) {
        var own = statusByConfig[name];
        if (own) return { status: own.status || 'unknown', task: own.task || '' };
        return { status: status.status || 'unknown', task: '' };
      }
      var configSeen = {};
      var configs = [];
      assignments.forEach(function (a) {
        if (configSeen[a.config_name]) return;
        configSeen[a.config_name] = true;
        var own = configStatusOf(a.config_name);
        configs.push({
          id: a.config_name,
          configId: a.config_name,
          name: a.config_name,
          displayName: a.config_name,
          task: own.task || '自动化任务',
          inRuntime: runtimeConfigs.indexOf(a.config_name) >= 0,
          device: a.device_id || '',
          deviceId: a.device_id || '',
          bound: true,
          runtime: own.status,
          status: own.status,
          openUrl: openUrl(a.config_name, a.device_id || '')
        });
      });
      // 目录始终并入 Runtime 配置:未建立关联的显示"未分配",
      // 管理员面板的自动授权清单也因此始终覆盖全部 Runtime 配置。
      runtimeConfigs.forEach(function (name) {
        if (configSeen[name]) return;
        configSeen[name] = true;
        var own = configStatusOf(name);
        configs.push({
          id: name,
          configId: name,
          name: name,
          displayName: name,
          task: own.task || '自动化任务',
          inRuntime: true,
          bound: false,
          device: '',
          deviceId: '',
          runtime: own.status,
          status: own.status,
          openUrl: openUrl(name, '')
        });
      });
      var relations = [];
      state.alasRelationsMap = {};
      assignments.forEach(function (a) {
        var rid = a.username + ':' + a.config_name;
        state.alasRelationsMap[rid] = { username: a.username, configName: a.config_name, deviceId: a.device_id || '' };
        relations.push({
          id: rid,
          relationId: rid,
          userId: a.username,
          user_id: a.username,
          cfgId: a.config_name,
          configId: a.config_name,
          device: a.device_id || '',
          deviceId: a.device_id || '',
          def: !!a.is_default,
          default: !!a.is_default,
          isDefault: !!a.is_default,
          run: a.can_run !== false,
          canRun: a.can_run !== false,
          edit: !!a.can_edit,
          canEdit: !!a.can_edit,
          updatedAt: fmtTs(a.updated_at),
          updated_at: fmtTs(a.updated_at)
        });
      });
      var statusLabel = status.status || '—';
      var summary = '服务' + (settings.enabled ? '已启用' : '未启用') + ' · 绑定 ' + configs.length + ' 个配置';
      return {
        ok: true,
        users: users,
        devices: devices,
        configs: configs,
        relations: relations,
        assignments: relations,
         serviceEnabled: settings.enabled === true,
         enabled: settings.enabled === true,
         workbenchAlasVisible: settingBoolean(
           settings.workbench_alas_visible !== undefined
             ? settings.workbench_alas_visible
             : settings.workbenchAlasVisible,
           true
         ),
         runtimeUrl: settings.base_url || '',
        connState: alasConnState(status.status),
        connection: { state: alasConnState(status.status), status: status.status },
        tokenConfigured: settings.token_set === true,
        hasToken: settings.token_set === true,
        // 服务端 ALAS Token 加密密钥状态（仅布尔与来源）：缺失时页面提前提示，
        // 而不是等保存 Token 时收到 400。旧服务端没有该字段时为 null，页面不提示。
        tokenKey: settings.token_key || settings.tokenKey || null,
        lastCheckText: new Date().toLocaleString(),
        syncTimeText: new Date().toLocaleString(),
        statusLabel: statusLabel,
        status: statusLabel,
        summary: summary
      };
    });
  }

  /* ---------------- 投屏视频管道(Raw v2 / JMuxer) ---------------- */

  function videoSurface() { return document.getElementById('video-surface'); }
  function videoCanvas() { return document.getElementById('raw-v2-surface'); }

  function animateVideoLayout() {
    // 设备端转屏时让画框与画面平滑过渡，而不是瞬间跳变。
    var panel = document.getElementById('mirror-panel');
    var targets = [panel, state.videoEl, state.staleVideoEl].filter(function (el) { return !!el; });
    var saved = targets.map(function (el) { return el.style ? el.style.transition : ''; });
    targets.forEach(function (el) { if (el.style) el.style.transition = VIDEO_LAYOUT_TRANSITION; });
    window.setTimeout(function () {
      targets.forEach(function (el, index) {
        if (el && el.style) el.style.transition = saved[index] || '';
      });
    }, VIDEO_LAYOUT_ANIMATION_MS + 60);
  }

  function emitVideoSize(width, height, authoritative) {
    var rawWidth = Number(width);
    var rawHeight = Number(height);
    if (!(rawWidth > 0 && rawHeight > 0)) return;
    if (authoritative === true) state.videoDimensionsAuthoritative = true;
    var normalizedWidth = Math.max(1, Math.round(rawWidth));
    var normalizedHeight = Math.max(1, Math.round(rawHeight));
    var changed = state.videoWidth !== normalizedWidth || state.videoHeight !== normalizedHeight;
    state.videoWidth = normalizedWidth;
    state.videoHeight = normalizedHeight;
    var orientation = normalizedWidth >= normalizedHeight ? 'landscape' : 'portrait';
    if (changed && state.videoOrientation && state.videoOrientation !== orientation) animateVideoLayout();
    state.videoOrientation = orientation;
    if (!changed) return;
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:videosize', {
        detail: {
          width: normalizedWidth,
          height: normalizedHeight,
          authoritative: state.videoDimensionsAuthoritative,
          orientation: normalizedWidth >= normalizedHeight ? 'landscape' : 'portrait'
        }
      }));
    } catch (e) {}
  }

  function videoSourceDimensions() {
    var sessionVideo = state.session && state.session.video ? state.session.video : {};
    var maxSize = sessionVideo.max_size;
    var element = state.videoEl;
    var canvas = videoCanvas();
    var width = 0;
    var height = 0;
    // Raw v2 packet dimensions are authoritative and can arrive before the
    // browser updates HTMLMediaElement.videoWidth after a decoder rebuild.
    if (state.videoDimensionsAuthoritative && state.videoWidth > 0 && state.videoHeight > 0) {
      width = Number(state.videoWidth);
      height = Number(state.videoHeight);
    }
    if (!(width > 0 && height > 0)) {
      width = Number(element && element.videoWidth);
      height = Number(element && element.videoHeight);
    }
    if (!(width > 0 && height > 0) && canvas && canvas.width > 320 && canvas.height > 240) {
      width = Number(canvas.width);
      height = Number(canvas.height);
    }
    if (!(width > 0 && height > 0) && state.videoWidth > 0 && state.videoHeight > 0) {
      width = state.videoWidth;
      height = state.videoHeight;
    }
    if (!(width > 0 && height > 0)) {
      width = profileWidth(maxSize);
      height = profileHeight(maxSize);
    }
    return { width: Math.max(1, width), height: Math.max(1, height) };
  }

  /* 画面方向：0 / 90 / 180 / 270（270 内部用 -90 表示）。
     90 与 270 会把画面转置（宽高互换），180 只翻转不换宽高。 */
  function normalizeViewRotation(rotation) {
    var value = Number(rotation);
    if (value === 90 || value === -270) return 90;
    if (value === -90 || value === 270) return -90;
    if (value === 180 || value === -180) return 180;
    return 0;
  }

  function transposedViewRotation(rotation) {
    return rotation === 90 || rotation === -90;
  }

  function rotationTransform(rotation) {
    if (rotation === 90) return 'translate(-50%, -50%) rotate(90deg)';
    if (rotation === -90) return 'translate(-50%, -50%) rotate(-90deg)';
    if (rotation === 180) return 'translate(-50%, -50%) rotate(180deg)';
    return 'translate(-50%, -50%)';
  }

  function applyVideoRotationLayout(element) {
    if (!element || !element.style) return;
    element.style.objectFit = 'contain';
    var surface = videoSurface();
    var rect = surface && surface.getBoundingClientRect ? surface.getBoundingClientRect() : null;
    var containerWidth = Number(rect && rect.width) || Number(surface && surface.clientWidth) || 0;
    var containerHeight = Number(rect && rect.height) || Number(surface && surface.clientHeight) || 0;
    var rotated = transposedViewRotation(state.videoRotation);
    var source = videoSourceDimensions();
    var sourceAspect = source.width / source.height;
    var visualAspect = rotated ? 1 / sourceAspect : sourceAspect;
    var visualWidth = 0;
    var visualHeight = 0;
    if (containerWidth > 0 && containerHeight > 0 && visualAspect > 0) {
      var containerAspect = containerWidth / containerHeight;
      if (visualAspect > containerAspect) {
        visualWidth = containerWidth;
        visualHeight = containerWidth / visualAspect;
      } else {
        visualHeight = containerHeight;
        visualWidth = containerHeight * visualAspect;
      }
    }

    // Size the media element to the source aspect instead of relying on a
    // wide container plus object-fit:contain. This preserves every source
    // pixel and removes black letterboxing after the actual stream size is
    // known. The fallback keeps the element fill behavior during startup.
    if (!(visualWidth > 0 && visualHeight > 0)) {
      element.style.inset = '0';
      element.style.width = '100%';
      element.style.height = '100%';
      element.style.maxWidth = '';
      element.style.maxHeight = '';
      element.style.left = '';
      element.style.top = '';
      element.style.right = '';
      element.style.bottom = '';
      element.style.transform = '';
      element.style.transformOrigin = '';
      return;
    }

    var elementWidth = rotated ? visualHeight : visualWidth;
    var elementHeight = rotated ? visualWidth : visualHeight;
    element.style.inset = 'auto';
    element.style.left = '50%';
    element.style.top = '50%';
    element.style.right = 'auto';
    element.style.bottom = 'auto';
    element.style.width = Math.max(1, elementWidth) + 'px';
    element.style.height = Math.max(1, elementHeight) + 'px';
    element.style.maxWidth = 'none';
    element.style.maxHeight = 'none';
    element.style.transform = rotationTransform(state.videoRotation);
    element.style.transformOrigin = 'center center';
  }

  function updateVideoRotationLayout() {
    var surface = videoSurface();
    if (surface && surface.classList) {
      surface.classList.toggle('rotated', transposedViewRotation(state.videoRotation));
      surface.classList.toggle('rotated-180', state.videoRotation === 180);
    }
    var canvas = videoCanvas();
    if (canvas && canvas.style) {
      canvas.style.position = 'absolute';
      canvas.style.background = 'transparent';
      canvas.style.objectFit = 'contain';
    }
    applyVideoRotationLayout(canvas);
    applyVideoRotationLayout(state.videoEl);
  }

  function setVideoRotation(rotation) {
    var previousRotation = state.videoRotation;
    state.videoRotation = normalizeViewRotation(rotation);
    if (previousRotation !== state.videoRotation) {
      videoRecord('rotate', 'info', '画面显示方向变化', {
        from: previousRotation,
        to: state.videoRotation
      });
    }
    updateVideoRotationLayout();
    if (state.scrcpyInput && state.scrcpyInput.setViewportRotation) {
      state.scrcpyInput.setViewportRotation(state.videoRotation);
    }
    if (state.scrcpyInput && state.scrcpyInput.invalidateGeometry) {
      state.scrcpyInput.invalidateGeometry();
    }
    return state.videoRotation;
  }

  function setFullscreenMode(active) {
    state.fullscreenMode = !!active;
    if (state.scrcpyInput && state.scrcpyInput.setFullscreenMode) {
      state.scrcpyInput.setFullscreenMode(state.fullscreenMode);
    }
    updateVideoRotationLayout();
    syncScrcpyInputGeometry();
    return state.fullscreenMode;
  }

  if (window.addEventListener) window.addEventListener('resize', updateVideoRotationLayout);

  function ensureVideoElement() {
    var surface = videoSurface();
    if (!surface) return null;
    if (state.videoEl && state.videoEl.parentNode) return state.videoEl;
    var el = document.createElement('video');
    el.id = 'sg-raw-v2-video';
    el.tabIndex = 0;
    el.setAttribute('autoplay', '');
    el.setAttribute('muted', '');
    el.setAttribute('playsinline', '');
    // Keep the media element hidden until a decoded frame is actually
    // presented.  MSE's `sourceopen`/JMuxer onReady is not a render signal;
    // exposing the element there can briefly show a green or half-decoded
    // frame during the first decoder configuration.
    el.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:transparent;touch-action:none;opacity:0;transition:opacity 80ms linear;';
    // Keep the gate explicit for lightweight WebViews and test DOM shims that
    // do not parse cssText into individual style properties.
    el.style.opacity = '0';
    el.style.transition = 'opacity 80ms linear';
    updateVideoRotationLayout();
    var refreshVideoGeometry = function () {
      if (Number(el.videoWidth) > 0 && Number(el.videoHeight) > 0) {
        var sizeKey = Number(el.videoWidth) + 'x' + Number(el.videoHeight);
        if (state.videoRecordLastSize !== sizeKey) {
          state.videoRecordLastSize = sizeKey;
          videoRecord('size', 'info', '解码画面尺寸', { width: Number(el.videoWidth), height: Number(el.videoHeight) });
        }
        emitVideoSize(el.videoWidth, el.videoHeight, true);
      }
      updateVideoRotationLayout();
      syncScrcpyInputGeometry();
    };
    el.addEventListener('loadedmetadata', refreshVideoGeometry);
    el.addEventListener('resize', refreshVideoGeometry);
    el.addEventListener('playing', function () {
      if (state.videoEl !== el) return;
      // `playing` means playback was scheduled, not that a decoded frame has
      // reached the compositor.  requestVideoFrameCallback is the reliable
      // hand-off; the timer fallback covers older WebViews/Safari versions.
      scheduleVideoFrameReveal(el);
    });
    surface.insertBefore(el, surface.firstChild);
    state.videoEl = el;
    updateVideoRotationLayout();
    return el;
  }

  function stopVideoLatencyGuard() {
    if (state.videoLatencyTimer) {
      window.clearInterval(state.videoLatencyTimer);
      state.videoLatencyTimer = null;
    }
    state.videoLatencyLastSeekAt = 0;
    state.videoLatencyRecovering = false;
  }

  function startVideoLatencyGuard() {
    if (state.videoLatencyTimer) return;
    state.videoLatencyTimer = window.setInterval(function () {
      var el = state.videoEl;
      if (!el || !el.buffered || !el.buffered.length || el.seeking) return;
      var end;
      try { end = el.buffered.end(el.buffered.length - 1); } catch (e) { return; }
      var current = Number(el.currentTime);
      if (!isFinite(end) || !isFinite(current)) return;
      // JMuxer also checks once per second. Use an upper/lower hysteresis pair
      // so normal append jitter does not cause repeated seeks and waiting /
      // playing event churn on otherwise healthy streams.
      var tuning = currentLatencyTuning();
      var now = (window.performance && typeof window.performance.now === 'function')
        ? window.performance.now() : Date.now();
      var lag = end - current;
      if (state.videoLatencyRecovering && lag <= tuning.recoverySeconds) {
        state.videoLatencyRecovering = false;
        return;
      }
      if (lag > tuning.seekThresholdSeconds
        && now - state.videoLatencyLastSeekAt >= tuning.seekCooldownMs) {
        state.videoLatencyLastSeekAt = now;
        state.videoLatencyRecovering = true;
        // 诊断用：回跳会把解码链拉到一个可能已经没有关键帧的区间，是「黑一下又
        // 恢复」的候选原因之一；限流（seekCooldownMs）之下最多每秒一条。
        try {
          console.info('[scrcpygate] video latency seek lag=' + lag.toFixed(2)
            + 's threshold=' + tuning.seekThresholdSeconds + 's');
        } catch (e) {}
        videoRecord('seek', 'info', '延迟回跳压缩积压', {
          lag_s: Math.round(lag * 100) / 100,
          threshold_s: tuning.seekThresholdSeconds
        });
        // 回跳目标之前的那个 IDR 可能已经被 JMuxer 按 maxDelay 裁掉，直接回跳会让
        // 解码器一时找不到参考帧（表现为无提示的黑屏）。顺手让服务端立刻补一个
        // IDR，并把喂包拦到关键帧为止，把黑屏窗口从「等自然关键帧（可达数秒）」
        // 压到「等这次补帧（实测毫秒级）」。
        requestFreshKeyframe('latency_seek');
        try { el.currentTime = Math.max(0, end - tuning.recoverySeconds); } catch (e) {
          state.videoLatencyRecovering = false;
        }
      }
    }, 200);
  }

  function clearStaleVideoRetireTimer() {
    if (state.staleVideoRetireTimer) {
      window.clearInterval(state.staleVideoRetireTimer);
      state.staleVideoRetireTimer = null;
    }
  }

  function removeStaleVideoElement() {
    clearStaleVideoRetireTimer();
    var stale = state.staleVideoEl;
    state.staleVideoEl = null;
    if (stale && stale.parentNode) stale.parentNode.removeChild(stale);
  }

  function retireStaleVideoElement(el) {
    // 新画面已经稳定显示：淡出旧画面并移除，形成平滑切换。
    clearStaleVideoRetireTimer();
    var stale = state.staleVideoEl;
    state.staleVideoEl = null;
    if (!stale || stale === el || !stale.parentNode) return;
    stale.style.transition = 'opacity ' + VIDEO_STALE_FADE_MS + 'ms linear';
    stale.style.opacity = '0';
    window.setTimeout(function () {
      if (stale.parentNode) stale.parentNode.removeChild(stale);
    }, VIDEO_STALE_FADE_MS + 60);
  }

  function staleVideoRetireReady() {
    // 新解码器已经拿到关键帧、可以出画面了。仅凭「揭示兜底」的 600ms 超时不足以
    // 判断这一点：解码器没有参考帧时不会输出任何画面，此时撤掉旧画面就是黑屏。
    return state.rawV2KeyframeSeen && !state.rawV2AwaitingKeyframe;
  }

  function scheduleStaleVideoRetire(el) {
    if (state.staleVideoRetireTimer) return;
    var startedAt = Date.now();
    state.staleVideoRetireTimer = window.setInterval(function () {
      if (!state.staleVideoEl || state.staleVideoEl === el || state.videoEl !== el) {
        clearStaleVideoRetireTimer();
        return;
      }
      var timedOut = Date.now() - startedAt >= VIDEO_STALE_MAX_WAIT_MS;
      if (staleVideoRetireReady() || timedOut) retireStaleVideoElement(el);
    }, 200);
  }

  function destroyPlayer(keepElement) {
    stopVideoLatencyGuard();
    state.videoPlaying = false;
    state.videoFrameReady = false;
    if (state.videoFrameRevealTimer) {
      window.clearTimeout(state.videoFrameRevealTimer);
      state.videoFrameRevealTimer = null;
    }
    if (state.videoRevealHoldTimer) {
      window.clearTimeout(state.videoRevealHoldTimer);
      state.videoRevealHoldTimer = null;
    }
    if (state.videoRevealMaxWaitTimer) {
      window.clearTimeout(state.videoRevealMaxWaitTimer);
      state.videoRevealMaxWaitTimer = null;
    }
    clearStaleVideoRetireTimer();
    state.videoRevealBaseline = null;
    state.videoRevealFallbackTicks = 0;
    state.videoRevealFirstFrameAt = null;
    var oldVideo = state.videoEl;
    if (oldVideo && state.videoFrameCallbackId !== null && typeof oldVideo.cancelVideoFrameCallback === 'function') {
      try { oldVideo.cancelVideoFrameCallback(state.videoFrameCallbackId); } catch (e) {}
    }
    state.videoFrameCallbackId = null;
    if (state.jmuxer) {
      try { state.jmuxer.destroy(); } catch (e) {}
      state.jmuxer = null;
    }
    if (state.scrcpyInput && state.videoEl && state.scrcpyInput.videoElement === state.videoEl) {
      destroyScrcpyInput();
    }
    if (!keepElement) {
      if (state.videoEl && state.videoEl.parentNode) state.videoEl.parentNode.removeChild(state.videoEl);
      removeStaleVideoElement();
    }
    state.videoEl = null;
    var canvas = videoCanvas();
    var unavailable = document.getElementById('stream-unavailable');
    // 重建期间旧画面仍然可见：不要露出占位层，否则会遮住冻结的最后一帧。
    if (!keepElement) {
      if (canvas) canvas.style.display = '';
      if (unavailable) unavailable.style.display = '';
    }
    updateVideoRotationLayout();
  }

  function markVideoFrameReady(el) {
    if (state.videoEl !== el || state.videoFrameReady || !state.watchActive) return;
    if (Number(el.readyState || 0) < 2) {
      scheduleVideoFrameReveal(el);
      return;
    }
    state.videoFrameReady = true;
    state.videoPlaying = true;
    clearFirstFrameWatchdog();
    el.style.opacity = '1';
    if (staleVideoRetireReady()) {
      retireStaleVideoElement(el);
    } else {
      // 旧画面还在、新解码器尚未拿到关键帧：先别撤旧画面（撤了就是黑屏），
      // 等关键帧到达或超过 VIDEO_STALE_MAX_WAIT_MS 再撤。
      scheduleStaleVideoRetire(el);
    }
    var canvas = videoCanvas();
    var unavailable = document.getElementById('stream-unavailable');
    if (canvas) canvas.style.display = 'none';
    if (unavailable) unavailable.style.display = 'none';
    emitVideoLifecycle('scrcpygate:videoplaying');
  }

  function revealAfterStability(el) {
    // 稳定性优先：确认关键帧已到达、解码器又稳定输出了若干帧之后，再额外保持
    // 一个短窗口才显示，宁可多几十毫秒延迟也不闪首帧绿屏。
    if (state.videoEl !== el || state.videoFrameReady || !state.watchActive) return;
    if (state.videoRevealHoldTimer) return;
    state.videoRevealHoldTimer = window.setTimeout(function () {
      state.videoRevealHoldTimer = null;
      markVideoFrameReady(el);
    }, VIDEO_REVEAL_HOLD_MS);
  }

  function scheduleVideoFrameReveal(el) {
    if (state.videoEl !== el || state.videoFrameReady || !state.watchActive) return;
    if (typeof el.requestVideoFrameCallback === 'function') {
      if (state.videoFrameCallbackId !== null) return;
      try {
        state.videoFrameCallbackId = el.requestVideoFrameCallback(function (_now, metadata) {
          state.videoFrameCallbackId = null;
          if (state.videoEl !== el || state.videoFrameReady || !state.watchActive) return;
          if (!state.rawV2KeyframeSeen) {
            scheduleVideoFrameReveal(el);
            return;
          }
          if (state.videoRevealFirstFrameAt === null) {
            state.videoRevealFirstFrameAt = Date.now();
            // 静态画面不会产生新帧，requestVideoFrameCallback 也就不会再触发；
            // 用独立定时器兜底，避免画面永远卡在等待态。
            if (state.videoRevealMaxWaitTimer) window.clearTimeout(state.videoRevealMaxWaitTimer);
            state.videoRevealMaxWaitTimer = window.setTimeout(function () {
              state.videoRevealMaxWaitTimer = null;
              if (state.videoEl === el && !state.videoFrameReady && state.watchActive && state.rawV2KeyframeSeen) {
                revealAfterStability(el);
              }
            }, VIDEO_REVEAL_MAX_WAIT_MS);
          }
          var waitedLongEnough = Date.now() - state.videoRevealFirstFrameAt >= VIDEO_REVEAL_MAX_WAIT_MS;
          var presented = Number(metadata && metadata.presentedFrames);
          if (isFinite(presented)) {
            if (state.videoRevealBaseline === null) state.videoRevealBaseline = presented;
            if (!waitedLongEnough && presented - state.videoRevealBaseline < VIDEO_REVEAL_MIN_FRAMES) {
              scheduleVideoFrameReveal(el);
              return;
            }
          } else {
            state.videoRevealFallbackTicks += 1;
            if (!waitedLongEnough && state.videoRevealFallbackTicks < VIDEO_REVEAL_MIN_FRAMES) {
              scheduleVideoFrameReveal(el);
              return;
            }
          }
          revealAfterStability(el);
        });
        return;
      } catch (e) {
        state.videoFrameCallbackId = null;
      }
    }
    if (state.videoFrameRevealTimer) return;
    state.videoFrameRevealTimer = window.setTimeout(function () {
      state.videoFrameRevealTimer = null;
      if (state.videoEl !== el || state.videoFrameReady || !state.watchActive) return;
      if (Number(el.readyState || 0) >= 2 && state.rawV2KeyframeSeen) {
        if (state.videoRevealFirstFrameAt === null) state.videoRevealFirstFrameAt = Date.now();
        var waitedLongEnough = Date.now() - state.videoRevealFirstFrameAt >= VIDEO_REVEAL_MAX_WAIT_MS;
        state.videoRevealFallbackTicks += 1;
        if (!waitedLongEnough && state.videoRevealFallbackTicks < VIDEO_REVEAL_MIN_FRAMES) {
          scheduleVideoFrameReveal(el);
          return;
        }
        revealAfterStability(el);
      } else {
        scheduleVideoFrameReveal(el);
      }
    }, 50);
  }

  function ensurePlayer() {
    var JMuxer = window.JMuxer;
    if (!JMuxer) {
      var canvas = videoCanvas();
      if (canvas && canvas.getContext) {
        var ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.fillStyle = '#000';
          ctx.fillRect(0, 0, canvas.width || 640, canvas.height || 360);
          ctx.fillStyle = '#8e8e93';
          ctx.font = '13px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText('视频组件未加载(JMuxer)', (canvas.width || 640) / 2, (canvas.height || 360) / 2);
        }
      }
      return false;
    }
    if (state.jmuxer) return true;
    var el = ensureVideoElement();
    if (!el) return false;
    var sessionVideo = state.session && state.session.video ? state.session.video : {};
    var configuredFps = Number(sessionVideo.max_fps);
    var fps = isFinite(configuredFps) && configuredFps > 0
      ? Math.max(1, Math.min(120, Math.round(configuredFps)))
      : 24;
    try {
      state.jmuxer = new JMuxer({
        node: el,
        mode: 'video',
        flushingTime: 0,
        clearBuffer: true,
        // Keep the MSE tail short so interaction follows the device instead
        // of replaying an old frame after a transient network stall.
        // JMuxer's default cancelDelay runs every second. A 500ms bound keeps
        // live latency controlled without seeking on every small append gap;
        // the low-latency preset tightens it to 250ms (see PROFILE_LATENCY_TUNING).
        maxDelay: currentLatencyTuning().maxDelayMs,
        fps: fps,
        readFpsFromTrack: true,
        debug: false,
        onReady: function () {
          if (state.videoEl !== el) return;
          state.videoSurfaceReady = true;
          startVideoLatencyGuard();
          if (state.controlOwnership) bindScrcpyInput(state.deviceId);
          syncScrcpyInputGeometry();
          // The element is muted and inline, so this is safe on mobile and
          // avoids waiting for a second user gesture after an MSE rebuild.
          if (el && typeof el.play === 'function') {
            try { var playResult = el.play(); if (playResult && playResult.catch) playResult.catch(function () {}); } catch (e) {}
          }
        },
        onError: function () {
          if (state.videoEl !== el) return;
          // 原始层：播放器/解码器报错的原文（派生事件只记「重建解码器」这个结论）。
          var mediaError = el && el.error ? { code: el.error.code, message: String(el.error.message || '') } : null;
          var readyState = el ? Number(el.readyState) : null;
          rawRecord('player', 'error', {
            media_error: mediaError,
            ready_state: readyState,
            network_state: el ? Number(el.networkState) : null,
            current_time: el ? Math.round(Number(el.currentTime || 0) * 1000) / 1000 : null,
            feed_count: state.jmuxerFeedCount || 0
          });
          schedulePlayerRecovery();
        }
      });
    } catch (e) {
      state.jmuxer = null;
      return false;
    }
    if (state.controlOwnership) bindScrcpyInput(state.deviceId);
    return true;
  }

  var RAW_V2_HEADER_LENGTH = 32;
  var RAW_V2_MAX_PACKET_BYTES = 25165824;
  var RAW_V2_FLAG_KEYFRAME = 0x01;
  var RAW_V2_FLAG_CONFIG = 0x02;
  var RAW_V2_FLAG_DISCONTINUITY = 0x04;

  function resetRawV2State(clearTransport) {
    state.rawV2Sequence = 0;
    state.rawV2SequenceSeen = false;
    state.rawV2KeyframeSeen = false;
    state.rawV2AwaitingKeyframe = true;
    state.rawV2LastFedSequence = 0;
    state.rawV2LastFedSequenceSeen = false;
    state.rawV2KeyframeRequestAt = 0;
    state.videoFrameReady = false;
    state.videoPlaying = false;
    state.rawV2ConfigGeneration = 0;
    state.rawV2ConfigGenerationSeen = false;
    state.videoRevealBaseline = null;
    state.videoRevealFallbackTicks = 0;
    state.videoRevealFirstFrameAt = null;
    if (state.videoRevealMaxWaitTimer) {
      window.clearTimeout(state.videoRevealMaxWaitTimer);
      state.videoRevealMaxWaitTimer = null;
    }
      state.videoWidth = 0;
      state.videoHeight = 0;
      state.videoDimensionsAuthoritative = false;
    if (clearTransport) {
      state.rawV2Transport = '';
      state.rawV2PacketUnit = '';
      state.rawV2ProtocolVersion = 0;
    }
  }

  function rawV2U16(bytes, offset) {
    return ((bytes[offset] << 8) | bytes[offset + 1]) >>> 0;
  }

  function rawV2U32(bytes, offset) {
    return ((bytes[offset] * 0x1000000) + ((bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3])) >>> 0;
  }

  // 共享的 Raw v2 解析器（static/shared/raw-v2.js，宫格播放器同样使用）。
  // 未加载时回退到本文件内的等价实现，保证单独引用适配器也能工作。
  var sharedRawV2 = window.ScrcpyGateRawV2 || null;

  function parseRawV2Packet(data) {
    if (sharedRawV2 && typeof sharedRawV2.parse === 'function') return sharedRawV2.parse(data);
    var bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    if (bytes.length < RAW_V2_HEADER_LENGTH) throw new Error('raw-v2-short-header');
    if (bytes.length > RAW_V2_MAX_PACKET_BYTES) throw new Error('raw-v2-packet-too-large');
    if (bytes[0] !== 0x53 || bytes[1] !== 0x47 || bytes[2] !== 0x56 || bytes[3] !== 0x32) throw new Error('raw-v2-magic');
    if (bytes[4] !== 1) throw new Error('raw-v2-version');
    var flags = bytes[5];
    if (flags & ~(RAW_V2_FLAG_KEYFRAME | RAW_V2_FLAG_CONFIG | RAW_V2_FLAG_DISCONTINUITY)) throw new Error('raw-v2-flags');
    var headerLength = rawV2U16(bytes, 6);
    if (headerLength !== RAW_V2_HEADER_LENGTH) throw new Error('raw-v2-header-length');
    var payloadLength = rawV2U32(bytes, 28);
    if (headerLength + payloadLength !== bytes.length) throw new Error('raw-v2-payload-length');
    return {
      sequence: rawV2U32(bytes, 8),
      configGeneration: rawV2U32(bytes, 12),
      width: rawV2U16(bytes, 24),
      height: rawV2U16(bytes, 26),
      keyframe: !!(flags & RAW_V2_FLAG_KEYFRAME),
      containsConfig: !!(flags & RAW_V2_FLAG_CONFIG),
      discontinuity: !!(flags & RAW_V2_FLAG_DISCONTINUITY),
      payload: bytes.slice(headerLength)
    };
  }

  function rawV2SequenceIsNewer(next, current) {
    if (sharedRawV2 && typeof sharedRawV2.sequenceIsNewer === 'function') return sharedRawV2.sequenceIsNewer(next, current);
    var distance = (Number(next) - Number(current)) >>> 0;
    return distance !== 0 && distance < 0x80000000;
  }

  function recreateVideoPlayer(reason) {
    state.videoSurfaceReady = false;
    // 新解码器从零开始：在拿到关键帧之前不要喂任何 P 帧，否则解码器没有参考帧、
    // 只能输出黑/绿屏，而保留的旧画面又会被揭示兜底撤掉。
    state.rawV2AwaitingKeyframe = true;
    state.rawV2LastFedSequenceSeen = false;
    videoRecord('rebuild', 'warn', '重建解码器', {
      reason: String(reason || 'unknown'),
      sequence: state.rawV2Sequence
    });
    rawRecord('player', 'rebuild', {
      reason: String(reason || 'unknown'),
      sequence: state.rawV2Sequence,
      last_fed: state.rawV2LastFedSequence,
      retry: state.videoRetryCount || 0,
      had_player: !!state.jmuxer,
      feed_count: state.jmuxerFeedCount || 0
    });
    var previous = state.videoEl;
    destroyPlayer(true);
    var ok = ensurePlayer();
    if (previous && previous.parentNode) {
      // 保留上一帧：新解码器就绪前继续显示旧画面，避免重建期间黑屏。
      removeStaleVideoElement();
      state.staleVideoEl = previous;
      previous.style.pointerEvents = 'none';
      previous.style.zIndex = '1';
      var next = state.videoEl;
      if (next && next.style) next.style.zIndex = '0';
    }
    return ok;
  }

  function clearFirstFrameWatchdog() {
    if (state.firstFrameTimer) {
      window.clearTimeout(state.firstFrameTimer);
      state.firstFrameTimer = null;
    }
  }

  function scheduleFirstFrameWatchdog() {
    clearFirstFrameWatchdog();
    state.firstFrameTimer = window.setTimeout(function () {
      state.firstFrameTimer = null;
      if (!state.watchActive || (state.rawV2KeyframeSeen && state.videoPlaying) || !state.videoSocket || state.videoSocket.readyState !== 1) return;
      if (state.firstFrameRecoveryCount < 1) {
        state.firstFrameRecoveryCount += 1;
        try { state.videoSocket.send(JSON.stringify({ type: 'player_reset' })); } catch (e) {}
        recreateVideoPlayer('first_frame_timeout');
        scheduleFirstFrameWatchdog();
      } else {
        // A second timeout means the current transport is no longer making
        // progress. Close it before reporting failure so it cannot block a
        // later, explicitly requested reconnect.
        state.watchActive = false;
        closeVideoSocket(true);
        emitVideoLifecycle('scrcpygate:videofailed');
      }
    }, 8000);
  }

  function schedulePlayerRecovery() {
    var wasVisible = state.videoFrameReady || state.videoPlaying;
    state.videoSurfaceReady = false;
    if (wasVisible) emitVideoLifecycle('scrcpygate:videodrop');
    if (state.playerRecoveryTimer) return;
    state.playerRecoveryTimer = window.setTimeout(function () {
      state.playerRecoveryTimer = null;
      if (!state.watchActive || !state.videoSocket || state.videoSocket.readyState !== 1) return;
      try { state.videoSocket.send(JSON.stringify({ type: 'player_reset' })); } catch (e) {}
      recreateVideoPlayer('player_recovery');
    }, 250);
  }

  function requestFreshKeyframe(reason) {
    // 服务端收到 player_reset 会立刻补发缓存关键帧并请求设备补一个 IDR，
    // 比等编码器自然出关键帧（这类设备实测可达数秒）快得多。按间隔限流，
    // 避免把无谓的控制消息打到设备上。
    state.rawV2AwaitingKeyframe = true;
    var now = Date.now();
    if (state.rawV2KeyframeRequestAt && now - state.rawV2KeyframeRequestAt < VIDEO_KEYFRAME_REQUEST_INTERVAL_MS) return;
    state.rawV2KeyframeRequestAt = now;
    if (!state.videoSocket || state.videoSocket.readyState !== 1) return;
    try {
      state.videoSocket.send(JSON.stringify({ type: 'player_reset' }));
      rawRecordOut({ type: 'player_reset', reason: String(reason), sequence: state.rawV2Sequence });
    } catch (e) {
      return;
    }
    try {
      console.info('[scrcpygate] video awaiting keyframe reason=' + String(reason)
        + ' sequence=' + String(state.rawV2Sequence) + ' last_fed=' + String(state.rawV2LastFedSequence));
    } catch (e) {}
    videoRecord('keyframe_wait', 'warn', '请求服务端补关键帧', {
      reason: String(reason),
      sequence: state.rawV2Sequence,
      last_fed: state.rawV2LastFedSequence
    });
  }

  function acceptRawV2Packet(packet) {
    var sequence = Number(packet.sequence) >>> 0;
    var configGeneration = Number(packet.configGeneration) >>> 0;
    if (state.rawV2SequenceSeen && !rawV2SequenceIsNewer(sequence, state.rawV2Sequence)) return false;
    if (
      state.rawV2ConfigGenerationSeen
      && configGeneration !== state.rawV2ConfigGeneration
      && !rawV2SequenceIsNewer(configGeneration, state.rawV2ConfigGeneration)
    ) return false;
    var generationChanged = state.rawV2ConfigGenerationSeen && configGeneration !== state.rawV2ConfigGeneration;
    state.rawV2Sequence = sequence;
    state.rawV2SequenceSeen = true;
    state.rawV2ConfigGeneration = configGeneration;
    state.rawV2ConfigGenerationSeen = true;
    if (packet.width > 0 && packet.height > 0) {
      var canvas = videoCanvas();
      if (canvas) { canvas.width = packet.width; canvas.height = packet.height; }
      emitVideoSize(packet.width, packet.height, true);
      updateVideoRotationLayout();
      syncScrcpyInputGeometry(packet.width, packet.height);
    }
    if (packet.discontinuity || generationChanged) {
      var wasVisible = state.videoFrameReady || state.videoPlaying;
      if (wasVisible) emitVideoLifecycle('scrcpygate:videodrop');
      if (!recreateVideoPlayer(packet.discontinuity ? 'discontinuity' : 'config_change')) {
        schedulePlayerRecovery();
        return false;
      }
    }
    return true;
  }

  function normalizedViewerStopSettings(payload) {
    var source = payload && payload.data && typeof payload.data === 'object' ? payload.data : (payload || {});
    var value = function (key, fallback) {
      return source[key] === undefined || source[key] === null ? fallback : source[key];
    };
    var boolValue = function (raw, fallback) {
      if (raw === true || raw === false) return raw;
      var text = String(raw == null ? '' : raw).trim().toLowerCase();
      if (text === 'true' || text === '1' || text === 'yes' || text === 'on') return true;
      if (text === 'false' || text === '0' || text === 'no' || text === 'off') return false;
      return fallback;
    };
    var minutesValue = function (raw, fallback) {
      var minutes = Number(raw);
      return isFinite(minutes) && Math.floor(minutes) === minutes && minutes >= 5 && minutes <= 10
        ? minutes : fallback;
    };
    var settings = {
      viewer_hidden_stop_enabled: boolValue(value('viewer_hidden_stop_enabled', VIEWER_STOP_DEFAULTS.viewer_hidden_stop_enabled), true),
      viewer_hidden_stop_minutes: minutesValue(value('viewer_hidden_stop_minutes', VIEWER_STOP_DEFAULTS.viewer_hidden_stop_minutes), 5),
      viewer_blur_stop_enabled: boolValue(value('viewer_blur_stop_enabled', VIEWER_STOP_DEFAULTS.viewer_blur_stop_enabled), false),
      viewer_blur_stop_minutes: minutesValue(value('viewer_blur_stop_minutes', VIEWER_STOP_DEFAULTS.viewer_blur_stop_minutes), 5),
      viewer_stop_settings_synced: boolValue(value('viewer_stop_settings_synced', VIEWER_STOP_DEFAULTS.viewer_stop_settings_synced), false)
    };
    if (settings.viewer_stop_settings_synced) {
      settings.viewer_blur_stop_enabled = settings.viewer_hidden_stop_enabled;
      settings.viewer_blur_stop_minutes = settings.viewer_hidden_stop_minutes;
    }
    return settings;
  }

  function adoptViewerStopSettings(payload) {
    if (!payload || typeof payload !== 'object') return;
    var source = payload.viewer_hidden_stop_enabled !== undefined
      ? payload
      : (payload.preferences || payload.settings || payload.data || null);
    if (!source || source.viewer_hidden_stop_enabled === undefined) return;
    state.viewerStopSettings = normalizedViewerStopSettings(source);
    state.viewerStopSettingsLoaded = true;
    if (state.watchActive) evaluateViewerStopPolicy(Date.now());
  }

  function pageIsHidden() {
    return document.visibilityState === 'hidden' || document.hidden === true;
  }

  function viewerStopMode() {
    if (pageIsHidden()) return 'hidden';
    if (state.windowFocused === false) return 'blur';
    return null;
  }

  function viewerStopPolicy(mode) {
    if (mode === 'hidden') {
      return {
        enabled: state.viewerStopSettings.viewer_hidden_stop_enabled === true,
        minutes: Number(state.viewerStopSettings.viewer_hidden_stop_minutes) || 5
      };
    }
    if (mode === 'blur') {
      return {
        enabled: state.viewerStopSettings.viewer_blur_stop_enabled === true,
        minutes: Number(state.viewerStopSettings.viewer_blur_stop_minutes) || 5
      };
    }
    return { enabled: false, minutes: 0 };
  }

  function cancelViewerStopTimer() {
    if (state.viewerStopTimer) {
      window.clearTimeout(state.viewerStopTimer);
      state.viewerStopTimer = null;
    }
  }

  function emitViewerStopCancelled(mode) {
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:viewer-stop-cancelled', {
        detail: { mode: mode || '', cancelledAt: Date.now() }
      }));
    } catch (e) {}
  }

  function resetViewerStopTracking(announceCancellation) {
    var previousMode = state.viewerStopMode;
    var wasWarning = state.viewerStopWarningShown;
    cancelViewerStopTimer();
    state.viewerStopMode = null;
    state.viewerStopStartedAt = 0;
    state.viewerStopWarningAt = 0;
    state.viewerStopDeadlineAt = 0;
    state.viewerStopWarningShown = false;
    if (announceCancellation && wasWarning) emitViewerStopCancelled(previousMode);
  }

  function emitViewerStopWarning(mode) {
    if (state.viewerStopWarningShown) return;
    state.viewerStopWarningShown = true;
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:viewer-stop-warning', {
        detail: {
          mode: mode,
          warningAt: state.viewerStopWarningAt,
          deadlineAt: state.viewerStopDeadlineAt,
          remainingSeconds: Math.max(0, Math.ceil((state.viewerStopDeadlineAt - Date.now()) / 1000))
        }
      }));
    } catch (e) {}
  }

  function stopViewerForPolicy() {
    if (state.viewerStopTerminal || !state.watchActive) return Promise.resolve({ ok: true, alreadyStopped: true });
    var deviceId = state.deviceId;
    var clientId = state.session && state.session.client_id || '';
    var viewerToken = state.viewerToken || '';
    var mode = state.viewerStopMode || '';
    state.viewerStopTerminal = true;
    state.watchActive = false;
    resetViewerStopTracking(false);
    clearCurrentSession(deviceId, clientId);
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:viewer-stopped', {
        detail: { deviceId: deviceId || '', clientId: clientId, mode: mode, reason: 'viewer_stop_policy' }
      }));
    } catch (e) {}
    if (!deviceId || (!clientId && !viewerToken)) return Promise.resolve({ ok: true, stopped: false });
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/stop-self', {
      client_id: clientId,
      viewer_token: viewerToken
    }).catch(function () { return { ok: false, stopped: false }; });
  }

  function evaluateViewerStopPolicy(now) {
    now = Number(now) || Date.now();
    if (!state.watchActive || state.viewerStopTerminal || state.authInvalid) {
      resetViewerStopTracking(false);
      return;
    }
    var mode = viewerStopMode();
    var policy = viewerStopPolicy(mode);
    // A throttled background timer can fire only after the page becomes
    // visible again. Recompute the previous mode's absolute deadline before
    // clearing its state so a hidden/blurred viewer cannot evade stop-self by
    // returning after the deadline.
    if (mode !== state.viewerStopMode && state.viewerStopMode && state.viewerStopStartedAt) {
      var previousMode = state.viewerStopMode;
      var previousPolicy = viewerStopPolicy(previousMode);
      if (previousPolicy.enabled) {
        state.viewerStopWarningAt = state.viewerStopStartedAt + previousPolicy.minutes * 60 * 1000;
        state.viewerStopDeadlineAt = state.viewerStopWarningAt + VIEWER_STOP_WARNING_MS;
        if (now >= state.viewerStopDeadlineAt) {
          emitViewerStopWarning(previousMode);
          stopViewerForPolicy();
          return;
        }
      }
    }
    if (mode !== state.viewerStopMode) {
      var wasWarning = state.viewerStopWarningShown;
      var previousMode = state.viewerStopMode;
      cancelViewerStopTimer();
      state.viewerStopMode = mode;
      state.viewerStopStartedAt = 0;
      state.viewerStopWarningAt = 0;
      state.viewerStopDeadlineAt = 0;
      state.viewerStopWarningShown = false;
      if (wasWarning) emitViewerStopCancelled(previousMode);
    }
    if (!mode || !policy.enabled) {
      cancelViewerStopTimer();
      state.viewerStopStartedAt = 0;
      state.viewerStopWarningAt = 0;
      state.viewerStopDeadlineAt = 0;
      state.viewerStopWarningShown = false;
      return;
    }
    if (!state.viewerStopStartedAt) {
      state.viewerStopStartedAt = now;
      state.viewerStopWarningAt = now + policy.minutes * 60 * 1000;
      state.viewerStopDeadlineAt = state.viewerStopWarningAt + VIEWER_STOP_WARNING_MS;
    } else {
      state.viewerStopWarningAt = state.viewerStopStartedAt + policy.minutes * 60 * 1000;
      state.viewerStopDeadlineAt = state.viewerStopWarningAt + VIEWER_STOP_WARNING_MS;
    }
    if (now >= state.viewerStopDeadlineAt) {
      emitViewerStopWarning(mode);
      stopViewerForPolicy();
      return;
    }
    if (now >= state.viewerStopWarningAt) {
      emitViewerStopWarning(mode);
    }
    cancelViewerStopTimer();
    var nextAt = state.viewerStopWarningShown ? state.viewerStopDeadlineAt : state.viewerStopWarningAt;
    state.viewerStopTimer = window.setTimeout(function () {
      state.viewerStopTimer = null;
      evaluateViewerStopPolicy(Date.now());
    }, Math.max(0, nextAt - now));
  }

  function loadViewerStopSettings() {
    return apiGet('/api/video/preferences').then(function (payload) {
      adoptViewerStopSettings(payload);
      return payload;
    });
  }

  /* ---------------- 实测码率(客户端按收到的视频字节统计) ---------------- */

  var VIDEO_RATE_WINDOW_MS = 1000;

  function emitVideoRate(mbps) {
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:videorate', {
        detail: { mbps: Number(mbps) || 0 }
      }));
    } catch (e) {}
  }

  function emitVideoFps(fps) {
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:videofps', {
        detail: { fps: Number(fps) || 0 }
      }));
    } catch (e) {}
  }

  /* 本浏览器的稳定设备标识（用户要求：同一台设备进出记录时 id 不要变）。
     连接级的 client_id 每次握手都会变，做不了「是不是同一台设备」的判断；
     这个 id 只存在本机 localStorage，登录、投屏、投屏记录都带上它。 */
  var BROWSER_ID_KEY = 'scrcpygate-device-id';

  function browserDeviceId() {
    if (state.browserDeviceId) return state.browserDeviceId;
    var value = '';
    try { value = String(window.localStorage.getItem(BROWSER_ID_KEY) || ''); } catch (e) { value = ''; }
    if (!/^b-[0-9a-z]{8,32}$/.test(value)) {
      var random = '';
      try {
        var bytes = new Uint8Array(8);
        if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(bytes);
        else for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
        for (var j = 0; j < bytes.length; j++) random += ('0' + bytes[j].toString(16)).slice(-2);
      } catch (e) {
        random = String(Date.now().toString(16)) + Math.random().toString(16).slice(2, 10);
      }
      value = 'b-' + random.slice(0, 16);
      try { window.localStorage.setItem(BROWSER_ID_KEY, value); } catch (e) {}
    }
    state.browserDeviceId = value;
    return value;
  }

  function resetVideoRateMeter() {
    state.videoRateBytes = 0;
    state.videoRateFrames = 0;
    if (state.videoRateTimer) {
      window.clearInterval(state.videoRateTimer);
      state.videoRateTimer = null;
    }
    if (state.videoRateMbps) {
      state.videoRateMbps = 0;
      emitVideoRate(0);
    }
    state.videoFps = 0;
    emitVideoFps(0);
  }

  function flushVideoRate() {
    var bytes = state.videoRateBytes;
    var frames = state.videoRateFrames;
    state.videoRateBytes = 0;
    state.videoRateFrames = 0;
    if (!state.videoSocket) {
      resetVideoRateMeter();
      return;
    }
    var mbps = Math.round((bytes * 8 / 1000000) * 10) / 10;
    // 指数平滑:编码器是 VBR,逐秒原始值抖动大,平滑后读数更稳定。
    state.videoRateMbps = state.videoRateMbps > 0
      ? Math.round((state.videoRateMbps * 0.4 + mbps * 0.6) * 10) / 10
      : mbps;
    emitVideoRate(state.videoRateMbps);
    // 实测帧率：同一个 1 秒窗口里收到的帧数（Raw v2 一个访问单元 = 一帧；legacy 一个包 = 一帧）。
    var fps = Math.max(0, Number(frames) || 0);
    state.videoFps = state.videoFps > 0 ? Math.round(state.videoFps * 0.4 + fps * 0.6) : fps;
    emitVideoFps(state.videoFps);
    // 记录里不要每秒一条码率：至少间隔 60s，或变化 ≥1 Mbps 且已过 15s 才记（帧率一并带上）。
    var now = Date.now();
    var elapsed = state.videoRecordLastRateAt ? now - state.videoRecordLastRateAt : Number.POSITIVE_INFINITY;
    var previous = state.videoRecordLastRateMbps;
    var bigChange = previous !== null && previous !== undefined
      && Math.abs(state.videoRateMbps - previous) >= VIDEO_RECORD_RATE_DELTA_MBPS;
    if (elapsed >= VIDEO_RECORD_RATE_REPORT_MS || (bigChange && elapsed >= 15000)) {
      state.videoRecordLastRateAt = now;
      state.videoRecordLastRateMbps = state.videoRateMbps;
      videoRecord('rate', 'info', '实测码率', { mbps: state.videoRateMbps, fps: state.videoFps });
    }
  }

  function noteVideoBytes(count) {
    var bytes = Number(count) || 0;
    if (bytes <= 0) return;
    state.videoRateBytes += bytes;
    state.videoRateFrames = (state.videoRateFrames || 0) + 1;
    if (!state.videoRateTimer) {
      state.videoRateTimer = window.setInterval(flushVideoRate, VIDEO_RATE_WINDOW_MS);
    }
  }

  function openVideoSocket(deviceId, viewerToken) {
    // 以视频通道实际绑定的设备为准判断"换设备":状态里的 deviceId 可能已被
    // sessions.create 提前改写,不能作为唯一依据。
    var deviceChanged = String(state.videoSocketDevice || state.deviceId || '') !== String(deviceId || '');
    // 换设备必须丢弃上一台设备的控制通道,否则按键/手势会继续发到旧设备。
    if (deviceChanged) closeControlSocket();
    closeVideoSocket(deviceChanged);
    state.videoSocketDevice = deviceId;
    state.deviceId = deviceId;
    state.viewerToken = viewerToken || '';
    state.watchActive = true;
    if (!state.authInvalid) state.viewerStopTerminal = false;
    state.firstFrameRecoveryCount = 0;
    resetRawV2State(true);
    evaluateViewerStopPolicy(Date.now());
    var query = '';
    var queryParts = [];
    if (state.viewerToken) queryParts.push('viewer_token=' + encodeURIComponent(state.viewerToken));
    // 带上稳定的浏览器设备标识：服务端据此判断「是不是同一台设备」，投屏记录里的 id
    // 因此不会每次进出都变（连接级 client_id 每次握手都会变）。
    queryParts.push('browser_id=' + encodeURIComponent(browserDeviceId()));
    if (queryParts.length) query = '?' + queryParts.join('&');
    var url = wsBase() + '/ws/devices/' + encodeURIComponent(deviceId) + '/video' + query;
    rawRecord('state', 'video_socket_open', { url: url, retry: state.videoRetryCount || 0 });
    var ws;
    try { ws = new window.WebSocket(url); } catch (e) { attemptVideoReconnect(); return; }
    state.videoSocket = ws;
    ws.binaryType = 'arraybuffer';
    ws.onopen = function () {
      if (state.videoSocket !== ws) return;
      ws._openedAt = Date.now();
      emitVideoLifecycle('scrcpygate:videoopen');
    };
    ws.onmessage = function (event) {
      if (state.videoSocket !== ws) return;
      if (typeof event.data === 'string') {
        var msg;
        try { msg = JSON.parse(event.data); } catch (e) {
          rawRecord('ws-in', 'unparsed', String(event.data).slice(0, 2000));
          return;
        }
        // 原始层：控制面消息原文（派生事件只保留结论，原文才能看到被摘要掉的字段）。
        rawRecord('ws-in', String(msg.type || 'unknown'), msg);
        if (msg.type === 'hello') {
          emitVideoLifecycle('scrcpygate:videohello');
          scheduleFirstFrameWatchdog();
          state.rawV2Transport = String(msg.video_transport || '');
          state.rawV2PacketUnit = String(msg.video_packet_unit || '');
          state.rawV2ProtocolVersion = Number(msg.video_protocol_version) || 0;
          if (msg.session) state.session = Object.assign({}, msg.session, { client_id: msg.client_id || '' });
          emitViewerUpdate(state.session, deviceId);
          // 画面（含中断后的重连）一旦握手成功，就核对一次控制权是否还在自己手上。
          revalidateControlOwnership();
          // 多端投屏记录：握手后对齐会话（晚到者补邀请、发起端刷新页面后拿回主导权）。
          attachMirrorRecord();
          if (msg.session && msg.session.video) {
            var v = msg.session.video;
            if (v && v.max_size) {
              var canvas = videoCanvas();
              if (canvas) {
                canvas.width = profileWidth(v.max_size);
                canvas.height = profileHeight(v.max_size);
                emitVideoSize(canvas.width, canvas.height);
              }
            }
          }
        } else if (msg.type === 'stream_reset') {
          var wasVisible = state.videoFrameReady || state.videoPlaying;
          videoRecord('reset', 'warn', '服务端重置画面流', {
            generation: msg.generation,
            was_visible: wasVisible
          });
          resetRawV2State(false);
          destroyPlayer();
          if (wasVisible) emitVideoLifecycle('scrcpygate:videodrop');
        } else if (msg.type === 'mirror_status' || msg.type === 'session_update') {
          if (msg.session) state.session = Object.assign({}, state.session || {}, msg.session);
          emitViewerUpdate(state.session, deviceId);
        } else if (msg.type === 'record_invite') {
          // 管理员发起了多端记录：本端是被邀请的观看端，交给页面弹窗询问是否参与。
          state.recordInvite = msg;
          state.recordInviteClientId = String(msg.client_id || '') || currentRecordClientId();
          emitRecordEvent('scrcpygate:record-invite', msg);
        } else if (msg.type === 'record_session') {
          // 服务端对齐后的会话快照：只有带着本机会话 id 回来的发起端会拿到 role=initiator，
          // 同账号的其它设备（手机端）拿到的是 role=participant。
          state.recordInvite = null;
          state.recordSession = msg.session || null;
          state.recordRole = String(msg.role || 'participant');
          state.recordArmed = recordRoleArmed(state.recordRole, msg.session);
          // 通知里带的是「这条通知属于哪个观看端」：宫格一页多端时，退出/响应必须认它。
          if (msg.client_id) state.recordInviteClientId = String(msg.client_id);
          emitRecordEvent('scrcpygate:record-session', {
            session: state.recordSession,
            role: state.recordRole,
            client_id: String(msg.client_id || '') || currentRecordClientId()
          });
        } else if (msg.type === 'record_upload_request') {
          state.recordInvite = null;
          emitRecordEvent('scrcpygate:record-upload-request', msg);
          uploadMirrorRecord(msg.session, msg.client_id);
        } else if (msg.type === 'record_participant') {
          applyRecordParticipant(msg);
        } else if (msg.type === 'record_bundle') {
          receiveRecordBundle(msg);
        } else if (msg.type === 'record_stopped') {
          state.recordInvite = null;
          state.recordInviteClientId = '';
          state.recordArmed = false;
          if (state.recordSession) state.recordSession.stopped = true;
          if (state.recordRole !== 'initiator') clearRecordClaim(String(msg.session || ''));
          emitRecordEvent('scrcpygate:record-stopped', msg);
        } else if (msg.type === 'record_stream_stopped') {
          // 画面停了但记录会话仍在（服务端不再因为停流终止记录）：只提示，不动状态。
          if (state.recordSession) state.recordSession.stream_stopped = true;
          emitRecordEvent('scrcpygate:record-stream-stopped', msg);
        }
        return;
      }
      var bytes = new Uint8Array(event.data);
      if (state.rawV2Transport === 'legacy-annexb') {
        noteVideoBytes(bytes.byteLength);
        rawRecord('packet', 'annexb', { bytes: bytes.byteLength, gap_ms: state.rawV2LastFedAt ? (Date.now() - state.rawV2LastFedAt) : null });
        state.rawV2LastFedAt = Date.now();
        if (!ensurePlayer()) { schedulePlayerRecovery(); return; }
        if (!state.rawV2KeyframeSeen) {
          state.rawV2KeyframeSeen = true;
          videoRecord('keyframe', 'info', '收到首个关键帧（legacy 通道）', { bytes: bytes.byteLength });
          emitVideoLifecycle('scrcpygate:videokeyframe');
        }
        try { state.jmuxer.feed({ video: bytes, duration: 0 }); } catch (e) {}
        return;
      }
      try {
        var packet = parseRawV2Packet(bytes);
        if (!acceptRawV2Packet(packet)) return;
        noteVideoBytes(packet.payload.byteLength);
        // 原始层：每个 Raw v2 包的包头（序号/类型/长度/代数/帧间隔），不含像素数据。
        // 派生事件只记「异常」，这里把每一帧的元数据都留下，便于对齐服务端日志与
        // 复现「帧在到但画面不动」这类问题。
        rawRecord('packet', packet.keyframe ? 'keyframe' : 'frame', {
          sequence: packet.sequence,
          flags: packet.flags,
          keyframe: !!packet.keyframe,
          discontinuity: !!packet.discontinuity,
          generation: packet.generation,
          bytes: bytes.byteLength,
          payload_bytes: packet.payload.byteLength,
          gap_ms: state.rawV2LastFedAt ? (Date.now() - state.rawV2LastFedAt) : null
        });
        state.rawV2LastFedAt = Date.now();
        // Do not mark a keyframe as consumed before the player exists. A
        // transient JMuxer/MSE construction failure must remain recoverable
        // by the watchdog instead of leaving the UI in a permanent wait state.
        if (!ensurePlayer()) { schedulePlayerRecovery(); return; }
        // 关键帧门控（与宫格 mirror-grid.js 同语义）：跳号说明中间缺了若干帧，
        // 此时继续喂 P 帧会让解码器找不到参考帧而输出黑/绿屏；重建后的新解码器
        // 更是必须从关键帧开始。命中时保持当前画面不变，并按需向服务端要一个新
        // 关键帧（服务端会立刻让设备补一个 IDR，通常几十毫秒）。
        if (state.rawV2KeyframeSeen && !packet.keyframe) {
          var expectedSequence = (state.rawV2LastFedSequence + 1) >>> 0;
          if (state.rawV2LastFedSequenceSeen && packet.sequence !== expectedSequence) {
            state.rawV2AwaitingKeyframe = true;
            videoRecord('gap', 'warn', '画面跳号，丢弃到下一关键帧', {
              expected: expectedSequence,
              received: packet.sequence,
              missing: ((packet.sequence - expectedSequence) >>> 0)
            });
          }
        }
        if (!state.rawV2KeyframeSeen && !packet.keyframe) {
          requestFreshKeyframe('before_first_keyframe');
          return;
        }
        if (state.rawV2AwaitingKeyframe && !packet.keyframe) {
          requestFreshKeyframe(state.rawV2LastFedSequenceSeen ? 'sequence_gap' : 'player_rebuilt');
          return;
        }
        if (packet.keyframe) {
          var wasWaitingKeyframe = state.rawV2AwaitingKeyframe || !state.rawV2KeyframeSeen;
          state.rawV2KeyframeSeen = true;
          state.rawV2AwaitingKeyframe = false;
          if (wasWaitingKeyframe) {
            videoRecord('keyframe', 'info', '关键帧到达，恢复解码', {
              sequence: packet.sequence,
              last_fed: state.rawV2LastFedSequence
            });
          }
          emitVideoLifecycle('scrcpygate:videokeyframe');
        }
        state.rawV2LastFedSequence = packet.sequence;
        state.rawV2LastFedSequenceSeen = true;
        // Raw v2/protocol packets are complete access units. Tell jMuxer so
        // it does not wait for the next NAL to flush one extra frame.
        try { state.jmuxer.feed({ video: packet.payload, duration: 0, isLastVideoFrameComplete: true }); state.jmuxerFeedCount = (state.jmuxerFeedCount || 0) + 1; } catch (e) {
          rawRecord('player', 'feed_failed', { error: String((e && e.message) || e), sequence: packet.sequence });
        }
      } catch (e) {
        state.watchActive = false;
        state.videoSurfaceReady = false;
        // A malformed packet is terminal for this viewer.  Clear the same
        // session state as stop-self so a later page action cannot reuse the
        // stale device or one-time viewer token.
        try { ws.close(1002, 'invalid raw v2 packet'); } catch (ignore) {}
        clearCurrentSession(deviceId, '');
        emitVideoLifecycle('scrcpygate:videofailed');
      }
    };
    ws.onclose = function (event) {
      // A previous socket can close after a new generation has already been
      // installed. Its event must not reset the new stream or schedule a
      // second reconnect.
      if (state.videoSocket !== ws) return;
      clearFirstFrameWatchdog();
      state.videoSocket = null;
      resetVideoRateMeter();
      resetRawV2State(true);
      if (!state.watchActive) {
        destroyPlayer();
        state.videoSurfaceReady = false;
        emitViewerUpdate(state.session, deviceId);
        return;
      }
      var closeReason = String(event && event.reason || '').toLowerCase();
      videoRecord('socket_close', 'warn', '视频通道关闭', {
        code: event && event.code,
        reason: event && event.reason ? String(event.reason).slice(0, 60) : '',
        retry: state.videoRetryCount
      });
      if (event && (event.code === 4401 || (event.code === 4403 && /session|auth|login/.test(closeReason)))) {
        invalidateAuthentication();
        return;
      }
      // 到期账户仍然保持登录（可以浏览与续期），只是投屏被拒绝：不要把它当作
      // 登录失效踢回登录页，而是停止观看并明确告知原因。
      if (event && event.code === 4403 && /account/.test(closeReason)) {
        giveUpVideoWatch();
        try { document.dispatchEvent(new CustomEvent('scrcpygate:account-expired', { detail: { scope: 'mirror' } })); } catch (e) {}
        emitViewerUpdate(state.session, deviceId);
        return;
      }
      // 权限撤销或服务端明确终止当前观看时，不再自动重连；普通网络抖动仍走退避重连。
      if (event && [4403, 4410, 4411, 4412].indexOf(event.code) >= 0) {
        if (event.code === 4403 && window.ScrcpyGateAccess) window.ScrcpyGateAccess.check();
        giveUpVideoWatch();
        emitViewerUpdate(state.session, deviceId);
        return;
      }
      if (ws._openedAt && Date.now() - ws._openedAt >= VIDEO_STABLE_MS) {
        state.videoRetryCount = 0;
      }
      emitVideoLifecycle('scrcpygate:videodrop');
      if (window.ScrcpyGateAccess) {
        window.ScrcpyGateAccess.check().then(function (result) {
          if (state.videoSocket || !state.watchActive || state.videoSocketDevice !== deviceId) return;
          if (result.blocked) { giveUpVideoWatch(); return; }
          attemptVideoReconnect();
        });
      } else attemptVideoReconnect();
    };
    ws.onerror = function () {
      if (state.videoSocket !== ws) return;
      try { ws.close(); } catch (e) {}
    };
  }

  function closeVideoSocket(destroy) {
    cancelVideoReconnect();
    clearFirstFrameWatchdog();
    resetVideoRateMeter();
    if (state.playerRecoveryTimer) {
      window.clearTimeout(state.playerRecoveryTimer);
      state.playerRecoveryTimer = null;
    }
    if (state.videoSocket) {
      try { state.videoSocket.close(); } catch (e) {}
      state.videoSocket = null;
      state.videoSocketDevice = '';
    }
    if (destroy) {
      resetRawV2State(true);
      state.watchActive = false;
      state.videoRetryCount = 0;
      destroyPlayer();
    }
  }

  function emitViewerUpdate(session, deviceId) {
    var s = session || {};
    var count = Number(s.viewer_count != null ? s.viewer_count : s.clients);
    if (!isFinite(count)) count = 0;
    // 会话自带 video 时同步画质胶囊:开始投屏后无需等设备列表刷新。
    var quality = adaptQuality(s.video);
    if (quality) {
      try {
        document.dispatchEvent(new CustomEvent('scrcpygate:quality', { detail: quality }));
      } catch (e) {}
    }
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:viewers', {
        detail: { deviceId: deviceId || state.deviceId || '', count: Math.max(0, count), session: s }
      }));
    } catch (e) {}
  }

  /* ---------------- 视频断线自动重连 ---------------- */

  var VIDEO_RETRY_DELAYS = [1000, 3000, 7000, 15000, 30000, 60000];
  var VIDEO_STABLE_MS = 5000;

  function emitVideoLifecycle(name) {
    var mapped = VIDEO_RECORD_LIFECYCLE[name];
    if (mapped) videoRecord(name.slice('scrcpygate:'.length), mapped[1], mapped[0], videoRecordLifecycleData(name));
    try { document.dispatchEvent(new CustomEvent(name)); } catch (e) {}
  }

  /* ---------------- 投屏实时记录（管理员排查用，只存在本机浏览器） ----------------
     记录的是"画面管道上发生了什么"：连接、握手、关键帧、跳号丢帧、重建解码器、
     延迟回跳、服务端重置、通道关闭原因、码率/分辨率/方向变化、控制权变化等。
     设计取舍：
       - 只进内存（环形缓冲），不落盘也不上报，避免给服务端加存储/隐私成本；
       - 码率这类高频事件做节流，否则 400 条缓冲几秒就被刷满；
       - 导出走 text()/json()，管理员可以直接把证据贴进 issue。 */

  var VIDEO_RECORD_LIFECYCLE = {
    'scrcpygate:videoopen': ['视频通道已打开，等待握手', 'info'],
    'scrcpygate:videohello': ['握手完成，等待关键帧', 'info'],
    'scrcpygate:videoplaying': ['画面开始播放', 'info'],
    'scrcpygate:videodrop': ['画面中断，进入恢复流程', 'warn'],
    'scrcpygate:videofailed': ['画面失败，已停止自动重连', 'error']
  };

  function videoRecordAllowed() {
    // 多端记录时，被邀请并同意的观看端（可能是普通用户）也要记录自己那份。
    if (state.recordArmed) return true;
    try {
      var session = window.ScrcpyGateSession;
      var user = session && typeof session.current === 'function' ? session.current() : null;
      if (!user) return false;
      return user.roleKey === 'admin' || user.role === '管理员' || user.isAdmin === true;
    } catch (e) {
      return false;
    }
  }

  function videoRecordLifecycleData(name) {    if (name === 'scrcpygate:videodrop' || name === 'scrcpygate:videofailed') {
      return { sequence: state.rawV2Sequence, last_fed: state.rawV2LastFedSequence, retry: state.videoRetryCount };
    }
    if (name === 'scrcpygate:videohello') {
      return { transport: state.rawV2Transport || '', protocol: state.rawV2ProtocolVersion };
    }
    return null;
  }

  function videoRecord(kind, level, text, data) {
    // 管理员专用：非管理员连采集都不做（不留数据、也不占内存），
    // 面板入口另外由 data-admin-only + mirrorIsAdmin() 双重把关。
    if (!videoRecordAllowed()) return null;
    if (!state.videoRecord) state.videoRecord = [];
    var entry = {
      seq: (state.videoRecordSeq = (state.videoRecordSeq || 0) + 1),
      t: Date.now(),
      kind: String(kind || 'event'),
      level: level === 'warn' || level === 'error' ? level : 'info',
      text: String(text == null ? '' : text),
      data: data && typeof data === 'object' ? data : null
    };
    state.videoRecord.push(entry);
    if (!state.videoRecordFirstAt) state.videoRecordFirstAt = entry.t;
    if (state.videoRecord.length > VIDEO_RECORD_LIMIT) {
      state.videoRecord.splice(0, state.videoRecord.length - VIDEO_RECORD_LIMIT);
    }
    var subs = state.videoRecordSubs || [];
    for (var i = 0; i < subs.length; i++) {
      try { subs[i](entry); } catch (e) {}
    }
    return entry;
  }

  function videoRecordSubscribe(fn) {
    if (typeof fn !== 'function') return function () {};
    if (!state.videoRecordSubs) state.videoRecordSubs = [];
    state.videoRecordSubs.push(fn);
    return function () {
      var list = state.videoRecordSubs || [];
      var index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    };
  }

  function videoRecordEntries() {
    return (state.videoRecord || []).slice();
  }

  function videoRecordClear() {
    state.videoRecord = [];
    state.videoRecordSeq = 0;
    state.videoRecordFirstAt = 0;
    state.videoRecordLastRateAt = 0;
    state.videoRecordLastRateMbps = null;
    return true;
  }

  function videoRecordStats() {
    var list = state.videoRecord || [];
    var stats = {
      total: list.length,
      warn: 0,
      error: 0,
      kinds: {},
      since: state.videoRecordFirstAt || 0,
      until: 0,
      duration_ms: 0,
      last_rate_mbps: state.videoRateMbps || 0,
      device_id: state.deviceId || ''
    };
    for (var i = 0; i < list.length; i++) {
      var entry = list[i];
      if (entry.level === 'warn') stats.warn += 1;
      else if (entry.level === 'error') stats.error += 1;
      stats.kinds[entry.kind] = (stats.kinds[entry.kind] || 0) + 1;
      stats.until = entry.t;
    }
    stats.duration_ms = stats.since && stats.until ? stats.until - stats.since : 0;
    return stats;
  }

  function videoRecordTime(ms) {
    var date = new Date(Number(ms) || Date.now());
    return pad2(date.getHours()) + ':' + pad2(date.getMinutes()) + ':' + pad2(date.getSeconds())
      + '.' + String(1000 + date.getMilliseconds()).slice(1);
  }

  // 一行文本：「时间 [级别/类型] 说明（附加字段）」，方便直接粘贴到 issue。
  function videoRecordLine(entry) {
    var extra = '';
    if (entry.data) {
      var parts = [];
      Object.keys(entry.data).forEach(function (key) {
        var value = entry.data[key];
        if (value === undefined || value === null || value === '') return;
        parts.push(key + '=' + String(value));
      });
      if (parts.length) extra = '（' + parts.join(' ') + '）';
    }
    return videoRecordTime(entry.t) + ' [' + entry.level + '/' + entry.kind + '] ' + entry.text + extra;
  }

  function videoRecordText() {
    var list = state.videoRecord || [];
    var lines = [
      'ScrcpyGate 投屏实时记录',
      '设备: ' + (state.deviceId || '-') + '  通道: ' + (state.rawV2Transport || '-')
        + '  条数: ' + list.length + '  导出时间: ' + new Date().toISOString()
    ];
    for (var i = 0; i < list.length; i++) lines.push(videoRecordLine(list[i]));
    return lines.join('\n');
  }

  function videoRecordJson() {
    return {
      device_id: state.deviceId || '',
      // 稳定标识：同一台设备跨会话不变；client_id 是本次连接的，两个都留着便于对照。
      browser_id: browserDeviceId(),
      client_id: (state.session && state.session.client_id) || '',
      transport: state.rawV2Transport || '',
      fps: state.videoFps || 0,
      mbps: state.videoRateMbps || 0,
      // 原始数据层：控制面原文 / 包头元数据 / 接口调用 / 状态切换 / 环境快照。
      raw: videoRecordRawJson(),
      generated_at: new Date().toISOString(),
      stats: videoRecordStats(),
      entries: (state.videoRecord || []).map(function (entry) {
        return {
          seq: entry.seq,
          time: new Date(entry.t).toISOString(),
          level: entry.level,
          kind: entry.kind,
          text: entry.text,
          data: entry.data
        };
      })
    };
  }

  /* ---------------- 原始数据层（排查用，用户要求「不要漏掉任何东西」） ----------------
     派生事件（时间线）只记「我们判断重要的事」，参数被摘要、频次被节流，正好会把
     问题的关键漏掉。这里再加一层**原始记录**：
       ws-in   视频通道收到的每一条控制面 JSON（原文）
       ws-out  本端发出去的每一条控制消息（原文）
       packet  Raw v2 每个包的包头（序号/关键帧/长度/代数/时间间隔），不含像素数据
       api     每次接口请求（方法/URL/请求体/状态/耗时/耗时；投屏与记录接口连响应原文）
       state   页面状态切换（watchState、控制权、全屏/旋转、离开超时决策）
       env     记录开始时的环境快照（UA/视口/DPR/内核数/解码能力/服务端画质参数）
     边界：**不记录视频像素**（每秒几 MB，浏览器端受不了）；需要像素级证据时用服务端
     scrcpy 日志 + 关键帧截图。原始层有独立的条数/字节上限，超了丢最旧的并计数
     （dropped），导出时能看到有没有丢。 */
  var RAW_ENTRY_LIMIT = 20000;
  var RAW_BYTE_LIMIT = 4 * 1024 * 1024;
  var RAW_UPLOAD_BYTE_BUDGET = 1500 * 1024;

  function rawAllowed() {
    // 记录中（管理员或已同意参与的观看端）才采集；开关关闭时完全不占内存。
    if (state.videoRawEnabled === false) return false;
    return videoRecordAllowed();
  }

  function rawEnabled() {
    return state.videoRawEnabled !== false;
  }

  function rawSetEnabled(enabled) {
    state.videoRawEnabled = enabled !== false;
    return state.videoRawEnabled;
  }

  function rawBytesOf(entry) {
    try {
      return JSON.stringify(entry || {}).length;
    } catch (e) {
      return 0;
    }
  }

  function rawRecord(channel, kind, payload) {
    if (!rawAllowed()) return null;
    if (!state.videoRaw) state.videoRaw = [];
    var entry = {
      seq: (state.videoRawSeq = (state.videoRawSeq || 0) + 1),
      t: Date.now(),
      channel: String(channel || 'misc'),
      kind: String(kind || ''),
      payload: payload === undefined ? null : payload
    };
    var size = rawBytesOf(entry);
    state.videoRaw.push(entry);
    state.videoRawBytes = (state.videoRawBytes || 0) + size;
    while (state.videoRaw.length > RAW_ENTRY_LIMIT
      || (state.videoRawBytes > RAW_BYTE_LIMIT && state.videoRaw.length > 1)) {
      var dropped = state.videoRaw.shift();
      state.videoRawBytes -= rawBytesOf(dropped);
      state.videoRawDropped = (state.videoRawDropped || 0) + 1;
    }
    if (!state.videoRawFirstAt) state.videoRawFirstAt = entry.t;
    var subs = state.videoRawSubs || [];
    for (var i = 0; i < subs.length; i++) {
      try { subs[i](entry); } catch (e) {}
    }
    return entry;
  }

  function rawRecordState(kind, payload) {
    return rawRecord('state', kind, payload);
  }

  function rawRecordOut(payload) {
    return rawRecord('ws-out', (payload && payload.type) || 'message', payload);
  }

  function videoRecordRaw() {
    return (state.videoRaw || []).slice();
  }

  function videoRecordRawJson() {
    return {
      entries: videoRecordRaw(),
      stats: videoRecordRawStats()
    };
  }

  function videoRecordRawStats() {
    var list = state.videoRaw || [];
    var channels = {};
    var kinds = {};
    list.forEach(function (entry) {
      channels[entry.channel] = (channels[entry.channel] || 0) + 1;
      var key = entry.channel + ':' + entry.kind;
      kinds[key] = (kinds[key] || 0) + 1;
    });
    return {
      total: list.length,
      dropped: state.videoRawDropped || 0,
      bytes: state.videoRawBytes || 0,
      entry_limit: RAW_ENTRY_LIMIT,
      byte_limit: RAW_BYTE_LIMIT,
      first_at: state.videoRawFirstAt || 0,
      last_at: list.length ? list[list.length - 1].t : 0,
      channels: channels,
      kinds: kinds
    };
  }

  function videoRecordRawText() {
    var lines = ['ScrcpyGate 原始投屏数据', '', '# 时间 通道 类型 内容'];
    videoRecordRaw().forEach(function (entry) {
      var time = videoRecordTime(entry.t);
      var payload = '';
      try {
        payload = typeof entry.payload === 'string' ? entry.payload : JSON.stringify(entry.payload);
      } catch (e) {
        payload = '<unserializable>';
      }
      lines.push(time + ' [' + entry.channel + '/' + entry.kind + '] ' + payload);
    });
    return lines.join('\n');
  }

  function videoRecordRawSubscribe(fn) {
    if (typeof fn !== 'function') return function () {};
    if (!state.videoRawSubs) state.videoRawSubs = [];
    state.videoRawSubs.push(fn);
    return function () {
      var list = state.videoRawSubs || [];
      var index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    };
  }

  function videoRecordRawClear() {
    state.videoRaw = [];
    state.videoRawSeq = 0;
    state.videoRawBytes = 0;
    state.videoRawDropped = 0;
    state.videoRawFirstAt = 0;
    return true;
  }

  /* 环境快照：复现问题时「什么浏览器 / 什么视口 / 服务端给了什么参数」经常是关键。 */
  function rawRecordEnvironment(extra) {
    var video = (state.session && state.session.video) || {};
    var env = {
      user_agent: String((window.navigator && window.navigator.userAgent) || ''),
      platform: String((window.navigator && window.navigator.platform) || ''),
      languages: (window.navigator && window.navigator.languages) ? Array.prototype.slice.call(window.navigator.languages) : [],
      hardware_concurrency: Number((window.navigator && window.navigator.hardwareConcurrency) || 0) || null,
      device_memory_gb: Number((window.navigator && window.navigator.deviceMemory) || 0) || null,
      viewport: { width: window.innerWidth || 0, height: window.innerHeight || 0 },
      screen: { width: (window.screen && window.screen.width) || 0, height: (window.screen && window.screen.height) || 0 },
      device_pixel_ratio: Number(window.devicePixelRatio) || 1,
      visibility: String(document.visibilityState || ''),
      webcodecs: typeof window.VideoDecoder === 'function',
      media_source: !!(window.MediaSource || window.ManagedMediaSource),
      secure_context: window.isSecureContext === true,
      device_id: state.deviceId || '',
      browser_id: browserDeviceId(),
      client_id: (state.session && state.session.client_id) || '',
      video_transport: state.rawV2Transport || '',
      video_options: video,
      quality: state.videoQuality || null
    };
    if (extra && typeof extra === 'object') {
      Object.keys(extra).forEach(function (key) { env[key] = extra[key]; });
    }
    return rawRecord('env', 'snapshot', env);
  }

  function trimRawForUpload(timeline) {
    // 服务端对上传体积有兜底上限（默认 2 MiB）：原始层优先保留最新的，
    // 并明确标出被裁剪的条数，避免「看起来完整其实被截了」。
    try {
      var raw = timeline && timeline.raw;
      if (!raw || !raw.entries) return timeline;
      var budget = RAW_UPLOAD_BYTE_BUDGET;
      var kept = raw.entries.slice();
      var trimmed = 0;
      while (kept.length && JSON.stringify(kept).length > budget) {
        kept.shift();
        trimmed += 1;
      }
      raw.entries = kept;
      raw.stats = Object.assign({}, raw.stats, { trimmed_for_upload: trimmed, upload_budget_bytes: budget });
      return timeline;
    } catch (e) {
      return timeline;
    }
  }
  /* ---------------- 多端投屏记录（同设备多观看端协同，服务端只做内存中继） ----------------
     流程：管理员点「多端记录」→ 服务端建内存会话并给同设备其他观看端下发邀请 →
     被邀请端弹窗确认 → 管理员停止记录 → 服务端请各参与端上传 → 各端上传**完整**记录 →
     服务端实时转发给发起端浏览器做汇总/导出。全程不落盘。 */

  function emitRecordEvent(name, detail) {
    try { document.dispatchEvent(new CustomEvent(name, { detail: detail || {} })); } catch (e) {}
  }

  /* 「我是这次记录的发起端」必须能被服务端验证：刷新页面后 client_id 会变，光靠用户名
     会把同一账号的另一台设备（手机端）也算成主端。因此发起/参与时把「会话 id + 角色」
     存在本机 localStorage，重连时作为 claim 带上去；只有能出示它的页面才是主端。 */
  var RECORD_CLAIM_KEY = 'scrcpygate-record-claim';

  function readRecordClaim() {
    try {
      var raw = window.localStorage.getItem(RECORD_CLAIM_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      var session = String(parsed.session || '');
      var role = String(parsed.role || '');
      if (!session || (role !== 'initiator' && role !== 'participant')) return null;
      return { session: session, role: role };
    } catch (e) {
      return null;
    }
  }

  function writeRecordClaim(session, role) {
    var id = String((session && session.session) || session || '');
    if (!id) return;
    try {
      window.localStorage.setItem(RECORD_CLAIM_KEY, JSON.stringify({ session: id, role: role }));
    } catch (e) {}
  }

  function clearRecordClaim(sessionId) {
    try {
      var current = readRecordClaim();
      if (sessionId && current && current.session !== String(sessionId)) return;
      window.localStorage.removeItem(RECORD_CLAIM_KEY);
    } catch (e) {}
  }

  function recordRoleArmed(role, session) {
    if (role === 'initiator') return true;
    if (role !== 'participant') return false;
    var mine = recordParticipantOf(session, currentRecordClientId());
    return !!(mine && mine.state === 'accepted');
  }

  function recordParticipantOf(session, clientId) {
    var list = (session && session.participants) || [];
    for (var i = 0; i < list.length; i++) {
      if (String(list[i].client_id) === String(clientId)) return list[i];
    }
    return null;
  }

  /* 握手后调用：把「本机 claim」交给服务端，由服务端决定本端是主端还是副端，
     并补发邀请/上传请求（晚一步接入的观看端因此也能收到邀请）。 */
  function attachMirrorRecord() {
    var clientId = currentRecordClientId();
    var deviceId = mirrorRecordDeviceId();
    if (!clientId || !deviceId) return Promise.resolve(null);
    var claim = readRecordClaim();
    return apiPost(recordEndpoint('attach'), {
      client_id: clientId,
      browser_id: browserDeviceId(),
      session: claim ? claim.session : '',
      role: claim ? claim.role : ''
    }).then(function (payload) {
      return unwrapRecordPayload(payload);
    }).catch(function () {
      // 老服务端没有该接口，或连接刚建立就断开：不影响观看与本地记录。
      return null;
    });
  }

  function currentRecordClientId() {
    return String((state.session && state.session.client_id) || '');
  }

  function mirrorRecordDeviceId() {
    return String(state.videoSocketDevice || state.deviceId || '');
  }

  function unwrapRecordPayload(payload) {
    if (payload && payload.data && typeof payload.data === 'object') return payload.data;
    return payload && typeof payload === 'object' ? payload : {};
  }

  function recordEndpoint(suffix) {
    return '/api/devices/' + encodeURIComponent(mirrorRecordDeviceId()) + '/mirror/record/' + suffix;
  }

  function startMirrorRecord() {
    // 发起多端记录：需要本端已经在看这台设备（服务端按 client_id 认发起端）。
    var clientId = currentRecordClientId();
    if (!mirrorRecordDeviceId() || !clientId) {
      return Promise.reject(new Error('record_client_required'));
    }
    return apiPost(recordEndpoint('start'), { client_id: clientId }).then(function (payload) {
      var data = unwrapRecordPayload(payload);
      state.recordSession = data.session || null;
      state.recordArmed = true;
      state.recordRole = 'initiator';
      state.recordInvite = null;
      // 本机记下「我发起的这次会话」：刷新页面后靠它向服务端证明主端身份。
      writeRecordClaim(state.recordSession, 'initiator');
      // 记录开始时留一份环境快照 + 一次状态标记（复现问题常要看这些）。
      rawRecordEnvironment({ record_session: (state.recordSession && state.recordSession.session) || '', role: 'initiator' });
      rawRecordState('record_start', { mode: 'multi', session: (state.recordSession && state.recordSession.session) || '' });
      videoRecord('record_start', 'info', '发起多端投屏记录', {
        invited: data.invited,
        delivered: data.delivered
      });
      emitRecordEvent('scrcpygate:record-started', state.recordSession);
      return state.recordSession;
    });
  }

  function respondMirrorRecord(sessionId, accept, clientId) {
    var targetClient = String(clientId || state.recordInviteClientId || currentRecordClientId());
    return apiPost(recordEndpoint('respond'), {
      session: String(sessionId || ''),
      client_id: targetClient,
      accept: !!accept
    }).then(function (payload) {
      var data = unwrapRecordPayload(payload);
      state.recordInvite = null;
      state.recordInviteClientId = '';
      state.recordArmed = !!accept;
      if (accept) {
        state.recordRole = 'participant';
        // 参与端也记下会话：刷新页面后服务端据此补发上传请求（而不是重新弹邀请）。
        writeRecordClaim(String(sessionId || ''), 'participant');
      } else {
        clearRecordClaim(String(sessionId || ''));
      }
      videoRecord('record_response', accept ? 'info' : 'warn',
        accept ? '同意参与管理员发起的多端记录' : '拒绝参与管理员发起的多端记录', { session: String(sessionId || '') });
      emitRecordEvent('scrcpygate:record-responded', { accept: !!accept, participant: data.participant || null });
      return data;
    });
  }

  // 上传本端那份**完整**记录（不做字段裁剪）；服务端原样转发给发起端。
  // `clientId` 是收到上传请求的那个观看端：宫格一页多个观看端时必须显式传。
  function uploadMirrorRecord(sessionId, clientId) {
    var targetClient = String(clientId || currentRecordClientId());
    if (!sessionId || !targetClient || !mirrorRecordDeviceId()) {
      return Promise.reject(new Error('record_client_required'));
    }
    var timeline = videoRecordJson();
    return apiPost(recordEndpoint('upload'), {
      session: String(sessionId),
      client_id: targetClient,
      browser_id: browserDeviceId(),
      timeline: trimRawForUpload(timeline)
    }).then(function (payload) {
      var data = unwrapRecordPayload(payload);
      var delivered = data.delivered !== false;
      videoRecord('record_upload', delivered ? 'info' : 'warn',
        delivered ? '本端记录已送达发起端' : '本端记录未能送达发起端（对方已离线）', {
          entries: data.entries,
          bytes: data.bytes
        });
      state.recordArmed = false;
      clearRecordClaim(String(sessionId));
      emitRecordEvent('scrcpygate:record-uploaded', data);
      return data;
    }).catch(function (error) {
      var message = error && error.message ? String(error.message) : 'unknown';
      videoRecord('record_upload', 'error', '本端记录上传失败', { reason: message.slice(0, 160) });
      emitRecordEvent('scrcpygate:record-upload-failed', { error: message });
      throw error;
    });
  }

  function stopMirrorRecord() {
    var session = state.recordSession && state.recordSession.session;
    if (!session) return Promise.resolve(null);
    return apiPost(recordEndpoint('stop'), { session: String(session) }).then(function (payload) {
      var data = unwrapRecordPayload(payload);
      if (data.session) state.recordSession = data.session;
      state.recordArmed = false;
      // 会话结束：本机不再对这次会话主张任何角色。
      clearRecordClaim(session);
      videoRecord('record_stop', 'info', '停止多端记录，等待其他端上传', {
        upload_requests: data.upload_requests
      });
      emitRecordEvent('scrcpygate:record-stop-requested', state.recordSession);
      return data;
    });
  }

  function applyRecordParticipant(msg) {
    var participant = msg && msg.participant;
    if (!participant || !state.recordSession || state.recordSession.session !== msg.session) return;
    var list = state.recordSession.participants || [];
    var replaced = false;
    for (var i = 0; i < list.length; i++) {
      if (String(list[i].client_id) === String(participant.client_id)) {
        list[i] = participant;
        replaced = true;
        break;
      }
    }
    if (!replaced) list.push(participant);
    state.recordSession.participants = list;
    emitRecordEvent('scrcpygate:record-participants', state.recordSession);
  }

  var RECORD_BUNDLE_LIMIT = 16;

  function receiveRecordBundle(msg) {
    if (!msg || !msg.timeline) return;
    state.recordBundles.push({
      session: msg.session,
      from: msg.from || {},
      received_at: msg.received_at || Math.floor(Date.now() / 1000),
      bytes: msg.bytes || 0,
      timeline: msg.timeline
    });
    if (state.recordBundles.length > RECORD_BUNDLE_LIMIT) {
      state.recordBundles.splice(0, state.recordBundles.length - RECORD_BUNDLE_LIMIT);
    }
    videoRecord('record_bundle', 'info', '收到其他观看端的记录', {
      user: (msg.from && msg.from.username) || '',
      entries: (msg.timeline.entries || []).length
    });
    // 服务端会把该参与端的最新状态一并带来（uploaded）；不更新的话面板会一直显示"已收到 0 份"。
    if (msg.participant) {
      applyRecordParticipant({ session: msg.session, participant: msg.participant });
    }
    emitRecordEvent('scrcpygate:record-bundle', state.recordBundles[state.recordBundles.length - 1]);
  }

  // 汇总导出：本端 + 各参与端的**完整**记录，按端分段（不同机器时钟可能不同，不混排）。
  function recordBundleJson() {
    return {
      kind: 'scrcpygate-mirror-record-bundle',
      generated_at: new Date().toISOString(),
      device_id: state.deviceId || '',
      browser_id: browserDeviceId(),
      session: state.recordSession ? state.recordSession.session : '',
      initiator: state.recordSession ? state.recordSession.initiator : '',
      participants: state.recordSession ? state.recordSession.participants || [] : [],
      local: videoRecordJson(),
      remote: (state.recordBundles || []).map(function (bundle) {
        return {
          from: bundle.from,
          received_at: bundle.received_at,
          bytes: bundle.bytes,
          timeline: bundle.timeline
        };
      })
    };
  }

  function recordBundleText() {
    var lines = [];
    lines.push('ScrcpyGate 多端投屏记录汇总');
    lines.push('设备: ' + (state.deviceId || '-')
      + '  会话: ' + (state.recordSession ? state.recordSession.session : '-')
      + '  参与端记录: ' + (state.recordBundles || []).length + ' 份');
    lines.push('');
    lines.push('===== 本端记录 =====');
    lines.push(videoRecordText());
    (state.recordBundles || []).forEach(function (bundle) {
      var from = bundle.from || {};
      lines.push('');
      lines.push('===== 参与端 ' + (from.username || '?') + ' (' + String(from.client_id || '').slice(0, 8)
        + ') · 收到于 ' + videoRecordTime(Number(bundle.received_at) * 1000)
        + ' · ' + ((bundle.timeline && bundle.timeline.entries) || []).length + ' 条 =====');
      var timeline = bundle.timeline || {};
      lines.push('设备: ' + (timeline.device_id || '-') + '  通道: ' + (timeline.transport || '-'));
      (timeline.entries || []).forEach(function (entry) {
        lines.push(videoRecordLine({
          t: Number(entry.t) || 0,
          level: entry.level || 'info',
          kind: entry.kind || 'event',
          text: entry.text || '',
          data: entry.data || null
        }));
      });
    });
    return lines.join('\n');
  }

  function clearRecordBundles() {
    state.recordBundles = [];
    emitRecordEvent('scrcpygate:record-bundle', null);
    return true;
  }

  function cancelVideoReconnect() {
    if (state.videoRetryTimer) {      window.clearTimeout(state.videoRetryTimer);
      state.videoRetryTimer = null;
    }
  }

  function giveUpVideoWatch() {
    var deviceId = state.deviceId;
    cancelVideoReconnect();
    state.viewerStopTerminal = true;
    state.watchActive = false;
    state.videoRetryCount = 0;
    closeControlSocket();
    resetViewerStopTracking(false);
    clearCurrentSession(deviceId, '');
    emitVideoLifecycle('scrcpygate:videofailed');
    if (!state.videoSocket) {
      destroyPlayer();
      state.videoSurfaceReady = false;
    }
  }

  function invalidateAuthentication(announceEvent) {
    if (state.authInvalid) return;
    state.authInvalid = true;
    state.viewerStopTerminal = true;
    state.watchActive = false;
    cancelVideoReconnect();
    resetViewerStopTracking(false);
    var deviceId = state.deviceId;
    closeVideoSocket(true);
    closeControlSocket();
    state.session = null;
    state.viewerToken = null;
    state.deviceId = null;
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:viewer-stopped', {
        detail: { deviceId: deviceId || '', mode: '', reason: 'auth_invalid' }
      }));
    } catch (e) {}
    if (announceEvent !== false) {
      try { document.dispatchEvent(new CustomEvent('scrcpygate:auth-invalid')); } catch (e) {}
    }
  }

  function attemptVideoReconnect() {
    if (!state.watchActive || state.viewerStopTerminal || state.authInvalid || state.videoSocket || !state.deviceId) return;
    if (state.videoRetryCount >= VIDEO_RETRY_DELAYS.length) {
      giveUpVideoWatch();
      return;
    }
    var delay = VIDEO_RETRY_DELAYS[state.videoRetryCount];
    state.videoRetryCount += 1;
    delay = Math.round(delay * (0.8 + Math.random() * 0.4));
    cancelVideoReconnect();
    state.videoRetryTimer = window.setTimeout(function () {
      state.videoRetryTimer = null;
      if (!state.watchActive || state.viewerStopTerminal || state.authInvalid || state.videoSocket || !state.deviceId) return;
      // 重连统一走 mirror/start:对运行中会话幂等(不重启流),会话已停时重新拉起,
      // 且总是签发新的 viewer_token(旧 token 15s 过期且一次性消费,不可复用)。
      apiPost('/api/devices/' + encodeURIComponent(state.deviceId) + '/mirror/start', {}).then(function (payload) {
        if (!state.watchActive || state.viewerStopTerminal || state.authInvalid || state.videoSocket) return;
        if (payload && payload.ok) {
          openVideoSocket(state.deviceId, payload.viewer_token || '');
        } else if (state.videoRetryCount >= VIDEO_RETRY_DELAYS.length) {
          giveUpVideoWatch();
        } else {
          attemptVideoReconnect();
        }
      }).catch(function (error) {
        if (!state.watchActive) return;
        var status = error && error.detail && error.detail.status;
        if (status === 401) {
          invalidateAuthentication();
          return;
        }
        if (status === 403 || status === 404) {
          giveUpVideoWatch();
          return;
        }
        if (state.videoRetryCount >= VIDEO_RETRY_DELAYS.length) {
          giveUpVideoWatch();
          return;
        }
        attemptVideoReconnect();
      });
    }, delay);
  }

  // 浏览器往返缓存(back/forward cache)恢复的页面不会重跑脚本,WebSocket 已死而画面
  // 停留在最后一帧——强制整页重载以重建观看管线。
  window.addEventListener('pageshow', function (event) {
    if (event && event.persisted) window.location.reload();
  });

  // Page Visibility is the primary policy signal. Window blur is tracked
  // separately and can never replace a hidden-page policy while hidden.
  document.addEventListener('visibilitychange', function () {
    evaluateViewerStopPolicy(Date.now());
    if (document.visibilityState === 'visible' && state.watchActive && !state.viewerStopTerminal && !state.authInvalid && !state.videoSocket) {
      attemptVideoReconnect();
    }
  });
  window.addEventListener('blur', function () {
    state.windowFocused = false;
    evaluateViewerStopPolicy(Date.now());
  });
  window.addEventListener('focus', function () {
    state.windowFocused = true;
    evaluateViewerStopPolicy(Date.now());
  });
  document.addEventListener('scrcpygate:auth-invalid', function () {
    invalidateAuthentication(false);
  });
  // 空闲/失焦自动停播也进记录：用户报"看着看着断了"时，这两条能直接区分原因。
  ['scrcpygate:viewer-stop-warning', 'scrcpygate:viewer-stopped', 'scrcpygate:viewer-stop-cancelled'].forEach(function (name) {
    document.addEventListener(name, function (event) {
      var detail = (event && event.detail) || {};
      var stopped = name === 'scrcpygate:viewer-stopped';
      var label = stopped ? '观看已停止'
        : (name === 'scrcpygate:viewer-stop-warning' ? '观看即将因空闲自动停止' : '已取消空闲自动停止');
      videoRecord('viewer_stop', stopped ? 'warn' : 'info', label, {
        reason: detail.reason || detail.mode || '',
        minutes: detail.minutes
      });
    });
  });

  /* ---------------- 控制协议管道 ---------------- */

  function setControlOwnership(ok, owner, reason) {
    var hadOwnership = !!state.controlOwnership;
    state.controlOwnership = !!ok;
    if (hadOwnership !== state.controlOwnership) {
      videoRecord('control', state.controlOwnership ? 'info' : 'warn',
        state.controlOwnership ? '取得设备控制权' : '失去设备控制权',
        { owner: owner ? String(owner) : '' });
    }
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:controlstate', {
        detail: { active: state.controlOwnership, owner: owner ? String(owner) : '' }
      }));
    } catch (e) {}
    if (!ok) {
      if (state.controlMoveRetryTimer) {
        window.clearTimeout(state.controlMoveRetryTimer);
        state.controlMoveRetryTimer = null;
      }
      state.pendingControlMove = null;
      stopControlKeepalive();
      destroyScrcpyInput();
    }
    if (hadOwnership && !ok && reason === 'taken_over') {
      document.dispatchEvent(new CustomEvent('scrcpygate:control-lost', {
        detail: { deviceId: state.controlSocketDevice, owner: owner || '', reason: reason }
      }));
    }
  }
  function stopControlKeepalive() {
    if (state.controlKeepaliveTimer) {
      window.clearInterval(state.controlKeepaliveTimer);
      state.controlKeepaliveTimer = null;
    }
  }
  function startControlKeepalive() {
    if (state.controlKeepaliveTimer) return;
    state.controlKeepaliveTimer = window.setInterval(function () {
      if (!state.controlSocket || state.controlSocket.readyState !== 1) {
        stopControlKeepalive();
        setControlOwnership(false);
        return;
      }
      state.controlSocket.send(JSON.stringify({ type: 'control_keepalive' }));
    }, 30000);
  }

  function destroyScrcpyInput() {
    var wasKeyboardOn = !!state.keyboardOn;
    if (state.scrcpyInput) {
      try { state.scrcpyInput.destroy(); } catch (e) {}
      state.scrcpyInput = null;
    }
    state.keyboardOn = false;
    if (wasKeyboardOn) emitKeyboardState(false);
  }

  function emitKeyboardState(active) {
    state.keyboardOn = !!active;
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:keyboard', {
        detail: { active: state.keyboardOn }
      }));
    } catch (e) {}
  }

  function emitControlFailure(message) {
    var detail = String(message || '控制指令发送失败');
    try {
      document.dispatchEvent(new CustomEvent('scrcpygate:controlerror', { detail: { message: detail } }));
    } catch (e) {}
  }

  function controlSend(msg) {
    if (!state.controlSocket || state.controlSocket.readyState !== 1) return false;
    state.controlSocket.send(typeof msg === 'string' ? msg : JSON.stringify(msg));
    // 原始层：控制通道发出的每条消息（按键/触摸/导航等），文本消息原样记，二进制记长度。
    if (typeof msg === 'string') rawRecord('ws-out', 'control-text', String(msg).slice(0, 2000));
    else rawRecord('ws-out', 'control-binary', { type: (msg && msg.type) || '', bytes: msg ? JSON.stringify(msg).length : 0 });
    return true;
  }

  /* 控制权自愈：画面中断重连后，服务端可能已经把旧 lease 释放掉
     （会话自动停止/超出时长/被管理员释放），此后每个控制事件都会被丢弃并回
     `control_lock ok:false`。客户端必须把服务端回复当权威：
     先自动重新获取一次（重连后不该再让用户手动开控制），拿不到就清掉本地标记，
     让按钮如实显示「获取控制 / 接管控制」。 */
  function recoverControlOwnership() {
    if (!state.controlOwnership) return false;
    if (state.pendingControl) return false;
    var socket = state.controlSocket;
    if (!socket || socket.readyState !== 1) { setControlOwnership(false); return false; }
    var request = { kind: 'acquire', resolve: function () {}, reject: function () {}, timer: null };
    request.timer = window.setTimeout(function () {
      if (state.pendingControl === request) state.pendingControl = null;
      setControlOwnership(false);
    }, 8000);
    state.pendingControl = request;
    if (!controlSend({ type: 'acquire_control', force: false })) {
      window.clearTimeout(request.timer);
      if (state.pendingControl === request) state.pendingControl = null;
      setControlOwnership(false);
      return false;
    }
    return true;
  }

  /* 画面重连成功后主动校验一次控制权：keepalive 的回复会走同一个 control_lock
     处理分支，lease 丢了就立刻自愈，而不是等用户下一次触摸才发现没反应。 */
  function revalidateControlOwnership() {
    if (!state.controlOwnership || state.pendingControl) return;
    var socket = state.controlSocket;
    if (!socket || socket.readyState !== 1) return;
    controlSend({ type: 'control_keepalive' });
  }

  // Public lock broadcasts omit the connection ID. Ask this control socket
  // to verify its lease, including when another browser uses the same account.
  document.addEventListener('scrcpygate:control-lock-changed', function (event) {
    var detail = event.detail || {};
    if (String(detail.device_id || '') !== String(state.controlSocketDevice || '')) return;
    revalidateControlOwnership();
  });

  function openControlSocket(deviceId, user) {
    if (state.controlSocket && state.controlSocket.readyState === 1) {
      // 只有同一台设备的控制通道可以复用;复用别的设备会让输入发到旧设备。
      if (String(state.controlSocketDevice || '') === String(deviceId || '')) return Promise.resolve(state.controlSocket);
    }
    closeControlSocket();
    return new Promise(function (resolve) {
      var url = wsBase() + '/ws/devices/' + encodeURIComponent(deviceId) + '/control';
      var ws;
      try { ws = new window.WebSocket(url); } catch (e) { resolve(null); return; }
      state.controlSocket = ws;
      state.controlSocketDevice = deviceId || '';
      ws.binaryType = 'arraybuffer';
      state.controlCurrentUser = user || '';
      var opened = false;
      ws.onopen = function () {
        if (state.controlSocket !== ws) { resolve(null); return; }
        opened = true;
        resolve(ws);
      };
      ws.onmessage = function (event) {
        if (state.controlSocket !== ws) return;
        var msg;
        try { msg = JSON.parse(event.data); } catch (e) { return; }
        if (msg.type === 'hello') {
          // 控制通道握手带着服务端权威的 lease：不是自己或已无 lease 就不能再显示「有控制权」。
          if (!state.controlCurrentUser) state.controlCurrentUser = currentUsername();
          var helloLock = msg.lock || null;
          var helloOwner = helloLock && helloLock.username ? String(helloLock.username) : '';
          if (state.controlOwnership && (!helloOwner || (state.controlCurrentUser && helloOwner !== state.controlCurrentUser))) {
            setControlOwnership(false, helloOwner);
          }
          return;
        }
        if (msg.type === 'control_lock') {
          if (state.pendingControl && state.pendingControl.kind === 'acquire') {
            var pending = state.pendingControl;
            state.pendingControl = null;
            window.clearTimeout(pending.timer);
            if (msg.ok) {
              setControlOwnership(true);
              startControlKeepalive();
              bindScrcpyInput(deviceId);
              pending.resolve({ ok: true, owner: msg.owner, expires_at: msg.expires_at });
            } else {
              setControlOwnership(false, msg.owner, msg.owner ? 'taken_over' : '');
              pending.resolve({ ok: false, owner: msg.owner, expires_at: msg.expires_at });
            }
            return;
          }
          // 非请求回复：服务端在续租/发送被拒时回 ok:false（lease 已不属于本连接）。
          // 这里不能装作没看见 —— 否则 UI 一直显示「释放控制」但指令全被丢弃。
          if (msg.ok === false) {
            var lostOwner = String(msg.owner || (msg.lock && msg.lock.username) || '');
            if (lostOwner) {
              // A denied lease belongs to another connection even if the
              // username matches. Never automatically take it back.
              setControlOwnership(false, lostOwner, 'taken_over');
              return;
            }
            recoverControlOwnership();
            return;
          }
          if (state.controlOwnership && msg.lock && state.controlCurrentUser && msg.lock.username && msg.lock.username !== state.controlCurrentUser) {
            setControlOwnership(false, msg.lock.username, 'taken_over');
          }
          return;
        }
        if (msg.type === 'control_released') {
          setControlOwnership(false);
          if (state.pendingControl && state.pendingControl.kind === 'release') {
            var pendingRelease = state.pendingControl;
            state.pendingControl = null;
            window.clearTimeout(pendingRelease.timer);
            pendingRelease.resolve({ ok: !!msg.ok });
          }
          return;
        }
        if (msg.type === 'control_error' || msg.type === 'error') {
          var message = msg.error || '控制指令发送失败';
          if (state.pendingControl) {
            var pendingErr = state.pendingControl;
            state.pendingControl = null;
            window.clearTimeout(pendingErr.timer);
            pendingErr.reject(new Error(message));
          } else {
            emitControlFailure(message);
          }
        }
      };
      ws.onclose = function (event) {
        if (state.controlSocket !== ws) return;
        if (state.controlSocket === ws) {
          state.controlSocket = null;
          state.controlSocketDevice = '';
        }
        setControlOwnership(false);
        if (state.pendingControl) {
          var pending = state.pendingControl;
          state.pendingControl = null;
          window.clearTimeout(pending.timer);
          pending.reject(new Error('控制通道已断开'));
        }
        var closeReason = String(event && event.reason || '').toLowerCase();
        if (window.ScrcpyGateAccess && event && (event.code === 1006 || event.code === 4403)) window.ScrcpyGateAccess.check();
        if (event && (event.code === 4401 || (event.code === 4403 && /session|auth|login/.test(closeReason)))) {
          invalidateAuthentication();
        } else if (event && event.code === 4403 && /account/.test(closeReason)) {
          // 到期账户保持登录；视频通道会同时关闭并给出提示。
          try { document.dispatchEvent(new CustomEvent('scrcpygate:account-expired', { detail: { scope: 'control' } })); } catch (e) {}
        }
      };
      ws.onerror = function () {
        if (!opened) { resolve(null); return; }
        try { ws.close(); } catch (e) {}
      };
      window.setTimeout(function () {
        if (!opened) resolve(null);
      }, 5000);
    });
  }

  function closeControlSocket() {
    if (state.pendingControl) {
      var pending = state.pendingControl;
      state.pendingControl = null;
      window.clearTimeout(pending.timer);
      pending.reject(new Error('控制通道已断开'));
    }
    if (state.controlMoveRetryTimer) {
      window.clearTimeout(state.controlMoveRetryTimer);
      state.controlMoveRetryTimer = null;
    }
    state.pendingControlMove = null;
    if (state.controlSocket) {
      try { state.controlSocket.close(); } catch (e) {}
      state.controlSocket = null;
    }
    state.controlSocketDevice = '';
    stopControlKeepalive();
    setControlOwnership(false);
    destroyScrcpyInput();
  }

  function flushPendingControlMove(bufferedAmount) {
    var data = state.pendingControlMove;
    var socket = state.controlSocket;
    if (!data) return;
    if (!state.controlOwnership || !socket || socket.readyState !== 1) {
      state.pendingControlMove = null;
      return;
    }
    var currentBufferedAmount = bufferedAmount === undefined
      ? Number(socket.bufferedAmount || 0)
      : bufferedAmount;
    if (currentBufferedAmount > CONTROL_MOVE_BUFFER_LIMIT) {
      if (!state.controlMoveRetryTimer) {
        state.controlMoveRetryTimer = window.setTimeout(function () {
          state.controlMoveRetryTimer = null;
          flushPendingControlMove();
        }, CONTROL_MOVE_RETRY_MS);
      }
      return;
    }
    state.pendingControlMove = null;
    try {
      socket.send(data);
    } catch (e) {
      setControlOwnership(false);
      emitControlFailure(e && e.message ? e.message : '控制通道发送失败');
    }
  }

  function scrcpyInputDimensions(width, height) {
    var el = state.videoEl;
    var maxSize = state.session && state.session.video ? state.session.video.max_size : 1280;
    var w = Number(width) || Number(el && el.videoWidth) || profileWidth(maxSize);
    var h = Number(height) || Number(el && el.videoHeight) || profileHeight(maxSize);
    return { width: Math.max(1, Math.round(w)), height: Math.max(1, Math.round(h)) };
  }

  function syncScrcpyInputGeometry(width, height) {
    if (!state.scrcpyInput) return;
    var size = scrcpyInputDimensions(width, height);
    if (state.scrcpyInput.resizeScreen) state.scrcpyInput.resizeScreen(size.width, size.height);
    if (state.scrcpyInput.setViewportRotation) state.scrcpyInput.setViewportRotation(state.videoRotation);
    if (state.scrcpyInput.invalidateGeometry) state.scrcpyInput.invalidateGeometry();
  }

  function bindScrcpyInput(deviceId) {
    var ScrcpyInputClass = window.ScrcpyInput || (typeof ScrcpyInput !== 'undefined' ? ScrcpyInput : null);
    if (!ScrcpyInputClass) return null;
    var target = state.videoEl && state.videoEl.parentNode ? state.videoEl : (videoCanvas() || videoSurface());
    if (!target) return null;
    if (state.scrcpyInput && state.scrcpyInput.videoElement === target) {
      target.tabIndex = 0;
      syncScrcpyInputGeometry();
      if (state.scrcpyInput.setFullscreenMode) state.scrcpyInput.setFullscreenMode(state.fullscreenMode);
      return state.scrcpyInput;
    }
    destroyScrcpyInput();
    target.tabIndex = 0;
    if (target.style) target.style.touchAction = 'none';
    var size = scrcpyInputDimensions();
    try {
      state.scrcpyInput = new ScrcpyInputClass(function (data) {
        var socket = state.controlSocket;
        if (!state.controlOwnership || !socket || socket.readyState !== 1) return;
        var bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : null;
        var isTouchMove = bytes && bytes[0] === 2 && bytes[1] === 2;
        var bufferedAmount = Number(socket.bufferedAmount || 0);
        if (isTouchMove) {
          state.pendingControlMove = data;
          flushPendingControlMove(bufferedAmount);
          return;
        }
        // Give a pending move a chance before the next important event, but
        // never delay the key, text, touch-down, or touch-up packet itself.
        flushPendingControlMove();
        try {
          socket.send(data);
        } catch (e) {
          setControlOwnership(false);
          emitControlFailure(e && e.message ? e.message : '控制通道发送失败');
        }
      }, target, size.width, size.height, false, function (active) {
        emitKeyboardState(active);
      });
      if (state.scrcpyInput.setViewportRotation) state.scrcpyInput.setViewportRotation(state.videoRotation);
      if (state.scrcpyInput.setFullscreenMode) state.scrcpyInput.setFullscreenMode(state.fullscreenMode);
    } catch (e) {
      state.scrcpyInput = null;
      emitControlFailure('控制输入初始化失败');
    }
    return state.scrcpyInput;
  }

  function currentUsername() {
    try {
      var s = window.ScrcpyGateSession && window.ScrcpyGateSession.current && window.ScrcpyGateSession.current();
      if (s && s.username) return s.username;
    } catch (e) {}
    return '';
  }

  /* HTTP 兜底获取控制权：拿到 lease 后必须补上控制通道与续租心跳，
     否则 90 秒租约会静默过期而 UI 仍显示「有控制权」。 */
  function adoptHttpControlOwnership(deviceId) {
    return openControlSocket(deviceId, currentUsername()).then(function (ws) {
      if (ws) startControlKeepalive();
      return !!ws;
    });
  }

  function controlAcquireHttp(deviceId) {
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/control/acquire', {}).then(function (r) {
      if (r && r.ok) { setControlOwnership(true); adoptHttpControlOwnership(deviceId); }
      else setControlOwnership(false);
      return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: r && r.ok ? 'self' : (r && r.owner ? 'other' : 'free') }) };
    });
  }

  function controlTakeoverHttp(deviceId) {
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/control/takeover', {}).then(function (r) {
      if (r && r.ok) { setControlOwnership(true); adoptHttpControlOwnership(deviceId); }
      else setControlOwnership(false);
      return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: r && r.ok ? 'self' : (r && r.owner ? 'other' : 'free') }) };
    });
  }

  /* 获取控制权（WebSocket 通道）。
     - 通道建不起来（握手被拒/网络抖动）：等 400ms 重试一次，再失败就抛可读原因。
     - 服务端回 ok:false 且持有者就是自己：说明同一账号在别的标签页/浏览器里留下了没过期的锁
       （服务端按 username + client_id 判定，本连接既不能续租也不能抢占）。先走 HTTP 释放
       （管理员是 force 释放）再重试一次 acquire，避免用户看到「点获取控制没反应」。 */
  function controlAcquireWs(deviceId, retriedOwnLock) {
    return openControlSocket(deviceId, currentUsername()).then(function (ws) {
      if (!ws) {
        if (!retriedOwnLock) {
          return new Promise(function (resolve) { window.setTimeout(resolve, 400); }).then(function () {
            return controlAcquireWs(deviceId, true);
          });
        }
        return Promise.reject(new Error('控制通道未连接（可能是网络或权限问题）'));
      }
      return new Promise(function (resolve, reject) {
        state.pendingControl = { kind: 'acquire', resolve: resolve, reject: reject, timer: window.setTimeout(function () {
          state.pendingControl = null;
          reject(new Error('控制请求超时（设备未响应）'));
        }, 8000) };
        controlSend({ type: 'acquire_control', force: false });
      }).then(function (r) {
        var me = currentUsername();
        if (!retriedOwnLock && r && r.ok === false && r.owner && me && String(r.owner) === String(me)) {
          // 非管理员释放自己的陈旧锁会被服务端按 client_id 拒绝：忽略释放失败，
          // 让重试的结果（ok / owner）走原来的映射，不额外抛错。
          return controlReleaseHttp(deviceId).catch(function () {}).then(function () {
            return controlAcquireWs(deviceId, true);
          });
        }
        return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: r && r.ok ? 'self' : (r && r.owner ? 'other' : 'free') }) };
      });
    });
  }

  function controlTakeoverWs(deviceId) {
    return openControlSocket(deviceId, currentUsername()).then(function (ws) {
      if (!ws) return controlTakeoverHttp(deviceId);
      return new Promise(function (resolve, reject) {
        state.pendingControl = { kind: 'acquire', resolve: resolve, reject: reject, timer: window.setTimeout(function () {
          state.pendingControl = null;
          reject(new Error('控制请求超时（设备未响应）'));
        }, 8000) };
        // 服务端只有「管理员 + 显式 force=true」才允许强制接管（见 mirror_websocket 的
        // acquire/takeover 分支）；以前这里从不带 force，管理员点「接管控制」也会被拒。
        controlSend({ type: 'takeover_control', force: currentUserIsAdmin() === true });
      }).then(function (r) {
        return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: r && r.ok ? 'self' : (r && r.owner ? 'other' : 'free') }) };
      });
    });
  }

  function controlReleaseHttp(deviceId) {
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/control/release', {}).then(function () {
      setControlOwnership(false);
      return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: 'free' }) };
    });
  }

  function controlReleaseWs(deviceId) {
    if (!state.controlSocket || state.controlSocket.readyState !== 1) return controlReleaseHttp(deviceId);
    return new Promise(function (resolve) {
      state.pendingControl = { kind: 'release', resolve: resolve, reject: resolve, timer: window.setTimeout(function () {
        state.pendingControl = null;
        setControlOwnership(false);
        resolve({ ok: true });
      }, 8000) };
      controlSend({ type: 'release_control' });
    }).then(function () {
      setControlOwnership(false);
      return { session: Object.assign({}, state.session || {}, { id: deviceId, controller: 'free' }) };
    });
  }

  /* ---------------- 动态端点分发 ---------------- */

  function handlerSessionCurrent() {
    return apiGet('/api/me').then(function (payload) {
      var user = payload.user || {};
      state.userRole = user.role === 'admin' ? 'admin' : 'user';
      var adapted = Object.assign({}, user, {
        roleKey: state.userRole,
        isAdmin: user.is_admin === true || user.role === 'admin',
        roleLabel: user.role === 'admin' ? '管理员' : '普通用户',
        displayName: user.username,
        name: user.username,
        email: user.username,
        handle: user.username,
        expiresAt: dateStr(user.expires_at),
        // The API stores login times as Unix seconds. Keep the session
        // contract display-ready so the account drawer does not expose the
        // raw epoch value to users.
        lastLoginAt: user.last_login_at ? fmtTs(user.last_login_at) : null,
        lastLoginIp: user.last_login_ip || '',
        lastLoginClient: ''
      });
      updateCsrfMeta(payload.csrf_token);
      return { user: adapted, csrf_token: payload.csrf_token };
    });
  }

  function handlerDevicesList(opts) {
    var query = (opts && opts.query) || {};
    return fetchDevicesEnriched(query.permission === 'watch').then(function (list) {
      if (query.permission === 'watch') {
        list = list.filter(function (d) { return d.permission !== false; });
      }
      return {
        items: list,
        devices: list,
        sessions: list.filter(function (d) { return d.streaming; }),
        stale: state.publicDevicesStale === true,
        stale_at: state.publicDevicesStaleAt || null
      };
    });
  }

  function dashboardAlasRefs(item) {
    if (window.ScrcpyGateDashboardState && typeof window.ScrcpyGateDashboardState.refs === 'function') {
      return window.ScrcpyGateDashboardState.refs(item);
    }
    var values = item ? [
      item.public_id, item.publicId, item.public_device_id, item.publicDeviceId,
      item.device_id, item.deviceId, item.id, item.internal_id, item.internalId
    ] : [];
    var refs = [];
    values.forEach(function (value) {
      var ref = String(value || '').trim();
      if (ref && refs.indexOf(ref) < 0) refs.push(ref);
    });
    return refs;
  }

  function dashboardAlasFalse(value) {
    if (window.ScrcpyGateDashboardState && typeof window.ScrcpyGateDashboardState.falseValue === 'function') {
      return window.ScrcpyGateDashboardState.falseValue(value);
    }
    return value === false || value === 0 || value === '0' || String(value || '').toLowerCase() === 'false';
  }

  function dashboardAlasEntry(item, binding, user) {
    if (window.ScrcpyGateDashboardState && typeof window.ScrcpyGateDashboardState.entry === 'function') {
      return window.ScrcpyGateDashboardState.entry(item, binding, user);
    }
    var userState = String(user && (user.expiration_state || user.status || '') || '').toLowerCase();
    var userInactive = !!user && (
      dashboardAlasFalse(user.enabled) || dashboardAlasFalse(user.is_active) || dashboardAlasFalse(user.isActive) ||
      userState === 'disabled' || userState === 'expired' || userState === 'inactive'
    );
    if (userInactive) {
      var inactiveCode = userState === 'expired' ? 'expired' : 'disabled';
      return { code: inactiveCode, label: inactiveCode === 'expired' ? '已到期' : '已停用', tone: 'none', error: false };
    }
    var raw = String(item && (item.status || item.state || item.status_label || item.statusLabel) || 'unknown').trim().toLowerCase();
    var aliases = {
      '运行中': 'running', '在线': 'connected', '已连接': 'connected',
      '已停止': 'stopped', '已停用': 'disabled', '已到期': 'expired',
      '未配置': 'unconfigured', '未检查': 'unknown', '异常': 'error',
      '不可达': 'unreachable', 'healthy': 'running', 'ok': 'running',
      'failed': 'error', 'failure': 'error'
    };
    raw = aliases[raw] || raw;
    if (raw === 'idle') raw = 'stopped';
    if (raw === 'online') raw = 'connected';
    var errorStates = ['error', 'partial_error', 'partial', 'disconnected', 'unreachable', 'timeout', 'token_missing', 'token_invalid', 'http_error', 'invalid_config'];
    var neutralStates = ['stopped', 'disabled', 'unconfigured', 'unknown', 'expired'];
    if (errorStates.indexOf(raw) >= 0 || (item && item.ok === false && neutralStates.indexOf(raw) < 0)) {
      var errorCode = raw === 'disconnected' || raw === 'unreachable' || raw === 'timeout' ? 'unreachable' : 'error';
      return { code: errorCode, label: errorCode === 'unreachable' ? '不可达' : '异常', tone: 'error', error: true };
    }
    if (raw === 'running') return { code: 'running', label: '运行中', tone: 'ok', error: false };
    if (raw === 'connected') return { code: 'connected', label: '已连接', tone: 'ok', error: false };
    if (raw === 'stopped') return { code: 'stopped', label: '已停止', tone: 'none', error: false };
    if (raw === 'disabled') return { code: 'disabled', label: '已停用', tone: 'none', error: false };
    if (raw === 'expired') return { code: 'expired', label: '已到期', tone: 'none', error: false };
    if (raw === 'unconfigured') return { code: 'unconfigured', label: '未配置', tone: 'none', error: false };
    return { code: 'unknown', label: '未检查', tone: 'none', error: false };
  }

  function dashboardAlasAggregate(entries, fallback) {
    if (window.ScrcpyGateDashboardState && typeof window.ScrcpyGateDashboardState.aggregate === 'function') {
      return window.ScrcpyGateDashboardState.aggregate(entries, fallback);
    }
    var list = (entries || []).filter(function (entry) { return !!entry; });
    if (!list.length) {
      var fallbackCode = String(fallback || 'unknown').toLowerCase();
      if (fallbackCode === 'unbound') return { code: 'unbound', label: '未关联', tone: 'none', error: false };
      return dashboardAlasEntry({ status: fallbackCode }, null, null);
    }
    var errors = list.filter(function (entry) { return entry.error === true || entry.tone === 'error'; });
    if (errors.length === list.length) {
      var allUnreachable = errors.every(function (entry) { return entry.code === 'unreachable'; });
      return { code: allUnreachable ? 'unreachable' : 'error', label: allUnreachable ? '不可达' : '异常', tone: 'error', error: true };
    }
    if (errors.length) return { code: 'partial_error', label: '部分异常', tone: 'error', error: true };
    if (list.some(function (entry) { return entry.tone === 'ok'; })) {
      return { code: 'running', label: '运行中', tone: 'ok', error: false };
    }
    if (list.every(function (entry) { return entry.code === 'expired'; })) {
      return { code: 'expired', label: '已到期', tone: 'none', error: false };
    }
    if (list.every(function (entry) { return entry.code === 'disabled'; })) {
      return { code: 'disabled', label: '已停用', tone: 'none', error: false };
    }
    if (list.every(function (entry) { return entry.code === 'unconfigured'; })) {
      return { code: 'unconfigured', label: '未配置', tone: 'none', error: false };
    }
    if (list.some(function (entry) { return entry.code === 'unknown'; })) {
      return { code: 'unknown', label: '未检查', tone: 'none', error: false };
    }
    return { code: 'stopped', label: '已停止', tone: 'none', error: false };
  }

  function handlerDashboardOverview(opts) {
    opts = opts || {};
    var query = opts.query || { runtime: '1' };
    return apiRequest('/api/admin/dashboard/snapshot', {
      query: query,
      force: opts.force === true,
      cache: opts.cache
    }).then(function (payload) {
      var sessions = payload.sessions || {};
      var devices = (payload.devices || []).map(function (device) {
        var id = device.id || device.device_id;
        var adapted = adaptDevice(Object.assign({}, device, { session: sessions[id] || device.session || null }), null);
        // The admin snapshot keeps the internal id in `id` and the opaque id in
        // `public_id`; retain both on the adapted row so ALAS bindings can be
        // matched without falling back to a global status.
        adapted.public_id = device.public_id || device.publicId || null;
        adapted.publicId = adapted.public_id;
        return adapted;
      });
      var alas = payload.alas || {};
      var usersByName = {};
      (Array.isArray(payload.users) ? payload.users : []).forEach(function (user) {
        var username = String(user && (user.username || user.user_name) || '').trim();
        if (username) usersByName[username] = user;
      });
      var statusByConfig = {};
      var statusItems = Array.isArray(alas.config_statuses) ? alas.config_statuses : [];
      statusItems.forEach(function (item) {
        var config = String(item && (item.config || item.config_name || item.configName) || '').trim();
        if (config) statusByConfig[config] = item;
      });
      var bindingRows = Array.isArray(alas.bindings) && alas.bindings.length ? alas.bindings : alas.assignments;
      var bindings = (Array.isArray(bindingRows) ? bindingRows : []).filter(function (binding) { return !!binding; });
      var bindingByDevice = {};
      var bindingByConfig = {};
      bindings.forEach(function (binding) {
        var config = String(binding.config_name || binding.configName || '').trim();
        if (config && !bindingByConfig[config]) bindingByConfig[config] = binding;
        dashboardAlasRefs(binding).forEach(function (deviceId) {
          if (!bindingByDevice[deviceId]) bindingByDevice[deviceId] = [];
          if (bindingByDevice[deviceId].indexOf(binding) < 0) bindingByDevice[deviceId].push(binding);
        });
      });
      var configNames = [];
      var addConfig = function (value) {
        var config = typeof value === 'object' ? value && (value.config || value.config_name || value.configName || value.name) : value;
        config = String(config || '').trim();
        if (config && configNames.indexOf(config) < 0) configNames.push(config);
      };
      (Array.isArray(alas.configs) ? alas.configs : []).forEach(addConfig);
      bindings.forEach(function (binding) { addConfig(binding.config_name || binding.configName); });
      statusItems.forEach(function (item) { addConfig(item); });
      var summaryEntries = configNames.map(function (config) {
        var binding = bindingByConfig[config];
        var item = statusByConfig[config];
        var owner = binding && binding.username || item && item.username;
        return dashboardAlasEntry(item, binding, usersByName[String(owner || '').trim()]);
      });
      var summaryFallback = configNames.length ? null : (alas.status || (alas.configured ? 'unknown' : 'disabled'));
      var summaryState = dashboardAlasAggregate(summaryEntries, summaryFallback);
      // A catalog-level failure is an independent service problem. Keep it in
      // the card summary without applying it to unrelated device rows.
      if (alas.catalog_error && summaryState.tone !== 'error') {
        summaryState = summaryEntries.length
          ? { code: 'partial_error', label: '部分异常', tone: 'error', error: true }
          : dashboardAlasAggregate([], 'error');
      }
      devices = devices.map(function (device) {
        var deviceBindings = [];
        dashboardAlasRefs(device).forEach(function (deviceId) {
          (bindingByDevice[deviceId] || []).forEach(function (binding) {
            if (deviceBindings.indexOf(binding) < 0) deviceBindings.push(binding);
          });
        });
        var deviceEntries = deviceBindings.map(function (binding) {
          var config = String(binding.config_name || binding.configName || '').trim();
          var item = statusByConfig[config];
          var owner = binding.username || item && item.username;
          return dashboardAlasEntry(item, binding, usersByName[String(owner || '').trim()]);
        });
        var deviceState = dashboardAlasAggregate(deviceEntries, deviceBindings.length ? null : 'unbound');
        var next = Object.assign({}, device);
        next.alasStatus = deviceState.label;
        next.alasStatusCode = deviceState.code;
        next.alasStatusTone = deviceState.tone;
        next.alasConfig = deviceBindings.length ? (deviceBindings[0].config_name || deviceBindings[0].configName || '') : '';
        next.alasConfigs = deviceBindings.map(function (binding) { return binding.config_name || binding.configName || ''; }).filter(Boolean);
        next.alasBinding = deviceBindings.length > 0;
        return next;
      });
      alas = Object.assign({}, alas, {
        dashboard_status: summaryState.code,
        dashboard_label: summaryState.label,
        dashboard_tone: summaryState.tone
      });
      var loggingHealth = payload.logging_health || payload.loggingHealth || {};
      return {
        items: devices,
        devices: devices,
        sessions: devices.filter(function (device) { return device.streaming; }),
        activeSessions: payload.active_sessions || [],
        users: payload.users || [],
        alas: alas,
        alerts: payload.alerts || {},
        attention: Array.isArray(payload.attention) ? payload.attention : [],
        recent_activities: Array.isArray(payload.recent_activities) ? payload.recent_activities : [],
        updated_at: payload.updated_at || null,
        logging_health: loggingHealth,
        loggingHealth: loggingHealth
      };
    });
  }

  function handlerWorkbenchSnapshot(opts) {
    var query = (opts && opts.query) || {};
    return apiGet('/api/workbench/snapshot', query);
  }

  // 无条件解绑当前观看:关闭视频与控制通道并清空会话/设备状态。
  function resetWatchBinding() {
    state.viewerStopTerminal = true;
    resetViewerStopTracking(false);
    closeVideoSocket(true);
    closeControlSocket();
    state.session = null;
    state.viewerToken = null;
    state.deviceId = null;
    state.videoSocketDevice = '';
  }

  function clearCurrentSession(deviceId, clientId) {
    var currentClient = state.session && state.session.client_id;
    if ((clientId && currentClient && String(clientId) !== String(currentClient)) ||
        (deviceId && state.deviceId && String(deviceId) !== String(state.deviceId))) {
      return;
    }
    resetWatchBinding();
  }

  function handlerSessionsStop(opts) {
    var params = (opts && opts.params) || {};
    var deviceId = params.deviceId || (opts && opts.body && opts.body.deviceId) || state.deviceId;
    var clientId = params.clientId || params.client_id || (opts && opts.body && (opts.body.clientId || opts.body.client_id));
    if (clientId) {
      if (!deviceId) return Promise.reject(unsupportedError('缺少观看会话所属设备'));
      return apiPost('/api/admin/devices/' + encodeURIComponent(deviceId) + '/viewers/' + encodeURIComponent(clientId) + '/disconnect', {})
        .then(function (payload) {
          clearCurrentSession(deviceId, clientId);
          return payload || { ok: true };
        });
    }
    if (!deviceId) return Promise.reject(unsupportedError('请先选择设备'));
    var stopDevice = params.admin === true || (opts && opts.body && opts.body.stopDevice === true);
    var endpoint = '/api/devices/' + encodeURIComponent(deviceId) + (stopDevice ? '/mirror/stop' : '/mirror/stop-self');
    var body = stopDevice ? {} : {
      client_id: state.session && state.session.client_id || '',
      viewer_token: state.viewerToken || ''
    };
    return apiPost(endpoint, body).then(function (payload) {
      clearCurrentSession(deviceId, '');
      return payload || { ok: true };
    });
  }

  // 宫格视图专用：只申请观看令牌 / 只结束自己的观看，不触碰单画面会话状态。
  // 宫格缩略档位：每秒 1 帧、640px 短边、码率上限 0.5 Mbps。观感等同于每秒
  // 刷新一次的截图，但比 adb screencap（每次约 2 MB）省一到两个数量级的带宽。
  var THUMBNAIL_VIDEO_OPTIONS = {
    profile: 'custom',
    max_size: 640,
    max_fps: 1,
    video_bit_rate: 500000
  };

  function handlerViewerStart(opts) {
    var body = (opts && opts.body) || {};
    var deviceId = body.deviceId || (opts && opts.params && opts.params.deviceId);
    if (!deviceId) return Promise.reject(new Error('请先选择设备'));
    var request = {};
    if (body.thumbnail === true) {
      request = Object.assign({}, THUMBNAIL_VIDEO_OPTIONS);
      var requestedFps = Number(body.thumbnailFps);
      if (isFinite(requestedFps) && requestedFps > 0) {
        request.max_fps = Math.max(1, Math.min(30, Math.round(requestedFps)));
      }
    }
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/start', request).then(function (payload) {
      return payload || { ok: true };
    });
  }

  function handlerViewerStop(opts) {
    var body = (opts && opts.body) || {};
    var params = (opts && opts.params) || {};
    var deviceId = body.deviceId || params.deviceId;
    if (!deviceId) return Promise.reject(new Error('请先选择设备'));
    return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/stop-self', {
      client_id: body.clientId || body.client_id || '',
      viewer_token: body.viewerToken || body.viewer_token || ''
    }).then(function (payload) {
      return payload || { ok: true };
    });
  }

  function handlerSessionsCreate(opts) {
    var body = (opts && opts.body) || {};
    var deviceId = body.deviceId || (opts && opts.params && opts.params.deviceId);
    // 换设备时先彻底结束上一台设备的观看与控制,避免旧设备的通道继续存活。
    var previousDevice = state.videoSocketDevice || state.deviceId || '';
    if (previousDevice && String(previousDevice) !== String(deviceId || '')) resetWatchBinding();
    var policyRequest = state.viewerStopSettingsLoaded
      ? Promise.resolve()
      : loadViewerStopSettings().catch(function (error) {
        var status = error && error.detail && error.detail.status;
        if (status === 401) throw error;
        return null;
      });
    return policyRequest.then(function () {
      return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/start', {});
    }).then(function (payload) {
      var sess = null;
      if (payload && payload.sessions) {
        var keys = Object.keys(payload.sessions);
        if (keys.length) sess = payload.sessions[keys[0]];
      }
      var adapted = {
        id: deviceId,
        deviceId: deviceId,
        controller: sess && sess.control_lock && sess.control_lock.username
          ? (String(sess.control_lock.username) === String(currentUsername()) ? 'self' : 'other')
          : 'free',
        running: !!(sess && sess.running),
        remainingSeconds: payload && payload.remaining_seconds != null ? payload.remaining_seconds : undefined
      };
      state.session = adapted;
      state.deviceId = deviceId;
      if (payload && payload.ok) {
        openVideoSocket(deviceId, payload.viewer_token || '');
      }
      return { ok: !!(payload && payload.ok), session: adapted, viewer_token: payload && payload.viewer_token };
    });
  }

  function handlerSessionsAction(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var action = body.action;
    var deviceId = body.deviceId || params.deviceId || params.id || state.deviceId;
    if (action === 'stop-device-stream') {
      var current = window.ScrcpyGateSession && window.ScrcpyGateSession.current && window.ScrcpyGateSession.current();
      var isAdmin = !!current && (current.roleKey === 'admin' || current.role === '管理员' || current.isAdmin === true);
      if (!isAdmin) return Promise.reject(new Error('管理员权限 required'));
      return apiPost('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/stop', {}).then(function (payload) {
        clearCurrentSession(deviceId, '');
        try { document.dispatchEvent(new CustomEvent('scrcpygate:session-stopped', { detail: { deviceId: deviceId, admin: true } })); } catch (e) {}
        return Object.assign({ session: null }, payload || {});
      });
    }
    if (action === 'keyboard') {
      if (!state.controlOwnership) return Promise.reject(new Error('请先获取控制权'));
      if (body.enabled) {
        var input = bindScrcpyInput(deviceId);
        if (!input) return Promise.reject(new Error('控制输入不可用'));
        if (!input.openKeyboard || input.openKeyboard() === false) {
          emitKeyboardState(false);
          return Promise.reject(new Error('无法打开键盘，请再次点击键盘按钮'));
        }
      } else if (state.scrcpyInput && state.scrcpyInput.closeKeyboard) {
        state.scrcpyInput.closeKeyboard(true);
      }
      emitKeyboardState(!!body.enabled);
      return Promise.resolve({ ok: true, session: state.session });
    }
    if (action === 'back' || action === 'home' || action === 'tasks') {
      var keycode = action === 'back' ? 4 : (action === 'home' ? 3 : 187);
      if (!state.controlOwnership || !state.scrcpyInput || !state.scrcpyInput.sendKeyCodePress) {
        return Promise.reject(new Error('请先获取控制权'));
      }
      try { state.scrcpyInput.sendKeyCodePress(keycode); } catch (e) { return Promise.reject(e); }
      return Promise.resolve({ ok: true, session: state.session });
    }
    return Promise.resolve({ ok: true, session: state.session });
  }

  function handlerControlAcquire(opts) {
    var params = (opts && opts.params) || {};
    var deviceId = params.deviceId || params.id || state.deviceId;
    if (!deviceId) return Promise.reject(new Error('请先选择设备'));
    return controlAcquireWs(deviceId);
  }

  function handlerControlTakeover(opts) {
    var params = (opts && opts.params) || {};
    var deviceId = params.deviceId || params.id || state.deviceId;
    if (!deviceId) return Promise.reject(new Error('请先选择设备'));
    return controlTakeoverWs(deviceId);
  }

  function handlerControlRelease(opts) {
    var params = (opts && opts.params) || {};
    var deviceId = params.deviceId || params.id || state.deviceId;
    return controlReleaseWs(deviceId);
  }

  function currentUserIsAdmin() {
    var current = window.ScrcpyGateSession && window.ScrcpyGateSession.current && window.ScrcpyGateSession.current();
    if (current) {
      return current.roleKey === 'admin' || current.role === '管理员' || current.isAdmin === true;
    }
    return state.userRole === 'admin';
  }

  function handlerQualityConfig() {
    // Regular users can read the public preference endpoint.  Calling the
    // admin endpoint speculatively creates a guaranteed 403 and can look like
    // abusive traffic to an upstream WAF, eventually blocking device reads.
    var adminRequest = currentUserIsAdmin()
      ? apiGet('/api/admin/video').catch(function () { return null; })
      : Promise.resolve(null);
    return Promise.all([
      apiGet('/api/video/preferences'),
      adminRequest
    ]).then(function (results) {
      return buildQualityPayload(results[0], results[1]);
    });
  }

  function handlerAdminQualityConfig() {
    return apiGet('/api/admin/video').then(function (admin) {
      var prefs = Object.assign({}, admin || {}, {
        defaults: (admin && admin.defaults) || {},
        effective: (admin && admin.defaults) || {},
        profiles: (admin && admin.profiles) || {},
        profile_labels: (admin && admin.profile_labels) || {},
        enabled_stream_modes: (admin && admin.enabled_stream_modes) || ['raw'],
        fullscreen_profile: (admin && admin.fullscreen_profile) || '',
        enabled_presets: (admin && admin.enabled_presets) || BUILTIN_PRESETS.slice(),
        max_size_limit: (admin && admin.max_size_limit) || 1920,
        bandwidth_preset: (admin && admin.bandwidth_preset) || '',
        bandwidth_profile: (admin && admin.bandwidth_profile) || null,
        bandwidth_profile_id: (admin && admin.bandwidth_profile_id) || '',
        allow_custom_tuning: !!(admin && admin.user_custom_tuning)
      });
      return buildQualityPayload(prefs, admin);
    });
  }

  function parseResMode(body, fallbackMaxSize) {
    var maxSize = fallbackMaxSize;
    var resMode = body.resMode || '';
    if (resMode === 'custom' || body.customChecked) {
      var w = Number(body.customW) || 0;
      var h = Number(body.customH) || 0;
      if (w > 0 || h > 0) maxSize = Math.max(w, h);
    } else {
      var m = String(resMode).match(/(\d+)\s*[x×]\s*(\d+)/i);
      if (m) maxSize = Math.max(Number(m[1]), Number(m[2]));
    }
    if (body.maxRes && !isNaN(Number(body.maxRes))) maxSize = Number(body.maxRes);
    return maxSize;
  }

  function qualityVideoBody(body) {
    body = body || {};
    var maxSize = parseResMode(body, 1280);
    var bitrate = Number(body.bitrate);
    var fps = Number(body.fps);
    var presetId = body.presetId || body.preset_id;
    var videoBody = {
      profile: presetId ? String(presetId) : 'custom',
      max_size: maxSize
    };
    if (!isNaN(bitrate) && bitrate > 0) videoBody.video_bit_rate = Math.round(bitrate * 1000000);
    if (!isNaN(fps) && fps > 0) videoBody.max_fps = Math.round(fps);
    if (body.defaultMode) videoBody.scrcpy_stream_mode = pageModeToServer(body.defaultMode);
    return videoBody;
  }

  function adoptEffectiveQuality(config, effective) {
    if (!config || !effective || typeof effective !== 'object') return config;
    var items = config.presets && Array.isArray(config.presets.items) ? config.presets.items : [];
    var id = String(effective.profile || '');
    var item = items.filter(function (row) { return row && row.id === id; })[0] || null;
    var c = config.config || config.settings || config.params || {};
    var maxSize = Number(effective.max_size) || 1280;
    if (item) {
      c.resMode = String(item.width) + 'x' + String(item.height);
      c.customW = Number(item.width) || maxSize;
      c.customH = Number(item.height) || profileHeight(maxSize);
      c.fps = Number(item.fps) || Number(effective.max_fps) || 24;
      c.bitrate = Number(item.bitrate) || bpsToMbps(effective.video_bit_rate);
      config.selectedPresetId = item.id;
      config.presetId = item.id;
    } else {
      c.resMode = resModeOf(maxSize);
      c.customW = profileWidth(maxSize);
      c.customH = profileHeight(maxSize);
      c.fps = Number(effective.max_fps) || c.fps;
      c.bitrate = bpsToMbps(effective.video_bit_rate) || c.bitrate;
      config.selectedPresetId = '';
      config.presetId = '';
    }
    if (effective.scrcpy_stream_mode) c.defaultMode = streamModeToPage(effective.scrcpy_stream_mode);
    config.config = c;
    config.settings = c;
    config.params = c;
    config.effective = effective;
    return config;
  }

  function handlerMirrorFullscreen(opts) {
    var body = (opts && opts.body) || {};
    var params = (opts && opts.params) || {};
    var deviceId = body.deviceId || body.device_id || params.deviceId || state.deviceId;
    if (!deviceId) return Promise.reject(unsupportedError('请先选择设备'));
    return apiPut('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/settings', {
      fullscreen: body.fullscreen !== false,
      client_id: body.clientId || body.client_id || (state.session && state.session.client_id) || ''
    }).then(function (payload) {
      return requireSuccessful(payload, '全屏画质切换失败') || { ok: true };
    });
  }

  function handlerQualityUpdate(opts) {
    var body = (opts && opts.body) || {};
    var deviceId = body.deviceId || body.device_id || state.deviceId;
    if (!deviceId) return Promise.reject(unsupportedError('请先选择设备'));
    var videoBody = qualityVideoBody(body);
    videoBody.client_id = body.clientId || body.client_id || (state.session && state.session.client_id) || '';
    return apiPut('/api/devices/' + encodeURIComponent(deviceId) + '/mirror/settings', videoBody)
      .then(function (payload) { return requireSuccessful(payload, '画质应用失败'); })
      .then(function (applied) {
        return handlerQualityConfig().then(function (config) {
          adoptEffectiveQuality(config, applied.effective || applied.preferences);
          config.applyResult = applied;
          config.applied = applied;
          config.restarted = !!applied.restarted;
          config.deferred = !!applied.deferred;
          return config;
        });
      });
  }

  function handlerAdminQualityUpdate(opts) {
    var body = (opts && opts.body) || {};
    var adminUpdates = qualityVideoBody(body);
    if (body.presets && typeof body.presets === 'object' && !Array.isArray(body.presets)) {
      adminUpdates.presets = body.presets;
    }
    var uqKeys = ['default_preset', 'default_preset_id', 'video_default_preset', 'enabled_presets', 'bandwidth_preset', 'bandwidth_profile', 'bandwidth_profile_id', 'max_size_limit', 'user_custom_tuning'];
    uqKeys.forEach(function (k) { if (body[k] !== undefined) adminUpdates[k] = body[k]; });
    ['viewer_hidden_stop_enabled', 'viewer_hidden_stop_minutes', 'viewer_blur_stop_enabled', 'viewer_blur_stop_minutes', 'viewer_stop_settings_synced'].forEach(function (k) {
      if (body[k] !== undefined) adminUpdates[k] = body[k];
    });
    if (body.viewerHiddenStopEnabled !== undefined) adminUpdates.viewer_hidden_stop_enabled = body.viewerHiddenStopEnabled;
    if (body.viewerHiddenStopMin !== undefined) adminUpdates.viewer_hidden_stop_minutes = Number(body.viewerHiddenStopMin);
    if (body.viewerBlurStopEnabled !== undefined) adminUpdates.viewer_blur_stop_enabled = body.viewerBlurStopEnabled;
    if (body.viewerBlurStopMin !== undefined) adminUpdates.viewer_blur_stop_minutes = Number(body.viewerBlurStopMin);
    if (body.viewerStopSettingsSynced !== undefined) adminUpdates.viewer_stop_settings_synced = body.viewerStopSettingsSynced;
    if (body.fullscreenQuality !== undefined) adminUpdates.fullscreen_profile = body.fullscreenQuality || '';
    if (body.rawV2 !== undefined || body.protoAvail !== undefined || body.legacyAvail !== undefined) {
      var modes = [];
      if (body.rawV2 !== false) modes.push('raw');
      if (body.protoAvail === true) modes.push('protocol');
      if (body.legacyAvail === true) modes.push('legacy');
      adminUpdates.enabled_stream_modes = modes.length ? modes : ['raw'];
    }
    var hasIdle = body.idleStopMin !== undefined && body.idleStopMin !== '' && !isNaN(Number(body.idleStopMin));
    var hasSession = body.sessionLimitH !== undefined && body.sessionLimitH !== '' && !isNaN(Number(body.sessionLimitH));
    if (hasIdle) {
      var idleVal = Number(body.idleStopMin);
      adminUpdates.auto_stop_minutes = body.idleStopUnit === 'sec' ? Math.round(idleVal / 60) : Math.round(idleVal);
    }
    if (hasSession) {
      var sessionVal = Number(body.sessionLimitH);
      adminUpdates.max_session_minutes = body.sessionLimitUnit === 'h' ? Math.round(sessionVal * 60) : Math.round(sessionVal);
    }
    return apiPut('/api/admin/video', adminUpdates)
      .then(function (payload) { return requireSuccessful(payload, '画质设置保存失败'); })
      .then(handlerAdminQualityConfig);
  }

  function handlerQualityPresets(action, opts) {
    var body = (opts && opts.body) || {};
    var params = (opts && opts.params) || {};
    var id = params.id || params.presetId || '';
    var path = '/api/admin/video/presets' + (action === 'create' ? '' : '/' + encodeURIComponent(id));
    var request;
    if (action === 'create') {
      request = apiPost(path, {
        name: body.name,
        width: Number(body.width),
        height: Number(body.height),
        fps: Number(body.fps),
        bitrate: Number(body.bitrate),
        fullscreen_only: body.fullscreen_only === true
      });
    } else if (action === 'update') {
      request = apiPut(path, {
        name: body.name,
        width: Number(body.width),
        height: Number(body.height),
        fps: Number(body.fps),
        bitrate: Number(body.bitrate),
        fullscreen_only: body.fullscreen_only === true
      });
    } else {
      request = apiDelete(path);
    }
    return request.then(function (payload) {
      return requireSuccessful(payload, '画质预设操作失败');
    }).then(function () {
      return handlerAdminQualityConfig();
    });
  }

  function handlerQualityStatus() {
    return apiGet('/api/admin/video/status').then(function (payload) {
      var result = Object.assign({}, payload || {});
      if (result.updatedAt !== undefined) result.updatedAt = fmtTs(result.updatedAt);
      return result;
    });
  }

  function handlerQualityClients() {
    return apiGet('/api/admin/video/status').then(function (payload) {
      var clients = (payload.clients || []).map(function (client) {
        return {
          id: client.client_id,
          name: client.username,
          username: client.username,
          device: client.device_name || client.device_id,
          conn: fmtTs(client.connected_at),
          queue: Number(client.queue_size) || 0,
          bytes: Math.round((Number(client.queue_bytes) || 0) / 1024),
          drop: Number(client.drops) || 0,
          role: client.has_control ? 'control' : 'watch'
        };
      });
      return { clients: clients, items: clients };
    });
  }

  function handlerAdminQualityReset(opts) {
    var params = (opts && opts.params) || {};
    var id = params.id || params.presetId || (opts && opts.body && (opts.body.preset_id || opts.body.presetId));
    if (!id) return Promise.reject(unsupportedError('请选择要恢复的内置预设'));
    return apiPost('/api/admin/video/presets/' + encodeURIComponent(id) + '/reset', {})
      .then(function (payload) { return requireSuccessful(payload, '恢复默认设置失败'); })
      .then(handlerAdminQualityConfig);
  }

  function handlerAdminQualityRestart() {
    return apiPost('/api/admin/video/restart', {}).then(function (payload) {
      return payload || { ok: true, restarted: [], deferred: [], failed: [] };
    });
  }

  function handlerLogsList(opts) {
    var query = (opts && opts.query) || {};
    var limit = Number(query.limit) || 40;
    var offset = Number(query.offset) || 0;
    // 列表视图默认只取 600 行, 按请求量自适应(导出等大批量场景仍取服务端上限),
    // 降低服务端逐行解析成本, 提升日志页与自动刷新时的响应速度。
    var serverQuery = { lines: Math.min(2000, Math.max(300, limit * 4)) };
    if (query.severity && query.severity !== 'all') {
      var sev = query.severity === 'warn' ? 'warning' : query.severity;
      if (['debug', 'info', 'warning', 'error', 'critical'].indexOf(sev) >= 0) serverQuery.min_severity = sev;
    }
    return apiGet('/api/admin/runtime-logs', serverQuery).then(function (payload) {
      return adaptRuntimeLogs(payload, query, offset, limit);
    });
  }

  function handlerLogsAudit(opts) {
    var query = (opts && opts.query) || {};
    var limit = Number(query.limit) || 40;
    var bounds = timeRangeBounds(query.timeRange, query.from, query.to);
    var needsLocalFiltering = !!(query.q || query.target || (query.eventType && query.eventType !== 'all'));
    var serverQuery = { limit: needsLocalFiltering ? 200 : Math.min(200, Math.max(1, limit)) };
    if (query.before !== undefined && query.before !== null && query.before !== '') serverQuery.before = query.before;
    if (bounds.fromTs !== null) serverQuery.from_ts = bounds.fromTs;
    if (bounds.toTs !== null) serverQuery.to_ts = bounds.toTs;
    if (query.actor && query.actor !== 'all' && query.actor !== 'none') serverQuery.actor = query.actor;
    if (query.result && query.result !== 'all') serverQuery.outcome = query.result === 'fail' ? 'failure' : query.result;
    return apiGet('/api/admin/logs', serverQuery).then(function (payload) {
      return adaptAuditList(payload, query);
    });
  }

  function handlerLogsAuditDetail(opts) {
    var params = (opts && opts.params) || {};
    var eventId = params.id || params.eventId;
    if (!eventId) return Promise.reject(new Error('缺少审计事件 ID'));
    return apiGet('/api/admin/logs/' + encodeURIComponent(eventId)).then(function (payload) {
      return { event: adaptAuditRecord((payload && payload.event) || {}) };
    });
  }

  function handlerAdminAlerts(opts) {
    opts = opts || {};
    var query = opts.query || {};
    var includeHandled = query.include_handled === true || query.includeHandled === true;
    return apiGet('/api/admin/alerts', includeHandled ? { include_handled: true } : undefined).then(function (payload) {
      var data = payload || {};
      var raw = Array.isArray(data.alerts) ? data.alerts : (Array.isArray(data.items) ? data.items : []);
      var items = raw.map(adaptAlertRecord);
      return {
        items: items,
        alerts: items,
        pendingCount: Number(data.pending_count != null ? data.pending_count : items.filter(function (item) { return !item.handled; }).length),
        total: Number(data.total != null ? data.total : items.length)
      };
    });
  }

  function handlerAdminAlertResolve(opts) {
    opts = opts || {};
    var params = opts.params || {};
    var eventId = params.id || params.eventId || (opts.body && (opts.body.event_id || opts.body.eventId));
    if (!eventId) return Promise.reject(new Error('缺少告警事件 ID'));
    return apiPost('/api/admin/alerts/' + encodeURIComponent(eventId) + '/resolve', {})
      .then(function (payload) {
        var result = payload || {};
        return Object.assign({}, result, {
          alert: result.alert ? adaptAlertRecord(result.alert) : null,
          pendingCount: Number(result.pending_count != null ? result.pending_count : 0)
        });
      });
  }

  function auditCsvCell(value) {
    if (value && typeof value === 'object') {
      try { value = JSON.stringify(value); } catch (e) { value = String(value); }
    }
    var text = String(value == null ? '' : value);
    if (/^[=+\-@]/.test(text)) text = "'" + text;
    return text;
  }
  function auditCsvEscape(text) {
    if (/[",\r\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }
  function auditToCsv(events) {
    var cols = ['ts', 'event_id', 'username', 'actor_role', 'action', 'target_type', 'target_id', 'outcome', 'reason', 'severity', 'request_id', 'source_ip', 'detail', 'metadata'];
    var rows = [cols.join(',')];
    (events || []).forEach(function (e) {
      rows.push(cols.map(function (c) { return auditCsvEscape(auditCsvCell(e[c])); }).join(','));
    });
    return '\ufeff' + rows.join('\r\n');
  }

  function handlerLogsExport(opts) {
    var body = (opts && opts.body) || {};
    var filters = body.filters || {};
    if (body.type === 'audit') {
      var bounds = timeRangeBounds(filters.timeRange, filters.from, filters.to);
      // 统一走 JSON 分支取回完整事件,再由客户端合成目标格式:
      // 避免 text/csv 附件响应在个别浏览器(headless Chrome)被吞成 204 空响应。
      var reqBody = { format: 'json' };
      if (filters.actor) reqBody.actor = filters.actor;
      if (filters.result && filters.result !== 'all') reqBody.outcome = filters.result === 'fail' ? 'failure' : filters.result;
      if (bounds.fromTs !== null) reqBody.from_ts = bounds.fromTs;
      if (bounds.toTs !== null) reqBody.to_ts = bounds.toTs;
      return apiPost('/api/admin/logs/export', reqBody).then(function (payload) {
        var events = payload && Array.isArray(payload.events) ? payload.events : [];
        if (body.format === 'csv') {
          return { content: auditToCsv(events), filename: 'scrcpygate-audit.csv' };
        }
        if (body.format === 'text') {
          var lines = events.map(function (e) {
            return [fmtTs(e.ts), e.username || '', e.action || '', e.outcome || '', e.source_ip || '', e.detail || ''].join(' | ');
          });
          return { content: lines.join('\n') || '(无记录)', filename: 'scrcpygate-audit.txt' };
        }
        return { content: JSON.stringify(events, null, 2), filename: 'scrcpygate-audit.json' };
      });
    }
    return handlerLogsList({ query: Object.assign({}, filters, { limit: 2000, offset: 0 }) }).then(function (adapted) {
      var lines = (adapted.logs || []).map(function (e) {
        return [e.time, '[' + e.level + ']', e.sourceLabel, e.userLabel, e.summary].join(' | ');
      });
      var content = lines.join('\n') || '(无记录)';
      if (body.format === 'json') content = JSON.stringify(adapted.logs || [], null, 2);
      return { content: content, filename: 'scrcpygate-runtime.' + (body.format === 'json' ? 'json' : 'txt') };
    });
  }

  function handlerLogsIntegrity() {
    return apiPost('/api/admin/logs/integrity-check', {}).then(function (r) {
      var ok = !!(r && r.ok);
      return {
        ok: ok,
        status: ok ? '通过' : '未通过',
        state: ok ? 'passed' : 'failed',
        resultLabel: ok ? '通过' : '未通过',
        checkedAt: new Date().toLocaleString(),
        range: '未检查',
        verifiedCount: r && r.checked != null ? r.checked : 0,
        failedCount: ok ? 0 : 1,
        issues: []
      };
    });
  }

  function handlerUsersList() {
    return fetchUsersEnriched();
  }

  function handlerUsersCreate(opts) {
    var body = (opts && opts.body) || {};
    var req = {
      username: body.username,
      role: body.role === 'admin' ? 'admin' : 'user',
      must_change_password: body.forcePasswordChange === true
    };
    if (body.password) req.password = body.password;
    if (body.enabled !== undefined) req.enabled = body.enabled !== false;
    if (body.alasVisible !== undefined || body.alas_visible !== undefined) {
      req.alas_visible = (body.alasVisible !== undefined ? body.alasVisible : body.alas_visible) !== false;
    }
    if (body.expiry) req.expires_at = expiryEpoch(body.expiry);
    else req.expires_at = null;
    return apiPut('/api/admin/users', req).then(function () {
      return fetchUsersEnriched();
    });
  }

  function handlerUsersUpdate(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var req = { username: params.id || body.username };
    if (body.role !== undefined) req.role = body.role === 'admin' ? 'admin' : 'user';
    if (body.expiry !== undefined) req.expires_at = body.expiry ? expiryEpoch(body.expiry) : null;
    if (body.password) req.password = body.password;
    if (body.enabled !== undefined) req.enabled = body.enabled !== false;
    if (body.alasVisible !== undefined || body.alas_visible !== undefined) {
      req.alas_visible = (body.alasVisible !== undefined ? body.alasVisible : body.alas_visible) !== false;
    }
    return apiPut('/api/admin/users', req).then(function () {
      return fetchUsersEnriched();
    });
  }

  function handlerUsersDelete(opts) {
    var params = (opts && opts.params) || {};
    return apiDelete('/api/admin/users/' + encodeURIComponent(params.id || '')).then(function () {
      return fetchUsersEnriched();
    });
  }

  function handlerUsersResetPassword(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    return apiPut('/api/admin/users', {
      username: params.id,
      password: body.password,
      must_change_password: body.forcePasswordChange === true
    }).then(function (r) {
      return r || { ok: true };
    });
  }

  function handlerDevicePermissionUpdate(opts) {
    var body = (opts && opts.body) || {};
    return apiPut('/api/admin/permissions', {
      username: body.username,
      device_id: body.deviceId || body.device_id,
      enabled: body.enabled !== false,
      can_view: body.canView !== undefined ? !!body.canView : !!body.can_view,
      can_control: body.canControl !== undefined ? !!body.canControl : !!body.can_control
    });
  }

  function handlerDeviceAlasBinding(opts) {
    var body = (opts && opts.body) || {};
    var req = {
      username: body.username,
      config_name: body.configName || body.config_name,
      device_id: body.deviceId || body.device_id,
      enabled: body.enabled !== false,
      can_run: body.canRun !== undefined ? !!body.canRun : true,
      can_edit: body.canEdit !== undefined ? !!body.canEdit : false,
      grant_view: body.grantView === true,
      confirm_move: body.confirmMove === true
    };
    /* 只有调用方显式指定时才动「默认配置」：设备页从不显示默认，
       以前这里默认 true 会在绑定配置时悄悄改掉用户的默认配置。 */
    if (body.isDefault !== undefined) req.is_default = !!body.isDefault;
    return apiPut('/api/admin/alas/permissions', req);
  }

  function handlerUsersPermissions(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var username = params.id;
    var method = String((opts && opts.method) || 'GET').toUpperCase();
    if (method === 'PUT') {
      // The management drawer submits a complete matrix. One server request
      // keeps device and ALAS changes atomic; the legacy per-row code below
      // remains as a compatibility fallback for older callers.
      if (Array.isArray(body.devicePermissions) || body.deviceIds !== undefined || body.configIds !== undefined) {
        var requestedDevices = Array.isArray(body.devicePermissions)
          ? body.devicePermissions.map(function (permission) {
            return {
              device_id: permission.deviceId || permission.device_id,
              can_view: permission.canView !== false && permission.can_view !== false,
              can_control: permission.canControl === true || permission.can_control === true
            };
          })
          : (Array.isArray(body.deviceIds) ? body.deviceIds.map(function (deviceId) {
            return { device_id: deviceId, can_view: true, can_control: false };
          }) : []);
        /* 抽屉打开时的快照：不在快照里、但服务端已经存在的授权，是别人在编辑期间
           新建的，不属于本次编辑范围 —— 保留，别让「整体替换」静默删掉它们。 */
        var deviceBaseline = Array.isArray(body.deviceIdsBaseline) ? body.deviceIdsBaseline.map(String) : null;
        var configBaseline = Array.isArray(body.configIdsBaseline) ? body.configIdsBaseline.map(String) : null;
        return Promise.all([
          apiGet('/api/admin/alas/permissions'),
          apiGet('/api/admin/alas/configs'),
          deviceBaseline ? apiGet('/api/admin/permissions') : Promise.resolve(null)
        ]).then(function (results) {
          var bindings = (results[0] && (results[0].assignments || results[0].bindings)) || [];
          var selected = body.configIds === null ? null : (Array.isArray(body.configIds) ? body.configIds.map(String) : null);
          var names = {};
          bindings.forEach(function (binding) {
            if (binding.username === username) names[String(binding.config_name)] = binding;
          });
          if (selected) selected.forEach(function (name) { if (!names[name]) names[name] = { config_name: name }; });
          var assignments = Object.keys(names).filter(function (name) {
            if (selected === null || selected.indexOf(name) >= 0) return true;
            return !!(configBaseline && configBaseline.indexOf(name) < 0);
          }).map(function (name, index) {
            var binding = names[name] || {};
            return {
              config_name: name,
              device_id: binding.device_id || null,
              can_run: binding.can_run !== false,
              can_edit: binding.can_edit === true,
              is_default: binding.is_default === true || (index === 0 && !Object.keys(names).some(function (key) { return names[key].is_default; }))
            };
          });
          var submittedDevices = requestedDevices;
          if (deviceBaseline && results[2]) {
            /* /api/admin/permissions 对「每个普通用户 × 每台设备」都返回一行，没有授权
               记录的行是 assigned=false / can_view=false。这里只想保留「编辑期间别人
               新加的授权」，所以必须同时看 assigned 与 can_view：只按 device_id 判断
               会把整库未授权设备都补成可观看（加一台设备保存后全部设备都被绑定）。 */
            var permissionRows = results[2].permissions || [];
            var publicByRef = {};
            permissionRows.forEach(function (row) {
              var publicId = String(row.public_device_id || '');
              if (!publicId) return;
              publicByRef[String(row.device_id || '')] = publicId;
              publicByRef[publicId] = publicId;
            });
            var inBaseline = function (ref) {
              var normalized = publicByRef[ref] || ref;
              return deviceBaseline.indexOf(ref) >= 0 || deviceBaseline.indexOf(normalized) >= 0;
            };
            var known = {};
            submittedDevices.forEach(function (row) {
              var ref = String(row.device_id || '');
              known[ref] = true;
              known[publicByRef[ref] || ref] = true;
            });
            permissionRows.forEach(function (row) {
              if (row.username !== username) return;
              if (!row.assigned || !row.can_view) return;
              var ref = publicByRef[String(row.device_id || '')] || String(row.public_device_id || row.device_id || '');
              if (!ref || known[ref] || inBaseline(ref)) return;
              submittedDevices.push({ device_id: ref, can_view: true, can_control: !!row.can_control });
              known[ref] = true;
            });
          }
          return apiPut('/api/admin/users/' + encodeURIComponent(username) + '/permissions', {
            device_permissions: submittedDevices,
            alas_assignments: assignments
          }).then(function (payload) { return requireSuccessful(payload, '用户权限保存失败'); });
        });
      }
      var hasDevicePermissions = Array.isArray(body.devicePermissions);
      var hasDeviceIds = body.deviceIds !== undefined;
      var hasConfigIds = body.configIds !== undefined;
      var deviceIds = hasDeviceIds ? body.deviceIds : null;
      var configIds = hasConfigIds ? body.configIds : null;
      var requestedPermissions = {};
      if (hasDevicePermissions) {
        body.devicePermissions.forEach(function (permission) {
          var deviceId = String(permission.deviceId || permission.device_id || '');
          if (!deviceId) return;
          requestedPermissions[deviceId] = {
            canView: permission.canView !== false && permission.can_view !== false,
            canControl: permission.canControl === true || permission.can_control === true
          };
        });
      }
      var deviceTask = (!hasDevicePermissions && !hasDeviceIds) ? Promise.resolve() : apiGet('/api/admin/permissions').then(function (payload) {
        var perms = payload.permissions || [];
        var tasks = [];
        perms.forEach(function (p) {
          if (p.username !== username) return;
          var publicId = p.public_device_id || (state.deviceRefMap[p.device_id] && state.deviceRefMap[p.device_id].publicId) || p.device_id;
          var requested = requestedPermissions[publicId] || requestedPermissions[p.device_id] || null;
          var allow = hasDevicePermissions ? !!(requested && requested.canView) : (deviceIds === null || deviceIds.indexOf(publicId) >= 0);
          var canControl = hasDevicePermissions ? !!(allow && requested.canControl) : !!(allow && p.can_control);
          tasks.push(apiPut('/api/admin/permissions', {
            username: username,
            device_id: publicId,
            enabled: allow,
            can_view: allow,
            can_control: canControl
          }));
        });
        return Promise.all(tasks);
      });
      return deviceTask.then(function () {
        if (!hasConfigIds) return null;
        return Promise.all([apiGet('/api/admin/alas/permissions'), apiGet('/api/admin/alas/configs')]);
      }).then(function (results) {
        if (!hasConfigIds || !results) return null;
        var bindings = (results[0].assignments || []).filter(function (b) { return b.username === username; });
        var names = {};
        bindings.forEach(function (b) { names[b.config_name] = true; });
        if (configIds !== null) configIds.forEach(function (name) { names[String(name)] = true; });
        var catalog = results[1].configs || [];
        catalog.forEach(function (entry) {
          var name = typeof entry === 'string' ? entry : (entry && (entry.config_name || entry.name || entry.id));
          if (configIds === null && name) names[String(name)] = true;
        });
        var defaultName = '';
        bindings.forEach(function (b) { if (b.is_default) defaultName = b.config_name; });
        if (!defaultName && configIds && configIds.length) defaultName = String(configIds[0]);
        var more = [];
        Object.keys(names).forEach(function (configName) {
          var existing = null;
          for (var i = 0; i < bindings.length; i++) {
            if (bindings[i].config_name === configName) { existing = bindings[i]; break; }
          }
          var allow = configIds === null || configIds.indexOf(configName) >= 0;
          if (!allow) {
            more.push(apiPut('/api/admin/alas/permissions', {
              username: username, config_name: configName, enabled: false, is_default: false
            }));
            return;
          }
          more.push(apiPut('/api/admin/alas/permissions', {
            username: username,
            config_name: configName,
            enabled: true,
            can_run: existing ? existing.can_run !== false : true,
            can_edit: existing ? existing.can_edit === true : false,
            is_default: configName === defaultName,
            device_id: existing && existing.device_id ? existing.device_id : undefined
          }));
        });
        return Promise.all(more);
      }).then(function () {
        return { ok: true };
      });
    }
    return apiGet('/api/admin/permissions').then(function (payload) {
      var perms = payload.permissions || [];
      var items = [];
      perms.forEach(function (p) {
        if (p.username !== username) return;
        var ref = state.deviceRefMap[p.device_id] || state.deviceRefMap[p.public_device_id] || {};
        var publicId = p.public_device_id || ref.publicId || p.device_id;
        items.push({
          permission: p.can_control ? 'control' : 'watch',
          device: ref.name || p.device_name || publicId,
          deviceId: publicId,
          canView: !!p.can_view,
          canControl: !!p.can_control
        });
      });
      return { items: items, permissions: items };
    });
  }

  function handlerAlasOverview() {
    return adaptAlasOverview();
  }

  function handlerAlasSettingsUpdate(opts) {
    var body = (opts && opts.body) || {};
    var req = {};
    if (body.enabled !== undefined) req.enabled = !!body.enabled;
    if (body.runtimeUrl !== undefined) req.base_url = body.runtimeUrl;
    var workbenchVisible = body.workbenchAlasVisible;
    if (workbenchVisible === undefined) workbenchVisible = body.workbench_alas_visible;
    if (workbenchVisible !== undefined) req.workbench_alas_visible = settingBoolean(workbenchVisible, true);
    if (body.token) req.api_token = body.token;
    if (body.clearToken) req.clear_token = true;
    return apiPut('/api/admin/alas', req).then(function (r) {
      return r || { ok: true };
    });
  }

  function handlerAlasConfigAction(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var configName = body.configId || params.id || body.config_name;
    return apiPost('/api/admin/alas/toggle', {
      config_name: configName,
      device_id: body.deviceId || body.device_id || '',
      action: body.action || 'toggle'
    }).then(function (r) { return requireSuccessful(r, 'ALAS 配置操作失败'); });
  }

  function handlerAlasRelationUpdate(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var relId = params.id;
    var rel = state.alasRelationsMap[relId];
    var req = {
      username: body.userId || (rel && rel.username),
      config_name: body.configId || (rel && rel.configName),
      can_run: body.canRun !== undefined ? !!body.canRun : true,
      can_edit: body.canEdit !== undefined ? !!body.canEdit : false
    };
    if (body.deviceId !== undefined) req.device_id = body.deviceId || null;
    if (body.isDefault !== undefined) req.is_default = !!body.isDefault;
    if (body.grantView !== undefined) req.grant_view = !!body.grantView;
    if (body.confirmMove !== undefined) req.confirm_move = !!body.confirmMove;
    return apiPut('/api/admin/alas/permissions', req).then(function (r) {
      return requireSuccessful(r, 'ALAS 权限保存失败') || { ok: true };
    });
  }

  function handlerAlasRelationCreate(opts) {
    var body = (opts && opts.body) || {};
    var req = {
      username: body.userId,
      config_name: body.configId,
      can_run: body.canRun !== undefined ? !!body.canRun : true,
      can_edit: body.canEdit !== undefined ? !!body.canEdit : false
    };
    if (body.deviceId !== undefined) req.device_id = body.deviceId || null;
    if (body.isDefault !== undefined) req.is_default = !!body.isDefault;
    if (body.grantView !== undefined) req.grant_view = !!body.grantView;
    if (body.confirmMove !== undefined) req.confirm_move = !!body.confirmMove;
    return apiPut('/api/admin/alas/permissions', req).then(function (r) {
      return requireSuccessful(r, 'ALAS 权限保存失败') || { ok: true };
    });
  }

  function handlerAlasRelationDelete(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var relId = params.id || body.relationId;
    var rel = state.alasRelationsMap[relId] || {};
    if (!rel.username || !rel.configName) return Promise.reject(unsupportedError('未找到要解除的 ALAS 绑定'));
    return apiPut('/api/admin/alas/permissions', {
      username: rel.username, config_name: rel.configName, enabled: false, is_default: false
    }).then(function (r) {
      return requireSuccessful(r, 'ALAS 绑定解除失败') || { ok: true };
    });
  }

  function handlerAlasRelationBulk(opts) {
    var body = (opts && opts.body) || {};
    var userId = body.userId;
    var relations = body.relations || [];
    var chain = Promise.resolve();
    relations.forEach(function (r) {
      chain = chain.then(function () {
        var rel = state.alasRelationsMap[r.id] || {};
        var req = {
          username: userId || rel.username,
          config_name: rel.configName,
          can_run: r.canRun !== undefined ? !!r.canRun : true,
          can_edit: r.canEdit !== undefined ? !!r.canEdit : false,
          is_default: !!r.isDefault
        };
        if (rel.deviceId) req.device_id = rel.deviceId;
        return apiPut('/api/admin/alas/permissions', req).then(function (payload) {
          return requireSuccessful(payload, 'ALAS 权限保存失败');
        });
      });
    });
    return chain.then(function () { return { ok: true }; });
  }

  function handlerAlasConnectionCheck(opts) {
    var body = (opts && opts.body) || {};
    var req = {};
    if (body.runtimeUrl) req.base_url = body.runtimeUrl;
    return apiPost('/api/admin/alas/check', req).then(function (payload) {
      var d = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return {
        state: d.state || (d.ok ? 'connected' : 'unreachable'),
        status: d.state || '',
        checkedAt: new Date().toLocaleString(),
        tokenConfigured: d.token_configured === true,
        baseUrl: d.base_url || ''
      };
    }).catch(function (error) {
      var status = error && error.detail && error.detail.status;
      return {
        state: error && error.code === 'TIMEOUT' ? 'timeout' : 'unreachable',
        status: String(status || ''),
        checkedAt: new Date().toLocaleString(),
        tokenConfigured: false
      };
    });
  }

  function handlerAlasConfigStatus() {
    return apiGet('/api/admin/alas').then(function (admin) {
      return admin.status || { ok: false, status: 'unknown' };
    });
  }

  function handlerAlasConfigs(opts) {
    var query = (opts && opts.query) || {};
    var serverQuery = {};
    if (query.device_id) serverQuery.device_id = query.device_id;
    return apiGet('/api/alas/configs', serverQuery).then(function (payload) {
      var configs = (payload.configs || []).map(function (c) {
        return {
          id: c.config_name,
          config_id: c.config_name,
          configId: c.config_name,
          name: c.config_name,
          config_name: c.config_name,
          displayName: c.config_name,
          task: '自动化任务',
          can_run: c.can_run !== false,
          can_edit: c.can_edit === true,
          is_default: c.is_default === true,
          // 管理员没有绑定时后端会回退到 Runtime 配置目录，这不是绑定关系；
          // 顶部 ALAS 状态项据此判断「未绑定不显示」。
          admin_fallback: c.admin_fallback === true,
          state: 'stopped',
          openUrl: '/alas/embed/'
        };
      });
      return { configs: configs, default_config: payload.default_config || '' };
    });
  }

  function handlerAlasStatus(opts) {
    var query = (opts && opts.query) || {};
    var serverQuery = {};
    if (query.config) serverQuery.config = query.config;
    if (query.device_id) serverQuery.device_id = query.device_id;
    return apiGet('/api/alas/status', serverQuery).then(function (payload) {
      var status = payload.status || 'stopped';
      var labels = { running: '运行中', error: '异常', unbound: '未绑定', stopped: '已停止', disabled: '已禁用', disconnected: '已断开' };
      return {
        state: status,
        status: status,
        label: labels[status] || status,
        detail: payload.error || '',
        message: payload.error || '',
        stopReason: null,
        ok: payload.ok !== false,
        configured: payload.configured !== false
      };
    });
  }

  function handlerAlasToggle(opts) {
    var body = (opts && opts.body) || {};
    return apiPost('/api/alas/toggle', {
      config_name: body.config_name || body.config_id,
      device_id: body.device_id,
      action: body.action || 'toggle'
    }).then(function (r) { return requireSuccessful(r, 'ALAS 操作失败'); });
  }

  function internalIdFor(publicId) {
    var ref = state.deviceRefMap && state.deviceRefMap[publicId];
    if (ref && ref.internalId) return ref.internalId;
    return publicId;
  }

  // 新建/更新设备后立刻登记 public→internal 映射：否则紧接着的
  // 测试连接 / 编辑 / 删除会把 public id 当成内部 id 发给后端并得到 404。
  function rememberDeviceRef(publicId, device) {
    var internalId = String((device && (device.id || device.device_id)) || publicId || '');
    var resolvedPublic = String(publicId || internalId);
    if (!resolvedPublic || !internalId) return;
    var ref = {
      publicId: resolvedPublic,
      internalId: internalId,
      address: (device && (device.address || device.adb)) || '',
      name: (device && (device.name || device.display_name)) || ''
    };
    if (!state.deviceRefMap) state.deviceRefMap = {};
    state.deviceRefMap[resolvedPublic] = ref;
    state.deviceRefMap[internalId] = ref;
  }

  function handlerDevicesCheck(opts) {
    var params = (opts && opts.params) || {};
    var publicId = params.id;
    var cached = state.deviceRefMap[publicId] || {};
    return apiPost('/api/admin/devices/' + encodeURIComponent(internalIdFor(publicId)) + '/adb/test', {}).then(function (r) {
      var d = r || {};
      var rawState = String(d.adb_state || d.state || '').toLowerCase();
      var status = d.ok === true || rawState === 'device' || rawState === 'connected' ? 'online' : deviceStatusOf({ enabled: true, adb_state: rawState || 'unknown' });
      var failed = d.ok === false || !!String(d.last_error || '').trim();
      return {
        device: {
          id: publicId,
          internalId: cached.internalId || internalIdFor(publicId),
          status: status,
          online: status === 'online',
          adb: d.address || cached.address || '',
          latency: d.latency_ms != null ? d.latency_ms : null,
          heartbeat: fmtTs(d.last_checked_at),
          lastError: failed ? (d.last_error || d.error || d.detail || null) : null,
          statusDetail: d.detail || d.diagnostic || '',
          checkOk: !failed
        }
      };
    });
  }

  function handlerDevicesCreate(opts) {
    var body = (opts && opts.body) || {};
    return apiPut('/api/admin/devices', {
      address: body.address || body.adb || '',
      name: body.name || '',
      enabled: body.enabled !== false
    }).then(function (r) {
      var devices = r.devices || [];
      var created = null;
      if (r.device_id) {
        for (var i = 0; i < devices.length; i++) {
          if (devices[i].id === r.device_id || devices[i].device_id === r.device_id) created = devices[i];
        }
      }
      if (!created && devices.length) created = devices[devices.length - 1];
      if (!created) return { ok: true, device: { id: r.public_device_id || r.device_id } };
      var adminById = {};
      devices.forEach(function (device) {
        adminById[device.id] = device;
        if (device.public_id) adminById[device.public_id] = device;
      });
      var publicId = created.public_id || created.id;
      rememberDeviceRef(publicId, created);
      return { ok: true, device: adaptDevice(Object.assign({}, created, { id: publicId, device_id: publicId }), adminById) };
    });
  }

  function handlerDevicesUpdate(opts) {
    var params = (opts && opts.params) || {};
    var body = (opts && opts.body) || {};
    var publicId = params.id;
    // 只下发调用方真正给出的字段：这里以前无条件带 address:'' ，
    // 于是「停用/启用设备」这种单字段请求会因为 address_required 被服务端拒绝。
    var req = { device_id: internalIdFor(publicId) };
    var address = body.address || body.adb;
    if (address) req.address = address;
    if (body.name !== undefined) req.name = body.name;
    if (body.enabled !== undefined) req.enabled = !!body.enabled;
    return apiPut('/api/admin/devices', req).then(function (r) {
      var devices = r.devices || [];
      var updated = null;
      for (var i = 0; i < devices.length; i++) {
        if (devices[i].id === internalIdFor(publicId)) updated = devices[i];
      }
      if (!updated) return { ok: true, device: { id: publicId } };
      var adminById = {};
      devices.forEach(function (device) {
        adminById[device.id] = device;
        if (device.public_id) adminById[device.public_id] = device;
      });
      var resolvedPublicId = updated.public_id || publicId;
      rememberDeviceRef(resolvedPublicId, updated);
      return { ok: true, device: adaptDevice(Object.assign({}, updated, { id: resolvedPublicId, device_id: resolvedPublicId }), adminById) };
    });
  }

  function handlerDevicesDelete(opts) {
    var params = (opts && opts.params) || {};
    return apiDelete('/api/admin/devices/' + encodeURIComponent(internalIdFor(params.id))).then(function (r) {
      return r || { ok: true };
    });
  }

  /* 工作台通知中心：读当前用户的收件箱（/api/notifications），
     服务端只存 kind + data，文案在这里按语言生成。 */
  var NOTIFICATION_TITLES = {
    account_expiring: ['账户即将到期', 'Account expiring soon'],
    account_expired: ['账户已到期', 'Account expired'],
    account_updated: ['账户信息已更新', 'Account updated'],
    permission_changed: ['权限已变更', 'Permissions changed'],
    session_disconnected: ['投屏会话已断开', 'Casting session ended'],
    device_unavailable: ['设备已不可用', 'Device unavailable']
  };
  var NOTIFICATION_SEVERITY_TONE = {
    error: 'error',
    warning: 'warn',
    warn: 'warn',
    success: 'success',
    info: 'info'
  };
  var ACCOUNT_FIELD_LABELS = {
    created: ['新建', 'created'],
    enabled: ['启用状态', 'enabled'],
    role: ['角色', 'role'],
    expires_at: ['到期时间', 'expiry'],
    password: ['密码', 'password']
  };

  function notificationDate(epochSeconds) {
    var numeric = Number(epochSeconds);
    if (!isFinite(numeric) || numeric <= 0) return '';
    var date = new Date(numeric * 1000);
    if (isNaN(date.getTime())) return '';
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function notificationMessage(kind, data) {
    data = data || {};
    var en = auditIsEnglish();
    var days = Number(data.days);
    var expires = notificationDate(data.expires_at);
    var device = String(data.device || '').trim();
    var actor = String(data.actor || '').trim();
    if (kind === 'account_expiring') {
      if (en) {
        return (expires ? 'Expires on ' + expires : 'Expiring soon')
          + (isFinite(days) && days > 0 ? ' · ' + days + ' days left' : '')
          + ' — ask an administrator to renew';
      }
      return (expires ? '将于 ' + expires + ' 到期' : '即将到期')
        + (isFinite(days) && days > 0 ? '，还剩 ' + days + ' 天' : '')
        + '，请联系管理员续期';
    }
    if (kind === 'account_expired') {
      if (en) {
        return (expires ? 'Expired on ' + expires : 'Expired') + ' — device and ALAS access has stopped';
      }
      return (expires ? '已于 ' + expires + ' 到期' : '已到期') + '，设备投屏与 ALAS 权限已失效';
    }
    if (kind === 'account_updated') {
      var changed = (data.changed || []).map(function (key) {
        var labels = ACCOUNT_FIELD_LABELS[key];
        return labels ? labels[en ? 1 : 0] : String(key || '');
      }).filter(Boolean);
      var list = changed.join(en ? ', ' : '、');
      if (en) {
        return 'An administrator' + (actor ? ' ' + actor : '') + ' updated your account'
          + (list ? ' (' + list + ')' : '');
      }
      return '管理员' + (actor ? ' ' + actor + ' ' : '') + '更新了你的账户' + (list ? '（' + list + '）' : '');
    }
    if (kind === 'permission_changed') {
      var scope = String(data.scope || '').toLowerCase();
      var detail = String(data.detail || '').trim();
      if (en) {
        var what = scope === 'alas' ? 'ALAS config' : (scope === 'device' ? 'Device' : 'Permission');
        return what + ' access ' + (data.revoked ? 'was revoked' : 'was updated')
          + (detail ? ': ' + detail : '')
          + (actor ? ' (by ' + actor + ')' : '');
      }
      var label = scope === 'alas' ? 'ALAS 配置' : (scope === 'device' ? '设备' : '权限');
      return label + '权限' + (data.revoked ? '已被收回' : '已更新')
        + (detail ? '：' + detail : '')
        + (actor ? '（管理员 ' + actor + '）' : '');
    }
    if (kind === 'session_disconnected') {
      if (en) {
        return 'An administrator' + (actor ? ' ' + actor : '') + ' ended '
          + (device ? 'the "' + device + '"' : 'your') + ' casting session';
      }
      return '管理员' + (actor ? ' ' + actor + ' ' : '') + '结束了'
        + (device ? '「' + device + '」' : '你的') + '投屏会话';
    }
    if (kind === 'device_unavailable') {
      if (en) return (device ? 'Device "' + device + '"' : 'A device you used') + ' was disabled or removed';
      return (device ? '设备「' + device + '」' : '你使用的设备') + '已被停用或删除';
    }
    return '';
  }

  function adaptNotification(item) {
    var raw = item && typeof item === 'object' ? item : {};
    var kind = String(raw.kind || '').trim();
    var data = raw.data && typeof raw.data === 'object' ? raw.data : {};
    var titlePair = NOTIFICATION_TITLES[kind] || [];
    var title = titlePair.length ? titlePair[auditIsEnglish() ? 1 : 0] : (auditIsEnglish() ? 'Notice' : '通知');
    var message = notificationMessage(kind, data);
    var severity = String(raw.severity || 'info').toLowerCase();
    return {
      id: String(raw.id == null ? '' : raw.id),
      kind: kind,
      title: title,
      message: message,
      href: String(raw.href || ''),
      severity: severity,
      result: severity === 'error' ? 'failure' : (severity === 'warning' ? 'denied' : 'success'),
      type: NOTIFICATION_SEVERITY_TONE[severity] || 'info',
      createdAt: Number(raw.created_at) || 0,
      time: Number(raw.created_at) || 0,
      read: raw.read === true
    };
  }

  function handlerNotificationsList(opts) {
    var query = (opts && opts.query) || {};
    var serverQuery = {};
    var limit = Number(query.limit);
    if (isFinite(limit) && limit > 0) serverQuery.limit = Math.min(100, Math.round(limit));
    return apiGet('/api/notifications', serverQuery).then(function (payload) {
      payload = payload && typeof payload === 'object' ? payload : {};
      var source = Array.isArray(payload.items) ? payload.items : [];
      var items = source.map(adaptNotification).filter(function (item) { return !!item.id; });
      return {
        items: items,
        notifications: items,
        total: Number(payload.total != null ? payload.total : items.length),
        unread: Number(payload.unread != null ? payload.unread : items.filter(function (item) { return !item.read; }).length)
      };
    });
  }

  function handlerNotificationsMarkRead(opts) {
    var body = (opts && opts.body) || {};
    var request = {};
    if (body.all === true || body.mark_all === true) request.all = true;
    else if (Array.isArray(body.ids)) request.ids = body.ids.map(Number).filter(function (value) { return isFinite(value); });
    if (!request.all && !(request.ids && request.ids.length)) {
      return Promise.resolve({ ok: true, marked: 0 });
    }
    return apiPost('/api/notifications/read', request).then(function (payload) {
      var result = payload && typeof payload === 'object' ? payload : {};
      return { ok: true, marked: Number(result.marked) || 0, unread: Number(result.unread) || 0 };
    });
  }

  function handlerAuthLogin(opts) {
    var body = (opts && opts.body) || {};
    return apiPost('/api/auth/login', {
      username: body.username,
      password: body.password,
      proof: body.proof || undefined
    }).then(function (payload) {
      updateCsrfMeta(payload && payload.csrf_token);
      return payload;
    });
  }

  function handlerAuthChallenge(opts) {
    var params = (opts && opts.params) || {};
    var user = encodeURIComponent(params.user || '');
    return apiGet('/api/auth/challenge?user=' + user).then(function (payload) { return payload || {}; });
  }

  function handlerLoginGuard(opts) {
    return apiGet('/api/admin/login-guard').then(function (payload) { return payload || {}; });
  }

  function handlerLoginGuardUpdate(opts) {
    var body = (opts && opts.body) || {};
    return apiPut('/api/admin/login-guard', body.config || body);
  }

  function handlerLoginGuardUnlock(opts) {
    var body = (opts && opts.body) || {};
    return apiPost('/api/admin/login-guard/unlock', { key: body.key });
  }

  /* 账户到期策略：只读写 /api/admin/settings 里的两个字段，其余设置保持不变。 */
  function accountPolicyPayload(payload) {
    var settings = (payload && payload.settings) || payload || {};
    return {
      expiryReminderDays: settings.expiryReminderDays == null ? 3 : Number(settings.expiryReminderDays),
      stopAlasOnExpiry: settings.stopAlasOnExpiry === true || settings.stopAlasOnExpiry === 'true'
    };
  }

  function handlerAccountPolicy() {
    return apiGet('/api/admin/settings').then(function (payload) { return accountPolicyPayload(payload); });
  }

  function handlerAccountPolicyUpdate(opts) {
    var body = (opts && opts.body) || {};
    var request = {};
    if (body.expiryReminderDays !== undefined) request.expiryReminderDays = Number(body.expiryReminderDays);
    if (body.stopAlasOnExpiry !== undefined) request.stopAlasOnExpiry = !!body.stopAlasOnExpiry;
    if (!Object.keys(request).length) return handlerUnsupported('没有需要保存的账户策略');
    return apiPut('/api/admin/settings', request).then(function (payload) { return accountPolicyPayload(payload); });
  }

  /* 日志保存时长：同样只读写 /api/admin/settings 的一个字段（0 = 永久保留）。 */
  function logRetentionPayload(payload) {
    var settings = (payload && payload.settings) || payload || {};
    var days = settings.logRetentionDays;
    days = days == null ? 90 : Number(days);
    if (!isFinite(days) || days < 0) days = 0;
    return { logRetentionDays: Math.floor(days) };
  }

  function handlerLogRetention() {
    return apiGet('/api/admin/settings').then(function (payload) { return logRetentionPayload(payload); });
  }

  function handlerLogRetentionUpdate(opts) {
    var body = (opts && opts.body) || {};
    if (body.logRetentionDays === undefined) return handlerUnsupported('没有需要保存的日志保留设置');
    return apiPut('/api/admin/settings', { logRetentionDays: Number(body.logRetentionDays) })
      .then(function (payload) { return logRetentionPayload(payload); });
  }

  /* 系统更新检查（只读）：后台只显示当前/最新版本与宿主机更新命令，
     不提供下载或应用按钮——应用更新由宿主机 deploy.sh --update 完成。 */
  function updateCheckPayload(payload) {
    var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
    var latest = data.latest && typeof data.latest === 'object' ? data.latest : null;
    return {
      ok: data.ok === true,
      reachable: data.reachable !== false && data.ok === true,
      noRelease: data.noRelease === true || data.no_release === true,
      checkedAt: data.checkedAt == null ? (data.checked_at == null ? null : Number(data.checked_at)) : Number(data.checkedAt),
      cached: data.cached === true,
      currentVersion: String(((data.current || {}).version) || 'dev'),
      currentImage: String(((data.current || {}).image) || ''),
      image: String(data.image || ''),
      repository: String(data.repository || ''),
      latestVersion: latest ? String(latest.version || latest.tag || '') : '',
      latestSource: latest ? String(latest.source || '') : '',
      latestPublishedAt: latest ? String(latest.published_at || '') : '',
      latestUrl: latest ? String(latest.url || '') : '',
      updateAvailable: data.updateAvailable === undefined ? data.update_available : data.updateAvailable,
      hostCommand: String(data.hostCommand || data.host_command || ''),
      releaseUrl: String(data.releaseUrl || data.release_url || ''),
      error: String(data.error || '')
    };
  }

  function handlerSystemUpdate(opts) {
    var query = {};
    if (opts && (opts.refresh || (opts.query && opts.query.refresh))) query.refresh = 1;
    return apiGet('/api/admin/update-check', query).then(function (payload) { return updateCheckPayload(payload); });
  }

  /* 访问记录（VIS）：IP 汇总 + 明细 + 线索 + 观测健康 + 导出。
     服务端已在写入时脱敏（无 query/Referer/body），这里只做形状归一化。 */
  function accessPayload(payload) {
    var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
    var page = data.page || {};
    return {
      items: Array.isArray(data.items) ? data.items : [],
      page: page,
      hasMore: page.hasMore === true || page.has_more === true,
      nextBeforeId: page.nextBeforeId != null ? page.nextBeforeId : (page.next_before_id != null ? page.next_before_id : null),
      offset: Number(page.offset) || 0,
      window: data.window || {},
      stats: data.stats || {},
      hints: data.hints || null,
      observation: data.observation || null,
      settings: data.settings || null
    };
  }

  function handlerAccessSummary(opts) {
    var query = (opts && opts.query) || {};
    return apiGet('/api/admin/access/summary', query).then(function (payload) { return accessPayload(payload); });
  }

  function handlerAccessRecords(opts) {
    var query = (opts && opts.query) || {};
    return apiGet('/api/admin/access/records', query).then(function (payload) { return accessPayload(payload); });
  }

  function handlerAccessHints(opts) {
    var query = (opts && opts.query) || {};
    return apiGet('/api/admin/access/hints', query).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return {
        hints: Array.isArray(data.hints) ? data.hints : [],
        window: data.window || {},
        thresholds: data.thresholds || {},
        note: String(data.note || '')
      };
    });
  }

  function handlerAccessStatus(opts) {
    return apiGet('/api/admin/access/status', {}).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return {
        enabled: data.enabled !== false,
        writer: data.writer || {},
        retentionDays: data.retentionDays != null ? data.retentionDays : (data.retention_days != null ? data.retention_days : null),
        rowCap: data.rowCap != null ? data.rowCap : (data.row_cap != null ? data.row_cap : null),
        dropped: data.dropped || {},
        degraded: data.degraded === true
      };
    });
  }

  /* 访问记录导出：与审计日志导出同样只取 JSON 分支，再由客户端合成目标格式。
     服务端确实有 text/csv 分支，但附件响应在个别浏览器（headless Chrome）会被
     吞成空响应，且 api.js 对非 JSON 响应只保留前 240 字符。 */
  var ACCESS_CSV_COLUMNS = ['ts', 'source_ip', 'ip_version', 'country', 'identity', 'account', 'method',
    'kind', 'route_template', 'path_sample', 'status', 'duration_ms', 'decision', 'request_id', 'user_agent'];

  function accessCsvCell(value) {
    if (value === null || value === undefined) return '';
    var text = String(value);
    // 防表格公式注入：与审计日志导出一致的处理。
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return text;
  }

  function accessCsvEscape(text) {
    return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function accessToCsv(records) {
    var rows = [ACCESS_CSV_COLUMNS.join(',')];
    (records || []).forEach(function (item) {
      rows.push(ACCESS_CSV_COLUMNS.map(function (column) {
        return accessCsvEscape(accessCsvCell(item[column]));
      }).join(','));
    });
    return '\ufeff' + rows.join('\r\n');
  }

  function handlerAccessExport(opts) {
    var body = (opts && opts.body) || {};
    var format = body.format === 'json' ? 'json' : 'csv';
    var reqBody = { format: 'json' };
    ['from_ts', 'to_ts', 'source_ip', 'country', 'ban_state', 'identity', 'status', 'status_class',
      'decision', 'kind', 'account', 'request_id', 'q', 'include_admin_poll'].forEach(function (key) {
        if (body[key] !== undefined && body[key] !== null && body[key] !== '') reqBody[key] = body[key];
      });
    return apiPost('/api/admin/access/export', reqBody).then(function (payload) {
      var records = (payload && Array.isArray(payload.records)) ? payload.records : [];
      var truncated = !!(payload && payload.truncated);
      if (format === 'json') {
        return {
          content: JSON.stringify({ records: records, exported_count: records.length, truncated: truncated }, null, 2),
          filename: 'scrcpygate-access.json',
          count: records.length,
          truncated: truncated
        };
      }
      return { content: accessToCsv(records), filename: 'scrcpygate-access.csv', count: records.length, truncated: truncated };
    });
  }

  /* 访问记录设置（采集开关 + 保留档位）：走 /api/admin/settings，与日志保留同一通道。
     只提交显式给出的字段，避免把另一项也一起回写。 */
  function accessSettingsPayload(payload) {
    var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
    var settings = data.settings || {};
    return {
      ok: true,
      enabled: settings.accessLogEnabled !== false,
      retentionDays: settings.accessRetentionDays == null ? null : Number(settings.accessRetentionDays)
    };
  }

  function handlerAccessSettingsUpdate(opts) {
    var body = (opts && opts.body) || {};
    var request = {};
    if (body.accessLogEnabled !== undefined) request.accessLogEnabled = body.accessLogEnabled === true;
    if (body.accessRetentionDays !== undefined) request.accessRetentionDays = Number(body.accessRetentionDays);
    if (!Object.keys(request).length) return handlerUnsupported('没有需要保存的访问记录设置');
    return apiPut('/api/admin/settings', request).then(accessSettingsPayload);
  }

  /* ---- IP 封禁（BAN）---- */
  function banRow(row) {
    return {
      ip: String(row.ip || ''),
      active: row.active !== false,
      permanent: row.permanent === true,
      expiresTs: row.expires_ts == null ? null : Number(row.expires_ts),
      remainingSeconds: row.remaining_seconds == null ? null : Number(row.remaining_seconds),
      reason: String(row.reason || ''),
      actor: String(row.actor || ''),
      createdTs: Number(row.created_ts || 0),
      updatedTs: Number(row.updated_ts || 0),
      revokedTs: row.revoked_ts == null ? null : Number(row.revoked_ts)
    };
  }

  function handlerBanList(opts) {
    var query = (opts && opts.query) || {};
    return apiGet('/api/admin/ip-bans', query).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      var page = data.page || {};
      return {
        items: (Array.isArray(data.items) ? data.items : []).map(banRow),
        hasMore: page.has_more === true || page.hasMore === true,
        offset: Number(page.offset) || 0,
        counters: data.counters || {},
        presets: Array.isArray(data.presets) ? data.presets : [],
        maxSeconds: Number(data.max_seconds || 0),
        snapshotTtlSeconds: Number(data.snapshot_ttl_seconds || 0)
      };
    });
  }

  function handlerBanCreate(opts) {
    var body = (opts && opts.body) || {};
    var request = { ip: String(body.ip || '').trim() };
    if (body.preset) request.preset = String(body.preset);
    if (body.seconds !== undefined && body.seconds !== null && body.seconds !== '') request.seconds = Number(body.seconds);
    if (body.reason) request.reason = String(body.reason).slice(0, 200);
    if (body.confirmSelfBan === true) request.confirmSelfBan = true;
    return apiPost('/api/admin/ip-bans', request).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return {
        ok: data.ok !== false,
        ban: data.ban ? banRow(data.ban) : null,
        closedConnections: Number(data.closed_connections || 0),
        selfBan: data.self_ban === true,
        counters: data.counters || {}
      };
    });
  }

  function handlerBanLift(opts) {
    var body = (opts && opts.body) || {};
    return apiDelete('/api/admin/ip-bans/' + encodeURIComponent(String(body.ip || '').trim())).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return { ok: data.ok !== false, ban: data.ban ? banRow(data.ban) : null, counters: data.counters || {} };
    });
  }

  function handlerBanEvents(opts) {
    var query = (opts && opts.query) || {};
    var ip = String(query.ip || '').trim();
    if (!ip) return handlerUnsupported('缺少要查询的 IP 地址');
    var request = {};
    if (query.limit) request.limit = Number(query.limit);
    if (query.offset) request.offset = Number(query.offset);
    return apiGet('/api/admin/ip-bans/' + encodeURIComponent(ip) + '/events', request).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return {
        ip: ip,
        items: (Array.isArray(data.items) ? data.items : []).map(function (item) {
          return {
            id: Number(item.id || 0),
            ts: Number(item.ts || 0),
            action: String(item.action || ''),
            actor: String(item.actor || ''),
            detail: item.detail && typeof item.detail === 'object' ? item.detail : {}
          };
        }),
        hasMore: data.has_more === true || data.hasMore === true
      };
    });
  }

  /* ---- 地域限制（GEO）---- */
  function geoStatusPayload(payload) {
    var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
    var database = data.database || {};
    var policy = data.policy || {};
    return {
      ok: true,
      running: data.running === true,
      jobState: String(data.job_state || 'idle'),
      downloadSettings: data.download_settings || { editions: ['GeoLite2-City'], proxy_mode: 'system' },
      events: Array.isArray(data.events) ? data.events : [],
      credentialSource: String(data.credential_source || "none"),
      credentialsManaged: data.credentials_managed === true,
      credentialsSaved: data.credentials_saved === true,
      credentialsError: String(data.credentials_error || ""),
      credentialVerification: String(data.credential_verification || 'unverified'),
      credentialCheckedTs: Number(data.credential_checked_ts || 0),
      credentialVerifiedTs: Number(data.credential_verified_ts || 0),
      accountIdPresent: data.account_id_present === true,
      enabled: data.enabled !== false,
      licenseKeyPresent: data.license_key_present === true,
      downloadBase: String(data.download_base || ''),
      edition: String(data.edition || ''),
      intervalHours: Number(data.interval_hours || 0),
      jitterSeconds: Number(data.jitter_seconds || 0),
      minManualIntervalSeconds: Number(data.min_manual_interval_seconds || 0),
      retryAfterSeconds: Number(data.retry_after_seconds || 0),
      retryIntervalSeconds: Number(data.retry_interval_seconds || 30),
      maxDownloadsPerDay: Number(data.max_downloads_per_day || 0),
      downloadsToday: Number(data.downloads_today || 0),
      attemptsToday: Number(data.attempts_today || 0),
      canUpdateNow: data.can_update_now === true,
      blockedReason: String(data.blocked_reason || ''),
      lastSuccessTs: Number(data.last_success_ts || 0),
      lastError: String(data.last_error || ''),
      lastHttpError: data.last_http_error || {},
      lastErrorTs: Number(data.last_error_ts || 0),
      nextDueTs: Number(data.next_due_ts || 0),
      removedOldDatabases: Number(data.removed_old_databases || 0),
      oldDatabaseMaxAgeDays: Number(data.old_database_max_age_days || 0),
      databaseAvailable: database.available === true,
      databaseFile: String(database.file || ''),
      databaseType: String(database.type || ''),
      cityAvailable: database.city_available === true,
      progress: (function (raw) {
        var source = raw && typeof raw === 'object' ? raw : {};
        return {
          stage: String(source.stage || 'idle'),
          edition: String(source.edition || ''),
          index: Number(source.index || 0),
          count: Number(source.count || 0),
          percent: Math.max(0, Math.min(100, Number(source.percent || 0))),
          downloadedBytes: Number(source.downloaded_bytes || 0),
          totalBytes: Number(source.total_bytes || 0)
        };
      })(data.progress),
      databaseEpoch: Number(database.epoch || 0),
      databaseSizeBytes: Number(database.size_bytes || 0),
      databases: Array.isArray(data.databases) ? data.databases : [],
      uploadMaxBytes: Number(data.upload_max_bytes || 268435456),
      uploadArchiveMaxBytes: Number(data.upload_archive_max_bytes || 134217728),
      databaseError: String(database.error || ''),
      mode: String(policy.mode || 'off'),
      forcedOff: policy.forced_off === true,
      allowedCountries: Array.isArray(policy.allowed_countries) ? policy.allowed_countries : [],
      unknownAction: String(policy.unknown_action || 'deny'),
      allowCidrs: Array.isArray(policy.allow_cidrs) ? policy.allow_cidrs : [],
      maxAllowedCountries: Number(data.max_allowed_countries || 0),
      maxAllowCidrs: Number(data.max_allow_cidrs || 0)
    };
  }

  function geoSimulation(payload) {
    var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
    var sim = data.simulation && typeof data.simulation === 'object' ? data.simulation : data;
    return {
      ok: true,
      ip: String(sim.ip || ''),
      ipClass: String(sim.ip_class || ''),
      country: String(sim.country || ''),
      would: String(sim.would || ''),
      reason: String(sim.reason || ''),
      mode: String(sim.mode || ''),
      self: sim.self === true,
      databaseAvailable: sim.database_available === true,
      databaseError: String(sim.database_error || '')
    };
  }

  function handlerGeoStatus() {
    return apiGet('/api/admin/geo/status', {}).then(geoStatusPayload);
  }

  function handlerGeoCheck() {
    return apiPost('/api/admin/geo/check', {}).then(function (payload) {
      var data = (payload && payload.data && typeof payload.data === 'object') ? payload.data : (payload || {});
      return { ok: data.ok !== false, status: geoStatusPayload(data.status || {}) };
    });
  }

  function handlerGeoSimulate(opts) {
    var query = (opts && opts.query) || {};
    var request = {};
    ['ip', 'mode', 'countries', 'unknown_action', 'allow_cidrs'].forEach(function (key) {
      if (query[key] !== undefined && query[key] !== null) request[key] = query[key];
    });
    return apiGet('/api/admin/geo/simulate', request).then(geoSimulation);
  }

  function handlerGeoPreview(opts) {
    var body = (opts && opts.body) || {};
    return apiPost('/api/admin/geo/preview', body).then(geoSimulation);
  }

  /* 地域设置走 /api/admin/settings；被自锁预检拦下（409）时把状态回传给界面，
     由界面显示警告并要求显式确认后再提交（绝不能默默拦住不说原因）。 */
  function handlerGeoSettingsUpdate(opts) {
    var body = (opts && opts.body) || {};
    var request = {};
    if (body.geoMode !== undefined) request.geoMode = String(body.geoMode);
    if (body.geoAllowedCountries !== undefined) request.geoAllowedCountries = String(body.geoAllowedCountries);
    if (body.geoUnknownAction !== undefined) request.geoUnknownAction = String(body.geoUnknownAction);
    if (body.geoAllowCidrs !== undefined) request.geoAllowCidrs = String(body.geoAllowCidrs);
    if (body.geoConfirmSelfLock === true) request.geoConfirmSelfLock = true;
    if (!Object.keys(request).length) return handlerUnsupported('没有需要保存的地域设置');
    return apiPut('/api/admin/settings', request).then(function () {
      // 保存本身已经成功。这里再取一次状态只为刷新界面：如果管理员刚把自己锁在外面，
      // 这次刷新必然 403——不能因此把「保存成功」报成失败（那会让人以为设置没生效而反复重试）。
      return handlerGeoStatus().then(function (status) {
        return { ok: true, status: status };
      }).catch(function () {
        return { ok: true, status: null, refreshFailed: true };
      });
    }).catch(function (error) {
      var detail = error && error.detail ? error.detail : {};
      var status = Number(detail.status || 0);
      var headers = detail.headers || {};
      var selfLock = String(headers['x-geo-self-lockout'] || headers['X-Geo-Self-Lockout'] || '') === '1';
      if (selfLock || (status === 409 && /自锁|self.lock|lockout|自己的来源/i.test((error && error.message) || ""))) {
        return { ok: false, selfLockBlocked: true, message: (error && error.message) || '' };
      }
      throw error;
    });
  }

  function handlerAuthLogout() {
    return apiPost('/api/auth/logout', {}).then(function (payload) {
      return payload || { ok: true };
    });
  }

  function handlerAccountPassword(opts) {
    var body = (opts && opts.body) || {};
    return apiPut('/api/account/password', {
      current_password: body.current,
      new_password: body.password || body.new_password,
      confirm_password: body.password || body.new_password
    });
  }

  function unsupportedError(detail) {
    var err = new Error(detail || '该功能暂未支持');
    err.code = 'HTTP_ERROR';
    err.name = 'ScrcpyGateApiError';
    err.detail = { status: 501, payload: { detail: detail || '该功能暂未支持' } };
    return err;
  }

  function handlerUnsupported(detail) {
    return Promise.reject(unsupportedError(detail));
  }

  var HANDLERS = {
    'auth.login': handlerAuthLogin,
    'auth.challenge': handlerAuthChallenge,
    'auth.logout': handlerAuthLogout,
    'session.current': handlerSessionCurrent,
    'account.password': handlerAccountPassword,
    'login.guard': handlerLoginGuard,
    'login.guard.update': handlerLoginGuardUpdate,
    'login.guard.unlock': handlerLoginGuardUnlock,
    'account.policy': handlerAccountPolicy,
    'account.policy.update': handlerAccountPolicyUpdate,
    'logs.retention': handlerLogRetention,
    'logs.retention.update': handlerLogRetentionUpdate,
    'system.update': handlerSystemUpdate,
    'access.summary': handlerAccessSummary,
    'access.records': handlerAccessRecords,
    'access.hints': handlerAccessHints,
    'access.status': handlerAccessStatus,
    'access.export': handlerAccessExport,
    'access.settings.update': handlerAccessSettingsUpdate,
    'ban.list': handlerBanList,
    'ban.create': handlerBanCreate,
    'ban.lift': handlerBanLift,
    'ban.events': handlerBanEvents,
    'geo.credentials.save': function (opts) { return apiPut('/api/admin/geo/credentials', (opts && opts.body) || {}); },
    'geo.credentials.clear': function () { return apiDelete('/api/admin/geo/credentials'); },
    'geo.schedule.save': function (opts) { return apiPut('/api/admin/geo/schedule', (opts && opts.body) || {}); },
    'geo.downloads.save': function (opts) { return apiPut('/api/admin/geo/downloads', (opts && opts.body) || {}); },
    'geo.status': handlerGeoStatus,
    'geo.check': handlerGeoCheck,
    'geo.upload': function (opts) {
      // File bodies bypass mirroring diagnostics; keep shared auth/CSRF/error handling.
      return Api.request('/api/admin/geo/upload', { method: 'POST', body: opts.file,
        query: { edition: opts.edition, format: opts.format }, timeout: 660000 });
    },
    'geo.simulate': handlerGeoSimulate,
    'geo.preview': handlerGeoPreview,
    'geo.settings.update': handlerGeoSettingsUpdate,
    'users.permissions': handlerUsersPermissions,
    'dashboard.overview': handlerDashboardOverview,
    'workbench.snapshot': handlerWorkbenchSnapshot,
    'devices.list': handlerDevicesList,
    'sessions.create': handlerSessionsCreate,
    'sessions.viewer.start': handlerViewerStart,
    'sessions.viewer.stop': handlerViewerStop,
    'sessions.stop': handlerSessionsStop,
    'sessions.action': handlerSessionsAction,
    'sessions.control.acquire': handlerControlAcquire,
    'sessions.control.takeover': handlerControlTakeover,
    'sessions.control.release': handlerControlRelease,
    'sessions.control.transfer': function (opts) {
      var params = (opts && opts.params) || {};
      var body = (opts && opts.body) || {};
      var deviceId = body.deviceId || params.deviceId;
      var targetClientId = body.targetClientId || body.target_client_id;
      if (!deviceId || !targetClientId) return handlerUnsupported('缺少控制权转交目标');
      return apiPost('/api/admin/devices/' + encodeURIComponent(deviceId) + '/control/transfer', {
        target_client_id: targetClientId
      }).then(function (payload) { return requireSuccessful(payload, '控制权转交失败'); });
    },
    'quality.config': handlerQualityConfig,
    'quality.update': handlerQualityUpdate,
    'mirror.fullscreen': handlerMirrorFullscreen,
    'quality.admin.config': handlerAdminQualityConfig,
    'quality.admin.update': handlerAdminQualityUpdate,
    'quality.status': handlerQualityStatus,
    'quality.clients': handlerQualityClients,
    'quality.reset': handlerAdminQualityReset,
    'quality.restart': handlerAdminQualityRestart,
    'quality.presets.create': function (opts) { return handlerQualityPresets('create', opts); },
    'quality.presets.update': function (opts) { return handlerQualityPresets('update', opts); },
    'quality.presets.delete': function (opts) { return handlerQualityPresets('delete', opts); },
    // 设备 ↔ Runtime 配置配对（只读）：用户页「选设备自动带出配置 / 选配置自动带出设备」用。
    'alas.configMatches': function () { return apiGet('/api/admin/alas/config-matches'); },
    'alas.configs': handlerAlasConfigs,
    'alas.status': handlerAlasStatus,
    'alas.toggle': handlerAlasToggle,
    'devices.check': handlerDevicesCheck,
    'devices.create': handlerDevicesCreate,
    'devices.update': handlerDevicesUpdate,
    'devices.delete': handlerDevicesDelete,
    'users.list': handlerUsersList,
    'users.create': handlerUsersCreate,
    'users.update': handlerUsersUpdate,
    'users.delete': handlerUsersDelete,
    'users.reset-password': handlerUsersResetPassword,
    'devices.permissions.update': handlerDevicePermissionUpdate,
    'devices.alas.bind': handlerDeviceAlasBinding,
    'permissions.catalog': function () { return apiGet('/api/admin/permissions'); },
    'alas.permissions.catalog': function () { return apiGet('/api/admin/alas/permissions'); },
    'alas.catalog': function () { return apiGet('/api/admin/alas/configs'); },
    'logs.list': handlerLogsList,
    'logs.audit': handlerLogsAudit,
    'logs.audit.detail': handlerLogsAuditDetail,
    'alerts.list': handlerAdminAlerts,
    'alerts.resolve': handlerAdminAlertResolve,
    'logs.export': handlerLogsExport,
    'logs.export.full': function () {
      return Api.request('/api/admin/logs/export-full', { method: 'POST', body: {}, timeout: 180000 })
        .then(function (payload) {
          if (!payload || payload.encoding !== 'base64' || typeof payload.content !== 'string') throw new Error('Invalid log archive response');
          var parts = [];
          for (var offset = 0; offset < payload.content.length; offset += 65536) {
            var binary = window.atob(payload.content.slice(offset, offset + 65536));
            var bytes = new Uint8Array(binary.length);
            for (var index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
            parts.push(bytes);
          }
          return { blob: new Blob(parts, { type: 'application/octet-stream' }), filename: 'scrcpygate-logs-full.zip' };
        });
    },
    'logs.integrity': handlerLogsIntegrity,
    'alas.overview': handlerAlasOverview,
    'alas.settings.update': handlerAlasSettingsUpdate,
    'alas.config.action': handlerAlasConfigAction,
    'alas.relations.update': handlerAlasRelationUpdate,
    'alas.relations.create': handlerAlasRelationCreate,
    'alas.relations.delete': handlerAlasRelationDelete,
    'alas.relations.bulkUpdate': handlerAlasRelationBulk,
    'alas.connection.check': handlerAlasConnectionCheck,
    'alas.config.status': handlerAlasConfigStatus,
    // 打开 ALAS 管理页时扫一次：按 ALAS 配置里的模拟器 ADB 地址补齐缺失的管理员关联。
    'alas.autoBind': function () {
      return apiPost('/api/admin/alas/auto-bind', {}).then(function (payload) { return payload || { ok: true }; });
    },
    'notifications.list': handlerNotificationsList,
    'notifications.markRead': handlerNotificationsMarkRead,
    'permissions.request': function () { return handlerUnsupported('该功能暂未支持：请由管理员在后台直接调整权限'); },
    'devices.permission.request': function () { return handlerUnsupported('该功能暂未支持：请由管理员在后台直接调整设备权限'); },
    'account.renewal.request': function () { return handlerUnsupported('该功能暂未支持：请由管理员在后台延长账户有效期'); },
    'users.renewal.request': function () { return handlerUnsupported('该功能暂未支持：请由管理员在后台延长账户有效期'); }
  };

  /* ---------------- 拦截与安装 ---------------- */

  var originalConfigured = Api.configured ? Api.configured.bind(Api) : function (name, opts) { return Api.call(name, opts); };
  var originalIsConfigured = Api.isConfigured ? Api.isConfigured.bind(Api) : function (name) { return false; };

  function dispatch(name, opts) {
    var handler = HANDLERS[name];
    if (handler) {
      // Invoke local handlers in the originating event stack. Mobile and
      // desktop browsers may reject focus() for the hidden keyboard proxy
      // once user activation has crossed a Promise microtask boundary.
      try {
        return Promise.resolve(handler(opts || {}));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return originalConfigured(name, opts);
  }

  Api.configured = function (name, opts) { return dispatch(name, opts); };
  Api.get = function (name, opts) { return dispatch(name, Object.assign({}, opts, { method: 'GET' })); };
  Api.post = function (name, opts) { return dispatch(name, Object.assign({}, opts, { method: 'POST' })); };
  Api.put = function (name, opts) { return dispatch(name, Object.assign({}, opts, { method: 'PUT' })); };
  Api.patch = function (name, opts) { return dispatch(name, Object.assign({}, opts, { method: 'PATCH' })); };
  Api.del = function (name, opts) { return dispatch(name, Object.assign({}, opts, { method: 'DELETE' })); };
  Api.isConfigured = function (name) { return !!HANDLERS[name] || originalIsConfigured(name); };

  /* ---------------- 全局退出登录委托 ---------------- */

  document.addEventListener('click', function (event) {
    // 退出登录只认账户菜单里的危险项：这里曾经是全局的 `.menu-item.danger`，
    // 于是任何页面只要用这个类做「删除/停用」这类危险动作，点一下就把自己登出了。
    // 账户菜单都是 `.menu-panel`（8 个后台页），/mirror 用 `.logout`/`data-logout`。
    var target = event.target && event.target.closest ? event.target.closest('.menu-item.logout, .menu-panel .menu-item.danger, [data-logout], #up-logout') : null;
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    handlerAuthLogout({}).then(function () {
      try { window.location.href = '/login'; } catch (e) {}
    }).catch(function () {
      try { document.dispatchEvent(new CustomEvent('scrcpygate:logout-failed')); } catch (e) {}
    });
  }, true);

  /* ---------------- 公开工具(供调试/测试) ---------------- */

  window.ScrcpyGateV2 = {
    state: state,
    fmtTs: fmtTs,
    timeRangeBounds: timeRangeBounds,
    withinTimeBounds: withinTimeBounds,
    normalizeLogLevel: normalizeLogLevel,
    auditActionKey: auditActionKey,
    reverseAuditActionLabel: reverseAuditActionLabel,
    auditIsAlertRecord: auditIsAlertRecord,
    auditActionLabel: auditActionLabel,
    auditCategoryLabel: auditCategoryLabel,
    auditReasonLabel: auditReasonLabel,
    auditReadableText: auditReadableText,
    auditRecordTitle: auditRecordTitle,
    runtimeEventLabel: runtimeEventLabel,
    adaptAuditList: adaptAuditList,
    adaptRuntimeLogs: adaptRuntimeLogs,
    rawV2SequenceIsNewer: rawV2SequenceIsNewer,
    // 管理员排查用：投屏实时记录（只存在本机浏览器内存里）。
    videoRecord: {
      entries: videoRecordEntries,
      subscribe: videoRecordSubscribe,
      clear: videoRecordClear,
      stats: videoRecordStats,
      text: videoRecordText,
      json: videoRecordJson,
      line: videoRecordLine,
      // 页面可以补一条自定义事件（例如「开始仅本端记录」这个纯前端的会话边界）。
      push: videoRecord,
      limit: VIDEO_RECORD_LIMIT
    },
    // 原始数据层（控制面原文 / 包头元数据 / 接口调用 / 状态 / 环境快照）。
    videoRecordRaw: {
      entries: videoRecordRaw,
      stats: videoRecordRawStats,
      text: videoRecordRawText,
      json: videoRecordRawJson,
      subscribe: videoRecordRawSubscribe,
      clear: videoRecordRawClear,
      push: rawRecord,
      environment: rawRecordEnvironment,
      setEnabled: rawSetEnabled,
      isEnabled: rawEnabled,
      entryLimit: RAW_ENTRY_LIMIT,
      byteLimit: RAW_BYTE_LIMIT
    },
    // 多端投屏记录：发起/响应/停止 + 本端与参与端的完整记录汇总。
    mirrorRecord: {
      start: startMirrorRecord,
      stop: stopMirrorRecord,
      respond: respondMirrorRecord,
      upload: uploadMirrorRecord,
      attach: attachMirrorRecord,
      state: function () {
        var session = state.recordSession;
        var clientId = currentRecordClientId();
        var mine = recordParticipantOf(session, clientId);
        var initiatorClient = String((session && session.initiator_client_id) || '');
        // 主端判定以服务端给的 initiator_client_id 为准；老服务端不带该字段时退回本机
        // 角色（只有本端自己发起或凭 claim 拿回主导权时才是 initiator，被邀请的端不会）。
        var isInitiator = initiatorClient
          ? initiatorClient === clientId
          : state.recordRole === 'initiator';
        return {
          device_id: mirrorRecordDeviceId(),
          client_id: clientId,
          // 稳定设备标识：面板/导出显示它，同一台设备进出记录时不会变。
          browser_id: browserDeviceId(),
          armed: !!state.recordArmed,
          invite: state.recordInvite,
          invite_client_id: state.recordInviteClientId,
          session: session,
          role: state.recordRole || (isInitiator ? 'initiator' : (session ? 'participant' : '')),
          // 主端 / 副端：只有发起这次会话的那个观看端能停止；同账号的其它设备是副端。
          is_initiator: isInitiator,
          me: mine,
          bundles: (state.recordBundles || []).slice()
        };
      },
      bundleJson: recordBundleJson,
      bundleText: recordBundleText,
      clearBundles: clearRecordBundles
    },
    // 本浏览器的稳定设备标识（同一台设备进出记录/登录会话都用它）。
    browserDeviceId: browserDeviceId,
    deviceIdentity: { browserId: browserDeviceId },
    setVideoRotation: setVideoRotation,
    setFullscreenMode: setFullscreenMode,
    refreshVideoLayout: updateVideoRotationLayout,
    getVideoSourceDimensions: videoSourceDimensions,
    getWorkbenchAlasVisible: function () { return state.workbenchAlasVisible !== false; },
    getWorkbenchFeatures: function () { return state.workbenchFeatures || null; },
    getWorkbenchLayout: function () { return state.workbenchLayout || null; },
    videoReconnect: { retryDelays: VIDEO_RETRY_DELAYS.slice(), stableMs: VIDEO_STABLE_MS }
  };
})(window, document);
