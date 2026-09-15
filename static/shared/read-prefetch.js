/*
 * 读模型预取（首屏提速）。
 *
 * 管理页的读模型请求原本要等 <script defer> 的控制器跑起来（实测 ~266ms）才发出；
 * 这个脚本在 <head> 里同步执行，把请求提前到解析阶段（~100ms）发起，再由 api.js 的
 * request() 复用同一个 Response，省掉一整个 JS 加载周期。
 *
 * 约定：
 *   - 页面通过 _injection 注入 window.ScrcpyGatePrefetchUrls = ['/api/...']（带 nonce）。
 *   - api.js 只对同 URL 的 GET 调用 take()；拿不到或预取失败时退回自己请求，
 *     因此这个模块失效不会影响任何页面功能。
 */
(function (window) {
  'use strict';

  var TIMEOUT_MS = 15000;
  var pending = new Map();

  /* api.js 的 buildUrl 返回绝对地址（url.href），所以这里也按绝对地址做键。 */
  function keyOf(url) {
    try {
      return new URL(String(url), window.location.origin).href;
    } catch (error) {
      return String(url);
    }
  }

  function start(rawUrl) {
    var url = keyOf(rawUrl);
    if (!rawUrl || pending.has(url)) return;
    if (typeof window.fetch !== 'function') return;
    var controller = window.AbortController ? new AbortController() : null;
    var timer = null;
    var promise = window.fetch(url, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      if (timer) window.clearTimeout(timer);
      return response;
    }).catch(function () {
      if (timer) window.clearTimeout(timer);
      return null;
    });
    if (controller) {
      timer = window.setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    }
    pending.set(url, promise);
  }

  function prefetch(urls) {
    if (!urls) return;
    for (var i = 0; i < urls.length; i += 1) start(urls[i]);
  }

  /* 供 api.js 调用：同一个 URL 只交付一次，交付后由调用方负责兜底重发。 */
  function take(url) {
    var key = keyOf(url);
    var promise = pending.get(key);
    if (!promise) return null;
    pending.delete(key);
    return promise;
  }

  window.ScrcpyGatePrefetch = { prefetch: prefetch, take: take };
  prefetch(window.ScrcpyGatePrefetchUrls);
})(window);
