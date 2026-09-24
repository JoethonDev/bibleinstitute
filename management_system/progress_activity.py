import json
from datetime import date, timedelta

from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import LectureProgress, LectureWatchDay, LectureWatchSession, Lesson, ViewingSession

WATCH_SESSION_GAP = timedelta(minutes=10)
MAX_HEARTBEAT_ACTIVE_SECONDS = 60
PROGRESS_ACTIVITY_WINDOW_DAYS = 30


def format_active_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return _("%(hours)s hr %(minutes)s min") % {"hours": hours, "minutes": minutes}
    if minutes:
        return _("%(minutes)s min") % {"minutes": minutes}
    return _("%(seconds)s sec") % {"seconds": seconds}


def part_display_name(lesson: Lesson, part_id: str) -> str:
    try:
        links = json.loads(lesson.links or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        links = []
    parts = []
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("file_type") in {"video", "audio"}:
                candidate = link.get("part_id")
                if candidate and candidate not in parts:
                    parts.append(candidate)
    if part_id in parts:
        return _("Part %(number)s") % {"number": parts.index(part_id) + 1}
    return _("Part")


def _row_filter(rows: list[LectureProgress]) -> Q:
    predicate = Q(pk__in=[])
    for row in rows:
        predicate |= Q(
            watch_session__student_id=row.student_id,
            watch_session__lesson_id=row.lesson_id,
            watch_session__part_id=row.part_id,
        )
    return predicate


def credit_watch_sample(
    viewing_session: ViewingSession,
    progress: LectureProgress,
    *,
    active_seconds,
    sequence,
    progress_before,
    progress_after,
    ended,
    now=None,
) -> int:
    """Persist a bounded, sequenced playback sample against the locked progress row."""
    now = now or timezone.now()
    viewing_session.last_heartbeat = now
    accepted_sequence = (
        isinstance(sequence, int)
        and not isinstance(sequence, bool)
        and 0 < sequence <= 2_147_483_647
        and sequence > viewing_session.heartbeat_sequence
    )
    if accepted_sequence:
        viewing_session.heartbeat_sequence = sequence
    viewing_session.save(update_fields=["last_heartbeat", "heartbeat_sequence"])

    credited = 0
    watch = None
    server_limit = 0
    if accepted_sequence:
        watch = LectureWatchSession.objects.filter(
            student_id=viewing_session.student_id,
            lesson_id=viewing_session.lesson_id,
            part_id=viewing_session.part_id,
            ended_at__isnull=True,
        ).first()
        if watch and now - watch.last_active_at >= WATCH_SESSION_GAP:
            watch.ended_at = watch.last_active_at
            watch.save(update_fields=["ended_at"])
            watch = None

        if watch is None and active_seconds > 0:
            requested_seconds = min(int(round(active_seconds)), MAX_HEARTBEAT_ACTIVE_SECONDS)
            if requested_seconds <= 0:
                if ended:
                    viewing_session.ended_at = now
                    viewing_session.save(update_fields=["ended_at"])
                return 0
            watch = LectureWatchSession.objects.create(
                student_id=viewing_session.student_id,
                lesson_id=viewing_session.lesson_id,
                part_id=viewing_session.part_id,
                started_at=now,
                last_active_at=now,
            )
            server_limit = MAX_HEARTBEAT_ACTIVE_SECONDS
        elif watch and active_seconds > 0:
            server_limit = min(
                MAX_HEARTBEAT_ACTIVE_SECONDS,
                max(0, int((now - watch.last_active_at).total_seconds())),
            )
        if watch and active_seconds > 0:
            credited = min(int(round(active_seconds)), server_limit)

        if watch:
            viewing_session.watch_session = watch
            viewing_session.save(update_fields=["watch_session"])
            study_date = timezone.localtime(now).date()
            if credited or progress_after != progress_before:
                day, _created = LectureWatchDay.objects.get_or_create(
                    watch_session=watch,
                    study_date=study_date,
                    defaults={
                        "active_watch_seconds": credited,
                        "progress_start_percent": progress_before,
                        "progress_end_percent": progress_after,
                    },
                )
                if not _created:
                    day.active_watch_seconds += credited
                    if day.progress_start_percent is None:
                        day.progress_start_percent = progress_before
                    day.progress_end_percent = progress_after
                    day.save(update_fields=[
                        "active_watch_seconds", "progress_start_percent", "progress_end_percent",
                    ])
            if credited:
                watch.active_watch_seconds += credited
                watch.last_active_at = now
                watch.save(update_fields=["active_watch_seconds", "last_active_at"])

    if ended:
        watch_id = viewing_session.watch_session_id
        if watch_id:
            LectureWatchSession.objects.filter(pk=watch_id, ended_at__isnull=True).update(ended_at=now)
        viewing_session.ended_at = now
        viewing_session.save(update_fields=["ended_at"])
    return credited


def progress_activity_for_rows(rows: list[LectureProgress], *, today: date | None = None) -> None:
    """Build summaries and 30-day daily activity for one already-paginated page."""
    rows = list(rows)
    if not rows:
        return
    today = today or timezone.localdate()
    window_start = today - timedelta(days=PROGRESS_ACTIVITY_WINDOW_DAYS - 1)
    keys = Q(pk__in=[])
    for row in rows:
        keys |= Q(
            student_id=row.student_id,
            lesson_id=row.lesson_id,
            part_id=row.part_id,
        )

    summary = {
        (entry["student_id"], entry["lesson_id"], entry["part_id"]): entry
        for entry in LectureWatchSession.objects.filter(keys).values(
            "student_id", "lesson_id", "part_id",
        ).annotate(
            total_active_seconds=Sum("active_watch_seconds"),
            session_count=Count("pk"),
            first_started_at=Min("started_at"),
            last_active_at=Max("last_active_at"),
        )
    }
    active_day_counts = {
        (entry["watch_session__student_id"], entry["watch_session__lesson_id"], entry["watch_session__part_id"]): entry["active_days"]
        for entry in LectureWatchDay.objects.filter(
            _row_filter(rows), active_watch_seconds__gt=0,
        ).values(
            "watch_session__student_id", "watch_session__lesson_id", "watch_session__part_id",
        ).annotate(active_days=Count("study_date", distinct=True))
    }
    daily_rows = LectureWatchDay.objects.filter(
        _row_filter(rows),
        study_date__gte=window_start,
        study_date__lte=today,
        active_watch_seconds__gt=0,
    ).values(
        "watch_session__student_id",
        "watch_session__lesson_id",
        "watch_session__part_id",
        "study_date",
    ).annotate(
        active_seconds=Sum("active_watch_seconds"),
        sessions=Count("watch_session_id", distinct=True),
        progress_start=Min("progress_start_percent"),
        progress_end=Max("progress_end_percent"),
    ).order_by("study_date")

    days_by_key = {}
    for daily in daily_rows:
        key = (
            daily["watch_session__student_id"],
            daily["watch_session__lesson_id"],
            daily["watch_session__part_id"],
        )
        gain = None
        if daily["progress_start"] is not None and daily["progress_end"] is not None:
            gain = max(0, daily["progress_end"] - daily["progress_start"])
        days_by_key.setdefault(key, []).append({
            "date": daily["study_date"],
            "active_seconds": daily["active_seconds"] or 0,
            "session_count": daily["sessions"],
            "progress_gain": gain,
            "duration": format_active_duration(daily["active_seconds"] or 0),
        })

    for row in rows:
        key = (row.student_id, row.lesson_id, row.part_id)
        values = summary.get(key)
        days = days_by_key.get(key, [])
        days_by_date = {day["date"]: day for day in days}
        timeline = []
        for offset in range(PROGRESS_ACTIVITY_WINDOW_DAYS - 1, -1, -1):
            activity_date = today - timedelta(days=offset)
            day = days_by_date.get(activity_date)
            active_seconds = day["active_seconds"] if day else 0
            timeline.append({
                "date": activity_date,
                "active_seconds": active_seconds,
                "duration": format_active_duration(active_seconds),
                "intensity": (
                    "high" if active_seconds >= 1800 else
                    "medium" if active_seconds >= 600 else
                    "low" if active_seconds > 0 else "none"
                ),
            })
        if values:
            first = None if row.activity_history_incomplete else values["first_started_at"]
            last = values["last_active_at"]
            active_end = row.completed_at or last
            elapsed_days = (
                (timezone.localtime(active_end).date() - timezone.localtime(first).date()).days + 1
                if first and active_end and not row.activity_history_incomplete else None
            )
            row.activity_summary = {
                "available": True,
                "history_incomplete": row.activity_history_incomplete,
                "tracking_started_at": row.activity_tracking_started_at,
                "active_seconds": values["total_active_seconds"] or 0,
                "active_duration": format_active_duration(values["total_active_seconds"] or 0),
                "session_count": values["session_count"],
                "active_days": active_day_counts.get(key, 0),
                "first_started_at": first,
                "last_active_at": last,
                "elapsed_days": elapsed_days,
                "days": days,
                "timeline": timeline,
                "most_active_day": max(days, key=lambda day: day["active_seconds"], default=None),
                "most_progress_day": max(
                    (day for day in days if day["progress_gain"] is not None),
                    key=lambda day: day["progress_gain"],
                    default=None,
                ),
                "window_start": window_start,
                "window_end": today,
            }
        else:
            row.activity_summary = {
                "available": False,
                "history_incomplete": row.activity_history_incomplete,
                "tracking_started_at": row.activity_tracking_started_at,
                "active_seconds": None,
                "active_duration": None,
                "session_count": None,
                "active_days": None,
                "first_started_at": None,
                "last_active_at": None,
                "elapsed_days": None,
                "days": [],
                "timeline": timeline,
                "most_active_day": None,
                "most_progress_day": None,
                "window_start": window_start,
                "window_end": today,
            }


def watch_sessions_for_day(row: LectureProgress, study_date: date):
    return LectureWatchSession.objects.filter(
        student_id=row.student_id,
        lesson_id=row.lesson_id,
        part_id=row.part_id,
        days__study_date=study_date,
        days__active_watch_seconds__gt=0,
    ).annotate(
        day_active_seconds=Sum("days__active_watch_seconds"),
    ).distinct().order_by("started_at", "pk")
