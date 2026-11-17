"""Bounded retirement of viewing-session evidence after its replay window."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from time import monotonic
from typing import Callable

from django.db import DatabaseError, connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.query import QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import LectureProgressEvent, VerifiedSegmentRequest, ViewingSession

REPLAY_WINDOW_DAYS = 7
MAX_CHILD_BATCH_SIZE = 5000
MAX_SESSION_BATCH_SIZE = 500
MIN_BATCH_SIZE = 100
TRANSACTION_TARGET_SECONDS = 2.0
MAX_TASK_RUNTIME_SECONDS = 25.0
CLEANUP_LEASE_KEY = 0x4C4D5353434C4541


class SessionCleanupError(Exception):
    """A translated, safe failure from viewing-session cleanup."""


def _expired_sessions(cutoff: datetime) -> QuerySet[ViewingSession]:
    return ViewingSession.objects.filter(
        Q(expires_at__lte=cutoff)
        | Q(superseded_at__lte=cutoff)
        | Q(ended_at__lte=cutoff)
    )


def _set_lock_timeout() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '2s'")


def _bounded_rows(queryset, limit: int) -> tuple[int, bool]:
    sample = list(queryset.order_by().values_list("pk", flat=True)[:limit + 1])
    return min(len(sample), limit + 1), len(sample) > limit


def _exact_rows(queryset) -> int:
    """Count one remaining backlog exactly; used by the operator dry-run."""

    return int(queryset.order_by().count())


def _remaining(
    queryset, limit: int, *, exact: bool
) -> tuple[int, bool]:
    """Report the remaining backlog and whether the number is a lower bound.

    The scheduled worker samples a bounded slice so one slice can never scan an
    unbounded backlog; the operator dry-run asks for exact totals instead.
    """

    if exact:
        return _exact_rows(queryset), False
    return _bounded_rows(queryset, limit)


def _contention(exc: DatabaseError) -> bool:
    cause = exc.__cause__
    return (
        getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    ) in {"55P03", "40P01", "57014"}


def _drain_batches(
    queryset,
    batch_size: int,
    deadline: float,
    mutate: Callable[[list[int]], int],
) -> tuple[int, float, bool, int]:
    total = 0
    max_transaction_seconds = 0.0
    contended = False
    transactions = 0
    while monotonic() < deadline:
        started = monotonic()
        try:
            with transaction.atomic():
                _set_lock_timeout()
                ids = list(
                    queryset.order_by("pk")
                    .select_for_update(skip_locked=True)
                    .values_list("pk", flat=True)[:batch_size]
                )
                changed = mutate(ids) if ids else 0
            elapsed = monotonic() - started
            max_transaction_seconds = max(max_transaction_seconds, elapsed)
            transactions += 1
            total += changed
            if not ids:
                break
            if elapsed > TRANSACTION_TARGET_SECONDS and batch_size > MIN_BATCH_SIZE:
                batch_size = max(MIN_BATCH_SIZE, batch_size // 2)
        except DatabaseError as exc:
            elapsed = monotonic() - started
            max_transaction_seconds = max(max_transaction_seconds, elapsed)
            transactions += 1
            if not _contention(exc):
                raise
            if batch_size <= MIN_BATCH_SIZE:
                contended = True
                break
            batch_size = max(MIN_BATCH_SIZE, batch_size // 2)
    return total, max_transaction_seconds, contended, transactions


def _delete_sessions(ids: list[int]) -> int:
    if not ids:
        return 0
    ViewingSession.objects.filter(pk__in=ids).delete()
    return len(ids)


def cleanup_expired_viewing_sessions(
    *,
    dry_run: bool,
    child_batch_size: int = MAX_CHILD_BATCH_SIZE,
    session_batch_size: int = MAX_SESSION_BATCH_SIZE,
    deadline: float | None = None,
) -> dict[str, object]:
    """Delete only replay-expired segment rows and empty sessions in short batches."""
    if connection.vendor != "postgresql":
        raise SessionCleanupError(_("Viewing-session cleanup requires PostgreSQL."))
    if not 1 <= child_batch_size <= MAX_CHILD_BATCH_SIZE:
        raise SessionCleanupError(_("Child batch size must be between 1 and %(maximum)s.") % {
            "maximum": MAX_CHILD_BATCH_SIZE,
        })
    if not 1 <= session_batch_size <= MAX_SESSION_BATCH_SIZE:
        raise SessionCleanupError(_("Session batch size must be between 1 and %(maximum)s.") % {
            "maximum": MAX_SESSION_BATCH_SIZE,
        })
    if deadline is not None and not isfinite(deadline):
        raise SessionCleanupError(_("The cleanup deadline must be finite."))

    started = monotonic()
    deadline = min(
        deadline if deadline is not None else started + MAX_TASK_RUNTIME_SECONDS,
        started + MAX_TASK_RUNTIME_SECONDS,
    )
    cutoff = timezone.now() - timedelta(days=REPLAY_WINDOW_DAYS)
    expired = _expired_sessions(cutoff)
    segment_rows = VerifiedSegmentRequest.objects.filter(session_id__in=expired.values("pk"))
    event_rows = LectureProgressEvent.objects.filter(viewing_session_id__in=expired.values("pk"))
    session_rows = expired
    result: dict[str, object] = {
        "dry_run": dry_run,
        "lease_acquired": False,
        "segments_deleted": 0,
        "events_detached": 0,
        "sessions_deleted": 0,
        "remaining_segments": 0,
        "remaining_events": 0,
        "remaining_sessions": 0,
        "segments_more": False,
        "events_more": False,
        "sessions_more": False,
        "contended": False,
        "transactions": 0,
        "max_transaction_seconds": 0.0,
    }

    # A session lock spans the short transactions and releases if the worker connection dies.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [CLEANUP_LEASE_KEY])
        acquired = bool(cursor.fetchone()[0])
    if not acquired:
        result.update(lease_acquired=False, has_backlog=True)
        return result
    result["lease_acquired"] = True

    try:
        if dry_run:
            result["remaining_segments"], result["segments_more"] = _remaining(
                segment_rows, child_batch_size, exact=True
            )
            result["remaining_events"], result["events_more"] = _remaining(
                event_rows, child_batch_size, exact=True
            )
            result["remaining_sessions"], result["sessions_more"] = _remaining(
                session_rows, session_batch_size, exact=True
            )
        else:
            segments_deleted, segment_time, segment_contended, segment_transactions = _drain_batches(
                segment_rows,
                child_batch_size,
                deadline,
                lambda ids: VerifiedSegmentRequest.objects.filter(pk__in=ids).delete()[0],
            )
            events_detached, event_time, event_contended, event_transactions = _drain_batches(
                event_rows,
                child_batch_size,
                deadline,
                lambda ids: LectureProgressEvent.objects.filter(pk__in=ids).update(viewing_session=None),
            )
            sessions_deleted, session_time, session_contended, session_transactions = _drain_batches(
                session_rows.annotate(
                    has_segments=Exists(VerifiedSegmentRequest.objects.filter(session_id=OuterRef("pk"))),
                    has_events=Exists(LectureProgressEvent.objects.filter(viewing_session_id=OuterRef("pk"))),
                ).filter(has_segments=False, has_events=False),
                session_batch_size,
                deadline,
                _delete_sessions,
            )
            result.update(
                segments_deleted=segments_deleted,
                events_detached=events_detached,
                sessions_deleted=sessions_deleted,
                contended=segment_contended or event_contended or session_contended,
                max_transaction_seconds=max(segment_time, event_time, session_time),
            )
            result["transactions"] = segment_transactions + event_transactions + session_transactions
            result["remaining_segments"], result["segments_more"] = _bounded_rows(segment_rows, child_batch_size)
            result["remaining_events"], result["events_more"] = _bounded_rows(event_rows, child_batch_size)
            result["remaining_sessions"], result["sessions_more"] = _bounded_rows(session_rows, session_batch_size)
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [CLEANUP_LEASE_KEY])
        except DatabaseError:
            connection.close()

    result["has_backlog"] = any((
        result["remaining_segments"], result["segments_more"],
        result["remaining_events"], result["events_more"],
        result["remaining_sessions"], result["sessions_more"],
    ))
    return result
