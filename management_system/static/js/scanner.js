(function () {
    'use strict';

    // The camera keeps running between students. A decoded code is not posted
    // again for this window so a code left in front of the lens does not create
    // a second request; the server also rejects true duplicates.
    var CODE_CACHE_MS = 5000;
    var FEEDBACK_MS = 2000;

    var active = null;
    var jsqrLoading = false;
    var jsqrFailed = false;
    var sending = false;
    var pendingCode = null;
    var recentCodes = Object.create(null);
    var feedbackTimer = null;
    var mode = 'entrance';
    var audioContext = null;

    function t(message) {
        return typeof gettext === 'function' ? gettext(message) : message;
    }

    function stop() {
        if (!active) return;
        if (active.timer) { window.clearTimeout(active.timer); active.timer = null; }
        if (active.stream) active.stream.getTracks().forEach(function (track) { track.stop(); });
        if (active.video) { active.video.pause(); active.video.srcObject = null; active.video.hidden = true; }
        if (active.start) active.start.hidden = false;
        if (active.stop) active.stop.hidden = true;
        active = null;
    }

    function csrfToken() {
        var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return field ? field.value : '';
    }

    function scanUrlTemplate() {
        var node = document.getElementById('scanner-video');
        return node ? node.getAttribute('data-scan-url') || '' : '';
    }

    function readToken(raw) {
        if (typeof raw !== 'string') return null;
        var value = raw.replace(/^\uFEFF/, '').trim();
        if (!value) return null;
        if (!/^(?:https?:\/\/|\/)/i.test(value)) return null;
        var parsed;
        try {
            parsed = new URL(value, window.location.origin);
        } catch (error) {
            return null;
        }
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
        var match = parsed.pathname.match(/^\/(?:(?:en|ar)\/)?scan\/([^/?#]+)\/?$/);
        if (!match || parsed.search || parsed.hash) return null;
        try {
            return decodeURIComponent(match[1]);
        } catch (error) {
            return null;
        }
    }

    function beep(ok) {
        try {
            if (!audioContext) {
                var AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                audioContext = new AudioCtx();
            }
            if (audioContext.state === 'suspended') audioContext.resume();
            var oscillator = audioContext.createOscillator();
            var gain = audioContext.createGain();
            oscillator.type = 'sine';
            oscillator.frequency.value = ok ? 880 : 220;
            gain.gain.setValueAtTime(0.06, audioContext.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + (ok ? 0.12 : 0.28));
            oscillator.connect(gain);
            gain.connect(audioContext.destination);
            oscillator.start();
            oscillator.stop(audioContext.currentTime + (ok ? 0.12 : 0.28));
        } catch (error) { /* feedback is best effort */ }
    }

    function vibrate(ok) {
        if (!navigator.vibrate) return;
        try {
            navigator.vibrate(ok ? 60 : [40, 60, 40]);
        } catch (error) { /* ignore */ }
    }

    function feedback(kind, title, detail) {
        var box = document.getElementById('scan-feedback');
        if (!box) return;
        box.hidden = false;
        box.className = 'aw-scan-feedback is-' + kind;
        box.innerHTML = '';
        var heading = document.createElement('b');
        heading.textContent = title;
        box.appendChild(heading);
        if (detail) {
            var line = document.createElement('span');
            line.textContent = detail;
            box.appendChild(line);
        }
        if (feedbackTimer) window.clearTimeout(feedbackTimer);
        feedbackTimer = window.setTimeout(function () {
            box.hidden = true;
            box.textContent = '';
        }, FEEDBACK_MS);
    }

    function clearFeedback() {
        var box = document.getElementById('scan-feedback');
        if (!box) return;
        if (feedbackTimer) window.clearTimeout(feedbackTimer);
        box.hidden = true;
        box.textContent = '';
    }

    function actionLabel(action) {
        return action === 'exit' ? t('Exit') : t('Entrance');
    }

    function modeLabel() {
        return mode === 'exit' ? t('Next scan records exit') : t('Next scan records entrance');
    }

    function renderMode() {
        var toggle = document.getElementById('scan-mode-toggle');
        var chip = document.getElementById('scan-mode');
        var frame = document.querySelector('.aw-scan-frame');
        if (toggle) {
            toggle.setAttribute('aria-pressed', mode === 'exit' ? 'true' : 'false');
            toggle.classList.toggle('is-exit', mode === 'exit');
            var text = toggle.querySelector('span');
            if (text) text.textContent = mode === 'exit' ? t('Exit mode') : t('Entrance mode');
        }
        if (chip) chip.textContent = modeLabel();
        if (frame) frame.classList.toggle('is-exit', mode === 'exit');
    }

    function resetMode() {
        if (mode !== 'exit') return;
        mode = 'entrance';
        renderMode();
    }

    function toggleMode() {
        mode = mode === 'exit' ? 'entrance' : 'exit';
        renderMode();
        clearFeedback();
    }

    function prependRecent(payload) {
        var body = document.getElementById('recent-scans-body');
        if (!body || !payload || !payload.student) return;
        var row = document.createElement('tr');
        var time = document.createElement('td');
        time.textContent = (payload.scanned_at || '').slice(0, 5);
        var student = document.createElement('td');
        var main = document.createElement('span');
        main.className = 'aw-cell-main';
        var holder = document.createElement('span');
        holder.className = 'aw-cell-main-text';
        var name = document.createElement('b');
        name.textContent = payload.student.name;
        var username = document.createElement('span');
        username.textContent = payload.student.username;
        holder.appendChild(name);
        holder.appendChild(username);
        main.appendChild(holder);
        student.appendChild(main);
        var action = document.createElement('td');
        var pill = document.createElement('span');
        pill.className = 'aw-pill ' + (payload.action === 'exit' ? 'aw-pill--blue' : 'aw-pill--green');
        pill.textContent = actionLabel(payload.action);
        action.appendChild(pill);
        var source = document.createElement('td');
        source.textContent = payload.source_label || payload.source || '';
        row.appendChild(time);
        row.appendChild(student);
        row.appendChild(action);
        row.appendChild(source);
        body.insertBefore(row, body.firstChild);
        while (body.children.length > 6) body.removeChild(body.lastElementChild);
        var empty = document.getElementById('recent-scans-empty');
        if (empty) empty.hidden = true;
        var wrap = document.getElementById('recent-scans-table');
        if (wrap) wrap.hidden = false;
    }

    function handleResult(payload, ok) {
        if (ok) {
            var name = payload.student ? payload.student.name : '';
            var detail = actionLabel(payload.action) + (payload.course ? ' - ' + payload.course : '');
            feedback('success', name ? name + ' - ' + detail : detail,
                     (payload.scanned_at || '').slice(0, 5));
            beep(true);
            vibrate(true);
            prependRecent(payload);
        } else {
            var who = payload.student ? payload.student.name + ' - ' : '';
            feedback('error', who + (payload.error || t('The scan was rejected.')), '');
            beep(false);
            vibrate(false);
        }
    }

    function finishRequest(code) {
        sending = false;
        if (pendingCode && pendingCode !== code) {
            var next = pendingCode;
            pendingCode = null;
            sendScan(next);
        } else {
            pendingCode = null;
        }
    }

    function sendScan(token, options) {
        var notify = !!(options && options.notify);
        if (sending) { pendingCode = token; return; }
        var now = Date.now();
        if (recentCodes[token] && now - recentCodes[token] < CODE_CACHE_MS) {
            // The camera re-reads a code that is still in front of the lens; a
            // deliberate lookup instead tells the operator it was just scanned.
            if (notify) {
                feedback('success', t('This student was scanned a moment ago.'), '');
            }
            return;
        }
        var template = scanUrlTemplate();
        if (!template) return;
        sending = true;
        // Replace the action first: a token that happens to contain the
        // placeholder text can then never consume the action slot.
        var url = template
            .replace('__action__', mode)
            .replace('__token__', encodeURIComponent(token));
        var scannedMode = mode;
        fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'fetch',
                'Accept': 'application/json'
            },
            credentials: 'same-origin'
        }).then(function (response) {
            return response.json().catch(function () { return null; }).then(function (payload) {
                if (response.status === 403) {
                    // An expired session or a revoked capability redirects or
                    // refuses here; never present that as a recorded scan.
                    return {
                        ok: false,
                        payload: { error: t('Your session ended. Sign in again and reload the scanner.') },
                    };
                }
                // A login redirect renders HTML, so only a real recorded answer
                // counts as success.
                return {
                    ok: response.ok && !!payload && payload.status === 'recorded',
                    payload: payload || {},
                };
            });
        }).then(function (result) {
            recentCodes[token] = Date.now();
            handleResult(result.payload || {}, result.ok);
            if (scannedMode === 'exit') resetMode();
        }).catch(function () {
            feedback('error', t('The scan could not be saved. Check the connection and try again.'), '');
            beep(false);
            vibrate(false);
        }).then(function () {
            finishRequest(token);
        });
    }

    function handleCode(raw) {
        var token = readToken(raw);
        if (!token) {
            feedback('error', t('Invalid QR content.'), '');
            beep(false);
            vibrate(false);
            return;
        }
        sendScan(token);
    }

    function startJsqrLoop(local) {
        var canvas = document.createElement('canvas');
        var context = canvas.getContext('2d', { willReadFrequently: true });
        function detect() {
            if (active !== local || !local.stream) return;
            try {
                if (local.video.videoWidth) {
                    var longSide = Math.max(local.video.videoWidth, local.video.videoHeight);
                    var scale = Math.min(1, 720 / longSide);
                    var width = Math.max(1, Math.floor(local.video.videoWidth * scale));
                    var height = Math.max(1, Math.floor(local.video.videoHeight * scale));
                    canvas.width = width;
                    canvas.height = height;
                    context.drawImage(local.video, 0, 0, width, height);
                    var imageData = context.getImageData(0, 0, width, height);
                    var code = window.jsQR(imageData.data, imageData.width, imageData.height);
                    if (code && code.data) handleCode(code.data);
                }
            } catch (error) { /* keep scanning */ }
            if (active === local) local.timer = window.setTimeout(detect, 120);
        }
        detect();
    }

    function loadJsqr(local) {
        var url = local.video.dataset.jsqrUrl;
        if (window.jsQR) { startJsqrLoop(local); return; }
        if (jsqrFailed || !url) {
            feedback('error', t('QR scanning is not available in this browser. Type the QR token in the Find Student field as a fallback.'), '');
            return;
        }
        if (jsqrLoading) return;
        jsqrLoading = true;
        var script = document.createElement('script');
        script.src = url;
        script.onload = function () {
            jsqrLoading = false;
            if (active) startJsqrLoop(active);
        };
        script.onerror = function () {
            jsqrLoading = false;
            jsqrFailed = true;
            feedback('error', t('QR scanning is not available in this browser. Type the QR token in the Find Student field as a fallback.'), '');
        };
        document.head.appendChild(script);
    }

    function scan(local) {
        if ('BarcodeDetector' in window) {
            var detector = new BarcodeDetector({ formats: ['qr_code'] });
            async function detect() {
                if (active !== local || !local.stream) return;
                try {
                    var codes = await detector.detect(local.video);
                    if (codes.length && codes[0].rawValue) handleCode(codes[0].rawValue);
                } catch (error) { /* keep scanning */ }
                if (active === local) window.requestAnimationFrame(detect);
            }
            detect();
            return;
        }
        loadJsqr(local);
    }

    function showCameraError(message) {
        var errorBox = document.getElementById('camera-error');
        if (!errorBox) return;
        errorBox.textContent = message;
        errorBox.hidden = false;
    }

    async function startCamera(local) {
        var errorBox = document.getElementById('camera-error');
        var resultBox = document.getElementById('scan-result');
        if (errorBox) errorBox.hidden = true;
        if (resultBox) resultBox.textContent = '';
        // One stream per page: a second request (auto-start plus a tap, or an
        // autoplay failure) must not leave an orphan camera running.
        if (local.stream) {
            local.stream.getTracks().forEach(function (track) { track.stop(); });
            local.stream = null;
        }
        if (active === local) {
            active = null;
            local.video.pause();
            local.video.srcObject = null;
        }
        try {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error('camera-unavailable');
            }
            local.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' } },
                audio: false
            });
            active = local;
            local.video.srcObject = local.stream;
            local.video.hidden = false;
            local.start.hidden = true;
            local.stop.hidden = false;
            local.video.setAttribute('playsinline', 'true');
            await local.video.play();
            scan(local);
            return true;
        } catch (error) {
            if (local.stream) {
                local.stream.getTracks().forEach(function (track) { track.stop(); });
                local.stream = null;
            }
            active = null;
            local.video.srcObject = null;
            local.video.hidden = true;
            local.start.hidden = false;
            showCameraError(error && error.name === 'NotAllowedError'
                ? t('Camera permission was denied. Allow camera access and try again.')
                : t('Camera access is unavailable on this device. Tap the button to try again.'));
            return false;
        }
    }

    function init() {
        var video = document.getElementById('scanner-video');
        var start = document.getElementById('start-camera');
        var stopButton = document.getElementById('stop-camera');
        if (!video || !start || !stopButton || start.dataset.scannerReady === 'true') return;
        start.dataset.scannerReady = 'true';
        var toggle = document.getElementById('scan-mode-toggle');
        var local = { video: video, start: start, stop: stopButton, stream: null, timer: null };

        start.addEventListener('click', function () {
            startCamera(local);
        });
        stopButton.addEventListener('click', function () { stop(); });
        if (toggle) toggle.addEventListener('click', toggleMode);
        renderMode();

        var form = document.getElementById('student-lookup-form');
        if (form) {
            form.addEventListener('submit', function (event) {
                if (!window.fetch) return;
                event.preventDefault();
                var lookupError = document.getElementById('lookup-error');
                if (lookupError) lookupError.hidden = true;
                var body = new FormData(form);
                fetch(form.action, {
                    method: 'POST',
                    body: body,
                    headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' },
                    credentials: 'same-origin'
                }).then(function (response) {
                    return response.json().catch(function () { return {}; }).then(function (payload) {
                        return { ok: response.ok, status: response.status, payload: payload };
                    });
                }).then(function (result) {
                    if (result.ok && result.payload.token) {
                        sendScan(result.payload.token, { notify: true });
                        return;
                    }
                    if (result.status === 403) {
                        if (lookupError) {
                            lookupError.textContent = t('Your session ended. Sign in again and reload the scanner.');
                            lookupError.hidden = false;
                        }
                        return;
                    }
                    if (!result.payload || !result.payload.error) {
                        if (lookupError) {
                            lookupError.textContent = t('The lookup failed. Check the connection and try again.');
                            lookupError.hidden = false;
                        }
                        return;
                    }
                    var message = result.payload.error || t('Student not found.');
                    if (lookupError) {
                        lookupError.textContent = message;
                        lookupError.hidden = false;
                    }
                }).catch(function () {
                    if (lookupError) {
                        lookupError.textContent = t('The lookup failed. Check the connection and try again.');
                        lookupError.hidden = false;
                    }
                });
            });
        }

        // The camera opens immediately; the button only appears when the
        // browser blocks auto-start (for example a permission or gesture rule).
        startCamera(local);
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:after:swap', init);
    document.addEventListener('htmx:before:swap', stop);
    window.addEventListener('beforeunload', stop);

    // Kept for the shell contract: pages that programmatically stop scanning.
    window.awScanner = { stop: stop, mode: function () { return mode; } };
}());
