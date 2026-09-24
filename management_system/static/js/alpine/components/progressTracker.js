const PROGRESS_HEARTBEAT_INTERVAL_MS = 10000;

function progressTracker() {
    return {
        playedRanges: [],
        activeSecondsBuffer: 0,
        heartbeatSequence: 0,
        playbackSample: null,
        progressPercent: 0,
        sessionId: null,
        offeringId: null,
        lessonId: null,
        partId: null,
        fileIndex: null,
        startSessionUrl: null,
        heartbeatUrl: null,
        mediaUrl: null,
        interval: null,
        mediaRefreshTimer: null,
        mediaRefreshInFlight: false,
        mediaRecoverySessionId: null,
        mediaAuthErrorHandler: null,
        destroyed: false,
        segmentTokenLifetimeSeconds: 600,
        heartbeatInFlight: false,
        pendingEnd: false,
        finalHeartbeatSent: false,
        pageExitHandler: null,
        progressUpdateHandler: null,
        visibilityHandler: null,

        init() {
            this.offeringId = this.$el.dataset.offeringId;
            this.lessonId = this.$el.dataset.lessonId;
            this.partId = this.$el.dataset.partId;
            this.fileIndex = this.$el.dataset.fileIndex;
            this.startSessionUrl = this.$el.dataset.startSessionUrl;
            this.heartbeatUrl = this.$el.dataset.heartbeatUrl;
            if (!this.startSessionUrl || !this.heartbeatUrl) {
                console.error('progressTracker: missing start-session-url or heartbeat-url data attributes');
                return;
            }
            this.progressUpdateHandler = event => {
                const detail = event.detail || {};
                if (
                    String(detail.lessonId) !== String(this.lessonId)
                    || String(detail.partId) !== String(this.partId)
                ) return;
                this.setProgressPercent(detail.percent);
            };
            window.addEventListener('lecture-progress-updated', this.progressUpdateHandler);
            this.mediaAuthErrorHandler = event => {
                const detail = event.detail || {};
                if (
                    detail.sourceUrl !== this.mediaUrl
                    || !this.sessionId
                    || this.mediaRecoverySessionId === this.sessionId
                ) return;
                this.mediaRecoverySessionId = this.sessionId;
                this.refreshMediaSession();
            };
            this.$el.addEventListener('media-auth-expired', this.mediaAuthErrorHandler);
            this.startSession();
            this.interval = setInterval(() => this.sendHeartbeat(), PROGRESS_HEARTBEAT_INTERVAL_MS);
            this.pageExitHandler = () => {
                this.resetPlaybackSample();
                this.cancelMediaRefresh();
                this.sendHeartbeat(true);
            };
            window.addEventListener('pagehide', this.pageExitHandler);
            this.visibilityHandler = () => {
                this.resetPlaybackSample();
                if (document.hidden) this.sendHeartbeat();
            };
            document.addEventListener('visibilitychange', this.visibilityHandler);
        },

        destroy() {
            this.destroyed = true;
            if (this.interval) clearInterval(this.interval);
            this.cancelMediaRefresh();
            if (this.pageExitHandler) window.removeEventListener('pagehide', this.pageExitHandler);
            if (this.visibilityHandler) document.removeEventListener('visibilitychange', this.visibilityHandler);
            if (this.progressUpdateHandler) {
                window.removeEventListener('lecture-progress-updated', this.progressUpdateHandler);
            }
            if (this.mediaAuthErrorHandler) {
                this.$el.removeEventListener('media-auth-expired', this.mediaAuthErrorHandler);
            }
            this.sendHeartbeat(true);
        },

        async startSession({refresh = false} = {}) {
            const response = await fetch(this.startSessionUrl);
            if (!response.ok) {
                throw new Error(`Viewing session request failed: ${response.status}`);
            }
            const data = await response.json();
            if (this.destroyed || this.finalHeartbeatSent) return;
            const hadSession = Boolean(this.sessionId);
            this.sessionId = data.session_id;
            if (Number.isFinite(data.segment_token_lifetime_seconds) && data.segment_token_lifetime_seconds > 0) {
                this.segmentTokenLifetimeSeconds = data.segment_token_lifetime_seconds;
            }
            if (typeof data.progress_percent === 'number' && Number.isFinite(data.progress_percent)) {
                this.setProgressPercent(data.progress_percent);
                this.publishProgress();
            }
            this.mediaUrl = `${data.manifest_url}?session_id=${encodeURIComponent(data.session_id)}&token=${encodeURIComponent(data.token)}`;
            const media = this.$el.querySelector('video, audio');
            const source = media?.querySelector('source');
            if (media && source) {
                source.src = this.mediaUrl;
                media.dispatchEvent(new CustomEvent('media-session-ready', {
                    bubbles: true,
                    detail: { element: media, url: this.mediaUrl, refresh: refresh || hadSession },
                }));
            }
            this.scheduleMediaRefresh();
        },

        scheduleMediaRefresh() {
            this.cancelMediaRefresh();
            if (this.destroyed || this.finalHeartbeatSent || !this.sessionId) return;
            const refreshDelay = Math.max(
                30000,
                Math.floor(this.segmentTokenLifetimeSeconds * 1000 * 0.8),
            );
            this.mediaRefreshTimer = setTimeout(() => this.refreshMediaSession(), refreshDelay);
        },

        cancelMediaRefresh() {
            if (this.mediaRefreshTimer) clearTimeout(this.mediaRefreshTimer);
            this.mediaRefreshTimer = null;
        },

        async refreshMediaSession() {
            if (this.destroyed || this.finalHeartbeatSent || !this.sessionId || this.mediaRefreshInFlight) return;
            this.mediaRefreshInFlight = true;
            try {
                await this.startSession({refresh: true});
                this.mediaRecoverySessionId = null;
            } catch (error) {
                console.warn('progressTracker: media token refresh failed', error);
                this.scheduleMediaRefresh();
            } finally {
                this.mediaRefreshInFlight = false;
            }
        },

        setProgressPercent(value) {
            if (typeof value !== 'number' || !Number.isFinite(value)) return;
            this.progressPercent = Math.min(Math.max(Math.round(value), 0), 100);
        },

        publishProgress() {
            window.dispatchEvent(new CustomEvent('lecture-progress-updated', {
                detail: {
                    lessonId: this.lessonId,
                    partId: this.partId,
                    percent: this.progressPercent,
                },
            }));
        },

        onTimeUpdate(event) {
            const media = event.currentTarget || event.target;
            if (!media || !Number.isFinite(media.currentTime)) return;

            this.samplePlayback(media);

            if (media.played && media.played.length) {
                const ranges = [];
                for (let index = 0; index < media.played.length; index += 1) {
                    const start = media.played.start(index);
                    const end = media.played.end(index);
                    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
                        ranges.push([start, end]);
                    }
                }
                this.playedRanges = ranges;
            } else {
                this.playedRanges = [[media.currentTime, media.currentTime + 5]];
            }
        },

        onPlay(event) {
            const media = event.currentTarget || event.target;
            this.playbackSample = media ? {at: performance.now(), time: media.currentTime} : null;
        },

        onPause() {
            this.resetPlaybackSample();
            this.sendHeartbeat();
        },

        resetPlaybackSample() {
            this.playbackSample = null;
        },

        samplePlayback(media) {
            const now = performance.now();
            const visibleAndPlaying = !document.hidden && !media.paused && !media.seeking;
            if (!visibleAndPlaying) {
                this.playbackSample = null;
                return;
            }
            const current = {at: now, time: media.currentTime};
            const previous = this.playbackSample;
            this.playbackSample = current;
            if (!previous) return;
            const elapsed = (now - previous.at) / 1000;
            const playbackDelta = current.time - previous.time;
            if (
                elapsed <= 2.5
                && elapsed > 0
                && playbackDelta > 0
                && playbackDelta / elapsed >= 0.25
                && playbackDelta / elapsed <= 4
            ) {
                this.activeSecondsBuffer += Math.min(elapsed, 2.5);
            }
        },

        sendHeartbeat(ending = false) {
            if (!this.sessionId) return;
            if (ending && this.finalHeartbeatSent) return;
            if (this.heartbeatInFlight) {
                if (ending) {
                    this.pendingEnd = true;
                    this.finalHeartbeatSent = true;
                }
                return;
            }
            if (this.playedRanges.length === 0 && this.activeSecondsBuffer === 0 && !ending) return;
            if (ending) this.finalHeartbeatSent = true;
            const ranges = this.playedRanges;
            const activeSeconds = this.activeSecondsBuffer;
            this.playedRanges = [];
            this.activeSecondsBuffer = 0;
            this.heartbeatSequence += 1;
            this.heartbeatInFlight = true;
            fetch(this.heartbeatUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRF()},
                body: JSON.stringify({
                    session_id: this.sessionId,
                    ranges: ranges,
                    ended: ending,
                    active_seconds: activeSeconds,
                    sequence: this.heartbeatSequence,
                }),
                keepalive: ending,
            }).then(async response => {
                if (response.ok) {
                    try {
                        const data = await response.json();
                        if (typeof data.percent === 'number' && Number.isFinite(data.percent)) {
                            this.setProgressPercent(data.percent);
                            this.publishProgress();
                        }
                    } catch (_) { /* ignore malformed json */ }
                }
            }).catch(() => { /* ignore network errors */ }).finally(() => {
                this.heartbeatInFlight = false;
                const shouldEnd = this.pendingEnd;
                this.pendingEnd = false;
                // Ordinary buffered ranges wait for the next 10-second tick.
                // Only a queued finalization is sent immediately.
                if (shouldEnd) this.sendHeartbeat(true);
            });
        },

        getCSRF() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        }
    };
}
