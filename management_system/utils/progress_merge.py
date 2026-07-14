def merge_ranges(ranges):
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda x: x[0])
    merged = [list(sorted_ranges[0])]
    for start, end in sorted_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(round(s, 2), round(e, 2)) for s, e in merged]


def unique_seconds(ranges):
    merged = merge_ranges(ranges)
    return sum(round(e - s, 2) for s, e in merged)


def calculate_percent(unique_secs, total_duration):
    if total_duration <= 0:
        return 0
    return min(int(unique_secs / total_duration * 100), 100)


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
