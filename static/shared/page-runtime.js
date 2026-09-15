/* Shared page primitives.  Feature pages keep their domain-specific flows. */
(function (window, document) {
  'use strict';

  var existing = window.ScrcpyGateUi || {};

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

  function modalOpen(id) {
    var modal = document.getElementById(id);
    if (!modal) return false;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    modal.removeAttribute('inert');
    return true;
  }

  function modalClose(id) {
    var modal = document.getElementById(id);
    if (!modal) return false;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    modal.setAttribute('inert', '');
    return true;
  }

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
