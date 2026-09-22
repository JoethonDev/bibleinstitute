import os
import uuid
import warnings
from dataclasses import dataclass
from io import BytesIO
from logging import getLogger

from PIL import Image, ImageOps, UnidentifiedImageError

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .storage_operations import delete_from_bucket, upload_to_bucket

logger = getLogger(__name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}
PREVIEW_QUALITY = 88

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


@dataclass(frozen=True)
class UploadedApplicationFile:
    """The preserved original and its optional private display derivative."""

    original_key: str
    preview_key: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key in (self.original_key, self.preview_key) if key)


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
        delete_from_bucket(cloud_client, bucket_name, original_key)
        logger.error("Failed to upload image preview for %s and user %s", file_type, user_id)
        return None

    return UploadedApplicationFile(original_key=original_key, preview_key=preview_key)
