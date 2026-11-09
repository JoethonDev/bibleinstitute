"""Explicit admin redelivery: immutable original audience and new delivery copies."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Max
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from .admin_operations import AdminOperationError, assert_admin_operation_progress, create_admin_operation, require_admin_operation_actor
from .announcements import announcement_recipient_queryset
from .models import (
    AdminOperationJob, PushDelivery, QuizStudentOpening, StudentNotification,
    TelegramAccount, TelegramBroadcast, TelegramNotificationDelivery, User,
)
from .notification_history import notification_materializing_ids, original_audience, original_notification
from .student_notifications import (
    eligible_student_offering_pairs,
    is_active_published_lesson,
    is_active_published_quiz,
)
from .mobile_push_queries import active_push_devices
from .telegram.broadcasts import (
    BroadcastError,
    _validate_target,
    broadcast_redelivery_audience,
    clone_broadcast_for_redelivery,
)
from .utils.lesson_access import lesson_is_available

from celery import current_app
from django.utils.dateparse import parse_datetime


BATCH_SIZE = 100
ACTIVE = ("queued", "running", "waiting")
logger = logging.getLogger(__name__)


def redelivery_source_queryset():
    return StudentNotification.objects.select_related(
        "student", "lesson__course_offering__academic_year_level__academic_year",
        "quiz__course_offering__academic_year_level__academic_year",
        "announcement__academic_year",
        "redelivery_job__actor__role",
    ).prefetch_related("announcement__levels")


def eligible_redelivery_notification_ids(notifications: list[StudentNotification]) -> set[int]:
    """Recheck current source/access and exact quiz window, batched by offering."""
    now = timezone.now()
    candidate = []
    offering_candidates = set()
    announcements = {}
    announcement_users = defaultdict(set)
    openings = {
        (row.quiz_id, row.student_id): (row.opening_date, row.closing_date)
        for row in QuizStudentOpening.objects.filter(
            quiz_id__in={n.quiz_id for n in notifications if n.quiz_id},
            student_id__in={n.student_id for n in notifications},
        )
    }
    for notification in notifications:
        if notification.cancelled_at or notification.withdrawn_at or notification.scheduled_for > now:
            continue
        if notification.is_manual_redelivery:
            parent = notification.redelivery_job
            if (parent is None or parent.status not in {"running", "waiting"}
                    or not parent.actor.is_active or not parent.actor.role or parent.actor.role.role != "admin"):
                continue
        if notification.notification_type == "announcement":
            source = notification.announcement
            if source is None or not source.academic_year.is_active or source.status != "completed":
                continue
            announcements[source.pk] = source
            announcement_users[source.pk].add(notification.student_id)
        else:
            source = notification.lesson if notification.notification_type == "lesson_published" else notification.quiz
            if source is None:
                continue
            if notification.notification_type == "lesson_published":
                if not is_active_published_lesson(source) or not lesson_is_available(source):
                    continue
            else:
                if not is_active_published_quiz(source):
                    continue
                window = openings.get((source.pk, notification.student_id), (source.opening_date, source.closing_date))
                if (window != (notification.effective_opening_at, notification.effective_closing_at)
                        or not notification.effective_opening_at <= now < notification.effective_closing_at):
                    continue
            offering = source.course_offering
            offering_candidates.add((
                notification.student_id,
                offering.pk,
                offering.academic_year_level_id,
            ))
        candidate.append(notification)
    eligible_offering_pairs = eligible_student_offering_pairs(offering_candidates)
    announcement_groups = defaultdict(list)
    for source_id, source in announcements.items():
        level_ids = tuple(sorted(level.pk for level in source.levels.all()))
        announcement_groups[(source.academic_year_id, level_ids)].append(source_id)
    eligible_announcements = {}
    for (academic_year_id, level_ids), source_ids in announcement_groups.items():
        candidate_student_ids = {
            student_id
            for source_id in source_ids
            for student_id in announcement_users[source_id]
        }
        eligible_students = set(announcement_recipient_queryset(
            level_ids,
            academic_year_id=academic_year_id,
        ).filter(pk__in=candidate_student_ids).values_list("pk", flat=True))
        for source_id in source_ids:
            eligible_announcements[source_id] = eligible_students & announcement_users[source_id]
    return {n.pk for n in candidate if (
        n.student_id in eligible_announcements.get(n.announcement_id, set()) if n.notification_type == "announcement"
        else (n.student_id, (n.lesson if n.lesson_id else n.quiz).course_offering_id) in eligible_offering_pairs
    )}


def enqueue_notification_redelivery(*, actor, source: str, source_id: int, mode: str, audience: str = "group", language: str = "ar"):
    if not User.objects.filter(pk=actor.pk, is_active=True, role__role="admin").exists():
        raise ValidationError(_("Only administrators can resend notifications."))
    if (source not in {"notification", "telegram"} or mode not in {"failed", "all"}
            or audience not in {"group", "recipient"} or (source == "telegram" and audience != "group")):
        raise ValidationError(_("Invalid notification redelivery selection."))
    with transaction.atomic():
        if source == "notification":
            sample = original_notification(StudentNotification.objects.get(pk=source_id))
            # One event-wide lock also excludes overlapping single-recipient sends.
            original = original_audience(sample).order_by("pk").first()
            if original is None or (sample.notification_type == "announcement" and sample.announcement_id is None):
                raise ValidationError(_("The original notification is no longer available."))
            list(StudentNotification.objects.select_for_update(of=("self",)).filter(
                pk__in={original.pk, sample.pk},
            ).order_by("pk").values_list("pk", flat=True))
            if sample.scheduled_for > timezone.now():
                raise ValidationError(_("This notification has not reached its scheduled time."))
            if mode == "all" and sample.pk in notification_materializing_ids([sample.pk]):
                raise ValidationError(_("Wait for the original delivery records to finish before resending all."))
            source_id = sample.pk
            if sample.announcement_id and sample.announcement.status != "completed":
                raise ValidationError(_("Finish notification generation before resending."))
            upper = original_audience(sample, audience=audience).aggregate(value=Max("pk"))["value"]
            key = f"notification-redelivery:{original.pk}"
        else:
            broadcast = TelegramBroadcast.objects.select_for_update(of=("self",)).select_related(
                "academic_year", "level",
            ).get(pk=source_id)
            if broadcast.redelivery_of_id:
                broadcast = TelegramBroadcast.objects.select_for_update(of=("self",)).select_related(
                    "academic_year", "level",
                ).get(pk=broadcast.redelivery_of_id)
            if broadcast.status not in {TelegramBroadcast.Status.COMPLETED, TelegramBroadcast.Status.FAILED}:
                raise ValidationError(_("Wait for the current delivery to finish before resending."))
            try:
                _validate_target(broadcast)
            except BroadcastError as exc:
                raise ValidationError(str(exc)) from exc
            if not broadcast_redelivery_audience(broadcast, failed_only=mode == "failed").exists():
                raise ValidationError(_("There are no currently eligible recipients for this delivery."))
            source_id = broadcast.pk
            upper = 0
            key = f"broadcast-redelivery:{broadcast.pk}"
        if AdminOperationJob.objects.filter(kind="notification_redelivery", conflict_key=key, status__in=ACTIVE).exists():
            raise ValidationError(_("A redelivery for this notification is already in progress."))
        return create_admin_operation(
            actor=actor, kind=AdminOperationJob.Kind.NOTIFICATION_REDELIVERY,
            input={"source": source, "source_id": source_id, "mode": mode, "audience": audience, "upper_pk": upper},
            conflict_key=key, language=language,
        )


def _latest_channel_rows(root_ids: list[int], devices: list) -> tuple[dict, dict]:
    pushes = PushDelivery.objects.annotate(root_id=Coalesce("notification__redelivery_of_id", "notification_id")).filter(
        root_id__in=root_ids, device_id__in=[d.pk for d in devices],
        device__user_id=F("notification__student_id"),
    ).order_by("root_id", "device_id", "-pk").distinct("root_id", "device_id").values("root_id", "device_id", "status")
    telegram = TelegramNotificationDelivery.objects.annotate(
        root_id=Coalesce("student_notification__redelivery_of_id", "student_notification_id"),
    ).filter(root_id__in=root_ids).order_by("root_id", "-pk").distinct("root_id").values("root_id", "status", "content_snapshot")
    return {(r["root_id"], r["device_id"]): r["status"] for r in pushes}, {r["root_id"]: r for r in telegram}


def execute_notification_redelivery(job, token) -> dict:
    require_admin_operation_actor(job)
    payload = job.input
    if (job.cursor or {}).get("prepared"):
        return {"status": AdminOperationJob.Status.WAITING, "result": job.result}
    if payload["source"] == "telegram":
        with transaction.atomic():
            try:
                broadcast = clone_broadcast_for_redelivery(
                    source_id=payload["source_id"], actor=job.actor, failed_only=payload["mode"] == "failed",
                )
            except BroadcastError as exc:
                raise AdminOperationError("redelivery_not_available", str(exc)) from exc
            result = {"broadcast_id": broadcast.pk, "expected": broadcast.recipient_count, "prepare_skipped": 0}
            assert_admin_operation_progress(job.pk, token, processed=0, succeeded=0, failed=0, skipped=0,
                                            total=broadcast.recipient_count, cursor={"prepared": True}, result=result)
        return {"status": AdminOperationJob.Status.WAITING, "result": result}
    source = StudentNotification.objects.get(pk=payload["source_id"])
    roots = original_audience(source, audience=payload["audience"], upper_pk=payload["upper_pk"])
    cursor = int((job.cursor or {}).get("last_pk", 0))
    prepared_skipped = int((job.result or {}).get("prepare_skipped", 0))
    expected = int((job.result or {}).get("expected", 0))
    errors = list((job.result or {}).get("row_errors") or [])[:20]
    while True:
        batch = list(redelivery_source_queryset().filter(pk__in=roots.values("pk"), pk__gt=cursor).order_by("pk")[:BATCH_SIZE])
        if not batch:
            break
        failed_only = payload["mode"] == "failed"
        with transaction.atomic():
            # Normal channel materializers take the same source-row locks. This
            # closes the gap where resend-all could race a not-yet-inserted
            # original delivery row and send both the original and its copy.
            locked_batch = list(redelivery_source_queryset().select_for_update(
                of=("self",),
            ).filter(pk__in=[n.pk for n in batch]).order_by("pk"))
            locked_ids = [row.pk for row in locked_batch]
            eligible_ids = eligible_redelivery_notification_ids(locked_batch)
            devices = list(active_push_devices().filter(user_id__in=[row.student_id for row in locked_batch]).only("pk", "user_id"))
            by_student = defaultdict(list)
            for device in devices:
                by_student[device.user_id].append(device)
            accounts = {
                user_id: (account_id, chat_id)
                for user_id, account_id, chat_id in TelegramAccount.objects.filter(
                user_id__in=[row.student_id for row in locked_batch], is_active=True,
                ).values_list("user_id", "pk", "telegram_chat_id")
            }
            latest_push, latest_telegram = _latest_channel_rows(locked_ids, devices)
            materializing_ids = notification_materializing_ids(locked_ids) if not failed_only else set()
            copies, plans = [], []
            for original in locked_batch:
                selected_devices = [d for d in by_student[original.student_id]
                                    if not failed_only or latest_push.get((original.pk, d.pk)) == "failed"]
                tg = latest_telegram.get(original.pk, {})
                telegram_account = accounts.get(original.student_id)
                send_telegram = bool(telegram_account and (not failed_only or tg.get("status") == "failed"))
                # Never overlap live attempts or silently resend an already-recovered channel.
                pending = not failed_only and (
                    original.pk in materializing_ids
                    or any(latest_push.get((original.pk, d.pk)) in {
                        PushDelivery.Status.QUEUED, PushDelivery.Status.SENDING, PushDelivery.Status.TICKETED,
                    } for d in by_student[original.student_id])
                    or tg.get("status") in {
                        TelegramNotificationDelivery.Status.QUEUED,
                        TelegramNotificationDelivery.Status.SENDING,
                    }
                )
                if original.pk not in eligible_ids or pending or (failed_only and not selected_devices and not send_telegram):
                    prepared_skipped += 1
                    if len(errors) < 20:
                        errors.append({"row": original.student_id, "message": str(_("No eligible failed delivery, or another delivery is still pending."))})
                    continue
                expires = timezone.now() + timedelta(seconds=settings.MOBILE_PUSH_LESSON_TTL_SECONDS)
                if original.notification_type == "quiz_opening":
                    expires = min(expires, original.effective_closing_at)
                copy = StudentNotification(
                    student_id=original.student_id, notification_type=original.notification_type,
                    lesson_id=original.lesson_id, quiz_id=original.quiz_id, announcement_id=original.announcement_id,
                    effective_opening_at=original.effective_opening_at, effective_closing_at=original.effective_closing_at,
                    idempotency_key=f"redelivery:{job.pk}:{original.pk}", redelivery_job=job,
                    redelivery_of=original, is_manual_redelivery=True, inbox_visible=not failed_only,
                    title_ar=original.title_ar, body_ar=original.body_ar, title_en=original.title_en, body_en=original.body_en,
                    action_label=original.action_label, action_url=original.action_url,
                    navigation_type=original.navigation_type, offering_id=original.offering_id, entity_id=original.entity_id,
                    scheduled_for=timezone.now(), push_expires_at=expires,
                )
                copies.append(copy)
                plans.append((copy, selected_devices, telegram_account if send_telegram else None, tg.get("content_snapshot") or ""))
            StudentNotification.objects.bulk_create(copies, batch_size=BATCH_SIZE)
            push_rows, telegram_rows = [], []
            for copy, selected_devices, telegram_account, content in plans:
                push_rows.extend(PushDelivery(notification=copy, device=device) for device in selected_devices)
                if telegram_account:
                    account_id, chat_id = telegram_account
                    telegram_rows.append(TelegramNotificationDelivery(
                        idempotency_key=copy.idempotency_key, notification_type=copy.notification_type,
                        user_id=copy.student_id, telegram_account_id=account_id, student_notification=copy,
                        telegram_chat_id=chat_id,
                        lesson_id=copy.lesson_id, quiz_id=copy.quiz_id, announcement_id=copy.announcement_id,
                        scheduled_for=timezone.now(), content_snapshot=content,
                    ))
            PushDelivery.objects.bulk_create(push_rows, batch_size=500)
            TelegramNotificationDelivery.objects.bulk_create(telegram_rows, batch_size=BATCH_SIZE)
            expected += len(push_rows) + len(telegram_rows) + (len(copies) if not failed_only else 0)
            cursor = batch[-1].pk
            result = {"expected": expected, "prepare_skipped": prepared_skipped, "row_errors": errors}
            assert_admin_operation_progress(job.pk, token, processed=prepared_skipped, succeeded=0, failed=0,
                                            skipped=prepared_skipped, total=expected + prepared_skipped,
                                            cursor={"last_pk": cursor}, result=result)
    result = {"expected": expected, "prepare_skipped": prepared_skipped, "row_errors": errors}
    assert_admin_operation_progress(job.pk, token, processed=prepared_skipped, succeeded=0, failed=0,
                                    skipped=prepared_skipped, total=expected + prepared_skipped,
                                    cursor={"last_pk": cursor, "prepared": True}, result=result)
    return {"status": AdminOperationJob.Status.WAITING, "result": result}


def _wake_broadcast(broadcast_id: int) -> None:

    try:
        current_app.send_task("management_system.telegram_tasks.process_due_telegram_broadcasts", args=[broadcast_id])
    except Exception:
        logger.warning("notification broadcast awaits dispatcher recovery", extra={"broadcast_id": broadcast_id})


def refresh_notification_redeliveries(*, limit: int = 25) -> int:
    """Refresh at most 25 waiting jobs; web polls read stored ledger counters."""
    ids = list(AdminOperationJob.objects.filter(kind="notification_redelivery", status="waiting")
               .order_by("updated_at").values_list("pk", flat=True)[:min(limit, 25)])
    for job_id in ids:
        with transaction.atomic():
            job = AdminOperationJob.objects.select_for_update(of=("self",), skip_locked=True).select_related("actor__role").filter(pk=job_id, status="waiting").first()
            if job is None:
                continue
            result = job.result or {}
            skipped = int(result.get("prepare_skipped", 0))
            counts = defaultdict(int)
            if result.get("broadcast_id"):
                broadcast = TelegramBroadcast.objects.filter(pk=result["broadcast_id"]).first()
                if broadcast:
                    for row in broadcast.recipients.values("status").annotate(n=Count("pk")):
                        counts[row["status"]] += row["n"]
                    stamp = result.get("last_dispatch_at", "")
                    last_dispatch = parse_datetime(stamp) if stamp else None
                    if (counts["queued"] or counts["sending"]) and (not last_dispatch or timezone.now() - last_dispatch > timedelta(seconds=60)):
                        result["last_dispatch_at"] = timezone.now().isoformat()
                        transaction.on_commit(lambda pk=broadcast.pk: _wake_broadcast(pk))
                succeeded = counts["sent"]
                failed = counts["failed"]
                skipped += counts["skipped"]
            else:
                copies = StudentNotification.objects.filter(redelivery_job=job)
                cancelled = copies.filter(cancelled_at__isnull=False)
                if not job.actor.is_active or not job.actor.role or job.actor.role.role != "admin":
                    copies.filter(cancelled_at__isnull=True).update(cancelled_at=timezone.now(), updated_at=timezone.now())
                PushDelivery.objects.filter(notification__in=cancelled, status="queued").update(
                    status="skipped", error_code="notification_cancelled", error_message="The notification was cancelled.", updated_at=timezone.now(),
                )
                TelegramNotificationDelivery.objects.filter(student_notification__in=cancelled, status="queued").update(
                    status="skipped", last_error=_("The notification was cancelled."), updated_at=timezone.now(),
                )
                succeeded = copies.filter(inbox_visible=True, cancelled_at__isnull=True).count()
                for model, key in ((PushDelivery, "notification__redelivery_job"), (TelegramNotificationDelivery, "student_notification__redelivery_job")):
                    for row in model.objects.filter(**{key: job}).values("status").annotate(n=Count("pk")):
                        counts[row["status"]] += row["n"]
                succeeded += counts["delivered"] + counts["sent"]
                failed = counts["failed"]
                skipped += counts["skipped"]
            pending = counts["queued"] + counts["sending"] + counts["ticketed"]
            total = int(result.get("expected", 0)) + int(result.get("prepare_skipped", 0))
            if not pending:
                skipped += max(0, total - succeeded - failed - skipped)
            processed = succeeded + failed + skipped
            status = "waiting" if pending else ("partial" if failed or skipped else "completed")
            AdminOperationJob.objects.filter(pk=job.pk, status="waiting").update(
                total=total, processed=processed, succeeded=succeeded, failed=failed, skipped=skipped,
                status=status, result=result, updated_at=timezone.now(), finished_at=timezone.now() if not pending else None,
            )
    return len(ids)
