(function () {
    'use strict';

    // Register Alpine components before Alpine Core fires alpine:init.
    document.addEventListener('alpine:init', function () {
        Alpine.data('r2Manager', r2Manager);
        Alpine.data('lessonForm', lessonForm);
        Alpine.data('autoSave', autoSave);
        Alpine.data('columnManager', columnManager);
        Alpine.data('bulkActions', bulkActions);
        Alpine.data('quizForm', quizForm);
        Alpine.data('formValidation', formValidation);
        Alpine.data('dashboardManager', dashboardManager);
        Alpine.data('filterManager', filterManager);
        Alpine.data('tableManager', tableManager);
        Alpine.data('progressTracker', progressTracker);
    });

    document.addEventListener('DOMContentLoaded', function () {
        var offcanvas = document.getElementById('mobileAdminMenu');
        var offcanvasLinks = offcanvas ? offcanvas.querySelectorAll('.nav-link') : [];
        function closeMenu() {
            if (!offcanvas || typeof bootstrap === 'undefined' || !bootstrap.Offcanvas) return;
            bootstrap.Offcanvas.getOrCreateInstance(offcanvas).hide();
        }
        offcanvasLinks.forEach(function (link) {
            link.addEventListener('click', closeMenu);
        });
    });

    var modal = document.getElementById('globalConfirmModal');
    var message = document.getElementById('globalConfirmMessage');
    var confirmButton = document.getElementById('globalConfirmButton');
    if (!modal || !message || !confirmButton || typeof bootstrap === 'undefined' || !bootstrap.Modal) return;

    var pendingAction = null;
    var confirming = false;
    var lastTrigger = null;

    function showConfirmation(question, action) {
        if (!question || typeof action !== 'function' || confirming) return false;
        confirming = true;
        pendingAction = action;
        message.textContent = question;
        lastTrigger = document.activeElement;

        var currentButton = document.getElementById('globalConfirmButton');
        var button = currentButton.cloneNode(true);
        currentButton.replaceWith(button);
        button.addEventListener('click', function () {
            if (!confirming) return;
            var callback = pendingAction;
            pendingAction = null;
            confirming = false;
            button.disabled = true;
            // Remove focus before Bootstrap sets aria-hidden on the modal.
            // This prevents a focused descendant from remaining inside a
            // hidden dialog during the hide transition.
            button.blur();
            var instance = bootstrap.Modal.getInstance(modal);
            if (instance) instance.hide();
            Promise.resolve(callback && callback()).finally(function () {
                button.disabled = false;
            });
        }, { once: true });

        modal.addEventListener('hidden.bs.modal', function cancel() {
            if (confirming) {
                confirming = false;
                pendingAction = null;
            }
            if (modal.contains(document.activeElement) && document.activeElement.blur) {
                document.activeElement.blur();
            }
            if (lastTrigger && typeof lastTrigger.focus === 'function') lastTrigger.focus();
            modal.removeEventListener('hidden.bs.modal', cancel);
        });

        var instance = bootstrap.Modal.getOrCreateInstance(modal);
        if (instance) instance.show();
        return true;
    }

    window.appConfirm = showConfirmation;

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || !form.matches || !form.matches('form[data-confirm-message]')) return;
        if (form.dataset.confirmed === 'true') {
            delete form.dataset.confirmed;
            return;
        }
        event.preventDefault();
        var submitter = event.submitter;
        showConfirmation(form.dataset.confirmMessage, function () {
            form.dataset.confirmed = 'true';
            if (form.requestSubmit) {
                submitter ? form.requestSubmit(submitter) : form.requestSubmit();
            } else {
                HTMLFormElement.prototype.submit.call(form);
            }
        });
    }, true);

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest && event.target.closest('[data-confirm-message]');
        if (!trigger || trigger.matches('form') || trigger.closest('form[data-confirm-message]')) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        showConfirmation(trigger.dataset.confirmMessage, function () {
            if (trigger.tagName === 'A' && trigger.href) window.location.assign(trigger.href);
        });
    }, true);

    document.addEventListener('app:confirm', function (event) {
        var detail = event.detail || {};
        showConfirmation(detail.message, detail.onConfirm);
    });

    document.addEventListener('htmx:confirm', function (event) {
        if (!event.detail || !event.detail.question) return;
        event.preventDefault();
        showConfirmation(event.detail.question, function () {
            event.detail.issueRequest(true);
        });
    });

    if (typeof bootstrap.Modal !== 'undefined') {
        var OrigModal = bootstrap.Modal;
        var origGetOrCreate = OrigModal.getOrCreateInstance;
        OrigModal.getOrCreateInstance = function (element, config) {
            try { return origGetOrCreate.call(this, element, config); } catch (_) { return null; }
        };
        var origGetInstance = OrigModal.getInstance;
        if (origGetInstance) {
            OrigModal.getInstance = function (element) {
                try { return origGetInstance.call(this, element); } catch (_) { return null; }
            };
        }
    }
}());
