// UI State Store
document.addEventListener('alpine:init', () => {
    Alpine.store('ui', {
        loading: false,
        sidebarCollapsed: Alpine.$persist(false).as('sidebar-collapsed'),
        mobileMenuOpen: false,
        toggleSidebar() {
            this.sidebarCollapsed = !this.sidebarCollapsed;
        },
        toggleMobileMenu() {
            this.mobileMenuOpen = !this.mobileMenuOpen;
        },
        closeMobileMenu() {
            this.mobileMenuOpen = false;
        }
    });
});
