"""Telegram-delivered mobile OTP challenge boundary."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import MobileOtpChallenge, TelegramAccount, TelegramBotConfig, User
from .mobile_auth import mobile_installation_conflict
from .mobile_otp_tasks import send_mobile_otp
from .telegram.configuration import decrypt_secret, encrypt_secret
from .telegram.linking import current_telegram_link
from .utils.validators import normalize_phone


OTP_TTL = timedelta(minutes=5)
OTP_RESEND_INTERVAL = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
OTP_RATE_WINDOW = 15 * 60
OTP_PHONE_LIMIT = 5
OTP_IP_LIMIT = 20


def _otp_digest(otp: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        otp.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


class MobileOtpError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        retry_after: int | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
        self.details = details


def _cache_key(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"mobile-otp:{prefix}:{digest}"


def _check_rate_limit(prefix: str, value: str, limit: int) -> None:
    key = _cache_key(prefix, value)
    if cache.add(key, 1, timeout=OTP_RATE_WINDOW):
        return
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=OTP_RATE_WINDOW)
        return
    if count > limit:
        raise MobileOtpError(
            "otp_rate_limited",
            _("Too many OTP requests. Try again later."),
            status=429,
            retry_after=OTP_RATE_WINDOW,
        )


def _eligible_mobile_user(phone: str) -> tuple[User, TelegramAccount]:
    try:
        user = User.objects.select_related("role").get(phone=phone)
    except User.DoesNotExist:
        raise MobileOtpError("otp_unavailable", _("OTP login is unavailable for this number."))
    if not user.is_active or user.application_status != "active":
        raise MobileOtpError("otp_unavailable", _("OTP login is unavailable for this number."))
    account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    if account is None:
        try:
            _account, link_url = current_telegram_link(user)
        except Exception:
            link_url = None
        raise MobileOtpError(
            "telegram_link_required",
            _("Link your Telegram account before using OTP login."),
            details={"link_url": link_url} if link_url else None,
        )
    return user, account


def _active_bot_token() -> str:
    config = TelegramBotConfig.objects.filter(is_active=True).only("token_ciphertext").first()
    if not config or not config.token_ciphertext:
        raise MobileOtpError(
            "otp_delivery_unavailable",
            _("OTP delivery is temporarily unavailable."),
            status=503,
        )
    try:
        return decrypt_secret(config.token_ciphertext)
    except Exception as exc:
        raise MobileOtpError(
            "otp_delivery_unavailable",
            _("OTP delivery is temporarily unavailable."),
            status=503,
        ) from exc


def request_mobile_otp(phone: str, installation_id: str, remote_addr: str) -> MobileOtpChallenge:
    """Create one bounded OTP challenge and queue its Telegram delivery."""
    try:
        normalized_phone = normalize_phone(phone)
    except (TypeError, ValueError):
        raise MobileOtpError("invalid_phone", _("Enter a valid phone number."))
    if not normalized_phone:
        raise MobileOtpError("invalid_phone", _("Enter a valid phone number."))
    installation_id = installation_id.strip() if isinstance(installation_id, str) else ""
    if not installation_id:
        raise MobileOtpError("installation_required", _("A device installation ID is required."))
    if len(installation_id) > 128:
        raise MobileOtpError("invalid_installation", _("The device installation ID is too long."))
    _check_rate_limit("phone", normalized_phone, OTP_PHONE_LIMIT)
    _check_rate_limit("ip", remote_addr or "unknown", OTP_IP_LIMIT)
    user, account = _eligible_mobile_user(normalized_phone)
    token = _active_bot_token()
    otp = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    with transaction.atomic():
        active = (
            MobileOtpChallenge.objects.select_for_update()
            .filter(
                student=user,
                purpose="login",
                installation_id=installation_id,
                status__in=[
                    MobileOtpChallenge.Status.QUEUED,
                    MobileOtpChallenge.Status.SENDING,
                    MobileOtpChallenge.Status.SENT,
                ],
            )
            .first()
        )
        if active and active.expires_at > now:
            if active.last_sent_at and now - active.last_sent_at < OTP_RESEND_INTERVAL:
                remaining = int((OTP_RESEND_INTERVAL - (now - active.last_sent_at)).total_seconds())
                raise MobileOtpError(
                    "otp_resend_too_soon",
                    _("Wait before requesting another OTP."),
                    status=429,
                    retry_after=max(1, remaining),
                )
            raise MobileOtpError(
                "otp_already_sent",
                _("An OTP has already been sent. Enter it or wait for it to expire."),
                status=429,
                retry_after=max(1, int((active.expires_at - now).total_seconds())),
            )
        if active:
            active.status = MobileOtpChallenge.Status.EXPIRED
            active.save(update_fields=["status"])
        challenge = MobileOtpChallenge.objects.create(
            student=user,
            installation_id=installation_id,
            otp_digest=_otp_digest(otp),
            purpose="login",
            status=MobileOtpChallenge.Status.QUEUED,
            expires_at=now + OTP_TTL,
        )
        encrypted_otp = encrypt_secret(otp)
        transaction.on_commit(
            lambda: send_mobile_otp.delay(str(challenge.challenge_id), encrypted_otp)
        )
    return challenge


def verify_mobile_otp(
    challenge_id: str,
    otp: str,
    installation_id: str,
    phone_number: str,
):
    """Consume a valid OTP and return the student used for session issuance."""
    now = timezone.now()
    if not isinstance(challenge_id, str) or not isinstance(otp, str):
        raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
    try:
        challenge_uuid = uuid.UUID(challenge_id)
    except (ValueError, TypeError, AttributeError):
        raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
    try:
        normalized_phone = normalize_phone(phone_number)
    except (TypeError, ValueError):
        raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
    if not normalized_phone:
        raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
    normalized_otp = otp.strip()
    if len(normalized_otp) != 6 or not normalized_otp.isascii() or not normalized_otp.isdigit():
        raise MobileOtpError("invalid_otp", _("Enter the six-digit OTP."))
    with transaction.atomic():
        try:
            challenge = MobileOtpChallenge.objects.select_for_update().get(
                challenge_id=challenge_uuid,
                purpose="login",
            )
            challenge = MobileOtpChallenge.objects.select_related(
                "student", "student__role"
            ).get(pk=challenge.pk)
        except MobileOtpChallenge.DoesNotExist:
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        normalized_installation_id = installation_id.strip() if isinstance(installation_id, str) else ""
        if not normalized_installation_id or len(normalized_installation_id) > 128:
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        if challenge.installation_id != normalized_installation_id:
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        if normalize_phone(challenge.student.phone or "") != normalized_phone:
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        if challenge.status != MobileOtpChallenge.Status.SENT or challenge.expires_at <= now:
            if challenge.expires_at <= now and challenge.status in {
                MobileOtpChallenge.Status.QUEUED,
                MobileOtpChallenge.Status.SENDING,
                MobileOtpChallenge.Status.SENT,
            }:
                challenge.status = MobileOtpChallenge.Status.EXPIRED
                challenge.save(update_fields=["status"])
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        if challenge.attempt_count >= OTP_MAX_ATTEMPTS:
            challenge.status = MobileOtpChallenge.Status.LOCKED
            challenge.save(update_fields=["status"])
            raise MobileOtpError("otp_locked", _("Too many incorrect OTP attempts."), status=429)
        challenge.attempt_count += 1
        digest = _otp_digest(normalized_otp)
        if not secrets.compare_digest(digest, challenge.otp_digest):
            if challenge.attempt_count >= OTP_MAX_ATTEMPTS:
                challenge.status = MobileOtpChallenge.Status.LOCKED
            challenge.save(update_fields=["attempt_count", "status"])
            raise MobileOtpError("invalid_otp", _("The OTP is invalid or expired."))
        User.objects.select_for_update().get(pk=challenge.student_id)
        user = User.objects.select_related("role").get(pk=challenge.student_id)
        if (
            not user.is_active
            or user.application_status != "active"
            or not TelegramAccount.objects.filter(user=user, is_active=True).exists()
        ):
            raise MobileOtpError("otp_unavailable", _("OTP login is unavailable for this number."))
        if mobile_installation_conflict(user, challenge.installation_id):
            raise MobileOtpError(
                "device_conflict",
                _("This push device belongs to another account."),
                status=409,
            )
        challenge.status = MobileOtpChallenge.Status.CONSUMED
        challenge.consumed_at = now
        challenge.save(update_fields=["attempt_count", "status", "consumed_at"])
        return user
