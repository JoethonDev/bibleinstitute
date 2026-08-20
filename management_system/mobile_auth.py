"""Bearer authentication for the mobile API."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .models import MobilePushDevice, StudentMobileSession
from .utils.localization import normalize_language


MOBILE_SESSION_LIFETIME = timedelta(days=30)
LOGIN_RATE_WINDOW = 15 * 60
LOGIN_USERNAME_LIMIT = 10
LOGIN_IP_LIMIT = 30


def _login_rate_key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"mobile-login:{kind}:{digest}"


def mobile_login_rate_limited(username: str, remote_addr: str) -> bool:
    username_count = cache.get(_login_rate_key("username", username), 0)
    ip_count = cache.get(_login_rate_key("ip", remote_addr or "unknown"), 0)
    return username_count >= LOGIN_USERNAME_LIMIT or ip_count >= LOGIN_IP_LIMIT


def record_mobile_login_failure(username: str, remote_addr: str) -> None:
    for kind, value in (("username", username), ("ip", remote_addr or "unknown")):
        if not value:
            continue
        key = _login_rate_key(kind, value)
        if cache.add(key, 1, timeout=LOGIN_RATE_WINDOW):
            continue
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=LOGIN_RATE_WINDOW)


def clear_mobile_login_failures(username: str, remote_addr: str) -> None:
    if username:
        cache.delete(_login_rate_key("username", username))
    cache.delete(_login_rate_key("ip", remote_addr or "unknown"))


def json_api_response(request, payload: dict, status: int = 200) -> JsonResponse:
    body = dict(payload)
    body.setdefault("language", normalize_language(request))
    return JsonResponse(body, status=status)


def mobile_installation_conflict(user, installation_id: str | None) -> bool:
    return bool(
        isinstance(installation_id, str)
        and installation_id
        and MobilePushDevice.objects.filter(installation_id=installation_id)
        .exclude(user=user)
        .exists()
    )


def issue_mobile_session(user):
    raw_token = secrets.token_urlsafe(32)
    session = StudentMobileSession.objects.create(
        user=user,
        token_digest=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=timezone.now() + MOBILE_SESSION_LIFETIME,
    )
    return raw_token, session


def revoke_mobile_session(request) -> bool:
    session = get_mobile_session(request)
    if session is None:
        return False
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])
    installation_id = request.headers.get("X-Installation-ID")
    if installation_id:
        MobilePushDevice.objects.filter(
            user=session.user,
            installation_id=installation_id,
            is_active=True,
        ).update(
            is_active=False,
            disabled_at=timezone.now(),
            updated_at=timezone.now(),
        )
    return True


def revoke_user_mobile_access(user) -> None:
    now = timezone.now()
    StudentMobileSession.objects.filter(
        user=user,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    MobilePushDevice.objects.filter(user=user, is_active=True).update(
        is_active=False,
        disabled_at=now,
        updated_at=now,
    )


def get_mobile_session(request):
    authorization = request.headers.get("Authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token or " " in raw_token:
        return None
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    session = StudentMobileSession.objects.select_related("user", "user__role").filter(
        token_digest=digest,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if session is None:
        return None
    user = session.user
    if not user.is_active or user.application_status != "active":
        return None
    session.last_used_at = timezone.now()
    session.save(update_fields=["last_used_at"])
    return session


def require_mobile_session(view):
    """Require bearer authentication and install the session on the request."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        language = normalize_language(request)
        with translation.override(language):
            session = get_mobile_session(request)
            if session is None:
                return json_api_response(
                    request,
                    {"error": {"code": "authentication_required", "message": _("Authentication required.")}},
                    status=401,
                )
            request.mobile_session = session
            request.user = session.user
            response = view(request, *args, **kwargs)
            return response

    return wrapped
