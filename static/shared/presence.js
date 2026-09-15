/* 页面在线标记（工作台「退出浏览器后自动检测 ALAS」的判定依据）。
 *
 * 每个已登录页面保持一条 /ws/events 连接：浏览器关闭或刷新时连接断开，
 * 服务端在宽限期（默认 60 秒）后执行一次性检测；宽限期内重新连上（刷新、
 * 页面跳转）会取消本次检测。会话失效（4401/4403）时不再重连。
 */
(function (global) {
  'use strict';
  if (!global || typeof global.WebSocket !== 'function') return;
  if (global.ScrcpyGatePresence) return;

  var PING_INTERVAL_MS = 45000;
  var RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000];
  var socket = null;
  var retryIndex = 0;
  var pingTimer = null;
  var reconnectTimer = null;
  var stopped = false;

  function wsUrl() {
    var location = global.location || {};
    return (location.protocol === 'https:' ? 'wss://' : 'ws://') + (location.host || '') + '/ws/events';
  }

  function clearPing() {
    if (pingTimer) {
      global.clearTimeout(pingTimer);
      pingTimer = null;
    }
  }

  function schedulePing() {
    clearPing();
    pingTimer = global.setTimeout(function () {
      pingTimer = null;
      if (socket && socket.readyState === 1) {
        try { socket.send('ping'); } catch (error) {}
      }
      schedulePing();
    }, PING_INTERVAL_MS);
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer) return;
    var delay = RECONNECT_DELAYS[Math.min(retryIndex, RECONNECT_DELAYS.length - 1)];
    retryIndex += 1;
    reconnectTimer = global.setTimeout(function () {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (stopped || socket) return;
    var next;
    try {
      next = new global.WebSocket(wsUrl());
    } catch (error) {
      scheduleReconnect();
      return;
    }
    socket = next;
    next.onopen = function () {
      if (socket !== next) return;
      retryIndex = 0;
      schedulePing();
    };
    next.onmessage = function () {};
    next.onerror = function () {
      if (socket !== next) return;
      try { next.close(); } catch (error) {}
    };
    next.onclose = function (event) {
      if (socket !== next) return;
      socket = null;
      clearPing();
      var code = Number(event && event.code);
      if (code === 4401 || code === 4403) {
        stopped = true;
        return;
      }
      scheduleReconnect();
    };
  }

  function shutdown() {
    stopped = true;
    clearPing();
    if (reconnectTimer) {
      global.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      var current = socket;
      socket = null;
      try { current.close(); } catch (error) {}
    }
  }

  global.addEventListener('pagehide', shutdown);
  global.addEventListener('beforeunload', shutdown);
  connect();

  global.ScrcpyGatePresence = { connect: connect, close: shutdown };
})(typeof window !== 'undefined' ? window : this);
