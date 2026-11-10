from collections import defaultdict

from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from ..academic_access import accessible_offerings
from ..course_content import with_offering_content_counts
from ..models import Enrollment
from ..utils.student_data import student_course_progress_data, student_next_actions
from ..utils.helpers import pagination_query_string
from .calendar import next_student_meeting

STUDENT_OFFERING_PAGE_SIZE = 20
_LEARNING_FILTERS = {"all", "current", "completed"}


def build_learning_context(request) -> dict:
    user = request.user
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

    return {
        "sections": sections,
        "active_scope": active_scope,
        "next_meeting": next_meeting,
        "resume": resume,
        "due_soon": due_soon,
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
