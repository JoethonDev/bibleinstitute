from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
import logging
import zoneinfo

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, TextChoices
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from ..models import (
    AcademicHoliday,
    AcademicYearLevel,
    AcademicYearLevelMeeting,
    AdminOperationJob,
    AttendancePolicy,
    AttendanceRecord,
    CourseOffering,
    Enrollment,
    Lesson,
    PublicationStatus,
    User,
)

from ..admin_operations import (
    AdminOperationError,
    assert_admin_operation_progress,
    require_admin_operation_actor,
)
from .decorators import can_correct_attendance


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class AttendanceScanAvailability:
    academic_year_level: AcademicYearLevel | None
    course_offering: CourseOffering | None
    blocked_reason: str | None
    recorded_actions: frozenset[str]

    @property
    def can_record(self) -> bool:
        return self.blocked_reason is None

    @staticmethod
    def duplicate_reason(action: str) -> str:
        if action == AttendanceRecord.Action.ENTRANCE:
            return str(_("Entrance has already been recorded for today."))
        return str(_("Exit has already been recorded for today."))

    def reason_for(self, action: str) -> str | None:
        if self.blocked_reason:
            return self.blocked_reason
        if action not in self.recorded_actions:
            return None
        return self.duplicate_reason(action)


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


def fully_scanned_attendance_day(pair: dict) -> bool:
    """Count a day when both actions were recorded by real scans, regardless of time."""
    entrance = pair.get(AttendanceRecord.Action.ENTRANCE)
    exit_record = pair.get(AttendanceRecord.Action.EXIT)
    return bool(
        entrance is not None
        and exit_record is not None
        and _scan_field(entrance, "source") == AttendanceRecord.Source.SCAN
        and _scan_field(exit_record, "source") == AttendanceRecord.Source.SCAN
    )


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


RECONCILE_BATCH_SIZE = 500


def execute_attendance_reconcile(job, token) -> dict:
    """J01 executor: bounded, resumable manual reconciliation for one date."""




    require_admin_operation_actor(job)
    if not can_correct_attendance(job.actor):
        raise AdminOperationError(
            "not_permitted",
            gettext("You are no longer allowed to reconcile attendance."),
        )
    raw_date = str((job.input or {}).get("date") or "")
    try:
        target_date = date.fromisoformat(raw_date)
    except (TypeError, ValueError) as exc:
        raise AdminOperationError(
            "invalid_date",
            gettext("The reconciliation date is not valid."),
        ) from exc

    def progress(*, created, total, cursor):
        assert_admin_operation_progress(
            job.pk,
            token,
            processed=created,
            succeeded=created,
            failed=0,
            skipped=0,
            total=total,
            cursor=cursor,
        )

    created, _cursor = reconcile_missing_exits(
        target_date,
        actor=job.actor,
        progress=progress,
        resume=job.cursor or {},
    )
    return {
        "status": AdminOperationJob.Status.COMPLETED,
        "result": {"date": target_date.isoformat(), "created": created},
    }


def _missing_exit_entrances(target_date, *, unassigned: bool):
    """Return the day's entrances that still have no matching exit, by pk.

    Assigned and unassigned legacy rows use different uniqueness rules, so they
    are resolved as two disjoint querysets instead of comparing a nullable key
    with ``=`` inside one anti-join.
    """

    if unassigned:
        exit_exists = AttendanceRecord.objects.filter(
            student_id=OuterRef("student_id"),
            attendance_date=target_date,
            action=AttendanceRecord.Action.EXIT,
            course_offering__isnull=True,
        )
    else:
        exit_exists = AttendanceRecord.objects.filter(
            student_id=OuterRef("student_id"),
            course_offering_id=OuterRef("course_offering_id"),
            attendance_date=target_date,
            action=AttendanceRecord.Action.EXIT,
        )
    return (
        AttendanceRecord.objects.filter(
            attendance_date=target_date,
            action=AttendanceRecord.Action.ENTRANCE,
            course_offering__isnull=unassigned,
        )
        .filter(~Exists(exit_exists))
        .order_by("pk")
    )


def _reconcile_missing_phase(queryset, *, target_date, source, actor, after_pk: int, phase: str, commit) -> int:
    """Insert missing exits for one keyset phase in bounded batches."""

    while True:
        rows = list(
            queryset.filter(pk__gt=after_pk).values("pk", "student_id", "course_offering_id")[:RECONCILE_BATCH_SIZE]
        )
        if not rows:
            return after_pk
        after_pk = rows[-1]["pk"]
        records = [
            AttendanceRecord(
                student_id=row["student_id"],
                course_offering_id=row["course_offering_id"],
                attendance_date=target_date,
                action=AttendanceRecord.Action.EXIT,
                source=source,
                scanned_by=actor,
            )
            for row in rows
        ]
        with transaction.atomic():
            AttendanceRecord.objects.bulk_create(
                records,
                batch_size=RECONCILE_BATCH_SIZE,
                ignore_conflicts=True,
            )
            commit(phase, after_pk, len(records))
        if len(rows) < RECONCILE_BATCH_SIZE:
            return after_pk


def reconcile_missing_exits(target_date=None, *, actor=None, progress=None, resume=None) -> tuple[int, dict]:
    """Record the missing exit for every entrance of one day, in bounded batches.

    Automatic reconciliation (no actor) stores the run time with an ``auto``
    source; the manual fallback records the acting administrator and a
    ``manual`` source. The operation is idempotent: the unique
    student/offering/date/action constraint and ``ignore_conflicts`` absorb
    concurrent scans, and a re-run finds no remaining missing exits. The
    returned count is the number of exits this run attempted to insert; a
    concurrent duplicate scan can therefore make it slightly higher than the
    number of newly created rows.

    ``resume`` carries the previous ``{"phase", "after_pk", "created"}`` cursor
    so a killed worker continues after the last committed batch, and
    ``progress`` receives the cursor in the same transaction as each insert.
    """

    target_date = target_date or timezone.localdate()
    resume = resume or {}
    source = AttendanceRecord.Source.MANUAL if actor is not None else AttendanceRecord.Source.AUTO
    start_unassigned = resume.get("phase") == "unassigned"
    after_pk = max(0, int(resume.get("after_pk") or 0))
    created_total = max(0, int(resume.get("created") or 0))

    assigned = _missing_exit_entrances(target_date, unassigned=False)
    unassigned = _missing_exit_entrances(target_date, unassigned=True)
    remaining = unassigned.filter(pk__gt=after_pk).count() if start_unassigned else (
        assigned.filter(pk__gt=after_pk).count() + unassigned.count()
    )
    total = created_total + remaining

    def commit(phase: str, last_pk: int, count: int) -> None:
        nonlocal created_total
        created_total += count
        if progress is not None:
            progress(
                created=created_total,
                total=total,
                cursor={"phase": phase, "after_pk": last_pk, "created": created_total},
            )

    if not start_unassigned:
        after_pk = _reconcile_missing_phase(
            assigned,
            target_date=target_date,
            source=source,
            actor=actor,
            after_pk=after_pk,
            phase="assigned",
            commit=commit,
        )
        # Publish the phase boundary even when nothing was inserted so a crash
        # between the two phases never rescans the assigned keyspace.
        commit("unassigned", 0, 0)
    _reconcile_missing_phase(
        unassigned,
        target_date=target_date,
        source=source,
        actor=actor,
        after_pk=after_pk if start_unassigned else 0,
        phase="unassigned",
        commit=commit,
    )
    logger.info(
        "missing exits reconciled",
        extra={
            "event": "attendance_reconciled",
            "on_date": target_date.isoformat(),
            "count": created_total,
            "source": source,
            "actor_id": actor.pk if actor is not None else None,
        },
    )
    return created_total, {"phase": "done", "after_pk": 0, "created": created_total}


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


def attendance_scan_availability(
    student: User,
    check_date: date,
    *,
    include_recorded_actions: bool = True,
) -> AttendanceScanAvailability:
    """Resolve scan eligibility and action-specific duplicate reasons for one student/date."""
    academic_year_level, course_offering = get_student_attendance_context(student, check_date)
    blocked_reason = None
    recorded_actions: frozenset[str] = frozenset()

    if student.study_mode == "online":
        blocked_reason = str(_("Online students cannot record attendance."))
    elif academic_year_level is None:
        blocked_reason = str(_("The student has no active academic-year enrollment."))
    elif not is_expected_date(academic_year_level, check_date):
        blocked_reason = str(_("Today is not an expected attendance day."))
    elif include_recorded_actions:
        recorded_actions = frozenset(AttendanceRecord.objects.filter(
            student_id=student.pk,
            course_offering_id=course_offering.pk if course_offering else None,
            attendance_date=check_date,
        ).values_list("action", flat=True))

    return AttendanceScanAvailability(
        academic_year_level=academic_year_level,
        course_offering=course_offering,
        blocked_reason=blocked_reason,
        recorded_actions=recorded_actions,
    )


def assign_unassigned_attendance(meeting) -> int:
    existing = AttendanceRecord.objects.filter(
        student_id=OuterRef("student_id"),
        course_offering_id=meeting.course_offering_id,
        attendance_date=OuterRef("attendance_date"),
        action=OuterRef("action"),
    )
    updated = AttendanceRecord.objects.filter(
        course_offering__isnull=True,
        attendance_date=meeting.meeting_date,
        student__enrollments__academic_year_level_id=meeting.academic_year_level_id,
    ).filter(~Exists(existing)).update(course_offering_id=meeting.course_offering_id)
    if updated:
        logger.info(
            "pending attendance backfilled",
            extra={
                "event": "attendance_backfilled",
                "count": updated,
                "offering_id": meeting.course_offering_id,
                "scope_id": meeting.academic_year_level_id,
            },
        )
    return updated

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
    uses the graded points (full + 0.5 x partial) over the published lessons.
    """
    if student.study_mode == "online":
        return {
            "expected": 0, "valid": 0, "invalid": 0, "absent": 0,
            "fully_scanned": 0,
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

    full = partial = fully_scanned = 0
    score = DAY_GRADE_NONE
    for pair in pair_attendance_records(records).values():
        fully_scanned += int(fully_scanned_attendance_day(pair))
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
        "fully_scanned": fully_scanned,
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
