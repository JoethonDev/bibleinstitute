"""Bounded course workspace presentation data."""

from django.db.models import Count
from django.urls import reverse
from django.utils.translation import gettext as _

from ..course_content import course_navigation, with_offering_content_counts
from ..models import Question
from ..utils.helpers import parse_json_value
from ..utils.student_data import (
    student_course_progress_data,
    student_course_quiz_data,
)
from .calendar import next_student_meeting

COURSE_CHILD_PAGE_SIZE = 20


def _lesson_media_summary(raw_links) -> str:
    """Return a concise, deduplicated label of part types inside a lesson."""
    parts = parse_json_value(raw_links, default=[])
    if not isinstance(parts, list):
        return ""
    labels: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        file_type = part.get("file_type")
        if file_type == "video":
            key, label = "video", _("Video")
        elif file_type == "audio":
            key, label = "audio", _("Audio")
        else:
            key, label = "pdf", "PDF"
        if key not in seen:
            seen.add(key)
            labels.append(label)
    return " + ".join(labels)


def build_course_context(user, offering, *, management_preview: bool = False) -> dict:
    """Build the visible course outline and own-account learning state."""
    if not hasattr(offering, "published_lesson_count"):
        offering = with_offering_content_counts(
            offering.__class__.objects.filter(pk=offering.pk),
            include_drafts=management_preview,
        ).select_related(
            "course",
            "academic_year_level__academic_year",
            "academic_year_level__level",
        ).get()
    navigation = course_navigation(offering, include_drafts=management_preview)
    lessons = list(
        navigation["lessons"]
        .only("id", "name", "course_offering_id", "opening_date", "links", "status")
        .order_by("created_date", "pk")[:COURSE_CHILD_PAGE_SIZE]
    )
    quiz_rows = student_course_quiz_data(
        user,
        offering,
        limit=COURSE_CHILD_PAGE_SIZE,
        include_drafts=management_preview,
    )
    quiz_ids = [row["id"] for row in quiz_rows]
    question_counts = (
        dict(
            Question.objects.filter(quiz_id__in=quiz_ids)
            .values_list("quiz_id")
            .annotate(total=Count("pk"))
        )
        if quiz_ids else {}
    )
    if management_preview:
        progress = {
            "watchable_parts": 0,
            "watched_parts": 0,
            "watch_percent": 0,
            "attendance_total": 0,
            "attendance_scanned": 0,
            "completion_percent": None,
            "ring_offset": 100,
            "quiz_average": None,
        }
        for lesson in lessons:
            lesson.media_summary = _lesson_media_summary(lesson.links)
            lesson.student_progress = None
    else:
        progress = student_course_progress_data(
            user,
            [offering],
            include_unpublished=False,
            lesson_detail_ids={lesson.pk for lesson in lessons},
            include_attendance_metrics=user.study_mode != "online",
            include_quiz_metrics=False,
        )[offering.pk]
        lesson_progress = progress.get("lesson_progress", {})
        for lesson in lessons:
            lesson.media_summary = _lesson_media_summary(lesson.links)
            lesson.student_progress = lesson_progress.get(lesson.pk, {
                "watched_parts": 0,
                "watchable_parts": 0,
                "watch_percent": 0,
            })
            lesson.student_progress["is_complete"] = bool(
                lesson.student_progress["watchable_parts"]
                and lesson.student_progress["watched_parts"]
                >= lesson.student_progress["watchable_parts"]
            )

        grade_percents = [
            round(100 * row["grade"].total_grade / row["quiz"].total_grade)
            for row in quiz_rows
            if row["grade"] is not None and row["quiz"].total_grade
        ]
        quiz_average = (
            round(sum(grade_percents) / len(grade_percents))
            if grade_percents else None
        )
        if progress.get("watchable_parts"):
            completion_percent = progress.get("watch_percent", 0)
        elif progress.get("attendance_total"):
            completion_percent = round(
                100 * progress.get("attendance_scanned", 0) / progress["attendance_total"]
            )
        else:
            completion_percent = 0
        progress["completion_percent"] = completion_percent
        progress["ring_offset"] = 100 - completion_percent
        progress["quiz_average"] = quiz_average

    for row in quiz_rows:
        row["url"] = reverse(
            "quiz-details", args=[offering.pk, row["id"]]
        )
        row["grade_value"] = (
            row["grade"].total_grade if row["grade"] is not None else None
        )
        row["question_count"] = question_counts.get(row["id"], 0)

    if management_preview:
        next_action = None
    else:
        next_quiz = next(
            (row for row in quiz_rows if row["status"] in {"ready", "exam"}),
            None,
        )
        next_lesson = next(
            (lesson for lesson in lessons if not lesson.student_progress["is_complete"]),
            None,
        )
        if next_lesson:
            next_action = {
                "label": next_lesson.name,
                "url": reverse(
                    "lesson-details", args=[offering.pk, next_lesson.pk]
                ),
                "progress": next_lesson.student_progress["watch_percent"],
                "kind": "lesson",
            }
        elif next_quiz:
            next_action = {
                "label": next_quiz["quiz"].name,
                "url": next_quiz["url"],
                "progress": None,
                "kind": "quiz",
            }
        else:
            next_action = None

    return {
        "offering": offering,
        "course": offering.course,
        "course_short": (
            offering.course.name.split()[0] if offering.course.name else ""
        ),
        "lessons": lessons,
        "quiz_rows": quiz_rows,
        "progress": progress,
        "next_meeting": None if management_preview else next_student_meeting(user),
        "next_action": next_action,
        "management_preview": management_preview,
        "has_more_lessons": offering.published_lesson_count > len(lessons),
        "has_more_quizzes": offering.published_quiz_count > len(quiz_rows),
    }