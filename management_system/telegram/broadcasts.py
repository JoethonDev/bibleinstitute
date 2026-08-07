"""Active-year Telegram broadcast snapshots and delivery."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import time
import uuid
from datetime import timedelta

import telebot
from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import (
    AcademicYear,
    AcademicYearLevel,
    TelegramAccount,
    TelegramBotConfig,
    TelegramBroadcast,
    TelegramBroadcastAttachment,
    TelegramBroadcastRecipient,
    User,
)
from ..utils.storage_operations import get_r2_client
from .configuration import stored_token
from .recipients import eligible_student_queryset


BROADCAST_BATCH_SIZE = 100
MAX_BROADCAST_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_BROADCAST_ATTEMPTS = 3
BROADCAST_RETRY_DELAYS = (60, 300, 900)
BROADCAST_SEND_DELAY_SECONDS = 0.05
ALL_LEVEL_VALUE = "all"

logger = logging.getLogger(__name__)


class BroadcastError(Exception):
    """A safe operator-facing broadcast error."""


class BroadcastDeliveryError(Exception):
    """An intentionally redacted retryable Telegram/R2 delivery error."""


def _require_admin(actor: User) -> None:
    if not getattr(getattr(actor, "role", None), "role", None) == "admin":
        raise BroadcastError(_("Only administrators can manage Telegram broadcasts."))


def active_broadcast_year():
    """Return ordered level links for the only active academic year."""
    return (
        AcademicYearLevel.objects.filter(academic_year__is_active=True)
        .select_related("academic_year", "level")
        .order_by("level__ordering")
    )


def _validate_target(broadcast: TelegramBroadcast) -> None:
    if not broadcast.academic_year.is_active:
        raise BroadcastError(_("The broadcast target must use the active academic year."))
    if broadcast.level_id and not AcademicYearLevel.objects.filter(
        academic_year_id=broadcast.academic_year_id,
        level_id=broadcast.level_id,
    ).exists():
        raise BroadcastError(_("The selected level is not open in the active academic year."))


def recipient_queryset(broadcast: TelegramBroadcast) -> QuerySet[User]:
    """Resolve linked, active, approved students without a per-user query."""
    return eligible_student_queryset(
        academic_year_id=broadcast.academic_year_id,
        level_id=broadcast.level_id,
    )


def _attachment_filename(uploaded_file) -> str:
    filename = str(getattr(uploaded_file, "name", "") or "").replace("\\", "/")
    filename = os.path.basename(filename).replace("\x00", "").strip()
    return (filename or "attachment")[:255]


def _attachment_content_type(filename: str, uploaded_file) -> str:
    return (getattr(uploaded_file, "content_type", "") or mimetypes.guess_type(filename)[0] or "application/octet-stream")[:255]


def create_broadcast_draft(*, actor: User, academic_year_id: int, level_id: int | None, message: str, files) -> TelegramBroadcast:
    """Persist a draft and its private R2 files before the confirmation page."""
    _require_admin(actor)
    message = str(message or "").strip()
    if not message:
        raise BroadcastError(_("A broadcast message is required."))
    if len(message) > 4000:
        raise BroadcastError(_("The broadcast message must be 4,000 characters or fewer."))
    uploaded_keys: list[str] = []
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if files and not bucket:
        raise BroadcastError(_("Telegram storage is not configured."))

    try:
        with transaction.atomic():
            year = AcademicYear.objects.filter(pk=academic_year_id, is_active=True).first()
            if year is None:
                raise BroadcastError(_("There is no active academic year."))
            year_links = AcademicYearLevel.objects.select_related("level").filter(
                academic_year=year,
            )
            if level_id is not None and not year_links.filter(level_id=level_id).exists():
                raise BroadcastError(_("The selected level is not open in the active academic year."))
            broadcast = TelegramBroadcast.objects.create(
                academic_year=year,
                level_id=level_id,
                message=message,
                created_by=actor,
            )
            client = get_r2_client() if files else None
            for ordering, uploaded_file in enumerate(files or []):
                size = int(getattr(uploaded_file, "size", 0) or 0)
                if size <= 0:
                    raise BroadcastError(_("Empty files are not accepted."))
                if size > MAX_BROADCAST_ATTACHMENT_BYTES:
                    raise BroadcastError(_("Each attachment must be 50 MB or smaller."))
                filename = _attachment_filename(uploaded_file)
                content_type = _attachment_content_type(filename, uploaded_file)
                digest = hashlib.sha256()
                for chunk in uploaded_file.chunks():
                    digest.update(chunk)
                uploaded_file.seek(0)
                key = f"Telegram Broadcast/{broadcast.pk}/{uuid.uuid4().hex}-{filename}"
                try:
                    client.upload_fileobj(
                        uploaded_file,
                        bucket,
                        key,
                        ExtraArgs={"ContentType": content_type},
                    )
                except Exception as exc:
                    raise BroadcastError(_("An attachment could not be stored.")) from exc
                uploaded_keys.append(key)
                TelegramBroadcastAttachment.objects.create(
                    broadcast=broadcast,
                    r2_key=key,
                    original_name=filename,
                    mime_type=content_type,
                    size_bytes=size,
                    sha256=digest.hexdigest(),
                    is_available=True,
                    ordering=ordering,
                )
            return broadcast
    except Exception:
        if uploaded_keys and bucket:
            try:
                client = get_r2_client()
                for key in uploaded_keys:
                    client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
        raise


def _bulk_snapshot_rows(broadcast: TelegramBroadcast, queryset: QuerySet[User]) -> int:
    count = 0
    batch: list[TelegramBroadcastRecipient] = []
    for user in queryset.iterator(chunk_size=BROADCAST_BATCH_SIZE):
        account = user.telegram_account
        display_name = user.get_full_name() or user.username
        batch.append(
            TelegramBroadcastRecipient(
                broadcast=broadcast,
                user_id=user.pk,
                telegram_account_id=account.pk,
                telegram_chat_id=account.telegram_chat_id,
                user_username=user.username,
                user_display_name=display_name[:255],
                scheduled_for=timezone.now(),
            )
        )
        if len(batch) >= BROADCAST_BATCH_SIZE:
            TelegramBroadcastRecipient.objects.bulk_create(batch)
            count += len(batch)
            batch = []
    if batch:
        TelegramBroadcastRecipient.objects.bulk_create(batch)
        count += len(batch)
    return count


def confirm_broadcast(broadcast_id: int, actor: User) -> TelegramBroadcast:
    """Lock a draft, snapshot its recipients, and queue one delivery task."""
    _require_admin(actor)
    with transaction.atomic():
        TelegramBroadcast.objects.select_for_update().get(pk=broadcast_id)
        broadcast = TelegramBroadcast.objects.select_related("academic_year", "level").get(pk=broadcast_id)
        if broadcast.created_by_id != actor.pk:
            raise BroadcastError(_("Only the administrator who created this draft may confirm it."))
        if broadcast.status != TelegramBroadcast.Status.DRAFT:
            raise BroadcastError(_("This broadcast is no longer awaiting confirmation."))
        _validate_target(broadcast)
        if broadcast.attachments.filter(is_available=False).exists():
            raise BroadcastError(_("One or more broadcast attachments are unavailable."))
        count = _bulk_snapshot_rows(broadcast, recipient_queryset(broadcast))
        if not count:
            raise BroadcastError(_("There are no linked eligible students in the selected target."))
        now = timezone.now()
        broadcast.status = TelegramBroadcast.Status.QUEUED
        broadcast.confirmed_by = actor
        broadcast.confirmed_at = now
        broadcast.queued_at = now
        broadcast.recipient_count = count
        broadcast.save(update_fields=["status", "confirmed_by", "confirmed_at", "queued_at", "recipient_count", "updated_at"])
        transaction.on_commit(
            lambda: current_app.send_task(
                "management_system.telegram_tasks.process_due_telegram_broadcasts",
                args=[broadcast.pk],
            )
        )
        return broadcast


def _claim_recipient_ids(broadcast_id: int, limit: int) -> list[int]:
    now = timezone.now()
    stale_before = now - timedelta(minutes=15)
    TelegramBroadcastRecipient.objects.filter(
        broadcast_id=broadcast_id,
        status=TelegramBroadcastRecipient.Status.SENDING,
        updated_at__lt=stale_before,
    ).update(
        status=TelegramBroadcastRecipient.Status.QUEUED,
        scheduled_for=now,
        last_error=_("A previous delivery worker stopped before completion."),
        updated_at=now,
    )
    with transaction.atomic():
        ids = list(
            TelegramBroadcastRecipient.objects.select_for_update(skip_locked=True)
            .filter(
                broadcast_id=broadcast_id,
                status=TelegramBroadcastRecipient.Status.QUEUED,
                scheduled_for__lte=now,
            )
            .order_by("scheduled_for", "pk")
            .values_list("pk", flat=True)[:max(1, min(limit, BROADCAST_BATCH_SIZE))]
        )
        if ids:
            TelegramBroadcastRecipient.objects.filter(pk__in=ids).update(
                status=TelegramBroadcastRecipient.Status.SENDING,
                attempt_count=F("attempt_count") + 1,
                updated_at=now,
            )
    return ids


def _mark_failed(recipient: TelegramBroadcastRecipient, message: str) -> None:
    attempt = int(recipient.attempt_count or 1)
    if attempt >= MAX_BROADCAST_ATTEMPTS:
        TelegramBroadcastRecipient.objects.filter(
            pk=recipient.pk, status=TelegramBroadcastRecipient.Status.SENDING
        ).update(
            status=TelegramBroadcastRecipient.Status.FAILED,
            last_error=message[:500],
            updated_at=timezone.now(),
        )
        return
    delay = BROADCAST_RETRY_DELAYS[min(attempt - 1, len(BROADCAST_RETRY_DELAYS) - 1)]
    TelegramBroadcastRecipient.objects.filter(
        pk=recipient.pk, status=TelegramBroadcastRecipient.Status.SENDING
    ).update(
        status=TelegramBroadcastRecipient.Status.QUEUED,
        scheduled_for=timezone.now() + timedelta(seconds=delay),
        last_error=message[:500],
        updated_at=timezone.now(),
    )


def _send_attachment(bot, chat_id: int, attachment: TelegramBroadcastAttachment, client, bucket: str):
    try:
        response = client.get_object(Bucket=bucket, Key=attachment.r2_key)
        body = response["Body"]
    except Exception as exc:
        raise BroadcastDeliveryError from exc
    try:
        input_file = telebot.types.InputFile(body, file_name=attachment.original_name)
        mime = attachment.mime_type.lower()
        if mime.startswith("audio/"):
            return bot.send_audio(chat_id, input_file)
        if mime.startswith("video/"):
            return bot.send_video(chat_id, input_file)
        return bot.send_document(chat_id, input_file)
    except Exception as exc:
        raise BroadcastDeliveryError from exc
    finally:
        try:
            body.close()
        except Exception:
            pass


def _send_recipient(bot, recipient: TelegramBroadcastRecipient, attachments, client, bucket: str):
    try:
        response = bot.send_message(recipient.telegram_chat_id, recipient.broadcast.message)
        message_id = getattr(response, "message_id", None)
        time.sleep(BROADCAST_SEND_DELAY_SECONDS)
        for attachment in attachments:
            response = _send_attachment(bot, recipient.telegram_chat_id, attachment, client, bucket)
            message_id = getattr(response, "message_id", message_id)
            time.sleep(BROADCAST_SEND_DELAY_SECONDS)
        return message_id
    except BroadcastDeliveryError:
        raise
    except Exception as exc:
        raise BroadcastDeliveryError from exc


def _refresh_broadcast(broadcast_id: int) -> dict[str, int | bool]:
    rows = TelegramBroadcastRecipient.objects.filter(broadcast_id=broadcast_id)
    counts = {status: 0 for status in TelegramBroadcastRecipient.Status.values}
    for row in rows.values("status").annotate(total=Count("pk")):
        counts[row["status"]] = row["total"]
    pending = counts[TelegramBroadcastRecipient.Status.QUEUED] + counts[TelegramBroadcastRecipient.Status.SENDING]
    now = timezone.now()
    updates = {
        "sent_count": counts[TelegramBroadcastRecipient.Status.SENT],
        "failed_count": counts[TelegramBroadcastRecipient.Status.FAILED],
        "status": TelegramBroadcast.Status.SENDING if pending else (
            TelegramBroadcast.Status.FAILED if counts[TelegramBroadcastRecipient.Status.FAILED] else TelegramBroadcast.Status.COMPLETED
        ),
    }
    if not pending:
        updates["completed_at"] = now
    TelegramBroadcast.objects.filter(pk=broadcast_id).update(**updates, updated_at=now)
    return {**counts, "pending": pending}


def process_due_broadcast(broadcast_id: int, limit: int = BROADCAST_BATCH_SIZE) -> dict[str, int]:
    """Deliver one bounded recipient batch and schedule remaining work."""
    ids = _claim_recipient_ids(broadcast_id, limit)
    if not ids:
        state = _refresh_broadcast(broadcast_id)
        return {"sent": int(state[TelegramBroadcastRecipient.Status.SENT]), "failed": int(state[TelegramBroadcastRecipient.Status.FAILED]), "queued": int(state["pending"])}
    TelegramBroadcast.objects.filter(pk=broadcast_id).update(status=TelegramBroadcast.Status.SENDING, updated_at=timezone.now())
    config = TelegramBotConfig.objects.filter(is_active=True).first()
    attachments = list(TelegramBroadcastAttachment.objects.filter(broadcast_id=broadcast_id))
    client = get_r2_client() if attachments else None
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    try:
        bot = telebot.TeleBot(stored_token(config), parse_mode=None, threaded=False) if config else None
    except Exception:
        bot = None
    recipients = TelegramBroadcastRecipient.objects.select_related("broadcast").filter(pk__in=ids)
    for recipient in recipients:
        if bot is None or any(not attachment.is_available for attachment in attachments) or (attachments and (client is None or not bucket)):
            _mark_failed(recipient, _("Telegram message delivery failed."))
            continue
        try:
            message_id = _send_recipient(bot, recipient, attachments, client, bucket)
            TelegramBroadcastRecipient.objects.filter(
                pk=recipient.pk, status=TelegramBroadcastRecipient.Status.SENDING
            ).update(
                status=TelegramBroadcastRecipient.Status.SENT,
                telegram_message_id=message_id,
                sent_at=timezone.now(),
                last_error="",
                updated_at=timezone.now(),
            )
            TelegramAccount.objects.filter(pk=recipient.telegram_account_id).update(last_outbound_at=timezone.now())
        except BroadcastDeliveryError:
            _mark_failed(recipient, _("Telegram message delivery failed."))
    state = _refresh_broadcast(broadcast_id)
    if int(state["pending"]):
        current_app.send_task(
            "management_system.telegram_tasks.process_due_telegram_broadcasts",
            args=[broadcast_id],
            countdown=1,
        )
    return {"sent": int(state[TelegramBroadcastRecipient.Status.SENT]), "failed": int(state[TelegramBroadcastRecipient.Status.FAILED]), "queued": int(state["pending"])}


def retry_failed_broadcast(broadcast_id: int, actor: User) -> TelegramBroadcast:
    """Requeue only terminal recipient failures after an admin action."""
    _require_admin(actor)
    with transaction.atomic():
        broadcast = TelegramBroadcast.objects.select_for_update().get(pk=broadcast_id)
        if broadcast.status not in (TelegramBroadcast.Status.FAILED, TelegramBroadcast.Status.COMPLETED):
            raise BroadcastError(_("This broadcast is still being delivered."))
        count = broadcast.recipients.filter(status=TelegramBroadcastRecipient.Status.FAILED).update(
            status=TelegramBroadcastRecipient.Status.QUEUED,
            scheduled_for=timezone.now(),
            attempt_count=0,
            telegram_message_id=None,
            sent_at=None,
            last_error="",
            updated_at=timezone.now(),
        )
        if not count:
            raise BroadcastError(_("This broadcast has no failed deliveries to retry."))
        broadcast.status = TelegramBroadcast.Status.QUEUED
        broadcast.completed_at = None
        broadcast.last_error = ""
        broadcast.save(update_fields=["status", "completed_at", "last_error", "updated_at"])
        transaction.on_commit(
            lambda: current_app.send_task(
                "management_system.telegram_tasks.process_due_telegram_broadcasts",
                args=[broadcast.pk],
            )
        )
        return broadcast


def cancel_broadcast(broadcast_id: int, actor: User) -> None:
    """Delete an unconfirmed draft and clean its private R2 objects."""
    _require_admin(actor)
    with transaction.atomic():
        broadcast = TelegramBroadcast.objects.select_for_update().get(pk=broadcast_id)
        if broadcast.status != TelegramBroadcast.Status.DRAFT:
            raise BroadcastError(_("This broadcast is no longer awaiting confirmation."))
        bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
        keys = list(broadcast.attachments.values_list("r2_key", flat=True)) if bucket else []
        broadcast.delete()
        if keys:
            transaction.on_commit(lambda: _delete_attachment_objects(bucket, keys))


def _delete_attachment_objects(bucket: str, keys: list[str]) -> None:
    try:
        client = get_r2_client()
        for key in keys:
            client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.warning("Telegram broadcast attachment cleanup failed.")
