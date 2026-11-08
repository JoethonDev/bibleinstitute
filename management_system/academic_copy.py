from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from .models import (
    AcademicYearLevel,
    AdminOperationJob,
    Course,
    CourseOffering,
    Lesson,
    PublicationStatus,
    Question,
    Quiz,
    User,
)
from .utils.decorators import can_manage_academic_setup, can_manage_content

from .admin_operations import AdminOperationError, assert_admin_operation_progress, require_admin_operation_actor



def _require_admin(actor: User) -> None:
    if not getattr(actor, "is_authenticated", False) or not can_manage_academic_setup(actor):
        raise PermissionDenied(_("Only administrators can copy academic offerings."))


def _require_content_manager(actor: User) -> None:
    if not getattr(actor, "is_authenticated", False) or not can_manage_content(actor):
        raise PermissionDenied(_("Only content managers can duplicate a course."))


def _frozen_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source_offerings(offering_ids: list[int], source_scope: AcademicYearLevel, *, copy_lessons: bool, copy_quizzes: bool):
    """Load the selected source offerings with only the requested children."""

    queryset = (
        CourseOffering.objects.select_for_update()
        .filter(academic_year_level=source_scope, pk__in=offering_ids)
        .select_related("course")
    )
    prefetches = []
    if copy_lessons:
        prefetches.append("lessons")
    if copy_quizzes:
        prefetches.append("quizzes__questions")
    if prefetches:
        queryset = queryset.prefetch_related(*prefetches)
    selected = list(queryset.order_by("pk"))
    if len(selected) != len(set(offering_ids)):
        raise ValidationError(_("One or more selected offerings do not belong to the source scope."))
    if any(offering.course.level_id != source_scope.level_id for offering in selected):
        raise ValidationError(_("A selected offering does not match the source scope level."))
    return selected


def _copy_offering_children(
    source_offering: CourseOffering,
    new_offering: CourseOffering,
    *,
    copy_lessons: bool,
    copy_quizzes: bool,
) -> dict:
    """Copy the requested child content as drafts and return bounded counts."""

    counts = {"lessons": 0, "quizzes": 0, "questions": 0}
    if copy_lessons:
        lessons = [
            Lesson(
                name=lesson.name,
                description=lesson.description,
                links=lesson.links,
                course_offering=new_offering,
                status=PublicationStatus.DRAFT,
                opening_date=lesson.opening_date,
                created_date=lesson.created_date,
            )
            for lesson in source_offering.lessons.all()
        ]
        if lessons:
            Lesson.objects.bulk_create(lessons, batch_size=500)
        counts["lessons"] = len(lessons)

    if copy_quizzes:
        quiz_specs = []
        for quiz in source_offering.quizzes.all():
            quiz_specs.append((
                quiz,
                Quiz(
                    name=quiz.name,
                    # Only the key is copied; the prefetched quiz type object is
                    # never dereferenced, so no per-quiz relation read is needed.
                    quiz_type_id=quiz.quiz_type_id,
                    course_offering=new_offering,
                    status=PublicationStatus.DRAFT,
                    total_grade=quiz.total_grade,
                    opening_date=quiz.opening_date,
                    closing_date=quiz.closing_date,
                    duration_minutes=quiz.duration_minutes,
                ),
            ))
        if quiz_specs:
            # bulk_create skips Quiz.save()'s full_clean; this copy path already
            # validates the source scope and copies an existing valid quiz.
            Quiz.objects.bulk_create([new_quiz for _, new_quiz in quiz_specs], batch_size=500)
        for quiz, new_quiz in quiz_specs:
            questions = [
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
            if questions:
                Question.objects.bulk_create(questions, batch_size=500)
            counts["quizzes"] += 1
            counts["questions"] += len(questions)
    return counts


@transaction.atomic
def copy_offerings(
    source_scope: AcademicYearLevel,
    target_scope: AcademicYearLevel,
    offering_ids: list[int],
    *,
    copy_lessons: bool,
    copy_quizzes: bool,
    actor: User,
) -> dict:
    """Copy selected offering content into a matching target scope as drafts.

    The whole selection is one all-or-nothing copy unit: prefetches are built
    from the requested flags only, and each selected offering's course keeps
    its existing target-scope conflict rejection.
    """

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
    if not copy_lessons and not copy_quizzes:
        raise ValidationError(_("Select lessons, quizzes, or both to copy."))

    selected = _source_offerings(
        offering_ids,
        source_scope,
        copy_lessons=copy_lessons,
        copy_quizzes=copy_quizzes,
    )
    existing_courses = set(
        CourseOffering.objects.filter(
            academic_year_level=target_scope,
            course_id__in=[offering.course_id for offering in selected],
        ).values_list("course_id", flat=True)
    )
    if existing_courses:
        raise ValidationError(_("The target scope already contains one or more selected courses."))

    copied = []
    counts = {"lessons": 0, "quizzes": 0, "questions": 0}
    for offering in selected:
        new_offering = CourseOffering.objects.create(
            course=offering.course,
            academic_year_level=target_scope,
            instructor=offering.instructor,
            status=PublicationStatus.DRAFT,
        )
        copied.append(new_offering)
        child_counts = _copy_offering_children(
            offering,
            new_offering,
            copy_lessons=copy_lessons,
            copy_quizzes=copy_quizzes,
        )
        for key, value in child_counts.items():
            counts[key] += value
    return {
        "offerings": [offering.pk for offering in copied],
        "copied": len(copied),
        **counts,
    }


@transaction.atomic
def duplicate_offering(
    *,
    source_offering_id: int,
    target_scope: AcademicYearLevel,
    new_course_name: str,
    copy_lessons: bool,
    copy_quizzes: bool,
    actor: User,
) -> dict:
    """Duplicate exactly one explicit source offering into a valid target scope.

    This never merges content from other historical offerings of the same
    catalogue course: only the requested source offering's lessons/quizzes are
    copied, as drafts, into a new catalogue course and offering.
    """

    _require_content_manager(actor)
    name = (new_course_name or "").strip()
    if not name:
        raise ValidationError(_("New course name is required."))
    if len(name) > 255:
        raise ValidationError(_("New course name is too long."))
    if not copy_lessons and not copy_quizzes:
        raise ValidationError(_("Select lessons, quizzes, or both to copy."))
    target = (
        AcademicYearLevel.objects.select_for_update()
        .filter(pk=target_scope.pk)
        .select_related("level", "academic_year")
        .first()
    )
    if target is None:
        raise ValidationError(_("The selected academic scope no longer exists."))
    if not target.academic_year.is_active:
        raise ValidationError(_("Offerings must be copied into the active academic year."))
    source = (
        CourseOffering.objects.select_for_update()
        .filter(pk=source_offering_id)
        .select_related("course")
        .first()
    )
    if source is None:
        raise ValidationError(_("The source offering no longer exists."))
    if source.course.level_id != target.level_id:
        raise ValidationError(_("The source offering level does not match the target academic scope."))
    if Course.objects.filter(name=name, level=target.level).exists():
        raise ValidationError(_("A course with this name already exists in the target level."))

    new_course = Course.objects.create(
        name=name,
        description=source.course.description,
        instructor=source.course.instructor,
        level=target.level,
    )
    new_offering = CourseOffering.objects.create(
        course=new_course,
        academic_year_level=target,
        instructor=source.instructor,
        status=PublicationStatus.DRAFT,
    )
    counts = _copy_offering_children(
        source,
        new_offering,
        copy_lessons=copy_lessons,
        copy_quizzes=copy_quizzes,
    )
    return {
        "offerings": [new_offering.pk],
        "copied": 1,
        "course_id": new_course.pk,
        **counts,
    }


def execute_course_copy(job, token) -> dict:
    """J01 executor: one all-or-nothing copy unit, then progress in the same commit."""


    require_admin_operation_actor(job)
    if (job.cursor or {}).get("index") and (job.result or {}).get("copied"):
        assert_admin_operation_progress(
            job.pk, token, processed=job.processed, succeeded=job.succeeded,
            failed=job.failed, skipped=job.skipped, cursor=job.cursor, result=job.result,
        )
        return {"status": AdminOperationJob.Status.COMPLETED, "result": job.result}
    payload = job.input or {}
    mode = str(payload.get("mode") or "")
    actor = job.actor
    try:
        if mode == "offerings":
            if not can_manage_academic_setup(actor):
                raise AdminOperationError(
                    "not_permitted",
                    _("You are no longer allowed to copy academic offerings."),
                )
            source_scope = AcademicYearLevel.objects.filter(
                pk=payload.get("source_scope_id")
            ).first()
            target_scope = AcademicYearLevel.objects.filter(
                pk=payload.get("target_scope_id")
            ).first()
            if source_scope is None or target_scope is None:
                raise AdminOperationError(
                    "scope_missing",
                    _("The selected academic scope no longer exists."),
                )
            offering_ids = payload.get("offering_ids")
            if not isinstance(offering_ids, list) or not offering_ids or len(offering_ids) > 200:
                raise AdminOperationError(
                    "invalid_selection",
                    _("Select between one and 200 offerings to copy."),
                )
            try:
                frozen_ids = [int(value) for value in offering_ids]
            except (TypeError, ValueError) as exc:
                raise AdminOperationError(
                    "invalid_selection",
                    _("Select between one and 200 offerings to copy."),
                ) from exc
            with transaction.atomic():
                outcome = copy_offerings(
                    source_scope,
                    target_scope,
                    frozen_ids,
                    copy_lessons=bool(payload.get("copy_lessons")),
                    copy_quizzes=bool(payload.get("copy_quizzes")),
                    actor=actor,
                )
                assert_admin_operation_progress(
                    job.pk,
                    token,
                    processed=outcome["copied"],
                    succeeded=outcome["copied"],
                    failed=0,
                    skipped=0,
                    total=outcome["copied"],
                    cursor={"index": outcome["copied"]},
                    result=outcome,
                )
        elif mode == "duplicate":
            if not can_manage_content(actor):
                raise AdminOperationError(
                    "not_permitted",
                    _("You are no longer allowed to duplicate a course."),
                )
            target_scope = AcademicYearLevel.objects.filter(
                pk=payload.get("target_scope_id")
            ).first()
            if target_scope is None:
                raise AdminOperationError(
                    "scope_missing",
                    _("The selected academic scope no longer exists."),
                )
            with transaction.atomic():
                outcome = duplicate_offering(
                    source_offering_id=_frozen_int(payload.get("source_offering_id")),
                    target_scope=target_scope,
                    new_course_name=str(payload.get("new_course_name") or ""),
                    copy_lessons=bool(payload.get("copy_lessons")),
                    copy_quizzes=bool(payload.get("copy_quizzes")),
                    actor=actor,
                )
                assert_admin_operation_progress(
                    job.pk,
                    token,
                    processed=1,
                    succeeded=1,
                    failed=0,
                    skipped=0,
                    total=1,
                    cursor={"index": 1},
                    result=outcome,
                )
        else:
            raise AdminOperationError(
                "invalid_mode",
                _("The frozen copy request is not valid."),
            )
    except PermissionDenied as exc:
        raise AdminOperationError("not_permitted", str(exc)) from exc
    except (ValidationError, IntegrityError) as exc:
        message = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else _("The target scope already contains conflicting content.")
        )
        raise AdminOperationError("copy_conflict", message) from exc

    return {"status": AdminOperationJob.Status.COMPLETED, "result": outcome}
