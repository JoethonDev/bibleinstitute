from collections import defaultdict
from django.db.models import Sum, Q
from ..models import Enrollment, Grade, AttendanceRecord
from .attendance import get_attendance_summary


def get_grade_summary(student, academic_year, course_offering_id=None):
    grade_filter = Q(user=student, quiz__course_offering__academic_year=academic_year)
    if course_offering_id:
        grade_filter &= Q(quiz__course_offering_id=course_offering_id)
    grades = Grade.objects.filter(grade_filter).select_related("quiz__course_offering__course")
    earned = 0
    available = 0
    details = []
    for g in grades:
        earned += g.total_grade
        available += g.quiz.total_grade
        details.append({
            "quiz_name": g.quiz.name,
            "course_name": g.quiz.course_offering.course.name if g.quiz.course_offering_id else "",
            "grade": g.total_grade,
            "total": g.quiz.total_grade,
        })
    return {
        "earned": earned,
        "available": available,
        "percent": round(earned / available * 100, 1) if available else 0,
        "details": details,
    }


def build_report_data(academic_year, student_id=None, study_mode=None, course_offering_id=None):
    enrollments = Enrollment.objects.filter(
        academic_year=academic_year, status__in=["active", "completed"]
    ).select_related("student", "student__role")
    if student_id:
        enrollments = enrollments.filter(student_id=student_id)
    if study_mode:
        enrollments = enrollments.filter(student__study_mode=study_mode)
    if course_offering_id:
        enrollments = enrollments.filter(
            Q(course_offering_id=course_offering_id) | Q(course_offering__isnull=True)
        )

    # Deduplicate by student — one row per student regardless of enrollment count
    seen = set()
    rows = []
    for enrollment in enrollments:
        student = enrollment.student
        if student.pk in seen:
            continue
        seen.add(student.pk)

        grade_info = get_grade_summary(student, academic_year, course_offering_id)
        attendance_info = get_attendance_summary(student, academic_year)
        expected = attendance_info["expected"]
        valid = attendance_info["valid"]
        invalid = attendance_info["invalid"]
        absent = attendance_info["absent"]

        rows.append({
            "student_id": student.pk,
            "username": student.username,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "study_mode": student.study_mode or "",
            "level": academic_year.level,
            "year_name": academic_year.name,
            "grade_earned": grade_info["earned"],
            "grade_available": grade_info["available"],
            "grade_percent": grade_info["percent"],
            "expected": expected,
            "valid": valid,
            "invalid": invalid,
            "absent": absent,
            "attendance_rate": round(valid / expected * 100, 1) if expected else 0,
            "absence_rate": round(absent / expected * 100, 1) if expected else 0,
            "grade_details": grade_info["details"],
        })
    return rows
