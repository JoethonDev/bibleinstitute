from django.db import transaction

from ..tasks import send_application_activated_task
from ..tasks import send_application_declined_task
from ..tasks import send_application_received_task


def send_application_received(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: send_application_received_task.delay(user_id))


def send_application_activated(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: send_application_activated_task.delay(user_id))


def send_application_declined(user):
    if not user.email:
        return
    user_id = user.pk
    transaction.on_commit(lambda: send_application_declined_task.delay(user_id))
