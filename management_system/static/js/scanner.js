(function () {
    'use strict';
    var active = null;
    var jsqrLoading = false;
    var jsqrFailed = false;

    function stop() {
        if (!active) return;
        if (active.timer) { window.clearTimeout(active.timer); active.timer = null; }
        if (active.stream) active.stream.getTracks().forEach(function (track) { track.stop(); });
        if (active.video) { active.video.pause(); active.video.srcObject = null; active.video.classList.add('d-none'); }
        if (active.start) active.start.classList.remove('d-none');
        if (active.stop) active.stop.classList.add('d-none');
        active = null;
    }

    function navigateToCode(raw) {
        try {
            if (typeof raw !== 'string') throw new Error('empty');
            // QR readers can return a BOM or surrounding whitespace.  Both
            // are harmless in a URL but make the old strict parser reject a
            // valid server-generated scan path on some devices.
            var value = raw.replace(/^\uFEFF/, '').trim();
            if (!value) throw new Error('empty');
            if (!/^(?:https?:\/\/|\/)/i.test(value)) throw new Error('relative-path');
            var parsed = new URL(value, window.location.origin);
            if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('protocol');
            if (!/^\/(?:(?:en|ar)\/)?scan\/[^/?#]+\/?$/.test(parsed.pathname) || parsed.search || parsed.hash) {
                throw new Error('scan-path');
            }
            // Older QR images and canonical QR images may contain the public
            // host. Rebase only the validated scan path onto this scanner's
            // origin; never navigate to the host carried by camera content.
            if (parsed.origin !== window.location.origin) {
                parsed = new URL(parsed.pathname, window.location.origin);
            }
            stop();
            window.location.href = parsed.href;
            return true;
        } catch (error) {
            document.getElementById('scan-result').textContent = gettext('Invalid QR content.');
            return false;
        }
    }

    function startJsqrLoop(local) {
        var canvas = document.createElement('canvas');
        var context = canvas.getContext('2d', { willReadFrequently: true });
        function detect() {
            if (active !== local || !local.stream) return;
            try {
                if (local.video.videoWidth) {
                    var longSide = Math.max(local.video.videoWidth, local.video.videoHeight);
                    var scale = Math.min(1, 800 / longSide);
                    var width = Math.max(1, Math.floor(local.video.videoWidth * scale));
                    var height = Math.max(1, Math.floor(local.video.videoHeight * scale));
                    canvas.width = width;
                    canvas.height = height;
                    context.drawImage(local.video, 0, 0, width, height);
                    var imageData = context.getImageData(0, 0, width, height);
                    var code = window.jsQR(imageData.data, imageData.width, imageData.height);
                    if (code && code.data) {
                        if (navigateToCode(code.data)) return;
                    }
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
            document.getElementById('scan-result').textContent = gettext('QR scanning is not available in this browser. Type the QR token in the Find Student field as a fallback.');
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
            document.getElementById('scan-result').textContent = gettext('QR scanning is not available in this browser. Type the QR token in the Find Student field as a fallback.');
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
                    if (codes.length && navigateToCode(codes[0].rawValue)) return;
                } catch (error) { /* keep scanning */ }
                if (active === local) window.requestAnimationFrame(detect);
            }
            detect();
            return;
        }
        loadJsqr(local);
    }

    function init() {
        var video = document.getElementById('scanner-video');
        var start = document.getElementById('start-camera');
        var stopButton = document.getElementById('stop-camera');
        if (!video || !start || !stopButton || start.dataset.scannerReady === 'true') return;
        start.dataset.scannerReady = 'true';
        var errorBox = document.getElementById('camera-error');
        var resultBox = document.getElementById('scan-result');
        var local = { video: video, start: start, stop: stopButton, stream: null, timer: null };
        start.addEventListener('click', async function () {
            stop();
            errorBox.classList.add('d-none'); resultBox.textContent = '';
            try {
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('camera-unavailable');
                local.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
                active = local; video.srcObject = local.stream; video.classList.remove('d-none');
                start.classList.add('d-none'); stopButton.classList.remove('d-none');
                video.setAttribute('playsinline', 'true');
                await video.play();
                scan(local);
            } catch (error) {
                errorBox.textContent = error.name === 'NotAllowedError'
                    ? gettext('Camera permission was denied. Allow camera access and try again.')
                    : gettext('Camera access is unavailable on this device.');
                errorBox.classList.remove('d-none');
            }
        });
        stopButton.addEventListener('click', stop);
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:after:swap', init);
    document.addEventListener('htmx:before:swap', stop);
    window.addEventListener('beforeunload', stop);
}());
