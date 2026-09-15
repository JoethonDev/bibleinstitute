// Toast Notification Store
document.addEventListener('alpine:init', () => {
    // Monotonic ids: Date.now() collides when two toasts are added in the
    // same millisecond, and duplicate x-for keys break Alpine's DOM processing.
    let nextNotificationId = 0;

    Alpine.store('notifications', {
        items: [],
        add(message, type = 'info') {
            const id = ++nextNotificationId;
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), 2000);
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
