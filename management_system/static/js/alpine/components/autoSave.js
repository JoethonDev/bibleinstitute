// Auto-save functionality for forms
function autoSave(formKey) {
    return {
        formData: {},
        draftKey: `admin-draft-${formKey}`,
        lastSaved: null,
        isSaving: false,
        hasChanges: false,
        
        init() {
            this.loadDraft();
            
            // Track changes
            this.$el.addEventListener('input', () => {
                this.hasChanges = true;
            });
            
            // Auto-save every 10 seconds if there are changes
            setInterval(() => {
                if (this.hasChanges) {
                    this.saveDraft();
                }
            }, 10000);
            
            // Save on page unload if there are changes
            window.addEventListener('beforeunload', (e) => {
                if (this.hasChanges) {
                    this.saveDraft();
                }
            });
        },
        
        saveDraft() {
            this.isSaving = true;
            const data = {};
            const form = this.$el.querySelector('form');
            
            if (form) {
                const formData = new FormData(form);
                for (let [key, value] of formData.entries()) {
                    data[key] = value;
                }
                
                try {
                    localStorage.setItem(this.draftKey, JSON.stringify(data));
                    this.lastSaved = new Date().toLocaleTimeString();
                    this.hasChanges = false;
                    this.isSaving = false;
                    
                    // Show notification
                    if (window.Alpine && Alpine.store('notifications')) {
                        Alpine.store('notifications').add(gettext('Draft saved'), 'success');
                    }
                } catch (e) {
                    console.error('Failed to save draft:', e);
                    this.isSaving = false;
                    
                    if (window.Alpine && Alpine.store('notifications')) {
                        Alpine.store('notifications').add(gettext('Failed to save draft'), 'danger');
                    }
                }
            }
        },
        
        loadDraft() {
            const draft = localStorage.getItem(this.draftKey);
            if (draft) {
                try {
                    this.formData = JSON.parse(draft);
                    
                    // Auto-restore draft if content exists and show notification
                    if (Object.keys(this.formData).length > 0) {
                        this.restoreDraft();
                        
                        // Show notification with option to clear
                        if (window.Alpine && Alpine.store('notifications')) {
                            Alpine.store('notifications').info(gettext('Draft restored from previous session'));
                        }
                    }
                } catch (e) {
                    console.error('Failed to load draft:', e);
                    this.clearDraft();
                }
            }
        },
        
        restoreDraft() {
            const form = this.$el.querySelector('form');
            if (form && this.formData) {
                Object.keys(this.formData).forEach(key => {
                    const field = form.querySelector(`[name="${key}"]`);
                    if (field) {
                        field.value = this.formData[key];
                        
                        // Trigger change event for reactive updates
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                });
                
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('Draft restored'), 'info');
                }
            }
        },
        
        clearDraft() {
            localStorage.removeItem(this.draftKey);
            this.formData = {};
            this.lastSaved = null;
            this.hasChanges = false;
        }
    };
}

// Form validation component
function formValidation() {
    return {
        errors: {},
        touched: {},
        submitting: false,
        
        validateField(fieldOrEvent) {
            // Accept either a DOM element or an event object
            const field = fieldOrEvent instanceof Event ? fieldOrEvent.target : fieldOrEvent;

            // Only validate actual form controls
            if (!field || !field.name || typeof field.checkValidity !== 'function') return true;

            const name = field.name;
            this.touched[name] = true;
            
            if (!field.checkValidity()) {
                this.errors[name] = field.validationMessage;
                field.classList.add('is-invalid');
                field.classList.remove('is-valid');
                return false;
            } else {
                delete this.errors[name];
                field.classList.remove('is-invalid');
                field.classList.add('is-valid');
                return true;
            }
        },
        
        validateForm(form) {
            let isValid = true;
            const fields = form.querySelectorAll('input, select, textarea');
            
            fields.forEach(field => {
                if (field.hasAttribute('required') || field.value) {
                    if (!this.validateField(field)) {
                        isValid = false;
                    }
                }
            });
            
            return isValid;
        },
        
        async handleSubmit(event) {
            event.preventDefault();
            const form = event.target;
            
            if (!this.validateForm(form)) {
                if (window.Alpine && Alpine.store('notifications')) {
                    Alpine.store('notifications').add(gettext('Please fix the errors before submitting'), 'danger');
                }
                return;
            }
            
            this.submitting = true;

            // Best-effort: clear saved draft from the outer autoSave scope.
            // Wrapped in try-catch so a failure never prevents form submission.
            try {
                const outerEl = document.getElementById('content');
                if (outerEl && outerEl._x_dataStack) {
                    const outerScope = outerEl._x_dataStack[0];
                    if (outerScope && typeof outerScope.clearDraft === 'function') {
                        outerScope.clearDraft();
                    }
                }
            } catch (e) { /* clearDraft is optional */ }

            // Native form submit — includes the csrfmiddlewaretoken hidden input.
            form.submit();
        }
    };
}
