(function () {
  "use strict";

  const form = document.getElementById("loginForm");
  const password = document.getElementById("password");
  const passwordToggle = document.getElementById("passwordToggle");
  const submitButton = document.getElementById("loginSubmit");
  const submitLabel = document.getElementById("loginSubmitLabel");
  const submitStatus = document.getElementById("loginSubmitStatus");
  let submitting = false;

  function setPasswordVisible(visible) {
    if (!password || !passwordToggle) return;
    const label = visible ? "隐藏密码" : "显示密码";
    password.type = visible ? "text" : "password";
    passwordToggle.setAttribute("aria-pressed", String(visible));
    passwordToggle.setAttribute("aria-label", label);
    passwordToggle.title = label;
  }

  function setSubmitting(busy) {
    submitting = busy;
    if (form) form.setAttribute("aria-busy", String(busy));
    if (submitLabel) submitLabel.textContent = busy ? "正在登录…" : "登录";
    if (submitStatus) submitStatus.textContent = busy ? "正在验证登录信息，请稍候。" : "";
    if (window.ScrcpyGateUI && typeof window.ScrcpyGateUI.setBusy === "function") {
      window.ScrcpyGateUI.setBusy(submitButton, busy, busy ? "正在登录" : undefined);
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
