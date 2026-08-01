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
        },

        destroy() {
            if (this.interval) clearInterval(this.interval);
            this.sendHeartbeat();
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
                media.load();
            }
        },

        onTimeUpdate(event) {
            const video = event.target;
            if (Number.isFinite(video.currentTime)) {
                this.playedRanges.push([video.currentTime, video.currentTime + 5]);
            }
        },

        sendHeartbeat() {
            if (!this.sessionId || this.playedRanges.length === 0) return;
            const ranges = this.playedRanges;
            this.playedRanges = [];
            fetch(this.heartbeatUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRF()},
                body: JSON.stringify({session_id: this.sessionId, ranges: ranges}),
            }).then(async response => {
                if (response.ok) {
                    try {
                        const data = await response.json();
                        if (typeof data.percent === 'number' && Number.isFinite(data.percent)) {
                            this.progressPercent = Math.min(Math.max(Math.round(data.percent), 0), 100);
                        }
                    } catch (_) { /* ignore malformed json */ }
                }
            }).catch(() => { /* ignore network errors */ });
        },

        getCSRF() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        }
    };
}
