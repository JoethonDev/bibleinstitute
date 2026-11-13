"""Small presentation helpers shared by the admin workspace components."""

from __future__ import annotations

import re

from django import template

from ..admin_navigation import (
    admin_workspace_navigation,
    quick_create_actions_for,
    top_actions_for,
)
from ..utils.decorators import (
    can_correct_attendance,
    can_delete_content,
    can_grade,
    can_manage_academic_setup,
    can_manage_applications,
    can_manage_content,
    can_scan_attendance,
    can_view_reports,
)

register = template.Library()

#: Presentation-only tone mapping. Labels always come from the owning model
#: display method or a translated msgid, never from this table.
STATUS_TONES: dict[str, str] = {
    "active": "green",
    "completed": "green",
    "published": "green",
    "sent": "green",
    "delivered": "green",
    "linked": "green",
    "passed": "green",
    "full": "green",
    "pending": "amber",
    "draft": "amber",
    "partial": "amber",
    "queued": "amber",
    "claimed": "amber",
    "warning": "amber",
    "needs review": "amber",
    "declined": "red",
    "failed": "red",
    "absent": "red",
    "error": "red",
    "rejected": "red",
    "manual": "teal",
    "auto": "teal",
    "scan": "teal",
    "info": "teal",
    "current": "teal",
    "processing": "blue",
    "running": "blue",
    "in progress": "blue",
    "scheduled": "blue",
    "archived": "muted",
    "cancelled": "muted",
    "inactive": "muted",
    "not started": "muted",
    "withdrawn": "muted",
    "unavailable": "muted",
}

_WORD_RE = re.compile(r"\S+")


def initials_from_name(name: str) -> str:
    """Return at most two initials, centered by the avatar component."""
    words = _WORD_RE.findall((name or "").strip())
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


@register.filter
def aw_initials(value) -> str:
    """Initials for a user, name string or empty value."""
    if value is None:
        return "?"
    display = ""
    for attribute in ("get_full_name", "get_username"):
        getter = getattr(value, attribute, None)
        if callable(getter):
            try:
                display = str(getter() or "")
            except TypeError:
                display = ""
            if display.strip():
                break
    if not display.strip():
        for attribute in ("full_name", "name", "username"):
            candidate = getattr(value, attribute, None)
            if isinstance(candidate, str) and candidate.strip():
                display = candidate
                break
    if not display.strip():
        display = str(value)
    return initials_from_name(display)


#: Pill tone names that `.aw-pill--<tone>` styles. An explicit tone passes
#: through aw_status_tone unchanged instead of being remapped to muted.
_AW_TONE_NAMES = frozenset(
    {"teal", "gold", "green", "red", "amber", "blue", "violet", "muted"}
)


@register.filter
def aw_status_tone(value) -> str:
    """Return the presentational tone for a raw status value.

    Callers that already know the tone (``tone="red"`` for a failure) must not
    be remapped through the status dictionary, which previously forced every
    explicit tone to muted.
    """
    key = str(value or "").strip().lower().replace("_", " ")
    if key in _AW_TONE_NAMES:
        return key
    return STATUS_TONES.get(key, "muted")


@register.filter
def aw_display_name(value) -> str:
    """Full name with a username fallback, never an empty cell."""
    if value is None:
        return ""
    getter = getattr(value, "get_full_name", None)
    if callable(getter):
        try:
            name = str(getter() or "").strip()
        except TypeError:
            name = ""
        if name:
            return name
    return str(getattr(value, "username", "") or value)


_CAPABILITY_CHECKS = {
    "manage_applications": can_manage_applications,
    "manage_academic_setup": can_manage_academic_setup,
    "manage_content": can_manage_content,
    "delete_content": can_delete_content,
    "grade": can_grade,
    "scan_attendance": can_scan_attendance,
    "correct_attendance": can_correct_attendance,
    "view_reports": can_view_reports,
}


@register.simple_tag(takes_context=True)
def aw_capabilities(context) -> dict:
    """Expose the canonical capability functions to templates by name.

    One source of truth: the tag calls the same functions the views use, so a
    template never compares role strings. A page whose intent is ambiguous must
    keep using the flag for its owning view's decorator.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None:
        return {key: False for key in _CAPABILITY_CHECKS}
    return {key: bool(check(user)) for key, check in _CAPABILITY_CHECKS.items()}


@register.simple_tag(takes_context=True)
def aw_admin_workspace(context) -> dict:
    """Return the canonical navigation contract for the current request."""
    request = context.get("request")
    user = getattr(request, "user", None)
    resolver_match = getattr(request, "resolver_match", None) if request else None
    url_name = getattr(resolver_match, "url_name", None) if resolver_match else None
    navigation = admin_workspace_navigation(user, url_name)
    navigation["quick_create"] = quick_create_actions_for(user) if user else ()
    navigation["top_actions"] = top_actions_for(user) if user else ()
    return navigation
