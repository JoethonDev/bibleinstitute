"""Shared month-grid and event-agenda builders for admin and students."""

from __future__ import annotations

from calendar import Calendar
from datetime import date

from django.utils import formats


def build_month_grid(
    *,
    year_start: date,
    year_end: date,
    cur_year: int,
    cur_month: int,
    today: date,
    holidays: dict[date, dict],
    meetings_by_date: dict[date, list],
    locked_weekdays: set[int],
    meeting_offerings_by_weekday: dict[int, list] | None = None,
) -> list[list[dict]]:
    """Return the canonical day shape consumed by admin and student calendars."""
    month_days = Calendar().monthdatescalendar(cur_year, cur_month)
    month_grid = []
    for week in month_days:
        week_data = []
        for day in week:
            in_year = year_start <= day <= year_end
            day_meetings = meetings_by_date.get(day, [])
            is_meeting = day.weekday() in locked_weekdays
            is_holiday = day in holidays
            offering_groups = {}
            for offering in (meeting_offerings_by_weekday or {}).get(day.weekday(), []):
                academic_year_level = offering.academic_year_level
                level = academic_year_level.level
                group = offering_groups.setdefault(
                    level.pk,
                    {"level_id": level.pk, "level_name": level.display_name, "offerings": []},
                )
                group["offerings"].append(offering)
            holiday = holidays.get(day, {})
            week_data.append({
                "day": day.day,
                "date": day.isoformat(),
                "date_label": formats.date_format(day, "l, j F"),
                "in_year": in_year,
                "is_meeting": is_meeting,
                "is_today": day == today,
                "is_holiday": is_holiday,
                "holiday_name": holiday.get("name", "") if is_holiday else "",
                "holiday_id": holiday.get("id") if is_holiday else None,
                "meetings": day_meetings,
                "meeting_offering_groups": list(offering_groups.values()),
                "disabled": not in_year,
                "outside_month": day.month != cur_month,
            })
        month_grid.append(week_data)
    return month_grid


def build_month_agenda(month_grid: list[list[dict]]) -> list[dict]:
    """Return in-year, in-month days carrying meetings or holidays."""
    if not month_grid:
        return []
    return [
        day
        for week in month_grid
        for day in week
        if day["in_year"]
        and not day["outside_month"]
        and (day["meetings"] or day["is_holiday"])
    ]
