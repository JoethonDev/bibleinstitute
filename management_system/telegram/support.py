"""Durable Telegram support conversations and anonymous admin replies."""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import uuid
from datetime import datetime, timedelta
from typing import cast

import telebot
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import (
    TelegramAccount,
    TelegramAttachment,
    TelegramConversation,
    TelegramMessage,
    User,
)
from ..utils.storage_operations import get_r2_client, upload_to_bucket
from .attachments import (
    CAPTION_MAX_LENGTH,
    SupportAttachmentSpec,
    classify_attachment,
    safe_filename,
)
from .text import strip_telegram_links


logger = logging.getLogger(__name__)

SUPPORT_MAX_MEDIA_BYTES = 50 * 1024 * 1024
SUPPORTED_MEDIA = {"photo", "document", "video", "voice", "audio"}
# Telegram cannot edit or delete bot messages indefinitely. Older digests are
# terminally marked failed without making an API call.
SUPPORT_DIGEST_ACTION_WINDOW = timedelta(hours=48)


class SupportReplyError(Exception):
    """A safe, localized failure from the shared support reply boundary."""


def _require_admin(user: User) -> None:
    if not user.is_authenticated or not user.role or user.role.role != "admin":
        raise SupportReplyError(_("Only administrators can start Telegram conversations."))


@transaction.atomic
def start_conversation(*, admin: User, user_id: int) -> TelegramConversation:
    """Open or return one support conversation for a linked Telegram user."""
    _require_admin(admin)
    try:
        target = User.objects.select_for_update().get(pk=user_id)
    except User.DoesNotExist as exc:
        raise SupportReplyError(_("The selected user no longer exists.")) from exc

    account = TelegramAccount.objects.filter(user=target, is_active=True).first()
    if account is None:
        raise SupportReplyError(_("This user has no active linked Telegram account."))

    conversation = TelegramConversation.objects.select_for_update().filter(
        user=target,
    ).order_by("-last_message_at", "-updated_at", "-pk").first()
    if conversation:
        if conversation.status == TelegramConversation.Status.HANDLED:
            conversation.status = TelegramConversation.Status.OPEN
            conversation.claimed_by = None
            conversation.claimed_at = None
            conversation.handled_at = None
            conversation.save(update_fields=[
                "status", "claimed_by", "claimed_at", "handled_at", "updated_at",
            ])
        return conversation
    return TelegramConversation.objects.create(
        user=target,
        status=TelegramConversation.Status.OPEN,
    )


def latest_inbound_message_queryset():
    """Return inbound support messages ordered newest-first for read-state checks."""
    return TelegramMessage.objects.filter(
        direction=TelegramMessage.Direction.INBOUND,
    ).exclude(
        content_type=TelegramMessage.ContentType.DIGEST,
    ).order_by("-created_at", "-pk")


def latest_message_queryset():
    """Non-digest support messages ordered newest-first for read-state checks."""
    return TelegramMessage.objects.exclude(
        content_type=TelegramMessage.ContentType.DIGEST,
    ).order_by("-created_at", "-pk")


def unread_conversation_filter() -> Q:
    """Canonical unread predicate.

    Requires the ``latest_inbound_at`` and ``latest_message_direction``
    annotations (both exclude digest rows). A conversation is unread while the
    student wrote last and that message is newer than the read marker; an admin
    message last always counts as handled.
    """
    return Q(latest_message_direction=TelegramMessage.Direction.INBOUND) & (
        Q(admin_read_at__isnull=True) | Q(latest_inbound_at__gt=F("admin_read_at"))
    )


def processed_conversation_filter() -> Q:
    """Canonical processed predicate.

    Requires the same annotations. A conversation is processed when the admin
    wrote last, has no inbound message at all, or the read marker is at/after
    the newest inbound message.
    """
    return (
        Q(latest_message_direction=TelegramMessage.Direction.OUTBOUND)
        | Q(latest_inbound_at__isnull=True)
        | Q(admin_read_at__isnull=False, latest_inbound_at__lte=F("admin_read_at"))
    )


def unread_conversations_queryset():
    """Conversations whose newest inbound message is newer than the read marker."""
    latest_inbound = latest_inbound_message_queryset().filter(
        conversation=OuterRef("pk"),
    )
    latest_message = latest_message_queryset().filter(
        conversation=OuterRef("pk"),
    )
    return TelegramConversation.objects.annotate(
        latest_inbound_at=Subquery(latest_inbound.values("created_at")[:1]),
        latest_message_direction=Subquery(latest_message.values("direction")[:1]),
    ).filter(unread_conversation_filter())


def count_unread_conversations() -> int:
    """Return the number of conversations still awaiting an admin read marker."""
    return unread_conversations_queryset().count()


@transaction.atomic
def mark_conversation_read(*, conversation_id: int, admin: User) -> TelegramConversation:
    """Record that an admin has read one support conversation."""
    _require_admin(admin)
    try:
        conversation = TelegramConversation.objects.select_for_update().get(pk=conversation_id)
    except TelegramConversation.DoesNotExist as exc:
        raise SupportReplyError(_("This conversation is no longer available.")) from exc
    conversation.admin_read_at = timezone.now()
    conversation.save(update_fields=["admin_read_at", "updated_at"])
    return conversation


def mark_all_conversations_read(*, admin: User) -> int:
    """Record the read marker for every conversation with unseen inbound messages."""
    _require_admin(admin)
    now = timezone.now()
    return TelegramConversation.objects.filter(
        pk__in=unread_conversations_queryset().values("pk"),
    ).update(admin_read_at=now, updated_at=now)


def _message_payload(message: dict) -> dict:
    if isinstance(message.get("text"), str):
        return {
            "content_type": TelegramMessage.ContentType.TEXT,
            "text": strip_telegram_links(message["text"].strip(), message.get("entities")),
        }
    if isinstance(message.get("photo"), list) and message["photo"]:
        photo = max((item for item in message["photo"] if isinstance(item, dict)), key=lambda item: int(item.get("file_size") or 0), default={})
        return {
            "content_type": TelegramMessage.ContentType.PHOTO,
            "text": strip_telegram_links(str(message.get("caption") or ""), message.get("caption_entities")),
            "file_id": photo.get("file_id"),
            "file_size": photo.get("file_size") or 0,
            "file_name": "telegram-photo.jpg",
            "mime_type": "image/jpeg",
        }
    voice = message.get("voice")
    if isinstance(voice, dict):
        return {
            "content_type": TelegramMessage.ContentType.VOICE,
            "text": "",
            "file_id": voice.get("file_id"),
            "file_size": voice.get("file_size") or 0,
            "file_name": "telegram-voice.oga",
            "mime_type": "audio/ogg",
        }
    audio = message.get("audio")
    if isinstance(audio, dict):
        original_name = str(audio.get("file_name") or "telegram-audio.mp3")
        return {
            "content_type": TelegramMessage.ContentType.AUDIO,
            "text": strip_telegram_links(str(message.get("caption") or ""), message.get("caption_entities")),
            "file_id": audio.get("file_id"),
            "file_size": audio.get("file_size") or 0,
            "file_name": original_name,
            "mime_type": audio.get("mime_type") or mimetypes.guess_type(original_name)[0] or "",
        }
    for field, content_type in (("document", TelegramMessage.ContentType.DOCUMENT), ("video", TelegramMessage.ContentType.VIDEO)):
        value = message.get(field)
        if isinstance(value, dict):
            return {
                "content_type": content_type,
                "text": strip_telegram_links(str(message.get("caption") or ""), message.get("caption_entities")),
                "file_id": value.get("file_id"),
                "file_size": value.get("file_size") or 0,
                "file_name": value.get("file_name") or f"telegram-{field}",
                "mime_type": value.get("mime_type") or "",
            }
    keys = sorted(str(key) for key in message if key not in {"chat", "from", "date", "message_id"})
    return {
        "content_type": TelegramMessage.ContentType.UNSUPPORTED,
        "text": _("Unsupported Telegram message: %(types)s") % {"types": ", ".join(keys)[:300]},
    }


def _conversation_for_user(user: User) -> TelegramConversation:
    active = TelegramConversation.objects.select_for_update().filter(
        user=user,
        status__in=(TelegramConversation.Status.OPEN, TelegramConversation.Status.CLAIMED),
    ).first()
    if active:
        return active
    latest = TelegramConversation.objects.select_for_update().filter(
        user=user,
    ).order_by("-last_message_at", "-updated_at", "-pk").first()
    if latest:
        latest.status = TelegramConversation.Status.OPEN
        latest.claimed_by = None
        latest.claimed_at = None
        latest.handled_at = None
        latest.save(update_fields=[
            "status", "claimed_by", "claimed_at", "handled_at", "updated_at",
        ])
        return latest
    try:
        with transaction.atomic():
            return TelegramConversation.objects.create(
                user=user,
                status=TelegramConversation.Status.OPEN,
                last_message_at=timezone.now(),
            )
    except IntegrityError:
        return TelegramConversation.objects.select_for_update().get(
            user=user,
            status__in=(TelegramConversation.Status.OPEN, TelegramConversation.Status.CLAIMED),
        )


@transaction.atomic
def persist_inbound_message(*, user: User, update_id: int, message: dict) -> tuple[TelegramConversation, TelegramMessage, bool]:
    """Persist one inbound update before any Telegram/R2 notification work."""
    existing = TelegramMessage.objects.select_related("conversation").filter(telegram_update_id=update_id).first()
    if existing:
        return existing.conversation, existing, False
    conversation = _conversation_for_user(user)
    payload = _message_payload(message)
    try:
        stored = TelegramMessage.objects.create(
            conversation=conversation,
            direction=TelegramMessage.Direction.INBOUND,
            sender_user=user,
            telegram_chat_id=int(message["chat"]["id"]),
            telegram_message_id=int(message["message_id"]),
            telegram_update_id=update_id,
            content_type=payload["content_type"],
            text=payload.get("text", ""),
        )
    except IntegrityError:
        stored = TelegramMessage.objects.select_related("conversation").get(telegram_update_id=update_id)
        return stored.conversation, stored, False
    conversation.last_message_at = timezone.now()
    conversation.version += 1
    if conversation.status == TelegramConversation.Status.HANDLED:
        conversation.status = TelegramConversation.Status.OPEN
        conversation.claimed_by = None
        conversation.claimed_at = None
        conversation.handled_at = None
    conversation.save(update_fields=["status", "claimed_by", "claimed_at", "handled_at", "last_message_at", "version", "updated_at"])
    if payload["content_type"] in SUPPORTED_MEDIA and payload.get("file_id"):
        TelegramAttachment.objects.create(
            message=stored,
            telegram_file_id=str(payload["file_id"]),
            media_type=payload["content_type"],
            original_name=safe_filename(payload.get("file_name", ""), f"telegram-{payload['content_type']}"),
            mime_type=str(payload.get("mime_type") or "")[:255],
            size_bytes=max(0, int(payload.get("file_size") or 0)),
        )
    return conversation, stored, True


def _r2_client():
    if not getattr(settings, "TELEGRAM_R2_BUCKET_NAME", ""):
        return None
    return get_r2_client()


def store_attachment(bot: telebot.TeleBot, attachment: TelegramAttachment) -> bool:
    """Download one bounded Telegram file and persist it under the support prefix."""
    if attachment.size_bytes and attachment.size_bytes > SUPPORT_MAX_MEDIA_BYTES:
        attachment.error = _("The attachment is too large to store.")
        attachment.save(update_fields=["error"])
        return False
    client = _r2_client()
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if client is None or not bucket:
        attachment.error = _("Support storage is unavailable.")
        attachment.save(update_fields=["error"])
        return False
    try:
        file_info = bot.get_file(attachment.telegram_file_id)
        remote_size = int(getattr(file_info, "file_size", 0) or 0)
        if remote_size > SUPPORT_MAX_MEDIA_BYTES:
            raise ValueError("oversize")
        file_path = getattr(file_info, "file_path", None)
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("missing file path")
        content = bot.download_file(file_path)
        if not isinstance(content, (bytes, bytearray)) or not content or len(content) > SUPPORT_MAX_MEDIA_BYTES:
            raise ValueError("invalid size")
        digest = hashlib.sha256(content).hexdigest()
        filename = safe_filename(attachment.original_name, f"telegram-{attachment.media_type}")
        key = f"Telegram Support/{attachment.message.conversation.user_id}/{attachment.message_id}/{filename}"
        if not upload_to_bucket(client, bucket, io.BytesIO(content), key, attachment.mime_type or "application/octet-stream"):
            raise ValueError("upload failed")
        attachment.r2_key = key
        attachment.size_bytes = len(content)
        attachment.sha256 = digest
        attachment.is_available = True
        attachment.error = ""
        attachment.save(update_fields=["r2_key", "size_bytes", "sha256", "is_available", "error"])
        return True
    except Exception:
        attachment.error = _("The attachment could not be stored.")
        attachment.save(update_fields=["error"])
        return False


def _message_text(message: TelegramMessage) -> str:
    if message.content_type == TelegramMessage.ContentType.VOICE:
        body = message.text.strip() or _("Voice message")
    elif message.content_type == TelegramMessage.ContentType.AUDIO:
        body = message.text.strip() or _("Audio message")
    else:
        body = message.text.strip() or _("[media message]")
    if message.content_type == TelegramMessage.ContentType.UNSUPPORTED:
        return _("Unsupported message") + ": " + body
    attachments = list(message.attachments.all())
    if attachments:
        attachment = attachments[0]
        body += "\n" + _("Attachment") + ": " + (attachment.original_name or attachment.media_type)
    return body


def _admin_accounts():
    return TelegramAccount.objects.select_related("user", "user__role").filter(
        is_active=True,
        user__is_active=True,
        user__application_status="active",
        user__role__role="admin",
    )


def send_support_digest(bot: telebot.TeleBot, conversation_id: int, message_id: int | None = None) -> int:
    """Announce one inbound student message to every linked admin account."""
    try:
        conversation = TelegramConversation.objects.select_related("user").prefetch_related("messages__attachments").get(pk=conversation_id)
    except TelegramConversation.DoesNotExist:
        return 0
    sent = 0
    inbound_messages = [
        message for message in conversation.messages.all()
        if message.direction == TelegramMessage.Direction.INBOUND
        and (message_id is None or message.pk == message_id)
    ]
    sender_identity = conversation.user.telegram_display_identity
    for message in inbound_messages:
        text = _("Support message from %(name)s") % {"name": sender_identity}
        text += "\n\n" + _message_text(message)
        for account in _admin_accounts().iterator(chunk_size=100):
            result = bot.send_message(account.telegram_chat_id, text)
            TelegramMessage.objects.create(
                conversation=conversation,
                direction=TelegramMessage.Direction.OUTBOUND,
                telegram_chat_id=account.telegram_chat_id,
                telegram_message_id=getattr(result, "message_id", None),
                source_message=message,
                content_type=TelegramMessage.ContentType.DIGEST,
                text=text,
                delivery_status=TelegramMessage.DeliveryStatus.SENT,
                sent_at=timezone.now(),
            )
            sent += 1
    if inbound_messages:
        answered_reply = TelegramMessage.objects.filter(
            conversation=conversation,
            direction=TelegramMessage.Direction.OUTBOUND,
            delivery_status=TelegramMessage.DeliveryStatus.SENT,
            sender_user__isnull=False,
            sent_at__gte=min(message.created_at for message in inbound_messages),
        ).exclude(
            content_type=TelegramMessage.ContentType.DIGEST,
        ).select_related("sender_user").order_by("-sent_at", "-pk").first()
        if answered_reply:
            try:
                resolve_answered_announcements(
                    bot,
                    conversation_id=conversation.pk,
                    responder=answered_reply.sender_user,
                    answered_before=answered_reply.sent_at,
                )
            except Exception:
                logger.exception(
                    "telegram_support_resolution_error conversation=%s",
                    conversation.pk,
                )
    return sent


def handle_student_message(bot: telebot.TeleBot, *, user: User, update_id: int, message: dict) -> bool:
    conversation, stored, created = persist_inbound_message(user=user, update_id=update_id, message=message)
    if not created:
        if not conversation.messages.filter(content_type=TelegramMessage.ContentType.DIGEST).exists():
            send_support_digest(bot, conversation.pk)
        return True
    for attachment in stored.attachments.all():
        store_attachment(bot, attachment)
    if stored.content_type == TelegramMessage.ContentType.UNSUPPORTED:
        bot.send_message(
            int(message["chat"]["id"]),
            _("This message type is not supported. Text, photos, documents, videos, and voice messages are accepted."),
        )
    else:
        send_support_digest(bot, conversation.pk, message_id=stored.pk)
    return True


def _answered_source_message(
    conversation: TelegramConversation,
    reply_target: TelegramMessage | None,
) -> TelegramMessage | None:
    """Return the student message a reply answers, or the pending student message."""
    if reply_target is not None:
        if reply_target.content_type == TelegramMessage.ContentType.DIGEST:
            if reply_target.source_message_id:
                return reply_target.source_message
            # A legacy digest without a source link still answers the pending
            # student message.
        elif reply_target.direction == TelegramMessage.Direction.INBOUND:
            return reply_target
        else:
            # Replying to an admin message is a threaded follow-up, never an
            # answer to the pending student message.
            return None
    return TelegramMessage.objects.filter(
        conversation=conversation,
        direction=TelegramMessage.Direction.INBOUND,
    ).order_by("-created_at", "-pk").first()


def _is_missing_message_error(exc: Exception) -> bool:
    """Telegram reports an already-deleted digest as a not-found message error."""
    message = str(exc).lower()
    return "message to delete not found" in message or "message to edit not found" in message


def resolve_answered_announcements(
    bot: telebot.TeleBot,
    *,
    conversation_id: int,
    responder: User,
    answered_before: datetime | None = None,
) -> int:
    """Resolve every pending digest in a conversation for the other admins."""
    now = timezone.now()
    answered_before = answered_before or now
    responder_chats = TelegramAccount.objects.filter(
        user_id=responder.pk,
    ).values_list("telegram_chat_id", flat=True)
    digests = TelegramMessage.objects.filter(
        conversation_id=conversation_id,
        content_type=TelegramMessage.ContentType.DIGEST,
        resolved_at__isnull=True,
    ).exclude(
        telegram_chat_id__in=responder_chats,
    )
    digests = digests.filter(
        Q(source_message__created_at__lte=answered_before)
        | Q(source_message__isnull=True, created_at__lte=answered_before)
    )
    resolved = 0
    for digest in digests.iterator(chunk_size=100):
        resolution = TelegramMessage.Resolution.FAILED
        terminal_failure = (
            digest.telegram_message_id is None
            or digest.created_at <= now - SUPPORT_DIGEST_ACTION_WINDOW
        )
        if not terminal_failure:
            message_id = cast(int, digest.telegram_message_id)
            try:
                bot.delete_message(digest.telegram_chat_id, message_id)
            except Exception as exc:
                if _is_missing_message_error(exc):
                    resolution = TelegramMessage.Resolution.DELETED
                else:
                    try:
                        bot.edit_message_text(
                            _("Replied by @%(username)s") % {"username": responder.username},
                            chat_id=digest.telegram_chat_id,
                            message_id=message_id,
                        )
                    except Exception as edit_exc:
                        if _is_missing_message_error(edit_exc):
                            resolution = TelegramMessage.Resolution.DELETED
                    else:
                        resolution = TelegramMessage.Resolution.EDITED
            else:
                resolution = TelegramMessage.Resolution.DELETED
        if resolution == TelegramMessage.Resolution.FAILED:
            logger.warning(
                "telegram_support_resolution_failed digest=%s chat=%s",
                digest.pk,
                digest.telegram_chat_id,
            )
            if terminal_failure:
                TelegramMessage.objects.filter(pk=digest.pk).update(
                    resolution=TelegramMessage.Resolution.FAILED,
                    resolved_at=now,
                )
            else:
                TelegramMessage.objects.filter(pk=digest.pk).update(
                    resolution=TelegramMessage.Resolution.FAILED,
                )
        else:
            TelegramMessage.objects.filter(pk=digest.pk).update(
                resolved_at=timezone.now(),
                resolution=resolution,
            )
        resolved += 1
    return resolved


def _queue_reply(
    *,
    admin: User,
    conversation_id: int,
    text: str,
    reply_to_telegram_message_id: int | None,
    origin: str,
    content_type: str = TelegramMessage.ContentType.TEXT,
) -> tuple[TelegramMessage, int, int | None]:
    """Queue one reply under a short row lock, then release before API I/O."""
    _require_admin(admin)
    with transaction.atomic():
        try:
            conversation = TelegramConversation.objects.select_for_update().get(pk=conversation_id)
        except TelegramConversation.DoesNotExist as exc:
            raise SupportReplyError(_("This conversation is no longer available.")) from exc
        if conversation.status == TelegramConversation.Status.BLOCKED:
            raise SupportReplyError(_("This conversation is blocked."))

        reply_target = None
        if reply_to_telegram_message_id is not None:
            reply_target = TelegramMessage.objects.filter(
                conversation__user_id=conversation.user_id,
                telegram_message_id=reply_to_telegram_message_id,
            ).first()
            if reply_target is None:
                raise SupportReplyError(_("The selected message is no longer available."))

        source_message = _answered_source_message(conversation, reply_target)
        if reply_target is not None and source_message is not None:
            already_answered = TelegramMessage.objects.filter(
                conversation=conversation,
                direction=TelegramMessage.Direction.OUTBOUND,
                source_message=source_message,
            ).exclude(
                content_type=TelegramMessage.ContentType.DIGEST,
            ).exclude(
                delivery_status=TelegramMessage.DeliveryStatus.FAILED,
            ).exclude(
                sender_user=admin,
            ).exists()
            if already_answered:
                raise SupportReplyError(_("This message was already answered by another admin."))

        student_account = TelegramAccount.objects.filter(
            user_id=conversation.user_id,
            is_active=True,
        ).first()
        if student_account is None:
            raise SupportReplyError(_("The student's Telegram account is no longer available."))

        telegram_reply_to = None
        if reply_target and reply_target.content_type != TelegramMessage.ContentType.DIGEST:
            if reply_target.telegram_chat_id == student_account.telegram_chat_id:
                telegram_reply_to = reply_to_telegram_message_id

        outbound = TelegramMessage.objects.create(
            conversation=conversation,
            direction=TelegramMessage.Direction.OUTBOUND,
            sender_user=admin,
            telegram_chat_id=student_account.telegram_chat_id,
            reply_to_telegram_message_id=reply_to_telegram_message_id,
            source_message=source_message,
            content_type=content_type,
            origin=origin,
            text=text,
            delivery_status=TelegramMessage.DeliveryStatus.QUEUED,
        )
        return outbound, student_account.telegram_chat_id, telegram_reply_to


def _fail_reply(outbound: TelegramMessage, message: str) -> None:
    outbound.delivery_status = TelegramMessage.DeliveryStatus.FAILED
    outbound.delivery_error = message[:500]
    outbound.save(update_fields=["delivery_status", "delivery_error"])


def _finalize_delivered_reply(
    bot: telebot.TeleBot,
    outbound: TelegramMessage,
    admin: User,
    result,
) -> TelegramMessage:
    """Record one successful Telegram send and resolve pending digests."""
    outbound.telegram_message_id = getattr(result, "message_id", None)
    outbound.delivery_status = TelegramMessage.DeliveryStatus.SENT
    outbound.sent_at = timezone.now()
    outbound.save(update_fields=["telegram_message_id", "delivery_status", "sent_at"])
    TelegramConversation.objects.filter(pk=outbound.conversation_id).update(
        status=TelegramConversation.Status.HANDLED,
        claimed_by=None,
        claimed_at=None,
        handled_at=timezone.now(),
        last_message_at=outbound.created_at,
        admin_read_at=timezone.now(),
        version=F("version") + 1,
        updated_at=timezone.now(),
    )
    try:
        resolve_answered_announcements(
            bot,
            conversation_id=outbound.conversation_id,
            responder=admin,
            answered_before=outbound.sent_at,
        )
    except Exception:
        logger.exception(
            "telegram_support_resolution_error conversation=%s",
            outbound.conversation_id,
        )
    return outbound


def reply_to_conversation(
    bot: telebot.TeleBot,
    *,
    admin: User,
    conversation_id: int,
    text: str,
    origin: str,
    reply_to_telegram_message_id: int | None = None,
) -> TelegramMessage:
    """Queue and deliver one anonymous admin reply for Telegram or web chat."""
    outbound, student_chat_id, telegram_reply_to = _queue_reply(
        admin=admin,
        conversation_id=conversation_id,
        text=text,
        reply_to_telegram_message_id=reply_to_telegram_message_id,
        origin=origin,
    )
    try:
        result = bot.send_message(
            student_chat_id,
            _("Admin") + ": " + text,
            reply_to_message_id=telegram_reply_to,
        )
    except Exception as exc:
        _fail_reply(outbound, _("Telegram message delivery failed."))
        raise SupportReplyError(_("The reply could not be delivered; the conversation remains open.")) from exc
    return _finalize_delivered_reply(bot, outbound, admin, result)


def _attachment_content_type(kind: str) -> str:
    return {
        "photo": TelegramMessage.ContentType.PHOTO,
        "video": TelegramMessage.ContentType.VIDEO,
        "document": TelegramMessage.ContentType.DOCUMENT,
    }[kind]


def _classify_uploaded_file(uploaded_file) -> SupportAttachmentSpec:
    try:
        uploaded_file.seek(0)
        head = uploaded_file.read(16)
        uploaded_file.seek(0)
        return classify_attachment(
            filename=str(getattr(uploaded_file, "name", "") or ""),
            size=int(getattr(uploaded_file, "size", 0) or 0),
            head=head,
        )
    except ValueError as exc:
        raise SupportReplyError(str(exc)) from exc
    except Exception as exc:
        raise SupportReplyError(_("The attachment could not be read.")) from exc


def _store_outbound_attachment(
    outbound: TelegramMessage,
    spec: SupportAttachmentSpec,
    uploaded_file,
) -> TelegramAttachment:
    """Persist one dashboard upload under the private support prefix."""
    client = _r2_client()
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if client is None or not bucket:
        raise SupportReplyError(_("Telegram storage is not configured."))
    filename = spec.filename
    key = f"Telegram Support/Outbound/{outbound.conversation.user_id}/{uuid.uuid4().hex}-{filename}"
    try:
        digest = hashlib.sha256()
        uploaded_file.seek(0)
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        uploaded_file.seek(0)
        if not upload_to_bucket(client, bucket, uploaded_file, key, spec.mime_type):
            raise SupportReplyError(_("The attachment could not be stored."))
        uploaded_file.seek(0)
    except SupportReplyError:
        raise
    except Exception as exc:
        logger.exception("Telegram support attachment storage failed.")
        raise SupportReplyError(_("The attachment could not be stored.")) from exc
    try:
        return TelegramAttachment.objects.create(
            message=outbound,
            telegram_file_id="",
            media_type=spec.kind,
            r2_key=key,
            original_name=filename,
            mime_type=spec.mime_type,
            size_bytes=spec.size,
            sha256=digest.hexdigest(),
            is_available=True,
        )
    except Exception:
        logger.exception("Telegram support attachment record failed.")
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            logger.warning("Telegram support attachment cleanup failed.")
        raise SupportReplyError(_("The attachment could not be stored.")) from None


def _telegram_input_file(uploaded_file, filename: str) -> tuple[telebot.types.InputFile, io.IOBase]:
    """Return a telebot-compatible input and the stream to close after sending.

    Large Django uploads are ``TemporaryUploadedFile`` instances whose ``.file``
    is not an ``io.IOBase``; telebot accepts their temp path directly. Small
    uploads expose a ``BytesIO``. Both are handed over without copying the file.
    """
    try:
        path = uploaded_file.temporary_file_path()
    except (AttributeError, ValueError):
        path = None
    if path:
        input_file = telebot.types.InputFile(path, file_name=filename)
        return input_file, input_file.file
    stream = getattr(uploaded_file, "file", uploaded_file)
    if not isinstance(stream, io.IOBase):
        raise ValueError("unsupported upload stream")
    return telebot.types.InputFile(stream, file_name=filename), stream


def reply_with_attachment_to_conversation(
    bot: telebot.TeleBot,
    *,
    admin: User,
    conversation_id: int,
    text: str,
    uploaded_file,
    origin: str,
    reply_to_telegram_message_id: int | None = None,
) -> TelegramMessage:
    """Queue and deliver one admin reply carrying a JPEG/PNG, MP4 or PDF file."""
    text = str(text or "").strip()
    spec = _classify_uploaded_file(uploaded_file)
    if len(text) > CAPTION_MAX_LENGTH:
        raise SupportReplyError(_("Attachments support a message of 1,024 characters or fewer."))
    outbound, student_chat_id, telegram_reply_to = _queue_reply(
        admin=admin,
        conversation_id=conversation_id,
        text=text,
        reply_to_telegram_message_id=reply_to_telegram_message_id,
        origin=origin,
        content_type=_attachment_content_type(spec.kind),
    )
    try:
        _store_outbound_attachment(outbound, spec, uploaded_file)
    except SupportReplyError as exc:
        _fail_reply(outbound, str(exc))
        raise
    try:
        input_file, cleanup_stream = _telegram_input_file(uploaded_file, spec.filename)
    except Exception as exc:
        _fail_reply(outbound, _("The attachment could not be read."))
        raise SupportReplyError(_("The attachment could not be read.")) from exc
    try:
        send_kwargs = {}
        if text:
            send_kwargs["caption"] = text
        if telegram_reply_to is not None:
            send_kwargs["reply_to_message_id"] = telegram_reply_to
        if spec.kind == "photo":
            result = bot.send_photo(student_chat_id, input_file, **send_kwargs)
        elif spec.kind == "video":
            result = bot.send_video(student_chat_id, input_file, **send_kwargs)
        else:
            result = bot.send_document(student_chat_id, input_file, **send_kwargs)
    except Exception as exc:
        _fail_reply(outbound, _("Telegram message delivery failed."))
        raise SupportReplyError(_("The reply could not be delivered; the conversation remains open.")) from exc
    finally:
        try:
            cleanup_stream.close()
        except Exception:
            pass
    return _finalize_delivered_reply(bot, outbound, admin, result)


def _reply_target(message: dict, admin_chat_id: int) -> tuple[TelegramConversation | None, int | None]:
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        try:
            digest = TelegramMessage.objects.select_related("conversation").filter(
                telegram_chat_id=admin_chat_id,
                telegram_message_id=int(reply["message_id"]),
                content_type=TelegramMessage.ContentType.DIGEST,
            ).first()
        except (KeyError, TypeError, ValueError):
            digest = None
        if digest:
            return digest.conversation, int(reply["message_id"])
    text = message.get("text")
    if isinstance(text, str) and text.startswith("/reply "):
        parts = text.split(maxsplit=2)
        if len(parts) == 3 and parts[1].isdigit():
            return TelegramConversation.objects.filter(pk=int(parts[1])).first(), None
    return None, None


def handle_admin_message(bot: telebot.TeleBot, *, admin: User, message: dict) -> bool:
    try:
        admin_chat_id = int(message["chat"]["id"])
    except (KeyError, TypeError, ValueError):
        return True
    conversation, reply_to = _reply_target(message, admin_chat_id)
    if conversation is None:
        bot.send_message(admin_chat_id, _("Reply to a support digest or use /reply <conversation_id> <message>."))
        return True
    text = str(message.get("text") or message.get("caption") or "").strip()
    if text.startswith("/reply "):
        parts = text.split(maxsplit=2)
        text = parts[2] if len(parts) == 3 else ""
    if not text:
        bot.send_message(admin_chat_id, _("A reply message is required."))
        return True
    try:
        reply_to_conversation(
            bot,
            admin=admin,
            conversation_id=conversation.pk,
            text=text,
            reply_to_telegram_message_id=reply_to,
            origin=TelegramMessage.Origin.TELEGRAM,
        )
    except SupportReplyError as exc:
        bot.send_message(admin_chat_id, str(exc))
    return True
