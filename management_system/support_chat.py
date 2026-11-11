"""Canonical in-app support chat over the durable support message rows.

One student has exactly one support conversation. The in-app chat and the
Telegram bot write the same ``TelegramMessage`` rows, so a message sent from
either channel appears in the other with no second store and no sync job.

Text, JPEG/PNG images and audio recordings of at most one minute are accepted
from the website. Validation reuses the canonical attachment rules in
``telegram/attachments.py``; nothing is trusted from the browser.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _

from .models import TelegramAttachment, TelegramConversation, TelegramMessage
from .telegram.attachments import (
    AttachmentValidationError,
    SupportAttachmentSpec,
    classify_uploaded_file,
    inline_attachment_mime_type,
)
from .utils.storage_operations import get_r2_client, upload_to_bucket

logger = logging.getLogger(__name__)

CHAT_PAGE_SIZE = 20
CHAT_POLL_LIMIT = 30
CHAT_TEXT_MAX_LENGTH = 4_000
# The in-app recorder is capped at one minute by the product contract; the
# server enforces the same limit because a browser value is never trusted.
CHAT_AUDIO_MAX_SECONDS = 60
CHAT_ATTACHMENT_KINDS = frozenset({"photo", "voice", "audio"})
CHAT_ATTACHMENT_PREFIX = "Telegram Support/Web"


class SupportChatError(Exception):
    """A localized, student-safe failure from the in-app support chat."""


def conversation_for_student(user) -> TelegramConversation | None:
    """Return the one conversation the in-app chat shows (read-only).

    The newest touched conversation is the one the student is talking in; the
    ordering avoids ``last_message_at`` so a conversation without messages yet
    is still selected instead of a NULL-sorted older one.
    """
    return (
        TelegramConversation.objects.filter(user=user)
        .select_related("claimed_by", "handled_by")
        .order_by("-updated_at", "-pk")
        .first()
    )


def visible_messages(conversation) -> QuerySet:
    """Return the messages the student may see in his own conversation."""
    if conversation is None:
        return TelegramMessage.objects.none()
    return (
        TelegramMessage.objects.select_related("conversation")
        .prefetch_related("attachments")
        .filter(conversation_id=conversation.pk)
        .exclude(content_type=TelegramMessage.ContentType.DIGEST)
        .filter(student_deleted_at__isnull=True)
        .order_by("-created_at", "-pk")
    )


def latest_message_id(conversation) -> int:
    if conversation is None:
        return 0
    return (
        visible_messages(conversation)
        .values_list("pk", flat=True)
        .first()
        or 0
    )


def newest_messages(conversation, *, limit: int = CHAT_PAGE_SIZE) -> list[TelegramMessage]:
    """Return the newest page of the conversation in reading order."""
    if conversation is None:
        return []
    rows = list(visible_messages(conversation)[: max(1, limit)])
    rows.reverse()
    return rows


def older_messages(
    conversation,
    *,
    before_created_at,
    before_id: int,
    limit: int = CHAT_PAGE_SIZE,
) -> tuple[list[TelegramMessage], bool]:
    """Return the next older page after one stable cursor."""
    if conversation is None or before_created_at is None or before_id <= 0:
        return [], False
    rows = list(
        visible_messages(conversation)
        .filter(
            Q(created_at__lt=before_created_at)
            | Q(created_at=before_created_at, pk__lt=before_id)
        )
        .order_by("-created_at", "-pk")[: max(1, limit) + 1]
    )
    # ``created_at`` is not unique; the (created_at, pk) cursor keeps the page
    # stable when several rows share one timestamp.
    if len(rows) > max(1, limit):
        rows = rows[: max(1, limit)]
        has_more = True
    else:
        has_more = False
    rows.reverse()
    return rows, has_more


def messages_after(conversation, *, after_id: int) -> list[TelegramMessage]:
    """Return messages newer than one id for the open-chat poller."""
    if conversation is None or after_id <= 0:
        return newest_messages(conversation, limit=CHAT_POLL_LIMIT)
    rows = list(
        visible_messages(conversation)
        .filter(pk__gt=after_id)
        .order_by("pk")[:CHAT_POLL_LIMIT]
    )
    return rows


def message_presentation(message: TelegramMessage) -> dict:
    """Return one student-safe bubble description.

    The team identity is deliberately opaque: a student sees "Bible Institute
    team", never the administrator's account, name or handle.
    """
    attachment_view = None
    attachment = next(iter(message.attachments.all()), None)
    if attachment is not None and attachment.is_available and attachment.r2_key:
        mime_type = inline_attachment_mime_type(attachment.mime_type) or ""
        attachment_view = {
            "url": attachment.id,
            "is_image": mime_type.startswith("image/"),
            "is_audio": mime_type.startswith("audio/"),
            "mime_type": mime_type,
            "name": attachment.original_name or "",
            "duration_seconds": attachment.duration_seconds,
        }
    is_team = message.direction == TelegramMessage.Direction.OUTBOUND
    return {
        "id": message.pk,
        "is_team": is_team,
        "text": message.text,
        "created_at": message.created_at,
        "content_type": message.content_type,
        "attachment": attachment_view,
        "can_delete": (
            not is_team
            and message.student_deleted_at is None
            and message.content_type != TelegramMessage.ContentType.DIGEST
        ),
    }


def chat_page_context(conversation) -> dict:
    """Return the bounded first page and the composer limits for the page."""
    rows = newest_messages(conversation)
    oldest = rows[0] if rows else None
    return {
        "chat_conversation": conversation,
        "chat_messages": [message_presentation(message) for message in rows],
        "chat_has_older": bool(rows) and _has_older(conversation, oldest),
        "chat_oldest": oldest,
        "chat_last_id": rows[-1].pk if rows else 0,
        "chat_text_max_length": CHAT_TEXT_MAX_LENGTH,
        "chat_audio_max_seconds": CHAT_AUDIO_MAX_SECONDS,
        "chat_attachment_enabled": chat_storage_configured(),
    }


def _has_older(conversation, oldest) -> bool:
    if conversation is None or oldest is None:
        return False
    return visible_messages(conversation).filter(
        Q(created_at__lt=oldest.created_at)
        | Q(created_at=oldest.created_at, pk__lt=oldest.pk)
    ).exists()


def send_student_message(*, user, text: str, upload=None) -> TelegramMessage:
    """Persist one student chat message from the website.

    The conversation is the same durable row the Telegram bot writes, so an
    admin sees the message in the existing support workspace and can reply
    through either channel.
    """
    from .telegram.support import conversation_for_user

    body = (text or "").strip()
    if len(body) > CHAT_TEXT_MAX_LENGTH:
        raise SupportChatError(
            _("Messages must be %(count)s characters or fewer.")
            % {"count": CHAT_TEXT_MAX_LENGTH}
        )
    spec = None
    if upload is not None:
        spec = _validated_attachment(upload)
    if not body and spec is None:
        raise SupportChatError(_("Write a message or attach an image or audio file."))
    if spec is not None and not chat_storage_configured():
        raise SupportChatError(_("Support attachments are unavailable right now."))

    with transaction.atomic():
        conversation = conversation_for_user(user)
        if conversation.status == TelegramConversation.Status.BLOCKED:
            raise SupportChatError(_("This conversation is closed."))
        try:
            stored = TelegramMessage.objects.create(
                conversation=conversation,
                direction=TelegramMessage.Direction.INBOUND,
                sender_user=user,
                content_type=_content_type_for(spec),
                origin=TelegramMessage.Origin.WEB,
                text=body,
                delivery_status=TelegramMessage.DeliveryStatus.RECEIVED,
            )
        except Exception as exc:
            logger.exception("support chat message creation failed")
            raise SupportChatError(_("The message could not be sent.")) from exc

        conversation.last_message_at = timezone.now()
        conversation.version += 1
        if conversation.status == TelegramConversation.Status.HANDLED:
            conversation.status = TelegramConversation.Status.OPEN
            conversation.claimed_by = None
            conversation.claimed_at = None
            conversation.handled_at = None
            conversation.handled_by = None
        conversation.admin_read_at = None
        conversation.admin_read_by = None
        conversation.save(update_fields=[
            "status", "claimed_by", "claimed_at", "handled_at", "handled_by",
            "admin_read_at", "admin_read_by", "last_message_at", "version",
            "updated_at",
        ])
        if spec is not None:
            _store_attachment(stored, spec, upload)

    _enqueue_admin_digest(stored.pk)
    return stored


def _content_type_for(spec: SupportAttachmentSpec | None) -> str:
    if spec is None:
        return TelegramMessage.ContentType.TEXT
    if spec.kind == "photo":
        return TelegramMessage.ContentType.PHOTO
    if spec.kind == "voice":
        return TelegramMessage.ContentType.VOICE
    return TelegramMessage.ContentType.AUDIO


def _validated_attachment(upload) -> SupportAttachmentSpec:
    try:
        spec = classify_uploaded_file(upload, audio_max_seconds=CHAT_AUDIO_MAX_SECONDS)
    except AttachmentValidationError as exc:
        raise SupportChatError(str(exc)) from exc
    if spec.kind not in CHAT_ATTACHMENT_KINDS:
        raise SupportChatError(_("Only images and audio recordings can be sent in the chat."))
    return spec


def _store_attachment(
    message: TelegramMessage,
    spec: SupportAttachmentSpec,
    upload,
) -> TelegramAttachment:
    """Store one bounded upload privately and record its metadata."""
    client = get_r2_client()
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if client is None or not bucket:
        raise SupportChatError(_("Support attachments are unavailable right now."))
    key = (
        f"{CHAT_ATTACHMENT_PREFIX}/{message.conversation.user_id}/"
        f"{timezone.now():%Y%m}/{message.pk}-{spec.filename}"
    )
    try:
        digest = hashlib.sha256()
        upload.seek(0)
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
        if not upload_to_bucket(client, bucket, upload, key, spec.mime_type):
            raise SupportChatError(_("The attachment could not be stored."))
        upload.seek(0)
    except SupportChatError:
        raise
    except Exception as exc:
        logger.exception("support chat attachment storage failed")
        raise SupportChatError(_("The attachment could not be stored.")) from exc
    try:
        return TelegramAttachment.objects.create(
            message=message,
            telegram_file_id="",
            media_type=spec.kind,
            r2_key=key,
            original_name=spec.filename,
            mime_type=spec.mime_type,
            size_bytes=spec.size,
            duration_seconds=int(spec.duration_seconds) if spec.duration_seconds else None,
            sha256=digest.hexdigest(),
            is_available=True,
        )
    except Exception:
        logger.exception("support chat attachment record failed")
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            logger.warning("support chat attachment cleanup failed")
        raise SupportChatError(_("The attachment could not be stored.")) from None


def chat_storage_configured() -> bool:
    """Return whether private support attachment storage is configured."""
    return bool(getattr(settings, "TELEGRAM_R2_BUCKET_NAME", ""))


def _enqueue_admin_digest(message_id: int) -> None:
    """Tell the Telegram-linked admins about a website message, best effort.

    The message is already durable, so a missing broker or bot configuration
    must never fail the student's send.
    """
    try:
        from .telegram_tasks import send_web_support_digest

        send_web_support_digest.delay(message_id)
    except Exception:
        logger.warning(
            "support chat admin digest not queued",
            extra={"event": "support_chat_digest_enqueue_failed", "message_id": message_id},
        )


def delete_student_message(*, user, message_id: int) -> bool:
    """Remove one of the student's own messages from his chat.

    The row is kept because the support workspace, the Telegram digest and the
    answer attribution still reference it. Only the student's own view is
    affected, and only inbound, non-digest messages can be removed.
    """
    with transaction.atomic():
        message = (
            TelegramMessage.objects.select_for_update()
            .filter(
                pk=message_id,
                conversation__user_id=user.pk,
                direction=TelegramMessage.Direction.INBOUND,
            )
            .exclude(content_type=TelegramMessage.ContentType.DIGEST)
            .first()
        )
        if message is None:
            raise SupportChatError(_("This message is no longer available."))
        if message.student_deleted_at is not None:
            return False
        message.student_deleted_at = timezone.now()
        message.save(update_fields=["student_deleted_at"])
    return True


def student_attachment(user, attachment_id: int) -> TelegramAttachment:
    """Return one attachment only when it belongs to this student's chat."""
    attachment = (
        TelegramAttachment.objects.select_related(
            "message",
            "message__conversation",
        )
        .filter(
            pk=attachment_id,
            message__conversation__user_id=user.pk,
            message__student_deleted_at__isnull=True,
        )
        .exclude(message__content_type=TelegramMessage.ContentType.DIGEST)
        .first()
    )
    if attachment is None or not attachment.is_available or not attachment.r2_key:
        raise Http404
    return attachment


def _attachment_chunks(body):
    """Stream one R2 body in bounded chunks and always close it."""
    try:
        yield from body.iter_chunks(chunk_size=64 * 1024)
    finally:
        try:
            body.close()
        except Exception:
            pass


def stream_support_attachment(request, attachment: TelegramAttachment) -> HttpResponse:
    """Stream one private support attachment for an authorized caller."""
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if not bucket:
        raise Http404
    client = get_r2_client()
    if client is None:
        raise Http404
    range_header = request.headers.get("Range", "").strip()
    if range_header and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", range_header):
        return HttpResponse(status=416)
    params = {"Bucket": bucket, "Key": attachment.r2_key}
    if range_header:
        params["Range"] = range_header
    try:
        storage_response = client.get_object(**params)
    except Exception as exc:
        status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 416:
            return HttpResponse(status=416)
        raise Http404 from exc

    filename = os.path.basename(attachment.original_name or attachment.r2_key) or "attachment"
    content_type = inline_attachment_mime_type(attachment.mime_type) or "application/octet-stream"
    as_attachment = request.GET.get("download") == "1" or content_type == "application/octet-stream"
    response = StreamingHttpResponse(
        _attachment_chunks(storage_response["Body"]),
        status=206 if storage_response.get("ContentRange") else 200,
        content_type=content_type,
    )
    response["Accept-Ranges"] = "bytes"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store" if as_attachment else "private, max-age=300"
    response["Content-Disposition"] = content_disposition_header(as_attachment, filename)
    if storage_response.get("ContentLength") is not None:
        response["Content-Length"] = str(storage_response["ContentLength"])
    if storage_response.get("ContentRange"):
        response["Content-Range"] = storage_response["ContentRange"]
    return response
