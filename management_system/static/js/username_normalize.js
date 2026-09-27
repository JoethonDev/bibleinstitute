/* Normalize username inputs while typing; server-side validation is canonical too. */
(function () {
    'use strict';

    const selector = 'input[name="username"], input[name="lms_username"]';

    function normalize(value) {
        return value.normalize('NFKC').trim().toLowerCase();
    }

    function normalizeInput(input) {
        if (!(input instanceof HTMLInputElement)) return;
        const original = input.value;
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const next = normalize(original);
        if (next === original) return;

        input.value = next;
        if (start !== null && end !== null && document.activeElement === input) {
            const nextStart = normalize(original.slice(0, start)).length;
            const nextEnd = normalize(original.slice(0, end)).length;
            input.setSelectionRange(nextStart, nextEnd);
        }
    }

    document.addEventListener('input', event => {
        if (event.isComposing) return;
        if (event.target?.matches?.(selector)) normalizeInput(event.target);
    });

    document.addEventListener('compositionend', event => {
        if (event.target?.matches?.(selector)) normalizeInput(event.target);
    });
})();
