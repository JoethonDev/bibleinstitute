from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

from django.db.models import Q

from ..models import AttendancePolicy, AttendanceRecord, Enrollment, Grade, Lesson, PublicationStatus
from .attendance import (
    DAY_GRADE_FULL,
    DAY_GRADE_PARTIAL,
    fully_scanned_attendance_day,
    grade_attendance_day,
    merge_attendance_pairs,
    pair_attendance_records,
)
from .search import normalized_contains_q

REPORT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ReportFacts:
    """Scope-wide report facts loaded once per bounded run or page."""

    policy: AttendancePolicy
    expected_count: int


@dataclass(frozen=True)
class ReportBatch:
    """One bounded enrollment page plus the facts needed to render its sheets."""

    rows: list
    attendance_by_student: dict
    facts: ReportFacts


def report_enrollments(academic_year_level, student_search=None, study_mode=None, course_offering_id=None):
    """Return one SQL-backed normal-enrollment row per student in a scope."""
    # Totals are computed by the canonical row builder from the bounded grade
    # detail query; annotating them here duplicated that work and made the
    # paginator/count SQL more expensive without any consumer.
    enrollments = Enrollment.objects.filter(
        academic_year_level=academic_year_level,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    ).select_related(
        "student", "student__role", "academic_year_level__academic_year", "academic_year_level__level"
    )
    if student_search:
        search_filter = normalized_contains_q(
            ("student__username", "student__first_name", "student__last_name", "student__email"),
            student_search,
        )
        if str(student_search).isdigit():
            search_filter |= Q(student_id=int(student_search))
        enrollments = enrollments.filter(search_filter)
    if study_mode in ("online", "offline"):
        enrollments = enrollments.filter(student__study_mode=study_mode)
    return enrollments.order_by("student__last_name", "student__first_name", "student__username")


def get_grade_summary(student, academic_year_level, course_offering_id=None):
    """Build a grade summary from one bounded grade query."""
    grades = Grade.objects.filter(
        user=student,
        **({"quiz__course_offering__academic_year_level": academic_year_level}),
    ).filter(
        **({"quiz__course_offering_id": course_offering_id} if course_offering_id else {})
    ).select_related("quiz__course_offering__course")
    earned = 0
    available = 0
    details = []
    for grade in grades:
        earned += grade.total_grade
        available += grade.quiz.total_grade
        details.append({
            "quiz_name": grade.quiz.name,
            "course_name": grade.quiz.course_offering.course.name,
            "grade": grade.total_grade,
            "total": grade.quiz.total_grade,
        })
    return {
        "earned": earned,
        "available": available,
        "percent": round(earned / available * 100, 1) if available else 0,
        "details": details,
    }


def load_report_facts(academic_year_level, course_offering_id=None) -> ReportFacts:
    """Load the scope-wide policy and lesson denominator once per report run."""

    lesson_counts = Lesson.objects.filter(
        course_offering__academic_year_level=academic_year_level,
        status=PublicationStatus.PUBLISHED,
    )
    if course_offering_id:
        lesson_counts = lesson_counts.filter(course_offering_id=course_offering_id)
    return ReportFacts(
        policy=AttendancePolicy.load(),
        expected_count=lesson_counts.count(),
    )


def _build_report_batch(enrollments, academic_year_level, course_offering_id, facts: ReportFacts) -> ReportBatch:
    """Load one bounded student batch's grades and attendance exactly once."""

    enrollments = list(enrollments)
    if not enrollments:
        return ReportBatch(rows=[], attendance_by_student={}, facts=facts)
    student_ids = [enrollment.student_id for enrollment in enrollments]
    grades = Grade.objects.filter(
        user_id__in=student_ids,
        quiz__course_offering__academic_year_level=academic_year_level,
    )
    if course_offering_id:
        grades = grades.filter(quiz__course_offering_id=course_offering_id)
    grades = grades.select_related("user", "quiz__course_offering__course").order_by(
        "user__last_name", "user__first_name", "quiz__course_offering__course__name", "quiz__name"
    )
    grade_details = defaultdict(list)
    for grade in grades:
        grade_details[grade.user_id].append({
            "quiz_name": grade.quiz.name,
            "course_name": grade.quiz.course_offering.course.name,
            "grade": grade.total_grade,
            "total": grade.quiz.total_grade,
        })

    records = AttendanceRecord.objects.filter(
        student_id__in=student_ids,
        course_offering__academic_year_level=academic_year_level,
    )
    if course_offering_id:
        records = records.filter(course_offering_id=course_offering_id)
    records = records.values("student_id", "course_offering_id", "attendance_date", "action", "source", "scanned_at")
    attendance_by_student = merge_attendance_pairs(pair_attendance_records(records))

    rows = []
    for enrollment in enrollments:
        student = enrollment.student
        details = grade_details[student.pk]
        earned = sum(detail["grade"] for detail in details)
        available = sum(detail["total"] for detail in details)
        if student.study_mode == "online":
            expected = valid = invalid = absent = 0
            attendance_score = Decimal("0")
            fully_scanned = 0
        else:
            days = attendance_by_student.get(student.pk, {})
            full = partial = 0
            fully_scanned = 0
            attendance_score = Decimal("0")
            for pair in days.values():
                fully_scanned += int(fully_scanned_attendance_day(pair))
                day = grade_attendance_day(pair.get("entrance"), pair.get("exit"), facts.policy)
                attendance_score += day.grade
                if day.grade == DAY_GRADE_FULL:
                    full += 1
                elif day.grade == DAY_GRADE_PARTIAL:
                    partial += 1
            expected = facts.expected_count
            valid = full
            invalid = partial
            absent = max(facts.expected_count - full - partial, 0)
        rows.append({
            "student_id": student.pk,
            "username": student.username,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "study_mode": student.study_mode or "",
            "level": enrollment.academic_year_level.level.display_name,
            "year_name": academic_year_level.academic_year.name,
            "grade_earned": earned,
            "grade_available": available,
            "grade_percent": round(earned / available * 100, 1) if available else 0,
            "expected": expected,
            "valid": valid,
            "invalid": invalid,
            "absent": absent,
            "fully_scanned": fully_scanned,
            "attendance_score": attendance_score,
            "attendance_rate": round(float(attendance_score) / expected * 100, 1) if expected else 0,
            "absence_rate": round(absent / expected * 100, 1) if expected else 0,
            "grade_details": details,
        })
    return ReportBatch(rows=rows, attendance_by_student=attendance_by_student, facts=facts)


def build_report_page_rows(enrollments, academic_year_level, course_offering_id=None, facts=None):
    """Build rows for an already SQL-paginated enrollment page.

    Grades, attendance, policy and the lesson denominator are loaded once for
    the complete page, never once per student.
    """
    enrollments = list(enrollments)
    if not enrollments:
        return []
    facts = facts or load_report_facts(academic_year_level, course_offering_id)
    return _build_report_batch(enrollments, academic_year_level, course_offering_id, facts).rows


def iter_report_batches(
    academic_year_level,
    student_id=None,
    study_mode=None,
    course_offering_id=None,
    facts=None,
) -> Iterator[ReportBatch]:
    """Yield bounded enrollment batches with their loaded facts exactly once.

    Report headings and all three workbook sheets consume these batches; the
    policy and lesson denominator are read once for the whole run, and only
    when the population is non-empty.
    """
    search = str(student_id) if student_id else None
    queryset = report_enrollments(academic_year_level, search, study_mode, course_offering_id)
    page = []
    for enrollment in queryset.iterator(chunk_size=REPORT_BATCH_SIZE):
        page.append(enrollment)
        if len(page) >= REPORT_BATCH_SIZE:
            if facts is None:
                facts = load_report_facts(academic_year_level, course_offering_id)
            yield _build_report_batch(page, academic_year_level, course_offering_id, facts)
            page = []
    if page:
        if facts is None:
            facts = load_report_facts(academic_year_level, course_offering_id)
        yield _build_report_batch(page, academic_year_level, course_offering_id, facts)


def iter_report_data(academic_year_level, student_id=None, study_mode=None, course_offering_id=None):
    """Yield report rows in bounded pages for streaming and workbook exports."""
    for batch in iter_report_batches(
        academic_year_level,
        student_id=student_id,
        study_mode=study_mode,
        course_offering_id=course_offering_id,
    ):
        yield from batch.rows
