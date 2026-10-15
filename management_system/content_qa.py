"""Read-only content QA checks — bounded SQL + bounded Python JSON parsing.

No writes, no R2 calls, no migration. Each check returns counts + up to 25
sample rows with links into the existing dashboards. Live segment existence
stays a manual R2-browser step (linked per row).
"""
from __future__ import annotations

import json

from django.db.models import Count

from .models import Lesson, PublicationStatus, Question, Quiz

SAMPLE = 25


def _parse_links(raw) -> list:
    try:
        links = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return links if isinstance(links, list) else []


def qa_counts() -> dict:
    """Aggregate counts for the QA header — 5 small COUNT queries."""
    empty_lessons = Lesson.objects.filter(links__in=["", "[]", None]).count()
    published = Lesson.objects.filter(status=PublicationStatus.PUBLISHED)
    published_total = published.count()
    null_type_quizzes = Quiz.objects.filter(quiz_type__isnull=True).count()
    quizzes_no_questions = (
        Quiz.objects.annotate(n=Count("questions")).filter(n=0).count()
    )
    return {
        "empty_lessons": empty_lessons,
        "published_lessons": published_total,
        "null_type_quizzes": null_type_quizzes,
        "quizzes_no_questions": quizzes_no_questions,
    }


def qa_samples() -> dict:
    """Bounded samples for each QA rule (<=25 rows each, no per-row queries)."""
    empty_lessons = list(
        Lesson.objects.filter(links__in=["", "[]", None])
        .select_related("course_offering__course")
        .order_by("name")[:SAMPLE]
    )
    # Published lessons whose links carry no playable video/audio.
    candidates = list(
        Lesson.objects.filter(status=PublicationStatus.PUBLISHED)
        .select_related("course_offering__course")
        .order_by("-updated_date")[:200]
    )
    no_media: list = []
    malformed: list = []
    for lesson in candidates:
        links = _parse_links(lesson.links)
        if not links:
            continue  # already covered by empty_lessons when applicable
        has_playable = any(
            isinstance(link, dict)
            and link.get("file_type") in {"video", "audio"}
            and link.get("id")
            for link in links
        )
        # A book-only lesson is legitimate content, not a defect.
        has_book = any(
            isinstance(link, dict)
            and link.get("file_type") == "book"
            and link.get("id")
            for link in links
        )
        if not has_playable and not has_book and len(no_media) < SAMPLE:
            no_media.append(lesson)
        bad = any(
            not isinstance(link, dict) or not link.get("id") or not link.get("part_id")
            for link in links
        )
        if bad and len(malformed) < SAMPLE:
            malformed.append(lesson)
        if len(no_media) >= SAMPLE and len(malformed) >= SAMPLE:
            break

    null_type = list(
        Quiz.objects.filter(quiz_type__isnull=True)
        .select_related("course_offering__course")
        .order_by("name")[:SAMPLE]
    )
    no_questions = list(
        Quiz.objects.annotate(n=Count("questions"))
        .filter(n=0)
        .select_related("course_offering__course")
        .order_by("name")[:SAMPLE]
    )
    # Orphan questions are impossible via FK cascade, but surface detached
    # submissions cleanup scope: grades without questions are read-only info.
    return {
        "empty_lessons": empty_lessons,
        "published_no_media": no_media,
        "malformed_links": malformed,
        "null_type_quizzes": null_type,
        "quizzes_no_questions": no_questions,
    }
