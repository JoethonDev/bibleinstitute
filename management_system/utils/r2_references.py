"""Keep lesson R2 references aligned with storage key changes."""

import json

from django.db import transaction

from management_system.models import Lesson


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
        lessons = Lesson.objects.filter(links__contains=search_key)
        for lesson in lessons:
            links = json.loads(lesson.links)
            rewritten = rewrite_r2_keys(links, old_key, new_key, is_folder)
            if rewritten != links:
                lesson.links = json.dumps(rewritten)
                lesson.save(update_fields=["links", "updated_date"])
                updated_count += 1
    return updated_count
