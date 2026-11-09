"""Durable Telegram support conversations and anonymous admin replies."""

from __future__ import annotations

import hashlib
import io
import os
import logging
import mimetypes
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta

import telebot
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import (
    AcademicYear,
    Enrollment,
    TelegramAccount,
    TelegramAttachment,
    TelegramBotConfig,
    TelegramConversation,
    TelegramMessage,
    User,
)
from ..utils.storage_operations import get_r2_client, upload_to_bucket
from .attachments import (
    AUDIO_MAX_SECONDS,
    CAPTION_MAX_LENGTH,
    SupportAttachmentSpec,
    classify_attachment,
    safe_filename,
)
from .deletion import delete_bot_message, is_missing_message_error
from .text import strip_telegram_links


logger = logging.getLogger(__name__)

SUPPORT_MAX_MEDIA_BYTES = 20 * 1024 * 1024
SUPPORTED_MEDIA = {"photo", "document", "video", "voice", "audio"}
# Telegram cannot edit or delete bot messages indefinitely.
SUPPORT_DIGEST_ACTION_WINDOW = timedelta(hours=48)
SUPPORT_DIGEST_RECOVERY_WINDOW = timedelta(minutes=15)
SUPPORT_REPLY_RECOVERY_WINDOW = timedelta(minutes=10)
SUPPORT_REPLY_RECOVERY_BATCH_SIZE = 100


class SupportReplyError(Exception):
    """A safe, localized failure from the shared support reply boundary."""


@dataclass(frozen=True)
class SupportMessageDeletionResult:
    status: str
    reopened_conversation: bool


def _require_admin(user: User) -> None:
    if (
        not user.is_authenticated
        or not user.is_active
        or not user.role
        or user.role.role != "admin"
    ):
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
            conversation.handled_by = None
            conversation.admin_read_at = None
            conversation.admin_read_by = None
            conversation.save(update_fields=[
                "status", "claimed_by", "claimed_at", "handled_at", "handled_by",
                "admin_read_at", "admin_read_by", "updated_at",
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


def unread_conversation_filter() -> Q:
    """Unread tracks whether an admin read the newest student message."""
    return Q(latest_inbound_at__isnull=False) & (
        Q(admin_read_at__isnull=True) | Q(latest_inbound_at__gt=F("admin_read_at"))
    )


def processed_conversation_filter() -> Q:
    """Processed is the inverse read state and is separate from resolution."""
    return (
        Q(latest_inbound_at__isnull=True)
        | Q(admin_read_at__isnull=False, latest_inbound_at__lte=F("admin_read_at"))
    )


def unread_conversations_queryset():
    """Conversations whose newest inbound message is newer than the read marker."""
    latest_inbound = latest_inbound_message_queryset().filter(
        conversation=OuterRef("pk"),
    )
    return TelegramConversation.objects.annotate(
        latest_inbound_at=Subquery(latest_inbound.values("created_at")[:1]),
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
    conversation.admin_read_by = admin
    conversation.save(update_fields=["admin_read_at", "admin_read_by", "updated_at"])
    return conversation


def mark_all_conversations_read(*, admin: User) -> int:
    """Record the read marker for every conversation with unseen inbound messages."""
    _require_admin(admin)
    now = timezone.now()
    return TelegramConversation.objects.filter(
        pk__in=unread_conversations_queryset().values("pk"),
    ).update(admin_read_at=now, admin_read_by=admin, updated_at=now)


@transaction.atomic
def recover_stale_support_replies(limit: int = SUPPORT_REPLY_RECOVERY_BATCH_SIZE) -> int:
    """Mark interrupted provider sends unknown and reopen their stale claims."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("Recovery limit must be between 1 and 1000.")

    cutoff = timezone.now() - SUPPORT_REPLY_RECOVERY_WINDOW
    stale = TelegramMessage.objects.filter(
        direction=TelegramMessage.Direction.OUTBOUND,
        delivery_status__in=(
            TelegramMessage.DeliveryStatus.QUEUED,
            TelegramMessage.DeliveryStatus.SENDING,
        ),
        created_at__lte=cutoff,
    ).exclude(content_type=TelegramMessage.ContentType.DIGEST)
    stale_rows = list(
        stale.order_by("created_at", "pk")
        .values_list("pk", "conversation_id")[:limit]
    )
    if not stale_rows:
        return 0
    stale_ids = [message_id for message_id, _ in stale_rows]
    conversation_ids = {conversation_id for _, conversation_id in stale_rows}

    conversations = {
        conversation.pk: conversation
        for conversation in TelegramConversation.objects.select_for_update(skip_locked=True)
        .filter(pk__in=conversation_ids)
        .order_by("pk")
    }
    if not conversations:
        return 0

    stale_messages = list(
        TelegramMessage.objects.select_for_update(of=("self",), skip_locked=True)
        .filter(
            pk__in=stale_ids,
            conversation_id__in=conversations,
            direction=TelegramMessage.Direction.OUTBOUND,
            delivery_status__in=(
                TelegramMessage.DeliveryStatus.QUEUED,
                TelegramMessage.DeliveryStatus.SENDING,
            ),
            created_at__lte=cutoff,
        )
        .exclude(content_type=TelegramMessage.ContentType.DIGEST)
        .order_by("conversation_id", "pk")[:limit]
    )
    if not stale_messages:
        return 0

    owner_ids_by_conversation: dict[int, set[int]] = {}
    for message in stale_messages:
        message.delivery_status = TelegramMessage.DeliveryStatus.UNKNOWN
        message.delivery_error = ""
        if message.sender_user_id is not None:
            owner_ids_by_conversation.setdefault(message.conversation_id, set()).add(
                message.sender_user_id,
            )
    TelegramMessage.objects.bulk_update(
        stale_messages,
        ["delivery_status", "delivery_error"],
        batch_size=SUPPORT_REPLY_RECOVERY_BATCH_SIZE,
    )

    recovered_ids = [message.pk for message in stale_messages]
    pending_conversation_ids = set(
        TelegramMessage.objects.filter(
            conversation_id__in=conversations,
            direction=TelegramMessage.Direction.OUTBOUND,
            delivery_status__in=(
                TelegramMessage.DeliveryStatus.QUEUED,
                TelegramMessage.DeliveryStatus.SENDING,
            ),
        )
        .exclude(content_type=TelegramMessage.ContentType.DIGEST)
        .exclude(pk__in=recovered_ids)
        .values_list("conversation_id", flat=True)
        .distinct()
    )
    reopened = []
    recovered_at = timezone.now()
    for conversation_id, conversation in conversations.items():
        if (
            conversation.status == TelegramConversation.Status.CLAIMED
            and conversation.claimed_by_id in owner_ids_by_conversation.get(conversation_id, set())
            and conversation_id not in pending_conversation_ids
        ):
            conversation.status = TelegramConversation.Status.OPEN
            conversation.claimed_by = None
            conversation.claimed_at = None
            conversation.version += 1
            conversation.updated_at = recovered_at
            reopened.append(conversation)
    if reopened:
        TelegramConversation.objects.bulk_update(
            reopened,
            ["status", "claimed_by", "claimed_at", "version", "updated_at"],
            batch_size=SUPPORT_REPLY_RECOVERY_BATCH_SIZE,
        )
    return len(stale_messages)


@transaction.atomic
def claim_conversation(*, conversation_id: int, admin: User) -> TelegramConversation:
    """Assign an open conversation to one admin under a row lock."""
    _require_admin(admin)
    try:
        conversation = TelegramConversation.objects.select_for_update().select_related(
            "claimed_by", "handled_by",
        ).get(pk=conversation_id)
    except TelegramConversation.DoesNotExist as exc:
        raise SupportReplyError(_("This conversation is no longer available.")) from exc
    if conversation.status == TelegramConversation.Status.BLOCKED:
        raise SupportReplyError(_("This conversation is blocked."))
    if conversation.status == TelegramConversation.Status.HANDLED:
        raise SupportReplyError(_("This conversation has already been resolved."))
    if conversation.claimed_by_id and conversation.claimed_by_id != admin.pk:
        raise SupportReplyError(
            _("This conversation is assigned to %(admin)s.")
            % {"admin": conversation.claimed_by.get_full_name() or conversation.claimed_by.username}
        )
    if (
        conversation.status != TelegramConversation.Status.CLAIMED
        or conversation.claimed_by_id is None
    ):
        conversation.status = TelegramConversation.Status.CLAIMED
        conversation.claimed_by = admin
        conversation.claimed_at = timezone.now()
        conversation.version += 1
        conversation.save(update_fields=[
            "status", "claimed_by", "claimed_at", "version", "updated_at",
        ])
    return conversation


@transaction.atomic
def release_conversation(*, conversation_id: int, admin: User) -> TelegramConversation:
    """Return an admin-owned conversation to the shared open queue."""
    _require_admin(admin)
    try:
        conversation = TelegramConversation.objects.select_for_update().get(pk=conversation_id)
    except TelegramConversation.DoesNotExist as exc:
        raise SupportReplyError(_("This conversation is no longer available.")) from exc
    if conversation.status != TelegramConversation.Status.CLAIMED:
        raise SupportReplyError(_("This conversation is not currently assigned."))
    if conversation.claimed_by_id != admin.pk:
        raise SupportReplyError(_("Only the assigned administrator can release this conversation."))
    pending_reply = TelegramMessage.objects.filter(
        conversation_id=conversation.pk,
        direction=TelegramMessage.Direction.OUTBOUND,
        delivery_status__in=(
            TelegramMessage.DeliveryStatus.QUEUED,
            TelegramMessage.DeliveryStatus.SENDING,
        ),
    ).exclude(content_type=TelegramMessage.ContentType.DIGEST).exists()
    if pending_reply:
        raise SupportReplyError(_("Wait for the pending reply to finish before releasing this conversation."))
    conversation.status = TelegramConversation.Status.OPEN
    conversation.claimed_by = None
    conversation.claimed_at = None
    conversation.version += 1
    conversation.save(update_fields=[
        "status", "claimed_by", "claimed_at", "version", "updated_at",
    ])
    return conversation


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
            "duration": int(voice.get("duration") or 0),
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
            "duration": int(audio.get("duration") or 0),
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
    ).order_by("-last_message_at", "-updated_at", "-pk").first()
    if active:
        return active
    latest = TelegramConversation.objects.select_for_update().filter(
        user=user,
    ).order_by("-last_message_at", "-updated_at", "-pk").first()
    if latest:
        if latest.status == TelegramConversation.Status.HANDLED:
            latest.status = TelegramConversation.Status.OPEN
            latest.claimed_by = None
            latest.claimed_at = None
            latest.handled_at = None
            latest.handled_by = None
            latest.save(update_fields=[
                "status", "claimed_by", "claimed_at", "handled_at", "handled_by", "updated_at",
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
        conversation.handled_by = None
    conversation.admin_read_at = None
    conversation.admin_read_by = None
    conversation.save(update_fields=[
        "status", "claimed_by", "claimed_at", "handled_at", "handled_by",
        "admin_read_at", "admin_read_by", "last_message_at", "version", "updated_at",
    ])
    if payload["content_type"] in SUPPORTED_MEDIA and payload.get("file_id"):
        TelegramAttachment.objects.create(
            message=stored,
            telegram_file_id=str(payload["file_id"]),
            media_type=payload["content_type"],
            original_name=safe_filename(payload.get("file_name", ""), f"telegram-{payload['content_type']}"),
            mime_type=str(payload.get("mime_type") or "")[:255],
            size_bytes=max(0, int(payload.get("file_size") or 0)),
            duration_seconds=int(payload.get("duration") or 0) or None,
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


def _sent_replies_to_source(source_message_id: int):
    return TelegramMessage.objects.filter(
        source_message_id=source_message_id,
        direction=TelegramMessage.Direction.OUTBOUND,
        delivery_status=TelegramMessage.DeliveryStatus.SENT,
    ).exclude(
        content_type=TelegramMessage.ContentType.DIGEST,
    ).order_by("-sent_at", "-pk")


def current_level_labels(user_ids) -> dict[int, str]:
    """Resolve the active-year full-year level for the supplied students.

    Exactly one active, normal, full-year enrollment in the active academic year
    is required. Ambiguous or missing cases return the translated
    "Level not assigned" label; no older, completed, or targeted enrollment is
    used as a fallback.
    """
    ids = {int(user_id) for user_id in user_ids if user_id}
    if not ids:
        return {}
    labels = {user_id: _("Level not assigned") for user_id in ids}
    active_year_id = (
        AcademicYear.objects.filter(is_active=True).values_list("id", flat=True).first()
    )
    if active_year_id is None:
        return labels
    enrollments = Enrollment.objects.filter(
        student_id__in=ids,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        academic_year_level__academic_year_id=active_year_id,
    ).select_related("academic_year_level__level")
    per_student: dict[int, dict[int, object]] = {}
    for enrollment in enrollments:
        level = enrollment.academic_year_level.level
        per_student.setdefault(enrollment.student_id, {})[level.pk] = level
    for user_id, levels in per_student.items():
        if len(levels) == 1:
            labels[user_id] = next(iter(levels.values())).display_name
    return labels


def _active_bot_config() -> TelegramBotConfig | None:
    return TelegramBotConfig.objects.filter(is_active=True).only("id", "bot_id").first()


def _digest_answer_text(digest: TelegramMessage, answer_line: str) -> str:
    """Keep the original digest visible and append the answer line."""
    base = digest.text.strip()
    suffix = "\n\n" + answer_line
    limit = 4096
    if len(base) + len(suffix) > limit:
        base = base[: max(0, limit - len(suffix) - 3)].rstrip() + "..."
    return base + suffix


def send_support_digest(bot: telebot.TeleBot, conversation_id: int, message_id: int) -> int:
    """Announce one inbound student message to every linked admin account."""
    try:
        conversation = TelegramConversation.objects.select_related("user").get(pk=conversation_id)
    except TelegramConversation.DoesNotExist:
        return 0
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 1:
        return 0
    sent = 0
    inbound_messages = list(TelegramMessage.objects.filter(
        conversation_id=conversation.pk,
        direction=TelegramMessage.Direction.INBOUND,
        pk=message_id,
    ).prefetch_related("attachments").order_by("created_at", "pk"))
    sender_identity = conversation.user.telegram_display_identity
    level_label = current_level_labels([conversation.user_id]).get(
        conversation.user_id,
        _("Level not assigned"),
    )
    active_config = _active_bot_config()
    stale_before = timezone.now() - SUPPORT_DIGEST_RECOVERY_WINDOW
    for message in inbound_messages:
        if _sent_replies_to_source(message.pk).exists():
            continue
        text = _("Support message from %(name)s") % {"name": sender_identity}
        text += "\n" + _("Level: %(level)s") % {"level": level_label}
        text += "\n\n" + _message_text(message)
        for account in _admin_accounts().iterator(chunk_size=100):
            with transaction.atomic():
                TelegramConversation.objects.select_for_update().get(pk=conversation.pk)
                if _sent_replies_to_source(message.pk).exists():
                    continue
                digests = TelegramMessage.objects.select_for_update().filter(
                    conversation_id=conversation.pk,
                    source_message_id=message.pk,
                    telegram_chat_id=account.telegram_chat_id,
                    content_type=TelegramMessage.ContentType.DIGEST,
                ).order_by("pk")
                sent_digest = digests.filter(
                    delivery_status=TelegramMessage.DeliveryStatus.SENT,
                ).first()
                recent_digest = digests.filter(
                    delivery_status=TelegramMessage.DeliveryStatus.SENDING,
                    created_at__gte=stale_before,
                ).first()
                if sent_digest is not None or recent_digest is not None:
                    continue
                digest = digests.first()
                if digest is None:
                    digest = TelegramMessage.objects.create(
                        conversation=conversation,
                        direction=TelegramMessage.Direction.OUTBOUND,
                        telegram_chat_id=account.telegram_chat_id,
                        source_message=message,
                        content_type=TelegramMessage.ContentType.DIGEST,
                        text=text,
                        delivery_status=TelegramMessage.DeliveryStatus.SENDING,
                        bot_config=active_config,
                    )
                else:
                    digest.text = text
                    digest.delivery_status = TelegramMessage.DeliveryStatus.SENDING
                    digest.delivery_error = ""
                    digest.save(update_fields=["text", "delivery_status", "delivery_error"])
            try:
                result = bot.send_message(account.telegram_chat_id, text)
                telegram_message_id = getattr(result, "message_id", None)
                if telegram_message_id is None:
                    raise ValueError("Telegram did not return a message identifier.")
            except Exception:
                TelegramMessage.objects.filter(
                    pk=digest.pk,
                    delivery_status=TelegramMessage.DeliveryStatus.SENDING,
                ).update(
                    delivery_status=TelegramMessage.DeliveryStatus.FAILED,
                    delivery_error=_("Telegram message delivery failed."),
                )
                logger.exception(
                    "telegram_support_digest_delivery_failed digest=%s",
                    digest.pk,
                )
            else:
                sent_digest_count = TelegramMessage.objects.filter(
                    pk=digest.pk,
                    delivery_status=TelegramMessage.DeliveryStatus.SENDING,
                ).update(
                    telegram_message_id=telegram_message_id,
                    delivery_status=TelegramMessage.DeliveryStatus.SENT,
                    sent_at=timezone.now(),
                    delivery_error="",
                )
                sent += sent_digest_count
                if sent_digest_count:
                    reply = _sent_replies_to_source(message.pk).select_related(
                        "sender_user",
                    ).first()
                    if reply is not None:
                        try:
                            resolve_answered_announcements(
                                bot,
                                source_message_ids=[message.pk],
                                reply=reply,
                            )
                        except Exception:
                            logger.exception(
                                "telegram_support_resolution_error conversation=%s",
                                conversation.pk,
                            )
    return sent


def handle_student_message(bot: telebot.TeleBot, *, user: User, update_id: int, message: dict) -> bool:
    conversation, stored, created = persist_inbound_message(user=user, update_id=update_id, message=message)
    if created:
        for attachment in stored.attachments.all():
            store_attachment(bot, attachment)
    if created and stored.content_type == TelegramMessage.ContentType.UNSUPPORTED:
        bot.send_message(
            int(message["chat"]["id"]),
            _("This message type is not supported. Text, photos, documents, videos, and voice messages are accepted."),
        )
    elif stored.content_type != TelegramMessage.ContentType.UNSUPPORTED:
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


def _covered_source_ids(
    conversation_id: int, source_message: TelegramMessage | None
) -> list[int]:
    """Freeze the still-unanswered inbound IDs from conversation start to source."""
    if source_message is None:
        return []
    ordered_ids = list(
        TelegramMessage.objects.filter(
            conversation_id=conversation_id,
            direction=TelegramMessage.Direction.INBOUND,
        ).order_by("created_at", "pk").values_list("pk", flat=True)
    )
    try:
        index = ordered_ids.index(source_message.pk)
    except ValueError:
        return []
    prefix_ids = ordered_ids[: index + 1]
    answered_ids = set(
        TelegramMessage.objects.filter(
            pk__in=prefix_ids,
            answered_at__isnull=False,
        ).values_list("pk", flat=True)
    )
    return [pk for pk in prefix_ids if pk not in answered_ids]


def resolve_answered_announcements(
    bot: telebot.TeleBot,
    *,
    source_message_ids,
    reply: TelegramMessage,
) -> int:
    """Annotate every delivered digest for the covered inbound messages.

    The digest is kept and its original text stays visible; the answer line is
    appended. Edits are never gated by the deletion age limit, and a failed edit
    never blocks the website answer state.
    """
    ids = [int(message_id) for message_id in source_message_ids if message_id]
    if not ids or reply.sender_user_id is None:
        return 0
    now = timezone.now()
    active_config_id = (
        TelegramBotConfig.objects.filter(is_active=True).values_list("id", flat=True).first()
    )
    answer_line = _("Answered by @%(username)s") % {
        "username": reply.sender_user.username,
    }
    digests = TelegramMessage.objects.filter(
        source_message_id__in=ids,
        content_type=TelegramMessage.ContentType.DIGEST,
        delivery_status=TelegramMessage.DeliveryStatus.SENT,
        resolved_at__isnull=True,
    ).order_by("pk")
    resolved = 0
    for digest in digests.iterator(chunk_size=100):
        resolution = TelegramMessage.Resolution.FAILED
        if digest.telegram_message_id is None or digest.sent_at is None:
            resolution = TelegramMessage.Resolution.FAILED
        elif (
            digest.bot_config_id
            and active_config_id
            and digest.bot_config_id != active_config_id
        ):
            resolution = TelegramMessage.Resolution.UNAVAILABLE
        else:
            try:
                bot.edit_message_text(
                    _digest_answer_text(digest, answer_line),
                    chat_id=digest.telegram_chat_id,
                    message_id=digest.telegram_message_id,
                )
            except Exception as exc:
                if is_missing_message_error(exc):
                    resolution = TelegramMessage.Resolution.DELETED
                else:
                    resolution = TelegramMessage.Resolution.FAILED
            else:
                resolution = TelegramMessage.Resolution.EDITED
        if resolution == TelegramMessage.Resolution.FAILED:
            logger.warning(
                "telegram_support_resolution_failed digest=%s chat=%s",
                digest.pk,
                digest.telegram_chat_id,
            )
        resolved += TelegramMessage.objects.filter(
            pk=digest.pk,
            resolved_at__isnull=True,
        ).update(
            resolved_at=now,
            resolution=resolution,
            resolved_by_id=reply.sender_user_id,
            resolution_reply_id=reply.pk,
        )
    return resolved


def _queue_reply(
    *,
    admin: User,
    conversation_id: int,
    text: str,
    reply_to_telegram_message_id: int | None,
    reply_target_chat_id: int | None,
    origin: str,
    content_type: str = TelegramMessage.ContentType.TEXT,
    target_message_id: int | None = None,
) -> tuple[TelegramMessage, int, int | None]:
    """Queue one reply under a short row lock, then release before API I/O."""
    _require_admin(admin)
    with transaction.atomic():
        try:
            conversation = TelegramConversation.objects.select_for_update(
                of=("self",),
            ).select_related(
                "claimed_by", "handled_by",
            ).get(pk=conversation_id)
        except TelegramConversation.DoesNotExist as exc:
            raise SupportReplyError(_("This conversation is no longer available.")) from exc
        if conversation.status == TelegramConversation.Status.BLOCKED:
            raise SupportReplyError(_("This conversation is blocked."))

        reply_target = None
        if target_message_id is not None:
            reply_target = TelegramMessage.objects.filter(
                conversation_id=conversation.pk,
                pk=target_message_id,
                direction=TelegramMessage.Direction.INBOUND,
            ).first()
            if reply_target is None:
                raise SupportReplyError(_("The selected message is no longer available."))
        elif reply_to_telegram_message_id is not None:
            reply_targets = TelegramMessage.objects.filter(
                conversation_id=conversation.pk,
                telegram_message_id=reply_to_telegram_message_id,
            )
            if reply_target_chat_id is not None:
                reply_targets = reply_targets.filter(telegram_chat_id=reply_target_chat_id)
            reply_target = reply_targets.order_by("-pk").first()
            if reply_target is None:
                raise SupportReplyError(_("The selected message is no longer available."))

        source_message = _answered_source_message(conversation, reply_target)
        if target_message_id is not None:
            source_message = reply_target
        covered_ids = _covered_source_ids(conversation.pk, source_message)
        if source_message is not None and source_message.answered_at is not None:
            # A reply to an already answered message is a threaded follow-up and
            # does not rewrite the earlier per-message answer attribution.
            covered_ids = []
        pending_replies = TelegramMessage.objects.filter(
            conversation=conversation,
            direction=TelegramMessage.Direction.OUTBOUND,
            delivery_status__in=(
                TelegramMessage.DeliveryStatus.QUEUED,
                TelegramMessage.DeliveryStatus.SENDING,
            ),
        ).exclude(content_type=TelegramMessage.ContentType.DIGEST)
        covered_set = set(covered_ids)
        for pending_reply in pending_replies.only("pk", "source_message_id", "covered_message_ids"):
            pending_coverage = set(pending_reply.covered_message_ids or [])
            if pending_reply.source_message_id:
                pending_coverage.add(pending_reply.source_message_id)
            if covered_set & pending_coverage:
                raise SupportReplyError(_("A reply to this message is already being sent."))

        if conversation.claimed_by_id and conversation.claimed_by_id != admin.pk:
            assigned_admin = conversation.claimed_by
            name = assigned_admin.get_full_name() or assigned_admin.username
            raise SupportReplyError(_("This conversation is assigned to %(admin)s.") % {"admin": name})
        if (
            conversation.status == TelegramConversation.Status.HANDLED
            and conversation.handled_by_id
            and conversation.handled_by_id != admin.pk
        ):
            handled_admin = conversation.handled_by
            name = handled_admin.get_full_name() or handled_admin.username
            raise SupportReplyError(_("This conversation was resolved by %(admin)s.") % {"admin": name})

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

        if (
            conversation.status == TelegramConversation.Status.OPEN
            or (
                conversation.status == TelegramConversation.Status.CLAIMED
                and conversation.claimed_by_id is None
            )
        ):
            conversation.status = TelegramConversation.Status.CLAIMED
            conversation.claimed_by = admin
            conversation.claimed_at = timezone.now()
            conversation.version += 1
            conversation.save(update_fields=[
                "status", "claimed_by", "claimed_at", "version", "updated_at",
            ])

        outbound = TelegramMessage.objects.create(
            conversation=conversation,
            direction=TelegramMessage.Direction.OUTBOUND,
            sender_user=admin,
            telegram_chat_id=student_account.telegram_chat_id,
            reply_to_telegram_message_id=telegram_reply_to,
            source_message=source_message,
            content_type=content_type,
            origin=origin,
            text=text,
            delivery_status=TelegramMessage.DeliveryStatus.QUEUED,
            covered_message_ids=covered_ids,
            bot_config=_active_bot_config(),
        )
        return outbound, student_account.telegram_chat_id, telegram_reply_to


def _fail_reply(outbound: TelegramMessage, message: str) -> None:
    outbound.delivery_status = TelegramMessage.DeliveryStatus.FAILED
    outbound.delivery_error = message[:500]
    outbound.save(update_fields=["delivery_status", "delivery_error"])


def _mark_reply_sending(outbound: TelegramMessage) -> None:
    updated = TelegramMessage.objects.filter(
        pk=outbound.pk,
        delivery_status=TelegramMessage.DeliveryStatus.QUEUED,
    ).update(delivery_status=TelegramMessage.DeliveryStatus.SENDING)
    if not updated:
        raise SupportReplyError(_("The reply status changed. Refresh before sending another reply."))


def _finalize_delivered_reply(
    bot: telebot.TeleBot,
    outbound: TelegramMessage,
    admin: User,
    result,
) -> TelegramMessage:
    """Persist delivery and resolve only the exact latest student message."""
    sent_at = timezone.now()
    message_id = getattr(result, "message_id", None)
    with transaction.atomic():
        conversation = TelegramConversation.objects.select_for_update().get(
            pk=outbound.conversation_id,
        )
        outbound = TelegramMessage.objects.select_for_update(
            of=("self",),
        ).select_related("sender_user").get(pk=outbound.pk)
        outbound.telegram_message_id = message_id
        outbound.delivery_status = TelegramMessage.DeliveryStatus.SENT
        outbound.sent_at = sent_at
        outbound.delivery_error = "" if message_id is not None else _(
            "Telegram sent the reply without returning a message identifier."
        )
        outbound.save(update_fields=[
            "telegram_message_id", "delivery_status", "sent_at", "delivery_error",
        ])
        covered_ids = [int(pk) for pk in (outbound.covered_message_ids or []) if pk]
        if covered_ids:
            TelegramMessage.objects.filter(
                conversation_id=conversation.pk,
                pk__in=covered_ids,
                direction=TelegramMessage.Direction.INBOUND,
                answered_at__isnull=True,
            ).update(
                answered_by=admin,
                answered_at=sent_at,
                answer_reply=outbound,
            )
        latest_inbound = TelegramMessage.objects.filter(
            conversation_id=conversation.pk,
            direction=TelegramMessage.Direction.INBOUND,
        ).order_by("-created_at", "-pk").only("pk").first()
        resolves_latest = bool(
            outbound.source_message_id
            and latest_inbound
            and outbound.source_message_id == latest_inbound.pk
        )
        later_reply_exists = bool(outbound.source_message_id) and TelegramMessage.objects.filter(
            conversation_id=conversation.pk,
            source_message_id=outbound.source_message_id,
            direction=TelegramMessage.Direction.OUTBOUND,
            pk__gt=outbound.pk,
        ).exclude(content_type=TelegramMessage.ContentType.DIGEST).exclude(
            delivery_status=TelegramMessage.DeliveryStatus.FAILED,
        ).exists()
        completes_admin_started = latest_inbound is None and conversation.status in {
            TelegramConversation.Status.OPEN,
            TelegramConversation.Status.CLAIMED,
        }
        may_resolve = (
            conversation.status in {
                TelegramConversation.Status.OPEN,
                TelegramConversation.Status.CLAIMED,
            }
            and conversation.claimed_by_id in (None, admin.pk)
            and not later_reply_exists
        )
        conversation.last_message_at = sent_at
        conversation.version += 1
        if may_resolve and (resolves_latest or completes_admin_started):
            conversation.status = TelegramConversation.Status.HANDLED
            conversation.claimed_by = None
            conversation.claimed_at = None
            conversation.handled_by = admin
            conversation.handled_at = sent_at
            if resolves_latest:
                conversation.admin_read_at = sent_at
                conversation.admin_read_by = admin
        conversation.save(update_fields=[
            "status", "claimed_by", "claimed_at", "handled_by", "handled_at",
            "admin_read_at", "admin_read_by", "last_message_at", "version", "updated_at",
        ])
    try:
        resolve_answered_announcements(
            bot,
            source_message_ids=covered_ids,
            reply=outbound,
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
    reply_target_chat_id: int | None = None,
    target_message_id: int | None = None,
) -> TelegramMessage:
    """Queue and deliver one anonymous admin reply for Telegram or web chat."""
    outbound, student_chat_id, telegram_reply_to = _queue_reply(
        admin=admin,
        conversation_id=conversation_id,
        text=text,
        reply_to_telegram_message_id=reply_to_telegram_message_id,
        reply_target_chat_id=reply_target_chat_id,
        origin=origin,
        target_message_id=target_message_id,
    )
    _mark_reply_sending(outbound)
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
        "voice": TelegramMessage.ContentType.VOICE,
        "audio": TelegramMessage.ContentType.AUDIO,
    }[kind]


def _probe_audio_seconds(uploaded_file) -> float:
    """Return the duration of a validated audio upload using ffprobe."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as handle:
            temporary_path = handle.name
            uploaded_file.seek(0)
            for chunk in uploaded_file.chunks():
                handle.write(chunk)
        result = subprocess.run(
            [
                getattr(settings, "FFPROBE_BINARY", "ffprobe"),
                "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", temporary_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        duration = float((result.stdout or "").strip())
    except FileNotFoundError as exc:
        raise SupportReplyError(_("Audio validation is unavailable on this server.")) from exc
    except Exception as exc:
        raise SupportReplyError(_("The audio file could not be inspected.")) from exc
    finally:
        uploaded_file.seek(0)
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    if duration <= 0 or duration > AUDIO_MAX_SECONDS:
        raise SupportReplyError(_("Audio messages must be 5 minutes or shorter."))
    return duration


def _classify_uploaded_file(uploaded_file) -> SupportAttachmentSpec:
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
            duration = _probe_audio_seconds(uploaded_file)
            return SupportAttachmentSpec(
                spec.kind, spec.mime_type, spec.filename, spec.size, duration,
            )
        return spec
    except SupportReplyError:
        raise
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
            duration_seconds=int(spec.duration_seconds) if spec.duration_seconds else None,
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
    reply_target_chat_id: int | None = None,
    target_message_id: int | None = None,
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
        reply_target_chat_id=reply_target_chat_id,
        origin=origin,
        content_type=_attachment_content_type(spec.kind),
        target_message_id=target_message_id,
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
        _mark_reply_sending(outbound)
        if spec.kind == "photo":
            result = bot.send_photo(student_chat_id, input_file, **send_kwargs)
        elif spec.kind == "video":
            result = bot.send_video(student_chat_id, input_file, **send_kwargs)
        elif spec.kind == "voice":
            result = bot.send_voice(student_chat_id, input_file, **send_kwargs)
        elif spec.kind == "audio":
            result = bot.send_audio(student_chat_id, input_file, **send_kwargs)
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


def reply_with_provider_media_to_conversation(
    bot: telebot.TeleBot,
    *,
    admin: User,
    conversation_id: int,
    provider_file_id: str,
    media_kind: str,
    caption: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    duration_seconds: float | None = None,
    target_message_id: int | None = None,
) -> TelegramMessage:
    """Relay one admin media message that arrived through the support bot.

    The provider file ID is bound to the sending bot, so the same bot must
    remain active for the relay. The private R2 copy gives the website a stable
    preview and keeps the provider URL and token out of the browser.
    """
    if media_kind not in {"photo", "video", "document", "voice", "audio"}:
        raise SupportReplyError(_("This media type is not supported."))
    if size_bytes and size_bytes > SUPPORT_MAX_MEDIA_BYTES:
        raise SupportReplyError(_("This media file is too large to archive."))
    if duration_seconds and duration_seconds > AUDIO_MAX_SECONDS:
        raise SupportReplyError(_("Audio messages must be 5 minutes or shorter."))
    text = str(caption or "").strip()
    if len(text) > CAPTION_MAX_LENGTH:
        raise SupportReplyError(_("Attachments support a message of 1,024 characters or fewer."))
    outbound, student_chat_id, _telegram_reply_to = _queue_reply(
        admin=admin,
        conversation_id=conversation_id,
        text=text,
        reply_to_telegram_message_id=None,
        reply_target_chat_id=None,
        origin=TelegramMessage.Origin.TELEGRAM,
        content_type=_attachment_content_type(media_kind),
        target_message_id=target_message_id,
    )
    attachment = TelegramAttachment.objects.create(
        message=outbound,
        telegram_file_id=str(provider_file_id),
        media_type=media_kind,
        original_name=safe_filename(filename, "telegram-%s" % media_kind),
        mime_type=str(mime_type or "")[:255],
        size_bytes=max(0, int(size_bytes or 0)),
        duration_seconds=int(duration_seconds) if duration_seconds else None,
    )
    if not store_attachment(bot, attachment):
        _fail_reply(outbound, attachment.error or _("The media could not be stored."))
        raise SupportReplyError(_("The media could not be stored."))
    send_kwargs = {}
    if text:
        send_kwargs["caption"] = text
    _mark_reply_sending(outbound)
    try:
        if media_kind == "photo":
            result = bot.send_photo(student_chat_id, provider_file_id, **send_kwargs)
        elif media_kind == "video":
            result = bot.send_video(student_chat_id, provider_file_id, **send_kwargs)
        elif media_kind == "voice":
            result = bot.send_voice(student_chat_id, provider_file_id, **send_kwargs)
        elif media_kind == "audio":
            result = bot.send_audio(student_chat_id, provider_file_id, **send_kwargs)
        else:
            result = bot.send_document(student_chat_id, provider_file_id, **send_kwargs)
    except Exception as exc:
        _fail_reply(outbound, _("Telegram message delivery failed."))
        raise SupportReplyError(_("The reply could not be delivered; the conversation remains open.")) from exc
    return _finalize_delivered_reply(bot, outbound, admin, result)


def delete_support_message(
    bot: telebot.TeleBot,
    *,
    admin: User,
    conversation_id: int,
    message_id: int,
) -> SupportMessageDeletionResult:
    """Delete an admin's sent reply and reopen it if that reply resolved the latest student message."""
    _require_admin(admin)
    try:
        message = TelegramMessage.objects.select_related("conversation").get(
            pk=message_id,
            conversation_id=conversation_id,
            direction=TelegramMessage.Direction.OUTBOUND,
        )
    except TelegramMessage.DoesNotExist as exc:
        raise SupportReplyError(_("This sent message is no longer available.")) from exc
    if message.content_type == TelegramMessage.ContentType.DIGEST:
        owns_digest_chat = TelegramAccount.objects.filter(
            user_id=admin.pk,
            telegram_chat_id=message.telegram_chat_id,
            is_active=True,
        ).exists()
        if not owns_digest_chat:
            raise SupportReplyError(_("You can delete only a Telegram alert sent to your linked account."))
    elif message.sender_user_id != admin.pk:
        raise SupportReplyError(_("You can delete only a reply you sent."))
    if message.delivery_status != TelegramMessage.DeliveryStatus.SENT:
        raise SupportReplyError(_("Only a sent Telegram reply can be deleted."))
    if message.telegram_deletion_status == TelegramMessage.DeletionStatus.DELETED:
        return SupportMessageDeletionResult(status="deleted", reopened_conversation=False)
    if message.telegram_deletion_status == TelegramMessage.DeletionStatus.EXPIRED:
        return SupportMessageDeletionResult(status="expired", reopened_conversation=False)

    status, error = delete_bot_message(
        bot,
        chat_id=message.telegram_chat_id,
        message_id=message.telegram_message_id,
        sent_at=message.sent_at,
    )
    reopened = False
    with transaction.atomic():
        conversation = TelegramConversation.objects.select_for_update().get(pk=conversation_id)
        message = TelegramMessage.objects.select_for_update().get(pk=message.pk)
        now = timezone.now()
        TelegramMessage.objects.filter(pk=message.pk).update(
            telegram_deletion_status=status,
            telegram_deleted_at=now if status == TelegramMessage.DeletionStatus.DELETED else None,
            telegram_deleted_by_id=admin.pk if status == TelegramMessage.DeletionStatus.DELETED else None,
            telegram_deletion_error=error,
        )
        reopened_inbound_ids: list[int] = []
        if status == TelegramMessage.DeletionStatus.DELETED:
            attributed_ids = list(
                TelegramMessage.objects.filter(
                    conversation_id=conversation.pk,
                    answer_reply_id=message.pk,
                    direction=TelegramMessage.Direction.INBOUND,
                ).values_list("pk", flat=True)
            )
            for inbound_id in attributed_ids:
                other_live_answer = TelegramMessage.objects.filter(
                    conversation_id=conversation.pk,
                    direction=TelegramMessage.Direction.OUTBOUND,
                    delivery_status=TelegramMessage.DeliveryStatus.SENT,
                ).exclude(
                    pk=message.pk,
                ).exclude(
                    content_type=TelegramMessage.ContentType.DIGEST,
                ).filter(
                    Q(source_message_id=inbound_id)
                    | Q(covered_message_ids__contains=[inbound_id])
                ).exclude(
                    telegram_deletion_status=TelegramMessage.DeletionStatus.DELETED,
                ).exists()
                if other_live_answer:
                    continue
                TelegramMessage.objects.filter(pk=inbound_id).update(
                    answered_by=None,
                    answered_at=None,
                    answer_reply=None,
                )
                reopened_inbound_ids.append(inbound_id)
        latest_inbound = TelegramMessage.objects.filter(
            conversation_id=conversation.pk,
            direction=TelegramMessage.Direction.INBOUND,
        ).order_by("-created_at", "-pk").only("pk").first()
        if (
            reopened_inbound_ids
            and latest_inbound is not None
            and latest_inbound.pk in reopened_inbound_ids
            and conversation.status == TelegramConversation.Status.HANDLED
        ):
            conversation.status = TelegramConversation.Status.OPEN
            conversation.handled_at = None
            conversation.handled_by = None
            conversation.claimed_by = None
            conversation.claimed_at = None
            conversation.admin_read_at = None
            conversation.admin_read_by = None
            conversation.version += 1
            conversation.save(update_fields=[
                "status", "handled_at", "handled_by", "claimed_by", "claimed_at",
                "admin_read_at", "admin_read_by", "version", "updated_at",
            ])
            reopened = True
    return SupportMessageDeletionResult(status=status, reopened_conversation=reopened)


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


_DOCUMENT_MIME_ALLOWLIST = {
    "application/pdf",
    "audio/ogg",
    "audio/opus",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
}


def _admin_media_payload(message: dict) -> dict | None:
    """Normalize one admin media update into the shared relay contract."""
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = max(
            (item for item in photos if isinstance(item, dict)),
            key=lambda item: int(item.get("file_size") or 0),
            default=None,
        )
        if largest and largest.get("file_id"):
            return {
                "kind": "photo",
                "file_id": largest["file_id"],
                "mime_type": "image/jpeg",
                "filename": "telegram-photo.jpg",
                "size_bytes": int(largest.get("file_size") or 0),
                "duration": None,
            }
    for key, kind in (
        ("video", "video"),
        ("voice", "voice"),
        ("audio", "audio"),
        ("document", "document"),
    ):
        payload = message.get(key)
        if not isinstance(payload, dict) or not payload.get("file_id"):
            continue
        mime_type = str(payload.get("mime_type") or "").lower()
        if kind == "document" and mime_type not in _DOCUMENT_MIME_ALLOWLIST:
            raise SupportReplyError(_("This document type is not supported."))
        if kind in {"voice", "audio"} and mime_type and not mime_type.startswith("audio/"):
            raise SupportReplyError(_("This audio type is not supported."))
        size = int(payload.get("file_size") or 0)
        if size and size > SUPPORT_MAX_MEDIA_BYTES:
            raise SupportReplyError(_("This media file is too large to archive."))
        duration = payload.get("duration")
        if kind in {"voice", "audio"} and duration and float(duration) > AUDIO_MAX_SECONDS:
            raise SupportReplyError(_("Audio messages must be 5 minutes or shorter."))
        return {
            "kind": kind,
            "file_id": str(payload["file_id"]),
            "mime_type": mime_type or ("application/pdf" if kind == "document" else ""),
            "filename": str(
                payload.get("file_name")
                or ("telegram-%s" % kind)
            ),
            "size_bytes": size,
            "duration": float(duration) if duration else None,
        }
    return None


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
    try:
        media = _admin_media_payload(message)
    except SupportReplyError as exc:
        bot.send_message(admin_chat_id, str(exc))
        return True
    if media is not None:
        digest = None
        if reply_to is not None:
            digest = TelegramMessage.objects.filter(
                telegram_chat_id=admin_chat_id,
                telegram_message_id=reply_to,
                content_type=TelegramMessage.ContentType.DIGEST,
            ).first()
        if digest is None or digest.source_message_id is None:
            bot.send_message(
                admin_chat_id,
                _("Reply to a support digest to send media."),
            )
            return True
        try:
            reply_with_provider_media_to_conversation(
                bot,
                admin=admin,
                conversation_id=conversation.pk,
                provider_file_id=media["file_id"],
                media_kind=media["kind"],
                caption=text,
                filename=media["filename"],
                mime_type=media["mime_type"],
                size_bytes=media["size_bytes"],
                duration_seconds=media["duration"],
                target_message_id=digest.source_message_id,
            )
        except SupportReplyError as exc:
            bot.send_message(admin_chat_id, str(exc))
        return True
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
            reply_target_chat_id=admin_chat_id,
            origin=TelegramMessage.Origin.TELEGRAM,
        )
    except SupportReplyError as exc:
        bot.send_message(admin_chat_id, str(exc))
    return True
