/* Server-side media processing upload page script.
 *
 * The browser never runs media encoding, never generates HLS/TS/MP3 outputs,
 * never rewrites playlists, and never constructs final R2 keys. It only:
 *   1. Validates selected original files client-side.
 *   2. Creates one independent MediaProcessingJob per file through Django.
 *   3. Uploads each original file directly to the returned private R2 staging
 *      URL with XHR progress (no Django request body).
 *   4. Sends the authenticated source-complete acknowledgement per job.
 *   5. Polls the durable job status (PostgreSQL + Redis live fallback) per job.
 *   6. Offers an independent retry for failed jobs with a retained source.
 *
 * Frozen selector: window.__adminUploadQueue remains a simple per-file
 * queue/state holder, never a batch aggregate.
 */
(function () {
    'use strict';

    // Frozen global: simple per-file queue/state holder (not a batch model).
    window.__adminUploadQueue = window.__adminUploadQueue || { items: [], running: false };

    var root = document.getElementById('content');
    if (!root || root.getAttribute('data-job-create-url') === null) {
        return;
    }
    // Guard against double initialization on the same DOM node (HTMX swaps
    // create a fresh #content, so re-entry on a new node still works).
    if (root.getAttribute('data-media-upload-initialized') === '1') {
        return;
    }
    root.setAttribute('data-media-upload-initialized', '1');

    // ---- Configuration (server-provided via data attributes) ----
    var CONFIG = {
        createUrl: root.getAttribute('data-job-create-url') || '',
        sourceCompleteTemplate: root.getAttribute('data-source-complete-url-template') || '',
        statusTemplate: root.getAttribute('data-status-url-template') || '',
         retryTemplate: root.getAttribute('data-retry-url-template') || '',
         attachmentRetryTemplate: root.getAttribute('data-attachment-retry-url-template') || '',
        placeholderUuid: root.getAttribute('data-placeholder-uuid') || '00000000-0000-0000-0000-000000000000'
    };

    function attr(name, fallback) {
        var value = root.getAttribute(name);
        return value === null || value === '' ? fallback : value;
    }

    function jsonAttr(name, fallback) {
        var raw = root.getAttribute(name);
        if (!raw) {
            return fallback;
        }
        try {
            return JSON.parse(raw);
        } catch (e) {
            return fallback;
        }
    }

    // ---- Translations (template-provided) ----
    var T = {
        uploadingSource: attr('data-trans-uploading-source', gettext('Uploading...')),
        waitingForUpload: attr('data-trans-waiting-for-upload', gettext('Waiting for upload')),
        sourceComplete: attr('data-trans-source-complete', gettext('Source upload complete')),
        uploadFailed: attr('data-trans-upload-failed', gettext('Upload failed')),
        networkError: attr('data-trans-network-error', gettext('Network error during upload')),
        invalidFileType: attr('data-trans-invalid-file-type', gettext('Invalid file type: {type}. Allowed types: MP4, MP3, PDF')),
        fileSizeExceeded: attr('data-trans-file-size-exceeded', gettext('File size exceeds maximum: {size}MB. Maximum: 2048MB')),
        noFilesSelected: attr('data-trans-no-files', gettext('No files selected')),
        invalidFiles: attr('data-trans-invalid-files', gettext('Some files are invalid and will not be uploaded')),
        valid: attr('data-trans-valid', gettext('Valid')),
         retry: attr('data-trans-retry', gettext('Retry')),
         retryAttachment: attr('data-trans-retry-attachment', gettext('Retry lesson attachment')),
        retrying: attr('data-trans-retrying', gettext('Retrying...')),
        retryUnavailable: attr('data-trans-retry-unavailable', gettext('Retry is not available because the original source is no longer retained.')),
        csrfMissing: attr('data-trans-csrf-missing', gettext('The page security token is missing. Reload the page and try again.')),
        jobCreateFailed: attr('data-trans-job-create-failed', gettext('Could not start the media upload.')),
        statusFetchFailed: attr('data-trans-status-fetch-failed', gettext('Could not load the job status.')),
        typeLabels: {
            mp4: attr('data-trans-type-mp4', gettext('MP4 Video')),
            mp3: attr('data-trans-type-mp3', gettext('MP3 Audio')),
            pdf: attr('data-trans-type-pdf', gettext('PDF Document'))
        },
        statusLabels: jsonAttr('data-status-labels', {}),
        phaseLabels: jsonAttr('data-phase-labels', {})
    };

    function message(template, params) {
        var output = template || '';
        Object.keys(params || {}).forEach(function (key) {
            output = output.replace('{' + key + '}', params[key]);
        });
        return output;
    }

    // ---- CSRF (existing admin body hx-headers contract) ----
    function getCSRFToken() {
        var raw = document.body.getAttribute('hx-headers');
        if (!raw) {
            return null;
        }
        try {
            var parsed = JSON.parse(raw);
            var token = parsed && parsed['X-CSRFToken'];
            return typeof token === 'string' && token.length > 0 ? token : null;
        } catch (e) {
            return null;
        }
    }

    // ---- Client-side validation (authoritative validation stays on Django) ----
    var ALLOWED_TYPES = {
        'video/mp4': { ext: '.mp4', label: 'mp4' },
        'audio/mp3': { ext: '.mp3', label: 'mp3' },
        'audio/mpeg': { ext: '.mp3', label: 'mp3' },
        'application/pdf': { ext: '.pdf', label: 'pdf' }
    };
    var ALLOWED_EXTENSIONS = ['.mp4', '.mp3', '.pdf'];
    var MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024; // 2GB

    function getFileExtension(filename) {
        var index = filename.lastIndexOf('.');
        return index >= 0 ? filename.slice(index).toLowerCase() : '';
    }

    function typeLabel(kind) {
        return T.typeLabels[kind] || T.typeLabels.pdf;
    }

    function formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB'];
        var i = Math.floor(Math.log(bytes) / Math.log(1024));
        return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + units[i];
    }

    function validateFile(file) {
        var errors = [];
        var ext = getFileExtension(file.name);
        if (ALLOWED_EXTENSIONS.indexOf(ext) === -1) {
            errors.push(message(T.invalidFileType, { type: ext || file.name }));
        }
        if (!ALLOWED_TYPES[file.type]) {
            errors.push(message(T.invalidFileType, { type: file.type || file.name }));
        }
        if (file.size > MAX_FILE_SIZE) {
            errors.push(message(T.fileSizeExceeded, { size: Math.round(file.size / (1024 * 1024)) }));
        }
        var type = ALLOWED_TYPES[file.type];
        return {
            file: file,
            isValid: errors.length === 0,
            errors: errors,
            kind: type ? type.label : 'pdf'
        };
    }

    // ---- DOM element helpers ----
    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) {
            node.className = className;
        }
        if (text !== undefined && text !== null) {
            node.textContent = text;
        }
        return node;
    }

    // ---- Rendering ----
    var fileInput = document.getElementById('formFileMultiple');
    var folderInput = document.getElementById('folder-name');
    var lessonSelect = document.getElementById('lesson-select');
    var uploadBtn = document.getElementById('upload-btn');
    var validationErrors = document.getElementById('validation-errors');
    var validationErrorList = document.getElementById('validation-error-list');
    var fileList = document.getElementById('file-list');
    var fileListItems = document.getElementById('file-list-items');
    var progressContainer = document.getElementById('progress-container');
    var driveFiles = document.getElementById('drive-files');

    if (!fileInput || !uploadBtn || !progressContainer) {
        return;
    }

    var localCardCounter = 0;

    function renderValidationErrors(invalidFiles) {
        validationErrorList.innerHTML = '';
        invalidFiles.forEach(function (result) {
            var li = el('li');
            var strong = el('strong', null, result.file.name);
            li.appendChild(strong);
            li.appendChild(document.createTextNode(': ' + result.errors.join(', ')));
            validationErrorList.appendChild(li);
        });
        validationErrors.classList.toggle('d-none', invalidFiles.length === 0);
    }

    function renderSelectedFiles(validFiles) {
        fileListItems.innerHTML = '';
        validFiles.forEach(function (result) {
            var li = el('li', 'list-group-item d-flex justify-content-between align-items-center');
            var info = el('div');
            var strong = el('strong', null, result.file.name);
            var small = el('small', 'text-muted', typeLabel(result.kind) + ' - ' + formatFileSize(result.file.size));
            info.appendChild(strong);
            info.appendChild(el('br'));
            info.appendChild(small);
            li.appendChild(info);
            li.appendChild(el('span', 'badge bg-success', T.valid));
            fileListItems.appendChild(li);
        });
        fileList.classList.toggle('d-none', validFiles.length === 0);
    }

    function cardIdFor(jobId, filename) {
        if (jobId) {
            return 'media-job-card-' + String(jobId).replace(/[^a-zA-Z0-9_-]/g, '');
        }
        localCardCounter += 1;
        return 'media-job-card-local-' + localCardCounter + '-' + String(filename || 'file').replace(/[^a-zA-Z0-9_-]/g, '-');
    }

    function createJobCard(job) {
        var card = el('div', 'ad-card mb-2 media-job-card');
        card.id = cardIdFor(job.id, job.filename);

        var body = el('div', 'ad-card-body');

        var head = el('div', 'd-flex justify-content-between align-items-start gap-2 flex-wrap');
        var info = el('div', 'media-job-info');
        var name = el('div', 'fw-semibold media-job-name', job.filename || '');
        var meta = el('div', 'small text-muted media-job-meta', formatFileSize(job.source_size || 0));
        info.appendChild(name);
        info.appendChild(meta);
         var retryBtn = el('button', 'ad-btn ad-btn--ghost ad-btn--sm media-job-retry d-none', T.retry);
         var attachmentRetryBtn = el('button', 'ad-btn ad-btn--ghost ad-btn--sm media-job-attachment-retry d-none', T.retryAttachment);
         retryBtn.type = 'button';
         attachmentRetryBtn.type = 'button';
         head.appendChild(info);
         var actions = el('div', 'ad-actions');
         actions.appendChild(retryBtn);
         actions.appendChild(attachmentRetryBtn);
         head.appendChild(actions);

        var progressWrap = el('div', 'progress mt-2 media-job-progress');
        var bar = el('div', 'progress-bar media-job-bar');
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        bar.setAttribute('aria-valuenow', '0');
        bar.style.width = '0%';
        progressWrap.appendChild(bar);

        var statusEl = el('div', 'small media-job-status mt-1', T.waitingForUpload);
        statusEl.setAttribute('role', 'status');
        statusEl.setAttribute('aria-live', 'polite');

        var errorEl = el('div', 'small text-danger media-job-error mt-1 d-none');
        errorEl.setAttribute('role', 'alert');

        body.appendChild(head);
        body.appendChild(progressWrap);
        body.appendChild(statusEl);
        body.appendChild(errorEl);
        card.appendChild(body);
        progressContainer.appendChild(card);

        return {
            jobId: job.id || null,
            filename: job.filename || '',
            card: card,
            bar: bar,
            statusEl: statusEl,
            errorEl: errorEl,
             retryBtn: retryBtn,
             attachmentRetryBtn: attachmentRetryBtn,
            pollTimer: null,
            pollStart: null,
            terminal: false
        };
    }

    function setCardProgress(state, percent) {
        var value = Math.max(0, Math.min(100, Math.round(percent || 0)));
        state.bar.setAttribute('aria-valuenow', String(value));
        state.bar.style.width = value + '%';
    }

    function setCardStatus(state, text) {
        state.statusEl.textContent = text || '';
        state.statusEl.classList.remove('d-none');
    }

    function setCardError(state, text) {
        state.errorEl.textContent = text || '';
        state.errorEl.classList.toggle('d-none', !text);
    }

     function showRetry(state, show) {
         state.retryBtn.classList.toggle('d-none', !show);
     }

     function showAttachmentRetry(state, show) {
         state.attachmentRetryBtn.classList.toggle('d-none', !show);
     }

    function stopPolling(state) {
        if (state.pollTimer) {
            window.clearTimeout(state.pollTimer);
            state.pollTimer = null;
        }
        state.pollStart = null;
    }

    // ---- URL template helpers ----
    function withJobId(template, jobId) {
        if (!template || !jobId) {
            return '';
        }
        if (CONFIG.placeholderUuid && template.indexOf(CONFIG.placeholderUuid) !== -1) {
            return template.split(CONFIG.placeholderUuid).join(jobId);
        }
        return template;
    }

    function apiPost(url, payload, csrf) {
        var headers = { 'Content-Type': 'application/json' };
        if (csrf) {
            headers['X-CSRFToken'] = csrf;
        }
        return fetch(url, {
            method: 'POST',
            headers: headers,
            body: payload === undefined ? '{}' : JSON.stringify(payload),
            credentials: 'same-origin'
        });
    }

    // ---- Direct R2 staging upload + acknowledgement (per job) ----
    function uploadOneSource(state, job, file) {
        var auth = job.upload || {};
        var method = auth.method || 'PUT';
        var url = auth.url || '';
        var headers = auth.headers || {};
        if (!url) {
            setCardError(state, T.uploadFailed);
            return;
        }

        setCardStatus(state, T.uploadingSource);

        var xhr = new XMLHttpRequest();
        xhr.open(method, url, true);
        Object.keys(headers).forEach(function (headerName) {
            xhr.setRequestHeader(headerName, headers[headerName]);
        });

        xhr.upload.onprogress = function (event) {
            if (!event.lengthComputable) {
                return;
            }
            var percent = Math.round((event.loaded / event.total) * 100);
            setCardProgress(state, percent);
            setCardStatus(state, message(T.uploadingSource) + ' ' + percent + '%');
        };

        xhr.onload = function () {
            if (xhr.status < 200 || xhr.status >= 300) {
                setCardError(state, T.uploadFailed + ' (' + xhr.status + ')');
                return;
            }
            var etag = xhr.getResponseHeader('ETag') || null;
            acknowledgeSource(state, job, file, etag);
        };

        xhr.onerror = function () {
            setCardError(state, T.networkError);
        };

        xhr.onabort = function () {
            setCardError(state, T.uploadFailed);
        };

        xhr.send(file);
    }

    function acknowledgeSource(state, job, file, etag) {
        var csrf = getCSRFToken();
        if (!csrf) {
            setCardError(state, T.csrfMissing);
            return;
        }
        var url = withJobId(CONFIG.sourceCompleteTemplate, job.id);
        if (!url) {
            setCardError(state, T.uploadFailed);
            return;
        }
        var payload = {
            source_key: job.source_key,
            size: file.size
        };
        if (etag) {
            payload.etag = etag;
        }
        apiPost(url, payload, csrf)
            .then(function (response) {
                if (response.status === 202 || response.status === 200) {
                    setCardStatus(state, T.sourceComplete);
                    setCardProgress(state, 100);
                    startPolling(state, job.id);
                    return;
                }
                return response.json().then(function (data) {
                    throw new Error((data && data.message) || T.uploadFailed);
                });
            })
            .catch(function (err) {
                setCardError(state, err && err.message ? err.message : T.uploadFailed);
            });
    }

    // ---- Status polling (independent per job, bounded) ----
    var POLL_BASE_MS = 2500;
    var POLL_MAX_MS = 15000;
    var POLL_MAX_TOTAL_MS = 2 * 60 * 60 * 1000; // bounded to the 2h job timeout

    function phaseLabel(state, job) {
        if (job.phase && T.phaseLabels[job.phase]) {
            return T.phaseLabels[job.phase];
        }
        if (job.status && T.statusLabels[job.status]) {
            return T.statusLabels[job.status];
        }
        return '';
    }

    function pollOnce(state, jobId) {
        var url = withJobId(CONFIG.statusTemplate, jobId);
        if (!url) {
            stopPolling(state);
            return;
        }
        var elapsed = state.pollStart ? (Date.now() - state.pollStart) : 0;
        if (elapsed >= POLL_MAX_TOTAL_MS) {
            stopPolling(state);
            return;
        }
        fetch(url, { method: 'GET', credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error(T.statusFetchFailed);
                }
                return response.json();
            })
            .then(function (data) {
                if (state.terminal) {
                    return;
                }
                var job = data && data.job;
                if (!job) {
                    throw new Error(T.statusFetchFailed);
                }
                var label = phaseLabel(state, job);
                setCardStatus(state, label);
                if (typeof job.progress === 'number') {
                    setCardProgress(state, job.progress);
                }
                 if (job.status === 'succeeded') {
                     setCardProgress(state, 100);
                     setCardStatus(state, T.statusLabels['succeeded'] || gettext('Succeeded'));
                     if (job.attachment_status === 'failed') {
                         state.terminal = true;
                         stopPolling(state);
                         setCardError(state, job.error_message || T.uploadFailed);
                         showAttachmentRetry(state, true);
                         return;
                     }
                     if (job.attachment_status === 'pending') {
                         schedulePoll(state, jobId);
                         return;
                     }
                     state.terminal = true;
                     stopPolling(state);
                     return;
                 }
                if (job.status === 'cancelled') {
                    state.terminal = true;
                    stopPolling(state);
                    setCardStatus(state, T.statusLabels['cancelled'] || gettext('Cancelled'));
                    return;
                }
                if (job.status === 'failed') {
                    state.terminal = true;
                    stopPolling(state);
                    setCardError(state, job.error_message || T.uploadFailed);
                    // Retry requires a retained, acknowledged source on the server.
                    if (job.source_acknowledged) {
                        showRetry(state, true);
                    } else {
                        setCardError(state, T.retryUnavailable);
                    }
                    return;
                }
                schedulePoll(state, jobId);
            })
            .catch(function (err) {
                if (state.terminal) {
                    return;
                }
                // Transient network error: keep polling with bounded backoff.
                schedulePoll(state, jobId);
            });
    }

    function schedulePoll(state, jobId) {
        if (state.terminal) {
            return;
        }
        var elapsed = state.pollStart ? (Date.now() - state.pollStart) : 0;
        if (elapsed >= POLL_MAX_TOTAL_MS) {
            stopPolling(state);
            return;
        }
        var next = POLL_BASE_MS;
        if (elapsed > 0) {
            var factor = Math.floor(elapsed / (POLL_BASE_MS * 10));
            next = Math.min(POLL_MAX_MS, POLL_BASE_MS * Math.pow(1.4, factor));
        }
        state.pollTimer = window.setTimeout(function () {
            state.pollTimer = null;
            pollOnce(state, jobId);
        }, next);
    }

    function startPolling(state, jobId) {
        stopPolling(state);
        state.terminal = false;
        state.pollStart = Date.now();
        schedulePoll(state, jobId);
    }

    function retryJob(state, jobId) {
        var csrf = getCSRFToken();
        if (!csrf) {
            setCardError(state, T.csrfMissing);
            return;
        }
        var url = withJobId(CONFIG.retryTemplate, jobId);
        if (!url) {
            setCardError(state, T.retryUnavailable);
            return;
        }
        showRetry(state, false);
        setCardStatus(state, T.retrying);
        apiPost(url, {}, csrf)
            .then(function (response) {
                if (response.status === 202 || response.status === 200) {
                    setCardError(state, '');
                    startPolling(state, jobId);
                    return;
                }
                return response.json().then(function (data) {
                    throw new Error((data && data.message) || T.retryUnavailable);
                });
            })
            .catch(function (err) {
                setCardError(state, err && err.message ? err.message : T.retryUnavailable);
            });
    }

    function retryAttachment(state, jobId) {
        var csrf = getCSRFToken();
        if (!csrf) {
            setCardError(state, T.csrfMissing);
            return;
        }
        var url = withJobId(CONFIG.attachmentRetryTemplate, jobId);
        if (!url) {
            setCardError(state, T.retryUnavailable);
            return;
        }
        showAttachmentRetry(state, false);
        setCardStatus(state, T.retrying);
        apiPost(url, {}, csrf)
            .then(function (response) {
                if (response.status === 202 || response.status === 200) {
                    setCardError(state, '');
                    startPolling(state, jobId);
                    return;
                }
                return response.json().then(function (data) {
                    throw new Error((data && data.message) || T.retryUnavailable);
                });
            })
            .catch(function (err) {
                setCardError(state, err && err.message ? err.message : T.retryUnavailable);
                showAttachmentRetry(state, true);
            });
    }

    // ---- Job initialization (one JSON call for all valid files) ----
    function createJobs(validFiles, folder, lessonId, csrf) {
        var descriptors = validFiles.map(function (result) {
            return { filename: result.file.name, size: result.file.size };
        });
        var payload = {
            requested_folder: folder || '',
            files: descriptors
        };
        if (lessonId) {
            payload.lesson_id = lessonId;
        }
        return apiPost(CONFIG.createUrl, payload, csrf)
            .then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok) {
                        throw new Error((data && data.message) || T.jobCreateFailed);
                    }
                    return data;
                });
            });
    }

    function startUploads(validFiles) {
        var csrf = getCSRFToken();
        if (!csrf) {
            renderValidationErrors([]);
            renderSelectedFiles([]);
            var errCard = el('div', 'ad-alert ad-alert--danger');
            errCard.setAttribute('role', 'alert');
            errCard.textContent = T.csrfMissing;
            progressContainer.appendChild(errCard);
            return;
        }
        var folder = folderInput ? folderInput.value : '';
        var lessonId = lessonSelect && lessonSelect.value ? lessonSelect.value : null;

        window.__adminUploadQueue.running = true;
        uploadBtn.disabled = true;

        createJobs(validFiles, folder, lessonId, csrf)
            .then(function (data) {
                var jobs = (data && data.jobs) || [];
                jobs.forEach(function (job, index) {
                    // Django preserves descriptor order and creates one
                    // independent job per descriptor. Index mapping also
                    // handles two selected files with the same filename.
                    var file = validFiles[index] ? validFiles[index].file : null;
                    if (!file) {
                        return;
                    }
                    var state = createJobCard(job);
                    window.__adminUploadQueue.items.push({
                        jobId: job.id,
                        filename: job.filename || file.name,
                        state: state,
                        file: file
                    });
                     state.retryBtn.addEventListener('click', function () {
                         retryJob(state, job.id);
                     });
                     state.attachmentRetryBtn.addEventListener('click', function () {
                         retryAttachment(state, job.id);
                     });
                    uploadOneSource(state, job, file);
                });
            })
            .catch(function (err) {
                var errCard = el('div', 'ad-alert ad-alert--danger');
                errCard.setAttribute('role', 'alert');
                errCard.textContent = (err && err.message) || T.jobCreateFailed;
                progressContainer.appendChild(errCard);
            })
            .then(function () {
                window.__adminUploadQueue.running = false;
                uploadBtn.disabled = false;
                if (fileInput) {
                    fileInput.value = '';
                }
                renderSelectedFiles([]);
            });
    }

    // ---- Events ----
    if (fileInput) {
        fileInput.addEventListener('change', function () {
            var files = Array.prototype.slice.call(fileInput.files || []);
            var results = files.map(validateFile);
            var validFiles = results.filter(function (r) { return r.isValid; });
            var invalidFiles = results.filter(function (r) { return !r.isValid; });
            renderValidationErrors(invalidFiles);
            renderSelectedFiles(validFiles);
        });
    }

    if (uploadBtn) {
        uploadBtn.addEventListener('click', function () {
            var files = Array.prototype.slice.call(fileInput ? fileInput.files : []);
            if (files.length === 0) {
                renderValidationErrors([]);
                var emptyCard = el('div', 'ad-alert ad-alert--warning');
                emptyCard.setAttribute('role', 'alert');
                emptyCard.textContent = T.noFilesSelected;
                progressContainer.appendChild(emptyCard);
                return;
            }
            var results = files.map(validateFile);
            var validFiles = results.filter(function (r) { return r.isValid; });
            var invalidFiles = results.filter(function (r) { return !r.isValid; });
            renderValidationErrors(invalidFiles);
            renderSelectedFiles(validFiles);
            if (validFiles.length === 0) {
                var invalidCard = el('div', 'ad-alert ad-alert--warning');
                invalidCard.setAttribute('role', 'alert');
                invalidCard.textContent = T.invalidFiles;
                progressContainer.appendChild(invalidCard);
                return;
            }
            startUploads(validFiles);
        });
    }

    // ---- Drive folder selection hooks ----
    // Keep the existing drive fragment contract: folder clicks and the back
    // button update #folder-name without reloading the upload page. #drive-files
    // persists across HTMX swaps (only its innerHTML is replaced), so one
    // delegated listener covers every navigation.
    function folderValueFromHref(href) {
        if (!href) {
            return null;
        }
        try {
            var url = new URL(href, window.location.origin);
            var marker = '/folder/';
            var index = url.pathname.indexOf(marker);
            if (index === -1) {
                return null;
            }
            var rest = url.pathname.slice(index + marker.length);
            if (rest.endsWith('/')) {
                rest = rest.slice(0, -1);
            }
            return decodeURIComponent(rest);
        } catch (e) {
            return null;
        }
    }

    if (driveFiles && folderInput) {
        driveFiles.addEventListener('click', function (event) {
            var target = event.target && event.target.closest ? event.target.closest('[hx-get]') : null;
            if (!target) {
                return;
            }
            var href = target.getAttribute('hx-get');
            var folder = folderValueFromHref(href);
            if (folder === null) {
                return;
            }
            folderInput.value = folder === 'None' ? '' : folder;
        });
    }
})();
