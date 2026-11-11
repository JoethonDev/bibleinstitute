"""Installable student PWA, offline-library, Web Push and passkey HTTP adapters."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .academic_access import get_accessible_offering_or_403
from .media_delivery import (
    MediaAccessError,
    open_audio_download,
    open_book_document,
    resolve_media_link,
)
from .mobile_downloads import MobileDownloadPolicy
from .storage_clients import CLOUD_CLIENT, bucket_name
from .models import (
    Lesson,
    PublicationStatus,
    StudentNotification,
    WebPushDelivery,
    WebPushSubscription,
    WebAuthnCredential,
)
from .passkey_auth import (
    PasskeyError,
    begin_authentication,
    begin_registration,
    default_redirect,
    finish_authentication,
    finish_registration,
    has_fresh_auth,
    mark_fresh_auth,
    revoke_credential,
)
from .utils.lesson_access import lesson_is_available

logger = logging.getLogger(__name__)
STUDENT_ROLE = "student"


def _is_student(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and getattr(getattr(user, "role", None), "role", None) == STUDENT_ROLE
    )


def _json_error(message: str, status: int = 400, code: str = "invalid_request"):
    return JsonResponse(
        {"ok": False, "error": {"code": code, "message": str(message)}},
        status=status,
    )


def _json_body(request) -> dict:
    if len(request.body) > 65536:
        raise ValueError("Request body is too large.")
    try:
        value = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError("Request body must be JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object.")
    return value


def _rate_limited(key: str, limit: int, window: int) -> bool:
    cache_key = f"pwa:rate:{key}"
    if cache.add(cache_key, 1, timeout=window):
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window)
        return False
    return count > limit


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR") or "unknown"


def _safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._")
    return (cleaned or fallback)[:120]


def _content_version(media) -> str:
    stamp = getattr(media.lesson, "updated_date", None) or getattr(
        media.lesson, "created_date", None
    )
    stamp_value = stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp or "")
    digest = hashlib.sha256(
        f"{media.media_key}:{stamp_value}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{media.file_index}-{digest}"


def pwa_manifest(request):
    requested = request.GET.get("lang")
    language = (
        requested
        if requested in {"ar", "en"}
        else (getattr(request, "LANGUAGE_CODE", None) or "ar").split("-")[0]
    )
    with translation.override(language):
        name = str(_("Bible Institute Student"))
        short_name = str(_("Bible Institute"))
        description = str(_("Lessons, quizzes and institute updates."))
        downloaded_media = str(_("Downloaded media"))
        notifications = str(_("Notifications"))
    icons = [
        {
            "src": static("pwa/icons/icon-192.png"),
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": static("pwa/icons/icon-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": static("pwa/icons/icon-maskable-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]
    return JsonResponse(
        {
            "name": name,
            "short_name": short_name,
            "description": description,
            "lang": language,
            "dir": "rtl" if language == "ar" else "ltr",
            "id": "/",
            "start_url": f"/{language}/courses/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#f6f1e6",
            "theme_color": "#1f4f56",
            "icons": icons,
            "shortcuts": [
                {
                    "name": downloaded_media,
                    "url": f"/{language}/offline-library/",
                },
                {
                    "name": notifications,
                    "url": f"/{language}/notifications/",
                },
            ],
        },
        json_dumps_params={"ensure_ascii": False},
        content_type="application/manifest+json",
    )


def pwa_service_worker(request):
    response = render(
        request,
        "pwa/service_worker.js",
        {
            "pwa_version": settings.STATIC_ASSET_VERSION,
            "offline_shell_ar": "/ar/pwa/offline-shell/",
            "offline_shell_en": "/en/pwa/offline-shell/",
            "offline_fallback": static("pwa/offline.html"),
        },
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@require_GET
def pwa_offline_shell(request):
    """Public, data-free shell cached by the service worker for offline use."""
    return render(request, "pwa/offline_library.html")


@login_required
@require_GET
@ensure_csrf_cookie
def offline_library(request):
    if not _is_student(request.user):
        raise PermissionDenied
    return render(
        request,
        "pwa/offline_library.html",
        {
            "offline_account_id": request.user.pk,
            "offline_max_bytes": settings.PWA_MEDIA_MAX_BYTES,
            "web_push_enabled": settings.WEB_PUSH_ENABLED,
            "webauthn_enabled": settings.WEBAUTHN_ENABLED,
        },
    )


@login_required
@require_POST
@csrf_protect
def pwa_save_media(request, offering_id: int, lesson_id: int, file_index: int):
    if not _is_student(request.user):
        return _json_error(_("Only student accounts can save offline media."), 403, "forbidden")
    policy = MobileDownloadPolicy.load()
    if not policy.in_app_download_enabled:
        return _json_error(
            _("Saving offline media is disabled."),
            403,
            "download_disabled",
        )
    try:
        media = resolve_media_link(
            user=request.user,
            offering_id=offering_id,
            lesson_id=lesson_id,
            file_index=file_index,
            allowed_types=("audio", "book"),
        )
    except MediaAccessError as exc:
        status = 404 if exc.code == "not_found" else 403 if exc.code == "forbidden" else 400
        return _json_error(exc.message, status, exc.code)
    if media.file_type == "audio":
        try:
            stream = open_audio_download(
                media=media,
                cloud_client=CLOUD_CLIENT,
                bucket_name=bucket_name,
            )
        except MediaAccessError as exc:
            return _json_error(exc.message, 503, exc.code)
        content_type = stream.content_type
        content_length = stream.content_length
        filename = stream.filename
        body = stream.body
        descriptor = _("Audio")
    else:
        try:
            stream = open_book_document(
                media=media,
                range_header="",
                cloud_client=CLOUD_CLIENT,
                bucket_name=bucket_name,
            )
        except MediaAccessError as exc:
            return _json_error(exc.message, 503, exc.code)
        content_type = "application/pdf"
        content_length = stream.content_length
        filename = f"{_safe_filename(media.lesson.name, 'lesson')}.pdf"
        body = stream.body
        descriptor = _("PDF")
    if (
        settings.PWA_MEDIA_MAX_BYTES
        and content_length
        and content_length > settings.PWA_MEDIA_MAX_BYTES
    ):
        body.close()
        return _json_error(
            _("This file is too large to save offline."),
            413,
            "file_too_large",
        )
    response = FileResponse(body, as_attachment=True, filename=filename)
    response["Content-Type"] = content_type
    if content_length:
        response["Content-Length"] = str(content_length)
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-PWA-Media-Type"] = media.file_type
    response["X-PWA-Media-Label"] = descriptor.encode("ascii", "ignore").decode() or "media"
    response["X-PWA-Content-Version"] = _content_version(media)
    return response


@login_required
@require_POST
@csrf_protect
def pwa_validate_offline(request):
    if not _is_student(request.user):
        return _json_error(_("Only student accounts can use offline media."), 403, "forbidden")
    try:
        payload = _json_body(request)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    items = payload.get("items")
    if not isinstance(items, list):
        return _json_error(_("Items must be a list."), 400)
    allowed = []
    seen = set()
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        try:
            offering_id = int(item.get("offering_id"))
            lesson_id = int(item.get("lesson_id"))
        except (TypeError, ValueError):
            continue
        key = (offering_id, lesson_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            offering = get_accessible_offering_or_403(
                request.user,
                offering_id,
            )
        except PermissionDenied:
            continue
        lesson = Lesson.objects.filter(
            pk=lesson_id,
            course_offering=offering,
            status=PublicationStatus.PUBLISHED,
        ).first()
        if lesson is None or not lesson_is_available(lesson):
            continue
        allowed.append({"offering_id": offering_id, "lesson_id": lesson_id})
    return JsonResponse({"ok": True, "allowed": allowed})


def _subscription_payload(value) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise ValueError("Subscription must be an object.")
    endpoint = value.get("endpoint")
    keys = value.get("keys")
    if not isinstance(endpoint, str) or not isinstance(keys, dict):
        raise ValueError("Subscription is incomplete.")
    endpoint = endpoint.strip()
    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Subscription endpoint is invalid.")
    try:
        ip_value = ipaddress.ip_address(host)
    except ValueError:
        ip_value = None
    if ip_value is not None and (
        ip_value.is_private or ip_value.is_loopback or ip_value.is_link_local
    ):
        raise ValueError("Subscription endpoint is invalid.")
    if (
        parsed.scheme != "https"
        or not host
        or len(endpoint) > 2048
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Subscription endpoint is invalid.")
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    for name, candidate in (("p256dh", p256dh), ("auth", auth)):
        if (
            not isinstance(candidate, str)
            or len(candidate) > 255
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,255}", candidate)
        ):
            raise ValueError(f"Subscription {name} key is invalid.")
    return endpoint, p256dh, auth


@login_required
@require_POST
@csrf_protect
def web_push_subscribe(request):
    if not settings.WEB_PUSH_ENABLED:
        return _json_error(_("Web push is not enabled."), 404, "disabled")
    if not _is_student(request.user):
        return _json_error(_("Web push is available to student accounts."), 403, "forbidden")
    if _rate_limited(f"push-subscribe:{request.user.pk}", 10, 300):
        return _json_error(_("Too many subscription attempts."), 429, "rate_limited")
    try:
        payload = _json_body(request)
        endpoint, p256dh, auth = _subscription_payload(payload.get("subscription"))
    except ValueError as exc:
        return _json_error(str(exc), 400)
    language = payload.get("language")
    if language not in {"ar", "en"}:
        language = "ar" if getattr(request, "LANGUAGE_CODE", "ar").startswith("ar") else "en"
    endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        WebPushSubscription.objects.select_for_update().filter(
            endpoint_hash=endpoint_hash,
            is_active=True,
        ).exclude(user=request.user).update(
            is_active=False,
            disabled_at=now,
            updated_at=now,
        )
        subscription, _ = WebPushSubscription.objects.update_or_create(
            user=request.user,
            endpoint_hash=endpoint_hash,
            defaults={
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "language": language,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
                "is_active": True,
                "disabled_at": None,
                "enabled_at": now,
            },
        )
        # No backfill: a student who enables notifications must not receive
        # the historical inbox as a burst of push notifications.
    return JsonResponse({"ok": True, "active": True, "language": subscription.language})


@login_required
@require_POST
@csrf_protect
def web_push_unsubscribe(request):
    if not _is_student(request.user):
        return _json_error(_("Web push is available to student accounts."), 403, "forbidden")
    now = timezone.now()
    with transaction.atomic():
        subscriptions = list(
            WebPushSubscription.objects.select_for_update().filter(
                user=request.user,
                is_active=True,
            )
        )
        if subscriptions:
            WebPushSubscription.objects.filter(
                pk__in=[subscription.pk for subscription in subscriptions]
            ).update(is_active=False, disabled_at=now, updated_at=now)
            WebPushDelivery.objects.filter(
                subscription__in=subscriptions,
                status=WebPushDelivery.Status.QUEUED,
            ).update(
                status=WebPushDelivery.Status.SKIPPED,
                error_code="unsubscribed",
                next_attempt_at=now,
                updated_at=now,
            )
    return JsonResponse({"ok": True, "active": False})


@login_required
@require_GET
def web_push_status(request):
    if not _is_student(request.user):
        return _json_error(_("Web push is available to student accounts."), 403, "forbidden")
    subscription = (
        WebPushSubscription.objects.filter(user=request.user, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    return JsonResponse(
        {
            "ok": True,
            "enabled": bool(settings.WEB_PUSH_ENABLED),
            "active": bool(subscription),
            "public_key": settings.WEB_PUSH_VAPID_PUBLIC_KEY if settings.WEB_PUSH_ENABLED else "",
            "language": subscription.language if subscription else None,
        }
    )


@require_POST
@csrf_protect
def passkey_login_options(request):
    if not settings.WEBAUTHN_ENABLED:
        return _json_error(_("Passkeys are not enabled."), 404, "disabled")
    if _rate_limited(f"passkey-options:{_client_ip(request)}", 60, 900):
        return _json_error(_("Too many passkey attempts."), 429, "rate_limited")
    try:
        return JsonResponse({"ok": True, **begin_authentication()})
    except PasskeyError as exc:
        return _json_error(exc.message, exc.status, exc.code)


@require_POST
@csrf_protect
def passkey_login_verify(request):
    if not settings.WEBAUTHN_ENABLED:
        return _json_error(_("Passkeys are not enabled."), 404, "disabled")
    if _rate_limited(f"passkey-login:{_client_ip(request)}", 20, 900):
        return _json_error(_("Too many passkey attempts."), 429, "rate_limited")
    try:
        payload = _json_body(request)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    try:
        user = finish_authentication(
            challenge_id=payload.get("challenge_id"),
            credential=payload.get("credential"),
        )
    except PasskeyError as exc:
        return _json_error(exc.message, exc.status, exc.code)
    if not user.is_active:
        return _json_error(_("This account is not active."), 403, "inactive_account")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    mark_fresh_auth(request)
    next_url = payload.get("next")
    redirect_to = (
        next_url
        if isinstance(next_url, str)
        and url_has_allowed_host_and_scheme(
            next_url, {request.get_host()}, require_https=request.is_secure()
        )
        else default_redirect(user)
    )
    return JsonResponse({"ok": True, "redirect": redirect_to})


@login_required
@require_POST
@csrf_protect
def passkey_register_options(request):
    if not settings.WEBAUTHN_ENABLED:
        return _json_error(_("Passkeys are not enabled."), 404, "disabled")
    if not has_fresh_auth(request):
        return _json_error(
            _("Sign in again before adding a passkey."),
            403,
            "reauth_required",
        )
    try:
        return JsonResponse({"ok": True, **begin_registration(request.user)})
    except PasskeyError as exc:
        return _json_error(exc.message, exc.status, exc.code)


@login_required
@require_POST
@csrf_protect
def passkey_register_verify(request):
    if not settings.WEBAUTHN_ENABLED:
        return _json_error(_("Passkeys are not enabled."), 404, "disabled")
    if not has_fresh_auth(request):
        return _json_error(
            _("Sign in again before adding a passkey."),
            403,
            "reauth_required",
        )
    try:
        payload = _json_body(request)
        credential = finish_registration(
            request.user,
            challenge_id=payload.get("challenge_id"),
            credential=payload.get("credential"),
            label=payload.get("label", ""),
        )
    except PasskeyError as exc:
        return _json_error(exc.message, exc.status, exc.code)
    return JsonResponse({"ok": True, "credential": {"id": credential.pk, "label": credential.label}})


@login_required
@require_POST
@csrf_protect
def passkey_revoke(request):
    if not settings.WEBAUTHN_ENABLED:
        return _json_error(_("Passkeys are not enabled."), 404, "disabled")
    if not has_fresh_auth(request):
        return _json_error(
            _("Sign in again before removing a passkey."),
            403,
            "reauth_required",
        )
    try:
        payload = _json_body(request)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    try:
        changed = revoke_credential(request.user, payload.get("credential_id"))
    except PasskeyError as exc:
        return _json_error(exc.message, exc.status, exc.code)
    if not changed:
        return _json_error(_("That passkey was not found."), 404, "not_found")
    return JsonResponse({"ok": True})


class PwaLoginView(LoginView):
    """Password login that also records a fresh-auth window for passkey changes."""

    def form_valid(self, form):
        response = super().form_valid(form)
        mark_fresh_auth(self.request)
        return response


class PwaLogoutView(LogoutView):
    """Logout also deactivates browser push subscriptions for the account."""

    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            now = timezone.now()
            subscriptions = WebPushSubscription.objects.filter(
                user=request.user,
                is_active=True,
            )
            subscription_ids = list(subscriptions.values_list("pk", flat=True))
            if subscription_ids:
                subscriptions.update(
                    is_active=False,
                    disabled_at=now,
                    updated_at=now,
                )
                WebPushDelivery.objects.filter(
                    subscription_id__in=subscription_ids,
                    status=WebPushDelivery.Status.QUEUED,
                ).update(
                    status=WebPushDelivery.Status.SKIPPED,
                    error_code="logout",
                    next_attempt_at=now,
                    updated_at=now,
                )
        return super().post(request, *args, **kwargs)
