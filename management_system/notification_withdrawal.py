"""Per-recipient withdrawal of saved inbox, push and Telegram notifications."""

from __future__ import annotations

from dataclasses import dataclass

import telebot
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    PushDelivery,
    StudentNotification,
    TelegramBotConfig,
    TelegramNotificationDelivery,
    User,
)
from .telegram.configuration import stored_token
from .telegram.deletion import delete_bot_message


class NotificationWithdrawalError(Exception):
    """A safe failure from the admin notification withdrawal boundary."""


@dataclass(frozen=True)
class NotificationWithdrawalResult:
    already_withdrawn: bool
    telegram_deleted: int
    telegram_expired: int
    telegram_failed: int
    push_pending_recall: int


def _require_admin(actor: User) -> None:
    if not actor.is_authenticated or not actor.is_active or not actor.role or actor.role.role != "admin":
        raise NotificationWithdrawalError(_("Only administrators can withdraw notifications."))


def _active_bot() -> telebot.TeleBot | None:
    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if config is None:
        return None
    try:
        return telebot.TeleBot(stored_token(config), parse_mode=None, threaded=False)
    except Exception:
        return None


@transaction.atomic
def _withdraw_notification(notification_id: int, actor: User) -> tuple[StudentNotification, bool]:
    _require_admin(actor)
    try:
        notification = StudentNotification.objects.select_for_update().get(pk=notification_id)
    except StudentNotification.DoesNotExist as exc:
        raise NotificationWithdrawalError(_("This notification is no longer available.")) from exc
    already_withdrawn = notification.withdrawn_at is not None
    if not already_withdrawn:
        now = timezone.now()
        notification.withdrawn_at = now
        notification.withdrawn_by = actor
        notification.cancelled_at = notification.cancelled_at or now
        notification.save(
            update_fields=["withdrawn_at", "withdrawn_by", "cancelled_at", "updated_at"],
        )
        PushDelivery.objects.filter(
            notification_id=notification.pk,
            status=PushDelivery.Status.QUEUED,
        ).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="notification_withdrawn",
            error_message="The notification was withdrawn before delivery.",
            updated_at=now,
        )
        TelegramNotificationDelivery.objects.filter(
            student_notification_id=notification.pk,
            status=TelegramNotificationDelivery.Status.QUEUED,
        ).update(
            status=TelegramNotificationDelivery.Status.SKIPPED,
            last_error=_("The notification was withdrawn before delivery."),
            updated_at=now,
        )
    return notification, already_withdrawn


def withdraw_student_notification(
    *,
    notification_id: int,
    actor: User,
) -> NotificationWithdrawalResult:
    """Hide one student's inbox item and delete its bot message where possible.

    Expo/FCM/APNs cannot remove an already-delivered system notification from
    the server. The mobile client consumes the withdrawal cursor and dismisses
    matching notifications when it next becomes active.
    """
    notification, already_withdrawn = _withdraw_notification(notification_id, actor)
    rows = list(
        TelegramNotificationDelivery.objects.filter(
            student_notification_id=notification.pk,
            status=TelegramNotificationDelivery.Status.SENT,
        ).exclude(
            telegram_deletion_status__in=(
                TelegramNotificationDelivery.DeletionStatus.DELETED,
                TelegramNotificationDelivery.DeletionStatus.EXPIRED,
            ),
        ).order_by("pk")
    )
    bot = _active_bot() if rows else None
    now = timezone.now()
    counts = {"deleted": 0, "expired": 0, "failed": 0}
    for delivery in rows:
        if bot is None:
            status, error = "failed", _("The Telegram bot is not active.")
        else:
            status, error = delete_bot_message(
                bot,
                chat_id=delivery.telegram_chat_id,
                message_id=delivery.telegram_message_id,
                sent_at=delivery.sent_at,
            )
        values = {
            "telegram_deletion_status": status,
            "telegram_deleted_by_id": actor.pk if status == "deleted" else None,
            "telegram_deleted_at": now if status == "deleted" else None,
            "telegram_deletion_error": error,
            "updated_at": now,
        }
        TelegramNotificationDelivery.objects.filter(pk=delivery.pk).update(**values)
        counts[status] += 1
    push_pending_recall = PushDelivery.objects.filter(
        notification_id=notification.pk,
        status__in=(
            PushDelivery.Status.SENDING,
            PushDelivery.Status.TICKETED,
            PushDelivery.Status.DELIVERED,
        ),
    ).count()
    return NotificationWithdrawalResult(
        already_withdrawn=already_withdrawn,
        telegram_deleted=counts["deleted"],
        telegram_expired=counts["expired"],
        telegram_failed=counts["failed"],
        push_pending_recall=push_pending_recall,
    )
