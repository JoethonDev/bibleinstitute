from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
import zoneinfo

from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Exists, OuterRef, TextChoices
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    AcademicHoliday,
    AcademicYearLevel,
    AcademicYearLevelMeeting,
    AttendancePolicy,
    AttendanceRecord,
    Course,
    CourseOffering,
    Enrollment,
    Lesson,
    Level,
    PublicationStatus,
    Role,
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


def attendance_roster_page(
    *,
    academic_year_id: int,
    starts_on,
    ends_on,
    level_id: int | None = None,
    offering_id: int | None = None,
    student_search: str = "",
    action: str = "",
    source: str = "",
    scan_status: str = "",
    page_number: int = 1,
    per_page: int = 25,
    policy: AttendancePolicy,
):
    """Return one SQL-paginated row per eligible student and calendar meeting.

    Calendar meetings define the attendance days. Offline normal enrollments
    provide the roster, while both attendance actions are optional left joins
    so absent students remain visible without materializing a cross-product in
    Python.
    """
    quote_name = connection.ops.quote_name
    tables = {
        "meeting": quote_name(AcademicYearLevelMeeting._meta.db_table),
        "scope": quote_name(AcademicYearLevel._meta.db_table),
        "level": quote_name(Level._meta.db_table),
        "offering": quote_name(CourseOffering._meta.db_table),
        "course": quote_name(Course._meta.db_table),
        "enrollment": quote_name(Enrollment._meta.db_table),
        "user": quote_name(User._meta.db_table),
        "role": quote_name(Role._meta.db_table),
        "record": quote_name(AttendanceRecord._meta.db_table),
    }

    meeting_source_filter = ""
    if source:
        meeting_source_filter = " AND {alias}.source = %s"

    student_filter = ""
    if student_search:
        student_filter = """
            AND (
                lms_arabic_search_normalize(u.username) LIKE %s
                OR lms_arabic_search_normalize(u.first_name) LIKE %s
                OR lms_arabic_search_normalize(u.last_name) LIKE %s
                OR lms_arabic_search_normalize(u.email) LIKE %s
            )
        """
    scope_filter = ""
    if level_id:
        scope_filter += " AND ayl.level_id = %s"
    if offering_id:
        scope_filter += " AND m.course_offering_id = %s"

    base_sql = f"""
        WITH roster AS (
            SELECT
                m.id AS meeting_id,
                m.meeting_date,
                ayl.id AS scope_id,
                lvl.id AS level_id,
                COALESCE(NULLIF(lvl.name_en, ''), NULLIF(lvl.name_ar, ''), CAST(lvl.ordering AS TEXT)) AS level_name,
                co.id AS offering_id,
                c.name AS course_name,
                u.id AS student_id,
                u.username,
                u.first_name,
                u.last_name,
                er.id AS entrance_id,
                er.scanned_at AS entrance_scanned_at,
                er.source AS entrance_source,
                er.scanned_by_id AS entrance_scanned_by_id,
                er.corrected_at AS entrance_corrected_at,
                eu.username AS entrance_scanned_by_username,
                eu.first_name AS entrance_scanned_by_first_name,
                eu.last_name AS entrance_scanned_by_last_name,
                xr.id AS exit_id,
                xr.scanned_at AS exit_scanned_at,
                xr.source AS exit_source,
                xr.scanned_by_id AS exit_scanned_by_id,
                xr.corrected_at AS exit_corrected_at,
                xu.username AS exit_scanned_by_username,
                xu.first_name AS exit_scanned_by_first_name,
                xu.last_name AS exit_scanned_by_last_name
            FROM {tables['meeting']} m
            JOIN {tables['scope']} ayl ON ayl.id = m.academic_year_level_id
            JOIN {tables['level']} lvl ON lvl.id = ayl.level_id
            JOIN {tables['offering']} co ON co.id = m.course_offering_id
            JOIN {tables['course']} c ON c.id = co.course_id
            JOIN {tables['enrollment']} en
              ON en.academic_year_level_id = m.academic_year_level_id
             AND en.enrollment_type = %s
             AND en.status IN (%s, %s)
             AND en.course_offering_id IS NULL
            JOIN {tables['user']} u ON u.id = en.student_id
            JOIN {tables['role']} role ON role.id = u.role_id AND role.role = %s
            LEFT JOIN {tables['record']} er
              ON er.student_id = u.id
             AND er.course_offering_id = m.course_offering_id
             AND er.attendance_date = m.meeting_date
             AND er.action = %s
             {meeting_source_filter.format(alias='er')}
            LEFT JOIN {tables['user']} eu ON eu.id = er.scanned_by_id
            LEFT JOIN {tables['record']} xr
              ON xr.student_id = u.id
             AND xr.course_offering_id = m.course_offering_id
             AND xr.attendance_date = m.meeting_date
             AND xr.action = %s
             {meeting_source_filter.format(alias='xr')}
            LEFT JOIN {tables['user']} xu ON xu.id = xr.scanned_by_id
            WHERE ayl.academic_year_id = %s
              AND m.meeting_date BETWEEN %s AND %s
              AND u.study_mode = %s
              AND u.application_status = %s
              {scope_filter}
              {student_filter}
            GROUP BY
                m.id, m.meeting_date, ayl.id, lvl.id,
                COALESCE(NULLIF(lvl.name_en, ''), NULLIF(lvl.name_ar, ''), CAST(lvl.ordering AS TEXT)),
                co.id, c.name, u.id, u.username, u.first_name, u.last_name,
                er.id, er.scanned_at, er.source, er.scanned_by_id,
                er.corrected_at, eu.username, eu.first_name, eu.last_name,
                xr.id, xr.scanned_at, xr.source, xr.scanned_by_id,
                xr.corrected_at, xu.username, xu.first_name, xu.last_name
        )
        SELECT * FROM roster
    """

    # The SQL text order is: enrollment type/status, role, record actions and
    # optional source filters, then year/date/study/application filters.
    sql_params = [
        "normal", "active", "completed", "student",
        AttendanceRecord.Action.ENTRANCE,
    ]
    if source:
        sql_params.append(source)
    sql_params.append(AttendanceRecord.Action.EXIT)
    if source:
        sql_params.append(source)
    sql_params.extend([academic_year_id, starts_on, ends_on, "offline", "active"])
    if level_id:
        sql_params.append(level_id)
    if offering_id:
        sql_params.append(offering_id)
    if student_search:
        sql_params.extend([f"%{student_search}%"] * 4)

    where = []
    if action == AttendanceRecord.Action.ENTRANCE:
        where.append("entrance_id IS NOT NULL")
    elif action == AttendanceRecord.Action.EXIT:
        where.append("exit_id IS NOT NULL")
    if scan_status in {"scanned", "not_scanned", "complete", "incomplete", "absent"}:
        if scan_status == "scanned":
            where.append("(entrance_id IS NOT NULL OR exit_id IS NOT NULL)")
        elif scan_status in {"not_scanned", "absent"}:
            where.append("entrance_id IS NULL AND exit_id IS NULL")
        elif scan_status == "complete":
            where.append("entrance_id IS NOT NULL AND exit_id IS NOT NULL")
        else:
            where.append("(entrance_id IS NOT NULL) <> (exit_id IS NOT NULL)")
    if where:
        base_sql += " WHERE " + " AND ".join(where)

    try:
        page_number = int(page_number)
    except (TypeError, ValueError):
        page_number = 1

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM ({base_sql}) attendance_roster", sql_params)
        total = cursor.fetchone()[0]
        offset = max(page_number - 1, 0) * per_page
        cursor.execute(
            base_sql + " ORDER BY meeting_date DESC, level_name, course_name, last_name, first_name, username, student_id LIMIT %s OFFSET %s",
            [*sql_params, per_page, offset],
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    source_labels = dict(AttendanceRecord.Source.choices)
    for row in rows:
        entrance = (
            {"source": row["entrance_source"], "scanned_at": row["entrance_scanned_at"]}
            if row["entrance_id"] else None
        )
        exit_record = (
            {"source": row["exit_source"], "scanned_at": row["exit_scanned_at"]}
            if row["exit_id"] else None
        )
        day = grade_attendance_day(entrance, exit_record, policy)
        row.update({
            "day_grade": day.grade,
            "day_status": day.status,
            "day_status_label": AttendanceDayStatus(day.status).label,
            "entrance_source_label": source_labels.get(row["entrance_source"], "—"),
            "exit_source_label": source_labels.get(row["exit_source"], "—"),
        })

    paginator = Paginator([], per_page)
    paginator.count = total
    page = paginator.get_page(max(page_number, 1))
    page.object_list = rows
    return page


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
