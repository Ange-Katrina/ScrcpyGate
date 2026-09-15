/* Compatibility facade for the historical dashboard label asset. */
(function (global) {
  'use strict';

  var FUNCTION_NAMES = [
    'isEnglish', 'code', 'canonicalAction', 'reverseActionLabel', 'action',
    'actionKey', 'result', 'displayResult', 'severity', 'isAlertRecord',
    'isAlert', 'resultLabel', 'resultClass', 'category', 'categoryLabel',
    'reason', 'reasonLabel', 'readableActor', 'readableText', 'targetLabel',
    'title', 'meta', 'severityLabel', 'runtimeEventLabel', 'statusKey',
    'statusLabel', 'formatCount', 'dateTime', 'formatTime', 'timeLabel',
    'dotClass'
  ];
  var PROPERTY_NAMES = ['actionTitles', 'reasonLabels', 'targetLabels',
    'categoryLabels', 'resultLabels'];

  function currentOwner() {
    return global.ScrcpyGateAuditMapping || null;
  }

  var facade = {};
  Object.defineProperty(facade, '__auditCompatibilityProxy', { value: true });
  FUNCTION_NAMES.forEach(function (name) {
    facade[name] = function () {
      var owner = currentOwner();
      if (owner && typeof owner[name] === 'function') {
        return owner[name].apply(owner, arguments);
      }
      return name === 'isEnglish' ? false : '';
    };
  });
  PROPERTY_NAMES.forEach(function (name) {
    Object.defineProperty(facade, name, {
      enumerable: true,
      configurable: false,
      get: function () {
        var owner = currentOwner();
        return owner && owner[name] ? owner[name] : {};
      }
    });
  });

  if (!global.ScrcpyGateDashboard || global.ScrcpyGateDashboard.__auditCompatibilityProxy === true) {
    global.ScrcpyGateDashboard = Object.freeze(facade);
  }
}(window));
