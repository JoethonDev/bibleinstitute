(function () {
  "use strict";

  function closeNotificationPanel(root) {
    var button = root && root.querySelector("#notification-bell-button");
    var panel = root && root.querySelector("#notification-panel");
    if (!button || !panel) return;
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
  }

  document.addEventListener("click", function (event) {
    var closeButton = event.target.closest("[data-alert-dismiss]");
    if (closeButton) {
      var alert = closeButton.closest("[data-alert-id]");
      if (alert) alert.remove();
    }

    var groupButton = event.target.closest("[data-collapse-trigger]");
    if (groupButton) {
      var target = document.getElementById(groupButton.getAttribute("aria-controls"));
      if (target) {
        var isOpen = groupButton.getAttribute("aria-expanded") === "true";
        groupButton.setAttribute("aria-expanded", String(!isOpen));
        target.hidden = isOpen;
      }
    }

    var tabButton = event.target.closest("[data-profile-tab]");
    if (tabButton) {
      var profileRoot = tabButton.closest("[data-profile-tabs]");
      if (profileRoot) {
        profileRoot.querySelectorAll("[data-profile-tab]").forEach(function (tab) {
          var active = tab === tabButton;
          tab.setAttribute("aria-selected", String(active));
          tab.classList.toggle("is-active", active);
        });
        profileRoot.querySelectorAll("[data-profile-panel]").forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-profile-panel") !== tabButton.getAttribute("data-profile-tab");
        });
      }
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
      closeNotificationPanel(root);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNotificationPanel(document.getElementById("notification-root"));
  });
})();
