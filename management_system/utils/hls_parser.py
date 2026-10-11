import json
import re
from logging import getLogger

from django.core.cache import cache

from ..utils.storage_operations import download_from_bucket

logger = getLogger(__name__)

SEGMENT_NUMBER_RE = re.compile(r"(?:^|[_-])(?P<number>\d+)\.ts(?:[?#].*)?$", re.IGNORECASE)

# Parsed manifests are immutable (the media pipeline writes a new key for every
# re-encode), so the segment timeline can be cached. This removes the per-
# heartbeat R2 GET that would otherwise run for every active student.
SEGMENTS_CACHE_TTL_SECONDS = 3600


def _segments_for_key(cloud_client, bucket_name, key):
    """Return the parsed segment timeline for one manifest key, cached briefly."""
    cache_key = f"hls:segments:{key}"
    try:
        cached = cache.get(cache_key)
    except Exception:  # a cache outage must not break progress tracking
        cached = None
    if cached is not None:
        return cached
    content = download_from_bucket(cloud_client, bucket_name, key)
    if not content:
        # Never cache a miss: a transient storage error would otherwise pin an
        # empty timeline for the whole TTL.
        return []
    segments = parse_hls_segments(content.read().decode())
    try:
        cache.set(cache_key, segments, SEGMENTS_CACHE_TTL_SECONDS)
    except Exception:
        pass
    return segments


def get_segment_number(segment_key: str):
    """Return the numeric HLS segment suffix used for progress receipts."""
    match = SEGMENT_NUMBER_RE.search(segment_key.strip())
    return int(match.group("number")) if match else None


def parse_hls_segments(playlist_content):
    segments = []
    current_duration = None
    current_start = 0.0

    for line in playlist_content.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            match = re.search(r"#EXTINF:\s*([\d.]+)", line)
            if match:
                current_duration = float(match.group(1))
        elif line and not line.startswith("#"):
            if current_duration is not None:
                segments.append({
                    "key": line,
                    "number": get_segment_number(line),
                    "duration": current_duration,
                    "start": round(current_start, 2),
                    "end": round(current_start + current_duration, 2),
                })
                current_start += current_duration
                current_duration = None
    return segments


def get_lesson_segments(
    lesson, cloud_client, bucket_name, media_key=None, requested_part_id=None
):
    result = {}
    links = json.loads(lesson.links)
    candidates = {}
    for item in links:
        if item.get("file_type") not in {"video", "audio"}:
            continue
        if media_key and item.get("id") != media_key:
            continue
        part_id = item.get("part_id")
        if not part_id or (
            requested_part_id is not None and part_id != requested_part_id
        ):
            continue
        if media_key or part_id not in candidates or item.get("file_type") == "video":
            candidates[part_id] = item

    for part_id, item in candidates.items():
        result[part_id] = _segments_for_key(cloud_client, bucket_name, item["id"])
    return result
