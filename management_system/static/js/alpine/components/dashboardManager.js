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

        init() {
            // Calculate active filters on init
            this.updateActiveFiltersCount();

            // Recalculate whenever HTMX refreshes the table section
            document.body.addEventListener('htmx:afterSwap', () => {
                // Small delay to ensure the new DOM is fully settled
                setTimeout(() => this.updateActiveFiltersCount(), 100);
            });
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
        }
    };
}
