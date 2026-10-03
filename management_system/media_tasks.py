"""Celery lifecycle for one server-side media processing job."""

from __future__ import annotations

import logging
import json
import time
import uuid
import posixpath
from datetime import timedelta

import redis
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .media_processing import (
    ACTIVE_WORKER_STATUSES,
    MAX_AUTOMATION_UPLOAD_FILES,
    MediaProcessingPhase,
    MediaProcessingStatus,
    MediaProcessingError,
    MediaJobCancelled,
    MediaJobLeaseLost,
    _attach_outputs,
    assert_media_job_lease,
    automation_lesson_media_complete,
    claim_attachment_retry,
    claim_queued_job,
    cleanup_job_workdirs,
    media_upload_job_queryset,
    finish_attachment_retry,
    is_automation_job,
    publish_automation_lesson,
    PUBLICATION_PENDING_ERROR,
    PUBLICATION_PENDING_MESSAGE,
    record_job_failure,
    requeue_stalled_job,
    run_media_job,
    queue_verified_job,
    schedule_media_job_after_commit,
    transition_job,
)
from .media_storage import (
    STAGING_UPLOAD_URL_TTL_SECONDS,
    MediaStorageError,
    delete_existing_objects_exact,
    delete_object_exact,
)
from .models import (
    LectureProgress,
    LectureProgressEvent,
    LectureWatchSession,
    MediaAttachmentStatus,
    MediaCleanupStatus,
    MediaProcessingJob,
    Lesson,
    PublicationStatus,
    StudentNotification,
    TelegramNotificationDelivery,
    ViewingSession,
)

logger = logging.getLogger(__name__)

LIVE_PROGRESS_TTL = 2 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 30


def _redis_client():
    try:
        return redis.Redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
    except Exception:
        return None


class MediaProgressReporter:
    """Write live progress and throttled durable heartbeats."""

    def __init__(self, job: MediaProcessingJob):
        self.job = job
        self.redis = _redis_client()
        self.key = f"media:job:{job.public_id}"
        self.last_db_heartbeat = 0.0
        self.last_lease_heartbeat = 0.0
        self.last_cancel_check = 0.0

    def check_cancelled(self, *, force: bool = False) -> None:
        """Poll durable stop intent at a bounded cadence while processing."""
        now = time.monotonic()
        if not force and now - self.last_cancel_check < 1.0:
            return
        self.last_cancel_check = now
        state = MediaProcessingJob.objects.filter(
            pk=self.job.pk,
            attempt_count=self.job.attempt_count,
        ).values("status", "stop_requested_at").first()
        if state is None:
            raise MediaJobLeaseLost
        if state["status"] == MediaProcessingStatus.CANCELLED or state["stop_requested_at"] is not None:
            raise MediaJobCancelled()
        if state["status"] not in {
            MediaProcessingStatus.PROCESSING,
            MediaProcessingStatus.UPLOADING,
            MediaProcessingStatus.VERIFYING,
        }:
            raise MediaJobLeaseLost
        if now - self.last_lease_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            heartbeat_at = timezone.now()
            updated = MediaProcessingJob.objects.filter(
                pk=self.job.pk,
                attempt_count=self.job.attempt_count,
                status__in=ACTIVE_WORKER_STATUSES,
                stop_requested_at__isnull=True,
            ).update(last_heartbeat_at=heartbeat_at)
            if not updated:
                latest = MediaProcessingJob.objects.filter(pk=self.job.pk).values(
                    "attempt_count", "status", "stop_requested_at"
                ).first()
                if latest and latest["attempt_count"] == self.job.attempt_count and latest["stop_requested_at"]:
                    raise MediaJobCancelled()
                raise MediaJobLeaseLost
            self.last_lease_heartbeat = now

    def update(self, phase: str, progress: int) -> None:
        self.check_cancelled()
        progress = max(0, min(99, int(progress)))
        now = time.time()
        heartbeat_at = timezone.now()
        if now - self.last_db_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            updated = MediaProcessingJob.objects.filter(
                pk=self.job.pk,
                attempt_count=self.job.attempt_count,
                status__in=(
                    MediaProcessingStatus.PROCESSING,
                    MediaProcessingStatus.UPLOADING,
                    MediaProcessingStatus.VERIFYING,
                ),
            ).update(
                phase=phase,
                progress=progress,
                last_heartbeat_at=heartbeat_at,
            )
            if not updated:
                raise MediaJobLeaseLost
            self.last_db_heartbeat = now
            self.last_lease_heartbeat = now
        values = {
            "phase": phase,
            "progress": progress,
            "heartbeat_at": heartbeat_at.isoformat(),
            "attempt": self.job.attempt_count,
            "worker_id": str(getattr(settings, "MEDIA_WORKER_ID", "media-worker")),
        }
        if self.redis is not None:
            try:
                self.redis.hset(self.key, mapping=values)
                self.redis.expire(self.key, LIVE_PROGRESS_TTL)
            except Exception:
                logger.warning(
                    "live media progress is unavailable",
                    extra={"event": "media_progress_redis_unavailable", "public_id": str(self.job.public_id)},
                    exc_info=True,
                )

    def clear(self) -> None:
        if self.redis is not None:
            try:
                if self.redis.hget(self.key, "attempt") == str(self.job.attempt_count):
                    self.redis.delete(self.key)
            except Exception:
                logger.warning(
                    "media progress clear failed",
                    extra={"event": "media_progress_clear_failed", "public_id": str(self.job.public_id)},
                    exc_info=True,
                )


def enqueue_media_job(public_id) -> None:
    """Enqueue one job explicitly on the dedicated media queue."""
    process_media_job.apply_async(args=[str(public_id)], queue="media")


def enqueue_media_attachment_retry(public_id) -> None:
    """Queue a lesson-attachment-only retry without retaining the source."""
    retry_media_attachment.apply_async(args=[str(public_id)], queue="media")


def enqueue_media_cleanup(public_id, job_ids: list[str], *, countdown: int = 0) -> None:
    """Queue exact cleanup on the worker that owns the private work directory."""
    cleanup_media_upload.apply_async(
        args=[str(public_id), job_ids],
        queue="media",
        countdown=max(0, int(countdown)),
    )


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    name="management_system.media_tasks.process_media_job",
)
def process_media_job(self, public_id: str):
    """Claim, process, verify, and finalize exactly one job."""
    job = claim_queued_job(public_id)
    if job is None:
        return {"status": "ignored", "public_id": str(public_id)}
    cleanup_job_workdirs(job.public_id)
    reporter = MediaProgressReporter(job)
    created = None
    started = time.monotonic()
    logger.info(
        "media job started",
        extra={
            "event": "media_job_started",
            "public_id": str(public_id),
            "attempt": job.attempt_count,
            "lesson_id": job.lesson_id,
        },
    )
    try:
        reporter.update(MediaProcessingPhase.DOWNLOAD, 1)
        upload_state = {"transitioned": False}

        def report_phase(phase, progress):
            reporter.check_cancelled()
            if phase == MediaProcessingPhase.UPLOAD_OUTPUT and not upload_state["transitioned"]:
                transition_job(
                    job.pk,
                    MediaProcessingStatus.UPLOADING,
                    phase=MediaProcessingPhase.UPLOAD_OUTPUT,
                    progress=progress,
                    expected_attempt_count=job.attempt_count,
                )
                upload_state["transitioned"] = True
            reporter.update(phase, progress)

        created = run_media_job(
            job,
            progress_callback=lambda progress: reporter.update(MediaProcessingPhase.ENCODE, progress),
            heartbeat_callback=report_phase,
            cancellation_check=reporter.check_cancelled,
        )
        reporter.check_cancelled()
        transition_job(
            job.pk,
            MediaProcessingStatus.VERIFYING,
            phase=MediaProcessingPhase.VERIFY,
            progress=96,
            expected_attempt_count=job.attempt_count,
        )
        now = timezone.now()
        with transaction.atomic():
            locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
            if locked.attempt_count != job.attempt_count:
                raise MediaJobLeaseLost
            locked.phase = MediaProcessingPhase.VERIFY
            locked.progress = 96
            locked.output_keys = created["output_keys"]
            locked.manifest_key = created["manifest_key"]
            locked.audio_manifest_key = created["audio_manifest_key"]
            locked.download_key = created["download_key"]
            if locked.lesson_id:
                locked.attachment_status = MediaAttachmentStatus.PENDING
            locked.last_heartbeat_at = now
            locked.save(update_fields=[
                "status", "phase", "progress", "output_keys", "manifest_key",
                "audio_manifest_key", "download_key", "attachment_status", "last_heartbeat_at",
            ])
            stop_after_processing = locked.stop_requested_at is not None
            if stop_after_processing:
                locked.status = MediaProcessingStatus.CANCELLED
                locked.phase = MediaProcessingPhase.CANCELLED
                locked.finished_at = now
                locked.cancel_acknowledged_at = now
                locked.error_code = "cancelled_by_operator"
                locked.error_message = str(_("Stopped by an administrator."))
                locked.save(update_fields=[
                    "status", "phase", "finished_at", "cancel_acknowledged_at",
                    "error_code", "error_message",
                ])
        if stop_after_processing:
            return {"status": "cancelled", "public_id": str(public_id)}

        attachment_error = None
        if job.lesson_id:
            try:
                reporter.update(MediaProcessingPhase.ATTACH, 98)
                reporter.check_cancelled(force=True)
                _attach_outputs(job, created["manifest_key"], created["audio_manifest_key"], created["download_key"])
            except MediaJobLeaseLost:
                raise
            except Exception as exc:
                attachment_error = exc

        staging_error = None
        staging_deleted = False
        if attachment_error is None:
            reporter.check_cancelled(force=True)
            assert_media_job_lease(job.pk, job.attempt_count)
            try:
                delete_object_exact(job.source_key)
                staging_deleted = True
            except Exception as exc:
                staging_error = exc

        cancelled = False
        with transaction.atomic():
            locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
            if locked.attempt_count != job.attempt_count:
                raise MediaJobLeaseLost
            locked.finished_at = now
            if locked.stop_requested_at is not None:
                locked.status = MediaProcessingStatus.CANCELLED
                locked.phase = MediaProcessingPhase.CANCELLED
                locked.cancel_acknowledged_at = now
                locked.error_code = "cancelled_by_operator"
                locked.error_message = str(_("Stopped by an administrator."))
                if staging_deleted:
                    locked.staging_deleted_at = now
                cancelled = True
            else:
                locked.status = MediaProcessingStatus.SUCCEEDED
                locked.phase = MediaProcessingPhase.COMPLETE
                locked.progress = 100
                if attachment_error:
                    locked.attachment_status = MediaAttachmentStatus.FAILED
                    locked.error_code = "attachment_failed"
                    locked.error_message = str(attachment_error)[:4000]
                elif locked.lesson_id:
                    locked.attachment_status = MediaAttachmentStatus.ATTACHED
                if staging_error:
                    locked.error_code = locked.error_code or "staging_cleanup_failed"
                    locked.error_message = locked.error_message or str(staging_error)[:4000]
                elif staging_deleted:
                    locked.staging_deleted_at = now
                    if is_automation_job(locked) and not attachment_error:
                        # This marker makes publication recovery safe if the
                        # worker exits after finalization and before publication.
                        locked.error_code = PUBLICATION_PENDING_ERROR
                        locked.error_message = PUBLICATION_PENDING_MESSAGE
            locked.save(update_fields=[
                "status", "phase", "progress", "finished_at", "attachment_status",
                "error_code", "error_message", "staging_deleted_at",
            ])
        if cancelled:
            return {"status": "cancelled", "public_id": str(public_id)}
        publication_ok = True
        if (
            attachment_error is None
            and staging_error is None
            and is_automation_job(job)
            and automation_lesson_media_complete(job.lesson_id, exclude_job_id=job.pk)
        ):
            # Multi-file lectures publish once, when the last file is ready.
            try:
                publish_automation_lesson(job.pk)
            except Exception as exc:
                logger.error(
                    "automation publication failed",
                    extra={
                        "event": "automation_publication_failed",
                        "public_id": str(job.pk),
                        "exception_type": type(exc).__name__,
                    },
                )
                publication_ok = False
        result = {
            "status": "succeeded",
            "public_id": str(public_id),
            "attachment_error": bool(attachment_error),
            "publication_error": not publication_ok,
        }
        logger.info(
            "media job succeeded",
            extra={
                "event": "media_job_succeeded",
                "public_id": str(public_id),
                "attempt": job.attempt_count,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "attachment_error": result["attachment_error"],
                "publication_error": result["publication_error"],
            },
        )
        return result
    except MediaJobLeaseLost:
        logger.warning(
            "media job lease lost",
            extra={
                "event": "media_job_lease_lost",
                "public_id": str(public_id),
                "attempt": job.attempt_count,
            },
        )
        return {"status": "stale", "public_id": str(public_id)}
    except MediaJobCancelled as exc:
        output_keys = list((created or {}).get("output_keys") or exc.output_keys or [])
        now = timezone.now()
        with transaction.atomic():
            locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
            if locked.attempt_count == job.attempt_count:
                locked.output_keys = list(dict.fromkeys([*(locked.output_keys or []), *output_keys]))
                if locked.status in ACTIVE_WORKER_STATUSES:
                    locked.status = MediaProcessingStatus.CANCELLED
                    locked.phase = MediaProcessingPhase.CANCELLED
                    locked.finished_at = now
                    locked.cancel_acknowledged_at = now
                    locked.error_code = "cancelled_by_operator"
                    locked.error_message = str(_("Stopped by an administrator."))
                locked.save(update_fields=[
                    "output_keys", "status", "phase", "finished_at",
                    "cancel_acknowledged_at", "error_code", "error_message",
                ])
        logger.info(
            "media job stopped",
            extra={"event": "media_job_cancelled", "public_id": str(public_id), "attempt": job.attempt_count},
        )
        return {"status": "cancelled", "public_id": str(public_id)}
    except Exception as exc:
        code = getattr(exc, "code", "media_processing_failed")
        logger.exception(
            "media job failed",
            extra={
                "event": "media_job_failed",
                "public_id": str(public_id),
                "attempt": job.attempt_count,
                "code": code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        record_job_failure(
            job.pk,
            code,
            str(exc),
            expected_attempt_count=job.attempt_count,
            residual_output_keys=list(getattr(exc, "residual_output_keys", []) or []),
        )
        raise
    finally:
        reporter.clear()


@shared_task(
    name="management_system.media_tasks.retry_media_attachment",
)
def retry_media_attachment(public_id: str):
    """Retry only the database lesson attachment for verified outputs."""
    job = claim_attachment_retry(public_id)
    try:
        _attach_outputs(job, job.manifest_key, job.audio_manifest_key, job.download_key)
    except Exception as exc:
        finish_attachment_retry(public_id, error=exc)
        logger.warning(
            "media attachment retry failed",
            extra={
                "event": "media_attachment_retry_failed",
                "public_id": str(public_id),
                "error_type": type(exc).__name__,
            },
        )
        return {"status": "failed", "public_id": str(public_id)}
    try:
        delete_object_exact(job.source_key)
    except Exception as exc:
        finish_attachment_retry(public_id, error=exc)
        logger.warning(
            "media attachment retry failed",
            extra={
                "event": "media_attachment_retry_failed",
                "public_id": str(public_id),
                "error_type": type(exc).__name__,
            },
        )
        return {"status": "failed", "public_id": str(public_id)}
    now = timezone.now()
    with transaction.atomic():
        finished = finish_attachment_retry(public_id)
        finished.staging_deleted_at = now
        if is_automation_job(finished):
            finished.error_code = PUBLICATION_PENDING_ERROR
            finished.error_message = PUBLICATION_PENDING_MESSAGE
        finished.save(update_fields=["staging_deleted_at", "error_code", "error_message"])
    if is_automation_job(finished) and automation_lesson_media_complete(
        finished.lesson_id, exclude_job_id=finished.pk
    ):
        try:
            publish_automation_lesson(finished.pk)
        except Exception as exc:
            logger.error(
                "automation publication failed",
                extra={
                    "event": "automation_publication_failed",
                    "public_id": str(finished.pk),
                    "exception_type": type(exc).__name__,
                },
            )
    logger.info(
        "media attachment retry succeeded",
        extra={"event": "media_attachment_retry_succeeded", "public_id": str(public_id)},
    )
    return {"status": "attached", "public_id": str(public_id)}


def _job_cleanup_keys(jobs: list[MediaProcessingJob]) -> tuple[set[str], set[str], dict[str, set[str]]]:
    all_keys: set[str] = set()
    direct_keys: set[str] = set()
    direct_parts: dict[str, set[str]] = {}
    for job in jobs:
        if job.source_key:
            all_keys.add(job.source_key)
        job_output_keys = {
            key for key in (job.output_keys or [])
            if isinstance(key, str) and key
        }
        job_output_keys.update(
            key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
            if isinstance(key, str) and key
        )
        all_keys.update(job_output_keys)
        job_direct_keys = {
            key for key in job_output_keys
            if posixpath.splitext(key)[1].lower() in {".m3u8", ".mp3", ".pdf"}
        }
        job_direct_keys.update(
            key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
            if isinstance(key, str) and key
        )
        direct_keys.update(job_direct_keys)
        for key in job_direct_keys:
            direct_parts.setdefault(key, set()).add(job.part_id or "")
    return all_keys, direct_keys, direct_parts


def _find_external_media_references(
    jobs: list[MediaProcessingJob],
    direct_keys: set[str],
) -> set[str]:
    """Find exact manifest/document/audio refs outside the upload group."""
    if not direct_keys:
        return set()
    group_ids = [job.pk for job in jobs]
    target_lesson_ids = {job.lesson_id for job in jobs if job.lesson_id}
    shared: set[str] = set()
    lesson_query = Q(pk__in=[])
    job_query = (
        Q(manifest_key__in=direct_keys)
        | Q(audio_manifest_key__in=direct_keys)
        | Q(download_key__in=direct_keys)
    )
    for key in direct_keys:
        lesson_query |= Q(links__contains=key)
        job_query |= Q(output_keys__contains=[key])

    lessons = Lesson.objects.filter(lesson_query)
    if target_lesson_ids:
        lessons = lessons.exclude(pk__in=target_lesson_ids)
    for raw_links in lessons.values_list("links", flat=True).iterator(chunk_size=250):
        try:
            links = json.loads(raw_links or "[]")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MediaProcessingError(
                _("A lesson reference could not be verified; cleanup stopped safely."),
                "cleanup_reference_check_failed",
            ) from exc
        if not isinstance(links, list):
            raise MediaProcessingError(
                _("A lesson reference could not be verified; cleanup stopped safely."),
                "cleanup_reference_check_failed",
            )
        for link in links:
            if isinstance(link, dict):
                shared.update(
                    value for value in (link.get("id"), link.get("download_id"))
                    if value in direct_keys
                )

    other_jobs = MediaProcessingJob.objects.exclude(pk__in=group_ids).filter(job_query)
    for item in other_jobs.values("manifest_key", "audio_manifest_key", "download_key", "output_keys").iterator(chunk_size=250):
        shared.update(
            value for value in (item["manifest_key"], item["audio_manifest_key"], item["download_key"])
            if value in direct_keys
        )
        shared.update(set(item["output_keys"] or []) & direct_keys)
    return shared


def _detach_upload_links_and_maybe_delete_lesson(
    jobs: list[MediaProcessingJob],
    direct_parts: dict[str, set[str]],
) -> dict:
    lesson_ids = {job.lesson_id for job in jobs if job.lesson_id}
    if not lesson_ids:
        return {"lesson_deleted": False, "media_links_removed": 0}
    if len(lesson_ids) != 1:
        raise MediaProcessingError(
            _("The upload jobs do not share one lesson; cleanup stopped safely."),
            "cleanup_lesson_mismatch",
        )
    lesson = Lesson.objects.select_for_update().filter(pk=next(iter(lesson_ids))).first()
    if lesson is None:
        return {"lesson_deleted": False, "media_links_removed": 0}
    try:
        links = json.loads(lesson.links or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProcessingError(
            _("The lesson media links are invalid; cleanup stopped safely."),
            "cleanup_invalid_lesson_links",
        ) from exc
    if not isinstance(links, list):
        raise MediaProcessingError(
            _("The lesson media links are invalid; cleanup stopped safely."),
            "cleanup_invalid_lesson_links",
        )

    remaining_links = []
    removed = 0
    for link in links:
        if not isinstance(link, dict):
            remaining_links.append(link)
            continue
        part_id = link.get("part_id", "") or ""
        owned = any(
            value in direct_parts and part_id in direct_parts[value]
            for value in (link.get("id"), link.get("download_id"))
            if isinstance(value, str)
        )
        if owned:
            removed += 1
        else:
            remaining_links.append(link)

    is_upload_created = bool(jobs) and all(is_automation_job(job) for job in jobs)
    other_jobs = MediaProcessingJob.objects.filter(lesson_id=lesson.pk).exclude(
        pk__in=[job.pk for job in jobs]
    ).exists()
    has_student_history = any((
        ViewingSession.objects.filter(lesson_id=lesson.pk).exists(),
        LectureProgress.objects.filter(lesson_id=lesson.pk).exists(),
        LectureProgressEvent.objects.filter(lesson_id=lesson.pk).exists(),
        LectureWatchSession.objects.filter(lesson_id=lesson.pk).exists(),
        StudentNotification.objects.filter(lesson_id=lesson.pk).exists(),
        TelegramNotificationDelivery.objects.filter(lesson_id=lesson.pk).exists(),
    ))
    can_delete_lesson = (
        is_upload_created
        and not (lesson.description or "").strip()
        and not remaining_links
        and not other_jobs
        and not has_student_history
    )
    if can_delete_lesson:
        lesson.delete()
        return {"lesson_deleted": True, "media_links_removed": removed}
    if removed:
        lesson.links = json.dumps(remaining_links, ensure_ascii=False)
        lesson.save(update_fields=["links", "updated_date"])
    return {"lesson_deleted": False, "media_links_removed": removed}


def _claim_media_cleanup(job_ids: list[uuid.UUID], now):
    with transaction.atomic():
        jobs = list(
            MediaProcessingJob.objects.select_for_update()
            .filter(public_id__in=job_ids)
            .order_by("pk")
        )
        if len(jobs) != len(job_ids):
            raise MediaProcessingError(
                _("One or more media jobs for this upload no longer exist."),
                "cleanup_jobs_missing",
            )
        if all(job.cleanup_status == MediaCleanupStatus.COMPLETE for job in jobs):
            return None
        if any(
            job.cleanup_status == MediaCleanupStatus.RUNNING
            and job.cleanup_started_at
            and now - job.cleanup_started_at < timedelta(minutes=15)
            for job in jobs
        ):
            return None
        if any(job.status not in {
            MediaProcessingStatus.CANCELLED,
            MediaProcessingStatus.FAILED,
            MediaProcessingStatus.SUCCEEDED,
        } for job in jobs):
            raise MediaProcessingError(
                _("Stop processing before requesting full cleanup."),
                "cleanup_job_active",
            )
        if any(job.attachment_status == MediaAttachmentStatus.PENDING for job in jobs):
            raise MediaProcessingError(
                _("Wait for the lesson attachment to finish before cleanup."),
                "cleanup_attachment_pending",
            )
        if any(
            job.status == MediaProcessingStatus.CANCELLED
            and job.stop_requested_at is not None
            and job.cancel_acknowledged_at is None
            for job in jobs
        ):
            raise MediaProcessingError(
                _("The worker has not confirmed that processing stopped."),
                "cleanup_stop_unconfirmed",
            )
        if any(job.cleanup_status not in {
            MediaCleanupStatus.QUEUED,
            MediaCleanupStatus.RUNNING,
        } for job in jobs):
            raise MediaProcessingError(
                _("A cleanup request is required before files can be removed."),
                "cleanup_not_requested",
            )
        for job in jobs:
            job.cleanup_status = MediaCleanupStatus.RUNNING
            job.cleanup_started_at = now
            job.cleanup_attempt_count += 1
            job.save(update_fields=[
                "cleanup_status", "cleanup_started_at", "cleanup_attempt_count",
            ])
        return jobs


def _finish_media_cleanup(
    job_ids: list[uuid.UUID],
    *,
    result: dict | None = None,
    preserved_keys: list[str] | None = None,
    failed: bool = False,
) -> None:
    status = MediaCleanupStatus.FAILED if failed else MediaCleanupStatus.COMPLETE
    now = timezone.now()
    MediaProcessingJob.objects.filter(public_id__in=job_ids).update(
        cleanup_status=status,
        cleanup_finished_at=now,
        cleanup_preserved_keys=preserved_keys or [],
        cleanup_result=result or {},
    )


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    name="management_system.media_tasks.cleanup_media_upload",
)
def cleanup_media_upload(self, public_id: str, requested_job_ids: list[str] | None = None):
    """Remove server temp files and exact R2 objects after an acknowledged stop."""
    root = MediaProcessingJob.objects.filter(public_id=public_id).first()
    if root is None:
        return {"status": "ignored", "public_id": str(public_id)}
    raw_ids = root.cleanup_group_ids or requested_job_ids or [str(root.public_id)]
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= MAX_AUTOMATION_UPLOAD_FILES:
        return {"status": "invalid_cleanup_group", "public_id": str(public_id)}
    try:
        job_ids = sorted({uuid.UUID(str(value)) for value in raw_ids}, key=str)
    except (TypeError, ValueError, AttributeError):
        return {"status": "invalid_cleanup_group", "public_id": str(public_id)}
    if root.pk not in MediaProcessingJob.objects.filter(public_id__in=job_ids).values_list("pk", flat=True):
        return {"status": "invalid_cleanup_group", "public_id": str(public_id)}

    jobs_before_claim = list(
        MediaProcessingJob.objects.filter(public_id__in=job_ids).order_by("pk")
    )
    not_before = max(
        (job.cleanup_not_before for job in jobs_before_claim if job.cleanup_not_before),
        default=None,
    )
    now = timezone.now()
    if not_before and not_before > now:
        delay = max(1, int((not_before - now).total_seconds()) + 1)
        MediaProcessingJob.objects.filter(
            public_id__in=job_ids,
            cleanup_status=MediaCleanupStatus.QUEUED,
        ).update(cleanup_last_dispatched_at=now)
        enqueue_media_cleanup(public_id, [str(item) for item in job_ids], countdown=delay)
        return {"status": "waiting_for_upload_url_expiry", "retry_after_seconds": delay}

    try:
        jobs = _claim_media_cleanup(job_ids, now)
    except Exception as exc:
        _finish_media_cleanup(job_ids, failed=True)
        logger.warning(
            "media cleanup claim failed",
            extra={"event": "media_cleanup_claim_failed", "public_id": str(public_id), "error_type": type(exc).__name__},
        )
        return {"status": "failed", "public_id": str(public_id)}
    if jobs is None:
        return {"status": "already_running_or_complete", "public_id": str(public_id)}

    result = dict(jobs[0].cleanup_result or {})
    result.setdefault("lesson_deleted", False)
    result.setdefault("media_links_removed", 0)
    result.setdefault("worker_directories_removed", 0)
    result.setdefault("objects_cleaned", 0)
    result.setdefault("objects_deleted_total", 0)
    preserved: set[str] = set()
    delete_keys: set[str] = set()
    try:
        for job in jobs:
            result["worker_directories_removed"] += cleanup_job_workdirs(job.public_id, raise_on_error=True)

        all_output_keys: set[str] = set()
        source_keys: set[str] = set()
        direct_parts: dict[str, set[str]] = {}
        for job in jobs:
            if job.source_key:
                source_keys.add(job.source_key)
            job_keys = {
                key for key in (job.output_keys or [])
                if isinstance(key, str) and key
            }
            job_keys.update(
                key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
                if isinstance(key, str) and key
            )
            all_output_keys.update(job_keys)
            direct_keys = {
                key for key in job_keys
                if posixpath.splitext(key)[1].lower() in {".m3u8", ".mp3", ".pdf"}
            }
            direct_keys.update(
                key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
                if isinstance(key, str) and key
            )
            for key in direct_keys:
                direct_parts.setdefault(key, set()).add(job.part_id or "")

        all_direct_keys = set(direct_parts)
        shared_direct = _find_external_media_references(jobs, all_direct_keys)
        with transaction.atomic():
            current_jobs = list(
                MediaProcessingJob.objects.select_for_update()
                .filter(public_id__in=job_ids)
                .order_by("pk")
            )
            if len(current_jobs) != len(job_ids):
                raise MediaProcessingError(
                    _("One or more media jobs for this upload no longer exist."),
                    "cleanup_jobs_missing",
                )
            lesson_result = _detach_upload_links_and_maybe_delete_lesson(current_jobs, direct_parts)
            result["lesson_deleted"] = result.get("lesson_deleted", False) or lesson_result["lesson_deleted"]
            result["media_links_removed"] = result.get("media_links_removed", 0) + lesson_result["media_links_removed"]
            MediaProcessingJob.objects.filter(pk__in=[job.pk for job in current_jobs]).update(
                cleanup_result=result
            )
            target_lesson_ids = {job.lesson_id for job in current_jobs if job.lesson_id}
            if target_lesson_ids:
                raw_values = Lesson.objects.filter(pk__in=target_lesson_ids).values_list("links", flat=True)
                for raw_links in raw_values:
                    try:
                        remaining_links = json.loads(raw_links or "[]")
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise MediaProcessingError(
                            _("Lesson references could not be verified; cleanup stopped safely."),
                            "cleanup_reference_check_failed",
                        ) from exc
                    for link in remaining_links if isinstance(remaining_links, list) else []:
                        if isinstance(link, dict):
                            shared_direct.update(
                                value for value in (link.get("id"), link.get("download_id"))
                                if value in all_direct_keys
                            )

        for job in jobs:
            keys = {
                key for key in (job.output_keys or [])
                if isinstance(key, str) and key
            }
            keys.update(
                key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
                if isinstance(key, str) and key
            )
            direct_keys = {
                key for key in (job.manifest_key, job.audio_manifest_key, job.download_key)
                if isinstance(key, str) and key
            }
            direct_keys.update(
                key for key in keys
                if posixpath.splitext(key)[1].lower() in {".m3u8", ".mp3", ".pdf"}
            )
            if keys and direct_keys & shared_direct:
                preserved.update(keys)

        delete_keys = (source_keys | all_output_keys) - preserved
        try:
            deletion_report = delete_existing_objects_exact(sorted(delete_keys))
        except Exception:
            result["objects_check_failed"] = True
            raise
        result["objects_checked"] = deletion_report["checked"]
        result["objects_found"] = deletion_report["found"]
        result["objects_cleaned"] = deletion_report["deleted"]
        result["objects_deleted_total"] += deletion_report["deleted"]
        result["objects_missing"] = deletion_report["missing"]
        result["objects_failed"] = len(deletion_report["failed_keys"])
        failed_keys = deletion_report["failed_keys"]
        if failed_keys:
            preserved.update(failed_keys)
            _finish_media_cleanup(
                job_ids,
                result=result,
                preserved_keys=sorted(preserved),
                failed=True,
            )
            return {"status": "failed", "public_id": str(public_id)}
        result["objects_preserved"] = len(preserved)
        _finish_media_cleanup(job_ids, result=result, preserved_keys=sorted(preserved))
        logger.info(
            "media upload cleanup completed",
            extra={
                "event": "media_cleanup_completed",
                "public_id": str(public_id),
                "job_count": len(job_ids),
                "objects_cleaned": result["objects_cleaned"],
                "objects_deleted_total": result["objects_deleted_total"],
                "objects_missing": result["objects_missing"],
                "objects_failed": result["objects_failed"],
                "objects_check_failed": bool(result.get("objects_check_failed")),
                "objects_preserved": result["objects_preserved"],
                "lesson_deleted": result["lesson_deleted"],
            },
        )
        return {"status": "complete", "public_id": str(public_id), "result": result}
    except Exception as exc:
        logger.exception(
            "media upload cleanup failed",
            extra={"event": "media_cleanup_failed", "public_id": str(public_id), "error_type": type(exc).__name__},
        )
        _finish_media_cleanup(
            job_ids,
            result=result,
            preserved_keys=sorted(preserved | delete_keys),
            failed=True,
        )
        return {"status": "failed", "public_id": str(public_id)}


def _mark_recovery_failure(job: MediaProcessingJob, code: str, message: str, now) -> None:
    history = list(job.failure_history or [])
    history.append({
        "attempt": job.attempt_count,
        "code": code[:80],
        "message": message[:4000],
        "at": now.isoformat(),
    })
    job.failure_history = history[-20:]
    job.error_code = code[:80]
    job.error_message = message[:4000]
    job.status = MediaProcessingStatus.FAILED
    job.phase = MediaProcessingPhase.FAILED
    job.finished_at = now
    job.staging_expires_at = now + timedelta(hours=int(getattr(settings, "MEDIA_FAILED_SOURCE_RETENTION_HOURS", 72)))
    job.save(update_fields=[
        "failure_history", "error_code", "error_message", "status", "phase",
        "finished_at", "staging_expires_at",
    ])


@shared_task(name="management_system.media_tasks.recover_pending_media_jobs")
def recover_pending_media_jobs(limit: int = 100):
    """Recover delayed acknowledgements and lost media-worker dispatches."""
    limit = max(1, min(100, int(limit)))
    now = timezone.now()
    client = _redis_client()
    queued_count = 0
    failed_count = 0
    cleanup_queued_count = 0
    candidate_ids = list(
        MediaProcessingJob.objects.filter(
            status=MediaProcessingStatus.AWAITING_UPLOAD,
            upload_ack_deadline_at__lte=now,
        ).order_by("pk").values_list("public_id", flat=True)[:limit]
    )
    for job_id in candidate_ids:
        try:
            queued, transitioned = queue_verified_job(job_id, acknowledged_at=now)
        except MediaStorageError as exc:
            with transaction.atomic():
                job = MediaProcessingJob.objects.select_for_update().filter(public_id=job_id).first()
                if job is not None and job.status == MediaProcessingStatus.AWAITING_UPLOAD:
                    _mark_recovery_failure(job, exc.code, str(exc), now)
            failed_count += 1
            continue
        except MediaProcessingJob.DoesNotExist:
            continue
        if transitioned:
            schedule_media_job_after_commit(queued.public_id)
            queued_count += 1

    grace = timedelta(seconds=60)
    heartbeat_grace = timedelta(seconds=120)
    pending_ids = list(
        MediaProcessingJob.objects.filter(
            Q(status__in=[MediaProcessingStatus.QUEUED, MediaProcessingStatus.PROCESSING])
            | Q(
                status__in=[MediaProcessingStatus.UPLOADING, MediaProcessingStatus.VERIFYING],
                stop_requested_at__isnull=False,
            )
        ).order_by("pk").values_list("pk", flat=True)[:limit]
    )
    for job_id in pending_ids:
        job = MediaProcessingJob.objects.filter(pk=job_id).first()
        if job is None:
            continue
        if job.status == MediaProcessingStatus.QUEUED:
            if job.last_dispatched_at and now - job.last_dispatched_at < grace:
                continue
            stale_before = now - grace
        elif job.status in ACTIVE_WORKER_STATUSES:
            if job.last_heartbeat_at and now - job.last_heartbeat_at < heartbeat_grace:
                continue
            stale_before = now - heartbeat_grace
        else:
            continue
        stalled = requeue_stalled_job(
            job.public_id,
            dispatched_at=now,
            stale_before=stale_before,
        )
        if stalled is not None:
            if client is not None:
                try:
                    client.delete(f"media:job:{stalled.public_id}")
                except Exception:
                    logger.warning(
                        "stale media progress clear failed",
                        extra={"event": "media_progress_clear_failed", "public_id": str(stalled.public_id)},
                        exc_info=True,
                    )
            if stalled.status in {MediaProcessingStatus.FAILED, MediaProcessingStatus.CANCELLED}:
                if stalled.status == MediaProcessingStatus.FAILED:
                    failed_count += 1
                continue
            schedule_media_job_after_commit(stalled.public_id)
            queued_count += 1

    cleanup_stale_before = now - timedelta(minutes=15)
    cleanup_dispatch_before = now - timedelta(seconds=120)
    cleanup_ids = list(
        MediaProcessingJob.objects.filter(
            Q(
                cleanup_status=MediaCleanupStatus.QUEUED,
                cleanup_not_before__lte=now,
                cleanup_last_dispatched_at__lte=cleanup_dispatch_before,
            )
            | Q(
                cleanup_status=MediaCleanupStatus.RUNNING,
                cleanup_started_at__lte=cleanup_stale_before,
            )
        ).order_by("pk").values_list("public_id", flat=True)[:limit]
    )
    for cleanup_id in cleanup_ids:
        cleanup_snapshot = MediaProcessingJob.objects.filter(pk=cleanup_id).first()
        if cleanup_snapshot is None:
            continue
        group_ids = cleanup_snapshot.cleanup_group_ids or [str(cleanup_snapshot.public_id)]
        with transaction.atomic():
            group = list(
                MediaProcessingJob.objects.select_for_update()
                .filter(public_id__in=group_ids)
                .order_by("pk")
            )
            cleanup_job = next((job for job in group if job.pk == cleanup_id), None)
            if cleanup_job is None:
                continue
            if not group or all(item.cleanup_status == MediaCleanupStatus.COMPLETE for item in group):
                continue
            group_not_before = max(
                (item.cleanup_not_before for item in group if item.cleanup_not_before),
                default=now,
            )
            if group_not_before > now:
                continue
            if any(
                item.cleanup_status == MediaCleanupStatus.RUNNING
                and item.cleanup_started_at
                and item.cleanup_started_at > cleanup_stale_before
                for item in group
            ):
                continue
            if not any(
                (
                    item.cleanup_status == MediaCleanupStatus.QUEUED
                    and (
                        item.cleanup_last_dispatched_at is None
                        or item.cleanup_last_dispatched_at <= cleanup_dispatch_before
                    )
                )
                or (
                    item.cleanup_status == MediaCleanupStatus.RUNNING
                    and item.cleanup_started_at is not None
                    and item.cleanup_started_at <= cleanup_stale_before
                )
                for item in group
            ):
                continue
            for item in group:
                item.cleanup_status = MediaCleanupStatus.QUEUED
                item.cleanup_started_at = None
                item.cleanup_last_dispatched_at = now
                item.save(update_fields=[
                    "cleanup_status", "cleanup_started_at", "cleanup_last_dispatched_at",
                ])
            transaction.on_commit(
                lambda public_id=cleanup_job.public_id, job_ids=group_ids:
                    enqueue_media_cleanup(public_id, job_ids)
            )
            cleanup_queued_count += 1

    if queued_count or failed_count or cleanup_queued_count:
        logger.info(
            "pending media jobs recovered",
            extra={
                "event": "media_jobs_recovered",
                "queued": queued_count,
                "failed": failed_count,
                "cleanup_queued": cleanup_queued_count,
            },
        )
    return {"queued": queued_count, "failed": failed_count, "cleanup_queued": cleanup_queued_count}


__all__ = [
    "enqueue_media_job",
    "enqueue_media_attachment_retry",
    "process_media_job",
    "cleanup_media_upload",
    "enqueue_media_cleanup",
    "recover_pending_media_jobs",
    "retry_media_attachment",
]
