// Auto-save functionality for forms
function autoSave(formKey) {
    return {
        formData: {},
        draftKey: `draft-${formKey}`,
        lastSaved: null,
        saving: false,
        
        init() {
            this.loadDraft();
            // Auto-save every 30 seconds if there are changes
            setInterval(() => {
                if (Object.keys(this.formData).length > 0) {
                    this.saveDraft();
                }
            }, 30000);
        },
        
        saveDraft() {
            this.saving = true;
            const data = {};
            const form = this.$el.querySelector('form');
            if (form) {
                const formData = new FormData(form);
                for (let [key, value] of formData.entries()) {
                    data[key] = value;
                }
                localStorage.setItem(this.draftKey, JSON.stringify(data));
                this.lastSaved = new Date().toLocaleTimeString();
                this.saving = false;
                
                // Show notification
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').info('Draft saved');
                }
            }
        },
        
        loadDraft() {
            const draft = localStorage.getItem(this.draftKey);
            if (draft) {
                try {
                    this.formData = JSON.parse(draft);
                    // Populate form fields
                    const form = this.$el.querySelector('form');
                    if (form) {
                        Object.keys(this.formData).forEach(key => {
                            const field = form.querySelector(`[name="${key}"]`);
                            if (field) {
                                field.value = this.formData[key];
                            }
                        });
                    }
                } catch (e) {
                    console.error('Failed to load draft:', e);
                }
            }
        },
        
        clearDraft() {
            localStorage.removeItem(this.draftKey);
            this.formData = {};
            this.lastSaved = null;
        }
    };
}
