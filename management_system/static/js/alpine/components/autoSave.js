// Auto-save functionality for forms

// Draft fields that must never be written to localStorage or restored from it:
// CSRF tokens can go stale and password fields are secrets. Non-string values
// (File objects) cannot be serialized into a draft either.
function isSensitiveDraftField(name) {
    const normalized = String(name).toLowerCase();
    return normalized === 'csrfmiddlewaretoken' || normalized.includes('password');
}

function autoSave(formKey) {
    return {
        formData: {},
        draftKey: `admin-draft-${formKey}`,
        lastSaved: null,
        isSaving: false,
        hasChanges: false,
        autoSaveTimer: null,
        inputHandler: null,
        beforeUnloadHandler: null,
        
        initAutoSave() {
            if (this.$el.dataset.draftReadonly === 'true') {
                this.clearDraft();
                return;
            }
            if (this.$el.dataset.draftSaved === 'true' || this.$el.dataset.formErrors === 'true') {
                this.clearDraft();
            } else {
                this.loadDraft();
            }
            
            // Track changes
            this.inputHandler = () => {
                this.hasChanges = true;
            };
            this.$el.addEventListener('input', this.inputHandler);
            
            // Auto-save every 10 seconds if there are changes
            this.autoSaveTimer = setInterval(() => {
                if (this.hasChanges) {
                    this.saveDraft();
                }
            }, 10000);
            
            // Save on page unload if there are changes
            this.beforeUnloadHandler = () => {
                if (this.hasChanges) {
                    this.saveDraft();
                }
            };
            window.addEventListener('beforeunload', this.beforeUnloadHandler);
        },

        destroy() {
            if (this.autoSaveTimer) {
                clearInterval(this.autoSaveTimer);
                this.autoSaveTimer = null;
            }
            if (this.inputHandler) {
                this.$el.removeEventListener('input', this.inputHandler);
                this.inputHandler = null;
            }
            if (this.beforeUnloadHandler) {
                window.removeEventListener('beforeunload', this.beforeUnloadHandler);
                this.beforeUnloadHandler = null;
            }
            this.clearDraft();
        },
        
        saveDraft() {
            // A debounced input save can still run after an HTMX swap removed
            // the form; never resurrect a draft that destroy() just cleared.
            if (!this.$el || !this.$el.isConnected || this.$el.dataset.draftReadonly === 'true') {
                return;
            }
            this.isSaving = true;
            const data = {version: 2, fields: Object.create(null), rows: null};
            const form = this.$el.querySelector('form');
            
            if (form) {
                if (typeof this.beforeDraftSave === 'function') this.beforeDraftSave();
                for (const field of form.elements) {
                    if (!field.name || field.matches(':disabled') || isSensitiveDraftField(field.name)
                        || ['file', 'submit', 'reset', 'button'].includes(field.type)) continue;
                    const values = data.fields[field.name] || (data.fields[field.name] = []);
                    values.push({type: field.type, value: field.value, checked: field.checked,
                        selected: field.multiple ? Array.from(field.selectedOptions, option => option.value) : null});
                }
                if (typeof this.captureDraftRows === 'function') data.rows = this.captureDraftRows();
                
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
                    // Old drafts lost repeated values and cannot be restored safely.
                    if (this.formData.version !== 2 || !this.formData.fields || typeof this.formData.fields !== 'object'
                        || Array.isArray(this.formData.fields)) {
                        this.clearDraft();
                        return;
                    }
                    if (Object.values(this.formData.fields).some(values => !Array.isArray(values)
                        || values.some(value => !value || typeof value.type !== 'string' || typeof value.value !== 'string'
                            || (value.selected !== null && (!Array.isArray(value.selected)
                                || value.selected.some(option => typeof option !== 'string')))))) {
                        throw new Error('Invalid form draft');
                    }
                    
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
                if (this.formData.rows && typeof this.restoreDraftRows === 'function') {
                    this.restoreDraftRows(this.formData.rows);
                }
                Object.keys(this.formData.fields).forEach(key => {
                    if (isSensitiveDraftField(key)) {
                        return;
                    }
                    const fields = Array.from(form.querySelectorAll(`[name="${CSS.escape(key)}"]`));
                    const saved = this.formData.fields[key];
                    if (!Array.isArray(saved) || fields.length !== saved.length) return;
                    fields.forEach((field, index) => {
                        const value = saved[index];
                        if (field.matches(':disabled') || field.readOnly || field.type !== value.type || field.type === 'file') return;
                        if (field.type === 'checkbox' || field.type === 'radio') {
                            if (field.value === value.value) field.checked = value.checked === true;
                        } else if (field.multiple) {
                            for (const option of field.options) option.selected = (value.selected || []).includes(option.value);
                        } else {
                            // A removed offering must not erase the current valid selection.
                            if (field.tagName === 'SELECT' && !Array.from(field.options).some(option => option.value === value.value)) return;
                            field.value = value.value;
                        }
                        if (!field.closest('.video-container, .question-card')) {
                            field.dispatchEvent(new Event('input', { bubbles: true }));
                            field.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    });
                });
                if (typeof this.afterDraftRestore === 'function') this.afterDraftRestore();
                
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

            // HTMX forms swap the response fragment into the page region; htmx
            // issues the request and Django remains the validation authority.
            // Native document submit remains the no-HTMX fallback.
            if (window.htmx && form.hasAttribute('hx-post')) {
                return;
            }

            // Native form submit — includes the csrfmiddlewaretoken hidden input.
            form.submit();
        }
    };
}
