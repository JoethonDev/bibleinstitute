/*
 * Direct-to-R2 uploader for annotated forms.
 *
 * A form opts in with data-direct-upload; each file input inside it is mapped
 * to a server purpose with data-upload-purpose. Files start uploading as soon
 * as they are picked, in the background, and the resulting capability token is
 * kept in sessionStorage so leaving the page and coming back - or a rejected
 * submission - does not lose the upload. Submitting only waits for transfers
 * already in flight; it never starts new ones.
 *
 * Every picker gets an injected status line and a Remove control, so removing
 * or replacing an attachment does not need per-template markup.
 */
(function () {
    'use strict';

    var DEFAULT_MESSAGES = {
        preparing: 'Preparing upload...',
        uploading: 'Uploading %(percent)s%',
        uploaded: 'Uploaded',
        remove: 'Remove',
        failed: 'The upload failed. Please try again.',
        tooLarge: 'This file is larger than the allowed limit.',
        unsupported: 'This file type is not allowed.',
        storage: 'File storage is unavailable. Try again shortly.'
    };

    var STORAGE_PREFIX = 'directUpload:';

    function messages(form) {
        var configured = window.DIRECT_UPLOAD_I18N || {};
        var merged = {};
        Object.keys(DEFAULT_MESSAGES).forEach(function (key) {
            merged[key] = configured[key] || DEFAULT_MESSAGES[key];
        });
        if (form && form.dataset.uploadMessageFailed) {
            merged.failed = form.dataset.uploadMessageFailed;
        }
        return merged;
    }

    function csrfToken(form) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) {
            return meta.content;
        }
        var inherited = document.body && document.body.getAttribute('hx-headers:inherited');
        if (inherited) {
            try {
                var parsed = JSON.parse(inherited);
                if (parsed && parsed['X-CSRFToken']) {
                    return parsed['X-CSRFToken'];
                }
            } catch (error) {
                /* fall through to the cookie */
            }
        }
        var input = form && form.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) {
            return input.value;
        }
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function statusElement(form) {
        return form.querySelector('[data-upload-status]');
    }

    function setStatus(form, text, isError) {
        var element = statusElement(form);
        if (!element) {
            return;
        }
        element.textContent = text || '';
        element.hidden = !text;
        element.classList.toggle('is-error', Boolean(isError));
    }

    function formatMessage(message, values) {
        return String(message || '').replace(/%\((\w+)\)s/g, function (_match, key) {
            return values && values[key] !== undefined ? values[key] : '';
        });
    }

    function fieldKey(input) {
        return input.getAttribute('data-upload-field') || input.name || 'file';
    }

    function storageKey(form, key) {
        var scope = form.getAttribute('action') || window.location.pathname;
        return STORAGE_PREFIX + scope + ':' + key;
    }

    function readStored(form, key) {
        try {
            var raw = window.sessionStorage.getItem(storageKey(form, key));
            var parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function writeStored(form, key, tokens) {
        try {
            if (!tokens.length) {
                window.sessionStorage.removeItem(storageKey(form, key));
                return;
            }
            window.sessionStorage.setItem(storageKey(form, key), JSON.stringify(tokens));
        } catch (error) {
            /* storage may be unavailable in private modes; uploads still work */
        }
    }

    function appendTokenInput(form, key, token) {
        // Always a new input: a multi-file picker submits the same field name
        // once per file, so the server reads the tokens with getlist().
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'direct_upload_token_' + key;
        input.value = token;
        input.dataset.uploadToken = token;
        form.appendChild(input);
        return input;
    }

    function clearTokenInputs(form, key) {
        Array.prototype.slice.call(
            form.querySelectorAll('input[type="hidden"][name="direct_upload_token_' + key + '"]')
        ).forEach(function (input) {
            input.parentNode.removeChild(input);
        });
    }

    function authorize(form, payload) {
        return fetch(form.getAttribute('data-direct-upload'), {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken(form)
            },
            body: JSON.stringify(payload)
        }).then(function (response) {
            return response.json().catch(function () {
                return {};
            }).then(function (data) {
                if (!response.ok) {
                    var error = new Error(data.error || 'authorize_failed');
                    error.code = data.code || '';
                    error.status = response.status;
                    throw error;
                }
                return data;
            });
        });
    }

    function discardUrl(form) {
        var explicit = form.getAttribute('data-direct-upload-discard');
        if (explicit) {
            return explicit;
        }
        // Both endpoints share the same route prefix, so the discard URL is
        // derived when a template only carries the authorize attribute.
        var authorizeUrl = form.getAttribute('data-direct-upload') || '';
        return authorizeUrl.indexOf('/authorize/') >= 0
            ? authorizeUrl.replace('/authorize/', '/discard/')
            : '';
    }

    function discard(form, token) {
        var url = discardUrl(form);
        if (!url || !token) {
            return Promise.resolve();
        }
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-CSRFToken': csrfToken(form)
            },
            body: JSON.stringify({token: token})
        }).catch(function () {
            /* a failed discard leaves the sweeper to expire it */
        });
    }

    function putFile(grant, file, onProgress) {
        return new Promise(function (resolve, reject) {
            var request = new XMLHttpRequest();
            request.open('PUT', grant.upload_url, true);
            request.setRequestHeader('Content-Type', file.type || grant.content_types[0] || 'application/octet-stream');
            request.upload.onprogress = function (event) {
                if (event.lengthComputable && onProgress) {
                    onProgress(Math.round((event.loaded / event.total) * 100));
                }
            };
            request.onerror = function () {
                reject(new Error('transfer_failed'));
            };
            request.onload = function () {
                if (request.status >= 200 && request.status < 300) {
                    resolve();
                } else {
                    reject(new Error('transfer_status_' + request.status));
                }
            };
            request.send(file);
        });
    }

    function clearFile(input) {
        try {
            input.value = '';
        } catch (error) {
            /* value clearing is best effort */
        }
    }

    function ensureControls(input, labels) {
        if (input.dataset.uploadControls === '1') {
            return input.uploadControls;
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'direct-upload-controls';
        var status = document.createElement('p');
        status.className = 'direct-upload-status';
        status.setAttribute('role', 'status');
        status.hidden = true;
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'direct-upload-remove';
        remove.textContent = labels.remove;
        remove.hidden = true;
        wrapper.appendChild(status);
        wrapper.appendChild(remove);
        input.insertAdjacentElement('afterend', wrapper);
        input.dataset.uploadControls = '1';
        input.uploadControls = {status: status, remove: remove};
        return input.uploadControls;
    }

    function renderInputState(form, input, labels) {
        var controls = ensureControls(input, labels);
        var stored = readStored(form, fieldKey(input));
        controls.status.textContent = stored.length ? labels.uploaded : '';
        controls.status.hidden = !stored.length;
        controls.remove.hidden = !stored.length;
    }

    function bindInput(form, input, labelSet) {
        if (input.dataset.uploadBound === '1') {
            return;
        }
        input.dataset.uploadBound = '1';
        renderInputState(form, input, labelSet);

        input.addEventListener('change', function () {
            var files = Array.prototype.slice.call(input.files || []);
            if (!files.length) {
                return;
            }
            clearFile(input);
            uploadFiles(form, input, files, labelSet);
        });

        var controls = ensureControls(input, labelSet);
        controls.remove.addEventListener('click', function () {
            var key = fieldKey(input);
            var tokens = readStored(form, key);
            tokens.forEach(function (token) {
                discard(form, token);
            });
            clearTokenInputs(form, key);
            writeStored(form, key, []);
            clearFile(input);
            renderInputState(form, input, labelsOf(form));
        });
    }

    function labelsOf(form) {
        if (!form.uploadLabels) {
            form.uploadLabels = messages(form);
        }
        return form.uploadLabels;
    }

    function uploadFiles(form, input, files, labels) {
        var key = fieldKey(input);
        var controls = ensureControls(input, labels);
        form.uploadPending = (form.uploadPending || 0) + 1;
        var chain = Promise.resolve();
        files.forEach(function (file) {
            chain = chain.then(function () {
                controls.status.hidden = false;
                controls.status.classList.remove('is-error');
                controls.status.textContent = labels.preparing;
                return authorize(form, {
                    purpose: input.getAttribute('data-upload-purpose'),
                    document_type: input.getAttribute('data-upload-document-type') || undefined,
                    target: input.getAttribute('data-upload-target') || undefined,
                    filename: file.name,
                    size: file.size,
                    content_type: file.type || 'application/octet-stream'
                }).then(function (grant) {
                    if (grant.max_bytes && file.size > grant.max_bytes) {
                        var tooLarge = new Error(labels.tooLarge);
                        tooLarge.code = 'file_too_large';
                        throw tooLarge;
                    }
                    return putFile(grant, file, function (percent) {
                        controls.status.textContent = formatMessage(labels.uploading, {percent: percent});
                    }).then(function () {
                        appendTokenInput(form, key, grant.token);
                        var tokens = readStored(form, key);
                        tokens.push(grant.token);
                        writeStored(form, key, tokens);
                    });
                });
            });
        });
        form.uploadChain = (form.uploadChain || Promise.resolve()).then(function () {
            return chain;
        }).then(function () {
            controls.status.textContent = labels.uploaded;
            controls.status.hidden = false;
            controls.remove.hidden = false;
        }).catch(function (error) {
            controls.status.classList.add('is-error');
            controls.status.hidden = false;
            var message = labels.failed;
            if (error && error.code === 'file_too_large') {
                message = labels.tooLarge;
            } else if (error && error.code === 'unsupported_type') {
                message = labels.unsupported;
            } else if (error && error.code === 'storage_unavailable') {
                message = labels.storage;
            } else if (error && error.message && !error.code) {
                message = error.message;
            }
            controls.status.textContent = message;
        }).then(function () {
            form.uploadPending = Math.max(0, (form.uploadPending || 1) - 1);
        });
    }

    function restoreStoredTokens(form, labels) {
        Array.prototype.slice.call(
            form.querySelectorAll('input[type="file"][data-upload-purpose]')
        ).forEach(function (input) {
            var key = fieldKey(input);
            var stored = readStored(form, key);
            if (!stored.length) {
                return;
            }
            clearTokenInputs(form, key);
            stored.forEach(function (token) {
                appendTokenInput(form, key, token);
            });
            renderInputState(form, input, labels);
        });
    }

    function bindForm(form) {
        if (form.dataset.directUploadBound === '1') {
            return;
        }
        form.dataset.directUploadBound = '1';
        var labels = labelsOf(form);
        Array.prototype.slice.call(
            form.querySelectorAll('input[type="file"][data-upload-purpose]')
        ).forEach(function (input) {
            bindInput(form, input, labels);
        });
        restoreStoredTokens(form, labels);

        form.addEventListener('submit', function (event) {
            if (form.dataset.directUploadReady === '1') {
                form.dataset.directUploadReady = '0';
                return;
            }
            if (form.dataset.directUploadInFlight === '1') {
                event.preventDefault();
                return;
            }
            if (!form.uploadPending) {
                return;
            }
            event.preventDefault();
            form.dataset.directUploadInFlight = '1';
            var submitter = form.querySelector('[type="submit"]');
            (form.uploadChain || Promise.resolve()).then(function () {
                form.dataset.directUploadInFlight = '0';
                form.dataset.directUploadReady = '1';
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit(submitter || undefined);
                } else {
                    form.submit();
                }
            });
        });
    }

    function scan(root) {
        var scope = root && root.querySelectorAll ? root : document;
        Array.prototype.slice.call(
            scope.querySelectorAll('form[data-direct-upload]')
        ).forEach(bindForm);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            scan(document);
        });
    } else {
        scan(document);
    }
    document.addEventListener('htmx:afterSwap', function (event) {
        scan(event.target || document);
    });
})();
