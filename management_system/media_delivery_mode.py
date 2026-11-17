"""Resolve and invalidate the configured HLS segment delivery mode."""

import logging

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError

from .models import MediaDeliverySettings

logger = logging.getLogger(__name__)

MEDIA_DELIVERY_SETTINGS_CACHE_KEY = "media_delivery:segment_mode:v1"
MEDIA_DELIVERY_SETTINGS_CACHE_TTL = 60


def invalidate_media_delivery_mode_cache() -> None:
    try:
        cache.delete(MEDIA_DELIVERY_SETTINGS_CACHE_KEY)
    except Exception:
        logger.warning("Could not invalidate the media delivery mode cache.")


def effective_segment_delivery_mode() -> str:
    """Return the stored segment delivery mode, or the environment default.

    The stored row decides the mode, including ``edge_gate``, before the legacy
    ``CLOUD_WORKER`` environment variable is considered. That variable only
    chooses the default when no valid mode is stored.
    """
    valid_modes = {
        MediaDeliverySettings.SegmentDeliveryMode.DIRECT_R2,
        MediaDeliverySettings.SegmentDeliveryMode.WORKER,
        MediaDeliverySettings.SegmentDeliveryMode.EDGE_GATE,
    }
    try:
        cached = cache.get(MEDIA_DELIVERY_SETTINGS_CACHE_KEY)
    except Exception:
        cached = None
    if cached in valid_modes:
        return cached
    try:
        mode = (
            MediaDeliverySettings.objects.filter(singleton="default")
            .values_list("segment_delivery_mode", flat=True)
            .first()
        )
    except DatabaseError:
        logger.warning("Could not read media delivery settings; falling back to legacy worker.")
        return MediaDeliverySettings.SegmentDeliveryMode.WORKER
    if mode in valid_modes:
        resolved = mode
    elif not getattr(settings, "CLOUD_WORKER", "").rstrip("/"):
        resolved = MediaDeliverySettings.SegmentDeliveryMode.DIRECT_R2
    else:
        resolved = MediaDeliverySettings.SegmentDeliveryMode.WORKER
    try:
        cache.set(
            MEDIA_DELIVERY_SETTINGS_CACHE_KEY,
            resolved,
            timeout=MEDIA_DELIVERY_SETTINGS_CACHE_TTL,
        )
    except Exception:
        pass
    return resolved
