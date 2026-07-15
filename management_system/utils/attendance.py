from datetime import date, timedelta
import zoneinfo

from ..models import AcademicYear, AcademicHoliday, AttendanceRecord

CAIRO_TZ = "Africa/Cairo"


def get_expected_dates(academic_year, student=None):
    cairo = zoneinfo.ZoneInfo(CAIRO_TZ)
    today = date.today()

    holidays = set(AcademicHoliday.objects.filter(
        academic_year=academic_year,
    ).values_list("date", flat=True))

    weekdays = academic_year.meeting_weekdays
    expected = []
    current = academic_year.starts_on
    while current <= min(academic_year.ends_on, today):
        if current.weekday() in weekdays and current not in holidays:
            expected.append(current)
        current += timedelta(days=1)
    return expected


def get_attendance_summary(student, academic_year):
    if student.study_mode == "online":
        return {"expected": 0, "valid": 0, "invalid": 0, "absent": 0, "attendance_rate": None, "absence_rate": None}

    records = AttendanceRecord.objects.filter(
        student=student, academic_year=academic_year
    )
    entrance_dates = set(records.filter(action="entrance").values_list("attendance_date", flat=True))
    exit_dates = set(records.filter(action="exit").values_list("attendance_date", flat=True))

    expected = get_expected_dates(academic_year, student)
    valid = entrance_dates & exit_dates
    invalid = (entrance_dates - valid) | (exit_dates - valid)
    absent = [d for d in expected if d not in entrance_dates and d not in exit_dates]

    expected_count = len(expected)
    return {
        "expected": expected_count,
        "valid": len(valid),
        "invalid": len(invalid),
        "absent": len(absent),
        "attendance_rate": len(valid) / expected_count if expected_count else 0,
        "absence_rate": len(absent) / expected_count if expected_count else 0,
    }


def is_expected_date(academic_year, check_date):
    if check_date < academic_year.starts_on or check_date > academic_year.ends_on:
        return False
    if check_date.weekday() not in academic_year.meeting_weekdays:
        return False
    if AcademicHoliday.objects.filter(academic_year=academic_year, date=check_date).exists():
        return False
    return True
