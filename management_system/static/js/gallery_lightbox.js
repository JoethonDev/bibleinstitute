/* Graduation gallery lightbox: smooth fade/slide, keyboard, swipe, video support. */
(function () {
  function collectCells() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-gallery-open]"));
  }
  function currentIndex(cells) {
    var box = document.getElementById("gallery-lightbox");
    return parseInt(box ? (box.getAttribute("data-index") || "0") : "0", 10) || 0;
  }
  function render(dir) {
    var cells = collectCells();
    if (!cells.length) return;
    var box = document.getElementById("gallery-lightbox");
    var media = document.getElementById("gallery-lightbox-media");
    var link = document.getElementById("gallery-lightbox-download");
    if (!box || !media) return;
    var idx = ((currentIndex(cells) % cells.length) + cells.length) % cells.length;
    box.setAttribute("data-index", String(idx));
    var btn = cells[idx];
    var kind = btn.getAttribute("data-kind");
    var src = btn.getAttribute("data-display");
    var download = btn.getAttribute("data-download") || src;
    media.classList.remove("is-enter");
    void media.offsetWidth;
    media.innerHTML = "";
    var node;
    if (kind === "video") {
      node = document.createElement("video");
      node.src = src;
      node.controls = true;
      node.playsInline = true;
      node.preload = "metadata";
    } else {
      node = document.createElement("img");
      node.src = src;
      node.alt = "";
      node.decoding = "async";
    }
    node.className = "gg-lightbox-node";
    media.appendChild(node);
    media.classList.add("is-enter");
    if (dir) media.setAttribute("data-dir", dir); else media.removeAttribute("data-dir");
    if (link) link.href = download;
  }
  function openAt(idx) {
    var box = document.getElementById("gallery-lightbox");
    if (!box) return;
    box.setAttribute("data-index", String(idx));
    box.hidden = false;
    document.body.classList.add("gg-lock");
    render("");
    var closeBtn = box.querySelector("[data-gallery-close]");
    if (closeBtn) closeBtn.focus({preventScroll: true});
  }
  function close() {
    var box = document.getElementById("gallery-lightbox");
    if (!box) return;
    box.hidden = true;
    document.body.classList.remove("gg-lock");
    var media = document.getElementById("gallery-lightbox-media");
    if (media) media.innerHTML = "";
  }
  function step(delta) {
    var cells = collectCells();
    var box = document.getElementById("gallery-lightbox");
    if (!box || box.hidden) return;
    box.setAttribute("data-index", String(currentIndex(cells) + delta));
    render(delta > 0 ? "next" : "prev");
  }
  function bind(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-gallery-open]").forEach(function (btn) {
      if (btn.dataset.galleryBound) return;
      btn.dataset.galleryBound = "1";
      btn.addEventListener("click", function () {
        var cells = collectCells();
        openAt(cells.indexOf(btn));
      });
    });
    var box = document.getElementById("gallery-lightbox");
    if (box && !box.dataset.galleryBound) {
      box.dataset.galleryBound = "1";
      box.querySelectorAll("[data-gallery-close]").forEach(function (el) {
        el.addEventListener("click", close);
      });
      var prev = box.querySelector("[data-gallery-prev]");
      var next = box.querySelector("[data-gallery-next]");
      if (prev) prev.addEventListener("click", function () { step(-1); });
      if (next) next.addEventListener("click", function () { step(1); });
      document.addEventListener("keydown", function (ev) {
        if (box.hidden) return;
        if (ev.key === "Escape") close();
        else if (ev.key === "ArrowRight") step(document.dir === "rtl" ? -1 : 1);
        else if (ev.key === "ArrowLeft") step(document.dir === "rtl" ? 1 : -1);
      });
      var startX = null;
      box.addEventListener("touchstart", function (ev) {
        startX = ev.touches.length ? ev.touches[0].clientX : null;
      }, {passive: true});
      box.addEventListener("touchend", function (ev) {
        if (startX === null) return;
        var dx = ev.changedTouches[0].clientX - startX;
        startX = null;
        if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1);
      }, {passive: true});
    }
  }
  document.addEventListener("DOMContentLoaded", function () { bind(document); });
  document.addEventListener("htmx:afterSwap", function (ev) { bind(ev.target || document); });
  window.galleryLightbox = {openAt: openAt, close: close, step: step};
})();
