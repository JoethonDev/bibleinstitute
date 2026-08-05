from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef
from django.utils.translation import gettext_lazy as _

from ..models import Enrollment, QuizStudentOpening, User


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


def grant_quiz_openings(quiz, students, opening_date, closing_date, granted_by) -> int:
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
