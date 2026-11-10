"""Student-only quiz presentation context."""


def build_quiz_presentation_context(quiz_context: dict) -> dict:
    """Add the derived display values used by the student quiz template."""
    context = dict(quiz_context)
    quiz = context["quiz"]
    mode = context["mode"]
    questions = context["questions"]
    context["question_count"] = (
        len(questions)
        if mode in {"exam", "view", "closed_unsolved"}
        else quiz.questions.count()
    )
    score_percent = (
        round(100 * (context["total_grade"] or 0) / quiz.total_grade)
        if quiz.total_grade
        else 0
    )
    context["score_percent"] = score_percent
    context["score_ring_offset"] = round(320.44 * (1 - score_percent / 100), 2)
    return context
