"""Durable, authorized Telegram lesson and exam notification delivery."""

from __future__ import annotations

import json
from datetime import timedelta

import telebot
from celery import current_app
from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from ..academic_access import active_year_published_offerings_for_user
from ..models import (
    Lesson,
    PublicationStatus,
    Quiz,
    QuizStudentOpening,
    TelegramAccount,
    TelegramBotConfig,
    TelegramNotificationDelivery,
    User,
)
from ..utils.helpers import is_quiz_open
from ..utils.quiz_access import quiz_window
from ..utils.timezones import ensure_aware
from .linking import is_eligible_user
from .media import audio_links
from .navigation import format_lesson, format_quiz, lesson_detail_keyboard, quiz_detail_keyboard
from .configuration import stored_token
from .recipients import eligible_student_queryset


NOTIFICATION_BATCH_SIZE = 500
MAX_DELIVERY_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)


def _eligible_recipient_queryset(offering) -> QuerySet[User]:
    return eligible_student_queryset(
        academic_year_id=offering.academic_year_level.academic_year_id,
        academic_year_level_id=offering.academic_year_level_id,
        course_offering_id=offering.pk,
    )


def _active_published_lesson(lesson: Lesson) -> Lesson | None:
    if lesson.status != PublicationStatus.PUBLISHED:
        return None
    offering = lesson.course_offering
    if offering.status != PublicationStatus.PUBLISHED:
        return None
    if not offering.academic_year_level.academic_year.is_active:
        return None
    return lesson


def _active_published_quiz(quiz: Quiz) -> Quiz | None:
    if quiz.status != PublicationStatus.PUBLISHED:
        return None
    offering = quiz.course_offering
    if offering.status != PublicationStatus.PUBLISHED:
        return None
    if not offering.academic_year_level.academic_year.is_active:
        return None
    return quiz


def _wake_delivery_task() -> None:
    """Queue one recovery task after the surrounding transaction commits."""
    transaction.on_commit(
        lambda: current_app.send_task(
            "management_system.telegram_tasks.process_due_telegram_notifications"
        )
    )


def _bulk_create(rows: list[TelegramNotificationDelivery]) -> int:
    created = 0
    for start in range(0, len(rows), NOTIFICATION_BATCH_SIZE):
        batch = rows[start:start + NOTIFICATION_BATCH_SIZE]
        TelegramNotificationDelivery.objects.bulk_create(batch, ignore_conflicts=True)
        created += len(batch)
    return created


def enqueue_lesson_notifications(lesson: Lesson) -> int:
    """Create idempotent lesson notifications for currently authorized students."""
    lesson = Lesson.objects.select_related(
        "course_offering__academic_year_level__academic_year",
    ).get(pk=lesson.pk)
    if _active_published_lesson(lesson) is None:
        return 0
    recipients = _eligible_recipient_queryset(lesson.course_offering)
    publication_key = lesson.updated_date.isoformat()
    rows = [
        TelegramNotificationDelivery(
            idempotency_key=f"lesson:{lesson.pk}:user:{user.pk}:publication:{publication_key}",
            notification_type=TelegramNotificationDelivery.NotificationType.LESSON_PUBLISHED,
            user_id=user.pk,
            telegram_account_id=user.telegram_account.pk,
            lesson_id=lesson.pk,
            scheduled_for=timezone.now(),
        )
        for user in recipients.iterator(chunk_size=NOTIFICATION_BATCH_SIZE)
    ]
    created = _bulk_create(rows) if rows else 0
    if rows:
        _wake_delivery_task()
    return created


def schedule_quiz_opening_notifications(quiz: Quiz) -> int:
    """Create idempotent opening rows using each student's effective window."""
    quiz = Quiz.objects.select_related(
        "course_offering__academic_year_level__academic_year",
    ).get(pk=quiz.pk)
    if _active_published_quiz(quiz) is None:
        return 0

    TelegramNotificationDelivery.objects.filter(
        quiz_id=quiz.pk,
        status=TelegramNotificationDelivery.Status.QUEUED,
    ).update(
        status=TelegramNotificationDelivery.Status.SKIPPED,
        last_error=_("Superseded by a newer exam opening window."),
        updated_at=timezone.now(),
    )

    exceptional_windows = {
        opening.student_id: (opening.opening_date, opening.closing_date)
        for opening in QuizStudentOpening.objects.filter(quiz_id=quiz.pk)
    }
    rows = []
    for user in _eligible_recipient_queryset(quiz.course_offering).iterator(chunk_size=NOTIFICATION_BATCH_SIZE):
        opening, closing = exceptional_windows.get(user.pk, (quiz.opening_date, quiz.closing_date))
        opening = ensure_aware(opening)
        closing = ensure_aware(closing)
        if closing <= opening:
            continue
        window_key = f"{opening.isoformat()}:{closing.isoformat()}"
        rows.append(
            TelegramNotificationDelivery(
                idempotency_key=f"quiz:{quiz.pk}:user:{user.pk}:window:{window_key}",
                notification_type=TelegramNotificationDelivery.NotificationType.QUIZ_OPENING,
                user_id=user.pk,
                telegram_account_id=user.telegram_account.pk,
                quiz_id=quiz.pk,
                scheduled_for=opening,
            )
        )
    created = _bulk_create(rows) if rows else 0
    if rows:
        _wake_delivery_task()
    return created


def _claim_due_ids(limit: int) -> list[int]:
    now = timezone.now()
    stale_before = now - timedelta(minutes=15)
    TelegramNotificationDelivery.objects.filter(
        status=TelegramNotificationDelivery.Status.SENDING,
        updated_at__lt=stale_before,
    ).update(
        status=TelegramNotificationDelivery.Status.QUEUED,
        scheduled_for=now,
        last_error=_("A previous delivery worker stopped before completion."),
        updated_at=now,
    )
    with transaction.atomic():
        ids = list(
            TelegramNotificationDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                status=TelegramNotificationDelivery.Status.QUEUED,
                scheduled_for__lte=now,
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


def _authorized_delivery(delivery: TelegramNotificationDelivery) -> bool:
    account = delivery.telegram_account
    if (
        account is None
        or not account.is_active
        or account.user_id != delivery.user_id
        or not is_eligible_user(delivery.user)
    ):
        return False
    if delivery.lesson_id:
        lesson = _active_published_lesson(delivery.lesson)
        return bool(
            lesson
            and active_year_published_offerings_for_user(delivery.user)
            .filter(pk=lesson.course_offering_id)
            .exists()
        )
    if delivery.quiz_id:
        quiz = _active_published_quiz(delivery.quiz)
        return bool(
            quiz
            and active_year_published_offerings_for_user(delivery.user)
            .filter(pk=quiz.course_offering_id)
            .exists()
            and is_quiz_open(quiz, timezone.now(), delivery.user)
        )
    return False


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


def _send_delivery(bot, delivery: TelegramNotificationDelivery):
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
                bool(links),
            ),
        )
    opening, closing = quiz_window(delivery.quiz, delivery.user)
    return bot.send_message(
        delivery.telegram_account.telegram_chat_id,
        format_quiz(delivery.quiz, delivery.user, opening, closing),
        reply_markup=quiz_detail_keyboard(delivery.quiz.course_offering_id, delivery.quiz.pk),
    )


def process_due_notifications(bot=None, limit: int = NOTIFICATION_BATCH_SIZE) -> dict[str, int]:
    """Claim and deliver due rows, rechecking authorization immediately before send."""
    ids = _claim_due_ids(limit)
    if not ids:
        return {"sent": 0, "skipped": 0, "failed": 0}

    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if config is None:
        for delivery_id in ids:
            _mark_failed_or_retry(delivery_id, 1, _("The Telegram bot is inactive."))
        return {"sent": 0, "skipped": 0, "failed": len(ids)}
    try:
        bot_token = stored_token(config)
        bot = bot or telebot.TeleBot(bot_token, parse_mode=None, threaded=False)
    except Exception:
        for delivery_id in ids:
            _mark_failed_or_retry(delivery_id, 1, _("Telegram message delivery failed."))
        return {"sent": 0, "skipped": 0, "failed": len(ids)}

    deliveries = TelegramNotificationDelivery.objects.select_related(
        "user", "user__role", "telegram_account",
        "lesson__course_offering__academic_year_level__academic_year",
        "lesson__course_offering__course",
        "quiz__course_offering__academic_year_level__academic_year",
        "quiz__course_offering__course", "quiz__quiz_type",
    ).filter(pk__in=ids)
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    for delivery in deliveries:
        if not _authorized_delivery(delivery):
            _mark_skipped(delivery.pk, _("Recipient is no longer authorized."))
            counts["skipped"] += 1
            continue
        try:
            response = _send_delivery(bot, delivery)
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
            TelegramAccount.objects.filter(pk=delivery.telegram_account_id).update(
                last_outbound_at=timezone.now(),
            )
            counts["sent"] += 1
        except Exception:
            _mark_failed_or_retry(delivery.pk, delivery.attempt_count, _("Telegram message delivery failed."))
            counts["failed"] += 1
    return counts
