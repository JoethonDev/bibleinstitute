"""Canonical command-palette search for management users.

One bounded query per entity type, reusing the shared normalized search.
No per-row queries; results carry only id/label/sub/url for navigation.
`type_filter` restricts to one group (u/o/l/q/m/t); unknown values are
ignored and all groups are searched. Telegram phone display is masked
unless `show_phone` (admin-only Telegram data) is true.
"""
from __future__ import annotations

import re

from django.db.models import Q
from django.urls import reverse

from .models import CourseOffering, Lesson, MediaProcessingJob, Quiz, TelegramAccount, User
from .utils.search import normalize_search_text, normalized_contains_q

PALETTE_LIMIT = 8

PALETTE_TYPES = ("user", "offering", "lesson", "quiz", "media", "telegram")

#: Single-letter prefixes accepted by the palette input (e.g. "u ahmed").
PREFIX_TO_TYPE = {"u": "user", "o": "offering", "l": "lesson", "q": "quiz", "m": "media", "t": "telegram"}


def _clean(raw: str | None) -> str:
    return normalize_search_text(str(raw or "").strip()[:100])


def parse_palette_prefix(raw: str | None) -> tuple[str, str | None]:
    """Split an optional leading `<letter> ` scope prefix from the query."""
    text = str(raw or "").strip()[:100]
    if len(text) >= 3 and text[1] == " " and text[0].lower() in PREFIX_TO_TYPE:
        return text[2:], PREFIX_TO_TYPE[text[0].lower()]
    return text, None


def _mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    return "•••" + digits[-4:] if len(digits) > 4 else "•••" + digits


def palette_results(query: str, type_filter: str | None = None, show_phone: bool = False) -> list[dict]:
    """Return up to 8 hits per group for users/offerings/lessons/quizzes/media/telegram."""
    raw_query, prefix_type = parse_palette_prefix(query)
    if type_filter not in PALETTE_TYPES:
        type_filter = None
    active = prefix_type or type_filter
    q = _clean(raw_query)
    if len(q) < 2:
        return []
    out: list[dict] = []
    want = lambda *names: active is None or active in names

    if want("user"):
        users = (
            User.objects.filter(
                normalized_contains_q(("username", "email", "first_name", "last_name"), q)
            )
            .select_related("role")
            .order_by("username")[:PALETTE_LIMIT]
        )
        seen_pks: set[int] = set()
        for u in users:
            seen_pks.add(u.pk)
            try:
                url = reverse("user-profile", args=[u.pk])
            except Exception:
                continue
            out.append({
                "type": "user",
                "id": u.pk,
                "label": u.get_full_name() or u.username,
                "sub": u.username,
                "url": url,
            })
        # Exact numeric ID lookup (indexed pk) for pasted IDs.
        if q.isdigit():
            exact = User.objects.select_related("role").filter(pk=int(q[:18])).first()
            if exact is not None and exact.pk not in seen_pks:
                try:
                    url = reverse("user-profile", args=[exact.pk])
                except Exception:
                    url = ""
                if url:
                    out.append({
                        "type": "user",
                        "id": exact.pk,
                        "label": exact.get_full_name() or exact.username,
                        "sub": exact.username,
                        "url": url,
                    })

    if want("offering"):
        offerings = (
            CourseOffering.objects.filter(
                normalized_contains_q(("course__name",), q)
            )
            .select_related("course", "academic_year_level__level", "academic_year_level__academic_year")
            .order_by("course__name")[:PALETTE_LIMIT]
        )
        for o in offerings:
            try:
                url = reverse("course-details", args=[o.pk])
            except Exception:
                continue
            out.append({
                "type": "offering",
                "id": o.pk,
                "label": o.course.name if getattr(o, "course_id", None) else f"#{o.pk}",
                "sub": f"{o.academic_year_level.level.display_name} — {o.academic_year_level.academic_year.name}" if getattr(o, "academic_year_level_id", None) else "",
                "url": url,
            })

    if want("lesson"):
        lessons = (
            Lesson.objects.filter(normalized_contains_q(("name",), q))
            .select_related("course_offering__course")
            .order_by("name")[:PALETTE_LIMIT]
        )
        for lesson in lessons:
            try:
                url = reverse("lesson-view", args=[lesson.pk])
            except Exception:
                continue
            out.append({
                "type": "lesson",
                "id": lesson.pk,
                "label": lesson.name,
                "sub": lesson.course_offering.course.name if getattr(lesson, "course_offering_id", None) and getattr(lesson.course_offering, "course_id", None) else "",
                "url": url,
            })

    if want("quiz"):
        quizzes = (
            Quiz.objects.filter(normalized_contains_q(("name",), q))
            .select_related("course_offering__course")
            .order_by("name")[:PALETTE_LIMIT]
        )
        for quiz in quizzes:
            try:
                url = reverse("quiz-view", args=[quiz.pk])
            except Exception:
                continue
            out.append({
                "type": "quiz",
                "id": quiz.pk,
                "label": quiz.name,
                "sub": quiz.course_offering.course.name if getattr(quiz, "course_offering_id", None) and getattr(quiz.course_offering, "course_id", None) else "",
                "url": url,
            })

    if want("media"):
        jobs = (
            MediaProcessingJob.objects.filter(original_filename__icontains=q.strip()[:100])
            .order_by("-created_at")[:PALETTE_LIMIT]
        )
        for job in jobs:
            try:
                url = reverse("media-processing-status") + f"?q={job.public_id}"
            except Exception:
                continue
            out.append({
                "type": "media",
                "id": str(job.public_id),
                "label": job.original_filename or str(job.public_id),
                "sub": job.status,
                "url": url,
            })

    if want("telegram"):
        digits = re.sub(r"\D", "", q)
        tg_filter = Q()
        if digits:
            tg_filter |= Q(phone__icontains=digits)
        tg_filter |= normalized_contains_q(
            ("user__username", "user__first_name", "user__last_name"), q
        )
        accounts = (
            TelegramAccount.objects.filter(tg_filter)
            .select_related("user")
            .order_by("-linked_at")[:PALETTE_LIMIT]
        )
        for acc in accounts:
            try:
                url = reverse("student-overview", args=[acc.user_id])
            except Exception:
                continue
            phone = acc.phone if show_phone else _mask_phone(acc.phone)
            out.append({
                "type": "telegram",
                "id": acc.user_id,
                "label": acc.user.get_full_name() or acc.user.username,
                "sub": phone,
                "url": url,
            })
    return out

    users = (
        User.objects.filter(
            normalized_contains_q(("username", "email", "first_name", "last_name"), q)
        )
        .select_related("role")
        .order_by("username")[:PALETTE_LIMIT]
    )
    for u in users:
        try:
            url = reverse("user-profile", args=[u.pk])
        except Exception:
            continue
        out.append({
            "type": "user",
            "id": u.pk,
            "label": u.get_full_name() or u.username,
            "sub": u.username,
            "url": url,
        })

    offerings = (
        CourseOffering.objects.filter(
            normalized_contains_q(("course__name",), q)
        )
        .select_related("course", "academic_year_level__level", "academic_year_level__academic_year")
        .order_by("course__name")[:PALETTE_LIMIT]
    )
    for o in offerings:
        try:
            url = reverse("course-details", args=[o.pk])
        except Exception:
            continue
        out.append({
            "type": "offering",
            "id": o.pk,
            "label": o.course.name if getattr(o, "course_id", None) else f"#{o.pk}",
            "sub": f"{o.academic_year_level.level.display_name} — {o.academic_year_level.academic_year.name}" if getattr(o, "academic_year_level_id", None) else "",
            "url": url,
        })

    lessons = (
        Lesson.objects.filter(normalized_contains_q(("name",), q))
        .select_related("course_offering__course")
        .order_by("name")[:PALETTE_LIMIT]
    )
    for lesson in lessons:
        try:
            url = reverse("lesson-view", args=[lesson.pk])
        except Exception:
            continue
        out.append({
            "type": "lesson",
            "id": lesson.pk,
            "label": lesson.name,
            "sub": lesson.course_offering.course.name if getattr(lesson, "course_offering_id", None) and getattr(lesson.course_offering, "course_id", None) else "",
            "url": url,
        })

    quizzes = (
        Quiz.objects.filter(normalized_contains_q(("name",), q))
        .select_related("course_offering__course")
        .order_by("name")[:PALETTE_LIMIT]
    )
    for quiz in quizzes:
        try:
            url = reverse("quiz-view", args=[quiz.pk])
        except Exception:
            continue
        out.append({
            "type": "quiz",
            "id": quiz.pk,
            "label": quiz.name,
            "sub": quiz.course_offering.course.name if getattr(quiz, "course_offering_id", None) and getattr(quiz.course_offering, "course_id", None) else "",
            "url": url,
        })

    jobs = (
        MediaProcessingJob.objects.filter(original_filename__icontains=q.strip()[:100])
        .order_by("-created_at")[:PALETTE_LIMIT]
    )
    for job in jobs:
        try:
            url = reverse("media-processing-status") + f"?q={job.public_id}"
        except Exception:
            continue
        out.append({
            "type": "media",
            "id": str(job.public_id),
            "label": job.original_filename or str(job.public_id),
            "sub": job.status,
            "url": url,
        })
    return out
