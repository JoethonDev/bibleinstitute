"""Durable, authorized Telegram academic and announcement delivery."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import telebot
from django.db import transaction
from django.db.models import Case, Exists, F, IntegerField, OuterRef, When
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import (
    Enrollment,
    StudentNotification,
    TelegramAccount,
    TelegramBotConfig,
    TelegramNotificationDelivery,
    QuizStudentOpening,
)
from ..notification_redelivery import eligible_redelivery_notification_ids, redelivery_source_queryset
from ..student_notifications import is_active_published_lesson, is_active_published_quiz
from ..utils.lesson_access import lesson_is_available
from ..utils.timezones import ensure_aware
from .media import audio_links
from .navigation import format_lesson, format_quiz, lesson_detail_keyboard, quiz_detail_keyboard
from .configuration import stored_token
from .deletion import delete_bot_message

logger = logging.getLogger(__name__)


NOTIFICATION_BATCH_SIZE = 500
MAX_DELIVERY_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)


def _claim_due_ids(limit: int) -> list[int]:
    now = timezone.now()
    batch_limit = max(1, min(limit, NOTIFICATION_BATCH_SIZE))
    stale_before = now - timedelta(minutes=15)
    with transaction.atomic():
        stale_rows = list(
            TelegramNotificationDelivery.objects.filter(
                status=TelegramNotificationDelivery.Status.SENDING,
                updated_at__lt=stale_before,
            )
            .order_by("updated_at", "pk")
            .values_list("pk", "student_notification_id")[:batch_limit]
        )
        if stale_rows:
            notification_ids = sorted({notification_id for _, notification_id in stale_rows if notification_id})
            notifications = {
                notification.pk: notification
                for notification in StudentNotification.objects.select_for_update(skip_locked=True)
                .filter(pk__in=notification_ids)
                .order_by("pk")
            }
            stale_ids = [
                delivery_id
                for delivery_id, notification_id in stale_rows
                if notification_id is None
                or notification_id not in notification_ids
                or notification_id in notifications
            ]
            stale_deliveries = list(
                TelegramNotificationDelivery.objects.select_for_update(skip_locked=True)
                .filter(pk__in=stale_ids, status=TelegramNotificationDelivery.Status.SENDING)
                .order_by("updated_at", "pk")
            )
            for delivery in stale_deliveries:
                notification = notifications.get(delivery.student_notification_id)
                if notification is None:
                    delivery.status = TelegramNotificationDelivery.Status.SKIPPED
                    delivery.last_error = _("Recipient is no longer authorized.")
                elif notification.withdrawn_at is not None:
                    delivery.status = TelegramNotificationDelivery.Status.SKIPPED
                    delivery.last_error = _("The notification was withdrawn before delivery.")
                elif notification.cancelled_at is not None:
                    delivery.status = TelegramNotificationDelivery.Status.SKIPPED
                    delivery.last_error = _("The notification was cancelled before delivery.")
                elif delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
                    delivery.status = TelegramNotificationDelivery.Status.FAILED
                    delivery.last_error = _("Telegram message delivery failed.")
                else:
                    delivery.status = TelegramNotificationDelivery.Status.QUEUED
                    delivery.scheduled_for = now
                    delivery.last_error = _("A previous delivery worker stopped before completion.")
                delivery.updated_at = now
            if stale_deliveries:
                TelegramNotificationDelivery.objects.bulk_update(
                    stale_deliveries,
                    ["status", "scheduled_for", "last_error", "updated_at"],
                    batch_size=NOTIFICATION_BATCH_SIZE,
                )

        ids = list(
            TelegramNotificationDelivery.objects.select_for_update(
                of=("self",),
                skip_locked=True,
            )
            .filter(
                status=TelegramNotificationDelivery.Status.QUEUED,
                scheduled_for__lte=now,
                student_notification__cancelled_at__isnull=True,
                student_notification__withdrawn_at__isnull=True,
            )
            .order_by("scheduled_for", "pk")
            .values_list("pk", flat=True)[:batch_limit]
        )
        if ids:
            TelegramNotificationDelivery.objects.filter(pk__in=ids).update(
                status=TelegramNotificationDelivery.Status.SENDING,
                attempt_count=F("attempt_count") + 1,
                updated_at=now,
            )
    return ids


def _authorized_delivery(
    delivery: TelegramNotificationDelivery,
    authorized_ids: set[int],
    exceptional_windows: dict[tuple[int, int], tuple[object, object]],
) -> bool:
    account = delivery.telegram_account
    if (
        account is None
        or not account.is_active
        or account.user_id != delivery.user_id
        or delivery.telegram_chat_id is None
        or account.telegram_chat_id != delivery.telegram_chat_id
        or delivery.pk not in authorized_ids
    ):
        return False
    notification = delivery.student_notification
    if notification is None or notification.cancelled_at is not None or notification.withdrawn_at is not None:
        return False
    if delivery.lesson_id:
        lesson = is_active_published_lesson(delivery.lesson)
        return bool(
            lesson
            and lesson_is_available(lesson)
            and delivery.pk in authorized_ids
        )
    if delivery.quiz_id:
        quiz = is_active_published_quiz(delivery.quiz)
        return bool(
            quiz
            and delivery.pk in authorized_ids
            and _quiz_delivery_is_open(delivery, exceptional_windows)
        )
    if delivery.notification_type == TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT:
        notification = delivery.student_notification
        return bool(
            delivery.announcement_id
            and delivery.announcement is not None
            and notification is not None
            and notification.notification_type == StudentNotification.NotificationType.ANNOUNCEMENT
            and notification.student_id == delivery.user_id
            and notification.announcement_id == delivery.announcement_id
            and notification.cancelled_at is None
        )
    return False


def _quiz_delivery_is_open(
    delivery: TelegramNotificationDelivery,
    exceptional_windows: dict[tuple[int, int], tuple[object, object]],
) -> bool:
    opening, closing = exceptional_windows.get(
        (delivery.quiz_id, delivery.user_id),
        (delivery.quiz.opening_date, delivery.quiz.closing_date),
    )
    current = timezone.now()
    return ensure_aware(opening) <= current < ensure_aware(closing)


def _authorized_delivery_ids(deliveries) -> set[int]:
    normal_access = Enrollment.objects.filter(
        student_id=OuterRef("user_id"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("source_scope_id"),
    )
    targeted_access = Enrollment.objects.filter(
        student_id=OuterRef("user_id"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type__in=(Enrollment.Type.REPEAT, Enrollment.Type.REMEDIAL, Enrollment.Type.MANUAL),
        course_offering_id=OuterRef("source_offering_id"),
    )
    academic_ids = set(
        deliveries.annotate(
            source_offering_id=Case(
                When(lesson_id__isnull=False, then=F("lesson__course_offering_id")),
                default=F("quiz__course_offering_id"),
                output_field=IntegerField(),
            ),
            source_scope_id=Case(
                When(lesson_id__isnull=False, then=F("lesson__course_offering__academic_year_level_id")),
                default=F("quiz__course_offering__academic_year_level_id"),
                output_field=IntegerField(),
            ),
        )
        .filter(
            notification_type__in=(
                TelegramNotificationDelivery.NotificationType.LESSON_PUBLISHED,
                TelegramNotificationDelivery.NotificationType.QUIZ_OPENING,
            ),
            user__is_active=True,
            user__application_status="active",
            user__role__role="student",
        )
        .filter(Exists(normal_access) | Exists(targeted_access))
        .values_list("pk", flat=True)
    )
    announcement_ids = set(
        deliveries.filter(
            notification_type=TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT,
            user__is_active=True,
            user__application_status="active",
            user__role__role="student",
            student_notification__notification_type=StudentNotification.NotificationType.ANNOUNCEMENT,
            student_notification__student_id=F("user_id"),
            student_notification__announcement_id=F("announcement_id"),
            student_notification__cancelled_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    return academic_ids | announcement_ids


def _mark_skipped(delivery_id: int, message: str) -> None:
    TelegramNotificationDelivery.objects.filter(
        pk=delivery_id,
        status=TelegramNotificationDelivery.Status.SENDING,
    ).update(
        status=TelegramNotificationDelivery.Status.SKIPPED,
        last_error=message[:500],
        updated_at=timezone.now(),
    )


@transaction.atomic
def _mark_failed_or_retry(delivery_id: int, message: str) -> None:
    notification_id = TelegramNotificationDelivery.objects.filter(pk=delivery_id).values_list(
        "student_notification_id", flat=True,
    ).first()
    notification = None
    if notification_id is not None:
        notification = StudentNotification.objects.select_for_update().filter(pk=notification_id).first()
    delivery = TelegramNotificationDelivery.objects.select_for_update().filter(pk=delivery_id).first()
    if delivery is None or delivery.status != TelegramNotificationDelivery.Status.SENDING:
        return

    now = timezone.now()
    if notification is None:
        delivery.status = TelegramNotificationDelivery.Status.SKIPPED
        delivery.last_error = _("Recipient is no longer authorized.")
    elif notification.withdrawn_at is not None:
        delivery.status = TelegramNotificationDelivery.Status.SKIPPED
        delivery.last_error = _("The notification was withdrawn before delivery.")
    elif notification.cancelled_at is not None:
        delivery.status = TelegramNotificationDelivery.Status.SKIPPED
        delivery.last_error = _("The notification was cancelled before delivery.")
    elif delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        delivery.status = TelegramNotificationDelivery.Status.FAILED
        delivery.last_error = message[:500]
    else:
        delay = RETRY_DELAYS[min(delivery.attempt_count - 1, len(RETRY_DELAYS) - 1)]
        delivery.status = TelegramNotificationDelivery.Status.QUEUED
        delivery.scheduled_for = now + timedelta(seconds=delay)
        delivery.last_error = message[:500]
    delivery.updated_at = now
    delivery.save(update_fields=["status", "scheduled_for", "last_error", "updated_at"])


def _send_delivery(
    bot,
    delivery: TelegramNotificationDelivery,
    exceptional_windows: dict[tuple[int, int], tuple[object, object]],
):
    if delivery.lesson_id:
        try:
            links = audio_links(json.loads(delivery.lesson.links))
        except (TypeError, ValueError, json.JSONDecodeError):
            links = []
        text = "\n\n".join((_("Lesson now available"), format_lesson(delivery.lesson, bool(links), delivery.user)))
        markup = lesson_detail_keyboard(delivery.lesson.course_offering_id, delivery.lesson.pk, links)
    elif delivery.notification_type == TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT:
        announcement = delivery.announcement
        if announcement is None:
            raise ValueError("Announcement delivery has no source.")
        markup = None
        if announcement.action_label and announcement.action_url.startswith(("http://", "https://")):
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton(announcement.action_label, url=announcement.action_url))
        text = f"{announcement.title}\n\n{announcement.body}"
    else:
        opening, closing = exceptional_windows.get(
            (delivery.quiz_id, delivery.user_id), (delivery.quiz.opening_date, delivery.quiz.closing_date),
        )
        text = "\n\n".join((_("Exam now available"), format_quiz(delivery.quiz, delivery.user, opening, closing)))
        markup = quiz_detail_keyboard(delivery.quiz.course_offering_id, delivery.quiz.pk)
    if delivery.content_snapshot:
        text = delivery.content_snapshot
    else:
        stored = TelegramNotificationDelivery.objects.filter(
            pk=delivery.pk, status=TelegramNotificationDelivery.Status.SENDING, content_snapshot="",
        ).update(content_snapshot=text)
        if not stored:
            text = TelegramNotificationDelivery.objects.filter(
                pk=delivery.pk, status=TelegramNotificationDelivery.Status.SENDING,
            ).values_list("content_snapshot", flat=True).first()
            if not text:
                raise ValueError("Notification delivery is no longer claimed.")
        delivery.content_snapshot = text
    return bot.send_message(delivery.telegram_chat_id, text, reply_markup=markup)


@transaction.atomic
def _materialize_due_deliveries(limit: int) -> int:
    """Create Telegram delivery rows at due time for eligible students.

    Recipients are resolved at due time (mirroring the mobile dispatcher) so a
    student who links a Telegram account after event creation still receives
    the message. Idempotent: an existing delivery for the event key is kept.

    The due scan excludes already-materialized events in SQL via an anti-join.
    Without this, the bounded ``[:scan_limit]`` window would keep returning
    the oldest already-delivered rows and starve newer notifications forever
    once more than ``NOTIFICATION_BATCH_SIZE`` deliveries accumulate.
    """
    now = timezone.now()
    scan_limit = max(1, min(int(limit), NOTIFICATION_BATCH_SIZE))
    active_telegram_account = TelegramAccount.objects.filter(
        user_id=OuterRef("student_id"),
        is_active=True,
    )
    already_materialized = TelegramNotificationDelivery.objects.filter(
        idempotency_key=OuterRef("idempotency_key"),
    )
    notifications = list(
        StudentNotification.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            is_manual_redelivery=False,
            scheduled_for__lte=now,
            cancelled_at__isnull=True,
            withdrawn_at__isnull=True,
            notification_type__in=(
                TelegramNotificationDelivery.NotificationType.LESSON_PUBLISHED,
                TelegramNotificationDelivery.NotificationType.QUIZ_OPENING,
                TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT,
            ),
        )
        .filter(Exists(active_telegram_account))
        .filter(~Exists(already_materialized))
        .prefetch_related("telegram_deliveries")
        .order_by("scheduled_for", "pk")[:scan_limit]
    )
    if not notifications:
        return 0

    existing_keys: set[str] = set()
    for notification in notifications:
        for delivery in notification.telegram_deliveries.all():
            existing_keys.add(delivery.idempotency_key)

    account_ids = {
        user_id: (account_id, chat_id)
        for user_id, account_id, chat_id in TelegramAccount.objects.filter(
            user_id__in=[notification.student_id for notification in notifications],
            is_active=True,
        ).values_list("user_id", "pk", "telegram_chat_id")
    }

    rows: list[TelegramNotificationDelivery] = []
    for notification in notifications:
        if notification.idempotency_key in existing_keys:
            continue
        account = account_ids.get(notification.student_id)
        if account is None:
            continue
        account_id, chat_id = account
        rows.append(
            TelegramNotificationDelivery(
                idempotency_key=notification.idempotency_key,
                notification_type=notification.notification_type,
                user_id=notification.student_id,
                telegram_account_id=account_id,
                telegram_chat_id=chat_id,
                student_notification_id=notification.pk,
                lesson_id=notification.lesson_id,
                quiz_id=notification.quiz_id,
                announcement_id=notification.announcement_id,
                scheduled_for=now,
            )
        )
    for start in range(0, len(rows), NOTIFICATION_BATCH_SIZE):
        TelegramNotificationDelivery.objects.bulk_create(
            rows[start:start + NOTIFICATION_BATCH_SIZE],
            ignore_conflicts=True,
        )
    if rows:
        logger.info(
            "telegram deliveries materialized",
            extra={"event": "telegram_deliveries_materialized", "count": len(rows)},
        )
    return len(rows)


def process_due_notifications(bot=None, limit: int = NOTIFICATION_BATCH_SIZE) -> dict[str, int]:
    """Materialize due rows, then claim and deliver them, rechecking
    authorization immediately before send."""
    _materialize_due_deliveries(limit)
    ids = _claim_due_ids(limit)
    if not ids:
        return {"sent": 0, "skipped": 0, "failed": 0}
    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if config is None:
        for delivery_id in ids:
            _mark_failed_or_retry(
                delivery_id,
                _("The Telegram bot is inactive."),
            )
        logger.warning(
            "telegram notifications unconfigured",
            extra={
                "event": "telegram_notifications_unconfigured",
                "failed_count": len(ids),
                "reason": "inactive_bot",
            },
        )
        return {"sent": 0, "skipped": 0, "failed": len(ids)}
    try:
        bot_token = stored_token(config)
        bot = bot or telebot.TeleBot(bot_token, parse_mode=None, threaded=False)
    except Exception:
        for delivery_id in ids:
            _mark_failed_or_retry(
                delivery_id,
                _("Telegram message delivery failed."),
            )
        logger.warning(
            "telegram notifications unconfigured",
            extra={
                "event": "telegram_notifications_unconfigured",
                "failed_count": len(ids),
                "reason": "token_unavailable",
            },
        )
        return {"sent": 0, "skipped": 0, "failed": len(ids)}

    deliveries = TelegramNotificationDelivery.objects.select_related(
        "user", "user__role", "telegram_account",
        "student_notification", "announcement",
        "lesson__course_offering__academic_year_level__academic_year",
        "lesson__course_offering__course",
        "quiz__course_offering__academic_year_level__academic_year",
        "quiz__course_offering__course", "quiz__quiz_type",
    ).filter(pk__in=ids)
    delivery_list = list(deliveries)
    exceptional_windows = {
        (opening.quiz_id, opening.student_id): (opening.opening_date, opening.closing_date)
        for opening in QuizStudentOpening.objects.filter(
            quiz_id__in=[delivery.quiz_id for delivery in delivery_list if delivery.quiz_id],
            student_id__in=[delivery.user_id for delivery in delivery_list],
        )
    }
    authorized_ids = _authorized_delivery_ids(deliveries)
    manual_ids = {d.student_notification_id for d in delivery_list
                  if d.student_notification is not None and d.student_notification.is_manual_redelivery}
    if manual_ids:
        allowed_manual = eligible_redelivery_notification_ids(list(redelivery_source_queryset().filter(pk__in=manual_ids)))
        authorized_ids.difference_update(d.pk for d in delivery_list
                                        if d.student_notification_id in manual_ids and d.student_notification_id not in allowed_manual)
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    outbound_account_ids = set()
    sent_for_withdrawal = []
    for delivery in delivery_list:
        if not _authorized_delivery(delivery, authorized_ids, exceptional_windows):
            _mark_skipped(delivery.pk, _("Recipient is no longer authorized."))
            counts["skipped"] += 1
            continue
        try:
            response = _send_delivery(bot, delivery, exceptional_windows)
            message_id = getattr(response, "message_id", None)
            sent_at = timezone.now()
            TelegramNotificationDelivery.objects.filter(
                pk=delivery.pk,
                status=TelegramNotificationDelivery.Status.SENDING,
            ).update(
                status=TelegramNotificationDelivery.Status.SENT,
                telegram_message_id=message_id,
                sent_at=sent_at,
                last_error="",
                updated_at=timezone.now(),
            )
            sent_for_withdrawal.append((
                delivery.pk,
                delivery.student_notification_id,
                delivery.telegram_chat_id,
                message_id,
                sent_at,
            ))
            outbound_account_ids.add(delivery.telegram_account_id)
            counts["sent"] += 1
        except Exception:
            _mark_failed_or_retry(delivery.pk, _("Telegram message delivery failed."))
            counts["failed"] += 1
    if sent_for_withdrawal:
        notification_ids = {row[1] for row in sent_for_withdrawal if row[1] is not None}
        withdrawn_ids = set(StudentNotification.objects.filter(
            pk__in=notification_ids,
            withdrawn_at__isnull=False,
        ).values_list("pk", flat=True))
        for delivery_id, notification_id, chat_id, message_id, sent_at in sent_for_withdrawal:
            if notification_id not in withdrawn_ids:
                continue
            deletion_status, deletion_error = delete_bot_message(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                sent_at=sent_at,
            )
            TelegramNotificationDelivery.objects.filter(pk=delivery_id).update(
                telegram_deletion_status=deletion_status,
                telegram_deleted_at=timezone.now() if deletion_status == "deleted" else None,
                telegram_deletion_error=deletion_error,
                updated_at=timezone.now(),
            )
    if outbound_account_ids:
        TelegramAccount.objects.filter(pk__in=outbound_account_ids).update(
            last_outbound_at=timezone.now(),
        )
    if counts["sent"] or counts["skipped"] or counts["failed"]:
        logger.info(
            "telegram deliveries processed",
            extra={
                "event": "telegram_deliveries_processed",
                "sent": counts["sent"],
                "skipped": counts["skipped"],
                "failed": counts["failed"],
            },
        )
    return counts
