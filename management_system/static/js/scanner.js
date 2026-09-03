(function () {
    'use strict';
    var active = null;

    function stop() {
        if (!active) return;
        if (active.stream) active.stream.getTracks().forEach(function (track) { track.stop(); });
        if (active.video) { active.video.pause(); active.video.srcObject = null; active.video.classList.add('d-none'); }
        if (active.start) active.start.classList.remove('d-none');
        if (active.stop) active.stop.classList.add('d-none');
        active = null;
    }

    function init() {
        var video = document.getElementById('scanner-video');
        var start = document.getElementById('start-camera');
        var stopButton = document.getElementById('stop-camera');
        if (!video || !start || !stopButton || start.dataset.scannerReady === 'true') return;
        start.dataset.scannerReady = 'true';
        var errorBox = document.getElementById('camera-error');
        var resultBox = document.getElementById('scan-result');
        var local = { video: video, start: start, stop: stopButton, stream: null };
        start.addEventListener('click', async function () {
            stop();
            errorBox.classList.add('d-none'); resultBox.textContent = '';
            try {
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('camera-unavailable');
                local.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
                active = local; video.srcObject = local.stream; video.classList.remove('d-none');
                start.classList.add('d-none'); stopButton.classList.remove('d-none'); await video.play(); scan(local);
            } catch (error) {
                errorBox.textContent = gettext('Camera access denied or unavailable.'); errorBox.classList.remove('d-none');
            }
        });
        stopButton.addEventListener('click', stop);
    }

    function scan(local) {
        if (!('BarcodeDetector' in window)) {
            document.getElementById('scan-result').textContent = gettext('BarcodeDetector not available. Type the QR token in the Find Student field as a fallback.');
            return;
        }
        var detector = new BarcodeDetector({ formats: ['qr_code'] });
        async function detect() {
            if (active !== local || !local.stream) return;
            try {
                var codes = await detector.detect(local.video);
                if (codes.length) {
                    var raw = codes[0].rawValue; stop();
                    try {
                        var parsed = new URL(raw);
                        if (parsed.origin !== window.location.origin) throw new Error('origin');
                        window.location.href = raw;
                    } catch (error) { document.getElementById('scan-result').textContent = gettext('Invalid QR content.'); }
                    return;
                }
            } catch (error) { /* keep scanning */ }
            if (active === local) window.requestAnimationFrame(detect);
        }
        detect();
    }

    document.addEventListener('DOMContentLoaded', init);
    document.addEventListener('htmx:afterSwap', init);
    document.addEventListener('htmx:beforeSwap', stop);
    window.addEventListener('beforeunload', stop);
}());
