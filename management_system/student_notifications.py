"""Canonical student academic notification events and channel fan-out."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .academic_access import TARGETED_ENROLLMENT_TYPES
from .models import (
    CourseOffering,
    Enrollment,
    Lesson,
    PublicationStatus,
    Quiz,
    QuizStudentOpening,
    PushDelivery,
    StudentNotification,
    TelegramNotificationDelivery,
    User,
)
from .telegram.navigation import format_lesson, website_url
from .telegram.recipients import eligible_student_queryset
from .utils.timezones import ensure_aware, format_user_datetime


NOTIFICATION_BATCH_SIZE = 500
TELEGRAM_NOTIFICATION_TASK = "management_system.telegram_tasks.process_due_telegram_notifications"


def is_active_published_lesson(lesson: Lesson) -> Lesson | None:
    """Return a lesson eligible for a current publication event, or ``None``."""
    offering = lesson.course_offering
    if lesson.status != PublicationStatus.PUBLISHED or not is_active_published_offering(offering):
        return None
    return lesson


def is_active_published_offering(offering: CourseOffering) -> bool:
    """Return whether an offering is currently eligible for publication."""
    return bool(
        offering.status == PublicationStatus.PUBLISHED
        and offering.academic_year_level.academic_year.is_active
    )


def is_active_published_quiz(quiz: Quiz) -> Quiz | None:
    """Return a quiz eligible for a current opening event, or ``None``."""
    offering = quiz.course_offering
    if (
        quiz.status != PublicationStatus.PUBLISHED
        or offering.status != PublicationStatus.PUBLISHED
        or not offering.academic_year_level.academic_year.is_active
    ):
        return None
    return quiz


def _eligible_students_for_offering(offering) -> QuerySet[User]:
    """Return all active students with current access to one offering.

    This is the mobile-inclusive form of the existing Telegram recipient rule:
    normal enrollment grants the exact year-level scope, while targeted
    enrollment grants only the exact offering. Telegram-linked recipients are
    resolved separately through ``eligible_student_queryset`` below.
    """
    normal_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=offering.academic_year_level_id,
    )
    targeted_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
        course_offering_id=offering.pk,
    )
    return (
        User.objects.filter(
            role__role="student",
            is_active=True,
            application_status="active",
        )
        .filter(Exists(normal_access) | Exists(targeted_access))
        .order_by("pk")
    )


def _telegram_account_ids_for_offering(offering) -> dict[int, int]:
    """Return linked Telegram accounts for the same authorized offering scope."""
    return dict(
        eligible_student_queryset(
            academic_year_id=offering.academic_year_level.academic_year_id,
            academic_year_level_id=offering.academic_year_level_id,
            course_offering_id=offering.pk,
        ).values_list("pk", "telegram_account__pk")
    )


def _datetime_key(value: datetime) -> str:
    return ensure_aware(value).isoformat()


def _push_expiry(event_type: str, scheduled_for: datetime, closing: datetime | None) -> datetime:
    if event_type == StudentNotification.NotificationType.QUIZ_OPENING and closing is not None:
        return ensure_aware(closing)
    return scheduled_for + timedelta(seconds=settings.MOBILE_PUSH_LESSON_TTL_SECONDS)


def _quiz_snapshot_body(quiz: Quiz, user: User, opening: datetime, closing: datetime, language: str) -> str:
    quiz_type_name = ""
    if quiz.quiz_type:
        quiz_type_name = quiz.quiz_type.name_ar if language == "ar" else quiz.quiz_type.name_en
    lines = [
        _("Course: %(name)s") % {"name": quiz.course_offering.course.name},
        _("Exam: %(name)s") % {"name": quiz.name},
        _("Type: %(type)s") % {"type": quiz_type_name or _("Unassigned")},
        _("Total grade: %(grade)s") % {"grade": quiz.total_grade},
        _("Opens: %(date)s") % {"date": format_user_datetime(opening, user, "%d/%m/%Y %H:%M")},
        _("Closes: %(date)s") % {"date": format_user_datetime(closing, user, "%d/%m/%Y %H:%M")},
    ]
    link = website_url("quiz-details", [quiz.course_offering_id, quiz.pk])
    if link:
        lines.append(_("Open exam on the website: %(link)s") % {"link": link})
    return "\n".join(lines)


def _lesson_snapshot_body(lesson: Lesson, language: str) -> str:
    """English/lesson body snapshot for a publication event.

    Lesson and course names are stored unlocalized, so the body uses the
    current UI language labels and includes the HTTPS access link, keeping the
    same semantic message as the Arabic snapshot.
    """
    lines = [
        _("Course: %(name)s") % {"name": lesson.course_offering.course.name},
        _("Lesson: %(name)s") % {"name": lesson.name},
    ]
    link = website_url("lesson-details", [lesson.course_offering_id, lesson.pk])
    if link:
        lines.append(_("Open the lesson on the website: %(link)s") % {"link": link})
    return "\n".join(lines)


def _snapshot(event_type: str, source, user: User, opening: datetime | None, closing: datetime | None) -> tuple[str, str, str, str]:
    if event_type == StudentNotification.NotificationType.LESSON_PUBLISHED:
        with translation.override("ar"):
            title_ar = _("Lesson published")
            body_ar = format_lesson(source, False, user)
        with translation.override("en"):
            title_en = _("Lesson published")
            body_en = _lesson_snapshot_body(source, "en")
        return title_ar, body_ar, title_en, body_en

    with translation.override("ar"):
        title_ar = _("Exam opening")
        body_ar = _quiz_snapshot_body(source, user, opening, closing, "ar")
    with translation.override("en"):
        title_en = _("Exam opening")
        body_en = _quiz_snapshot_body(source, user, opening, closing, "en")
    return title_ar, body_ar, title_en, body_en


def _persist_event_batch(
    *,
    source,
    event_type: str,
    specs: list[tuple[User, str, datetime | None, datetime | None]],
    telegram_account_ids: dict[int, int],
) -> tuple[int, bool]:
    if not specs:
        return 0, False

    is_lesson = event_type == StudentNotification.NotificationType.LESSON_PUBLISHED
    navigation_type = (
        StudentNotification.NavigationType.LESSON
        if is_lesson
        else StudentNotification.NavigationType.QUIZ
    )
    event_keys = [spec[1] for spec in specs]
    existing_keys = set(
        StudentNotification.objects.filter(idempotency_key__in=event_keys)
        .values_list("idempotency_key", flat=True)
    )
    rows = []
    for user, event_key, opening, closing in specs:
        title_ar, body_ar, title_en, body_en = _snapshot(event_type, source, user, opening, closing)
        scheduled_for = timezone.now() if opening is None else ensure_aware(opening)
        rows.append(
            StudentNotification(
                student_id=user.pk,
                notification_type=event_type,
                lesson_id=source.pk if is_lesson else None,
                quiz_id=None if is_lesson else source.pk,
                effective_opening_at=None if is_lesson else ensure_aware(opening),
                effective_closing_at=None if is_lesson else ensure_aware(closing),
                idempotency_key=event_key,
                title_ar=title_ar,
                body_ar=body_ar,
                title_en=title_en,
                body_en=body_en,
                navigation_type=navigation_type,
                offering_id=source.course_offering_id,
                entity_id=source.pk,
                scheduled_for=scheduled_for,
                push_expires_at=_push_expiry(event_type, scheduled_for, closing),
            )
        )
    StudentNotification.objects.bulk_create(rows, ignore_conflicts=True)
    persisted = {
        row.idempotency_key: row
        for row in StudentNotification.objects.filter(idempotency_key__in=event_keys)
    }

    telegram_rows = []
    for user, event_key, opening, _closing in specs:
        account_id = telegram_account_ids.get(user.pk)
        notification = persisted.get(event_key)
        if account_id is None or notification is None:
            continue
        telegram_rows.append(
            TelegramNotificationDelivery(
                idempotency_key=event_key,
                notification_type=event_type,
                user_id=user.pk,
                telegram_account_id=account_id,
                student_notification_id=notification.pk,
                lesson_id=source.pk if is_lesson else None,
                quiz_id=None if is_lesson else source.pk,
                scheduled_for=timezone.now() if opening is None else ensure_aware(opening),
            )
        )
    if telegram_rows:
        TelegramNotificationDelivery.objects.bulk_create(telegram_rows, ignore_conflicts=True)
        existing_deliveries = list(
            TelegramNotificationDelivery.objects.filter(
                idempotency_key__in=[row.idempotency_key for row in telegram_rows]
            )
        )
        for delivery in existing_deliveries:
            notification = persisted.get(delivery.idempotency_key)
            if notification is not None and delivery.student_notification_id != notification.pk:
                delivery.student_notification_id = notification.pk
        TelegramNotificationDelivery.objects.bulk_update(existing_deliveries, ["student_notification"])

    return len(set(event_keys) - existing_keys), bool(telegram_rows)


def _persist_event_specs(
    *,
    source,
    event_type: str,
    specs: Iterable[tuple[User, str, datetime | None, datetime | None]],
    telegram_account_ids: dict[int, int],
) -> int:
    created = 0
    has_telegram_rows = False
    batch = []
    for spec in specs:
        batch.append(spec)
        if len(batch) >= NOTIFICATION_BATCH_SIZE:
            count, batch_has_telegram = _persist_event_batch(
                source=source,
                event_type=event_type,
                specs=batch,
                telegram_account_ids=telegram_account_ids,
            )
            created += count
            has_telegram_rows = has_telegram_rows or batch_has_telegram
            batch = []
    if batch:
        count, batch_has_telegram = _persist_event_batch(
            source=source,
            event_type=event_type,
            specs=batch,
            telegram_account_ids=telegram_account_ids,
        )
        created += count
        has_telegram_rows = has_telegram_rows or batch_has_telegram
    if has_telegram_rows:
        transaction.on_commit(
            lambda: current_app.send_task(TELEGRAM_NOTIFICATION_TASK)
        )
    return created


def create_lesson_publication_event(lesson: Lesson) -> int:
    """Create one idempotent lesson event per currently eligible student."""
    lesson = Lesson.objects.select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
    ).get(pk=lesson.pk)
    if is_active_published_lesson(lesson) is None:
        return 0

    offering = lesson.course_offering
    telegram_account_ids = _telegram_account_ids_for_offering(offering)
    specs = (
        (
            user,
            f"lesson:{lesson.pk}:user:{user.pk}:publication:{lesson.publication_event_version or lesson.updated_date.isoformat()}",
            None,
            None,
        )
        for user in _eligible_students_for_offering(offering).iterator(
            chunk_size=NOTIFICATION_BATCH_SIZE
        )
    )
    with transaction.atomic():
        return _persist_event_specs(
            source=lesson,
            event_type=StudentNotification.NotificationType.LESSON_PUBLISHED,
            specs=specs,
            telegram_account_ids=telegram_account_ids,
        )


def cancel_future_quiz_opening_events(quiz: Quiz) -> int:
    """Cancel future inbox and channel deliveries when a quiz is unpublished."""
    cancellation_time = timezone.now()
    stale_notifications = StudentNotification.objects.filter(
        quiz_id=quiz.pk,
        scheduled_for__gt=cancellation_time,
        cancelled_at__isnull=True,
    )
    stale_ids = Subquery(stale_notifications.values("pk"))
    TelegramNotificationDelivery.objects.filter(
        student_notification_id__in=stale_ids,
        status=TelegramNotificationDelivery.Status.QUEUED,
    ).update(
        status=TelegramNotificationDelivery.Status.SKIPPED,
        last_error=_("Superseded by a newer exam opening window."),
        updated_at=cancellation_time,
    )
    PushDelivery.objects.filter(
        notification_id__in=stale_ids,
        status=PushDelivery.Status.QUEUED,
    ).update(
        status=PushDelivery.Status.SKIPPED,
        error_code="notification_cancelled",
        error_message="The academic notification was cancelled.",
        next_attempt_at=cancellation_time,
        updated_at=cancellation_time,
    )
    return stale_notifications.update(
        cancelled_at=cancellation_time,
        updated_at=cancellation_time,
    )


def schedule_quiz_opening_events(quiz: Quiz, *, locked: bool = False) -> int:
    """Create one idempotent opening event per student/window."""
    if not locked:
        quiz = Quiz.objects.select_related(
            "course_offering__course",
            "course_offering__academic_year_level__level",
            "course_offering__academic_year_level__academic_year",
            "quiz_type",
        ).get(pk=quiz.pk)
    if is_active_published_quiz(quiz) is None:
        return 0

    offering = quiz.course_offering
    telegram_account_ids = _telegram_account_ids_for_offering(offering)

    def batch_specs(users):
        exceptional_windows = {
            opening.student_id: (opening.opening_date, opening.closing_date)
            for opening in QuizStudentOpening.objects.filter(
                quiz_id=quiz.pk,
                student_id__in=[user.pk for user in users],
            )
        }
        for user in users:
            opening, closing = exceptional_windows.get(
                user.pk,
                (quiz.opening_date, quiz.closing_date),
            )
            opening = ensure_aware(opening)
            closing = ensure_aware(closing)
            if closing <= opening:
                continue
            window_key = f"{_datetime_key(opening)}:{_datetime_key(closing)}"
            yield (
                user,
                f"quiz:{quiz.pk}:user:{user.pk}:window:{window_key}",
                opening,
                closing,
            )

    def specs():
        users = []
        for user in _eligible_students_for_offering(offering).iterator(
            chunk_size=NOTIFICATION_BATCH_SIZE
        ):
            users.append(user)
            if len(users) >= NOTIFICATION_BATCH_SIZE:
                yield from batch_specs(users)
                users = []
        if users:
            yield from batch_specs(users)

    with transaction.atomic():
        cancellation_time = timezone.now()
        exceptional_openings = QuizStudentOpening.objects.filter(quiz_id=quiz.pk)
        has_exception = Exists(
            exceptional_openings.filter(student_id=OuterRef("student_id"))
        )
        current_exception = Exists(
            exceptional_openings.filter(
                student_id=OuterRef("student_id"),
                opening_date=OuterRef("effective_opening_at"),
                closing_date=OuterRef("effective_closing_at"),
            )
        )
        stale_notifications = StudentNotification.objects.filter(
            quiz_id=quiz.pk,
            scheduled_for__gt=cancellation_time,
            cancelled_at__isnull=True,
        ).filter(~current_exception).filter(
            has_exception
            | ~Q(
                effective_opening_at=ensure_aware(quiz.opening_date),
                effective_closing_at=ensure_aware(quiz.closing_date),
            )
        )
        stale_ids = Subquery(stale_notifications.values("pk"))
        TelegramNotificationDelivery.objects.filter(
            student_notification_id__in=stale_ids,
            status=TelegramNotificationDelivery.Status.QUEUED,
        ).update(
            status=TelegramNotificationDelivery.Status.SKIPPED,
            last_error=_("Superseded by a newer exam opening window."),
            updated_at=cancellation_time,
        )
        PushDelivery.objects.filter(
            notification_id__in=stale_ids,
            status=PushDelivery.Status.QUEUED,
        ).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="notification_superseded",
            error_message="The academic notification was superseded.",
            next_attempt_at=cancellation_time,
            updated_at=cancellation_time,
        )
        stale_notifications.update(cancelled_at=cancellation_time, updated_at=cancellation_time)
        return _persist_event_specs(
            source=quiz,
            event_type=StudentNotification.NotificationType.QUIZ_OPENING,
            specs=specs(),
            telegram_account_ids=telegram_account_ids,
        )
