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
  const iconUrl = "/static/icons/lucide.svg?v=b32a80c7785c#";

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

    document.querySelectorAll("[data-ui-theme-select]").forEach(enhanceThemeSelect);

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".ui-theme-picker")) closeThemeMenus(false);
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
