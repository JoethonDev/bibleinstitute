"""Draft lesson creation and upload-completion publication."""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import CourseOffering, Lesson, PublicationStatus
from .utils.file_validator import validate_hls_object_key


PART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MEDIA_TYPES = {"video", "audio"}


def _safe_object_key(key: Any) -> str:
    if not isinstance(key, str) or not key or len(key) > 1024:
        raise ValidationError(_("Expected upload key is invalid."))
    if "\\" in key or any(char in key for char in "?#"):
        raise ValidationError(_("Expected upload key is invalid."))
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValidationError(_("Expected upload key is invalid."))
    parts = key.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValidationError(_("Expected upload key is invalid."))
    return key


def _validate_expected_media(expected_media: Any) -> list[dict[str, Any]]:
    if not isinstance(expected_media, list) or not expected_media or len(expected_media) > 100:
        raise ValidationError(_("Expected uploaded media is invalid."))

    links: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in expected_media:
        if not isinstance(item, dict):
            raise ValidationError(_("Expected uploaded media is invalid."))
        file_type = item.get("file_type")
        name = item.get("name")
        key = _safe_object_key(item.get("id"))
        part_id = item.get("part_id")
        if file_type not in MEDIA_TYPES | {"book"}:
            raise ValidationError(_("Expected upload type is invalid."))
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValidationError(_("Expected upload name is invalid."))
        if not isinstance(part_id, str) or not PART_ID_RE.fullmatch(part_id):
            raise ValidationError(_("Expected upload part is invalid."))
        if key in seen_keys:
            raise ValidationError(_("Expected upload keys must be unique."))
        seen_keys.add(key)

        if file_type in MEDIA_TYPES:
            valid, errors = validate_hls_object_key(key)
            if not valid or not key.lower().endswith(".m3u8"):
                raise ValidationError(errors or [_('Expected HLS manifest key is invalid.')])
        elif not key.lower().endswith(".pdf"):
            raise ValidationError(_("Expected book key must be a PDF."))

        links.append({
            "file_type": file_type,
            "name": name,
            "id": key,
            "part_id": part_id,
            "scheduled": True,
        })

    return links


def create_scheduled_lesson(*, lesson_name: str, offering_id: int, expected_media: Any, description: str | None = None) -> Lesson:
    if not isinstance(lesson_name, str) or not lesson_name.strip() or len(lesson_name.strip()) > 255:
        raise ValidationError(_("Lesson name is required and must be 255 characters or fewer."))
    if description is not None and not isinstance(description, str):
        raise ValidationError(_("Lesson description is invalid."))
    links = _validate_expected_media(expected_media)
    offering = CourseOffering.objects.select_related(
        "academic_year_level__academic_year",
    ).filter(
        pk=offering_id,
        academic_year_level__academic_year__is_active=True,
    ).first()
    if offering is None:
        raise ValidationError(_("The selected course offering is invalid."))

    with transaction.atomic():
        return Lesson.objects.create(
            name=lesson_name.strip(),
            description=(description.strip() if isinstance(description, str) else "") or None,
            course_offering=offering,
            links=json.dumps(links, ensure_ascii=False),
            status=PublicationStatus.DRAFT,
        )


def _manifest_segment_keys(manifest_key: str, playlist: str) -> list[str]:
    manifest_folder = posixpath.dirname(manifest_key)
    keys: list[str] = []
    for line in playlist.splitlines():
        reference = line.strip()
        if not reference or reference.startswith("#"):
            continue
        if reference.startswith(("/", "http://", "https://")) or not reference.lower().endswith(".ts"):
            raise ValidationError(_("Uploaded HLS manifest contains an invalid segment reference."))
        segment_key = posixpath.normpath(posixpath.join(manifest_folder, reference))
        if segment_key in {".", ".."} or segment_key.startswith("../"):
            raise ValidationError(_("Uploaded HLS manifest contains an invalid segment reference."))
        if manifest_folder and not segment_key.startswith(f"{manifest_folder}/"):
            raise ValidationError(_("Uploaded HLS segment escapes its manifest folder."))
        valid, errors = validate_hls_object_key(segment_key)
        if not valid:
            raise ValidationError(errors or [_('Uploaded HLS segment key is invalid.')])
        keys.append(segment_key)
    if not keys:
        raise ValidationError(_("Uploaded HLS manifest contains no segments."))
    return keys


def _missing_r2_objects(lesson: Lesson, cloud_client, bucket_name: str) -> list[str]:
    links = json.loads(lesson.links)
    if not links or any(link.get("scheduled") is not True for link in links):
        raise ValidationError(_("This lesson is not a scheduled upload draft."))

    missing: list[str] = []
    checked: set[str] = set()

    def check_object(key: str) -> bool:
        if key in checked:
            return True
        checked.add(key)
        try:
            cloud_client.head_object(Bucket=bucket_name, Key=key)
            return True
        except Exception:
            missing.append(key)
            return False

    for link in links:
        key = link["id"]
        if not check_object(key) or link.get("file_type") not in MEDIA_TYPES:
            continue
        try:
            response = cloud_client.get_object(Bucket=bucket_name, Key=key)
            playlist = response["Body"].read().decode("utf-8")
            segment_keys = _manifest_segment_keys(key, playlist)
        except ValidationError:
            raise
        except Exception:
            missing.append(key)
            continue
        for segment_key in segment_keys:
            check_object(segment_key)

    return missing


def finalize_scheduled_lesson(*, lesson_id: int, cloud_client, bucket_name: str) -> Lesson:
    with transaction.atomic():
        lesson = Lesson.objects.select_for_update().select_related("course_offering").get(pk=lesson_id)
        if lesson.status != PublicationStatus.DRAFT:
            raise ValidationError(_("Only a draft scheduled lesson can be finalized."))
        missing = _missing_r2_objects(lesson, cloud_client, bucket_name)
        if missing:
            raise ValidationError({"missing_objects": missing[:100]})

        links = json.loads(lesson.links)
        for link in links:
            link.pop("scheduled", None)
        lesson.links = json.dumps(links, ensure_ascii=False)
        lesson.status = PublicationStatus.PUBLISHED
        lesson.save(update_fields=["links", "status", "updated_date"])
        return lesson
