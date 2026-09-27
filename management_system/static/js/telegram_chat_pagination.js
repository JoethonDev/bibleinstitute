(() => {
    const observers = new WeakMap();
    const requestsInFlight = new WeakSet();

    function beginRequest(root) {
        if (requestsInFlight.has(root)) return false;
        requestsInFlight.add(root);
        return true;
    }

    function endRequest(root) {
        requestsInFlight.delete(root);
        root.dataset.scrollLockTop = String(root.scrollTop);
        if (root.isConnected) bindScrollLoading(root);
        else bindScrollLoading();
    }

    function removeDuplicateConversationRows(root) {
        if (root.id !== 'telegram-conversation-list') return;
        const seenUsers = new Set();
        root.querySelectorAll('.tg-chat-row[data-chat-user-id]').forEach((row) => {
            const userId = row.dataset.chatUserId;
            if (seenUsers.has(userId)) row.remove();
            else seenUsers.add(userId);
        });
    }

    function bindScrollLoading(singleRoot = null) {
        const roots = singleRoot
            ? [singleRoot]
            : document.querySelectorAll('#telegram-conversation-list, #telegram-message-list');
        roots.forEach((root) => {
            const previous = observers.get(root);
            if (previous) previous.disconnect();

            if (root.dataset.scrollHooksReady !== 'true') {
                const armAfterUserScroll = () => {
                    if (requestsInFlight.has(root) || root.dataset.scrollArmed === 'true') return;
                    const lockTop = Number(root.dataset.scrollLockTop);
                    if (Number.isFinite(lockTop) && Math.abs(root.scrollTop - lockTop) < 1) return;
                    root.dataset.scrollArmed = 'true';
                    bindScrollLoading(root);
                };
                root.addEventListener('scroll', armAfterUserScroll, { passive: true });
                root.dataset.scrollHooksReady = 'true';
            }

            if (requestsInFlight.has(root)) return;
            const sentinel = root.querySelector('[data-scroll-page][data-url]');
            if (!sentinel || !window.htmx || !('IntersectionObserver' in window)) return;

            const observer = new IntersectionObserver(async (entries) => {
                const entry = entries[0];
                const marker = entry && entry.target;
                if (!entry?.isIntersecting || !marker || marker.dataset.loading === 'true'
                    || requestsInFlight.has(root)
                    || root.dataset.scrollArmed === 'false') return;
                const url = marker.dataset.url;
                if (!url) return;
                if (!beginRequest(root)) return;

                marker.dataset.loading = 'true';
                root.dataset.scrollArmed = 'false';
                const direction = marker.dataset.direction;
                const oldHeight = root.scrollHeight;
                const oldTop = root.scrollTop;
                try {
                    await window.htmx.ajax('GET', url, { target: marker, swap: 'outerHTML' });
                    if (root.id === 'telegram-conversation-list' && direction === 'next') {
                        root.dataset.loadedPages = String(Number(root.dataset.loadedPages || '1') + 1);
                        removeDuplicateConversationRows(root);
                    }
                    if (root.id === 'telegram-message-list') {
                        root.scrollTop = oldTop + (root.scrollHeight - oldHeight);
                    }
                } catch (_) {
                    if (marker.isConnected) marker.dataset.loading = 'false';
                } finally {
                    endRequest(root);
                }
            }, {
                root,
                rootMargin: root.id === 'telegram-message-list' ? '40px 0px 0px' : '0px 0px 80px',
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
    window.telegramChatBeginRequest = beginRequest;
    window.telegramChatEndRequest = endRequest;
})();
