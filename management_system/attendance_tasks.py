"""Daily attendance reconciliation on the shared Celery worker."""

from celery import shared_task
from django.utils import timezone

from .utils.attendance import reconcile_missing_exits


@shared_task(name="management_system.attendance_tasks.reconcile_daily_attendance")
def reconcile_daily_attendance() -> int:
    """Record the missing exit of every entrance for the local current day."""
    return reconcile_missing_exits(timezone.localdate())
