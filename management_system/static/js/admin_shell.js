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
        // HTMX 4 does not use localStorage for history snapshots by default.
        // Alpine owns client state, so every restored URL is authoritative.
        var offcanvas = document.getElementById('mobileAdminMenu');
        function closeMenu() {
            if (!offcanvas || typeof bootstrap === 'undefined' || !bootstrap.Offcanvas) return;
            bootstrap.Offcanvas.getOrCreateInstance(offcanvas).hide();
        }
        if (offcanvas) {
            // The shared admin links use .ad-tab, not Bootstrap's .nav-link.
            // Delegation also covers links added by an HTMX fragment.
            offcanvas.addEventListener('click', function (event) {
                if (event.target.closest && event.target.closest('a.ad-tab')) closeMenu();
            });
        }
        document.addEventListener('htmx:before:request', function (event) {
            if (offcanvas && event.target && offcanvas.contains(event.target)) closeMenu();
        });
    });

    // Scroll-triggered pagination for the R2/Drive browser. The server renders
    // a #drive-next-sentinel carrying the next-page URL; when it approaches the
    // viewport, the next page replaces #file-list-container (innerHTML) exactly
    // like the former Next button, and the new sentinel is observed. No URL is
    // pushed to history: scrolling must not spam browser history.
    var driveSentinelObserver = null;

    function loadDriveNextPage(sentinel) {
        if (!sentinel || !sentinel.dataset.nextUrl) return;
        if (sentinel.dataset.loading) return;
        sentinel.classList.add('is-loading');
        sentinel.dataset.loading = '1';
        var request = htmx.ajax('GET', sentinel.dataset.nextUrl, {
            target: '#file-list-container',
            swap: 'innerHTML'
        });
        var settle = function () {
            sentinel.classList.remove('is-loading');
            delete sentinel.dataset.loading;
            if (driveSentinelObserver && !sentinel.isConnected) {
                driveSentinelObserver.unobserve(sentinel);
            }
        };
        request.then(settle).catch(settle);
    }

    function scanDriveSentinels() {
        if (typeof IntersectionObserver === 'undefined') return;
        if (!driveSentinelObserver) {
            driveSentinelObserver = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) loadDriveNextPage(entry.target);
                });
            }, { rootMargin: '300px 0px' });
        }
        var sentinels = document.querySelectorAll('#drive-next-sentinel[data-next-url]');
        for (var i = 0; i < sentinels.length; i++) {
            var sentinel = sentinels[i];
            if (!sentinel.dataset.observed) {
                sentinel.dataset.observed = '1';
                driveSentinelObserver.observe(sentinel);
            }
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        scanDriveSentinels();
        document.addEventListener('htmx:after:swap', scanDriveSentinels);
        document.addEventListener('click', function (event) {
            var sentinel = event.target.closest && event.target.closest('#drive-next-sentinel[data-next-url]');
            if (sentinel) loadDriveNextPage(sentinel);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            var sentinel = event.target.closest && event.target.closest('#drive-next-sentinel[data-next-url]');
            if (sentinel) {
                event.preventDefault();
                loadDriveNextPage(sentinel);
            }
        });
    });

    var modal = document.getElementById('globalConfirmModal');
    var message = document.getElementById('globalConfirmMessage');
    var confirmButton = document.getElementById('globalConfirmButton');
    if (!modal || !message || !confirmButton || typeof bootstrap === 'undefined' || !bootstrap.Modal) return;

    var pendingAction = null;
    var pendingCancel = null;
    var confirming = false;
    var lastTrigger = null;

    function showConfirmation(question, action, onCancel) {
        if (!question || typeof action !== 'function' || confirming) return false;
        confirming = true;
        pendingAction = action;
        pendingCancel = typeof onCancel === 'function' ? onCancel : null;
        message.textContent = question;
        lastTrigger = document.activeElement;

        var currentButton = document.getElementById('globalConfirmButton');
        var button = currentButton.cloneNode(true);
        currentButton.replaceWith(button);
        button.addEventListener('click', function () {
            if (!confirming) return;
            var callback = pendingAction;
            pendingAction = null;
            pendingCancel = null;
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
                if (pendingCancel) { var drop = pendingCancel; pendingCancel = null; drop(); }
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
        // Capture phase + stopPropagation: the vendored HTMX runtime never
        // checks defaultPrevented, so without this the hx-post request would
        // fire immediately, before the user confirms.
        event.preventDefault();
        event.stopPropagation();
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
        if (!event.detail || typeof event.detail.issueRequest !== 'function') return;
        // The vendored HTMX 4 runtime exposes the question on the request
        // context (ctx.confirm), not as detail.question.
        var question = event.detail.question
            || (event.detail.ctx && event.detail.ctx.confirm)
            || '';
        if (!question) return;
        event.preventDefault();
        var dropRequest = event.detail.dropRequest;
        showConfirmation(String(question), function () {
            event.detail.issueRequest(true);
        }, dropRequest);
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
