/* Student notification bell, header panel poller, and pop-up cards.
   Presentation-only: the backend routes and payloads are frozen. */
(function () {
    'use strict';

    if (window.__lmsNotificationCenter) {
        return;
    }
    window.__lmsNotificationCenter = true;

    var root = document.getElementById('notification-root');
    if (!root) {
        return;
    }

    var POLL_INTERVAL = 30000;
    var MAX_POPUPS = 3;

    var liveUrl = root.getAttribute('data-live-url');
    var lastSeenKey = root.getAttribute('data-last-seen-key') || 'lms-notifications-last-seen';
    var bellButton = document.getElementById('notification-bell-button');
    var panel = document.getElementById('notification-panel');
    var popups = document.getElementById('notification-popups');

    var lastSeen = parseInt(window.localStorage.getItem(lastSeenKey) || '0', 10) || 0;
    var shownIds = new Set();

    function translate(message) {
        return typeof gettext === 'function' ? gettext(message) : message;
    }

    function readCookie(name) {
        var match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
        return match ? decodeURIComponent(match[2]) : '';
    }

    function applyBadge(count) {
        // Re-query: server OOB panel responses replace the badge element, so a
        // cached reference would become detached and stop updating.
        var target = document.getElementById('notification-badge');
        if (!target) {
            return;
        }
        var value = parseInt(count, 10);
        if (isNaN(value) || value < 0) {
            value = 0;
        }
        target.textContent = value > 0 ? String(value) : '';
        target.hidden = value <= 0;
    }

    function removePopup(element) {
        if (element && element.parentNode) {
            element.parentNode.removeChild(element);
        }
    }

    function openNotification(item) {
        if (!item || !item.open_url) {
            return;
        }
        fetch(item.open_url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'X-CSRFToken': readCookie('csrftoken')
            }
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('notification open failed');
                }
                return response.json();
            })
            .then(function (data) {
                var destination = (data && data.redirect) ? data.redirect : item.target_url;
                if (destination) {
                    window.location.href = destination;
                }
            })
            .catch(function () { /* errors are intentionally swallowed */ });
    }

    function markRead(item, popupElement) {
        if (!item || !item.read_url) {
            return;
        }
        fetch(item.read_url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Accept': 'application/json',
                'X-CSRFToken': readCookie('csrftoken')
            }
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('notification read failed');
                }
                return response.json();
            })
            .then(function (data) {
                applyBadge(data.unread_count);
                removePopup(popupElement);
            })
            .catch(function () { /* errors are intentionally swallowed */ });
    }

    function renderPopup(item) {
        if (!popups) {
            return;
        }

        var article = document.createElement('article');
        article.className = 'ps-notif-popup';
        article.setAttribute('role', 'status');

        var head = document.createElement('div');
        head.className = 'ps-notif-popup-head';

        var icon = document.createElement('i');
        icon.className = 'fas fa-bell';
        icon.setAttribute('aria-hidden', 'true');

        var title = document.createElement('strong');
        title.className = 'ps-notif-popup-title';
        title.textContent = item.title || '';

        head.appendChild(icon);
        head.appendChild(title);

        var body = document.createElement('p');
        body.className = 'ps-notif-popup-body';
        body.textContent = item.body || '';

        var actions = document.createElement('div');
        actions.className = 'ps-notif-popup-actions';

        var viewButton = document.createElement('button');
        viewButton.type = 'button';
        viewButton.className = 'ps-notif-popup-open';
        viewButton.textContent = translate('View');
        viewButton.addEventListener('click', function () {
            openNotification(item);
        });

        var readButton = document.createElement('button');
        readButton.type = 'button';
        readButton.className = 'ps-notif-popup-read';
        readButton.textContent = translate('Mark as read');
        readButton.addEventListener('click', function () {
            markRead(item, article);
        });

        var dismissButton = document.createElement('button');
        dismissButton.type = 'button';
        dismissButton.className = 'ps-notif-popup-dismiss';
        dismissButton.setAttribute('aria-label', translate('Close'));
        dismissButton.textContent = '\u00d7';
        dismissButton.addEventListener('click', function () {
            removePopup(article);
        });

        actions.appendChild(viewButton);
        actions.appendChild(readButton);
        actions.appendChild(dismissButton);

        article.appendChild(head);
        article.appendChild(body);
        article.appendChild(actions);
        popups.appendChild(article);

        while (popups.children.length > MAX_POPUPS) {
            popups.removeChild(popups.firstElementChild);
        }
    }

    function poll() {
        if (!liveUrl) {
            return;
        }
        fetch(liveUrl + '?after=' + lastSeen, {
            headers: { 'Accept': 'application/json' },
            credentials: 'same-origin'
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('notification poll failed');
                }
                return response.json();
            })
            .then(function (data) {
                applyBadge(data.unread_count);

                var items = Array.isArray(data.items) ? data.items : [];
                items.forEach(function (item) {
                    if (!item || shownIds.has(item.id)) {
                        return;
                    }
                    shownIds.add(item.id);
                    renderPopup(item);
                });

                lastSeen = Math.max(lastSeen, parseInt(data.max_id, 10) || 0);
                try {
                    window.localStorage.setItem(lastSeenKey, String(lastSeen));
                } catch (error) { /* storage unavailable */ }
            })
            .catch(function () { /* errors are intentionally swallowed */ });
    }

    function closePanel() {
        if (!panel) {
            return;
        }
        panel.hidden = true;
        if (bellButton) {
            bellButton.setAttribute('aria-expanded', 'false');
        }
    }

    function togglePanel() {
        if (!panel) {
            return;
        }
        if (panel.hidden) {
            panel.hidden = false;
            if (bellButton) {
                bellButton.setAttribute('aria-expanded', 'true');
            }
        } else {
            closePanel();
        }
    }

    if (bellButton) {
        bellButton.addEventListener('click', function (event) {
            event.preventDefault();
            togglePanel();
        });
    }

    document.addEventListener('click', function (event) {
        if (!root.contains(event.target)) {
            closePanel();
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' || event.key === 'Esc') {
            closePanel();
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            poll();
        }
    });

    poll();
    window.setInterval(function () {
        if (!document.hidden) {
            poll();
        }
    }, POLL_INTERVAL);
})();
