from django.conf import settings


def static_asset_version(request):
    """Expose the deployment release key used by cache-busted static URLs."""
    return {"static_asset_version": settings.STATIC_ASSET_VERSION}
