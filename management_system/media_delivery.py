"""Authorized lesson-media resolution shared by web and mobile adapters.

The adapters keep their own transport authentication (browser session or mobile
bearer/audience token) and HTTP formatting. This module owns the canonical
lesson/file authorization, manifest rewrite, download URL and book-stream
resolution below HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import posixpath
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.utils.translation import gettext as _

from .academic_access import get_accessible_offering_or_403
from .models import CourseOffering, LectureProgressEvent, Lesson, PublicationStatus, ViewingSession
from .storage_ownership import is_private_storage_key
from .utils.file_validator import downloadable_audio_key, validate_downloadable_audio_key
from .utils.helpers import user_has_management_role
from .utils.hls_parser import get_segment_number
from .utils.storage_operations import generate_unique_url

logger = logging.getLogger(__name__)

MEDIA_TOKEN_VERSION = "v2"
MEDIA_AUDIENCES = frozenset({"web", "mobile"})
MEDIA_SEGMENT_TOKEN_LIFETIME = timedelta(
    minutes=max(1, int(getattr(settings, "MEDIA_SEGMENT_TOKEN_MINUTES", 180) or 180))
)


class MediaAccessError(Exception):
    """Expected media rejection; adapters choose the HTTP status and body."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class MediaLink:
    offering: CourseOffering
    lesson: Lesson
    file_index: int
    link: dict
    file_type: str
    media_key: str
    part_id: str


@dataclass(frozen=True)
class AudioDownload:
    url: str


@dataclass(frozen=True)
class BookDocumentStream:
    body: Any
    status_code: int
    content_type: str
    content_length: int | None
    content_range: str
    accept_ranges: str = "bytes"


def create_viewing_session(student, lesson, part_id, *, mobile_session=None, media_key=""):
    session_id = secrets.token_urlsafe(32)
    session_hours = max(1, int(getattr(settings, "MEDIA_VIEWING_SESSION_HOURS", 3) or 3))
    expires_at = timezone.now() + timedelta(hours=session_hours)
    access_channel = (
        ViewingSession.AccessChannel.MOBILE
        if mobile_session is not None
        else ViewingSession.AccessChannel.WEB
    )
    if mobile_session is not None and mobile_session.user_id != student.pk:
        raise PermissionDenied(_("The viewing session is invalid."))
    session = ViewingSession.objects.create(
        student=student,
        lesson=lesson,
        mobile_session=mobile_session,
        access_channel=access_channel,
        part_id=part_id,
        media_key=media_key or "",
        session_id=session_id,
        expires_at=expires_at,
    )
    LectureProgressEvent.objects.get_or_create(
        viewing_session=session,
        event_type=LectureProgressEvent.EventType.STARTED,
        defaults={
            "student": student,
            "lesson": lesson,
            "part_id": part_id,
            "occurred_at": timezone.now(),
        },
    )
    return session


def _media_binding(audience, mobile_session_id=None):
    """Return the opaque bearer-session binding carried by mobile tokens."""
    if audience != "mobile":
        return ""
    if mobile_session_id is None:
        raise ValueError("Mobile media tokens require a mobile session binding.")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"mobile-media:{mobile_session_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _validate_media_audience(audience):
    if audience not in MEDIA_AUDIENCES:
        raise ValueError("Unsupported media audience.")
    return audience


def sign_media_message(message):
    return hmac.new(
        settings.MEDIA_WORKER_HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def sign_session(session_id, expires_at, *, audience="web", mobile_session_id=None):
    audience = _validate_media_audience(audience)
    expires_at_value = int(expires_at.timestamp())
    binding = _media_binding(audience, mobile_session_id)
    message = f"{MEDIA_TOKEN_VERSION}|{audience}|{session_id}|{expires_at_value}|{binding}"
    signature = sign_media_message(message)
    return f"{MEDIA_TOKEN_VERSION}:{audience}:{session_id}:{expires_at_value}:{binding}:{signature}"


def sign_media_token(session_id, expires_at, segment_key, *, audience="web", token_expires_at=None):
    audience = _validate_media_audience(audience)
    token_expiry = token_expires_at or expires_at
    expires_at_value = int(token_expiry.timestamp())
    message = f"{MEDIA_TOKEN_VERSION}|{audience}|{session_id}|{expires_at_value}|{segment_key}"
    return f"{MEDIA_TOKEN_VERSION}:{audience}:{session_id}:{expires_at_value}:{sign_media_message(message)}"


def valid_session_token(token, session, *, audience="web", mobile_session_id=None):
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 6 or parts[0] != MEDIA_TOKEN_VERSION or parts[1] != audience:
        return False
    if parts[2] != session.session_id:
        return False
    try:
        expires_at = int(parts[3])
    except (TypeError, ValueError):
        return False
    if expires_at != int(session.expires_at.timestamp()) or timezone.now().timestamp() >= expires_at:
        return False
    binding = parts[4]
    if audience == "mobile":
        if not binding:
            return False
        if mobile_session_id is not None:
            try:
                expected_binding = _media_binding(audience, mobile_session_id)
            except ValueError:
                return False
            if not hmac.compare_digest(binding, expected_binding):
                return False
    elif binding:
        return False
    expected = sign_media_message(
        f"{MEDIA_TOKEN_VERSION}|{audience}|{session.session_id}|{expires_at}|{binding}"
    )
    return hmac.compare_digest(parts[5], expected)


def resolve_media_link(
    *,
    user,
    offering_id: int,
    lesson_id: int,
    file_index: int,
    allowed_types: Iterable[str] | None = None,
    allow_unpublished: bool = False,
) -> MediaLink:
    """Resolve one exact authorized lesson file below HTTP."""

    try:
        offering = get_accessible_offering_or_403(
            user,
            offering_id,
            allow_management=allow_unpublished,
        )
    except PermissionDenied:
        raise MediaAccessError("forbidden", _("You do not have access to this lesson."))
    lesson = (
        Lesson.objects.select_related(
            "course_offering__course",
            "course_offering__academic_year_level__academic_year",
            "course_offering__academic_year_level__level",
        )
        .filter(pk=lesson_id, course_offering=offering)
        .first()
    )
    if lesson is None:
        raise MediaAccessError("not_found", _("Media file was not found."))
    if lesson.status != PublicationStatus.PUBLISHED and not (
        allow_unpublished and user_has_management_role(user)
    ):
        raise MediaAccessError("forbidden", _("You do not have access to this lesson."))
    try:
        links = json.loads(lesson.links or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    if (
        not isinstance(links, list)
        or not isinstance(file_index, int)
        or file_index < 0
        or file_index >= len(links)
        or not isinstance(links[file_index], dict)
    ):
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    link = links[file_index]
    raw_type = link.get("file_type")
    raw_key = link.get("id")
    file_type = raw_type if isinstance(raw_type, str) else ""
    media_key = raw_key if isinstance(raw_key, str) else ""
    if (
        not media_key
        or is_private_storage_key(media_key)
        or (allowed_types is not None and file_type not in set(allowed_types))
    ):
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    part_id = link.get("part_id")
    return MediaLink(
        offering=offering,
        lesson=lesson,
        file_index=file_index,
        link=link,
        file_type=file_type,
        media_key=media_key,
        part_id=str(part_id) if isinstance(part_id, str) else "",
    )


def resolve_book_url(*, media: MediaLink, cloud_client, bucket_name: str) -> str | None:
    if media.file_type != "book":
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    return generate_unique_url(cloud_client, bucket_name, media.media_key, expires_in=3600)


def resolve_audio_download(
    *,
    media: MediaLink,
    cloud_client,
    bucket_name: str,
    expires_in: int = 3600,
) -> AudioDownload:
    if media.file_type != "audio":
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    manifest_key = media.media_key
    download_key = media.link.get("download_id") or downloadable_audio_key(manifest_key)
    valid, errors = validate_downloadable_audio_key(download_key or "")
    if (
        not valid
        or is_private_storage_key(download_key)
        or download_key != downloadable_audio_key(manifest_key)
    ):
        logger.warning(
            "Invalid downloadable audio metadata for lesson %s link %s: %s",
            media.lesson.pk,
            media.file_index,
            errors,
        )
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", posixpath.basename(download_key)) or "audio.mp3"
    try:
        download_url = cloud_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket_name,
                "Key": download_key,
                "ResponseContentType": "audio/mpeg",
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=max(60, min(int(expires_in), 3600)),
        )
    except Exception:  # noqa: BLE001 - provider boundary
        logger.exception("Error generating downloadable audio URL for lesson %s", media.lesson.pk)
        raise MediaAccessError("unavailable", _("Download is temporarily unavailable."))
    return AudioDownload(url=download_url)


def build_manifest_playlist(
    *,
    user,
    media: MediaLink,
    session: ViewingSession,
    audience: str,
    cloud_client,
    bucket_name: str,
) -> str:
    """Return the signed playlist for one already-authenticated session."""

    audience = _validate_media_audience(audience)
    if session.student_id != user.pk:
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    if timezone.now() >= session.expires_at:
        raise MediaAccessError("expired", _("Viewing session expired."))
    expected_channel = (
        ViewingSession.AccessChannel.MOBILE
        if audience == "mobile"
        else ViewingSession.AccessChannel.WEB
    )
    if session.access_channel != expected_channel:
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    if (
        session.lesson_id != media.lesson.pk
        or session.part_id != media.part_id
        or (session.media_key and session.media_key != media.media_key)
    ):
        raise MediaAccessError("forbidden", _("The viewing session is invalid."))
    try:
        response = cloud_client.get_object(Bucket=bucket_name, Key=media.media_key)
        body = response["Body"]
        try:
            playlist = body.read().decode()
        finally:
            body.close()
    except Exception:  # noqa: BLE001 - provider boundary
        raise MediaAccessError("unavailable", _("The media is unavailable."))

    playlist_lines = []
    manifest_folder = posixpath.dirname(media.media_key).strip("/")
    for line in playlist.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.lower().endswith(".ts"):
            if stripped.startswith(("/", "http://", "https://")):
                raise MediaAccessError("unavailable", _("The media is unavailable."))
            segment_key = posixpath.normpath(
                posixpath.join(posixpath.dirname(media.media_key), stripped)
            )
            if segment_key in {".", ".."} or segment_key.startswith("../"):
                raise MediaAccessError("unavailable", _("The media is unavailable."))
            if manifest_folder and not segment_key.startswith(f"{manifest_folder}/"):
                raise MediaAccessError("unavailable", _("The media is unavailable."))
            if get_segment_number(segment_key) is None:
                raise MediaAccessError("unavailable", _("The media is unavailable."))
            segment_expires_at = min(
                session.expires_at,
                timezone.now() + MEDIA_SEGMENT_TOKEN_LIFETIME,
            )
            signed_token = sign_media_token(
                session.session_id,
                session.expires_at,
                segment_key,
                audience=audience,
                token_expires_at=segment_expires_at,
            )
            worker_base = settings.CLOUD_WORKER.rstrip("/")
            playlist_lines.append(
                f"{worker_base}/media/{quote(session.session_id, safe='')}/"
                f"{quote(segment_key, safe='')}?token={quote(signed_token, safe='')}"
            )
        else:
            playlist_lines.append(line)
    return "\n".join(playlist_lines) + ("\n" if playlist.endswith("\n") else "")


def open_book_document(
    *,
    media: MediaLink,
    range_header: str,
    cloud_client,
    bucket_name: str,
) -> BookDocumentStream:
    """Open one authorized PDF object with an optional bounded Range header."""

    if media.file_type != "book":
        raise MediaAccessError("invalid_media", _("Invalid media file."))
    range_header = str(range_header or "").strip()
    if range_header and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", range_header):
        raise MediaAccessError("invalid_range", _("Invalid media file."))
    params = {"Bucket": bucket_name, "Key": media.media_key}
    if range_header:
        params["Range"] = range_header
    try:
        storage_response = cloud_client.get_object(**params)
    except Exception as exc:  # noqa: BLE001 - provider boundary
        status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 416:
            raise MediaAccessError("range_not_satisfiable", _("Requested range is not satisfiable."))
        raise MediaAccessError("unavailable", _("The media is unavailable."))
    return BookDocumentStream(
        body=storage_response["Body"],
        status_code=206 if storage_response.get("ContentRange") else 200,
        content_type="application/pdf",
        content_length=storage_response.get("ContentLength"),
        content_range=storage_response.get("ContentRange") or "",
    )
