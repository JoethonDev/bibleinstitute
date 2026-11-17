"""Durable cleanup for abandoned direct-upload capabilities."""

from __future__ import annotations

import logging

from celery import shared_task

from .direct_uploads import expire_abandoned_direct_uploads

logger = logging.getLogger(__name__)


@shared_task(name="management_system.direct_upload_tasks.expire_abandoned_direct_uploads", ignore_result=False)
def expire_abandoned_uploads(limit: int = 500) -> dict[str, int]:
    """Expire a bounded batch of orphaned capabilities and their staged bytes."""

    result = expire_abandoned_direct_uploads(limit=limit)
    if result["expired"]:
        logger.info(
            "expired abandoned direct uploads",
            extra={
                "event": "direct_upload_expired",
                "expired": result["expired"],
                "keys_removed": result["keys_removed"],
            },
        )
    return result
