{% load static %}/* Bible Institute student PWA service worker. */
const PWA_VERSION = "{{ pwa_version|escapejs }}";
const SHELL_CACHE = `bi-pwa-shell-${PWA_VERSION}`;
const OFFLINE_FALLBACK = "{{ offline_fallback|escapejs }}";
const OFFLINE_SHELLS = {
  ar: "{{ offline_shell_ar|escapejs }}",
  en: "{{ offline_shell_en|escapejs }}",
};
const PRECACHE_URLS = [
  OFFLINE_SHELLS.ar,
  OFFLINE_SHELLS.en,
  OFFLINE_FALLBACK,
  "{% static 'pwa/pwa.css' %}?v={{ pwa_version|urlencode }}",
  "{% static 'pwa/offline-library.js' %}?v={{ pwa_version|urlencode }}",
  "{% static 'pwa/offline.html' %}",
  "{% static 'vendor/pdfjs/3.11.174/pdf.min.js' %}?v={{ pwa_version|urlencode }}",
  "{% static 'vendor/pdfjs/3.11.174/pdf.worker.min.js' %}?v={{ pwa_version|urlencode }}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitDingbats.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitFixed.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitFixedBold.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitFixedBoldItalic.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitFixedItalic.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitSerif.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitSerifBold.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitSerifBoldItalic.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitSerifItalic.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/FoxitSymbol.pfb' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/LiberationSans-Bold.ttf' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/LiberationSans-BoldItalic.ttf' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/LiberationSans-Italic.ttf' %}",
  "{% static 'vendor/pdfjs/3.11.174/standard_fonts/LiberationSans-Regular.ttf' %}",
  "{% static 'pwa/icons/icon-192.png' %}",
  "{% static 'pwa/icons/icon-512.png' %}",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      Promise.all(PRECACHE_URLS.map((url) => cache.add(url)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith("bi-pwa-shell-") && key !== SHELL_CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

function shellFor(request) {
  const path = new URL(request.url).pathname;
  const language = path.startsWith("/ar/") ? "ar" : "en";
  return OFFLINE_SHELLS[language];
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    if (url.pathname.includes("/offline-library/") || url.pathname.includes("/pwa/offline-shell/")) {
      event.respondWith(
        caches.match(shellFor(request)).then((cached) =>
          cached || fetch(request).catch(() => caches.match(OFFLINE_FALLBACK))
        )
      );
      return;
    }
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_FALLBACK))
    );
    return;
  }

  // Static assets are cache-first: the offline library must open a saved PDF
  // with no network, so the vendored pdf.js reader is served from this cache
  // (precached at install, refreshed on demand).
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        });
      })
    );
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = {};
  }
  const title = typeof payload.title === "string" && payload.title ? payload.title : "Bible Institute";
  const body = typeof payload.body === "string" && payload.body ? payload.body : "";
  const url = typeof payload.url === "string" && payload.url.startsWith("/") ? payload.url : "/notifications/";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "{% static 'pwa/icons/icon-192.png' %}",
      badge: "{% static 'pwa/icons/icon-192.png' %}",
      tag: payload.notification_id ? `notification-${payload.notification_id}` : "student-notification",
      data: { url },
      renotify: false,
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data && event.notification.data.url
    ? new URL(event.notification.data.url, self.location.origin)
    : new URL("/notifications/", self.location.origin);
  if (target.origin !== self.location.origin) return;
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) {
          client.navigate(target.href);
          return client.focus();
        }
      }
      return clients.openWindow(target.href);
    })
  );
});
