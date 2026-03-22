// Column Visibility Manager
function columnManager(columns) {
    return {
        columns: columns || [],
        
        init() {
            // Load saved column preferences
            const saved = localStorage.getItem('column-visibility');
            if (saved) {
                try {
                    const savedColumns = JSON.parse(saved);
                    this.columns = this.columns.map(col => {
                        const savedCol = savedColumns.find(c => c.key === col.key);
                        return savedCol || col;
                    });
                } catch (e) {
                    console.error('Failed to load column preferences:', e);
                }
            }
        },
        
        get visibleColumns() {
            return this.columns.filter(col => col.visible);
        },
        
        toggleColumn(key) {
            const col = this.columns.find(c => c.key === key);
            if (col) {
                col.visible = !col.visible;
                this.savePreferences();
            }
        },
        
        savePreferences() {
            localStorage.setItem('column-visibility', JSON.stringify(this.columns));
        },
        
        resetColumns() {
            this.columns.forEach(col => col.visible = true);
            this.savePreferences();
        }
    };
}
