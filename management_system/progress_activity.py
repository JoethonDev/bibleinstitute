import json
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .academic_access import user_can_write_offering_activity
from .models import (
    AttendancePolicy,
    Enrollment,
    LectureProgress,
    LectureProgressEvent,
    LectureWatchDay,
    LectureWatchSession,
    Lesson,
    PublicationStatus,
    ViewingSession,
)
from .utils.hls_parser import get_lesson_segments
from .utils.lesson_access import lesson_is_available
from .utils.progress_merge import (
    calculate_percent,
    intersect_ranges,
    normalize_ranges,
    saved_prefix_ack,
    subtract_ranges,
    take_ordered_prefix,
    unique_seconds,
)

WATCH_SESSION_GAP = timedelta(minutes=10)
MAX_HEARTBEAT_ACTIVE_SECONDS = 60
PROGRESS_ACTIVITY_WINDOW_DAYS = 30
# One acknowledgement must stay a small bounded JSON body. The server trims it
# rather than failing a request whose mutations already committed.
ACK_MAX_BYTES = 16_384


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
    status: str = "ok"
    session_id: str = ""
    sequence: int | None = None
    accepted_ranges: list[tuple[float, float]] = field(default_factory=list)
    already_saved_ranges: list[tuple[float, float]] = field(default_factory=list)
    remaining_ranges: list[tuple[float, float]] = field(default_factory=list)
    credited_active_seconds: float = 0
    reason: str = ""
    terminal: bool = False
    last_sequence: int = 0
    next_range_index: int = 0
    ack_truncated: bool = False
    stored_acknowledgement: dict | None = None

    def as_payload(self) -> dict:
        if self.stored_acknowledgement is not None:
            return dict(self.stored_acknowledgement)
        payload = {
            "status": self.status,
            "percent": self.percent,
            "completed": self.completed,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "accepted_ranges": self.accepted_ranges,
            "already_saved_ranges": self.already_saved_ranges,
            "remaining_ranges": self.remaining_ranges,
            "credited_active_seconds": self.credited_active_seconds,
            "reason": self.reason,
            "terminal": self.terminal,
            "last_sequence": self.last_sequence,
            "next_range_index": self.next_range_index,
            "ack_truncated": self.ack_truncated,
        }
        if self.note:
            payload["note"] = self.note
        return payload


class ProgressHeartbeatError(Exception):
    """Expected domain rejection; adapters choose the HTTP status."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_progress_heartbeat(payload: object) -> dict:
    """Validate one request and freeze its centisecond-level replay identity."""
    if not isinstance(payload, dict):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid request format."))

    session_id = payload.get("session_id")
    raw_ranges = payload.get("ranges", [])
    ended = payload.get("ended", False)
    raw_active_seconds = payload.get("active_seconds", 0)
    sequence = payload.get("sequence")
    if (
        not isinstance(session_id, str)
        or not session_id
        or len(session_id) > 64
        or not isinstance(raw_ranges, list)
        or len(raw_ranges) > 200
        or not isinstance(ended, bool)
        or isinstance(raw_active_seconds, bool)
        or not isinstance(raw_active_seconds, (int, float))
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence <= 0
        or sequence > 2_147_483_647
    ):
        raise ProgressHeartbeatError(
            "invalid_payload",
            _("Missing session identifier or invalid heartbeat data."),
        )

    try:
        active_decimal = Decimal(str(raw_active_seconds))
    except (InvalidOperation, ValueError, TypeError):
        raise ProgressHeartbeatError("invalid_payload", _("Invalid active time."))
    if not active_decimal.is_finite() or active_decimal < 0 or active_decimal > 3600:
        raise ProgressHeartbeatError("invalid_payload", _("Invalid active time."))
    active_centiseconds = int(
        (active_decimal * 100).to_integral_value(rounding=ROUND_HALF_UP)
    )
    active_seconds = active_centiseconds / 100
    # The client reports the playback speed it used for this batch. The budget
    # is earned at that speed (clamped to 1x-2x), so an honest 1x viewer earns
    # exactly what they watch and a forged 2x claim buys no more than today.
    raw_playback_rate = payload.get("playback_rate", 1)
    try:
        rate_decimal = Decimal(str(raw_playback_rate))
    except (InvalidOperation, ValueError, TypeError):
        rate_decimal = Decimal("1")
    if not rate_decimal.is_finite() or rate_decimal <= 0:
        rate_decimal = Decimal("1")
    rate_decimal = min(Decimal("2"), max(Decimal("1"), rate_decimal))
    playback_rate_centi = int(
        (rate_decimal * 100).to_integral_value(rounding=ROUND_HALF_UP)
    )
    max_duration = Decimal(str(getattr(settings, "MEDIA_MAX_DURATION_SECONDS", 3 * 60 * 60)))
    raw_centisecond_ranges = []
    for item in raw_ranges:
        if (
            not isinstance(item, (list, tuple))
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in item)
        ):
            raise ProgressHeartbeatError("invalid_payload", _("Invalid time range format."))
        try:
            start, end = (Decimal(str(value)) for value in item)
        except (InvalidOperation, ValueError, TypeError):
            raise ProgressHeartbeatError("invalid_payload", _("Invalid time range format."))
        if (
            not start.is_finite()
            or not end.is_finite()
            or start < 0
            or end <= start
            or end > max_duration
        ):
            raise ProgressHeartbeatError("invalid_payload", _("Invalid time range format."))
        start_cs = int((start * 100).to_integral_value(rounding=ROUND_HALF_UP))
        end_cs = int((end * 100).to_integral_value(rounding=ROUND_HALF_UP))
        if end_cs <= start_cs:
            raise ProgressHeartbeatError("invalid_payload", _("Time range is below centisecond precision."))
        raw_centisecond_ranges.append((start_cs / 100, end_cs / 100))

    ranges = normalize_ranges(raw_centisecond_ranges)
    canonical_ranges = [
        [int(Decimal(str(start)) * 100), int(Decimal(str(end)) * 100)]
        for start, end in ranges
    ]
    canonical = {
        "session_id": session_id,
        "sequence": sequence,
        "ranges": canonical_ranges,
        "active_centiseconds": active_centiseconds,
        "playback_rate_centi": playback_rate_centi,
        "ended": ended,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "session_id": session_id,
        "sequence": sequence,
        "ranges": ranges,
        "active_seconds": active_seconds,
        "active_centiseconds": active_centiseconds,
        "playback_rate": playback_rate_centi / 100,
        "ended": ended,
        "digest": digest,
    }


def edge_credit_budget_centiseconds(
    started_at,
    window_end,
    earned_consumed_seconds,
    slack_remaining_seconds,
    playback_rate=None,
) -> tuple[int, int]:
    """Return total earned and currently available credit in centiseconds.

    ``playback_rate`` is the highest speed the session has used, clamped to
    1.00-2.00: the budget accrues at that rate per second of server time.
    """
    try:
        rate_centi = int(round(float(playback_rate if playback_rate is not None else 1) * 100))
    except (TypeError, ValueError):
        rate_centi = 100
    rate_centi = max(100, min(200, rate_centi))
    if started_at is None or window_end is None:
        earned_centiseconds = 0
    else:
        elapsed = max(timedelta(0), window_end - started_at)
        elapsed_microseconds = (
            (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000
            + elapsed.microseconds
        )
        # microseconds x centi-rate / 1e6 = media centiseconds, rounded half up.
        earned_centiseconds = (elapsed_microseconds * rate_centi + 500_000) // 1_000_000
    consumed_centiseconds = int(
        (Decimal(str(earned_consumed_seconds)) * 100).to_integral_value(
            rounding=ROUND_HALF_UP,
        )
    )
    slack_centiseconds = int(
        (Decimal(str(slack_remaining_seconds)) * 100).to_integral_value(
            rounding=ROUND_HALF_UP,
        )
    )
    available_centiseconds = max(0, earned_centiseconds - consumed_centiseconds)
    return earned_centiseconds, available_centiseconds + max(0, slack_centiseconds)


def _acknowledgement_size(ack: dict) -> int:
    return len(json.dumps(ack, separators=(",", ":")).encode("utf-8"))


def _fit_acknowledgement(ack: dict) -> dict:
    """Keep one acknowledgement inside the transport bound without losing data.

    Confirmations are advisory: a client only clears ranges it sees confirmed,
    so trimming the response is safe and never discards accepted progress.
    Dropping the response makes the client resubmit; confirming a range that is
    not persisted would be the only unsafe direction.
    """

    for field in ("remaining_ranges", "accepted_ranges", "already_saved_ranges"):
        while ack[field] and _acknowledgement_size(ack) > ACK_MAX_BYTES:
            ack[field].pop()
            ack["ack_truncated"] = True
    return ack


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
    """Persist one validated playback sample under the shared progress lock."""
    if not user or not getattr(user, "is_authenticated", False):
        raise ProgressHeartbeatError("unauthenticated", _("Authentication required"))

    heartbeat = normalize_progress_heartbeat(payload)
    session_id = heartbeat["session_id"]
    sequence = heartbeat["sequence"]
    requested_ranges = heartbeat["ranges"]
    if session is None:
        session_query = ViewingSession.objects.filter(session_id=session_id)
    else:
        session_query = ViewingSession.objects.filter(pk=session.pk, session_id=session_id)
    session = session_query.select_related("lesson__course_offering").first()
    if session is None:
        raise ProgressHeartbeatError("session_not_found", _("Viewing session was not found."))
    if session.student_id != user.pk:
        raise ProgressHeartbeatError("forbidden", _("Session belongs to another user."))
    if not lesson_is_available(session.lesson):
        raise ProgressHeartbeatError("forbidden", _("This lesson is not available yet."))
    if not user_can_write_offering_activity(
        user,
        session.lesson.course_offering,
        allow_management=allow_management,
    ):
        current = LectureProgress.objects.filter(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
        ).values("percent", "completed_at").first()
        return ProgressHeartbeatResult(
            percent=int(current["percent"] or 0) if current else 0,
            completed=bool(current and current["completed_at"]),
            note=_("Historical content is read-only"),
            status="rejected",
            session_id=session_id,
            sequence=sequence,
            reason="historical_read_only",
            terminal=True,
            last_sequence=int(session.heartbeat_sequence or 0),
        )

    def response(
        progress,
        *,
        status="ok",
        reason="",
        terminal=False,
        note="",
        ack=None,
        credited_active_seconds=0,
        last_sequence=None,
    ) -> ProgressHeartbeatResult:
        ack = ack or {}
        return ProgressHeartbeatResult(
            percent=int(progress.percent or 0) if progress else 0,
            completed=bool(progress and progress.completed_at),
            note=note,
            status=status,
            session_id=session_id,
            sequence=sequence,
            accepted_ranges=ack.get("accepted_ranges", []),
            already_saved_ranges=ack.get("already_saved_ranges", []),
            remaining_ranges=ack.get("remaining_ranges", []),
            credited_active_seconds=credited_active_seconds,
            reason=reason,
            terminal=terminal,
            last_sequence=(
                int(last_sequence)
                if last_sequence is not None
                else int(session.heartbeat_sequence or 0)
            ),
            next_range_index=int(ack.get("next_range_index", 0)),
            ack_truncated=bool(ack.get("ack_truncated", False)),
        )

    def resync(reason, progress, last_sequence):
        already_saved = intersect_ranges(
            requested_ranges,
            progress.merged_ranges if progress is not None else [],
        )
        ack = saved_prefix_ack(requested_ranges, already_saved, [])
        return response(
            progress,
            status="resync",
            reason=reason,
            ack=ack,
            last_sequence=last_sequence,
        )

    if (
        sequence == session.heartbeat_sequence
        and heartbeat["digest"] == session.last_payload_digest
        and session.last_acknowledgement
    ):
        return ProgressHeartbeatResult(
            percent=0,
            completed=False,
            stored_acknowledgement=session.last_acknowledgement,
        )
    if sequence <= session.heartbeat_sequence:
        current = LectureProgress.objects.filter(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
        ).first()
        return resync("stale_sequence", current, session.heartbeat_sequence)
    if sequence != session.heartbeat_sequence + 1:
        current = LectureProgress.objects.filter(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
        ).first()
        return resync("sequence_gap", current, session.heartbeat_sequence)

    checked_at = timezone.now()
    initial_window_end = min(
        value for value in (
            checked_at,
            session.expires_at,
            session.superseded_at,
            session.ended_at,
        ) if value is not None
    )
    replay_until = initial_window_end + timedelta(days=7)
    if checked_at > replay_until:
        raise ProgressHeartbeatError("expired", _("Session replay window expired."))

    # A report that arrives less than two seconds after the previous one is
    # deferred, not rejected: nothing is written, the ranges stay with the
    # client and the same sequence is retried. The budget already caps credit;
    # this bounds the write load a scripted client can create.
    if (
        session.heartbeat_sequence
        and session.last_heartbeat
        and (checked_at - session.last_heartbeat) < timedelta(seconds=2)
    ):
        current = LectureProgress.objects.filter(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
        ).first()
        return resync("throttled", current, session.heartbeat_sequence)

    try:
        lesson_segments = get_lesson_segments(
            session.lesson,
            cloud_client,
            bucket_name,
            media_key=session.media_key or None,
            requested_part_id=session.part_id,
        ).get(session.part_id, [])
    except Exception as exc:
        raise ProgressHeartbeatError("media_unavailable", _("The media is unavailable.")) from exc
    timeline_ranges = normalize_ranges([
        (segment.get("start", 0), segment.get("end", 0))
        for segment in lesson_segments
    ])
    if requested_ranges and not timeline_ranges:
        return ProgressHeartbeatResult(
            percent=0,
            completed=False,
            status="rejected",
            session_id=session_id,
            sequence=sequence,
            remaining_ranges=requested_ranges,
            reason="media_unavailable",
            terminal=True,
            last_sequence=int(session.heartbeat_sequence or 0),
        )

    def as_json_ranges(ranges):
        return [[float(start), float(end)] for start, end in ranges]

    with transaction.atomic():
        if not user_can_write_offering_activity(
            user,
            session.lesson.course_offering,
            allow_management=allow_management,
        ):
            raise ProgressHeartbeatError("forbidden", _("Progress is read-only."))
        progress, _created = LectureProgress.objects.get_or_create(
            student=user,
            lesson=session.lesson,
            part_id=session.part_id,
            defaults={"merged_ranges": []},
        )
        progress = LectureProgress.objects.select_for_update().get(pk=progress.pk)
        locked_session = ViewingSession.objects.select_for_update().select_related(
            "lesson__course_offering__academic_year_level__academic_year"
        ).get(pk=session.pk)
        if (
            locked_session.session_id != session_id
            or locked_session.student_id != user.pk
            or locked_session.lesson_id != session.lesson_id
            or locked_session.part_id != session.part_id
            or locked_session.media_key != session.media_key
        ):
            raise ProgressHeartbeatError("forbidden", _("Viewing session changed."))
        if not lesson_is_available(locked_session.lesson):
            raise ProgressHeartbeatError("forbidden", _("This lesson is not available yet."))
        if not user_can_write_offering_activity(
            user,
            locked_session.lesson.course_offering,
            allow_management=allow_management,
        ):
            raise ProgressHeartbeatError("forbidden", _("Progress is read-only."))

        last_sequence = int(locked_session.heartbeat_sequence or 0)
        if sequence == last_sequence:
            if (
                heartbeat["digest"] == locked_session.last_payload_digest
                and locked_session.last_acknowledgement
            ):
                return ProgressHeartbeatResult(
                    percent=0,
                    completed=False,
                    stored_acknowledgement=locked_session.last_acknowledgement,
                )
            return resync("sequence_conflict", progress, last_sequence)
        if sequence < last_sequence:
            return resync("stale_sequence", progress, last_sequence)
        if sequence != last_sequence + 1:
            return resync("sequence_gap", progress, last_sequence)

        now = timezone.now()
        window_end = min(
            value for value in (
                now,
                locked_session.expires_at,
                locked_session.superseded_at,
                locked_session.ended_at,
            ) if value is not None
        )
        if now > window_end + timedelta(days=7):
            raise ProgressHeartbeatError("expired", _("Session replay window expired."))

        timeline = timeline_ranges
        requested_on_timeline = intersect_ranges(requested_ranges, timeline)
        saved_before = normalize_ranges(progress.merged_ranges or [])
        already_saved = intersect_ranges(requested_on_timeline, saved_before)
        new_candidates = subtract_ranges(requested_on_timeline, saved_before)
        # A session keeps the fastest speed it ever reported, so switching to
        # 2x mid-lecture earns the wider allowance from then on.
        reported_rate = Decimal(str(heartbeat["playback_rate"]))
        session_rate = max(
            Decimal(str(locked_session.edge_credit_rate or 1)), reported_rate
        )
        if session_rate > Decimal(str(locked_session.edge_credit_rate or 1)):
            locked_session.edge_credit_rate = session_rate
        _earned, available_centiseconds = edge_credit_budget_centiseconds(
            locked_session.started_at,
            window_end,
            locked_session.edge_earned_seconds_consumed,
            progress.edge_slack_remaining_seconds,
            session_rate,
        )
        accepted_ranges, _remaining_by_budget = take_ordered_prefix(
            new_candidates,
            min(12_000, available_centiseconds) / 100,
        )
        accepted_centiseconds = sum(
            int(Decimal(str(end)) * 100) - int(Decimal(str(start)) * 100)
            for start, end in accepted_ranges
        )
        earned_total, _available = edge_credit_budget_centiseconds(
            locked_session.started_at,
            window_end,
            locked_session.edge_earned_seconds_consumed,
            progress.edge_slack_remaining_seconds,
            session_rate,
        )
        consumed_before = int(
            (Decimal(str(locked_session.edge_earned_seconds_consumed)) * 100).to_integral_value(
                rounding=ROUND_HALF_UP,
            )
        )
        slack_before = int(
            (Decimal(str(progress.edge_slack_remaining_seconds)) * 100).to_integral_value(
                rounding=ROUND_HALF_UP,
            )
        )
        earned_available_centiseconds = max(0, earned_total - consumed_before)
        earned_spent = min(accepted_centiseconds, earned_available_centiseconds)
        slack_spent = accepted_centiseconds - earned_spent
        locked_session.edge_earned_seconds_consumed = Decimal(
            consumed_before + earned_spent
        ) / 100
        progress.edge_slack_remaining_seconds = Decimal(
            max(0, slack_before - slack_spent)
        ) / 100

        merged = normalize_ranges(saved_before + accepted_ranges)
        total_seconds = unique_seconds(timeline)
        media_saved = intersect_ranges(merged, timeline)
        unique_secs = unique_seconds(media_saved)
        percent = calculate_percent(unique_secs, total_seconds) if total_seconds > 0 else 0
        previous_percent = progress.percent
        progress.merged_ranges = merged
        progress.percent = percent
        progress.unique_seconds = int(round(unique_seconds(merged)))
        if merged:
            furthest = max(float(end) for _start, end in merged)
            progress.last_position_seconds = max(
                int(progress.last_position_seconds or 0),
                int(furthest),
            )
        if not progress.completed_at and percent >= 80:
            progress.completed_at = now

        window_close = min(
            value for value in (
                locked_session.expires_at,
                locked_session.superseded_at,
                locked_session.ended_at,
            ) if value is not None
        )
        live_window = now < window_close and locked_session.ended_at is None
        was_ended = locked_session.ended_at
        if live_window:
            activity_cursor = (
                locked_session.activity_cursor_at
                or locked_session.started_at
                or locked_session.last_heartbeat
            )
            elapsed_seconds = max(0, int((now - activity_cursor).total_seconds()))
            requested_activity = min(
                60,
                int(heartbeat["active_seconds"] + 0.5),
                elapsed_seconds,
            )
            credited_active_seconds = credit_watch_sample(
                locked_session,
                progress,
                active_seconds=requested_activity,
                sequence=sequence,
                progress_before=previous_percent,
                progress_after=percent,
                ended=heartbeat["ended"],
                now=now,
            )
            locked_session.activity_cursor_at = now
        else:
            credited_active_seconds = 0
            locked_session.heartbeat_sequence = sequence

        if heartbeat["ended"] and was_ended is None:
            locked_session.ended_at = now
            _record_progress_event(
                locked_session,
                LectureProgressEvent.EventType.ENDED,
                occurred_at=now,
            )
        if percent != previous_percent:
            _record_progress_event(
                locked_session,
                LectureProgressEvent.EventType.PROGRESS,
                occurred_at=now,
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

        progress.save(update_fields=[
            "merged_ranges",
            "percent",
            "unique_seconds",
            "last_position_seconds",
            "completed_at",
            "edge_slack_remaining_seconds",
        ])

        ack_ranges = saved_prefix_ack(
            requested_ranges,
            already_saved,
            accepted_ranges,
        )
        reason = ""
        if ack_ranges["remaining_ranges"]:
            if requested_ranges and not requested_on_timeline:
                reason = "outside_media_timeline"
            elif available_centiseconds <= 0:
                reason = "credit_budget_exhausted"
            else:
                reason = "partial_progress"
        elif not live_window and heartbeat["active_centiseconds"]:
            reason = "replay_activity_not_credited"

        ack = {
            "status": "ok",
            "percent": percent,
            "completed": progress.completed_at is not None,
            "session_id": session_id,
            "sequence": sequence,
            "accepted_ranges": as_json_ranges(ack_ranges["accepted_ranges"]),
            "already_saved_ranges": as_json_ranges(ack_ranges["already_saved_ranges"]),
            "remaining_ranges": as_json_ranges(ack_ranges["remaining_ranges"]),
            "credited_active_seconds": int(credited_active_seconds or 0),
            "reason": reason,
            "terminal": False,
            "last_sequence": sequence,
            "next_range_index": int(ack_ranges["next_range_index"]),
            "ack_truncated": bool(ack_ranges["ack_truncated"]),
        }
        ack = _fit_acknowledgement(ack)
        locked_session.last_payload_digest = heartbeat["digest"]
        locked_session.last_acknowledgement = ack
        locked_session.save(update_fields=[
            "heartbeat_sequence",
            "activity_cursor_at",
            "edge_earned_seconds_consumed",
            "edge_credit_rate",
            "last_payload_digest",
            "last_acknowledgement",
            "ended_at",
        ])
        return ProgressHeartbeatResult(
            percent=percent,
            completed=progress.completed_at is not None,
            stored_acknowledgement=ack,
        )


def _online_attendance_outcome(
    published_at: datetime,
    completed_at: datetime | None,
    policy: AttendancePolicy,
) -> tuple[str, Decimal]:
    if completed_at is None:
        return "absent", Decimal("0.00")
    if completed_at <= published_at + timedelta(days=policy.online_full_days):
        return "full", Decimal("1.00")
    if completed_at <= published_at + timedelta(days=policy.online_partial_days):
        return "partial", policy.online_partial_value
    return "absent", Decimal("0.00")


def online_attendance_score_for_lesson(
    published_at: datetime,
    completed_at: datetime | None,
    policy: AttendancePolicy,
) -> Decimal:
    """Return the approved online attendance score at exact day boundaries."""
    return _online_attendance_outcome(published_at, completed_at, policy)[1]


def online_attendance_for_students(
    *,
    student_ids: list[int],
    offering_ids: list[int],
    window_start: date | datetime,
    window_end: date | datetime,
    policy: AttendancePolicy,
) -> dict[int, dict[str, int | Decimal]]:
    """Aggregate online lesson completion for one bounded management report batch."""
    student_ids = list(dict.fromkeys(student_ids))
    if not student_ids or not offering_ids:
        return {}
    student_ids = list(Enrollment.objects.filter(
        student_id__in=student_ids,
        academic_year_level__course_offerings__pk__in=offering_ids,
        enrollment_type=Enrollment.Type.NORMAL,
        course_offering__isnull=True,
        status=Enrollment.Status.ACTIVE,
        student__is_active=True,
        student__role__role="student",
        student__study_mode="online",
    ).values_list("student_id", flat=True).distinct())
    if not student_ids:
        return {}
    start = timezone.make_aware(window_start) if isinstance(window_start, datetime) and timezone.is_naive(window_start) else window_start
    end = timezone.make_aware(window_end) if isinstance(window_end, datetime) and timezone.is_naive(window_end) else window_end

    lessons = {}
    required_parts = {}
    for lesson in Lesson.objects.filter(
        course_offering_id__in=offering_ids,
        status=PublicationStatus.PUBLISHED,
    ).values("id", "links", "opening_date", "created_date"):
        published_at = lesson["opening_date"] or timezone.make_aware(
            datetime.combine(lesson["created_date"], time.min)
        )
        publish_day = timezone.localtime(published_at).date()
        if (published_at < start if isinstance(start, datetime) else publish_day < start) or (
            published_at > end if isinstance(end, datetime) else publish_day > end
        ):
            continue
        try:
            links = json.loads(lesson["links"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            links = []
        parts = set()
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, dict) or link.get("file_type") not in {"video", "audio"}:
                    continue
                part_id = link.get("part_id")
                if isinstance(part_id, str) and part_id.strip():
                    parts.add(part_id)
        if parts:
            lessons[lesson["id"]] = (len(parts), published_at)
            required_parts[lesson["id"]] = parts

    result = {
        student_id: {
            "expected": len(lessons),
            "valid": 0,
            "partial": 0,
            "absent": len(lessons),
            "attendance_score": Decimal("0.00"),
        }
        for student_id in student_ids
    }
    if not lessons:
        return result

    required_progress = Q(pk__in=[])
    for lesson_id, part_ids in required_parts.items():
        required_progress |= Q(lesson_id=lesson_id, part_id__in=part_ids)
    completions = LectureProgress.objects.filter(
        student_id__in=student_ids,
        percent__gte=80,
        completed_at__isnull=False,
    ).filter(required_progress).values("student_id", "lesson_id").annotate(
        completed_parts=Count("pk"),
        completed_at=Max("completed_at"),
    )
    for completion in completions:
        student_facts = result[completion["student_id"]]
        lesson_id = completion["lesson_id"]
        required_count, published_at = lessons[lesson_id]
        if completion["completed_parts"] != required_count:
            continue
        outcome, score = _online_attendance_outcome(
            published_at,
            completion["completed_at"],
            policy,
        )
        if outcome == "full":
            student_facts["valid"] += 1
        elif outcome == "partial":
            student_facts["partial"] += 1
        else:
            continue
        student_facts["absent"] -= 1
        student_facts["attendance_score"] += score
    return result
