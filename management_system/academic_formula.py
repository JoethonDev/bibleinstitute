from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _
from django.utils import timezone

from .models import AcademicYearLevel, PromotionFormula, PromotionRule, User


def _require_admin(actor: User) -> None:
    if not actor.is_authenticated or not actor.role or actor.role.role != "admin":
        raise PermissionDenied(_("Only administrators can manage promotion formulas."))


def normalize_formula_rules(rules: list[dict]) -> list[dict]:
    active_rules = [dict(rule) for rule in rules if rule and not rule.get("DELETE")]
    if not active_rules:
        raise ValidationError(_("Add at least one promotion rule."))

    if len(active_rules) == 1 and active_rules[0].get("weight_percent") in (None, ""):
        active_rules[0]["weight_percent"] = Decimal("100")

    for rule in active_rules:
        if rule.get("weight_percent") in (None, ""):
            raise ValidationError(_("Enter a weight for every rule when there is more than one rule."))
        if rule.get("minimum_percent") in (None, ""):
            rule["minimum_percent"] = Decimal("0")
    return active_rules


def validate_formula_rules(rules: list[dict]) -> None:
    rules = normalize_formula_rules(rules)

    total_weight = Decimal("0")
    attendance_count = 0
    all_quiz_count = 0
    quiz_type_ids = set()

    for rule in rules:
        metric = rule.get("metric")
        quiz_type = rule.get("quiz_type")
        try:
            weight = Decimal(rule.get("weight_percent", 0))
            minimum = Decimal(rule.get("minimum_percent", 0))
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError(_("Rule percentages must be valid numbers."))
        if not 0 <= weight <= 100 or not 0 <= minimum <= 100:
            raise ValidationError(_("Rule percentages must be between 0 and 100."))
        if metric == PromotionRule.Metric.ATTENDANCE:
            attendance_count += 1
            if quiz_type is not None:
                raise ValidationError(_("Attendance rules cannot select a quiz type."))
        elif metric == PromotionRule.Metric.QUIZ:
            if quiz_type is None:
                all_quiz_count += 1
            elif quiz_type.pk in quiz_type_ids:
                raise ValidationError(_("A quiz type cannot appear in more than one rule."))
            else:
                quiz_type_ids.add(quiz_type.pk)
        else:
            raise ValidationError(_("Select a valid promotion rule metric."))
        total_weight += weight

    if attendance_count > 1:
        raise ValidationError(_("Only one attendance rule is allowed."))
    if all_quiz_count > 1 or (all_quiz_count and quiz_type_ids):
        raise ValidationError(_("All-quizzes and specific quiz-type rules cannot overlap."))
    if total_weight != Decimal("100"):
        raise ValidationError(_("Rule weights must total exactly 100 percent."))


@transaction.atomic
def save_promotion_formula(
    *,
    scope: AcademicYearLevel,
    course_offering,
    overall_pass_percent: Decimal,
    evaluation_starts_on: date,
    evaluation_ends_on: date,
    failed_courses_repeat_threshold: int,
    rules: list[dict],
    actor: User,
) -> PromotionFormula:
    _require_admin(actor)
    if not scope.academic_year.is_active:
        raise ValidationError(_("Promotion formulas can only be saved for the active academic year."))
    if course_offering and course_offering.academic_year_level_id != scope.pk:
        raise ValidationError(_("Course offering must belong to the selected academic scope."))
    if not 0 <= overall_pass_percent <= 100:
        raise ValidationError(_("Overall passing threshold must be between 0 and 100."))
    if failed_courses_repeat_threshold < 1:
        raise ValidationError(_("The repeat threshold must be at least 1."))
    year = scope.academic_year
    if evaluation_starts_on > evaluation_ends_on:
        raise ValidationError(_("Evaluation end date must not be before the start date."))
    if not year.starts_on <= evaluation_starts_on <= year.ends_on:
        raise ValidationError(_("Evaluation start date must be inside the academic year."))
    if not year.starts_on <= evaluation_ends_on <= min(timezone.localdate(), year.ends_on):
        raise ValidationError(_("Evaluation end date must be inside the academic year and cannot be in the future."))
    rules = normalize_formula_rules(rules)
    validate_formula_rules(rules)

    formula, created = PromotionFormula.objects.select_for_update().get_or_create(
        academic_year_level=scope,
        course_offering=course_offering,
        defaults={
            "overall_pass_percent": overall_pass_percent,
            "evaluation_starts_on": evaluation_starts_on,
            "evaluation_ends_on": evaluation_ends_on,
            "failed_courses_repeat_threshold": failed_courses_repeat_threshold,
            "created_by": actor,
            "updated_by": actor,
        },
    )
    if not created:
        formula.overall_pass_percent = overall_pass_percent
        formula.evaluation_starts_on = evaluation_starts_on
        formula.evaluation_ends_on = evaluation_ends_on
        formula.failed_courses_repeat_threshold = failed_courses_repeat_threshold
        formula.updated_by = actor
        formula.save(update_fields=[
            "overall_pass_percent", "evaluation_starts_on", "evaluation_ends_on",
            "failed_courses_repeat_threshold", "updated_by", "updated_at",
        ])
        formula.rules.all().delete()

    PromotionRule.objects.bulk_create([
        PromotionRule(
            formula=formula,
            metric=rule["metric"],
            quiz_type=rule.get("quiz_type"),
            weight_percent=rule["weight_percent"],
            minimum_percent=rule["minimum_percent"],
            ordering=index,
        )
        for index, rule in enumerate(rules, start=1)
    ])
    return formula
