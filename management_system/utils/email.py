import logging

from django.db import transaction

from ..tasks import send_application_activated_task
from ..tasks import send_application_declined_task
from ..tasks import send_application_received_task
from ..models import User

logger = logging.getLogger(__name__)


def _enqueue_application_email(task, user_id: int) -> None:
    """Queue the status email, falling back to a direct send when the broker is down."""
    try:
        task.delay(user_id)
    except Exception:
        logger.exception("Application email could not be queued", extra={"event": "application_email_queue_failed", "user_id": user_id})
        try:
            task(user_id)
        except Exception:
            logger.exception("Application email could not be sent", extra={"event": "application_email_send_failed", "user_id": user_id})


def send_application_received(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: _enqueue_application_email(send_application_received_task, user_id))


def send_application_activated(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: _enqueue_application_email(send_application_activated_task, user_id))


def send_application_declined(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: _enqueue_application_email(send_application_declined_task, user_id))


def send_application_status_notification(user: User, status: str) -> None:
    if status == "active":
        send_application_activated(user)
    elif status == "declined":
        send_application_declined(user)
