// Toast Notification Store
document.addEventListener('alpine:init', () => {
    Alpine.store('notifications', {
        items: [],
        add(message, type = 'info') {
            const id = Date.now();
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), 5000);
        },
        remove(id) {
            this.items = this.items.filter(item => item.id !== id);
        },
        success(message) {
            this.add(message, 'success');
        },
        error(message) {
            this.add(message, 'danger');
        },
        info(message) {
            this.add(message, 'info');
        },
        warning(message) {
            this.add(message, 'warning');
        }
    });
});
