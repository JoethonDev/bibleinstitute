(() => {
    "use strict";

    const form = document.getElementById("quiz-form");
    if (!form) return;

    const questions = Array.from(form.querySelectorAll("[data-question-card]"));
    const navButtons = Array.from(form.querySelectorAll("[data-qnav]"));
    const progressNode = document.getElementById("current-question");
    const warningNode = document.getElementById("question-warning");
    const dialog = document.getElementById("unansweredQuestionsFallback");
    let current = 1;
    let confirmedSubmit = false;

    function questionIsAnswered(questionCard) {
        const answerSection = questionCard.querySelector(".answers-section");
        if (!answerSection) return false;
        const choices = answerSection.querySelectorAll('input[type="radio"]');
        if (choices.length) return Array.from(choices).some((input) => input.checked);

        const structured = answerSection.querySelector("[data-structured-answer]");
        if (structured?.dataset.structuredType === "order_events") {
            try {
                const answer = JSON.parse(structured.value || "null");
                const itemCount = answerSection.querySelectorAll("[data-order-item-row]").length;
                return itemCount > 0 && Array.isArray(answer) && answer.length === itemCount
                    && answer.every((item) => typeof item === "string" && item.trim());
            } catch (error) {
                return false;
            }
        }
        if (structured?.dataset.structuredType === "match_related") {
            const matches = answerSection.querySelectorAll("[data-match-select]");
            return matches.length > 0 && Array.from(matches).every((select) => Boolean(select.value.trim()));
        }
        const fields = answerSection.querySelectorAll(
            'input:not([type="hidden"]):not([type="radio"]), textarea, select'
        );
        return fields.length > 0 && Array.from(fields).some((field) => Boolean((field.value || "").trim()));
    }

    function updateQuestionUi(questionCard) {
        const answered = questionIsAnswered(questionCard);
        const number = Number(questionCard.dataset.question);
        const nav = navButtons.find((button) => Number(button.dataset.qnav) === number);
        if (nav) nav.classList.toggle("is-answered", answered);
        questionCard.toggleAttribute("data-answered", answered);
        questionCard.querySelectorAll(".sn-option").forEach((label) => {
            label.classList.toggle("is-selected", Boolean(label.querySelector('input[type="radio"]')?.checked));
        });
        if (answered && number === current && warningNode) warningNode.hidden = true;
    }

    function showQuestion(number, checkPrevious) {
        if (!questions.length) return;
        const previous = questions[current - 1];
        const next = Math.max(1, Math.min(questions.length, number));
        if (checkPrevious && previous && next !== current && !questionIsAnswered(previous)) {
            if (warningNode) warningNode.hidden = false;
        } else if (warningNode) {
            warningNode.hidden = true;
        }
        current = next;
        questions.forEach((questionCard) => {
            questionCard.hidden = Number(questionCard.dataset.question) !== current;
        });
        navButtons.forEach((button) => button.classList.toggle("is-current", Number(button.dataset.qnav) === current));
        if (progressNode) progressNode.textContent = String(current);
        form.querySelectorAll("[data-quiz-prev]").forEach((button) => { button.disabled = current === 1; });
        form.querySelectorAll("[data-quiz-next]").forEach((button) => { button.disabled = current === questions.length; });
    }

    function moveOrderItem(button, direction) {
        const row = button.closest("[data-order-item-row]");
        const list = button.closest("[data-order-item-list]");
        if (!row || !list) return;
        if (direction === "up" && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
        else if (direction === "down" && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
        window.StudentQuizRuntime?.markDirty(row.closest("[data-question-card]"));
    }

    function showSubmitDialog() {
        const missing = questions.filter((questionCard) => !questionIsAnswered(questionCard)).length;
        const description = document.getElementById("quiz-submit-description");
        if (description) {
            const unanswered = missing === 1
                ? form.dataset.unansweredSingular
                : (form.dataset.unansweredPlural || "").replace("__count__", String(missing));
            description.textContent = missing
                ? `${unanswered} ${form.dataset.submitWarningText}`
                : form.dataset.submitConfirmText;
        }
        if (dialog?.showModal) dialog.showModal();
        else if (window.confirm(description?.textContent || form.dataset.submitConfirmText)) {
            confirmedSubmit = true;
            form.requestSubmit();
        }
    }

    questions.forEach(updateQuestionUi);
    showQuestion(1, false);
    form.addEventListener("studentquiz:answerchanged", (event) => {
        if (event.detail?.questionCard) updateQuestionUi(event.detail.questionCard);
    });
    form.querySelectorAll('[data-action="move-order-up"], [data-action="move-order-down"]')
        .forEach((button) => button.addEventListener("click", (event) => {
            event.preventDefault();
            moveOrderItem(button, button.dataset.action === "move-order-up" ? "up" : "down");
        }));
    form.querySelectorAll("[data-flag-question]").forEach((button) => {
        button.addEventListener("click", () => {
            const flagged = button.classList.toggle("is-flagged");
            button.setAttribute("aria-pressed", String(flagged));
        });
    });
    form.querySelectorAll("[data-quiz-prev]").forEach((button) => button.addEventListener("click", () => showQuestion(current - 1, true)));
    form.querySelectorAll("[data-quiz-next]").forEach((button) => button.addEventListener("click", () => showQuestion(current + 1, true)));
    navButtons.forEach((button) => button.addEventListener("click", () => showQuestion(Number(button.dataset.qnav), true)));
    form.querySelector("[data-quiz-submit]")?.addEventListener("click", (event) => {
        event.preventDefault();
        showSubmitDialog();
    });
    dialog?.querySelector("[data-quiz-cancel]")?.addEventListener("click", () => dialog.close());
    dialog?.querySelector("[data-quiz-confirm]")?.addEventListener("click", () => {
        confirmedSubmit = true;
        dialog.close();
        form.requestSubmit();
    });
    dialog?.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && dialog?.open) dialog.close();
    });
    form.addEventListener("submit", (event) => {
        if (confirmedSubmit) return;
        if (questions.some((questionCard) => !questionIsAnswered(questionCard))) {
            event.preventDefault();
            showSubmitDialog();
        }
    });
})();
