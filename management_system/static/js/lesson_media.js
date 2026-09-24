/* Shared student lesson media initialization.
 * Video.js owns the transport for audio and video where HLS is not native.
 * Safari/iOS keeps the browser's native HLS controls as the compatibility
 * path, and failed initialization always falls back to those controls.
 */
(function () {
    'use strict';

    const translations = window.lessonMediaTranslations || {};

    function translate(key, fallback) {
        return translations[key] || (typeof gettext === 'function' ? gettext(fallback) : fallback);
    }

    function isNativeHls(media) {
        if (!media || typeof media.canPlayType !== 'function') return false;
        return Boolean(
            media.canPlayType('application/vnd.apple.mpegurl')
            || media.canPlayType('application/x-mpegURL')
        );
    }

    function isVideo(media) {
        return media?.tagName?.toLowerCase() === 'video';
    }

    function mediaErrorStatus(error) {
        return Number(
            error?.status
            || error?.statusCode
            || error?.response?.status
            || error?.response?.code
            || error?.xhr?.status
        );
    }

    function reloadExpiredMedia() {
        const key = `media-session-reload:${window.location.pathname}`;
        if (sessionStorage.getItem(key)) return;
        sessionStorage.setItem(key, '1');
        // Signed media URLs are refreshed by swapping the current content region.
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

    function handleMediaError(media, player) {
        const error = player?.error?.() || media?.error;
        if ([401, 403].includes(mediaErrorStatus(error))) reloadExpiredMedia();
    }

    function registerVideoJsLanguage() {
        if (!window.videojs || typeof videojs.addLanguage !== 'function') return;
        const language = translations.language || 'en';
        videojs.addLanguage(language, {
            Play: translate('play', 'Play'),
            Pause: translate('pause', 'Pause'),
            Mute: translate('mute', 'Mute'),
            Unmute: translate('unmute', 'Unmute'),
            Fullscreen: translate('fullscreen', 'Enter fullscreen'),
            'Exit Fullscreen': translate('exitFullscreen', 'Exit fullscreen'),
            'Playback Rate': translate('playbackRate', 'Playback speed'),
            'Rewind 10 Seconds': translate('rewind', 'Rewind 10 seconds'),
            'Forward 10 Seconds': translate('forward', 'Forward 10 seconds'),
        });
    }

    function nativeFallback(media, url) {
        if (!media || !url) return;
        media.classList.remove('video-js', 'vjs-theme-city');
        media.controls = true;
        media._mediaSourceUrl = url;
        media.src = url;
        media.load();
    }

    function videoJsOptions(media) {
        const controls = [
            'playToggle',
            'volumePanel',
            'currentTimeDisplay',
            'timeDivider',
            'durationDisplay',
            'progressControl',
            'playbackRateMenuButton',
        ];
        if (isVideo(media)) controls.push('fullscreenToggle');
        return {
            controls: true,
            fluid: isVideo(media),
            responsive: isVideo(media),
            preload: 'auto',
            playbackRates: [0.75, 1, 1.25, 1.5, 2],
            language: translations.language || 'en',
            userActions: { hotkeys: true },
            html5: {
                vhs: { overrideNative: true },
                nativeAudioTracks: false,
                nativeVideoTracks: false,
            },
            controlBar: { children: controls },
        };
    }

    function initializeVideoJs(media, url) {
        if (!media || !url) return null;
        if (!window.videojs || typeof videojs !== 'function') {
            nativeFallback(media, url);
            return null;
        }

        media.classList.add('video-js', 'vjs-theme-city');
        try {
            const player = media.player || videojs(media, videoJsOptions(media));
            player.controls(true);
            player.src({ src: url, type: 'application/x-mpegURL' });
            if (typeof player.seekButtons === 'function') {
                player.seekButtons({ forward: 10, back: 10 });
            }
            if (!player._lessonMediaErrorBound) {
                player._lessonMediaErrorBound = true;
                player.on('error', () => handleMediaError(media, player));
            }
            media._mediaSourceUrl = url;
            return player;
        } catch (error) {
            console.warn('Video.js initialization failed; using native controls.', error);
            const player = media.player;
            if (player && typeof player.dispose === 'function') player.dispose();
            nativeFallback(media, url);
            return null;
        }
    }

    function disposeMedia(media) {
        media._mediaErrorCleanup?.();
        const player = media.player;
        if (player && typeof player.dispose === 'function') player.dispose();
        media._mediaSourceUrl = null;
        media._lessonMediaInitialized = false;
    }

    function disposeVideoJsPlayers(container) {
        container.querySelectorAll('video, audio').forEach(disposeMedia);
    }

    function attachMediaSource(media, url) {
        const source = media?.querySelector('source');
        if (!media || !source || !url || media._mediaSourceUrl === url) return;
        source.src = url;

        if (isNativeHls(media)) {
            nativeFallback(media, url);
            return;
        }

        initializeVideoJs(media, url);
    }

    function initializeMediaElement(media) {
        if (!media || media._lessonMediaInitialized) return;
        media._lessonMediaInitialized = true;
        media.controls = true;
        media.setAttribute('controlsList', 'nodownload');

        const errorHandler = () => handleMediaError(media, media.player);
        media.addEventListener('error', errorHandler);
        media._mediaErrorCleanup = () => {
            media.removeEventListener('error', errorHandler);
            delete media._mediaErrorCleanup;
        };

        const source = media.querySelector('source');
        if (source?.src) attachMediaSource(media, source.src);
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
        registerVideoJsLanguage();
        container.querySelectorAll('video, audio').forEach(initializeMediaElement);
        container.querySelectorAll('.pdf-viewer').forEach(initializePdfViewer);
    }

    document.addEventListener('media-session-ready', event => {
        attachMediaSource(event.detail?.element, event.detail?.url);
    });

    function swapTarget(event) {
        return event.detail?.ctx?.target || event.detail?.target || null;
    }

    function isMediaSwapTarget(target) {
        return Boolean(target) && (target.id === 'display-page' || target.id === 'content');
    }

    document.body.addEventListener('htmx:before:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) disposeVideoJsPlayers(target);
    });

    document.body.addEventListener('htmx:after:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) {
            sessionStorage.removeItem(`media-session-reload:${window.location.pathname}`);
            initializeAllElements(target);
        }
    });

    initializeAllElements();
})();
