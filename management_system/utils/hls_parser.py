import json
import re
from logging import getLogger

from ..utils.storage_operations import download_from_bucket

logger = getLogger(__name__)


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
                    "duration": current_duration,
                    "start": round(current_start, 2),
                    "end": round(current_start + current_duration, 2),
                })
                current_start += current_duration
                current_duration = None
    return segments


def get_lesson_segments(lesson, cloud_client, bucket_name):
    result = {}
    links = json.loads(lesson.links)
    for item in links:
        if item.get("file_type") != "video":
            continue
        part_id = item.get("part_id")
        if not part_id:
            continue
        content = download_from_bucket(cloud_client, bucket_name, item["id"])
        if content:
            playlist = content.read().decode()
            segments = parse_hls_segments(playlist)
            if part_id not in result:
                result[part_id] = []
            result[part_id].extend(segments)
    return result
