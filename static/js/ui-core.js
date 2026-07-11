(function () {
  "use strict";

  const THEME_KEY = "scrcpygate:theme";
  const THEMES = new Set(["system", "dark", "light"]);
  const root = document.documentElement;
  const busyState = new WeakMap();
  const layerTriggers = new WeakMap();
  const activeLayers = [];
  const iconUrl = "/static/icons/lucide.svg?v=20260712#";

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
    messageNode.textContent = String(message || "操作已完成");
    closeButton.className = "ui-toast__close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "关闭通知");
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
    )).filter((node) => !node.hidden && node.getAttribute("aria-hidden") !== "true");
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
    const focusTarget = layer.querySelector("[autofocus]") || focusableElements(layer)[0] || layer;
    if (!layer.hasAttribute("tabindex") && focusTarget === layer) layer.setAttribute("tabindex", "-1");
    window.requestAnimationFrame(() => focusTarget.focus({ preventScroll: true }));
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
    const targetToFocus = dialog.querySelector("[autofocus]") || focusableElements(dialog)[0] || dialog;
    window.requestAnimationFrame(() => targetToFocus.focus({ preventScroll: true }));
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
    syncThemeControls(root.dataset.theme || readTheme());
    updateThemeColor();

    document.querySelectorAll("[data-ui-theme-select]").forEach((select) => {
      select.addEventListener("change", () => applyTheme(select.value, true));
    });

    document.addEventListener("click", (event) => {
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
      if (event.key === "Escape" && topLayer()) {
        event.preventDefault();
        closeTopLayer();
        return;
      }
      trapLayerFocus(event);
    });
  }

  applyTheme(readTheme(), false);

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

  window.ScrcpyGateUI = Object.freeze({
    THEME_KEY,
    getTheme: () => root.dataset.theme || readTheme(),
    setTheme: (theme) => applyTheme(theme, true),
    setBusy,
    toast,
    openDrawer,
    closeDrawer,
    openDialog,
    closeDialog
  });
})();
