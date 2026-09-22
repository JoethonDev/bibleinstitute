"""Read-only R2 audit for private application image originals and previews.

This command reports database references to private application documents
(User signup documents and AcademicPayment receipts), whether the referenced
objects exist on R2, their content-type metadata, preview size optimization,
and -- with the explicit ``--verify-content`` flag -- validates the actual
WebP bytes.  It never mutates PostgreSQL, SQLite, R2, or source data.
"""

from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.models import Q

from management_system.models import AcademicPayment, User
from management_system.utils.storage_operations import get_r2_client

# Provider/body bounds. A single preview body is never read beyond this limit.
PREVIEW_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
# Bounded orphan scan: at most 1000 provider keys per page and 100 pages.
SCAN_PAGE_SIZE = 1000
SCAN_MAX_PAGES = 100
# SQL iteration chunk size for database references.
CHUNK_SIZE = 500

# Canonical User document pairs: (original field, preview field).
USER_DOCUMENT_PAIRS = (
    ("identity_front_key", "identity_front_preview_key"),
    ("identity_back_key", "identity_back_preview_key"),
    ("payment_key", "payment_preview_key"),
    ("profile_image_key", "profile_image_preview_key"),
)
USER_DOCUMENT_LABELS = ("identity_front", "identity_back", "payment", "profile")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
EXPECTED_ORIGINAL_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}
EXPECTED_PREVIEW_CONTENT_TYPE = "image/webp"


def _file_extension(key):
    """Return the lower-case extension (with dot) of a private object key."""
    if not key or "." not in key:
        return None
    ext = key.rsplit(".", 1)[-1].strip().lower()
    return f".{ext}" if ext else None


def _provider_code(exc):
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return ((response.get("Error") or {}).get("Code") or "").strip()
    return ""


def _provider_status(exc):
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        metadata = response.get("ResponseMetadata") or {}
        status = metadata.get("HTTPStatusCode")
        return str(status) if status is not None else None
    return None


def _safe_provider_error(exc):
    """Return a safe provider failure summary without credentials or URLs."""
    parts = [type(exc).__name__]
    code = _provider_code(exc)
    status = _provider_status(exc)
    if code:
        parts.append(code)
    if status:
        parts.append(status)
    return " ".join(parts)


def _is_missing_error(exc):
    """Distinguish a provider 'object not found' result from other failures."""
    return _provider_code(exc) in {"404", "NoSuchKey", "NotFound"}


class Command(BaseCommand):
    help = (
        "Read-only R2 audit of private application image originals and previews. "
        "Reports database references, R2 existence, metadata/content-type "
        "correctness, preview size optimization, and optional WebP byte "
        "validation. Never mutates PostgreSQL, SQLite, R2, or source data."
    )

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")
        parser.add_argument(
            "--verify-content",
            action="store_true",
            help="Download each stored preview body (bounded) and verify it is valid WebP.",
        )
        parser.add_argument(
            "--fail-on-warnings",
            action="store_true",
            help="Exit non-zero when any warnings are reported (errors always fail).",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Include full private object keys in findings instead of a redacted marker.",
        )
        parser.add_argument(
            "--scan-orphans",
            action="store_true",
            help="List the bounded applications/ prefix and report objects not referenced by any database row.",
        )

    def handle(self, *args, **options):
        db_alias = options["database"]
        if db_alias not in connections.databases:
            raise CommandError(f"Database alias {db_alias!r} is not configured.")

        self.verbose = options["verbose"]
        self.verify_content = options["verify_content"]
        self.fail_on_warnings = options["fail_on_warnings"]
        self.scan_orphans = options["scan_orphans"]
        self.errors = []
        self.warnings = []
        self.referenced_keys = set() if self.scan_orphans else None
        self.totals = {
            "users": 0,
            "payments": 0,
            "originals_checked": 0,
            "previews_checked": 0,
        }

        bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
        missing_config = []
        if not bucket:
            missing_config.append("R2_BUCKET_NAME")
        for name in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
            if not getattr(settings, name, None):
                missing_config.append(name)
        if missing_config:
            raise CommandError(
                "R2 is not configured; missing: " + ", ".join(missing_config)
            )
        try:
            client = get_r2_client()
        except Exception as exc:  # provider/client construction failure
            raise CommandError(f"Could not create the R2 client: {_safe_provider_error(exc)}")

        self.stdout.write("Auditing User application documents ...")
        user_fields = ["pk"]
        for original_field, preview_field in USER_DOCUMENT_PAIRS:
            user_fields.extend((original_field, preview_field))
        user_filter = Q()
        for original_field, preview_field in USER_DOCUMENT_PAIRS:
            user_filter |= Q(**{f"{original_field}__isnull": False})
            user_filter |= Q(**{f"{preview_field}__isnull": False})
        for row in (
            User.objects.using(db_alias)
            .filter(user_filter)
            .order_by("pk")
            .values_list(*user_fields)
            .iterator(chunk_size=CHUNK_SIZE)
        ):
            user_id = row[0]
            self.totals["users"] += 1
            offset = 1
            for index, (original_field, preview_field) in enumerate(USER_DOCUMENT_PAIRS):
                original_key = row[offset]
                preview_key = row[offset + 1]
                offset += 2
                label = f"user={user_id} {USER_DOCUMENT_LABELS[index]}"
                self._audit_pair(client, bucket, label, original_key, preview_key)

        self.stdout.write("Auditing AcademicPayment receipts ...")
        for row in (
            AcademicPayment.objects.using(db_alias)
            .filter(Q(receipt_key__isnull=False))
            .order_by("pk")
            .values_list("pk", "receipt_key", "receipt_preview_key")
            .iterator(chunk_size=CHUNK_SIZE)
        ):
            payment_id, original_key, preview_key = row
            self.totals["payments"] += 1
            self._audit_pair(client, bucket, f"payment={payment_id} receipt", original_key, preview_key)

        if self.scan_orphans:
            self._scan_orphans(client, bucket)

        self.stdout.write(
            f"Summary: {self.totals['users']} user(s), {self.totals['payments']} payment(s); "
            f"originals checked {self.totals['originals_checked']}, "
            f"previews checked {self.totals['previews_checked']}; "
            f"{len(self.warnings)} warning(s), {len(self.errors)} error(s)"
        )
        if self.errors:
            raise CommandError(
                f"Application image preview audit failed with {len(self.errors)} error(s)."
            )
        if self.warnings and self.fail_on_warnings:
            raise CommandError(
                f"Application image preview audit found {len(self.warnings)} warning(s) "
                "and --fail-on-warnings was set."
            )

    # ------------------------------------------------------------------ output

    def error(self, message):
        self.errors.append(message)
        self.stderr.write(self.style.ERROR(f"ERROR: {message}"))

    def warning(self, message):
        self.warnings.append(message)
        self.stdout.write(self.style.WARNING(f"WARNING: {message}"))

    def _key_marker(self, key):
        """Redacted key marker by default; the full private key with --verbose."""
        if self.verbose:
            return repr(key)
        return "[redacted]"

    def _record_referenced(self, key):
        if self.referenced_keys is not None and key:
            self.referenced_keys.add(key)

    # ------------------------------------------------------------ R2 head helper

    def _head(self, client, bucket, key):
        """Return (metadata, provider_error); metadata None + error None = missing."""
        try:
            response = client.head_object(Bucket=bucket, Key=key)
            return response, None
        except Exception as exc:
            if _is_missing_error(exc):
                return None, None
            return None, _safe_provider_error(exc)

    # ------------------------------------------------------------- pair audit

    def _audit_pair(self, client, bucket, label, original_key, preview_key):
        original_metadata = None
        if original_key:
            self._record_referenced(original_key)
            original_metadata = self._audit_original(client, bucket, label, original_key)
        elif preview_key:
            self.error(
                f"{label} preview_without_original: preview is stored without an original "
                f"key={self._key_marker(preview_key)}"
            )
        if _file_extension(original_key) in IMAGE_EXTENSIONS and not preview_key:
            self.warning(
                f"{label} missing_preview: original image has no stored preview "
                f"(historical fallback) key={self._key_marker(original_key)}"
            )
        if preview_key:
            self._record_referenced(preview_key)
            if original_key and _file_extension(original_key) not in IMAGE_EXTENSIONS:
                self.error(
                    f"{label} preview_for_non_image: a preview is stored for a non-image "
                    f"original key={self._key_marker(preview_key)}"
                )
            self._audit_preview(client, bucket, label, preview_key, original_metadata)

    def _audit_original(self, client, bucket, label, key):
        marker = self._key_marker(key)
        metadata, provider_error = self._head(client, bucket, key)
        if provider_error:
            self.error(f"{label} original provider error: {provider_error} key={marker}")
            return None
        if metadata is None:
            self.error(f"{label} missing_original: referenced original is absent from R2 key={marker}")
            return None
        self.totals["originals_checked"] += 1
        original_extension = _file_extension(key)
        expected_type = EXPECTED_ORIGINAL_CONTENT_TYPES.get(original_extension) if original_extension else None
        content_type = (metadata.get("ContentType") or "").strip().lower()
        if expected_type:
            if content_type and content_type != expected_type:
                self.error(
                    f"{label} original wrong content type: expected {expected_type}, "
                    f"stored {content_type!r} key={marker}"
                )
            elif not content_type:
                self.warning(f"{label} original missing content type metadata key={marker}")
        return metadata

    def _audit_preview(self, client, bucket, label, key, original_metadata):
        marker = self._key_marker(key)
        extension = _file_extension(key)
        if extension != ".webp":
            self.error(f"{label} preview non-webp extension: key ends with {extension!r} key={marker}")
        metadata, provider_error = self._head(client, bucket, key)
        if provider_error:
            self.error(f"{label} preview provider error: {provider_error} key={marker}")
            return
        if metadata is None:
            self.error(f"{label} missing_preview_object: referenced preview is absent from R2 key={marker}")
            return
        self.totals["previews_checked"] += 1
        content_type = (metadata.get("ContentType") or "").strip().lower()
        if content_type and content_type != EXPECTED_PREVIEW_CONTENT_TYPE:
            self.error(
                f"{label} preview wrong content type: expected {EXPECTED_PREVIEW_CONTENT_TYPE}, "
                f"stored {content_type!r} key={marker}"
            )
        elif not content_type:
            self.warning(f"{label} preview missing content type metadata key={marker}")
        length = metadata.get("ContentLength") or 0
        if length <= 0:
            self.error(f"{label} preview non-positive content length: {length} key={marker}")
        if original_metadata:
            original_length = original_metadata.get("ContentLength") or 0
            if original_length > 0 and length >= original_length:
                self.warning(
                    f"{label} not_optimized: preview {length} bytes >= original "
                    f"{original_length} bytes key={marker}"
                )
        if self.verify_content:
            self._verify_preview_content(client, bucket, label, key, marker)

    # ------------------------------------------------------ opt-in body check

    def _verify_preview_content(self, client, bucket, label, key, marker):
        try:
            response = client.get_object(Bucket=bucket, Key=key)
        except Exception as exc:
            self.error(f"{label} preview content read provider error: {_safe_provider_error(exc)} key={marker}")
            return
        body = response.get("Body")
        if body is None:
            self.error(f"{label} preview content response has no body key={marker}")
            return
        try:
            length = response.get("ContentLength") or 0
            if length > PREVIEW_MAX_BYTES:
                self.error(
                    f"{label} preview content exceeds {PREVIEW_MAX_BYTES} bytes; body not read key={marker}"
                )
                return
            data = body.read(PREVIEW_MAX_BYTES + 1)
            if len(data) > PREVIEW_MAX_BYTES:
                self.error(
                    f"{label} preview content exceeded {PREVIEW_MAX_BYTES} bytes during read key={marker}"
                )
                return
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(data)) as image:
                        image.verify()
                        if image.format != "WEBP":
                            self.error(
                                f"{label} preview content is not WebP (format={image.format!r}) key={marker}"
                            )
                        elif image.width <= 0 or image.height <= 0:
                            self.error(
                                f"{label} preview content has non-positive dimensions "
                                f"({image.width}x{image.height}) key={marker}"
                            )
            except (
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
                UnidentifiedImageError,
                OSError,
                ValueError,
                SyntaxError,
            ):
                self.error(f"{label} preview content could not be read as an image key={marker}")
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    # --------------------------------------------------------- orphan scanning

    def _scan_orphans(self, client, bucket):
        prefix = "applications/"
        referenced = self.referenced_keys or set()
        scanned = 0
        pages = 0
        token = None
        scan_truncated = False
        while pages < SCAN_MAX_PAGES:
            pages += 1
            params = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": SCAN_PAGE_SIZE}
            if token:
                params["ContinuationToken"] = token
            try:
                response = client.list_objects_v2(**params)
            except Exception as exc:
                self.error(f"orphan scan provider error: {_safe_provider_error(exc)}")
                break
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                scanned += 1
                if key not in referenced:
                    self.warning(f"unreferenced application object key={self._key_marker(key)}")
            if response.get("IsTruncated"):
                token = response.get("NextContinuationToken")
                if not token:
                    break
            else:
                break
        else:
            scan_truncated = bool(token)
        if scan_truncated:
            self.warning(
                f"orphan scan truncated after {SCAN_MAX_PAGES} provider pages; "
                "the orphan inventory is incomplete"
            )
        self.stdout.write(
            f"Orphan scan: {scanned} object(s) under {prefix!r}, {pages} provider page(s) used"
        )
