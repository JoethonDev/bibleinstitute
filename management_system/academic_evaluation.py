from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from .academic_formula import save_promotion_formula
from .models import (
    AcademicYearLevel,
    AttendanceRecord,
    CourseOffering,
    Enrollment,
    EvaluationResult,
    Lesson,
    PromotionFormula,
    PromotionRule,
    PublicationStatus,
    Question,
    Submission,
    Quiz,
)


@dataclass(frozen=True)
class EvaluationPlan:
    formula: PromotionFormula
    rules: tuple[PromotionRule, ...]
    course_offering: CourseOffering
    published_lesson_count: int
    quiz_possible_points: dict[int, int]
    grading_errors: tuple[str, ...]


def applicable_formula(scope: AcademicYearLevel, offering: CourseOffering) -> PromotionFormula | None:
    """Return the specific offering formula, falling back to all courses."""
    specific = PromotionFormula.objects.filter(
        academic_year_level=scope,
        course_offering=offering,
    ).prefetch_related("rules").first()
    if specific:
        return specific
    return PromotionFormula.objects.filter(
        academic_year_level=scope,
        course_offering__isnull=True,
    ).prefetch_related("rules").first()


def evaluation_offerings(formula: PromotionFormula):
    """Return the published offerings evaluated by a formula."""
    if formula.course_offering_id:
        return CourseOffering.objects.filter(
            pk=formula.course_offering_id,
            academic_year_level=formula.academic_year_level,
            status=PublicationStatus.PUBLISHED,
        ).select_related("course", "academic_year_level__academic_year", "academic_year_level__level")
    return CourseOffering.objects.filter(
        academic_year_level=formula.academic_year_level,
        status=PublicationStatus.PUBLISHED,
    ).select_related("course", "academic_year_level__academic_year", "academic_year_level__level").order_by("course__name", "pk")


def _quiz_scope(rule: PromotionRule, offering: CourseOffering, starts_on: date, ends_on: date) -> Q:
    query = Q(
        course_offering=offering,
        status=PublicationStatus.PUBLISHED,
        closing_date__date__gte=starts_on,
        closing_date__date__lte=ends_on,
    )
    if rule.quiz_type_id:
        query &= Q(quiz_type_id=rule.quiz_type_id)
    return query


def _selected_quiz_query(rules, offering, starts_on, ends_on):
    query = Q(pk__in=[])
    has_quiz = False
    for rule in rules:
        if rule.metric == PromotionRule.Metric.QUIZ:
            query |= _quiz_scope(rule, offering, starts_on, ends_on)
            has_quiz = True
    return query if has_quiz else Q(pk__in=[])


def _possible_points(rule, offering, starts_on, ends_on) -> int:
    value = Question.objects.filter(
        quiz__in=Quiz.objects.filter(_quiz_scope(rule, offering, starts_on, ends_on))
    ).aggregate(total=Sum("grade"))["total"]
    return int(value or 0)


def _quiz_mismatch_errors(rules, offering, starts_on, ends_on) -> tuple[str, ...]:
    mismatches = Quiz.objects.filter(
        _selected_quiz_query(rules, offering, starts_on, ends_on)
    ).annotate(question_total=Coalesce(Sum("questions__grade"), Value(0))).exclude(
        total_grade=F("question_total")
    ).values_list("name", "total_grade", "question_total")
    return tuple(
        _("Quiz %(name)s declares %(declared)s points but its questions total %(actual)s.") % {
            "name": name,
            "declared": declared,
            "actual": actual or 0,
        }
        for name, declared, actual in mismatches
    )


def build_evaluation_plan(
    formula: PromotionFormula,
    rules=None,
    course_offering: CourseOffering | None = None,
) -> EvaluationPlan:
    offering = course_offering or formula.course_offering
    if offering is None:
        raise ValidationError(_("A course offering is required to build a course evaluation plan."))
    rules = tuple(rules if rules is not None else formula.rules.select_related("quiz_type").all())
    starts_on = formula.evaluation_starts_on
    ends_on = formula.evaluation_ends_on
    quiz_possible_points = {
        rule.pk: _possible_points(rule, offering, starts_on, ends_on)
        for rule in rules
        if rule.metric == PromotionRule.Metric.QUIZ
    }
    return EvaluationPlan(
        formula=formula,
        rules=rules,
        course_offering=offering,
        published_lesson_count=Lesson.objects.filter(
            course_offering=offering,
            status=PublicationStatus.PUBLISHED,
        ).count(),
        quiz_possible_points=quiz_possible_points,
        grading_errors=_quiz_mismatch_errors(rules, offering, starts_on, ends_on),
    )


def evaluation_enrollments(plan: EvaluationPlan):
    """Return one lazy annotated queryset for normal students in one offering."""
    selected_quizzes = _selected_quiz_query(
        plan.rules,
        plan.course_offering,
        plan.formula.evaluation_starts_on,
        plan.formula.evaluation_ends_on,
    )
    annotations = {}

    unresolved = Submission.objects.filter(
        user=OuterRef("student_id"),
        question__quiz__in=Quiz.objects.filter(selected_quizzes),
        is_graded=False,
    ).values("user").annotate(
        count=Count("question_id", distinct=True),
        points=Sum("question__grade"),
    )
    annotations["unresolved_question_count"] = Coalesce(
        Subquery(unresolved.values("count")[:1]), Value(0), output_field=IntegerField()
    )
    annotations["unresolved_question_points"] = Coalesce(
        Subquery(unresolved.values("points")[:1]), Value(0), output_field=IntegerField()
    )

    for rule in plan.rules:
        prefix = f"evaluation_rule_{rule.pk}"
        if rule.metric == PromotionRule.Metric.QUIZ:
            rule_quizzes = Quiz.objects.filter(_quiz_scope(
                rule,
                plan.course_offering,
                plan.formula.evaluation_starts_on,
                plan.formula.evaluation_ends_on,
            ))
            earned = Submission.objects.filter(
                user=OuterRef("student_id"),
                question__quiz__in=rule_quizzes,
                is_graded=True,
            ).values("user").annotate(total=Sum("grade")).values("total")[:1]
            unresolved_rule = Submission.objects.filter(
                user=OuterRef("student_id"),
                question__quiz__in=rule_quizzes,
                is_graded=False,
            ).values("user").annotate(points=Sum("question__grade")).values("points")[:1]
            annotations[f"{prefix}_earned"] = Coalesce(
                Subquery(earned), Value(0), output_field=IntegerField()
            )
            annotations[f"{prefix}_unresolved_points"] = Coalesce(
                Subquery(unresolved_rule), Value(0), output_field=IntegerField()
            )
        else:
            attendance = AttendanceRecord.objects.filter(
                student=OuterRef("student_id"),
                course_offering=plan.course_offering,
                attendance_date__gte=plan.formula.evaluation_starts_on,
                attendance_date__lte=plan.formula.evaluation_ends_on,
            ).values("student", "attendance_date").annotate(
                action_count=Count("action", distinct=True)
            ).filter(action_count=2).values("student").annotate(
                total=Count("attendance_date")
            ).values("total")[:1]
            annotations[f"{prefix}_valid_days"] = Coalesce(
                Subquery(attendance), Value(0), output_field=IntegerField()
            )

    return Enrollment.objects.filter(
        academic_year_level=plan.formula.academic_year_level,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    ).select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level"
    ).annotate(**annotations)


def evaluate_enrollment(enrollment, plan: EvaluationPlan) -> dict:
    """Calculate one already-annotated course row; callers pass bounded pages."""
    metrics = []
    online = enrollment.student.study_mode == "online"
    for rule in plan.rules:
        prefix = f"evaluation_rule_{rule.pk}"
        if rule.metric == PromotionRule.Metric.ATTENDANCE:
            if online:
                continue
            denominator = plan.published_lesson_count
            earned = getattr(enrollment, f"{prefix}_valid_days", 0)
        else:
            denominator = plan.quiz_possible_points[rule.pk] - getattr(
                enrollment, f"{prefix}_unresolved_points", 0
            )
            earned = getattr(enrollment, f"{prefix}_earned", 0)
        denominator = max(int(denominator), 0)
        percent = (Decimal(earned) * Decimal("100") / Decimal(denominator)) if denominator else Decimal("0")
        metrics.append({
            "rule_id": rule.pk,
            "metric": rule.metric,
            "quiz_type_id": rule.quiz_type_id,
            "percent": percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "minimum_percent": rule.minimum_percent,
            "passed": percent >= rule.minimum_percent,
            "denominator": denominator,
            "earned": int(earned),
        })

    errors = list(plan.grading_errors)
    unresolved_count = int(getattr(enrollment, "unresolved_question_count", 0))
    unresolved_points = int(getattr(enrollment, "unresolved_question_points", 0))
    if unresolved_count:
        errors.append(_("%(count)d unresolved manual-grade question(s), excluding %(points)d point(s).") % {
            "count": unresolved_count,
            "points": unresolved_points,
        })
    if plan.published_lesson_count == 0:
        errors.append(_("This course has no published lectures."))

    applicable = [metric for metric in metrics if metric["metric"] != PromotionRule.Metric.ATTENDANCE or not online]
    weighted_score = Decimal("0")
    if applicable:
        applicable_rule_ids = {metric["rule_id"] for metric in applicable}
        total_weight = sum(rule.weight_percent for rule in plan.rules if rule.pk in applicable_rule_ids)
        for metric in applicable:
            rule = next(rule for rule in plan.rules if rule.pk == metric["rule_id"])
            weighted_score += metric["percent"] * rule.weight_percent / total_weight

    if errors:
        status = EvaluationResult.Status.UNEVALUABLE
    else:
        passed = bool(applicable) and all(metric["passed"] for metric in applicable) and weighted_score >= plan.formula.overall_pass_percent
        status = EvaluationResult.Status.PASS if passed else EvaluationResult.Status.FAIL
    return {
        "enrollment": enrollment,
        "student": enrollment.student,
        "course_offering": plan.course_offering,
        "computed_score": weighted_score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "computed_status": status,
        "metrics": metrics,
        "grading_errors": errors,
        "unresolved_question_count": unresolved_count,
        "unresolved_question_points": unresolved_points,
        "published_lesson_count": plan.published_lesson_count,
    }


def _metric_snapshot(row: dict) -> dict:
    return {
        "course_offering_id": row["course_offering"].pk,
        "course_name": row["course_offering"].course.name,
        "metrics": [
            {
                **metric,
                "percent": str(metric["percent"]),
                "minimum_percent": str(metric["minimum_percent"]),
            }
            for metric in row["metrics"]
        ],
        "published_lesson_count": row["published_lesson_count"],
        "grading_errors": row["grading_errors"],
        "unresolved_question_count": row["unresolved_question_count"],
        "unresolved_question_points": row["unresolved_question_points"],
    }


def _normal_enrollment_ids(scope):
    return Enrollment.objects.filter(
        academic_year_level=scope,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    ).values("pk")


def _persist_course_result_batch(formula, plan, enrollments) -> tuple[int, int]:
    enrollment_ids = [enrollment.pk for enrollment in enrollments]
    consumed_enrollment_ids = set(EvaluationResult.objects.filter(
        formula=formula,
        course_offering__isnull=True,
        promotion_history__isnull=False,
        enrollment_id__in=enrollment_ids,
    ).values_list("enrollment_id", flat=True))
    existing = {
        result.enrollment_id: result
        for result in EvaluationResult.objects.filter(
            formula=formula,
            course_offering=plan.course_offering,
            enrollment_id__in=enrollment_ids,
        )
    }
    new_results = []
    changed_results = []
    for enrollment in enrollments:
        if enrollment.pk in consumed_enrollment_ids:
            continue
        row = evaluate_enrollment(enrollment, plan)
        result = existing.get(enrollment.pk)
        if result:
            result.computed_score = row["computed_score"]
            result.computed_status = row["computed_status"]
            result.final_status = row["computed_status"]
            result.metric_snapshot = _metric_snapshot(row)
            result.override_note = ""
            result.overridden_by = None
            result.overridden_at = None
            changed_results.append(result)
        else:
            new_results.append(EvaluationResult(
                formula=formula,
                enrollment=enrollment,
                course_offering=plan.course_offering,
                computed_score=row["computed_score"],
                computed_status=row["computed_status"],
                final_status=row["computed_status"],
                metric_snapshot=_metric_snapshot(row),
            ))
    if new_results:
        EvaluationResult.objects.bulk_create(new_results, batch_size=500)
    if changed_results:
        EvaluationResult.objects.bulk_update(
            changed_results,
            [
                "computed_score", "computed_status", "final_status", "metric_snapshot",
                "override_note", "overridden_by", "overridden_at", "calculated_at",
            ],
            batch_size=500,
        )
    return len(new_results), len(changed_results)


def _persist_aggregate_batch(formula, results_by_enrollment):
    existing = {
        result.enrollment_id: result
        for result in EvaluationResult.objects.filter(
            formula=formula,
            course_offering__isnull=True,
            enrollment_id__in=list(results_by_enrollment),
        )
    }
    new_results = []
    changed_results = []
    for enrollment_id, course_results in results_by_enrollment.items():
        failed = sum(result.computed_status == EvaluationResult.Status.FAIL for result in course_results)
        unevaluable = any(result.computed_status == EvaluationResult.Status.UNEVALUABLE for result in course_results)
        score = sum((result.computed_score for result in course_results), Decimal("0")) / len(course_results)
        status = EvaluationResult.Status.UNEVALUABLE if unevaluable else (
            EvaluationResult.Status.PASS if failed == 0 else EvaluationResult.Status.FAIL
        )
        snapshot = {
            "failed_course_count": failed,
            "repeat_threshold": formula.failed_courses_repeat_threshold,
            "courses": [
                {
                    "result_id": result.pk,
                    "offering_id": result.course_offering_id,
                    "course_id": result.course_offering.course_id,
                    "course_name": result.course_offering.course.name,
                    "score": str(result.computed_score),
                    "computed_status": result.computed_status,
                    "final_status": result.final_status,
                    "metric_snapshot": result.metric_snapshot,
                }
                for result in course_results
            ],
        }
        result = existing.get(enrollment_id)
        if result:
            if hasattr(result, "promotion_history"):
                continue
            result.computed_score = score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.computed_status = status
            result.final_status = status
            result.metric_snapshot = snapshot
            result.override_note = ""
            result.overridden_by = None
            result.overridden_at = None
            changed_results.append(result)
        else:
            new_results.append(EvaluationResult(
                formula=formula,
                enrollment_id=enrollment_id,
                computed_score=score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                computed_status=status,
                final_status=status,
                metric_snapshot=snapshot,
            ))
    if new_results:
        EvaluationResult.objects.bulk_create(new_results, batch_size=500)
    if changed_results:
        EvaluationResult.objects.bulk_update(
            changed_results,
            [
                "computed_score", "computed_status", "final_status", "metric_snapshot",
                "override_note", "overridden_by", "overridden_at", "calculated_at",
            ],
            batch_size=500,
        )


def _recalculate_scope_results(scope) -> tuple[int, int]:
    """Recalculate effective course formulas and the all-course aggregate in batches."""
    formulas = list(PromotionFormula.objects.select_for_update().filter(
        academic_year_level=scope,
    ).prefetch_related("rules", "course_offering"))
    global_formula = next((formula for formula in formulas if formula.course_offering_id is None), None)
    specific_formulas = {
        formula.course_offering_id: formula
        for formula in formulas
        if formula.course_offering_id is not None
    }
    offerings = list(CourseOffering.objects.filter(
        academic_year_level=scope,
        status=PublicationStatus.PUBLISHED,
    ).select_related("course", "academic_year_level__academic_year", "academic_year_level__level").order_by("course__name", "pk"))
    if not offerings:
        raise ValidationError(_("No published course offerings are available for this formula."))
    if global_formula is None and not specific_formulas:
        return 0, 0

    active_formula_ids = [formula.pk for formula in formulas]
    current_enrollments = _normal_enrollment_ids(scope)
    EvaluationResult.objects.filter(
        formula_id__in=active_formula_ids,
        course_offering__isnull=False,
        promotion_history__isnull=True,
    ).exclude(enrollment_id__in=current_enrollments).delete()
    if global_formula is not None and specific_formulas:
        EvaluationResult.objects.filter(
            formula=global_formula,
            course_offering_id__in=list(specific_formulas),
            promotion_history__isnull=True,
        ).delete()

    total_created = total_updated = 0
    for offering in offerings:
        effective_formula = specific_formulas.get(offering.pk) or global_formula
        if effective_formula is None:
            continue
        plan = build_evaluation_plan(effective_formula, course_offering=offering)
        batch = []
        for enrollment in evaluation_enrollments(plan).order_by("pk").iterator(chunk_size=500):
            batch.append(enrollment)
            if len(batch) >= 500:
                created, updated = _persist_course_result_batch(effective_formula, plan, batch)
                total_created += created
                total_updated += updated
                batch.clear()
        if batch:
            created, updated = _persist_course_result_batch(effective_formula, plan, batch)
            total_created += created
            total_updated += updated

    if global_formula is not None:
        enrollment_ids = []
        for enrollment_row in _normal_enrollment_ids(scope).order_by("pk").iterator(chunk_size=500):
            enrollment_ids.append(enrollment_row["pk"])
            if len(enrollment_ids) >= 500:
                course_results = EvaluationResult.objects.filter(
                    formula_id__in=active_formula_ids,
                    enrollment_id__in=enrollment_ids,
                    course_offering__isnull=False,
                ).select_related("course_offering__course")
                grouped = {enrollment_id: [] for enrollment_id in enrollment_ids}
                for course_result in course_results:
                    grouped[course_result.enrollment_id].append(course_result)
                _persist_aggregate_batch(global_formula, grouped)
                enrollment_ids.clear()
        if enrollment_ids:
            course_results = EvaluationResult.objects.filter(
                formula_id__in=active_formula_ids,
                enrollment_id__in=enrollment_ids,
                course_offering__isnull=False,
            ).select_related("course_offering__course")
            grouped = {enrollment_id: [] for enrollment_id in enrollment_ids}
            for course_result in course_results:
                grouped[course_result.enrollment_id].append(course_result)
            _persist_aggregate_batch(global_formula, grouped)
    else:
        EvaluationResult.objects.filter(
            formula_id__in=active_formula_ids,
            course_offering__isnull=True,
            promotion_history__isnull=True,
        ).delete()
    return total_created, total_updated


@transaction.atomic
def save_formula_and_results(
    *, scope, course_offering, overall_pass_percent, evaluation_starts_on,
    evaluation_ends_on, failed_courses_repeat_threshold, rules, actor,
):
    """Replace one formula and recalculate effective course/aggregate results."""
    formula = save_promotion_formula(
        scope=scope,
        course_offering=course_offering,
        overall_pass_percent=overall_pass_percent,
        evaluation_starts_on=evaluation_starts_on,
        evaluation_ends_on=evaluation_ends_on,
        failed_courses_repeat_threshold=failed_courses_repeat_threshold,
        rules=rules,
        actor=actor,
    )
    PromotionFormula.objects.select_for_update().get(pk=formula.pk)
    locked_formula = PromotionFormula.objects.select_related(
        "academic_year_level", "course_offering"
    ).get(pk=formula.pk)
    counts = _recalculate_scope_results(scope)
    return locked_formula, counts


@transaction.atomic
def override_evaluation_result(*, result_id: int, final_status: str, note: str, actor):
    if not actor.is_authenticated or not actor.role or actor.role.role != "admin":
        raise PermissionError(_("Only administrators can override evaluation results."))
    if final_status not in EvaluationResult.Status.values:
        raise ValueError(_("Invalid evaluation status."))
    result = EvaluationResult.objects.select_for_update().select_related("formula").get(pk=result_id)
    if hasattr(result, "promotion_history"):
        raise ValidationError(_("A promoted result is immutable and cannot be overridden."))
    result.final_status = final_status
    result.override_note = note.strip()
    result.overridden_by = actor
    result.overridden_at = timezone.now()
    result.save(update_fields=["final_status", "override_note", "overridden_by", "overridden_at"])
    if result.course_offering_id:
        global_formula = PromotionFormula.objects.filter(
            academic_year_level_id=result.formula.academic_year_level_id,
            course_offering__isnull=True,
        ).first()
        if global_formula:
            formula_ids = PromotionFormula.objects.filter(
                academic_year_level_id=result.formula.academic_year_level_id,
            ).values_list("pk", flat=True)
            course_results = list(EvaluationResult.objects.filter(
                formula_id__in=formula_ids,
                enrollment_id=result.enrollment_id,
                course_offering__isnull=False,
            ).select_related("course_offering__course"))
            _persist_aggregate_batch(global_formula, {result.enrollment_id: course_results})
    return result
