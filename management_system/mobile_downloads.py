"""Backend-controlled mobile download switches (in-app library vs device export).

The admin dashboard owns two master toggles (see ``MobileDownloadPolicy``).
The mobile client fetches them from ``GET /api/mobile/v1/download-policy/``
and hides the corresponding save buttons. Endpoints that issue fresh download
bytes additionally enforce the matching toggle when the client declares its
``purpose`` (``in_app`` for offline-library saves, ``device`` for
save-to-phone/share exports):

- ``lesson_media_resource`` (audio presigned URL, download-only endpoint)
- ``lesson_book_document`` (PDF bytes; the online reader sends no purpose, so
  reading keeps working while in-app saves are blocked)

Requests without a ``purpose`` are allowed through so builds predating the
toggles keep working. Gallery ``download_url`` values are bearer-less signed
storage URLs, so gallery exports are gated client-side only.
"""

from __future__ import annotations

from .mobile_http import json_api_response
from .models import MobileDownloadPolicy

PURPOSE_IN_APP = "in_app"
PURPOSE_DEVICE = "device"


def normalize_purpose(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in (PURPOSE_IN_APP, PURPOSE_DEVICE):
        return candidate
    return None


def download_policy_payload() -> dict:
    policy = MobileDownloadPolicy.load()
    return {
        "in_app_download_enabled": bool(policy.in_app_download_enabled),
        "device_download_enabled": bool(policy.device_download_enabled),
    }


def download_disabled_response(request, purpose: str):
    """Return a 403 when ``purpose`` is disabled, else ``None``."""
    policy = MobileDownloadPolicy.load()
    if purpose == PURPOSE_IN_APP and not policy.in_app_download_enabled:
        return json_api_response(
            request,
            {
                "error": {
                    "code": "download_disabled",
                    "message": "Saving in the app is disabled.",
                    "details": {"purpose": purpose},
                }
            },
            status=403,
        )
    if purpose == PURPOSE_DEVICE and not policy.device_download_enabled:
        return json_api_response(
            request,
            {
                "error": {
                    "code": "download_disabled",
                    "message": "Saving on the device is disabled.",
                    "details": {"purpose": purpose},
                }
            },
            status=403,
        )
    return None


def enforce_download_purpose(request):
    """Refuse the request when its ``purpose`` toggle is off, else ``None``.

    Requests without a ``purpose`` (online reading, streaming, legacy builds)
    always pass through.
    """
    purpose = normalize_purpose(request.GET.get("purpose"))
    if purpose is None:
        return None
    return download_disabled_response(request, purpose)
