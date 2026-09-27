/* Shared student lesson media initialization.
 * Video.js owns the transport for audio and video where HLS is not native.
 * Safari/iOS keeps the browser's native HLS controls as the compatibility
 * path, and failed initialization always falls back to those controls.
 */
(function () {
    'use strict';

    const translations = window.lessonMediaTranslations || {};

    const pdfAssets = window.pdfViewerAssets || {};
    const PDF_WORKER_SRC = pdfAssets.workerSrc || '/static/vendor/pdfjs/3.11.174/pdf.worker.min.js';
    const PDF_CMAP_URL = pdfAssets.cMapUrl || '/static/vendor/pdfjs/3.11.174/cmaps/';
    const PDF_STANDARD_FONT_URL = pdfAssets.standardFontDataUrl || '/static/vendor/pdfjs/3.11.174/standard_fonts/';

    function ensurePdfWorker() {
        try {
            if (window.pdfjsLib?.GlobalWorkerOptions) {
                // Set unconditionally before every document load so no
                // navigation/swap ordering can leave workerSrc empty.
                pdfjsLib.GlobalWorkerOptions.workerSrc = PDF_WORKER_SRC;
            } else if (!window.pdfjsLib) {
                console.error('PDF viewer library is unavailable.');
            }
        } catch (_) {
            // Worker setup is best-effort; rendering still attempts without it.
        }
    }

    function pdfDocumentOptions(url) {
        ensurePdfWorker();
        return {
            url,
            cMapUrl: PDF_CMAP_URL,
            cMapPacked: true,
            standardFontDataUrl: PDF_STANDARD_FONT_URL,
            useSystemFonts: true,
        };
    }

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

    function requestMediaRefresh(media) {
        const source = media?.querySelector('source');
        const url = media?._mediaSourceUrl || source?.src;
        if (!media || !url) return;
        media.dispatchEvent(new CustomEvent('media-auth-expired', {
            bubbles: true,
            detail: {element: media, sourceUrl: url},
        }));
    }

    function handleMediaError(media, player) {
        const error = player?.error?.() || media?.error;
        const status = mediaErrorStatus(error);
        // Video.js/VHS reports failed segment requests as a generic network
        // MediaError on some browsers, without exposing the HTTP status.
        if (status === 401 || error?.code === 2 || media?.error?.code === 2) {
            requestMediaRefresh(media);
        }
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

    function armPlaybackRestore(media, preservePosition) {
        if (!preservePosition || !media) return null;
        const currentTime = Number.isFinite(media.currentTime) ? media.currentTime : 0;
        const wasPlaying = !media.paused && !media.ended;
        let restored = false;
        const restore = () => {
            if (restored) return;
            restored = true;
            media.removeEventListener('loadedmetadata', restore);
            try {
                const player = media.player;
                if (currentTime > 0) {
                    if (player && typeof player.currentTime === 'function') {
                        player.currentTime(currentTime);
                    } else {
                        media.currentTime = currentTime;
                    }
                }
                if (wasPlaying) {
                    const playResult = player && typeof player.play === 'function'
                        ? player.play()
                        : media.play();
                    if (playResult?.catch) playResult.catch(() => {});
                }
            } catch (_) {
                // The source may still be changing; the next media error can refresh it again.
            }
        };
        media.addEventListener('loadedmetadata', restore);
        window.setTimeout(restore, 1500);
        return restore;
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

    function initializeVideoJs(media, url, preservePosition = false) {
        if (!media || !url) return null;
        if (!window.videojs || typeof videojs !== 'function') {
            nativeFallback(media, url);
            return null;
        }

        media.classList.add('video-js', 'vjs-theme-city');
        try {
            const player = media.player || videojs(media, videoJsOptions(media));
            player.controls(true);
            if (preservePosition && typeof player.error === 'function') player.error(null);
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

    function attachMediaSource(media, url, preservePosition = false) {
        const source = media?.querySelector('source');
        if (!media || !source || !url || media._mediaSourceUrl === url) return;
        armPlaybackRestore(media, preservePosition);
        source.src = url;

        if (isNativeHls(media)) {
            nativeFallback(media, url);
            return;
        }

        initializeVideoJs(media, url, preservePosition);
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
        if (!viewer || viewer._pdfInitializing) return;

        const canvas = viewer.querySelector('.pdf-canvas');
        const stage = viewer.querySelector('.pdf-stage');
        const pageInfo = viewer.querySelector('.page-info');
        const prevBtn = viewer.querySelector('.prev');
        const nextBtn = viewer.querySelector('.next');
        const zoomOutBtn = viewer.querySelector('.pdf-zoom-out');
        const zoomInBtn = viewer.querySelector('.pdf-zoom-in');
        const fitWidthBtn = viewer.querySelector('.pdf-fit-width');
        const fullscreenBtn = viewer.querySelector('.pdf-fullscreen');
        const zoomLabel = viewer.querySelector('.pdf-zoom-level');
        if (!canvas || !stage || !pageInfo) return;

        const bookTitle = viewer.closest('.lesson-media-card')?.querySelector('.lesson-media-header h6');
        if (bookTitle) stage.dir = getComputedStyle(bookTitle).direction;

        let currentPage = 1;
        let zoom = 1;
        let pdfDoc = null;
        let loadingTask = null;
        let renderTask = null;
        let renderVersion = 0;
        let disposed = false;
        viewer._pdfInitializing = true;

        const updateControls = () => {
            if (prevBtn) prevBtn.disabled = currentPage <= 1;
            if (nextBtn && pdfDoc) nextBtn.disabled = currentPage >= pdfDoc.numPages;
            if (zoomLabel) zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
        };

        const renderPage = async (num) => {
            if (!pdfDoc || disposed) return;
            currentPage = Math.max(1, Math.min(pdfDoc.numPages, num));
            const version = ++renderVersion;
            if (renderTask) {
                renderTask.cancel();
                renderTask = null;
            }
            updateControls();
            pageInfo.textContent = `${translate('page', 'Page')} ${currentPage} / ${pdfDoc.numPages}`;

            try {
                const page = await pdfDoc.getPage(currentPage);
                if (disposed || version !== renderVersion) return;
                const baseViewport = page.getViewport({scale: 1});
                const availableWidth = Math.max(240, stage.clientWidth - 16);
                const fitScale = availableWidth / baseViewport.width;
                const viewport = page.getViewport({scale: fitScale * zoom});
                const pixelRatio = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2.5);
                canvas.width = Math.ceil(viewport.width * pixelRatio);
                canvas.height = Math.ceil(viewport.height * pixelRatio);
                canvas.style.width = `${viewport.width}px`;
                canvas.style.height = `${viewport.height}px`;
                // PDF canvas must stay LTR: the stage takes its direction from
                // the (often Arabic) book title, and an RTL canvas disturbs
                // pdf.js fillText positioning for visually-ordered glyph runs.
                canvas.dir = 'ltr';
                canvas.style.direction = 'ltr';
                const context = canvas.getContext('2d', {alpha: false});
                if (context && 'direction' in context) context.direction = 'ltr';
                renderTask = page.render({
                    canvasContext: context,
                    viewport,
                    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
                });
                await renderTask.promise;
                if (version === renderVersion) renderTask = null;
            } catch (error) {
                if (error?.name !== 'RenderingCancelledException' && !disposed) {
                    console.error('Error rendering PDF page:', error);
                    pageInfo.textContent = translate('pdfError', 'Error loading PDF.');
                }
            }
        };

        const toggleFullscreen = () => {
            const isFullscreen = viewer.classList.toggle('is-fullscreen');
            fullscreenBtn?.setAttribute('aria-pressed', String(isFullscreen));
            const label = translate(
                isFullscreen ? 'exitFullscreen' : 'fullscreen',
                isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen',
            );
            if (fullscreenBtn) {
                fullscreenBtn.setAttribute('aria-label', label);
                fullscreenBtn.title = label;
                const text = fullscreenBtn.querySelector('span');
                if (text) text.textContent = label;
                const icon = fullscreenBtn.querySelector('i');
                if (icon) {
                    icon.classList.toggle('fa-expand', !isFullscreen);
                    icon.classList.toggle('fa-compress', isFullscreen);
                }
            }
            renderPage(currentPage);
        };

        const onKeyDown = event => {
            if (event.key === 'Escape' && viewer.classList.contains('is-fullscreen')) {
                toggleFullscreen();
                fullscreenBtn?.focus();
            }
        };

        const resizeObserver = typeof ResizeObserver === 'function'
            ? new ResizeObserver(() => renderPage(currentPage))
            : null;
        resizeObserver?.observe(stage);

        prevBtn?.addEventListener('click', () => renderPage(currentPage - 1));
        nextBtn?.addEventListener('click', () => renderPage(currentPage + 1));
        zoomOutBtn?.addEventListener('click', () => {
            zoom = Math.max(0.5, Math.round((zoom - 0.2) * 100) / 100);
            renderPage(currentPage);
        });
        zoomInBtn?.addEventListener('click', () => {
            zoom = Math.min(4, Math.round((zoom + 0.2) * 100) / 100);
            renderPage(currentPage);
        });
        fitWidthBtn?.addEventListener('click', () => {
            zoom = 1;
            renderPage(currentPage);
        });
        fullscreenBtn?.addEventListener('click', toggleFullscreen);
        viewer.addEventListener('keydown', onKeyDown);

        viewer._pdfCleanup = () => {
            disposed = true;
            viewer.classList.remove('is-fullscreen');
            resizeObserver?.disconnect();
            if (renderTask) renderTask.cancel();
            loadingTask?.destroy();
            viewer.removeEventListener('keydown', onKeyDown);
            delete viewer._pdfCleanup;
            delete viewer._pdfInitializing;
        };

        try {
            const response = await fetch(viewer.dataset.url, {credentials: 'same-origin'});
            if (!response.ok) throw new Error(`PDF request failed (${response.status})`);
            const result = await response.json();
            if (disposed) return;
            if (!result?.url) throw new Error('PDF source is unavailable');
            loadingTask = pdfjsLib.getDocument(pdfDocumentOptions(result.url));
            pdfDoc = await loadingTask.promise;
            if (disposed) {
                loadingTask.destroy();
                return;
            }
            viewer.pdfDoc = pdfDoc;
            updateControls();
            await renderPage(currentPage);
        } catch (error) {
            if (!disposed) {
                console.error('Error loading PDF:', error);
                pageInfo.textContent = translate('pdfError', 'Error loading PDF.');
            }
        } finally {
            viewer._pdfInitializing = false;
        }
    }

    function disposePdfViewers(container) {
        if (!container) return;
        const viewers = [];
        if (container.matches?.('.pdf-viewer')) viewers.push(container);
        viewers.push(...(container.querySelectorAll?.('.pdf-viewer') || []));
        viewers.forEach(viewer => viewer._pdfCleanup?.());
    }

    function initializeAllElements(container = document) {
        registerVideoJsLanguage();
        container.querySelectorAll('video, audio').forEach(initializeMediaElement);
        container.querySelectorAll('.pdf-viewer').forEach(initializePdfViewer);
    }

    document.addEventListener('media-session-ready', event => {
        attachMediaSource(
            event.detail?.element,
            event.detail?.url,
            Boolean(event.detail?.refresh),
        );
    });

    function swapTarget(event) {
        return event.detail?.ctx?.target || event.detail?.target || null;
    }

    function isMediaSwapTarget(target) {
        return Boolean(target) && (target.id === 'display-page' || target.id === 'content');
    }

    document.body.addEventListener('htmx:before:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) {
            disposeVideoJsPlayers(target);
            disposePdfViewers(target);
        }
    });

    document.body.addEventListener('htmx:after:swap', event => {
        const target = swapTarget(event);
        if (isMediaSwapTarget(target)) {
            initializeAllElements(target);
        }
    });

    initializeAllElements();
})();
