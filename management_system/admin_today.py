"""Bounded read adapters for the admin Today cockpit.

The cockpit answers "what needs a decision today". Every queue is one count
statement plus one bounded sample statement against the canonical destination
queryset, capability-filtered and ownership-filtered before reading. There is
no per-row query, no population hydration and no invented metric:

- applications: users whose application decision is pending (admin queue);
- media: failed processing jobs (staff see only jobs they created);
- grading: answers still awaiting manual grading (`Submission.is_graded`);
- support: conversations whose newest inbound message is unread;
- upcoming: dated meetings and holidays of the active academic year.

Metric definitions that are not settled in the owning domain (missing-scan
counts, receipt coverage, live worker health) are intentionally absent; they
remain open requirements instead of being guessed from unrelated rows.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    AcademicHoliday,
    AcademicYearLevelMeeting,
    Enrollment,
    MediaProcessingJob,
    MediaProcessingStatus,
    Submission,
    User,
)
from .telegram.support import count_unread_conversations, unread_conversations_queryset

#: One sample page per queue; the count is a separate aggregate statement.
MAX_QUEUE_ITEMS = 5
#: Upcoming rows look one fortnight ahead and are bounded to eight rows.
UPCOMING_DAYS = 14
MAX_UPCOMING_ITEMS = 8


def _queue(key, label, *, count, url, items, empty_label, icon, tone="red", action_label=None):
    return {
        "key": key,
        "label": label,
        "count": count,
        "url": url,
        "items": items,
        "empty_label": empty_label,
        "icon": icon,
        "tone": tone,
        "action_label": action_label,
    }


def _pending_applications_queue(actor):
    queryset = User.objects.filter(application_status="pending")
    count = queryset.count()
    items = []
    if count:
        # The queue only needs identity and date columns. `role` is not read
        # here, and combining select_related("role") with only() that omits it
        # makes Django raise "Field User.role cannot be both deferred and
        # traversed using select_related", so the join is not requested.
        rows = (
            queryset.order_by("-date_joined", "-pk")
            .only("id", "first_name", "last_name", "username", "date_joined", "city")
            [:MAX_QUEUE_ITEMS]
        )
        for row in rows:
            name = row.get_full_name() or row.username
            meta = row.city or row.username
            submitted = timezone.localtime(row.date_joined).strftime("%Y-%m-%d")
            items.append({
                "label": name,
                "meta": f"{meta} - {submitted}",
                "url": f"{reverse('application-review', args=[row.pk])}?status=pending",
            })
    return _queue(
        "applications",
        _("Pending applications"),
        count=count,
        url=f"{reverse('applications-dashboard')}?status=pending",
        items=items,
        empty_label=_("No pending applications."),
        icon="i-file",
        tone="amber",
        action_label=_("Review queue"),
    )


def _failed_media_queue(actor, *, can_see_all: bool):
    queryset = MediaProcessingJob.objects.filter(status=MediaProcessingStatus.FAILED)
    if not can_see_all:
        queryset = queryset.filter(created_by=actor)
    count = queryset.count()
    items = []
    if count:
        rows = (
            queryset.order_by("-created_at", "-pk")
            .only("id", "original_filename", "lesson_id", "created_at")
            [:MAX_QUEUE_ITEMS]
        )
        for row in rows:
            items.append({
                "label": row.original_filename,
                "meta": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
                "url": f"{reverse('media-processing-status')}?status=failed&q={quote(row.original_filename)}",
            })
    return _queue(
        "media",
        _("Failed media jobs"),
        count=count,
        url=f"{reverse('media-processing-status')}?status=failed",
        items=items,
        empty_label=_("No failed media jobs."),
        icon="i-upload",
        tone="red",
        action_label=_("Open pipeline"),
    )


def _ungraded_queue():
    queryset = Submission.objects.filter(is_graded=False)
    count = queryset.count()
    items = []
    if count:
        rows = queryset.select_related("user", "question__quiz").order_by("-id")[
            :MAX_QUEUE_ITEMS
        ]
        for row in rows:
            student = row.user.get_full_name() or row.user.username
            quiz = row.question.quiz if row.question_id and row.question.quiz_id else None
            items.append({
                "label": student,
                "meta": quiz.name if quiz else _("Deleted question"),
                "url": (
                    reverse("submission-dashboard", args=[quiz.pk])
                    if quiz
                    else reverse("content-qa")
                ),
            })
    return _queue(
        "grading",
        _("Ungraded answers"),
        count=count,
        url=reverse("content-qa"),
        items=items,
        empty_label=_("No answers are waiting for grading."),
        icon="i-check-square",
        tone="blue",
        action_label=_("Open Content QA"),
    )


def _unread_support_queue():
    count = count_unread_conversations()
    items = []
    if count:
        rows = (
            unread_conversations_queryset()
            .select_related("user")
            .order_by("-updated_at", "-pk")[:MAX_QUEUE_ITEMS]
        )
        for row in rows:
            student = row.user.get_full_name() or row.user.username
            items.append({
                "label": student,
                "meta": row.get_status_display(),
                "url": reverse("telegram-conversation-detail", args=[row.pk]),
            })
    return _queue(
        "support",
        _("Unread Telegram conversations"),
        count=count,
        url=reverse("telegram-conversations"),
        items=items,
        empty_label=_("No unread support conversations."),
        icon="i-message",
        tone="teal",
        action_label=_("Open inbox"),
    )


def _upcoming_rows(active_year) -> list[dict]:
    """Dated meetings and holidays of the active academic year."""
    if active_year is None:
        return []
    today = timezone.localdate()
    horizon = today + timedelta(days=UPCOMING_DAYS)
    meetings = (
        AcademicYearLevelMeeting.objects.filter(
            academic_year_level__academic_year=active_year,
            meeting_date__gte=today,
            meeting_date__lte=horizon,
        )
        .select_related(
            "course_offering__course",
            "academic_year_level__level",
        )
        .order_by("meeting_date", "pk")[:MAX_UPCOMING_ITEMS]
    )
    rows = []
    for meeting in meetings:
        rows.append({
            "date": meeting.meeting_date,
            "label": meeting.course_offering.course.name,
            "meta": meeting.academic_year_level.level.display_name,
            "kind": "meeting",
        })
    holidays = (
        AcademicHoliday.objects.filter(
            academic_year=active_year,
            date__gte=today,
            date__lte=horizon,
        )
        .order_by("date", "pk")[:MAX_UPCOMING_ITEMS]
    )
    for holiday in holidays:
        rows.append({
            "date": holiday.date,
            "label": holiday.name,
            "meta": _("Academic holiday"),
            "kind": "holiday",
        })
    rows.sort(key=lambda row: row["date"])
    return rows[:MAX_UPCOMING_ITEMS]


def build_today_context(*, actor, active_year, can_see_admin_queues, can_grade) -> dict:
    """Return the capability-safe Today queue contract for one actor.

    ``can_manage_content`` owns the page gate; the extra capability flags come
    from the canonical decorator functions in the calling view so templates
    never repeat role strings. The academic scope is passed in normalized form
    (one active year or none).
    """
    queues = []
    if can_see_admin_queues:
        queues.append(_pending_applications_queue(actor))
    queues.append(_failed_media_queue(actor, can_see_all=can_see_admin_queues))
    if can_grade:
        queues.append(_ungraded_queue())
    if can_see_admin_queues:
        queues.append(_unread_support_queue())
    active_students = (
        Enrollment.objects.filter(status="active")
        .values("student")
        .distinct()
        .count()
    )
    return {
        "queues": queues,
        "upcoming": _upcoming_rows(active_year),
        "stats": {"active_students": active_students},
        "as_of": timezone.localtime(),
        "upcoming_days": UPCOMING_DAYS,
    }
