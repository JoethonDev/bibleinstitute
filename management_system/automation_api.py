"""Small X-Key API for driving the existing one-file media pipeline."""

from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import uuid
from functools import wraps
from typing import Any, Callable

import redis
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .media_processing import (
    PUBLICATION_ERROR_CODES,
    MediaProcessingPhase,
    MediaProcessingStatus,
    MediaProcessingError,
    claim_attachment_retry,
    is_automation_job,
    publish_automation_lesson,
    queue_verified_job,
    safe_output_base_name,
    schedule_attachment_retry_after_commit,
    schedule_media_job_after_commit,
    validate_requested_folder,
    validate_source_descriptor,
)
from .media_storage import (
    ERR_STAGING_ETAG_MISMATCH,
    ERR_STAGING_FILENAME,
    ERR_STAGING_INCOMPLETE,
    ERR_STAGING_KEY,
    ERR_STAGING_MISSING,
    ERR_STAGING_SIZE_MISMATCH,
    MediaStorageError,
    build_staging_key,
    content_type_for_key,
    create_staging_upload_url,
)
from .models import (
    AcademicYearLevelMeeting,
    Lesson,
    MediaAttachmentStatus,
    MediaProcessingJob,
    PublicationStatus,
    User,
)
from .utils.decorators import can_manage_content


MAX_JSON_BYTES = 64 * 1024
MAX_TEXT_LENGTH = 255
NEXT_POLL = "poll"
NEXT_RETRY = "retry"
NEXT_RETRY_ATTACHMENT = "retry_attachment"
NEXT_PUBLISH = "publish"
NEXT_DELETE = "delete_local_source"
NEXT_OPERATOR = "operator_review"
AUTH_FAILURE_WINDOW_SECONDS = 60
AUTH_FAILURE_LIMIT = 20
logger = logging.getLogger(__name__)


class AutomationApiError(Exception):
    def __init__(self, error_code: str, status: int):
        super().__init__(error_code)
        self.error_code = error_code
        self.status = status


def _error(error_code: str, status: int) -> JsonResponse:
    return JsonResponse({"error_code": error_code}, status=status)


def _failed_auth_is_rate_limited(request) -> bool:
    """Throttle failed key attempts without trusting forwarded client IPs."""
    ip_address = request.META.get("REMOTE_ADDR", "unknown")
    cache_key = f"automation-auth-failure:{ip_address}"
    try:
        if cache.add(cache_key, 1, timeout=AUTH_FAILURE_WINDOW_SECONDS):
            count = 1
        else:
            count = cache.incr(cache_key)
        return count > AUTH_FAILURE_LIMIT
    except Exception:
        return False


def _automation_authentication_error(request) -> JsonResponse | None:
    configured = str(getattr(settings, "AUTOMATION_API_KEY", "") or "")
    if not configured or len(configured) < 32:
        return _error("automation_not_configured", 503)
    supplied = request.META.get("HTTP_X_KEY")
    valid = (
        isinstance(supplied, str)
        and bool(supplied)
        and "," not in supplied
        and hmac.compare_digest(supplied, configured)
    )
    if not valid:
        if _failed_auth_is_rate_limited(request):
            return _error("rate_limited", 429)
        return _error("authentication_required", 401)
    if not settings.DEBUG and not request.is_secure():
        return _error("https_required", 400)
    return None


def automation_key_required(view: Callable) -> Callable:
    """Authenticate only the exact configured X-Key header before the view."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        authentication_error = _automation_authentication_error(request)
        if authentication_error is not None:
            return authentication_error
        return view(request, *args, **kwargs)

    return wrapper


def automation_endpoint(method: str) -> Callable:
    """Apply the machine-only auth, CSRF, method, and JSON error boundary."""
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def method_guard(request, *args, **kwargs):
            if request.method != method:
                return _error("method_not_allowed", 405)
            return view(request, *args, **kwargs)

        authenticated_view = automation_key_required(method_guard)

        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            try:
                return authenticated_view(request, *args, **kwargs)
            except Exception as exc:
                logger.error(
                    "automation_api_failed",
                    extra={
                        "automation_method": request.method,
                        "automation_path": request.path,
                        "exception_type": type(exc).__name__,
                    },
                )
                return _error("operator_review", 503)

        return wrapper

    return decorator


def _parse_json(request, *, allow_empty: bool = False) -> dict[str, Any]:
    if len(request.body or b"") > MAX_JSON_BYTES:
        raise AutomationApiError("invalid_lecture", 400)
    if not request.body and allow_empty:
        return {}
    try:
        payload = json.loads(request.body or b"")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AutomationApiError("invalid_lecture", 400) from exc
    if not isinstance(payload, dict):
        raise AutomationApiError("invalid_lecture", 400)
    return payload


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_LENGTH:
        raise AutomationApiError("invalid_lecture", 400)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AutomationApiError("invalid_lecture", 400)
    return value.strip()


def _parse_date(value: Any) -> dt.date:
    if (
        not isinstance(value, str)
        or len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
    ):
        raise AutomationApiError("invalid_date", 400)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AutomationApiError("invalid_date", 400) from exc


def _event_values(meeting: AcademicYearLevelMeeting) -> dict[str, str]:
    scope = meeting.academic_year_level
    return {
        "course": meeting.course_offering.course.name,
        "academic_year": scope.academic_year.name,
        "level": scope.level.display_name,
    }


def _meetings_for_date(meeting_date: dt.date):
    return AcademicYearLevelMeeting.objects.select_related(
        "academic_year_level__academic_year",
        "academic_year_level__level",
        "course_offering__course",
    ).filter(
        meeting_date=meeting_date,
        course_offering__academic_year_level_id=F("academic_year_level_id"),
    ).order_by(
        "course_offering__course__name",
        "academic_year_level__academic_year__name",
        "academic_year_level__level__ordering",
        "pk",
    )


def _resolve_event(payload: dict[str, Any]) -> AcademicYearLevelMeeting:
    meeting_date = _parse_date(payload.get("date"))
    requested = {
        "course": _text(payload, "course"),
        "academic_year": _text(payload, "academic_year"),
        "level": _text(payload, "level"),
    }
    level_filter = Q(academic_year_level__level__name_en=requested["level"])
    level_filter |= Q(
        academic_year_level__level__name_en="",
        academic_year_level__level__name_ar=requested["level"],
    )
    if requested["level"].isdigit():
        level_filter |= Q(
            academic_year_level__level__name_en="",
            academic_year_level__level__name_ar="",
            academic_year_level__level__ordering=int(requested["level"]),
        )
    matches = list(
        _meetings_for_date(meeting_date)
        .filter(
            course_offering__course__name=requested["course"],
            academic_year_level__academic_year__name=requested["academic_year"],
        )
        .filter(level_filter)[:2]
    )
    if not matches:
        raise AutomationApiError("calendar_event_not_found", 404)
    if len(matches) != 1:
        raise AutomationApiError("calendar_event_ambiguous", 409)
    return matches[0]


def _canonical_folder(meeting: AcademicYearLevelMeeting) -> str:
    values = _event_values(meeting)
    year = values["academic_year"].replace("/", "-")
    try:
        return validate_requested_folder(
            f'{values["level"]}/Academic Year - {year}/{values["course"]}/Lectures'
        )
    except ValidationError as exc:
        raise AutomationApiError(NEXT_OPERATOR, 409) from exc


def _automation_user() -> User:
    user = User.objects.select_related("role").filter(
        is_active=True,
        role__role="admin",
    ).order_by("pk").first()
    if user is None:
        user = User.objects.select_related("role").filter(
            pk=2,
            is_active=True,
        ).first()
    if user is None or not can_manage_content(user):
        raise AutomationApiError("automation_not_configured", 503)
    return user


def _safe_storage_error(exc: MediaStorageError, *, start: bool = False) -> AutomationApiError:
    if exc.code in {ERR_STAGING_MISSING, ERR_STAGING_INCOMPLETE}:
        return AutomationApiError("staging_object_missing", 400)
    if exc.code == ERR_STAGING_SIZE_MISMATCH:
        return AutomationApiError("staging_size_mismatch", 400)
    if exc.code == ERR_STAGING_ETAG_MISMATCH:
        return AutomationApiError("operator_review", 409)
    return AutomationApiError("operator_review" if start else "processing_failed", 409)


def _live_progress(public_id: uuid.UUID) -> dict[str, str]:
    try:
        client = redis.Redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        return client.hgetall(f"media:job:{public_id}")
    except Exception:
        return {}


def _status_contract(lesson: Lesson, job: MediaProcessingJob, live: dict[str, str] | None = None) -> dict[str, Any]:
    live = live or {}
    status = live.get("status", job.status)
    phase = live.get("phase", job.phase)
    try:
        progress = max(0, min(100, int(live.get("progress", job.progress))))
    except (TypeError, ValueError):
        progress = int(job.progress)

    if job.status == MediaProcessingStatus.SUCCEEDED:
        if job.attachment_status == MediaAttachmentStatus.FAILED:
            return {
                "status": "attachment_failed",
                "phase": phase,
                "progress": progress,
                "next_action": NEXT_RETRY_ATTACHMENT,
                "safe_to_delete_local": False,
            }
        if job.attachment_status != MediaAttachmentStatus.ATTACHED:
            return {
                "status": status,
                "phase": phase,
                "progress": progress,
                "next_action": NEXT_POLL,
                "safe_to_delete_local": False,
            }
        if lesson.status == PublicationStatus.PUBLISHED and job.error_code in PUBLICATION_ERROR_CODES:
            return {
                "status": "publish_failed",
                "phase": "publication",
                "progress": 100,
                "next_action": NEXT_PUBLISH,
                "safe_to_delete_local": False,
            }
        if lesson.status == PublicationStatus.PUBLISHED and job.staging_deleted_at:
            return {
                "status": "published",
                "phase": MediaProcessingPhase.COMPLETE,
                "progress": 100,
                "next_action": NEXT_DELETE,
                "safe_to_delete_local": True,
            }
        if job.error_code in PUBLICATION_ERROR_CODES and lesson.status == PublicationStatus.DRAFT:
            return {
                "status": "publish_failed",
                "phase": "publication",
                "progress": 100,
                "next_action": NEXT_PUBLISH,
                "safe_to_delete_local": False,
            }
        return {
            "status": status,
            "phase": phase,
            "progress": progress,
            "next_action": NEXT_OPERATOR,
            "safe_to_delete_local": False,
        }

    if job.status == MediaProcessingStatus.FAILED:
        if job.source_acknowledged_at and not job.staging_deleted_at:
            return {
                "status": status,
                "phase": phase,
                "progress": progress,
                "next_action": NEXT_RETRY,
                "safe_to_delete_local": False,
            }
        return {
            "status": status,
            "phase": phase,
            "progress": progress,
            "next_action": NEXT_OPERATOR,
            "safe_to_delete_local": False,
        }

    if job.status == MediaProcessingStatus.CANCELLED:
        next_action = NEXT_OPERATOR
    else:
        next_action = NEXT_POLL
    return {
        "status": status,
        "phase": phase,
        "progress": progress,
        "next_action": next_action,
        "safe_to_delete_local": False,
    }


def _automation_job(job_id: uuid.UUID) -> MediaProcessingJob:
    job = MediaProcessingJob.objects.filter(public_id=job_id).first()
    if job is None or not is_automation_job(job):
        raise AutomationApiError(NEXT_OPERATOR, 404)
    return job


def _lesson_job(lesson_id: int) -> tuple[Lesson, MediaProcessingJob]:
    lesson = Lesson.objects.select_related("course_offering").filter(pk=lesson_id).first()
    if lesson is None:
        raise AutomationApiError(NEXT_OPERATOR, 404)
    jobs = list(MediaProcessingJob.objects.filter(lesson_id=lesson.pk).order_by("pk")[:2])
    if len(jobs) != 1 or not is_automation_job(jobs[0]):
        raise AutomationApiError(NEXT_OPERATOR, 409)
    return lesson, jobs[0]


@automation_endpoint("GET")
def calendar_events(request):
    try:
        selected_date = _parse_date(request.GET.get("date")) if request.GET.get("date") else timezone.localdate()
        return JsonResponse([_event_values(meeting) for meeting in _meetings_for_date(selected_date)], safe=False)
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)


@automation_endpoint("POST")
def lecture_upload(request):
    try:
        payload = _parse_json(request)
        if set(payload) - {
            "date", "course", "academic_year", "level", "lecture_name",
            "filename", "size",
        }:
            raise AutomationApiError("invalid_lecture", 400)
        meeting = _resolve_event(payload)
        lecture_name = _text(payload, "lecture_name")
        filename, source_kind, source_size = validate_source_descriptor(payload.get("filename"), payload.get("size"))
        public_id = uuid.uuid4()
        source_key = build_staging_key(str(public_id), filename)
        folder = _canonical_folder(meeting)
        user = _automation_user()
        authorization = create_staging_upload_url(
            source_key,
            content_type=content_type_for_key(filename),
            expires_in=3600,
        )
        with transaction.atomic():
            lesson = Lesson.objects.create(
                name=lecture_name,
                description=None,
                links="[]",
                course_offering=meeting.course_offering,
                status=PublicationStatus.DRAFT,
            )
            job = MediaProcessingJob.objects.create(
                public_id=public_id,
                created_by=user,
                requested_folder=folder,
                original_filename=filename,
                output_base_name=safe_output_base_name(filename),
                source_kind=source_kind,
                source_key=source_key,
                source_size=source_size,
                lesson=lesson,
                # Existing part_id is the durable per-lesson media identity;
                # this namespace also lets the worker distinguish the new
                # automation-owned draft from the browser's existing-lesson
                # upload flow without a schema change.
                part_id=f"automation-{uuid.uuid4().hex[:32]}",
                attachment_status=MediaAttachmentStatus.PENDING,
                upload_ack_deadline_at=timezone.now() + dt.timedelta(minutes=30),
                staging_expires_at=timezone.now() + dt.timedelta(hours=24),
            )
        return JsonResponse({
            "lesson_id": lesson.pk,
            "job_id": str(job.public_id),
            "upload_url": authorization["url"],
            "content_type": authorization["headers"]["Content-Type"],
        }, status=201)
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)
    except MediaStorageError as exc:
        if exc.code in {ERR_STAGING_FILENAME, ERR_STAGING_KEY}:
            return _error("invalid_lecture", 400)
        return _error("operator_review", 409)
    except (ValidationError, TypeError, ValueError, OverflowError):
        return _error("invalid_lecture", 400)


@automation_endpoint("POST")
def media_start(request, job_id: uuid.UUID):
    try:
        payload = _parse_json(request, allow_empty=True)
        if set(payload) - {"size", "etag"}:
            raise AutomationApiError("invalid_lecture", 400)
        if "size" in payload and (
            not isinstance(payload["size"], int)
            or isinstance(payload["size"], bool)
            or payload["size"] <= 0
        ):
            raise AutomationApiError("invalid_lecture", 400)
        etag = payload.get("etag")
        if etag is not None and (
            not isinstance(etag, str)
            or not etag
            or len(etag) > 255
            or any(ord(char) < 32 or ord(char) == 127 for char in etag)
        ):
            raise AutomationApiError("invalid_lecture", 400)
        job = _automation_job(job_id)
        if job.status != MediaProcessingStatus.AWAITING_UPLOAD:
            if job.status in {
                MediaProcessingStatus.QUEUED,
                MediaProcessingStatus.PROCESSING,
                MediaProcessingStatus.UPLOADING,
                MediaProcessingStatus.VERIFYING,
                MediaProcessingStatus.SUCCEEDED,
            }:
                return JsonResponse({"status": job.status}, status=202)
            raise AutomationApiError("processing_failed", 409)
        if "size" in payload and payload["size"] != job.source_size:
            raise AutomationApiError("staging_size_mismatch", 400)
        try:
            queued, transitioned = queue_verified_job(
                job.public_id,
                expected_etag=etag,
                acknowledged_at=timezone.now(),
            )
        except MediaStorageError as exc:
            raise _safe_storage_error(exc, start=True) from exc
        if not transitioned:
            return JsonResponse({"status": queued.status}, status=202)
        schedule_media_job_after_commit(queued.public_id)
        return JsonResponse({"status": "queued"}, status=202)
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)
    except (ValidationError, MediaStorageError):
        return _error("processing_failed", 409)


@automation_endpoint("GET")
def lecture_status(request, lesson_id: int):
    try:
        lesson, job = _lesson_job(lesson_id)
        return JsonResponse(_status_contract(lesson, job, _live_progress(job.public_id)))
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)


@automation_endpoint("POST")
def media_retry(request, job_id: uuid.UUID):
    try:
        payload = _parse_json(request, allow_empty=True)
        if payload:
            raise AutomationApiError("invalid_lecture", 400)
        job = _automation_job(job_id)
        if job.status == MediaProcessingStatus.SUCCEEDED and job.attachment_status == MediaAttachmentStatus.FAILED:
            try:
                with transaction.atomic():
                    pending = claim_attachment_retry(job.public_id)
                    schedule_attachment_retry_after_commit(pending.public_id)
            except (ValidationError, MediaProcessingJob.DoesNotExist) as exc:
                raise AutomationApiError("attachment_failed", 409) from exc
            return JsonResponse({"status": "queued"}, status=202)
        if job.status != MediaProcessingStatus.FAILED or not job.source_acknowledged_at or job.staging_deleted_at:
            raise AutomationApiError("media_not_ready", 409)
        try:
            queued, transitioned = queue_verified_job(
                job.public_id,
                acknowledged_at=job.source_acknowledged_at,
            )
        except MediaStorageError as exc:
            raise _safe_storage_error(exc) from exc
        if not transitioned:
            return JsonResponse({"status": queued.status}, status=202)
        schedule_media_job_after_commit(queued.public_id)
        return JsonResponse({"status": "queued"}, status=202)
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)
    except (ValidationError, MediaStorageError):
        return _error("processing_failed", 409)


@automation_endpoint("POST")
def lecture_publish(request, lesson_id: int):
    try:
        payload = _parse_json(request, allow_empty=True)
        if payload:
            raise AutomationApiError("invalid_lecture", 400)
        lesson, job = _lesson_job(lesson_id)
        current = _status_contract(lesson, job)
        if current["status"] == "published" and current["safe_to_delete_local"]:
            return JsonResponse(
                {
                    "status": "published",
                    "next_action": NEXT_DELETE,
                    "safe_to_delete_local": True,
                }
            )
        if current["next_action"] != NEXT_PUBLISH:
            raise AutomationApiError("media_not_ready", 409)
        try:
            publish_automation_lesson(job.pk)
        except MediaProcessingError as exc:
            raise AutomationApiError("media_not_ready", 409) from exc
        except Exception as exc:
            raise AutomationApiError("publish_failed", 409) from exc
        return JsonResponse(
            {
                "status": "published",
                "next_action": NEXT_DELETE,
                "safe_to_delete_local": True,
            }
        )
    except AutomationApiError as exc:
        return _error(exc.error_code, exc.status)


__all__ = [
    "automation_key_required",
    "calendar_events",
    "lecture_publish",
    "lecture_status",
    "lecture_upload",
    "media_retry",
    "media_start",
]
