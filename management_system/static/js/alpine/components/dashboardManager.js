/**
 * Alpine.js Component: dashboardManager
 *
 * Manages the admin dashboard page state:
 *  - showFilters  — mobile filter panel visibility
 *  - activeFiltersCount — badge count on the filter toggle button
 *
 * Extracted from dashboard.html so the function is globally available
 * BEFORE Alpine.initTree() is called after an HTMX swap.
 */
function dashboardManager() {
    return {
        showFilters: false,
        activeFiltersCount: 0,
        afterSwapHandler: null,
        filterCountTimer: null,

        init() {
            // Calculate active filters on init
            this.updateActiveFiltersCount();

            // Recalculate whenever HTMX refreshes the table section
            this.afterSwapHandler = () => {
                // Small delay to ensure the new DOM is fully settled
                clearTimeout(this.filterCountTimer);
                this.filterCountTimer = setTimeout(() => this.updateActiveFiltersCount(), 100);
            };
            document.body.addEventListener('htmx:after:swap', this.afterSwapHandler);
        },

        destroy() {
            if (this.afterSwapHandler) {
                document.body.removeEventListener('htmx:after:swap', this.afterSwapHandler);
                this.afterSwapHandler = null;
            }
            clearTimeout(this.filterCountTimer);
            this.filterCountTimer = null;
        },

        updateActiveFiltersCount() {
            const filterInputs = this.$el.querySelectorAll('.filters-fields');
            let count = 0;

            filterInputs.forEach(input => {
                if (input.tagName === 'SELECT') {
                    const firstOption = input.querySelector('option');
                    if (input.value && input.value !== '' && input.value !== firstOption?.value) {
                        count++;
                    }
                } else if (input.tagName === 'INPUT') {
                    if (input.value && input.value !== '') {
                        count++;
                    }
                }
            });

            this.activeFiltersCount = count;
        },

        clearFilters() {
            const filterInputs = this.$el.querySelectorAll('.filters-fields');

            filterInputs.forEach(input => {
                if (input.tagName === 'SELECT') {
                    input.selectedIndex = 0;
                } else if (input.tagName === 'INPUT') {
                    input.value = '';
                }
            });

            const firstFilter = filterInputs[0];
            if (firstFilter && firstFilter.hasAttribute('hx-get') && window.htmx) {
                window.htmx.trigger(firstFilter, 'change');
            } else {
                window.location.href = this.$el.dataset.baseUrl || window.location.pathname;
            }
        }
    };
}
