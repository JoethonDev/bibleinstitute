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
        positionHandler: null,
        resumeSeconds: 0,
        resumeApplied: false,
        lastPositionBroadcastAt: 0,
        suppressPositionBroadcastUntil: 0,

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
            // Paired audio/video of one lesson part share one backend progress
            // row. Mirror the seek slider between them so both stay aligned.
            this.positionHandler = event => this.applyPeerPosition(event.detail || {});
            window.addEventListener('lecture-position-updated', this.positionHandler);
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
            if (this.positionHandler) {
                window.removeEventListener('lecture-position-updated', this.positionHandler);
            }
            if (this.mediaAuthErrorHandler) {
                this.$el.removeEventListener('media-auth-expired', this.mediaAuthErrorHandler);
            }
            this.sendHeartbeat(true);
        },

        applyPeerPosition(detail) {
            if (detail.lessonId === undefined || detail.partId === undefined) return;
            if (
                String(detail.lessonId) !== String(this.lessonId)
                || String(detail.partId) !== String(this.partId)
            ) return;
            const media = this.$el.querySelector('video, audio');
            if (!media || media === detail.element) return;
            // Never disturb a peer that is actually playing; only align the
            // slider of an idle pair.
            if (media.paused !== true) return;
            const target = Number(detail.time);
            if (!Number.isFinite(target) || target < 0) return;
            const current = Number(media.currentTime) || 0;
            if (Math.abs(current - target) <= 2) return;
            this.suppressPositionBroadcastUntil = performance.now() + 700;
            try {
                const player = media.player;
                if (player && typeof player.currentTime === 'function') player.currentTime(target);
                else media.currentTime = target;
            } catch (_) { /* a source swap may own the element; skip this tick */ }
        },

        broadcastPosition(media) {
            if (!media || !Number.isFinite(media.currentTime)) return;
            const now = performance.now();
            if (now < this.suppressPositionBroadcastUntil) return;
            if (now - this.lastPositionBroadcastAt < 800) return;
            this.lastPositionBroadcastAt = now;
            window.dispatchEvent(new CustomEvent('lecture-position-updated', {
                detail: {
                    lessonId: this.lessonId,
                    partId: this.partId,
                    time: media.currentTime,
                    element: media,
                },
            }));
        },

        applyResumePosition(media) {
            if (!media || this.resumeApplied || this.resumeSeconds <= 0) return;
            const target = this.resumeSeconds;
            let attempts = 0;
            const maxAttempts = 4;
            const readPosition = () => {
                try {
                    const player = media.player;
                    if (player && typeof player.currentTime === 'function') {
                        return Number(player.currentTime());
                    }
                    return Number(media.currentTime);
                } catch (_) {
                    return NaN;
                }
            };
            const isSettled = () => {
                const current = readPosition();
                return Number.isFinite(current) && current >= target - 2;
            };
            const stopListening = () => {
                media.removeEventListener('loadedmetadata', seek);
                media.removeEventListener('canplay', seek);
            };
            const seek = () => {
                if (this.resumeApplied || this.destroyed) return;
                if (isSettled()) {
                    this.resumeApplied = true;
                    stopListening();
                    return;
                }
                if (attempts >= maxAttempts) {
                    this.resumeApplied = true;
                    stopListening();
                    console.warn('progressTracker: resume seek did not settle', {
                        target: target,
                        attempts: attempts,
                    });
                    return;
                }
                attempts += 1;
                try {
                    const player = media.player;
                    const hasPlayer = player && typeof player.currentTime === 'function';
                    const current = hasPlayer ? Number(player.currentTime()) : Number(media.currentTime);
                    if (!Number.isFinite(current) || current < target - 1) {
                        if (hasPlayer) player.currentTime(target);
                        else media.currentTime = target;
                    }
                } catch (_) { /* tech may still be settling */ }
                // The first metadata event can precede a seekable tech (the
                // tracker sets source.src and Video.js then loads the same URL
                // through its own tech). Latching blindly here is exactly how
                // a resume gets lost, so re-enter seek shortly after: it
                // latches when settled, warns when attempts run out, and
                // otherwise chains the next attempt.
                window.setTimeout(() => {
                    if (!this.resumeApplied && !this.destroyed) seek();
                }, 600);
            };
            media.addEventListener('loadedmetadata', seek);
            media.addEventListener('canplay', seek);
            window.setTimeout(seek, 2000);
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
            if (typeof data.resume_seconds === 'number' && Number.isFinite(data.resume_seconds)) {
                this.resumeSeconds = Math.max(0, Math.floor(data.resume_seconds));
            }
            this.mediaUrl = `${data.manifest_url}?session_id=${encodeURIComponent(data.session_id)}&token=${encodeURIComponent(data.token)}`;
            const media = this.$el.querySelector('video, audio');
            const source = media?.querySelector('source');
            if (media && source) {
                // On the very first session, continue from the last verified
                // second. Token refreshes keep the live position instead.
                if (!refresh && !hadSession) this.applyResumePosition(media);
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
            this.broadcastPosition(media);

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
            } else if (!media.paused) {
                // Some browsers report an empty played list while playing.
                // Never synthesise a range for a paused player (e.g. a seek on
                // the idle paired audio/video track): a paused seek plays
                // nothing and must not be credited.
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
