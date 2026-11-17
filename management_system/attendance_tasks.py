"""Attendance reconciliation and off-peak session cleanup tasks."""

import time

from celery import shared_task
from django.utils import timezone

from .session_cleanup import cleanup_expired_viewing_sessions
from .utils.attendance import reconcile_missing_exits


@shared_task(name="management_system.attendance_tasks.reconcile_daily_attendance")
def reconcile_daily_attendance() -> int:
    """Record the missing exit of every entrance for the local current day."""

    created, _cursor = reconcile_missing_exits(timezone.localdate())
    return created


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=27,
    time_limit=30,
    max_retries=5,
    name="management_system.attendance_tasks.cleanup_viewing_sessions",
)
def cleanup_viewing_sessions(self) -> dict[str, object]:
    """Run one bounded cleanup slice on the dedicated admin queue."""
    result = cleanup_expired_viewing_sessions(
        dry_run=False,
        deadline=time.monotonic() + 25,
    )
    if result["lease_acquired"] and result["has_backlog"]:
        try:
            self.apply_async(queue="admin", countdown=2)
        except Exception as exc:
            raise self.retry(exc=exc, countdown=60)
    return result
