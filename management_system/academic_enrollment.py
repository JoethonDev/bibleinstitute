from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils.timezone import now
from django.utils.translation import gettext as _

from .models import (
    AdminOperationJob,
    AcademicYear,
    AcademicYearLevel,
    AcademicPayment,
    CourseOffering,
    Enrollment,
    EvaluationResult,
    PublicationStatus,
    Level,
    PromotionFormula,
    PromotionHistory,
    Role,
    SignupSettings,
    User,
    HistoricalAcademicSummary,
)
from .mobile_auth import revoke_user_mobile_access, revoke_users_mobile_access

logger = logging.getLogger(__name__)


PROMOTABLE_HISTORICAL_OUTCOMES = frozenset({
    HistoricalAcademicSummary.Outcome.COMPLETED,
    HistoricalAcademicSummary.Outcome.PASSED,
    HistoricalAcademicSummary.Outcome.PARTIAL,
})
APPLICATION_STATUS_VALUES = frozenset(value for value, _label in User.APPLICATION_STATUSES)


@dataclass(frozen=True)
class PromotionFacts:
    """Stable per-batch reads reused by each student's locked promotion."""

    results: Mapping[int, EvaluationResult]
    formulas: Mapping[int, PromotionFormula]
    course_results: Mapping[tuple[int, int], list]
    max_level_ordering: int
    destination_scopes: Mapping[tuple[int, int], AcademicYearLevel]
    offerings: Mapping[tuple[int, int], CourseOffering]
    active_years: tuple[AcademicYear, ...]
    incomplete_scopes: frozenset[int]
    payments: frozenset[tuple[int, int]]
    destination_enrollments: frozenset[tuple[int, int]]


def load_promotion_facts(result_ids) -> PromotionFacts:
    """Load the bounded, reusable facts for one promotion chunk."""

    results = list(
        EvaluationResult.objects.filter(pk__in=result_ids).select_related(
            "formula__academic_year_level__academic_year",
            "formula__academic_year_level__level",
            "enrollment__student",
            "enrollment__academic_year_level__academic_year",
            "enrollment__academic_year_level__level",
        )
    )
    result_map = {result.pk: result for result in results}
    formula_ids = {result.formula_id for result in results}
    formulas = {
        formula.pk: formula
        for formula in PromotionFormula.objects.filter(pk__in=formula_ids).select_related(
            "academic_year_level__academic_year", "academic_year_level__level"
        ).prefetch_related("rules")
    }
    course_results: dict[tuple[int, int], list] = defaultdict(list)
    if result_map:
        rows = EvaluationResult.objects.filter(
            formula_id__in=formula_ids,
            enrollment_id__in={result.enrollment_id for result in results},
            course_offering__isnull=False,
        ).select_related("course_offering__course")
        for row in rows:
            course_results[(row.formula_id, row.enrollment_id)].append(row)
    max_level_ordering = (
        Level.objects.aggregate(max_ordering=Max("ordering"))["max_ordering"] or 0
    )
    active_scopes = list(
        AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("level")
    )
    destination_scopes = {
        (scope.academic_year_id, scope.level.ordering): scope for scope in active_scopes
    }
    offerings = {
        (offering.academic_year_level_id, offering.course_id): offering
        for offering in CourseOffering.objects.filter(
            academic_year_level__academic_year__is_active=True,
            status=PublicationStatus.PUBLISHED,
        ).select_related("course")
    }
    student_ids = {result.enrollment.student_id for result in results}
    scope_ids = {result.formula.academic_year_level_id for result in results}
    payments = frozenset(
        AcademicPayment.objects.filter(
            student_id__in=student_ids,
            academic_year_level__academic_year__is_active=True,
        ).values_list("student_id", "academic_year_level_id")
    )
    destination_enrollments = frozenset(
        Enrollment.objects.filter(
            student_id__in=student_ids,
            course_offering__isnull=True,
            academic_year_level__academic_year__is_active=True,
        ).values_list("student_id", "academic_year_level_id")
    )
    active_years = tuple(
        AcademicYear.objects.filter(is_active=True).order_by("ordering", "pk")
    )
    return PromotionFacts(
        results=result_map,
        formulas=formulas,
        course_results=course_results,
        max_level_ordering=max_level_ordering,
        destination_scopes=destination_scopes,
        offerings=offerings,
        active_years=active_years,
        incomplete_scopes=frozenset(_incomplete_formula_scope_ids(scope_ids)),
        payments=payments,
        destination_enrollments=destination_enrollments,
    )



def _validate_expected_application_status(current_status: str, expected_status: str) -> None:
    if expected_status not in APPLICATION_STATUS_VALUES:
        raise ValidationError(_("Invalid expected application status."))
    if current_status != expected_status:
        raise ValidationError(
            _("The application status changed before this action was executed."),
            code="stale_status",
        )


def lock_application_status_for_edit(user_id: int, expected_status: str) -> User:
    """Lock and validate status before saving a bound user form inside atomic()."""
    user = User.objects.select_for_update().get(pk=user_id)
    _validate_expected_application_status(user.application_status, expected_status)
    return user


def _require_admin(actor: User) -> None:
    if not actor.is_authenticated or not actor.role or actor.role.role != "admin":
        raise PermissionDenied(_("Only administrators can change academic enrollment state."))


def _resolve_intake_scope(active_year: AcademicYear) -> AcademicYearLevel | None:
    """Return the configured intake scope, or the active year's lowest level."""
    settings_row = (
        SignupSettings.objects.select_related(
            "intake_scope__academic_year", "intake_scope__level"
        )
        .filter(singleton="default")
        .first()
    )
    if (
        settings_row is not None
        and settings_row.intake_scope_id
        and settings_row.intake_scope.academic_year_id == active_year.pk
    ):
        return settings_row.intake_scope
    return (
        AcademicYearLevel.objects.select_related("level")
        .filter(academic_year=active_year)
        .order_by("level__ordering", "pk")
        .first()
    )


def _promotion_formula_snapshot(result: EvaluationResult, course_results=None, formula=None) -> dict:
    formula = formula or result.formula
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
            for rule in formula.rules.all()
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


def _failed_destination_offerings(source_scope, destination_scope, failed_results, preloaded=None):
    course_ids = [result.course_offering.course_id for result in failed_results]
    if preloaded is not None:
        by_course = {
            course_id: preloaded[(destination_scope.pk, course_id)]
            for course_id in course_ids
            if (destination_scope.pk, course_id) in preloaded
        }
    else:
        offerings = list(CourseOffering.objects.select_for_update().select_related("course").filter(
            academic_year_level=destination_scope,
            course_id__in=course_ids,
            status=PublicationStatus.PUBLISHED,
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
    logger.info(
        "academic year activated",
        extra={"event": "academic_year_activated", "year_id": target.pk, "year_name": target.name},
    )
    return target


@transaction.atomic
def set_application_status(
    application: User,
    actor: User,
    status: str,
    expected_status: str | None = None,
) -> tuple[User, Enrollment | None]:
    """Set an application status with admin-only locking and enrollment rules."""
    _require_admin(actor)
    locked_user = User.objects.select_for_update().get(pk=application.pk)
    if status not in APPLICATION_STATUS_VALUES:
        raise ValidationError(_("Invalid application status."))
    if expected_status is not None:
        _validate_expected_application_status(locked_user.application_status, expected_status)

    if status != "active":
        locked_user.application_status = status
        locked_user.is_active = False
        if status == "pending":
            locked_user.decided_by = None
            locked_user.decided_at = None
        else:
            locked_user.decided_by = actor
            locked_user.decided_at = now()
        locked_user.save(update_fields=[
            "application_status", "is_active", "decided_by", "decided_at",
        ])
        if status != "active":
            revoke_user_mobile_access(locked_user)
        logger.info(
            "application status changed",
            extra={
                "event": "application_status_changed",
                "student_id": locked_user.pk,
                "status": status,
                "expected_status": expected_status,
            },
        )
        return locked_user, None

    active_years = list(AcademicYear.objects.select_for_update().filter(is_active=True).order_by("pk"))
    if len(active_years) != 1:
        raise ValidationError(_("Exactly one active academic year is required before acceptance."))
    active_year = active_years[0]
    scope = _resolve_intake_scope(active_year)
    if scope is None:
        raise ValidationError(_("The active academic year has no opened level."))

    student_role = Role.objects.get(role="student")
    locked_user.application_status = "active"
    locked_user.is_active = True
    locked_user.role = student_role
    locked_user.decided_by = actor
    locked_user.decided_at = now()
    locked_user.save(update_fields=["application_status", "is_active", "role", "decided_by", "decided_at"])

    enrollment, _created = Enrollment.objects.get_or_create(
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
    logger.info(
        "application status changed",
        extra={
            "event": "application_status_changed",
            "student_id": locked_user.pk,
            "status": status,
            "expected_status": expected_status,
            "enrollment_id": enrollment.pk,
        },
    )
    return locked_user, enrollment


@transaction.atomic
def bulk_set_application_status(
    user_ids: Iterable[int],
    actor: User,
    status: str,
    expected_statuses: Mapping[int, str],
) -> dict:
    """Apply one application decision to one bounded, server-selected batch.

    User rows are locked once per batch and enrollment rows are reconciled with
    bulk queries. ``expected_statuses`` must be derived by the caller from its
    authoritative selection scope; browser payloads must never be passed here.
    """
    _require_admin(actor)
    user_ids = list(dict.fromkeys(int(user_id) for user_id in user_ids))
    if not user_ids or len(user_ids) > 100:
        raise ValidationError(_("Bulk application batches must contain between one and 100 users."))
    if status not in APPLICATION_STATUS_VALUES:
        raise ValidationError(_("Invalid application status."))

    locked_users = list(
        User.objects.select_for_update().filter(pk__in=user_ids).order_by("pk")
    )
    users_by_id = {user.pk: user for user in locked_users}
    results = {"success": [], "stale": [], "errors": [], "changed_users": []}
    eligible = []
    for user_id in user_ids:
        user = users_by_id.get(user_id)
        if user is None:
            results["errors"].append({"id": user_id, "error": str(_("User not found."))})
            continue
        expected_status = expected_statuses.get(user_id)
        if expected_status not in APPLICATION_STATUS_VALUES:
            results["stale"].append({
                "id": user_id,
                "error": str(_("The application is no longer in the selected status.")),
            })
            continue
        if user.application_status != expected_status:
            results["stale"].append({
                "id": user_id,
                "error": str(_("The application status changed before this action was executed.")),
            })
            continue
        eligible.append(user)

    if status == "active" and eligible:
        active_years = list(
            AcademicYear.objects.select_for_update().filter(is_active=True).order_by("pk")
        )
        if len(active_years) != 1:
            for user in eligible:
                results["errors"].append({
                    "id": user.pk,
                    "error": str(_("Exactly one active academic year is required before acceptance.")),
                })
            eligible = []
        else:
            scope = _resolve_intake_scope(active_years[0])
            if scope is None:
                for user in eligible:
                    results["errors"].append({
                        "id": user.pk,
                        "error": str(_("The active academic year has no opened level.")),
                    })
                eligible = []
            else:
                student_role = Role.objects.get(role="student")
                eligible_ids = [user.pk for user in eligible]
                enrollments = list(
                    Enrollment.objects.select_for_update().filter(
                        student_id__in=eligible_ids,
                        academic_year_level_id=scope.pk,
                        course_offering__isnull=True,
                    )
                )
                enrollment_by_student = {enrollment.student_id: enrollment for enrollment in enrollments}
                valid_users = []
                for user in eligible:
                    enrollment = enrollment_by_student.get(user.pk)
                    if enrollment and (enrollment.enrollment_type != Enrollment.Type.NORMAL or enrollment.course_offering_id):
                        results["errors"].append({
                            "id": user.pk,
                            "error": str(_("The application already has an incompatible enrollment.")),
                        })
                        continue
                    valid_users.append(user)
                eligible = valid_users
                eligible_ids = [user.pk for user in eligible]
                missing_enrollments = [
                    Enrollment(
                        student_id=user.pk,
                        academic_year_level_id=scope.pk,
                        course_offering_id=None,
                        enrollment_type=Enrollment.Type.NORMAL,
                        status=Enrollment.Status.ACTIVE,
                        enrolled_by_id=actor.pk,
                    )
                    for user in eligible
                    if user.pk not in enrollment_by_student
                ]
                if missing_enrollments:
                    Enrollment.objects.bulk_create(missing_enrollments, ignore_conflicts=True)
                    enrollments = list(
                        Enrollment.objects.select_for_update().filter(
                            student_id__in=eligible_ids,
                            academic_year_level_id=scope.pk,
                            course_offering__isnull=True,
                        )
                    )
                    enrollment_by_student = {enrollment.student_id: enrollment for enrollment in enrollments}
                enrollment_updates = []
                for user in eligible:
                    enrollment = enrollment_by_student.get(user.pk)
                    if enrollment is None:
                        results["errors"].append({
                            "id": user.pk,
                            "error": str(_("The normal enrollment could not be created.")),
                        })
                        continue
                    enrollment.status = Enrollment.Status.ACTIVE
                    enrollment.enrolled_by_id = enrollment.enrolled_by_id or actor.pk
                    enrollment_updates.append(enrollment)
                if enrollment_updates:
                    Enrollment.objects.bulk_update(enrollment_updates, ["status", "enrolled_by"])
                eligible = [user for user in eligible if user.pk in {enrollment.student_id for enrollment in enrollment_updates}]
                decision_time = now()
                if eligible:
                    User.objects.filter(pk__in=[user.pk for user in eligible]).update(
                        application_status="active",
                        is_active=True,
                        role_id=student_role.pk,
                        decided_by_id=actor.pk,
                        decided_at=decision_time,
                    )
                    for user in eligible:
                        if user.application_status != "active":
                            results["changed_users"].append(user)
                        user.application_status = "active"
                        user.is_active = True
                        user.role_id = student_role.pk
                        user.decided_by_id = actor.pk
                        user.decided_at = decision_time

    elif eligible:
        decision_time = now()
        eligible_ids = [user.pk for user in eligible]
        User.objects.filter(pk__in=eligible_ids).update(
            application_status=status,
            is_active=False,
            decided_by_id=actor.pk if status == "declined" else None,
            decided_at=decision_time if status == "declined" else None,
        )
        if status in {"pending", "declined"}:
            revoke_users_mobile_access(eligible_ids)
        for user in eligible:
            if user.application_status != status:
                results["changed_users"].append(user)
            user.application_status = status
            user.is_active = False
            user.decided_by_id = actor.pk if status == "declined" else None
            user.decided_at = decision_time if status == "declined" else None

    results["success"] = [user.pk for user in eligible]
    logger.info(
        "application batch changed",
        extra={
            "event": "application_bulk_changed",
            "status": status,
            "requested": len(user_ids),
            "success_count": len(results["success"]),
            "stale_count": len(results["stale"]),
            "error_count": len(results["errors"]),
            "changed_count": len(results["changed_users"]),
        },
    )
    return results


@transaction.atomic
def accept_application(
    application: User,
    actor: User,
    expected_status: str | None = None,
) -> tuple[User, Enrollment]:
    """Accept or re-accept one application into the active year's lowest level."""
    user, enrollment = set_application_status(application, actor, "active", expected_status)
    return user, enrollment


@transaction.atomic
def decline_application(
    application: User,
    actor: User,
    expected_status: str | None = None,
) -> User:
    """Decline or re-decline an application with the same locking rules."""
    user, _enrollment = set_application_status(application, actor, "declined", expected_status)
    return user


@transaction.atomic
def reopen_application(
    application: User,
    actor: User,
    expected_status: str | None = None,
) -> User:
    """Return an application to pending without deleting academic history."""
    user, _enrollment = set_application_status(application, actor, "pending", expected_status)
    return user


@transaction.atomic
def set_user_normal_enrollment_scope(
    student: User,
    actor: User,
    scope: AcademicYearLevel,
) -> Enrollment:
    """Assign one admin-selected active-year normal enrollment scope."""
    _require_admin(actor)
    locked_user = User.objects.select_for_update().get(pk=student.pk)
    selected_scope = AcademicYearLevel.objects.select_related(
        "academic_year", "level"
    ).get(pk=scope.pk)
    if not selected_scope.academic_year.is_active:
        raise ValidationError(_("The selected academic year is not active."))

    Enrollment.objects.select_for_update().filter(
        student=locked_user,
        academic_year_level__academic_year__is_active=True,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
    ).exclude(academic_year_level=selected_scope).update(
        status=Enrollment.Status.INACTIVE,
    )
    enrollment, _created = Enrollment.objects.select_for_update().get_or_create(
        student=locked_user,
        academic_year_level=selected_scope,
        course_offering=None,
        defaults={
            "enrollment_type": Enrollment.Type.NORMAL,
            "status": Enrollment.Status.ACTIVE,
            "enrolled_by": actor,
        },
    )
    if enrollment.enrollment_type != Enrollment.Type.NORMAL or enrollment.course_offering_id:
        raise ValidationError(_("The selected enrollment is not a normal full-year enrollment."))
    if enrollment.status != Enrollment.Status.ACTIVE or enrollment.enrolled_by_id is None:
        enrollment.status = Enrollment.Status.ACTIVE
        enrollment.enrolled_by = enrollment.enrolled_by or actor
        enrollment.save(update_fields=["status", "enrolled_by"])
    logger.info(
        "enrollment scope assigned",
        extra={
            "event": "enrollment_scope_assigned",
            "student_id": locked_user.pk,
            "scope_id": selected_scope.pk,
        },
    )
    return enrollment


@transaction.atomic
def promote_evaluation_result(
    *,
    result_id: int,
    actor: User,
    facts: PromotionFacts | None = None,
) -> PromotionHistory:
    """Consume an aggregate result and apply course-aware promotion rules."""
    _require_admin(actor)
    result = EvaluationResult.objects.select_for_update().select_related(
        "formula__academic_year_level__academic_year",
        "formula__academic_year_level__level",
        "enrollment__student",
        "enrollment__academic_year_level__academic_year",
        "enrollment__academic_year_level__level",
    ).get(pk=result_id)
    formula = (facts.formulas.get(result.formula_id) if facts is not None else None) or result.formula
    if result.course_offering_id:
        raise ValidationError(_("Only the aggregate evaluation result can be promoted."))
    if facts is not None:
        if formula.academic_year_level_id in facts.incomplete_scopes:
            raise ValidationError(
                _("The formula calculation for this academic scope is incomplete. Finish it before promotion.")
            )
    else:
        assert_formula_calculation_complete(formula.academic_year_level)
    existing_history_id = PromotionHistory.objects.filter(
        evaluation_result_id=result_id
    ).values_list("pk", flat=True).first()
    if existing_history_id:
        raise ValidationError(_("This evaluation result has already been processed."))

    source = Enrollment.objects.select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level"
    ).select_for_update().get(pk=result.enrollment_id)
    if formula.academic_year_level_id != source.academic_year_level_id:
        raise ValidationError(_("The evaluation formula and enrollment scopes do not match."))
    if formula.course_offering_id:
        raise ValidationError(_("An offering-specific formula cannot make an aggregate promotion decision."))
    if source.enrollment_type != Enrollment.Type.NORMAL or source.course_offering_id:
        raise ValidationError(_("Only a normal enrollment can be promoted."))
    if source.status not in (Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED):
        raise ValidationError(_("Only active or completed enrollments can be promoted."))

    if facts is not None:
        course_results = list(
            facts.course_results.get((result.formula_id, result.enrollment_id), [])
        )
    else:
        course_results = _course_results_for(result, lock=True)
    failed_results = _promotion_failure_state(result, course_results)
    source_scope = source.academic_year_level
    source_year = source_scope.academic_year
    if facts is not None and facts.active_years:
        active_years = list(
            AcademicYear.objects.select_for_update()
            .filter(pk__in=[year.pk for year in facts.active_years])
            .order_by("ordering", "pk")
        )
    else:
        active_years = list(AcademicYear.objects.select_for_update().filter(is_active=True).order_by("ordering", "pk"))
    if len(active_years) != 1 or active_years[0].ordering <= source_year.ordering:
        raise ValidationError(_("Activate a higher academic year before promotion."))
    destination_year = active_years[0]
    if facts is not None:
        is_last_level = source_scope.level.ordering >= facts.max_level_ordering
    else:
        is_last_level = not Level.objects.filter(ordering__gt=source_scope.level.ordering).exists()

    def destination_scope_for(ordering):
        if facts is not None:
            scope = facts.destination_scopes.get((destination_year.pk, ordering))
            if scope is not None:
                return scope
        return _destination_scope(source_scope, destination_year, ordering)

    passed = not failed_results
    threshold = formula.failed_courses_repeat_threshold
    partial_failure = bool(failed_results) and len(failed_results) < threshold

    destination_scope = None
    create_normal_destination = False
    if passed:
        if is_last_level:
            outcome = "graduated"
        else:
            destination_scope = destination_scope_for(source_scope.level.ordering + 1)
            create_normal_destination = True
            outcome = "passed"
    elif is_last_level and partial_failure:
        destination_scope = destination_scope_for(source_scope.level.ordering)
        outcome = "last_level_exceptional"
    else:
        destination_scope = destination_scope_for(
            source_scope.level.ordering if not partial_failure else source_scope.level.ordering + 1
        )
        create_normal_destination = True
        outcome = "repeated" if not partial_failure else "passed_with_exceptions"

    if destination_scope is None and outcome != "graduated":
        raise ValidationError(_("The destination academic level is not open in the active year."))
    if destination_scope is not None:
        if facts is not None:
            paid = (source.student_id, destination_scope.pk) in facts.payments
        else:
            paid = AcademicPayment.objects.filter(
                student=source.student,
                academic_year_level=destination_scope,
            ).exists()
        if not paid:
            raise ValidationError(
                _("Upload the payment receipt for the destination academic year before promotion.")
            )

    exceptional_offerings = []
    if failed_results and partial_failure:
        exceptional_offerings = _failed_destination_offerings(
            source_scope,
            destination_scope,
            failed_results,
            preloaded=facts.offerings if facts is not None else None,
        )

    destination = None
    if create_normal_destination:
        if facts is not None:
            destination_exists = (source.student_id, destination_scope.pk) in facts.destination_enrollments
        else:
            destination_exists = Enrollment.objects.filter(
                student=source.student,
                academic_year_level=destination_scope,
                course_offering__isnull=True,
            ).exists()
        if destination_exists:
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
        enrollment, created = Enrollment.objects.get_or_create(
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
        if not created and enrollment.status != Enrollment.Status.ACTIVE:
            enrollment.status = Enrollment.Status.ACTIVE
            enrollment.enrolled_by = actor
            enrollment.save(update_fields=["status", "enrolled_by"])
        created_exceptional.append(enrollment)

    source.status = Enrollment.Status.COMPLETED
    source.save(update_fields=["status"])
    history = PromotionHistory.objects.create(
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
        formula_snapshot=_promotion_formula_snapshot(result, course_results, formula=formula),
        promotion_method=PromotionHistory.Method.SYSTEM,
        reason="",
        actor=actor,
    )
    logger.info(
        "student promoted",
        extra={
            "event": "promotion_applied",
            "student_id": source.student_id,
            "result_id": result_id,
            "outcome": outcome,
            "source_scope_id": source_scope.pk,
            "destination_scope_id": destination_scope.pk if destination_scope else None,
            "exceptional_count": len(created_exceptional),
        },
    )
    return history


def execute_bulk_promotion(job, token) -> dict:
    """J01 executor: promote the frozen selected results with a committed cursor.

    Each student still uses the canonical atomic `promote_evaluation_result`;
    already-promoted rows are skipped idempotently, and row failures are
    bounded and reported without stopping the remaining selection.
    """

    from .admin_operations import AdminOperationError, assert_admin_operation_progress, require_admin_operation_actor

    require_admin_operation_actor(job)
    _require_admin(job.actor)
    raw_ids = (job.input or {}).get("result_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 1000:
        raise AdminOperationError("invalid_selection", _("Select between one and 1,000 students."))
    try:
        result_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        raise AdminOperationError("invalid_selection", _("Select between one and 1,000 students."))

    cursor_index = max(0, int((job.cursor or {}).get("index", 0) or 0))
    succeeded = int(job.succeeded or 0)
    failed = int(job.failed or 0)
    skipped = int(job.skipped or 0)
    errors = list((job.result or {}).get("row_errors") or [])[:100]
    total = len(result_ids)

    chunk_size = 100
    for chunk_start in range(cursor_index, total, chunk_size):
        chunk_end = min(chunk_start + chunk_size, total)
        chunk_ids = result_ids[chunk_start:chunk_end]
        # One bounded set of stable fact reads supplies the whole chunk.
        chunk_facts = load_promotion_facts(chunk_ids)
        # One bounded read supplies the idempotency check for the whole chunk.
        already_promoted = set(
            PromotionHistory.objects.filter(evaluation_result_id__in=chunk_ids)
            .values_list("evaluation_result_id", flat=True)
        )
        # Preload the incomplete-calculation guard for the chunk so a stopped
        # formula generation rejects every affected student up front.
        chunk_scopes = {
            result_id: result.formula.academic_year_level_id
            for result_id, result in chunk_facts.results.items()
        }
        incomplete_scopes = _incomplete_formula_scope_ids(set(chunk_scopes.values()))
        for index in range(chunk_start, chunk_end):
            with transaction.atomic():
                result_id = result_ids[index]
                if result_id in already_promoted:
                    skipped += 1
                elif chunk_scopes.get(result_id) in incomplete_scopes:
                    failed += 1
                    if len(errors) < 100:
                        errors.append({
                            "result_id": result_id,
                            "message": str(_("The formula calculation for this academic scope is incomplete. Finish it before promotion.")),
                        })
                else:
                    try:
                        promote_evaluation_result(
                            result_id=result_id,
                            actor=job.actor,
                            facts=chunk_facts,
                        )
                        succeeded += 1
                    except (EvaluationResult.DoesNotExist, ValidationError, PermissionDenied) as exc:
                        failed += 1
                        if len(errors) < 100:
                            errors.append({"result_id": result_id, "message": str(exc)[:300]})
                assert_admin_operation_progress(
                    job.pk,
                    token,
                    processed=index + 1,
                    succeeded=succeeded,
                    failed=failed,
                    skipped=skipped,
                    total=total,
                    cursor={"index": index + 1},
                    result={"row_errors": errors} if errors else None,
                )

    status = AdminOperationJob.Status.PARTIAL if failed else AdminOperationJob.Status.COMPLETED
    return {
        "status": status,
        "result": {
            "promoted": succeeded,
            "skipped": skipped,
            "failed": failed,
            "row_errors": errors,
        },
    }


def is_last_academic_level(level: Level) -> bool:
    """Return whether no configured level has a higher canonical ordering.

    A configured level counts even when no academic year has opened it, so
    preview and apply agree on whether a destination is required.
    """

    return not Level.objects.filter(ordering__gt=level.ordering).exists()


def _incomplete_formula_scope_ids(scope_ids) -> set[int]:
    """Return scopes whose latest formula calculation has not completed."""

    latest_status: dict[int, str] = {}
    rows = (
        AdminOperationJob.objects.filter(
            kind=AdminOperationJob.Kind.FORMULA_RECALCULATION,
            academic_year_level_id__in=list(scope_ids),
        )
        .order_by("academic_year_level_id", "-created_at", "-updated_at")
        .values_list("academic_year_level_id", "status")
    )
    for scope_id, status in rows:
        latest_status.setdefault(scope_id, status)
    return {
        scope_id
        for scope_id, status in latest_status.items()
        if status != AdminOperationJob.Status.COMPLETED
    }


def assert_formula_calculation_complete(scope) -> None:
    """Block promotion while the scope's calculation is incomplete or stale."""

    if scope.pk in _incomplete_formula_scope_ids({scope.pk}):
        raise ValidationError(
            _("The formula calculation for this academic scope is incomplete. Finish it before promotion.")
        )


@transaction.atomic
def promote_historical_summary(
    *,
    summary_id: int,
    destination_scope_id: int | None,
    exceptional_offering_ids: list[int] | None,
    reason: str,
    actor: User,
) -> PromotionHistory:
    """Promote a reviewed historical intake row without inventing exam results."""
    _require_admin(actor)
    promotion_reason = reason.strip()
    if not promotion_reason:
        raise ValidationError(_("A manual historical promotion requires a reason."))
    summary = HistoricalAcademicSummary.objects.select_for_update().select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level"
    ).get(pk=summary_id)
    if summary.promoted_at:
        raise ValidationError(_("This historical summary has already been promoted."))
    if summary.outcome not in PROMOTABLE_HISTORICAL_OUTCOMES:
        raise ValidationError(_("Review the historical outcome before promotion."))
    if not summary.reviewed_by_id or not summary.reviewed_at:
        raise ValidationError(_("A reviewed historical outcome is required before promotion."))
    is_last_level = is_last_academic_level(summary.academic_year_level.level)
    destination = None
    if destination_scope_id:
        destination = AcademicYearLevel.objects.select_for_update().select_related(
            "academic_year", "level"
        ).get(pk=destination_scope_id)
        if not destination.academic_year.is_active:
            raise ValidationError(_("The destination academic year must be active."))
        if destination.academic_year.ordering <= summary.academic_year_level.academic_year.ordering:
            raise ValidationError(_("The destination academic year must be later than the historical year."))
    active_years = list(AcademicYear.objects.select_for_update().filter(is_active=True))
    if len(active_years) != 1:
        raise ValidationError(_("Exactly one active academic year is required for promotion."))
    if destination is not None and destination.academic_year_id != active_years[0].pk:
        raise ValidationError(_("The destination must belong to the sole active academic year."))
    if is_last_level and summary.outcome in {
        HistoricalAcademicSummary.Outcome.COMPLETED,
        HistoricalAcademicSummary.Outcome.PASSED,
    } and destination is not None:
        raise ValidationError(_("A completed last-level student does not need a destination enrollment."))
    if not is_last_level and destination is None:
        raise ValidationError(_("Select a destination academic scope before promotion."))
    if summary.outcome == HistoricalAcademicSummary.Outcome.PARTIAL and destination is None:
        raise ValidationError(_("Partial promotion requires a destination academic scope."))
    if destination is not None:
        expected_level = summary.academic_year_level.level.ordering
        if not is_last_level:
            expected_level += 1
        if destination.level.ordering != expected_level:
            raise ValidationError(_("The destination level does not match the historical promotion outcome."))

    source = Enrollment.objects.select_for_update().get(
        student=summary.student,
        academic_year_level=summary.academic_year_level,
        course_offering=None,
    )
    if source.enrollment_type != Enrollment.Type.NORMAL:
        raise ValidationError(_("The historical source enrollment must be normal."))
    if source.status == Enrollment.Status.WITHDRAWN:
        raise ValidationError(_("The historical source enrollment is withdrawn."))
    source.status = Enrollment.Status.COMPLETED
    source.save(update_fields=["status"])

    exceptional_ids = list(dict.fromkeys(exceptional_offering_ids or []))
    if summary.outcome == HistoricalAcademicSummary.Outcome.PARTIAL and not exceptional_ids:
        raise ValidationError(_("Partial promotion requires at least one failed course."))
    last_level_partial = is_last_level and summary.outcome == HistoricalAcademicSummary.Outcome.PARTIAL
    last_level_pass = is_last_level and summary.outcome in {
        HistoricalAcademicSummary.Outcome.COMPLETED,
        HistoricalAcademicSummary.Outcome.PASSED,
    }
    destination_enrollment = None
    if not last_level_partial and not last_level_pass:
        destination_enrollment, created = Enrollment.objects.select_for_update().get_or_create(
            student=summary.student,
            academic_year_level=destination,
            course_offering=None,
            defaults={
                "enrollment_type": Enrollment.Type.NORMAL,
                "status": Enrollment.Status.ACTIVE,
                "enrolled_by": actor,
            },
        )
        if destination_enrollment.enrollment_type != Enrollment.Type.NORMAL:
            raise ValidationError(_("The destination enrollment is not a normal enrollment."))
        if not created and destination_enrollment.status == Enrollment.Status.WITHDRAWN:
            raise ValidationError(_("The destination enrollment is withdrawn."))
        if destination_enrollment.status != Enrollment.Status.ACTIVE:
            destination_enrollment.status = Enrollment.Status.ACTIVE
            destination_enrollment.enrolled_by = destination_enrollment.enrolled_by or actor
            destination_enrollment.save(update_fields=["status", "enrolled_by"])
    exceptional_offerings = list(CourseOffering.objects.select_for_update().filter(
        pk__in=exceptional_ids,
        academic_year_level=destination,
        status=PublicationStatus.PUBLISHED,
    )) if destination is not None else []
    if exceptional_ids and destination is None:
        raise ValidationError(_("Exceptional courses require a destination academic scope."))
    if last_level_partial and not exceptional_offerings:
        raise ValidationError(_("Last-level partial promotion requires failed course offerings."))
    if len(exceptional_offerings) != len(exceptional_ids):
        raise ValidationError(_("Every exceptional course must belong to the destination year and be published."))
    exceptional_enrollment_ids = []
    for offering in exceptional_offerings:
        enrollment, created = Enrollment.objects.get_or_create(
            student=summary.student,
            course_offering=offering,
            defaults={
                "academic_year_level": destination,
                "enrollment_type": Enrollment.Type.REPEAT,
                "status": Enrollment.Status.ACTIVE,
                "enrolled_by": actor,
            },
        )
        if enrollment.enrollment_type == Enrollment.Type.NORMAL:
            raise ValidationError(_("An exceptional offering cannot use a normal enrollment."))
        if enrollment.academic_year_level_id != offering.academic_year_level_id:
            raise ValidationError(_("Exceptional enrollment scope does not match its offering."))
        if not created and enrollment.status != Enrollment.Status.ACTIVE:
            enrollment.status = Enrollment.Status.ACTIVE
            enrollment.enrolled_by = actor
            enrollment.save(update_fields=["status", "enrolled_by"])
        exceptional_enrollment_ids.append(offering.pk)

    summary.promoted_at = now()
    summary.reviewed_by = summary.reviewed_by or actor
    summary.reviewed_at = summary.reviewed_at or now()
    summary.certificate_eligible = summary.outcome in {
        HistoricalAcademicSummary.Outcome.COMPLETED,
        HistoricalAcademicSummary.Outcome.PASSED,
    } and not exceptional_enrollment_ids
    summary.save(update_fields=["promoted_at", "reviewed_by", "reviewed_at", "certificate_eligible", "updated_at"])
    history = PromotionHistory.objects.create(
        evaluation_result=None,
        historical_summary=summary,
        source_enrollment=source,
        destination_enrollment=destination_enrollment,
        student=summary.student,
        source_year_level=summary.academic_year_level,
        destination_year_level=destination,
        outcome=("historical_graduated" if last_level_pass else ("historical_last_level_partial" if last_level_partial else ("historical_partial" if exceptional_enrollment_ids else "historical_promoted"))),
        score=None,
        computed_status=summary.outcome,
        final_status=summary.outcome,
        exceptional_offering_ids=exceptional_enrollment_ids,
        formula_snapshot={"historical_summary_id": summary.pk, "outcome": summary.outcome},
        promotion_method=PromotionHistory.Method.MANUAL_HISTORICAL,
        reason=promotion_reason,
        actor=actor,
    )
    logger.info(
        "historical student promoted",
        extra={
            "event": "historical_promotion_applied",
            "student_id": summary.student_id,
            "summary_id": summary.pk,
            "outcome": summary.outcome,
            "destination_scope_id": destination.pk if destination else None,
            "exceptional_count": len(exceptional_enrollment_ids),
        },
    )
    return history


def _materialize_last_level_exceptional_access(destination_year: AcademicYear, actor: User) -> int:
    """Create failed-course-only access for archived last-level partial failures."""
    last_level = Level.objects.order_by("-ordering").first()
    if last_level is None:
        return 0
    candidate_queryset = EvaluationResult.objects.filter(
        course_offering__isnull=True,
        promotion_history__isnull=True,
        formula__course_offering__isnull=True,
        formula__academic_year_level__level=last_level,
        formula__academic_year_level__academic_year__ordering__lt=destination_year.ordering,
        enrollment__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    )
    target_scope = AcademicYearLevel.objects.filter(
        academic_year=destination_year,
        level=last_level,
    ).first()
    if target_scope is None:
        if candidate_queryset.exists():
            raise ValidationError(_("The next academic year must open the last level before failed-course access can be created."))
        return 0

    last_candidate_pk = candidate_queryset.aggregate(max_pk=Max("pk"))["max_pk"]
    if last_candidate_pk is None:
        return 0

    created_count = 0
    last_pk = 0
    while last_pk < last_candidate_pk:
        candidate_batch = list(
            candidate_queryset.filter(pk__gt=last_pk, pk__lte=last_candidate_pk)
            .select_for_update(of=("self",))
            .select_related("formula", "enrollment", "enrollment__student", "enrollment__academic_year_level")
            .order_by("pk")[:500]
        )
        if not candidate_batch:
            break
        last_pk = candidate_batch[-1].pk
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

        student_ids = [result.enrollment.student_id for result, _rows, _failed_rows in eligible]
        target_offering_ids = [destination_offerings[course_id].pk for course_id in course_ids]
        existing = {
            (enrollment.student_id, enrollment.course_offering_id): enrollment
            for enrollment in Enrollment.objects.filter(
                student_id__in=student_ids,
                course_offering_id__in=target_offering_ids,
            )
        }
        to_create = []
        for result, _rows, failed_rows in eligible:
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

        source_ids = [result.enrollment_id for result, _rows, _failed_rows in eligible]
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
                promotion_method=PromotionHistory.Method.SYSTEM,
                reason="",
                actor=actor,
            ))
        PromotionHistory.objects.bulk_create(histories, batch_size=500)
    if created_count:
        logger.info(
            "last-level failed-course access created",
            extra={
                "event": "last_level_exceptional_access_created",
                "count": created_count,
                "destination_year_id": destination_year.pk,
            },
        )
    return created_count
