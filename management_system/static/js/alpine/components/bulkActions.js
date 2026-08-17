// Bulk Actions Component
function bulkActions(deleteUrl) {
    return {
        selected: [],
        selectAll: false,
        
        toggleAll() {
            const checkboxes = this.$el.querySelectorAll('.row-checkbox');
            if (this.selectAll) {
                this.selected = Array.from(checkboxes).map(cb => cb.value);
            } else {
                this.selected = [];
            }
        },
        
        toggleSelection(id) {
            const index = this.selected.indexOf(id);
            if (index > -1) {
                this.selected.splice(index, 1);
            } else {
                this.selected.push(id);
            }
            // Update selectAll state
            const checkboxes = this.$el.querySelectorAll('.row-checkbox');
            this.selectAll = this.selected.length === checkboxes.length;
        },
        
        async deleteSelected() {
            if (this.selected.length === 0) return;
            
            if (!confirm(interpolate(gettext('Delete %(count)s selected item(s)?'), { count: this.selected.length }, true))) {
                return;
            }
            
            try {
                const response = await fetch(deleteUrl, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
                    },
                    body: JSON.stringify({ ids: this.selected })
                });
                
                if (response.ok) {
                    // Remove rows from UI
                    this.selected.forEach(id => {
                        const row = this.$el.querySelector(`tr[data-id="${id}"]`);
                        if (row) {
                            row.remove();
                        }
                    });
                    this.selected = [];
                    this.selectAll = false;
                    
                    if (Alpine.store('notifications')) {
                        Alpine.store('notifications').success(gettext('Items deleted successfully'));
                    }
                    
                    // Reload table
                    if (window.htmx) {
                        htmx.trigger('#table-container', 'refresh');
                    }
                } else {
                    throw new Error(gettext('Delete failed'));
                }
            } catch (error) {
                console.error('Bulk delete error:', error);
                if (Alpine.store('notifications')) {
                    Alpine.store('notifications').error(gettext('Failed to delete items'));
                }
            }
        }
    };
}
