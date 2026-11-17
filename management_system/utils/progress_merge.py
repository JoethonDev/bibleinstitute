import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Sequence


def _centiseconds(value: object) -> int | None:
    try:
        seconds = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not seconds.is_finite():
        return None
    return int((seconds * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _from_centiseconds(value: int) -> float:
    return value / 100


def normalize_ranges(ranges: Iterable[Sequence[float]]) -> list[tuple[float, float]]:
    """Normalize and merge playback coverage at exact centisecond precision."""
    normalized = []
    for item in ranges or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        start, end = _centiseconds(item[0]), _centiseconds(item[1])
        if start is None or end is None:
            continue
        start = max(0, start)
        if end <= start:
            continue
        normalized.append((start, end))
    normalized.sort()
    merged: list[list[int]] = []
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(_from_centiseconds(start), _from_centiseconds(end)) for start, end in merged]


def subtract_ranges(
    ranges: Iterable[Sequence[float]], covered_ranges: Iterable[Sequence[float]]
) -> list[tuple[float, float]]:
    """Return normalized portions of ranges not already covered."""
    requested = [(_centiseconds(start), _centiseconds(end)) for start, end in normalize_ranges(ranges)]
    covered = [(_centiseconds(start), _centiseconds(end)) for start, end in normalize_ranges(covered_ranges)]
    difference: list[tuple[int, int]] = []
    for start, end in requested:
        cursor = start
        for covered_start, covered_end in covered:
            if covered_end <= cursor:
                continue
            if covered_start >= end:
                break
            if covered_start > cursor:
                difference.append((cursor, min(covered_start, end)))
            cursor = max(cursor, covered_end)
            if cursor >= end:
                break
        if cursor < end:
            difference.append((cursor, end))
    return [(_from_centiseconds(start), _from_centiseconds(end)) for start, end in difference]


def intersect_ranges(
    ranges: Iterable[Sequence[float]], allowed_ranges: Iterable[Sequence[float]]
) -> list[tuple[float, float]]:
    """Return the centisecond-normalized overlap between two range unions."""
    requested = normalize_ranges(ranges)
    allowed = normalize_ranges(allowed_ranges)
    intersections: list[tuple[float, float]] = []
    requested_index = allowed_index = 0
    while requested_index < len(requested) and allowed_index < len(allowed):
        start = max(requested[requested_index][0], allowed[allowed_index][0])
        end = min(requested[requested_index][1], allowed[allowed_index][1])
        if start < end:
            intersections.append((start, end))
        if requested[requested_index][1] <= allowed[allowed_index][1]:
            requested_index += 1
        else:
            allowed_index += 1
    return intersections


def take_ordered_prefix(
    ranges: Iterable[Sequence[float]], max_seconds: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Allocate a bounded earliest-first prefix and return every unaccepted suffix."""
    budget = _centiseconds(max_seconds)
    remaining_budget = max(0, budget or 0)
    accepted: list[tuple[float, float]] = []
    remaining: list[tuple[float, float]] = []
    for start_value, end_value in normalize_ranges(ranges):
        start, end = _centiseconds(start_value), _centiseconds(end_value)
        take = min(end - start, remaining_budget)
        if take:
            accepted.append((_from_centiseconds(start), _from_centiseconds(start + take)))
            remaining_budget -= take
        if start + take < end:
            remaining.append((_from_centiseconds(start + take), _from_centiseconds(end)))
    return accepted, remaining


def saved_prefix_ack(
    ranges: Iterable[Sequence[float]],
    already_saved: Iterable[Sequence[float]],
    newly_saved: Iterable[Sequence[float]],
    *,
    max_fragments: int = 200,
) -> dict:
    """Confirm contiguous saved prefixes and report every unconfirmed suffix.

    The acknowledgement never invents coverage: a fragment is only confirmed
    when it is fully covered by ``already_saved`` or ``newly_saved``. Once a
    gap is reached, every remaining suffix of every requested range is
    reported so a client can retain and resubmit them; the response itself is
    bounded by ``max_fragments`` and says so through ``ack_truncated`` and
    ``next_range_index``.
    """
    requested = normalize_ranges(ranges)
    saved = [(_centiseconds(start), _centiseconds(end)) for start, end in normalize_ranges(already_saved)]
    accepted = [(_centiseconds(start), _centiseconds(end)) for start, end in normalize_ranges(newly_saved)]
    saved_index = accepted_index = fragments = 0
    accepted_ack: list[tuple[float, float]] = []
    saved_ack: list[tuple[float, float]] = []
    remaining_ack: list[tuple[float, float]] = []
    limit = max(1, int(max_fragments))

    def append_remaining(range_index: int, cursor: int) -> bool:
        """Append each unconfirmed suffix; return True when the bound cuts it."""
        nonlocal fragments
        for index in range(range_index, len(requested)):
            start = _centiseconds(requested[index][0])
            end = _centiseconds(requested[index][1])
            suffix_start = cursor if index == range_index else max(start, cursor)
            if suffix_start >= end:
                continue
            if fragments >= limit:
                return True
            remaining_ack.append(
                (_from_centiseconds(suffix_start), _from_centiseconds(end))
            )
            fragments += 1
        return False

    for range_index, (start_value, end_value) in enumerate(requested):
        start, end = _centiseconds(start_value), _centiseconds(end_value)
        cursor = start
        while cursor < end:
            while saved_index < len(saved) and saved[saved_index][1] <= cursor:
                saved_index += 1
            while accepted_index < len(accepted) and accepted[accepted_index][1] <= cursor:
                accepted_index += 1
            if saved_index < len(saved) and saved[saved_index][0] <= cursor < saved[saved_index][1]:
                covered_end = min(end, saved[saved_index][1])
                target = saved_ack
            elif accepted_index < len(accepted) and accepted[accepted_index][0] <= cursor < accepted[accepted_index][1]:
                covered_end = min(end, accepted[accepted_index][1])
                target = accepted_ack
            else:
                return {
                    "accepted_ranges": accepted_ack,
                    "already_saved_ranges": saved_ack,
                    "remaining_ranges": remaining_ack,
                    "next_range_index": range_index,
                    "ack_truncated": append_remaining(range_index, cursor),
                }
            if fragments >= limit:
                append_remaining(range_index, cursor)
                return {
                    "accepted_ranges": accepted_ack,
                    "already_saved_ranges": saved_ack,
                    "remaining_ranges": remaining_ack,
                    "next_range_index": range_index,
                    "ack_truncated": True,
                }
            target.append((_from_centiseconds(cursor), _from_centiseconds(covered_end)))
            fragments += 1
            cursor = covered_end

    return {
        "accepted_ranges": accepted_ack,
        "already_saved_ranges": saved_ack,
        "remaining_ranges": remaining_ack,
        "next_range_index": len(requested),
        "ack_truncated": False,
    }


def merge_ranges(ranges):
    if not ranges:
        return []
    valid_ranges = []
    for item in ranges:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start, end = max(0.0, start), max(0.0, end)
        if end <= start:
            continue
        valid_ranges.append((start, end))
    if not valid_ranges:
        return []
    sorted_ranges = sorted(valid_ranges, key=lambda x: x[0])
    merged = [list(sorted_ranges[0])]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(s, 2), round(e, 2)) for s, e in merged]


def unique_seconds(ranges):
    """Return exact coverage seconds, summing whole centiseconds once."""
    merged = normalize_ranges(ranges)
    total_centiseconds = sum(
        _centiseconds(end) - _centiseconds(start) for start, end in merged
    )
    return total_centiseconds / 100


def calculate_percent(unique_secs, total_duration):
    """Return integer percent without binary-float drift at exact boundaries.

    A student who covered exactly 80 percent of the timeline must be treated
    as complete, so the division is decimal and only truncated afterwards.
    """

    try:
        covered = Decimal(str(unique_secs))
        total = Decimal(str(total_duration))
    except (InvalidOperation, ValueError, TypeError):
        return 0
    if not covered.is_finite() or not total.is_finite() or total <= 0:
        return 0
    return min(int((covered / total) * 100), 100)


def intersect_verified(player_ranges, verified_segments):
    if not player_ranges or not verified_segments:
        return []
    verified = [(s["start"], s["end"]) for s in verified_segments]
    merged_verified = merge_ranges(verified)
    merged_player = merge_ranges(player_ranges)
    result = []
    for p_start, p_end in merged_player:
        for v_start, v_end in merged_verified:
            start = max(p_start, v_start)
            end = min(p_end, v_end)
            if start < end:
                result.append((start, end))
    return merge_ranges(result)


def merge_verified_progress_ranges(existing_ranges, player_ranges, verified_segments):
    """Merge only newly verified playback into the persisted coverage union."""
    newly_verified = intersect_verified(player_ranges, verified_segments)
    return merge_ranges(list(existing_ranges or []) + newly_verified)
