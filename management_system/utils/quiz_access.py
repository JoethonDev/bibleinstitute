from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils.translation import gettext_lazy as _

from ..models import Enrollment, Grade, Quiz, QuizStudentOpening, User
from .timezones import ensure_aware


def eligible_quiz_students(quiz):
    normal_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        academic_year_level_id=quiz.course_offering.academic_year_level_id,
        enrollment_type=Enrollment.Type.NORMAL,
        status=Enrollment.Status.ACTIVE,
    )
    targeted_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        course_offering_id=quiz.course_offering_id,
        enrollment_type__in=(Enrollment.Type.REPEAT, Enrollment.Type.REMEDIAL, Enrollment.Type.MANUAL),
        status=Enrollment.Status.ACTIVE,
    )
    return User.objects.filter(
        role__role="student",
        is_active=True,
        application_status="active",
    ).filter(
        Exists(normal_access) | Exists(targeted_access)
    ).exclude(
        submitted_quizzes__quiz=quiz,
    ).order_by("last_name", "first_name", "username")


def quiz_window(quiz, user=None):
    if user is not None:
        opening = QuizStudentOpening.objects.filter(quiz=quiz, student=user).first()
        if opening:
            return opening.opening_date, opening.closing_date
    return quiz.opening_date, quiz.closing_date


def quiz_window_is_open(opening_date: datetime, closing_date: datetime, current_time: datetime) -> bool:
    current_time = ensure_aware(current_time)
    return ensure_aware(opening_date) <= current_time < ensure_aware(closing_date)


def quiz_status_from(quiz, grade, opening, current_time, can_write):
    """Canonical quiz-mode decision for one quiz with grade/opening pre-loaded.

    The single-quiz reader, the batched reader, and the mobile reader all call
    this function, so a page cannot drift from another page.
    """
    if grade is not None:
        return "view", grade

    opening_date = opening.opening_date if opening is not None else quiz.opening_date
    closing_date = opening.closing_date if opening is not None else quiz.closing_date
    if current_time < ensure_aware(opening_date):
        return "upcoming", None
    if not can_write:
        return "closed_unsolved", None
    if quiz_window_is_open(opening_date, closing_date, current_time):
        return "exam", None

    return "closed_unsolved", None


def quiz_attempt_status_from(quiz, grade, opening, current_time, can_write, snapshot=None):
    """Resolve one student-facing state, including the server-owned attempt."""
    mode, grade = quiz_status_from(quiz, grade, opening, current_time, can_write)
    if grade is not None or not can_write or mode != "exam":
        return mode, grade
    if snapshot is not None:
        if ensure_aware(snapshot.deadline_at) <= ensure_aware(current_time):
            return "closed_unsolved", None
        return "exam", None
    if mode == "exam":
        return "ready", None
    return mode, None


def answers_releasable(grade: Grade | None) -> bool:
    """Canonical answer-release rule: solved students see the correct answers.

    Answers are released as soon as this student has a submitted grade, even
    while the quiz is still open for peers. A closed-and-unsolved student never
    receives answers (web renders ``serialize_student`` and the mobile payload
    omits ``correct_answer``). Management preview/correction callers bypass this
    rule and must gate separately.
    """
    return grade is not None


@transaction.atomic
def grant_quiz_openings(quiz, students, opening_date, closing_date, granted_by) -> int:
    # Attempt creation locks this same row before reading per-student windows.
    quiz = Quiz.objects.select_for_update(of=("self",)).select_related(
        "course_offering",
    ).get(pk=quiz.pk)
    if closing_date <= opening_date:
        raise ValidationError({"closing_date": _("Closing date must be after opening date.")})

    student_ids = list(students.values_list("pk", flat=True))
    eligible_ids = set(eligible_quiz_students(quiz).filter(pk__in=student_ids).values_list("pk", flat=True))
    if eligible_ids != set(student_ids):
        raise ValidationError(_("Every selected student must be actively enrolled in this quiz's course scope."))

    existing = {
        opening.student_id: opening
        for opening in QuizStudentOpening.objects.filter(quiz=quiz, student_id__in=student_ids)
    }
    to_create = []
    to_update = []
    for student_id in student_ids:
        opening = existing.get(student_id)
        if opening is None:
            to_create.append(QuizStudentOpening(
                quiz=quiz,
                student_id=student_id,
                opening_date=opening_date,
                closing_date=closing_date,
                granted_by=granted_by,
            ))
        else:
            opening.opening_date = opening_date
            opening.closing_date = closing_date
            opening.granted_by = granted_by
            to_update.append(opening)
    if to_create:
        QuizStudentOpening.objects.bulk_create(to_create)
    if to_update:
        QuizStudentOpening.objects.bulk_update(to_update, ["opening_date", "closing_date", "granted_by"])
    return len(student_ids)
