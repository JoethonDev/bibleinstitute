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

import os
import posixpath
import re
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext as _, ngettext

PHOTO_MAX_BYTES = 10 * 1024 * 1024
FILE_MAX_BYTES = 50 * 1024 * 1024
AUDIO_MAX_BYTES = 20 * 1024 * 1024
AUDIO_MAX_SECONDS = 300
CAPTION_MAX_LENGTH = 1_024
SUPPORT_ATTACHMENT_ACCEPT = ".jpg,.jpeg,.png,.mp4,.pdf,.ogg,.opus,.mp3,.m4a,.webm"

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

    kind: str  # "photo" | "video" | "document" | "voice" | "audio"
    mime_type: str
    filename: str
    size: int
    duration_seconds: float | None = None


class AttachmentValidationError(ValueError):
    """A localized, user-facing attachment rejection."""


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

    if extension in {".ogg", ".opus"}:
        if size > AUDIO_MAX_BYTES:
            raise ValueError(_("Audio files must be 20 MB or smaller."))
        if not _is_ogg(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("voice", "audio/ogg", safe, size)

    if extension == ".mp3":
        if size > AUDIO_MAX_BYTES:
            raise ValueError(_("Audio files must be 20 MB or smaller."))
        if not _is_mp3(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("audio", "audio/mpeg", safe, size)

    if extension == ".m4a":
        if size > AUDIO_MAX_BYTES:
            raise ValueError(_("Audio files must be 20 MB or smaller."))
        if not _is_m4a(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("audio", "audio/mp4", safe, size)

    if extension == ".webm":
        if size > AUDIO_MAX_BYTES:
            raise ValueError(_("Audio files must be 20 MB or smaller."))
        if not _is_webm(head):
            raise ValueError(_("The attachment content does not match its file type."))
        return SupportAttachmentSpec("document", "audio/webm", safe, size)

    raise ValueError(_("The attachment must be a JPEG, PNG, MP4, PDF, OGG/Opus, MP3, M4A, or WebM file."))


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


def _is_ogg(head: bytes) -> bool:
    return bytes(head[:4]) == b"OggS"


def _is_mp3(head: bytes) -> bool:
    return bytes(head[:3]) == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)


def _is_m4a(head: bytes) -> bool:
    return len(head) >= 12 and bytes(head[4:8]) == b"ftyp"


def _is_webm(head: bytes) -> bool:
    return bytes(head[:4]) == b"\x1aE\xdf\xa3"


def _ffprobe_duration_seconds(path: str) -> float:
    """Return one media file's duration in seconds, even without a header.

    Browser voice recordings are streamed WebM/Opus files that carry no
    container duration, so an empty header falls back to the end of the last
    audio packet instead of rejecting a valid recording.
    """
    binary = getattr(settings, "FFPROBE_BINARY", "ffprobe")
    result = subprocess.run(
        [
            binary,
            "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", path,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    header_value = (result.stdout or "").strip()
    if header_value and header_value != "N/A":
        try:
            header_seconds = float(header_value)
        except ValueError:
            header_seconds = 0.0
        if header_seconds > 0:
            return header_seconds

    packets = subprocess.run(
        [
            binary,
            "-v", "error", "-select_streams", "a:0",
            "-show_entries", "packet=pts_time,duration_time",
            "-of", "csv=p=0", path,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    end_seconds = 0.0
    for line in (packets.stdout or "").splitlines():
        parts = line.strip().split(",")
        if not parts or not parts[0]:
            continue
        try:
            packet_start = float(parts[0])
        except ValueError:
            continue
        packet_length = 0.0
        if len(parts) > 1 and parts[1]:
            try:
                packet_length = float(parts[1])
            except ValueError:
                packet_length = 0.0
        end_seconds = max(end_seconds, packet_start + packet_length)
    return end_seconds


def probe_audio_seconds(
    uploaded_file,
    *,
    max_seconds: int = AUDIO_MAX_SECONDS,
) -> float:
    """Return the duration of one audio upload using ffprobe.

    The server never trusts a browser-supplied duration, and the cap is a
    parameter so one canonical implementation serves the Telegram support
    reply (5 minutes) and the shorter in-app chat recording limit.
    """
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as handle:
            temporary_path = handle.name
            uploaded_file.seek(0)
            for chunk in uploaded_file.chunks():
                handle.write(chunk)
        duration = _ffprobe_duration_seconds(temporary_path)
    except FileNotFoundError as exc:
        raise AttachmentValidationError(
            _("Audio validation is unavailable on this server.")
        ) from exc
    except Exception as exc:
        raise AttachmentValidationError(
            _("The audio file could not be inspected.")
        ) from exc
    finally:
        uploaded_file.seek(0)
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    if duration <= 0 or duration > max_seconds:
        if max_seconds >= 60 and max_seconds % 60 == 0:
            minutes = max_seconds // 60
            raise AttachmentValidationError(
                _("Audio messages must be %(limit)s or shorter.")
                % {
                    "limit": ngettext(
                        "%(count)s minute",
                        "%(count)s minutes",
                        minutes,
                    ) % {"count": minutes}
                }
            )
        raise AttachmentValidationError(
            _("Audio messages must be %(limit)s or shorter.")
            % {
                "limit": ngettext(
                    "%(count)s second",
                    "%(count)s seconds",
                    max_seconds,
                ) % {"count": max_seconds}
            }
        )
    return duration


def classify_uploaded_file(
    uploaded_file,
    *,
    audio_max_seconds: int = AUDIO_MAX_SECONDS,
) -> SupportAttachmentSpec:
    """Validate one browser upload through the canonical attachment rules."""
    try:
        uploaded_file.seek(0)
        head = uploaded_file.read(16)
        uploaded_file.seek(0)
        spec = classify_attachment(
            filename=str(getattr(uploaded_file, "name", "") or ""),
            size=int(getattr(uploaded_file, "size", 0) or 0),
            head=head,
        )
        if spec.kind in {"voice", "audio"} or spec.mime_type == "audio/webm":
            duration = probe_audio_seconds(
                uploaded_file,
                max_seconds=audio_max_seconds,
            )
            # A browser recording arrives as WebM and the container cannot say
            # whether it holds audio or video; a probed duration proves audio,
            # and a video-only file yields no duration and is rejected above.
            kind = spec.kind if spec.kind in {"voice", "audio"} else "voice"
            return SupportAttachmentSpec(
                kind, spec.mime_type, spec.filename, spec.size, duration,
            )
        return spec
    except AttachmentValidationError:
        raise
    except ValueError as exc:
        raise AttachmentValidationError(str(exc)) from exc
    except Exception as exc:
        raise AttachmentValidationError(
            _("The attachment could not be read.")
        ) from exc
