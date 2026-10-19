/**
 * Alpine.js Component: tableManager
 *
 * Manages the admin table: column visibility, bulk row selection, and pagination.
 * Template-specific data (column list, view key) must be provided via the spread pattern:
 *
 *   x-data="{ ...tableManager(),
 *              columns: [{% for col in columns %}{ name: '{{ col|escapejs }}', visible: true }{% endfor %}],
 *              viewKey: '{{ view|default:'default'|escapejs }}' }"
 *
 * Extracted from table_and_pagination.html so the function is globally available
 * BEFORE Alpine.initTree() is called after an HTMX swap.
 */
function tableManager() {
    return {
        columns: [],        // overridden by template via spread
        viewKey: 'default', // overridden by template via spread
        bulkDeleteUrl: '',  // overridden by template via spread
        selectionToken: '', // signed view+filter+actor token, server-issued
        tableTotal: 0,      // full filtered result count, server-supplied
        selected: [],
        selectAll: false,
        allMatching: false,
        excluded: [],
        pageSize: 15,
        quizRecalculateUrl: '',
        operatorId: '',
        recalculationBusy: false,
        recalculationStatus: '',
        recalculationStatusUrl: '',
        recalculationTimer: null,
        jobStartedHandler: null,
        discoveryTimer: null,

        init() {
            this.loadColumnPreferences();
            if (this.quizRecalculateUrl) {
                this.jobStartedHandler = event => {
                    if (String(event.detail.operator) !== String(this.operatorId) || !this.$root.isConnected) return;
                    this.recalculationStatusUrl = event.detail.url;
                    this.pollRecalculation();
                };
                window.addEventListener('quiz-operation-started', this.jobStartedHandler);
                try {
                    const url = sessionStorage.getItem(this.recalculationKey());
                    if (url && url.startsWith(this.quizRecalculateUrl)) {
                        this.recalculationStatusUrl = url;
                        this.pollRecalculation();
                    }
                } catch (error) { /* Storage is optional; the job runs on the worker. */ }
                this.discoverJob();
                this.discoveryTimer = setInterval(() => this.discoverJob(), 5000);
            }
        },

        destroy() {
            clearTimeout(this.recalculationTimer);
            clearInterval(this.discoveryTimer);
            if (this.jobStartedHandler) window.removeEventListener("quiz-operation-started", this.jobStartedHandler);
        },

        async discoverJob() {
            if (!this.$root.isConnected || this.recalculationBusy || this.recalculationStatusUrl) return;
            try {
                const response = await fetch(this.quizRecalculateUrl, {cache: 'no-store'});
                if (!response.ok) return;
                const data = await response.json();
                if (!data.status_url || !this.$root.isConnected || this.recalculationStatusUrl) return;
                let completed = '';
                try { completed = sessionStorage.getItem(this.recalculationKey() + '-completed'); } catch (error) { /* Optional. */ }
                if (data.status_url === completed) return;
                this.recalculationStatusUrl = data.status_url;
                this.pollRecalculation();
            } catch (error) { /* Discovery retries while this table is visible. */ }
        },

        recalculationKey() {
            return `admin-quiz-recalculation-${this.operatorId}`;
        },

        recalculateQuizzes(mode, quizId = null, kind = 'recalculate') {
            if (!this.quizRecalculateUrl || this.recalculationBusy || this.recalculationStatusUrl) return;
            const all = mode === 'all' || (mode === 'selected' && this.allMatching);
            const ids = mode === 'single' ? [quizId] : (all ? [] : this.selected);
            const count = mode === 'single' ? 1 : (mode === 'all' ? this.tableTotal : this.selectionCount());
            if (!count) return;
            const payload = {kind, selection_token: this.selectionToken, select_all: all,
                ids: ids.map(Number), excluded_ids: mode === 'selected' && all ? this.excluded.map(Number) : []};
            const message = interpolate(kind === 'reconcile' ? gettext('Submit saved snapshots after the grace period for %(count)s quizzes?') : gettext('Recalculate saved submissions for %(count)s quizzes? Manually awarded grades will be preserved.'), {count}, true);
            const start = () => this.startRecalculation(payload);
            if (typeof window.appConfirm === 'function') window.appConfirm(message, start);
            else start();
        },

        async startRecalculation(payload) {
            if (this.recalculationBusy || this.recalculationStatusUrl) return;
            this.recalculationBusy = true;
            this.recalculationStatus = gettext('Starting quiz operation…');
            const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value
                || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
            try {
                const response = await fetch(this.quizRecalculateUrl, {method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(payload)});
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || gettext('Unable to start quiz operation.'));
                this.recalculationStatusUrl = data.status_url;
                try { sessionStorage.setItem(this.recalculationKey(), data.status_url); } catch (error) { /* Optional. */ }
                window.dispatchEvent(new CustomEvent("quiz-operation-started", {detail: {operator: this.operatorId, url: data.status_url}}));
            } catch (error) {
                this.recalculationStatus = error.message || gettext('Unable to start quiz operation.');
                this.recalculationBusy = false;
            }
        },

        async pollRecalculation() {
            if (!this.recalculationStatusUrl || !this.$root.isConnected) return;
            this.recalculationBusy = true;
            clearTimeout(this.recalculationTimer);
            try {
                const response = await fetch(this.recalculationStatusUrl, {cache: 'no-store'});
                if (response.status === 404) {
                    this.recalculationStatusUrl = '';
                    try { sessionStorage.removeItem(this.recalculationKey()); } catch (error) { /* Optional. */ }
                    throw new Error(gettext('The selection has expired. Reload the list and try again.'));
                }
                if (!response.ok) throw new Error(gettext('Unable to read quiz operation progress. Refresh the status to try again.'));
                const data = await response.json();
                if (!this.$root.isConnected) return;
                const progress = interpolate(data.kind === 'reconcile' ? gettext('%(done)s / %(total)s quizzes; %(answers)s snapshots submitted.') : gettext('%(done)s / %(total)s quizzes; %(answers)s answers recalculated.'),
                    {done: data.processed, total: data.total, answers: data.answers}, true);
                this.recalculationStatus = data.status === 'queued' ? gettext('Quiz operation is queued.') : progress;
                if (data.status === 'completed' || data.status === 'failed') {
                    this.recalculationStatus = data.status === 'failed' ? `${data.error} ${progress}`
                        : `${gettext('Quiz operation completed.')} ${progress} `
                          + interpolate(gettext('%(count)s quizzes no longer matched the selection.'), {count: data.skipped}, true);
                    try { sessionStorage.setItem(this.recalculationKey() + '-completed', this.recalculationStatusUrl); } catch (error) { /* Optional. */ }
                    this.recalculationBusy = false;
                    this.recalculationStatusUrl = '';
                    try { sessionStorage.removeItem(this.recalculationKey()); } catch (error) { /* Optional. */ }
                } else {
                    this.recalculationTimer = setTimeout(() => this.pollRecalculation(), 2000);
                }
            } catch (error) {
                this.recalculationStatus = error.message;
                this.recalculationBusy = false;
            }
        },

        rowCheckboxes() {
            // $el is the element the expression is evaluated on (the header
            // checkbox); $root is always the .ad-table-shell component root.
            return Array.from(this.$root.querySelectorAll('.row-checkbox'));
        },

        toggleAll(event) {
            if (event.target.checked) {
                this.allMatching = false;
                this.excluded = [];
                this.selected = this.rowCheckboxes().map(cb => cb.value);
                this.selectAll = true;
            } else {
                this.clearSelection();
            }
        },

        updateSelectAll() {
            const checkboxes = this.rowCheckboxes();
            this.selectAll = checkboxes.length > 0 && this.selected.length === checkboxes.length;
            if (this.allMatching) {
                this.excluded = checkboxes.filter(cb => !cb.checked).map(cb => cb.value);
            }
        },

        clearSelection() {
            this.selected = [];
            this.selectAll = false;
            this.allMatching = false;
            this.excluded = [];
        },

        // NOTE: the template composes this component with object spread
        // (x-data="{ ...tableManager(), ... }"). Object spread snapshots
        // accessor properties, so every computed value below must be a
        // method and be invoked from the template with parentheses.
        canSelectAllMatching() {
            return !!(this.bulkDeleteUrl && this.selectionToken && this.tableTotal > this.selected.length);
        },

        showSelectAllHint() {
            return this.selectAll && !this.allMatching && this.canSelectAllMatching();
        },

        selectAllMatching() {
            if (!this.canSelectAllMatching()) return;
            this.allMatching = true;
            this.excluded = [];
            this.selected = this.rowCheckboxes().map(cb => cb.value);
        },

        selectionCount() {
            return this.allMatching
                ? Math.max(this.tableTotal - this.excluded.length, 0)
                : this.selected.length;
        },

        selectionLabel() {
            if (this.allMatching) {
                return interpolate(gettext('%(count)s matching records selected'), { count: this.selectionCount() }, true);
            }
            if (this.selected.length > 0) {
                return interpolate(gettext('%(count)s selected on this page'), { count: this.selected.length }, true);
            }
            return '';
        },

        selectAllMatchingLabel() {
            return interpolate(gettext('Select all %(total)s matching records'), { total: this.tableTotal }, true);
        },

        deleteSelected() {
            if (!this.bulkDeleteUrl) {
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('Bulk delete is not supported for this view.'), 'warning');
                }
                return;
            }

            if (!this.selectionToken || this.selectionCount() === 0) {
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('The selection has expired. Reload the list and try again.'), 'warning');
                }
                return;
            }

            const message = interpolate(
                gettext('Delete %(count)s selected item(s)? This action cannot be undone.'),
                { count: this.selectionCount() },
                true
            );
            if (typeof window.appConfirm === 'function') window.appConfirm(message, () => this.confirmDelete());
            else this.confirmDelete();
        },

        async confirmDelete() {
            if (!this.bulkDeleteUrl || this.selectionCount() === 0) return;

            const toIds = values => values
                .map(value => parseInt(value, 10))
                .filter(id => !isNaN(id));

            const payload = this.allMatching
                ? {
                      select_all: true,
                      excluded_ids: toIds(this.excluded),
                      selection_token: this.selectionToken,
                  }
                : {
                      select_all: false,
                      ids: toIds(this.selected),
                      selection_token: this.selectionToken,
                  };

            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
                || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

            try {
                const response = await fetch(this.bulkDeleteUrl, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    },
                    body: JSON.stringify(payload),
                });

                const data = await response.json();

                if (response.ok) {
                    if (window.Alpine && Alpine.store('notifications')) {
                        Alpine.store('notifications').add(
                            interpolate(gettext('%(count)s item(s) deleted successfully.'), { count: data.deleted }, true),
                            'success'
                        );
                    }
                    if (window.htmx) {
                        htmx.ajax('GET', window.location.pathname + window.location.search, {
                            target: '#content',
                            select: '#content',
                            swap: 'outerHTML'
                        });
                    } else {
                        window.location.reload();
                    }
                } else {
                    if (window.Alpine && Alpine.store('notifications')) {
                        Alpine.store('notifications').add(data.error || gettext('Delete failed. Please try again.'), 'danger');
                    }
                }
            } catch (err) {
                console.error('Bulk delete error:', err);
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('Delete failed. Please try again.'), 'danger');
                }
            } finally {
                this.clearSelection();
            }
        },

        loadColumnPreferences() {
            const saved = localStorage.getItem(`admin-table-columns-${this.viewKey}`);
            if (saved) {
                try {
                    const savedColumns = JSON.parse(saved);
                    const lookup = {};
                    savedColumns.forEach(sc => { lookup[sc.name] = sc.visible; });
                    this.columns = this.columns.map(col => {
                        if (col.name in lookup) {
                            col.visible = lookup[col.name];
                        }
                        return col;
                    });
                } catch (e) {
                    console.error('tableManager: failed to load column preferences', e);
                }
            }
        },

        saveColumnPreferences() {
            const prefs = this.columns.map(col => ({ name: col.name, visible: col.visible }));
            localStorage.setItem(
                `admin-table-columns-${this.viewKey}`,
                JSON.stringify(prefs)
            );
        },

        resetColumns() {
            this.columns.forEach(col => col.visible = true);
            this.saveColumnPreferences();
        }
    };
}
