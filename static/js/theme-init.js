(function () {
  "use strict";

  var theme = "dark";
  try {
    var stored = window.localStorage.getItem("scrcpygate:theme");
    if (stored === "system" || stored === "dark" || stored === "light") {
      theme = stored;
    }
  } catch (_) {
    // A blocked localStorage must not prevent the page from rendering.
  }
  document.documentElement.dataset.theme = theme;
})();
