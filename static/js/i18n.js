(function () {
  "use strict";

  const node = document.getElementById("scrcpygate-i18n");
  let payload = { locale: "zh-CN", messages: {} };
  if (node) {
    try {
      const parsed = JSON.parse(node.textContent || "{}");
      if (parsed && typeof parsed === "object") payload = parsed;
    } catch (_) {
      // Missing keys remain visible as keys instead of breaking page startup.
    }
  }

  const locale = String(payload.locale || "zh-CN");
  const messages = payload.messages && typeof payload.messages === "object" ? payload.messages : {};

  function resolve(key) {
    let current = messages;
    for (const part of String(key || "").split(".")) {
      if (!part || !current || typeof current !== "object" || !(part in current)) return null;
      current = current[part];
    }
    return typeof current === "string" ? current : null;
  }

  function t(key, values = {}) {
    const message = resolve(key);
    if (message == null) return String(key || "");
    return message.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
    ));
  }

  function has(key) {
    return resolve(key) != null;
  }

  document.documentElement.lang = locale;
  window.ScrcpyGateI18n = Object.freeze({ locale, messages, resolve, has, t });
})();
