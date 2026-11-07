"""Keep lesson R2 references aligned with storage key changes."""

import json
from collections.abc import Iterable, Iterator
from typing import Any

from django.db import transaction
from django.db.models import CharField, F, Q, QuerySet, Value
from django.db.models.functions import Concat, Substr
from django.utils import timezone

from management_system.models import (
    AcademicPayment,
    GraduationGalleryItem,
    Lesson,
    MobileAppRelease,
    User,
    ViewingSession,
)
from management_system.utils.application_uploads import (
    APPLICATION_DOCUMENT_FIELDS,
    APPLICATION_PREVIEW_FIELDS,
)
from management_system.utils.file_validator import downloadable_audio_key

REFERENCE_UPDATE_BATCH_SIZE = 500


def rewrite_r2_keys(value: Any, old_key: str, new_key: str, is_folder: bool = False) -> Any:
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


def lesson_rows_matching_storage_values(values: Iterable[str]) -> QuerySet[Lesson]:
    """Return candidate lesson rows for raw or JSON-escaped storage keys."""

    query = Q()
    has_values = False
    for value in set(values):
        if not isinstance(value, str) or not value:
            continue
        escaped = json.dumps(value, ensure_ascii=True)[1:-1]
        for candidate in {value, escaped}:
            if candidate:
                query |= Q(links__contains=candidate)
                has_values = True
    return Lesson.objects.filter(query) if has_values else Lesson.objects.none()


def _storage_search_variants(value: str) -> set[str]:
    return {value, json.dumps(value, ensure_ascii=True)[1:-1]}


def rewrite_storage_references(old_key: str, new_key: str, is_folder: bool = False) -> int:
    """Update database references to renamed storage objects."""
    search_key = old_key.rstrip("/") if is_folder else old_key
    if is_folder:
        search_key += "/"

    updated_count = 0
    with transaction.atomic():
        # ponytail: row-lock so concurrent renames touching the same lesson
        # cannot lose each other's link rewrite (last-write-wins race).
        lessons = Lesson.objects.select_for_update().filter(
            pk__in=lesson_rows_matching_storage_values([search_key]).values("pk")
        )
        changed_lessons = []
        for lesson in lessons.iterator(chunk_size=REFERENCE_UPDATE_BATCH_SIZE):
            try:
                links = json.loads(lesson.links)
            except (TypeError, ValueError):
                # A malformed row must not fail the rename; skip it.
                continue
            rewritten = rewrite_r2_keys(links, old_key, new_key, is_folder)
            if rewritten != links:
                lesson.links = json.dumps(rewritten)
                lesson.updated_date = timezone.now()
                changed_lessons.append(lesson)
                if len(changed_lessons) == REFERENCE_UPDATE_BATCH_SIZE:
                    Lesson.objects.bulk_update(
                        changed_lessons,
                        ["links", "updated_date"],
                        batch_size=REFERENCE_UPDATE_BATCH_SIZE,
                    )
                    updated_count += _rewrite_viewing_references_for_lessons(
                        [row.pk for row in changed_lessons], old_key, new_key, is_folder
                    )
                    updated_count += len(changed_lessons)
                    changed_lessons.clear()
        if changed_lessons:
            Lesson.objects.bulk_update(
                changed_lessons,
                ["links", "updated_date"],
                batch_size=REFERENCE_UPDATE_BATCH_SIZE,
            )
            updated_count += _rewrite_viewing_references_for_lessons(
                [row.pk for row in changed_lessons], old_key, new_key, is_folder
            )
            updated_count += len(changed_lessons)
        updated_count += _rewrite_application_document_references(old_key, new_key, is_folder)
        updated_count += _rewrite_gallery_and_release_references(old_key, new_key, is_folder)
        if not is_folder and str(old_key).lower().endswith(".mp3"):
            updated_count += _relink_derived_download_key(old_key, new_key)
    return updated_count


def _application_document_fields() -> tuple[str, ...]:
    return tuple(dict.fromkeys((
        *APPLICATION_DOCUMENT_FIELDS.values(),
        *APPLICATION_PREVIEW_FIELDS.values(),
    )))


def _rewrite_application_document_references(old_key: str, new_key: str, is_folder: bool) -> int:
    fields = _application_document_fields()
    if is_folder:
        old_prefix = old_key.rstrip("/") + "/"
        new_prefix = new_key.rstrip("/") + "/"
        suffix_position = len(old_prefix) + 1
        expression = lambda field: Concat(  # noqa: E731 - per-field SQL expression
            Value(new_prefix),
            Substr(F(field), suffix_position),
            output_field=CharField(),
        )
        updated = sum(
            User.objects.filter(**{f"{field}__startswith": old_prefix}).update(
                **{field: expression(field)}
            )
            for field in fields
        )
        payment_fields = ("receipt_key", "receipt_preview_key")
        return updated + sum(
            AcademicPayment.objects.filter(**{f"{field}__startswith": old_prefix}).update(
                **{field: expression(field)}
            )
            for field in payment_fields
        )
    return sum(
        User.objects.filter(**{field: old_key}).update(**{field: new_key})
        for field in fields
    ) + sum(
        AcademicPayment.objects.filter(**{field: old_key}).update(**{field: new_key})
        for field in ("receipt_key", "receipt_preview_key")
    )


def _rewrite_storage_fields(
    queryset,
    fields: tuple[str, ...],
    old_key: str,
    new_key: str,
    is_folder: bool,
) -> int:
    """Rewrite one model's exact key fields, using SQL for bounded bulk updates."""

    if is_folder:
        old_prefix = old_key.rstrip("/") + "/"
        new_prefix = new_key.rstrip("/") + "/"
        suffix_position = len(old_prefix) + 1
        return sum(
            queryset.filter(**{f"{field}__startswith": old_prefix}).update(
                **{
                    field: Concat(
                        Value(new_prefix),
                        Substr(F(field), suffix_position),
                        output_field=CharField(),
                    )
                }
            )
            for field in fields
        )
    return sum(
        queryset.filter(**{field: old_key}).update(**{field: new_key})
        for field in fields
    )


def _rewrite_gallery_and_release_references(old_key: str, new_key: str, is_folder: bool) -> int:
    return _rewrite_storage_fields(
        GraduationGalleryItem.objects.all(),
        ("display_key", "original_key"),
        old_key,
        new_key,
        is_folder,
    ) + _rewrite_storage_fields(
        MobileAppRelease.objects.all(),
        ("apk_key",),
        old_key,
        new_key,
        is_folder,
    )


def _rewrite_viewing_references_for_lessons(
    lesson_ids: list[int], old_key: str, new_key: str, is_folder: bool
) -> int:
    """Update session keys through the indexed lesson FK, in bounded batches."""

    if not lesson_ids:
        return 0
    sessions = ViewingSession.objects.filter(lesson_id__in=lesson_ids)
    if is_folder:
        normalized_prefix = old_key.rstrip("/") + "/"
        return sessions.filter(media_key__startswith=normalized_prefix).update(
            media_key=Concat(
                Value(new_key.rstrip("/") + "/"),
                Substr(F("media_key"), len(normalized_prefix) + 1),
                output_field=CharField(),
            )
        )
    return sessions.filter(media_key=old_key).update(media_key=new_key)


def lesson_referenced_storage_keys(keys: Iterable[str]) -> set[str]:
    """Return exact lesson media keys among a bounded set of storage keys."""

    candidates = set(keys)
    if not candidates:
        return set()
    aliases = {key: {key} for key in candidates}
    for key in candidates:
        if key.lower().endswith(".mp3"):
            base = key.rsplit("/", 1)[-1][:-4]
            folder = key.rsplit("/", 1)[0] if "/" in key else ""
            for manifest in (f"{base}_audio.m3u8", f"{base}.m3u8"):
                aliases[key].add(f"{folder}/{manifest}" if folder else manifest)
    search_values = set().union(*aliases.values())
    referenced: set[str] = set()
    for links_text in lesson_rows_matching_storage_values(search_values).values_list(
        "links", flat=True
    ).iterator(chunk_size=500):
        try:
            links = json.loads(links_text or "[]")
        except (TypeError, ValueError):
            for key in candidates:
                text = links_text or ""
                if any(
                    variant in text
                    for alias in aliases[key]
                    for variant in _storage_search_variants(alias)
                ):
                    referenced.add(key)
            continue
        if not isinstance(links, (dict, list)):
            for key in candidates:
                text = links_text or ""
                if any(
                    variant in text
                    for alias in aliases[key]
                    for variant in _storage_search_variants(alias)
                ):
                    referenced.add(key)
            continue
        for link in _lesson_link_objects(links):
            for field in ("id", "download_id"):
                value = link.get(field)
                if isinstance(value, str) and value in candidates:
                    referenced.add(value)
            manifest = link.get("id")
            if (
                link.get("file_type") == "audio"
                and isinstance(manifest, str)
            ):
                derived = downloadable_audio_key(manifest)
                if derived in candidates:
                    referenced.add(derived)
    return referenced


def _lesson_link_objects(value: Any) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _lesson_link_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _lesson_link_objects(nested)


def lesson_storage_keys(links: Any) -> set[str]:
    """Collect exact storage-key fields from a normalized lesson-link value."""

    return {
        value
        for link in _lesson_link_objects(links)
        for field in ("id", "download_id")
        if isinstance((value := link.get(field)), str) and value
    }


def application_referenced_storage_keys(keys: Iterable[str]) -> set[str]:
    """Return requested keys stored in application document fields."""

    candidates = set(keys)
    if not candidates:
        return set()
    query = Q()
    fields = _application_document_fields()
    for field in fields:
        query |= Q(**{f"{field}__in": candidates})
    referenced: set[str] = set()
    rows = User.objects.filter(query).values_list(*fields).iterator(chunk_size=500)
    for values in rows:
        referenced.update(value for value in values if value in candidates)
    payment_query = Q(receipt_key__in=candidates) | Q(receipt_preview_key__in=candidates)
    for values in AcademicPayment.objects.filter(payment_query).values_list(
        "receipt_key", "receipt_preview_key"
    ).iterator(chunk_size=500):
        referenced.update(value for value in values if value in candidates)
    return referenced


def application_references_under(prefix: str) -> bool:
    """Check whether application document fields reference a folder subtree."""

    normalized_prefix = prefix.rstrip("/") + "/"
    query = Q()
    for field in _application_document_fields():
        query |= Q(**{f"{field}__startswith": normalized_prefix})
    return User.objects.filter(query).exists() or AcademicPayment.objects.filter(
        Q(receipt_key__startswith=normalized_prefix)
        | Q(receipt_preview_key__startswith=normalized_prefix)
    ).exists()


def other_referenced_storage_keys(keys: Iterable[str]) -> set[str]:
    """Return requested keys retained by gallery, app-release, or active-session rows."""

    candidates = set(keys)
    if not candidates:
        return set()
    referenced: set[str] = set()
    model_fields = (
        (GraduationGalleryItem, ("display_key", "original_key")),
        (MobileAppRelease, ("apk_key",)),
    )
    for model, fields in model_fields:
        query = Q()
        for field in fields:
            query |= Q(**{f"{field}__in": candidates})
        for row in model.objects.filter(query).values_list(*fields).iterator(chunk_size=500):
            referenced.update(value for value in row if value in candidates)
    return referenced


def storage_reference_transition_conflicts(
    old_key: str,
    new_key: str,
    is_folder: bool = False,
) -> bool:
    """Whether a rename would collide with a unique gallery or APK key."""

    unique_fields = (
        (GraduationGalleryItem, ("display_key", "original_key")),
        (MobileAppRelease, ("apk_key",)),
    )
    if is_folder:
        old_prefix = old_key.rstrip("/") + "/"
        new_prefix = new_key.rstrip("/") + "/"
        for model, fields in unique_fields:
            for field in fields:
                if model.objects.filter(**{f"{field}__startswith": new_prefix}).exclude(
                    **{f"{field}__startswith": old_prefix}
                ).exists():
                    return True
        return False
    for model, fields in unique_fields:
        for field in fields:
            if model.objects.filter(**{field: new_key}).exclude(**{field: old_key}).exists():
                return True
    return False


def _relink_derived_download_key(old_key: str, new_key: str) -> int:
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
    updated = 0
    changed_lessons = []
    manifest_keys = {
        f"{manifest_folder}/{candidate}" if manifest_folder else candidate
        for candidate in (f"{base}_audio.m3u8", f"{base}.m3u8")
    }
    lessons = Lesson.objects.select_for_update().filter(
        pk__in=lesson_rows_matching_storage_values(manifest_keys).values("pk")
    )
    for lesson in lessons.iterator(chunk_size=REFERENCE_UPDATE_BATCH_SIZE):
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
            lesson.updated_date = timezone.now()
            changed_lessons.append(lesson)
            if len(changed_lessons) == REFERENCE_UPDATE_BATCH_SIZE:
                Lesson.objects.bulk_update(
                    changed_lessons,
                    ["links", "updated_date"],
                    batch_size=REFERENCE_UPDATE_BATCH_SIZE,
                )
                updated += len(changed_lessons)
                changed_lessons.clear()
    if changed_lessons:
        Lesson.objects.bulk_update(
            changed_lessons,
            ["links", "updated_date"],
            batch_size=REFERENCE_UPDATE_BATCH_SIZE,
        )
        updated += len(changed_lessons)
    return updated
