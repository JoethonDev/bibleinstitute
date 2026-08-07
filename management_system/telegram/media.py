"""Bounded server-side retrieval of lesson audio assets for Telegram delivery.

Phase 3 helper: for one ``Lesson.links`` audio entry, return a seekable,
size-validated binary object plus a safe filename and content type so the
caller can pass it to ``telebot.TeleBot.send_audio``.

Plain functions only: no models, views, storage mutation, HTTP fetches, or
Telegram calls.  The boto3-compatible R2 client and bucket name are passed in
explicitly.  HLS audio is concatenated from validated ``.ts`` segments without
transcoding; direct audio files are streamed with a bounded read.  Anything
that cannot be delivered safely returns ``None``; the caller falls back to the
trusted website link.
"""

from __future__ import annotations

import logging
import posixpath
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# Telegram Bot API upload ceiling for this feature.
TELEGRAM_AUDIO_MAX_BYTES = 50 * 1024 * 1024

# Allowed direct (non-HLS) audio containers and their content types.
DIRECT_AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}

# Content type reported for concatenated HLS transport streams.
_HLS_CONTENT_TYPE = "audio/mp2t"

# Upper bound for one manifest read; larger playlists are treated as malformed.
_HLS_MANIFEST_MAX_BYTES = 512 * 1024

# Bounded read chunk for R2 response bodies.
_READ_CHUNK = 64 * 1024

# In-memory ceiling before the spool rolls over to a temporary file.
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024

# Canonical HLS segment folders (mirrors utils.file_validator.validate_hls_object_key).
_CANONICAL_SEGMENT_FOLDERS = ("Video Segments", "Audio Segments")

_MAX_KEY_LENGTH = 1024

# HLS tags that make a playlist impossible to assemble safely.
_UNSUPPORTED_PLAYLIST_TAGS = ("#EXT-X-KEY:", "#EXT-X-BYTERANGE:")


def audio_links(lesson_links: object) -> list[dict]:
    """Return only the audio entries of a ``Lesson.links`` JSON list.

    Accepts only a list of dicts (anything else yields ``[]``).  Entries with
    ``file_type == 'audio'`` and a nonempty string ``id`` are shallow-copied so
    the original ``index``/``name``/``part_id`` (and any other link fields) are
    preserved.  Video and book entries are never returned.
    """
    if not isinstance(lesson_links, list):
        return []
    result: list[dict] = []
    for item in lesson_links:
        if not isinstance(item, dict):
            continue
        if item.get("file_type") != "audio":
            continue
        link_id = item.get("id")
        if not isinstance(link_id, str) or not link_id:
            continue
        result.append(dict(item))
    return result


def download_audio_asset(
    link: dict,
    cloud_client: Any,
    bucket_name: str,
    max_bytes: int = TELEGRAM_AUDIO_MAX_BYTES,
) -> tuple[Any, str, str] | None:
    """Retrieve one audio link from R2 as a bounded, seekable binary object.

    Returns ``(file_like, filename, content_type)`` or ``None``.  The caller
    owns the returned file-like object and must call ``.close()`` on it.

    Direct audio files (.mp3/.m4a/.ogg/.wav) are HEAD-validated then streamed
    with a bounded max+1 read.  HLS audio (.m3u8) is parsed with a bounded
    manifest read, resolved against the manifest directory with POSIX path
    rules, HEAD-validated per segment, then concatenated into a temporary
    spool while the cumulative byte limit is enforced.

    Rejects path traversal, absolute URLs, scheme/netloc references, query or
    fragment characters, non-audio extensions, malformed or unsupported
    playlists, missing R2 objects, and anything over ``max_bytes``.  Response
    bodies and partial spools are always closed on failure.  Errors are never
    exposed to callers and never include object contents or credentials.
    """
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return None
    if not isinstance(bucket_name, str) or not bucket_name:
        return None
    if not callable(getattr(cloud_client, "head_object", None)) or not callable(
        getattr(cloud_client, "get_object", None)
    ):
        return None
    if not isinstance(link, dict):
        return None
    key = _safe_key(link.get("id"))
    if key is None:
        return None
    try:
        if key.lower().endswith(".m3u8"):
            return _download_hls(cloud_client, bucket_name, key, max_bytes)
        ext = _extension(key)
        content_type = DIRECT_AUDIO_EXTENSIONS.get(ext)
        if content_type is None:
            return None
        return _download_direct(cloud_client, bucket_name, key, content_type, max_bytes)
    except Exception:  # never leak an internal failure to the caller
        logger.warning("Telegram audio asset could not be retrieved.")
        return None


def _safe_key(key: Any) -> str | None:
    """Validate an R2 object key: relative, slash-separated, no traversal.

    Rejects absolute paths, backslashes, scheme/netloc markers, query or
    fragment characters, control characters, empty or ``.``/``..`` path
    components, and keys longer than ``_MAX_KEY_LENGTH``.
    """
    if not isinstance(key, str) or not key or len(key) > _MAX_KEY_LENGTH:
        return None
    if key.startswith("/") or "\\" in key or "://" in key:
        return None
    if "?" in key or "#" in key:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        return None
    parts = key.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        return None
    return key


def _extension(key: str) -> str:
    """Return the lowercase dotted extension of the final path component."""
    basename = posixpath.basename(key)
    _, dot, ext = basename.rpartition(".")
    return "." + ext.lower() if dot else ""


def _resolve_hls_segment(manifest_key: str, reference: str) -> str | None:
    """Resolve one relative ``.ts`` reference against the manifest directory.

    The reference must be a relative POSIX path ending in ``.ts`` with no
    traversal, absolute prefix, scheme/netloc, query, or fragment.  The
    resolved key must stay inside the manifest's directory subtree and its
    immediate parent must be a canonical ``Video Segments``/``Audio Segments``
    folder.
    """
    if not reference or len(reference) > _MAX_KEY_LENGTH:
        return None
    if "://" in reference or reference.startswith("/"):
        return None
    if "?" in reference or "#" in reference:
        return None
    parts = reference.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        return None
    if not reference.lower().endswith(".ts"):
        return None
    folder = posixpath.dirname(manifest_key)
    resolved = posixpath.normpath(posixpath.join(folder, reference))
    if resolved in {".", ".."} or resolved.startswith("../"):
        return None
    if folder and not resolved.startswith(folder + "/"):
        return None
    resolved_parts = resolved.split("/")
    if len(resolved_parts) < 2 or resolved_parts[-2] not in _CANONICAL_SEGMENT_FOLDERS:
        return None
    return _safe_key(resolved)


def _parse_segment_references(playlist: str) -> list[str] | None:
    """Return the non-comment segment references of an HLS playlist.

    Returns ``None`` for unsupported layouts (encrypted or byte-range
    playlists) and an empty list for an empty playlist.
    """
    references: list[str] = []
    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith(_UNSUPPORTED_PLAYLIST_TAGS):
                return None
            continue
        references.append(line)
    return references


def _head_object(cloud_client: Any, bucket_name: str, key: str) -> dict | None:
    try:
        return cloud_client.head_object(Bucket=bucket_name, Key=key)
    except Exception:
        logger.debug("R2 head_object failed for key %r.", key)
        return None


def _get_object(cloud_client: Any, bucket_name: str, key: str) -> dict | None:
    try:
        return cloud_client.get_object(Bucket=bucket_name, Key=key)
    except Exception:
        logger.debug("R2 get_object failed for key %r.", key)
        return None


def _close_body(body: Any) -> None:
    try:
        close = getattr(body, "close", None)
        if close is not None:
            close()
    except Exception:
        pass


def _new_spool(max_bytes: int):
    memory_limit = min(max_bytes, _SPOOL_MEMORY_LIMIT)
    return tempfile.SpooledTemporaryFile(max_size=memory_limit, mode="w+b")


def _copy_bounded(body: Any, target: Any, limit: int) -> bool:
    """Stream ``body`` into ``target`` without ever exceeding ``limit`` bytes.

    Returns ``False`` (without writing the overflowing chunk) as soon as the
    cumulative size would exceed the limit.
    """
    total = 0
    while True:
        chunk = body.read(_READ_CHUNK)
        if not chunk:
            return True
        total += len(chunk)
        if total > limit:
            return False
        target.write(chunk)


def _download_direct(
    cloud_client: Any,
    bucket_name: str,
    key: str,
    content_type: str,
    max_bytes: int,
) -> tuple[Any, str, str] | None:
    head = _head_object(cloud_client, bucket_name, key)
    if head is None:
        return None
    try:
        size = int(head.get("ContentLength", 0))
    except (TypeError, ValueError):
        return None
    if size <= 0 or size > max_bytes:
        return None
    response = _get_object(cloud_client, bucket_name, key)
    if response is None:
        return None
    body = response.get("Body")
    if body is None:
        return None
    spool = _new_spool(max_bytes)
    try:
        if not _copy_bounded(body, spool, max_bytes):
            spool.close()
            return None
        spool.seek(0)
        return spool, posixpath.basename(key), content_type
    except Exception:
        spool.close()
        return None
    finally:
        _close_body(body)


def _download_hls(
    cloud_client: Any,
    bucket_name: str,
    manifest_key: str,
    max_bytes: int,
) -> tuple[Any, str, str] | None:
    response = _get_object(cloud_client, bucket_name, manifest_key)
    if response is None:
        return None
    body = response.get("Body")
    if body is None:
        return None
    try:
        raw = body.read(_HLS_MANIFEST_MAX_BYTES + 1)
        if len(raw) > _HLS_MANIFEST_MAX_BYTES:
            return None
        try:
            playlist = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    finally:
        _close_body(body)

    references = _parse_segment_references(playlist)
    if not references:
        return None

    resolved_keys: list[str] = []
    for reference in references:
        resolved = _resolve_hls_segment(manifest_key, reference)
        if resolved is None:
            return None
        resolved_keys.append(resolved)

    # Validate every segment before reading anything: existence, size, and the
    # cumulative ceiling so oversized playlists stop without streaming.
    cumulative = 0
    for segment_key in resolved_keys:
        head = _head_object(cloud_client, bucket_name, segment_key)
        if head is None:
            return None
        try:
            size = int(head.get("ContentLength", 0))
        except (TypeError, ValueError):
            return None
        if size <= 0 or size > max_bytes:
            return None
        cumulative += size
        if cumulative > max_bytes:
            return None

    spool = _new_spool(max_bytes)
    try:
        for segment_key in resolved_keys:
            response = _get_object(cloud_client, bucket_name, segment_key)
            if response is None:
                spool.close()
                return None
            segment_body = response.get("Body")
            if segment_body is None:
                spool.close()
                return None
            try:
                if not _copy_bounded(segment_body, spool, max_bytes - spool.tell()):
                    spool.close()
                    return None
            finally:
                _close_body(segment_body)
        spool.seek(0)
        return spool, _hls_filename(manifest_key), _HLS_CONTENT_TYPE
    except Exception:
        spool.close()
        return None


def _hls_filename(manifest_key: str) -> str:
    """Derive a safe ``.ts`` filename from the validated manifest basename."""
    name = posixpath.basename(manifest_key)
    if name.lower().endswith(".m3u8"):
        name = name[:-5]
    if not name.lower().endswith(".ts"):
        name += ".ts"
    return name
