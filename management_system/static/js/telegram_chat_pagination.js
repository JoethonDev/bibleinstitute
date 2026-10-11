(() => {
    // Scroll-triggered pagination for the Telegram chat surfaces.
    //
    // Two independent scroll roots:
    //   - #telegram-message-list   loads OLDER messages at the top
    //   - #telegram-conversation-list loads the NEXT page of conversations
    //
    // The sentinel is loaded whenever it becomes visible. A user scroll is not
    // required: a thread shorter than its viewport (for example a single
    // visible message) would never emit a scroll event, so gating on a manual
    // "armed" flag permanently blocked the rest of the chat. Requests are still
    // serialized per root so a swap can never fan out into parallel loads.
    const observers = new Map();
    const requestsInFlight = new WeakSet();

    function isConversationRoot(root) {
        return root.id === 'telegram-conversation-list';
    }

    function removeDuplicateConversationRows(root) {
        if (!isConversationRoot(root)) return;
        const seenUsers = new Set();
        root.querySelectorAll('.tg-chat-row[data-chat-user-id]').forEach((row) => {
            const userId = row.dataset.chatUserId;
            if (seenUsers.has(userId)) row.remove();
            else seenUsers.add(userId);
        });
    }

    function rootsToBind(singleRoot = null) {
        if (singleRoot && singleRoot.nodeType === 1) return [singleRoot];
        return document.querySelectorAll('#telegram-conversation-list, #telegram-message-list');
    }

    function bindScrollLoading(singleRoot = null) {
        rootsToBind(singleRoot).forEach((root) => {
            const previous = observers.get(root);
            if (previous) previous.disconnect();

            const sentinel = root.querySelector('[data-scroll-page][data-url]');
            if (!sentinel || !window.htmx || !('IntersectionObserver' in window)) {
                observers.delete(root);
                return;
            }

            const observer = new IntersectionObserver((entries) => {
                const entry = entries[0];
                const marker = entry && entry.target;
                if (!entry?.isIntersecting || !marker || !marker.dataset.url) return;
                if (marker.dataset.loading === 'true' || requestsInFlight.has(root)) return;
                requestsInFlight.add(root);
                marker.dataset.loading = 'true';

                const direction = marker.dataset.direction;
                const oldHeight = root.scrollHeight;
                const oldTop = root.scrollTop;
                window.htmx.ajax('GET', marker.dataset.url, {
                    target: marker,
                    swap: 'outerHTML',
                })
                    .then(() => {
                        if (isConversationRoot(root) && direction === 'next') {
                            root.dataset.loadedPages = String(Number(root.dataset.loadedPages || '1') + 1);
                            removeDuplicateConversationRows(root);
                        }
                        if (root.id === 'telegram-message-list') {
                            // Older messages were prepended: keep the reader's
                            // current line in place.
                            root.scrollTop = oldTop + (root.scrollHeight - oldHeight);
                        }
                    })
                    .catch(() => {
                        if (marker.isConnected) marker.dataset.loading = 'false';
                    })
                    .finally(() => {
                        requestsInFlight.delete(root);
                        // The sentinel is replaced by the response, so the
                        // observer must be rebuilt around the new element.
                        bindScrollLoading(root);
                    });
            }, {
                root,
                rootMargin: root.id === 'telegram-message-list' ? '150px 0px 0px' : '0px 0px 200px',
                threshold: 0,
            });

            observer.observe(sentinel);
            observers.set(root, observer);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => bindScrollLoading(), { once: true });
    } else {
        bindScrollLoading();
    }
    document.addEventListener('htmx:after:swap', () => bindScrollLoading());

    window.telegramChatRebind = bindScrollLoading;
    window.telegramChatInFlight = (root) => requestsInFlight.has(root);
    // Shared serialization gate so a polling refresh and a scroll page never
    // run against the same scroll root at the same time.
    window.telegramChatBeginRequest = (root) => {
        if (!root || requestsInFlight.has(root)) return false;
        requestsInFlight.add(root);
        return true;
    };
    window.telegramChatEndRequest = (root) => {
        if (root) requestsInFlight.delete(root);
    };
})();
