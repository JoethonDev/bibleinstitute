"""Student in-app support chat routes for the new student experience."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from . import support_chat
from .models import StudentMode
from .student_experience.mode import resolve_student_mode
from .student_experience.render import render_student_page
from .support_chat import SupportChatError

SUPPORT_CHAT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _is_student(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and getattr(getattr(user, "role", None), "role", None) == "student"
    )


def _require_chat_student(request) -> None:
    """Authorization boundary for every in-app chat action."""
    if not _is_student(request.user):
        raise PermissionDenied
    if resolve_student_mode(request) != StudentMode.NEW:
        raise PermissionDenied


def _is_fetch_request(request) -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def _chat_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


def _parse_cursor(request) -> tuple[object, int]:
    created_at = parse_datetime(request.GET.get("before_at", "") or "")
    try:
        before_id = int(request.GET.get("before_id", "") or 0)
    except (TypeError, ValueError):
        before_id = 0
    if created_at is not None and timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    return created_at, before_id


@login_required
@require_GET
def student_support_chat(request):
    """The one support conversation for the signed-in student."""
    if not _is_student(request.user):
        raise PermissionDenied
    if resolve_student_mode(request) != StudentMode.NEW:
        return redirect("student-notifications")
    conversation = support_chat.conversation_for_student(request.user)
    fragment = request.GET.get("fragment", "")
    if fragment == "older":
        created_at, before_id = _parse_cursor(request)
        if created_at is None or before_id <= 0:
            return _chat_error(str(_("Invalid message cursor.")))
        messages, has_older = support_chat.older_messages(
            conversation,
            before_created_at=created_at,
            before_id=before_id,
        )
        oldest = messages[0] if messages else None
        return render(
            request,
            "student_next/partials/support_chat_message_page.html",
            {
                "chat_messages": [
                    support_chat.message_presentation(message) for message in messages
                ],
                "chat_has_older": has_older,
                "chat_oldest": oldest,
                "chat_attachment_enabled": support_chat.chat_storage_configured(),
            },
        )
    if fragment == "poll":
        try:
            after_id = max(0, int(request.GET.get("after", "") or 0))
        except (TypeError, ValueError):
            after_id = 0
        messages = support_chat.messages_after(conversation, after_id=after_id)
        return render(
            request,
            "student_next/partials/support_chat_message_page.html",
            {
                "chat_messages": [
                    support_chat.message_presentation(message) for message in messages
                ],
                "chat_append_only": True,
                "chat_last_id": messages[-1].pk if messages else after_id,
                "chat_attachment_enabled": support_chat.chat_storage_configured(),
            },
        )
    return render_student_page(
        request,
        page="support",
        template="student_next/support.html",
        context=support_chat.chat_page_context(conversation),
    )


@login_required
@require_POST
def student_support_send(request):
    """Accept one text, image, or audio message from the student."""
    _require_chat_student(request)
    upload = request.FILES.get("attachment")
    if upload is not None and upload.size > SUPPORT_CHAT_MAX_UPLOAD_BYTES:
        return _chat_error(str(_("This file is too large to send.")))
    try:
        message = support_chat.send_student_message(
            user=request.user,
            text=request.POST.get("message", ""),
            upload=upload,
        )
    except SupportChatError as exc:
        return _chat_error(str(exc))
    if not _is_fetch_request(request):
        return redirect("student-support")
    return render(
        request,
        "student_next/partials/support_chat_message.html",
        {
            "item": support_chat.message_presentation(message),
            "chat_attachment_enabled": support_chat.chat_storage_configured(),
        },
    )


@login_required
@require_POST
def student_support_message_delete(request, message_id):
    """Remove one of the student's own chat messages from his view."""
    _require_chat_student(request)
    try:
        support_chat.delete_student_message(user=request.user, message_id=message_id)
    except SupportChatError as exc:
        return _chat_error(str(exc))
    return HttpResponse(status=204)


@login_required
@require_GET
def student_support_attachment(request, attachment_id):
    """Stream one attachment that belongs to this student's own chat."""
    _require_chat_student(request)
    attachment = support_chat.student_attachment(request.user, attachment_id)
    return support_chat.stream_support_attachment(request, attachment)
