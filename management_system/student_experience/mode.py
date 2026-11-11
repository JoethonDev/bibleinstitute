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


def _global_settings() -> tuple[str, bool]:
    try:
        cached = cache.get(STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY)
    except Exception:
        cached = None
    if isinstance(cached, dict) and cached.get("mode") in _VALID_MODES:
        return cached["mode"], bool(cached.get("force_all"))

    try:
        settings_row = StudentExperienceSettings.load()
    except DatabaseError:
        logger.warning("Could not read the student experience mode; using legacy mode.")
        return StudentMode.LEGACY, False
    mode = settings_row.mode if settings_row and settings_row.mode in _VALID_MODES else StudentMode.LEGACY
    force_all = bool(getattr(settings_row, "force_all", False))
    try:
        cache.set(
            STUDENT_EXPERIENCE_SETTINGS_CACHE_KEY,
            {"mode": mode, "force_all": force_all},
            timeout=STUDENT_EXPERIENCE_SETTINGS_CACHE_TTL,
        )
    except Exception:
        pass
    return mode, force_all


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
            global_mode, force_all = _global_settings()
            if force_all:
                mode = global_mode
            else:
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
                    mode = global_mode

    if request is not None:
        request._student_experience_mode = mode
    return mode
