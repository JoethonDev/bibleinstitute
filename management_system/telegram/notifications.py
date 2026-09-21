"""Durable, authorized Telegram academic and announcement delivery."""

from __future__ import annotations

import json
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
from ..student_notifications import is_active_published_lesson, is_active_published_quiz
from ..utils.timezones import ensure_aware
from .media import audio_links
from .navigation import format_lesson, format_quiz, lesson_detail_keyboard, quiz_detail_keyboard
from .configuration import stored_token


NOTIFICATION_BATCH_SIZE = 500
MAX_DELIVERY_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)


def _claim_due_ids(limit: int) -> list[int]:
    now = timezone.now()
    stale_before = now - timedelta(minutes=15)
    stale_deliveries = TelegramNotificationDelivery.objects.filter(
        status=TelegramNotificationDelivery.Status.SENDING,
        updated_at__lt=stale_before,
    )
    stale_deliveries.filter(
        attempt_count__gte=MAX_DELIVERY_ATTEMPTS,
    ).update(
        status=TelegramNotificationDelivery.Status.FAILED,
        last_error=_("Telegram message delivery failed."),
        updated_at=now,
    )
    stale_deliveries.filter(
        attempt_count__lt=MAX_DELIVERY_ATTEMPTS,
    ).update(
        status=TelegramNotificationDelivery.Status.QUEUED,
        scheduled_for=now,
        last_error=_("A previous delivery worker stopped before completion."),
        updated_at=now,
    )
    with transaction.atomic():
        ids = list(
            TelegramNotificationDelivery.objects.select_for_update(
                of=("self",),
                skip_locked=True,
            )
            .filter(
                status=TelegramNotificationDelivery.Status.QUEUED,
                scheduled_for__lte=now,
                student_notification__cancelled_at__isnull=True,
            )
            .order_by("scheduled_for", "pk")
            .values_list("pk", flat=True)[:max(1, min(limit, NOTIFICATION_BATCH_SIZE))]
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
        or delivery.pk not in authorized_ids
    ):
        return False
    if delivery.lesson_id:
        lesson = is_active_published_lesson(delivery.lesson)
        return bool(
            lesson
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
    return ensure_aware(opening) <= current <= ensure_aware(closing) + timedelta(minutes=30)


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


def _mark_failed_or_retry(delivery_id: int, attempt_count: int | None, message: str) -> None:
    if attempt_count is None:
        attempt_count = int(TelegramNotificationDelivery.objects.filter(pk=delivery_id).values_list(
            "attempt_count", flat=True
        ).first() or 1)
    attempt = int(attempt_count)
    if attempt >= MAX_DELIVERY_ATTEMPTS:
        TelegramNotificationDelivery.objects.filter(
            pk=delivery_id,
            status=TelegramNotificationDelivery.Status.SENDING,
        ).update(
            status=TelegramNotificationDelivery.Status.FAILED,
            last_error=message[:500],
            updated_at=timezone.now(),
        )
        return
    delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
    TelegramNotificationDelivery.objects.filter(
        pk=delivery_id,
        status=TelegramNotificationDelivery.Status.SENDING,
    ).update(
        status=TelegramNotificationDelivery.Status.QUEUED,
        scheduled_for=timezone.now() + timedelta(seconds=delay),
        last_error=message[:500],
        updated_at=timezone.now(),
    )


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
        return bot.send_message(
            delivery.telegram_account.telegram_chat_id,
            format_lesson(delivery.lesson, bool(links), delivery.user),
            reply_markup=lesson_detail_keyboard(
                delivery.lesson.course_offering_id,
                delivery.lesson.pk,
                links,
            ),
        )
    if delivery.notification_type == TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT:
        announcement = delivery.announcement
        if announcement is None:
            raise ValueError("Announcement delivery has no source.")
        reply_markup = None
        if announcement.action_label and announcement.action_url.startswith(("http://", "https://")):
            reply_markup = telebot.types.InlineKeyboardMarkup()
            reply_markup.add(
                telebot.types.InlineKeyboardButton(
                    announcement.action_label,
                    url=announcement.action_url,
                )
            )
        return bot.send_message(
            delivery.telegram_account.telegram_chat_id,
            f"{announcement.title}\n\n{announcement.body}",
            reply_markup=reply_markup,
        )
    opening, closing = exceptional_windows.get(
        (delivery.quiz_id, delivery.user_id),
        (delivery.quiz.opening_date, delivery.quiz.closing_date),
    )
    return bot.send_message(
        delivery.telegram_account.telegram_chat_id,
        format_quiz(delivery.quiz, delivery.user, opening, closing),
        reply_markup=quiz_detail_keyboard(delivery.quiz.course_offering_id, delivery.quiz.pk),
    )


def _materialize_due_deliveries(limit: int) -> int:
    """Create Telegram delivery rows at due time for eligible students.

    Recipients are resolved at due time (mirroring the mobile dispatcher) so a
    student who links a Telegram account after event creation still receives
    the message. Idempotent: an existing delivery for the event key is kept.
    """
    now = timezone.now()
    scan_limit = max(1, min(int(limit), NOTIFICATION_BATCH_SIZE))
    active_telegram_account = TelegramAccount.objects.filter(
        user_id=OuterRef("student_id"),
        is_active=True,
    )
    notifications = list(
        StudentNotification.objects.filter(
            scheduled_for__lte=now,
            cancelled_at__isnull=True,
            notification_type__in=(
                TelegramNotificationDelivery.NotificationType.LESSON_PUBLISHED,
                TelegramNotificationDelivery.NotificationType.QUIZ_OPENING,
                TelegramNotificationDelivery.NotificationType.ANNOUNCEMENT,
            ),
        )
        .filter(Exists(active_telegram_account))
        .prefetch_related("telegram_deliveries")
        .order_by("scheduled_for", "pk")[:scan_limit]
    )
    if not notifications:
        return 0

    existing_keys: set[str] = set()
    for notification in notifications:
        for delivery in notification.telegram_deliveries.all():
            existing_keys.add(delivery.idempotency_key)

    account_ids = dict(
        TelegramAccount.objects.filter(
            user_id__in=[notification.student_id for notification in notifications],
            is_active=True,
        ).values_list("user_id", "pk")
    )

    rows: list[TelegramNotificationDelivery] = []
    for notification in notifications:
        if notification.idempotency_key in existing_keys:
            continue
        account_id = account_ids.get(notification.student_id)
        if account_id is None:
            continue
        rows.append(
            TelegramNotificationDelivery(
                idempotency_key=notification.idempotency_key,
                notification_type=notification.notification_type,
                user_id=notification.student_id,
                telegram_account_id=account_id,
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
    return len(rows)


def process_due_notifications(bot=None, limit: int = NOTIFICATION_BATCH_SIZE) -> dict[str, int]:
    """Materialize due rows, then claim and deliver them, rechecking
    authorization immediately before send."""
    _materialize_due_deliveries(limit)
    ids = _claim_due_ids(limit)
    if not ids:
        return {"sent": 0, "skipped": 0, "failed": 0}
    attempt_counts = dict(
        TelegramNotificationDelivery.objects.filter(pk__in=ids).values_list(
            "pk", "attempt_count"
        )
    )

    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if config is None:
        for delivery_id in ids:
            _mark_failed_or_retry(
                delivery_id,
                attempt_counts.get(delivery_id),
                _("The Telegram bot is inactive."),
            )
        return {"sent": 0, "skipped": 0, "failed": len(ids)}
    try:
        bot_token = stored_token(config)
        bot = bot or telebot.TeleBot(bot_token, parse_mode=None, threaded=False)
    except Exception:
        for delivery_id in ids:
            _mark_failed_or_retry(
                delivery_id,
                attempt_counts.get(delivery_id),
                _("Telegram message delivery failed."),
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
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    outbound_account_ids = set()
    for delivery in delivery_list:
        if not _authorized_delivery(delivery, authorized_ids, exceptional_windows):
            _mark_skipped(delivery.pk, _("Recipient is no longer authorized."))
            counts["skipped"] += 1
            continue
        try:
            response = _send_delivery(bot, delivery, exceptional_windows)
            message_id = getattr(response, "message_id", None)
            TelegramNotificationDelivery.objects.filter(
                pk=delivery.pk,
                status=TelegramNotificationDelivery.Status.SENDING,
            ).update(
                status=TelegramNotificationDelivery.Status.SENT,
                telegram_message_id=message_id,
                sent_at=timezone.now(),
                last_error="",
                updated_at=timezone.now(),
            )
            outbound_account_ids.add(delivery.telegram_account_id)
            counts["sent"] += 1
        except Exception:
            _mark_failed_or_retry(delivery.pk, delivery.attempt_count, _("Telegram message delivery failed."))
            counts["failed"] += 1
    if outbound_account_ids:
        TelegramAccount.objects.filter(pk__in=outbound_account_ids).update(
            last_outbound_at=timezone.now(),
        )
    return counts
