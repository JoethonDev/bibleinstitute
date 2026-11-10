(function () {
  "use strict";

  var root = document.getElementById("notification-root");
  if (!root) return;
  var popupRoot = root.querySelector("#notification-popups");
  var lastSeenKey = "lms-notifications-last-seen";
  var lastId = 0;
  var initialized = false;
  var timer = null;
  var delay = 30000;
  var inFlight = false;

  try {
    lastId = Number(window.localStorage.getItem(lastSeenKey)) || 0;
    initialized = lastId > 0;
  } catch (_) {}

  function showBadge(value) {
    var badge = document.getElementById("notification-badge");
    if (!badge) return;
    var count = Number(value) || 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count < 1;
  }

  function addPopup(item) {
    if (!popupRoot || !item || !item.title) return;
    var card = document.createElement("a");
    card.className = "sn-notification-toast";
    card.href = item.target_url || item.open_url || "#";
    var title = document.createElement("strong");
    title.textContent = item.title;
    var body = document.createElement("span");
    body.textContent = item.body || "";
    card.append(title, body);
    popupRoot.appendChild(card);
    window.setTimeout(function () { card.remove(); }, 6000);
  }

  function poll() {
    if (document.hidden || !root.isConnected || inFlight) {
      schedule(delay);
      return;
    }
    inFlight = true;
    fetch(root.dataset.liveUrl + "?after=" + encodeURIComponent(lastId), {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) throw new Error("Notification status unavailable");
      return response.json();
    }).then(function (data) {
      showBadge(data.unread_count);
      var responseMaxId = Number(data.max_id) || 0;
      if (initialized) (data.items || []).forEach(addPopup);
      lastId = Math.max(lastId, responseMaxId);
      initialized = true;
      try { window.localStorage.setItem(lastSeenKey, String(lastId)); } catch (_) {}
      delay = 30000;
      schedule(delay);
    }).catch(function () {
      delay = Math.min(delay * 2, 120000);
      schedule(delay);
    }).finally(function () {
      inFlight = false;
    });
  }

  function schedule(wait) {
    window.clearTimeout(timer);
    timer = window.setTimeout(poll, wait);
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });

  function csrfToken(form) {
    var field = form.querySelector('input[name="csrfmiddlewaretoken"]');
    if (field) return field.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function updateHistoryState() {
    var history = document.getElementById("notification-history");
    if (!history) return;
    var items = document.querySelectorAll("#notification-history-items [data-notification-id]");
    var empty = document.getElementById("notification-empty");
    if (empty) empty.hidden = items.length > 0;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var openForm = form.closest("[data-notification-open]");
    var readForm = form.closest("[data-notification-read]");
    var readAllForm = form.closest("[data-notification-read-all]");
    if (!openForm && !readForm && !readAllForm) return;
    event.preventDefault();
    var headers = { "Accept": "application/json", "X-CSRFToken": csrfToken(form) };
    fetch(form.action, { method: "POST", credentials: "same-origin", headers: headers })
      .then(function (response) {
        if (!response.ok) throw new Error("Notification action failed");
        return response.json();
      })
      .then(function (data) {
        showBadge(data.unread_count);
        if (openForm) {
          if (data.redirect) window.location.assign(data.redirect);
          return;
        }
        if (readAllForm) {
          document.querySelectorAll("[data-notification-id].is-unread").forEach(function (item) {
            item.classList.remove("is-unread");
            var dot = item.querySelector(".sn-unread-dot");
            if (dot) dot.remove();
            item.querySelectorAll("[data-notification-read]").forEach(function (readButton) { readButton.remove(); });
          });
          if (historyIsUnread()) {
            var list = document.getElementById("notification-history-items");
            if (list) list.replaceChildren();
          }
          updateHistoryState();
          return;
        }
        var card = readForm.closest("[data-notification-id]");
        if (card) {
          if (historyIsUnread()) card.remove();
          else {
            card.classList.remove("is-unread");
            var dot = card.querySelector(".sn-unread-dot");
            if (dot) dot.remove();
            readForm.remove();
          }
        }
        updateHistoryState();
      })
      .catch(function () {});
  });

  function historyIsUnread() {
    var history = document.getElementById("notification-history");
    return !!history && history.dataset.filter === "unread";
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-notification-load-more]");
    if (!button) return;
    var history = document.getElementById("notification-history");
    var items = document.getElementById("notification-history-items");
    var status = document.querySelector("[data-notification-status]");
    var nextUrl = history && history.dataset.nextUrl;
    if (!nextUrl || !items || button.disabled) return;
    button.disabled = true;
    if (status) status.textContent = button.dataset.loadingLabel || "";
    fetch(nextUrl, { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (response) {
        if (!response.ok) throw new Error("Notification history unavailable");
        return response.text();
      })
      .then(function (html) {
        var fragment = new DOMParser().parseFromString(html, "text/html").querySelector(".sn-notif-history-fragment");
        if (!fragment) throw new Error("Notification history page missing");
        var known = new Set(Array.from(items.querySelectorAll("[data-notification-id]"), function (row) { return row.dataset.notificationId; }));
        Array.from(fragment.children).forEach(function (row) {
          var id = row.dataset && row.dataset.notificationId;
          if (id && !known.has(id)) items.appendChild(document.importNode(row, true));
        });
        history.dataset.nextUrl = fragment.dataset.nextUrl || "";
        if (!history.dataset.nextUrl) button.remove();
        else button.disabled = false;
        if (status) status.textContent = "";
        updateHistoryState();
      })
      .catch(function () {
        button.disabled = false;
        if (status) status.textContent = "";
      });
  });

  poll();
})();
