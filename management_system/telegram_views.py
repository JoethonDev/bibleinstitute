"""Admin Dashboard views for Telegram bot configuration."""

from __future__ import annotations

import json
import mimetypes
import os
import secrets

import telebot
from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import (
    AcademicYear,
    TelegramAttachment,
    TelegramAccount,
    TelegramBotConfig,
    TelegramBroadcast,
    TelegramBroadcastRecipient,
    TelegramConversation,
    TelegramMessage,
    TelegramWebhookUpdate,
)
from .telegram.configuration import (
    TelegramConfigurationError,
    activate_bot,
    deactivate_bot,
    get_or_create_config,
    rotate_bot,
    save_bot_token,
    stored_token,
    webhook_secret,
)
from .telegram.linking import unlink_own_telegram_account
from .telegram.support import SupportReplyError, reply_to_conversation, start_conversation
from .telegram_tasks import process_telegram_update
from .telegram.policy import webhook_url
from .telegram.broadcasts import (
    ALL_LEVEL_VALUE,
    BroadcastError,
    MAX_BROADCAST_ATTACHMENT_BYTES,
    active_broadcast_year,
    cancel_broadcast,
    confirm_broadcast,
    create_broadcast_draft,
    recipient_queryset,
    retry_failed_broadcast,
)
from .telegram_forms import TelegramBotConfigForm, TelegramBroadcastForm, TelegramSupportReplyForm
from .utils.decorators import can_manage_academic_setup, capability_required
from .utils.storage_operations import get_r2_client


def _configuration_context(config, form, config_error=""):
    try:
        target_webhook_url = config.webhook_url or webhook_url(settings.DJANGO_SITE_DOMAIN)
    except ValueError:
        target_webhook_url = ""
    return {
        "config": config,
        "config_form": form,
        "webhook_url": target_webhook_url,
        "status_label": _("Active") if config.is_active else _("Inactive"),
        "config_error": config_error or config.last_error,
    }


@capability_required(can_manage_academic_setup)
def telegram_config(request):
    config = get_or_create_config()
    form = TelegramBotConfigForm(request.POST or None)

    if request.method == "POST":
        action = request.POST.get("action", "")
        requires_token_validation = action in {"save", "activate", "rotate"}
        if requires_token_validation and not form.is_valid():
            return render(request, "telegram_config.html", _configuration_context(config, form))

        try:
            token = form.cleaned_data.get("token", "") if requires_token_validation else ""
            if action == "save":
                if not token:
                    form.add_error("token", _("Enter a Telegram bot token to save."))
                    return render(request, "telegram_config.html", _configuration_context(config, form))
                save_bot_token(config.pk, token)
                messages.success(request, _("Telegram bot token saved securely. The bot remains inactive."))
            elif action == "activate":
                activate_bot(config.pk, request.user, token or None)
                messages.success(request, _("Telegram bot activated and webhook registered."))
            elif action == "rotate":
                rotate_bot(config.pk, request.user, token)
                messages.success(request, _("Telegram bot token rotated successfully."))
            elif action == "deactivate":
                deactivate_bot(config.pk, request.user)
                messages.success(request, _("Telegram bot deactivated."))
            else:
                messages.error(request, _("Invalid Telegram configuration action."))
        except TelegramConfigurationError as exc:
            messages.error(request, str(exc))
        except Exception:
            messages.error(request, _("Telegram configuration could not be updated."))
        return redirect("telegram-config")

    return render(request, "telegram_config.html", _configuration_context(config, form))


def _private_chat_id(payload: dict) -> int | None:
    candidates = []
    message = payload.get("message")
    if isinstance(message, dict):
        candidates.append(message)
    callback = payload.get("callback_query")
    if isinstance(callback, dict) and isinstance(callback.get("message"), dict):
        candidates.append(callback["message"])
    for candidate in candidates:
        chat = candidate.get("chat")
        if isinstance(chat, dict) and chat.get("type") == "private":
            try:
                return int(chat["id"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


@csrf_exempt
@require_POST
def telegram_webhook(request):
    """Validate Telegram before parsing and enqueue one private update quickly."""
    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if not config or not config.bot_id:
        return JsonResponse({"ok": False}, status=404)
    try:
        expected_secret = webhook_secret(config)
    except TelegramConfigurationError:
        return JsonResponse({"ok": False}, status=503)
    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not supplied_secret or not secrets.compare_digest(supplied_secret, expected_secret):
        return JsonResponse({"ok": False}, status=403)
    if len(request.body) > 1_000_000:
        return JsonResponse({"ok": False}, status=413)
    try:
        payload = json.loads(request.body)
    except (TypeError, json.JSONDecodeError):
        return JsonResponse({"ok": False}, status=400)
    if not isinstance(payload, dict) or isinstance(payload.get("update_id"), bool):
        return JsonResponse({"ok": False}, status=400)
    try:
        update_number = int(payload["update_id"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)
    if _private_chat_id(payload) is None:
        return JsonResponse({"ok": True, "ignored": True})

    try:
        with transaction.atomic():
            update, created = TelegramWebhookUpdate.objects.get_or_create(
                bot_id=config.bot_id,
                update_id=update_number,
                defaults={"payload": payload},
            )
    except IntegrityError:
        update = TelegramWebhookUpdate.objects.get(bot_id=config.bot_id, update_id=update_number)
        created = False
    if created:
        transaction.on_commit(lambda: process_telegram_update.delay(update.pk))
    return JsonResponse({"ok": True, "duplicate": not created})


@require_POST
@login_required
def telegram_unlink(request):
    if getattr(getattr(request.user, "role", None), "role", None) != "admin":
        return JsonResponse({"error": _("Only administrators may unlink a Telegram account.")}, status=403)
    unlink_own_telegram_account(request.user)
    messages.success(request, _("Your Telegram account was unlinked."))
    return redirect("view-profile")


TELEGRAM_CONVERSATION_STATUSES = frozenset(
    {
        TelegramConversation.Status.OPEN,
        TelegramConversation.Status.CLAIMED,
        TelegramConversation.Status.HANDLED,
        TelegramConversation.Status.BLOCKED,
    }
)


def _conversation_search_queryset(search: str):
    queryset = TelegramConversation.objects.all()
    if search:
        queryset = queryset.filter(
            Q(user__username__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(messages__text__icontains=search)
        ).distinct()
    return queryset


def _conversation_status_counts(queryset):
    counts = queryset.aggregate(
        all=Count("pk"),
        open=Count("pk", filter=Q(status=TelegramConversation.Status.OPEN)),
        claimed=Count("pk", filter=Q(status=TelegramConversation.Status.CLAIMED)),
        handled=Count("pk", filter=Q(status=TelegramConversation.Status.HANDLED)),
        blocked=Count("pk", filter=Q(status=TelegramConversation.Status.BLOCKED)),
    )
    return counts


def _conversation_list_context(request):
    search = request.GET.get("search", "").strip()[:120]
    status = request.GET.get("status", "").strip()
    if status not in TELEGRAM_CONVERSATION_STATUSES:
        status = ""
    searched = _conversation_search_queryset(search)
    status_counts = _conversation_status_counts(searched)
    conversations = searched.select_related("user", "claimed_by")
    if status:
        conversations = conversations.filter(status=status)
    last_message = TelegramMessage.objects.filter(
        conversation_id=OuterRef("pk"),
    ).exclude(
        content_type=TelegramMessage.ContentType.DIGEST,
    ).order_by("-created_at", "-pk")
    conversations = conversations.annotate(
        latest_message_at=Subquery(last_message.values("created_at")[:1]),
        last_message_text=Subquery(last_message.values("text")[:1]),
        last_message_content_type=Subquery(last_message.values("content_type")[:1]),
        last_message_direction=Subquery(last_message.values("direction")[:1]),
        message_count=Count(
            "messages",
            filter=~Q(messages__content_type=TelegramMessage.ContentType.DIGEST),
            distinct=True,
        ),
    ).order_by("-latest_message_at", "-pk")
    page_obj = Paginator(conversations, 25).get_page(request.GET.get("page", 1))
    return {
        "page_obj": page_obj,
        "search": search,
        "status": status,
        "status_counts": status_counts,
        "conversation_list_url": reverse("telegram-conversations"),
    }


@capability_required(can_manage_academic_setup)
@require_GET
def telegram_conversations(request):
    context = _conversation_list_context(request)
    if request.GET.get("fragment") == "1":
        return render(request, "partials/telegram_conversation_list.html", context)

    start_search = request.GET.get("start_search", "").strip()[:120]
    linked_users = TelegramAccount.objects.filter(
        is_active=True,
    ).select_related("user", "user__role")
    if start_search:
        linked_users = linked_users.filter(
            Q(user__username__icontains=start_search)
            | Q(user__first_name__icontains=start_search)
            | Q(user__last_name__icontains=start_search)
            | Q(user__email__icontains=start_search)
        )
    active_conversation = TelegramConversation.objects.filter(
        user_id=OuterRef("user_id"),
        status__in=(TelegramConversation.Status.OPEN, TelegramConversation.Status.CLAIMED),
    ).order_by("pk")
    linked_users = linked_users.annotate(
        active_conversation_id=Subquery(active_conversation.values("pk")[:1]),
    ).order_by("user__username", "user__pk")
    context.update({
        "start_search": start_search,
        "linked_user_page_obj": Paginator(linked_users, 10).get_page(
            request.GET.get("start_page", 1)
        ),
    })
    return render(request, "telegram_conversations.html", context)


def _valid_reply_target(conversation_id: int, value: str | None) -> int | None:
    if not value:
        return None
    try:
        message_id = int(value)
    except (TypeError, ValueError):
        return None
    if message_id <= 0:
        return None
    if not TelegramMessage.objects.filter(
        conversation_id=conversation_id,
        telegram_message_id=message_id,
    ).exclude(content_type=TelegramMessage.ContentType.DIGEST).exists():
        return None
    return message_id


def _conversation_detail_context(request, conversation, reply_form=None):
    messages_queryset = TelegramMessage.objects.filter(
        conversation=conversation,
    ).exclude(
        content_type=TelegramMessage.ContentType.DIGEST,
    ).prefetch_related("attachments").order_by("created_at", "pk")
    messages_page_obj = Paginator(messages_queryset, 30).get_page(request.GET.get("page", 1))
    reply_to_message_id = _valid_reply_target(
        conversation.pk,
        request.GET.get("reply_to"),
    )
    if reply_form is None:
        reply_form = TelegramSupportReplyForm(
            initial={
                "reply_to_telegram_message_id": reply_to_message_id,
            }
        )
    return {
        "conversation": conversation,
        "messages_page_obj": messages_page_obj,
        "reply_form": reply_form,
        "reply_url": reverse("telegram-conversation-reply", kwargs={"conversation_id": conversation.pk}),
        "detail_url": reverse("telegram-conversation-detail", kwargs={"conversation_id": conversation.pk}),
        "panel_url": reverse("telegram-conversation-detail", kwargs={"conversation_id": conversation.pk}),
        "conversation_list_url": reverse("telegram-conversations"),
        "search": request.GET.get("search", "").strip()[:120],
        "status": request.GET.get("status", "").strip(),
        "reply_to_message_id": reply_to_message_id,
    }


@capability_required(can_manage_academic_setup)
@require_GET
def telegram_conversation_detail(request, conversation_id):
    conversation = get_object_or_404(
        TelegramConversation.objects.select_related("user", "claimed_by"),
        pk=conversation_id,
    )
    context = _conversation_detail_context(request, conversation)
    if request.GET.get("fragment") == "panel":
        return render(request, "partials/telegram_chat_panel.html", context)
    if request.GET.get("fragment") == "1":
        return render(request, "partials/telegram_message_list.html", context)
    return render(request, "telegram_conversation_detail.html", context)


@capability_required(can_manage_academic_setup)
@require_POST
def telegram_conversation_reply(request, conversation_id):
    conversation = get_object_or_404(
        TelegramConversation.objects.select_related("user", "claimed_by"),
        pk=conversation_id,
    )
    form = TelegramSupportReplyForm(request.POST)
    panel_request = request.GET.get("fragment") == "panel"
    if not form.is_valid():
        context = _conversation_detail_context(request, conversation, form)
        template = "partials/telegram_chat_panel.html" if panel_request else "telegram_conversation_detail.html"
        return render(request, template, context, status=200 if panel_request else 400)

    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if not config:
        messages.error(request, _("Telegram bot is not active."))
        if panel_request:
            return render(request, "partials/telegram_chat_panel.html", _conversation_detail_context(request, conversation))
        return redirect("telegram-conversation-detail", conversation_id=conversation.pk)
    try:
        bot = telebot.TeleBot(stored_token(config), parse_mode=None, threaded=False)
        reply_to_conversation(
            bot,
            admin=request.user,
            conversation_id=conversation.pk,
            text=form.cleaned_data["message"],
            reply_to_telegram_message_id=form.cleaned_data.get("reply_to_telegram_message_id"),
        )
    except SupportReplyError as exc:
        messages.error(request, str(exc))
    except TelegramConfigurationError:
        messages.error(request, _("Telegram bot configuration is unavailable."))
    except Exception:
        messages.error(request, _("The reply could not be delivered."))
    else:
        messages.success(request, _("Reply sent successfully."))
    if panel_request:
        refreshed = get_object_or_404(
            TelegramConversation.objects.select_related("user", "claimed_by"),
            pk=conversation.pk,
        )
        return render(request, "partials/telegram_chat_panel.html", _conversation_detail_context(request, refreshed))
    return redirect("telegram-conversation-detail", conversation_id=conversation.pk)


@capability_required(can_manage_academic_setup)
@require_POST
def telegram_start_conversation(request, user_id):
    try:
        conversation = start_conversation(admin=request.user, user_id=user_id)
    except SupportReplyError as exc:
        messages.error(request, str(exc))
        return redirect("user-profile", user_id=user_id)
    return redirect("telegram-conversation-detail", conversation_id=conversation.pk)


@capability_required(can_manage_academic_setup)
def telegram_attachment(request, attachment_id):
    attachment = get_object_or_404(
        TelegramAttachment.objects.select_related("message__conversation"),
        pk=attachment_id,
    )
    if not attachment.is_available or not attachment.r2_key:
        raise Http404
    bucket = getattr(settings, "TELEGRAM_R2_BUCKET_NAME", "")
    if not bucket:
        raise Http404
    client = get_r2_client()
    try:
        storage_response = client.get_object(Bucket=bucket, Key=attachment.r2_key)
    except Exception as exc:
        raise Http404 from exc
    filename = os.path.basename(attachment.original_name or attachment.r2_key) or "attachment"
    response = FileResponse(
        storage_response["Body"],
        content_type=attachment.mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        as_attachment=request.GET.get("download") == "1",
        filename=filename,
    )
    if storage_response.get("ContentLength") is not None:
        response["Content-Length"] = str(storage_response["ContentLength"])
    return response


def _broadcast_year_context():
    links = list(active_broadcast_year())
    active_year = AcademicYear.objects.filter(is_active=True).first()
    choices = [(ALL_LEVEL_VALUE, _("All levels"))]
    choices.extend((str(link.level_id), link.level.display_name) for link in links)
    return active_year, links, choices


def _broadcast_target_label(broadcast):
    if broadcast.level:
        return str(broadcast.level.display_name)
    return str(_("All levels"))


@capability_required(can_manage_academic_setup)
@require_GET
def telegram_broadcasts(request):
    broadcasts = TelegramBroadcast.objects.select_related(
        "academic_year", "level", "created_by"
    ).order_by("-created_at", "-pk")
    page_obj = Paginator(broadcasts, 25).get_page(request.GET.get("page", 1))
    context = {
        "page_obj": page_obj,
        "broadcast_list_url": reverse("telegram-broadcasts"),
        "compose_url": reverse("telegram-broadcast-create"),
    }
    if request.GET.get("fragment") == "1":
        return render(request, "partials/telegram_broadcast_list.html", context)
    return render(request, "telegram_broadcasts.html", context)


@capability_required(can_manage_academic_setup)
def telegram_broadcast_create(request):
    active_year, links, choices = _broadcast_year_context()
    form = TelegramBroadcastForm(
        request.POST or None,
        request.FILES or None,
        level_choices=choices,
    )
    context = {
        "broadcast_form": form,
        "active_year": active_year,
        "levels": links,
        "compose_url": reverse("telegram-broadcast-create"),
        "broadcast_list_url": reverse("telegram-broadcasts"),
        "max_attachment_bytes": MAX_BROADCAST_ATTACHMENT_BYTES,
    }
    if request.method != "POST":
        return render(request, "telegram_broadcast.html", context)
    if not form.is_valid():
        return render(request, "telegram_broadcast.html", context, status=400)
    if active_year is None:
        form.add_error(None, _("There is no active academic year."))
        return render(request, "telegram_broadcast.html", context, status=400)
    level_value = form.cleaned_data["level"]
    level_id = None if level_value == ALL_LEVEL_VALUE else int(level_value)
    try:
        broadcast = create_broadcast_draft(
            actor=request.user,
            academic_year_id=active_year.pk,
            level_id=level_id,
            message=form.cleaned_data["message"],
            files=form.cleaned_data.get("attachments") or [],
        )
    except (BroadcastError, ValueError) as exc:
        form.add_error(None, str(exc))
        return render(request, "telegram_broadcast.html", context, status=400)
    return redirect("telegram-broadcast-confirm", broadcast_id=broadcast.pk)


@capability_required(can_manage_academic_setup)
@require_GET
def telegram_broadcast_confirm_page(request, broadcast_id):
    broadcast = get_object_or_404(
        TelegramBroadcast.objects.select_related("academic_year", "level", "created_by"),
        pk=broadcast_id,
    )
    if broadcast.status != TelegramBroadcast.Status.DRAFT:
        messages.info(request, _("This broadcast has already left the confirmation stage."))
        return redirect("telegram-broadcast-detail", broadcast_id=broadcast.pk)
    try:
        active_year, _links, _choices = _broadcast_year_context()
        if active_year is None or active_year.pk != broadcast.academic_year_id:
            raise BroadcastError(_("The broadcast target must use the active academic year."))
        recipient_preview_count = recipient_queryset(broadcast).count()
    except BroadcastError as exc:
        messages.error(request, str(exc))
        return redirect("telegram-broadcasts")
    return render(
        request,
        "telegram_broadcast_confirm.html",
        {
            "broadcast": broadcast,
            "active_year": active_year,
            "target_level": broadcast.level,
            "target_label": _broadcast_target_label(broadcast),
            "attachments": broadcast.attachments.all(),
            "recipient_preview_count": recipient_preview_count,
            "confirm_url": reverse("telegram-broadcast-confirm", kwargs={"broadcast_id": broadcast.pk}),
            "cancel_url": reverse("telegram-broadcast-cancel", kwargs={"broadcast_id": broadcast.pk}),
            "broadcast_list_url": reverse("telegram-broadcasts"),
        },
    )


@capability_required(can_manage_academic_setup)
@require_POST
def telegram_broadcast_confirm(request, broadcast_id):
    try:
        broadcast = confirm_broadcast(broadcast_id, request.user)
    except (BroadcastError, TelegramBroadcast.DoesNotExist) as exc:
        messages.error(request, str(exc) or _("The broadcast could not be queued."))
        return redirect("telegram-broadcasts")
    messages.success(request, _("The broadcast was confirmed and queued for delivery."))
    return redirect("telegram-broadcast-detail", broadcast_id=broadcast.pk)


@capability_required(can_manage_academic_setup)
@require_POST
def telegram_broadcast_cancel(request, broadcast_id):
    try:
        cancel_broadcast(broadcast_id, request.user)
    except (BroadcastError, TelegramBroadcast.DoesNotExist) as exc:
        messages.error(request, str(exc) or _("The broadcast could not be cancelled."))
    else:
        messages.success(request, _("The broadcast draft was cancelled."))
    return redirect("telegram-broadcasts")


@capability_required(can_manage_academic_setup)
@require_GET
def telegram_broadcast_detail(request, broadcast_id):
    broadcast = get_object_or_404(
        TelegramBroadcast.objects.select_related("academic_year", "level", "created_by", "confirmed_by"),
        pk=broadcast_id,
    )
    recipients = broadcast.recipients.order_by("pk")
    recipient_page_obj = Paginator(recipients, 50).get_page(request.GET.get("page", 1))
    return render(
        request,
        "telegram_broadcast_detail.html",
        {
            "broadcast": broadcast,
            "target_label": _broadcast_target_label(broadcast),
            "attachments": broadcast.attachments.all(),
            "recipient_page_obj": recipient_page_obj,
            "broadcast_list_url": reverse("telegram-broadcasts"),
            "retry_url": reverse("telegram-broadcast-retry", kwargs={"broadcast_id": broadcast.pk}),
        },
    )


@capability_required(can_manage_academic_setup)
@require_POST
def telegram_broadcast_retry(request, broadcast_id):
    try:
        broadcast = retry_failed_broadcast(broadcast_id, request.user)
    except (BroadcastError, TelegramBroadcast.DoesNotExist) as exc:
        messages.error(request, str(exc) or _("Failed broadcast deliveries could not be retried."))
        return redirect("telegram-broadcast-detail", broadcast_id=broadcast_id)
    messages.success(request, _("Failed broadcast deliveries were queued again."))
    return redirect("telegram-broadcast-detail", broadcast_id=broadcast.pk)
