"""Shared validation for HLS segment storage keys."""

import posixpath

from django.utils.translation import gettext as _

from .storage_ownership import is_private_storage_key
from .utils.hls_parser import get_segment_number


class MediaAccessError(Exception):
    """Expected media rejection; adapters choose the HTTP status and body."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def normalize_segment_key(segment_key: str) -> str:
    """Validate and normalize one relative HLS segment storage key."""
    if not isinstance(segment_key, str):
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    cleaned = segment_key.lstrip("/")
    if (
        not cleaned
        or "\x00" in cleaned
        or "\\" in cleaned
        or "?" in cleaned
        or "#" in cleaned
        or cleaned.startswith(("http://", "https://"))
    ):
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    parts = cleaned.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    normalized = posixpath.normpath(cleaned)
    if (
        normalized != cleaned
        or not normalized.lower().endswith(".ts")
        or is_private_storage_key(normalized)
        or get_segment_number(normalized) is None
    ):
        raise MediaAccessError("forbidden", _("Invalid viewing session token."))
    return normalized
