from __future__ import annotations

from typing import Any

from django.core.paginator import Page
from django.db.models import Count, Q, QuerySet

from .academic_access import READABLE_ENROLLMENT_STATUSES
from .models import AcademicYearLevel, AcademicYearLevelMeeting, AttendanceRecord, CourseOffering, Enrollment, User
from .utils.attendance import AttendanceDayStatus, grade_attendance_day
from .utils.search import normalized_contains_q, normalize_search_text


ATTENDANCE_MATRIX_PAGE_SIZE = 25
ATTENDANCE_MATRIX_STATUS_VALUES = frozenset({"all", "scanned", "not_scanned", "complete", "incomplete", "absent"})


def attendance_matrix_meetings(
    scope: AcademicYearLevel,
    *,
    offering_id: int | None = None,
    starts_on=None,
    ends_on=None,
) -> list[dict[str, Any]]:
    """Return the bounded date/course columns for one academic scope."""
    meetings = AcademicYearLevelMeeting.objects.filter(
        academic_year_level_id=scope.pk,
    ).select_related("course_offering__course").order_by(
        "meeting_date", "course_offering__course__name", "pk"
    )
    if offering_id is not None:
        meetings = meetings.filter(course_offering_id=offering_id)
    if starts_on is not None:
        meetings = meetings.filter(meeting_date__gte=starts_on)
    if ends_on is not None:
        meetings = meetings.filter(meeting_date__lte=ends_on)

    return [
        {
            "id": meeting.pk,
            "date": meeting.meeting_date,
            "offering_id": getattr(meeting, "course_offering_id"),
            "course_name": meeting.course_offering.course.name,
        }
        for meeting in meetings
    ]


def _meeting_record_queryset(meetings: list[dict[str, Any]]) -> QuerySet[AttendanceRecord]:
    """Build the common record scope from the selected matrix columns."""
    if not meetings:
        return AttendanceRecord.objects.none()
    meeting_filter = Q()
    for meeting in meetings:
        meeting_filter |= Q(
            course_offering_id=meeting["offering_id"],
            attendance_date=meeting["date"],
        )
    return AttendanceRecord.objects.filter(meeting_filter)


def attendance_matrix_student_queryset(
    scope: AcademicYearLevel,
    meetings: list[dict[str, Any]],
    *,
    name: str = "",
    source: str = "",
    action: str = "",
    status: str = "all",
) -> QuerySet[User]:
    """Return the SQL-paginated offline roster for the selected matrix."""
    queryset = User.objects.filter(
        role__role="student",
        study_mode="offline",
        application_status="active",
        enrollments__academic_year_level_id=scope.pk,
        enrollments__status__in=READABLE_ENROLLMENT_STATUSES,
        enrollments__enrollment_type=Enrollment.Type.NORMAL,
        enrollments__course_offering__isnull=True,
    )
    if name:
        normalized_name = normalize_search_text(name)
        queryset = queryset.filter(
            normalized_contains_q(("first_name", "last_name", "username", "email"), normalized_name)
        )

    records = _meeting_record_queryset(meetings)
    if source:
        records = records.filter(source=source)
    if action:
        records = records.filter(action=action)

    student_ids = records.values("student_id")
    if status in {"scanned", "complete", "incomplete"}:
        queryset = queryset.filter(pk__in=student_ids)
        if status in {"complete", "incomplete"}:
            grouped = records.values(
                "student_id", "course_offering_id", "attendance_date"
            ).annotate(action_count=Count("action", distinct=True))
            grouped = grouped.filter(action_count=2 if status == "complete" else 1)
            queryset = queryset.filter(pk__in=grouped.values("student_id"))
    elif status in {"not_scanned", "absent"}:
        queryset = queryset.exclude(pk__in=student_ids)

    return queryset.distinct().order_by("last_name", "first_name", "username", "pk")


def _record_source_label(record: AttendanceRecord | None) -> str:
    if record is None:
        return "—"
    return str(dict(AttendanceRecord.Source.choices).get(record.source, record.source))


def _record_by_label(record: AttendanceRecord | None) -> str:
    if record is None:
        return "—"
    actor = getattr(record, "scanned_by", None)
    if getattr(record, "scanned_by_id", None) and actor:
        return actor.get_full_name() or actor.username
    return _record_source_label(record)


def attendance_matrix_page(
    page: Page,
    meetings: list[dict[str, Any]],
    *,
    source: str = "",
    action: str = "",
    policy,
) -> list[dict[str, Any]]:
    """Shape one bounded student page without queries inside the row loop."""
    students = list(page.object_list)
    student_ids = [student.pk for student in students]
    if not students or not meetings:
        return []

    records = _meeting_record_queryset(meetings).filter(student_id__in=student_ids)
    if source:
        records = records.filter(source=source)
    if action:
        records = records.filter(action=action)
    records = records.select_related("scanned_by")
    record_map: dict[tuple[int, int, Any], dict[str, AttendanceRecord]] = {}
    for record in records:
        student_id = getattr(record, "student_id")
        offering_id = getattr(record, "course_offering_id")
        record_map.setdefault(
            (student_id, offering_id, record.attendance_date),
            {},
        )[record.action] = record

    rows = []
    for student in students:
        cells = []
        full_days = partial_days = incomplete_days = absent_days = 0
        score = 0
        for meeting in meetings:
            pair = record_map.get((student.pk, meeting["offering_id"], meeting["date"]), {})
            day = grade_attendance_day(pair.get("entrance"), pair.get("exit"), policy)
            if day.status == AttendanceDayStatus.ON_TIME:
                full_days += 1
            elif day.status in {
                AttendanceDayStatus.LATE_ENTRANCE,
                AttendanceDayStatus.EARLY_EXIT,
                AttendanceDayStatus.LATE_AND_EARLY,
            }:
                partial_days += 1
            elif day.status == AttendanceDayStatus.INCOMPLETE:
                incomplete_days += 1
            else:
                absent_days += 1
            score += day.grade
            cells.append({
                "meeting": meeting,
                "entrance": pair.get("entrance"),
                "exit": pair.get("exit"),
                "status": day.status,
                "status_label": AttendanceDayStatus(day.status).label,
                "grade": day.grade,
                "entrance_source_label": _record_source_label(pair.get("entrance")),
                "exit_source_label": _record_source_label(pair.get("exit")),
                "entrance_by_label": _record_by_label(pair.get("entrance")),
                "exit_by_label": _record_by_label(pair.get("exit")),
            })
        rows.append({
            "student": student,
            "cells": cells,
            "full_days": full_days,
            "partial_days": partial_days,
            "incomplete_days": incomplete_days,
            "absent_days": absent_days,
            "score": score,
        })
    return rows
