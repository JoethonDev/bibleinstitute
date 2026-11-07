"""Telegram Bot API limits for support-chat attachments.

One canonical implementation for the reply form, the admin view and the
support service.  The browser-supplied MIME type is deliberately ignored:
the filename extension selects the accepted kind, then the first bytes must
match that kind, and the stored/served MIME type is the canonical one.

Bot API limits implemented here:

* photos (JPEG/PNG): 10 MB;
* videos (MP4) and documents (PDF): 50 MB;
* one attachment travels with one caption of at most 1,024 characters.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from django.utils.translation import gettext as _

PHOTO_MAX_BYTES = 10 * 1024 * 1024
FILE_MAX_BYTES = 50 * 1024 * 1024
CAPTION_MAX_LENGTH = 1_024
SUPPORT_ATTACHMENT_ACCEPT = ".jpg,.jpeg,.png,.mp4,.pdf"

# Inbound Telegram documents can carry any MIME type chosen by the sender.
# Only these are safe to serve inline from the LMS origin; everything else is
# forced to download as application/octet-stream (stored-XSS prevention).
INLINE_ATTACHMENT_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "audio/aac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/quicktime",
        "video/webm",
    }
)

_PHOTO_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass(frozen=True)
class SupportAttachmentSpec:
    """A validated outbound attachment ready for one Telegram send call."""

    kind: str  # "photo" | "video" | "document"
    mime_type: str
    filename: str
    size: int


def safe_filename(name: str, fallback: str) -> str:
    """Return one path-free, bounded filename that keeps its extension.

    Unicode letters are preserved so an Arabic document name survives the
    round trip; path separators, control characters and shell-like punctuation
    collapse to underscores.
    """
    value = posixpath.basename(str(name or "")).replace("\\", "_")
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
    value = value or fallback
    if len(value) <= 180:
        return value
    stem, dot, extension = value.rpartition(".")
    if dot and 0 < len(extension) <= 16:
        return f"{stem[:180 - len(extension) - 1]}.{extension}"
    return value[:180]


def inline_attachment_mime_type(value: object) -> str | None:
    """Return the canonical MIME type when it is safe to render inline."""
    mime_type = str(value or "").split(";", 1)[0].strip().lower()
    return mime_type if mime_type in INLINE_ATTACHMENT_MIME_TYPES else None


def classify_attachment(*, filename: str, size: int, head: bytes) -> SupportAttachmentSpec:
    """Validate one dashboard upload against the Telegram Bot API limits.

    Raises ``ValueError`` with a localized, operator-safe message.  Only a
    matching extension and matching magic bytes are accepted, so a renamed
    HTML/SVG/executable file can never be forwarded as a photo or PDF.
    """
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        raise ValueError(_("Empty files are not accepted."))
    if not isinstance(head, (bytes, bytearray)):
        head = b""
    safe = safe_filename(filename, "")
    extension = _extension(safe)

    if extension in _PHOTO_MIME_TYPES:
        if size > PHOTO_MAX_BYTES:
            raise ValueError(_("Images must be 10 MB or smaller."))
        mime_type = _PHOTO_MIME_TYPES[extension]
        if mime_type == "image/jpeg" and not _is_jpeg(head):
            raise ValueError(_("The attachment content does not match its file type."))
        if mime_type == "image/png" and not _is_png(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("photo", mime_type, safe, size)

    if extension == ".mp4":
        if size > FILE_MAX_BYTES:
            raise ValueError(_("Videos and PDF files must be 50 MB or smaller."))
        if not _is_mp4(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("video", "video/mp4", safe, size)

    if extension == ".pdf":
        if size > FILE_MAX_BYTES:
            raise ValueError(_("Videos and PDF files must be 50 MB or smaller."))
        if not _is_pdf(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("document", "application/pdf", safe, size)

    raise ValueError(_("The attachment must be a JPEG, PNG, MP4, or PDF file."))


def _extension(filename: str) -> str:
    basename = posixpath.basename(filename)
    _, dot, extension = basename.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def _is_jpeg(head: bytes) -> bool:
    return bytes(head[:3]) == b"\xff\xd8\xff"


def _is_png(head: bytes) -> bool:
    return bytes(head[:8]) == b"\x89PNG\r\n\x1a\n"


def _is_pdf(head: bytes) -> bool:
    return bytes(head[:5]) == b"%PDF-"


def _is_mp4(head: bytes) -> bool:
    return len(head) >= 12 and bytes(head[4:8]) == b"ftyp"
