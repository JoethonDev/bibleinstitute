"""Celery entry points for bounded Expo push dispatch and receipt polling."""

from __future__ import annotations

from celery import shared_task

from .mobile_push import (
    dispatch_due_mobile_push as dispatch_due_mobile_push_records,
    process_mobile_push_receipts as process_mobile_push_receipt_records,
)


@shared_task(ignore_result=True)
def dispatch_due_mobile_push(limit: int = 1000) -> None:
    """Materialize and send due StudentNotification push deliveries."""
    dispatch_due_mobile_push_records(limit=limit)


@shared_task(ignore_result=True)
def process_mobile_push_receipts(limit: int = 1000) -> None:
    """Poll Expo receipts for ticketed deliveries."""
    process_mobile_push_receipt_records(limit=limit)
