const PROGRESS_HEARTBEAT_INTERVAL_MS = 10000;

function progressTracker() {
    return {
        playedRanges: [],
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
        destroyed: false,
        segmentTokenLifetimeSeconds: 600,
        heartbeatInFlight: false,
        pendingEnd: false,
        finalHeartbeatSent: false,
        pageExitHandler: null,

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
            this.startSession();
            this.interval = setInterval(() => this.sendHeartbeat(), PROGRESS_HEARTBEAT_INTERVAL_MS);
            this.pageExitHandler = () => {
                this.cancelMediaRefresh();
                this.sendHeartbeat(true);
            };
            window.addEventListener('pagehide', this.pageExitHandler);
        },

        destroy() {
            this.destroyed = true;
            if (this.interval) clearInterval(this.interval);
            this.cancelMediaRefresh();
            if (this.pageExitHandler) window.removeEventListener('pagehide', this.pageExitHandler);
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
                this.progressPercent = Math.min(Math.max(Math.round(data.progress_percent), 0), 100);
            }
            if (!refresh) sessionStorage.removeItem(`media-session-reload:${window.location.pathname}`);
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
            } catch (error) {
                console.warn('progressTracker: media token refresh failed', error);
                this.scheduleMediaRefresh();
            } finally {
                this.mediaRefreshInFlight = false;
            }
        },

        onTimeUpdate(event) {
            const media = event.currentTarget || event.target;
            if (!media || !Number.isFinite(media.currentTime)) return;

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
            if (this.playedRanges.length === 0 && !ending) return;
            if (ending) this.finalHeartbeatSent = true;
            const ranges = this.playedRanges;
            this.playedRanges = [];
            this.heartbeatInFlight = true;
            fetch(this.heartbeatUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRF()},
                body: JSON.stringify({session_id: this.sessionId, ranges: ranges, ended: ending}),
                keepalive: ending,
            }).then(async response => {
                if (response.ok) {
                    try {
                        const data = await response.json();
                        if (typeof data.percent === 'number' && Number.isFinite(data.percent)) {
                            this.progressPercent = Math.min(Math.max(Math.round(data.percent), 0), 100);
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
