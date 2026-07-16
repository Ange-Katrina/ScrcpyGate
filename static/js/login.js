(function () {
  "use strict";

  const i18n = window.ScrcpyGateI18n;
  const t = (key, values) => (
    i18n && typeof i18n.t === "function" ? i18n.t(key, values) : String(key || "")
  );
  const form = document.getElementById("loginForm");
  const password = document.getElementById("password");
  const passwordToggle = document.getElementById("passwordToggle");
  const submitButton = document.getElementById("loginSubmit");
  const submitLabel = document.getElementById("loginSubmitLabel");
  const submitStatus = document.getElementById("loginSubmitStatus");
  let submitting = false;

  function setPasswordVisible(visible) {
    if (!password || !passwordToggle) return;
    const label = t(visible ? "login.hide_password" : "login.show_password");
    password.type = visible ? "text" : "password";
    passwordToggle.setAttribute("aria-pressed", String(visible));
    passwordToggle.setAttribute("aria-label", label);
    passwordToggle.title = label;
  }

  function setSubmitting(busy) {
    submitting = busy;
    if (form) form.setAttribute("aria-busy", String(busy));
    if (submitLabel) submitLabel.textContent = busy ? t("login.submitting") : t("login.submit");
    if (submitStatus) submitStatus.textContent = busy ? t("login.verifying") : "";
    if (window.ScrcpyGateUI && typeof window.ScrcpyGateUI.setBusy === "function") {
      window.ScrcpyGateUI.setBusy(submitButton, busy, busy ? t("login.submitting_short") : undefined);
    } else if (submitButton) {
      submitButton.disabled = busy;
      submitButton.toggleAttribute("aria-busy", busy);
    }
  }

  if (passwordToggle) {
    passwordToggle.addEventListener("click", () => {
      setPasswordVisible(passwordToggle.getAttribute("aria-pressed") !== "true");
      password.focus({ preventScroll: true });
    });
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      if (submitting) {
        event.preventDefault();
        return;
      }
      setSubmitting(true);
    });
  }

  window.addEventListener("pageshow", () => setSubmitting(false));
})();
