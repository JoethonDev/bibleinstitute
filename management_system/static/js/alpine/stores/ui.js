// UI State Store
document.addEventListener('alpine:init', () => {
    let sidebarCollapsed = false;
    try {
        sidebarCollapsed = window.localStorage.getItem('sidebar-collapsed') === 'true';
    } catch (error) {
        // Continue with the default when browser storage is unavailable.
    }

    Alpine.store('ui', {
        loading: false,
        sidebarCollapsed,
        mobileMenuOpen: false,
        toggleSidebar() {
            this.sidebarCollapsed = !this.sidebarCollapsed;
            try {
                window.localStorage.setItem('sidebar-collapsed', String(this.sidebarCollapsed));
            } catch (error) {
                // The UI remains functional without persistent sidebar state.
            }
        },
        toggleMobileMenu() {
            this.mobileMenuOpen = !this.mobileMenuOpen;
        },
        closeMobileMenu() {
            this.mobileMenuOpen = false;
        }
    });
});
