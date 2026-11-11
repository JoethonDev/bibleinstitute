"""Student notification inbox and admin announcement management."""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .announcements import (
    AnnouncementError,
    active_announcement_levels,
    count_announcement_recipients,
    retry_announcement,
    send_announcement,
)
from .admin_operations import create_admin_operation, enqueue_admin_operation
from .forms import AnnouncementForm
from .models import AdminOperationJob, Announcement, StudentNotification
from .models import StudentMode
from .student_experience.mode import resolve_student_mode
from .student_experience.render import render_student_page
from .notification_history import notification_message_recipients
from .notification_withdrawal import (
    NotificationWithdrawalError,
    withdraw_student_notification,
)
from .utils.decorators import can_manage_academic_setup, capability_required
from .utils.helpers import (
    generate_breadcrumb,
    is_htmx,
    pagination_query_string,
    render_page,
)
from .utils.student_data import (
    PAGE_SIZE as STUDENT_NOTIFICATION_PAGE_SIZE,
    filtered_student_notifications_queryset,
    localized_notification_text,
    student_notification_cursor_page,
    student_notification_data,
    student_notifications_queryset,
    unread_notification_count,
)


PANEL_LIMIT = 10
LIVE_LIMIT = 3
ADMIN_PAGE_SIZE = 25


def _web_language(request) -> str:
    language = (translation.get_language() or "en").split("-")[0]
    return language if language in {"ar", "en"} else "en"


def _notification_target_url(notification: StudentNotification) -> str | None:
    """Return the student route a notification points at, when still valid."""
    if notification.action_url:
        return notification.action_url
    if (
        notification.navigation_type == StudentNotification.NavigationType.LESSON
        and notification.lesson_id
    ):
        return reverse(
            "lesson-details",
            args=[notification.offering_id, notification.lesson_id],
        )
    if (
        notification.navigation_type == StudentNotification.NavigationType.QUIZ
        and notification.quiz_id
    ):
        return reverse(
            "quiz-details",
            args=[notification.offering_id, notification.quiz_id],
        )
    return None


def _notification_item(
    notification: StudentNotification,
    language: str,
    *,
    include_kind: bool = False,
) -> dict:
    title, body = localized_notification_text(notification, language)
    item = {
        "id": notification.pk,
        "title": title,
        "body": body,
        "action_label": notification.action_label,
        "action_url": notification.action_url,
        "is_read": notification.read_at is not None,
        "created_at": notification.created_at,
        "target_url": _notification_target_url(notification),
        "open_url": reverse("student-notification-open", args=[notification.pk]),
        "read_url": reverse("student-notification-read", args=[notification.pk]),
    }
    if include_kind:
        item["kind"] = notification.notification_type
    return item


def _mark_notification_read(notification: StudentNotification) -> None:
    if notification.read_at is not None:
        return
    now = timezone.now()
    StudentNotification.objects.filter(
        pk=notification.pk,
        read_at__isnull=True,
    ).update(read_at=now, updated_at=now)
    notification.read_at = now


def _wants_json(request) -> bool:
    return (
        "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def _live_notification_item(notification: StudentNotification, language: str) -> dict:
    item = _notification_item(notification, language)
    return {
        "id": item["id"],
        "title": item["title"],
        "body": item["body"],
        "created_at": notification.created_at.isoformat(),
        "open_url": item["open_url"],
        "read_url": item["read_url"],
        "target_url": item["target_url"],
        "action_label": item["action_label"],
        "action_url": item["action_url"],
    }


def _notification_cursor_url(
    notifications: list[StudentNotification],
    has_more: bool,
    *,
    selected_filter: str = "",
) -> str:
    if not notifications or not has_more:
        return ""
    last = notifications[-1]
    values = {
        "before_created_at": last.created_at.isoformat(),
        "before_id": last.pk,
        "fragment": "1",
    }
    if selected_filter and selected_filter != "all":
        values["filter"] = selected_filter
    query = urlencode(values)
    return f"{reverse('student-notifications')}?{query}"


def _panel_context(request) -> dict:
    language = _web_language(request)
    include_kind = resolve_student_mode(request) == StudentMode.NEW
    notifications = list(
        student_notifications_queryset(request.user)
        .select_related("lesson", "quiz")
        .order_by("-created_at", "-pk")[:PANEL_LIMIT]
    )
    return {
        "notification_items": [
            _notification_item(notification, language, include_kind=include_kind)
            for notification in notifications
        ],
        "unread_count": unread_notification_count(request.user),
    }


def _panel_response(request, status: int = 200):
    template = (
        "student_next/partials/notification_panel.html"
        if resolve_student_mode(request) == StudentMode.NEW
        else "partials/notification_panel.html"
    )
    return render(
        request,
        template,
        _panel_context(request),
        status=status,
    )


_STUDENT_INBOX_FILTERS = {"all", "unread", "lessons", "quizzes", "announcements"}


def _student_inbox_filter(request) -> str:
    selected = (request.GET.get("filter") or "all").strip()
    return selected if selected in _STUDENT_INBOX_FILTERS else "all"


def _student_inbox_counts(request) -> dict[str, int]:
    queryset = student_notifications_queryset(request.user)
    counts = queryset.aggregate(
        all=Count("pk"),
        unread=Count("pk", filter=Q(read_at__isnull=True)),
        lessons=Count(
            "pk",
            filter=Q(
                notification_type=StudentNotification.NotificationType.LESSON_PUBLISHED
            ),
        ),
        quizzes=Count(
            "pk",
            filter=Q(
                notification_type=StudentNotification.NotificationType.QUIZ_OPENING
            ),
        ),
        announcements=Count(
            "pk",
            filter=Q(
                notification_type=StudentNotification.NotificationType.ANNOUNCEMENT
            ),
        ),
    )
    return {key: counts[key] or 0 for key in _STUDENT_INBOX_FILTERS}


def _student_inbox_filter_values(selected: str, counts: dict[str, int]) -> list[dict]:
    labels = {
        "all": _("All"),
        "unread": _("Unread"),
        "lessons": _("Lessons"),
        "quizzes": _("Quizzes"),
        "announcements": _("Announcements"),
    }
    return [
        {
            "key": key,
            "label": labels[key],
            "count": counts[key],
            "url": f"{reverse('student-notifications')}?filter={key}",
            "active": key == selected,
        }
        for key in ("all", "unread", "lessons", "quizzes", "announcements")
    ]


@login_required
@require_GET
def student_notifications(request):
    """Bounded, paginated notification history for the signed-in account."""
    language = _web_language(request)
    student_next = resolve_student_mode(request) == StudentMode.NEW
    selected_filter = _student_inbox_filter(request) if student_next else "all"
    filter_kind = selected_filter if selected_filter in {"lessons", "quizzes", "announcements"} else None
    only_unread = selected_filter == "unread"
    if request.GET.get("fragment") == "1":
        before_created_at = parse_datetime(request.GET.get("before_created_at", ""))
        try:
            before_id = int(request.GET.get("before_id", ""))
        except (TypeError, ValueError):
            before_id = 0
        if (
            before_created_at is not None
            and timezone.is_aware(before_created_at)
            and before_id > 0
        ):
            notifications, has_more = student_notification_cursor_page(
                request.user,
                before_created_at,
                before_id,
                kind=filter_kind if student_next else None,
                unread=only_unread if student_next else False,
            )
        else:
            first_rows = list(
                filtered_student_notifications_queryset(
                    request.user,
                    kind=filter_kind if student_next else None,
                    unread=only_unread if student_next else False,
                ).order_by("-created_at", "-pk")[: STUDENT_NOTIFICATION_PAGE_SIZE + 1]
            )
            notifications = first_rows[:STUDENT_NOTIFICATION_PAGE_SIZE]
            has_more = len(first_rows) > STUDENT_NOTIFICATION_PAGE_SIZE
        if student_next:
            return render(
                request,
                "student_next/partials/notification_history_page.html",
                {
                    "notification_items": [
                        _notification_item(
                            notification,
                            language,
                            include_kind=student_next,
                        )
                        for notification in notifications
                    ],
                    "next_url": _notification_cursor_url(
                        notifications,
                        has_more,
                        selected_filter=selected_filter,
                    ),
                },
            )
        return render(
            request,
            "partials/notification_history_page.html",
            {
                "notification_items": [
                    _notification_item(notification, language)
                    for notification in notifications
                ],
                "next_url": _notification_cursor_url(notifications, has_more),
            },
        )
    try:
        page_number = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page_number = 1
    filter_counts = _student_inbox_counts(request) if student_next else None
    page_obj, unread_count = student_notification_data(
        request.user,
        page_number,
        kind=filter_kind if student_next else None,
        unread=only_unread if student_next else False,
        total_count=(
            filter_counts.get(selected_filter)
            if student_next and filter_counts
            else None
        ),
        unread_count=(
            filter_counts.get("unread")
            if student_next and filter_counts
            else None
        ),
    )
    notifications = list(page_obj.object_list)
    next_url = _notification_cursor_url(
        notifications,
        page_obj.has_next(),
        selected_filter=selected_filter if student_next else "",
    )
    context = {
        "page_obj": page_obj,
        "notification_items": [
            _notification_item(
                notification,
                language,
                include_kind=student_next,
            )
            for notification in notifications
        ],
        "unread_count": unread_count,
        "pagination_query": pagination_query_string(request),
        "read_all_url": reverse("student-notification-read-all"),
        "next_url": next_url,
        "breadcrumb_items": generate_breadcrumb([(_("Notifications"), None)]),
    }
    if student_next:
        context.update({
            "selected_filter": selected_filter,
            "filter_tabs": _student_inbox_filter_values(
                selected_filter,
                filter_counts,
            ),
        })
        return render_student_page(
            request,
            page="notifications",
            template="student_next/notifications.html",
            context=context,
        )
    return render(request, "notifications.html", context)


@login_required
@require_GET
def student_notification_panel(request):
    """Recent notifications for the header bell panel (HTMX fragment)."""
    return _panel_response(request)


@login_required
@require_GET
def student_notification_live(request):
    """Bounded JSON contract for the badge and pop-up poller."""
    language = _web_language(request)
    try:
        after = max(0, int(request.GET.get("after", 0)))
    except (TypeError, ValueError):
        after = 0
    queryset = student_notifications_queryset(request.user)
    stats = queryset.aggregate(
        unread=Count("pk", filter=Q(read_at__isnull=True)),
        latest=Max("pk"),
    )
    fresh = list(
        queryset.filter(read_at__isnull=True, pk__gt=after)
        .select_related("lesson", "quiz")
        .order_by("-created_at", "-pk")[:LIVE_LIMIT]
    )
    return JsonResponse(
        {
            "unread_count": stats["unread"] or 0,
            "max_id": stats["latest"] or 0,
            "items": [
                _live_notification_item(notification, language)
                for notification in fresh
            ],
        }
    )


@login_required
@require_POST
def student_notification_open(request, notification_id):
    """Mark one notification read and continue to its target."""
    notification = (
        student_notifications_queryset(request.user)
        .select_related("lesson", "quiz")
        .filter(pk=notification_id)
        .first()
    )
    if notification is None:
        raise Http404
    _mark_notification_read(notification)
    target = _notification_target_url(notification) or reverse(
        "student-notifications"
    )
    if _wants_json(request):
        return JsonResponse(
            {
                "ok": True,
                "redirect": target,
                "unread_count": unread_notification_count(request.user),
            }
        )
    return redirect(target)


@login_required
@require_POST
def student_notification_read(request, notification_id):
    """Mark one notification read without leaving the current page."""
    notification = (
        student_notifications_queryset(request.user).filter(pk=notification_id).first()
    )
    if notification is None:
        raise Http404
    _mark_notification_read(notification)
    if _wants_json(request):
        return JsonResponse(
            {"ok": True, "unread_count": unread_notification_count(request.user)}
        )
    if is_htmx(request):
        return _panel_response(request)
    return redirect("student-notifications")


@login_required
@require_POST
def student_notification_read_all(request):
    """Mark every visible notification read."""
    updated = student_notifications_queryset(request.user).filter(
        read_at__isnull=True
    ).update(read_at=timezone.now(), updated_at=timezone.now())
    if _wants_json(request):
        return JsonResponse({"ok": True, "updated": updated, "unread_count": 0})
    if is_htmx(request):
        return _panel_response(request)
    return redirect("student-notifications")


def _announcement_level_choices() -> list[tuple[str, str]]:
    return [
        (str(level.pk), str(level)) for level in active_announcement_levels()
    ]


@capability_required(can_manage_academic_setup)
@require_http_methods(["GET", "POST"])
def notifications_management(request):
    """Admin announcement composer and sent-history list."""
    form = AnnouncementForm(
        request.POST or None,
        level_choices=_announcement_level_choices(),
    )
    status = 200
    if request.method == "POST":
        if form.is_valid():
            try:
                send_announcement(
                    actor=request.user,
                    title=form.cleaned_data["title"],
                    body=form.cleaned_data["body"],
                    action_label=form.cleaned_data["action_label"],
                    action_url=form.cleaned_data["action_url"],
                    level_ids=form.cleaned_data["levels"],
                )
            except AnnouncementError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request, _("Notification queued for delivery.")
                )
                return redirect("notifications-management")
        status = 400

    announcements = (
        Announcement.objects.select_related("academic_year", "created_by")
        .prefetch_related("levels")
        .order_by("-created_at", "-pk")
    )
    page_obj = Paginator(announcements, ADMIN_PAGE_SIZE).get_page(
        request.GET.get("page", 1)
    )
    context = {
        "announcement_form": form,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "recipient_count_url": reverse("notifications-recipient-count"),
        "breadcrumb_items": generate_breadcrumb(
            [
                (_("Admin"), reverse("admin-panel")),
                (_("Notifications"), None),
            ]
        ),
    }
    return render_page(
        request,
        "notifications_management.html",
        "partials/notifications_management_content.html",
        context,
        status=status,
    )


@capability_required(can_manage_academic_setup)
@require_POST
def announcement_retry(request, announcement_id):
    """Requeue one failed announcement fan-out."""
    try:
        retry_announcement(announcement_id, request.user)
    except AnnouncementError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Notification queued for delivery."))
    return redirect("notifications-management")


@capability_required(can_manage_academic_setup)
@require_POST
def notification_withdraw(request, notification_id):
    """Withdraw one recipient's inbox event and remove its bot message if possible."""
    try:
        result = withdraw_student_notification(
            notification_id=notification_id,
            actor=request.user,
        )
    except NotificationWithdrawalError as exc:
        messages.error(request, str(exc))
        return redirect("notification-history")

    if result.already_withdrawn:
        messages.info(request, _("This notification was already withdrawn. Telegram removal was retried where possible."))
    else:
        messages.success(
            request,
            _("Notification withdrawn from the student's inbox and pending delivery."),
        )
    if result.telegram_deleted:
        messages.success(
            request,
            _("Deleted %(count)s Telegram message(s).") % {"count": result.telegram_deleted},
        )
    if result.telegram_expired:
        messages.warning(
            request,
            _("%(count)s Telegram message(s) are older than 48 hours and remain in Telegram.")
            % {"count": result.telegram_expired},
        )
    if result.telegram_failed:
        messages.error(
            request,
            _("Telegram could not delete %(count)s message(s). Check the delivery details.")
            % {"count": result.telegram_failed},
        )
    if result.push_pending_recall:
        messages.info(
            request,
            _("The mobile app will remove this push notification when it next syncs (%(count)s device(s)).")
            % {"count": result.push_pending_recall},
        )
    return redirect("background-job-detail", family="notification", job_id=notification_id)


@capability_required(can_manage_academic_setup)
@require_POST
def notification_group_withdraw(request, group_id: int):
    """Queue one message withdrawn for every pending recipient.

    A broadcast is thousands of recipient rows and each withdrawal may call the
    Telegram API, so this is the durable `NOTIFICATION_WITHDRAWAL` job over the
    canonical per-recipient operation. The page then follows the job status.
    """
    notification, recipients = notification_message_recipients(group_id)
    if notification is None:
        raise Http404
    pending = recipients.filter(withdrawn_at__isnull=True).count()
    if not pending:
        messages.info(request, _("Every recipient of this notification is already withdrawn."))
        return redirect("notification-history")
    try:
        job = create_admin_operation(
            actor=request.user,
            kind=AdminOperationJob.Kind.NOTIFICATION_WITHDRAWAL,
            input={"message_id": notification.pk},
            conflict_key=f"notification_withdrawal:{notification.pk}",
            language=_web_language(request),
            total=pending,
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
        return redirect("notification-history")
    enqueue_admin_operation(str(job.pk))
    messages.success(
        request,
        _("Withdrawing this notification for %(count)s recipient(s). Track it below.")
        % {"count": pending},
    )
    return redirect("background-job-detail", family="management", job_id=job.pk)


@capability_required(can_manage_academic_setup)
@require_POST
def notifications_recipient_count(request):
    """Live recipient count for the composer target selection.

    POST keeps the CSRF token in the request body instead of the URL.
    """
    level_ids = []
    for value in request.POST.getlist("levels"):
        text = str(value).strip()
        if not text.isdigit() or len(text) > 18:
            return HttpResponse("0")
        level_ids.append(int(text))
    try:
        count = count_announcement_recipients(level_ids)
    except AnnouncementError:
        count = 0
    return HttpResponse(str(count))
