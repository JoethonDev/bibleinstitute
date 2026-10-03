"""Backend-controlled mobile build versioning (force + optional update).

The mobile client reports its native build on every request via
``X-App-Platform`` (``android``/``ios``) and ``X-App-Build`` (native
versionCode / CFBundleVersion as an integer string), with ``X-App-Version``
carrying the human-readable version name. The same values are accepted as
``platform`` / ``version_code`` / ``version_name`` query parameters on the
public ``GET /api/mobile/v1/app-version/`` endpoint.

When a platform policy is enabled:
- client build < ``min_version_code``  -> forced update (HTTP 426 + blocking UI)
- client build < ``latest_version_code`` -> optional update (dismissible UI)
- the redirect link always comes from the backend ``update_url`` field.
"""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from .mobile_http import json_api_response
from .models import MobileAppUpdatePolicy

SUPPORTED_PLATFORMS = (
    MobileAppUpdatePolicy.Platform.ANDROID,
    MobileAppUpdatePolicy.Platform.IOS,
)

MAX_VERSION_CODE = 2_147_483_647


def normalize_platform(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if candidate in SUPPORTED_PLATFORMS:
        return candidate
    return None


def parse_version_code(value: object) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        code = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    if code < 0 or code > MAX_VERSION_CODE:
        return None
    return code


def is_valid_update_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        return False
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


def get_policy(platform: str) -> MobileAppUpdatePolicy | None:
    return MobileAppUpdatePolicy.objects.filter(platform=platform).first()


def evaluate_policy(
    policy: MobileAppUpdatePolicy | None,
    client_code: int | None,
) -> dict:
    """Return the force/optional decision for a client build number."""
    if policy is None or not policy.is_enabled or client_code is None:
        return {"update_required": False, "update_optional": False}
    if client_code < policy.min_version_code:
        return {"update_required": True, "update_optional": False}
    if policy.latest_version_code > 0 and client_code < policy.latest_version_code:
        return {"update_required": False, "update_optional": True}
    return {"update_required": False, "update_optional": False}


def build_version_payload(
    platform: str,
    client_code: int | None,
    client_name: str = "",
) -> dict:
    policy = get_policy(platform)
    decision = evaluate_policy(policy, client_code)
    return {
        "platform": platform,
        "client_version_code": client_code,
        "client_version_name": client_name,
        "update_required": decision["update_required"],
        "update_optional": decision["update_optional"],
        "is_enabled": bool(policy.is_enabled) if policy else False,
        "min_version_code": policy.min_version_code if policy else 0,
        "min_version_name": policy.min_version_name if policy else "",
        "latest_version_code": policy.latest_version_code if policy else 0,
        "latest_version_name": policy.latest_version_name if policy else "",
        "update_url": policy.update_url if policy else "",
    }


def request_client_version(request) -> tuple[str | None, int | None, str]:
    """Read platform/build/version from headers, falling back to query params."""
    platform = normalize_platform(
        request.headers.get("X-App-Platform") or request.GET.get("platform")
    )
    raw_code = request.headers.get("X-App-Build")
    if raw_code in (None, ""):
        raw_code = request.GET.get("version_code")
    raw_name = request.headers.get("X-App-Version") or request.GET.get("version_name") or ""
    client_name = raw_name.strip()[:50] if isinstance(raw_name, str) else ""
    return platform, parse_version_code(raw_code), client_name


def version_block_response(request):
    """Return a 426 response when the caller's build is force-outdated.

    Returns ``None`` when versioning is disabled, the platform/build is
    unknown, or the build satisfies the policy. Missing version headers are
    allowed through so builds predating this feature keep working until they
    upgrade to a version-reporting client.
    """
    platform, client_code, _ = request_client_version(request)
    if platform is None or client_code is None:
        return None
    policy = get_policy(platform)
    if policy is None or not policy.is_enabled:
        return None
    if client_code >= policy.min_version_code:
        return None
    return json_api_response(
        request,
        {
            "error": {
                "code": "app_update_required",
                "message": "A new version of the app is required.",
                "details": {
                    "platform": platform,
                    "min_version_code": policy.min_version_code,
                    "min_version_name": policy.min_version_name,
                    "latest_version_code": policy.latest_version_code,
                    "latest_version_name": policy.latest_version_name,
                    "update_url": policy.update_url,
                },
            }
        },
        status=426,
    )


def enforce_app_version(view):
    """Refuse force-outdated builds with 426 before the view runs."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        blocked = version_block_response(request)
        if blocked is not None:
            return blocked
        return view(request, *args, **kwargs)

    return wrapped
