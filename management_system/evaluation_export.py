"""Bounded XLSX export for saved course-level evaluation results.

Schema discovery and workbook rows read only the saved result snapshots; the
aggregate/scoped workbook is produced by the durable evaluation-export job and
stored as a private, owner-authorized artifact.
"""

from __future__ import annotations

import re
from tempfile import SpooledTemporaryFile

import openpyxl
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _

from .models import (
    AdminOperationJob,
    CourseOffering,
    EvaluationResult,
    PromotionFormula,
    PromotionRule,
    QuizType,
)
from .xlsx_export import unique_sheet_title, xlsx_safe_cell

from .admin_operations import (
    AdminOperationError,
    assert_admin_operation_progress,
    create_admin_operation,
    finalize_admin_operation_export,
    require_admin_operation_actor,
)
from .utils.decorators import can_view_reports



def _formula_ids(formula) -> list[int]:
    """Return the immutable result-formula set for one requested export."""
    if formula.course_offering_id is not None:
        return [formula.pk]
    return list(PromotionFormula.objects.filter(
        academic_year_level_id=formula.academic_year_level_id,
    ).values_list("pk", flat=True))


def _results(formula, offering_id=None, formula_ids=None):
    formula_ids = formula_ids or _formula_ids(formula)
    query = EvaluationResult.objects.filter(
        formula_id__in=formula_ids,
        course_offering__isnull=False,
    )
    if offering_id is not None:
        query = query.filter(course_offering_id=offering_id)
    return query.select_related(
        "enrollment__student",
        "course_offering__course",
        "course_offering__academic_year_level__academic_year",
        "course_offering__academic_year_level__level",
        "overridden_by",
        "formula",
    ).order_by(
        "course_offering__course__name",
        "enrollment__student__last_name",
        "enrollment__student__first_name",
        "enrollment__student__username",
    )


def _offering_ids(formula, formula_ids) -> list[int]:
    """Return the bounded set of offerings that have saved course results."""
    return list(
        _results(formula, formula_ids=formula_ids)
        .values_list("course_offering_id", flat=True)
        .distinct()
        .order_by("course_offering_id")
    )


def _metric_label(metric, quiz_types):
    if metric.get("metric") == PromotionRule.Metric.ATTENDANCE:
        return str(_("Attendance"))
    return quiz_types.get(metric.get("quiz_type_id"), str(_("Quiz")))


def _metric_schema(formula, offering_id, quiz_types, formula_ids=None):
    """Discover metric columns from snapshots only; no joined result rows."""
    schema = {}
    snapshots = _results(formula, offering_id, formula_ids=formula_ids).values_list("metric_snapshot", flat=True)
    for snapshot in snapshots.iterator(chunk_size=500):
        for metric in (snapshot or {}).get("metrics", []):
            key = (metric.get("metric"), metric.get("quiz_type_id"))
            schema.setdefault(key, metric)
    return sorted(
        schema.values(),
        key=lambda metric: (
            metric.get("metric") == PromotionRule.Metric.ATTENDANCE,
            metric.get("quiz_type_id") or 0,
        ),
    )


def _metric_headers(schema, quiz_types):
    headers = []
    for metric in schema:
        label = _metric_label(metric, quiz_types)
        headers.extend([
            f"{label} {_('Earned')}",
            f"{label} {_('Denominator')}",
            f"{label} %",
            f"{label} {_('Min %')}",
            f"{label} {_('Passed')}",
        ])
    return headers


def _status_display(result, field):
    method = getattr(result, f"get_{field}_display", None)
    return method() if method else getattr(result, field)


def _iter_with_progress(rows, advance):
    """Yield rows while reporting progress once per bounded iterator batch."""
    pending = 0
    for row in rows:
        yield row
        pending += 1
        if pending >= 500:
            advance(pending)
            pending = 0
    if pending:
        advance(pending)


def build_evaluation_workbook(formula: PromotionFormula, progress=None) -> SpooledTemporaryFile:
    """Build a write-only workbook without materializing the result population."""

    workbook = openpyxl.Workbook(write_only=True)
    formula_ids = _formula_ids(formula)
    quiz_types = {
        item.pk: item.name_en or item.code
        for item in QuizType.objects.only("pk", "name_en", "code").iterator(chunk_size=100)
    }
    offering_ids = _offering_ids(formula, formula_ids)
    result_count = _results(formula, formula_ids=formula_ids).count()
    # One summary row plus one course row per saved result.
    total_units = result_count * 2
    processed_units = 0

    def advance(count: int) -> None:
        nonlocal processed_units
        processed_units += count
        if progress is not None:
            progress(processed=processed_units, total=total_units)

    seen_titles: set[str] = set()

    if not offering_ids:
        sheet = workbook.create_sheet(unique_sheet_title(str(_("No Results")), seen_titles))
        sheet.append([xlsx_safe_cell(_("No course-level evaluation results found for this formula."))])
        result = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        workbook.save(result)
        result.seek(0)
        return result

    summary = workbook.create_sheet(unique_sheet_title(str(_("Summary")), seen_titles))
    summary.append([xlsx_safe_cell(cell) for cell in [
        _("Course Offering"), _("Course"), _("Student ID"), _("Student Name"),
        _("Score"), _("Status"), _("Published Lectures"),
        _("Attendance Score"), _("Attendance %"),
        _("Repeat Threshold"), _("Evaluation Start"), _("Evaluation End"),
    ]])
    summary_rows = _iter_with_progress(
        _results(formula, formula_ids=formula_ids).iterator(chunk_size=500),
        advance,
    )
    for result_row in summary_rows:
        snapshot = result_row.metric_snapshot or {}
        attendance = next(
            (metric for metric in snapshot.get("metrics", []) if metric.get("metric") == PromotionRule.Metric.ATTENDANCE),
            {},
        )
        student = result_row.enrollment.student
        summary.append([
            xlsx_safe_cell(result_row.course_offering_id),
            xlsx_safe_cell(snapshot.get("course_name", result_row.course_offering.course.name)),
            xlsx_safe_cell(student.pk),
            xlsx_safe_cell(student.get_full_name() or student.username),
            xlsx_safe_cell(str(result_row.computed_score)),
            xlsx_safe_cell(_status_display(result_row, "computed_status")),
            xlsx_safe_cell(snapshot.get("published_lesson_count", "")),
            xlsx_safe_cell(attendance.get("earned", "")),
            xlsx_safe_cell(attendance.get("percent", "")),
            xlsx_safe_cell(result_row.formula.failed_courses_repeat_threshold),
            xlsx_safe_cell(result_row.formula.evaluation_starts_on),
            xlsx_safe_cell(result_row.formula.evaluation_ends_on),
        ])

    for offering_id in offering_ids:
        offering = CourseOffering.objects.select_related(
            "course", "academic_year_level__academic_year", "academic_year_level__level"
        ).get(pk=offering_id)
        course = offering.course
        scope = offering.academic_year_level
        sheet = workbook.create_sheet(unique_sheet_title(f"{course.name} - {scope.level.display_name}", seen_titles))
        schema = _metric_schema(formula, offering_id, quiz_types, formula_ids=formula_ids)
        sheet.append([xlsx_safe_cell(cell) for cell in [
            _("Student ID"), _("Student Name"), _("Username"), _("Academic Year"),
            _("Level"), _("Course"), _("Evaluation Start"), _("Evaluation End"),
            _("Published Lectures"), *_metric_headers(schema, quiz_types),
            _("Weighted Score"), _("Computed Status"), _("Final Status"),
            _("Override Note"), _("Evaluation Errors"),
        ]])
        offering_rows = _iter_with_progress(
            _results(formula, offering_id, formula_ids=formula_ids).iterator(chunk_size=500),
            advance,
        )
        for result_row in offering_rows:
            snapshot = result_row.metric_snapshot or {}
            metrics = {
                (metric.get("metric"), metric.get("quiz_type_id")): metric
                for metric in snapshot.get("metrics", [])
            }
            student = result_row.enrollment.student
            row = [
                xlsx_safe_cell(student.pk),
                xlsx_safe_cell(student.get_full_name() or student.username),
                xlsx_safe_cell(student.username),
                xlsx_safe_cell(scope.academic_year.name),
                xlsx_safe_cell(scope.level.display_name),
                xlsx_safe_cell(course.name),
                xlsx_safe_cell(result_row.formula.evaluation_starts_on),
                xlsx_safe_cell(result_row.formula.evaluation_ends_on),
                xlsx_safe_cell(snapshot.get("published_lesson_count", "")),
            ]
            for schema_metric in schema:
                metric = metrics.get((schema_metric.get("metric"), schema_metric.get("quiz_type_id")), {})
                row.extend([
                    xlsx_safe_cell(metric.get("earned", "")),
                    xlsx_safe_cell(metric.get("denominator", "")),
                    xlsx_safe_cell(metric.get("percent", "")),
                    xlsx_safe_cell(metric.get("minimum_percent", "")),
                    xlsx_safe_cell(metric.get("passed", "")),
                ])
            row.extend([
                xlsx_safe_cell(str(result_row.computed_score)),
                xlsx_safe_cell(_status_display(result_row, "computed_status")),
                xlsx_safe_cell(_status_display(result_row, "final_status")),
                xlsx_safe_cell(result_row.override_note),
                xlsx_safe_cell("; ".join(snapshot.get("grading_errors", []))),
            ])
            sheet.append(row)

    result = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    workbook.save(result)
    result.seek(0)
    return result


def _export_filename(formula) -> str:
    scope = formula.academic_year_level
    raw = f"evaluation_{scope.academic_year.name}-{scope.level.display_name}"
    safe = re.sub(r"[^0-9A-Za-z_\-\u0600-\u06FF]+", "_", raw).strip("_") or "evaluation"
    return f"{safe}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.xlsx"


def enqueue_evaluation_export(*, actor, formula: PromotionFormula, language: str = "ar") -> AdminOperationJob:
    """Freeze the requested aggregate export and create one idempotent job."""


    result_count = _results(formula).count()
    return create_admin_operation(
        actor=actor,
        kind=AdminOperationJob.Kind.EVALUATION_EXPORT,
        input={
            "formula_id": formula.pk,
            "scope_id": formula.academic_year_level_id,
            "offering_id": formula.course_offering_id,
            "result_count": result_count,
        },
        conflict_key=f"evaluation-export:{formula.pk}",
        scope=formula.academic_year_level,
        offering=formula.course_offering,
        language=language,
        total=result_count,
    )


def execute_evaluation_export(job: AdminOperationJob, token) -> dict:
    """J01 executor: build the aggregate workbook and store one private artifact."""




    require_admin_operation_actor(job)
    if not can_view_reports(job.actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to export evaluation results."),
        )
    payload = job.input or {}
    formula = PromotionFormula.objects.select_related(
        "academic_year_level__academic_year", "academic_year_level__level",
    ).filter(
        pk=payload.get("formula_id"),
        academic_year_level_id=payload.get("scope_id"),
        course_offering__isnull=True,
    ).first()
    if formula is None:
        raise AdminOperationError("formula_missing", _("The promotion formula no longer exists."))

    last = {"processed": 0, "total": int(job.total or 0)}

    def progress(*, processed, total):
        last["processed"] = processed
        last["total"] = total
        assert_admin_operation_progress(
            job.pk,
            token,
            processed=processed,
            succeeded=processed,
            failed=0,
            skipped=0,
            total=total,
        )

    with translation.override(job.language or "ar"):
        filename = _export_filename(formula)
        workbook = build_evaluation_workbook(formula, progress=progress)
        finalize_admin_operation_export(job, token, workbook, filename)
    return {
        "status": AdminOperationJob.Status.COMPLETED,
        "result": {
            "filename": filename,
            "rows": last["processed"],
        },
    }
