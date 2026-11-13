from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy


_NAV_ITEMS = (
    ("learn", _("Learn"), "courses", "book-open"),
    ("calendar", _("Calendar"), "student-calendar", "calendar"),
    ("support", _("Support"), "student-support", "message"),
    ("offline", pgettext_lazy("student navigation", "Offline"), "offline-library", "download"),
    ("notifications", _("Notifications"), "student-notifications", "bell"),
    ("profile", _("Profile"), "view-profile", "user"),
)


def student_navigation(
    active_page: str,
    *,
    include_support: bool = True,
    include_offline: bool = True,
) -> list[dict[str, object]]:
    active_key = "learn" if active_page in {
        "courses", "course", "lesson", "quiz", "graduates", "download",
    } else active_page
    return [
        {
            "key": key,
            "label": label,
            "url": reverse(url_name),
            "icon": icon,
            "active": key == active_key,
        }
        for key, label, url_name, icon in _NAV_ITEMS
        if (include_support or key != "support") and (include_offline or key != "offline")
    ]
