"""Coordinate short storage writes with durable storage and media jobs."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from .models import (
    AdminOperationJob,
    GraduationGalleryJob,
    MediaCleanupStatus,
    MediaProcessingJob,
    MediaProcessingStatus,
)


MAX_ACTIVE_STORAGE_JOBS = 50
STORAGE_JOB_CAPACITY_LOCK = 0x4C4D5353544F5241
STORAGE_JOB_STATUSES = (AdminOperationJob.Status.QUEUED, AdminOperationJob.Status.RUNNING)
MEDIA_OUTPUT_STATUSES = (
    MediaProcessingStatus.AWAITING_UPLOAD,
    MediaProcessingStatus.QUEUED,
    MediaProcessingStatus.PROCESSING,
    MediaProcessingStatus.UPLOADING,
    MediaProcessingStatus.VERIFYING,
)
MEDIA_CLEANUP_STATUSES = (
    MediaCleanupStatus.QUEUED,
    MediaCleanupStatus.RUNNING,
    MediaCleanupStatus.FAILED,
)


def _storage_path_lock_id(path: str) -> int:
    digest = hashlib.blake2b(
        ("lms-storage-path:" + path).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def lock_storage_namespace(*paths: str, reserve_job_slot: bool = False) -> None:
    """Lock only the affected paths, sharing ancestors and owning each exact path.

    This lets writes to sibling objects proceed together while a folder-level
    reservation still conflicts with every child path. The optional capacity
    lock is used only when creating a storage job, so the global active-job
    limit remains race-safe without serializing ordinary R2 traffic.
    """

    normalized_paths = tuple(str(path or "").strip().strip("/") for path in paths)
    if not normalized_paths or any(not path for path in normalized_paths):
        raise ValidationError(_("A storage path is required."))
    if connection.vendor != "postgresql":
        return

    lock_modes: dict[int, bool] = {}
    for path in normalized_paths:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            ancestor_lock = _storage_path_lock_id("/".join(parts[:depth]))
            lock_modes.setdefault(ancestor_lock, False)
        exact_lock = _storage_path_lock_id(path)
        lock_modes[exact_lock] = True

    with connection.cursor() as cursor:
        if reserve_job_slot:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [STORAGE_JOB_CAPACITY_LOCK])
        for lock_id, exclusive in sorted(lock_modes.items()):
            function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
            cursor.execute(f"SELECT {function}(%s)", [lock_id])


def _key_overlaps(first: str, second: str) -> bool:
    left = str(first or "").strip().rstrip("/")
    right = str(second or "").strip().rstrip("/")
    if not left or not right:
        return True
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _media_folder_filter(keys: tuple[str, ...]) -> Q:
    """Build one bounded SQL predicate for media output-folder overlap."""

    query = Q(requested_folder="")
    for raw_key in keys:
        key = str(raw_key or "").strip().rstrip("/")
        if not key:
            return Q()
        query |= Q(requested_folder=key) | Q(requested_folder__startswith=key + "/")
        ancestor = key
        while "/" in ancestor:
            ancestor = ancestor.rsplit("/", 1)[0]
            query |= Q(requested_folder=ancestor)
    return query


def assert_storage_namespace_available(
    *keys: str,
    creating_storage_operation: bool = False,
    check_media_outputs: bool = True,
    check_media_cleanup: bool = True,
    ignore_media_job_ids: tuple[int, ...] = (),
    ignore_gallery_jobs: bool = False,
) -> None:
    """Fail closed when a mutation overlaps a live durable storage/media operation.

    Callers that create a durable operation must hold path locks and the
    capacity lock in their transaction. Direct writes use
    ``guarded_storage_write`` so the reservation check and provider mutation
    share the same affected-path lock window.
    """

    normalized_keys = tuple(str(key or "").strip().rstrip("/") for key in keys)
    if not normalized_keys:
        raise ValidationError(_("A storage path is required."))

    active_jobs = list(
        AdminOperationJob.objects.filter(
            kind__in=(AdminOperationJob.Kind.STORAGE_RENAME, AdminOperationJob.Kind.STORAGE_DELETE),
            status__in=STORAGE_JOB_STATUSES,
        ).order_by("created_at").values_list("kind", "input")[:MAX_ACTIVE_STORAGE_JOBS + 1]
    )
    if (
        len(active_jobs) > MAX_ACTIVE_STORAGE_JOBS
        or creating_storage_operation and len(active_jobs) >= MAX_ACTIVE_STORAGE_JOBS
    ):
        raise ValidationError(_("Storage has reached its active operation limit. Try again shortly."))

    for _kind, payload in active_jobs:
        existing_keys = (payload or {}).get("old_key"), (payload or {}).get("new_key"), (payload or {}).get("folder_path")
        if any(_key_overlaps(key, existing) for key in normalized_keys for existing in existing_keys if existing):
            raise ValidationError(_("Another storage operation is already using these folders."))

    gallery_paths = ("galleries", "gallery-staging")
    if not ignore_gallery_jobs and any(
        _key_overlaps(key, gallery_path)
        for key in normalized_keys
        for gallery_path in gallery_paths
    ) and GraduationGalleryJob.objects.filter(
        status__in=(GraduationGalleryJob.Status.QUEUED, GraduationGalleryJob.Status.PROCESSING)
    ).exists():
        raise ValidationError(_("A graduation gallery upload is using this storage folder."))

    if not check_media_outputs and not check_media_cleanup:
        return

    media_query = Q()
    if check_media_outputs:
        media_query |= Q(status__in=MEDIA_OUTPUT_STATUSES)
    if check_media_cleanup:
        media_query |= Q(cleanup_status__in=MEDIA_CLEANUP_STATUSES)
    media_jobs = MediaProcessingJob.objects.filter(media_query).filter(
        _media_folder_filter(normalized_keys)
    )
    if ignore_media_job_ids:
        media_jobs = media_jobs.exclude(pk__in=ignore_media_job_ids)
    if media_jobs.exists():
        raise ValidationError(_("A media upload or cleanup is already using this storage folder."))


@contextmanager
def guarded_storage_write(*keys: str) -> Iterator[None]:
    """Run one bounded direct storage mutation against active job reservations."""

    with transaction.atomic():
        lock_storage_namespace(*keys)
        assert_storage_namespace_available(*keys)
        yield


@contextmanager
def reserve_media_output_folder(folder: str) -> Iterator[None]:
    """Reserve a media destination while creating or retrying its durable job."""

    with transaction.atomic():
        lock_storage_namespace(folder)
        assert_storage_namespace_available(
            folder,
            check_media_outputs=False,
            check_media_cleanup=True,
        )
        yield
