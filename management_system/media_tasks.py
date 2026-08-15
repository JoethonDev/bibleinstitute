"""Celery lifecycle for one server-side media processing job."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import redis
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .media_processing import (
    MediaProcessingError,
    MediaProcessingPhase,
    MediaProcessingStatus,
    _attach_outputs,
    claim_attachment_retry,
    claim_queued_job,
    finish_attachment_retry,
    record_job_failure,
    run_media_job,
    transition_job,
)
from .media_storage import MediaStorageError, delete_object_exact, verify_staging_object
from .models import MediaAttachmentStatus, MediaProcessingJob

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
    """Write bounded live progress and throttled durable heartbeats."""

    def __init__(self, job: MediaProcessingJob):
        self.job = job
        self.redis = _redis_client()
        self.key = f"media:job:{job.public_id}"
        self.last_db_heartbeat = 0.0

    def update(self, phase: str, progress: int) -> None:
        progress = max(0, min(99, int(progress)))
        now = time.time()
        values = {
            "phase": phase,
            "progress": progress,
            "heartbeat_at": timezone.now().isoformat(),
            "attempt": self.job.attempt_count,
            "worker_id": str(getattr(settings, "MEDIA_WORKER_ID", "media-worker")),
        }
        if self.redis is not None:
            try:
                self.redis.hset(self.key, mapping=values)
                self.redis.expire(self.key, LIVE_PROGRESS_TTL)
            except Exception:
                logger.warning("Redis live media progress is unavailable", exc_info=True)
        if now - self.last_db_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            MediaProcessingJob.objects.filter(pk=self.job.pk).update(
                phase=phase,
                progress=progress,
                last_heartbeat_at=timezone.now(),
            )
            self.last_db_heartbeat = now

    def clear(self) -> None:
        if self.redis is not None:
            try:
                self.redis.delete(self.key)
            except Exception:
                logger.warning("Could not clear Redis media progress", exc_info=True)


def enqueue_media_job(public_id) -> None:
    """Enqueue one job explicitly on the dedicated media queue."""
    process_media_job.apply_async(args=[str(public_id)], queue="media")


def enqueue_media_attachment_retry(public_id) -> None:
    """Queue a lesson-attachment-only retry without retaining the source."""
    retry_media_attachment.apply_async(args=[str(public_id)], queue="media")


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
    reporter = MediaProgressReporter(job)
    created = None
    try:
        reporter.update(MediaProcessingPhase.DOWNLOAD, 1)
        upload_state = {"transitioned": False}

        def report_phase(phase, progress):
            if phase == MediaProcessingPhase.UPLOAD_OUTPUT and not upload_state["transitioned"]:
                transition_job(
                    job.pk,
                    MediaProcessingStatus.UPLOADING,
                    phase=MediaProcessingPhase.UPLOAD_OUTPUT,
                    progress=progress,
                )
                upload_state["transitioned"] = True
            reporter.update(phase, progress)

        created = run_media_job(
            job,
            progress_callback=lambda progress: reporter.update(MediaProcessingPhase.ENCODE, progress),
            heartbeat_callback=report_phase,
        )
        transition_job(
            job.pk,
            MediaProcessingStatus.VERIFYING,
            phase=MediaProcessingPhase.VERIFY,
            progress=96,
        )
        now = timezone.now()
        with transaction.atomic():
            locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
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

        attachment_error = None
        if job.lesson_id:
            try:
                reporter.update(MediaProcessingPhase.ATTACH, 98)
                _attach_outputs(job, created["manifest_key"], created["audio_manifest_key"], created["download_key"])
            except Exception as exc:
                attachment_error = exc

        staging_error = None
        try:
            delete_object_exact(job.source_key)
        except Exception as exc:
            staging_error = exc

        with transaction.atomic():
            locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
            locked.status = MediaProcessingStatus.SUCCEEDED
            locked.phase = MediaProcessingPhase.COMPLETE
            locked.progress = 100
            locked.finished_at = now
            if attachment_error:
                locked.attachment_status = MediaAttachmentStatus.FAILED
                locked.error_code = "attachment_failed"
                locked.error_message = str(attachment_error)[:4000]
            elif locked.lesson_id:
                locked.attachment_status = MediaAttachmentStatus.ATTACHED
            if staging_error:
                locked.error_code = locked.error_code or "staging_cleanup_failed"
                locked.error_message = locked.error_message or str(staging_error)[:4000]
            else:
                locked.staging_deleted_at = now
            locked.save(update_fields=[
                "status", "phase", "progress", "finished_at", "attachment_status",
                "error_code", "error_message", "staging_deleted_at",
            ])
        return {"status": "succeeded", "public_id": str(public_id), "attachment_error": bool(attachment_error)}
    except Exception as exc:
        code = getattr(exc, "code", "media_processing_failed")
        record_job_failure(job.pk, code, str(exc))
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
        return {"status": "failed", "public_id": str(public_id)}
    finish_attachment_retry(public_id)
    return {"status": "attached", "public_id": str(public_id)}


def _recovery_redis_client():
    return _redis_client()


def _has_live_progress(client, public_id) -> bool:
    if client is None:
        return False
    try:
        return bool(client.exists(f"media:job:{public_id}"))
    except Exception:
        return False


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
    client = _recovery_redis_client()
    queued_count = 0
    failed_count = 0
    candidate_ids = list(
        MediaProcessingJob.objects.filter(
            status=MediaProcessingStatus.AWAITING_UPLOAD,
            upload_ack_deadline_at__lte=now,
        ).order_by("pk").values_list("pk", flat=True)[:limit]
    )
    for job_id in candidate_ids:
        with transaction.atomic():
            job = MediaProcessingJob.objects.select_for_update().filter(pk=job_id).first()
            if job is None or job.status != MediaProcessingStatus.AWAITING_UPLOAD:
                continue
            try:
                metadata = verify_staging_object(job.source_key, expected_size=job.source_size)
            except MediaStorageError as exc:
                _mark_recovery_failure(job, exc.code, str(exc), now)
                failed_count += 1
                continue
            job.status = MediaProcessingStatus.QUEUED
            job.phase = MediaProcessingPhase.QUEUED
            job.progress = 0
            job.source_etag = metadata.get("etag", "")
            job.source_acknowledged_at = now
            job.last_dispatched_at = now
            job.save(update_fields=[
                "status", "phase", "progress", "source_etag",
                "source_acknowledged_at", "last_dispatched_at",
            ])
            transaction.on_commit(lambda public_id=job.public_id: enqueue_media_job(public_id))
            queued_count += 1

    grace = timedelta(seconds=60)
    heartbeat_grace = timedelta(seconds=120)
    pending_ids = list(
        MediaProcessingJob.objects.filter(
            status__in=[MediaProcessingStatus.QUEUED, MediaProcessingStatus.PROCESSING],
        ).order_by("pk").values_list("pk", flat=True)[:limit]
    )
    for job_id in pending_ids:
        with transaction.atomic():
            job = MediaProcessingJob.objects.select_for_update().filter(pk=job_id).first()
            if job is None or job.status not in {MediaProcessingStatus.QUEUED, MediaProcessingStatus.PROCESSING}:
                continue
            live = _has_live_progress(client, job.public_id)
            if live:
                continue
            if job.status == MediaProcessingStatus.QUEUED:
                if job.last_dispatched_at and now - job.last_dispatched_at < grace:
                    continue
            elif job.last_heartbeat_at and now - job.last_heartbeat_at < heartbeat_grace:
                continue
            job.status = MediaProcessingStatus.QUEUED
            job.phase = MediaProcessingPhase.QUEUED
            job.progress = 0
            job.last_dispatched_at = now
            job.save(update_fields=["status", "phase", "progress", "last_dispatched_at"])
            transaction.on_commit(lambda public_id=job.public_id: enqueue_media_job(public_id))
            queued_count += 1
    return {"queued": queued_count, "failed": failed_count}


def retry_failed_media_jobs(limit: int = 100):
    """Verify retained failed sources and requeue a bounded set of jobs."""
    limit = max(1, min(100, int(limit)))
    queued = 0
    rejected = 0
    job_ids = list(
        MediaProcessingJob.objects.filter(
            status=MediaProcessingStatus.FAILED,
            source_acknowledged_at__isnull=False,
        ).order_by("finished_at", "pk").values_list("pk", flat=True)[:limit]
    )
    for job_id in job_ids:
        with transaction.atomic():
            job = MediaProcessingJob.objects.select_for_update().filter(pk=job_id).first()
            if job is None or job.status != MediaProcessingStatus.FAILED:
                continue
            try:
                metadata = verify_staging_object(job.source_key, expected_size=job.source_size)
            except MediaStorageError:
                rejected += 1
                continue
            job.status = MediaProcessingStatus.QUEUED
            job.phase = MediaProcessingPhase.QUEUED
            job.progress = 0
            job.source_etag = metadata.get("etag", "")
            job.last_dispatched_at = timezone.now()
            job.error_code = ""
            job.error_message = ""
            job.save(update_fields=[
                "status", "phase", "progress", "source_etag", "last_dispatched_at",
                "error_code", "error_message",
            ])
            transaction.on_commit(lambda public_id=job.public_id: enqueue_media_job(public_id))
            queued += 1
    return {"queued": queued, "rejected": rejected}


__all__ = [
    "enqueue_media_job",
    "enqueue_media_attachment_retry",
    "process_media_job",
    "recover_pending_media_jobs",
    "retry_media_attachment",
    "retry_failed_media_jobs",
]
