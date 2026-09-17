"""Celery entry points for admin announcement fan-out."""

from __future__ import annotations

from celery import shared_task

from .announcements import (
    fan_out_announcement as run_announcement_fanout,
    pending_announcement_ids,
)


@shared_task
def fan_out_announcement(announcement_id: int) -> int:
    """Create the durable student-inbox rows for one announcement."""
    return run_announcement_fanout(announcement_id)


@shared_task
def recover_pending_announcements(limit: int = 50) -> int:
    """Requeue queued or stale sending announcements after worker loss."""
    ids = pending_announcement_ids(limit)
    for announcement_id in ids:
        fan_out_announcement.delay(announcement_id)
    return len(ids)
