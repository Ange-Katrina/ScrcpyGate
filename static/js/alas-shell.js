(function () {
  "use strict";

  const THEME_KEY = "scrcpygate:theme";
  const THEMES = new Set(["system", "dark", "light"]);
  const frame = document.getElementById("alasFrame");
  const state = document.getElementById("alasState");
  const stateTitle = document.getElementById("stateTitle");
  const stateDetail = document.getElementById("stateDetail");
  const stateActions = document.getElementById("stateActions");
  const loadStatus = document.getElementById("loadStatus");
  const refreshButton = document.getElementById("refreshFrame");
  const retryButton = document.getElementById("retryFrame");
  const LOAD_TIMEOUT_MS = 15000;
  let timeoutId = 0;

  if (!frame || !state || !stateTitle || !stateDetail || !stateActions || !loadStatus || !refreshButton || !retryButton) {
    return;
  }

  function updateThemeColor() {
    const preference = document.documentElement.dataset.theme;
    const light = preference === "light" || (
      preference === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    );
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = light ? "#f6f7f9" : "#111315";
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = THEMES.has(theme) ? theme : "dark";
    updateThemeColor();
  }

  function showState(kind, title, detail) {
    state.dataset.kind = kind;
    state.classList.add("is-visible");
    state.setAttribute("role", kind === "loading" ? "status" : "alert");
    stateTitle.textContent = title;
    stateDetail.textContent = detail;
    stateActions.hidden = kind === "loading";
    loadStatus.dataset.state = kind === "loading" ? "loading" : "error";
    loadStatus.textContent = kind === "loading" ? "正在连接" : (kind === "timeout" ? "加载超时" : "连接失败");
    frame.setAttribute("aria-busy", "true");
  }

  function hideState() {
    state.classList.remove("is-visible");
    state.setAttribute("role", "status");
    loadStatus.dataset.state = "ready";
    loadStatus.textContent = "已连接";
    frame.setAttribute("aria-busy", "false");
  }

  function clearLoadTimeout() {
    if (!timeoutId) return;
    window.clearTimeout(timeoutId);
    timeoutId = 0;
  }

  function scheduleLoadTimeout() {
    clearLoadTimeout();
    timeoutId = window.setTimeout(() => {
      timeoutId = 0;
      showState(
        "timeout",
        "ALAS 加载超时",
        "Runtime 仍未响应。你可以重试，或确认 ALAS 服务与网络连接是否正常。"
      );
    }, LOAD_TIMEOUT_MS);
  }

  function retryUrl() {
    const url = new URL(frame.getAttribute("src"), window.location.href);
    url.searchParams.set("_scrcpygate_retry", String(Date.now()));
    return url.href;
  }

  function startLoad(forceReload) {
    showState("loading", "正在加载 ALAS", "正在连接 ALAS Runtime，请稍候。");
    scheduleLoadTimeout();
    if (forceReload) frame.src = retryUrl();
  }

  function responseLooksUnavailable(doc) {
    if (!doc) return true;
    const contentType = String(doc.contentType || "").toLowerCase();
    const text = String(doc.body && (doc.body.innerText || doc.body.textContent) || "").slice(0, 1200);
    return contentType.includes("application/json") ||
      /unreachable|not configured|控制未启用|不可达|未配置|Bad Gateway/i.test(text);
  }

  function inspectLoadedFrame() {
    clearLoadTimeout();
    try {
      if (responseLooksUnavailable(frame.contentDocument)) {
        showState(
          "unreachable",
          "ALAS Runtime 不可达",
          "无法载入上游页面。请确认 Runtime 已启动、地址配置正确，然后重试。"
        );
        return;
      }
    } catch (_) {
      showState(
        "unreachable",
        "ALAS Runtime 不可达",
        "无法读取上游页面。请确认 Runtime 已启动、地址配置正确，然后重试。"
      );
      return;
    }
    hideState();
  }

  frame.addEventListener("load", inspectLoadedFrame);
  frame.addEventListener("error", () => {
    clearLoadTimeout();
    showState("unreachable", "ALAS Runtime 不可达", "浏览器无法载入上游页面，请检查服务状态后重试。");
  });
  refreshButton.addEventListener("click", () => startLoad(true));
  retryButton.addEventListener("click", () => startLoad(true));
  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) applyTheme(event.newValue);
  });
  if (window.matchMedia) {
    const colorScheme = window.matchMedia("(prefers-color-scheme: light)");
    if (colorScheme.addEventListener) colorScheme.addEventListener("change", updateThemeColor);
  }

  applyTheme(document.documentElement.dataset.theme);
  startLoad(false);
  try {
    if (frame.contentWindow.location.href !== "about:blank" && frame.contentDocument.readyState === "complete") {
      if (window.queueMicrotask) window.queueMicrotask(inspectLoadedFrame);
      else window.setTimeout(inspectLoadedFrame, 0);
    }
  } catch (_) {
    // The regular load event handles frames that are not yet readable.
  }
})();
