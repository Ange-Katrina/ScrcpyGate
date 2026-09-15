/* State-driven non-video control helpers for the mirror page. */
(function (global) {
  'use strict';

  function create(options) {
    options = options || {};
    var getState = options.getState || function () { return {}; };
    var setState = options.setState || function () {};
    var onControlAction = options.onControlAction || function () {};

    function canUseMenu() {
      var state = getState() || {};
      return state.watchState === 'playing' && !state.controlBusy && !state.navBusy && !state.moreActionBusy;
    }

    function canUseControlMenu() {
      var state = getState() || {};
      return canUseMenu() && state.controlState === 'self';
    }

    function action(name, payload) {
      onControlAction(name, payload || {});
    }

    function setKeyboard(enabled) {
      var state = getState() || {};
      setState(Object.assign({}, state, { keyboardOn: !!enabled }));
      action(enabled ? 'keyboard_open' : 'keyboard_close');
    }

    function setDockHidden(hidden) {
      var state = getState() || {};
      setState(Object.assign({}, state, { dockHidden: !!hidden }));
      action(hidden ? 'dock_hide' : 'dock_show');
    }

    return Object.freeze({
      canUseMenu: canUseMenu,
      canUseControlMenu: canUseControlMenu,
      action: action,
      setKeyboard: setKeyboard,
      setDockHidden: setDockHidden
    });
  }

  global.ScrcpyGateMirrorControls = Object.freeze({ create: create });
}(window));
