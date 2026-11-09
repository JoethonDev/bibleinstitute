"""Durable management-operation ledger: creation, lease claim and bounded status.

Owning domain modules still validate, authorize and execute every operation.
This module only owns the shared status/lease/idempotency contract so a worker
restart, duplicate delivery or double submit cannot replay effects.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from typing import Any

from django.conf import settings
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext as _
from django.core.cache import cache
from celery import current_app

from .models import AdminOperationJob, User
import logging
from django.db import connections
import hashlib
from .utils.storage_operations import (
    delete_from_bucket,
    download_from_bucket,
    get_r2_client,
    upload_to_bucket,
)
from .xlsx_export import XLSX_CONTENT_TYPE


LEASE_SECONDS = 120
MAX_ERROR_LENGTH = 2000
MAX_ROW_ERRORS = 100
# Private job inputs (raw imports) live under a private prefix with a bounded
# byte budget and a hard expiry so no plaintext credential outlives the run.
# Generated export files use a sibling prefix and the same expiry/cleanup.
ADMIN_ARTIFACT_ROOT = "admin-operations/"
ARTIFACT_PREFIX = f"{ADMIN_ARTIFACT_ROOT}inputs/"
EXPORT_ARTIFACT_PREFIX = f"{ADMIN_ARTIFACT_ROOT}exports/"
ARTIFACT_MAX_BYTES = 2 * 1024 * 1024
ARTIFACT_TTL_HOURS = 24
ARTIFACT_CLEANUP_LIMIT = 100
ARTIFACT_CLEANUP_LOCK_SECONDS = 600
ARTIFACT_CLEANUP_CACHE_PREFIX = "admin-operation-artifact-cleanup:v1"
TERMINAL_STATUSES = frozenset({
    AdminOperationJob.Status.COMPLETED,
    AdminOperationJob.Status.PARTIAL,
    AdminOperationJob.Status.FAILED,
})
DOWNLOADABLE_EXPORT_KINDS = frozenset({
    AdminOperationJob.Kind.EVALUATION_EXPORT,
    AdminOperationJob.Kind.GRADES_REPORT_EXPORT,
    AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT,
    AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT,
})


class AdminOperationError(Exception):
    """Expected domain failure raised by an executor with a translated message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


ADMIN_OPERATION_TASK_NAME = "management_system.admin_operation_tasks.run_admin_operation"


def enqueue_admin_operation(job_id: str, *, queue: str = "admin") -> None:
    """Dispatch a durable admin job without importing its worker executors."""
    current_app.send_task(ADMIN_OPERATION_TASK_NAME, args=[str(job_id)], queue=queue)


def _validate_kind(kind: str) -> str:
    if kind not in AdminOperationJob.Kind.values:
        raise AdminOperationError("invalid_kind", _("Invalid management operation."))
    return kind


CONTENT_OPERATION_KINDS = frozenset({
        AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT,
        AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT,
        AdminOperationJob.Kind.EVALUATION_EXPORT,
        AdminOperationJob.Kind.GRADES_REPORT_EXPORT,
        AdminOperationJob.Kind.STORAGE_RENAME,
        AdminOperationJob.Kind.STORAGE_USAGE_REFRESH,
})


def _operation_roles(job) -> tuple[str, ...]:
    content_operation = job.kind in CONTENT_OPERATION_KINDS or (
        job.kind == AdminOperationJob.Kind.COURSE_COPY
        and (job.input or {}).get("mode") == "duplicate"
    )
    return ("admin", "staff") if content_operation else ("admin",)


def require_admin_operation_actor(job: AdminOperationJob) -> User:
    actor = User.objects.select_related("role").filter(
        pk=job.actor_id, is_active=True, role__role__in=_operation_roles(job),
    ).first()
    if actor is None:
        raise AdminOperationError("not_permitted", _("You are no longer allowed to run this operation."))
    job.actor = actor
    return actor


def _matching_operation(job, payload, scope, offering):
    expected = dict(payload)
    existing = dict(job.input or {})
    if job.kind == AdminOperationJob.Kind.LEGACY_USER_IMPORT:
        # Upload attempts have distinct private keys but the digest binds bytes.
        expected.pop("artifact_key", None)
        existing.pop("artifact_key", None)
        expected.pop("filename", None)
        existing.pop("filename", None)
    if (existing != expected or job.academic_year_level_id != getattr(scope, "pk", None)
            or job.course_offering_id != getattr(offering, "pk", None)):
        raise ValidationError(_("Another operation with different input is already running. Wait for it to finish."))
    return job


def create_admin_operation(
    *,
    actor,
    kind: str,
    input: dict[str, Any] | None = None,
    conflict_key: str = "",
    scope=None,
    offering=None,
    language: str = "ar",
    total: int = 0,
) -> AdminOperationJob:
    """Create the durable job, or return the matching active job (idempotent)."""

    kind = _validate_kind(kind)
    payload = input or {}
    conflict_key = (conflict_key or "").strip()[:120]
    with transaction.atomic():
        if conflict_key:
            existing = (
                AdminOperationJob.objects.select_for_update()
                .filter(
                    actor=actor,
                    kind=kind,
                    conflict_key=conflict_key,
                    status__in=[AdminOperationJob.Status.QUEUED, AdminOperationJob.Status.RUNNING, AdminOperationJob.Status.WAITING],
                )
                .first()
            )
            if existing is not None:
                return _matching_operation(existing, payload, scope, offering)
        try:
            # The savepoint must roll back before querying a competing insert.
            with transaction.atomic():
                return AdminOperationJob.objects.create(
                    actor=actor,
                    kind=kind,
                    input=payload,
                    conflict_key=conflict_key,
                    academic_year_level=scope,
                    course_offering=offering,
                    language=(language or "ar")[:8],
                    total=max(0, int(total)),
                )
        except IntegrityError:
            # A concurrent submit won the conflict constraint; reuse its job.
            existing = AdminOperationJob.objects.filter(
                actor=actor,
                kind=kind,
                conflict_key=conflict_key,
                status__in=[AdminOperationJob.Status.QUEUED, AdminOperationJob.Status.RUNNING, AdminOperationJob.Status.WAITING],
            ).first()
            if existing is None:
                raise
            return _matching_operation(existing, payload, scope, offering)


def claim_admin_operation(
    job_id,
    token: str | uuid.UUID,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> tuple[AdminOperationJob | None, bool]:
    """Claim a queued job or take over an expired lease; reject live duplicates."""

    token = uuid.UUID(str(token))
    now = timezone.now()
    with transaction.atomic():
        job = AdminOperationJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status in TERMINAL_STATUSES or job.status == AdminOperationJob.Status.WAITING:
            return job, False
        claimable = (
            job.status == AdminOperationJob.Status.QUEUED
            or (job.lease_until is not None and job.lease_until <= now)
            or job.lease_token == token
        )
        if not claimable:
            return job, False
        job.status = AdminOperationJob.Status.RUNNING
        job.lease_token = token
        job.lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
        job.started_at = job.started_at or now
        job.save(update_fields=["status", "lease_token", "lease_until", "started_at", "updated_at"])
        return job, True


def renew_admin_operation_lease(
    job_id,
    token: str | uuid.UUID,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> bool:
    """Extend a live lease; a stale worker token cannot renew after takeover."""

    now = timezone.now()
    updated = (
        AdminOperationJob.objects.filter(
            pk=job_id,
            lease_token=uuid.UUID(str(token)),
            status=AdminOperationJob.Status.RUNNING,
        ).update(lease_until=now + timedelta(seconds=max(30, int(lease_seconds))), updated_at=now)
    )
    return bool(updated)


def record_admin_operation_progress(
    job_id,
    token: str | uuid.UUID,
    *,
    processed: int,
    succeeded: int,
    failed: int,
    skipped: int,
    total: int | None = None,
    cursor: dict | None = None,
    result: dict | None = None,
) -> bool:
    """Commit counters/cursor together with the caller's batch transaction."""

    if cursor is not None and not isinstance(cursor, dict):
        raise AdminOperationError("invalid_cursor", _("Invalid management operation cursor."))
    now = timezone.now()
    values = {
        "processed": max(0, int(processed)),
        "succeeded": max(0, int(succeeded)),
        "failed": max(0, int(failed)),
        "skipped": max(0, int(skipped)),
        "updated_at": now,
        "lease_until": now + timedelta(seconds=LEASE_SECONDS),
    }
    if total is not None:
        values["total"] = max(0, int(total))
    if cursor is not None:
        values["cursor"] = cursor
    if result is not None:
        values["result"] = result
    updated = AdminOperationJob.objects.filter(
        pk=job_id,
        lease_token=uuid.UUID(str(token)),
        status=AdminOperationJob.Status.RUNNING,
        actor__is_active=True,
    ).filter(
        Q(actor__role__role="admin")
        | (Q(actor__role__role="staff") & (
            Q(kind__in=CONTENT_OPERATION_KINDS)
            | Q(kind=AdminOperationJob.Kind.COURSE_COPY, input__mode="duplicate")
        ))
    ).update(**values)
    return bool(updated)


@contextmanager
def admin_operation_heartbeat(job_id, token):
    """One lease heartbeat per running task, including long hashing/provider calls."""

    stopped = Event()

    def heartbeat():
        try:
            while not stopped.wait(max(1, LEASE_SECONDS // 3)):
                close_old_connections()
                try:
                    if not renew_admin_operation_lease(job_id, token):
                        return
                except Exception:
                    logging.getLogger(__name__).warning(
                        "admin operation heartbeat failed", extra={"job_id": str(job_id)}, exc_info=True,
                    )
                finally:
                    close_old_connections()
        finally:
            connections.close_all()

    thread = Thread(target=heartbeat, daemon=True, name="admin-operation-lease")
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def assert_admin_operation_progress(job_id, token: str | uuid.UUID, **kwargs) -> None:
    """Record batch progress and stop a stale/duplicate worker immediately.

    Raising inside the executor's batch transaction rolls the batch back, so a
    worker that lost its lease cannot commit effects after a takeover.
    """

    if not record_admin_operation_progress(job_id, token, **kwargs):
        raise AdminOperationError(
            "lease_lost",
            _("The operation was taken over by another worker."),
        )


def finish_admin_operation(
    job_id,
    token: str | uuid.UUID,
    *,
    status: str,
    error: str = "",
    result: dict | None = None,
) -> bool:
    """Finish the job from its live lease holder with a truthful terminal state."""

    if status not in TERMINAL_STATUSES:
        raise AdminOperationError("invalid_status", _("Invalid management operation status."))
    now = timezone.now()
    values = {
        "status": status,
        "lease_token": None,
        "lease_until": None,
        "finished_at": now,
        "updated_at": now,
        "error": (error or "")[:MAX_ERROR_LENGTH],
    }
    if result is not None:
        values["result"] = result
    updated = AdminOperationJob.objects.filter(
        pk=job_id,
        lease_token=uuid.UUID(str(token)),
        status=AdminOperationJob.Status.RUNNING,
    ).update(**values)
    return bool(updated)


def fail_admin_operation(job_id, token: str | uuid.UUID, message: str) -> bool:
    return finish_admin_operation(
        job_id,
        token,
        status=AdminOperationJob.Status.FAILED,
        error=message,
    )


def wait_for_notification_delivery(job_id, token: str | uuid.UUID, result: dict) -> bool:
    return bool(AdminOperationJob.objects.filter(
        pk=job_id, lease_token=uuid.UUID(str(token)), status=AdminOperationJob.Status.RUNNING,
        kind=AdminOperationJob.Kind.NOTIFICATION_REDELIVERY,
    ).update(status=AdminOperationJob.Status.WAITING, lease_token=None, lease_until=None,
             result=result, updated_at=timezone.now()))


def _artifact_client_bucket():
    """Return the shared private-bucket client, or ``(None, "")`` when absent."""


    bucket = (getattr(settings, "R2_BUCKET_NAME", "") or "").strip()
    if not bucket:
        return None, ""
    try:
        return get_r2_client(), bucket
    except Exception:  # noqa: BLE001 - provider/client construction boundary
        return None, ""


def _artifact_filename(filename: str, *, fallback: str = "input.csv") -> str:
    """Return a bounded, path-free filename component for the private key."""

    base = os.path.basename(str(filename or "")).strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]
    return safe or fallback


def _is_operation_artifact_key(value: str) -> bool:
    return str(value or "").startswith(ADMIN_ARTIFACT_ROOT)


def store_admin_operation_artifact(*, content: bytes, filename: str) -> str:
    """Upload one bounded plaintext job input to the private artifact prefix.

    The key is unguessable and worker-only; the caller records it on the job
    input so the executor can read it. Any provider failure raises a translated
    AdminOperationError; the caller must not enqueue work then.
    """


    if not isinstance(content, (bytes, bytearray)) or not content:
        raise AdminOperationError("invalid_artifact", _("The uploaded file is empty."))
    if len(content) > ARTIFACT_MAX_BYTES:
        raise AdminOperationError(
            "artifact_too_large",
            _("The uploaded file is too large."),
        )
    client, bucket = _artifact_client_bucket()
    if client is None:
        raise AdminOperationError(
            "artifact_storage_unavailable",
            _("Private import storage is not available. Try again later."),
        )
    key = f"{ARTIFACT_PREFIX}{uuid.uuid4().hex}/{_artifact_filename(filename)}"
    if not upload_to_bucket(
        client,
        bucket,
        io.BytesIO(bytes(content)),
        key,
        content_type="text/csv",
    ):
        raise AdminOperationError(
            "artifact_upload_failed",
            _("The private import file could not be stored. Try again later."),
        )
    return key


def read_admin_operation_artifact(job, *, expected_digest: str = "") -> bytes:
    """Read one bounded private input artifact, verifying the frozen digest."""



    key = str((job.input or {}).get("artifact_key") or "")
    if not key.startswith(ARTIFACT_PREFIX):
        raise AdminOperationError("artifact_missing", _("The import file is no longer available."))
    client, bucket = _artifact_client_bucket()
    if client is None:
        raise AdminOperationError(
            "artifact_storage_unavailable",
            _("Private import storage is not available. Try again later."),
        )
    stream = download_from_bucket(client, bucket, key)
    if stream is None:
        raise AdminOperationError("artifact_missing", _("The import file is no longer available."))
    content = stream.read(ARTIFACT_MAX_BYTES + 1)
    if len(content) > ARTIFACT_MAX_BYTES:
        raise AdminOperationError("artifact_too_large", _("The uploaded file is too large."))
    if expected_digest and hashlib.sha256(content).hexdigest() != expected_digest:
        raise AdminOperationError("artifact_mismatch", _("The import file changed after it was queued."))
    return content


def delete_admin_operation_artifact(job, key: str | None = None) -> bool:
    """Delete one consumed private job artifact and record cleanup failures."""



    logger = logging.getLogger(__name__)
    artifact_key = key or str((job.input or {}).get("artifact_key") or job.artifact_key or "")
    if not _is_operation_artifact_key(artifact_key):
        return True
    client, bucket = _artifact_client_bucket()
    if client is None:
        logger.warning(
            "admin operation artifact cleanup failed",
            extra={"event": "admin_artifact_cleanup_failed", "job_id": str(job.pk), "reason": "storage"},
        )
        return False
    if not delete_from_bucket(client, bucket, artifact_key):
        logger.warning(
            "admin operation artifact cleanup failed",
            extra={"event": "admin_artifact_cleanup_failed", "job_id": str(job.pk), "reason": "provider"},
        )
        return False
    # The private artifact is gone; drop its dangling reference so a later
    # status read or recovery never points at a missing file. An export key is
    # cleared only while it is still the recorded one, because a takeover may
    # already have stored a newer artifact for the same job.
    remaining = {k: v for k, v in (job.input or {}).items() if k != "artifact_key"}
    if artifact_key.startswith(EXPORT_ARTIFACT_PREFIX):
        AdminOperationJob.objects.filter(pk=job.pk, artifact_key=artifact_key).update(
            input=remaining, artifact_key=""
        )
    else:
        AdminOperationJob.objects.filter(pk=job.pk).update(input=remaining)
    return True


def store_admin_operation_export(
    *,
    job,
    fileobj,
    filename: str,
    token,
    content_type: str = "",
) -> str:
    """Upload one generated export stream to the private artifact prefix.

    The caller passes a disk-backed spooled file opened at position zero; the
    provider client streams it, so the export never has to be copied into
    request memory. Any failure raises a translated ``AdminOperationError``.
    """


    client, bucket = _artifact_client_bucket()
    if client is None:
        raise AdminOperationError(
            "artifact_storage_unavailable",
            _("Private export storage is not available. Try again later."),
        )
    # One key per lease attempt: a stale worker's cleanup can only remove its
    # own upload, never a successor's artifact for the same job.
    attempt = uuid.UUID(str(token)).hex
    key = (
        f"{EXPORT_ARTIFACT_PREFIX}{str(job.pk).replace('-', '')}/{attempt}/"
        f"{_artifact_filename(filename, fallback='export.bin')}"
    )
    if hasattr(fileobj, "seek"):
        fileobj.seek(0)
    if not upload_to_bucket(
        client,
        bucket,
        fileobj,
        key,
        content_type=content_type or XLSX_CONTENT_TYPE,
    ):
        raise AdminOperationError(
            "artifact_upload_failed",
            _("The export file could not be stored. Try again later."),
        )
    return key


def open_admin_operation_export(job) -> tuple[Any, int]:
    """Return one private export stream and its declared size for FileResponse."""

    key = str(job.artifact_key or "")
    if not key.startswith(EXPORT_ARTIFACT_PREFIX):
        raise AdminOperationError("artifact_missing", _("This export is no longer available."))
    client, bucket = _artifact_client_bucket()
    if client is None:
        raise AdminOperationError(
            "artifact_storage_unavailable",
            _("Private export storage is not available. Try again later."),
        )
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception:  # noqa: BLE001 - provider boundary; treated as missing
        raise AdminOperationError("artifact_missing", _("This export is no longer available."))
    stream = response.get("Body")
    if stream is None:
        raise AdminOperationError("artifact_missing", _("This export is no longer available."))
    return stream, int(response.get("ContentLength") or 0)


def finalize_admin_operation_export(
    job,
    token,
    workbook,
    filename: str,
    content_type: str = "",
) -> str:
    """Store a finished export file and record its key under the live lease.

    The upload is the external side effect; the artifact key is committed only
    while this worker still owns the lease. A takeover therefore cannot be
    overwritten by the stale worker, and a stale upload is cleaned up.
    """

    try:
        artifact_key = store_admin_operation_export(
            job=job,
            fileobj=workbook,
            filename=filename,
            token=token,
            content_type=content_type,
        )
    finally:
        workbook.close()
    recorded = AdminOperationJob.objects.filter(
        pk=job.pk,
        lease_token=token,
        status=AdminOperationJob.Status.RUNNING,
    ).update(artifact_key=artifact_key)
    if not recorded:
        delete_admin_operation_artifact(job, artifact_key)
        raise AdminOperationError(
            "lease_lost",
            _("The operation was taken over by another worker."),
        )
    return artifact_key


def purge_expired_admin_operation_artifacts(*, limit: int = ARTIFACT_CLEANUP_LIMIT) -> int:
    """Delete expired private artifacts from bounded, resumable provider pages.

    The cache cursor advances over the provider's lexicographic listing, so a
    prefix larger than one page is eventually covered. Active jobs keep their
    input files until execution no longer depends on them. A failed page is
    retried from its current cursor on the next scheduled run.
    """


    logger = logging.getLogger(__name__)
    client, bucket = _artifact_client_bucket()
    if client is None:
        return 0
    try:
        if not cache.add(
            f"{ARTIFACT_CLEANUP_CACHE_PREFIX}:lock",
            uuid.uuid4().hex,
            timeout=ARTIFACT_CLEANUP_LOCK_SECONDS,
        ):
            return 0
    except Exception:  # noqa: BLE001 - cleanup coordination is best-effort
        logger.warning("admin artifact cleanup lock unavailable", exc_info=True)
        return 0

    cutoff = timezone.now() - timedelta(hours=ARTIFACT_TTL_HOURS)
    total_limit = min(ARTIFACT_CLEANUP_LIMIT, max(1, int(limit)))
    prefix_budget = [total_limit // 2, total_limit // 2]
    if total_limit % 2:
        priority_key = f"{ARTIFACT_CLEANUP_CACHE_PREFIX}:priority"
        try:
            priority = int(cache.get(priority_key) or 0) % 2
            cache.set(priority_key, 1 - priority, timeout=None)
            prefix_budget[priority] += 1
        except Exception:  # noqa: BLE001 - the default order remains safe
            prefix_budget[0] += 1
    deleted = 0
    for prefix, page_budget in zip((ARTIFACT_PREFIX, EXPORT_ARTIFACT_PREFIX), prefix_budget):
        if page_budget <= 0:
            continue
        cursor_key = f"{ARTIFACT_CLEANUP_CACHE_PREFIX}:{prefix.split('/')[-2]}"
        try:
            continuation_token = cache.get(cursor_key)
            page_options = {
                "Bucket": bucket,
                "Prefix": prefix,
                "MaxKeys": min(1000, page_budget),
            }
            if continuation_token:
                page_options["ContinuationToken"] = continuation_token
            page = client.list_objects_v2(**page_options)
        except Exception:  # noqa: BLE001 - provider boundary; retried next run
            logger.warning("admin artifact cleanup listing failed", extra={"prefix": prefix}, exc_info=True)
            continue

        entries = page.get("Contents", [])
        expired_entries = []
        for entry in entries:
            entry_key = entry.get("Key") or ""
            if not entry_key.startswith(prefix):
                continue
            modified = entry.get("LastModified")
            if modified is not None and modified < cutoff:
                expired_entries.append(entry)

        active_artifact_keys = set()
        if expired_entries:
            expired_keys = [entry["Key"] for entry in expired_entries]
            active_jobs = AdminOperationJob.objects.filter(
                status__in=[
                    AdminOperationJob.Status.QUEUED,
                    AdminOperationJob.Status.RUNNING,
                    AdminOperationJob.Status.WAITING,
                ],
            )
            if prefix == ARTIFACT_PREFIX:
                active_artifact_keys = set(
                    active_jobs.filter(
                        input__artifact_key__in=expired_keys,
                    ).values_list("input__artifact_key", flat=True)
                )
            else:
                active_artifact_keys = set(
                    active_jobs.filter(artifact_key__in=expired_keys).values_list("artifact_key", flat=True)
                )

        page_failed = False
        prefix_deleted = 0
        for entry in expired_entries:
            if prefix_deleted >= page_budget:
                break
            entry_key = entry["Key"]
            if entry_key in active_artifact_keys:
                continue
            if delete_from_bucket(client, bucket, entry_key):
                deleted += 1
                prefix_deleted += 1
            else:
                page_failed = True

        # Do not move past a failed delete. Successful deletes are idempotent,
        # so retrying this page is safe and gives provider failures another try.
        if page_failed:
            continue
        next_token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
        try:
            if next_token:
                cache.set(cursor_key, next_token, timeout=None)
            else:
                cache.delete(cursor_key)
        except Exception:  # noqa: BLE001 - a repeated page is safe on next run
            logger.warning("admin artifact cleanup cursor update failed", extra={"prefix": prefix}, exc_info=True)
    return deleted


def active_admin_operation(actor, kind: str, conflict_key: str):
    """Return the active job for this actor/scope conflict key, if any."""

    if not conflict_key:
        return None
    return AdminOperationJob.objects.filter(
        actor=actor,
        kind=kind,
        conflict_key=conflict_key[:120],
        status__in=[AdminOperationJob.Status.QUEUED, AdminOperationJob.Status.RUNNING, AdminOperationJob.Status.WAITING],
    ).first()


def admin_operation_status_payload(job: AdminOperationJob) -> dict[str, Any]:
    """Bounded status payload for polling; never includes input populations."""

    result = job.result if isinstance(job.result, dict) else {}
    if job.kind == AdminOperationJob.Kind.STORAGE_USAGE_REFRESH:
        stats = result.get("stats") or {}
        result = {"available": bool(stats.get("available")), "refreshed_at": stats.get("refreshed_at")}
    return {
        "id": str(job.pk),
        "kind": job.kind,
        "status": job.status,
        "language": job.language,
        "total": job.total,
        "processed": job.processed,
        "succeeded": job.succeeded,
        "failed": job.failed,
        "skipped": job.skipped,
        "error": (job.error or "")[:MAX_ERROR_LENGTH],
        "result": result,
        "artifact_ready": bool(job.artifact_key),
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
