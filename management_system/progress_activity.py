import json
import math
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .academic_access import user_can_write_offering_activity
from .models import (
    LectureProgress,
    LectureProgressEvent,
    LectureWatchDay,
    LectureWatchSession,
    Lesson,
    ViewingSession,
)
from .utils.hls_parser import get_lesson_segments
from .utils.lesson_access import lesson_is_available
from .utils.progress_merge import calculate_percent, merge_verified_progress_ranges, unique_seconds

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
            total_active_seconds = values["total_active_seconds"] or 0
            for day in days:
                day["share_percent"] = round(
                    day["active_seconds"] * 100 / total_active_seconds
                ) if total_active_seconds else 0
            active_end = row.completed_at or last
            elapsed_days = (
                (timezone.localtime(active_end).date() - timezone.localtime(first).date()).days + 1
                if first and active_end and not row.activity_history_incomplete else None
            )
            row.activity_summary = {
                "available": True,
                "history_incomplete": row.activity_history_incomplete,
                "tracking_started_at": row.activity_tracking_started_at,
                "active_seconds": total_active_seconds,
                "active_duration": format_active_duration(total_active_seconds),
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


@dataclass(frozen=True)
class ProgressHeartbeatResult:
    """Domain result shared by the web and mobile progress adapters."""

    percent: int
    completed: bool
    note: str = ""

    def as_payload(self) -> dict:
        payload = {"status": "ok", "percent": self.percent, "completed": self.completed}
        if self.note:
            payload["note"] = self.note
        return payload


class ProgressHeartbeatError(Exception):
    """Expected domain rejection; adapters choose the HTTP status."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _record_progress_event(
    session,
    event_type,
    *,
    occurred_at=None,
    percent=0,
    unique_seconds=0,
):
    occurred_at = occurred_at or timezone.now()
    defaults = {
        "student": session.student,
        "lesson": session.lesson,
        "part_id": session.part_id,
        "viewing_session": session,
        "occurred_at": occurred_at,
        "percent": max(0, min(int(percent or 0), 100)),
        "unique_seconds": max(0, int(unique_seconds or 0)),
    }
    if event_type == LectureProgressEvent.EventType.COMPLETED:
        lookup = {
            "student": session.student,
            "lesson": session.lesson,
            "part_id": session.part_id,
            "event_type": event_type,
        }
    else:
        lookup = {
            "viewing_session": session,
            "event_type": event_type,
        }
    if event_type in {
        LectureProgressEvent.EventType.ACTIVITY,
        LectureProgressEvent.EventType.PROGRESS,
    }:
        lookup["bucket_start"] = occurred_at.replace(second=0, microsecond=0)
    if event_type == LectureProgressEvent.EventType.PROGRESS:
        lookup["percent"] = max(0, min(int(percent or 0), 100))
    LectureProgressEvent.objects.get_or_create(**lookup, defaults=defaults)


def apply_progress_heartbeat(
    *,
    user,
    payload,
    cloud_client,
    bucket_name: str,
    allow_management: bool = False,
    session: ViewingSession | None = None,
) -> ProgressHeartbeatResult:
    """Validate, authorize and persist one playback heartbeat.

    The caller owns transport authentication (browser session or mobile
    bearer/audience token); this operation owns input validation, session
    ownership/expiry, write-access policy, verified-range merging and the
    locked progress/event writes.
    """

    if not user or not getattr(user, "is_authenticated", False):
        raise ProgressHeartbeatError("unauthenticated", _("Authentication required"))
    if not isinstance(payload, dict):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid request format."))

    session_id = payload.get("session_id")
    ranges = payload.get("ranges", [])
    ended = payload.get("ended", False)
    active_seconds = payload.get("active_seconds", 0)
    sequence = payload.get("sequence")

    if (
        not session_id
        or not isinstance(ranges, list)
        or not isinstance(ended, bool)
        or isinstance(active_seconds, bool)
        or not isinstance(active_seconds, (int, float))
        or not math.isfinite(active_seconds)
        or active_seconds < 0
        or active_seconds > 3600
    ):
        raise ProgressHeartbeatError(
            "invalid_payload",
            _("Missing session identifier or time ranges."),
        )
    if active_seconds > 0 and (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= 0
        or sequence > 2_147_483_647
    ):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid playback sequence."))
    if len(ranges) > 200:
        raise ProgressHeartbeatError("invalid_payload", _("Too many time ranges."))
    try:
        ranges = [[float(start), float(end)] for start, end in ranges]
    except (ValueError, TypeError):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid time range format."))
    max_duration = float(getattr(settings, "MEDIA_MAX_DURATION_SECONDS", 3 * 60 * 60))
    if any(
        not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
        or end > max_duration
        for start, end in ranges
    ):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid time range format."))

    if session is None:
        session = (
            ViewingSession.objects.select_related("lesson__course_offering")
            .filter(session_id=session_id)
            .first()
        )
        if session is None:
            raise ProgressHeartbeatError("session_not_found", _("Viewing session was not found."))
    if str(session.session_id) != str(session_id) or session.student_id != user.pk:
        raise ProgressHeartbeatError("forbidden", _("Session belongs to another user."))
    if timezone.now() >= session.expires_at:
        raise ProgressHeartbeatError("expired", _("Session expired."))
    if not lesson_is_available(session.lesson):
        raise ProgressHeartbeatError("forbidden", _("This lesson is not available yet."))

    def result(note=None) -> ProgressHeartbeatResult:
        current = LectureProgress.objects.filter(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
        ).values("percent", "completed_at").first()
        return ProgressHeartbeatResult(
            percent=int(current["percent"] or 0) if current else 0,
            completed=bool(current and current["completed_at"]),
            note=str(note or ""),
        )

    if session.ended_at is not None:
        return result()

    if not user_can_write_offering_activity(
        user,
        session.lesson.course_offering,
        allow_management=allow_management,
    ):
        return result(_("Historical content is read-only"))

    heartbeat_at = timezone.now()
    session.last_heartbeat = heartbeat_at
    update_fields = ["last_heartbeat"]
    if ended and session.ended_at is None:
        session.ended_at = heartbeat_at
        update_fields.append("ended_at")
    session.save(update_fields=update_fields)

    if ended:
        _record_progress_event(
            session,
            LectureProgressEvent.EventType.ENDED,
            occurred_at=heartbeat_at,
        )

    verified = set(session.verified_requests.values_list("segment_number", flat=True))
    if not verified:
        return result(_("No verified segments yet"))

    lesson_segments = get_lesson_segments(
        session.lesson,
        cloud_client,
        bucket_name,
        media_key=session.media_key or None,
        requested_part_id=session.part_id,
    ).get(session.part_id, [])
    all_ranges = [
        [float(segment.get("start", 0)), float(segment.get("end", 0))]
        for segment in lesson_segments
        if float(segment.get("end", 0)) > float(segment.get("start", 0))
    ]
    verified_ranges = []
    for segment in lesson_segments:
        if segment.get("number") in verified:
            verified_ranges.append({
                "start": float(segment.get("start", 0)),
                "end": float(segment.get("end", 0)),
            })
    if not verified_ranges:
        return result(_("No verified segment ranges"))

    total_secs = sum(end - start for start, end in all_ranges)
    with transaction.atomic():
        locked_session = ViewingSession.objects.select_for_update().get(pk=session.pk)
        if locked_session.ended_at is not None and locked_session.ended_at != heartbeat_at:
            return result()
        progress, _created = LectureProgress.objects.select_for_update().get_or_create(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
            defaults={"merged_ranges": []},
        )
        merged = merge_verified_progress_ranges(
            progress.merged_ranges,
            ranges,
            verified_ranges,
        )
        unique_secs = unique_seconds(merged)
        percent = calculate_percent(unique_secs, total_secs) if total_secs > 0 else 0
        previous_percent = progress.percent
        progress.merged_ranges = merged
        progress.percent = percent
        progress.unique_seconds = int(round(unique_secs))
        if merged:
            furthest = max(float(end) for _start, end in merged)
            progress.last_position_seconds = max(
                int(progress.last_position_seconds or 0),
                int(furthest),
            )
        if not progress.completed_at and percent >= 80:
            progress.completed_at = timezone.now()
        progress.save()

        credit_watch_sample(
            locked_session,
            progress,
            active_seconds=active_seconds,
            sequence=sequence,
            progress_before=previous_percent,
            progress_after=percent,
            ended=ended,
            now=heartbeat_at,
        )

        if percent != previous_percent:
            _record_progress_event(
                locked_session,
                LectureProgressEvent.EventType.PROGRESS,
                occurred_at=heartbeat_at,
                percent=percent,
                unique_seconds=progress.unique_seconds,
            )
        if progress.completed_at:
            _record_progress_event(
                locked_session,
                LectureProgressEvent.EventType.COMPLETED,
                occurred_at=progress.completed_at,
                percent=percent,
                unique_seconds=progress.unique_seconds,
            )
    return ProgressHeartbeatResult(percent=percent, completed=progress.completed_at is not None)
