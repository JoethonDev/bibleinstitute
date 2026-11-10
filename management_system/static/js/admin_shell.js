(function () {
    'use strict';

    // Register Alpine components before Alpine Core fires alpine:init.
    document.addEventListener('alpine:init', function () {
        Alpine.data('r2Manager', r2Manager);
        Alpine.data('lessonForm', lessonForm);
        Alpine.data('autoSave', autoSave);
        Alpine.data('quizForm', quizForm);
        Alpine.data('quizJsonImport', quizJsonImport);
        Alpine.data('formValidation', formValidation);
        Alpine.data('tableManager', tableManager);
        Alpine.data('adminOperationStatus', adminOperationStatus);
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
            // The shared admin links use the workspace navigation classes (the
            // workspace rail/sidebar links are handled by delegation.
            // also covers links added by an HTMX fragment.
            offcanvas.addEventListener('click', function (event) {
                if (event.target.closest && event.target.closest('a.aw-side-link, a.aw-rail-link')) closeAdminMenu();
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
            // Page-level swaps are any region that carries the workspace body:
            // sidebar/rail navigations target #display-page, while table, form
            // and pagination links target #content. Both replace the persistent
            // OOB sidebar, so both must re-initialize Alpine on it.
            var pageRegionIds = ['display-page', 'page-region', 'content'];
            if (pageRegionIds.indexOf(target.id) === -1) return;
            closeAdminMenu();
            // Each tree gets its own guard: a throw while initializing the
            // swapped page must not skip the persistent OOB regions, or the
            // sidebar toggle loses its Alpine binding after navigation.
            if (window.Alpine && typeof window.Alpine.initTree === 'function') {
                try {
                    window.Alpine.initTree(target);
                } catch (error) { /* Alpine owns its scope; a missed tree stays as-is. */ }
                // Page navigation also replaces the persistent sidebar and
                // mobile navigation through hx-select-oob. Those regions are
                // new DOM after the swap and carry Alpine bindings, so they
                // are initialized here instead of rebuilding the shell.
                var persistent = ['#aw-sidebar-body', '#aw-mobile-nav'];
                for (var p = 0; p < persistent.length; p++) {
                    var regionNode = document.querySelector(persistent[p]);
                    if (!regionNode) continue;
                    try {
                        window.Alpine.initTree(regionNode);
                    } catch (error) { /* A missed region keeps its markup. */ }
                }
            }
            // The main column is the scroll container now, so the browser's
            // navigation scroll reset no longer applies: a page swap must put the
            // new page back at the top. In-place updates (pagination, filters,
            // which target #content) keep their position.
            if (target.id === 'display-page' || target.id === 'page-region') {
                var mainColumn = document.querySelector('.aw-main');
                if (mainColumn) mainColumn.scrollTop = 0;
            }
            var region = target.id === 'page-region'
                ? target
                : (target.querySelector('#page-region') || document.getElementById('page-region'));
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

    // Bootstrap marks a dialog aria-hidden while a descendant (usually its
    // close button) can still hold focus, which browsers now reject with a
    // console warning. Move focus out of the dialog before it hides.
    document.addEventListener('hide.bs.modal', function (event) {
        var modal = event.target;
        if (!modal || typeof modal.contains !== 'function') return;
        var active = document.activeElement;
        if (active && active !== modal && modal.contains(active) && typeof active.blur === 'function') {
            active.blur();
        }
    }, true);

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

    // ---- Tab panels -------------------------------------------------------
    // Real panels for tabbed workspaces (academic setup, promotion): one tab
    // is selected and the others are hidden. Markup opts in with data-aw-tab /
    // data-aw-tabpanel inside a [data-aw-tabs] root, so no page needs its own
    // inline script and HTMX swaps keep working through delegation.
    function selectWorkspaceTab(tab) {
        var root = tab.closest('[data-aw-tabs]');
        if (!root) return;
        var name = tab.getAttribute('data-aw-tab');
        root.querySelectorAll('[data-aw-tab]').forEach(function (item) {
            var active = item === tab;
            item.classList.toggle('is-active', active);
            item.setAttribute('aria-selected', active ? 'true' : 'false');
            item.setAttribute('tabindex', active ? '0' : '-1');
        });
        root.querySelectorAll('[data-aw-tabpanel]').forEach(function (panel) {
            panel.hidden = panel.getAttribute('data-aw-tabpanel') !== name;
        });
    }

    document.addEventListener('click', function (event) {
        if (!event.target.closest) return;
        var tab = event.target.closest('[data-aw-tab]');
        if (!tab) return;
        event.preventDefault();
        selectWorkspaceTab(tab);
    });

    // ---- Workspace top-bar menus and the one peek drawer -------------------
    // Registered once at load. HTMX replaces persistent navigation regions and
    // drawer bodies without re-running this file, so every handler delegates
    // from document instead of binding per element.
    var openMenu = null;

    function closeWorkspaceMenu() {
        if (!openMenu) return;
        openMenu.menu.hidden = true;
        openMenu.trigger.setAttribute('aria-expanded', 'false');
        openMenu = null;
    }

    function toggleWorkspaceMenu(trigger) {
        var wrap = trigger.closest('.aw-menu-wrap');
        var menu = wrap && wrap.querySelector('.aw-menu');
        if (!menu) return;
        var wasOpen = openMenu && openMenu.menu === menu;
        closeWorkspaceMenu();
        if (wasOpen) return;
        menu.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        openMenu = { menu: menu, trigger: trigger };
        var first = menu.querySelector('.aw-menu-item');
        if (first && typeof first.focus === 'function') first.focus();
    }

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest && event.target.closest('[data-aw-menu-trigger]');
        if (trigger) {
            event.preventDefault();
            event.stopPropagation();
            toggleWorkspaceMenu(trigger);
            return;
        }
        if (openMenu && !(event.target.closest && event.target.closest('.aw-menu'))) {
            closeWorkspaceMenu();
        }
    });

    var drawer = document.getElementById('aw-drawer');
    var drawerBody = document.getElementById('aw-drawer-body');
    var drawerOverlay = document.getElementById('aw-drawer-overlay');
    var drawerTitle = document.getElementById('aw-drawer-title');
    var drawerSubtitle = document.getElementById('aw-drawer-subtitle');
    var drawerSequence = 0;
    var drawerReturnFocus = null;

    function drawerLabels() {
        return (drawer && drawer.dataset) || {};
    }

    function drawerIsOpen() {
        return !!(drawer && !drawer.hasAttribute('hidden'));
    }

    function closeDrawer() {
        if (!drawerIsOpen()) return;
        // Fence any in-flight fragment: a late response must not reopen or
        // overwrite the panel after the operator closed it.
        drawerSequence += 1;
        drawer.setAttribute('hidden', '');
        drawer.setAttribute('aria-hidden', 'true');
        if (drawerOverlay) drawerOverlay.setAttribute('hidden', '');
        if (drawerBody) drawerBody.innerHTML = '';
        document.body.classList.remove('aw-drawer-open');
        if (drawerReturnFocus && typeof drawerReturnFocus.focus === 'function') drawerReturnFocus.focus();
        drawerReturnFocus = null;
    }

    function showDrawer() {
        if (!drawer) return;
        drawer.removeAttribute('hidden');
        drawer.setAttribute('aria-hidden', 'false');
        if (drawerOverlay) drawerOverlay.removeAttribute('hidden');
        document.body.classList.add('aw-drawer-open');
    }

    function drawerState(message, role) {
        if (!drawerBody) return;
        drawerBody.innerHTML = '';
        var state = document.createElement('div');
        state.className = 'aw-drawer-state';
        state.setAttribute('role', role || 'status');
        state.textContent = message;
        drawerBody.appendChild(state);
    }

    function loadDrawer(trigger) {
        var url = trigger.getAttribute('data-aw-drawer') || trigger.getAttribute('href');
        if (!url || !drawerBody) return;
        var labels = drawerLabels();
        drawerReturnFocus = trigger;
        if (drawerTitle) drawerTitle.textContent = trigger.getAttribute('data-aw-drawer-title') || '';
        if (drawerSubtitle) drawerSubtitle.textContent = trigger.getAttribute('data-aw-drawer-subtitle') || '';
        showDrawer();
        var sequence = ++drawerSequence;
        drawerState(labels.awLoading || '...');
        fetch(url, {
            credentials: 'same-origin',
            headers: { 'HX-Request': 'true', 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (response) {
            if (sequence !== drawerSequence) return null;
            // An expired session navigates to the login page instead of
            // rendering login markup inside the drawer.
            if (response.status === 401 || (response.redirected && /\/login\//.test(response.url))) {
                window.location.assign(response.url);
                return null;
            }
            if (response.status === 403) {
                drawerState(labels.awForbidden || '', 'alert');
                return null;
            }
            if (!response.ok) throw new Error('drawer-load-failed');
            return response.text();
        }).then(function (html) {
            if (html === null || sequence !== drawerSequence) return;
            drawerBody.innerHTML = html;
            if (window.htmx && typeof window.htmx.process === 'function') window.htmx.process(drawerBody);
            if (window.Alpine && typeof window.Alpine.initTree === 'function') window.Alpine.initTree(drawerBody);
            var focusTarget = drawerBody.querySelector('[autofocus], button, a, input, select, textarea');
            if (focusTarget && typeof focusTarget.focus === 'function') focusTarget.focus();
        }).catch(function () {
            if (sequence !== drawerSequence) return;
            drawerState(labels.awError || '', 'alert');
        });
    }

    // Programmatic entry point for components that already own their markup
    // (the calendar day agenda clones it from the grid) instead of a URL.
    window.awDrawer = {
        open: function (options) {
            options = options || {};
            if (!drawer || !drawerBody) return;
            drawerReturnFocus = (options.trigger instanceof Element) ? options.trigger : document.activeElement;
            if (drawerTitle) drawerTitle.textContent = options.title || '';
            if (drawerSubtitle) drawerSubtitle.textContent = options.subtitle || '';
            showDrawer();
            drawerBody.innerHTML = options.html || '';
            if (window.htmx && typeof window.htmx.process === 'function') window.htmx.process(drawerBody);
            if (window.Alpine && typeof window.Alpine.initTree === 'function') window.Alpine.initTree(drawerBody);
            var focusTarget = drawerBody.querySelector('[autofocus], button, a, input, select, textarea');
            if (focusTarget && typeof focusTarget.focus === 'function') focusTarget.focus();
        },
        close: closeDrawer
    };

    document.addEventListener('click', function (event) {
        var close = event.target.closest && event.target.closest('[data-aw-drawer-close]');
        if (close) {
            event.preventDefault();
            closeDrawer();
            return;
        }
        var trigger = event.target.closest && event.target.closest('[data-aw-drawer]');
        if (trigger) {
            event.preventDefault();
            loadDrawer(trigger);
        }
    });

    if (drawerOverlay) {
        drawerOverlay.addEventListener('click', closeDrawer);
    }

    // ---- Media lightbox ----------------------------------------------------
    // Chat images and video open in a full-screen overlay instead of a new tab.
    // Delegated so it also covers fragments swapped in by HTMX.
    var lightbox = null;

    function ensureLightbox() {
        if (lightbox) return lightbox;
        lightbox = document.createElement('div');
        lightbox.className = 'aw-lightbox';
        lightbox.hidden = true;
        lightbox.setAttribute('role', 'dialog');
        lightbox.setAttribute('aria-modal', 'true');
        lightbox.innerHTML = '<button type="button" class="aw-lightbox-close" aria-label="Close">&times;</button><div class="aw-lightbox-body"></div>';
        document.body.appendChild(lightbox);
        lightbox.addEventListener('click', function (event) {
            if (event.target === lightbox || (event.target.closest && event.target.closest('.aw-lightbox-close'))) {
                closeLightbox();
            }
        });
        return lightbox;
    }

    function openLightbox(src, kind) {
        if (!src) return;
        var box = ensureLightbox();
        var body = box.querySelector('.aw-lightbox-body');
        body.innerHTML = '';
        var node = document.createElement(kind === 'video' ? 'video' : 'img');
        node.setAttribute('src', src);
        if (kind === 'video') {
            node.setAttribute('controls', '');
            node.setAttribute('autoplay', '');
        } else {
            node.setAttribute('alt', '');
        }
        body.appendChild(node);
        box.hidden = false;
        document.body.classList.add('aw-lightbox-open');
        var close = box.querySelector('.aw-lightbox-close');
        if (close && typeof close.focus === 'function') close.focus();
    }

    function closeLightbox() {
        if (!lightbox || lightbox.hidden) return;
        lightbox.hidden = true;
        var body = lightbox.querySelector('.aw-lightbox-body');
        if (body) body.innerHTML = '';
        document.body.classList.remove('aw-lightbox-open');
    }

    document.addEventListener('click', function (event) {
        if (!event.target.closest) return;
        var image = event.target.closest('img.aw-msg-image');
        if (image) {
            event.preventDefault();
            openLightbox(image.currentSrc || image.src, 'image');
            return;
        }
        var expand = event.target.closest('[data-aw-lightbox]');
        if (expand) {
            event.preventDefault();
            openLightbox(
                expand.getAttribute('data-aw-lightbox'),
                expand.getAttribute('data-aw-lightbox-kind') || 'image'
            );
            return;
        }
        // Attendance matrix: clicking a row opens that student's meeting history
        // in the drawer instead of expanding a second table row inline.
        var row = event.target.closest('tr[data-attendance-row]');
        if (row && window.awDrawer) {
            if (event.target.closest('a, button, input, select, textarea, form, label')) return;
            var template = row.querySelector('template[data-attendance-detail]');
            if (!template) return;
            event.preventDefault();
            window.awDrawer.open({
                title: row.getAttribute('data-attendance-title') || '',
                subtitle: row.getAttribute('data-attendance-subtitle') || '',
                html: template.innerHTML,
                trigger: row
            });
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (lightbox && !lightbox.hidden) {
            event.preventDefault();
            closeLightbox();
            return;
        }
        if (openMenu) {
            closeWorkspaceMenu();
            return;
        }
        if (!drawerIsOpen()) return;
        event.preventDefault();
        closeDrawer();
    });

    // Minimal focus trap: keep Tab inside the open drawer.
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Tab' || !drawerIsOpen() || !drawerBody) return;
        var focusables = drawerBody.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (!focusables.length) return;
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    // Navigation is a teardown boundary: an open peek must not survive a page
    // swap, and its pending request is fenced by closeDrawer().
    document.addEventListener('htmx:before:request', function (event) {
        var ctx = event.detail && event.detail.ctx;
        var target = (ctx && ctx.target) || event.target;
        if (!target || !(target instanceof Element)) return;
        if (target.id === 'display-page' || target.id === 'page-region') closeDrawer();
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
