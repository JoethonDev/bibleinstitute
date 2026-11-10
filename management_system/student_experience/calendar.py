"""Bounded student calendar presentation data."""

from datetime import date, timedelta
import re

from django.db.models import Subquery
from django.utils import formats, timezone

from ..calendar_grid import build_month_agenda, build_month_grid
from ..models import AcademicYearLevelMeeting, Enrollment
from ..utils.student_data import student_calendar_data


def next_student_meeting(user) -> dict | None:
    """Return the next meeting in the active normal enrollment's scope."""
    today = timezone.localdate()
    active_scope_id = Enrollment.objects.filter(
        student=user,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        academic_year_level__academic_year__is_active=True,
    ).order_by("-enrolled_at", "-pk").values("academic_year_level_id")[:1]
    meeting = AcademicYearLevelMeeting.objects.filter(
        academic_year_level_id=Subquery(active_scope_id),
        meeting_date__gte=today,
    ).select_related("course_offering__course").order_by(
        "meeting_date", "pk"
    ).select_related(
        "academic_year_level__academic_year",
        "academic_year_level__level",
    ).first()
    if meeting is None:
        return None
    scope = meeting.academic_year_level
    return {
        "date": meeting.meeting_date,
        "date_label": formats.date_format(meeting.meeting_date, "D, j M"),
        "weekday": formats.date_format(meeting.meeting_date, "l"),
        "course": meeting.course_offering.course.name,
        "scope": scope,
        "scope_label": f"{scope.academic_year.name} - {scope.level.display_name}",
    }


def build_calendar_context(request) -> dict:
    """Build one active-scope, one-month calendar with a bounded event set."""
    raw_month = (request.GET.get("month") or "").strip()
    requested_month = None
    if re.fullmatch(r"\d{4}-\d{1,2}", raw_month):
        year_text, month_text = raw_month.split("-", 1)
        month_number = int(month_text)
        if 1 <= month_number <= 12:
            requested_month = f"{int(year_text):04d}-{month_number:02d}"

    language = (request.LANGUAGE_CODE or "ar").split("-")[0]
    try:
        data = student_calendar_data(
            request.user,
            language=language,
            scope_id=None,
            month=requested_month,
        )
    except ValueError:
        data = student_calendar_data(request.user, language=language, scope_id=None)

    next_meeting = next_student_meeting(request.user)
    if not data or not data["scope"]:
        return {
            "calendar": None,
            "next_meeting": next_meeting,
            "weekdays": [
                formats.date_format(date(2000, 1, day), "D")
                for day in range(3, 10)
            ],
        }

    selected_month = date.fromisoformat(f"{data['month']}-01")
    year_start = date.fromisoformat(data["year_starts_on"])
    year_end = date.fromisoformat(data["year_ends_on"])
    today = timezone.localdate()
    holidays = {
        date.fromisoformat(row["date"]): {"id": None, "name": row["name"]}
        for row in data["holidays"]
    }
    meeting_weekdays = set(data["scope"]["meeting_weekdays"] or [])
    meetings_by_date = {}
    for row in data["meetings"]:
        day = date.fromisoformat(row["date"])
        meetings_by_date.setdefault(day, []).append(row)

    grid = build_month_grid(
        year_start=year_start,
        year_end=year_end,
        cur_year=selected_month.year,
        cur_month=selected_month.month,
        today=today,
        holidays=holidays,
        meetings_by_date=meetings_by_date,
        locked_weekdays=meeting_weekdays,
    )
    for week in grid:
        for day in week:
            day["selected"] = False

    event_days = build_month_agenda(grid)
    selectable_days = [
        day for week in grid for day in week
        if day["in_year"] and not day["outside_month"]
    ]
    selected_day = next(
        (day for day in selectable_days if day["is_today"]),
        event_days[0] if event_days else (selectable_days[0] if selectable_days else None),
    )
    if selected_day:
        selected_day["selected"] = True

    available_months = data["available_months"]
    month_index = available_months.index(data["month"])
    previous_month = available_months[month_index - 1] if month_index else None
    following_month = (
        available_months[month_index + 1]
        if month_index + 1 < len(available_months)
        else None
    )
    next_seven_days = list(
        AcademicYearLevelMeeting.objects.filter(
            academic_year_level_id=data["scope"]["id"],
            meeting_date__gte=today,
            meeting_date__lt=today + timedelta(days=7),
        ).select_related("course_offering__course").order_by(
            "meeting_date", "pk"
        )
    )
    return {
        "calendar": {
            **data,
            "title": formats.date_format(selected_month, "F Y"),
            "grid": grid,
            "event_days": event_days[:20],
            "selected_day": selected_day,
            "previous_month": previous_month,
            "next_month": following_month,
            "meeting_count": sum(len(row) for row in meetings_by_date.values()),
            "holiday_count": len(holidays),
            "next_seven_days": [
                {
                    "date": row.meeting_date,
                    "date_label": formats.date_format(row.meeting_date, "D, j M"),
                    "course": row.course_offering.course.name,
                }
                for row in next_seven_days
            ],
        },
        "next_meeting": next_meeting,
        "weekdays": [
            formats.date_format(date(2000, 1, day), "D")
            for day in range(3, 10)
        ],
    }
