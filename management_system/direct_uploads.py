"""Authorize and finalize direct browser-to-R2 file transfers.

The browser sends file bytes straight to a private staging key; Django only
authorizes before the transfer and validates the stored object on finalize, so
no request body ever carries a file. A token or storage key is never
authorization: every call rechecks the actor, purpose, target and status.

Each owning domain keeps its own validation, transaction and durable job. This
module owns only the shared capability, staging and immutable-publication
rules; it is not a generic upload engine.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Sequence

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import DirectUploadAuthorization
from .storage_coordination import assert_storage_namespace_available, lock_storage_namespace
from .utils.storage_operations import delete_from_bucket, get_r2_client

logger = logging.getLogger(__name__)

STAGING_PREFIX = "direct-upload-staging"
FINAL_PREFIX = "direct-uploads"
UPLOAD_URL_TTL_SECONDS = 900
FINALIZE_GRACE = timedelta(minutes=30)
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_SWEEP_ROWS = 500

MEGABYTE = 1024 * 1024
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
AUDIO_CONTENT_TYPES = (
    "audio/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/x-m4a",
)
AUDIO_EXTENSIONS = (".webm", ".mp3", ".m4a", ".ogg", ".wav")
DOCUMENT_CONTENT_TYPES = ("image/jpeg", "image/png", "application/pdf")
DOCUMENT_EXTENSIONS = (".jpg", ".jpeg", ".png", ".pdf")
APK_CONTENT_TYPE = "application/vnd.android.package-archive"

# One hard-limit table for the dashboard, every picker, and both the authorize
# and finalize paths. Size families are per content type where a purpose has
# more than one accepted media family.
PURPOSE_LIMITS: dict[str, dict[str, object]] = {
    DirectUploadAuthorization.Purpose.APK: {
        "max_bytes": 500 * MEGABYTE,
        "content_types": (APK_CONTENT_TYPE,),
        "extensions": (".apk",),
    },
    DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT: {
        "max_bytes": 10 * MEGABYTE,
        "content_types": DOCUMENT_CONTENT_TYPES,
        "extensions": DOCUMENT_EXTENSIONS,
    },
    DirectUploadAuthorization.Purpose.SUPPORT_ATTACHMENT: {
        "max_bytes": 20 * MEGABYTE,
        "content_types": IMAGE_CONTENT_TYPES + AUDIO_CONTENT_TYPES,
        "extensions": IMAGE_EXTENSIONS + AUDIO_EXTENSIONS,
        "families": {
            "image": {
                "max_bytes": 10 * MEGABYTE,
                "content_types": IMAGE_CONTENT_TYPES,
                "extensions": IMAGE_EXTENSIONS,
            },
            "audio": {
                "max_bytes": 20 * MEGABYTE,
                "content_types": AUDIO_CONTENT_TYPES,
                "extensions": AUDIO_EXTENSIONS,
                "audio_seconds": 60,
            },
        },
    },
    DirectUploadAuthorization.Purpose.TELEGRAM_ATTACHMENT: {
        "max_bytes": 50 * MEGABYTE,
        "content_types": IMAGE_CONTENT_TYPES + AUDIO_CONTENT_TYPES,
        "extensions": IMAGE_EXTENSIONS + AUDIO_EXTENSIONS,
        "families": {
            "image": {
                "max_bytes": 10 * MEGABYTE,
                "content_types": IMAGE_CONTENT_TYPES,
                "extensions": IMAGE_EXTENSIONS,
            },
            "audio": {
                "max_bytes": 20 * MEGABYTE,
                "content_types": AUDIO_CONTENT_TYPES,
                "extensions": AUDIO_EXTENSIONS,
                "audio_seconds": 300,
            },
        },
    },
    DirectUploadAuthorization.Purpose.HISTORICAL_INTAKE: {
        "max_bytes": 10 * MEGABYTE,
        "content_types": (
            "text/csv",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "extensions": (".csv", ".xlsx"),
    },
    DirectUploadAuthorization.Purpose.LEGACY_USER_CSV: {
        "max_bytes": 2 * MEGABYTE,
        "content_types": ("text/csv", "application/vnd.ms-excel", "application/octet-stream"),
        "extensions": (".csv",),
    },
    DirectUploadAuthorization.Purpose.QUIZ_JSON: {
        "max_bytes": 512 * 1024,
        "content_types": ("application/json", "text/json", "text/plain"),
        "extensions": (".json",),
    },
}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DirectUploadError(Exception):
    """Expected, translated rejection from the direct-upload boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadLimits:
    """Effective limits for one browser file after content-type narrowing."""

    max_bytes: int
    content_types: tuple[str, ...]
    extensions: tuple[str, ...]
    audio_seconds: int | None = None


@dataclass(frozen=True)
class DirectUploadGrant:
    """What the browser needs before starting the transfer."""

    token: str
    upload_url: str
    staging_key: str
    expires_at: str
    max_bytes: int
    content_types: tuple[str, ...]
    extensions: tuple[str, ...]
    audio_seconds: int | None


@dataclass(frozen=True)
class FinalizedUpload:
    """A validated, immutably published object a domain may now reference."""

    token: str
    key: str
    original_filename: str
    content_type: str
    size: int
    metadata: dict


def _safe_filename(filename: str, extensions: Sequence[str]) -> str:
    """Return a generated name that keeps only a validated extension."""

    base = os.path.basename(str(filename or "").strip())
    extension = os.path.splitext(base)[1].lower()
    if extension not in extensions:
        raise DirectUploadError(
            "unsupported_type",
            _("This file type is not allowed. Allowed types: %(types)s.")
            % {"types": ", ".join(extension.lstrip(".").upper() for extension in extensions)},
        )
    return f"{uuid.uuid4().hex}{extension}"


def _family_limits(purpose: str, content_type: str) -> UploadLimits:
    configured = PURPOSE_LIMITS.get(purpose)
    if not configured:
        raise DirectUploadError("unsupported_purpose", _("This upload purpose is not supported."))
    families = configured.get("families") or {}
    content_type = str(content_type or "").split(";")[0].strip().lower()
    if families:
        for family in families.values():
            if content_type in family["content_types"]:
                return UploadLimits(
                    max_bytes=int(family["max_bytes"]),
                    content_types=tuple(family["content_types"]),
                    extensions=tuple(family["extensions"]),
                    audio_seconds=family.get("audio_seconds"),
                )
        raise DirectUploadError("unsupported_type", _("This file type is not allowed."))
    if content_type not in configured["content_types"]:
        raise DirectUploadError(
            "unsupported_type",
            _("This file type is not allowed. Allowed types: %(types)s.")
            % {"types": ", ".join(extension.lstrip(".").upper() for extension in configured["extensions"])},
        )
    return UploadLimits(
        max_bytes=int(configured["max_bytes"]),
        content_types=tuple(configured["content_types"]),
        extensions=tuple(configured["extensions"]),
        audio_seconds=configured.get("audio_seconds"),
    )


def upload_limits(*, purpose: str, content_type: str, filename: str) -> UploadLimits:
    """Return the canonical hard limits for one purpose and content type."""

    limits = _family_limits(purpose, content_type)
    extension = os.path.splitext(os.path.basename(str(filename or "")))[1].lower()
    if extension not in limits.extensions:
        raise DirectUploadError(
            "unsupported_type",
            _("This file type is not allowed. Allowed types: %(types)s.")
            % {"types": ", ".join(item.lstrip(".").upper() for item in limits.extensions)},
        )
    return limits


def upload_limits_by_purpose(purpose: str) -> list[dict[str, object]]:
    """Describe the limits for dashboard and picker labels without a file."""

    configured = PURPOSE_LIMITS.get(purpose)
    if not configured:
        return []
    families = configured.get("families") or {}
    if families:
        return [
            {
                "family": name,
                "max_bytes": int(family["max_bytes"]),
                "extensions": tuple(family["extensions"]),
                "audio_seconds": family.get("audio_seconds"),
            }
            for name, family in families.items()
        ]
    return [
        {
            "family": "file",
            "max_bytes": int(configured["max_bytes"]),
            "extensions": tuple(configured["extensions"]),
            "audio_seconds": configured.get("audio_seconds"),
        }
    ]


def _storage_target(purpose: str, target: str) -> str:
    """Build the exact private folder for one already-authorized target."""

    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(target or "").strip())[:120]
    if not normalized:
        raise DirectUploadError("missing_target", _("An upload target is required."))
    return f"{STAGING_PREFIX}/{purpose}/{normalized}"


def _final_folder(purpose: str, target: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(target or "").strip())[:120]
    return f"{FINAL_PREFIX}/{purpose}/{normalized}"


def _presigned_put(*, client, bucket: str, key: str, content_type: str) -> str:
    try:
        return client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise DirectUploadError(
            "storage_unavailable", _("File storage is unavailable. Try again shortly.")
        ) from exc


def _bucket_name() -> str:
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    if not bucket:
        raise DirectUploadError(
            "storage_unavailable", _("File storage is not configured.")
        )
    return bucket


def purpose_bucket(purpose: str) -> str:
    """Return the provider bucket a purpose writes to.

    Support attachments can live in their own private bucket, exactly as the
    support chat already stores them; every other purpose uses the media
    bucket. One rule for authorize and finalize.
    """

    if purpose == DirectUploadAuthorization.Purpose.SUPPORT_ATTACHMENT:
        dedicated = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "") or ""
        if dedicated:
            return dedicated
    return _bucket_name()



def authorize_direct_upload(
    *,
    actor,
    purpose: str,
    target: str,
    filename: str,
    size: int,
    content_type: str,
    metadata: dict | None = None,
) -> DirectUploadGrant:
    """Create one short-lived capability for an exact private staging key.

    The browser is told a size and type limit; both are enforced again from the
    real stored object at finalize, because a presigned URL constrains the key
    and expiry but not the bytes the client eventually sends.
    """

    limits = upload_limits(purpose=purpose, content_type=content_type, filename=filename)
    try:
        declared_size = int(size)
    except (TypeError, ValueError) as exc:
        raise DirectUploadError("invalid_size", _("A valid file size is required.")) from exc
    if declared_size <= 0:
        raise DirectUploadError("invalid_size", _("A valid file size is required."))
    if declared_size > limits.max_bytes:
        raise DirectUploadError(
            "file_too_large",
            _("This file exceeds the %(limit)s MB limit for this field.")
            % {"limit": round(limits.max_bytes / MEGABYTE, 2)},
        )

    bucket = purpose_bucket(purpose)
    client = get_r2_client()
    if client is None:
        raise DirectUploadError(
            "storage_unavailable", _("File storage is unavailable. Try again shortly.")
        )

    token = secrets.token_urlsafe(24)
    safe_name = _safe_filename(filename, limits.extensions)
    folder = _storage_target(purpose, target)
    staging_key = f"{folder}/{token[:12]}-{safe_name}"
    expires_at = timezone.now() + timedelta(seconds=UPLOAD_URL_TTL_SECONDS)

    with transaction.atomic():
        lock_storage_namespace(staging_key)
        assert_storage_namespace_available(staging_key, check_media_outputs=False)
        DirectUploadAuthorization.objects.create(
            token=token,
            actor=actor if getattr(actor, "pk", None) else None,
            target=str(target or ""),
            purpose=purpose,
            staging_key=staging_key,
            bucket=bucket,
            declared_filename=safe_name,
            declared_content_type=str(content_type or "").split(";")[0].strip().lower(),
            declared_size=declared_size,
            max_bytes=limits.max_bytes,
            metadata=dict(metadata or {}),
            expires_at=expires_at,
        )

    upload_url = _presigned_put(
        client=client,
        bucket=bucket,
        key=staging_key,
        content_type=content_type,
    )
    return DirectUploadGrant(
        token=token,
        upload_url=upload_url,
        staging_key=staging_key,
        expires_at=expires_at.isoformat(),
        max_bytes=limits.max_bytes,
        content_types=limits.content_types,
        extensions=limits.extensions,
        audio_seconds=limits.audio_seconds,
    )


class _StagedUpload:
    """Minimal uploaded-file shape accepted by the existing domain validators."""

    def __init__(self, handle, *, name: str, content_type: str, size: int):
        self.file = handle
        self.name = name
        self.content_type = content_type
        self.size = size

    def read(self, *args):
        return self.file.read(*args)

    def seek(self, *args):
        return self.file.seek(*args)

    def tell(self):
        return self.file.tell()

    def __iter__(self):
        return iter(self.file)

    def chunks(self, chunk_size=DOWNLOAD_CHUNK_BYTES):
        while True:
            chunk = self.file.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _download_to_temp(*, client, bucket: str, key: str, expected_size: int) -> tuple[str, str, int]:
    """Stream one stored object to a bounded temporary file and verify length."""

    destination = tempfile.NamedTemporaryFile(delete=False, suffix=".upload")
    written = 0
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            while True:
                chunk = body.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_size:
                    raise DirectUploadError(
                        "file_too_large",
                        _("The uploaded file is larger than the declared size."),
                    )
                destination.write(chunk)
        finally:
            body.close()
        destination.flush()
        destination.close()
        return destination.name, str(response.get("ContentType") or ""), written
    except DirectUploadError:
        destination.close()
        os.unlink(destination.name)
        raise
    except Exception as exc:  # noqa: BLE001 - provider boundary
        try:
            destination.close()
        finally:
            if os.path.exists(destination.name):
                os.unlink(destination.name)
        raise DirectUploadError(
            "object_missing", _("The uploaded file was not found. Please upload it again.")
        ) from exc


def _publish_final(*, client, bucket: str, temp_path: str, final_key: str, content_type: str) -> None:
    """Upload exactly the validated local bytes to a new immutable key."""

    try:
        with open(temp_path, "rb") as handle:
            client.upload_fileobj(
                handle,
                bucket,
                final_key,
                ExtraArgs={"ContentType": content_type},
            )
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise DirectUploadError(
            "storage_unavailable", _("File storage is unavailable. Try again shortly.")
        ) from exc


def _finalize_direct_upload(
    *,
    actor,
    token: str,
    validator: Callable[[object], None] | None = None,
    expected_purpose: str | None = None,
    metadata: dict | None = None,
    final_folder: str | None = None,
) -> FinalizedUpload:
    """Validate the stored object and publish those exact bytes to a final key.

    Idempotent: finalizing an already-finalized capability returns the same
    object reference instead of publishing twice or re-validating.
    """

    client = get_r2_client()
    if client is None:
        raise DirectUploadError(
            "storage_unavailable", _("File storage is unavailable. Try again shortly.")
        )
    now = timezone.now()
    with transaction.atomic():
        authorization = (
            DirectUploadAuthorization.objects.select_for_update()
            .filter(token=str(token or ""))
            .first()
        )
        if authorization is None:
            raise DirectUploadError(
                "not_found", _("This upload is no longer available. Please upload the file again.")
            )
        # The bucket is part of the capability, never taken from the request.
        bucket = authorization.bucket or _bucket_name()
        if authorization.actor_id != getattr(actor, "pk", None):
            raise DirectUploadError("forbidden", _("This upload belongs to another account."))
        if expected_purpose and authorization.purpose != expected_purpose:
            raise DirectUploadError("forbidden", _("This upload cannot be used here."))
        if authorization.status == DirectUploadAuthorization.Status.FINALIZED:
            return FinalizedUpload(
                token=authorization.token,
                key=authorization.final_key,
                original_filename=authorization.declared_filename,
                content_type=authorization.declared_content_type,
                size=authorization.declared_size,
                metadata=dict(authorization.metadata or {}),
            )
        if authorization.status != DirectUploadAuthorization.Status.PENDING:
            raise DirectUploadError(
                "expired", _("This upload expired. Please upload the file again.")
            )
        if now > authorization.expires_at + FINALIZE_GRACE:
            raise DirectUploadError(
                "expired", _("This upload expired. Please upload the file again.")
            )
        try:
            head = client.head_object(Bucket=bucket, Key=authorization.staging_key)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise DirectUploadError(
                "object_missing", _("The uploaded file was not found. Upload it again.")
            ) from exc
        real_size = int(head.get("ContentLength") or 0)
        real_type = str(head.get("ContentType") or "").split(";")[0].strip().lower()
        if real_size <= 0 or real_size > authorization.max_bytes:
            _delete_staging(client, bucket, authorization.staging_key)
            raise DirectUploadError(
                "file_too_large",
                _("This file exceeds the allowed size for this field."),
            )
        if real_size != authorization.declared_size:
            # The declared size is part of the capability: a transfer that does
            # not match it is either truncated or padded, so it is never
            # published under the user's original authorization.
            _delete_staging(client, bucket, authorization.staging_key)
            raise DirectUploadError(
                "size_mismatch",
                _("The uploaded file did not arrive completely. Please upload it again."),
            )
        if real_type and real_type != authorization.declared_content_type:
            try:
                limits = _family_limits(authorization.purpose, real_type)
            except DirectUploadError:
                limits = None
            if limits is None or real_type not in limits.content_types:
                _delete_staging(client, bucket, authorization.staging_key)
                raise DirectUploadError(
                    "unsupported_type", _("This file type is not allowed.")
                )
            authorization.declared_content_type = real_type

        temp_path = None
        try:
            temp_path, _stored_type, written = _download_to_temp(
                client=client,
                bucket=bucket,
                key=authorization.staging_key,
                expected_size=authorization.declared_size,
            )
            if written != real_size:
                raise DirectUploadError(
                    "size_mismatch",
                    _("The uploaded file changed during validation. Please upload it again."),
                )
            if validator is not None:
                with open(temp_path, "rb") as probe:
                    validator(
                        _StagedUpload(
                            probe,
                            name=authorization.declared_filename,
                            content_type=authorization.declared_content_type,
                            size=written,
                        )
                    )
            final_key = (
                f"{(final_folder or _final_folder(authorization.purpose, authorization.target)).rstrip('/')}"
                f"/{uuid.uuid4().hex}-{authorization.declared_filename}"
            )
            lock_storage_namespace(final_key)
            assert_storage_namespace_available(final_key, check_media_outputs=False)
            _publish_final(
                client=client,
                bucket=bucket,
                temp_path=temp_path,
                final_key=final_key,
                content_type=authorization.declared_content_type,
            )
        except Exception:
            _delete_staging(client, bucket, authorization.staging_key)
            raise
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        if metadata:
            authorization.metadata = {**dict(authorization.metadata or {}), **metadata}
        authorization.final_key = final_key
        authorization.status = DirectUploadAuthorization.Status.FINALIZED
        authorization.finalized_at = now
        authorization.failure_reason = ""
        authorization.save(
            update_fields=[
                "final_key",
                "status",
                "finalized_at",
                "failure_reason",
                "metadata",
                "declared_content_type",
            ]
        )

    transaction.on_commit(
        lambda: _delete_staging_safely(client, bucket, authorization.staging_key)
    )
    return FinalizedUpload(
        token=authorization.token,
        key=final_key,
        original_filename=authorization.declared_filename,
        content_type=authorization.declared_content_type,
        size=real_size,
        metadata=dict(authorization.metadata or {}),
    )


TERMINAL_FAILURE_CODES = frozenset(
    {
        "file_too_large",
        "size_mismatch",
        "unsupported_type",
        "validation_failed",
        "expired",
    }
)


def _persist_failure(*, token: str, actor, code: str) -> None:
    """Record a terminal failure after the failed attempt's transaction rolled back.

    The attempt itself runs inside ``atomic()`` so a rejected upload cannot leave
    a partial row, which also means its own status write is rolled back. This
    runs afterwards in a fresh transaction, and only for a row that still
    belongs to the same actor and is still pending.
    """

    normalized = str(code or "validation_failed")[:200]
    if normalized not in TERMINAL_FAILURE_CODES:
        return
    target_status = (
        DirectUploadAuthorization.Status.EXPIRED
        if normalized == "expired"
        else DirectUploadAuthorization.Status.FAILED
    )
    try:
        with transaction.atomic():
            DirectUploadAuthorization.objects.filter(
                token=str(token or ""),
                actor_id=getattr(actor, "pk", None),
                status=DirectUploadAuthorization.Status.PENDING,
            ).update(status=target_status, failure_reason=normalized)
    except Exception:  # noqa: BLE001 - never mask the original failure
        logger.warning("direct upload failure state could not be recorded")


def finalize_direct_upload(
    *,
    actor,
    token: str,
    validator: Callable[[object], None] | None = None,
    expected_purpose: str | None = None,
    metadata: dict | None = None,
    final_folder: str | None = None,
) -> FinalizedUpload:
    """Finalize one capability, persisting a terminal failure outside its transaction."""

    try:
        return _finalize_direct_upload(
            actor=actor,
            token=token,
            validator=validator,
            expected_purpose=expected_purpose,
            metadata=metadata,
            final_folder=final_folder,
        )
    except DirectUploadError as exc:
        _persist_failure(token=token, actor=actor, code=exc.code)
        raise
    except Exception as exc:
        _persist_failure(
            token=token,
            actor=actor,
            code=str(getattr(exc, "code", "") or "validation_failed"),
        )
        raise


def _delete_staging(client, bucket: str, key: str) -> None:
    try:
        delete_from_bucket(client, bucket, key)
    except Exception:  # noqa: BLE001 - cleanup is best effort and observable elsewhere
        pass


def _delete_staging_safely(client, bucket: str, key: str) -> None:
    _delete_staging(client, bucket, key)


def discard_direct_upload(*, actor, token: str) -> None:
    """Drop one pending capability and its staged object after a cancelled form."""

    authorization = DirectUploadAuthorization.objects.filter(
        token=str(token or ""),
        actor_id=getattr(actor, "pk", None),
        status=DirectUploadAuthorization.Status.PENDING,
    ).first()
    if authorization is None:
        return
    authorization.status = DirectUploadAuthorization.Status.EXPIRED
    authorization.failure_reason = "cancelled"
    authorization.save(update_fields=["status", "failure_reason"])
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    if bucket:
        _delete_staging(get_r2_client(), bucket, authorization.staging_key)


def expire_abandoned_direct_uploads(*, limit: int = MAX_SWEEP_ROWS) -> dict[str, int]:
    """Expire a bounded batch of abandoned capabilities and their staged bytes."""

    bounded = max(1, min(int(limit), MAX_SWEEP_ROWS))
    now = timezone.now()
    stale = list(
        DirectUploadAuthorization.objects.filter(
            status=DirectUploadAuthorization.Status.PENDING,
            expires_at__lt=now - FINALIZE_GRACE,
        )
        .order_by("expires_at")
        .values_list("pk", "staging_key")[:bounded]
    )
    if not stale:
        return {"expired": 0, "keys_removed": 0}
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    client = get_r2_client() if bucket else None
    removed = 0
    for _pk, key in stale:
        if client is not None and key:
            _delete_staging(client, bucket, key)
            removed += 1
    DirectUploadAuthorization.objects.filter(pk__in=[pk for pk, _key in stale]).update(
        status=DirectUploadAuthorization.Status.EXPIRED,
        failure_reason="expired_without_finalize",
    )
    return {"expired": len(stale), "keys_removed": removed}
