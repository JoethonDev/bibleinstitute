"""Resolve the global and per-account student presentation mode."""

import logging

from django.core.cache import cache
from django.db import DatabaseError

from ..models import (
    StudentExperienceOverride,
    StudentExperienceSettings,
    StudentMode,
)
from .constants import (
    STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY,
    STUDENT_EXPERIENCE_SETTINGS_CACHE_TTL,
)

logger = logging.getLogger(__name__)
_VALID_MODES = {StudentMode.LEGACY, StudentMode.NEW}


def invalidate_student_experience_mode_cache() -> None:
    try:
        cache.delete(STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY)
    except Exception:
        logger.warning("Could not invalidate the student experience mode cache.")


def _global_mode() -> str:
    try:
        cached = cache.get(STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY)
    except Exception:
        cached = None
    if cached in _VALID_MODES:
        return cached

    try:
        settings_row = StudentExperienceSettings.load()
    except DatabaseError:
        logger.warning("Could not read the student experience mode; using legacy mode.")
        return StudentMode.LEGACY
    mode = settings_row.mode if settings_row and settings_row.mode in _VALID_MODES else StudentMode.LEGACY
    try:
        cache.set(
            STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY,
            mode,
            timeout=STUDENT_EXPERIENCE_SETTINGS_CACHE_TTL,
        )
    except Exception:
        pass
    return mode


def resolve_student_mode(request_or_user) -> str:
    """Return a request-cached mode; only student accounts can use new mode."""
    request = request_or_user if hasattr(request_or_user, "user") else None
    if request is not None and hasattr(request, "_student_experience_mode"):
        return request._student_experience_mode

    user = request.user if request is not None else request_or_user
    mode = StudentMode.LEGACY
    if getattr(user, "is_authenticated", False):
        try:
            role = getattr(getattr(user, "role", None), "role", None)
        except DatabaseError:
            role = None
        if role == "student":
            override = getattr(
                user,
                "student_experience_override",
                StudentExperienceOverride.INHERIT,
            )
            if override == StudentExperienceOverride.NEW:
                mode = StudentMode.NEW
            elif override == StudentExperienceOverride.LEGACY:
                mode = StudentMode.LEGACY
            elif override == StudentExperienceOverride.INHERIT:
                mode = _global_mode()

    if request is not None:
        request._student_experience_mode = mode
    return mode
