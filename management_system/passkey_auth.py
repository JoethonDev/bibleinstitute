"""Browser WebAuthn passkey registration and authentication helpers."""

from __future__ import annotations

import hashlib
import json
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .models import User, WebAuthnCredential

CHALLENGE_CACHE_PREFIX = "pwa:webauthn:challenge:"
FRESH_AUTH_SESSION_KEY = "pwa_fresh_auth_at"
FRESH_AUTH_SECONDS = 300
ALLOWED_TRANSPORTS = frozenset({"usb", "nfc", "ble", "internal", "hybrid"})
MAX_CREDENTIALS = 10


class PasskeyError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def webauthn_settings() -> tuple[str, str, str]:
    return (
        settings.WEBAUTHN_RP_ID,
        settings.WEBAUTHN_RP_NAME,
        settings.WEBAUTHN_EXPECTED_ORIGIN,
    )


def user_handle(user: User) -> bytes:
    return hashlib.sha256(
        f"pwa-webauthn:{user.pk}:{settings.SECRET_KEY}".encode("utf-8")
    ).digest()


def mark_fresh_auth(request) -> None:
    request.session[FRESH_AUTH_SESSION_KEY] = timezone.now().timestamp()


def has_fresh_auth(request) -> bool:
    value = request.session.get(FRESH_AUTH_SESSION_KEY)
    try:
        issued_at = float(value)
    except (TypeError, ValueError):
        return False
    return (timezone.now().timestamp() - issued_at) <= FRESH_AUTH_SECONDS


def _challenge_key(challenge_id: str) -> str:
    return f"{CHALLENGE_CACHE_PREFIX}{challenge_id}"


def _store_challenge(kind: str, challenge: bytes, *, user_id: int | None = None) -> str:
    challenge_id = secrets.token_urlsafe(32)
    cache.set(
        _challenge_key(challenge_id),
        {
            "kind": kind,
            "user_id": user_id,
            "challenge": bytes_to_base64url(challenge),
        },
        timeout=settings.WEBAUTHN_CHALLENGE_TTL_SECONDS,
    )
    return challenge_id


def _take_challenge(challenge_id: str, kind: str) -> dict:
    if not isinstance(challenge_id, str) or len(challenge_id) > 128:
        raise PasskeyError("invalid_challenge", _("The passkey challenge is invalid."))
    key = _challenge_key(challenge_id)
    payload = cache.get(key)
    cache.delete(key)
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        raise PasskeyError("expired_challenge", _("The passkey challenge has expired."))
    return payload


def _rp_and_origin() -> tuple[str, str, str]:
    return webauthn_settings()


def _transports(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            transport
            for transport in value
            if isinstance(transport, str) and transport in ALLOWED_TRANSPORTS
        }
    )


def begin_registration(user: User) -> dict:
    if not settings.WEBAUTHN_ENABLED:
        raise PasskeyError("disabled", _("Passkeys are not enabled."), 404)
    if WebAuthnCredential.objects.filter(user=user, is_active=True).count() >= MAX_CREDENTIALS:
        raise PasskeyError(
            "credential_limit",
            _("This account already has the maximum number of passkeys."),
            409,
        )
    rp_id, rp_name, _ = _rp_and_origin()
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id))
        for credential_id in WebAuthnCredential.objects.filter(
            user=user, is_active=True
        ).values_list("credential_id", flat=True)
    ]
    display_name = user.get_full_name() or user.username
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=user.username,
        user_id=user_handle(user),
        user_display_name=display_name,
        challenge=secrets.token_bytes(32),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=existing or None,
    )
    challenge_id = _store_challenge(
        "registration",
        options.challenge,
        user_id=user.pk,
    )
    return {
        "challenge_id": challenge_id,
        "publicKey": json.loads(options_to_json(options)),
    }


def finish_registration(
    user: User,
    *,
    challenge_id: str,
    credential: dict,
    label: str,
) -> WebAuthnCredential:
    if not settings.WEBAUTHN_ENABLED:
        raise PasskeyError("disabled", _("Passkeys are not enabled."), 404)
    payload = _take_challenge(challenge_id, "registration")
    if payload.get("user_id") != user.pk:
        raise PasskeyError("invalid_challenge", _("The passkey challenge is invalid."))
    label = (label or "").strip()[:80]
    if not label:
        raise PasskeyError("invalid_label", _("Enter a name for this passkey."))
    if not isinstance(credential, dict):
        raise PasskeyError("invalid_credential", _("The passkey response is invalid."))
    rp_id, _, origin = _rp_and_origin()
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(payload["challenge"]),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError(
            "verification_failed",
            _("The passkey could not be verified."),
        ) from exc
    transports = _transports(
        (credential.get("response") or {}).get("transports")
        if isinstance(credential.get("response"), dict)
        else None
    )
    try:
        with transaction.atomic():
            return WebAuthnCredential.objects.create(
                user=user,
                credential_id=bytes_to_base64url(verified.credential_id),
                public_key=bytes_to_base64url(verified.credential_public_key),
                sign_count=verified.sign_count,
                transports=transports,
                label=label,
                aaguid=str(verified.aaguid or "")[:36],
                backup_eligible=bool(verified.credential_backed_up),
                backup_state=bool(verified.credential_backed_up),
            )
    except IntegrityError as exc:
        raise PasskeyError(
            "duplicate_credential",
            _("This passkey is already registered."),
            409,
        ) from exc


def begin_authentication() -> dict:
    if not settings.WEBAUTHN_ENABLED:
        raise PasskeyError("disabled", _("Passkeys are not enabled."), 404)
    rp_id, _, _ = _rp_and_origin()
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=secrets.token_bytes(32),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = _store_challenge("authentication", options.challenge)
    return {
        "challenge_id": challenge_id,
        "publicKey": json.loads(options_to_json(options)),
    }


def finish_authentication(*, challenge_id: str, credential: dict) -> User:
    if not settings.WEBAUTHN_ENABLED:
        raise PasskeyError("disabled", _("Passkeys are not enabled."), 404)
    payload = _take_challenge(challenge_id, "authentication")
    if not isinstance(credential, dict):
        raise PasskeyError("invalid_credential", _("The passkey response is invalid."))
    credential_id = credential.get("id") or credential.get("rawId")
    if not isinstance(credential_id, str) or not credential_id:
        raise PasskeyError("invalid_credential", _("The passkey response is invalid."))
    try:
        stored = WebAuthnCredential.objects.select_related("user").get(
            credential_id=credential_id[:1024],
            is_active=True,
        )
    except WebAuthnCredential.DoesNotExist as exc:
        raise PasskeyError(
            "unknown_credential",
            _("That passkey is not registered."),
            401,
        ) from exc
    user = stored.user
    if not user.is_active:
        raise PasskeyError("inactive_account", _("This account is not active."), 403)
    rp_id, _, origin = _rp_and_origin()
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(payload["challenge"]),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(stored.public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError(
            "verification_failed",
            _("The passkey could not be verified."),
            401,
        ) from exc
    if verified.new_sign_count and verified.new_sign_count < stored.sign_count:
        raise PasskeyError(
            "counter_regression",
            _("The passkey response was rejected."),
            401,
        )
    stored.sign_count = verified.new_sign_count
    stored.last_used_at = timezone.now()
    stored.backup_eligible = bool(verified.credential_backed_up)
    stored.backup_state = bool(verified.credential_backed_up)
    stored.save(
        update_fields=[
            "sign_count",
            "last_used_at",
            "backup_eligible",
            "backup_state",
            "updated_at",
        ]
    )
    return user


def revoke_credential(user: User, credential_id: str) -> bool:
    if not isinstance(credential_id, str) or not credential_id:
        raise PasskeyError("invalid_credential", _("The passkey is invalid."))
    updated = WebAuthnCredential.objects.filter(
        user=user,
        credential_id=credential_id[:1024],
        is_active=True,
    ).update(is_active=False, updated_at=timezone.now())
    return bool(updated)


def default_redirect(user: User) -> str:
    if getattr(getattr(user, "role", None), "role", None) == "student":
        return reverse("courses")
    return reverse("admin-panel")
