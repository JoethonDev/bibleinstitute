/* Browser Web Push opt-in; permission is requested only from the Enable click. */
(function () {
  "use strict";
  const roots = Array.from(document.querySelectorAll("[data-push-root]"));
  if (!roots.length) return;
  const PROMPT_DISMISSED_KEY = "pwa-push-prompt-dismissed";

  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const T = isArabic ? {
    enabled: "الإشعارات مفعّلة على هذا الجهاز.",
    unsupported: "هذا المتصفح لا يدعم إشعارات المتصفح. افتح الموقع في متصفح حديث أو أضفه إلى الشاشة الرئيسية.",
    blocked: "الإشعارات محظورة في إعدادات المتصفح. اسمح بالإشعارات لهذا الموقع ثم أعد المحاولة.",
    readFailed: "تعذر قراءة حالة الإشعارات.",
    denied: "لم يُمنح إذن الإشعارات.",
    enableFailed: "تعذر تفعيل الإشعارات.",
    disabled: "الإشعارات معطّلة على هذا الجهاز.",
    disableFailed: "تعذر إيقاف الإشعارات."
  } : {
    enabled: "Notifications are enabled on this device.",
    unsupported: "This browser does not support notifications. Use a modern browser or add the site to your home screen.",
    blocked: "Notifications are blocked in browser settings. Allow notifications for this site, then try again.",
    readFailed: "Could not read notification status.",
    denied: "Notification permission was not granted.",
    enableFailed: "Could not enable notifications.",
    disabled: "Notifications are disabled on this device.",
    disableFailed: "Could not disable notifications."
  };

  function fromB64(value) {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function supported() {
    return Boolean(window.Notification && "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext);
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, Object.assign({ credentials: "same-origin" }, options || {}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.error && data.error.message) || "Request failed.");
    return data;
  }

  function initRoot(root) {
    const enable = root.querySelector("[data-push-enable]");
    const disable = root.querySelector("[data-push-disable]");
    const status = root.querySelector("[data-push-status]");
    const closeButton = root.querySelector("[data-push-close]");
    const isPrompt = root.hasAttribute("data-push-prompt");
    const dialog = isPrompt ? root : null;
    const vapid = root.dataset.vapidPublicKey || "";

    function setStatus(message, error) {
      if (!status) return;
      status.textContent = message || "";
      status.style.color = error ? "#9a2f2f" : "";
    }

    function openPrompt() {
      if (!dialog) return;
      document.documentElement.classList.add("pwa-push-open");
      if (typeof dialog.showModal === "function") {
        if (!dialog.open) dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
    }

    function closePrompt() {
      document.documentElement.classList.remove("pwa-push-open");
      if (!dialog) return;
      if (typeof dialog.close === "function" && dialog.open) dialog.close();
      else dialog.removeAttribute("open");
    }

    function show(active) {
      if (enable) enable.hidden = active;
      if (disable) disable.hidden = !active;
      if (active) {
        setStatus(T.enabled, false);
        closePrompt();
      }
    }

    async function load() {
      if (!supported()) {
        if (enable) enable.disabled = true;
        setStatus(T.unsupported, true);
        openPrompt();
        return;
      }
      if (Notification.permission === "denied") {
        if (enable) enable.disabled = true;
        setStatus(T.blocked, true);
        openPrompt();
        return;
      }
      try {
        const data = await fetchJson(root.dataset.statusUrl);
        if (data.active) {
          const registration = await navigator.serviceWorker.ready;
          const subscription = await registration.pushManager.getSubscription();
          if (subscription) {
            show(true);
          } else {
            show(false);
            openPrompt();
          }
        } else {
          show(false);
          openPrompt();
        }
      } catch (error) {
        setStatus(T.readFailed, true);
        openPrompt();
      }
    }

    if (enable) {
      enable.addEventListener("click", async () => {
        if (!supported()) return;
        enable.disabled = true;
        try {
          const permission = await Notification.requestPermission();
          if (permission !== "granted") {
            setStatus(permission === "denied" ? T.blocked : T.denied, true);
            enable.disabled = permission === "denied";
            return;
          }
          const registration = await navigator.serviceWorker.ready;
          let subscription = await registration.pushManager.getSubscription();
          if (!subscription) {
            subscription = await registration.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: fromB64(vapid),
            });
          }
          await fetchJson(root.dataset.subscribeUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: JSON.stringify({
              subscription: subscription.toJSON(),
              language: (document.documentElement.lang || "ar").slice(0, 2),
            }),
          });
          show(true);
          enable.disabled = false;
        } catch (error) {
          setStatus(error.message || T.enableFailed, true);
          enable.disabled = false;
        }
      });
    }

    if (disable) {
      disable.addEventListener("click", async () => {
        disable.disabled = true;
        try {
          const registration = await navigator.serviceWorker.ready;
          const subscription = await registration.pushManager.getSubscription();
          if (subscription) await subscription.unsubscribe();
          await fetchJson(root.dataset.unsubscribeUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
            body: "{}",
          });
          show(false);
          setStatus(T.disabled, false);
        } catch (error) {
          setStatus(error.message || T.disableFailed, true);
        }
        disable.disabled = false;
      });
    }

    if (closeButton) {
      closeButton.addEventListener("click", closePrompt);
    }
    if (dialog) {
      dialog.addEventListener("close", () => {
        document.documentElement.classList.remove("pwa-push-open");
        try {
          sessionStorage.setItem(PROMPT_DISMISSED_KEY, "1");
        } catch (error) {
          // Private mode or storage disabled: the prompt can reappear.
        }
      });
    }

    if (isPrompt) {
      let dismissed = false;
      try {
        dismissed = sessionStorage.getItem(PROMPT_DISMISSED_KEY) === "1";
      } catch (error) {
        dismissed = false;
      }
      if (dismissed) {
        closePrompt();
        return;
      }
    }

    load();
  }

  roots.forEach(initRoot);
})();
