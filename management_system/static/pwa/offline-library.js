/* Encrypted local audio/PDF library. No private bytes ever enter Cache Storage unencrypted. */
(function () {
  "use strict";

  const BODY = document.body;
  const VIEWER = document.querySelector("[data-offline-viewer]");
  const VIEWER_TITLE = document.querySelector("[data-viewer-title]");
  const VIEWER_CONTENT = document.querySelector("[data-viewer-content]");
  const VIEWER_CLOSE = document.querySelector("[data-viewer-close]");
  let viewerUrl = "";
  const ACCOUNT = BODY.dataset.accountId || localStorage.getItem("pwa-account") || "";
  const MAX_BYTES = Number(BODY.dataset.maxBytes || 0);
  const VALIDATE_URL = BODY.dataset.validateUrl || "";
  const DB_NAME = "bi-pwa";
  const DB_VERSION = 1;
  const ITEMS = "items";
  const KEYS = "keys";
  const META = "meta";
  const CACHE_PREFIX = "bi-pwa-media-";
  const UNLOCK_PREFIX = "pwa-unlock:";
  function cookie(name) {
    const prefix = `${name}=`;
    return document.cookie.split(";").map((part) => part.trim()).filter((part) => part.startsWith(prefix)).map((part) => decodeURIComponent(part.slice(prefix.length)))[0] || "";
  }
  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || cookie("csrftoken");
  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const T = isArabic ? {
    saved: "حُفظ للاستخدام دون اتصال.",
    unlocked: "تم فتح التنزيلات لهذه الجلسة.",
    cancelled: "أُلغي الفتح أو أنه غير متاح.",
    missing: "الملف المحفوظ لم يعد متاحاً على هذا الجهاز.",
    unsupported: "هذا الجهاز لا يدعم التخزين الآمن دون اتصال.",
    tooLarge: "هذا الملف كبير جداً للحفظ دون اتصال.",
    openAccount: "افتح هذه الصفحة من حساب الطالب.",
    unavailable: "الوسائط دون اتصال غير متاحة في هذا المتصفح.",
    supported: "مدعوم",
    notSupported: "غير مدعوم في هذا المتصفح",
    close: "إغلاق",
    openFailed: "تعذر عرض الملف. أعد فتح المكتبة وحاول مرة أخرى.",
    audio: "صوت",
    pdf: "PDF",
    prevPage: "الصفحة السابقة",
    nextPage: "الصفحة التالية",
    pageOf: "صفحة {page} من {total}",
    loading: "جارٍ التحميل..."
  } : {
    saved: "Saved for offline use.",
    unlocked: "Downloads unlocked for this session.",
    cancelled: "Unlock was cancelled or is unavailable.",
    missing: "The saved file is no longer available on this device.",
    unsupported: "This device does not support secure offline storage.",
    tooLarge: "This file is too large to save offline.",
    openAccount: "Open this page from your student account.",
    unavailable: "Offline media is unavailable on this browser.",
    supported: "Supported",
    notSupported: "Not supported on this browser",
    close: "Close",
    openFailed: "Could not display the file. Reopen the library and try again.",
    audio: "Audio",
    pdf: "PDF",
    prevPage: "Previous page",
    nextPage: "Next page",
    pageOf: "Page {page} of {total}",
    loading: "Loading..."
  };
  const PDF_DEFAULT_WORKER = "/static/vendor/pdfjs/3.11.174/pdf.worker.min.js";
  let pdfState = null;
  let dbPromise = null;
  let unlocked = false;

  function accountKey() {
    if (!ACCOUNT) throw new Error("account");
    return String(ACCOUNT);
  }

  function b64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function fromB64(value) {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(ITEMS)) {
          const store = db.createObjectStore(ITEMS, { keyPath: "id" });
          store.createIndex("account", "account", { unique: false });
        }
        if (!db.objectStoreNames.contains(KEYS)) db.createObjectStore(KEYS);
        if (!db.objectStoreNames.contains(META)) db.createObjectStore(META);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("idb"));
    });
    return dbPromise;
  }

  async function tx(store, mode, operation) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(store, mode);
      const request = operation(transaction.objectStore(store));
      transaction.oncomplete = () => resolve(request && request.result);
      transaction.onerror = () => reject(transaction.error || new Error("idb"));
      transaction.onabort = () => reject(transaction.error || new Error("idb"));
    });
  }

  async function getKey(create) {
    const keyName = `account:${accountKey()}`;
    let key = await tx(KEYS, "readonly", (store) => store.get(keyName));
    if (key) return key;
    if (!create) throw new Error("locked");
    key = await crypto.subtle.generateKey(
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
    await tx(KEYS, "readwrite", (store) => store.put(key, keyName));
    return key;
  }

  async function getLocalCredential(create) {
    const key = `credential:${accountKey()}`;
    let value = await tx(META, "readonly", (store) => store.get(key));
    if (value || !create) return value;
    if (!window.PublicKeyCredential || !navigator.credentials || !window.isSecureContext) {
      throw new Error("unsupported");
    }
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    let credential;
    try {
      credential = await navigator.credentials.create({
        publicKey: {
          challenge,
          rp: { name: "Bible Institute" },
          user: {
            id: crypto.getRandomValues(new Uint8Array(16)),
            name: `offline-${accountKey()}`,
            displayName: "Bible Institute offline library",
          },
          pubKeyCredParams: [{ type: "public-key", alg: -7 }],
          authenticatorSelection: {
            authenticatorAttachment: "platform",
            residentKey: "required",
            userVerification: "required",
          },
          timeout: 60000,
          attestation: "none",
        },
      });
    } catch (error) {
      throw new Error("unsupported");
    }
    if (!credential) throw new Error("unsupported");
    value = b64(credential.rawId);
    await tx(META, "readwrite", (store) => store.put(value, key));
    return value;
  }

  async function unlock(force) {
    const account = accountKey();
    if (unlocked || (!force && sessionStorage.getItem(UNLOCK_PREFIX + account) === "1")) {
      unlocked = true;
      return true;
    }
    const credentialId = await getLocalCredential(true);
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    let assertion;
    try {
      assertion = await navigator.credentials.get({
        publicKey: {
          challenge,
          allowCredentials: [{ id: fromB64(credentialId), type: "public-key" }],
          userVerification: "required",
          timeout: 60000,
        },
      });
    } catch (error) {
      throw new Error("cancelled");
    }
    if (!assertion) throw new Error("cancelled");
    unlocked = true;
    sessionStorage.setItem(UNLOCK_PREFIX + account, "1");
    return true;
  }

  function isUnlocked() {
    return unlocked || (ACCOUNT && sessionStorage.getItem(UNLOCK_PREFIX + ACCOUNT) === "1");
  }

  function fmtBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function setStatus(message, isError) {
    const node = document.querySelector("[data-offline-status]");
    if (!node) return;
    node.textContent = message || "";
    node.style.color = isError ? "var(--pwa-danger)" : "";
  }

  async function listItems() {
    if (!ACCOUNT) return [];
    return (await tx(ITEMS, "readonly", (store) => store.index("account").getAll(accountKey()))) || [];
  }

  async function removeItem(item) {
    const cache = await caches.open(CACHE_PREFIX + accountKey());
    if (item.cacheKey) await cache.delete(item.cacheKey);
    await tx(ITEMS, "readwrite", (store) => store.delete(item.id));
    await render();
  }

  async function validateOnline(items) {
    if (!navigator.onLine || !VALIDATE_URL || !items.length) return items;
    try {
      const response = await fetch(VALIDATE_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({
          items: items.map((item) => ({
            offering_id: item.offeringId,
            lesson_id: item.lessonId,
          })),
        }),
      });
      if (!response.ok) return items;
      const data = await response.json();
      const allowed = new Set((data.allowed || []).map((item) => `${item.offering_id}:${item.lesson_id}`));
      const kept = [];
      for (const item of items) {
        if (allowed.has(`${item.offeringId}:${item.lessonId}`)) kept.push(item);
        else {
          const cache = await caches.open(CACHE_PREFIX + accountKey());
          if (item.cacheKey) await cache.delete(item.cacheKey);
          await tx(ITEMS, "readwrite", (store) => store.delete(item.id));
        }
      }
      return kept;
    } catch (error) {
      return items;
    }
  }

  async function render() {
    const items = await listItems();
    const list = document.querySelector("[data-offline-list]");
    const empty = document.querySelector("[data-offline-empty]");
    const locked = document.querySelector("[data-offline-locked]");
    const template = document.getElementById("offline-item-template");
    if (!list || !template) return;
    list.innerHTML = "";
    const valid = await validateOnline(items);
    valid.sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
    if (locked) locked.hidden = !valid.length || isUnlocked();
    valid.forEach((item) => {
      const fragment = template.content.cloneNode(true);
      const article = fragment.querySelector(".pwa-item");
      article.dataset.itemId = item.id;
      fragment.querySelector("[data-item-icon]").textContent = item.type === "audio" ? "AUDIO" : "PDF";
      fragment.querySelector("[data-item-title]").textContent = item.title || "Lesson media";
      fragment.querySelector("[data-item-meta]").textContent = `${item.type === "audio" ? "Audio" : "PDF"} - ${fmtBytes(item.size)}`;
      fragment.querySelector("[data-item-open]").addEventListener("click", () => openItem(item));
      fragment.querySelector("[data-item-remove]").addEventListener("click", () => removeItem(item));
      list.appendChild(fragment);
    });
    if (empty) empty.hidden = valid.length > 0;
    const count = document.querySelector("[data-offline-count]");
    if (count) count.textContent = String(valid.length);
    updateUsage(valid);
  }

  async function updateUsage(items) {
    const usage = document.querySelector("[data-offline-usage]");
    if (!usage) return;
    const local = items.reduce((total, item) => total + Number(item.size || 0), 0);
    if (!navigator.storage || !navigator.storage.estimate) {
      usage.textContent = fmtBytes(local);
      return;
    }
    try {
      const estimate = await navigator.storage.estimate();
      usage.textContent = `${fmtBytes(local)} / ${fmtBytes(estimate.quota || 0)}`;
    } catch (error) {
      usage.textContent = fmtBytes(local);
    }
  }

  function closeViewer() {
    if (viewerUrl) {
      URL.revokeObjectURL(viewerUrl);
      viewerUrl = "";
    }
    const state = pdfState;
    pdfState = null;
    if (state) {
      try {
        if (state.task) state.task.cancel();
      } catch (error) {
        // A finished render cannot be cancelled; nothing to release.
      }
      try {
        if (state.loading) state.loading.destroy();
        else if (state.doc) state.doc.destroy();
      } catch (error) {
        // pdf.js releases its worker on its own if destroy already ran.
      }
    }
    if (VIEWER_CONTENT) VIEWER_CONTENT.replaceChildren();
    if (VIEWER) VIEWER.hidden = true;
  }

  function showAudio(url, title) {
    if (!VIEWER || !VIEWER_CONTENT) return;
    closeViewer();
    viewerUrl = url;
    VIEWER.hidden = false;
    if (VIEWER_TITLE) VIEWER_TITLE.textContent = title || T.audio;
    const audio = document.createElement("audio");
    audio.src = url;
    audio.controls = true;
    audio.autoplay = true;
    audio.preload = "auto";
    audio.playsInline = true;
    audio.addEventListener("error", () => setStatus(T.openFailed, true));
    VIEWER_CONTENT.replaceChildren(audio);
    VIEWER.scrollIntoView({ behavior: "smooth", block: "start" });
    audio.load();
    const playback = audio.play();
    if (playback && typeof playback.catch === "function") playback.catch(() => {});
  }

  function pdfLibrary() {
    const library = window.pdfjsLib;
    if (!library) return null;
    const assets = window.pdfViewerAssets || {};
    if (library.GlobalWorkerOptions) {
      if (assets.workerSrc) library.GlobalWorkerOptions.workerSrc = assets.workerSrc;
      else if (!library.GlobalWorkerOptions.workerSrc) {
        library.GlobalWorkerOptions.workerSrc = PDF_DEFAULT_WORKER;
      }
    }
    return library;
  }

  /* Render the decrypted PDF on a canvas inside the app. No blob URL and no new
     tab: the bytes exist only as a decrypted buffer handed to pdf.js, so the
     document cannot be handed to the device PDF viewer or another app. */
  function showPdf(bytes, title) {
    if (!VIEWER || !VIEWER_CONTENT) return;
    const library = pdfLibrary();
    closeViewer();
    VIEWER.hidden = false;
    if (VIEWER_TITLE) VIEWER_TITLE.textContent = title || T.pdf;
    VIEWER.scrollIntoView({ behavior: "smooth", block: "start" });
    if (!library) {
      setStatus(T.openFailed, true);
      return;
    }
    const bar = document.createElement("div");
    bar.className = "pwa-pdf-bar";
    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "pwa-btn pwa-btn--small";
    previous.textContent = T.prevPage;
    previous.disabled = true;
    const counter = document.createElement("span");
    counter.className = "pwa-pdf-page";
    counter.setAttribute("aria-live", "polite");
    counter.textContent = T.loading;
    const next = document.createElement("button");
    next.type = "button";
    next.className = "pwa-btn pwa-btn--small";
    next.textContent = T.nextPage;
    next.disabled = true;
    bar.append(previous, counter, next);
    const stage = document.createElement("div");
    stage.className = "pwa-pdf-stage";
    const canvas = document.createElement("canvas");
    canvas.className = "pwa-pdf-canvas";
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", title || T.pdf);
    stage.appendChild(canvas);
    const wrapper = document.createElement("div");
    wrapper.className = "pwa-pdf";
    wrapper.append(bar, stage);
    VIEWER_CONTENT.replaceChildren(wrapper);

    const state = { loading: null, doc: null, page: 1, total: 0, task: null, generation: 0 };
    pdfState = state;
    const sync = () => {
      if (pdfState !== state) return;
      previous.disabled = state.page <= 1;
      next.disabled = state.page >= state.total;
      counter.textContent = T.pageOf
        .replace("{page}", String(state.page))
        .replace("{total}", String(state.total));
    };
    const draw = async () => {
      if (pdfState !== state || !state.doc) return;
      state.generation += 1;
      const generation = state.generation;
      try {
        if (state.task) state.task.cancel();
      } catch (error) {
        // The previous page already finished rendering.
      }
      state.task = null;
      try {
        const page = await state.doc.getPage(state.page);
        if (pdfState !== state || generation !== state.generation) return;
        const base = page.getViewport({ scale: 1 });
        const available = Math.max(220, (stage.clientWidth || 320) - 2);
        const scale = Math.min(available / base.width, 2.4);
        const viewport = page.getViewport({ scale });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
        canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        state.task = page.render({
          canvasContext: canvas.getContext("2d", { alpha: false }),
          viewport,
          transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
        });
        await state.task.promise;
        if (pdfState !== state) return;
        setStatus("", false);
      } catch (error) {
        if (pdfState !== state || (error && error.name === "RenderingCancelledException")) return;
        setStatus(T.openFailed, true);
      }
    };
    previous.addEventListener("click", () => {
      if (state.page <= 1) return;
      state.page -= 1;
      sync();
      draw();
    });
    next.addEventListener("click", () => {
      if (state.page >= state.total) return;
      state.page += 1;
      sync();
      draw();
    });
    const assets = window.pdfViewerAssets || {};
    const options = { data: bytes, isEvalSupported: false };
    if (assets.cMapUrl) {
      options.cMapUrl = assets.cMapUrl;
      options.cMapPacked = true;
    }
    if (assets.standardFontDataUrl) options.standardFontDataUrl = assets.standardFontDataUrl;
    state.loading = library.getDocument(options);
    state.loading.promise.then((doc) => {
      if (pdfState !== state) {
        try {
          doc.destroy();
        } catch (error) {
          // The viewer was closed while the document was loading.
        }
        return;
      }
      state.doc = doc;
      state.total = Math.max(1, doc.numPages || 1);
      state.page = 1;
      sync();
      draw();
    }).catch(() => {
      if (pdfState !== state) return;
      counter.textContent = "";
      setStatus(T.openFailed, true);
    });
  }

  async function openItem(item) {
    try {
      await unlock();
      const key = await getKey(false);
      const cache = await caches.open(CACHE_PREFIX + accountKey());
      const response = await cache.match(item.cacheKey);
      if (!response) throw new Error("missing");
      const cipher = await response.arrayBuffer();
      const plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: fromB64(item.iv) },
        key,
        cipher
      );
      const mime = item.mime || (item.type === "audio" ? "audio/mpeg" : "application/pdf");
      if (item.type === "audio") {
        showAudio(URL.createObjectURL(new Blob([plain], { type: mime })), item.title);
      } else {
        showPdf(new Uint8Array(plain), item.title);
      }
    } catch (error) {
      setStatus(error.message === "missing" ? T.missing : T.cancelled, true);
    }
  }

  async function saveMedia(options) {
    const account = accountKey();
    try {
      await unlock();
      const key = await getKey(true);
      const response = await fetch(options.url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": CSRF, "Accept": "application/octet-stream" },
      });
      if (!response.ok) {
        let message = "Could not save this file.";
        try {
          const data = await response.json();
          message = (data.error && data.error.message) || message;
        } catch (error) {
          message = response.status === 403 ? "Saving offline media is disabled." : message;
        }
        throw new Error(message);
      }
      const declaration = Number(response.headers.get("Content-Length") || 0);
      if (MAX_BYTES > 0 && declaration && declaration > MAX_BYTES) throw new Error(T.tooLarge);
      let plain;
      if (response.body && declaration && options.onProgress) {
        const reader = response.body.getReader();
        const chunks = [];
        let received = 0;
        while (true) {
          const step = await reader.read();
          if (step.done) break;
          chunks.push(step.value);
          received += step.value.byteLength;
          options.onProgress(Math.min(100, Math.round((received / declaration) * 100)));
        }
        plain = new Uint8Array(received);
        let offset = 0;
        chunks.forEach((chunk) => {
          plain.set(chunk, offset);
          offset += chunk.byteLength;
        });
        plain = plain.buffer;
      } else {
        plain = await response.arrayBuffer();
      }
      if (MAX_BYTES > 0 && plain.byteLength > MAX_BYTES) throw new Error(T.tooLarge);
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain);
      const id = `${options.offeringId}:${options.lessonId}:${options.fileIndex}`;
      const cacheKey = `/__pwa_media__/${account}/${id}`;
      const cache = await caches.open(CACHE_PREFIX + account);
      await cache.put(cacheKey, new Response(cipher, { headers: { "Content-Type": "application/octet-stream" } }));
      const item = {
        id,
        account,
        title: options.title || "Lesson media",
        type: options.type === "audio" ? "audio" : "pdf",
        mime: options.type === "audio" ? "audio/mpeg" : "application/pdf",
        size: plain.byteLength,
        offeringId: Number(options.offeringId),
        lessonId: Number(options.lessonId),
        fileIndex: Number(options.fileIndex),
        cacheKey,
        iv: b64(iv),
        version: response.headers.get("X-PWA-Content-Version") || "",
        savedAt: Date.now(),
      };
      await tx(ITEMS, "readwrite", (store) => store.put(item));
      setStatus(T.saved, false);
      await render();
      return true;
    } catch (error) {
      setStatus(error.message === "unsupported" ? T.unsupported : error.message, true);
      throw error;
    }
  }

  async function clearAccount(accountOverride) {
    const account = String(accountOverride || accountKey());
    const items = (await tx(ITEMS, "readonly", (store) => store.index("account").getAll(account))) || [];
    const cache = await caches.open(CACHE_PREFIX + account);
    await Promise.all(items.map((item) => cache.delete(item.cacheKey)));
    await tx(ITEMS, "readwrite", (store) => {
      const index = store.index("account");
      const request = index.openCursor(IDBKeyRange.only(account));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        store.delete(cursor.primaryKey);
        cursor.continue();
      };
    });
    await tx(KEYS, "readwrite", (store) => store.delete(`account:${account}`));
    await tx(META, "readwrite", (store) => store.delete(`credential:${account}`));
    sessionStorage.removeItem(UNLOCK_PREFIX + account);
  }

  async function init() {
    if (!document.querySelector("[data-offline-list]") && !document.querySelector("[data-pwa-save]")) return;
    const support = document.querySelector("[data-offline-support]");
    const supported = Boolean(window.crypto && crypto.subtle && window.indexedDB && window.caches && window.PublicKeyCredential);
    if (support) {
      support.textContent = supported ? T.supported : T.notSupported;
      support.style.color = supported ? "var(--pwa-success)" : "var(--pwa-danger)";
    }
    if (!supported || !ACCOUNT) {
      if (document.querySelector("[data-offline-unlock]")) document.querySelector("[data-offline-unlock]").disabled = true;
      setStatus(supported ? T.openAccount : T.unavailable, true);
      return;
    }
    document.querySelectorAll("[data-offline-unlock]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await unlock(true);
          setStatus(T.unlocked, false);
          await render();
        } catch (error) {
          setStatus(T.cancelled, true);
        }
      });
    });
    document.querySelectorAll("[data-offline-refresh]").forEach((button) => button.addEventListener("click", render));
    if (navigator.onLine) {
      try {
        const credential = await getLocalCredential(false);
        if (credential && isUnlocked()) await render();
        else await render();
      } catch (error) {
        await render();
      }
    } else {
      await render();
    }
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  }

  window.PWALibrary = {
    save: saveMedia,
    clear: clearAccount,
    clearFor: clearAccount,
    list: listItems,
    unlock,
  };

  if (VIEWER_CLOSE) VIEWER_CLOSE.addEventListener("click", closeViewer);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
