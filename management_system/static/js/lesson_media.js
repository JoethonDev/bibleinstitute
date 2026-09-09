/* Shared student lesson media initialization.
 * The course page loads this once; HTMX lesson fragments reuse the same
 * listeners instead of registering a second media/PDF implementation.
 */
(function () {
    'use strict';

    const translations = window.lessonMediaTranslations || {};

    function translate(key, fallback) {
        return translations[key] || (typeof gettext === 'function' ? gettext(fallback) : fallback);
    }

    function shouldUseNativeHls(media) {
        const native = media?.canPlayType('application/vnd.apple.mpegurl');
        if (!native) return false;
        if (!window.Hls || !Hls.isSupported()) return true;
        return 'ManagedMediaSource' in window;
    }

    function disposeVideoJsPlayers(container) {
        container.querySelectorAll('video.video-js').forEach(videoEl => {
            const player = videoEl.player;
            if (player && typeof player.dispose === 'function') {
                player.dispose();
            }
        });
    }

    function initializeVideoJs(video) {
        if (!video || video.player) return;
        const source = video.querySelector('source');
        if (!source || !source.src) return;
        if (shouldUseNativeHls(video)) {
            video.src = source.src;
            video.load();
            return;
        }
        const player = videojs(video, {
            controls: true,
            fluid: true,
            playbackRates: [0.5, 1, 1.5, 2],
            controlBar: { volumePanel: true, playbackRateMenuButton: true, playToggle: true }
        });
        player.seekButtons({ forward: 10, back: 10 });
    }

    function reloadExpiredMedia() {
        const key = `media-session-reload:${window.location.pathname}`;
        if (sessionStorage.getItem(key)) return;
        sessionStorage.setItem(key, '1');
        // Media authorization expired: refresh the page content region so all
        // signed media URLs are re-issued without a full document reload.
        if (window.htmx) {
            htmx.ajax('GET', window.location.pathname + window.location.search, {
                target: '#content',
                select: '#content',
                swap: 'outerHTML',
            });
        } else {
            window.location.reload();
        }
    }

    function initializeHlsAudio(audio) {
        if (!audio || audio.hls) return;
        const source = audio.querySelector('source');
        if (!source || !source.src) return;
        if (shouldUseNativeHls(audio)) {
            audio.src = source.src;
            audio.load();
            return;
        }
        if (!window.Hls || !Hls.isSupported()) return;
        const hls = new Hls();
        hls.loadSource(source.src);
        hls.attachMedia(audio);
        hls.on(Hls.Events.ERROR, (_event, data) => {
            if (data?.response?.code === 401 || data?.response?.code === 403) {
                reloadExpiredMedia();
            }
        });
        audio.hls = hls;
    }

    function attachMediaSource(element, url) {
        const source = element?.querySelector('source');
        if (!element || !source || !url) return;
        source.src = url;
        if (element.tagName.toLowerCase() === 'video') {
            if (shouldUseNativeHls(element)) {
                element.src = url;
                element.load();
                return;
            }
            let player = element.player;
            if (!player) {
                initializeVideoJs(element);
                player = element.player;
            }
            if (!player) return;
            player.src({ src: url, type: 'application/x-mpegURL' });
            player.one('error', () => {
                const response = player.error()?.response;
                if (response?.code === 401 || response?.code === 403) reloadExpiredMedia();
            });
        } else {
            element.load();
            initializeHlsAudio(element);
        }
    }

    async function initializePdfViewer(viewer) {
        if (!viewer || viewer.pdfDoc) return;

        const url = viewer.dataset.url;
        const canvas = viewer.querySelector('.pdf-canvas');
        const ctx = canvas.getContext('2d');
        const pageInfo = viewer.querySelector('.page-info');
        const prevBtn = viewer.querySelector('.prev');
        const nextBtn = viewer.querySelector('.next');
        let currentPage = 1;
        viewer.pdfDoc = true;

        try {
            const response = await fetch(url);
            const result = await response.json();
            const loadingTask = pdfjsLib.getDocument(result.url);
            const pdfDoc = await loadingTask.promise;

            const renderPage = (num) => {
                pdfDoc.getPage(num).then(page => {
                    canvas.height = 600;
                    canvas.width = 400;
                    const viewportDefault = page.getViewport({ scale: 1 });
                    const scale = Math.min(canvas.width / viewportDefault.width, canvas.height / viewportDefault.height);
                    const viewport = page.getViewport({ scale });
                    const tempCanvas = document.createElement('canvas');
                    const tempCtx = tempCanvas.getContext('2d');
                    tempCanvas.width = viewport.width;
                    tempCanvas.height = viewport.height;
                    page.render({ canvasContext: tempCtx, viewport }).promise.then(() => {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                        ctx.fillStyle = 'white';
                        ctx.fillRect(0, 0, canvas.width, canvas.height);
                        const xOffset = (canvas.width - viewport.width) / 2;
                        const yOffset = (canvas.height - viewport.height) / 2;
                        ctx.drawImage(tempCanvas, xOffset, yOffset);
                    });
                    pageInfo.textContent = `${translate('page', 'Page')} ${num} / ${pdfDoc.numPages}`;
                });
            };

            renderPage(currentPage);
            prevBtn.addEventListener('click', () => {
                if (currentPage <= 1) return;
                currentPage--;
                renderPage(currentPage);
            });
            nextBtn.addEventListener('click', () => {
                if (currentPage >= pdfDoc.numPages) return;
                currentPage++;
                renderPage(currentPage);
            });
        } catch (error) {
            console.error('Error loading PDF:', error);
            if (pageInfo) pageInfo.textContent = translate('pdfError', 'Error loading PDF.');
        }
    }

    function initializeAllElements(container = document) {
        container.querySelectorAll('video.video-js').forEach(initializeVideoJs);
        container.querySelectorAll('audio').forEach(initializeHlsAudio);
        container.querySelectorAll('video, audio').forEach(media => {
            media.addEventListener('error', reloadExpiredMedia, { once: true });
            media.setAttribute('controlsList', 'nodownload');
        });
        container.querySelectorAll('.pdf-viewer').forEach(initializePdfViewer);
    }

    document.addEventListener('media-session-ready', event => {
        attachMediaSource(event.detail?.element, event.detail?.url);
    });

    function swapTarget(event) {
        // The vendored HTMX 4 runtime dispatches lifecycle events with the
        // request context: detail.ctx.target holds the swap target element.
        return event.detail?.ctx?.target || event.detail?.target || null;
    }

    function isMediaSwapTarget(target) {
        return Boolean(target) && (target.id === 'display-page' || target.id === 'content');
    }

    document.body.addEventListener('htmx:before:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) {
            disposeVideoJsPlayers(target);
        }
    });

    document.body.addEventListener('htmx:after:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) {
            // A fresh content region carries newly signed media URLs, so any
            // one-shot expired-media reload guard can be released.
            sessionStorage.removeItem(`media-session-reload:${window.location.pathname}`);
            initializeAllElements(target);
        }
    });

    initializeAllElements();
})();
