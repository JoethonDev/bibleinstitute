function progressTracker() {
    return {
        playedRanges: [],
        sessionId: null,
        lessonId: null,
        partId: null,
        interval: null,

        init() {
            this.lessonId = this.$el.dataset.lessonId;
            this.partId = this.$el.dataset.partId;
            this.startSession();
            this.interval = setInterval(() => this.sendHeartbeat(), 10000);
        },

        destroy() {
            if (this.interval) clearInterval(this.interval);
            this.sendHeartbeat();
        },

        startSession() {
            fetch(`/api/lesson/${this.lessonId}/start-session/${this.partId}/`)
                .then(r => r.json())
                .then(data => this.sessionId = data.session_id);
        },

        onTimeUpdate(event) {
            const video = event.target;
            this.playedRanges.push([video.currentTime, video.currentTime + 5]);
        },

        sendHeartbeat() {
            if (!this.sessionId || this.playedRanges.length === 0) return;
            const ranges = this.playedRanges;
            this.playedRanges = [];
            fetch('/api/progress/heartbeat/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.getCSRF()},
                body: JSON.stringify({session_id: this.sessionId, ranges: ranges}),
            });
        },

        getCSRF() {
            return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        }
    };
}
