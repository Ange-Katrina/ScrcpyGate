/* Pure dashboard state aggregation.
 *
 * This module has no API, DOM, or adapter dependency. It turns per-config
 * ALAS status rows and user bindings into presentation state while keeping
 * disabled/expired users neutral instead of reporting a false error.
 */
(function (global) {
  'use strict';

  function languageIsEnglish() {
    return !!(global.ScrcpyGateI18n && typeof global.ScrcpyGateI18n.getLang === 'function' &&
      global.ScrcpyGateI18n.getLang() === 'en-US');
  }

  function labels(zh, en) { return languageIsEnglish() ? en : zh; }

  function refs(item) {
    var values = item ? [
      item.public_id, item.publicId, item.public_device_id, item.publicDeviceId,
      item.device_id, item.deviceId, item.id, item.internal_id, item.internalId
    ] : [];
    var result = [];
    values.forEach(function (value) {
      var ref = String(value || '').trim();
      if (ref && result.indexOf(ref) < 0) result.push(ref);
    });
    return result;
  }

  function falseValue(value) {
    return value === false || value === 0 || value === '0' || String(value || '').toLowerCase() === 'false';
  }

  function normalizeStatus(value) {
    var raw = String(value == null ? '' : value).trim().toLowerCase();
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
    return raw || 'unknown';
  }

  function entry(item, binding, user) {
    var userState = String(user && (user.expiration_state || user.status || '') || '').toLowerCase();
    var userInactive = !!user && (
      falseValue(user.enabled) || falseValue(user.is_active) || falseValue(user.isActive) ||
      userState === 'disabled' || userState === 'expired' || userState === 'inactive'
    );
    if (userInactive) {
      var inactiveCode = userState === 'expired' ? 'expired' : 'disabled';
      return {
        code: inactiveCode,
        label: labels(inactiveCode === 'expired' ? '已到期' : '已停用', inactiveCode === 'expired' ? 'Expired' : 'Disabled'),
        tone: 'none',
        error: false
      };
    }

    var raw = normalizeStatus(item && (item.status || item.state || item.status_label || item.statusLabel));
    var errorStates = ['error', 'partial_error', 'partial', 'disconnected', 'unreachable', 'timeout',
      'token_missing', 'token_invalid', 'http_error', 'invalid_config'];
    var neutralStates = ['stopped', 'disabled', 'unconfigured', 'unknown', 'expired'];
    if (errorStates.indexOf(raw) >= 0 || (item && item.ok === false && neutralStates.indexOf(raw) < 0)) {
      var unreachable = raw === 'disconnected' || raw === 'unreachable' || raw === 'timeout';
      return {
        code: unreachable ? 'unreachable' : 'error',
        label: labels(unreachable ? '不可达' : '异常', unreachable ? 'Unreachable' : 'Error'),
        tone: 'error',
        error: true
      };
    }
    if (raw === 'running') return { code: raw, label: labels('运行中', 'Running'), tone: 'ok', error: false };
    if (raw === 'connected') return { code: raw, label: labels('已连接', 'Connected'), tone: 'ok', error: false };
    if (raw === 'stopped') return { code: raw, label: labels('已停止', 'Stopped'), tone: 'none', error: false };
    if (raw === 'disabled') return { code: raw, label: labels('已停用', 'Disabled'), tone: 'none', error: false };
    if (raw === 'expired') return { code: raw, label: labels('已到期', 'Expired'), tone: 'none', error: false };
    if (raw === 'unconfigured') return { code: raw, label: labels('未配置', 'Not configured'), tone: 'none', error: false };
    return { code: 'unknown', label: labels('未检查', 'Not checked'), tone: 'none', error: false };
  }

  function aggregate(entries, fallback) {
    var list = (entries || []).filter(function (item) { return !!item; });
    if (!list.length) {
      var fallbackCode = normalizeStatus(fallback || 'unknown');
      if (fallbackCode === 'unbound') {
        return { code: 'unbound', label: labels('未关联', 'Unlinked'), tone: 'none', error: false };
      }
      return entry({ status: fallbackCode }, null, null);
    }
    var errors = list.filter(function (item) { return item.error === true || item.tone === 'error'; });
    if (errors.length === list.length) {
      var allUnreachable = errors.every(function (item) { return item.code === 'unreachable'; });
      return {
        code: allUnreachable ? 'unreachable' : 'error',
        label: labels(allUnreachable ? '不可达' : '异常', allUnreachable ? 'Unreachable' : 'Error'),
        tone: 'error',
        error: true
      };
    }
    if (errors.length) return { code: 'partial_error', label: labels('部分异常', 'Partially degraded'), tone: 'error', error: true };
    if (list.some(function (item) { return item.tone === 'ok'; })) {
      return { code: 'running', label: labels('运行中', 'Running'), tone: 'ok', error: false };
    }
    if (list.every(function (item) { return item.code === 'expired'; })) {
      return { code: 'expired', label: labels('已到期', 'Expired'), tone: 'none', error: false };
    }
    if (list.every(function (item) { return item.code === 'disabled'; })) {
      return { code: 'disabled', label: labels('已停用', 'Disabled'), tone: 'none', error: false };
    }
    if (list.every(function (item) { return item.code === 'unconfigured'; })) {
      return { code: 'unconfigured', label: labels('未配置', 'Not configured'), tone: 'none', error: false };
    }
    if (list.some(function (item) { return item.code === 'unknown'; })) {
      return { code: 'unknown', label: labels('未检查', 'Not checked'), tone: 'none', error: false };
    }
    return { code: 'stopped', label: labels('已停止', 'Stopped'), tone: 'none', error: false };
  }

  global.ScrcpyGateDashboardState = Object.freeze({
    refs: refs,
    falseValue: falseValue,
    normalizeStatus: normalizeStatus,
    entry: entry,
    aggregate: aggregate
  });
}(window));
