import os
from logging import getLogger
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .storage_operations import upload_to_bucket

logger = getLogger(__name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def validate_application_file(file_obj):
    ext = os.path.splitext(file_obj.name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(_("File type %(ext)s is not allowed. Allowed: JPG, PNG, PDF.") % {"ext": ext})
    if file_obj.content_type not in ALLOWED_MIME_TYPES:
        raise ValidationError(_("Unexpected content type."))
    if file_obj.size > MAX_FILE_SIZE:
        raise ValidationError(_("File size exceeds 10 MB limit."))
    # Check file header signature
    header = file_obj.read(8)
    file_obj.seek(0)
    is_jpeg = header[:2] == b'\xff\xd8'
    is_png = header[:8] == b'\x89PNG\r\n\x1a\n'
    is_pdf = header[:4] == b'%PDF'
    if not (is_jpeg or is_png or is_pdf):
        raise ValidationError(_("File content does not match allowed types."))


def _safe_key(prefix, file_obj):
    ext = os.path.splitext(file_obj.name)[1].lower()
    # Use a safe generated name instead of the original filename
    import uuid
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return f"{prefix}/{safe_name}"


def upload_application_file(cloud_client, bucket_name, user_id, file_obj, file_type):
    validate_application_file(file_obj)
    prefix = f"applications/{user_id}/{file_type}"
    key = _safe_key(prefix, file_obj)
    success = upload_to_bucket(cloud_client, bucket_name, file_obj, key)
    if success:
        return key
    logger.error(f"Failed to upload {file_type} for user {user_id}")
    return None
