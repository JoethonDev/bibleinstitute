import os
import uuid
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from io import BytesIO
from logging import getLogger

from PIL import Image, ImageOps, UnidentifiedImageError

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from ..direct_uploads import DirectUploadError, finalize_direct_upload
from ..models import DirectUploadAuthorization
from ..storage_coordination import assert_storage_namespace_available, lock_storage_namespace
from .storage_operations import delete_from_bucket, upload_to_bucket

logger = getLogger(__name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
PREVIEW_QUALITY = 88


def lock_application_document_namespace(user_id: int) -> None:
    """Reserve one user's private document folder before locking database rows.

    Call inside ``transaction.atomic()`` and before any user/payment row lock.
    The order matches generic storage rename/delete, which holds path locks
    before updating document references.
    """

    folder = f"applications/{user_id}"
    lock_storage_namespace(folder)
    assert_storage_namespace_available(folder)

APPLICATION_DOCUMENT_FIELDS = {
    "identity_front": "identity_front_key",
    "identity_back": "identity_back_key",
    "payment": "payment_key",
    "profile": "profile_image_key",
}
APPLICATION_PREVIEW_FIELDS = {
    "identity_front": "identity_front_preview_key",
    "identity_back": "identity_back_preview_key",
    "payment": "payment_preview_key",
    "profile": "profile_image_preview_key",
}
APPLICATION_DOCUMENT_LABELS = {
    "identity_front": _("Identity Front"),
    "identity_back": _("Identity Back"),
    "payment": _("Payment"),
    "profile": _("Profile"),
}


@dataclass(frozen=True)
class UploadedApplicationFile:
    """The preserved original and its optional private display derivative."""

    original_key: str
    preview_key: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key in (self.original_key, self.preview_key) if key)


@dataclass(frozen=True)
class StagedApplicationDocuments:
    """Files uploaded before the database swap, with their rollback keys."""

    uploads: Mapping[str, UploadedApplicationFile]
    keys: tuple[str, ...]


@dataclass(frozen=True)
class ApplicationDocumentChange:
    """Old keys safe to clean after commit and the newly referenced keys."""

    old_keys: tuple[str, ...]
    new_keys: tuple[str, ...]


def validate_application_file(file_obj):
    ext = os.path.splitext(file_obj.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(_("File type %(ext)s is not allowed. Allowed: JPG, PNG, PDF.") % {"ext": ext})
    if file_obj.content_type not in ALLOWED_MIME_TYPES:
        raise ValidationError(_("Unexpected content type."))
    if ext == ".pdf" and file_obj.content_type != "application/pdf":
        raise ValidationError(_("Unexpected content type."))
    if ext in IMAGE_EXTENSIONS and file_obj.content_type not in IMAGE_MIME_TYPES:
        raise ValidationError(_("Unexpected content type."))
    if file_obj.size > MAX_FILE_SIZE:
        raise ValidationError(_("File size exceeds 10 MB limit."))
    # Check file header signature
    header = file_obj.read(8)
    file_obj.seek(0)
    is_jpeg = header[:2] == b'\xff\xd8'
    is_png = header[:8] == b'\x89PNG\r\n\x1a\n'
    is_pdf = header[:4] == b'%PDF'
    matches_extension = (
        (ext in {".jpg", ".jpeg"} and is_jpeg)
        or (ext == ".png" and is_png)
        or (ext == ".pdf" and is_pdf)
    )
    if not matches_extension:
        raise ValidationError(_("File content does not match allowed types."))
    if ext in IMAGE_EXTENSIONS:
        try:
            file_obj.seek(0)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(file_obj) as image:
                    image.verify()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ):
            raise ValidationError(_("Image content could not be read."))
        finally:
            file_obj.seek(0)


def _safe_key(prefix, file_obj):
    ext = os.path.splitext(file_obj.name)[1].lower()
    # Use a safe generated name instead of the original filename
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return f"{prefix}/{safe_name}"


def _build_image_preview(file_obj) -> BytesIO:
    file_obj.seek(0)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(file_obj) as source:
            transposed = ImageOps.exif_transpose(source)
            image = transposed if transposed is not None else source
            if image.mode in {"RGBA", "LA", "P"}:
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")
            preview = BytesIO()
            image.save(preview, format="WEBP", quality=PREVIEW_QUALITY, method=6)
    preview.seek(0)
    return preview


def upload_application_file(cloud_client, bucket_name, user_id, file_obj, file_type):
    validate_application_file(file_obj)
    prefix = f"applications/{user_id}/{file_type}"
    original_key = _safe_key(prefix, file_obj)
    preview_key = None
    preview = None
    if os.path.splitext(file_obj.name)[1].lower() in IMAGE_EXTENSIONS:
        try:
            preview = _build_image_preview(file_obj)
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ):
            logger.exception("Failed to create application image preview for user %s", user_id)
            return None
        preview_key = f"{os.path.splitext(original_key)[0]}.webp"

    file_obj.seek(0)
    original_uploaded = upload_to_bucket(
        cloud_client,
        bucket_name,
        file_obj,
        original_key,
        content_type=file_obj.content_type,
    )
    if not original_uploaded:
        logger.error("Failed to upload %s for user %s", file_type, user_id)
        return None

    if preview_key and not upload_to_bucket(
        cloud_client,
        bucket_name,
        preview,
        preview_key,
        content_type="image/webp",
    ):
        if not delete_from_bucket(cloud_client, bucket_name, original_key):
            # Cleanup failure stays observable; the upload still fails closed.
            logger.warning(
                "application upload rollback cleanup failed",
                extra={"event": "application_upload_rollback_cleanup_failed", "file_type": file_type},
            )
        logger.error("Failed to upload image preview for %s and user %s", file_type, user_id)
        return None

    return UploadedApplicationFile(original_key=original_key, preview_key=preview_key)


def delete_application_files(cloud_client, bucket_name, keys: Iterable[str]) -> list[str]:
    """Delete staged/replaced application objects and report failed keys.

    The return value is the aggregate cleanup status for callers that want to
    record or retry; a provider failure never rolls back an already committed
    user change and never deletes the currently referenced document.
    """

    failed = []
    for key in sorted(set(keys)):
        if not delete_from_bucket(cloud_client, bucket_name, key):
            failed.append(key)
    if failed:
        # Bounded record: key list is private application metadata.
        logger.warning(
            "application file cleanup failed",
            extra={
                "event": "application_cleanup_failed",
                "failed_count": len(failed),
                "failed_keys": failed[:20],
            },
        )
    return failed


def _preview_from_finalized(
    *,
    cloud_client,
    bucket_name: str,
    user_id: int,
    document_type: str,
    finalized,
) -> str | None:
    """Build the private display derivative for one already-stored document."""

    extension = os.path.splitext(finalized.original_filename)[1].lower()
    if extension not in IMAGE_EXTENSIONS:
        return None
    try:
        response = cloud_client.get_object(Bucket=bucket_name, Key=finalized.key)
        body = response["Body"]
        try:
            payload = body.read(MAX_FILE_SIZE + 1)
        finally:
            body.close()
    except Exception as exc:  # noqa: BLE001 - provider boundary
        logger.exception("application preview download failed")
        raise ValidationError(_("The uploaded document could not be read.")) from exc
    if len(payload) > MAX_FILE_SIZE:
        raise ValidationError(_("File size exceeds 10 MB limit."))
    try:
        preview = _build_image_preview(BytesIO(payload))
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise ValidationError(_("Image content could not be read.")) from exc
    preview_key = (
        f"applications/{user_id}/{document_type}/{uuid.uuid4().hex}.webp"
    )
    if not upload_to_bucket(
        cloud_client,
        bucket_name,
        preview,
        preview_key,
        content_type="image/webp",
    ):
        raise ValidationError(_("The profile preview could not be stored."))
    return preview_key


def finalize_application_documents(
    *,
    actor,
    tokens: Mapping[str, str],
    cloud_client,
    bucket_name: str,
    user_id: int,
) -> dict[str, UploadedApplicationFile]:
    """Validate already-uploaded browser documents and build their previews.

    The bytes were validated and published by ``finalize_direct_upload`` using
    the canonical ``validate_application_file`` rule; this only adds the private
    display derivative the application screens already expect. Any failure
    removes the objects this call produced so nothing is left unreferenced.
    """

    finalized: dict[str, UploadedApplicationFile] = {}
    created_keys: list[str] = []
    try:
        for document_type in APPLICATION_DOCUMENT_FIELDS:
            token = str(tokens.get(document_type) or "").strip()
            if not token:
                continue
            try:
                upload = finalize_direct_upload(
                    actor=actor,
                    token=token,
                    validator=validate_application_file,
                    expected_purpose=DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT,
                    metadata={"document_type": document_type},
                    final_folder=f"applications/{user_id}/{document_type}",
                )
            except DirectUploadError as exc:
                raise ValidationError(exc.message) from exc
            preview_key = _preview_from_finalized(
                cloud_client=cloud_client,
                bucket_name=bucket_name,
                user_id=user_id,
                document_type=document_type,
                finalized=upload,
            )
            item = UploadedApplicationFile(
                original_key=upload.key,
                preview_key=preview_key,
            )
            finalized[document_type] = item
            created_keys.extend(item.keys)
    except Exception:
        delete_application_files(cloud_client, bucket_name, created_keys)
        raise
    return finalized


def stage_application_documents(
    *,
    cloud_client,
    bucket_name: str,
    user_id: int,
    cleaned_data: Mapping,
    finalized: Mapping[str, UploadedApplicationFile] | None = None,
) -> StagedApplicationDocuments:
    """Upload every accepted document before the locked database swap.

    Documents already uploaded by the browser are passed in ``finalized`` and
    are not uploaded again. Any failure deletes the files staged so far and
    raises one translated validation error; the caller never receives a partial
    stage.
    """

    uploads: dict[str, UploadedApplicationFile] = dict(finalized or {})
    uploaded_keys: list[str] = [
        key for item in uploads.values() for key in item.keys
    ]
    try:
        for document_type in APPLICATION_DOCUMENT_FIELDS:
            if document_type in uploads:
                continue
            uploaded_file = cleaned_data.get(document_type)
            if not uploaded_file:
                continue
            uploaded = upload_application_file(
                cloud_client,
                bucket_name,
                user_id,
                uploaded_file,
                document_type,
            )
            if uploaded is None:
                raise ValidationError(
                    _("The %(document)s could not be uploaded.")
                    % {"document": APPLICATION_DOCUMENT_LABELS[document_type]}
                )
            uploads[document_type] = uploaded
            uploaded_keys.extend(uploaded.keys)
    except Exception:
        delete_application_files(cloud_client, bucket_name, uploaded_keys)
        raise
    return StagedApplicationDocuments(
        uploads=uploads,
        keys=tuple(dict.fromkeys(uploaded_keys)),
    )


def apply_application_document_changes(
    user,
    staged: StagedApplicationDocuments,
    cleaned_data: Mapping,
) -> ApplicationDocumentChange:
    """Swap document references on an already-locked user and save just those fields.

    The caller owns the surrounding ``transaction.atomic()`` and the row lock;
    this operation never deletes a storage object.
    """

    old_keys: list[str] = []
    affected_keys: list[str] = []
    clear_types = {
        document_type
        for document_type in APPLICATION_DOCUMENT_FIELDS
        if cleaned_data.get(f"clear_{document_type}")
    }
    for document_type, model_field in APPLICATION_DOCUMENT_FIELDS.items():
        preview_field = APPLICATION_PREVIEW_FIELDS[document_type]
        previous_key = getattr(user, model_field, None)
        previous_preview_key = getattr(user, preview_field, None)
        uploaded = staged.uploads.get(document_type)
        if document_type in clear_types or uploaded:
            affected_keys.extend(key for key in (previous_key, previous_preview_key) if key)
            if uploaded:
                affected_keys.extend(uploaded.keys)

    if affected_keys:
        # The provider objects were staged before this short DB transition.
        # Recheck under the shared namespace lock so a storage move/delete
        # cannot race the durable application-document references.
        lock_storage_namespace(*affected_keys)
        assert_storage_namespace_available(*affected_keys)

    for document_type, model_field in APPLICATION_DOCUMENT_FIELDS.items():
        preview_field = APPLICATION_PREVIEW_FIELDS[document_type]
        previous_key = getattr(user, model_field, None)
        previous_preview_key = getattr(user, preview_field, None)
        if document_type in clear_types:
            old_keys.extend(key for key in (previous_key, previous_preview_key) if key)
            setattr(user, model_field, None)
            setattr(user, preview_field, None)
        uploaded = staged.uploads.get(document_type)
        if uploaded:
            old_keys.extend(
                key
                for key in (previous_key, previous_preview_key)
                if key and key not in uploaded.keys
            )
            setattr(user, model_field, uploaded.original_key)
            setattr(user, preview_field, uploaded.preview_key)
    if staged.uploads or clear_types:
        update_fields = [
            *APPLICATION_DOCUMENT_FIELDS.values(),
            *APPLICATION_PREVIEW_FIELDS.values(),
        ]
        user.save(update_fields=update_fields)
    return ApplicationDocumentChange(
        old_keys=tuple(dict.fromkeys(key for key in old_keys if key)),
        new_keys=staged.keys,
    )


def cleanup_replaced_application_documents(
    change: ApplicationDocumentChange,
    cloud_client,
    bucket_name: str,
) -> list[str]:
    """Delete replaced objects only after the database swap committed."""

    return delete_application_files(cloud_client, bucket_name, change.old_keys)


def rollback_staged_application_documents(
    staged: StagedApplicationDocuments,
    cloud_client,
    bucket_name: str,
) -> list[str]:
    """Delete newly uploaded objects when the database swap did not commit."""

    return delete_application_files(cloud_client, bucket_name, staged.keys)
