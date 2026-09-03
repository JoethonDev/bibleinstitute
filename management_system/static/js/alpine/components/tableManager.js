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
        selected: [],
        selectAll: false,
        pageSize: 15,

        init() {
            this.loadColumnPreferences();
        },

        toggleAll(event) {
            if (event.target.checked) {
                const checkboxes = this.$el.querySelectorAll('.row-checkbox');
                this.selected = Array.from(checkboxes).map(cb => cb.value);
                this.selectAll = true;
            } else {
                this.selected = [];
                this.selectAll = false;
            }
        },

        updateSelectAll() {
            const checkboxes = this.$el.querySelectorAll('.row-checkbox');
            this.selectAll = this.selected.length === checkboxes.length && checkboxes.length > 0;
        },

        get selectionLabel() {
            if (this.selected.length > 0) {
                return interpolate(gettext('%(count)s selected on this page'), { count: this.selected.length }, true);
            }
            return '';
        },

        deleteSelected() {
            if (this.selected.length === 0) return;
            if (!this.bulkDeleteUrl) {
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('Bulk delete is not supported for this view.'), 'warning');
                }
                return;
            }

            const message = interpolate(
                gettext('Delete %(count)s selected item(s)? This action cannot be undone.'),
                { count: this.selected.length },
                true
            );
            if (typeof window.appConfirm === 'function') window.appConfirm(message, () => this.confirmDelete());
            else this.confirmDelete();
        },

        async confirmDelete() {
            if (!this.bulkDeleteUrl || this.selected.length === 0) return;

            // Extract numeric IDs from localized user-detail URLs.
            const ids = this.selected.map(url => {
                const parts = url.replace(/\/$/, '').split('/');
                return parseInt(parts[parts.length - 1], 10);
            }).filter(id => !isNaN(id));

            if (ids.length === 0) return;

            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
                || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';

            try {
                const response = await fetch(this.bulkDeleteUrl, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    },
                    body: JSON.stringify({ ids }),
                });

                const data = await response.json();

                if (response.ok) {
                    // Remove deleted rows from DOM
                    this.selected.forEach(url => {
                        const row = this.$el.querySelector(`tr[data-id="${url}"]`);
                        if (row) row.remove();
                    });
                    if (window.Alpine && Alpine.store('notifications')) {
                        Alpine.store('notifications').add(
                            interpolate(gettext('%(count)s item(s) deleted successfully.'), { count: data.deleted }, true),
                            'success'
                        );
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
                this.selected = [];
                this.selectAll = false;
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
