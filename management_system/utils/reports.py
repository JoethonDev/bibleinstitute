from collections import defaultdict

from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce

from ..models import AttendanceRecord, Enrollment, Grade, Lesson, PublicationStatus
from .attendance import get_expected_dates
from .search import normalized_contains_q


def _grade_scope_filter(academic_year_level, course_offering_id=None) -> Q:
    query = Q(quiz__course_offering__academic_year_level=academic_year_level)
    if course_offering_id:
        query &= Q(quiz__course_offering_id=course_offering_id)
    return query


def report_enrollments(academic_year_level, student_search=None, study_mode=None, course_offering_id=None):
    """Return one SQL-backed normal-enrollment row per student in a scope."""
    grade_filter = Q(student__submitted_quizzes__quiz__course_offering__academic_year_level=academic_year_level)
    if course_offering_id:
        grade_filter &= Q(student__submitted_quizzes__quiz__course_offering_id=course_offering_id)
    enrollments = Enrollment.objects.filter(
        academic_year_level=academic_year_level,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
    ).select_related(
        "student", "student__role", "academic_year_level__academic_year", "academic_year_level__level"
    ).annotate(
        grade_earned=Coalesce(Sum("student__submitted_quizzes__total_grade", filter=grade_filter), Value(0)),
        grade_available=Coalesce(Sum("student__submitted_quizzes__quiz__total_grade", filter=grade_filter), Value(0)),
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


def build_report_page_rows(enrollments, academic_year_level, course_offering_id=None):
    """Build rows for an already SQL-paginated enrollment page.

    Grades and attendance are loaded in two bounded queries for the complete
    page, never once per student.
    """
    enrollments = list(enrollments)
    if not enrollments:
        return []
    student_ids = [enrollment.student_id for enrollment in enrollments]
    grade_filter = _grade_scope_filter(academic_year_level, course_offering_id)
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
    records = records.values("student_id", "course_offering_id", "attendance_date", "action")
    attendance_by_student = defaultdict(lambda: {"entrance": set(), "exit": set()})
    for record in records:
        attendance_by_student[record["student_id"]][record["action"]].add(
            (record["course_offering_id"], record["attendance_date"])
        )

    lesson_counts = Lesson.objects.filter(
        course_offering__academic_year_level=academic_year_level,
        status=PublicationStatus.PUBLISHED,
    )
    if course_offering_id:
        lesson_counts = lesson_counts.filter(course_offering_id=course_offering_id)
    expected_count = lesson_counts.count()
    rows = []
    for enrollment in enrollments:
        student = enrollment.student
        details = grade_details[student.pk]
        earned = sum(detail["grade"] for detail in details)
        available = sum(detail["total"] for detail in details)
        if student.study_mode == "online":
            expected = valid = invalid = absent = 0
        else:
            attendance = attendance_by_student[student.pk]
            entrance = attendance["entrance"]
            exits = attendance["exit"]
            valid_dates = entrance & exits
            invalid_dates = (entrance - valid_dates) | (exits - valid_dates)
            absent_dates = max(expected_count - len(valid_dates), 0)
            expected = expected_count
            valid = len(valid_dates)
            invalid = len(invalid_dates)
            absent = absent_dates
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
            "attendance_rate": round(valid / expected * 100, 1) if expected else 0,
            "absence_rate": round(absent / expected * 100, 1) if expected else 0,
            "grade_details": details,
        })
    return rows


def build_report_data(academic_year_level, student_id=None, study_mode=None, course_offering_id=None):
    """Build report rows in bounded pages for exports and legacy callers."""
    return list(iter_report_data(academic_year_level, student_id, study_mode, course_offering_id))


def iter_report_data(academic_year_level, student_id=None, study_mode=None, course_offering_id=None):
    """Yield report rows in bounded pages for streaming and workbook exports."""
    search = str(student_id) if student_id else None
    queryset = report_enrollments(academic_year_level, search, study_mode, course_offering_id)
    page = []
    for enrollment in queryset.iterator(chunk_size=500):
        page.append(enrollment)
        if len(page) == 500:
            yield from build_report_page_rows(page, academic_year_level, course_offering_id)
            page.clear()
    if page:
        yield from build_report_page_rows(page, academic_year_level, course_offering_id)
