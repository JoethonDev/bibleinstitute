from collections import defaultdict
from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from ..academic_access import accessible_offerings
from ..course_content import with_offering_content_counts
from ..models import (
    AcademicYear,
    AcademicYearLevel,
    AcademicYearLevelMeeting,
    Enrollment,
    Level,
    PublicationStatus,
    StudentNotification,
)
from ..utils.student_data import (
    localized_notification_text,
    student_course_progress_data,
    student_next_actions,
    student_notifications_queryset,
)
from ..utils.helpers import pagination_query_string
from .calendar import next_student_meeting
from .mode import is_management_student_view

STUDENT_OFFERING_PAGE_SIZE = 20
_LEARNING_FILTERS = {"all", "current", "completed"}
_MANAGEMENT_FILTERS = {"all", "published", "unpublished"}


def _time_greeting() -> str:
    hour = timezone.localtime().hour
    if 5 <= hour < 12:
        return _("Good morning")
    if 12 <= hour < 17:
        return _("Good afternoon")
    return _("Good evening")


def _management_learning_context(request) -> dict:
    user = request.user
    years = list(AcademicYear.objects.order_by("-is_active", "-ordering", "-pk")[:25])
    levels = list(Level.objects.order_by("ordering", "pk")[:25])
    year_ids = {str(year.pk) for year in years}
    level_ids = {str(level.pk) for level in levels}

    raw_year = (request.GET.get("year") or "").strip()
    if raw_year == "all" or raw_year in year_ids:
        selected_year = raw_year
    else:
        active_year = next((year for year in years if year.is_active), None)
        selected_year = str(active_year.pk) if active_year else "all"

    raw_level = (request.GET.get("level") or "").strip()
    selected_level = raw_level if raw_level in level_ids else "all"

    selected_filter = request.GET.get("filter", "all")
    if selected_filter not in _MANAGEMENT_FILTERS:
        selected_filter = "all"

    scoped_offerings = with_offering_content_counts(
        accessible_offerings(user, include_management=True),
        include_drafts=True,
    ).select_related(
        "course",
        "academic_year_level__academic_year",
        "academic_year_level__level",
    )
    if selected_year != "all":
        scoped_offerings = scoped_offerings.filter(
            academic_year_level__academic_year_id=int(selected_year)
        )
    if selected_level != "all":
        scoped_offerings = scoped_offerings.filter(
            academic_year_level__level_id=int(selected_level)
        )
    scoped_offerings = scoped_offerings.order_by(
        "-academic_year_level__academic_year__ordering",
        "academic_year_level__level__ordering",
        "course__name",
    )

    counts = scoped_offerings.aggregate(
        all=Count("pk"),
        published=Count("pk", filter=Q(status=PublicationStatus.PUBLISHED)),
        unpublished=Count("pk", filter=~Q(status=PublicationStatus.PUBLISHED)),
    )
    filtered = scoped_offerings
    if selected_filter == "published":
        filtered = filtered.filter(status=PublicationStatus.PUBLISHED)
    elif selected_filter == "unpublished":
        filtered = filtered.exclude(status=PublicationStatus.PUBLISHED)

    paginator = Paginator(filtered, STUDENT_OFFERING_PAGE_SIZE)
    paginator.count = counts[selected_filter] or 0
    page_obj = paginator.get_page(request.GET.get("page", 1))
    page_offerings = list(page_obj.object_list)

    grouped = defaultdict(list)
    for offering in page_offerings:
        offering.student_progress = None
        scope = offering.academic_year_level
        grouped[(scope.academic_year_id, scope.level_id)].append(offering)

    sections = []
    for _key, rows in grouped.items():
        scope = rows[0].academic_year_level
        sections.append({
            "academic_year": scope.academic_year,
            "level": scope.level,
            "level_name": scope.level.display_name,
            "access_kind": "current" if scope.academic_year.is_active else "historical",
            "is_completed": False,
            "offerings": rows,
        })

    active_scope = (
        AcademicYearLevel.objects.filter(academic_year_id=int(selected_year))
        .select_related("academic_year", "level")
        .order_by("level__ordering", "pk")
        .first()
        if selected_year != "all" else None
    )
    if active_scope is None and sections:
        first_offering = sections[0]["offerings"][0]
        active_scope = first_offering.academic_year_level

    today = timezone.localdate()
    week_meetings = []
    if active_scope is not None:
        week_filters = {
            "meeting_date__gte": today,
            "meeting_date__lt": today + timedelta(days=7),
            "academic_year_level__academic_year_id": active_scope.academic_year_id,
        }
        if selected_level != "all":
            week_filters["academic_year_level__level_id"] = int(selected_level)
        week_meetings = list(
            AcademicYearLevelMeeting.objects.filter(**week_filters)
            .select_related("course_offering__course")
            .order_by("meeting_date", "pk")[:7]
        )

    base_query = f"year={selected_year}&level={selected_level}"
    return {
        "sections": sections,
        "active_scope": active_scope,
        "next_meeting": None,
        "resume": None,
        "due_soon": [],
        "announcements": [],
        "week_meetings": week_meetings,
        "greeting": _time_greeting(),
        "overall_percent": None,
        "overall_ring_offset": 100,
        "selected_filter": selected_filter,
        "selected_year": selected_year,
        "selected_level": selected_level,
        "year_options": [
            {"value": "all", "label": _("All academic years"), "selected": selected_year == "all"},
            *[
                {
                    "value": str(year.pk),
                    "label": year.name,
                    "selected": selected_year == str(year.pk),
                }
                for year in years
            ],
        ],
        "level_options": [
            {"value": "all", "label": _("All levels"), "selected": selected_level == "all"},
            *[
                {
                    "value": str(level.pk),
                    "label": level.display_name,
                    "selected": selected_level == str(level.pk),
                }
                for level in levels
            ],
        ],
        "filter_counts": counts,
        "filter_tabs": [
            {
                "key": key,
                "label": label,
                "count": counts[key] or 0,
                "url": f"{reverse('courses')}?{base_query}&filter={key}",
                "active": key == selected_filter,
            }
            for key, label in (
                ("all", _("All")),
                ("published", _("Published")),
                ("unpublished", _("Drafts")),
            )
        ],
        "visible_count": len(page_offerings),
        "total_count": counts[selected_filter] or 0,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "is_empty": counts[selected_filter] == 0,
        "is_management_view": True,
    }


def build_learning_context(request) -> dict:
    user = request.user
    if is_management_student_view(user):
        return _management_learning_context(request)
    normal_active = Enrollment.objects.filter(
        student=user,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
    )
    normal_readable = Enrollment.objects.filter(
        student=user,
        status__in=(Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED),
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
    )
    normal_completed = Enrollment.objects.filter(
        student=user,
        status=Enrollment.Status.COMPLETED,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
    )
    offerings = with_offering_content_counts(
        accessible_offerings(user),
        include_drafts=False,
    ).select_related(
        "course",
        "academic_year_level__academic_year",
        "academic_year_level__level",
    ).annotate(
        has_normal_readable_access=Exists(normal_readable),
        has_normal_active_access=Exists(normal_active),
        is_normal_completed=Exists(normal_completed),
    ).order_by(
        "-academic_year_level__academic_year__ordering",
        "-academic_year_level__level__ordering",
        "course__name",
    )
    counts = offerings.aggregate(
        all=Count("pk"),
        current=Count("pk", filter=Q(has_normal_active_access=True)),
        completed=Count("pk", filter=Q(is_normal_completed=True)),
    )

    selected_filter = request.GET.get("filter", "all")
    if selected_filter not in _LEARNING_FILTERS:
        selected_filter = "all"
    filtered = offerings
    if selected_filter == "current":
        filtered = filtered.filter(has_normal_active_access=True)
    elif selected_filter == "completed":
        filtered = filtered.filter(is_normal_completed=True)

    paginator = Paginator(filtered, STUDENT_OFFERING_PAGE_SIZE)
    paginator.count = counts[selected_filter] or 0
    page_obj = paginator.get_page(request.GET.get("page", 1))
    page_offerings = list(page_obj.object_list)
    progress_by_offering = student_course_progress_data(
        user,
        page_offerings,
        include_attendance_metrics=user.study_mode != "online",
        include_quiz_metrics=False,
    )

    grouped = defaultdict(list)
    for offering in page_offerings:
        scope = offering.academic_year_level
        progress = progress_by_offering[offering.pk]
        if progress["watchable_parts"]:
            progress["completion_percent"] = progress["watch_percent"]
            progress["completion_kind"] = "parts"
        elif progress["attendance_total"]:
            progress["completion_percent"] = round(
                100 * progress["attendance_scanned"] / progress["attendance_total"]
            )
            progress["completion_kind"] = "meetings"
        else:
            progress["completion_percent"] = None
            progress["completion_kind"] = ""
        offering.student_progress = progress
        if progress["completion_percent"] is None:
            progress["ring_offset"] = 100
        else:
            progress["ring_offset"] = 100 - progress["completion_percent"]
        access_kind = (
            "current"
            if offering.has_normal_active_access
            else "historical"
            if offering.has_normal_readable_access
            else "exceptional"
        )
        grouped[(scope.academic_year_id, scope.level_id)].append(
            (offering, access_kind)
        )

    sections = []
    active_scope = None
    for _key, rows in grouped.items():
        first = rows[0][0]
        scope = first.academic_year_level
        access_kind = rows[0][1]
        completed = all(row[0].is_normal_completed for row in rows)
        sections.append({
            "academic_year": scope.academic_year,
            "level": scope.level,
            "level_name": scope.level.display_name,
            "access_kind": access_kind,
            "is_completed": completed,
            "offerings": [row[0] for row in rows],
        })
        if active_scope is None and access_kind == "current":
            active_scope = scope

    actions = student_next_actions(user)
    resume = actions["resume"]
    if resume and resume["offering_id"] and resume["lesson_id"]:
        resume["url"] = reverse(
            "lesson-details",
            args=[resume["offering_id"], resume["lesson_id"]],
        )
        resume["ring_offset"] = 100 - (resume.get("percent") or 0)
    due_soon = [
        {
            **quiz,
            "deadline": parse_datetime(quiz["closing_at"]),
            "url": reverse(
                "quiz-details",
                args=[quiz["offering_id"], quiz["id"]],
            ),
        }
        for quiz in actions["next_quizzes"]
        if quiz.get("closing_at") and quiz.get("offering_id")
    ][:2]
    next_meeting = next_student_meeting(user)
    if active_scope is None and next_meeting:
        active_scope = next_meeting["scope"]

    completion_values = [
        offering.student_progress["completion_percent"]
        for section in sections
        for offering in section["offerings"]
        if offering.student_progress["completion_percent"] is not None
    ]
    overall_percent = (
        round(sum(completion_values) / len(completion_values))
        if completion_values else 0
    )

    announcements = []
    week_meetings = []
    hour = timezone.localtime().hour
    if 5 <= hour < 12:
        greeting = _("Good morning")
    elif 12 <= hour < 17:
        greeting = _("Good afternoon")
    else:
        greeting = _("Good evening")
    if active_scope is not None:
        language = (request.LANGUAGE_CODE or "ar").split("-")[0]
        announcement_rows = student_notifications_queryset(user).filter(
            notification_type=StudentNotification.NotificationType.ANNOUNCEMENT,
        ).order_by("-created_at", "-pk")[:2]
        for notification in announcement_rows:
            title, body = localized_notification_text(notification, language)
            announcements.append({
                "title": title,
                "body": body,
                "created_at": notification.created_at,
            })
        today = timezone.localdate()
        week_meetings = list(
            AcademicYearLevelMeeting.objects.filter(
                academic_year_level=active_scope,
                meeting_date__gte=today,
                meeting_date__lt=today + timedelta(days=7),
            )
            .select_related("course_offering__course")
            .order_by("meeting_date", "pk")[:7]
        )

    return {
        "sections": sections,
        "active_scope": active_scope,
        "next_meeting": next_meeting,
        "resume": resume,
        "due_soon": due_soon,
        "announcements": announcements,
        "week_meetings": week_meetings,
        "greeting": greeting,
        "overall_percent": overall_percent,
        "overall_ring_offset": 100 - overall_percent,
        "selected_filter": selected_filter,
        "filter_counts": counts,
        "filter_tabs": [
            {
                "key": key,
                "label": label,
                "count": counts[key] or 0,
                "url": f"{reverse('courses')}?filter={key}",
                "active": key == selected_filter,
            }
            for key, label in (
                ("all", _("All")),
                ("current", _("Current")),
                ("completed", _("Completed")),
            )
        ],
        "visible_count": len(page_offerings),
        "total_count": counts[selected_filter] or 0,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "is_empty": counts[selected_filter] == 0,
    }
