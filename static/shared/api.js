(function (window, document) {
  'use strict';

  /*
   * The UI never invents API paths. A server-rendered bootstrap may provide
   * window.ScrcpyGateConfig = { endpoints: { 'resource.action': '/path' } }.
   * Until that contract exists, pages stay in an explicit unavailable state.
   */
  var DEFAULT_TIMEOUT = 15000;
  var authRedirecting = false;
  var authInvalidEventSent = false;
  var inflightGets = new Map();
  var recentGets = new Map();
  var readCacheEpoch = 0;
  var backgroundJobs = new Map();
  var BACKGROUND_DELAYS = [30000, 60000, 120000, 300000];

  function getCacheTtl(url) {
    var path = String(url || '').split('?', 1)[0];
    if (/\/api\/(?:workbench\/snapshot|devices)$/.test(path)) return 60000;
    if (/\/api\/(?:alas\/status|admin\/dashboard\/snapshot|admin\/overview\/alas)$/.test(path)) return 120000;
    return 0;
  }

  function clonePayload(payload) {
    if (payload === undefined || payload === null) return payload;
    try { return JSON.parse(JSON.stringify(payload)); } catch (error) { return payload; }
  }

  function config() {
    return window.ScrcpyGateConfig || {};
  }

  function routes() {
    var value = config().endpoints;
    return value && typeof value === 'object' ? value : {};
  }

  function createError(code, message, detail) {
    var error = new Error(message || code);
    error.name = 'ScrcpyGateApiError';
    error.code = code;
    error.detail = detail || null;
    return error;
  }

  function scheduleAuthenticationRedirect() {
    if (window.location.pathname === '/login') return;
    if (!authRedirecting) {
      authRedirecting = true;
      window.setTimeout(function () { window.location.replace('/login'); }, 0);
    }
  }

  function handleAuthenticationInvalid() {
    if (window.location.pathname === '/login') return;
    if (!authInvalidEventSent) {
      authInvalidEventSent = true;
      try { document.dispatchEvent(new CustomEvent('scrcpygate:auth-invalid')); } catch (error) {}
    }
    scheduleAuthenticationRedirect();
  }

  // A WebSocket can discover session expiry without an HTTP 401.  Consume the
  // same event as HTTP failures so cleanup and the one-shot redirect stay in
  // one lifecycle, regardless of which transport noticed the expiry first.
  if (document && typeof document.addEventListener === 'function') {
    document.addEventListener('scrcpygate:auth-invalid', function () {
      authInvalidEventSent = true;
      scheduleAuthenticationRedirect();
    });
  }

  function routeConfig(name) {
    var route = routes()[name];
    if (!route) {
      throw createError('API_NOT_CONFIGURED', '接口尚未配置：' + name);
    }
    if (typeof route === 'string') {
      return { path: route, method: 'GET' };
    }
    if (!route.path) {
      throw createError('API_ROUTE_INVALID', '接口配置无效：' + name);
    }
    return route;
  }

  function baseUrl() {
    var base = config().baseUrl || '';
    return String(base).replace(/\/$/, '');
  }

  function allowedApiOrigins() {
    var value = config().allowedApiOrigins || config().apiAllowedOrigins || [];
    if (typeof value === 'string') value = [value];
    return Array.isArray(value) ? value : [];
  }

  function allowedDownloadOrigins() {
    var out = [];
    ['allowedDownloadOrigins', 'allowedExportOrigins', 'downloadAllowedOrigins'].forEach(function (key) {
      var value = config()[key];
      if (typeof value === 'string') value = [value];
      if (Array.isArray(value)) out = out.concat(value);
    });
    return out;
  }

  function normalizeOrigin(origin) {
    try {
      return new URL(String(origin), window.location.origin).origin;
    } catch (error) {
      return '';
    }
  }

  function assertAllowedUrl(url) {
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      throw createError('API_URL_INVALID', '接口地址协议无效');
    }
    if (url.origin === window.location.origin) return;
    var allowed = allowedApiOrigins().map(normalizeOrigin).filter(Boolean);
    if (allowed.indexOf(url.origin) === -1) {
      throw createError('API_ORIGIN_NOT_ALLOWED', '接口来源未被允许：' + url.origin);
    }
  }

  function safeDownloadUrl(raw) {
    var value = String(raw == null ? '' : raw).trim();
    if (!value || value === '#') return '';
    try {
      var url = new URL(value, window.location.origin);
      if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
      if (url.origin === window.location.origin) return url.href;
      var allowed = allowedDownloadOrigins().map(normalizeOrigin).filter(Boolean);
      return allowed.indexOf(url.origin) >= 0 ? url.href : '';
    } catch (error) {
      return '';
    }
  }

  function buildUrl(path, query) {
    var raw = String(path);
    var joined = /^https?:\/\//i.test(raw) ? raw : baseUrl() + (raw.charAt(0) === '/' ? raw : '/' + raw);
    var url;
    try {
      url = new URL(joined, window.location.origin);
    } catch (error) {
      throw createError('API_URL_INVALID', '接口地址无效');
    }
    assertAllowedUrl(url);
    if (!query || typeof query !== 'object') return url.href;
    Object.keys(query).forEach(function (key) {
      var value = query[key];
      if (value === undefined || value === null || value === '') return;
      if (Array.isArray(value)) {
        value.forEach(function (item) { url.searchParams.append(key, item); });
      } else {
        url.searchParams.append(key, value);
      }
    });
    return url.href;
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    var match = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function retryAfterMs(response) {
    var value = response.headers.get('retry-after');
    if (!value) return 0;
    var seconds = Number(value);
    if (isFinite(seconds) && seconds >= 0) return Math.min(300000, Math.round(seconds * 1000));
    var date = Date.parse(value);
    return isFinite(date) ? Math.max(0, Math.min(300000, date - Date.now())) : 0;
  }

  function parseBody(response) {
    var type = response.headers.get('content-type') || '';
    return response.text().then(function (text) {
      var trimmed = String(text || '').trim();
      if (type.indexOf('application/json') !== -1) {
        if (!trimmed) return null;
        try { return JSON.parse(trimmed); } catch (error) {
          return { __invalidJson: true, __contentType: type, __text: trimmed.slice(0, 240) };
        }
      }
      return {
        __nonJson: true,
        __html: /<\s*!doctype\s+html|<\s*html(?:\s|>)/i.test(trimmed) || type.indexOf('text/html') !== -1,
        __contentType: type,
        __text: trimmed.slice(0, 240)
      };
    });
  }

  /* 浏览器稳定设备标识：由适配器生成（localStorage 里存一份随机 id），
     适配器还没加载时退回直接读 localStorage；都没有就不带这个头。 */
  var DEVICE_ID_KEY = 'scrcpygate-device-id';
  function deviceIdHeader() {
    try {
      if (window.ScrcpyGateV2 && typeof window.ScrcpyGateV2.browserDeviceId === 'function') {
        return String(window.ScrcpyGateV2.browserDeviceId() || '');
      }
      return String(window.localStorage.getItem(DEVICE_ID_KEY) || '');
    } catch (error) {
      return '';
    }
  }

  function request(path, options) {    options = options || {};
    var method = String(options.method || 'GET').toUpperCase();
    var headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body instanceof Blob) {
      headers.set('Content-Type', 'application/octet-stream');
    } else if (options.body !== undefined && options.body !== null && !(options.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
    }
    var token = csrfToken();
    if (token) headers.set('X-CSRF-Token', token);
    // 浏览器稳定设备标识：登录会话列表据此把同一台设备的多条会话归并成一行。
    var deviceId = deviceIdHeader();
    if (deviceId) headers.set('X-Device-Id', deviceId);
    var body = options.body;
    if (body !== undefined && body !== null && headers.get('Content-Type') === 'application/json' && typeof body !== 'string') {
      body = JSON.stringify(body);
    }
    var url = buildUrl(path, options.query);
    var coalesce = method === 'GET' && options.coalesce !== false && !options.signal;
    var requestKey = method + ' ' + url;
    if (coalesce && inflightGets.has(requestKey)) return inflightGets.get(requestKey);
    var cacheTtl = method === 'GET' && options.cache !== false ? getCacheTtl(url) : 0;
    var requestEpoch = readCacheEpoch;
    if (cacheTtl && !options.force && recentGets.has(requestKey)) {
      var cached = recentGets.get(requestKey);
      if (Date.now() - cached.at < cacheTtl) return Promise.resolve(clonePayload(cached.payload));
      recentGets.delete(requestKey);
    }
    var controller = window.AbortController ? new AbortController() : null;
    var timeout = options.timeout || DEFAULT_TIMEOUT;
    var timer = controller ? window.setTimeout(function () { controller.abort(); }, timeout) : null;
    var signal = options.signal || (controller && controller.signal);
    var fetchOptions = {
      method: method,
      headers: headers,
      body: body,
      credentials: options.credentials || 'same-origin',
      signal: signal
    };
    // 页面可以在 <head> 里预取读模型（static/shared/read-prefetch.js）：同 URL 的 GET
    // 直接复用它已经拿到的 Response，省掉一个 JS 加载周期。预取失败返回 null，此处重发。
    var prefetch = method === 'GET' && window.ScrcpyGatePrefetch;
    var warmResponse = prefetch ? prefetch.take(url) : null;
    var responsePromise = warmResponse
      ? warmResponse.then(function (response) { return response || window.fetch(url, fetchOptions); })
      : window.fetch(url, fetchOptions);
    var promise = responsePromise.then(function (response) {
      return parseBody(response).then(function (payload) {
        var retryMs = retryAfterMs(response);
        if (payload && payload.__invalidJson) {
          throw createError('INVALID_JSON', '数据服务返回了无效 JSON', { status: response.status, retryAfterMs: retryMs, payload: payload });
        }
        if (payload && payload.__nonJson) {
          // Classify gateway status before looking at the body.  WAFs often
          // return an HTML body for 429/444, which used to be mislabeled as a
          // generic challenge and made retry/backoff behavior incorrect.
          var edgeCode = response.status === 429 ? 'RATE_LIMITED' :
            (response.status === 444 ? 'WAF_BLOCKED' :
              (payload.__html ? 'EDGE_CHALLENGE' : 'NON_JSON_RESPONSE'));
          var edgeMessage = edgeCode === 'RATE_LIMITED' ? '请求触发网关频率限制' :
            (edgeCode === 'WAF_BLOCKED' ? '请求被雷池 WAF 拦截' :
              (edgeCode === 'EDGE_CHALLENGE' ? '请求被网关验证页拦截' : '数据服务返回了非 JSON 响应'));
          if (response.status === 401) handleAuthenticationInvalid();
          throw createError(edgeCode, edgeMessage, {
            status: response.status, retryAfterMs: retryMs, contentType: payload.__contentType, preview: payload.__text
          });
        }
        if (!response.ok) {
          if (response.status === 401) handleAuthenticationInvalid();
          var detailBody = payload && payload.detail;
          var message = payload && (payload.message || payload.error)
            || (detailBody && typeof detailBody === 'object' && typeof detailBody.message === 'string' && detailBody.message)
            || (typeof detailBody === 'string' && detailBody)
            || ('请求失败（' + response.status + '）');
          var responseCode = response.status === 429 ? 'RATE_LIMITED' : (response.status === 444 ? 'WAF_BLOCKED' : 'HTTP_ERROR');
          throw createError(responseCode, message, {
            status: response.status, retryAfterMs: retryMs, payload: payload,
            headers: {
              'x-geo-update-error': response.headers.get('x-geo-update-error') || '',
              'x-geo-self-lockout': response.headers.get('x-geo-self-lockout') || ''
            }
          });
        }
        if (cacheTtl && requestEpoch === readCacheEpoch) {
          recentGets.set(requestKey, { at: Date.now(), payload: clonePayload(payload) });
          if (recentGets.size > 64) recentGets.delete(recentGets.keys().next().value);
        }
        if (method !== 'GET') invalidateReadCache();
        return payload;
      });
    }).catch(function (error) {
      if (error && error.name === 'AbortError') throw createError('TIMEOUT', '请求超时');
      if (error && error.name === 'ScrcpyGateApiError') throw error;
      throw createError('NETWORK_ERROR', '无法连接数据服务', { cause: error });
    }).finally(function () {
      if (timer) window.clearTimeout(timer);
      if (coalesce && inflightGets.get(requestKey) === promise) inflightGets.delete(requestKey);
    });
    if (coalesce) inflightGets.set(requestKey, promise);
    return promise;
  }

  function backgroundGet(path, options) {
    options = options || {};
    var query = options.query || {};
    var key;
    try { key = buildUrl(path, query); } catch (error) { return Promise.reject(error); }
    var job = backgroundJobs.get(key) || { failures: 0, nextAt: 0, promise: null };
    if (job.promise) return job.promise;
    if (document.visibilityState && document.visibilityState !== 'visible') {
      return Promise.reject(createError('BACKGROUND_PAUSED', '页面处于后台，已暂停自动刷新'));
    }
    var now = Date.now();
    if (!options.force && job.nextAt > now) {
      return Promise.reject(createError('BACKGROUND_BACKOFF', '自动刷新正在退避中', { retryAfterMs: job.nextAt - now }));
    }
    var requestOptions = Object.assign({}, options, { method: 'GET', coalesce: true });
    delete requestOptions.force;
    var promise = request(path, requestOptions).then(function (payload) {
      job.failures = 0;
      job.nextAt = 0;
      return payload;
    }).catch(function (error) {
      job.failures = Math.min(BACKGROUND_DELAYS.length - 1, job.failures + 1);
      var retry = error && error.detail && error.detail.retryAfterMs;
      var base = retry || BACKGROUND_DELAYS[job.failures];
      job.nextAt = Date.now() + Math.round(base * (0.8 + Math.random() * 0.4));
      throw error;
    }).finally(function () {
      if (job.promise === promise) job.promise = null;
      backgroundJobs.set(key, job);
    });
    job.promise = promise;
    backgroundJobs.set(key, job);
    return promise;
  }

  function clearBackground(path, query) {
    var key;
    try { key = buildUrl(path, query || {}); } catch (error) { return; }
    backgroundJobs.delete(key);
  }

  function invalidateReadCache() {
    // A successful mutation can change devices, permissions, quality, ALAS,
    // alerts, or dashboard aggregates. Do not keep stale snapshots for their
    // normal TTL after the user has just saved a change.
    readCacheEpoch += 1;
    recentGets.clear();
  }

  function replaceParams(path, params) {
    return String(path).replace(/:([A-Za-z0-9_]+)/g, function (_, key) {
      return params && params[key] !== undefined ? encodeURIComponent(params[key]) : ':' + key;
    });
  }

  function call(name, options) {
    options = options || {};
    var route = routeConfig(name);
    var params = options.params || {};
    var path = replaceParams(route.path, params);
    var requestOptions = {};
    Object.keys(options).forEach(function (key) {
      if (key !== 'params') requestOptions[key] = options[key];
    });
    requestOptions.method = options.method || route.method || 'GET';
    return request(path, requestOptions);
  }

  function payloadList(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    if (payload && payload.data && Array.isArray(payload.data)) return payload.data;
    if (payload && payload.data && Array.isArray(payload.data.items)) return payload.data.items;
    return [];
  }

  function errorMessage(error) {
    if (!error) return '请求失败';
    if (error.code === 'API_NOT_CONFIGURED') return '数据接口尚未配置，请先连接后端服务';
    if (error.code === 'API_ORIGIN_NOT_ALLOWED') return '接口来源未被允许，请检查后端接口配置';
    if (error.code === 'API_URL_INVALID') return '接口地址无效，请检查后端接口配置';
    if (error.code === 'TIMEOUT') return '数据服务响应超时，请重试';
    if (error.code === 'NETWORK_ERROR') return '无法连接数据服务，请检查网络或服务状态';
    if (error.code === 'EDGE_CHALLENGE') return '请求被雷池网关拦截，请稍后重试或完成人机验证';
    if (error.code === 'WAF_BLOCKED') return '请求被雷池网关拒绝，请稍后重试';
    if (error.code === 'NON_JSON_RESPONSE') return '网关返回了非数据响应，请稍后重试';
    if (error.code === 'INVALID_JSON') return '数据服务响应格式异常，请稍后重试';
    if (error.code === 'BACKGROUND_PAUSED') return '页面处于后台，已暂停自动刷新';
    if (error.code === 'BACKGROUND_BACKOFF') {
      var wait = error.detail && error.detail.retryAfterMs;
      return wait ? '服务暂时退避，请在 ' + Math.max(1, Math.ceil(wait / 1000)) + ' 秒后重试' : '服务暂时退避，请稍后重试';
    }
    if (error.code === 'RATE_LIMITED') {
      var retry = error.detail && error.detail.retryAfterMs;
      return retry ? '请求过于频繁，请在 ' + Math.max(1, Math.ceil(retry / 1000)) + ' 秒后重试' : '请求过于频繁，请稍后重试';
    }
    if (error.code === 'HTTP_ERROR') {
      var payload = error.detail && error.detail.payload;
      var apiCode = payload && (payload.code || payload.errorCode || payload.error_code);
      var known = {
        AUTH_INVALID: '用户名或密码错误',
        UNAUTHENTICATED: '登录状态已失效，请重新登录',
        FORBIDDEN: '当前账号没有权限执行此操作',
        NOT_FOUND: '请求的数据不存在或已被移除',
        CONFLICT: '数据状态已变化，请刷新后重试',
        VALIDATION_ERROR: '提交内容不符合要求，请检查后重试',
        RATE_LIMITED: '操作过于频繁，请稍后重试',
        INTERNAL_ERROR: '数据服务暂时不可用，请稍后重试'
      };
      if (apiCode && known[apiCode]) return known[apiCode];
      var status = error.detail && error.detail.status;
      var serverDetail = payload && (payload.detail || payload.message);
      if (serverDetail) {
        return '请求失败' + (status ? '（' + status + '）' : '') + '：' + serverDetail;
      }
      if (status) {
        if (status === 405) return '请求被拦截（HTTP 405）：请确认反向代理或 WAF 放行 PUT/PATCH/DELETE 方法';
        if (status === 400) return '请求被拒绝（HTTP 400）：安全校验未通过，请刷新页面后重试';
        if (status === 401) return '登录状态已失效，请重新登录（HTTP 401）';
        if (status === 403) return '请求被拒绝（HTTP 403）：权限不足或安全校验未通过';
        if (status === 404) return '请求的资源不存在（HTTP 404）';
        if (status >= 500) return '服务端错误（HTTP ' + status + '），请查看服务日志';
        return '请求失败（HTTP ' + status + '）';
      }
      return '请求失败，请稍后重试';
    }
    return '请求失败，请稍后重试';
  }

  function callConfigured(name, options) {
    if (!routes()[name]) {
      return Promise.reject(createError('API_NOT_CONFIGURED', '接口尚未配置：' + name));
    }
    options = options || {};
    if (options.background === true) {
      var route = routeConfig(name);
      var params = options.params || {};
      var path = replaceParams(route.path, params);
      var requestOptions = {};
      Object.keys(options).forEach(function (key) {
        if (key !== 'params' && key !== 'background') requestOptions[key] = options[key];
      });
      requestOptions.method = 'GET';
      return backgroundGet(path, requestOptions);
    }
    return call(name, options);
  }

  window.ScrcpyGateApi = {
    request: request,
    backgroundGet: backgroundGet,
    clearBackground: clearBackground,
    call: call,
    get: function (name, options) { return call(name, Object.assign({}, options, { method: 'GET' })); },
    post: function (name, options) { return call(name, Object.assign({}, options, { method: 'POST' })); },
    put: function (name, options) { return call(name, Object.assign({}, options, { method: 'PUT' })); },
    patch: function (name, options) { return call(name, Object.assign({}, options, { method: 'PATCH' })); },
    del: function (name, options) { return call(name, Object.assign({}, options, { method: 'DELETE' })); },
    isConfigured: function (name) { return !!routes()[name]; },
    list: payloadList,
    errorMessage: errorMessage,
    safeDownloadUrl: safeDownloadUrl,
    configuration: config
    ,configured: callConfigured
  };
})(window, document);
