"""Server-owned timed exam attempts, snapshots, and bounded finalization."""
import json
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Grade, PublicationStatus, Question, Quiz, QuizAnswerSnapshot, Submission
from .utils.helpers import is_quiz_in_user_window

from .utils.quiz_access import quiz_window, quiz_window_is_open
from .utils.timezones import ensure_aware


def start_quiz_attempt(quiz_id: int, user) -> QuizAnswerSnapshot:
    """Create one server-owned timed attempt after rechecking access and window."""
    if getattr(getattr(user, "role", None), "role", None) in {"admin", "staff", "moderator"}:
        raise PermissionDenied(_("Management users cannot start a student exam attempt."))
    expired_snapshot = False
    with transaction.atomic():
        quiz = Quiz.objects.select_for_update(of=("self",)).select_related(
            "course_offering",
        ).filter(pk=quiz_id).first()
        current = timezone.now()
        if (
            quiz is None
            or quiz.status != PublicationStatus.PUBLISHED
            or not is_quiz_in_user_window(quiz, user)
        ):
            raise PermissionDenied(_("You do not have access to start this quiz."))
        opening, closing = quiz_window(quiz, user)
        if current < ensure_aware(opening):
            raise PermissionDenied(_("This quiz is not open yet."))
        if not quiz_window_is_open(opening, closing, current):
            raise PermissionDenied(_("This quiz is closed."))

        if Grade.objects.filter(quiz_id=quiz.pk, user=user).exists():
            raise PermissionDenied(_("This quiz has already been submitted."))
        snapshot = QuizAnswerSnapshot.objects.filter(quiz_id=quiz.pk, user=user).first()
        if snapshot is not None:
            if snapshot.deadline_at <= current:
                expired_snapshot = True
            else:
                return snapshot
        else:
            closing = ensure_aware(closing)
            deadline = min(current + timedelta(minutes=quiz.duration_minutes), closing)
            snapshot = QuizAnswerSnapshot.objects.create(
                quiz_id=quiz.pk,
                user=user,
                answers={},
                revision=1,
                deadline_at=deadline,
            )

    if expired_snapshot:
        reconcile_snapshots(quiz.pk, user_id=user.pk)
        raise PermissionDenied(_("The time allowed for this quiz has expired."))
    return snapshot


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
    quiz = Quiz.objects.get(pk=quiz_id)
    grade = Grade.objects.filter(quiz=quiz, user=user).first()
    if grade:
        QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).delete()
        return grade
    snapshot = QuizAnswerSnapshot.objects.select_for_update().filter(quiz=quiz, user=user).first()
    if snapshot is None:
        grade = Grade.objects.filter(quiz=quiz, user=user).first()
        if grade:
            return grade
        raise PermissionDenied(_("Start the quiz before submitting answers."))
    current_time = timezone.now()
    if snapshot.deadline_at <= current_time:
        reconcile_snapshots(quiz.pk, user_id=user.pk)
        grade = Grade.objects.filter(quiz=quiz, user=user).first()
        if grade:
            return grade
        raise PermissionDenied(_("The time allowed for this quiz has expired."))
    if quiz.status != PublicationStatus.PUBLISHED or not is_quiz_in_user_window(quiz, user):
        raise PermissionDenied(_("Invalid Request, submission is closed!"))
    opening, closing = quiz_window(quiz, user)
    if not quiz_window_is_open(opening, closing, current_time):
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
    quiz = Quiz.objects.select_related("course_offering").get(pk=quiz_id)
    if Grade.objects.filter(quiz=quiz, user=user).exists():
        QuizAnswerSnapshot.objects.filter(quiz=quiz, user=user).delete()
        return None
    snapshot = QuizAnswerSnapshot.objects.select_for_update().filter(quiz=quiz, user=user).first()
    current_time = timezone.now()
    opening, closing = quiz_window(quiz, user)
    if (
        snapshot is None
        or snapshot.deadline_at <= current_time
        or quiz.status != PublicationStatus.PUBLISHED
        or not is_quiz_in_user_window(quiz, user)
        or not quiz_window_is_open(opening, closing, current_time)
    ):
        raise PermissionDenied(_("Invalid Request, submission is closed!"))
    answers = normalize_answers(quiz, answers)
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
        raise PermissionDenied(_("Start the quiz before saving answers."))
    return snapshot


def due_snapshots() -> QuerySet[QuizAnswerSnapshot]:
    cutoff = timezone.now()
    return QuizAnswerSnapshot.objects.annotate(
        has_grade=Exists(Grade.objects.filter(quiz_id=OuterRef("quiz_id"), user_id=OuterRef("user_id"))),
    ).filter(deadline_at__lte=cutoff)


@transaction.atomic
def reconcile_snapshots(quiz_id: int, *, user_id: int | None = None, batch_size: int = 100) -> tuple[int, bool]:
    """Finalize only saved answers whose server-owned attempt deadline has passed.

    Accepted drafts retain their authority after enrollment/year archival. Current
    student input is never consulted by this recovery path.
    """
    quiz = Quiz.objects.get(pk=quiz_id)
    rows = due_snapshots().filter(quiz=quiz)
    if user_id is not None:
        rows = rows.filter(user_id=user_id)
    snapshots = list(rows.select_for_update(of=("self",)).order_by("pk")[:batch_size])
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


def quiz_snapshot_result(*, quiz: Quiz, user, payload: object) -> dict[str, int | bool]:
    """Apply a validated web or mobile snapshot command and return its result."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid quiz answers.")
    if payload.get("reconcile") is True:
        reconcile_snapshots(quiz.pk, user_id=user.pk)
        submitted = Grade.objects.filter(quiz=quiz, user=user).exists()
        return {"submitted": submitted}

    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("Invalid quiz answers.")
    snapshot = save_snapshot(quiz.pk, user, payload.get("answers"), revision)
    return {
        "submitted": snapshot is None,
        "revision": snapshot.revision if snapshot else 0,
    }
