/* Student banner strip: rotate latest banners, dismiss for the session. */
(function () {
  var KEY = "site-banner-dismissed";
  var timer = null;
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  function show(box, index) {
    var items = box.querySelectorAll("[data-banner-item]");
    var dots = box.querySelectorAll("[data-banner-dot]");
    items.forEach(function (el, i) {
      el.classList.toggle("is-hidden", i !== index);
      el.classList.toggle("is-enter", i === index);
    });
    dots.forEach(function (el, i) {
      el.classList.toggle("is-active", i === index);
    });
    box.setAttribute("data-index", String(index));
  }
  function bind(box) {
    if (!box || box.dataset.bannerBound) return;
    box.dataset.bannerBound = "1";
    var items = box.querySelectorAll("[data-banner-item]");
    if (!items.length) return;
    try {
      if (window.sessionStorage && sessionStorage.getItem(KEY)) {
        box.hidden = true;
        return;
      }
    } catch (e) { /* storage unavailable: keep banner visible */ }
    show(box, 0);
    var close = box.querySelector("#student-banner-close");
    if (close) close.addEventListener("click", function () {
      stop();
      box.hidden = true;
      try { sessionStorage.setItem(KEY, "1"); } catch (e) { /* ignore */ }
    });
    if (items.length > 1 && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      stop();
      timer = setInterval(function () {
        if (box.hidden || document.hidden) return;
        var next = (parseInt(box.getAttribute("data-index") || "0", 10) + 1) % items.length;
        show(box, next);
      }, 6000);
    }
  }
  function init(root) {
    (root || document).querySelectorAll("#student-banner").forEach(bind);
  }
  document.addEventListener("DOMContentLoaded", function () { init(document); });
  document.addEventListener("htmx:afterSwap", function (ev) { init(ev.target || document); });
})();
