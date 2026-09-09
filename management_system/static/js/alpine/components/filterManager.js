/**
 * Alpine.js Component: filterManager
 *
 * Manages the filter panel inside the dashboard.
 * The `baseUrl` property must be provided by the template via the spread pattern:
 *
 *   x-data="{ ...filterManager(), baseUrl: '{{ url }}' }"
 *
 * Extracted from dashboard.html so the function is globally available
 * BEFORE Alpine.initTree() is called after an HTMX swap.
 */
function filterManager() {
    return {
        baseUrl: '',   // overridden by template via x-data spread

        init() {
            // Notify parent dashboardManager whenever a filter input changes
            this.$el.querySelectorAll('.filters-fields').forEach(field => {
                field.addEventListener('input', () => {
                    const parentScope = this.$el.closest('[x-data]');
                    if (parentScope) {
                        parentScope.dispatchEvent(new CustomEvent('filter-changed'));
                    }
                });
            });

            // Listen for the custom event to propagate back up
            const parentScope = this.$el.closest('[x-data]');
            if (parentScope) {
                parentScope.addEventListener('filter-changed', () => {
                    const dashboard = Alpine.$data(parentScope);
                    if (dashboard && typeof dashboard.updateActiveFiltersCount === 'function') {
                        dashboard.updateActiveFiltersCount();
                    }
                });
            }
        },

        clearFilters() {
            const filterInputs = this.$el.querySelectorAll('.filters-fields');

            // Reset all filter values
            filterInputs.forEach(input => {
                if (input.tagName === 'SELECT') {
                    input.selectedIndex = 0;
                } else if (input.tagName === 'INPUT') {
                    input.value = '';
                }
            });

            // Trigger an HTMX reload if possible, otherwise fall back to URL navigation
            const firstFilter = filterInputs[0];
            if (firstFilter && firstFilter.hasAttribute('hx-get') && window.htmx) {
                htmx.trigger(firstFilter, 'change');
            } else {
                window.location.href = this.baseUrl || window.location.pathname;
            }
        }
    };
}
