"""Shared publication-window checks for student lesson access."""

from django.db.models import Q
from django.utils import timezone

from .timezones import ensure_aware


def lesson_available_filter(current_time=None) -> Q:
    current = ensure_aware(current_time or timezone.now())
    return Q(opening_date__isnull=True) | Q(opening_date__lte=current)


def lesson_is_available(lesson, current_time=None) -> bool:
    opening = lesson.opening_date
    if opening is None:
        return True
    return ensure_aware(opening) <= ensure_aware(current_time or timezone.now())
