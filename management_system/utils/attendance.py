from datetime import datetime, timedelta
import zoneinfo

from ..models import AcademicHoliday, AttendanceRecord, Lesson, PublicationStatus

CAIRO_TZ = "Africa/Cairo"


def get_expected_dates(academic_year_level, through_date=None):
    cairo = zoneinfo.ZoneInfo(CAIRO_TZ)
    today = datetime.now(cairo).date()
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
