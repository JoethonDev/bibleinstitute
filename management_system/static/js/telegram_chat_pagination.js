(() => {
    const observers = new WeakMap();

    function bindScrollLoading() {
        document.querySelectorAll('#telegram-conversation-list, #telegram-message-list').forEach((root) => {
            const previous = observers.get(root);
            if (previous) previous.disconnect();
            const sentinel = root.querySelector('[data-scroll-page][data-url]');
            if (!sentinel || !window.htmx || !('IntersectionObserver' in window)) return;

            const observer = new IntersectionObserver(async (entries) => {
                const entry = entries[0];
                const marker = entry && entry.target;
                if (!entry?.isIntersecting || !marker || marker.dataset.loading === 'true') return;
                const url = marker.dataset.url;
                if (!url) return;

                marker.dataset.loading = 'true';
                const direction = marker.dataset.direction;
                const oldHeight = root.scrollHeight;
                const oldTop = root.scrollTop;
                try {
                    await window.htmx.ajax('GET', url, { target: marker, swap: 'outerHTML' });
                    if (root.id === 'telegram-conversation-list' && direction === 'next') {
                        root.dataset.loadedPages = String(Number(root.dataset.loadedPages || '1') + 1);
                    }
                    if (root.id === 'telegram-message-list') {
                        root.scrollTop = oldTop + (root.scrollHeight - oldHeight);
                    }
                } catch (_) {
                    marker.dataset.loading = 'false';
                }
            }, {
                root,
                rootMargin: root.id === 'telegram-message-list' ? '160px 0px 0px' : '0px 0px 240px',
                threshold: 0,
            });
            observer.observe(sentinel);
            observers.set(root, observer);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bindScrollLoading, { once: true });
    } else {
        bindScrollLoading();
    }
    document.addEventListener('htmx:after:swap', bindScrollLoading);
})();
