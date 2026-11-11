"""Lazy shell context (A24): resolve gallery visibility and banners only on use.

Context processors run for every rendered fragment. Returning callables lets
Django resolve them only when a template actually references them, and a
request-local cache prevents a repeated query when several rendered fragments in
one request need the same value.
"""

from django.conf import settings
from django.db.models import Case, IntegerField, Value, When
from django.urls import translate_url

from management_system.graduation_gallery import graduation_scopes_for_user
from management_system.models import StudentBanner, StudentMode
from management_system.student_experience.mode import resolve_student_mode


def static_asset_version(request):
    """Expose the deployment release key used by cache-busted static URLs."""
    return {"static_asset_version": settings.STATIC_ASSET_VERSION}


def localized_alternate_urls(request):
    """Expose language-prefixed versions of the current request URL."""
    current_url = request.get_full_path()
    return {
        "localized_url_en": translate_url(current_url, "en"),
        "localized_url_ar": translate_url(current_url, "ar"),
    }


def _memoized(request, key, compute):
    """Return a lazy callable backed by one request-local value."""

    def resolve():
        if not hasattr(request, key):
            setattr(request, key, compute())
        return getattr(request, key)

    return resolve


def graduation_gallery_visibility(request):
    """Expose whether the Graduates tab is visible to the current user."""

    def compute():
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        try:
            role = getattr(getattr(user, "role", None), "role", None)
            if role in {"admin", "staff"}:
                return True
            if role != "student":
                return False
            return graduation_scopes_for_user(user).exists()
        except Exception:
            return False

    return {"show_graduation_gallery": _memoized(request, "_show_graduation_gallery", compute)}


def student_banners(request):
    """Expose the latest active banner strips for signed-in student pages."""

    def compute():
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return []
        try:
            if getattr(getattr(user, "role", None), "role", None) != "student":
                return []
            if resolve_student_mode(request) != StudentMode.NEW:
                return list(
                    StudentBanner.objects.filter(is_active=True)
                    .order_by("-created_at", "-pk")[:3]
                )
            severity_order = Case(
                When(severity=StudentBanner.Severity.CRITICAL, then=Value(0)),
                When(severity=StudentBanner.Severity.WARNING, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
            return list(
                StudentBanner.objects.filter(is_active=True)
                .annotate(_severity_order=severity_order)
                .order_by("_severity_order", "-created_at", "-pk")[:2]
            )
        except Exception:
            return []

    return {"student_banners": _memoized(request, "_student_banners", compute)}
