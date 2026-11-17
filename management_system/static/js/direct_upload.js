/*
 * Direct-to-R2 uploader for annotated forms.
 *
 * A form opts in with data-direct-upload. Each file input inside it is mapped
 * to a server purpose with data-upload-purpose. On submit the script asks the
 * app for one short-lived capability per file, PUTs the bytes straight to the
 * private staging key, then replaces the file with a hidden
 * direct_upload_token_<field> value so the ordinary form POST carries only the
 * capability. Without JavaScript the form still posts, and the server rejects
 * the request with the same translated message instead of accepting a body.
 */
(function () {
    'use strict';

    var DEFAULT_MESSAGES = {
        preparing: 'Preparing upload...',
        uploading: 'Uploading %(percent)s%',
        failed: 'The upload failed. Please try again.',
        tooLarge: 'This file is larger than the allowed limit.',
        unsupported: 'This file type is not allowed.',
        storage: 'File storage is unavailable. Try again shortly.'
    };

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

    function hiddenTokenInput(form, key) {
        var name = 'direct_upload_token_' + key;
        var existing = form.querySelector('input[type="hidden"][name="' + name + '"]');
        if (existing) {
            return existing;
        }
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        form.appendChild(input);
        return input;
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

    function bindForm(form) {
        if (form.dataset.directUploadBound === '1') {
            return;
        }
        form.dataset.directUploadBound = '1';
        var labels = messages(form);

        form.addEventListener('submit', function (event) {
            var inputs = Array.prototype.slice.call(
                form.querySelectorAll('input[type="file"][data-upload-purpose]')
            );
            var pending = inputs.filter(function (input) {
                return input.files && input.files.length > 0;
            });
            if (!pending.length) {
                return;
            }
            if (form.dataset.directUploadInFlight === '1') {
                event.preventDefault();
                return;
            }
            event.preventDefault();
            form.dataset.directUploadInFlight = '1';
            var submitter = form.querySelector('[type="submit"]');
            if (submitter) {
                submitter.disabled = true;
            }
            var chain = Promise.resolve();
            pending.forEach(function (input) {
                chain = chain.then(function () {
                    var file = input.files[0];
                    var key = fieldKey(input);
                    setStatus(form, labels.preparing);
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
                            setStatus(form, formatMessage(labels.uploading, { percent: percent }));
                        }).then(function () {
                            hiddenTokenInput(form, key).value = grant.token;
                            clearFile(input);
                        });
                    });
                });
            });
            chain.then(function () {
                setStatus(form, '');
                form.dataset.directUploadInFlight = '0';
                if (submitter) {
                    submitter.disabled = false;
                }
                form.submit();
            }).catch(function (error) {
                form.dataset.directUploadInFlight = '0';
                if (submitter) {
                    submitter.disabled = false;
                }
                var message = labels.failed;
                if (error && error.code === 'file_too_large') {
                    message = labels.tooLarge;
                } else if (error && error.code === 'unsupported_type') {
                    message = labels.unsupported;
                } else if (error && error.code === 'storage_unavailable') {
                    message = labels.storage;
                } else if (error && error.message && error.code === '') {
                    message = error.message;
                }
                setStatus(form, message, true);
            });
        });
    }

    function scan(root) {
        var scope = root && root.querySelectorAll ? root : document;
        Array.prototype.forEach.call(
            scope.querySelectorAll('form[data-direct-upload]'),
            bindForm
        );
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
