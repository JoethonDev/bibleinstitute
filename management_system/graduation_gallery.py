"""Canonical graduation-gallery rules shared by web, mobile, and workers."""

from __future__ import annotations

import os
import uuid
import warnings
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.db.models import Exists, F, OuterRef, QuerySet, Subquery
from django.utils.translation import gettext as _

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import AcademicYearLevel, GraduationGalleryItem, PromotionHistory
from .utils.storage_operations import get_r2_client


GALLERY_PAGE_SIZE = 30
GALLERY_DISPLAY_MAX_DIM = 1600
GALLERY_DISPLAY_QUALITY = 82
GALLERY_PUBLIC_PREFIX = "galleries"
GALLERY_STAGING_PREFIX = "gallery-staging"

# Full-pass promotion outcomes that open the gallery for the source scope.
# Partial/repeat outcomes never qualify.
GRADUATION_PASS_OUTCOMES = frozenset(
    {
        "passed",
        "passed_with_exceptions",
        "graduated",
        "historical_promoted",
        "historical_graduated",
    }
)

GALLERY_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
GALLERY_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
GALLERY_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
GALLERY_VIDEO_MIME_TYPES = {"video/mp4", "video/quicktime", "video/x-m4v"}
GALLERY_ZIP_MIME_TYPES = {"application/zip", "application/x-zip-compressed"}

MAX_SINGLE_IMAGE_BYTES = 25 * 1024 * 1024
MAX_SINGLE_VIDEO_BYTES = 500 * 1024 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000


def is_management_user(user) -> bool:
    """Return True for admin/staff gallery managers and viewers."""
    return bool(getattr(getattr(user, "role", None), "role", None) in {"admin", "staff"})


def can_upload_gallery(user) -> bool:
    """Admin and staff may upload gallery files (content management)."""
    return is_management_user(user)


def can_delete_gallery(user) -> bool:
    """Admin and staff may delete gallery items (content management)."""
    return is_management_user(user)


def graduation_scope_for_year(academic_year) -> object | None:
    """Return the AcademicYearLevel that opens the gallery for a year, if any."""
    graduation_level_id = getattr(academic_year, "graduation_level_id", None)
    if not graduation_level_id:
        return None
    return (
        AcademicYearLevel.objects.filter(
            academic_year_id=academic_year.pk, level_id=graduation_level_id
        )
        .select_related("academic_year", "level")
        .first()
    )


def is_scope_graduate(user, scope) -> bool:
    """Return True when the user fully passed the year's graduation scope."""
    if user is None or getattr(user, "pk", None) is None or scope is None:
        return False
    if is_management_user(user):
        return True
    graduation_level_id = getattr(
        getattr(scope, "academic_year", None), "graduation_level_id", None
    )
    if not graduation_level_id or scope.level_id != graduation_level_id:
        return False
    return PromotionHistory.objects.filter(
        student_id=user.pk,
        source_year_level_id=scope.pk,
        outcome__in=GRADUATION_PASS_OUTCOMES,
    ).exists()


def graduation_scopes_for_user(user) -> QuerySet:
    """Return graduation scopes the user may browse, newest year first."""
    scopes = AcademicYearLevel.objects.filter(
        academic_year__graduation_level_id=F("level_id"),
    ).select_related("academic_year", "level")
    if is_management_user(user):
        return scopes.order_by("-academic_year__ordering", "level__ordering")
    passed = PromotionHistory.objects.filter(
        student_id=getattr(user, "pk", None),
        source_year_level_id=OuterRef("pk"),
        outcome__in=GRADUATION_PASS_OUTCOMES,
    )
    return (
        scopes.annotate(_passed=Exists(passed))
        .filter(_passed=True)
        .order_by("-academic_year__ordering", "level__ordering")
    )


def gallery_has_access(user, scope) -> bool:
    """One viewer check reused by every web, API, and mobile entry point."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return is_scope_graduate(user, scope)


def _slug_year_name(name: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in (name or "").strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "year"


def slug_year_name(name: str) -> str:
    """Public slug used for gallery R2 prefixes."""
    return _slug_year_name(name)


def human_slug(filename: str, fallback: str) -> str:
    """Return a human-readable slug of a filename base, keeping unicode words."""
    base = os.path.splitext(os.path.basename(filename or ""))[0].strip()
    cleaned = "".join(
        ch if (ch.isalnum() or ch in ("-", "_", " ")) else "-"
        for ch in base
    )
    slug = "-".join(cleaned.split())
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")[:60].strip("-")
    return slug or fallback


def build_original_key(academic_year_name: str, level_ordering: int, filename: str) -> str:
    """Build one human-readable original key under the gallery prefix."""
    year = _slug_year_name(academic_year_name)
    ext = os.path.splitext(filename or "")[1].lower() or ".mp4"
    if ext == ".mov":
        ext = ".mp4"
    if ext not in GALLERY_VIDEO_EXTENSIONS:
        ext = ".mp4"
    name = human_slug(filename, "video")
    unique_suffix = uuid.uuid4().hex[:6]
    return f"{GALLERY_PUBLIC_PREFIX}/{year}/{int(level_ordering)}/original/{name}-{unique_suffix}{ext}"


def build_image_keys(academic_year_name: str, level_ordering: int, filename: str) -> tuple[str, str]:
    """Build the human-readable (display_webp_key, original_key) pair for one image."""
    year = _slug_year_name(academic_year_name)
    level = int(level_ordering)
    name = human_slug(filename, "photo")
    unique_suffix = uuid.uuid4().hex[:6]
    display_key = f"{GALLERY_PUBLIC_PREFIX}/{year}/{level}/display/{name}-{unique_suffix}.webp"
    ext = os.path.splitext(filename or "")[1].lower() or ".jpg"
    if ext not in GALLERY_IMAGE_EXTENSIONS:
        ext = ".jpg"
    original_key = f"{GALLERY_PUBLIC_PREFIX}/{year}/{level}/original/{name}-{unique_suffix}{ext}"
    return display_key, original_key


def build_staging_key(job_uuid: str, filename: str) -> str:
    safe = os.path.basename(filename or "upload").strip() or "upload"
    safe = "".join(ch for ch in safe if ord(ch) >= 32 and ch not in ("/", "\\"))[:180]
    return f"{GALLERY_STAGING_PREFIX}/{job_uuid}/{uuid.uuid4().hex}-{safe}"


def _gallery_url_expires_seconds() -> int:
    try:
        value = int(getattr(settings, "GALLERY_URL_EXPIRES_SECONDS", 21600) or 21600)
    except (TypeError, ValueError):
        value = 21600
    return max(600, min(value, 7 * 24 * 3600))


def _gallery_cache_timeout(expires_in: int) -> int:
    return max(300, min(expires_in - 600, 18000))


def presigned_gallery_url(key: str, expires_in: int | None = None) -> str | None:
    """Return a cached signed GET URL for a gallery key.

    The signed URL is reused from the Django cache until shortly before its
    expiry so Cloudflare and browsers can actually cache the bytes (a fresh
    signature per render would defeat edge caching). Gallery keys are
    unguessable uuid values and the album listing stays authenticated, so the
    URL itself is the capability.
    """
    if not key:
        return None
    cache_key = f"gallery_url:{key}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    client = get_r2_client()
    if client is None or not bucket:
        return None
    expires = expires_in or _gallery_url_expires_seconds()
    try:
        max_age = int(getattr(settings, "GALLERY_R2_PUBLIC_CACHE_MAX_AGE", 31536000) or 31536000)
    except (TypeError, ValueError):
        max_age = 31536000
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseCacheControl": f"public, max-age={max_age}, immutable",
            },
            ExpiresIn=expires,
        )
    except Exception:
        return None
    cache.set(cache_key, url, _gallery_cache_timeout(expires))
    return url


def gallery_file_url(key: str) -> str | None:
    """One URL builder reused by every web, API, and mobile gallery reader."""
    return presigned_gallery_url(key)


def gallery_item_urls(item) -> tuple[str | None, str | None]:
    """Return (display_url, download_url) for a gallery item with one client."""
    return gallery_file_url(item.display_key), gallery_file_url(item.original_key)


def invalidate_gallery_urls(*keys: str) -> None:
    """Drop cached signed URLs after an item is deleted."""
    cache_keys = [f"gallery_url:{key}" for key in keys if key]
    if cache_keys:
        cache.delete_many(cache_keys)


def album_covers(scope_ids) -> dict:
    """Return the newest gallery item per scope using the gallery ordering index."""
    if not scope_ids:
        return {}
    newest_item_ids = AcademicYearLevel.objects.filter(pk__in=scope_ids).annotate(
        newest_item_id=Subquery(
            GraduationGalleryItem.objects.filter(academic_year_level_id=OuterRef("pk"))
            .order_by("-created_at", "-pk")
            .values("pk")[:1]
        )
    ).exclude(newest_item_id=None).values_list("newest_item_id", flat=True)
    return {
        row.academic_year_level_id: row
        for row in GraduationGalleryItem.objects.filter(pk__in=newest_item_ids).only(
            "academic_year_level_id", "display_key", "original_key", "kind"
        )
    }


def classify_upload(filename: str, content_type: str) -> str:
    """Return 'image', 'video', 'zip', or '' for an upload candidate."""
    ext = os.path.splitext(filename or "")[1].lower()
    mime = (content_type or "").split(";")[0].strip().lower()
    if ext == ".zip" or mime in GALLERY_ZIP_MIME_TYPES:
        return "zip"
    if ext in GALLERY_IMAGE_EXTENSIONS and (not mime or mime in GALLERY_IMAGE_MIME_TYPES):
        return "image"
    if ext in GALLERY_VIDEO_EXTENSIONS and (not mime or mime in GALLERY_VIDEO_MIME_TYPES):
        return "video"
    # Extension-led acceptance: cameras often send octet-stream for valid files.
    if ext in GALLERY_IMAGE_EXTENSIONS:
        return "image"
    if ext in GALLERY_VIDEO_EXTENSIONS:
        return "video"
    return ""


def validate_single_upload(filename: str, content_type: str, size: int) -> None:
    """Raise ValueError with a translated message when a single upload is invalid."""
    kind = classify_upload(filename, content_type)
    if kind == "image":
        if size > MAX_SINGLE_IMAGE_BYTES:
            raise ValueError(_("Image files must be 25 MB or smaller."))
        return
    if kind == "video":
        if size > MAX_SINGLE_VIDEO_BYTES:
            raise ValueError(_("Video files must be 500 MB or smaller."))
        return
    if kind == "zip":
        if size > MAX_ZIP_BYTES:
            raise ValueError(_("Zip files must be 2 GB or smaller."))
        return
    raise ValueError(_("Only JPG, PNG, WebP images, MP4 videos, or a zip of those is allowed."))


def optimize_gallery_image(raw: bytes) -> tuple[bytes, int, int]:
    """Return (webp_bytes, width, height) bounded to the display maximum."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                transposed = ImageOps.exif_transpose(source)
                image = transposed if transposed is not None else source
                if image.mode in {"RGBA", "LA", "P"}:
                    image = image.convert("RGBA")
                else:
                    image = image.convert("RGB")
                resample = getattr(Image, "LANCZOS", None)
                if resample is None:
                    resample = Image.Resampling.LANCZOS
                image.thumbnail(
                    (GALLERY_DISPLAY_MAX_DIM, GALLERY_DISPLAY_MAX_DIM), resample
                )
                width, height = image.size
                out = BytesIO()
                image.save(out, format="WEBP", quality=GALLERY_DISPLAY_QUALITY, method=6)
                return out.getvalue(), int(width), int(height)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(_("Image content could not be read.")) from exc
