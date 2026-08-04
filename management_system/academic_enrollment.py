from __future__ import annotations

from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.timezone import now
from django.utils.translation import gettext as _

from .models import (
    AcademicYear,
    AcademicYearLevel,
    CourseOffering,
    Enrollment,
    EvaluationResult,
    Level,
    PromotionHistory,
    Role,
    User,
)


def _require_admin(actor: User) -> None:
    if not actor.is_authenticated or not actor.role or actor.role.role != "admin":
        raise PermissionDenied(_("Only administrators can change academic enrollment state."))


def _promotion_formula_snapshot(result: EvaluationResult, course_results=None) -> dict:
    formula = result.formula
    return {
        "formula_id": formula.pk,
        "course_offering_id": formula.course_offering_id,
        "overall_pass_percent": str(formula.overall_pass_percent),
        "evaluation_starts_on": formula.evaluation_starts_on.isoformat(),
        "evaluation_ends_on": formula.evaluation_ends_on.isoformat(),
        "failed_courses_repeat_threshold": formula.failed_courses_repeat_threshold,
        "rules": [
            {
                "metric": rule.metric,
                "quiz_type_id": rule.quiz_type_id,
                "weight_percent": str(rule.weight_percent),
                "minimum_percent": str(rule.minimum_percent),
                "ordering": rule.ordering,
            }
            for rule in formula.rules.order_by("ordering")
        ],
        "metric_snapshot": result.metric_snapshot,
        "course_results": [
            {
                "result_id": course_result.pk,
                "course_offering_id": course_result.course_offering_id,
                "course_name": course_result.course_offering.course.name,
                "computed_score": str(course_result.computed_score),
                "computed_status": course_result.computed_status,
                "final_status": course_result.final_status,
                "metric_snapshot": course_result.metric_snapshot,
            }
            for course_result in (course_results or [])
        ],
    }


def _course_results_for(result: EvaluationResult, lock=False):
    queryset = EvaluationResult.objects.filter(
        formula_id=result.formula_id,
        enrollment_id=result.enrollment_id,
        course_offering__isnull=False,
    ).select_related("course_offering__course")
    return list(queryset.select_for_update() if lock else queryset)


def _promotion_failure_state(result: EvaluationResult, course_results):
    if not course_results:
        raise ValidationError(_("No course-level evaluation results exist for this student."))
    unevaluable = [
        course_result for course_result in course_results
        if course_result.final_status == EvaluationResult.Status.UNEVALUABLE
    ]
    if unevaluable:
        raise ValidationError(_("Unevaluable courses must be resolved before promotion."))
    failed = [
        course_result for course_result in course_results
        if course_result.final_status == EvaluationResult.Status.FAIL
    ]
    return failed


def _destination_scope(source_scope, destination_year, level_ordering):
    return AcademicYearLevel.objects.select_related("level").filter(
        academic_year=destination_year,
        level__ordering=level_ordering,
    ).first()


def _failed_destination_offerings(source_scope, destination_scope, failed_results):
    course_ids = [result.course_offering.course_id for result in failed_results]
    offerings = list(CourseOffering.objects.select_for_update().select_related("course").filter(
        academic_year_level=destination_scope,
        course_id__in=course_ids,
    ))
    by_course = {offering.course_id: offering for offering in offerings}
    missing = sorted(set(course_ids) - set(by_course))
    if missing:
        raise ValidationError(
            _("Destination offerings are missing for failed course IDs: %(ids)s.")
            % {"ids": ", ".join(str(course_id) for course_id in missing)}
        )
    return [by_course[course_id] for course_id in course_ids]


@transaction.atomic
def activate_academic_year(year: AcademicYear, actor: User) -> AcademicYear:
    """Activate one academic year and materialize archived last-level access."""
    _require_admin(actor)
    locked_years = list(AcademicYear.objects.select_for_update().order_by("pk"))
    target = next((locked for locked in locked_years if locked.pk == year.pk), None)
    if target is None:
        raise ValidationError(_("Academic year does not exist."))

    AcademicYear.objects.filter(is_active=True).update(is_active=False)
    target.is_active = True
    target.save(update_fields=["is_active"])
    _materialize_last_level_exceptional_access(target, actor)
    return target


@transaction.atomic
def accept_application(application: User, actor: User) -> tuple[User, Enrollment]:
    """Accept one pending application into the lowest level of the active year."""
    _require_admin(actor)
    locked_user = User.objects.select_for_update().get(pk=application.pk)
    if locked_user.application_status != "pending":
        raise ValidationError(
            _("Cannot accept a %(status)s application.")
            % {"status": locked_user.application_status}
        )

    active_years = list(AcademicYear.objects.select_for_update().filter(is_active=True).order_by("pk"))
    if len(active_years) != 1:
        raise ValidationError(_("Exactly one active academic year is required before acceptance."))
    active_year = active_years[0]
    scope = (
        AcademicYearLevel.objects.select_related("level")
        .filter(academic_year=active_year)
        .order_by("level__ordering", "pk")
        .first()
    )
    if scope is None:
        raise ValidationError(_("The active academic year has no opened level."))

    student_role = Role.objects.get(role="student")
    locked_user.application_status = "active"
    locked_user.is_active = True
    locked_user.role = student_role
    locked_user.decided_by = actor
    locked_user.decided_at = now()
    locked_user.save(update_fields=["application_status", "is_active", "role", "decided_by", "decided_at"])

    enrollment, _ = Enrollment.objects.get_or_create(
        student=locked_user,
        academic_year_level=scope,
        course_offering=None,
        defaults={"enrollment_type": Enrollment.Type.NORMAL, "status": Enrollment.Status.ACTIVE, "enrolled_by": actor},
    )
    if enrollment.enrollment_type != Enrollment.Type.NORMAL or enrollment.course_offering_id:
        raise ValidationError(_("The application already has an incompatible enrollment."))
    if enrollment.status != Enrollment.Status.ACTIVE or enrollment.enrolled_by_id is None:
        enrollment.status = Enrollment.Status.ACTIVE
        enrollment.enrolled_by = enrollment.enrolled_by or actor
        enrollment.save(update_fields=["status", "enrolled_by"])
    return locked_user, enrollment


@transaction.atomic
def decline_application(application: User, actor: User) -> User:
    """Decline one pending application under the same locking rules as acceptance."""
    _require_admin(actor)
    locked_user = User.objects.select_for_update().get(pk=application.pk)
    if locked_user.application_status != "pending":
        raise ValidationError(
            _("Cannot decline a %(status)s application.")
            % {"status": locked_user.application_status}
        )
    locked_user.application_status = "declined"
    locked_user.is_active = False
    locked_user.decided_by = actor
    locked_user.decided_at = now()
    locked_user.save(update_fields=["application_status", "is_active", "decided_by", "decided_at"])
    return locked_user


@transaction.atomic
def promote_evaluation_result(*, result_id: int, actor: User) -> PromotionHistory:
    """Consume an aggregate result and apply course-aware promotion rules."""
    _require_admin(actor)
    EvaluationResult.objects.select_for_update().get(pk=result_id)
    result = EvaluationResult.objects.select_related(
        "formula__academic_year_level__academic_year",
        "formula__academic_year_level__level",
        "enrollment__student",
        "enrollment__academic_year_level__academic_year",
        "enrollment__academic_year_level__level",
    ).get(pk=result_id)
    if result.course_offering_id:
        raise ValidationError(_("Only the aggregate evaluation result can be promoted."))
    existing_history_id = PromotionHistory.objects.filter(evaluation_result_id=result_id).values_list("pk", flat=True).first()
    if existing_history_id:
        raise ValidationError(_("This evaluation result has already been processed."))

    source = Enrollment.objects.select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level"
    ).select_for_update().get(pk=result.enrollment_id)
    if result.formula.academic_year_level_id != source.academic_year_level_id:
        raise ValidationError(_("The evaluation formula and enrollment scopes do not match."))
    if result.formula.course_offering_id:
        raise ValidationError(_("An offering-specific formula cannot make an aggregate promotion decision."))
    if source.enrollment_type != Enrollment.Type.NORMAL or source.course_offering_id:
        raise ValidationError(_("Only a normal enrollment can be promoted."))
    if source.status not in (Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED):
        raise ValidationError(_("Only active or completed enrollments can be promoted."))

    course_results = _course_results_for(result, lock=True)
    failed_results = _promotion_failure_state(result, course_results)
    source_scope = source.academic_year_level
    source_year = source_scope.academic_year
    active_years = list(AcademicYear.objects.select_for_update().filter(is_active=True).order_by("ordering", "pk"))
    if len(active_years) != 1 or active_years[0].ordering <= source_year.ordering:
        raise ValidationError(_("Activate a higher academic year before promotion."))
    destination_year = active_years[0]
    is_last_level = not Level.objects.filter(ordering__gt=source_scope.level.ordering).exists()
    passed = not failed_results
    threshold = result.formula.failed_courses_repeat_threshold
    partial_failure = bool(failed_results) and len(failed_results) < threshold

    destination_scope = None
    create_normal_destination = False
    if passed:
        if is_last_level:
            outcome = "graduated"
        else:
            destination_scope = _destination_scope(source_scope, destination_year, source_scope.level.ordering + 1)
            create_normal_destination = True
            outcome = "passed"
    elif is_last_level and partial_failure:
        destination_scope = _destination_scope(source_scope, destination_year, source_scope.level.ordering)
        outcome = "last_level_exceptional"
    else:
        destination_scope = _destination_scope(
            source_scope,
            destination_year,
            source_scope.level.ordering if not partial_failure else source_scope.level.ordering + 1,
        )
        create_normal_destination = True
        outcome = "repeated" if not partial_failure else "passed_with_exceptions"

    if destination_scope is None and outcome != "graduated":
        raise ValidationError(_("The destination academic level is not open in the active year."))

    exceptional_offerings = []
    if failed_results and partial_failure:
        exceptional_offerings = _failed_destination_offerings(source_scope, destination_scope, failed_results)

    destination = None
    if create_normal_destination:
        if Enrollment.objects.filter(
            student=source.student,
            academic_year_level=destination_scope,
            course_offering__isnull=True,
        ).exists():
            raise ValidationError(_("A destination normal enrollment already exists for this student."))
        destination = Enrollment.objects.create(
            student=source.student,
            academic_year_level=destination_scope,
            enrollment_type=Enrollment.Type.NORMAL,
            status=Enrollment.Status.ACTIVE,
            enrolled_by=actor,
        )

    created_exceptional = []
    for offering in exceptional_offerings:
        enrollment, _ = Enrollment.objects.get_or_create(
            student=source.student,
            course_offering=offering,
            defaults={
                "academic_year_level": offering.academic_year_level,
                "enrollment_type": Enrollment.Type.REPEAT,
                "status": Enrollment.Status.ACTIVE,
                "enrolled_by": actor,
            },
        )
        if enrollment.academic_year_level_id != offering.academic_year_level_id:
            raise ValidationError(_("Exceptional enrollment scope does not match its offering."))
        created_exceptional.append(enrollment)

    source.status = Enrollment.Status.COMPLETED
    source.save(update_fields=["status"])
    return PromotionHistory.objects.create(
        evaluation_result=result,
        source_enrollment=source,
        destination_enrollment=destination,
        student=source.student,
        source_year_level=source_scope,
        destination_year_level=destination_scope,
        outcome=outcome,
        score=result.computed_score,
        computed_status=result.computed_status,
        final_status=result.final_status,
        override_note=result.override_note,
        override_actor_id=result.overridden_by_id,
        exceptional_offering_ids=[enrollment.course_offering_id for enrollment in created_exceptional],
        formula_snapshot=_promotion_formula_snapshot(result, course_results),
        actor=actor,
    )


def promote_evaluation_results(*, result_ids: list[int], actor: User) -> tuple[list[PromotionHistory], list[dict]]:
    """Process at most 1,000 aggregate results with isolated transactions."""
    _require_admin(actor)
    if len(result_ids) > 1000:
        raise ValidationError(_("Select no more than 1,000 students at a time."))
    histories, errors = [], []
    for result_id in dict.fromkeys(result_ids):
        try:
            histories.append(promote_evaluation_result(result_id=result_id, actor=actor))
        except (EvaluationResult.DoesNotExist, ValidationError, PermissionDenied) as exc:
            errors.append({"result_id": result_id, "message": str(exc)})
    return histories, errors


def _materialize_last_level_exceptional_access(destination_year: AcademicYear, actor: User) -> int:
    """Create failed-course-only access for archived last-level partial failures."""
    last_level = Level.objects.order_by("-ordering").first()
    if last_level is None:
        return 0
    candidates = EvaluationResult.objects.select_for_update().select_related(
        "formula", "enrollment", "enrollment__student", "enrollment__academic_year_level"
    ).filter(
        course_offering__isnull=True,
        promotion_history__isnull=True,
        formula__course_offering__isnull=True,
        formula__academic_year_level__level=last_level,
        formula__academic_year_level__academic_year__ordering__lt=destination_year.ordering,
        enrollment__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    ).order_by("pk")
    created_count = 0
    target_scope = AcademicYearLevel.objects.filter(
        academic_year=destination_year,
        level=last_level,
    ).first()
    if target_scope is None:
        if candidates.exists():
            raise ValidationError(_("The next academic year must open the last level before failed-course access can be created."))
        return 0
    for candidate_batch_start in range(0, candidates.count(), 500):
        candidate_batch = list(candidates[candidate_batch_start:candidate_batch_start + 500])
        if not candidate_batch:
            break
        enrollment_ids = [result.enrollment_id for result in candidate_batch]
        formula_ids = [result.formula_id for result in candidate_batch]
        course_results = EvaluationResult.objects.filter(
            formula_id__in=formula_ids,
            enrollment_id__in=enrollment_ids,
            course_offering__isnull=False,
        ).select_related("course_offering__course")
        grouped = defaultdict(list)
        for course_result in course_results:
            grouped[(course_result.formula_id, course_result.enrollment_id)].append(course_result)

        eligible = []
        course_ids = set()
        for result in candidate_batch:
            rows = grouped[(result.formula_id, result.enrollment_id)]
            if not rows or any(row.final_status == EvaluationResult.Status.UNEVALUABLE for row in rows):
                continue
            failed_rows = [row for row in rows if row.final_status == EvaluationResult.Status.FAIL]
            if not failed_rows or len(failed_rows) >= result.formula.failed_courses_repeat_threshold:
                continue
            eligible.append((result, rows, failed_rows))
            course_ids.update(row.course_offering.course_id for row in failed_rows)
        if not eligible:
            continue

        destination_offerings = {
            offering.course_id: offering
            for offering in CourseOffering.objects.filter(
                academic_year_level=target_scope,
                course_id__in=course_ids,
            )
        }
        missing = course_ids - set(destination_offerings)
        if missing:
            raise ValidationError(
                _("Destination offerings are missing for failed course IDs: %(ids)s.")
                % {"ids": ", ".join(str(course_id) for course_id in sorted(missing))}
            )

        student_ids = [result.enrollment.student_id for result, _, _ in eligible]
        target_offering_ids = [destination_offerings[course_id].pk for course_id in course_ids]
        existing = {
            (enrollment.student_id, enrollment.course_offering_id): enrollment
            for enrollment in Enrollment.objects.filter(
                student_id__in=student_ids,
                course_offering_id__in=target_offering_ids,
            )
        }
        to_create = []
        for result, _, failed_rows in eligible:
            for failed_row in failed_rows:
                offering = destination_offerings[failed_row.course_offering.course_id]
                key = (result.enrollment.student_id, offering.pk)
                if key not in existing:
                    to_create.append(Enrollment(
                        student_id=result.enrollment.student_id,
                        academic_year_level=target_scope,
                        course_offering=offering,
                        enrollment_type=Enrollment.Type.REPEAT,
                        status=Enrollment.Status.ACTIVE,
                        enrolled_by=actor,
                    ))
        if to_create:
            Enrollment.objects.bulk_create(to_create, batch_size=500)
            created_count += len(to_create)
            existing.update({
                (enrollment.student_id, enrollment.course_offering_id): enrollment
                for enrollment in Enrollment.objects.filter(
                    student_id__in=student_ids,
                    course_offering_id__in=target_offering_ids,
                )
            })

        source_ids = [result.enrollment_id for result, _, _ in eligible]
        Enrollment.objects.filter(pk__in=source_ids).update(status=Enrollment.Status.COMPLETED)
        histories = []
        for result, rows, failed_rows in eligible:
            created_ids = [
                destination_offerings[row.course_offering.course_id].pk
                for row in failed_rows
            ]
            histories.append(PromotionHistory(
                evaluation_result=result,
                source_enrollment=result.enrollment,
                destination_enrollment=None,
                student=result.enrollment.student,
                source_year_level=result.enrollment.academic_year_level,
                destination_year_level=target_scope,
                outcome="last_level_exceptional",
                score=result.computed_score,
                computed_status=result.computed_status,
                final_status=result.final_status,
                override_note=result.override_note,
                override_actor_id=result.overridden_by_id,
                exceptional_offering_ids=created_ids,
                formula_snapshot=_promotion_formula_snapshot(result, rows),
                actor=actor,
            ))
        PromotionHistory.objects.bulk_create(histories, batch_size=500)
    return created_count
