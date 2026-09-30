from __future__ import annotations

from django.db.models import Q

from .models import OfflineCity, User
from .utils.search import normalize_search_text


_OFFLINE_CITY_ALIASES = {
    "cairo": (
        "Cairo",
        "القاهرة",
        "القاهره",
    ),
    "giza": (
        "Giza",
        "الجيزة",
        "الجيزه",
    ),
}
_OFFLINE_CITY_KEYS = {
    normalize_search_text(alias): aliases
    for aliases in _OFFLINE_CITY_ALIASES.values()
    for alias in aliases
}


def is_configured_offline_city(city: str | None) -> bool:
    """Match a submitted city to an active OfflineCity setting, including Cairo/Giza Arabic spellings."""
    normalized = normalize_search_text(city)
    if not normalized:
        return False
    aliases = _OFFLINE_CITY_KEYS.get(normalized, (str(city).strip(),))
    candidates = Q()
    for alias in aliases:
        candidates |= Q(name__iexact=alias)
    return OfflineCity.objects.filter(is_active=True).filter(candidates).exists()


def apply_study_mode_from_city(user: User) -> bool:
    """Refresh a non-overridden student's mode from the configured offline-city list."""
    if user.study_mode_override:
        return False
    role = getattr(getattr(user, "role", None), "role", None)
    if getattr(user, "role_id", None) and role != "student":
        return False
    mode = "offline" if is_configured_offline_city(user.city) else "online"
    if user.study_mode == mode:
        return False
    user.study_mode = mode
    return True
