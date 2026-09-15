(function () {
  "use strict";

  const i18n = window.ScrcpyGateI18n;
  const t = (key, values) => (
    i18n && typeof i18n.t === "function" ? i18n.t(key, values) : String(key || "")
  );
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
  const localeSelect = document.getElementById("localeSelect");
  const LOAD_TIMEOUT_MS = 15000;
  const READY_MESSAGE = "scrcpygate:alas-ready";
  let timeoutId = 0;
  let readyIntervalId = 0;

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
    loadStatus.textContent = kind === "loading"
      ? t("alas.shell.connecting")
      : t(kind === "timeout" ? "alas.shell.load_timeout_status" : "alas.shell.connection_failed");
    frame.setAttribute("aria-busy", "true");
  }

  function hideState() {
    state.classList.remove("is-visible");
    state.setAttribute("role", "status");
    loadStatus.dataset.state = "ready";
    loadStatus.textContent = t("alas.shell.connected");
    frame.setAttribute("aria-busy", "false");
  }

  function clearLoadTimeout() {
    if (timeoutId) window.clearTimeout(timeoutId);
    if (readyIntervalId) window.clearInterval(readyIntervalId);
    timeoutId = 0;
    readyIntervalId = 0;
  }

  function scheduleLoadTimeout() {
    clearLoadTimeout();
    if (frameOrigin() !== window.location.origin) {
      requestReadyMessage();
      readyIntervalId = window.setInterval(requestReadyMessage, 250);
    }
    timeoutId = window.setTimeout(() => {
      timeoutId = 0;
      if (readyIntervalId) window.clearInterval(readyIntervalId);
      readyIntervalId = 0;
      showState(
        "timeout",
        t("alas.shell.timeout_title"),
        t("alas.shell.timeout_detail")
      );
    }, LOAD_TIMEOUT_MS);
  }

  function retryUrl() {
    const url = new URL(frame.getAttribute("src"), window.location.href);
    url.searchParams.set("_scrcpygate_retry", String(Date.now()));
    return url.href;
  }

  function frameOrigin() {
    try {
      return new URL(frame.getAttribute("src"), window.location.href).origin;
    } catch (_) {
      return "";
    }
  }

  function isHttpOrigin(value) {
    const text = String(value || "");
    if (!text) return false;
    try {
      const parsed = new URL(text);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") &&
        parsed.origin === text;
    } catch (_) {
      return false;
    }
  }

  function isTrustedReadyMessage(event) {
    return event && event.source === frame.contentWindow &&
      event.origin === frameOrigin() && event.data &&
      event.data.type === READY_MESSAGE;
  }

  function requestReadyMessage() {
    const origin = frameOrigin();
    if (!isHttpOrigin(origin) || !frame.contentWindow) return;
    try {
      frame.contentWindow.postMessage(
        {
          type: `${READY_MESSAGE}-request`,
          parentOrigin: window.location.origin
        },
        origin
      );
    } catch (_) {}
  }

  function startLoad(forceReload) {
    showState("loading", t("alas.shell.loading_title"), t("alas.shell.loading_detail"));
    scheduleLoadTimeout();
    if (forceReload) frame.src = retryUrl();
  }

  function switchLocale(locale, persist) {
    if (!i18n || !i18n.supportedLocales.includes(locale)) return;
    if (persist) {
      try { window.localStorage.setItem(i18n.storageKey, locale); } catch (_) {}
    }
    document.cookie = `${encodeURIComponent(i18n.cookieName)}=${encodeURIComponent(locale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
    if (locale !== i18n.locale) window.location.reload();
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
      // Cross-origin iframe documents cannot be inspected. The proxied HTML
      // sends a validated postMessage when it has rendered successfully.
      if (frameOrigin() !== window.location.origin) {
        requestReadyMessage();
        scheduleLoadTimeout();
        return;
      }
      if (responseLooksUnavailable(frame.contentDocument)) {
        showState(
          "unreachable",
          t("alas.shell.unreachable_title"),
          t("alas.shell.unreachable_load_detail")
        );
        return;
      }
    } catch (_) {
      showState(
        "unreachable",
        t("alas.shell.unreachable_title"),
        t("alas.shell.unreachable_read_detail")
      );
      return;
    }
    hideState();
  }

  window.addEventListener("message", (event) => {
    if (isTrustedReadyMessage(event)) hideState();
  });
  frame.addEventListener("load", inspectLoadedFrame);
  frame.addEventListener("error", () => {
    clearLoadTimeout();
    showState(
      "unreachable",
      t("alas.shell.unreachable_title"),
      t("alas.shell.unreachable_browser_detail")
    );
  });
  refreshButton.addEventListener("click", () => startLoad(true));
  retryButton.addEventListener("click", () => startLoad(true));
  if (localeSelect && i18n) {
    localeSelect.value = i18n.locale;
    localeSelect.addEventListener("change", () => {
      switchLocale(localeSelect.value, true);
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) applyTheme(event.newValue);
    if (i18n && event.key === i18n.storageKey) switchLocale(event.newValue, false);
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
