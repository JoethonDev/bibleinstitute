/**
 * Durable management-operation status poller (J01).
 *
 * Polls one lightweight owner-scoped status request at a time: 2s while the job
 * is active, bounded backoff on errors, paused while the tab is hidden and
 * stopped on a terminal state. The payload contains bounded row errors and no
 * input populations, so polling cannot re-run the operation query.
 */
function adminOperationStatus(config) {
    return {
        statusUrl: config.statusUrl || '',
        status: config.status || '',
        total: config.total || 0,
        processed: config.processed || 0,
        succeeded: config.succeeded || 0,
        failed: config.failed || 0,
        skipped: config.skipped || 0,
        error: config.error || '',
        warning: '',
        rowErrors: [],
        destroyed: false,
        loaded: false,
        artifactReady: Boolean(config.artifactReady),
        timer: null,
        inFlight: false,
        delay: 2000,
        maxDelay: 30000,

        init() {
            this.wakeHandler = () => {
                if (document.hidden) return;
                this.delay = 2000;
                if (!this.loaded || !this.isTerminal()) this.poll(0, !this.loaded);
            };
            document.addEventListener('visibilitychange', this.wakeHandler);
            window.addEventListener('focus', this.wakeHandler);
            if (this.statusUrl) this.poll(0, true);
        },

        destroy() {
            this.destroyed = true;
            clearTimeout(this.timer);
            if (this.wakeHandler) {
                document.removeEventListener('visibilitychange', this.wakeHandler);
                window.removeEventListener('focus', this.wakeHandler);
            }
        },

        isTerminal() {
            return this.status === 'completed' || this.status === 'partial' || this.status === 'failed';
        },

        statusLabel() {
            const labels = {
                queued: gettext('Queued'),
                running: gettext('Running'),
                waiting: gettext('Waiting for delivery'),
                completed: gettext('Completed'),
                failed: gettext('Failed'),
                partial: gettext('Completed with failures'),
            };
            return labels[this.status] || this.status;
        },

        schedule(delay) {
            clearTimeout(this.timer);
            this.timer = null;
            if (this.destroyed || document.hidden || !this.statusUrl || this.isTerminal()) return;
            this.timer = setTimeout(() => this.poll(), delay);
        },

        async poll(immediateDelay = null, initial = false) {
            if (this.destroyed || !this.statusUrl || this.inFlight || document.hidden || (this.isTerminal() && !initial)) return;
            if (immediateDelay !== null) {
                clearTimeout(this.timer);
                this.timer = null;
            }
            this.inFlight = true;
            try {
                const response = await fetch(this.statusUrl, {cache: 'no-store'});
                if (!response.ok) throw new Error(gettext('Unable to read operation progress.'));
                const data = await response.json();
                this.loaded = true;
                this.status = data.status || this.status;
                this.total = data.total || 0;
                this.processed = data.processed || 0;
                this.succeeded = data.succeeded || 0;
                this.failed = data.failed || 0;
                this.skipped = data.skipped || 0;
                this.error = data.error || '';
                this.warning = data.result?.warning || '';
                this.rowErrors = (data.result?.row_errors || []).slice(0, 20);
                this.artifactReady = Boolean(data.artifact_ready);
                this.delay = 2000;
                if (!this.isTerminal()) this.schedule(this.delay);
            } catch (error) {
                if (!this.isTerminal() || !this.error) this.error = error.message;
                this.delay = Math.min(this.delay * 2, this.maxDelay);
                this.schedule(this.delay);
            } finally {
                this.inFlight = false;
            }
        },

        percent() {
            return this.total > 0 ? Math.min(100, Math.round((this.processed / this.total) * 100)) : 0;
        },
    };
}

window.adminOperationStatus = adminOperationStatus;
