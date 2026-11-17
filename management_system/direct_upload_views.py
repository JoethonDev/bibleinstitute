"""HTTP boundary for direct browser-to-R2 file transfers.

Only authorization is exposed as an endpoint: the browser PUTs the bytes to the
presigned private key, and the owning view finalizes the capability inside the
form POST that already carries the domain validation and transaction. There is
no second generic upload endpoint.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .direct_uploads import (
    PURPOSE_LIMITS,
    DirectUploadError,
    authorize_direct_upload,
    upload_limits_by_purpose,
)
from .models import DirectUploadAuthorization, User
from .utils.application_uploads import APPLICATION_DOCUMENT_FIELDS
from .utils.decorators import can_manage_academic_setup, can_manage_content

logger = logging.getLogger(__name__)

SESSION_APPLICANT_METADATA_KEY = "applicant_session_key"

# A signup visitor is not signed in yet, so the document authorization cannot be
# bound to an account. It is bound to the browser session instead and bounded by
# a per-session counter, because that route has no login gate.
APPLICANT_CAPABILITY_LIMIT = 8
APPLICANT_CAPABILITY_TTL_SECONDS = 3600
APPLICANT_SESSION_PREFIX = "direct-upload-applicant:"

ADMIN_PURPOSES = {
    DirectUploadAuthorization.Purpose.APK,
    DirectUploadAuthorization.Purpose.TELEGRAM_ATTACHMENT,
    DirectUploadAuthorization.Purpose.HISTORICAL_INTAKE,
    DirectUploadAuthorization.Purpose.LEGACY_USER_CSV,
}
CONTENT_PURPOSES = {DirectUploadAuthorization.Purpose.QUIZ_JSON}


def _error(code: str, message: str, status: int = 400) -> JsonResponse:
    response = JsonResponse({"error": message, "code": code}, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _payload(request) -> dict:
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DirectUploadError("invalid_payload", _("Invalid request format.")) from exc
    if not isinstance(payload, dict):
        raise DirectUploadError("invalid_payload", _("Invalid request format."))
    return payload


def _document_target(user, requested_target) -> str:
    """Bind a document upload to the acting account or an authorized subject."""

    if requested_target in (None, "", "self"):
        if not user.pk:
            raise DirectUploadError("forbidden", _("Sign in to upload this file."), )
        return f"user:{user.pk}"
    try:
        target_id = int(requested_target)
    except (TypeError, ValueError) as exc:
        raise DirectUploadError("invalid_target", _("Invalid upload target.")) from exc
    if target_id == user.pk:
        return f"user:{target_id}"
    if not (can_manage_academic_setup(user) or can_manage_content(user)):
        raise DirectUploadError("forbidden", _("You cannot upload files for this account."))
    if not User.objects.filter(pk=target_id).exists():
        raise DirectUploadError("invalid_target", _("Invalid upload target."))
    return f"user:{target_id}"


def applicant_session_digest(request) -> str:
    """Bind a pre-account capability to this browser session."""

    if not request.session.session_key:
        request.session.create()
    session_key = request.session.session_key or ""
    if not session_key:
        return ""
    return hashlib.sha256(
        f"{APPLICANT_SESSION_PREFIX}{session_key}".encode("utf-8")
    ).hexdigest()[:32]


def _applicant_capability_allowed(digest: str) -> bool:
    """Count capabilities per session; a cache outage must not block signup."""

    cache_key = f"direct_upload:applicant:{digest}"
    try:
        used = int(cache.get(cache_key) or 0)
        if used >= APPLICANT_CAPABILITY_LIMIT:
            return False
        cache.set(cache_key, used + 1, APPLICANT_CAPABILITY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - cache outage is not an authorization signal
        logger.warning("applicant upload counter unavailable")
    return True


def _authorize_applicant_document(request):
    """Authorize one signup document for a visitor without an account yet."""

    try:
        payload = _payload(request)
        purpose = DirectUploadAuthorization.Purpose(payload.get("purpose"))
    except ValueError:
        return _error(
            "unsupported_purpose", _("This upload purpose is not supported."), status=400
        )
    except DirectUploadError as exc:
        return _error(exc.code, exc.message, status=400)
    if purpose != DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT:
        return _error("unauthenticated", _("Authentication required."), status=401)
    document_type = str(payload.get("document_type") or "").strip()
    if document_type not in APPLICATION_DOCUMENT_FIELDS:
        return _error(
            "invalid_document_type", _("Choose which document this file is."), status=400
        )
    digest = applicant_session_digest(request)
    if not digest:
        return _error("unauthenticated", _("Authentication required."), status=401)
    if not _applicant_capability_allowed(digest):
        return _error(
            "rate_limited",
            _("Too many uploads from this browser. Try again later."),
            status=429,
        )
    try:
        grant = authorize_direct_upload(
            actor=None,
            purpose=purpose,
            target=f"applicant:{digest}",
            filename=str(payload.get("filename") or ""),
            size=payload.get("size"),
            content_type=str(payload.get("content_type") or ""),
            metadata={
                "document_type": document_type,
                "applicant_session": digest,
            },
        )
    except DirectUploadError as exc:
        status = 413 if exc.code == "file_too_large" else 400
        return _error(exc.code, exc.message, status=status)
    response = JsonResponse(
        {
            "token": grant.token,
            "upload_url": grant.upload_url,
            "key": grant.staging_key,
            "expires_at": grant.expires_at,
            "max_bytes": grant.max_bytes,
            "content_types": list(grant.content_types),
            "extensions": list(grant.extensions),
            "audio_seconds": grant.audio_seconds,
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
def direct_upload_authorize(request):
    """Create one bounded capability for an exact private staging key."""

    if not request.user.is_authenticated:
        return _authorize_applicant_document(request)
    user = request.user
    try:
        payload = _payload(request)
        try:
            purpose = DirectUploadAuthorization.Purpose(payload.get("purpose"))
        except ValueError as exc:
            raise DirectUploadError(
                "unsupported_purpose", _("This upload purpose is not supported.")
            ) from exc
        if purpose in ADMIN_PURPOSES and not can_manage_academic_setup(user):
            return _error("forbidden", _("You cannot upload this file here."), status=403)
        if purpose in CONTENT_PURPOSES and not can_manage_content(user):
            return _error("forbidden", _("You cannot upload this file here."), status=403)

        metadata: dict = {}
        if purpose == DirectUploadAuthorization.Purpose.APPLICATION_DOCUMENT:
            document_type = str(payload.get("document_type") or "").strip()
            if document_type not in APPLICATION_DOCUMENT_FIELDS:
                raise DirectUploadError(
                    "invalid_document_type", _("Choose which document this file is.")
                )
            metadata["document_type"] = document_type
            target = _document_target(user, payload.get("target"))
        elif purpose == DirectUploadAuthorization.Purpose.SUPPORT_ATTACHMENT:
            target = f"user:{user.pk}"
        elif purpose in ADMIN_PURPOSES or purpose in CONTENT_PURPOSES:
            target = f"manager:{user.pk}"
        else:
            raise DirectUploadError(
                "unsupported_purpose", _("This upload purpose is not supported.")
            )

        grant = authorize_direct_upload(
            actor=user,
            purpose=purpose,
            target=target,
            filename=str(payload.get("filename") or ""),
            size=payload.get("size"),
            content_type=str(payload.get("content_type") or ""),
            metadata=metadata,
        )
    except DirectUploadError as exc:
        status = 413 if exc.code == "file_too_large" else 403 if exc.code == "forbidden" else 400
        return _error(exc.code, exc.message, status=status)
    except ValidationError as exc:
        return _error("validation_error", " ".join(exc.messages), status=400)
    response = JsonResponse(
        {
            "token": grant.token,
            "upload_url": grant.upload_url,
            "key": grant.staging_key,
            "expires_at": grant.expires_at,
            "max_bytes": grant.max_bytes,
            "content_types": list(grant.content_types),
            "extensions": list(grant.extensions),
            "audio_seconds": grant.audio_seconds,
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def direct_upload_limits(request):
    """Return the server-owned hard limits so pickers can label them truthfully."""

    if not request.user.is_authenticated:
        return _error("unauthenticated", _("Authentication required."), status=401)
    limits = []
    for purpose in DirectUploadAuthorization.Purpose.values:
        if purpose in ADMIN_PURPOSES and not can_manage_academic_setup(request.user):
            continue
        if purpose in CONTENT_PURPOSES and not can_manage_content(request.user):
            continue
        for entry in upload_limits_by_purpose(purpose):
            limits.append({"purpose": purpose, **entry})
    response = JsonResponse({"limits": limits})
    response["Cache-Control"] = "private, no-store"
    return response


def purpose_limits_for_template(purpose: str) -> list[dict]:
    """Describe one purpose for a rendered picker hint."""

    return upload_limits_by_purpose(purpose)


__all__ = [
    "PURPOSE_LIMITS",
    "direct_upload_authorize",
    "direct_upload_limits",
    "purpose_limits_for_template",
]
