"""Server-owned quiz drafts and atomic finalization under the canonical quiz lock."""
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Grade, PublicationStatus, Question, Quiz, QuizAnswerSnapshot, QuizStudentOpening, Submission
from .utils.helpers import is_quiz_open, is_quiz_in_user_window

from .utils.quiz_access import QUIZ_SUBMISSION_GRACE


def normalize_answers(quiz: Quiz, answers: dict) -> dict[str, str]:
    if not isinstance(answers, dict) or len(answers) > 500:
        raise ValidationError(_("Invalid quiz answers."))
    valid = set(Question.objects.filter(quiz=quiz).values_list("pk", flat=True)[:501])
    if len(valid) > 500:
        raise ValidationError(_("Invalid quiz answers."))
    result = {}
    for key, value in answers.items():
        if not str(key).isdigit():
            raise ValidationError(_("Invalid quiz question."))
        # A live editor can remove questions after the student loaded the exam.
        # Only current quiz IDs may be stored or graded; discard obsolete IDs.
        if int(key) not in valid:
            continue
        value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        if not isinstance(value, str) or len(value) > 50000:
            raise ValidationError(_("Invalid quiz answers."))
        result[str(int(key))] = value
    if len(json.dumps(result)) > 1000000:
        raise ValidationError(_("Invalid quiz answers."))
    return result


def _submission_rows(quiz: Quiz, user_id: int, answers: dict, questions: list[Question]) -> tuple[list[Submission], Grade]:
    submissions = []
    for question in questions:
        submission = Submission(user_id=user_id, question=question,
                                submitted_answer=answers.get(str(question.pk), "-"))
        submission.assign_grade()
        submissions.append(submission)
    return submissions, Grade(quiz=quiz, user_id=user_id, total_grade=sum(row.grade for row in submissions))


@transaction.atomic
def submit_answers(quiz_id: int, user, answers: dict) -> Grade:
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
    grade = Grade.objects.filter(quiz=quiz, user=user).first()
    if grade:
        QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).delete()
        return grade
    if quiz.status != PublicationStatus.PUBLISHED or not is_quiz_open(quiz, timezone.now(), user) or not is_quiz_in_user_window(quiz, user):
        raise PermissionDenied(_("Invalid Request, submission is closed!"))
    answers = normalize_answers(quiz, answers)
    questions = list(Question.objects.filter(quiz=quiz).order_by("pk")[:501])
    submissions, grade = _submission_rows(quiz, user.pk, answers, questions)
    Submission.objects.bulk_create(submissions, batch_size=500)
    grade.save()
    QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).delete()
    return grade


@transaction.atomic
def save_snapshot(quiz_id: int, user, answers: dict, revision: int) -> QuizAnswerSnapshot | None:
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
    if Grade.objects.filter(quiz=quiz, user=user).exists():
        QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).delete()
        return None
    if quiz.status != PublicationStatus.PUBLISHED or not is_quiz_open(quiz, timezone.now(), user) or not is_quiz_in_user_window(quiz, user):
        raise PermissionDenied(_("Invalid Request, submission is closed!"))
    answers = normalize_answers(quiz, answers)
    snapshot = QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).first()
    # Retrying an acknowledged-equivalent payload after a lost response is safe.
    if snapshot and (snapshot.answers == answers or (
        revision != snapshot.revision and normalize_answers(quiz, snapshot.answers) == answers
    )):
        return snapshot
    if revision != (snapshot.revision if snapshot else 0):
        raise ValidationError(_("Answers changed in another window. Reload before saving."), code="snapshot_conflict")
    if snapshot:
        snapshot.answers = answers
        snapshot.revision += 1
        snapshot.save(update_fields=["answers", "revision", "updated_at"])
    else:
        snapshot = QuizAnswerSnapshot.objects.create(quiz=quiz, user=user, answers=answers)
    return snapshot


def due_snapshots() -> QuerySet[QuizAnswerSnapshot]:
    opening = QuizStudentOpening.objects.filter(quiz_id=OuterRef("quiz_id"), student_id=OuterRef("user_id"))
    cutoff = timezone.now() - QUIZ_SUBMISSION_GRACE
    return QuizAnswerSnapshot.objects.annotate(
        exceptional_close=Subquery(opening.values("closing_date")[:1]),
        has_grade=Exists(Grade.objects.filter(quiz_id=OuterRef("quiz_id"), user_id=OuterRef("user_id"))),
    ).filter(Q(has_grade=True) | Q(exceptional_close__lt=cutoff) |
             Q(exceptional_close__isnull=True, quiz__closing_date__lt=cutoff))


@transaction.atomic
def reconcile_snapshots(quiz_id: int, *, user_id: int | None = None, batch_size: int = 100) -> tuple[int, bool]:
    """Only previously accepted server data can be finalized after the grace period.

    Accepted drafts retain their authority after enrollment/year archival. Current
    student input is never consulted by this recovery path.
    """
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
    rows = due_snapshots().filter(quiz=quiz)
    if user_id is not None:
        rows = rows.filter(user_id=user_id)
    snapshots = list(rows.order_by("pk")[:batch_size])
    if not snapshots:
        return 0, False
    questions = list(Question.objects.filter(quiz=quiz).order_by("pk")[:501])
    if len(questions) > 500:
        raise ValidationError(_("Invalid quiz answers."))
    # Bulk write a bounded student batch; no queries inside the student loop.
    submissions, grades = [], []
    for snapshot in snapshots:
        if snapshot.has_grade:
            continue
        answers, grade = _submission_rows(quiz, snapshot.user_id, snapshot.answers, questions)
        submissions.extend(answers)
        grades.append(grade)
    Submission.objects.bulk_create(submissions, batch_size=500)
    Grade.objects.bulk_create(grades, batch_size=100)
    QuizAnswerSnapshot.objects.filter(pk__in=[row.pk for row in snapshots]).delete()
    return len(grades), len(snapshots) == batch_size
