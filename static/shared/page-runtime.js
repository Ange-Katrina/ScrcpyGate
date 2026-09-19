/* Shared page primitives.  Feature pages keep their domain-specific flows. */
(function (window, document) {
  'use strict';

  var existing = window.ScrcpyGateUi || {};
  var modalStack = [];

  function topModal() { return modalStack[modalStack.length - 1]; }

  function canFocus(node) {
    if (!node || !node.isConnected || node.closest('[inert]') || node.matches(':disabled')) return false;
    var style = window.getComputedStyle(node);
    return style.visibility === 'visible' && node.getClientRects().length > 0;
  }

  function focusableNodes(modal) {
    return Array.prototype.filter.call(
      modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]'),
      function (node) { return node.tabIndex >= 0 && canFocus(node); }
    );
  }

  function focusModal(entry) {
    var initial = entry.options.initialFocus && entry.modal.querySelector(entry.options.initialFocus);
    var target = canFocus(entry.lastFocus) ? entry.lastFocus : initial;
    if (!canFocus(target)) target = focusableNodes(entry.modal)[0];
    if (!target) {
      entry.modal.setAttribute('tabindex', '-1');
      target = entry.modal;
    }
    target.focus({ preventScroll: true });
  }

  function toast(message, type) {
    var host = document.querySelector('.sg-runtime-toast-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'sg-runtime-toast-host';
      host.setAttribute('aria-live', 'polite');
      host.setAttribute('aria-atomic', 'true');
      document.body.appendChild(host);
    }
    var item = document.createElement('div');
    item.className = 'sg-runtime-toast sg-runtime-toast-' + (type || 'info');
    item.setAttribute('role', type === 'error' ? 'alert' : 'status');
    item.textContent = String(message == null ? '' : message);
    host.appendChild(item);
    window.setTimeout(function () {
      if (item.parentNode) item.parentNode.removeChild(item);
    }, 3600);
  }

  function modalOpen(id, options) {
    var modal = document.getElementById(id);
    if (!modal) return false;
    if (modalStack.some(function (entry) { return entry.modal === modal; })) return false;
    var entry = { modal: modal, options: options || {}, opener: document.activeElement,
      openerId: document.activeElement.id, background: [] };
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    modal.removeAttribute('inert');
    modalStack.push(entry);
    focusModal(entry);
    // Inert siblings at each ancestor level, preserving their previous state.
    // A nested picker temporarily disables its parent drawer, not its draft.
    var branch = modal;
    while (branch && branch.parentElement) {
      Array.prototype.forEach.call(branch.parentElement.children, function (node) {
        if (node === branch || node.matches('script, style, link, .sg-runtime-toast-host, #toast-root')) return;
        if ((entry.options.companions || []).indexOf(node.id) !== -1) return;
        entry.background.push({ node: node, inert: node.inert });
        node.inert = true;
      });
      if (branch.parentElement === document.body) break;
      branch = branch.parentElement;
    }
    return true;
  }

  function modalClose(id) {
    var modal = document.getElementById(id);
    if (!modal) return false;
    var index = modalStack.findIndex(function (entry) { return entry.modal === modal; });
    if (index !== -1) {
      // A completed parent operation also dismisses any remaining child layers.
      while (modalStack.length > index + 1) modalClose(topModal().modal.id);
      var entry = modalStack.pop();
      entry.background.forEach(function (saved) { saved.node.inert = saved.inert; });
      var parent = topModal();
      var opener = entry.opener.isConnected ? entry.opener : document.getElementById(entry.openerId);
      if (canFocus(opener)) opener.focus({ preventScroll: true });
      else if (parent) focusModal(parent);
    }
    // Release focus before hiding the subtree to avoid aria-hidden focus conflicts.
    if (modal.contains(document.activeElement)) document.activeElement.blur();
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    modal.setAttribute('inert', '');
    return true;
  }

  document.addEventListener('keydown', function (event) {
    var entry = topModal();
    if (!entry || event.isComposing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (entry.options.onEscape) entry.options.onEscape();
      else modalClose(entry.modal.id);
      return;
    }
    if (event.key !== 'Tab') return;
    var nodes = focusableNodes(entry.modal);
    var first = nodes[0], last = nodes[nodes.length - 1];
    var active = document.activeElement;
    if (!first) { event.preventDefault(); focusModal(entry); return; }
    if (!entry.modal.contains(active) || (event.shiftKey ? active === first : active === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  }, true);

  document.addEventListener('focusin', function (event) {
    var entry = topModal();
    if (!entry) return;
    if (entry.modal.contains(event.target)) entry.lastFocus = event.target;
    else focusModal(entry);
  }, true);

  function bindVisibility(onChange) {
    if (typeof onChange !== 'function') return function () {};
    var handler = function () { onChange(document.visibilityState === 'visible'); };
    document.addEventListener('visibilitychange', handler, { passive: true });
    return function () { document.removeEventListener('visibilitychange', handler); };
  }

  window.ScrcpyGateUi = Object.assign(existing, {
    toast: existing.toast || toast,
    modal: existing.modal || { open: modalOpen, close: modalClose },
    bindVisibility: existing.bindVisibility || bindVisibility
  });
})(window, document);
