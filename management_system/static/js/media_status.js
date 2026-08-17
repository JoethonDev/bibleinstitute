(function () {
    'use strict';

    var root = document.getElementById('content');
    if (!root) {
        return;
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
    var retryFailed = root.getAttribute('data-trans-retry-failed') || gettext('Retry failed');
    var csrfInput = document.querySelector('#media-status-csrf input[name="csrfmiddlewaretoken"]');
    var csrf = csrfInput ? csrfInput.value : '';
    document.querySelectorAll('[data-media-retry-url]').forEach(function (button) {
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
                window.location.reload();
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
    }

    function pollStatusRows() {
        document.querySelectorAll('[data-media-status-url]').forEach(function (row) {
            if (row.getAttribute('data-media-terminal') === '1') {
                return;
            }
            var url = row.getAttribute('data-media-status-url');
            if (!url) return;
            fetch(url, { credentials: 'same-origin' })
                .then(function (response) { return response.ok ? response.json() : null; })
                .then(function (data) {
                    var job = data && data.job;
                    if (!job) return;
                    updateRow(row, job);
                    var attachmentPending = job.status === 'succeeded' && job.attachment_status === 'pending';
                    if ((job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') && !attachmentPending) {
                        row.setAttribute('data-media-terminal', '1');
                        if (job.status === 'failed' || (job.status === 'succeeded' && job.attachment_status === 'failed')) {
                            window.location.reload();
                        }
                    }
                })
                .catch(function () {
                    // Durable PostgreSQL values remain visible when Redis/API polling is unavailable.
                });
        });
    }
    window.setInterval(pollStatusRows, 5000);
})();
