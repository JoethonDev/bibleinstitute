// ponytail: localStorage filter presets; no server, keeps signed-scope flow intact.
(function () {
  function t(s) { try { return (typeof gettext === "function") ? gettext(s) : s; } catch (e) { return s; } }
  function key() { return "ad-filter-presets:" + window.location.pathname; }
  function load() { try { return JSON.parse(localStorage.getItem(key()) || "[]"); } catch (e) { return []; } }
  function save(list) { try { localStorage.setItem(key(), JSON.stringify(list.slice(0, 20))); } catch (e) {} }
  function currentQuery() { return window.location.search || ""; }
  function init() {
    var bar = document.querySelector(".ad-filters");
    if (!bar || document.getElementById("ad-preset-wrap")) { sync(); return; }
    var wrap = document.createElement("div");
    wrap.id = "ad-preset-wrap";
    wrap.className = "d-flex align-items-center gap-2";
    var select = document.createElement("select");
    select.id = "ad-preset-select";
    select.className = "form-select form-select-sm";
    select.style.maxWidth = "16rem";
    select.setAttribute("aria-label", t("Saved filters"));
    var btnSave = document.createElement("button");
    btnSave.type = "button";
    btnSave.className = "ad-btn ad-btn--outline-gold ad-btn--sm";
    btnSave.textContent = t("Save");
    var btnDel = document.createElement("button");
    btnDel.type = "button";
    btnDel.className = "ad-btn ad-btn--danger ad-btn--sm";
    btnDel.textContent = t("Delete");
    btnSave.addEventListener("click", function () {
      var name = window.prompt(t("Name this filter"));
      if (!name) return;
      var list = load();
      list.push({ name: name.slice(0, 60), q: currentQuery() });
      save(list); sync();
    });
    btnDel.addEventListener("click", function () {
      var sel = document.getElementById("ad-preset-select");
      if (!sel || sel.value === "") return;
      var list = load();
      list.splice(parseInt(sel.value, 10), 1);
      save(list); sync();
    });
    select.addEventListener("change", function () {
      if (select.value === "") return;
      var p = load()[parseInt(select.value, 10)];
      if (p) window.location.href = window.location.pathname + p.q;
    });
    wrap.appendChild(select); wrap.appendChild(btnSave); wrap.appendChild(btnDel);
    bar.appendChild(wrap);
    sync();
  }
  function sync() {
    // Highlight the preset matching the current URL so the active filter
    // stays visible (and deletable) after navigation and HTMX swaps.
    var sel = document.getElementById("ad-preset-select");
    if (!sel) return;
    var list = load();
    var q = currentQuery();
    sel.innerHTML = "";
    var opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = t("Saved filters") + " (" + list.length + ")";
    sel.appendChild(opt0);
    var active = -1;
    list.forEach(function (p, i) {
      var o = document.createElement("option");
      o.value = String(i);
      o.textContent = p.name;
      sel.appendChild(o);
      if (active < 0 && (p.q || "") === q) active = i;
    });
    sel.value = active >= 0 ? String(active) : "";
  }
  document.addEventListener("DOMContentLoaded", init);
  // Re-run init (not just sync): an HTMX page swap replaces the filter bar,
  // so the preset controls must be re-created, then matched to the new URL.
  if (window.htmx) document.addEventListener("htmx:after:swap", function () { init(); });
  init();
  window.syncFilterPresets = sync;
})();
