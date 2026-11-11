/* In-app support chat: bounded polling, older-message paging, send and delete. */
(function () {
  "use strict";
  const root = document.querySelector("[data-chat-root]");
  if (!root) return;

  const list = root.querySelector("#sn-chat-list");
  const body = root.querySelector(".sn-chat-body");
  const emptyState = root.querySelector("#sn-chat-empty");
  const olderButton = root.querySelector("[data-chat-older]");
  const form = root.querySelector("[data-chat-form]");
  const input = root.querySelector("#sn-chat-input");
  const fileInput = root.querySelector("#sn-chat-file");
  const fileName = root.querySelector("#sn-chat-filename");
  const recordButton = root.querySelector("#sn-chat-record");
  const sendButton = root.querySelector("#sn-chat-send");
  const errorBox = root.querySelector("#sn-chat-error");
  if (!list || !body || !form || !input || !sendButton) return;

  const csrfToken =
    (document.querySelector('meta[name="csrf-token"]') || {}).content ||
    (form.querySelector('input[name="csrfmiddlewaretoken"]') || {}).value ||
    "";
  const POLL_MS = 5000;
  const POLL_BACKOFF_MS = 30000;
  const OLDER_TRIGGER_PX = 80;
  const TEXT = {
    recording: root.dataset.textRecording || "Recording... tap to send",
    record: root.dataset.textRecord || "Record an audio message",
    sending: root.dataset.textSending || "Sending...",
    micBlocked: root.dataset.textMicBlocked || "Microphone access was refused.",
    micMissing: root.dataset.textMicMissing || "This browser cannot record audio.",
    failed: root.dataset.textFailed || "The message could not be sent.",
    upload: root.dataset.textUpload || "Attaching file...",
  };
  const MAX_SECONDS = Math.max(1, parseInt(root.dataset.maxSeconds || "60", 10) || 60);
  const MAX_CHARS = Math.max(1, parseInt(root.dataset.maxChars || "4000", 10) || 4000);
  const attachmentsEnabled = root.dataset.attachments === "1";

  let lastId = parseInt(root.dataset.lastId || "0", 10) || 0;
  let oldestAt = root.dataset.oldestAt || "";
  let oldestId = parseInt(root.dataset.oldestId || "0", 10) || 0;
  let hasOlder = root.dataset.hasOlder === "1";
  let pollTimer = null;
  let pollDelay = POLL_MS;
  let busy = false;
  let loadingOlder = false;
  let mediaRecorder = null;
  let mediaStream = null;
  let recordedChunks = [];
  let recordTimer = null;
  let recordSeconds = 0;

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message || "";
    errorBox.hidden = !message;
  }

  function atBottom() {
    return body.scrollHeight - body.scrollTop - body.clientHeight < 120;
  }

  function scrollToBottom() {
    body.scrollTop = body.scrollHeight;
  }

  function refreshEmptyState() {
    if (!emptyState) return;
    emptyState.hidden = list.children.length > 0;
  }

  function highestId(container) {
    let highest = 0;
    container.querySelectorAll("[data-chat-message]").forEach((node) => {
      const value = parseInt(node.dataset.chatMessage || "0", 10) || 0;
      if (value > highest) highest = value;
    });
    return highest;
  }

  function appendNodes(container) {
    if (!container) return 0;
    const nodes = Array.from(container.querySelectorAll("[data-chat-message]"));
    nodes.forEach((node) => {
      const id = node.dataset.chatMessage;
      if (id && list.querySelector('[data-chat-message="' + id + '"]')) return;
      list.appendChild(node);
    });
    refreshEmptyState();
    return nodes.length;
  }

  function updateOlderState(container) {
    if (!container) {
      if (olderButton) olderButton.hidden = !hasOlder;
      return;
    }
    const nextOldest = container.querySelector("[data-chat-message]");
    hasOlder = container.dataset.hasOlder === "1";
    if (nextOldest) {
      oldestId = parseInt(nextOldest.dataset.chatMessage || "0", 10) || oldestId;
      const time = nextOldest.querySelector("time");
      oldestAt = (time && time.getAttribute("datetime")) || oldestAt;
    }
    if (olderButton) olderButton.hidden = !hasOlder;
  }

  function parseFragment(html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    return template.content;
  }

  async function poll() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(
        root.dataset.pollUrl + "&after=" + encodeURIComponent(lastId),
        { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } }
      );
      if (!response.ok) throw new Error("poll failed");
      const wrapper = parseFragment(await response.text()).querySelector("[data-chat-page]");
      pollDelay = POLL_MS;
      if (!wrapper) return;
      const wasAtBottom = atBottom();
      const added = appendNodes(wrapper);
      const serverLast = parseInt(wrapper.dataset.lastId || "0", 10) || 0;
      if (serverLast > lastId) lastId = serverLast;
      if (added && wasAtBottom) scrollToBottom();
    } catch (error) {
      pollDelay = Math.min(pollDelay * 2, POLL_BACKOFF_MS);
    } finally {
      busy = false;
    }
  }

  function schedulePoll(delay) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(async function () {
      await poll();
      schedulePoll(pollDelay);
    }, Math.max(delay || POLL_MS, POLL_MS));
  }

  async function loadOlder() {
    if (loadingOlder || busy || !hasOlder || !oldestAt || oldestId <= 0) return;
    loadingOlder = true;
    busy = true;
    try {
      const url =
        root.dataset.olderUrl +
        "&before_at=" + encodeURIComponent(oldestAt) +
        "&before_id=" + encodeURIComponent(oldestId);
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
      });
      if (!response.ok) return;
      const wrapper = parseFragment(await response.text()).querySelector("[data-chat-page]");
      if (!wrapper) return;
      const previousHeight = body.scrollHeight;
      const previousTop = body.scrollTop;
      const nodes = Array.from(wrapper.querySelectorAll("[data-chat-message]"));
      const reference = list.firstElementChild;
      nodes.forEach((node) => {
        const id = node.dataset.chatMessage;
        if (id && list.querySelector('[data-chat-message="' + id + '"]')) return;
        list.insertBefore(node, reference);
      });
      updateOlderState(wrapper);
      refreshEmptyState();
      body.scrollTop = previousTop + (body.scrollHeight - previousHeight);
    } finally {
      loadingOlder = false;
      busy = false;
    }
  }

  function clearMedia() {
    if (fileInput) fileInput.value = "";
    if (fileName) {
      fileName.textContent = "";
      fileName.hidden = true;
    }
  }

  function setBusy(state) {
    root.classList.toggle("is-busy", state);
    sendButton.disabled = state;
  }

  async function parseError(response) {
    try {
      const data = await response.json();
      return data && data.error ? data.error : TEXT.failed;
    } catch (error) {
      return TEXT.failed;
    }
  }

  async function send(payload) {
    setBusy(true);
    showError("");
    try {
      const response = await fetch(root.dataset.sendUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrfToken },
        body: payload,
      });
      if (!response.ok) {
        showError(await parseError(response));
        return false;
      }
      const wrapper = parseFragment(await response.text()).querySelector("[data-chat-message]");
      if (wrapper) {
        const wasAtBottom = atBottom();
        const id = wrapper.dataset.chatMessage;
        if (!id || !list.querySelector('[data-chat-message="' + id + '"]')) {
          list.appendChild(wrapper);
        }
        const newest = highestId(list);
        if (newest > lastId) lastId = newest;
        refreshEmptyState();
        if (wasAtBottom) scrollToBottom();
      }
      return true;
    } catch (error) {
      showError(TEXT.failed);
      return false;
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (busy) return;
    const text = input.value.trim();
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!text && !file) {
      showError(TEXT.failed);
      input.focus();
      return;
    }
    if (text.length > MAX_CHARS) {
      showError(TEXT.failed);
      return;
    }
    const payload = new FormData();
    payload.append("message", text);
    payload.append("csrfmiddlewaretoken", csrfToken);
    if (file) payload.append("attachment", file, file.name);
    const label = sendButton.querySelector("span");
    const original = label ? label.textContent : "";
    if (label) label.textContent = TEXT.sending;
    const sent = await send(payload);
    if (label) label.textContent = original;
    if (sent) {
      input.value = "";
      clearMedia();
      input.focus();
      poll();
    }
  });

  list.addEventListener("click", async function (event) {
    const button = event.target.closest("[data-chat-delete] button");
    if (!button) return;
    const formElement = button.closest("[data-chat-delete]");
    const item = button.closest("[data-chat-message]");
    if (!formElement || !item) return;
    event.preventDefault();
    if (item.dataset.deleting === "1") return;
    item.dataset.deleting = "1";
    try {
      const response = await fetch(formElement.getAttribute("action"), {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrfToken },
        body: new URLSearchParams({ csrfmiddlewaretoken: csrfToken }),
      });
      if (response.ok) {
        item.remove();
        refreshEmptyState();
      } else {
        item.dataset.deleting = "0";
        showError(await parseError(response));
      }
    } catch (error) {
      item.dataset.deleting = "0";
      showError(TEXT.failed);
    }
  });

  if (olderButton) {
    olderButton.addEventListener("click", function (event) {
      event.preventDefault();
      loadOlder();
    });
  }

  body.addEventListener("scroll", function () {
    if (body.scrollTop <= OLDER_TRIGGER_PX && hasOlder) loadOlder();
  }, { passive: true });

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      const file = fileInput.files && fileInput.files[0];
      if (!fileName) return;
      fileName.textContent = file ? file.name : "";
      fileName.hidden = !file;
    });
  }

  function stopRecordingStream() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
    if (recordTimer) {
      clearInterval(recordTimer);
      recordTimer = null;
    }
  }

  async function uploadRecording(blob) {
    if (!blob || !blob.size) return;
    const payload = new FormData();
    payload.append("message", "");
    payload.append("csrfmiddlewaretoken", csrfToken);
    payload.append("attachment", blob, "voice.webm");
    if (fileName) {
      fileName.textContent = TEXT.upload;
      fileName.hidden = false;
    }
    const sent = await send(payload);
    if (fileName) {
      fileName.textContent = "";
      fileName.hidden = true;
    }
    if (sent) poll();
  }

  async function startRecording() {
    if (!attachmentsEnabled) return;
    if (!navigator.mediaDevices || typeof window.MediaRecorder === "undefined") {
      showError(TEXT.micMissing);
      return;
    }
    showError("");
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      showError(TEXT.micBlocked);
      return;
    }
    recordedChunks = [];
    try {
      mediaRecorder = new MediaRecorder(mediaStream);
    } catch (error) {
      stopRecordingStream();
      showError(TEXT.micMissing);
      return;
    }
    mediaRecorder.addEventListener("dataavailable", function (event) {
      if (event.data && event.data.size) recordedChunks.push(event.data);
    });
    mediaRecorder.addEventListener("stop", function () {
      const blob = new Blob(recordedChunks, { type: "audio/webm" });
      recordedChunks = [];
      stopRecordingStream();
      mediaRecorder = null;
      if (recordButton) {
        recordButton.classList.remove("is-recording");
        recordButton.title = TEXT.record;
      }
      uploadRecording(blob);
    });
    mediaRecorder.start();
    recordSeconds = 0;
    if (recordButton) {
      recordButton.classList.add("is-recording");
      recordButton.title = TEXT.recording;
    }
    recordTimer = setInterval(function () {
      recordSeconds += 1;
      if (recordSeconds >= MAX_SECONDS) stopRecording();
    }, 1000);
  }

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    } else {
      stopRecordingStream();
    }
  }

  if (recordButton) {
    recordButton.addEventListener("click", function () {
      if (mediaRecorder && mediaRecorder.state === "recording") stopRecording();
      else startRecording();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
      if (mediaRecorder && mediaRecorder.state === "recording") stopRecording();
      return;
    }
    poll().then(function () {
      schedulePoll(pollDelay);
    });
  });

  window.addEventListener("pagehide", function () {
    stopRecordingStream();
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  });

  updateOlderState(null);
  refreshEmptyState();
  scrollToBottom();
  schedulePoll(POLL_MS);
})();
