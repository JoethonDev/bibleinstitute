"""HTTP boundary for direct browser-to-R2 file transfers.

Only authorization is exposed as an endpoint: the browser PUTs the bytes to the
presigned private key, and the owning view finalizes the capability inside the
form POST that already carries the domain validation and transaction. There is
no second generic upload endpoint.
"""

from __future__ import annotations

import json
import logging

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


@require_POST
def direct_upload_authorize(request):
    """Create one bounded capability for an exact private staging key."""

    if not request.user.is_authenticated:
        return _error("unauthenticated", _("Authentication required."), status=401)
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
