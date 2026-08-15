"""
Bounded shared R2 storage helpers for the server-side FFmpeg migration.

Storage-only module. It never creates Django views, Celery tasks, model
transitions, lesson attachments, or batch behavior, and it never reads or
writes job state. R2 access uses the shared factory
``management_system.utils.storage_operations.get_r2_client()`` and
``settings.R2_BUCKET_NAME`` by default; an explicit ``client``/``bucket`` may
be supplied for isolated verification.

Contracts implemented here:

- Private staging keys are exactly ``Raw Files/<job_uuid>/<safe_filename>``.
- Short-lived direct PUT authorization for a staging key.
- Bounded ``head_object`` metadata verification for staging and outputs.
- Streamed staging download to a caller-provided local path.
- Collision-rejected output uploads from local files or file objects.
- Exact-key cleanup only (no prefix listing or prefix deletion).

Every failure raises :class:`MediaStorageError` with a stable machine-readable
``code``. Existing objects are never overwritten or deleted by upload helpers;
a destination collision raises ``output_collision`` and leaves the existing
object untouched.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any, Optional

from django.conf import settings

from management_system.utils.storage_operations import get_r2_client

# Private staging prefix reserved by the approved migration proposal.
STAGING_PREFIX = "Raw Files"
_STAGING_PREFIX_SLASH = STAGING_PREFIX + "/"

# Stable machine-readable error codes.
ERR_STAGING_FILENAME = "invalid_staging_filename"
ERR_STAGING_KEY = "invalid_staging_key"
ERR_OUTPUT_KEY = "invalid_output_key"
ERR_CONFIG = "storage_configuration"
ERR_STORAGE = "storage_client_error"
ERR_STAGING_MISSING = "staging_object_missing"
ERR_STAGING_SIZE_MISMATCH = "staging_size_mismatch"
ERR_STAGING_ETAG_MISMATCH = "staging_etag_mismatch"
ERR_STAGING_INCOMPLETE = "staging_object_incomplete"
ERR_DOWNLOAD = "staging_download_failed"
ERR_OUTPUT_MISSING = "output_object_missing"
ERR_OUTPUT_COLLISION = "output_collision"
ERR_OUTPUT_SIZE_MISMATCH = "output_size_mismatch"
ERR_OUTPUT_CONTENT_TYPE_MISMATCH = "output_content_type_mismatch"

_MEDIA_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
}

_MAX_STAGING_FILENAME_LENGTH = 255
_MAX_KEY_LENGTH = 1024
_MAX_JOB_UUID_LENGTH = 128
_DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
_DEFAULT_PUT_EXPIRES = 900  # seconds, short-lived direct PUT authorization
_MIN_PUT_EXPIRES = 60
_MAX_PUT_EXPIRES = 3600

_MISSING_CODES = ("404", "NoSuchKey", "NotFound")


class MediaStorageError(Exception):
    """Bounded R2 storage failure with a stable machine-readable code."""

    def __init__(self, message: str, code: str = ERR_STORAGE):
        super().__init__(message)
        self.code = code
        self.message = message


def content_type_for_key(key: str) -> str:
    """Return the canonical MIME type for a generated media key."""
    extension = posixpath.splitext(key)[1].lower()
    return _MEDIA_CONTENT_TYPES.get(extension, "application/octet-stream")


def normalize_staging_filename(filename: Any) -> str:
    """
    Return the safe deterministic basename for a staging source filename.

    Rejects empty, absolute, path-containing (``/`` or ``\\``), traversal
    (``.``/``..``), control-character, query/fragment (``?``/``#``), and
    overlong filenames. Because path separators are rejected, the returned
    basename is exactly the input unchanged.
    """
    if not isinstance(filename, str) or not filename:
        raise MediaStorageError(
            "Staging filename must be a non-empty string.", ERR_STAGING_FILENAME
        )
    if filename.startswith("/") or "/" in filename or "\\" in filename:
        raise MediaStorageError(
            "Staging filename must not contain path separators.", ERR_STAGING_FILENAME
        )
    if posixpath.basename(filename) != filename:
        raise MediaStorageError(
            "Staging filename must be a plain basename.", ERR_STAGING_FILENAME
        )
    if filename in (".", ".."):
        raise MediaStorageError(
            "Staging filename must not contain traversal components.",
            ERR_STAGING_FILENAME,
        )
    if "?" in filename or "#" in filename:
        raise MediaStorageError(
            "Staging filename must not contain query or fragment characters.",
            ERR_STAGING_FILENAME,
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise MediaStorageError(
            "Staging filename must not contain control characters.",
            ERR_STAGING_FILENAME,
        )
    if len(filename) > _MAX_STAGING_FILENAME_LENGTH:
        raise MediaStorageError(
            f"Staging filename exceeds {_MAX_STAGING_FILENAME_LENGTH} characters.",
            ERR_STAGING_FILENAME,
        )
    return filename


def build_staging_key(job_uuid: Any, original_filename: Any) -> str:
    """
    Build the exact private staging key ``Raw Files/<job_uuid>/<safe_filename>``.

    ``job_uuid`` must be a non-empty safe single path component. The filename
    is validated through :func:`normalize_staging_filename`. This helper never
    accepts a client-supplied destination prefix.
    """
    if not isinstance(job_uuid, str) or not job_uuid:
        raise MediaStorageError(
            "Job UUID must be a non-empty string.", ERR_STAGING_KEY
        )
    if "/" in job_uuid or "\\" in job_uuid or job_uuid in (".", ".."):
        raise MediaStorageError(
            "Job UUID must not contain path separators or traversal components.",
            ERR_STAGING_KEY,
        )
    if "?" in job_uuid or "#" in job_uuid:
        raise MediaStorageError(
            "Job UUID must not contain query or fragment characters.",
            ERR_STAGING_KEY,
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in job_uuid):
        raise MediaStorageError(
            "Job UUID must not contain control characters.", ERR_STAGING_KEY
        )
    if len(job_uuid) > _MAX_JOB_UUID_LENGTH:
        raise MediaStorageError(
            f"Job UUID exceeds {_MAX_JOB_UUID_LENGTH} characters.", ERR_STAGING_KEY
        )
    safe_filename = normalize_staging_filename(original_filename)
    key = f"{_STAGING_PREFIX_SLASH}{job_uuid}/{safe_filename}"
    _validate_staging_key(key)
    return key


def create_staging_upload_url(
    staging_key: Any,
    *,
    client: Any = None,
    bucket: Optional[str] = None,
    content_type: Optional[str] = None,
    expires_in: int = _DEFAULT_PUT_EXPIRES,
) -> dict:
    """
    Return short-lived direct PUT authorization for a private staging key.

    The ``ContentType`` is included in the signature, so the uploading client
    must send the same ``Content-Type`` header with the PUT request. When no
    ``content_type`` is supplied it is derived from the key extension.
    ``expires_in`` is clamped to the 60-3600 second bound.
    """
    key = _validate_staging_key(staging_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    if (
        not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or not (_MIN_PUT_EXPIRES <= expires_in <= _MAX_PUT_EXPIRES)
    ):
        expires_in = _DEFAULT_PUT_EXPIRES
    media_type = content_type or content_type_for_key(key)
    try:
        url = resolved_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": resolved_bucket, "Key": key, "ContentType": media_type},
            ExpiresIn=expires_in,
        )
        return {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": media_type},
        }
    except Exception as exc:
        raise MediaStorageError(
            f"Could not authorize staging upload: {exc}", ERR_STORAGE
        ) from exc


def verify_staging_object(
    staging_key: Any,
    *,
    expected_size: Optional[int] = None,
    expected_etag: Optional[str] = None,
    client: Any = None,
    bucket: Optional[str] = None,
) -> dict:
    """
    Verify a staged source object with ``head_object`` and return metadata.

    Returns ``{"size", "etag", "content_type", "last_modified"}``. Raises
    ``staging_object_missing`` when absent, ``staging_object_incomplete`` for a
    zero-length object, ``staging_size_mismatch`` or ``staging_etag_mismatch``
    when an expected value is supplied and does not match.
    """
    key = _validate_staging_key(staging_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    try:
        response = resolved_client.head_object(Bucket=resolved_bucket, Key=key)
    except Exception as exc:
        _raise_not_found_or_storage(exc, ERR_STAGING_MISSING, "Staging object")
    metadata = _metadata_from_head(response)
    if metadata["size"] == 0:
        raise MediaStorageError(
            "Staging object is empty; the upload is incomplete.",
            ERR_STAGING_INCOMPLETE,
        )
    if expected_size is not None and metadata["size"] != expected_size:
        raise MediaStorageError(
            f"Staging object size {metadata['size']} does not match "
            f"expected {expected_size}.",
            ERR_STAGING_SIZE_MISMATCH,
        )
    if expected_etag is not None and metadata["etag"] != str(expected_etag).strip('"'):
        raise MediaStorageError(
            "Staging object ETag does not match the expected value.",
            ERR_STAGING_ETAG_MISMATCH,
        )
    return metadata


def download_staging_object(
    staging_key: Any,
    destination_path: Any,
    *,
    client: Any = None,
    bucket: Optional[str] = None,
    expected_size: Optional[int] = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> str:
    """
    Stream a staging object to a caller-provided local path.

    The body is streamed in bounded chunks (never loaded fully into memory),
    written to ``<destination_path>.part``, then atomically renamed to the
    final path so a failed download never leaves a partial file at the
    destination. Parent directories are created as needed.
    """
    key = _validate_staging_key(staging_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    if not isinstance(destination_path, str) or not destination_path:
        raise MediaStorageError(
            "Destination path must be a non-empty string.", ERR_DOWNLOAD
        )
    parent = os.path.dirname(os.path.abspath(destination_path))
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise MediaStorageError(
            f"Could not create download directory: {exc}", ERR_DOWNLOAD
        ) from exc
    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise MediaStorageError("Download chunk size is invalid.", ERR_DOWNLOAD)
    if expected_size is not None and (
        not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size <= 0
    ):
        raise MediaStorageError("Expected staging size is invalid.", ERR_DOWNLOAD)
    partial_path = f"{destination_path}.part"
    try:
        response = resolved_client.get_object(Bucket=resolved_bucket, Key=key)
    except Exception as exc:
        _raise_not_found_or_storage(exc, ERR_STAGING_MISSING, "Staging object")
    body = response["Body"]
    bytes_written = 0
    try:
        with open(partial_path, "wb") as out_file:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                bytes_written += len(chunk)
                if expected_size is not None and bytes_written > expected_size:
                    raise MediaStorageError(
                        "Downloaded staging object exceeds the verified size.",
                        ERR_DOWNLOAD,
                    )
        if expected_size is not None and bytes_written != expected_size:
            raise MediaStorageError(
                f"Downloaded staging object size {bytes_written} does not match expected {expected_size}.",
                ERR_DOWNLOAD,
            )
        os.replace(partial_path, destination_path)
    except Exception as exc:
        _remove_file(partial_path)
        raise MediaStorageError(
            f"Could not download staging object: {exc}", ERR_DOWNLOAD
        ) from exc
    finally:
        try:
            body.close()
        except Exception:
            pass
    return destination_path


def upload_output_file(
    output_key: Any,
    local_path: Any,
    *,
    content_type: Optional[str] = None,
    client: Any = None,
    bucket: Optional[str] = None,
) -> dict:
    """
    Upload a local output file to its exact final key, rejecting collisions.

    The key is caller-supplied as an already validated exact final key; only a
    defensive shape check is applied here. ``head_object`` is a fast early
    collision check, while the provider-side ``If-None-Match: *`` condition on
    ``PutObject`` is the authoritative no-overwrite guard for concurrent
    workers. The file is streamed from disk and a MIME ``ContentType`` is
    supplied.
    """
    key = _validate_exact_key(output_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    if not isinstance(local_path, str) or not local_path or not os.path.isfile(local_path):
        raise MediaStorageError(
            "Output source file does not exist.", ERR_STORAGE
        )
    _reject_collision(resolved_client, resolved_bucket, key)
    media_type = content_type or content_type_for_key(key)
    with open(local_path, "rb") as file_obj:
        _put_output_conditionally(
            resolved_client,
            resolved_bucket,
            key,
            file_obj,
            media_type,
            os.path.getsize(local_path),
        )
    try:
        return verify_output_object(
            key,
            expected_size=os.path.getsize(local_path),
            expected_content_type=media_type,
            client=resolved_client,
            bucket=resolved_bucket,
        )
    except Exception:
        try:
            delete_object_exact(key, client=resolved_client, bucket=resolved_bucket)
        except Exception:
            pass
        raise


def upload_output_fileobj(
    output_key: Any,
    file_obj: Any,
    *,
    content_type: Optional[str] = None,
    client: Any = None,
    bucket: Optional[str] = None,
) -> dict:
    """
    Upload an output file object to its exact final key, rejecting collisions.

    Same collision contract as :func:`upload_output_file`; the object is
    streamed from its current file-like source with a provider-side
    ``If-None-Match: *`` condition and a MIME ``ContentType``. Intended for
    tests and small generated objects.
    """
    key = _validate_exact_key(output_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    if file_obj is None or not hasattr(file_obj, "read"):
        raise MediaStorageError(
            "Output source must be a readable file object.", ERR_STORAGE
        )
    try:
        file_obj.seek(0)
    except Exception:
        pass
    _reject_collision(resolved_client, resolved_bucket, key)
    media_type = content_type or content_type_for_key(key)
    expected_size = None
    try:
        file_obj.seek(0, 2)
        expected_size = file_obj.tell()
        file_obj.seek(0)
    except Exception:
        try:
            file_obj.seek(0)
        except Exception:
            pass
    _put_output_conditionally(
        resolved_client,
        resolved_bucket,
        key,
        file_obj,
        media_type,
        expected_size,
    )
    try:
        return verify_output_object(
            key,
            expected_size=expected_size,
            expected_content_type=media_type,
            client=resolved_client,
            bucket=resolved_bucket,
        )
    except Exception:
        try:
            delete_object_exact(key, client=resolved_client, bucket=resolved_bucket)
        except Exception:
            pass
        raise


def _put_output_conditionally(
    client: Any,
    bucket: str,
    key: str,
    body: Any,
    content_type: str,
    content_length: Optional[int],
) -> None:
    """Stream one output with R2's atomic no-overwrite precondition."""
    params = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
        "IfNoneMatch": "*",
    }
    if content_length is not None:
        params["ContentLength"] = content_length
    try:
        client.put_object(**params)
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if _error_code(exc) in {"PreconditionFailed", "ConditionalRequestConflict", "412"} or status_code == 412:
            raise MediaStorageError(
                "Output object already exists.",
                ERR_OUTPUT_COLLISION,
            ) from exc
        raise MediaStorageError(f"Could not upload output object: {exc}", ERR_STORAGE) from exc


def verify_output_object(
    output_key: Any,
    *,
    expected_size: Optional[int] = None,
    expected_content_type: Optional[str] = None,
    client: Any = None,
    bucket: Optional[str] = None,
) -> dict:
    """
    Verify an uploaded output object with ``head_object`` and return metadata.

    Raises ``output_object_missing`` when absent, ``output_size_mismatch`` or
    ``output_content_type_mismatch`` when an expected value is supplied and
    does not match (MIME parameters after ``;`` are ignored in the comparison).
    """
    key = _validate_exact_key(output_key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    try:
        response = resolved_client.head_object(Bucket=resolved_bucket, Key=key)
    except Exception as exc:
        _raise_not_found_or_storage(exc, ERR_OUTPUT_MISSING, "Output object")
    metadata = _metadata_from_head(response)
    if expected_size is not None and metadata["size"] != expected_size:
        raise MediaStorageError(
            f"Output object size {metadata['size']} does not match "
            f"expected {expected_size}.",
            ERR_OUTPUT_SIZE_MISMATCH,
        )
    if expected_content_type:
        expected = expected_content_type.split(";")[0].strip().lower()
        actual = (metadata["content_type"] or "").split(";")[0].strip().lower()
        if actual != expected:
            raise MediaStorageError(
                f"Output object content type '{actual}' does not match "
                f"expected '{expected}'.",
                ERR_OUTPUT_CONTENT_TYPE_MISMATCH,
            )
    return metadata


def delete_object_exact(
    key: Any, *, client: Any = None, bucket: Optional[str] = None
) -> bool:
    """
    Delete exactly one object key. Idempotent; returns True when deleted.

    The key must be supplied by the caller as an exact job-owned key (staging
    or partial output). This helper never lists prefixes and never deletes by
    prefix, so it cannot touch objects outside the exact key.
    """
    validated = _validate_exact_key(key)
    resolved_client, resolved_bucket = _resolve_client_bucket(client, bucket)
    try:
        resolved_client.delete_object(Bucket=resolved_bucket, Key=validated)
    except Exception as exc:
        if _error_code(exc) in _MISSING_CODES:
            return False
        raise MediaStorageError(f"Could not delete object: {exc}", ERR_STORAGE) from exc
    return True


# ---------------------------------------------------------------------------
# Internal validation and helpers
# ---------------------------------------------------------------------------


def _validate_staging_key(key: Any) -> str:
    """Validate an exact ``Raw Files/<job_uuid>/<filename>`` staging key."""
    if not isinstance(key, str) or not key:
        raise MediaStorageError(
            "Staging key must be a non-empty string.", ERR_STAGING_KEY
        )
    if not key.startswith(_STAGING_PREFIX_SLASH):
        raise MediaStorageError(
            "Staging key must live under the reserved 'Raw Files/' prefix.",
            ERR_STAGING_KEY,
        )
    parts = key[len(_STAGING_PREFIX_SLASH):].split("/")
    if len(parts) != 2 or any(not part for part in parts):
        raise MediaStorageError(
            "Staging key must be exactly 'Raw Files/<job_uuid>/<filename>'.",
            ERR_STAGING_KEY,
        )
    if any(part in (".", "..") for part in parts):
        raise MediaStorageError(
            "Staging key must not contain traversal components.", ERR_STAGING_KEY
        )
    if any("\\" in part or "?" in part or "#" in part for part in parts):
        raise MediaStorageError(
            "Staging key must not contain backslashes, '?' or '#'.",
            ERR_STAGING_KEY,
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for part in parts for ch in part):
        raise MediaStorageError(
            "Staging key must not contain control characters.", ERR_STAGING_KEY
        )
    if len(key) > _MAX_KEY_LENGTH:
        raise MediaStorageError(
            f"Staging key exceeds {_MAX_KEY_LENGTH} characters.", ERR_STAGING_KEY
        )
    return key


def _validate_exact_key(key: Any) -> str:
    """Defensive shape check for a caller-supplied exact output key."""
    if not isinstance(key, str) or not key:
        raise MediaStorageError(
            "Output key must be a non-empty string.", ERR_OUTPUT_KEY
        )
    if key.startswith("/") or "\\" in key:
        raise MediaStorageError(
            "Output key must not be absolute or contain backslashes.", ERR_OUTPUT_KEY
        )
    parts = key.split("/")
    if any(not part for part in parts):
        raise MediaStorageError(
            "Output key must not contain empty path components.", ERR_OUTPUT_KEY
        )
    if any(part in (".", "..") for part in parts):
        raise MediaStorageError(
            "Output key must not contain traversal components.", ERR_OUTPUT_KEY
        )
    if "?" in key or "#" in key:
        raise MediaStorageError(
            "Output key must not contain query or fragment characters.",
            ERR_OUTPUT_KEY,
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for part in parts for ch in part):
        raise MediaStorageError(
            "Output key must not contain control characters.", ERR_OUTPUT_KEY
        )
    if len(key) > _MAX_KEY_LENGTH:
        raise MediaStorageError(
            f"Output key exceeds {_MAX_KEY_LENGTH} characters.", ERR_OUTPUT_KEY
        )
    return key


def _resolve_client_bucket(client: Any = None, bucket: Optional[str] = None):
    """Resolve the shared client and configured bucket, or the explicit pair."""
    resolved_client = client or get_r2_client()
    resolved_bucket = bucket or getattr(settings, "R2_BUCKET_NAME", "") or ""
    if not resolved_bucket:
        raise MediaStorageError(
            "R2 bucket is not configured.", ERR_CONFIG
        )
    return resolved_client, resolved_bucket


def _metadata_from_head(response: dict) -> dict:
    return {
        "size": response.get("ContentLength", 0),
        "etag": str(response.get("ETag", "") or "").strip('"'),
        "content_type": response.get("ContentType"),
        "last_modified": response.get("LastModified"),
    }


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        return str(error.get("Code", ""))
    return ""


def _raise_not_found_or_storage(exc: Exception, missing_code: str, label: str):
    if _error_code(exc) in _MISSING_CODES:
        raise MediaStorageError(f"{label} was not found in R2.", missing_code) from exc
    raise MediaStorageError(f"R2 storage error: {exc}", ERR_STORAGE) from exc


def _reject_collision(client: Any, bucket: str, key: str):
    """Raise ``output_collision`` when the exact output key already exists."""
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _error_code(exc) in _MISSING_CODES:
            return
        raise MediaStorageError(
            f"Could not check for an existing output object: {exc}", ERR_STORAGE
        ) from exc
    raise MediaStorageError(
        f"Output object already exists and will not be overwritten: {key}",
        ERR_OUTPUT_COLLISION,
    )


def _remove_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
