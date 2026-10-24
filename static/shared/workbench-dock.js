/* Shared toolbar geometry and overflow policy for workbench and admin preview. */
(function (global) {
  'use strict';
  var previousEdges = new WeakMap();
  var protectedFeatures = ['@alas', 'nav', 'more'];
  var priorities = ['rotate', 'fullscreen', 'keyboard', 'watch', 'acquire'];
  function buttons(dock) {
    var result = [];
    Array.prototype.forEach.call(dock.children, function (node) {
      if (node.matches('button, .wb-dock-item')) result.push(node);
      else if (node.matches('.cb-more')) {
        var trigger = node.querySelector(':scope > button');
        if (trigger) result.push(trigger);
      }
    });
    return result;
  }
  function visible(button) {
    return !button.closest('[hidden], [data-feature-off]') && button.getBoundingClientRect().width > 0;
  }
  function overflowCandidates(dock) {
    var controls = buttons(dock).filter(function (button) {
      var fixed = ['cb-alas', 'cb-back', 'cb-home', 'cb-tasks', 'cb-more-btn'].indexOf(button.id) >= 0;
      return !fixed && protectedFeatures.indexOf(button.getAttribute('data-dock-feature')) < 0 && visible(button);
    });
    var optional = controls.filter(function (button) {
      return priorities.indexOf(button.getAttribute('data-dock-feature')) < 0;
    }).reverse();
    priorities.forEach(function (feature) {
      controls.forEach(function (button) {
        if (button.getAttribute('data-dock-feature') === feature) optional.push(button);
      });
    });
    return optional;
  }
  function syncEdges(dock) {
    (previousEdges.get(dock) || []).forEach(function (button) { button.removeAttribute('data-dock-edge'); });
    dock.classList.add('sg-dock-shape');
    var controls = buttons(dock).filter(visible);
    if (controls.length) {
      controls[0].setAttribute('data-dock-edge', 'start');
      var last = controls[controls.length - 1];
      last.setAttribute('data-dock-edge', controls.length === 1 ? 'start end' : 'end');
    }
    previousEdges.set(dock, controls);
    return controls;
  }
  global.ScrcpyGateWorkbenchDock = Object.freeze({ buttons: buttons, overflowCandidates: overflowCandidates, syncEdges: syncEdges });
}(window));
