"""Background processing for graduation-gallery uploads."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext as _

from . import graduation_gallery as gg
from .models import GraduationGalleryItem
from .utils.storage_operations import (
    delete_from_bucket,
    get_r2_client,
)

logger = logging.getLogger(__name__)

GALLERY_TASK_NAME = "management_system.gallery_tasks.process_gallery_job"
_CHUNK = 200


def enqueue_gallery_job(public_id) -> None:
    """Queue one gallery job on the default queue after commit."""
    process_gallery_job.delay(str(public_id))


def _bucket() -> str:
    return getattr(settings, "R2_BUCKET_NAME", "") or ""


def _cache_control() -> str:
    max_age = int(getattr(settings, "GALLERY_R2_PUBLIC_CACHE_MAX_AGE", 31536000) or 31536000)
    return f"public, max-age={max_age}, immutable"


def _put_bytes(client, bucket: str, key: str, data: bytes, content_type: str) -> None:
    from io import BytesIO

    client.upload_fileobj(
        BytesIO(data),
        bucket,
        key,
        ExtraArgs={"ContentType": content_type, "CacheControl": _cache_control()},
    )


def _head_object(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return None


def _claim_job(public_id: str):
    from .models import GraduationGalleryJob

    with transaction.atomic():
        job = (
            GraduationGalleryJob.objects.select_for_update(skip_locked=True)
            .filter(public_id=public_id, status=GraduationGalleryJob.Status.QUEUED)
            .first()
        )
        if job is None:
            return None
        job.status = GraduationGalleryJob.Status.PROCESSING
        job.save(update_fields=["status", "updated_at"])
        return job


def _fail_job(job, message: str):
    job.status = job.__class__.Status.FAILED
    job.last_error = (message or "")[:2000]
    job.save(update_fields=["status", "last_error", "updated_at"])


def _iter_zip_entries(zf: zipfile.ZipFile):
    count = 0
    for info in zf.infolist():
        if count >= gg.MAX_ZIP_ENTRIES:
            break
        name = info.filename or ""
        base = os.path.basename(name)
        if info.is_dir() or not base:
            continue
        if "/__MACOSX/" in f"/{name}" or base.startswith("."):
            continue
        count += 1
        yield info, base


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    name=GALLERY_TASK_NAME,
)
def process_gallery_job(self, job_public_id: str):
    """Process one staged gallery upload into public R2 keys + item rows."""
    from .models import GraduationGalleryJob

    job = _claim_job(job_public_id)
    if job is None:
        return {"status": "ignored", "public_id": str(job_public_id)}
    bucket = _bucket()
    if not bucket:
        _fail_job(job, str(_("Storage is not configured.")))
        return {"status": "failed", "public_id": str(job_public_id)}
    client = get_r2_client()
    if client is None:
        _fail_job(job, str(_("Storage is not configured.")))
        return {"status": "failed", "public_id": str(job_public_id)}
    try:
        meta = _head_object(client, bucket, job.staging_key)
        if not meta or not int(meta.get("ContentLength", 0) or 0):
            _fail_job(job, str(_("The uploaded file is missing or empty.")))
            return {"status": "failed", "public_id": str(job_public_id)}
        if int(meta.get("ContentLength", 0) or 0) > gg.MAX_ZIP_BYTES:
            _fail_job(job, str(_("Zip files must be 2 GB or smaller.")))
            return {"status": "failed", "public_id": str(job_public_id)}
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            client.download_fileobj(bucket, job.staging_key, tmp)
            tmp.flush()
            tmp.seek(0)
            if job.is_zip:
                result = _process_zip(client, bucket, job, tmp.name)
            else:
                with open(tmp.name, "rb") as fh:
                    raw = fh.read()
                result = _process_single(client, bucket, job, job.original_filename, raw)
        processed = result["processed"]
        failed = result["failed"]
        total = result["total"]
        job.total_files = total
        job.processed_files = processed
        job.failed_files = failed
        if processed:
            job.status = GraduationGalleryJob.Status.COMPLETED
            job.last_error = result.get("error", "")[:2000]
            job.save(update_fields=["status", "total_files", "processed_files", "failed_files", "last_error", "updated_at"])
            try:
                delete_from_bucket(client, bucket, job.staging_key)
            except Exception:
                logger.warning("gallery staging cleanup failed", extra={"public_id": str(job.public_id)})
        else:
            _fail_job(job, result.get("error") or str(_("No usable images or videos were found.")))
            job.total_files = total
            job.processed_files = 0
            job.failed_files = failed
            job.save(update_fields=["total_files", "processed_files", "failed_files"])
        return {"status": job.status, "public_id": str(job_public_id), "processed": processed, "failed": failed}
    except Exception as exc:
        logger.exception("gallery job failed", extra={"public_id": str(job_public_id)})
        try:
            job.refresh_from_db()
            _fail_job(job, str(_("Processing failed. Please retry with a smaller file.")))
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30)


def _upload_image(client, bucket: str, job, filename: str, raw: bytes):
    display_bytes, width, height = gg.optimize_gallery_image(raw)
    scope = job.academic_year_level
    display_key, original_key = gg.build_image_keys(
        scope.academic_year.name, int(scope.level.ordering), filename
    )
    ext = os.path.splitext(original_key)[1].lower() or ".jpg"
    _put_bytes(client, bucket, display_key, display_bytes, "image/webp")
    _put_bytes(client, bucket, original_key, raw, _image_content_type(ext))
    return GraduationGalleryItem(
        academic_year_level_id=job.academic_year_level_id,
        kind=GraduationGalleryItem.Kind.IMAGE,
        display_key=display_key,
        original_key=original_key,
        original_filename=(filename or "image")[:255],
        byte_size=len(raw),
        width=width,
        height=height,
        uploaded_by_id=job.created_by_id,
    )


def _image_content_type(ext: str) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(
        (ext or "").lower(), "image/jpeg"
    )


def _upload_video(client, bucket: str, job, filename: str, raw: bytes):
    ext = os.path.splitext(filename or "")[1].lower() or ".mp4"
    if ext == ".mov":
        content_type = "video/quicktime"
    elif ext == ".m4v":
        content_type = "video/x-m4v"
    else:
        ext = ".mp4"
        content_type = "video/mp4"
    original_key = gg.build_original_key(
        job.academic_year_level.academic_year.name,
        int(job.academic_year_level.level.ordering),
        filename,
    )
    _put_bytes(client, bucket, original_key, raw, content_type)
    return GraduationGalleryItem(
        academic_year_level_id=job.academic_year_level_id,
        kind=GraduationGalleryItem.Kind.VIDEO,
        display_key=original_key,
        original_key=original_key,
        original_filename=(filename or "video.mp4")[:255],
        byte_size=len(raw),
        width=None,
        height=None,
        uploaded_by_id=job.created_by_id,
    )


def _process_single(client, bucket: str, job, filename: str, raw: bytes) -> dict:
    kind = gg.classify_upload(filename, job.content_type or "")
    if kind == "image" and len(raw) > gg.MAX_SINGLE_IMAGE_BYTES:
        return {"total": 1, "processed": 0, "failed": 1, "error": str(_("Image files must be 25 MB or smaller."))}
    if kind == "video" and len(raw) > gg.MAX_SINGLE_VIDEO_BYTES:
        return {"total": 1, "processed": 0, "failed": 1, "error": str(_("Video files must be 500 MB or smaller."))}
    try:
        if kind == "image":
            item = _upload_image(client, bucket, job, filename, raw)
        elif kind == "video":
            item = _upload_video(client, bucket, job, filename, raw)
        else:
            return {"total": 1, "processed": 0, "failed": 1, "error": str(_("Only JPG, PNG, WebP images or MP4 videos are allowed."))}
        item.save()
        return {"total": 1, "processed": 1, "failed": 0, "error": ""}
    except ValueError as exc:
        return {"total": 1, "processed": 0, "failed": 1, "error": str(exc)}


def _process_zip(client, bucket: str, job, tmp_path: str) -> dict:
    items: list[GraduationGalleryItem] = []
    failed = 0
    total = 0
    error = ""
    try:
        archive = zipfile.ZipFile(tmp_path)
    except zipfile.BadZipFile:
        return {"total": 0, "processed": 0, "failed": 0, "error": str(_("The zip file could not be read."))}
    with archive:
        for info, base in _iter_zip_entries(archive):
            total += 1
            if info.file_size > gg.MAX_SINGLE_VIDEO_BYTES:
                failed += 1
                continue
            try:
                raw = archive.read(info.filename)
            except Exception:
                failed += 1
                continue
            kind = gg.classify_upload(base, "")
            if kind == "image" and len(raw) > gg.MAX_SINGLE_IMAGE_BYTES:
                failed += 1
                continue
            if kind not in ("image", "video"):
                failed += 1
                continue
            try:
                if kind == "image":
                    items.append(_upload_image(client, bucket, job, base, raw))
                else:
                    items.append(_upload_video(client, bucket, job, base, raw))
            except (ValueError, OSError):
                failed += 1
                continue
            if len(items) >= _CHUNK:
                GraduationGalleryItem.objects.bulk_create(items, batch_size=_CHUNK, ignore_conflicts=True)
                items = []
    if items:
        GraduationGalleryItem.objects.bulk_create(items, batch_size=_CHUNK, ignore_conflicts=True)
    processed = total - failed
    if processed and failed:
        error = str(_("Some files were skipped because they are not supported images or videos."))
    return {"total": total, "processed": processed, "failed": failed, "error": error}
