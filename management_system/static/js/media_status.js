(function () {
    'use strict';

    var timer = null;
    var cleanup = null;
    function init() {
    var root = document.getElementById('content');
    if (!root || !root.hasAttribute('data-status-labels')) return;
    if (cleanup) cleanup();
    function refreshStatusRegion() {
        if (window.htmx) {
            htmx.ajax('GET', window.location.pathname + window.location.search, { target: '#content', select: '#content', swap: 'outerMorph' });
        } else {
            window.location.reload();
        }
    }
    function parseLabels(name) {
        try {
            return JSON.parse(root.getAttribute(name) || '{}');
        } catch (error) {
            return {};
        }
    }
    var statusLabels = parseLabels('data-status-labels');
    var batchStatusUrl = root.getAttribute('data-batch-status-url') || '';
    var retryFailed = root.getAttribute('data-trans-retry-failed') || gettext('Retry failed');
    var csrfInput = document.querySelector('#media-status-csrf input[name="csrfmiddlewaretoken"]');
    var csrf = csrfInput ? csrfInput.value : '';
    root.querySelectorAll('[data-media-retry-url]').forEach(function (button) {
        button.addEventListener('click', function () {
            var url = button.getAttribute('data-media-retry-url');
            if (!url || !csrf) {
                return;
            }
            button.disabled = true;
            fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
                body: '{}'
            }).then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error((data && data.message) || retryFailed);
                    });
                }
                refreshStatusRegion();
            }).catch(function (error) {
                button.disabled = false;
                button.setAttribute('aria-label', error.message);
                window.alert(error.message);
            });
        });
    });

    function notify(message, type) {
        var store = window.Alpine && window.Alpine.store
            ? window.Alpine.store('notifications')
            : null;
        if (store && typeof store.add === 'function') store.add(message, type || 'info');
    }

    function submitConfirmedAction(button, row, url, fallbackMessage) {
        if (!url || !csrf || button.dataset.pending === '1') return;
        if (typeof window.appConfirm !== 'function') {
            notify(gettext('The confirmation dialog is unavailable. Reload the page and try again.'), 'danger');
            return;
        }
        window.appConfirm(button.dataset.mediaConfirmMessage || fallbackMessage, function () {
            button.dataset.pending = '1';
            button.disabled = true;
            return fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
                body: '{}'
            }).then(function (response) {
                return response.text().then(function (text) {
                    var data = {};
                    try { data = text ? JSON.parse(text) : {}; } catch (error) {}
                    if (!response.ok) throw new Error(data.message || gettext('The request failed. Please try again.'));
                    return data;
                });
            }).then(function () {
                if (button.hasAttribute('data-media-cleanup-url')) notify(gettext('Cleanup scheduled'), 'success');
                else if (button.hasAttribute('data-media-stop-url')) notify(gettext('Stopping'), 'success');
                refreshStatusRegion();
            }).catch(function (error) {
                delete button.dataset.pending;
                button.disabled = false;
                var errorNode = row && row.querySelector('[data-media-cleanup-error]');
                if (errorNode) {
                    errorNode.textContent = error.message || gettext('The request failed. Please try again.');
                    errorNode.hidden = false;
                }
                notify(error.message || gettext('The request failed. Please try again.'), 'danger');
            });
        });
    }

    root.querySelectorAll('[data-media-stop-url]').forEach(function (button) {
        button.addEventListener('click', function () {
            submitConfirmedAction(
                button,
                button.closest('tr'),
                button.getAttribute('data-media-stop-url'),
                gettext('Stop processing this upload and any companion files?')
            );
        });
    });

    root.querySelectorAll('[data-media-cleanup-url]').forEach(function (button) {
        button.addEventListener('click', function () {
            submitConfirmedAction(
                button,
                button.closest('tr'),
                button.getAttribute('data-media-cleanup-url'),
                gettext('Remove this upload’s server files and R2 objects?')
            );
        });
    });

    function updateRow(row, job) {
        var status = row.querySelector('[data-media-status-value]');
        var phase = row.querySelector('[data-media-phase-value]');
        var bar = row.querySelector('[data-media-progress-bar]');
        var value = row.querySelector('[data-media-progress-value]');
        var error = row.querySelector('[data-media-error]');
        if (status) status.textContent = statusLabels[job.status] || job.status || '';
        if (phase) phase.textContent = job.current_phase_label || '';
        if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job.progress) || 0)) + '%';
        if (value) value.textContent = (Number(job.progress) || 0) + '%';
        if (error) {
            error.textContent = job.error_message || '';
            error.hidden = !job.error_message;
        }
        var nextStep = row.querySelector('[data-media-next-step]');
        if (nextStep) nextStep.textContent = job.next_step_label || '';
        var completed = row.querySelector('[data-media-completed]');
        if (completed) {
            completed.textContent = (job.completed_steps || []).length
                ? gettext('Done:') + ' ' + job.completed_steps.join(' · ')
                : '';
            completed.hidden = !(job.completed_steps || []).length;
        }
        var cleanupError = row.querySelector('[data-media-cleanup-error]');
        if (cleanupError) {
            cleanupError.textContent = job.cleanup_error_message || '';
            cleanupError.hidden = !job.cleanup_error_message;
        }
        row.querySelectorAll('[data-media-retry-url]').forEach(function (button) {
            var kind = button.getAttribute('data-media-retry-kind') || 'processing';
            button.hidden = kind === 'publish'
                ? !job.can_publish
                : kind === 'attachment' ? !job.can_retry_attachment : !job.can_retry;
        });
        row.querySelectorAll('[data-media-stop-url]').forEach(function (button) {
            button.hidden = !job.can_stop;
        });
        row.querySelectorAll('[data-media-cleanup-url]').forEach(function (button) {
            button.hidden = !job.can_cleanup;
        });
        var lessonCleanup = row.querySelector('[data-media-cleanup-lesson]');
        if (lessonCleanup) lessonCleanup.hidden = !job.lesson_deleted;
        var preservedCleanup = row.querySelector('[data-media-cleanup-preserved]');
        if (preservedCleanup) preservedCleanup.hidden = !(Number(job.cleanup_preserved_count) > 0);
        row.setAttribute('data-current-media-status', job.status || '');
        row.setAttribute('data-current-cleanup-status', job.cleanup_status || '');
        row.setAttribute('data-current-publication-pending', job.publication_pending ? 'true' : 'false');
        row.setAttribute('data-current-can-publish', job.can_publish ? 'true' : 'false');
        row.setAttribute('data-cleanup-not-before', job.cleanup_not_before || '');
        var progress = row.querySelector('[role="progressbar"]');
        if (progress) progress.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, Number(job.progress) || 0))));
    }

    function pollStatusRows() {
        var rows = Array.from(root.querySelectorAll('[data-media-status-url]')).filter(function (row) {
            if (row.getAttribute('data-media-terminal') === '1' || !row.getAttribute('data-media-job-id')) return false;
            if (row.getAttribute('data-current-cleanup-status') === 'queued') {
                var notBefore = Date.parse(row.getAttribute('data-cleanup-not-before') || '');
                if (Number.isFinite(notBefore) && notBefore > Date.now() + 5000) return false;
            }
            return true;
        });
        if (!rows.length || !batchStatusUrl) return;
        var ids = rows.map(function (row) { return row.getAttribute('data-media-job-id'); });
        fetch(batchStatusUrl + '?ids=' + encodeURIComponent(ids.join(',')), { credentials: 'same-origin' })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) {
                var jobs = data && data.jobs || {};
                rows.forEach(function (row) {
                    var job = jobs[row.getAttribute('data-media-job-id')];
                    if (!job) return;
                    var previousStatus = row.getAttribute('data-current-media-status') || '';
                    var previousCleanupStatus = row.getAttribute('data-current-cleanup-status') || '';
                    var previousPublicationPending = row.getAttribute('data-current-publication-pending') === 'true';
                    var previousCanPublish = row.getAttribute('data-current-can-publish') === 'true';
                    updateRow(row, job);
                    var attachmentPending = job.status === 'succeeded' && job.attachment_status === 'pending';
                    var cleanupPending = job.cleanup_status === 'queued' || job.cleanup_status === 'running';
                    if (
                        (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled')
                        && !attachmentPending && !cleanupPending && !job.publication_pending
                    ) {
                        row.setAttribute('data-media-terminal', '1');
                        var cleanupChanged = previousCleanupStatus && previousCleanupStatus !== job.cleanup_status;
                        var publicationChanged = previousPublicationPending !== Boolean(job.publication_pending)
                            || previousCanPublish !== Boolean(job.can_publish);
                        var statusChangedToTerminal = (
                            (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled')
                            && previousStatus !== job.status
                        );
                        if (
                            statusChangedToTerminal
                            || cleanupChanged
                            || publicationChanged
                        ) {
                            refreshStatusRegion();
                        }
                    }
                });
            })
            .catch(function () {
                // Durable PostgreSQL values remain visible when Redis/API polling is unavailable.
            });
    }
    timer = window.setInterval(pollStatusRows, 5000);
    cleanup = function () { window.clearInterval(timer); timer = null; };
    }
    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:after:swap', init);
    document.addEventListener('htmx:before:swap', function () { if (cleanup) cleanup(); });
})();
