"""Keep lesson R2 references aligned with storage key changes."""

import json

from django.db import transaction
from django.db.models import Q

from management_system.models import Lesson
from management_system.utils.file_validator import downloadable_audio_key


def rewrite_r2_keys(value, old_key, new_key, is_folder=False):
    """Rewrite exact file keys or all keys below a folder prefix in JSON data."""
    if isinstance(value, str):
        if is_folder:
            old_prefix = old_key.rstrip("/") + "/"
            new_prefix = new_key.rstrip("/") + "/"
            if value == old_key.rstrip("/"):
                return new_key.rstrip("/")
            if value.startswith(old_prefix):
                return new_prefix + value[len(old_prefix):]
        elif value == old_key:
            return new_key
        return value
    if isinstance(value, list):
        return [rewrite_r2_keys(item, old_key, new_key, is_folder) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_r2_keys(item, old_key, new_key, is_folder) for key, item in value.items()}
    return value


def rewrite_lesson_r2_references(old_key, new_key, is_folder=False):
    """Update every lesson that points at a renamed or moved R2 object."""
    search_key = old_key.rstrip("/") if is_folder else old_key
    if is_folder:
        search_key += "/"

    updated_count = 0
    with transaction.atomic():
        # ponytail: row-lock so concurrent renames touching the same lesson
        # cannot lose each other's link rewrite (last-write-wins race).
        lessons = Lesson.objects.select_for_update().filter(links__contains=search_key)
        for lesson in lessons:
            try:
                links = json.loads(lesson.links)
            except (TypeError, ValueError):
                # A malformed row must not fail the rename; skip it.
                continue
            rewritten = rewrite_r2_keys(links, old_key, new_key, is_folder)
            if rewritten != links:
                lesson.links = json.dumps(rewritten)
                lesson.save(update_fields=["links", "updated_date"])
                updated_count += 1
        if not is_folder and str(old_key).lower().endswith(".mp3"):
            updated_count += _relink_derived_download_key(old_key, new_key)
    return updated_count


def _relink_derived_download_key(old_key, new_key):
    """Repoint lessons that derive their MP3 download key from an audio manifest.

    A lesson may store only the audio manifest and derive the downloadable MP3
    as `<manifest folder>/Downloadable Files/<base>.mp3`. When that MP3 is
    renamed on R2 there is no stored `download_id` to rewrite, so the derived
    link would break; store the new key explicitly on the matching audio links.
    """
    base = old_key.rsplit("/", 1)[-1][:-len(".mp3")]
    if not base:
        return 0
    manifest_folder = old_key.rsplit("/", 2)[0] if old_key.count("/") >= 2 else ""
    condition = Q()
    for candidate in (f"{base}_audio.m3u8", f"{base}.m3u8"):
        full = f"{manifest_folder}/{candidate}" if manifest_folder else candidate
        condition |= Q(links__contains=full)

    updated = 0
    for lesson in Lesson.objects.select_for_update().filter(condition):
        try:
            links = json.loads(lesson.links or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(links, list):
            continue
        changed = False
        for link in links:
            if not isinstance(link, dict) or link.get("file_type") != "audio":
                continue
            manifest = link.get("id")
            if manifest and downloadable_audio_key(manifest) == old_key and link.get("download_id") != new_key:
                link["download_id"] = new_key
                changed = True
        if changed:
            lesson.links = json.dumps(links)
            lesson.save(update_fields=["links", "updated_date"])
            updated += 1
    return updated
