/* Student PWA registration, install prompt and save-button binding. */
(function () {
  "use strict";
  const body = document.body;
  const enabled = body.dataset.pwaEnabled === "true";
  const account = body.dataset.pwaAccount || "";
  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const text = isArabic
    ? { saving: "جارٍ الحفظ...", saved: "محفوظ دون اتصال", unavailable: "المكتبة غير متاحة", failed: "تعذر حفظ هذا الملف." }
    : { saving: "Saving...", saved: "Saved offline", unavailable: "Offline library unavailable", failed: "Could not save this file." };
  let installPrompt = null;

  if (enabled) {
    const previousAccount = localStorage.getItem("pwa-account");
    if (previousAccount && account && previousAccount !== account) {
      window.addEventListener("load", () => {
        if (window.PWALibrary) window.PWALibrary.clearFor(previousAccount).catch(() => {});
      });
    }
    if (account) localStorage.setItem("pwa-account", account);
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {});
      });
    }
    window.addEventListener("beforeinstallprompt", (event) => {
      event.preventDefault();
      installPrompt = event;
      document.querySelectorAll("[data-pwa-install]").forEach((button) => {
        button.hidden = false;
      });
    });
    window.addEventListener("appinstalled", () => {
      installPrompt = null;
      document.querySelectorAll("[data-pwa-install]").forEach((button) => {
        button.hidden = true;
      });
    });
  }

  document.addEventListener("click", async (event) => {
    const installButton = event.target.closest("[data-pwa-install]");
    if (installButton) {
      if (!installPrompt) {
        installButton.textContent = isArabic ? "افتح قائمة المتصفح واختر تثبيت التطبيق" : "Use your browser menu to install";
        return;
      }
      installPrompt.prompt();
      await installPrompt.userChoice.catch(() => null);
      installPrompt = null;
      installButton.hidden = true;
      return;
    }
    const saveButton = event.target.closest("[data-pwa-save]");
    if (!saveButton) return;
    if (!window.PWALibrary) {
      saveButton.textContent = text.unavailable;
      return;
    }
    const original = saveButton.innerHTML;
    saveButton.disabled = true;
    saveButton.textContent = text.saving;
    try {
      await window.PWALibrary.save({
        url: saveButton.dataset.url,
        title: saveButton.dataset.title,
        type: saveButton.dataset.type,
        offeringId: saveButton.dataset.offeringId,
        lessonId: saveButton.dataset.lessonId,
        fileIndex: saveButton.dataset.fileIndex,
        onProgress: (percent) => {
          saveButton.textContent = `${percent}%`;
        },
      });
      saveButton.classList.add("is-saved");
      saveButton.textContent = text.saved;
    } catch (error) {
      saveButton.innerHTML = original;
      saveButton.disabled = false;
      if (window.alert) window.alert(error.message || text.failed);
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-pwa-logout]");
    if (!form || form.dataset.pwaClearing === "1") return;
    if (!window.PWALibrary) return;
    event.preventDefault();
    form.dataset.pwaClearing = "1";
    try {
      await window.PWALibrary.clear();
    } catch (error) {
      // A logout must still complete if local storage is unavailable.
    }
    localStorage.removeItem("pwa-account");
    form.submit();
  });
})();
