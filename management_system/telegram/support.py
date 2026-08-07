"""Durable Telegram support conversations and anonymous admin replies."""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
from datetime import datetime

import telebot
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
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
from .linking import is_eligible_user


SUPPORT_MAX_MEDIA_BYTES = 50 * 1024 * 1024
SUPPORT_CALLBACK_PREFIX = "sup1"
SUPPORTED_MEDIA = {"photo", "document", "video"}


class SupportReplyError(Exception):
    """A safe, localized failure from the shared support reply boundary."""


def _support_callback(action: str, conversation_id: int) -> str:
    return f"{SUPPORT_CALLBACK_PREFIX}:{action}:{int(conversation_id)}"


def is_support_callback(data: object) -> bool:
    return isinstance(data, str) and data.startswith(f"{SUPPORT_CALLBACK_PREFIX}:")


def _parse_support_callback(data: str) -> tuple[str, int] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != SUPPORT_CALLBACK_PREFIX or not parts[1].isalpha():
        return None
    try:
        conversation_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    return parts[1], conversation_id


def _message_payload(message: dict) -> dict:
    if isinstance(message.get("text"), str):
        return {"content_type": TelegramMessage.ContentType.TEXT, "text": message["text"].strip()}
    if isinstance(message.get("photo"), list) and message["photo"]:
        photo = max((item for item in message["photo"] if isinstance(item, dict)), key=lambda item: int(item.get("file_size") or 0), default={})
        return {
            "content_type": TelegramMessage.ContentType.PHOTO,
            "text": str(message.get("caption") or ""),
            "file_id": photo.get("file_id"),
            "file_size": photo.get("file_size") or 0,
            "file_name": "telegram-photo.jpg",
            "mime_type": "image/jpeg",
        }
    for field, content_type in (("document", TelegramMessage.ContentType.DOCUMENT), ("video", TelegramMessage.ContentType.VIDEO)):
        value = message.get(field)
        if isinstance(value, dict):
            return {
                "content_type": content_type,
                "text": str(message.get("caption") or ""),
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


def _safe_filename(name: str, fallback: str) -> str:
    value = posixpath.basename(str(name or "")).replace("\\", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (value or fallback)[:180]


def _conversation_for_user(user: User) -> TelegramConversation:
    active = TelegramConversation.objects.select_for_update().filter(
        user=user,
        status__in=(TelegramConversation.Status.OPEN, TelegramConversation.Status.CLAIMED),
    ).first()
    if active:
        return active
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
            original_name=_safe_filename(payload.get("file_name", ""), f"telegram-{payload['content_type']}"),
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
        filename = _safe_filename(attachment.original_name, f"telegram-{attachment.media_type}")
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


def _digest_text(conversation: TelegramConversation) -> str:
    lines = []
    for message in conversation.messages.all():
        if message.direction != TelegramMessage.Direction.INBOUND:
            continue
        body = message.text.strip() or _("[media message]")
        if message.content_type == TelegramMessage.ContentType.UNSUPPORTED:
            body = _("Unsupported message") + ": " + body
        else:
            attachments = list(message.attachments.all())
            if attachments:
                attachment = attachments[0]
                body += "\n" + _("Attachment") + ": " + (attachment.original_name or attachment.media_type)
        lines.append(body)
    return "\n\n".join(lines)


def _admin_accounts():
    return TelegramAccount.objects.select_related("user", "user__role").filter(
        is_active=True,
        user__is_active=True,
        user__application_status="active",
        user__role__role="admin",
    )


def send_support_digest(bot: telebot.TeleBot, conversation_id: int) -> int:
    try:
        conversation = TelegramConversation.objects.prefetch_related("messages__attachments").get(pk=conversation_id)
    except TelegramConversation.DoesNotExist:
        return 0
    text = _("Support message from a student") + "\n\n" + _digest_text(conversation)
    keyboard = telebot.types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        telebot.types.InlineKeyboardButton(
            _("Claim conversation"), callback_data=_support_callback("claim", conversation.pk)
        ),
        telebot.types.InlineKeyboardButton(
            _("Send all messages"), callback_data=_support_callback("sendall", conversation.pk)
        ),
    )
    sent = 0
    for account in _admin_accounts().iterator(chunk_size=100):
        result = bot.send_message(account.telegram_chat_id, text, reply_markup=keyboard)
        TelegramMessage.objects.create(
            conversation=conversation,
            direction=TelegramMessage.Direction.OUTBOUND,
            telegram_chat_id=account.telegram_chat_id,
            telegram_message_id=getattr(result, "message_id", None),
            content_type=TelegramMessage.ContentType.DIGEST,
            text=text,
            delivery_status=TelegramMessage.DeliveryStatus.SENT,
            sent_at=timezone.now(),
        )
        sent += 1
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
            _("This message type is not supported. Text, photos, documents, and videos are accepted."),
        )
    else:
        send_support_digest(bot, conversation.pk)
    return True


def _claim_conversation(conversation_id: int, admin: User) -> TelegramConversation | None:
    with transaction.atomic():
        try:
            conversation = TelegramConversation.objects.select_for_update().get(pk=conversation_id)
        except TelegramConversation.DoesNotExist:
            return None
        if conversation.status == TelegramConversation.Status.OPEN:
            conversation.status = TelegramConversation.Status.CLAIMED
            conversation.claimed_by = admin
            conversation.claimed_at = timezone.now()
            conversation.version += 1
            conversation.save(update_fields=["status", "claimed_by", "claimed_at", "version", "updated_at"])
            return conversation
        if conversation.status == TelegramConversation.Status.CLAIMED and conversation.claimed_by_id == admin.pk:
            return conversation
        return None


def reply_to_conversation(
    bot: telebot.TeleBot,
    *,
    admin: User,
    conversation_id: int,
    text: str,
    reply_to_telegram_message_id: int | None = None,
) -> TelegramMessage:
    """Claim and deliver one anonymous admin reply for Telegram or the web chat."""
    try:
        conversation = TelegramConversation.objects.get(pk=conversation_id)
    except TelegramConversation.DoesNotExist as exc:
        raise SupportReplyError(_("This conversation is no longer available.")) from exc

    reply_target = None
    if reply_to_telegram_message_id is not None:
        reply_target = TelegramMessage.objects.filter(
            conversation_id=conversation.pk,
            telegram_message_id=reply_to_telegram_message_id,
        ).first()
        if reply_target is None:
            raise SupportReplyError(_("The selected message is no longer available."))

    claimed = _claim_conversation(conversation.pk, admin)
    if claimed is None:
        raise SupportReplyError(_("This conversation is already claimed or handled."))

    student_account = TelegramAccount.objects.filter(user_id=conversation.user_id, is_active=True).first()
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
        content_type=TelegramMessage.ContentType.TEXT,
        text=text,
        delivery_status=TelegramMessage.DeliveryStatus.QUEUED,
    )
    try:
        result = bot.send_message(
            student_account.telegram_chat_id,
            _("Admin") + ": " + text,
            reply_to_message_id=telegram_reply_to,
        )
    except Exception as exc:
        outbound.delivery_status = TelegramMessage.DeliveryStatus.FAILED
        outbound.delivery_error = _("Telegram message delivery failed.")
        outbound.save(update_fields=["delivery_status", "delivery_error"])
        raise SupportReplyError(_("The reply could not be delivered; the conversation remains claimed.")) from exc

    outbound.telegram_message_id = getattr(result, "message_id", None)
    outbound.delivery_status = TelegramMessage.DeliveryStatus.SENT
    outbound.sent_at = timezone.now()
    outbound.save(update_fields=["telegram_message_id", "delivery_status", "sent_at"])
    TelegramConversation.objects.filter(pk=conversation.pk).update(
        status=TelegramConversation.Status.HANDLED,
        handled_at=timezone.now(),
        version=F("version") + 1,
        updated_at=timezone.now(),
    )
    return outbound


def _admin_for_chat(chat_id: int) -> TelegramAccount | None:
    account = TelegramAccount.objects.select_related("user", "user__role").filter(
        telegram_chat_id=chat_id,
        is_active=True,
        user__role__role="admin",
    ).first()
    return account if account and is_eligible_user(account.user) else None


def handle_support_callback(bot: telebot.TeleBot, callback: dict) -> bool:
    parsed = _parse_support_callback(str(callback.get("data") or ""))
    if parsed is None:
        return False
    action, conversation_id = parsed
    message = callback.get("message") or {}
    try:
        chat_id = int(message["chat"]["id"])
        callback_id = str(callback["id"])
    except (KeyError, TypeError, ValueError):
        return True
    account = _admin_for_chat(chat_id)
    if account is None:
        bot.answer_callback_query(callback_id, text=_("This action is for administrators only."), show_alert=True)
        return True
    if action == "sendall":
        if send_support_digest(bot, conversation_id) == 0:
            bot.answer_callback_query(callback_id, text=_("This conversation is no longer available."), show_alert=True)
        else:
            bot.answer_callback_query(callback_id, text=_("Messages sent to administrators."))
        return True
    if action == "claim":
        claimed = _claim_conversation(conversation_id, account.user)
        bot.answer_callback_query(
            callback_id,
            text=_("Conversation claimed.") if claimed else _("This conversation is already claimed or handled."),
            show_alert=not bool(claimed),
        )
        return True
    return True


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
        )
    except SupportReplyError as exc:
        bot.send_message(admin_chat_id, str(exc))
    return True
