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
            htmx.ajax('GET', window.location.pathname + window.location.search, { target: '#content', select: '#content', swap: 'outerHTML' });
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
    var phaseLabels = parseLabels('data-phase-labels');
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

    function updateRow(row, job) {
        var status = row.querySelector('[data-media-status-value]');
        var phase = row.querySelector('[data-media-phase-value]');
        var bar = row.querySelector('[data-media-progress-bar]');
        var value = row.querySelector('[data-media-progress-value]');
        var error = row.querySelector('[data-media-error]');
        if (status) status.textContent = statusLabels[job.status] || job.status || '';
        if (phase) phase.textContent = phaseLabels[job.phase] || job.phase || '';
        if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job.progress) || 0)) + '%';
        if (value) value.textContent = (Number(job.progress) || 0) + '%';
        if (error) error.textContent = job.error_message || '';
        var progress = row.querySelector('[role="progressbar"]');
        if (progress) progress.setAttribute('aria-valuenow', String(Math.max(0, Math.min(100, Number(job.progress) || 0))));
    }

    function pollStatusRows() {
        var rows = Array.from(root.querySelectorAll('[data-media-status-url]')).filter(function (row) {
            return row.getAttribute('data-media-terminal') !== '1' && row.getAttribute('data-media-job-id');
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
                    updateRow(row, job);
                    var attachmentPending = job.status === 'succeeded' && job.attachment_status === 'pending';
                    if ((job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') && !attachmentPending) {
                        row.setAttribute('data-media-terminal', '1');
                        if (job.status === 'failed' || (job.status === 'succeeded' && job.attachment_status === 'failed')) {
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
