// Theme handling + global keyboard shortcuts + PWA service-worker
// registration. Everything here is additive and defensive: a missing
// element or unsupported browser just does nothing.
(function () {
  "use strict";

  // ---- Theme toggle ----
  var toggle = document.getElementById("themeToggle");
  function currentDark() {
    return document.documentElement.dataset.theme === "dark";
  }
  function setTheme(dark, persist) {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    if (persist) {
      try { localStorage.setItem("kayock-theme", dark ? "dark" : "light"); } catch (e) {}
    }
  }
  if (toggle) {
    toggle.addEventListener("click", function () { setTheme(!currentDark(), true); });
  }
  // Follow the OS when the user hasn't made an explicit choice here.
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function (e) {
      var saved = localStorage.getItem("kayock-theme");
      if (saved !== "light" && saved !== "dark") setTheme(e.matches, false);
    });
  } catch (e) {}

  // ---- Keyboard shortcuts ----
  function isTyping(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }
  function focusableQuestionBox() {
    return (
      document.getElementById("questionInput") ||
      document.getElementById("deviceSearchInput") ||
      document.getElementById("obdInput") ||
      document.querySelector(".ask-box input[type=text]")
    );
  }
  var modal = document.getElementById("kbdModal");
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      if (modal && modal.classList.contains("show")) { modal.classList.remove("show"); }
      return;
    }
    if (isTyping(ev.target) || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.key === "/") {
      var box = focusableQuestionBox();
      if (box) { ev.preventDefault(); box.focus(); box.select && box.select(); }
    } else if (ev.key === "t") {
      if (toggle) { setTheme(!currentDark(), true); }
    } else if (ev.key === "?") {
      if (modal) { ev.preventDefault(); modal.classList.add("show"); }
    }
  });
  var kbdClose = document.getElementById("kbdClose");
  if (kbdClose && modal) {
    kbdClose.addEventListener("click", function () { modal.classList.remove("show"); });
    modal.addEventListener("click", function (e) { if (e.target === modal) modal.classList.remove("show"); });
  }

  // ---- PWA service worker (offline static shell; answers stay live) ----
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/static/js/sw.js").catch(function () {
        /* registration failures (e.g. plain http on LAN without SW support)
           are harmless -- the app keeps working as a normal web page */
      });
    });
  }
})();
