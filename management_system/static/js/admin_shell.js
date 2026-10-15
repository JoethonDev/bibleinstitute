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
        Alpine.data('quizJsonImport', quizJsonImport);
        Alpine.data('formValidation', formValidation);
        Alpine.data('tableManager', tableManager);
        Alpine.data('progressTracker', progressTracker);
    });

    // Mobile offcanvas: one canonical close used by the click path and by
    // every HTMX page navigation, so closing never depends on which page
    // issued the navigation request.
    function closeAdminMenu() {
        var menu = document.getElementById('mobileAdminMenu');
        if (!menu || typeof bootstrap === 'undefined' || !bootstrap.Offcanvas) return;
        bootstrap.Offcanvas.getOrCreateInstance(menu).hide();
    }

    document.addEventListener('DOMContentLoaded', function () {
        // HTMX 4 does not use localStorage for history snapshots by default.
        // Alpine owns client state, so every restored URL is authoritative.
        // Preserve the live input value across content swaps; explicit Clear
        // actions reset it before their request so hx-preserve cannot restore
        // the previous query into the cleared page.
        document.addEventListener('click', function (event) {
            var clearTrigger = event.target.closest && event.target.closest('[data-clear-search]');
            if (!clearTrigger) return;
            var input = document.getElementById(clearTrigger.dataset.clearSearch);
            if (input && input.matches('input[type="search"], input[type="text"]')) input.value = '';
        }, true);

        var offcanvas = document.getElementById('mobileAdminMenu');
        if (offcanvas) {
            // The shared admin links use .ad-tab, not Bootstrap's .nav-link.
            // Delegation also covers links added by an HTMX fragment.
            offcanvas.addEventListener('click', function (event) {
                if (event.target.closest && event.target.closest('a.ad-tab')) closeAdminMenu();
            });
        }
        document.addEventListener('htmx:before:request', function (event) {
            if (offcanvas && event.target instanceof Element && offcanvas.contains(event.target)) closeAdminMenu();
        });
        // Page-navigation uniformity: after every HTMX page-region swap the
        // mobile menu is closed, Alpine settles the new tree, and a subtle
        // transition runs. Narrow filter/table swaps target other regions and
        // are intentionally left untouched, so this behavior never depends on
        // which page issued the navigation request.
        document.addEventListener('htmx:after:swap', function (event) {
            // HTMX 4 dispatches htmx:after:swap on the triggering element
            // (event.target is the clicked nav link), not the swapped region.
            // The real swapped region lives on the request context, so the
            // old event.target check always bailed and page navigations never
            // ran closeAdminMenu() or Alpine.initTree() on fresh content.
            var ctx = event.detail && event.detail.ctx;
            var target = (ctx && ctx.target) || event.target;
            if (!target || !(target instanceof Element)) return;
            if (target.id !== 'display-page' && target.id !== 'page-region') return;
            closeAdminMenu();
            try {
                if (window.Alpine && typeof window.Alpine.initTree === 'function') {
                    window.Alpine.initTree(target);
                }
            } catch (error) { /* Alpine owns its scope; a missed tree stays as-is. */ }
            var region = target.id === 'page-region' ? target : target.querySelector('#page-region');
            if (region && typeof region.animate === 'function'
                    && window.matchMedia
                    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                try {
                    region.animate(
                        [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }],
                        { duration: 140, easing: 'ease-out' }
                    );
                } catch (error) { /* The transition is decorative only. */ }
            }
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

    // An HTMX swap can replace an open Bootstrap modal before its hide
    // transition completes, so Bootstrap's _hideModal never runs and leaves
    // a stranded .modal-backdrop plus body.modal-open. Remove stray backdrops
    // whenever no modal is shown; keep the body scroll state while the shared
    // confirmation modal or the mobile offcanvas is open.
    function cleanupOrphanedModalBackdrop() {
        if (document.querySelector('.modal.show')) return;
        var backdrops = document.querySelectorAll('.modal-backdrop');
        for (var i = 0; i < backdrops.length; i++) backdrops[i].remove();
        if (document.querySelector('.offcanvas.show')) return;
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    document.addEventListener('DOMContentLoaded', function () {
        scanDriveSentinels();
        document.addEventListener('htmx:after:swap', scanDriveSentinels);
        document.addEventListener('htmx:after:swap', cleanupOrphanedModalBackdrop);
        document.addEventListener('input', function (event) {
            var search = event.target.closest && event.target.closest('[data-exceptional-student-search]');
            if (!search) return;
            var select = document.getElementById(search.getAttribute('aria-controls'));
            if (!select || !select.matches('[data-exceptional-student-options]')) return;
            var query = search.value.trim().toLocaleLowerCase();
            Array.prototype.forEach.call(select.options, function (option) {
                option.hidden = Boolean(query) && !option.selected && !option.text.toLocaleLowerCase().includes(query);
            });
        });
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
        if (!question || typeof action !== 'function') return false;
        // Only block while an actual confirmation modal is on screen. A stale
        // `confirming` flag (e.g. an HTMX swap hid the modal without firing
        // hidden.bs.modal) must not disable every later confirmation until a
        // full page refresh.
        if (confirming && modal.classList.contains('show')) return false;
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
