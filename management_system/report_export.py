"""Combined grades/attendance report workbook built from one bounded pass.

The HTML page, CSV stream and this workbook all consume the canonical report
row builder. The workbook writes all three worksheets while iterating one
bounded enrollment batch at a time, so grades, attendance, the attendance
policy and the published-lesson denominator are loaded once per run instead of
once per sheet. Whole-scope workbook generation runs on the durable export job;
CSV remains a bounded streaming response.
"""

from __future__ import annotations

import hashlib
import json
from tempfile import SpooledTemporaryFile

import openpyxl
from django.core.exceptions import ValidationError
from django.utils import formats, timezone
from django.utils import translation
from django.utils.translation import gettext as _

from .models import AcademicYearLevel, AdminOperationJob, CourseOffering
from .utils.attendance import (
    AttendanceDayStatus,
    get_expected_dates,
    grade_attendance_day,
    local_scan_time,
)
from .utils.csv_export import _safe_filename
from .utils.reports import iter_report_batches, load_report_facts, report_enrollments
from .utils.search import normalize_search_text
from .xlsx_export import RolloverSheet

from .admin_operations import (
    AdminOperationError,
    assert_admin_operation_progress,
    create_admin_operation,
    finalize_admin_operation_export,
    require_admin_operation_actor,
)
from .utils.decorators import can_view_reports


REPORT_EXPORT_CONFLICT_PREFIX = "grades-report"


def normalize_report_filters(student=None, study_mode=None, course_offering_id=None) -> dict:
    """Normalize the report query filters once for both display and job input."""

    normalized_student = normalize_search_text(str(student or "").strip()[:100])
    normalized_mode = study_mode if study_mode in ("online", "offline") else ""
    offering_value = str(course_offering_id if course_offering_id is not None else "")
    normalized_offering = int(offering_value) if offering_value.isdigit() else None
    return {
        "student": normalized_student,
        "study_mode": normalized_mode,
        "course_offering_id": normalized_offering,
    }


def report_export_conflict_key(academic_year_level, *, student=None, study_mode=None, course_offering_id=None) -> str:
    """Return the idempotency key for this scope/filter combination."""

    filters = normalize_report_filters(student, study_mode, course_offering_id)
    digest = hashlib.sha256(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"{REPORT_EXPORT_CONFLICT_PREFIX}:{academic_year_level.pk}:{digest}"[:120]


def report_export_filename(academic_year_level) -> str:
    safe_name = _safe_filename(academic_year_level.academic_year.name)
    return f"report_{safe_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.xlsx"


def build_report_workbook(
    academic_year_level,
    student_id=None,
    study_mode=None,
    course_offering_id=None,
    progress=None,
) -> SpooledTemporaryFile:
    """Build the three-sheet workbook without materializing the population.

    ``progress(processed, total)`` is called after each bounded student batch
    when a durable job needs a truthful committed counter.
    """

    facts = load_report_facts(academic_year_level, course_offering_id)
    expected_dates = get_expected_dates(academic_year_level)
    total_students = None
    processed_students = 0
    if progress is not None:
        total_students = report_enrollments(
            academic_year_level,
            student_search=student_id,
            study_mode=study_mode,
            course_offering_id=course_offering_id,
        ).count()
    workbook = openpyxl.Workbook(write_only=True)
    seen_titles: set[str] = set()

    grades_sheet = RolloverSheet(
        workbook,
        str(_("Grades Summary")),
        [
            _("Student ID"), _("Username"), _("First Name"), _("Last Name"),
            _("Course"), _("Quiz"), _("Grade"), _("Total"), _("Percent"),
        ],
        seen_titles,
    )
    attendance_sheet = RolloverSheet(
        workbook,
        str(_("Attendance Summary")),
        [
            _("Student ID"), _("Username"), _("First Name"), _("Last Name"),
            _("Expected"), _("Full days"), _("Fully scanned days"), _("Partial"), _("Absent"),
            _("Attendance Score"), _("Attendance %"), _("Absence %"),
        ],
        seen_titles,
    )
    daily_sheet = RolloverSheet(
        workbook,
        str(_("Attendance Daily")),
        [
            _("Student ID"), _("Username"), _("First Name"), _("Last Name"),
            _("Date"), _("Day"), _("Entrance"), _("Exit"), _("Status"), _("Grade"),
        ],
        seen_titles,
    )

    for batch in iter_report_batches(
        academic_year_level,
        student_id=student_id,
        study_mode=study_mode,
        course_offering_id=course_offering_id,
        facts=facts,
    ):
        for row in batch.rows:
            for detail in row["grade_details"]:
                percent = round(detail["grade"] / detail["total"] * 100, 1) if detail["total"] else 0
                grades_sheet.append([
                    row["student_id"], row["username"], row["first_name"], row["last_name"],
                    detail["course_name"], detail["quiz_name"],
                    detail["grade"], detail["total"], percent,
                ])
            attendance_sheet.append([
                row["student_id"], row["username"], row["first_name"], row["last_name"],
                row["expected"], row["valid"], row["fully_scanned"], row["invalid"], row["absent"],
                row["attendance_score"], row["attendance_rate"], row["absence_rate"],
            ])
            days = batch.attendance_by_student.get(row["student_id"], {})
            for day in expected_dates:
                pair = days.get(day, {})
                entrance = pair.get("entrance")
                exit_record = pair.get("exit")
                attendance = grade_attendance_day(entrance, exit_record, facts.policy)
                daily_sheet.append([
                    row["student_id"], row["username"], row["first_name"], row["last_name"],
                    day.isoformat(), formats.date_format(day, "l"),
                    local_scan_time(entrance).strftime("%H:%M:%S") if entrance else "",
                    local_scan_time(exit_record).strftime("%H:%M:%S") if exit_record else "",
                    AttendanceDayStatus(attendance.status).label, attendance.grade,
                ])
        if progress is not None:
            processed_students += len(batch.rows)
            progress(processed=processed_students, total=total_students)

    result = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    workbook.save(result)
    result.seek(0)
    return result


def enqueue_report_export(
    *,
    actor,
    academic_year_level,
    student=None,
    study_mode=None,
    course_offering_id=None,
    language: str = "ar",
) -> AdminOperationJob:
    """Freeze the validated report filters and create one idempotent export job."""


    raw_mode = str(study_mode or "").strip()
    if raw_mode and raw_mode not in ("online", "offline"):
        raise ValidationError(_("Invalid study mode."))
    raw_offering = str(course_offering_id if course_offering_id is not None else "").strip()
    if raw_offering and not raw_offering.isdigit():
        raise ValidationError(
            _("The selected course offering does not belong to this academic scope.")
        )
    filters = normalize_report_filters(student, study_mode, course_offering_id)
    offering = None
    offering_id = filters["course_offering_id"]
    if offering_id is not None:
        offering = CourseOffering.objects.filter(
            pk=offering_id,
            academic_year_level=academic_year_level,
        ).only("pk").first()
        if offering is None:
            raise ValidationError(
                _("The selected course offering does not belong to this academic scope.")
            )
    total = report_enrollments(
        academic_year_level,
        student_search=filters["student"] or None,
        study_mode=filters["study_mode"] or None,
        course_offering_id=offering_id,
    ).count()
    return create_admin_operation(
        actor=actor,
        kind=AdminOperationJob.Kind.GRADES_REPORT_EXPORT,
        input={
            "academic_year_level_id": academic_year_level.pk,
            "student": filters["student"],
            "study_mode": filters["study_mode"],
            "course_offering_id": offering_id,
            "student_count": total,
        },
        conflict_key=report_export_conflict_key(
            academic_year_level,
            student=filters["student"],
            study_mode=filters["study_mode"],
            course_offering_id=offering_id,
        ),
        scope=academic_year_level,
        offering=offering,
        language=language,
        total=total,
    )


def execute_report_export(job: AdminOperationJob, token) -> dict:
    """J01 executor: rebuild the frozen report workbook as a private artifact."""




    require_admin_operation_actor(job)
    if not can_view_reports(job.actor):
        raise AdminOperationError(
            "not_permitted",
            _("You are no longer allowed to export reports."),
        )
    payload = job.input or {}
    scope = AcademicYearLevel.objects.filter(pk=payload.get("academic_year_level_id")).first()
    if scope is None:
        raise AdminOperationError("scope_missing", _("The academic scope no longer exists."))
    filters = normalize_report_filters(
        payload.get("student"),
        payload.get("study_mode"),
        payload.get("course_offering_id"),
    )
    offering_id = filters["course_offering_id"]
    if offering_id is not None and not CourseOffering.objects.filter(
        pk=offering_id,
        academic_year_level=scope,
    ).exists():
        raise AdminOperationError(
            "offering_missing",
            _("The selected course offering does not belong to this academic scope."),
        )

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
        filename = report_export_filename(scope)
        workbook = build_report_workbook(
            scope,
            student_id=filters["student"] or None,
            study_mode=filters["study_mode"] or None,
            course_offering_id=offering_id,
            progress=progress,
        )
        finalize_admin_operation_export(job, token, workbook, filename)
    return {
        "status": AdminOperationJob.Status.COMPLETED,
        "result": {
            "filename": filename,
            "students": last["processed"],
        },
    }
