(function () {
    'use strict';

    var sectionId = 'app-table-section';
    var initialized = false;

    function section() { return document.getElementById(sectionId); }

    function notify(message, level) {
        try {
            if (window.Alpine && Alpine.store('notifications')) {
                Alpine.store('notifications').add(message, level || 'warning');
                return;
            }
        } catch (error) { /* use alert below */ }
        window.alert(message);
    }

    function stateOwner() {
        return document.getElementById('content') || document;
    }

    function getState(root) {
        var owner = stateOwner();
        var status = root.dataset.selectionStatus || 'all';
        var token = root.dataset.selectionToken || '';
        if (!owner._applicationBulkState
            || owner._applicationBulkState.status !== status
            || owner._applicationBulkState.token !== token) {
            owner._applicationBulkState = {
                status: status,
                token: token,
                all: false,
                selected: new Set(),
                excluded: new Set()
            };
        }
        return owner._applicationBulkState;
    }

    function visibleIds(root) {
        return Array.from(root.querySelectorAll('.user-checkbox')).map(function (checkbox) {
            return String(checkbox.value);
        });
    }

    function syncCheckboxes(root) {
        var state = getState(root);
        root.querySelectorAll('.user-checkbox').forEach(function (checkbox) {
            var id = String(checkbox.value);
            checkbox.checked = state.all ? !state.excluded.has(id) : state.selected.has(id);
        });
        var header = root.querySelector('#select-all');
        if (header) {
            var ids = visibleIds(root);
            header.checked = ids.length > 0 && ids.every(function (id) {
                return state.all ? !state.excluded.has(id) : state.selected.has(id);
            });
            header.indeterminate = !header.checked && ids.some(function (id) {
                return state.all ? !state.excluded.has(id) : state.selected.has(id);
            });
        }
        var allButton = root.querySelector('#select-all-records');
        if (allButton) allButton.classList.toggle('d-none', !root.querySelector('#select-all')?.checked || state.all);
    }

    function handleSelection(root, checkbox) {
        var state = getState(root);
        var id = String(checkbox.value);
        if (state.all) {
            checkbox.checked ? state.excluded.delete(id) : state.excluded.add(id);
        } else if (checkbox.checked) {
            state.selected.add(id);
        } else {
            state.selected.delete(id);
        }
        syncCheckboxes(root);
    }

    function submitDecision(root, button) {
        var state = getState(root);
        var selected = state.all ? [] : Array.from(state.selected);
        if (!state.all && selected.length === 0) {
            notify(root.dataset.noSelectionMessage, 'warning');
            return;
        }
        if (root.dataset.bulkPending === 'true') return;

        var run = function () {
            root.dataset.bulkPending = 'true';
            button.disabled = true;
            var payload = {
                decision: button.dataset.decision,
                user_ids: selected,
                select_all: state.all,
                excluded_user_ids: Array.from(state.excluded),
                selection_token: root.dataset.selectionToken || ''
            };
            fetch(root.dataset.bulkUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': root.dataset.csrf },
                body: JSON.stringify(payload)
            }).then(function (response) {
                return response.json().then(function (data) { return { ok: response.ok, data: data }; });
            }).then(function (result) {
                if (!result.ok) throw new Error(result.data && result.data.error || root.dataset.requestFailedMessage);
                var summary = document.getElementById('application-bulk-result');
                if (summary) {
                    var processed = Number(result.data.processed) || 0;
                    var stale = Number(result.data.stale_count) || 0;
                    var failed = Number(result.data.failed) || 0;
                    var notificationFailed = Number(result.data.notification_failed_count) || 0;
                    var parts = [
                        summary.dataset.processedLabel + ': ' + processed,
                        summary.dataset.staleLabel + ': ' + stale,
                        summary.dataset.failedLabel + ': ' + failed
                    ];
                    if (notificationFailed) {
                        parts.push(summary.dataset.notificationFailedLabel + ': ' + notificationFailed);
                    }
                    summary.textContent = parts.join(' · ');
                    summary.classList.remove('d-none', 'ad-alert--danger', 'ad-alert--info', 'ad-alert--warning', 'ad-alert--success');
                    summary.classList.add(
                        stale || failed || notificationFailed ? 'ad-alert--warning' : 'ad-alert--success'
                    );
                }
                var owner = stateOwner();
                delete owner._applicationBulkState;
                root.dataset.bulkPending = 'false';
                button.disabled = false;
                if (window.htmx) {
                    var refresh = htmx.ajax('GET', window.location.href, {
                        target: '#applications-workspace',
                        select: '#applications-workspace',
                        swap: 'outerHTML',
                        pushUrl: false
                    });
                    if (refresh && typeof refresh.catch === 'function') {
                        refresh.catch(function (error) {
                            notify(error.message || root.dataset.requestFailedMessage, 'danger');
                        });
                    }
                } else {
                    window.setTimeout(function () { window.location.reload(); }, 1500);
                }
            }).catch(function (error) {
                root.dataset.bulkPending = 'false';
                button.disabled = false;
                notify(error.message || root.dataset.requestFailedMessage, 'danger');
            });
        };
        if (typeof window.appConfirm === 'function') {
            window.appConfirm(root.dataset.bulkConfirmMessage, run);
        } else {
            run();
        }
    }

    function init() {
        var root = section();
        if (!root) return;
        syncCheckboxes(root);
        if (root.dataset.applicationHandlers === 'true') return;
        root.dataset.applicationHandlers = 'true';
        root.addEventListener('change', function (event) {
            if (event.target.id === 'select-all') {
                var state = getState(root);
                if (!event.target.checked) {
                    state.all = false; state.selected.clear(); state.excluded.clear();
                } else {
                    root.querySelectorAll('.user-checkbox').forEach(function (checkbox) {
                        var id = String(checkbox.value);
                        state.selected.add(id);
                    });
                }
                syncCheckboxes(root);
            } else if (event.target.classList.contains('user-checkbox')) {
                handleSelection(root, event.target);
            }
        });
        root.addEventListener('click', function (event) {
            var allButton = event.target.closest('#select-all-records');
            if (allButton) {
                event.preventDefault();
                var state = getState(root);
                state.all = true; state.selected.clear(); state.excluded.clear();
                syncCheckboxes(root);
                return;
            }
            var decisionButton = event.target.closest('#bulk-activate, #bulk-decline, #bulk-reopen');
            if (decisionButton) {
                event.preventDefault();
                submitDecision(root, decisionButton);
            }
        });
    }

    function boot() { window.requestAnimationFrame(init); }
    if (!initialized) {
        initialized = true;
        document.addEventListener('DOMContentLoaded', boot);
        document.addEventListener('htmx:afterSwap', boot);
        document.addEventListener('htmx:afterSettle', boot);
        document.addEventListener('applicationDecisionCompleted', function (event) {
            var drawer = document.getElementById('applicationReviewDrawer');
            if (drawer && window.bootstrap && bootstrap.Offcanvas) {
                bootstrap.Offcanvas.getOrCreateInstance(drawer).hide();
            }
            var detail = event.detail || {};
            if (detail.message) notify(detail.message, detail.level || 'success');
        });
    }
}());
