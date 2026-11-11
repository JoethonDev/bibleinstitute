/* Browser Web Push opt-in; permission is requested only from the Enable click. */
(function () {
  "use strict";
  const root = document.querySelector("[data-push-root]");
  if (!root) return;
  const enable = root.querySelector("[data-push-enable]");
  const disable = root.querySelector("[data-push-disable]");
  const status = root.querySelector("[data-push-status]");
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const vapid = root.dataset.vapidPublicKey || "";

  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const T = isArabic ? {
    enabled: "الإشعارات مفعّلة على هذا الجهاز.",
    unsupported: "هذا المتصفح لا يدعم إشعارات المتصفح.",
    blocked: "الإشعارات محظورة في إعدادات المتصفح.",
    readFailed: "تعذر قراءة حالة الإشعارات.",
    denied: "لم يُمنح إذن الإشعارات.",
    enableFailed: "تعذر تفعيل الإشعارات.",
    disabled: "الإشعارات معطّلة على هذا الجهاز.",
    disableFailed: "تعذر إيقاف الإشعارات."
  } : {
    enabled: "Notifications are enabled on this device.",
    unsupported: "This browser does not support browser notifications.",
    blocked: "Notifications are blocked in browser settings.",
    readFailed: "Could not read notification status.",
    denied: "Notification permission was not granted.",
    enableFailed: "Could not enable notifications.",
    disabled: "Notifications are disabled on this device.",
    disableFailed: "Could not disable notifications."
  };
  function setStatus(message, error) {
    if (!status) return;
    status.textContent = message || "";
    status.style.color = error ? "#9a2f2f" : "";
  }

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

  function show(active) {
    if (enable) enable.hidden = active;
    if (disable) disable.hidden = !active;
    if (active) setStatus(T.enabled, false);
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, Object.assign({ credentials: "same-origin" }, options || {}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.error && data.error.message) || "Request failed.");
    return data;
  }

  async function load() {
    if (!supported()) {
      if (enable) enable.disabled = true;
      setStatus(T.unsupported, true);
      return;
    }
    if (Notification.permission === "denied") {
      if (enable) enable.disabled = true;
      setStatus(T.blocked, true);
      return;
    }
    try {
      const data = await fetchJson(root.dataset.statusUrl);
      if (data.active) {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();
        show(Boolean(subscription));
        if (!subscription && data.active) show(false);
      } else {
        show(false);
      }
    } catch (error) {
      setStatus(T.readFailed, true);
    }
  }

  if (enable) {
    enable.addEventListener("click", async () => {
      if (!supported()) return;
      enable.disabled = true;
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          setStatus(T.denied, true);
          enable.disabled = false;
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

  load();
})();
