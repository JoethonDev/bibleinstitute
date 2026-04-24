/**
 * Theme Store for Admin Dashboard
 * Manages dark/light mode with localStorage persistence and system preference detection
 */
document.addEventListener('alpine:init', () => {
    Alpine.store('theme', {
        // Initialize with persisted value or system preference
        mode: Alpine.$persist('light').as('theme-mode'),
        
        /**
         * Initialize theme on page load
         * Detects system preference if no stored value exists
         */
        init() {
            // Check if theme was previously set
            const storedTheme = localStorage.getItem('theme-mode');
            
            // If no stored theme, detect system preference
            if (!storedTheme) {
                const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                this.mode = prefersDark ? 'dark' : 'light';
            }
            
            // Apply the theme
            this.applyTheme();
            
            // Listen for system theme changes
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
                // Only auto-switch if user hasn't manually set a preference
                if (!localStorage.getItem('theme-mode-manual')) {
                    this.mode = e.matches ? 'dark' : 'light';
                    this.applyTheme();
                }
            });
        },
        
        /**
         * Toggle between dark and light mode
         */
        toggle() {
            this.mode = this.mode === 'dark' ? 'light' : 'dark';
            this.applyTheme();
            // Mark as manually set
            localStorage.setItem('theme-mode-manual', 'true');
        },
        
        /**
         * Apply theme to document
         */
        applyTheme() {
            document.documentElement.setAttribute('data-bs-theme', this.mode);
            
            // Dispatch custom event for components that need to react to theme changes
            window.dispatchEvent(new CustomEvent('theme-changed', { 
                detail: { mode: this.mode } 
            }));
        },
        
        /**
         * Get current theme mode
         * @returns {string} 'dark' or 'light'
         */
        get isDark() {
            return this.mode === 'dark';
        },
        
        /**
         * Get current theme mode
         * @returns {string} 'dark' or 'light'
         */
        get isLight() {
            return this.mode === 'light';
        }
    });
});
