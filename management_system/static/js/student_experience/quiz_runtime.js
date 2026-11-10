(() => {
    "use strict";

    const form = document.getElementById("quiz-form");
    if (!form || !form.dataset.snapshotUrl) return;

    const timerNode = document.querySelector("#timer span");
    const statusNode = document.getElementById("snapshot-status");
    const snapshotScript = document.getElementById("quiz-answer-snapshot");
    const snapshotUrl = form.dataset.snapshotUrl;
    const deadline = Number(form.dataset.deadline || 0);
    const serverTimeAtRender = Number(form.dataset.serverNow || 0);
    const monotonicTimeAtRender = performance.now();
    let snapshot = { answers: {}, revision: 0 };
    try {
        snapshot = JSON.parse(snapshotScript?.textContent || "{}");
    } catch (error) {
        snapshot = { answers: {}, revision: 0 };
    }
    if (!snapshot || typeof snapshot !== "object") snapshot = { answers: {}, revision: 0 };
    if (!snapshot.answers || typeof snapshot.answers !== "object") snapshot.answers = {};
    if (!Number.isSafeInteger(snapshot.revision) || snapshot.revision < 0) snapshot.revision = 0;

    let snapshotRevision = snapshot.revision;
    let snapshotDirty = false;
    let snapshotSaving = false;
    let snapshotBlocked = false;
    let snapshotTimer = null;
    let pendingSnapshot = null;
    let timerInterval = null;

    function statusText(text) {
        if (statusNode) statusNode.textContent = text;
    }

    function serializeStructuredAnswer(questionCard) {
        const structured = questionCard.querySelector("[data-structured-answer]");
        const type = structured?.dataset.structuredType;
        if (type === "order_events") {
            return JSON.stringify(Array.from(questionCard.querySelectorAll("[data-order-item-row]"))
                .map((row) => row.dataset.orderLabel || "")
                .filter(Boolean));
        }
        if (type === "match_related") {
            const answer = {};
            questionCard.querySelectorAll("[data-match-select]").forEach((select) => {
                const leftLabel = select.dataset.matchLeftLabel || "";
                if (leftLabel && select.value) answer[leftLabel] = select.value;
            });
            return JSON.stringify(answer);
        }
        return "";
    }

    function applyStructuredAnswer(questionCard, value) {
        const structured = questionCard.querySelector("[data-structured-answer]");
        const type = structured?.dataset.structuredType;
        if (!type || !value) return;
        try {
            const answer = JSON.parse(value);
            if (type === "order_events" && Array.isArray(answer)) {
                const list = questionCard.querySelector("[data-order-item-list]");
                if (!list) return;
                const rows = Array.from(list.querySelectorAll("[data-order-item-row]"));
                const ordered = [];
                answer.forEach((label) => {
                    const row = rows.find((item) => item.dataset.orderLabel === label);
                    if (row && !ordered.includes(row)) ordered.push(row);
                });
                rows.forEach((row) => { if (!ordered.includes(row)) ordered.push(row); });
                const fragment = document.createDocumentFragment();
                ordered.forEach((row) => fragment.appendChild(row));
                list.appendChild(fragment);
            } else if (type === "match_related" && answer && typeof answer === "object") {
                Object.entries(answer).forEach(([leftLabel, rightLabel]) => {
                    const select = Array.from(questionCard.querySelectorAll("[data-match-select]"))
                        .find((item) => item.dataset.matchLeftLabel === leftLabel);
                    if (select) select.value = rightLabel;
                });
            }
        } catch (error) {
            return;
        }
    }

    function restoreSnapshotAnswers() {
        Object.entries(snapshot.answers).forEach(([questionId, answer]) => {
            const idInput = Array.from(form.querySelectorAll('input[type="hidden"][name$="[id]"]'))
                .find((input) => input.value === questionId);
            const questionCard = idInput?.closest("[data-question-card]");
            if (!questionCard) return;
            const structured = questionCard.querySelector("[data-structured-answer]");
            if (structured) {
                structured.value = answer || "";
                applyStructuredAnswer(questionCard, answer || "");
                return;
            }
            const radios = questionCard.querySelectorAll('.answers-section input[type="radio"]');
            if (radios.length) {
                radios.forEach((field) => { field.checked = field.value == answer; });
                return;
            }
            const field = questionCard.querySelector(
                '.answers-section input:not([type="hidden"]), .answers-section textarea, .answers-section select'
            );
            if (field) field.value = answer || "";
        });
    }

    function collectAnswers() {
        const answers = {};
        form.querySelectorAll("[data-question-card]").forEach((questionCard) => {
            const id = questionCard.querySelector('input[name$="[id]"]')?.value;
            const structured = questionCard.querySelector("[data-structured-answer]");
            const field = structured || questionCard.querySelector(
                'input[type="radio"]:checked, textarea, .answers-section input:not([type="radio"]):not([type="hidden"]), .answers-section select'
            );
            if (id) answers[id] = field?.value || "";
        });
        return answers;
    }

    function announceAnswerChange(questionCard) {
        form.dispatchEvent(new CustomEvent("studentquiz:answerchanged", {
            detail: { questionCard },
        }));
    }

    function markDirty(questionCard) {
        if (!questionCard || !form.contains(questionCard)) return;
        const structured = questionCard.querySelector("[data-structured-answer]");
        if (structured) structured.value = serializeStructuredAnswer(questionCard);
        snapshotDirty = true;
        statusText(form.dataset.savingText || "");
        window.clearTimeout(snapshotTimer);
        snapshotTimer = window.setTimeout(persistSnapshot, 1000);
        announceAnswerChange(questionCard);
    }

    async function persistSnapshot() {
        if (!snapshotUrl || (!snapshotDirty && !pendingSnapshot) || snapshotSaving || snapshotBlocked) return;
        snapshotSaving = true;
        if (!pendingSnapshot) {
            pendingSnapshot = JSON.stringify({ revision: snapshotRevision, answers: collectAnswers() });
            snapshotDirty = false;
        }
        const body = pendingSnapshot;
        const controller = new AbortController();
        const requestTimeout = window.setTimeout(() => controller.abort(), 15000);
        const csrf = form.querySelector('[name="csrfmiddlewaretoken"]')?.value || "";
        try {
            const response = await fetch(snapshotUrl, {
                method: "POST",
                keepalive: new Blob([body]).size <= 60000,
                signal: controller.signal,
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
                body,
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (response.status === 409 || response.status === 403) snapshotBlocked = true;
                if (response.status === 400) {
                    pendingSnapshot = null;
                    snapshotDirty = true;
                }
                throw new Error(data.error || form.dataset.saveErrorText || "");
            }
            if (!Number.isSafeInteger(data.revision) || data.revision < 0 || typeof data.submitted !== "boolean") {
                throw new Error(form.dataset.saveErrorText || "");
            }
            snapshotRevision = data.revision;
            pendingSnapshot = null;
            if (data.submitted) snapshotBlocked = true;
            statusText(snapshotDirty
                ? (form.dataset.savingText || "")
                : (form.dataset.savedText || ""));
        } catch (error) {
            statusText(error.name === "AbortError"
                ? (form.dataset.saveErrorText || "")
                : (error.message || form.dataset.saveErrorText || ""));
        } finally {
            window.clearTimeout(requestTimeout);
            snapshotSaving = false;
            if ((snapshotDirty || pendingSnapshot) && !snapshotBlocked) {
                snapshotTimer = window.setTimeout(persistSnapshot, 2000);
            }
        }
    }

    function formatClock(totalSeconds) {
        const remaining = Math.max(0, Math.floor(totalSeconds));
        const hours = Math.floor(remaining / 3600);
        const minutes = Math.floor((remaining % 3600) / 60);
        const seconds = remaining % 60;
        if (hours) {
            return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
        }
        return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function formatVerbose(totalSeconds) {
        let remaining = Math.max(0, Math.floor(totalSeconds));
        const days = Math.floor(remaining / 86400);
        remaining %= 86400;
        const hours = Math.floor(remaining / 3600);
        remaining %= 3600;
        const minutes = Math.floor(remaining / 60);
        const seconds = remaining % 60;
        const parts = [];
        if (days) parts.push(`${days} ${form.dataset.timerDays && days !== 1 ? form.dataset.timerDays : form.dataset.timerDay}`);
        if (hours) parts.push(`${hours} ${hours === 1 ? form.dataset.timerHour : form.dataset.timerHours}`);
        if (minutes) parts.push(`${minutes} ${minutes === 1 ? form.dataset.timerMinute : form.dataset.timerMinutes}`);
        if (!parts.length || seconds) {
            parts.push(`${seconds} ${seconds === 1 ? form.dataset.timerSecond : form.dataset.timerSeconds}`);
        }
        if (parts.length === 1) return parts[0];
        const lastPart = parts.pop();
        return `${parts.join(form.dataset.timerListSeparator || ", ")}${form.dataset.timerAnd || " and "}${lastPart}`;
    }

    function countDown() {
        const currentEpoch = Math.floor(
            (serverTimeAtRender + performance.now() - monotonicTimeAtRender) / 1000
        );
        const remaining = deadline - currentEpoch;
        if (remaining <= 0) {
            window.clearInterval(timerInterval);
            if (timerNode) {
                timerNode.textContent = form.dataset.timerFormat === "verbose"
                    ? formatVerbose(0)
                    : formatClock(0);
            }
            form.submit();
            return;
        }
        if (timerNode) {
            timerNode.textContent = form.dataset.timerFormat === "verbose"
                ? formatVerbose(remaining)
                : formatClock(remaining);
        }
    }

    restoreSnapshotAnswers();
    form.addEventListener("input", (event) => {
        const questionCard = event.target.closest?.("[data-question-card]");
        if (questionCard) markDirty(questionCard);
    });
    form.addEventListener("change", (event) => {
        const questionCard = event.target.closest?.("[data-question-card]");
        if (questionCard) markDirty(questionCard);
    });
    form.addEventListener("submit", persistSnapshot);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) persistSnapshot();
    });
    window.addEventListener("pagehide", persistSnapshot);

    window.StudentQuizRuntime = Object.freeze({ markDirty, persistSnapshot });
    if (deadline > 0 && timerNode) {
        countDown();
        timerInterval = window.setInterval(countDown, 1000);
    }
})();
