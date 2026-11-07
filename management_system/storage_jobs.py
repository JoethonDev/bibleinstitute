"""Durable, bounded storage rename and recursive delete operations.

Folder renames never stream-destroy: every source page is copied first, a
dedicated verification pass proves the destination holds each source key, and
only then are source keys deleted. Recursive deletes consume one provider page
at a time and commit their cursor with the deletions. Both operations share the
AdminOperationJob lease/cursor ledger, so a worker restart resumes from the
committed page instead of rebuilding a whole-prefix collection in memory.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .admin_operations import (
    AdminOperationError,
    active_admin_operation,
    assert_admin_operation_progress,
    create_admin_operation,
    require_admin_operation_actor,
)
from .models import AdminOperationJob
from .storage_coordination import (
    assert_storage_namespace_available,
    lock_storage_namespace,
)
from .storage_ownership import storage_key_transition_allowed, user_may_manage_storage_key
from .utils.decorators import can_delete_content, can_manage_content
from .utils.r2_manager import R2Manager
from .utils.r2_references import (
    application_references_under,
    application_referenced_storage_keys,
    lesson_referenced_storage_keys,
    other_referenced_storage_keys,
    rewrite_storage_references,
    storage_reference_transition_conflicts,
)
from .utils.storage_operations import get_r2_client

logger = logging.getLogger(__name__)

RENAME_COPY_PAGE_SIZE = 500
DELETE_PAGE_SIZE = 200
MAX_ROW_ERRORS = 100
VERIFY_COMMIT_STEP = 500


def _manager() -> R2Manager:
    bucket = str(getattr(settings, "R2_BUCKET_NAME", "") or "").strip()
    if not bucket:
        raise AdminOperationError("storage_unavailable", _("Storage is not available right now."))
    try:
        client = get_r2_client()
    except Exception:  # noqa: BLE001 - provider boundary
        raise AdminOperationError("storage_unavailable", _("Storage is not available right now."))
    return R2Manager(client, bucket)


def _safe_prefix(value: str) -> str:
    prefix = str(value or "").strip().rstrip("/")
    return f"{prefix}/" if prefix else ""


def _valid_folder_prefix(value: str) -> str | None:
    prefix = _safe_prefix(value)
    if not prefix or prefix == "/":
        return None
    if not R2Manager._is_safe_object_key(prefix.rstrip("/")):
        return None
    return prefix


def _normalized_folder_keys(old_key, new_key) -> tuple[str, str] | None:
    old_prefix = _valid_folder_prefix(old_key)
    new_prefix = _valid_folder_prefix(new_key)
    if old_prefix is None or new_prefix is None or old_prefix == new_prefix:
        return None
    if new_prefix.startswith(old_prefix) or old_prefix.startswith(new_prefix):
        return None
    return old_prefix, new_prefix


def _normalized_file_keys(old_key, new_key) -> tuple[str, str] | None:
    if not isinstance(old_key, str) or not isinstance(new_key, str):
        return None
    if not R2Manager._is_safe_object_key(old_key) or not R2Manager._is_safe_object_key(new_key):
        return None
    if old_key == new_key:
        return None
    if new_key.startswith(old_key + "/") or old_key.startswith(new_key + "/"):
        return None
    return old_key, new_key


def _require_manage_actor(actor, *keys: str) -> None:
    if not actor or not getattr(actor, "is_active", False) or not can_manage_content(actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to manage storage."),
        )
    if not all(user_may_manage_storage_key(actor, key) for key in keys):
        raise AdminOperationError(
            "not_permitted",
            _("You do not have permission to manage this storage key."),
        )


def _require_delete_actor(actor, key: str) -> None:
    if not actor or not getattr(actor, "is_active", False) or not can_delete_content(actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to delete storage."),
        )
    if not user_may_manage_storage_key(actor, key):
        raise AdminOperationError(
            "not_permitted",
            _("You do not have permission to manage this storage key."),
        )


def _conflict_key(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        json.dumps(list(parts), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}:{digest}"[:120]


def enqueue_storage_rename(
    *,
    actor,
    old_key: str,
    new_key: str,
    is_folder: bool,
    language: str = "ar",
) -> AdminOperationJob:
    """Validate and freeze one rename request as an idempotent storage job."""

    if is_folder:
        normalized = _normalized_folder_keys(old_key, new_key)
        if normalized is None:
            raise ValidationError(_("Invalid folder rename"))
        old_key, new_key = normalized
    else:
        normalized = _normalized_file_keys(old_key, new_key)
        if normalized is None:
            raise ValidationError(_("Invalid file key"))
        old_key, new_key = normalized
        if old_key.lower().endswith(".ts") or new_key.lower().endswith(".ts"):
            raise ValidationError(
                _("Segment files cannot be renamed directly. Rename the video file instead.")
            )
        if old_key.lower().endswith(".m3u8") != new_key.lower().endswith(".m3u8"):
            raise ValidationError(_("Both names must be video manifests"))
    if not storage_key_transition_allowed(old_key, new_key):
        raise ValidationError(_("You do not have permission to manage this storage key."))
    _require_manage_actor(actor, old_key, new_key)
    conflict_key = _conflict_key("storage-rename", old_key, new_key, bool(is_folder))
    existing = active_admin_operation(actor, AdminOperationJob.Kind.STORAGE_RENAME, conflict_key)
    if existing is not None:
        return existing
    with transaction.atomic():
        lock_storage_namespace(old_key, new_key, reserve_job_slot=True)
        existing = active_admin_operation(actor, AdminOperationJob.Kind.STORAGE_RENAME, conflict_key)
        if existing is not None:
            return existing
        assert_storage_namespace_available(
            old_key,
            new_key,
            creating_storage_operation=True,
        )
        if storage_reference_transition_conflicts(old_key, new_key, is_folder=is_folder):
            raise ValidationError(_("A record already uses a destination file name."))
        return create_admin_operation(
            actor=actor,
            kind=AdminOperationJob.Kind.STORAGE_RENAME,
            input={"old_key": old_key, "new_key": new_key, "is_folder": bool(is_folder)},
            conflict_key=conflict_key,
            language=language,
        )


def enqueue_storage_delete(*, actor, folder_path: str, language: str = "ar") -> AdminOperationJob:
    """Freeze one recursive folder delete as an idempotent storage job."""

    prefix = _valid_folder_prefix(folder_path)
    if prefix is None:
        raise ValidationError(_("Invalid folder request"))
    _require_delete_actor(actor, prefix)
    conflict_key = _conflict_key("storage-delete", prefix)
    existing = active_admin_operation(actor, AdminOperationJob.Kind.STORAGE_DELETE, conflict_key)
    if existing is not None:
        return existing
    with transaction.atomic():
        lock_storage_namespace(prefix, reserve_job_slot=True)
        existing = active_admin_operation(actor, AdminOperationJob.Kind.STORAGE_DELETE, conflict_key)
        if existing is not None:
            return existing
        if application_references_under(prefix):
            raise ValidationError(_("The folder contains files still linked to application records."))
        assert_storage_namespace_available(
            prefix,
            creating_storage_operation=True,
        )
        return create_admin_operation(
            actor=actor,
            kind=AdminOperationJob.Kind.STORAGE_DELETE,
            input={"folder_path": prefix, "recursive": True},
            conflict_key=conflict_key,
            language=language,
        )


def _cutoff_from_cursor(cursor: dict) -> datetime:
    raw = str(cursor.get("started_at") or "")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = timezone.now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed


def _modified_after_cutoff(obj: dict, cutoff: datetime) -> bool:
    modified = obj.get("LastModified")
    if modified is None:
        return False
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=datetime_timezone.utc)
    return modified > cutoff


def _iter_pages_after(manager: R2Manager, prefix: str, after_key: str, page_size: int):
    """Yield bounded provider pages after a committed source key."""

    cursor = after_key
    while True:
        page = manager.list_folder_page(prefix, start_after=cursor, max_keys=page_size)
        contents = page.get("Contents", [])
        if not contents:
            return
        yield page, contents
        if not page.get("IsTruncated"):
            return
        cursor = contents[-1]["Key"]


def _iter_objects_after(manager: R2Manager, prefix: str, after_key: str):
    """Yield provider objects one page at a time for bounded verification."""

    for _page, contents in _iter_pages_after(manager, prefix, after_key, 1000):
        yield from contents


def _count_objects(manager: R2Manager, prefix: str) -> int:
    total = 0
    for _page, contents in _iter_pages_after(manager, prefix, "", 1000):
        total += len(contents)
    return total


def _add_row_error(errors: list[dict], key: str, message: str) -> None:
    if len(errors) >= MAX_ROW_ERRORS:
        return
    errors.append({"key": str(key)[:512], "message": str(message)[:300]})


def _commit(
    job,
    token,
    *,
    processed: int,
    total: int,
    succeeded: int,
    failed: int,
    skipped: int,
    cursor: dict,
    result: dict,
) -> None:
    assert_admin_operation_progress(
        job.pk,
        token,
        processed=max(0, int(processed)),
        succeeded=max(0, int(succeeded)),
        failed=max(0, int(failed)),
        skipped=max(0, int(skipped)),
        total=max(0, int(total)),
        cursor=cursor,
        result=result,
    )


def _rollback_folder_rename(
    job,
    token,
    manager: R2Manager,
    old_prefix: str,
    new_prefix: str,
    cursor: dict,
    *,
    copied: int,
    verified: int,
) -> bool:
    """Best-effort bounded rollback: restore every destination key, then remove copies."""

    cursor.update({
        "phase": "rolling_back",
        "rollback_after": "",
        "rollback_cleanup_after": "",
    })
    _commit(
        job, token,
        processed=0, total=max(1, copied), succeeded=0, failed=0, skipped=0,
        cursor=cursor, result={"phase": "rolling_back"},
    )
    restored = 0
    try:
        for _page, contents in _iter_pages_after(
            manager, new_prefix, "", RENAME_COPY_PAGE_SIZE
        ):
            for obj in contents:
                relative = obj["Key"][len(new_prefix):]
                # Preserve uploads changed during the rename. Restore only
                # originals already removed by this operation.
                original = old_prefix + relative
                if not manager.object_exists(original):
                    manager.copy_key(obj["Key"], original)
                restored += 1
            cursor["rollback_after"] = contents[-1]["Key"]
            _commit(
                job, token,
                processed=restored, total=max(1, restored), succeeded=restored,
                failed=0, skipped=0, cursor=cursor,
                result={"phase": "rolling_back"},
            )
    except Exception:  # noqa: BLE001 - provider boundary; destination copies remain
        logger.exception(
            "storage rename rollback restore failed",
            extra={"event": "storage_rename_rollback_restore_failed", "job_id": str(job.pk)},
        )
        return False

    cleaned = 0
    try:
        while True:
            page = manager.list_folder_page(
                new_prefix,
                start_after=str(cursor.get("rollback_cleanup_after") or ""),
                max_keys=DELETE_PAGE_SIZE,
            )
            contents = page.get("Contents", [])
            if not contents:
                break
            keys = [obj["Key"] for obj in contents]
            _successful, failures = manager.delete_files_batch(keys)
            if failures:
                return False
            cleaned += len(keys)
            cursor["rollback_cleanup_after"] = keys[-1]
            _commit(
                job, token,
                processed=cleaned, total=max(1, cleaned), succeeded=cleaned,
                failed=0, skipped=0, cursor=cursor,
                result={"phase": "rolling_back"},
            )
            if not page.get("IsTruncated"):
                break
    except Exception:  # noqa: BLE001 - provider boundary; destination copies remain
        logger.exception(
            "storage rename rollback cleanup failed",
            extra={"event": "storage_rename_rollback_cleanup_failed", "job_id": str(job.pk)},
        )
        return False
    return True


def _execute_folder_rename(
    job,
    token,
    manager: R2Manager,
    old_prefix: str,
    new_prefix: str,
) -> dict:
    cursor = dict(job.cursor or {})
    result_meta = dict(job.result or {})
    errors = list(result_meta.get("row_errors") or [])[:MAX_ROW_ERRORS]
    phase = str(cursor.get("phase") or "")
    if not cursor.get("started_at"):
        cursor["started_at"] = timezone.now().isoformat()
    cutoff = _cutoff_from_cursor(cursor)
    source_total = int(cursor.get("source_total") or 0)
    copied = int(cursor.get("copied") or 0)
    verified = int(cursor.get("verified") or 0)
    deleted = int(cursor.get("deleted") or 0)
    delete_checked = int(cursor.get("delete_checked") or 0)
    failed = int(cursor.get("failed") or 0)
    skipped = int(cursor.get("skipped") or 0)
    total_units = source_total * 3
    warning = str(result_meta.get("warning") or "")

    if not phase:
        if manager.list_folder_page(new_prefix, max_keys=1).get("Contents"):
            raise AdminOperationError(
                "destination_not_empty",
                _("The destination folder is not empty."),
            )
        source_total = _count_objects(manager, old_prefix)
        total_units = source_total * 3
        phase = "copying"
        cursor = {
            "phase": phase,
            "started_at": cursor["started_at"],
            "source_total": source_total,
            "copied": 0,
            "verified": 0,
            "deleted": 0,
            "delete_checked": 0,
            "failed": 0,
            "skipped": 0,
            "copy_after": "",
        }
        _commit(
            job, token,
            processed=0, total=total_units, succeeded=0, failed=0, skipped=0,
            cursor=cursor,
            result={"phase": phase, "row_errors": errors},
        )

    if phase == "copying":
        for page, contents in _iter_pages_after(
            manager, old_prefix, str(cursor.get("copy_after") or ""), RENAME_COPY_PAGE_SIZE
        ):
            for obj in contents:
                if _modified_after_cutoff(obj, cutoff):
                    skipped += 1
                    continue
                source_key = obj["Key"]
                relative = source_key[len(old_prefix):]
                try:
                    manager.copy_key(source_key, new_prefix + relative)
                except Exception as exc:  # noqa: BLE001 - provider boundary
                    raise AdminOperationError(
                        "copy_failed",
                        _("The folder copy failed before any old file was removed."),
                    ) from exc
                copied += 1
            cursor.update({
                "phase": "copying",
                "copy_after": contents[-1]["Key"],
                "copied": copied,
                "skipped": skipped,
            })
            _commit(
                job, token,
                processed=copied, total=total_units, succeeded=copied,
                failed=failed, skipped=skipped, cursor=cursor,
                result={"phase": "copying", "row_errors": errors},
            )
        phase = "verifying"
        cursor.update({
            "phase": phase,
            "verify_source_after": "",
            "verify_dest_after": "",
            "verified": 0,
        })
        _commit(
            job, token,
            processed=copied, total=total_units, succeeded=copied,
            failed=failed, skipped=skipped, cursor=cursor,
            result={"phase": phase, "row_errors": errors},
        )

    if phase == "verifying":
        source_iter = _iter_objects_after(
            manager, old_prefix, str(cursor.get("verify_source_after") or "")
        )
        destination_iter = _iter_objects_after(
            manager, new_prefix, str(cursor.get("verify_dest_after") or "")
        )
        source_obj = next(source_iter, None)
        destination_obj = next(destination_iter, None)
        pending = 0
        while source_obj is not None:
            if _modified_after_cutoff(source_obj, cutoff):
                source_obj = next(source_iter, None)
                continue
            source_key = source_obj["Key"]
            source_suffix = source_key[len(old_prefix):]
            while destination_obj is not None:
                destination_suffix = destination_obj["Key"][len(new_prefix):]
                if destination_suffix >= source_suffix:
                    break
                destination_obj = next(destination_iter, None)
            if destination_obj is None or destination_obj["Key"][len(new_prefix):] != source_suffix:
                raise AdminOperationError(
                    "verification_failed",
                    _("The renamed copy is incomplete; old files were not removed."),
                )
            verified += 1
            cursor.update({
                "verify_source_after": source_key,
                "verify_dest_after": destination_obj["Key"],
                "verified": verified,
            })
            pending += 1
            if pending >= VERIFY_COMMIT_STEP:
                _commit(
                    job, token,
                    processed=copied + verified, total=total_units,
                    succeeded=copied + verified, failed=failed, skipped=skipped,
                    cursor=cursor, result={"phase": "verifying", "row_errors": errors},
                )
                pending = 0
            source_obj = next(source_iter, None)
            destination_obj = next(destination_iter, None)
        _commit(
            job, token,
            processed=copied + verified, total=total_units,
            succeeded=copied + verified, failed=failed, skipped=skipped,
            cursor=cursor, result={"phase": "verifying", "row_errors": errors},
        )
        phase = "deleting"
        cursor.update({"phase": phase, "delete_after": ""})
        _commit(
            job, token,
            processed=copied + verified, total=total_units,
            succeeded=copied + verified, failed=failed, skipped=skipped,
            cursor=cursor, result={"phase": phase, "row_errors": errors},
        )

    if phase == "deleting":
        while True:
            page = manager.list_folder_page(
                old_prefix,
                start_after=str(cursor.get("delete_after") or ""),
                max_keys=DELETE_PAGE_SIZE,
            )
            contents = page.get("Contents", [])
            if not contents:
                break
            deletable: list[str] = []
            for obj in contents:
                delete_checked += 1
                source_key = obj["Key"]
                if _modified_after_cutoff(obj, cutoff):
                    skipped += 1
                    continue
                relative = source_key[len(old_prefix):]
                if not manager.object_exists(new_prefix + relative):
                    failed += 1
                    _add_row_error(
                        errors,
                        source_key,
                        _("The copied file could not be verified, so the original was kept."),
                    )
                    continue
                deletable.append(source_key)
            if deletable:
                successful, failures = manager.delete_files_batch(deletable)
                deleted += len(successful)
                for failed_key in failures:
                    failed += 1
                    _add_row_error(
                        errors,
                        failed_key,
                        _("The old file could not be removed."),
                    )
            cursor.update({
                "phase": "deleting",
                "delete_after": contents[-1]["Key"],
                "deleted": deleted,
                "delete_checked": delete_checked,
                "failed": failed,
                "skipped": skipped,
            })
            _commit(
                job, token,
                processed=copied + verified + delete_checked,
                total=total_units,
                succeeded=max(0, copied + verified + delete_checked - failed),
                failed=failed,
                skipped=skipped,
                cursor=cursor,
                result={"phase": "deleting", "row_errors": errors},
            )
            if not page.get("IsTruncated"):
                break

    remaining = bool(manager.list_folder_page(old_prefix, max_keys=1).get("Contents"))
    if failed or skipped or remaining:
        restored = _rollback_folder_rename(
            job,
            token,
            manager,
            old_prefix,
            new_prefix,
            cursor,
            copied=copied,
            verified=verified,
        )
        if restored:
            raise AdminOperationError(
                "rename_failed_restored",
                _("The folder rename failed and the original files were restored."),
            )
        warning = str(_("The new files are ready, but some old files remain."))
        return {
            "status": AdminOperationJob.Status.PARTIAL,
            "result": {
                "phase": "complete",
                "old_key": old_prefix,
                "new_key": new_prefix,
                "copied": copied,
                "verified": verified,
                "deleted": deleted,
                "failed": failed,
                "skipped": skipped,
                "row_errors": errors,
                "warning": warning,
            },
        }

    if skipped:
        warning = str(_("Some files changed during the rename and were not moved."))
    elif failed or remaining:
        warning = str(_("The new files are ready, but some old files remain."))
    updated_references = 0
    if not failed and not skipped and not remaining:
        try:
            updated_references = rewrite_storage_references(
                old_prefix, new_prefix, is_folder=True
            )
        except Exception:  # noqa: BLE001 - DB reference repair is best-effort
            logger.exception(
                "storage rename reference update failed",
                extra={"event": "storage_rename_reference_failed", "job_id": str(job.pk)},
            )
            restored = _rollback_folder_rename(
                job, token, manager, old_prefix, new_prefix, cursor,
                copied=copied, verified=verified,
            )
            if restored:
                raise AdminOperationError(
                    "references_failed_restored",
                    _("The folder rename failed and the original files were restored."),
                )
            warning = str(_("The files were renamed, but some record links were not updated."))
    status = (
        AdminOperationJob.Status.COMPLETED
        if not failed and not skipped and not remaining and not warning
        else AdminOperationJob.Status.PARTIAL
    )
    result = {
        "phase": "complete",
        "old_key": old_prefix,
        "new_key": new_prefix,
        "copied": copied,
        "verified": verified,
        "deleted": deleted,
        "failed": failed,
        "skipped": skipped,
        "updated_references": updated_references,
        "row_errors": errors,
    }
    if warning:
        result["warning"] = warning
    return {"status": status, "result": result}


def _execute_file_rename(
    job,
    token,
    manager: R2Manager,
    old_key: str,
    new_key: str,
) -> dict:
    cursor = dict(job.cursor or {})
    phase = str(cursor.get("phase") or "")
    resume = phase in {"copying", "deleting"}
    warning = str((job.result or {}).get("warning") or "")
    last = {"phase": phase, "done": int(cursor.get("done") or 0), "total": int(cursor.get("total") or 0)}

    def report(progress_phase, done, total):
        nonlocal warning
        if progress_phase == "warning":
            warning = str(_("The new files are ready, but some old files remain."))
            return
        done = max(0, int(done or 0))
        total = max(0, int(total or 0))
        step = max(1, total // 100)
        if (
            progress_phase == last["phase"]
            and done < total
            and done - last["done"] < step
        ):
            return
        last.update({"phase": progress_phase, "done": done, "total": total})
        _commit(
            job, token,
            processed=done, total=total, succeeded=done,
            failed=0, skipped=0,
            cursor={"phase": progress_phase, "done": done, "total": total},
            result={"phase": progress_phase},
        )

    if old_key.lower().endswith(".m3u8"):
        success, error, created = manager.rename_m3u8_with_segments(
            old_key, new_key, progress_callback=report, resume=resume
        )
        if not success and new_key in created:
            success = True
            warning = str(_("The new files are ready, but some old files remain."))
    else:
        success = manager.rename_file(
            old_key, new_key, progress_callback=report, resume=resume
        )
        error = str(_("The file rename failed."))
    if not success:
        raise AdminOperationError("rename_failed", error or _("The rename failed."))
    updated_references = 0
    try:
        updated_references = rewrite_storage_references(old_key, new_key, is_folder=False)
    except Exception:  # noqa: BLE001 - DB reference repair is best-effort
        logger.exception(
            "storage rename reference update failed",
            extra={"event": "storage_rename_reference_failed", "job_id": str(job.pk)},
        )
        warning = str(_("The files were renamed, but some record links were not updated."))
    result = {
        "phase": "complete",
        "old_key": old_key,
        "new_key": new_key,
        "updated_references": updated_references,
    }
    if warning:
        result["warning"] = warning
    status = (
        AdminOperationJob.Status.PARTIAL
        if warning
        else AdminOperationJob.Status.COMPLETED
    )
    return {"status": status, "result": result}


def execute_storage_rename(job: AdminOperationJob, token) -> dict:
    """J01 executor for folder and file renames."""

    require_admin_operation_actor(job)
    payload = job.input or {}
    old_key = payload.get("old_key")
    new_key = payload.get("new_key")
    is_folder = bool(payload.get("is_folder"))
    if not isinstance(old_key, str) or not isinstance(new_key, str):
        raise AdminOperationError("invalid_input", _("Invalid rename request"))
    if is_folder:
        normalized = _normalized_folder_keys(old_key, new_key)
        if normalized is None:
            raise AdminOperationError("invalid_input", _("Invalid folder rename"))
        old_key, new_key = normalized
    else:
        normalized = _normalized_file_keys(old_key, new_key)
        if normalized is None:
            raise AdminOperationError("invalid_input", _("Invalid file key"))
        old_key, new_key = normalized
    if not storage_key_transition_allowed(old_key, new_key):
        raise AdminOperationError("not_permitted", _("You do not have permission to manage this storage key."))
    _require_manage_actor(job.actor, old_key, new_key)
    manager = _manager()
    if is_folder:
        return _execute_folder_rename(job, token, manager, old_key, new_key)
    return _execute_file_rename(job, token, manager, old_key, new_key)


def execute_storage_delete(job: AdminOperationJob, token) -> dict:
    """J01 executor for a recursive exact-prefix folder delete."""

    require_admin_operation_actor(job)
    payload = job.input or {}
    prefix = _valid_folder_prefix(payload.get("folder_path"))
    if prefix is None or not payload.get("recursive"):
        raise AdminOperationError("invalid_input", _("Invalid folder request"))
    _require_delete_actor(job.actor, prefix)
    manager = _manager()
    cursor = dict(job.cursor or {})
    result_meta = dict(job.result or {})
    errors = list(result_meta.get("row_errors") or [])[:MAX_ROW_ERRORS]
    deleted = int(cursor.get("deleted") or 0)
    failed = int(cursor.get("failed") or 0)
    skipped = int(cursor.get("skipped") or 0)
    processed = int(cursor.get("processed") or 0)
    total = int(cursor.get("total") or 0)
    token_value = str(cursor.get("continuation") or "")
    pending = list(cursor.get("pending") or [])
    pending_protected = list(cursor.get("pending_protected") or [])
    next_token = str(cursor.get("next_token") or "")
    if not total and not pending and not token_value and "total" not in cursor:
        total = _count_objects(manager, prefix)
        cursor.update({
            "total": total,
            "deleted": 0,
            "failed": 0,
            "skipped": 0,
            "processed": 0,
        })
        _commit(
            job, token,
            processed=0, total=total, succeeded=0, failed=0, skipped=0,
            cursor=cursor, result={"phase": "deleting", "row_errors": errors},
        )

    while True:
        if pending or pending_protected:
            newly_referenced = (
                lesson_referenced_storage_keys(pending)
                | application_referenced_storage_keys(pending)
                | other_referenced_storage_keys(pending)
            )
            protected = list(dict.fromkeys([*pending_protected, *newly_referenced]))
            deletable = [key for key in pending if key not in newly_referenced]
            successful, failures = manager.delete_files_batch(deletable) if deletable else ([], [])
            deleted += len(successful)
            for failed_key in failures:
                failed += 1
                _add_row_error(errors, failed_key, _("The file could not be removed."))
            for protected_key in protected:
                skipped += 1
                _add_row_error(errors, protected_key, _("The file is still linked to a record and was kept."))
            processed += len(pending) + len(pending_protected)
            cursor.update({
                "pending": [],
                "pending_protected": [],
                "continuation": next_token,
                "deleted": deleted,
                "failed": failed,
                "skipped": skipped,
                "processed": processed,
            })
            _commit(
                job, token,
                processed=processed, total=total,
                succeeded=max(0, processed - failed - skipped), failed=failed, skipped=skipped,
                cursor=cursor, result={"phase": "deleting", "row_errors": errors},
            )
            token_value = next_token
            pending = []
            pending_protected = []
            next_token = ""
            if not token_value:
                break
            continue
        page = manager.list_folder_page(
            prefix,
            continuation_token=token_value,
            max_keys=DELETE_PAGE_SIZE,
        )
        contents = page.get("Contents", [])
        if not contents:
            break
        next_token = str(page.get("NextContinuationToken") or "")
        keys = [obj["Key"] for obj in contents]
        pending = keys
        pending_protected = []
        cursor.update({
            "pending": pending,
            "pending_protected": pending_protected,
            "next_token": next_token,
            "continuation": token_value,
            "deleted": deleted,
            "failed": failed,
            "skipped": skipped,
            "processed": processed,
        })
        _commit(
            job, token,
            processed=processed, total=total,
            succeeded=max(0, processed - failed - skipped), failed=failed, skipped=skipped,
            cursor=cursor, result={"phase": "deleting", "row_errors": errors},
        )
        if not next_token and not page.get("IsTruncated"):
            # Delete the final page, then stop after the pending branch.
            pass

    remaining = bool(manager.list_folder_page(prefix, max_keys=1).get("Contents"))
    status = (
        AdminOperationJob.Status.COMPLETED
        if not failed and not skipped and not remaining
        else AdminOperationJob.Status.PARTIAL
    )
    result = {
        "phase": "complete",
        "folder_path": prefix,
        "deleted": deleted,
        "failed": failed,
        "skipped": skipped,
        "row_errors": errors,
    }
    if remaining and not failed:
        result["warning"] = str(_("Some files remain in the folder."))
    return {"status": status, "result": result}



def storage_usage_key(prefix: str) -> str:
    return "storage-usage:" + hashlib.sha256(prefix.encode("utf-8")).hexdigest()


def storage_usage_snapshot(prefix: str) -> dict | None:
    # One indexed kind/status lookup and bounded JSON; never contact R2 here.
    row = AdminOperationJob.objects.filter(
        kind=AdminOperationJob.Kind.STORAGE_USAGE_REFRESH,
        conflict_key=storage_usage_key(prefix), status=AdminOperationJob.Status.COMPLETED,
    ).order_by("-finished_at").values("result").first()
    return row["result"].get("stats") if row else None


def enqueue_storage_usage(*, actor, prefix: str, language: str = "ar") -> AdminOperationJob:
    if prefix:
        _require_manage_actor(actor, prefix)
    elif not actor.is_active or not can_manage_content(actor):
        raise ValidationError(_("You are no longer allowed to manage storage."))
    return create_admin_operation(
        actor=actor, kind=AdminOperationJob.Kind.STORAGE_USAGE_REFRESH,
        input={"prefix": prefix}, conflict_key=storage_usage_key(prefix), language=language,
    )


def execute_storage_usage(job: AdminOperationJob, token) -> dict:
    require_admin_operation_actor(job)
    prefix = str((job.input or {}).get("prefix") or "")
    if prefix:
        _require_manage_actor(job.actor, prefix)
    manager = _manager()
    from .utils.cloudflare_provider import CloudflareR2Client

    account = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
    api_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
    if account and api_token:
        manager.cloudflare = CloudflareR2Client(account, api_token)
    stats = manager.get_storage_stats(prefix)
    stats["refreshed_at"] = timezone.now().isoformat()
    stats["total_size_formatted"] = manager.format_file_size(stats["total_size"])
    for groups in (stats.get("by_extension", {}), stats.get("by_kind", {})):
        for value in groups.values():
            value["size_formatted"] = manager.format_file_size(value["size"])
    for entry in stats.get("largest_files", []):
        entry["size_formatted"] = manager.format_file_size(entry["size"])
        modified = entry.get("last_modified")
        if modified is not None:
            entry["last_modified"] = modified.isoformat()
    result = {"stats": stats}
    assert_admin_operation_progress(
        job.pk, token, processed=1, succeeded=1, failed=0, skipped=0, total=1, result=result,
    )
    return {"status": AdminOperationJob.Status.COMPLETED, "result": result}
