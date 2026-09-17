"""Admin-authored announcements fanned out to the canonical student inbox."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import timedelta

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from .academic_access import TARGETED_ENROLLMENT_TYPES
from .models import (
    AcademicYear,
    Announcement,
    Enrollment,
    Level,
    StudentNotification,
    User,
)


ANNOUNCEMENT_BATCH_SIZE = 500
MAX_ANNOUNCEMENT_TITLE = 255
MAX_ANNOUNCEMENT_BODY = 4000
ANNOUNCEMENT_FANOUT_TASK = "management_system.announcement_tasks.fan_out_announcement"
ANNOUNCEMENT_RECOVERY_GRACE = timedelta(minutes=2)
ANNOUNCEMENT_STALE_AFTER = timedelta(minutes=15)
ANNOUNCEMENT_RECOVERY_LIMIT = 200

logger = logging.getLogger(__name__)


class AnnouncementError(Exception):
    """A safe operator-facing announcement error."""


def _require_admin(actor: User) -> None:
    if getattr(getattr(actor, "role", None), "role", None) != "admin":
        raise AnnouncementError(_("Only administrators can send announcements."))


def active_announcement_levels() -> QuerySet[Level]:
    """Return the levels open in the active academic year, in study order."""
    return (
        Level.objects.filter(year_links__academic_year__is_active=True)
        .distinct()
        .order_by("ordering")
    )


def resolve_announcement_levels(level_ids: Iterable[int]) -> list[Level]:
    """Resolve selected levels against the active year or fail clearly."""
    try:
        wanted = sorted({int(value) for value in level_ids})
    except (TypeError, ValueError) as exc:
        raise AnnouncementError(
            _("The selected level is not open in the active academic year.")
        ) from exc
    if not wanted:
        return []
    levels = list(active_announcement_levels().filter(pk__in=wanted))
    if len(levels) != len(wanted):
        raise AnnouncementError(
            _("The selected level is not open in the active academic year.")
        )
    return levels


def announcement_recipient_queryset(
    level_ids: Iterable[int] | None,
    *,
    academic_year_id: int | None = None,
) -> QuerySet[User]:
    """Return the active students targeted by an announcement.

    With no level selected every active student account is targeted. Selected
    levels target active normal or targeted enrollments inside the active
    academic year at those levels.
    """
    students = User.objects.filter(
        role__role="student",
        is_active=True,
        application_status="active",
    )
    wanted = [int(value) for value in (level_ids or [])]
    if not wanted:
        return students.order_by("pk")
    if academic_year_id is None:
        academic_year_id = (
            AcademicYear.objects.filter(is_active=True)
            .values_list("pk", flat=True)
            .first()
        )
        if academic_year_id is None:
            raise AnnouncementError(_("There is no active academic year."))
    normal_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level__academic_year_id=academic_year_id,
        academic_year_level__level_id__in=wanted,
    )
    targeted_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
        course_offering__academic_year_level__academic_year_id=academic_year_id,
        course_offering__academic_year_level__level_id__in=wanted,
    )
    return students.filter(Exists(normal_access) | Exists(targeted_access)).order_by("pk")


def count_announcement_recipients(level_ids: Iterable[int] | None) -> int:
    """Return the number of active students the target resolves to."""
    return announcement_recipient_queryset(level_ids).count()


def _clean_content(value, *, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise AnnouncementError(
            _("Announcement title and body are required in both languages.")
        )
    if len(text) > max_length:
        raise AnnouncementError(
            _("Announcement content exceeds the allowed length.")
        )
    return text


def send_announcement(
    *,
    actor: User,
    title_ar: str,
    body_ar: str,
    title_en: str,
    body_en: str,
    level_ids: Iterable[int] | None = None,
) -> Announcement:
    """Queue one announcement; the inbox fan-out runs in a background task."""
    _require_admin(actor)
    title_ar = _clean_content(title_ar, max_length=MAX_ANNOUNCEMENT_TITLE)
    title_en = _clean_content(title_en, max_length=MAX_ANNOUNCEMENT_TITLE)
    body_ar = _clean_content(body_ar, max_length=MAX_ANNOUNCEMENT_BODY)
    body_en = _clean_content(body_en, max_length=MAX_ANNOUNCEMENT_BODY)
    levels = resolve_announcement_levels(level_ids or [])

    with transaction.atomic():
        academic_year = (
            AcademicYear.objects.select_for_update()
            .filter(is_active=True)
            .order_by("pk")
            .first()
        )
        if academic_year is None:
            raise AnnouncementError(_("There is no active academic year."))
        if not announcement_recipient_queryset(
            [level.pk for level in levels],
            academic_year_id=academic_year.pk,
        ).exists():
            raise AnnouncementError(
                _("There are no active students in the selected target.")
            )
        announcement = Announcement.objects.create(
            academic_year=academic_year,
            created_by=actor,
            title_ar=title_ar,
            body_ar=body_ar,
            title_en=title_en,
            body_en=body_en,
        )
        if levels:
            announcement.levels.set(levels)
        transaction.on_commit(
            lambda: current_app.send_task(
                ANNOUNCEMENT_FANOUT_TASK,
                args=[announcement.pk],
            )
        )
    return announcement


def _claim_announcement(announcement_id: int) -> Announcement | None:
    """Claim one queued (or stale) announcement for exactly one worker."""
    now = timezone.now()
    stale_before = now - ANNOUNCEMENT_STALE_AFTER
    with transaction.atomic():
        announcement = (
            Announcement.objects.select_for_update()
            .filter(pk=announcement_id)
            .first()
        )
        if announcement is None or announcement.status in (
            Announcement.Status.COMPLETED,
            Announcement.Status.FAILED,
        ):
            return None
        if (
            announcement.status == Announcement.Status.SENDING
            and announcement.updated_at > stale_before
        ):
            return None
        Announcement.objects.filter(pk=announcement.pk).update(
            status=Announcement.Status.SENDING,
            updated_at=now,
        )
        announcement.status = Announcement.Status.SENDING
        announcement.updated_at = now
        return announcement


def fan_out_announcement(announcement_id: int) -> int:
    """Create the durable inbox rows for one claimed announcement.

    Rows use per-announcement idempotency keys and ``ignore_conflicts``, so the
    task is safe to retry after a partial failure.
    """
    announcement = _claim_announcement(announcement_id)
    if announcement is None:
        return 0
    try:
        level_ids = list(announcement.levels.values_list("pk", flat=True))
        recipients = announcement_recipient_queryset(
            level_ids,
            academic_year_id=announcement.academic_year_id,
        )
        scheduled_for = timezone.now()
        push_expires_at = scheduled_for + timedelta(
            seconds=settings.MOBILE_PUSH_LESSON_TTL_SECONDS
        )
        batch: list[StudentNotification] = []
        for student in recipients.iterator(chunk_size=ANNOUNCEMENT_BATCH_SIZE):
            batch.append(
                StudentNotification(
                    student_id=student.pk,
                    notification_type=StudentNotification.NotificationType.ANNOUNCEMENT,
                    announcement=announcement,
                    navigation_type=StudentNotification.NavigationType.NONE,
                    offering_id=0,
                    entity_id=0,
                    idempotency_key=f"announcement:{announcement.pk}:user:{student.pk}",
                    title_ar=announcement.title_ar,
                    body_ar=announcement.body_ar,
                    title_en=announcement.title_en,
                    body_en=announcement.body_en,
                    scheduled_for=scheduled_for,
                    push_expires_at=push_expires_at,
                )
            )
            if len(batch) >= ANNOUNCEMENT_BATCH_SIZE:
                StudentNotification.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []
        if batch:
            StudentNotification.objects.bulk_create(batch, ignore_conflicts=True)
        created = StudentNotification.objects.filter(
            announcement_id=announcement.pk
        ).count()
        if not created:
            raise AnnouncementError(
                _("There are no active students in the selected target.")
            )
        completed_at = timezone.now()
        Announcement.objects.filter(pk=announcement.pk).update(
            status=Announcement.Status.COMPLETED,
            recipient_count=created,
            completed_at=completed_at,
            last_error="",
            updated_at=completed_at,
        )
        return created
    except Exception:
        logger.exception("Announcement %s fan-out failed.", announcement_id)
        Announcement.objects.filter(pk=announcement.pk).update(
            status=Announcement.Status.FAILED,
            last_error=_("Notification delivery failed."),
            updated_at=timezone.now(),
        )
        raise


def retry_announcement(announcement_id: int, actor: User) -> Announcement:
    """Requeue one failed announcement for another background attempt."""
    _require_admin(actor)
    with transaction.atomic():
        announcement = (
            Announcement.objects.select_for_update()
            .filter(pk=announcement_id)
            .first()
        )
        if announcement is None:
            raise AnnouncementError(_("This notification does not exist."))
        if announcement.status != Announcement.Status.FAILED:
            raise AnnouncementError(_("This notification does not need a retry."))
        Announcement.objects.filter(pk=announcement.pk).update(
            status=Announcement.Status.QUEUED,
            last_error="",
            updated_at=timezone.now(),
        )
        transaction.on_commit(
            lambda: current_app.send_task(
                ANNOUNCEMENT_FANOUT_TASK,
                args=[announcement.pk],
            )
        )
        announcement.status = Announcement.Status.QUEUED
        return announcement


def pending_announcement_ids(limit: int) -> list[int]:
    """Return queued or stale sending announcements for recovery."""
    now = timezone.now()
    bounded = max(1, min(int(limit), ANNOUNCEMENT_RECOVERY_LIMIT))
    return list(
        Announcement.objects.filter(
            Q(
                status=Announcement.Status.QUEUED,
                updated_at__lt=now - ANNOUNCEMENT_RECOVERY_GRACE,
            )
            | Q(
                status=Announcement.Status.SENDING,
                updated_at__lt=now - ANNOUNCEMENT_STALE_AFTER,
            )
        )
        .order_by("updated_at", "pk")
        .values_list("pk", flat=True)[:bounded]
    )
