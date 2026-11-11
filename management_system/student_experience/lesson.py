"""Lesson-page progress and bounded outline presentation data."""

from django.urls import reverse

from .course import build_course_context


def build_lesson_context(
    user,
    offering,
    lesson,
    links: list[dict],
    *,
    management_preview: bool = False,
) -> dict:
    course_context = build_course_context(
        user,
        offering,
        management_preview=management_preview,
    )
    lessons = course_context["lessons"]
    lesson_ids = [row.pk for row in lessons]
    position = lesson_ids.index(lesson.pk) if lesson.pk in lesson_ids else -1
    previous_lesson = lessons[position - 1] if position > 0 else None
    next_lesson = lessons[position + 1] if 0 <= position < len(lessons) - 1 else None
    lesson_progress = course_context["progress"].get("lesson_progress", {}).get(lesson.pk, {
        "watched_parts": 0,
        "watchable_parts": 0,
        "watch_percent": 0,
    })
    lesson_progress = dict(lesson_progress)
    lesson_progress["ring_offset"] = 100 - lesson_progress.get("watch_percent", 0)
    for part in links:
        part["progress_url"] = reverse("progress-heartbeat")

    next_quiz = next(
        (
            row for row in course_context["quiz_rows"]
            if row["status"] in {"ready", "exam", "upcoming"}
        ),
        None,
    )
    return {
        **course_context,
        "offering": offering,
        "offering_id": offering.pk,
        "course": offering.course,
        "lesson": lesson,
        "lesson_id": lesson.pk,
        "links": links,
        "lesson_parts_count": len(links),
        "lesson_position": position + 1 if position >= 0 else None,
        "lesson_total": offering.published_lesson_count,
        "lesson_progress": lesson_progress,
        "next_quiz": next_quiz,
        "previous_lesson": previous_lesson,
        "next_lesson": next_lesson,
        "previous_url": (
            reverse("lesson-details", args=[offering.pk, previous_lesson.pk])
            if previous_lesson else None
        ),
        "next_url": (
            reverse("lesson-details", args=[offering.pk, next_lesson.pk])
            if next_lesson else None
        ),
        "management_preview": management_preview,
    }
