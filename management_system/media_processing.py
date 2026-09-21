"""Shared validation and durable state transitions for media jobs."""

from __future__ import annotations

import re
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import unicodedata
from contextlib import suppress
from datetime import timedelta
from dataclasses import dataclass
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .media_storage import (
    delete_object_exact,
    download_staging_object,
    upload_output_file,
    verify_staging_object,
)
from .models import (
    MediaAttachmentStatus,
    Lesson,
    MediaProcessingJob,
    MediaProcessingPhase,
    MediaProcessingStatus,
    PublicationStatus,
)
from .student_notifications import is_active_published_offering
from .utils.decorators import can_manage_content


MEDIA_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
PART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
AUDIO_BITRATE_RE = re.compile(r"^[1-9]\d{0,2}k$")
logger = logging.getLogger(__name__)
X264_PRESETS = frozenset({
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
})
SOURCE_EXTENSIONS = {
    ".mp4": "video",
    ".mp3": "audio",
    ".pdf": "document",
}
MAX_FAILURE_HISTORY = 20
MAX_ERROR_MESSAGE_LENGTH = 4000
MAX_STDERR_LENGTH = 8000
AUTOMATION_PART_PREFIX = "automation-"
PUBLICATION_PENDING_ERROR = "publication_pending"
PUBLICATION_FAILED_ERROR = "publish_failed"
PUBLICATION_ERROR_CODES = frozenset({PUBLICATION_PENDING_ERROR, PUBLICATION_FAILED_ERROR})
PUBLICATION_PENDING_MESSAGE = "Publication event is pending."
NON_RETRYABLE_FAILURE_CODES = frozenset({
    "invalid_pdf",
    "invalid_media",
    "invalid_probe",
    "duration_limit",
    "missing_video_stream",
    "missing_audio_stream",
    "invalid_playlist",
    "empty_playlist",
    "output_collision",
    "segment_size_exceeded",
    "staging_object_missing",
    "staging_object_incomplete",
    "staging_size_mismatch",
})

ALLOWED_TRANSITIONS = {
    MediaProcessingStatus.AWAITING_UPLOAD: frozenset({MediaProcessingStatus.QUEUED, MediaProcessingStatus.FAILED, MediaProcessingStatus.CANCELLED}),
    MediaProcessingStatus.QUEUED: frozenset({MediaProcessingStatus.PROCESSING, MediaProcessingStatus.FAILED, MediaProcessingStatus.CANCELLED}),
    MediaProcessingStatus.PROCESSING: frozenset({MediaProcessingStatus.QUEUED, MediaProcessingStatus.UPLOADING, MediaProcessingStatus.FAILED}),
    MediaProcessingStatus.UPLOADING: frozenset({MediaProcessingStatus.VERIFYING, MediaProcessingStatus.FAILED}),
    MediaProcessingStatus.VERIFYING: frozenset({MediaProcessingStatus.SUCCEEDED, MediaProcessingStatus.FAILED}),
    MediaProcessingStatus.FAILED: frozenset({MediaProcessingStatus.QUEUED, MediaProcessingStatus.CANCELLED}),
    MediaProcessingStatus.SUCCEEDED: frozenset(),
    MediaProcessingStatus.CANCELLED: frozenset(),
}


def media_limits() -> dict[str, Any]:
    """Return the bounded media limits from Django settings.

    Encoding values are validated here so a malformed environment value can
    never reach the FFmpeg command line: an unknown x264 preset falls back to
    ``fast``, malformed bitrates to their defaults, and the CRF is clamped to
    the valid H.264 range.
    """
    preset = str(getattr(settings, "MEDIA_VIDEO_PRESET", "fast")).strip().lower()
    if preset not in X264_PRESETS:
        preset = "fast"

    def bitrate_setting(name: str, default: str) -> str:
        value = str(getattr(settings, name, default)).strip().lower()
        return value if AUDIO_BITRATE_RE.fullmatch(value) else default

    bitrate = bitrate_setting("MEDIA_AUDIO_BITRATE", "96k")
    crf = int(getattr(settings, "MEDIA_VIDEO_CRF", 23))
    max_width = int(getattr(settings, "MEDIA_VIDEO_MAX_WIDTH", 854))
    return {
        "max_source_size": int(getattr(settings, "MEDIA_MAX_SOURCE_SIZE", 2 * 1024**3)),
        "max_duration": int(getattr(settings, "MEDIA_MAX_DURATION_SECONDS", 3 * 60 * 60)),
        "job_timeout": int(getattr(settings, "MEDIA_JOB_TIMEOUT_SECONDS", 2 * 60 * 60)),
        "segment_seconds": max(2, int(getattr(settings, "MEDIA_SEGMENT_SECONDS", 6))),
        "source_retention_hours": int(getattr(settings, "MEDIA_SOURCE_RETENTION_HOURS", 24)),
        "failed_retention_hours": int(getattr(settings, "MEDIA_FAILED_SOURCE_RETENTION_HOURS", 72)),
        "video_crf": max(0, min(51, crf)),
        "video_preset": preset,
        "video_maxrate": bitrate_setting("MEDIA_VIDEO_MAXRATE", "448k"),
        "video_bufsize": bitrate_setting("MEDIA_VIDEO_BUFSIZE", "448k"),
        "video_max_width": 0 if max_width <= 0 else min(max_width, 3840),
        "audio_bitrate": bitrate,
        "audio_mono_bitrate": bitrate_setting("MEDIA_AUDIO_MONO_BITRATE", "64k"),
        "mp3_bitrate": bitrate_setting("MEDIA_MP3_BITRATE", "128k"),
        "max_segment_bytes": max(0, int(getattr(settings, "MEDIA_MAX_SEGMENT_BYTES", 2048000))),
    }


def _scaled_bitrate(value: str, factor: float) -> str:
    """Return a bitrate like ``448k`` scaled by ``factor`` (min 16k)."""
    digits = int(value[:-1])
    scaled = max(16, round(digits * factor))
    return f"{scaled}k"


def video_scale_filter(max_width: int) -> str:
    """Downscale the long side to ``max_width`` without upscaling or squashing.

    Landscape sources cap their width, portrait sources cap their height, and
    ``-2`` keeps the other dimension even for H.264.
    """
    return (
        f"scale='if(gt(iw,ih),min({max_width},iw),-2)':"
        f"'if(gt(iw,ih),-2,min({max_width},ih))'"
    )


def audio_encode_args(probe: MediaProbe, limits: dict[str, Any]) -> list[str]:
    """AAC args tuned for the smallest file at the highest perceivable quality.

    Mono sources are encoded mono (same quality, one third fewer bytes) and
    stereo sources use the configured AAC-LC bitrate, which is transparent for
    speech and good for music at 96k.
    """
    mono = (probe.channels or 2) <= 1
    bitrate = limits["audio_mono_bitrate"] if mono else limits["audio_bitrate"]
    return [
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", bitrate,
        "-ac", "1" if mono else "2",
    ]


def _oversized_segments(output_dir: str, segment_names: list[str], max_bytes: int) -> list[str]:
    """Return the generated segments that exceed the per-file size cap."""
    if max_bytes <= 0:
        return []
    oversized = []
    for name in segment_names:
        path = os.path.join(output_dir, name)
        with suppress(OSError):
            if os.path.getsize(path) > max_bytes:
                oversized.append(name)
    return oversized


def validate_requested_folder(folder: Any) -> str:
    """Normalize and validate a final R2 destination folder.

    Empty means the bucket root. The private staging prefix is never accepted
    as a final output destination; academic-year prefixes remain valid output
    roots so new-year media can be added beside existing content.
    """
    if not isinstance(folder, str):
        raise ValidationError(_("Requested folder is invalid."))
    folder = folder.strip().strip("/")
    if len(folder) > 1000:
        raise ValidationError(_("Requested folder is too long."))
    if not folder:
        return ""
    if "\\" in folder or "?" in folder or "#" in folder:
        raise ValidationError(_("Requested folder is invalid."))
    if any(ord(char) < 32 or ord(char) == 127 for char in folder):
        raise ValidationError(_("Requested folder is invalid."))
    parts = folder.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValidationError(_("Requested folder is invalid."))
    if parts[0] == "Raw Files":
        raise ValidationError(_("This storage folder is reserved."))
    return folder


def source_kind_for_filename(filename: str) -> str:
    """Classify a source by its allowed extension before ffprobe verification."""
    if not isinstance(filename, str):
        raise ValidationError(_("Invalid file name."))
    suffix = PurePosixPath(filename).suffix.lower()
    try:
        return SOURCE_EXTENSIONS[suffix]
    except KeyError as exc:
        raise ValidationError(_("Unsupported media file type.")) from exc


def safe_output_base_name(filename: str) -> str:
    """Derive a bounded server-owned output basename without an extension."""
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
        raise ValidationError(_("Invalid file name."))
    stem = PurePosixPath(filename).stem
    normalized = unicodedata.normalize("NFKC", stem)
    normalized = MEDIA_FILENAME_RE.sub("_", normalized).strip("_-")
    normalized = normalized[:200]
    return normalized or "media"


def validate_source_descriptor(filename: Any, size: Any) -> tuple[str, str, int]:
    """Validate one client descriptor and return filename, kind, and size."""
    if not isinstance(filename, str) or not filename or len(filename) > 255:
        raise ValidationError(_("Invalid file name."))
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise ValidationError(_("Invalid file name."))
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValidationError(_("Invalid file size."))
    max_size = media_limits()["max_source_size"]
    if size > max_size:
        raise ValidationError(
            _("File size exceeds the maximum allowed source size of %(size)s bytes.")
            % {"size": max_size}
        )
    return filename, source_kind_for_filename(filename), size


def validate_part_id(part_id: Any) -> str:
    """Validate optional lesson media-part identity."""
    if part_id in (None, ""):
        return ""
    if not isinstance(part_id, str) or not PART_ID_RE.fullmatch(part_id):
        raise ValidationError(_("Media part identifier is invalid."))
    return part_id


def initialize_deadlines(created_at=None) -> tuple[Any, Any]:
    """Return the 30-minute acknowledgement and initial staging deadlines."""
    created_at = created_at or timezone.now()
    return (
        created_at + timedelta(minutes=30),
        created_at + timedelta(hours=media_limits()["source_retention_hours"]),
    )


@transaction.atomic
def transition_job(job_id: int, target_status: str, *, phase: str | None = None, progress: int | None = None) -> MediaProcessingJob:
    """Lock one job and apply one legal lifecycle transition."""
    job = MediaProcessingJob.objects.select_for_update().get(pk=job_id)
    if target_status != job.status and target_status not in ALLOWED_TRANSITIONS.get(job.status, frozenset()):
        raise ValidationError(
            _("Media job cannot transition from %(current)s to %(target)s.")
            % {"current": job.status, "target": target_status}
        )
    if progress is not None and not 0 <= progress <= 100:
        raise ValidationError(_("Media job progress must be between 0 and 100."))
    job.status = target_status
    if phase is not None:
        job.phase = phase
    if progress is not None:
        job.progress = progress
    update_fields = ["status", "phase", "progress"]
    if target_status in {MediaProcessingStatus.SUCCEEDED, MediaProcessingStatus.FAILED, MediaProcessingStatus.CANCELLED}:
        job.finished_at = timezone.now()
        update_fields.append("finished_at")
    job.save(update_fields=update_fields)
    return job


@transaction.atomic
def claim_queued_job(public_id: Any) -> MediaProcessingJob | None:
    """Atomically claim one queued job for a media worker attempt."""
    job = MediaProcessingJob.objects.select_for_update().filter(public_id=public_id).first()
    if job is None or job.status != MediaProcessingStatus.QUEUED:
        return None
    timestamp = timezone.now()
    job.status = MediaProcessingStatus.PROCESSING
    job.phase = MediaProcessingPhase.DOWNLOAD
    job.progress = 0
    job.attempt_count += 1
    job.started_at = timestamp
    job.last_heartbeat_at = timestamp
    job.save(update_fields=["status", "phase", "progress", "attempt_count", "started_at", "last_heartbeat_at"])
    return job


@transaction.atomic
def record_job_failure(job_id: int, error_code: str, error_message: str, *, phase: str = MediaProcessingPhase.FAILED) -> MediaProcessingJob:
    """Persist a bounded failure record while retaining the job for retry."""
    job = MediaProcessingJob.objects.select_for_update().get(pk=job_id)
    message = str(error_message or _("Media processing failed."))[:MAX_ERROR_MESSAGE_LENGTH]
    history = list(job.failure_history or [])
    history.append({
        "attempt": job.attempt_count,
        "code": str(error_code)[:80],
        "message": message,
        "at": timezone.now().isoformat(),
    })
    job.failure_history = history[-MAX_FAILURE_HISTORY:]
    job.error_code = str(error_code)[:80]
    job.error_message = message
    job.status = MediaProcessingStatus.FAILED
    job.phase = phase
    job.finished_at = timezone.now()
    job.staging_expires_at = timezone.now() + timedelta(hours=media_limits()["failed_retention_hours"])
    job.save(update_fields=["failure_history", "error_code", "error_message", "status", "phase", "finished_at", "staging_expires_at"])
    return job


def _queue_locked_job(job: MediaProcessingJob, *, acknowledged_at=None) -> MediaProcessingJob:
    """Apply the queued transition to a job already locked by the caller."""
    if job.status == MediaProcessingStatus.AWAITING_UPLOAD:
        pass
    elif job.status == MediaProcessingStatus.FAILED and job.source_acknowledged_at:
        pass
    elif job.status == MediaProcessingStatus.QUEUED:
        return job
    else:
        raise ValidationError(_("This media job is not ready to be queued."))
    job.status = MediaProcessingStatus.QUEUED
    job.phase = MediaProcessingPhase.QUEUED
    job.progress = 0
    job.error_code = ""
    job.error_message = ""
    if acknowledged_at is not None:
        job.source_acknowledged_at = acknowledged_at
    job.save(update_fields=["status", "phase", "progress", "error_code", "error_message", "source_acknowledged_at"])
    return job


def queue_verified_job(
    public_id: Any,
    *,
    expected_etag: str | None = None,
    acknowledged_at=None,
) -> tuple[MediaProcessingJob, bool]:
    """Verify a staged source, then atomically acknowledge and queue its job.

    Object verification intentionally happens before the database lock. The
    locked transaction only rechecks the state and records the verified
    metadata, so network storage work never extends a row-lock duration.
    """
    job = MediaProcessingJob.objects.filter(public_id=public_id).first()
    if job is None:
        raise MediaProcessingJob.DoesNotExist
    if job.status in {
        MediaProcessingStatus.QUEUED,
        MediaProcessingStatus.PROCESSING,
        MediaProcessingStatus.UPLOADING,
        MediaProcessingStatus.VERIFYING,
        MediaProcessingStatus.SUCCEEDED,
    }:
        return job, False
    if job.status == MediaProcessingStatus.FAILED and not job.source_acknowledged_at:
        raise ValidationError(_("This media job has no acknowledged source to retry."))
    if job.status not in {MediaProcessingStatus.AWAITING_UPLOAD, MediaProcessingStatus.FAILED}:
        raise ValidationError(_("This media job is not ready to be queued."))

    metadata = verify_staging_object(
        job.source_key,
        expected_size=job.source_size,
        expected_etag=expected_etag,
    )
    with transaction.atomic():
        locked = MediaProcessingJob.objects.select_for_update().get(pk=job.pk)
        if locked.status in {
            MediaProcessingStatus.QUEUED,
            MediaProcessingStatus.PROCESSING,
            MediaProcessingStatus.UPLOADING,
            MediaProcessingStatus.VERIFYING,
            MediaProcessingStatus.SUCCEEDED,
        }:
            return locked, False
        queued = _queue_locked_job(locked, acknowledged_at=acknowledged_at)
        queued.source_etag = metadata.get("etag", "")
        queued.last_dispatched_at = timezone.now()
        queued.save(update_fields=["source_etag", "last_dispatched_at"])
    return queued, True


@transaction.atomic
def requeue_stalled_job(public_id: Any, *, dispatched_at=None) -> MediaProcessingJob | None:
    """Reset one stalled queued/processing job and return its locked row."""
    job = MediaProcessingJob.objects.select_for_update().filter(public_id=public_id).first()
    if job is None or job.status not in {
        MediaProcessingStatus.QUEUED,
        MediaProcessingStatus.PROCESSING,
    }:
        return None
    job.status = MediaProcessingStatus.QUEUED
    job.phase = MediaProcessingPhase.QUEUED
    job.progress = 0
    job.last_dispatched_at = dispatched_at or timezone.now()
    job.save(update_fields=["status", "phase", "progress", "last_dispatched_at"])
    return job


def schedule_media_job_after_commit(public_id: Any) -> None:
    """Dispatch one media job only after its queue transition commits."""
    transaction.on_commit(
        lambda public_id=public_id: _enqueue_media_job(public_id)
    )


def schedule_attachment_retry_after_commit(public_id: Any) -> None:
    """Dispatch one attachment retry only after its claim commits."""
    transaction.on_commit(
        lambda public_id=public_id: _enqueue_attachment_retry(public_id)
    )


def _enqueue_media_job(public_id: Any) -> None:
    from .media_tasks import enqueue_media_job

    enqueue_media_job(public_id)


def _enqueue_attachment_retry(public_id: Any) -> None:
    from .media_tasks import enqueue_media_attachment_retry

    enqueue_media_attachment_retry(public_id)


def job_is_visible_to(user, job: MediaProcessingJob) -> bool:
    """Return whether a content manager may inspect/mutate this job."""
    role = getattr(getattr(user, "role", None), "role", None)
    return role == "admin" or job.created_by_id == getattr(user, "pk", None)


def attachment_requested(job: MediaProcessingJob) -> bool:
    return bool(job.lesson_id and job.part_id)


def is_automation_job(job: MediaProcessingJob) -> bool:
    """Return whether the job belongs to the automation-owned media namespace."""
    return bool(job.lesson_id and (job.part_id or "").startswith(AUTOMATION_PART_PREFIX))


def automation_part_id(lesson_token: str, position: int) -> str:
    """Deterministic automation part identity: token plus zero-padded position.

    The zero padding keeps link ordering stable when several source files of one
    lecture finish in a different order than they were uploaded in.
    """
    if not isinstance(lesson_token, str) or not lesson_token or not PART_ID_RE.fullmatch(lesson_token):
        raise ValidationError(_("Media part identifier is invalid."))
    if not isinstance(position, int) or isinstance(position, bool) or position < 1:
        raise ValidationError(_("Media part identifier is invalid."))
    return validate_part_id(f"{AUTOMATION_PART_PREFIX}{lesson_token}-p{position:02d}")


def automation_lesson_media_complete(lesson_id: Any, *, exclude_job_id: Any = None) -> bool:
    """True when every media job of a lesson has verified attached outputs.

    Publication is a lesson-level transition, so a multi-file lesson may only be
    published after the last source file has been processed, attached, and had
    its staging object removed.
    """
    pending = MediaProcessingJob.objects.filter(lesson_id=lesson_id)
    if exclude_job_id is not None:
        pending = pending.exclude(pk=exclude_job_id)
    return not pending.exclude(
        status=MediaProcessingStatus.SUCCEEDED,
        attachment_status=MediaAttachmentStatus.ATTACHED,
        staging_deleted_at__isnull=False,
    ).exists()


def is_retryable_media_failure(error_code: Any) -> bool:
    """Return whether a failed source can be safely retried unchanged."""
    return str(error_code or "") not in NON_RETRYABLE_FAILURE_CODES


def is_retryable_failed_job(job: MediaProcessingJob) -> bool:
    """Apply the shared retry gate for retained failed media jobs."""
    return bool(
        job.status == MediaProcessingStatus.FAILED
        and job.source_acknowledged_at
        and not job.staging_deleted_at
        and is_retryable_media_failure(job.error_code)
    )


class MediaProcessingError(Exception):
    """Bounded processing failure with a stable operator-facing code."""

    def __init__(self, message: str, code: str = "processing_failed"):
        super().__init__(message)
        self.code = code


def mark_publication_failed(job_id: Any) -> None:
    """Persist a safe recovery marker without storing provider exception text."""
    MediaProcessingJob.objects.filter(pk=job_id).update(
        error_code=PUBLICATION_FAILED_ERROR,
        error_message="Automatic publication failed; retry is required.",
    )


@transaction.atomic
def publish_attached_lesson(job_id: Any) -> MediaProcessingJob:
    """Publish an attached automation lesson and persist its notification state.

    The lesson/job transition is committed before notification fan-out. The
    durable pending marker makes a worker crash recoverable without holding
    row locks while thousands of student events are inserted.
    """
    job = MediaProcessingJob.objects.select_for_update().get(pk=job_id)
    if not job.lesson_id:
        return job
    if not is_automation_job(job):
        raise MediaProcessingError("Media job is not owned by automation.", PUBLICATION_FAILED_ERROR)
    if (
        job.status != MediaProcessingStatus.SUCCEEDED
        or job.attachment_status != MediaAttachmentStatus.ATTACHED
        or not job.staging_deleted_at
    ):
        raise MediaProcessingError("Media job is not ready for publication.", PUBLICATION_FAILED_ERROR)
    if not automation_lesson_media_complete(job.lesson_id, exclude_job_id=job.pk):
        # A multi-file lecture publishes once, after every source file is ready.
        raise MediaProcessingError("Media files are still processing.", PUBLICATION_FAILED_ERROR)

    lesson = Lesson.objects.select_for_update().get(pk=job.lesson_id)
    offering = lesson.course_offering
    if not is_active_published_offering(offering):
        raise MediaProcessingError(
            "The lesson offering is not active for publication.",
            PUBLICATION_FAILED_ERROR,
        )
    if lesson.status == PublicationStatus.DRAFT:
        if job.error_code not in PUBLICATION_ERROR_CODES:
            raise MediaProcessingError("Media job has no pending publication.", PUBLICATION_FAILED_ERROR)
        lesson.status = PublicationStatus.PUBLISHED
        lesson.publication_event_version += 1
        lesson.save(update_fields=["status", "updated_date", "publication_event_version"])
        job.error_code = PUBLICATION_PENDING_ERROR
        job.error_message = PUBLICATION_PENDING_MESSAGE
        job.save(update_fields=["error_code", "error_message"])
    elif lesson.status != PublicationStatus.PUBLISHED:
        raise MediaProcessingError("The lesson is not an editable draft.", PUBLICATION_FAILED_ERROR)
    elif job.error_code not in PUBLICATION_ERROR_CODES:
        return job

    # The transaction decorator commits before this function returns. The
    # caller performs notification fan-out after that commit.
    return job


def finish_lesson_publication(job_id: Any) -> MediaProcessingJob:
    """Fan out idempotent publication events, then clear the recovery markers."""
    job = MediaProcessingJob.objects.get(pk=job_id)
    if not job.lesson_id:
        return job
    lesson = Lesson.objects.get(pk=job.lesson_id)
    try:
        from .student_notifications import create_lesson_publication_event

        create_lesson_publication_event(lesson)
    except Exception:
        mark_publication_failed(job_id)
        raise
    with transaction.atomic():
        # Every file of the lesson shares one publication event; clear the
        # pending marker on all of them, not only on the job that published.
        MediaProcessingJob.objects.filter(
            lesson_id=job.lesson_id,
            error_code__in=PUBLICATION_ERROR_CODES,
        ).update(error_code="", error_message="")
        return MediaProcessingJob.objects.select_for_update().get(pk=job_id)


def publish_automation_lesson(job_id: Any) -> MediaProcessingJob:
    """Run the short publication transition followed by idempotent fan-out."""
    try:
        prepared = publish_attached_lesson(job_id)
        if prepared.error_code not in PUBLICATION_ERROR_CODES:
            return prepared
        return finish_lesson_publication(job_id)
    except Exception:
        mark_publication_failed(job_id)
        raise


def validate_browser_part_id(part_id: Any) -> str:
    """Validate a browser part while keeping the automation namespace reserved."""
    value = validate_part_id(part_id)
    if value.startswith(AUTOMATION_PART_PREFIX):
        raise ValidationError(_("This media part identifier is reserved."))
    return value


@dataclass(frozen=True)
class MediaProbe:
    source_kind: str
    duration: float
    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    sample_rate: int = 0
    channels: int = 0

    @property
    def has_video(self) -> bool:
        return bool(self.video_codec)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)


def _binary_command(binary: str, args: list[str]) -> list[str]:
    return [binary, *args]


def _run_command(command: list[str], *, timeout: int, progress_callback=None, duration: float = 0.0) -> str:
    """Run a native command without a shell and kill its process group on timeout."""
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name != "nt"),
            creationflags=creationflags if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise MediaProcessingError(_("Native media tools are unavailable."), "media_tools_unavailable") from exc

    timed_out = threading.Event()

    def stop_for_timeout():
        timed_out.set()
        _terminate_process(process)

    watchdog = threading.Timer(timeout, stop_for_timeout)
    watchdog.daemon = True
    watchdog.start()
    progress: dict[str, str] = {}
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            progress[key] = value
            if key == "out_time_ms" and progress_callback and duration > 0:
                try:
                    percent = max(0, min(99, round((int(value) / 1_000_000) / duration * 100)))
                except (TypeError, ValueError):
                    continue
                progress_callback(percent)
        stderr = (process.stderr.read() if process.stderr else "")[-MAX_STDERR_LENGTH:]
        return_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise MediaProcessingError(_("Media processing exceeded the allowed time."), "processing_timeout") from exc
    except Exception:
        _terminate_process(process)
        raise
    finally:
        watchdog.cancel()
    if timed_out.is_set():
        raise MediaProcessingError(_("Media processing exceeded the allowed time."), "processing_timeout")
    if return_code:
        raise MediaProcessingError(stderr or _("Native media processing failed."), "ffmpeg_failed")
    if progress_callback:
        progress_callback(100)
    return stderr


def _terminate_process(process) -> None:
    try:
        if process.poll() is not None:
            return
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=5)
        else:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def probe_source(source_path: str, source_kind: str) -> MediaProbe:
    """Inspect a downloaded source with ffprobe and enforce basic limits."""
    if source_kind == "document":
        try:
            with open(source_path, "rb") as source:
                if source.read(5) != b"%PDF-":
                    raise MediaProcessingError(_("The uploaded PDF is invalid."), "invalid_pdf")
        except OSError as exc:
            raise MediaProcessingError(_("The uploaded source cannot be read."), "source_read_failed") from exc
        return MediaProbe(source_kind="document", duration=0)

    command = _binary_command("ffprobe", [
        "-v", "error", "-print_format", "json", "-show_format", "-show_streams", source_path,
    ])
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=media_limits()["job_timeout"],
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaProcessingError(_("The uploaded media could not be inspected."), "ffprobe_failed") from exc
    if completed.returncode:
        raise MediaProcessingError(
            completed.stderr[-MAX_STDERR_LENGTH:] or _("The uploaded media is corrupt."),
            "invalid_media",
        )
    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        format_duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProcessingError(_("The media inspection result is invalid."), "invalid_probe") from exc
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    def stream_duration(stream):
        try:
            return float(stream.get("duration") or 0)
        except (TypeError, ValueError):
            return 0.0

    duration = max(format_duration, stream_duration(video), stream_duration(audio))
    if duration <= 0 or duration > media_limits()["max_duration"]:
        raise MediaProcessingError(_("The media duration exceeds the allowed limit."), "duration_limit")
    if source_kind == "video" and not video:
        raise MediaProcessingError(_("The video source has no video stream."), "missing_video_stream")
    if source_kind == "audio" and not audio:
        raise MediaProcessingError(_("The audio source has no audio stream."), "missing_audio_stream")
    return MediaProbe(
        source_kind=source_kind,
        duration=duration,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        sample_rate=int(audio.get("sample_rate") or 0),
        channels=int(audio.get("channels") or 0),
    )


def _hls_segment_names(playlist_path: str) -> list[str]:
    try:
        lines = open(playlist_path, "r", encoding="utf-8").read().splitlines()
    except OSError as exc:
        raise MediaProcessingError(_("Generated HLS playlist cannot be read."), "playlist_read_failed") from exc
    names: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not value.lower().endswith(".ts") or value.startswith(("/", "http://", "https://")):
            raise MediaProcessingError(_("Generated HLS playlist contains an invalid segment."), "invalid_playlist")
        path = PurePosixPath(value)
        if len(path.parts) != 1 or path.name in {".", ".."}:
            raise MediaProcessingError(_("Generated HLS playlist contains an unsafe path."), "invalid_playlist")
        names.append(path.name)
    if not names:
        raise MediaProcessingError(_("Generated HLS playlist contains no segments."), "empty_playlist")
    return names


def _write_rewritten_playlist(playlist_path: str, segment_folder: str) -> str:
    try:
        lines = open(playlist_path, "r", encoding="utf-8").read().splitlines()
    except OSError as exc:
        raise MediaProcessingError(_("Generated HLS playlist cannot be read."), "playlist_read_failed") from exc
    rewritten: list[str] = []
    for line in lines:
        value = line.strip()
        if value and not value.startswith("#") and value.lower().endswith(".ts"):
            value = f"{segment_folder}/{PurePosixPath(value).name}"
        rewritten.append(value)
    rewritten_path = f"{playlist_path}.rewritten"
    try:
        with open(rewritten_path, "w", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(rewritten) + "\n")
    except OSError as exc:
        raise MediaProcessingError(_("Generated HLS playlist cannot be written."), "playlist_write_failed") from exc
    return rewritten_path


def _run_hls(
    input_path: str,
    output_dir: str,
    base_name: str,
    stream: str,
    duration: float,
    progress_callback=None,
    *,
    probe: MediaProbe | None = None,
    rate_scale: float = 1.0,
) -> tuple[str, list[str]]:
    """Encode one HLS rendition from a local source.

    Video output is always re-encoded (H.264 High profile with a capped-CRF
    rate window and a forced keyframe on every segment boundary) and keeps the
    source audio muxed as AAC-LC, so the video player carries sound and every
    segment stays inside the configured size cap. The extracted audio
    intermediate is segmented separately for the audio player and MP3.
    """
    os.makedirs(output_dir, exist_ok=True)
    limits = media_limits()
    playlist = os.path.join(output_dir, f"{base_name}.m3u8")
    segment_pattern = os.path.join(output_dir, f"{base_name}_%03d.ts")
    muxer = [
        "-f", "hls", "-hls_time", str(limits["segment_seconds"]),
        "-hls_list_size", "0", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
        "-hls_segment_filename", segment_pattern, "-progress", "pipe:1", "-nostats",
        playlist,
    ]
    if stream == "v:0":
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", input_path,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-crf", str(limits["video_crf"]), "-preset", limits["video_preset"],
            "-maxrate", _scaled_bitrate(limits["video_maxrate"], rate_scale),
            "-bufsize", _scaled_bitrate(limits["video_bufsize"], rate_scale),
            "-profile:v", "high", "-pix_fmt", "yuv420p",
        ]
        if limits["video_max_width"]:
            command += ["-vf", video_scale_filter(limits["video_max_width"])]
        command += [
            "-force_key_frames", f"expr:gte(t,n_forced*{limits['segment_seconds']})",
            *audio_encode_args(probe or MediaProbe(source_kind="video", duration=0.0, channels=2), limits),
            *muxer,
        ]
    else:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", input_path,
            "-map", "0:a:0", "-vn", "-c:a", "copy",
            *muxer,
        ]
    _run_command(command, timeout=limits["job_timeout"], progress_callback=progress_callback, duration=duration)
    names = _hls_segment_names(playlist)
    return playlist, names


def _encode_hls_with_size_cap(
    input_path: str,
    output_dir: str,
    base_name: str,
    stream: str,
    duration: float,
    progress_callback=None,
    *,
    probe: MediaProbe | None = None,
) -> tuple[str, list[str]]:
    """Encode HLS and guarantee every segment is inside the size cap.

    Capped-CRF already makes an oversized segment unlikely; if one still
    appears (unusual source or a misconfigured rate), the encode is retried
    once with the rate window reduced by 20% before the job fails for
    operator review.
    """
    limits = media_limits()
    playlist, names = _run_hls(
        input_path, output_dir, base_name, stream, duration, progress_callback, probe=probe,
    )
    oversized = _oversized_segments(output_dir, names, limits["max_segment_bytes"])
    if not oversized:
        return playlist, names
    logger.warning(
        "media_segment_over_cap retrying_lower_rate oversized=%s base=%s",
        len(oversized),
        base_name,
    )
    playlist, names = _run_hls(
        input_path, output_dir, base_name, stream, duration, progress_callback,
        probe=probe, rate_scale=0.8,
    )
    oversized = _oversized_segments(output_dir, names, limits["max_segment_bytes"])
    if oversized:
        raise MediaProcessingError(
            _("A generated media segment exceeds the maximum allowed size."),
            "segment_size_exceeded",
        )
    return playlist, names


def _prepare_audio(source_path: str, probe: MediaProbe, work_dir: str, duration: float, progress_callback=None) -> str:
    """Return the AAC audio source used for the audio HLS and MP3 outputs.

    Video sources are always re-encoded to the configured AAC bitrate so the
    extracted track is compressed independently of the video container. An MP3
    source is already a compressed deliverable and is reused unchanged.
    """
    if probe.source_kind == "audio" and source_path.lower().endswith(".mp3") and probe.audio_codec == "mp3":
        return source_path
    output = os.path.join(work_dir, "audio-intermediate.m4a")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", source_path,
        "-map", "0:a:0", "-vn",
        *audio_encode_args(probe, media_limits()),
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", output,
    ]
    _run_command(command, timeout=media_limits()["job_timeout"], progress_callback=progress_callback, duration=duration)
    return output


def _make_mp3(audio_path: str, source_path: str, source_kind: str, work_dir: str, duration: float, progress_callback=None) -> str:
    output = os.path.join(work_dir, "downloadable.mp3")
    if source_kind == "audio" and source_path.lower().endswith(".mp3"):
        shutil.copyfile(source_path, output)
        return output
    _run_command(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", audio_path, "-vn", "-c:a", "libmp3lame", "-b:a", media_limits()["mp3_bitrate"], "-progress", "pipe:1", "-nostats", output],
        timeout=media_limits()["job_timeout"],
        progress_callback=progress_callback,
        duration=duration,
    )
    return output


def _destination_key(folder: str, *parts: str) -> str:
    return "/".join(part for part in (folder, *parts) if part)


def _upload_hls_outputs(playlist_path: str, segment_names: list[str], output_dir: str, folder: str, base_name: str, segment_folder: str, created_keys: list[str], progress_callback=None) -> tuple[str, list[str]]:
    for index, name in enumerate(segment_names, start=1):
        key = _destination_key(folder, segment_folder, name)
        upload_output_file(key, os.path.join(output_dir, name), content_type="video/mp2t")
        created_keys.append(key)
        if progress_callback:
            progress_callback(round(index / len(segment_names) * 90))
    rewritten = _write_rewritten_playlist(playlist_path, segment_folder)
    manifest_key = _destination_key(folder, f"{base_name}.m3u8")
    upload_output_file(manifest_key, rewritten, content_type="application/vnd.apple.mpegurl")
    created_keys.append(manifest_key)
    return manifest_key, [_destination_key(folder, segment_folder, name) for name in segment_names]


def _attach_outputs(job: MediaProcessingJob, manifest_key: str, audio_manifest_key: str, download_key: str) -> None:
    if not attachment_requested(job):
        return

    with transaction.atomic():
        lesson = job.lesson.__class__.objects.select_for_update().get(pk=job.lesson_id)
        if not can_manage_content(job.created_by) or not lesson.can_edit:
            raise MediaProcessingError(_("The lesson is no longer editable."), "attachment_not_editable")
        try:
            links = json.loads(lesson.links or "[]")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MediaProcessingError(_("The lesson media links are invalid."), "attachment_invalid_links") from exc
        if not isinstance(links, list):
            raise MediaProcessingError(_("The lesson media links are invalid."), "attachment_invalid_links")
        existing_ids = {link.get("id") for link in links if isinstance(link, dict) and link.get("id")}
        existing_downloads = {
            link.get("download_id")
            for link in links
            if isinstance(link, dict) and link.get("download_id")
        }
        generated = []
        if manifest_key:
            generated.append({
                "file_type": "video" if job.source_kind == "video" else "audio",
                "name": job.output_base_name,
                "id": manifest_key,
                "part_id": job.part_id,
            })
        if audio_manifest_key:
            generated.append({
                "file_type": "audio",
                "name": f"{job.output_base_name}_audio",
                "id": audio_manifest_key,
                "part_id": job.part_id,
                "download_id": download_key,
            })
        if not generated and job.source_kind == "document":
            generated.append({
                "file_type": "book",
                "name": job.output_base_name,
                "id": _destination_key(job.requested_folder, f"{job.output_base_name}.pdf"),
                "part_id": job.part_id,
            })
        if any(
            link["id"] in existing_ids
            or (link.get("download_id") and link["download_id"] in existing_downloads)
            for link in generated
        ):
            raise MediaProcessingError(_("The lesson already contains one of these media files."), "attachment_collision")
        if is_automation_job(job):
            # Automation part ids are zero-padded, so inserting each finished
            # file in part order keeps the lecture parts stable no matter which
            # upload finishes first.
            position = 0
            for index, link in enumerate(links):
                existing_part = link.get("part_id", "") if isinstance(link, dict) else ""
                if existing_part.startswith(AUTOMATION_PART_PREFIX) and existing_part > job.part_id:
                    break
                position = index + 1
            links[position:position] = generated
        else:
            links.extend(generated)
        lesson.links = json.dumps(links, ensure_ascii=False)
        lesson.save(update_fields=["links", "updated_date"])


@transaction.atomic
def claim_attachment_retry(public_id: Any) -> MediaProcessingJob:
    """Mark a successful job's failed lesson attachment as pending retry."""
    job = MediaProcessingJob.objects.select_for_update().get(public_id=public_id)
    if job.status != MediaProcessingStatus.SUCCEEDED or job.attachment_status != MediaAttachmentStatus.FAILED:
        raise ValidationError(_("Only successful jobs with a failed lesson attachment can be retried."))
    job.attachment_status = MediaAttachmentStatus.PENDING
    job.error_code = ""
    job.error_message = ""
    job.save(update_fields=["attachment_status", "error_code", "error_message"])
    return job


@transaction.atomic
def finish_attachment_retry(public_id: Any, *, error: Exception | None = None) -> MediaProcessingJob:
    """Persist the terminal result of an attachment-only retry."""
    job = MediaProcessingJob.objects.select_for_update().get(public_id=public_id)
    if error is None:
        job.attachment_status = MediaAttachmentStatus.ATTACHED
        if job.error_code == "attachment_failed":
            job.error_code = ""
            job.error_message = ""
    else:
        job.attachment_status = MediaAttachmentStatus.FAILED
        job.error_code = "attachment_failed"
        job.error_message = str(error)[:MAX_ERROR_MESSAGE_LENGTH]
    job.save(update_fields=["attachment_status", "error_code", "error_message"])
    return job


def run_media_job(job: MediaProcessingJob, *, progress_callback=None, heartbeat_callback=None) -> dict:
    """Process one claimed job and return verified output metadata."""
    created_keys: list[str] = []
    folder = validate_requested_folder(job.requested_folder)
    work_root = getattr(settings, "MEDIA_WORK_DIR", "/var/lib/lms-media-processing")
    os.makedirs(work_root, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=f"media-{job.public_id}-", dir=work_root) as work_dir:
            source_path = os.path.join(work_dir, "source")
            verify_staging_object(job.source_key, expected_size=job.source_size)
            download_staging_object(job.source_key, source_path, expected_size=job.source_size)
            if heartbeat_callback:
                heartbeat_callback(MediaProcessingPhase.PROBE, 5)
            probe = probe_source(source_path, job.source_kind)
            if heartbeat_callback:
                heartbeat_callback(MediaProcessingPhase.ENCODE, 10)
            if job.source_kind == "document":
                output_path = os.path.join(work_dir, f"{job.output_base_name}.pdf")
                shutil.copyfile(source_path, output_path)
                key = _destination_key(folder, f"{job.output_base_name}.pdf")
                if heartbeat_callback:
                    heartbeat_callback(MediaProcessingPhase.UPLOAD_OUTPUT, 70)
                upload_output_file(key, output_path, content_type="application/pdf")
                created_keys.append(key)
                manifest_key = ""
                audio_manifest_key = ""
                download_key = ""
            else:
                video_output_dir = os.path.join(work_dir, "video-hls")
                audio_output_dir = os.path.join(work_dir, "audio-hls")
                manifest_path, video_names = _encode_hls_with_size_cap(
                    source_path, video_output_dir, job.output_base_name, "v:0",
                    probe.duration, progress_callback, probe=probe,
                ) if probe.has_video else (None, [])
                if manifest_path:
                    if heartbeat_callback:
                        heartbeat_callback(MediaProcessingPhase.UPLOAD_OUTPUT, 70)
                    manifest_key, _ = _upload_hls_outputs(
                        manifest_path, video_names, video_output_dir, folder,
                        job.output_base_name, "Video Segments", created_keys,
                        (lambda value: heartbeat_callback(MediaProcessingPhase.UPLOAD_OUTPUT, value)) if heartbeat_callback else None,
                    )
                else:
                    manifest_key = ""
                audio_manifest_key = ""
                download_key = ""
                if probe.has_audio:
                    audio_path = _prepare_audio(source_path, probe, work_dir, probe.duration, progress_callback)
                    audio_playlist, audio_names = _encode_hls_with_size_cap(
                        audio_path, audio_output_dir, f"{job.output_base_name}_audio",
                        "a:0", probe.duration, progress_callback, probe=probe,
                    )
                    if heartbeat_callback:
                        heartbeat_callback(MediaProcessingPhase.UPLOAD_OUTPUT, 70)
                    audio_manifest_key, _ = _upload_hls_outputs(
                        audio_playlist, audio_names, audio_output_dir, folder,
                        f"{job.output_base_name}_audio", "Audio Segments", created_keys,
                        (lambda value: heartbeat_callback(MediaProcessingPhase.UPLOAD_OUTPUT, value)) if heartbeat_callback else None,
                    )
                    mp3_path = _make_mp3(audio_path, source_path, job.source_kind, work_dir, probe.duration, progress_callback)
                    download_key = _destination_key(folder, "Downloadable Files", f"{job.output_base_name}.mp3")
                    upload_output_file(download_key, mp3_path, content_type="audio/mpeg")
                    created_keys.append(download_key)
            if heartbeat_callback:
                heartbeat_callback(MediaProcessingPhase.VERIFY, 95)
            return {
                "output_keys": created_keys,
                "manifest_key": manifest_key,
                "audio_manifest_key": audio_manifest_key,
                "download_key": download_key,
            }
    except Exception:
        for key in created_keys:
            try:
                delete_object_exact(key)
            except Exception:
                pass
        raise


__all__ = [
    "ALLOWED_TRANSITIONS",
    "AUTOMATION_PART_PREFIX",
    "MediaProcessingPhase",
    "MediaProcessingStatus",
    "NON_RETRYABLE_FAILURE_CODES",
    "PUBLICATION_ERROR_CODES",
    "PUBLICATION_FAILED_ERROR",
    "PUBLICATION_PENDING_ERROR",
    "PUBLICATION_PENDING_MESSAGE",
    "attachment_requested",
    "claim_queued_job",
    "claim_attachment_retry",
    "finish_attachment_retry",
    "finish_lesson_publication",
    "initialize_deadlines",
    "is_automation_job",
    "is_retryable_failed_job",
    "is_retryable_media_failure",
    "job_is_visible_to",
    "mark_publication_failed",
    "media_limits",
    "publish_attached_lesson",
    "publish_automation_lesson",
    "queue_verified_job",
    "requeue_stalled_job",
    "record_job_failure",
    "safe_output_base_name",
    "schedule_attachment_retry_after_commit",
    "schedule_media_job_after_commit",
    "source_kind_for_filename",
    "transition_job",
    "validate_browser_part_id",
    "validate_part_id",
    "validate_requested_folder",
    "validate_source_descriptor",
]
