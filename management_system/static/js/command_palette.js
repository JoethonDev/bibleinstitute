// ponytail: Ctrl+K jump palette; one debounced JSON GET, no framework.
// v2: localStorage recents, g+letter and "u query" scope filters, Telegram group.
// v3: pinned/favorite users (star), always on top, matched first on search.
(function () {
  var input = null, box = null, timer = null, aborter = null;
  var items = [], active = 0, typeFilter = null, gArmedAt = 0;
  var RECENT_KEY = "cmd-palette-recent";
  var PIN_KEY = "cmd-palette-pins";
  var MAX_PINS = 10;
  var PREFIXES = { u: "user", o: "offering", l: "lesson", q: "quiz", m: "media", t: "telegram" };
  function t(s) { try { return (typeof gettext === "function") ? gettext(s) : s; } catch (e) { return s; } }
  function ensure() {
    input = document.getElementById("command-palette-input");
    box = document.getElementById("command-palette-results");
    return input && box;
  }
  function loadList(key) {
    try {
      var list = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }
  function loadRecents() { return loadList(RECENT_KEY).slice(0, 8); }
  function loadPins() { return loadList(PIN_KEY).slice(0, MAX_PINS); }
  function storeRecent(r) {
    if (!r || !r.url) return;
    try {
      var list = loadRecents().filter(function (x) { return x.url !== r.url; });
      list.unshift({ type: r.type, label: r.label, sub: r.sub || "", url: r.url });
      localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8)));
    } catch (e) {}
  }
  function togglePin(r) {
    if (!r || r.type !== "user" || !r.url) return;
    try {
      var pins = loadPins().filter(function (x) { return x.url !== r.url; });
      if (pins.length === loadPins().length) {
        pins.unshift({ type: r.type, label: r.label, sub: r.sub || "", url: r.url });
        pins = pins.slice(0, MAX_PINS);
      }
      localStorage.setItem(PIN_KEY, JSON.stringify(pins));
    } catch (e) {}
    rerender();
  }
  function pinUrls() {
    var set = {};
    loadPins().forEach(function (p) { set[p.url] = true; });
    return set;
  }
  function labelForType(type) {
    var map = { user: "Users", offering: "Courses", lesson: "Lessons", quiz: "Quizzes", media: "Media", telegram: "Telegram" };
    return t(map[type] || type);
  }
  function open() {
    if (!ensure()) return;
    var m = document.getElementById("commandPaletteModal");
    if (window.bootstrap && m) bootstrap.Modal.getOrCreateInstance(m).show();
    typeFilter = null;
    gArmedAt = 0;
    input.value = "";
    try { input.setAttribute("placeholder", t("Search")); } catch (e) {}
    setTimeout(function () { input.focus(); input.select(); renderRecents(); }, 50);
  }
  function headerEl(text) {
    var h = document.createElement("div");
    h.className = "list-group-item list-group-item-secondary py-1 small fw-bold";
    h.textContent = text;
    return h;
  }
  function starBtn(r, pinned) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-sm btn-link p-0 flex-shrink-0 " + (pinned ? "text-warning" : "text-muted");
    b.textContent = pinned ? "★" : "☆";
    var label = pinned ? t("Unpin") : t("Pin");
    b.setAttribute("aria-label", label);
    b.title = label;
    b.addEventListener("click", function (ev) { ev.preventDefault(); ev.stopPropagation(); togglePin(r); });
    return b;
  }
  function rowEl(r, i, pinned) {
    var wrap = document.createElement("div");
    wrap.className = "list-group-item d-flex align-items-center gap-2" + (i === active ? " active" : "");
    wrap.setAttribute("role", "option");
    if (i === active) wrap.setAttribute("aria-selected", "true");
    var a = document.createElement("a");
    a.href = r.url;
    a.className = "flex-grow-1 text-reset text-decoration-none";
    var b = document.createElement("div");
    b.className = "d-flex justify-content-between gap-2";
    var s1 = document.createElement("span");
    s1.textContent = "[" + r.type + "] " + r.label;
    var s2 = document.createElement("small");
    s2.className = "text-muted text-truncate";
    s2.textContent = r.sub || "";
    b.appendChild(s1); b.appendChild(s2);
    a.appendChild(b);
    a.addEventListener("click", function () { storeRecent(r); });
    wrap.appendChild(a);
    if (r.type === "user") wrap.appendChild(starBtn(r, pinned));
    return wrap;
  }
  function paint(groups) {
    // groups: [{header: string|null, rows: [{r, pinned}]}] in display order.
    if (!box) return;
    box.innerHTML = "";
    items = [];
    active = 0;
    var flat = 0;
    groups.forEach(function (g) {
      if (!g.rows.length) return;
      if (g.header) box.appendChild(headerEl(g.header));
      g.rows.forEach(function (row) {
        items.push(row.r);
        box.appendChild(rowEl(row.r, flat, row.pinned));
        flat += 1;
      });
    });
    if (!items.length) {
      var d = document.createElement("div");
      d.className = "list-group-item text-muted";
      d.textContent = t("No results found.");
      box.appendChild(d);
    }
  }
  function matchesQuery(r, q) {
    var hay = ((r.label || "") + " " + (r.sub || "")).toLowerCase();
    return hay.indexOf(q.toLowerCase()) !== -1;
  }
  function renderRecents() {
    if (!box) return;
    var pinned = pinUrls();
    var pins = loadPins();
    var recents = loadRecents().filter(function (r) { return !pinned[r.url]; });
    paint([
      { header: pins.length ? t("Pinned") : null, rows: pins.map(function (r) { return { r: r, pinned: true }; }) },
      { header: recents.length ? t("Recent") : null, rows: recents.map(function (r) { return { r: r, pinned: false }; }) },
    ]);
    if (!loadPins().length && !recents.length) {
      box.innerHTML = "";
      var d = document.createElement("div");
      d.className = "list-group-item text-muted";
      d.textContent = t("Type to search. Prefix with u, o, l, q, m, t to scope.");
      box.appendChild(d);
    }
  }
  function parseQuery() {
    var raw = input.value.trim();
    var q = raw, scoped = typeFilter;
    if (raw.length >= 3 && raw[1] === " " && PREFIXES[raw[0].toLowerCase()]) {
      scoped = PREFIXES[raw[0].toLowerCase()];
      q = raw.slice(2).trim();
    }
    return { q: q, scoped: scoped };
  }
  function render(serverRows, q) {
    var pinned = pinUrls();
    var matchedPins = loadPins().filter(function (r) { return matchesQuery(r, q); });
    var byType = {};
    (serverRows || []).forEach(function (r) {
      if (pinned[r.url]) return;
      if (!byType[r.type]) byType[r.type] = [];
      byType[r.type].push({ r: r, pinned: false });
    });
    var groups = [];
    if (matchedPins.length) {
      groups.push({ header: t("Pinned"), rows: matchedPins.map(function (r) { return { r: r, pinned: true }; }) });
    }
    Object.keys(byType).forEach(function (type) {
      groups.push({ header: labelForType(type), rows: byType[type] });
    });
    paint(groups);
  }
  function setLoading() {
    if (!box) return;
    box.innerHTML = "";
    var d = document.createElement("div");
    d.className = "list-group-item text-muted";
    d.textContent = t("Searching…");
    box.appendChild(d);
  }
  function search() {
    if (!ensure()) return;
    var parsed = parseQuery();
    if (parsed.q.length < 2) { if (aborter) aborter.abort(); renderRecents(); return; }
    if (aborter) aborter.abort();
    aborter = ("AbortController" in window) ? new AbortController() : null;
    setLoading();
    var url = input.dataset.searchUrl + "?q=" + encodeURIComponent(parsed.q) + (parsed.scoped ? "&type=" + encodeURIComponent(parsed.scoped) : "");
    var opts = { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" };
    if (aborter) opts.signal = aborter.signal;
    fetch(url, opts)
      .then(function (r) { return r.json(); })
      .then(function (j) { items = []; active = 0; render((j && j.results) || [], parsed.q); })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        items = [];
        if (!box) return;
        box.innerHTML = "";
        var e = document.createElement("div");
        e.className = "list-group-item text-danger";
        e.textContent = t("Search failed. Try again.");
        box.appendChild(e);
      });
  }
  function rerender() {
    if (!ensure()) return;
    if (parseQuery().q.length < 2) renderRecents();
    else search();
  }
  function pick(i) {
    var r = items[i];
    if (r) { storeRecent(r); window.location.href = r.url; }
  }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); open(); return; }
    if (!ensure()) return;
    var modal = document.getElementById("commandPaletteModal");
    var shown = modal && modal.classList.contains("show");
    if (!shown) return;
    // vim-style "g u" scope shortcut while the query is empty.
    if (document.activeElement === input && !input.value && e.key.toLowerCase() === "g" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      gArmedAt = Date.now();
      input.setAttribute("placeholder", "g …");
      return;
    }
    if (gArmedAt) {
      var fresh = Date.now() - gArmedAt < 1200;
      gArmedAt = 0;
      input.setAttribute("placeholder", t("Search"));
      if (fresh && PREFIXES[e.key.toLowerCase()] && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        typeFilter = PREFIXES[e.key.toLowerCase()];
        input.value = "";
        input.setAttribute("placeholder", t("Search") + " [" + typeFilter + "]");
        input.focus();
        renderRecents();
        return;
      }
      if (fresh && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== "Escape") {
        // Not a scope letter: replay the held "g" plus this key as text.
        e.preventDefault();
        input.value = "g" + e.key;
        search();
        return;
      }
      if (e.key === "Escape") return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(items.length - 1, active + 1); rerenderKeep(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); rerenderKeep(); }
    else if (e.key === "Enter" && items[active]) { pick(active); }
    else if (e.key === "Escape" && typeFilter) { typeFilter = null; input.setAttribute("placeholder", t("Search")); search(); }
  });
  function rerenderKeep() {
    // Move highlight without refetching: toggle classes on current rows.
    if (!box || !items.length) return;
    var rows = box.querySelectorAll('[role="option"]');
    for (var i = 0; i < rows.length; i++) {
      if (i === active) { rows[i].classList.add("active"); rows[i].setAttribute("aria-selected", "true"); }
      else { rows[i].classList.remove("active"); rows[i].removeAttribute("aria-selected"); }
    }
  }
  document.addEventListener("input", function (e) {
    if (e.target && e.target.id === "command-palette-input") {
      clearTimeout(timer);
      timer = setTimeout(search, 200);
    }
  });
  window.openCommandPalette = open;
})();
