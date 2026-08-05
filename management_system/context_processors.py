from django.conf import settings
from django.urls import translate_url


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
