"""Student-only quiz presentation context."""

from django.utils import formats, timezone

from ..utils.timezones import ensure_aware, user_timezone


def _datetime_label(value, user) -> str:
    if not value:
        return ""
    localized = timezone.localtime(ensure_aware(value), user_timezone(user))
    return formats.date_format(localized, "l, j F Y, H:i")


def build_quiz_presentation_context(quiz_context: dict, *, user=None) -> dict:
    """Add the derived display values used by the student quiz template."""
    context = dict(quiz_context)
    quiz = context["quiz"]
    mode = context["mode"]
    questions = context["questions"]
    context["question_count"] = (
        len(questions)
        if mode in {"exam", "view", "closed_unsolved"}
        else getattr(quiz, "question_total", None) or quiz.questions.count()
    )
    score_percent = (
        round(100 * (context["total_grade"] or 0) / quiz.total_grade)
        if quiz.total_grade
        else 0
    )
    context["score_percent"] = score_percent
    context["score_ring_offset"] = round(320.44 * (1 - score_percent / 100), 2)
    if user is not None:
        context["quiz_opening_label"] = _datetime_label(
            context.get("effective_opening_date"), user
        )
        context["quiz_closing_label"] = _datetime_label(
            context.get("effective_closing_date"), user
        )
    return context
