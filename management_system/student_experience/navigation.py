from django.urls import reverse
from django.utils.translation import gettext_lazy as _


_NAV_ITEMS = (
    ("learn", _("Learn"), "courses", "book-open"),
    ("calendar", _("Calendar"), "student-calendar", "calendar"),
    ("notifications", _("Notifications"), "student-notifications", "bell"),
    ("profile", _("Profile"), "view-profile", "user"),
)


def student_navigation(active_page: str) -> list[dict[str, object]]:
    active_key = "learn" if active_page in {"courses", "course", "lesson", "quiz"} else active_page
    return [
        {
            "key": key,
            "label": label,
            "url": reverse(url_name),
            "icon": icon,
            "active": key == active_key,
        }
        for key, label, url_name, icon in _NAV_ITEMS
    ]
