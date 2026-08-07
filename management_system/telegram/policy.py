"""Stable Telegram role and endpoint boundaries.

These constants are the integration policy boundary. Telegram capabilities are
not inferred from a user's Telegram identity or from hidden UI controls.
"""

from __future__ import annotations

from urllib.parse import urlsplit


TELEGRAM_WEBHOOK_PATH = "/api/telegram/webhook/"
TELEGRAM_ADMIN_ROLE = "admin"
TELEGRAM_STUDENT_ROLE = "student"

# Existing LMS terminology maps to these role codes. ``moderator`` remains an
# attendance-only role and is deliberately not silently granted Telegram
# management or support access.
TELEGRAM_ROLE_MAPPING = {
    "Teacher": "staff",
    "Mandatory": "moderator",
    "Admin": TELEGRAM_ADMIN_ROLE,
}
TELEGRAM_MANAGEMENT_READ_ROLES = frozenset({TELEGRAM_ADMIN_ROLE, "staff"})
TELEGRAM_LINKABLE_ROLES = frozenset({TELEGRAM_ADMIN_ROLE, "staff", TELEGRAM_STUDENT_ROLE})


def webhook_url(site_domain: str) -> str:
    """Return the fixed non-localized HTTPS webhook URL for a site domain."""
    value = site_domain.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError("DJANGO_SITE_DOMAIN must be an HTTPS origin without credentials.")
    return f"{value}{TELEGRAM_WEBHOOK_PATH}"
