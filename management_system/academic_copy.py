from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import AcademicYearLevel, CourseOffering, Lesson, PublicationStatus, Question, Quiz, User


def _require_admin(actor: User) -> None:
    if not actor.is_authenticated or not actor.role or actor.role.role != "admin":
        raise PermissionDenied(_("Only administrators can copy academic offerings."))


@transaction.atomic
def copy_offerings(
    source_scope: AcademicYearLevel,
    target_scope: AcademicYearLevel,
    offering_ids: list[int],
    *,
    copy_lessons: bool,
    copy_quizzes: bool,
    actor: User,
) -> list[CourseOffering]:
    """Copy selected offering content into a matching target scope as drafts."""
    _require_admin(actor)
    if source_scope.pk == target_scope.pk:
        raise ValidationError(_("Source and target academic scopes must be different."))
    locked_scopes = list(
        AcademicYearLevel.objects.select_for_update()
        .filter(pk__in=[source_scope.pk, target_scope.pk])
        .select_related("level", "academic_year")
        .order_by("pk")
    )
    scopes_by_id = {scope.pk: scope for scope in locked_scopes}
    source_scope = scopes_by_id.get(source_scope.pk)
    target_scope = scopes_by_id.get(target_scope.pk)
    if source_scope is None or target_scope is None:
        raise ValidationError(_("The selected academic scope no longer exists."))
    if source_scope.academic_year.is_active:
        raise ValidationError(_("Offerings must be copied from an inactive academic year."))
    if not target_scope.academic_year.is_active:
        raise ValidationError(_("Offerings must be copied into the active academic year."))
    if source_scope.level_id != target_scope.level_id:
        raise ValidationError(_("Source and target levels must match."))
    if not offering_ids:
        raise ValidationError(_("Select at least one offering to copy."))

    selected = list(
        CourseOffering.objects.select_for_update()
        .filter(academic_year_level=source_scope, pk__in=offering_ids)
        .select_related("course")
        .prefetch_related("lessons", "quizzes__questions")
        .order_by("pk")
    )
    if len(selected) != len(set(offering_ids)):
        raise ValidationError(_("One or more selected offerings do not belong to the source scope."))
    if any(offering.course.level_id != source_scope.level_id for offering in selected):
        raise ValidationError(_("A selected offering does not match the source scope level."))

    existing_courses = set(
        CourseOffering.objects.filter(
            academic_year_level=target_scope,
            course_id__in=[offering.course_id for offering in selected],
        ).values_list("course_id", flat=True)
    )
    if existing_courses:
        raise ValidationError(_("The target scope already contains one or more selected courses."))

    copied = []
    for offering in selected:
        new_offering = CourseOffering.objects.create(
            course=offering.course,
            academic_year_level=target_scope,
            instructor=offering.instructor,
            status=PublicationStatus.DRAFT,
        )
        copied.append(new_offering)

        if copy_lessons:
            Lesson.objects.bulk_create(
                [
                    Lesson(
                        name=lesson.name,
                        description=lesson.description,
                        links=lesson.links,
                        course_offering=new_offering,
                        status=PublicationStatus.DRAFT,
                        created_date=lesson.created_date,
                    )
                    for lesson in offering.lessons.all()
                ]
            )

        if copy_quizzes:
            for quiz in offering.quizzes.all():
                new_quiz = Quiz.objects.create(
                    name=quiz.name,
                    quiz_type=quiz.quiz_type,
                    course_offering=new_offering,
                    status=PublicationStatus.DRAFT,
                    total_grade=quiz.total_grade,
                    opening_date=quiz.opening_date,
                    closing_date=quiz.closing_date,
                )
                Question.objects.bulk_create(
                    [
                        Question(
                            quiz=new_quiz,
                            title=question.title,
                            correct_answer=question.correct_answer,
                            question_type=question.question_type,
                            choices=question.choices,
                            config=question.config,
                            grade=question.grade,
                            auto_grade=question.auto_grade,
                        )
                        for question in quiz.questions.all()
                    ]
                )
    return copied
