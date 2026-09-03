"""Bearer authentication for the mobile API."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from functools import wraps

from django.core.cache import cache
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from .models import MobileBiometricCredential, MobilePushDevice, StudentMobileSession, User
from .utils.localization import normalize_language


MOBILE_SESSION_LIFETIME = timedelta(days=30)
LOGIN_RATE_WINDOW = 15 * 60
LOGIN_USERNAME_LIMIT = 10
LOGIN_IP_LIMIT = 30
BIOMETRIC_RATE_WINDOW = 15 * 60
BIOMETRIC_INSTALLATION_LIMIT = 10
BIOMETRIC_IP_LIMIT = 30


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


def normalize_mobile_installation_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        return None
    return normalized


def _biometric_digest(credential: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        credential.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _biometric_rate_key(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"mobile-biometric:{kind}:{digest}"


def mobile_biometric_rate_limited(installation_id: str, remote_addr: str) -> bool:
    installation_count = cache.get(_biometric_rate_key("installation", installation_id), 0)
    ip_count = cache.get(_biometric_rate_key("ip", remote_addr or "unknown"), 0)
    return installation_count >= BIOMETRIC_INSTALLATION_LIMIT or ip_count >= BIOMETRIC_IP_LIMIT


def record_mobile_biometric_failure(installation_id: str, remote_addr: str) -> None:
    for kind, value in (
        ("installation", installation_id),
        ("ip", remote_addr or "unknown"),
    ):
        key = _biometric_rate_key(kind, value)
        if cache.add(key, 1, timeout=BIOMETRIC_RATE_WINDOW):
            continue
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=BIOMETRIC_RATE_WINDOW)


def clear_mobile_biometric_failures(installation_id: str, remote_addr: str) -> None:
    cache.delete(_biometric_rate_key("installation", installation_id))
    cache.delete(_biometric_rate_key("ip", remote_addr or "unknown"))


class MobileBiometricError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def enroll_mobile_biometric(user, installation_id: str) -> str:
    raw_credential = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        MobileBiometricCredential.objects.select_for_update().update_or_create(
            installation_id=installation_id,
            defaults={
                "user": user,
                "credential_digest": _biometric_digest(raw_credential),
                "updated_at": now,
                "last_used_at": None,
                "revoked_at": None,
            },
        )
    return raw_credential


def unlock_mobile_biometric(
    installation_id: str,
    raw_credential: object,
    remote_addr: str,
):
    if mobile_biometric_rate_limited(installation_id, remote_addr):
        raise MobileBiometricError(
            "biometric_rate_limited",
            _("Too many biometric attempts. Try again later."),
            status=429,
        )
    if (
        not isinstance(raw_credential, str)
        or len(raw_credential) < 20
        or len(raw_credential) > 256
    ):
        record_mobile_biometric_failure(installation_id, remote_addr)
        raise MobileBiometricError("biometric_invalid", _("Biometric unlock is unavailable."), status=401)
    try:
        digest = _biometric_digest(raw_credential)
    except UnicodeEncodeError:
        record_mobile_biometric_failure(installation_id, remote_addr)
        raise MobileBiometricError("biometric_invalid", _("Biometric unlock is unavailable."), status=401)

    now = timezone.now()
    with transaction.atomic():
        # Keep the lock query free of the nullable User.role outer join.
        credential = (
            MobileBiometricCredential.objects.select_for_update()
            .filter(
                installation_id=installation_id,
                credential_digest=digest,
                revoked_at__isnull=True,
            )
            .first()
        )
        if credential is None:
            record_mobile_biometric_failure(installation_id, remote_addr)
            raise MobileBiometricError("biometric_invalid", _("Biometric unlock is unavailable."), status=401)
        user = User.objects.select_related("role").get(pk=credential.user_id)
        if not user.is_active or user.application_status != "active":
            credential.revoked_at = now
            credential.save(update_fields=["revoked_at", "updated_at"])
            record_mobile_biometric_failure(installation_id, remote_addr)
            raise MobileBiometricError("biometric_revoked", _("Biometric unlock is unavailable."), status=401)
        token, session = issue_mobile_session(user)
        credential.last_used_at = now
        credential.save(update_fields=["last_used_at", "updated_at"])
    clear_mobile_biometric_failures(installation_id, remote_addr)
    return token, session, user


def revoke_mobile_biometric(user, installation_id: str) -> int:
    now = timezone.now()
    with transaction.atomic():
        credential = (
            MobileBiometricCredential.objects.select_for_update()
            .filter(installation_id=installation_id)
            .first()
        )
        if credential is None:
            return 0
        if credential.user_id != user.pk:
            raise MobileBiometricError(
                "biometric_conflict",
                _("This device installation belongs to another account."),
                status=409,
            )
        if credential.revoked_at is None:
            credential.revoked_at = now
            credential.save(update_fields=["revoked_at", "updated_at"])
            return 1
    return 0


def revoke_other_account_biometric(user, installation_id: str | None) -> None:
    if not installation_id:
        return
    now = timezone.now()
    with transaction.atomic():
        credential = (
            MobileBiometricCredential.objects.select_for_update()
            .filter(installation_id=installation_id)
            .first()
        )
        if credential and credential.user_id != user.pk and credential.revoked_at is None:
            credential.revoked_at = now
            credential.save(update_fields=["revoked_at", "updated_at"])


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
    MobileBiometricCredential.objects.filter(
        user=user,
        revoked_at__isnull=True,
    ).update(revoked_at=now, updated_at=now)


def revoke_users_mobile_access(user_ids) -> None:
    """Revoke mobile access for a bounded set of users in bulk."""
    user_ids = list(user_ids)
    if not user_ids:
        return
    revoked_at = timezone.now()
    StudentMobileSession.objects.filter(
        user_id__in=user_ids,
        revoked_at__isnull=True,
    ).update(revoked_at=revoked_at)
    MobilePushDevice.objects.filter(user_id__in=user_ids, is_active=True).update(
        is_active=False,
        disabled_at=revoked_at,
        updated_at=revoked_at,
    )
    MobileBiometricCredential.objects.filter(
        user_id__in=user_ids,
        revoked_at__isnull=True,
    ).update(revoked_at=revoked_at, updated_at=revoked_at)


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
