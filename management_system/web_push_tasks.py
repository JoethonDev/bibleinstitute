"""Celery entry point for bounded browser Web Push delivery."""

from celery import shared_task

from .web_push import dispatch_due_web_push as dispatch_due_web_push_records


@shared_task(ignore_result=True)
def dispatch_due_web_push(limit: int = 1000) -> None:
    dispatch_due_web_push_records(limit=limit)
