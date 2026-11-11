(function () {
  "use strict";

  var ALERT_STORAGE_PREFIX = "sn-alert-dismissed:";

  function storageGet(key) {
    try {
      return window.sessionStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function storageSet(key) {
    try {
      window.sessionStorage.setItem(key, "1");
    } catch (error) {
      // Storage can be unavailable in private modes; dismissal then lasts for
      // this page view only, which is the safe fallback.
    }
  }

  function alertStorageKey(alert) {
    var key = alert.getAttribute("data-alert-key");
    return key ? ALERT_STORAGE_PREFIX + key : "";
  }

  function restoreDismissedAlerts(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-alert-id]").forEach(function (alert) {
      var key = alertStorageKey(alert);
      if (key && storageGet(key)) alert.remove();
    });
  }

  function closeNotificationPanel(root, restoreFocus) {
    var button = root && root.querySelector("#notification-bell-button");
    var panel = root && root.querySelector("#notification-panel");
    if (!button || !panel) return;
    var wasOpen = !panel.hidden;
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (wasOpen && restoreFocus) button.focus();
  }

  function reinitAlpine(target) {
    if (!target || !(target instanceof Element)) return;
    if (!window.Alpine || typeof window.Alpine.initTree !== "function") return;
    try {
      window.Alpine.initTree(target);
    } catch (error) {
      // Alpine owns its own scope; a detached or already-initialized subtree
      // must never break the rest of the page.
    }
  }

  function activateProfileTab(tabButton) {
    var profileRoot = tabButton.closest(".sn-profile-card") || tabButton.closest("[data-profile-tabs], .sn-tabs");
    if (!profileRoot) return;
    profileRoot.querySelectorAll("[data-profile-tab]").forEach(function (tab) {
      var active = tab === tabButton;
      tab.setAttribute("aria-selected", String(active));
      tab.setAttribute("tabindex", active ? "0" : "-1");
      tab.classList.toggle("is-active", active);
    });
    profileRoot.querySelectorAll("[data-profile-panel], [data-tab-panel]").forEach(function (panel) {
      var name = panel.getAttribute("data-profile-panel") || panel.getAttribute("data-tab-panel");
      panel.hidden = name !== tabButton.getAttribute("data-profile-tab");
    });
  }

  document.addEventListener("click", function (event) {
    var closeButton = event.target.closest("[data-alert-dismiss]");
    if (closeButton) {
      var alert = closeButton.closest("[data-alert-id]");
      if (alert) {
        var key = alertStorageKey(alert);
        if (key) storageSet(key);
        alert.remove();
      }
    }

    var groupButton = event.target.closest("[data-collapse-trigger]");
    if (groupButton) {
      var target = document.getElementById(groupButton.getAttribute("aria-controls"));
      if (target) {
        var isOpen = groupButton.getAttribute("aria-expanded") === "true";
        groupButton.setAttribute("aria-expanded", String(!isOpen));
        target.hidden = isOpen;
        var section = groupButton.closest("[data-year-section], .sn-year-section");
        if (section) section.classList.toggle("is-collapsed", isOpen);
      }
    }

    var tabButton = event.target.closest("[data-profile-tab]");
    if (tabButton) {
      activateProfileTab(tabButton);
    }

    var dayButton = event.target.closest("[data-calendar-select]");
    if (dayButton) {
      var calendarRoot = dayButton.closest("[data-calendar-root]");
      if (calendarRoot) {
        calendarRoot.querySelectorAll("[data-calendar-select]").forEach(function (button) {
          button.classList.toggle("is-selected", button === dayButton);
          button.setAttribute("aria-pressed", String(button === dayButton));
        });
        calendarRoot.querySelectorAll("[data-calendar-panel]").forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-calendar-panel") !== dayButton.getAttribute("data-calendar-select");
        });
      }
    }

    var bell = event.target.closest("#notification-bell-button");
    var root = document.getElementById("notification-root");
    var panel = root && root.querySelector("#notification-panel");
    if (bell && panel) {
      panel.hidden = !panel.hidden;
      bell.setAttribute("aria-expanded", String(!panel.hidden));
    } else if (root && panel && !root.contains(event.target)) {
      closeNotificationPanel(root, false);
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("[data-confirm-message]");
    if (form && !window.confirm(form.getAttribute("data-confirm-message"))) {
      event.preventDefault();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeNotificationPanel(document.getElementById("notification-root"), true);
      return;
    }
    var tab = event.target.closest("[data-profile-tab]");
    if (!tab) return;
    if (["ArrowRight", "ArrowLeft", "Home", "End"].indexOf(event.key) === -1) return;
    var root = tab.closest(".sn-profile-card") || tab.closest("[data-profile-tabs], .sn-tabs");
    if (!root) return;
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-profile-tab]"));
    if (!tabs.length) return;
    var index = tabs.indexOf(tab);
    var rtl = document.dir === "rtl";
    if (event.key === "Home") index = 0;
    else if (event.key === "End") index = tabs.length - 1;
    else if (event.key === "ArrowRight") index = (index + (rtl ? -1 : 1) + tabs.length) % tabs.length;
    else index = (index + (rtl ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    activateProfileTab(tabs[index]);
    tabs[index].focus();
  });

  document.addEventListener("htmx:after:swap", function (event) {
    var ctx = event.detail && event.detail.ctx;
    var target = (ctx && ctx.target) || event.target;
    if (!target || !(target instanceof Element)) return;
    restoreDismissedAlerts(target);
    reinitAlpine(target);
  });

  restoreDismissedAlerts(document);
})();
