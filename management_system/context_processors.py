from django.conf import settings
from django.urls import translate_url

from management_system.graduation_gallery import graduation_scopes_for_user
from management_system.models import StudentBanner


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


def graduation_gallery_visibility(request):
    """Expose whether the Graduates tab is visible to the current user."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"show_graduation_gallery": False}
    try:
        role = getattr(getattr(user, "role", None), "role", None)
        if role in {"admin", "staff"}:
            return {"show_graduation_gallery": True}
        if role != "student":
            return {"show_graduation_gallery": False}
        return {"show_graduation_gallery": graduation_scopes_for_user(user).exists()}
    except Exception:
        return {"show_graduation_gallery": False}


def student_banners(request):
    """Expose the latest active banner strips for signed-in student pages."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {"student_banners": []}
    try:
        if getattr(getattr(user, "role", None), "role", None) != "student":
            return {"student_banners": []}
        return {
            "student_banners": list(
                StudentBanner.objects.filter(is_active=True).order_by("-created_at", "-pk")[:3]
            )
        }
    except Exception:
        return {"student_banners": []}
