/**
 * Shared toast notification system — the single client-side notification
 * mechanism for the app. Renders with the same alert/icon markup as the
 * server-rendered Django messages in base.html, so a toast and a flash
 * message look identical regardless of which path produced it.
 *
 * Usage: window.notify("Saved successfully", "success");
 * Types: "success" | "error" (alias "danger") | "warning" | "info"
 */
(function () {
  "use strict";

  const ICONS = {
    success: "fa-check-circle",
    error: "fa-exclamation-circle",
    danger: "fa-exclamation-circle",
    warning: "fa-exclamation-triangle",
    info: "fa-info-circle",
  };

  function normalizeType(type) {
    return type === "danger" ? "error" : (ICONS[type] ? type : "info");
  }

  function getStack() {
    let stack = document.getElementById("equiper-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "equiper-toast-stack";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      stack.style.cssText =
        "position:fixed; top:1rem; right:1rem; z-index:99999; " +
        "display:flex; flex-direction:column; gap:.5rem; max-width:360px;";
      document.body.appendChild(stack);
    }
    return stack;
  }

  function notify(message, type = "info", duration = 4000) {
    const kind = normalizeType(type);
    const stack = getStack();

    const toast = document.createElement("div");
    toast.className = `alert alert-${kind === "error" ? "danger" : kind} alert-dismissible fade show equiper-alert`;
    toast.setAttribute("role", "alert");
    toast.innerHTML = `
      <div class="alert-inner">
        <i class="fas ${ICONS[kind]} alert-icon" aria-hidden="true"></i>
        <span class="alert-text"></span>
      </div>
      <button type="button" class="btn-close" aria-label="Close"></button>`;
    toast.querySelector(".alert-text").textContent = message;

    const dismiss = () => {
      toast.style.transition = "opacity .3s";
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 300);
    };
    toast.querySelector(".btn-close").addEventListener("click", dismiss);

    stack.appendChild(toast);
    setTimeout(dismiss, duration);
  }

  window.notify = notify;
})();
