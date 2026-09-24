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
        heartbeatInFlight: false,
        pendingEnd: false,
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
            this.interval = setInterval(() => this.sendHeartbeat(), 10000);
            this.pageExitHandler = () => this.sendHeartbeat(true);
            window.addEventListener('pagehide', this.pageExitHandler);
        },

        destroy() {
            if (this.interval) clearInterval(this.interval);
            if (this.pageExitHandler) window.removeEventListener('pagehide', this.pageExitHandler);
            this.sendHeartbeat(true);
        },

        async startSession() {
            const response = await fetch(this.startSessionUrl);
            if (!response.ok) {
                throw new Error(`Viewing session request failed: ${response.status}`);
            }
            const data = await response.json();
            this.sessionId = data.session_id;
            if (typeof data.progress_percent === 'number' && Number.isFinite(data.progress_percent)) {
                this.progressPercent = Math.min(Math.max(Math.round(data.progress_percent), 0), 100);
            }
            sessionStorage.removeItem(`media-session-reload:${window.location.pathname}`);
            this.mediaUrl = `${data.manifest_url}?session_id=${encodeURIComponent(data.session_id)}&token=${encodeURIComponent(data.token)}`;
            const media = this.$el.querySelector('video, audio');
            const source = media?.querySelector('source');
            if (media && source) {
                source.src = this.mediaUrl;
                media.dispatchEvent(new CustomEvent('media-session-ready', {
                    bubbles: true,
                    detail: { element: media, url: this.mediaUrl },
                }));
            }
        },

        onTimeUpdate(event) {
            const media = event.currentTarget || event.target;
            if (!media || !Number.isFinite(media.currentTime)) return;

            if (media.played && media.played.length) {
                for (let index = 0; index < media.played.length; index += 1) {
                    const start = media.played.start(index);
                    const end = media.played.end(index);
                    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
                        this.playedRanges.push([start, end]);
                    }
                }
            } else {
                this.playedRanges.push([media.currentTime, media.currentTime + 5]);
            }
        },

        sendHeartbeat(ending = false) {
            if (!this.sessionId) return;
            if (this.heartbeatInFlight) {
                this.pendingEnd = this.pendingEnd || ending;
                return;
            }
            if (this.playedRanges.length === 0 && !ending) return;
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
                if (this.playedRanges.length || shouldEnd) this.sendHeartbeat(shouldEnd);
            });
        },

        getCSRF() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        }
    };
}
