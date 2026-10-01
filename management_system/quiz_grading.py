"""Reconcile saved quiz scores with the current question grading rules."""

from collections.abc import Iterable

from django.db import transaction
from django.db.models import IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import Grade, Question, Quiz, Submission


def regrade_quiz_submissions(
    quiz_id: int,
    question_ids: Iterable[int] = (),
    *,
    reset_manual_ids: Iterable[int] = (),
    clamp_manual_ids: Iterable[int] = (),
) -> int:
    """Regrade selected questions and refresh every saved total for this quiz."""
    selected_ids = set(question_ids)
    reset_ids = set(reset_manual_ids)
    clamp_ids = set(clamp_manual_ids)
    evaluated = 0

    with transaction.atomic():
        # Submission writes lock the same quiz row. Keep the definition change,
        # batched regrade, and saved totals consistent until this transaction commits.
        Quiz.objects.select_for_update().only("pk").get(pk=quiz_id)
        questions = {
            question.pk: question
            for question in Question.objects.filter(quiz_id=quiz_id, pk__in=selected_ids)
            if question.auto_grade or question.pk in reset_ids or question.pk in clamp_ids
        }
        last_pk = 0
        while questions:
            batch = list(
                Submission.objects.filter(
                    question_id__in=questions.keys(),
                    pk__gt=last_pk,
                )
                .only("pk", "question_id", "submitted_answer", "grade", "is_graded")
                .order_by("pk")[:500]
            )
            if not batch:
                break
            last_pk = batch[-1].pk
            changed = []
            for submission in batch:
                question = questions[submission.question_id]
                evaluated += 1
                old_state = (submission.is_graded, submission.grade)
                if question.auto_grade:
                    submission.is_graded = True
                    submission.grade = question.get_auto_grade(submission.submitted_answer)
                elif question.pk in reset_ids:
                    submission.is_graded = False
                    submission.grade = 0
                elif submission.is_graded:
                    submission.grade = min(submission.grade, question.grade)
                if old_state != (submission.is_graded, submission.grade):
                    changed.append(submission)
            if changed:
                Submission.objects.bulk_update(changed, ["is_graded", "grade"], batch_size=500)

        totals = (
            Submission.objects.filter(user_id=OuterRef("user_id"), question__quiz_id=quiz_id)
            .order_by()
            .values("user_id")
            .annotate(total=Sum("grade"))
            .values("total")[:1]
        )
        Grade.objects.filter(quiz_id=quiz_id).update(
            total_grade=Coalesce(Subquery(totals, output_field=IntegerField()), Value(0))
        )

    return evaluated
