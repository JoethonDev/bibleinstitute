// ponytail: admin hotkeys; Ctrl on Windows/Linux, Cmd on macOS, e.code
// (physical key position) so Arabic layouts work. Never fires while typing.
(function () {
  var gArmedAt = 0;
  // Substring matched against the sidebar links' hrefs (locale prefix agnostic).
  var GOTO = {
    KeyD: "/dashboard/",
    KeyU: "/dashboard/users/",
    KeyA: "/dashboard/applications/",
    KeyC: "/dashboard/courses/",
    KeyL: "/dashboard/lessons/",
    KeyQ: "/dashboard/quizzez/",
    KeyM: "/dashboard/media-processing/",
    KeyR: "/dashboard/r2-management/",
    KeyS: "/scanner/",
    KeyP: "/dashboard/progress/",
  };
  function modalOpen(id) {
    var m = document.getElementById(id);
    return !!(m && m.classList.contains("show"));
  }
  function anyModalOpen() {
    return modalOpen("commandPaletteModal") || modalOpen("shortcutsHelpModal") || modalOpen("globalConfirmModal");
  }
  function typing() {
    var el = document.activeElement;
    return !!(el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable));
  }
  function toggleHelp() {
    var m = document.getElementById("shortcutsHelpModal");
    if (window.bootstrap && m) bootstrap.Modal.getOrCreateInstance(m).toggle();
  }
  function goTo(path) {
    // Click the matching sidebar link so HTMX navigation is preserved.
    // Suffix match: "/dashboard/" must not match "/dashboard/users/".
    var link = document.querySelector('.ad-tab[href$="' + path + '"]');
    if (link) { link.click(); return; }
    var lang = document.documentElement.lang || "en";
    window.location.href = "/" + lang + path;
  }
  function focusSearch() {
    var inputs = document.querySelectorAll('#content input[type="search"], #display-page input[type="search"]');
    for (var i = 0; i < inputs.length; i++) {
      if (inputs[i].id === "command-palette-input") continue;
      if (inputs[i].offsetParent !== null) { inputs[i].focus(); inputs[i].select(); return true; }
    }
    return false;
  }
  document.addEventListener("keydown", function (e) {
    var mod = e.ctrlKey || e.metaKey;
    // Help: ? (no modifiers) or Ctrl/Cmd + / — not while typing, not in palette.
    if (!mod && e.key === "?" && !typing() && !modalOpen("commandPaletteModal")) {
      e.preventDefault();
      toggleHelp();
      return;
    }
    if (mod && e.code === "Slash" && !e.shiftKey) {
      e.preventDefault();
      if (!modalOpen("commandPaletteModal")) toggleHelp();
      return;
    }
    if (anyModalOpen() || typing() || mod || e.altKey) { gArmedAt = 0; return; }
    // Focus page search with / (physical key, layout independent).
    if (e.code === "Slash") {
      if (focusSearch()) e.preventDefault();
      gArmedAt = 0;
      return;
    }
    // vim-style "g u" navigation.
    if (e.code === "KeyG") { gArmedAt = Date.now(); return; }
    if (gArmedAt) {
      var fresh = Date.now() - gArmedAt < 1000;
      gArmedAt = 0;
      if (fresh && GOTO[e.code]) {
        e.preventDefault();
        goTo(GOTO[e.code]);
      }
    }
  });
  window.toggleShortcutsHelp = toggleHelp;
})();
