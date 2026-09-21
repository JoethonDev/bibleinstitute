from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
import zoneinfo

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, TextChoices
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    AcademicHoliday,
    AcademicYearLevel,
    AcademicYearLevelMeeting,
    AttendancePolicy,
    AttendanceRecord,
    Enrollment,
    Lesson,
    PublicationStatus,
    User,
)


DAY_GRADE_FULL = Decimal("1")
DAY_GRADE_PARTIAL = Decimal("0.5")
DAY_GRADE_NONE = Decimal("0")


class AttendanceDayStatus(TextChoices):
    ON_TIME = "on_time", _("On time")
    LATE_ENTRANCE = "late_entrance", _("Late entrance")
    EARLY_EXIT = "early_exit", _("Early exit")
    LATE_AND_EARLY = "late_and_early", _("Late entrance and early exit")
    INCOMPLETE = "incomplete", _("Incomplete")
    ABSENT = "absent", _("Absent")


@dataclass(frozen=True)
class DayAttendance:
    grade: Decimal
    status: str
    late_entrance: bool
    early_exit: bool


def _scan_field(record, name):
    """Read one scan attribute from a model instance or a values() dict row."""
    if isinstance(record, dict):
        return record[name]
    return getattr(record, name)


def local_scan_time(record) -> dt_time:
    return timezone.localtime(_scan_field(record, "scanned_at")).time()


def grade_attendance_day(entrance, exit_record, policy: AttendancePolicy) -> DayAttendance:
    """Grade one student's day: 1 for full, 0.5 for late/early, 0 otherwise.

    A reconciled exit (auto or manual) is treated as an on-time exit; only a
    real scanned exit before the threshold counts as an early departure.
    """
    if entrance is None and exit_record is None:
        return DayAttendance(DAY_GRADE_NONE, AttendanceDayStatus.ABSENT, False, False)
    if entrance is None or exit_record is None:
        return DayAttendance(DAY_GRADE_NONE, AttendanceDayStatus.INCOMPLETE, False, False)
    late = (
        _scan_field(entrance, "source") == AttendanceRecord.Source.SCAN
        and local_scan_time(entrance) > policy.entrance_limit
    )
    early = (
        _scan_field(exit_record, "source") == AttendanceRecord.Source.SCAN
        and local_scan_time(exit_record) < policy.exit_earliest
    )
    if late and early:
        status = AttendanceDayStatus.LATE_AND_EARLY
    elif late:
        status = AttendanceDayStatus.LATE_ENTRANCE
    elif early:
        status = AttendanceDayStatus.EARLY_EXIT
    else:
        status = AttendanceDayStatus.ON_TIME
    grade = DAY_GRADE_PARTIAL if late or early else DAY_GRADE_FULL
    return DayAttendance(grade, status, late, early)


def pair_attendance_records(records) -> dict:
    """Group one bounded record page into entrance/exit pairs per student/day/offering."""
    days: dict = {}
    for record in records:
        key = (
            _scan_field(record, "student_id"),
            _scan_field(record, "course_offering_id"),
            _scan_field(record, "attendance_date"),
        )
        days.setdefault(key, {})[_scan_field(record, "action")] = record
    return days


def _pair_completeness(pair: dict) -> int:
    return int(pair.get("entrance") is not None) + int(pair.get("exit") is not None)


def merge_attendance_pairs(pairs: dict) -> dict:
    """Merge per-offering pairs into one pair per student and day.

    When a student has records for more than one offering on the same day, the
    most complete pair wins; an incomplete pair never overwrites a complete one.
    """
    merged: dict = {}
    for (student_id, _course_offering_id, attendance_day), pair in pairs.items():
        existing = merged.setdefault(student_id, {}).get(attendance_day)
        if existing is None or _pair_completeness(pair) > _pair_completeness(existing):
            merged[student_id][attendance_day] = pair
    return merged


def attendance_scope_query(*, academic_year_id, level_id=None, offering_id=None) -> Q:
    """Return the record scope for one management attendance filter set."""
    if offering_id:
        return Q(course_offering_id=offering_id)

    offering_scope = Q(course_offering__academic_year_level__academic_year_id=academic_year_id)
    unassigned_scope = Q(
        course_offering__isnull=True,
        student__enrollments__academic_year_level__academic_year_id=academic_year_id,
        student__enrollments__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
        student__enrollments__enrollment_type=Enrollment.Type.NORMAL,
    )
    if level_id:
        offering_scope &= Q(course_offering__academic_year_level__level_id=level_id)
        unassigned_scope &= Q(student__enrollments__academic_year_level__level_id=level_id)
    return offering_scope | unassigned_scope


def offline_attendance_students(*, academic_year_id, level_id=None, offering_scope_id=None):
    """Return the eligible offline students for a year/level attendance roster."""
    enrollments = Q(
        enrollments__academic_year_level__academic_year_id=academic_year_id,
        enrollments__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
        enrollments__enrollment_type=Enrollment.Type.NORMAL,
    )
    if offering_scope_id:
        enrollments &= Q(enrollments__academic_year_level_id=offering_scope_id)
    elif level_id:
        enrollments &= Q(enrollments__academic_year_level__level_id=level_id)
    return User.objects.filter(
        role__role="student",
        application_status="active",
        study_mode="offline",
    ).filter(enrollments).distinct()


def annotate_daily_attendance_students(
    students,
    *,
    academic_year_id,
    level_id=None,
    offering_id=None,
    source=None,
    attendance_date,
):
    """Annotate a bounded roster with whether each student has entrance/exit scans."""
    record_scope = attendance_scope_query(
        academic_year_id=academic_year_id,
        level_id=level_id,
        offering_id=offering_id,
    )
    entrance = AttendanceRecord.objects.filter(
        student_id=OuterRef("pk"),
        attendance_date=attendance_date,
        action=AttendanceRecord.Action.ENTRANCE,
    ).filter(record_scope)
    exit_records = AttendanceRecord.objects.filter(
        student_id=OuterRef("pk"),
        attendance_date=attendance_date,
        action=AttendanceRecord.Action.EXIT,
    ).filter(record_scope)
    if source:
        entrance = entrance.filter(source=source)
        exit_records = exit_records.filter(source=source)
    return students.annotate(
        has_entrance_scan=Exists(entrance),
        has_exit_scan=Exists(exit_records),
    )


def daily_attendance_summary(students) -> dict[str, int]:
    """Aggregate roster coverage without materializing the student population."""
    counts = students.aggregate(
        total_offline=Count("pk"),
        entrance=Count("pk", filter=Q(has_entrance_scan=True)),
        exit=Count("pk", filter=Q(has_exit_scan=True)),
        complete=Count("pk", filter=Q(has_entrance_scan=True, has_exit_scan=True)),
        scanned=Count(
            "pk",
            filter=Q(has_entrance_scan=True) | Q(has_exit_scan=True),
        ),
    )
    counts["not_scanned"] = counts["total_offline"] - counts["scanned"]
    counts["incomplete"] = counts["scanned"] - counts["complete"]
    return counts


def attendance_scores_for_students(
    *,
    student_ids,
    course_offering,
    starts_on,
    ends_on,
    policy: AttendancePolicy,
) -> dict[int, Decimal]:
    """Return graded attendance points per student for one bounded student page."""
    student_ids = list(student_ids)
    if not student_ids:
        return {}
    records = AttendanceRecord.objects.filter(
        student_id__in=student_ids,
        course_offering=course_offering,
        attendance_date__gte=starts_on,
        attendance_date__lte=ends_on,
    ).values("student_id", "course_offering_id", "attendance_date", "action", "source", "scanned_at")
    scores: dict[int, Decimal] = {}
    for (student_id, _offering_id, _attendance_date), pair in pair_attendance_records(records).items():
        day = grade_attendance_day(pair.get("entrance"), pair.get("exit"), policy)
        scores[student_id] = scores.get(student_id, DAY_GRADE_NONE) + day.grade
    return scores


def reconcile_missing_exits(target_date=None, *, actor=None) -> int:
    """Record the missing exit for every entrance of one day.

    Automatic reconciliation (no actor) stores the run time with an ``auto``
    source; the manual fallback records the acting administrator and a
    ``manual`` source. The operation is idempotent.
    """
    target_date = target_date or timezone.localdate()
    entrances = AttendanceRecord.objects.filter(
        attendance_date=target_date,
        action=AttendanceRecord.Action.ENTRANCE,
    ).values("student_id", "course_offering_id")
    existing = set(
        AttendanceRecord.objects.filter(
            attendance_date=target_date,
            action=AttendanceRecord.Action.EXIT,
        ).values_list("student_id", "course_offering_id")
    )
    source = AttendanceRecord.Source.MANUAL if actor is not None else AttendanceRecord.Source.AUTO
    missing = [
        AttendanceRecord(
            student_id=row["student_id"],
            course_offering_id=row["course_offering_id"],
            attendance_date=target_date,
            action=AttendanceRecord.Action.EXIT,
            source=source,
            scanned_by=actor,
        )
        for row in entrances
        if (row["student_id"], row["course_offering_id"]) not in existing
    ]
    if not missing:
        return 0
    with transaction.atomic():
        AttendanceRecord.objects.bulk_create(missing, batch_size=500, ignore_conflicts=True)
    return len(missing)


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
    """Return graded attendance against the offering's current published lesson count.

    ``valid`` counts full days, ``invalid`` partial days, and ``attendance_rate``
    uses the graded points (full + 0.5 × partial) over the published lessons.
    """
    if student.study_mode == "online":
        return {
            "expected": 0, "valid": 0, "invalid": 0, "absent": 0,
            "score": DAY_GRADE_NONE, "attendance_rate": None, "absence_rate": None,
        }

    year = course_offering.academic_year_level.academic_year
    starts_on = starts_on or year.starts_on
    effective_end = min(through_date or year.ends_on, year.ends_on)
    policy = AttendancePolicy.load()
    records = AttendanceRecord.objects.filter(
        student=student,
        course_offering=course_offering,
        attendance_date__gte=starts_on,
        attendance_date__lte=effective_end,
    ).values("student_id", "course_offering_id", "attendance_date", "action", "source", "scanned_at")

    full = partial = 0
    score = DAY_GRADE_NONE
    for pair in pair_attendance_records(records).values():
        day = grade_attendance_day(pair.get("entrance"), pair.get("exit"), policy)
        score += day.grade
        if day.grade == DAY_GRADE_FULL:
            full += 1
        elif day.grade == DAY_GRADE_PARTIAL:
            partial += 1
    expected_count = Lesson.objects.filter(
        course_offering=course_offering,
        status=PublicationStatus.PUBLISHED,
    ).count()
    absent_count = max(expected_count - full - partial, 0)
    return {
        "expected": expected_count,
        "valid": full,
        "invalid": partial,
        "absent": absent_count,
        "score": score,
        "attendance_rate": float(score / expected_count) if expected_count else 0,
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
