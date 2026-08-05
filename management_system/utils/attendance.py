from datetime import datetime, timedelta
import zoneinfo
from django.conf import settings
from django.db.models import Exists, OuterRef

from ..models import AcademicHoliday, AcademicYearLevelMeeting, AttendanceRecord, Enrollment, Lesson, PublicationStatus


def get_student_attendance_context(student, check_date):
    enrollment = Enrollment.objects.filter(
        student=student,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level__academic_year__is_active=True,
        academic_year_level__academic_year__starts_on__lte=check_date,
        academic_year_level__academic_year__ends_on__gte=check_date,
    ).select_related("academic_year_level").order_by(
        "-academic_year_level__academic_year__ordering", "-enrolled_at"
    ).first()
    if not enrollment:
        return None, None
    meeting = AcademicYearLevelMeeting.objects.filter(
        academic_year_level_id=enrollment.academic_year_level_id,
        meeting_date=check_date,
    ).select_related("course_offering").first()
    return enrollment.academic_year_level, meeting.course_offering if meeting else None


def assign_unassigned_attendance(meeting) -> int:
    existing = AttendanceRecord.objects.filter(
        student_id=OuterRef("student_id"),
        course_offering_id=meeting.course_offering_id,
        attendance_date=OuterRef("attendance_date"),
        action=OuterRef("action"),
    )
    return AttendanceRecord.objects.filter(
        course_offering__isnull=True,
        attendance_date=meeting.meeting_date,
        student__enrollments__academic_year_level_id=meeting.academic_year_level_id,
    ).filter(~Exists(existing)).update(course_offering_id=meeting.course_offering_id)

def get_expected_dates(academic_year_level, through_date=None):
    application_tz = zoneinfo.ZoneInfo(settings.TIME_ZONE)
    today = datetime.now(application_tz).date()
    year = academic_year_level.academic_year

    effective_end = min(year.ends_on, through_date or today)

    if effective_end < year.starts_on:
        return []

    holidays = set(AcademicHoliday.objects.filter(
        academic_year=year,
    ).values_list("date", flat=True))

    weekdays = academic_year_level.meeting_weekdays
    expected = []
    current = year.starts_on
    while current <= effective_end:
        if current.weekday() in weekdays and current not in holidays:
            expected.append(current)
        current += timedelta(days=1)
    return expected


def get_course_attendance_summary(student, course_offering, through_date=None, starts_on=None):
    """Return attendance against the offering's current published lesson count."""
    if student.study_mode == "online":
        return {"expected": 0, "valid": 0, "invalid": 0, "absent": 0, "attendance_rate": None, "absence_rate": None}

    year = course_offering.academic_year_level.academic_year
    starts_on = starts_on or year.starts_on
    effective_end = min(through_date or year.ends_on, year.ends_on)
    records = AttendanceRecord.objects.filter(
        student=student,
        course_offering=course_offering,
        attendance_date__gte=starts_on,
        attendance_date__lte=effective_end,
    )
    entrance_dates = set(records.filter(action="entrance").values_list("attendance_date", flat=True))
    exit_dates = set(records.filter(action="exit").values_list("attendance_date", flat=True))

    valid = entrance_dates & exit_dates
    invalid = (entrance_dates - valid) | (exit_dates - valid)
    expected_count = Lesson.objects.filter(
        course_offering=course_offering,
        status=PublicationStatus.PUBLISHED,
    ).count()
    absent_count = max(expected_count - len(valid), 0)
    return {
        "expected": expected_count,
        "valid": len(valid),
        "invalid": len(invalid),
        "absent": absent_count,
        "attendance_rate": len(valid) / expected_count if expected_count else 0,
        "absence_rate": absent_count / expected_count if expected_count else 0,
    }


def is_expected_date(academic_year_level, check_date):
    year = academic_year_level.academic_year
    if check_date < year.starts_on or check_date > year.ends_on:
        return False
    if check_date.weekday() not in academic_year_level.meeting_weekdays:
        return False
    if AcademicHoliday.objects.filter(academic_year=year, date=check_date).exists():
        return False
    return True
