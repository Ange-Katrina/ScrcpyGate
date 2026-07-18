(function () {
  "use strict";

  const i18n = window.ScrcpyGateI18n;
  const t = (key, values) => (
    i18n && typeof i18n.t === "function" ? i18n.t(key, values) : String(key || "")
  );
  const THEME_KEY = "scrcpygate:theme";
  const THEMES = new Set(["system", "dark", "light"]);
  const root = document.documentElement;
  const busyState = new WeakMap();
  const layerTriggers = new WeakMap();
  const activeLayers = [];
  const themeControls = [];
  const localeControls = [];
  const localePopupControls = [];
  const iconUrl = "/static/icons/lucide.svg?v=cb7d1235489f#";
  let viewportFrame = 0;
  let viewportSignature = "";

  function finiteMetric(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : fallback;
  }

  function syncVisualViewportMetrics() {
    const viewport = window.visualViewport;
    const fallbackHeight = Math.max(1, finiteMetric(window.innerHeight, root.clientHeight || 1));
    const height = Math.max(1, Math.round(finiteMetric(viewport && viewport.height, fallbackHeight)));
    const offsetTop = Math.round(finiteMetric(viewport && viewport.offsetTop, 0));
    const offsetLeft = Math.round(finiteMetric(viewport && viewport.offsetLeft, 0));
    const keyboardInset = Math.max(0, Math.round(fallbackHeight - height - offsetTop));
    const compact = height <= 520 || keyboardInset >= 120;
    const signature = `${height}:${offsetTop}:${offsetLeft}:${compact ? 1 : 0}`;
    if (signature === viewportSignature) return;
    viewportSignature = signature;
    root.style.setProperty("--ui-visual-viewport-height", `${height}px`);
    root.style.setProperty("--ui-visual-viewport-offset-top", `${offsetTop}px`);
    root.style.setProperty("--ui-visual-viewport-offset-left", `${offsetLeft}px`);
    root.classList.toggle("ui-compact-viewport", compact);
    window.dispatchEvent(new CustomEvent("scrcpygate:viewportchange", {
      detail: { height, offsetTop, offsetLeft, keyboardInset, compact }
    }));
  }

  function scheduleVisualViewportMetrics() {
    if (viewportFrame) return;
    viewportFrame = window.requestAnimationFrame(() => {
      viewportFrame = 0;
      syncVisualViewportMetrics();
    });
  }

  function readTheme() {
    try {
      const value = window.localStorage.getItem(THEME_KEY);
      return THEMES.has(value) ? value : "dark";
    } catch (_) {
      return "dark";
    }
  }

  function updateThemeColor() {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    const resolvedLight = root.dataset.theme === "light" || (
      root.dataset.theme === "system" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches
    );
    meta.content = resolvedLight ? "#f6f7f9" : "#111315";
  }

  function syncThemeControls(theme) {
    document.querySelectorAll("[data-ui-theme-select]").forEach((select) => {
      if (select.value !== theme) select.value = theme;
    });
    themeControls.forEach((control) => control.sync(theme));
  }

  function applyTheme(theme, persist) {
    const next = THEMES.has(theme) ? theme : "dark";
    root.dataset.theme = next;
    if (persist) {
      try {
        window.localStorage.setItem(THEME_KEY, next);
      } catch (_) {
        // Storage can be blocked by browser privacy settings; the theme still works for this page.
      }
    }
    syncThemeControls(next);
    updateThemeColor();
    window.dispatchEvent(new CustomEvent("scrcpygate:themechange", { detail: { theme: next } }));
    return next;
  }

  function supportedLocale(locale) {
    return !!(i18n && Array.isArray(i18n.supportedLocales) && i18n.supportedLocales.includes(locale));
  }

  function readLocalePreference() {
    if (!i18n) return "zh-CN";
    try {
      const value = window.localStorage.getItem(i18n.storageKey);
      return supportedLocale(value) ? value : i18n.locale;
    } catch (_) {
      return i18n.locale;
    }
  }

  function writeLocaleCookie(locale) {
    if (!i18n || !supportedLocale(locale)) return;
    document.cookie = `${encodeURIComponent(i18n.cookieName)}=${encodeURIComponent(locale)}; Path=/; Max-Age=31536000; SameSite=Lax`;
  }

  function syncLocaleControls(locale) {
    localeControls.forEach((select) => {
      if (select.value !== locale) select.value = locale;
    });
    localePopupControls.forEach((control) => control.sync(locale));
  }

  function applyLocale(locale, persist = true) {
    if (!i18n || !supportedLocale(locale)) return i18n ? i18n.locale : "zh-CN";
    if (persist) {
      try { window.localStorage.setItem(i18n.storageKey, locale); }
      catch (_) { /* The cookie still preserves the selected locale. */ }
    }
    writeLocaleCookie(locale);
    syncLocaleControls(locale);
    if (locale !== i18n.locale) {
      root.dataset.localeSwitching = "true";
      window.location.reload();
    }
    return locale;
  }

  function syncStoredLocale() {
    if (!i18n) return false;
    const preferred = readLocalePreference();
    if (preferred === i18n.locale) {
      try { window.sessionStorage.removeItem(`${i18n.storageKey}:reload`); }
      catch (_) { /* Session storage is optional. */ }
      writeLocaleCookie(preferred);
      return false;
    }
    let alreadyRetried = false;
    try {
      alreadyRetried = window.sessionStorage.getItem(`${i18n.storageKey}:reload`) === preferred;
      if (!alreadyRetried) window.sessionStorage.setItem(`${i18n.storageKey}:reload`, preferred);
    } catch (_) {
      // Do not auto-reload when the browser blocks sessionStorage; an explicit selection still works.
      return false;
    }
    writeLocaleCookie(preferred);
    if (!alreadyRetried) {
      root.dataset.localeSwitching = "true";
      window.location.reload();
      return true;
    }
    return false;
  }

  function closeLocaleMenus(restoreFocus, except) {
    localePopupControls.forEach((control) => {
      if (control !== except && control.isOpen()) control.close(restoreFocus);
    });
  }

  function initializeLocaleSelect(select, index) {
    if (!i18n || select.dataset.uiLocaleReady === "true") return;
    const wrapper = select.closest(".ui-locale-picker");
    if (!wrapper) return;
    select.dataset.uiLocaleReady = "true";
    localeControls.push(select);
    select.value = supportedLocale(i18n.locale) ? i18n.locale : i18n.supportedLocales[0];
    select.addEventListener("change", () => applyLocale(select.value, true));

    const trigger = document.createElement("button");
    const triggerLabel = document.createElement("span");
    const triggerArrow = document.createElement("span");
    const menu = document.createElement("div");
    const menuId = `uiLocaleMenu-${select.id || index + 1}`;

    select.classList.add("ui-locale-native");
    select.hidden = true;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    trigger.type = "button";
    trigger.className = "ui-locale-trigger";
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", menuId);
    triggerLabel.className = "ui-locale-trigger__label";
    triggerArrow.className = "ui-picker-arrow";
    triggerArrow.setAttribute("aria-hidden", "true");
    trigger.append(triggerLabel, triggerArrow);

    menu.id = menuId;
    menu.className = "ui-locale-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", t("common.language.label"));
    menu.hidden = true;

    const items = Array.from(select.options).map((option) => {
      const item = document.createElement("button");
      const label = document.createElement("span");
      item.type = "button";
      item.className = "ui-locale-option";
      item.dataset.localeValue = option.value;
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", String(option.value === select.value));
      item.tabIndex = -1;
      label.textContent = option.textContent;
      item.append(label, icon("check", "ui-icon ui-locale-option__check"));
      menu.appendChild(item);
      return item;
    });

    function selectedIndex() {
      return Math.max(0, items.findIndex((item) => item.dataset.localeValue === select.value));
    }

    function focusItem(itemIndex) {
      if (!items.length) return;
      items[(itemIndex + items.length) % items.length].focus({ preventScroll: true });
    }

    const control = {
      sync(locale) {
        if (select.value !== locale) select.value = locale;
        const selectedOption = Array.from(select.options).find((option) => option.value === locale) || select.options[0];
        const label = selectedOption ? selectedOption.textContent : locale;
        triggerLabel.textContent = label;
        trigger.setAttribute("aria-label", `${t("common.language.label")}：${label}`);
        items.forEach((item) => item.setAttribute("aria-checked", String(item.dataset.localeValue === locale)));
      },
      isOpen() { return !menu.hidden; },
      open(itemIndex) {
        closeThemeMenus(false);
        closeLocaleMenus(false, control);
        menu.hidden = false;
        wrapper.classList.add("is-open");
        trigger.setAttribute("aria-expanded", "true");
        window.requestAnimationFrame(() => focusItem(itemIndex === undefined ? selectedIndex() : itemIndex));
      },
      close(restoreFocus) {
        menu.hidden = true;
        wrapper.classList.remove("is-open");
        trigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) trigger.focus({ preventScroll: true });
      }
    };

    items.forEach((item) => item.addEventListener("click", () => {
      const locale = item.dataset.localeValue;
      control.close(false);
      applyLocale(locale, true);
    }));
    trigger.addEventListener("click", () => control.isOpen() ? control.close(true) : control.open(selectedIndex()));
    trigger.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End", "Escape"].includes(event.key)) return;
      if (event.key === "Escape") {
        if (!control.isOpen()) return;
        event.preventDefault();
        event.stopPropagation();
        control.close(true);
        return;
      }
      event.preventDefault();
      control.open(event.key === "ArrowUp" || event.key === "End" ? items.length - 1 : 0);
    });
    menu.addEventListener("keydown", (event) => {
      const current = items.indexOf(document.activeElement);
      if (event.key === "Tab") { control.close(false); return; }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        control.close(true);
        return;
      }
      if ((event.key === "Enter" || event.key === " ") && current >= 0) {
        event.preventDefault();
        items[current].click();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") focusItem(0);
      else if (event.key === "End") focusItem(items.length - 1);
      else focusItem((current < 0 ? selectedIndex() : current) + (event.key === "ArrowUp" ? -1 : 1));
    });

    wrapper.append(trigger, menu);
    localePopupControls.push(control);
    control.sync(select.value);
  }

  function icon(name, className) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    svg.setAttribute("class", className || "ui-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    use.setAttribute("href", iconUrl + name);
    svg.appendChild(use);
    return svg;
  }

  function closeThemeMenus(restoreFocus, except) {
    themeControls.forEach((control) => {
      if (control !== except && control.isOpen()) control.close(restoreFocus);
    });
  }

  function enhanceThemeSelect(select, index) {
    const wrapper = select.closest(".ui-theme-picker");
    if (!wrapper || select.dataset.uiThemeEnhanced === "true") return null;

    const trigger = document.createElement("button");
    const triggerLabel = document.createElement("span");
    const triggerArrow = document.createElement("span");
    const menu = document.createElement("div");
    const menuId = `uiThemeMenu-${select.id || index + 1}`;

    select.dataset.uiThemeEnhanced = "true";
    select.classList.add("ui-theme-native");
    select.hidden = true;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    trigger.type = "button";
    trigger.className = "ui-theme-trigger";
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", menuId);
    triggerLabel.className = "ui-theme-trigger__label";
    triggerArrow.className = "ui-theme-trigger__arrow";
    triggerArrow.setAttribute("aria-hidden", "true");
    trigger.append(triggerLabel, triggerArrow);

    menu.id = menuId;
    menu.className = "ui-theme-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", t("common.theme.label"));
    menu.hidden = true;

    const items = Array.from(select.options).map((option) => {
      const item = document.createElement("button");
      const label = document.createElement("span");
      item.type = "button";
      item.className = "ui-theme-option";
      item.dataset.themeValue = option.value;
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", "false");
      item.tabIndex = -1;
      label.textContent = option.textContent;
      item.append(label, icon("check", "ui-icon ui-theme-option__check"));
      item.addEventListener("click", () => {
        applyTheme(option.value, true);
        control.close(true);
      });
      menu.appendChild(item);
      return item;
    });

    function selectedIndex() {
      const selected = Math.max(0, items.findIndex((item) => item.dataset.themeValue === select.value));
      return Math.min(selected, Math.max(0, items.length - 1));
    }

    function focusItem(itemIndex) {
      if (!items.length) return;
      items[(itemIndex + items.length) % items.length].focus({ preventScroll: true });
    }

    const control = {
      sync(theme) {
        if (select.value !== theme) select.value = theme;
        const selectedOption = Array.from(select.options).find((option) => option.value === theme) || select.options[0];
        const label = selectedOption ? selectedOption.textContent : t("common.theme.dark");
        triggerLabel.textContent = label;
        trigger.setAttribute("aria-label", t("common.theme.current", { theme: label }));
        items.forEach((item) => item.setAttribute("aria-checked", String(item.dataset.themeValue === theme)));
      },
      isOpen() {
        return !menu.hidden;
      },
      open(itemIndex) {
        closeLocaleMenus(false);
        closeThemeMenus(false, control);
        menu.hidden = false;
        wrapper.classList.add("is-open");
        trigger.setAttribute("aria-expanded", "true");
        window.requestAnimationFrame(() => focusItem(itemIndex === undefined ? selectedIndex() : itemIndex));
      },
      close(restoreFocus) {
        menu.hidden = true;
        wrapper.classList.remove("is-open");
        trigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) trigger.focus({ preventScroll: true });
      }
    };

    trigger.addEventListener("click", () => {
      if (control.isOpen()) control.close(true);
      else control.open(selectedIndex());
    });
    trigger.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End", "Escape"].includes(event.key)) return;
      if (event.key === "Escape") {
        if (!control.isOpen()) return;
        event.preventDefault();
        event.stopPropagation();
        control.close(true);
        return;
      }
      event.preventDefault();
      if (event.key === "Home" || event.key === "ArrowDown") control.open(0);
      else control.open(items.length - 1);
    });
    menu.addEventListener("keydown", (event) => {
      const current = items.indexOf(document.activeElement);
      if (event.key === "Tab") {
        control.close(false);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        control.close(true);
        return;
      }
      if ((event.key === "Enter" || event.key === " ") && current >= 0) {
        event.preventDefault();
        items[current].click();
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") focusItem(0);
      else if (event.key === "End") focusItem(items.length - 1);
      else focusItem((current < 0 ? selectedIndex() : current) + (event.key === "ArrowUp" ? -1 : 1));
    });

    wrapper.append(trigger, menu);
    select.addEventListener("change", () => applyTheme(select.value, true));
    themeControls.push(control);
    control.sync(root.dataset.theme || readTheme());
    return control;
  }

  function setBusy(element, busy, label) {
    if (!element) return;
    if (busy) {
      if (!busyState.has(element)) {
        busyState.set(element, {
          disabled: Boolean(element.disabled),
          ariaLabel: element.getAttribute("aria-label")
        });
      }
      element.disabled = true;
      element.setAttribute("aria-busy", "true");
      element.classList.add("is-busy");
      if (label) element.setAttribute("aria-label", label);
      return;
    }
    const previous = busyState.get(element);
    element.disabled = previous ? previous.disabled : false;
    element.removeAttribute("aria-busy");
    element.classList.remove("is-busy");
    if (previous && previous.ariaLabel !== null) element.setAttribute("aria-label", previous.ariaLabel);
    else if (previous) element.removeAttribute("aria-label");
    busyState.delete(element);
  }

  function ensureToastRegion() {
    let region = document.getElementById("uiToastRegion");
    if (region) return region;
    region = document.createElement("div");
    region.id = "uiToastRegion";
    region.className = "ui-toast-region";
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "false");
    document.body.appendChild(region);
    return region;
  }

  function toast(message, options) {
    const settings = Object.assign({ type: "info", duration: 3600 }, options || {});
    const type = ["success", "warning", "danger", "info"].includes(settings.type) ? settings.type : "info";
    const toastNode = document.createElement("div");
    const messageNode = document.createElement("div");
    const closeButton = document.createElement("button");
    const iconName = { success: "check", warning: "triangle-alert", danger: "triangle-alert", info: "info" }[type];
    let timeoutId = null;

    toastNode.className = `ui-toast ui-toast--${type}`;
    toastNode.setAttribute("role", type === "danger" ? "alert" : "status");
    messageNode.className = "ui-toast__message";
    messageNode.textContent = String(message || t("common.feedback.completed"));
    closeButton.className = "ui-toast__close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", t("common.actions.close_notification"));
    closeButton.appendChild(icon("x"));
    toastNode.append(icon(iconName), messageNode, closeButton);

    function remove() {
      if (timeoutId) window.clearTimeout(timeoutId);
      toastNode.classList.remove("is-visible");
      window.setTimeout(() => toastNode.remove(), 180);
    }

    function schedule() {
      if (settings.duration > 0) timeoutId = window.setTimeout(remove, settings.duration);
    }

    closeButton.addEventListener("click", remove);
    toastNode.addEventListener("mouseenter", () => {
      if (timeoutId) window.clearTimeout(timeoutId);
    });
    toastNode.addEventListener("mouseleave", schedule);
    ensureToastRegion().appendChild(toastNode);
    window.requestAnimationFrame(() => toastNode.classList.add("is-visible"));
    schedule();
    return toastNode;
  }

  function resolveElement(target) {
    if (!target) return null;
    if (typeof target === "string") return document.getElementById(target) || document.querySelector(target);
    return target.nodeType === 1 ? target : null;
  }

  function focusableElements(layer) {
    return Array.from(layer.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter((node) => {
      if (node.hidden || node.getAttribute("aria-hidden") === "true") return false;
      if (node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
      if (!node.getClientRects().length) return false;
      const style = window.getComputedStyle(node);
      return style.display !== "none" && style.visibility !== "hidden";
    });
  }

  function showLayer(layer, trigger) {
    if (!layer) return null;
    if (trigger) layerTriggers.set(layer, trigger);
    layer.hidden = false;
    layer.removeAttribute("inert");
    layer.setAttribute("aria-hidden", "false");
    layer.classList.add("is-open");
    if (!activeLayers.includes(layer)) activeLayers.push(layer);
    document.documentElement.classList.add("ui-layer-open");
    const available = focusableElements(layer);
    const requested = layer.querySelector("[data-ui-initial-focus], [autofocus]");
    const closeControl = layer.querySelector('[data-ui-drawer-close], [data-ui-dialog-close], .ui-icon-button[aria-label]');
    const coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    const requestedInput = requested && requested.matches("input, select, textarea, [contenteditable]");
    const focusTarget = (coarsePointer && requestedInput ? closeControl : requested) || closeControl || available[0] || layer;
    if (!layer.hasAttribute("tabindex") && focusTarget === layer) layer.setAttribute("tabindex", "-1");
    window.requestAnimationFrame(() => {
      if (layer.hidden || layer.getAttribute("aria-hidden") === "true" || layer.hasAttribute("inert")) return;
      focusTarget.focus({ preventScroll: true });
    });
    return layer;
  }

  function hideLayer(layer) {
    if (!layer) return;
    layer.classList.remove("is-open");
    layer.setAttribute("aria-hidden", "true");
    layer.setAttribute("inert", "");
    layer.hidden = true;
    const index = activeLayers.lastIndexOf(layer);
    if (index >= 0) activeLayers.splice(index, 1);
    if (!activeLayers.length) document.documentElement.classList.remove("ui-layer-open");
    const trigger = layerTriggers.get(layer);
    if (trigger && trigger.isConnected) trigger.focus({ preventScroll: true });
    layerTriggers.delete(layer);
  }

  function openDrawer(target, trigger) {
    const drawer = resolveElement(target);
    if (!drawer) return null;
    const backdropId = drawer.getAttribute("data-ui-backdrop");
    const backdrop = backdropId ? resolveElement(backdropId) : null;
    if (backdrop) showLayer(backdrop, null);
    return showLayer(drawer, trigger || document.activeElement);
  }

  function closeDrawer(target) {
    const drawer = resolveElement(target);
    if (!drawer) return;
    const backdropId = drawer.getAttribute("data-ui-backdrop");
    hideLayer(drawer);
    if (backdropId) hideLayer(resolveElement(backdropId));
  }

  function openDialog(target, trigger) {
    const dialog = resolveElement(target);
    if (!dialog) return null;
    if (trigger) layerTriggers.set(dialog, trigger);
    dialog.hidden = false;
    dialog.removeAttribute("inert");
    dialog.setAttribute("aria-hidden", "false");
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    else dialog.setAttribute("open", "");
    if (!activeLayers.includes(dialog)) activeLayers.push(dialog);
    document.documentElement.classList.add("ui-layer-open");
    const available = focusableElements(dialog);
    const requested = dialog.querySelector("[data-ui-initial-focus], [autofocus]");
    const closeControl = dialog.querySelector('[data-ui-dialog-close], .ui-icon-button[aria-label]');
    const coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    const requestedInput = requested && requested.matches("input, select, textarea, [contenteditable]");
    const targetToFocus = (coarsePointer && requestedInput ? closeControl : requested) || closeControl || available[0] || dialog;
    window.requestAnimationFrame(() => {
      if (dialog.hidden || dialog.getAttribute("aria-hidden") === "true" || dialog.hasAttribute("inert")) return;
      targetToFocus.focus({ preventScroll: true });
    });
    return dialog;
  }

  function closeDialog(target, returnValue) {
    const dialog = resolveElement(target);
    if (!dialog) return;
    if (typeof dialog.close === "function" && dialog.open) dialog.close(returnValue || "");
    else dialog.removeAttribute("open");
    hideLayer(dialog);
  }

  function topLayer() {
    return activeLayers.length ? activeLayers[activeLayers.length - 1] : null;
  }

  function closeTopLayer() {
    const layer = topLayer();
    if (!layer) return;
    if (layer.matches("dialog")) closeDialog(layer, "cancel");
    else if (layer.classList.contains("ui-drawer-backdrop")) hideLayer(layer);
    else closeDrawer(layer);
  }

  function trapLayerFocus(event) {
    if (event.key !== "Tab") return;
    const layer = topLayer();
    if (!layer || layer.classList.contains("ui-drawer-backdrop")) return;
    const focusable = focusableElements(layer);
    if (!focusable.length) {
      event.preventDefault();
      layer.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function initialize() {
    if (syncStoredLocale()) return;
    scheduleVisualViewportMetrics();
    syncThemeControls(root.dataset.theme || readTheme());
    updateThemeColor();

    document.querySelectorAll("[data-ui-theme-select]").forEach(enhanceThemeSelect);
    document.querySelectorAll("[data-ui-locale-select]").forEach(initializeLocaleSelect);

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".ui-theme-picker")) closeThemeMenus(false);
      if (!event.target.closest(".ui-locale-picker")) closeLocaleMenus(false);
      const openDrawerButton = event.target.closest("[data-ui-drawer-open]");
      const closeDrawerButton = event.target.closest("[data-ui-drawer-close]");
      const openDialogButton = event.target.closest("[data-ui-dialog-open]");
      const closeDialogButton = event.target.closest("[data-ui-dialog-close]");
      if (openDrawerButton) openDrawer(openDrawerButton.dataset.uiDrawerOpen, openDrawerButton);
      if (closeDrawerButton) closeDrawer(closeDrawerButton.dataset.uiDrawerClose || closeDrawerButton.closest(".ui-drawer"));
      if (openDialogButton) openDialog(openDialogButton.dataset.uiDialogOpen, openDialogButton);
      if (closeDialogButton) closeDialog(closeDialogButton.dataset.uiDialogClose || closeDialogButton.closest("dialog"), closeDialogButton.value);
    });

    document.addEventListener("keydown", (event) => {
      const openThemeControl = themeControls.find((control) => control.isOpen());
      const openLocaleControl = localePopupControls.find((control) => control.isOpen());
      if (event.key === "Escape" && openLocaleControl) {
        event.preventDefault();
        event.stopImmediatePropagation();
        openLocaleControl.close(true);
        return;
      }
      if (event.key === "Escape" && openThemeControl) {
        event.preventDefault();
        event.stopImmediatePropagation();
        openThemeControl.close(true);
        return;
      }
      if (event.key === "Escape" && topLayer()) {
        event.preventDefault();
        closeTopLayer();
        return;
      }
      trapLayerFocus(event);
    });
  }

  applyTheme(readTheme(), false);
  scheduleVisualViewportMetrics();

  window.addEventListener("resize", scheduleVisualViewportMetrics, { passive: true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", scheduleVisualViewportMetrics, { passive: true });
    window.visualViewport.addEventListener("scroll", scheduleVisualViewportMetrics, { passive: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();

  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) applyTheme(THEMES.has(event.newValue) ? event.newValue : "dark", false);
  });

  if (window.matchMedia) {
    const colorScheme = window.matchMedia("(prefers-color-scheme: light)");
    const onSystemThemeChange = () => {
      if (root.dataset.theme === "system") updateThemeColor();
    };
    if (colorScheme.addEventListener) colorScheme.addEventListener("change", onSystemThemeChange);
    else if (colorScheme.addListener) colorScheme.addListener(onSystemThemeChange);
  }

  window.addEventListener("storage", (event) => {
    if (i18n && event.key === i18n.storageKey && supportedLocale(event.newValue) && event.newValue !== i18n.locale) {
      applyLocale(event.newValue, false);
    }
  });

  window.ScrcpyGateUI = Object.freeze({
    THEME_KEY,
    getTheme: () => root.dataset.theme || readTheme(),
    setTheme: (theme) => applyTheme(theme, true),
    getLocale: () => i18n ? i18n.locale : "zh-CN",
    setLocale: (locale) => applyLocale(locale, true),
    setBusy,
    toast,
    openDrawer,
    closeDrawer,
    openDialog,
    closeDialog,
    syncViewport: scheduleVisualViewportMetrics
  });
})();
