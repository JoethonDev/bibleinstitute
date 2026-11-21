"""Bounded all-courses quiz index for the student shell and home shortcuts."""

from collections import defaultdict

from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..academic_access import (
    accessible_offerings,
    active_enrollment_scope,
    can_write_offering_from_scope,
)
from ..models import (
    Grade,
    PublicationStatus,
    Quiz,
    QuizAnswerSnapshot,
    QuizStudentOpening,
)
from ..utils.quiz_access import management_quiz_status, quiz_attempt_status_from
from ..utils.timezones import format_human_datetime
from .mode import is_management_student_view

_INDEX_STATUSES = {"pending", "completed"}
_PENDING_ORDER = {"exam": 0, "ready": 1, "upcoming": 2, "closed_unsolved": 3, "draft": 4}
# The student sees only their readable offerings and the course page caps each
# course at 20 quizzes; this hard cap guards a pathological content volume
# without paginating the course grouping away.
QUIZ_INDEX_MAX = 2000


def _readable_offerings(user, *, management: bool):
    return (
        accessible_offerings(user, include_management=management)
        .select_related(
            "course",
            "academic_year_level__academic_year",
            "academic_year_level__level",
        )
        .order_by(
            "-academic_year_level__academic_year__ordering",
            "academic_year_level__level__ordering",
            "course__name",
            "pk",
        )
    )


def quiz_home_summary(user) -> dict[str, int]:
    """Pending and completed quiz counts for the learner's readable offerings."""
    readable = accessible_offerings(user).values("pk")
    pending = (
        Quiz.objects.filter(
            course_offering__in=readable,
            status=PublicationStatus.PUBLISHED,
        )
        .exclude(Exists(Grade.objects.filter(quiz_id=OuterRef("pk"), user=user)))
        .count()
    )
    completed = Grade.objects.filter(
        user=user,
        quiz__course_offering__in=readable,
        quiz__status=PublicationStatus.PUBLISHED,
    ).count()
    return {"pending": pending, "completed": completed}


def build_quiz_index_context(request) -> dict:
    """Group every readable quiz by course and status with bounded reads."""
    user = request.user
    management = is_management_student_view(user)
    offerings = list(_readable_offerings(user, management=management))
    offering_ids = [offering.pk for offering in offerings]
    current = timezone.now()

    quizzes = []
    if offering_ids:
        queryset = (
            Quiz.objects.filter(course_offering_id__in=offering_ids)
            .select_related(
                "course_offering__course",
                "course_offering__academic_year_level__academic_year",
                "course_offering__academic_year_level__level",
            )
            .order_by("opening_date", "pk")
        )
        if not management:
            queryset = queryset.filter(status=PublicationStatus.PUBLISHED)
        quizzes = list(queryset[:QUIZ_INDEX_MAX])
    quiz_ids = [quiz.pk for quiz in quizzes]

    grades = {}
    openings = {}
    snapshots = {}
    if not management:
        grades = {
            row.quiz_id: row
            for row in Grade.objects.filter(
                user=user, quiz_id__in=quiz_ids
            ).only("quiz_id", "total_grade")
        }
        openings = {
            row.quiz_id: row
            for row in QuizStudentOpening.objects.filter(
                student=user, quiz_id__in=quiz_ids
            ).only("quiz_id", "opening_date", "closing_date")
        }
        # The status rule only reads the deadline; a snapshot answer payload can
        # be large, so never load it for the index.
        snapshots = {
            row.quiz_id: row
            for row in QuizAnswerSnapshot.objects.filter(
                user=user, quiz_id__in=quiz_ids
            ).only("quiz_id", "deadline_at")
        }
        normal_scopes, targeted_offerings = active_enrollment_scope(user)

    rows_by_offering: dict[int, list[dict]] = defaultdict(list)
    for quiz in quizzes:
        offering = quiz.course_offering
        if management:
            status = management_quiz_status(quiz, current)
            grade = None
            closing_date = quiz.closing_date
        else:
            grade = grades.get(quiz.pk)
            opening = openings.get(quiz.pk)
            status, _grade = quiz_attempt_status_from(
                quiz,
                grade,
                opening,
                current,
                can_write_offering_from_scope(
                    offering, normal_scopes, targeted_offerings
                ),
                snapshots.get(quiz.pk),
            )
            closing_date = opening.closing_date if opening else quiz.closing_date
        rows_by_offering[offering.pk].append({
            "quiz": quiz,
            "name": quiz.name,
            "status": status,
            "url": reverse("quiz-details", args=[offering.pk, quiz.pk]),
            "opening_date": quiz.opening_date,
            "closing_date": closing_date,
            "closing_label": (
                format_human_datetime(closing_date, user) if closing_date else ""
            ),
            "window_closed": bool(closing_date and closing_date <= current),
            "grade_value": grade.total_grade if grade else None,
            "grade_percent": (
                round(100 * grade.total_grade / quiz.total_grade)
                if grade and quiz.total_grade else None
            ),
        })

    def groups_for(completed: bool) -> list[dict]:
        groups = []
        for offering in offerings:
            # A grade renders as "view"; every other status is pending attention.
            rows = [
                row for row in rows_by_offering.get(offering.pk, [])
                if (row["status"] == "view") == completed
            ]
            if not rows:
                continue
            if completed:
                rows.sort(key=lambda row: row["closing_date"] or current, reverse=True)
            else:
                rows.sort(key=lambda row: (
                    _PENDING_ORDER.get(row["status"], 9),
                    row["closing_date"] or current,
                    row["opening_date"] or current,
                ))
            groups.append({
                "course_name": offering.course.name,
                "year_name": offering.academic_year_level.academic_year.name,
                "level_name": offering.academic_year_level.level.display_name,
                "quizzes": rows,
            })
        return groups

    pending_groups = groups_for(completed=False)
    completed_groups = groups_for(completed=True)
    pending_count = sum(len(group["quizzes"]) for group in pending_groups)
    completed_count = sum(len(group["quizzes"]) for group in completed_groups)

    selected = request.GET.get("status", "pending")
    if selected not in _INDEX_STATUSES:
        selected = "pending"
    base_url = reverse("student-quizzes")
    return {
        "selected_status": selected,
        "visible_groups": pending_groups if selected == "pending" else completed_groups,
        "visible_count": pending_count if selected == "pending" else completed_count,
        "filter_tabs": [
            {
                "label": label,
                "count": pending_count if key == "pending" else completed_count,
                "url": f"{base_url}?status={key}",
                "active": selected == key,
            }
            for key, label in (("pending", _("Pending")), ("completed", _("Completed")))
        ],
    }
