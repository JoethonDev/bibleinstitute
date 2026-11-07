function backgroundJobsMonitor() {
    return {
        timer: null,
        pending: false,
        disposed: false,
        delay: 2000,
        controller: null,
        pollingEnabled: true,
        pollImmediately: false,
        init() {
            try { this.pollingEnabled = localStorage.getItem('background-jobs-polling') !== 'paused'; } catch (error) {}
            this.wake = () => {
                if (document.hidden) {
                    clearTimeout(this.timer);
                    this.controller?.abort();
                } else if (this.pending) {
                    this.pollImmediately = true;
                } else {
                    this.poll();
                }
            };
            document.addEventListener('visibilitychange', this.wake);
            window.addEventListener('focus', this.wake);
            this.schedule();
        },
        togglePolling() {
            this.pollingEnabled = !this.pollingEnabled;
            try { localStorage.setItem('background-jobs-polling', this.pollingEnabled ? 'active' : 'paused'); } catch (error) {}
            clearTimeout(this.timer);
            if (this.pollingEnabled) {
                if (this.pending) {
                    this.pollImmediately = true;
                    this.controller?.abort();
                } else {
                    this.poll();
                }
            } else {
                this.pollImmediately = false;
                this.controller?.abort();
            }
        },
        activeRows() {
            return [...this.$el.querySelectorAll('[data-job-record][data-active="true"]')];
        },
        schedule() {
            clearTimeout(this.timer);
            const rows = this.activeRows();
            if (!this.pollingEnabled || this.disposed || document.hidden || !rows.length) return;
            const now = Date.now();
            const ready = rows.some(row => !row.dataset.nextPollAt || Date.parse(row.dataset.nextPollAt) <= now);
            const future = rows.map(row => Date.parse(row.dataset.nextPollAt || ""))
                .filter(value => Number.isFinite(value) && value > now);
            const wait = ready ? this.delay : Math.max(1000, Math.min(...future) - now);
            this.timer = setTimeout(() => this.poll(), Math.min(wait, 2147483647));
        },
        async poll() {
            if (!this.pollingEnabled || this.pending || this.disposed || document.hidden) return;
            clearTimeout(this.timer);
            const allRows = this.activeRows();
            const now = Date.now();
            const rows = allRows.filter(row => !row.dataset.nextPollAt || Date.parse(row.dataset.nextPollAt) <= now);
            if (!rows.length) { this.schedule(); return; }
            const url = new URL(this.$el.dataset.jobStatusUrl, window.location.origin);
            rows.forEach(row => url.searchParams.append('job', row.dataset.family + ':' + row.dataset.id));
            this.pending = true;
            this.controller = new AbortController();
            try {
                const response = await fetch(url, {cache: 'no-store', signal: this.controller.signal});
                if (!response.ok) throw new Error('Status unavailable');
                const data = await response.json();
                if (this.disposed) return;
                let finished = false;
                rows.forEach(row => {
                    const job = data.jobs.find(item => item.family === row.dataset.family && item.id === row.dataset.id);
                    if (!job) {
                        row.dataset.active = 'false';
                        const label = row.querySelector('[data-job-field="status_label"]');
                        if (label) label.textContent = this.$el.dataset.unavailableLabel || gettext('Unavailable');
                        return;
                    }
                    row.dataset.active = job.active ? 'true' : 'false';
                    row.dataset.nextPollAt = job.next_poll ? new Date(job.next_poll).toISOString() : '';
                    finished = finished || (!job.active && row.dataset.primaryJob === 'true');
                    row.querySelectorAll('[data-job-field]').forEach(field => {
                        const key = field.dataset.jobField;
                        field.textContent = job[key] ?? '';
                        if (key === 'error') field.hidden = !job.error;
                    });
                    const bar = row.querySelector('progress');
                    if (bar) bar.value = job.percent;
                });
                this.$el.querySelector('[data-job-poll-error]').hidden = true;
                this.delay = 2000;
                if (finished && this.$el.dataset.jobDetail === 'true') {
                    if (window.htmx) htmx.ajax('GET', window.location.href, {target: '#content', select: '#content', swap: 'outerHTML'});
                    else window.location.reload();
                }
            } catch (error) {
                if (!this.disposed && error.name !== 'AbortError') {
                    this.$el.querySelector('[data-job-poll-error]').hidden = false;
                    this.delay = Math.min(30000, this.delay * 2);
                }
            } finally {
                this.pending = false;
                this.controller = null;
                if (this.pollImmediately && this.pollingEnabled && !this.disposed && !document.hidden) {
                    this.pollImmediately = false;
                    this.poll();
                } else {
                    this.pollImmediately = false;
                    this.schedule();
                }
            }
        },
        destroy() {
            this.disposed = true;
            clearTimeout(this.timer);
            this.controller?.abort();
            document.removeEventListener('visibilitychange', this.wake);
            window.removeEventListener('focus', this.wake);
        },
    };
}
window.backgroundJobsMonitor = backgroundJobsMonitor;
