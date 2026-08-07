"""Race-safe LMS account linking for Telegram private chats."""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import quote

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import TelegramAccount, TelegramBotConfig, TelegramLinkToken, User
from ..utils.validators import normalize_phone
from .configuration import decrypt_secret, encrypt_secret
from .policy import TELEGRAM_LINKABLE_ROLES


class TelegramLinkError(Exception):
    """A safe Arabic-facing account-linking failure."""


def is_eligible_user(user: User) -> bool:
    role_code = getattr(getattr(user, "role", None), "role", None)
    return bool(
        user.is_active
        and user.application_status == "active"
        and role_code in TELEGRAM_LINKABLE_ROLES
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_link_token(user: User) -> tuple[TelegramLinkToken, str]:
    raw_token = secrets.token_urlsafe(32)
    link_token = TelegramLinkToken.objects.create(
        user=user,
        token_digest=_token_digest(raw_token),
        token_ciphertext=encrypt_secret(raw_token),
    )
    return link_token, raw_token


def _lock_user(user_id: int) -> User:
    """Lock the nullable-role user row without asking PostgreSQL to lock a join."""
    User.objects.select_for_update().get(pk=user_id)
    return User.objects.select_related("role").get(pk=user_id)


@transaction.atomic
def ensure_current_link_token(user: User) -> tuple[TelegramLinkToken, str]:
    """Return the permanent current token, creating it exactly once if absent."""
    locked_user = _lock_user(user.pk)
    if not is_eligible_user(locked_user):
        raise TelegramLinkError(_("This LMS account is not eligible for Telegram linking."))
    current = (
        TelegramLinkToken.objects.select_for_update()
        .filter(user=locked_user, used_at__isnull=True, revoked_at__isnull=True)
        .first()
    )
    if current:
        return current, decrypt_secret(current.token_ciphertext)
    try:
        return _new_link_token(locked_user)
    except IntegrityError as exc:
        raise TelegramLinkError(_("A Telegram linking request is already being prepared. Try again.")) from exc


@transaction.atomic
def regenerate_link_token(user: User) -> tuple[TelegramLinkToken, str]:
    """Revoke the unused token and issue one new permanent-until-used token."""
    locked_user = _lock_user(user.pk)
    if not is_eligible_user(locked_user):
        raise TelegramLinkError(_("This LMS account is not eligible for Telegram linking."))
    now = timezone.now()
    TelegramLinkToken.objects.select_for_update().filter(
        user=locked_user,
        used_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    return _new_link_token(locked_user)


def current_telegram_link(user: User) -> tuple[TelegramAccount | None, str | None]:
    """Return the user's linked account and safe website-ready deep link data."""
    account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    if account:
        return account, None
    if not is_eligible_user(user):
        return None, None
    config = TelegramBotConfig.objects.filter(is_active=True).first()
    if not config or not config.bot_username:
        return None, None
    _token, raw_token = ensure_current_link_token(user)
    return None, f"https://t.me/{quote(config.bot_username, safe='')}?start={quote(raw_token, safe='')}"


def _account_conflict(user_id: int, telegram_user_id: int, telegram_chat_id: int):
    return TelegramAccount.objects.filter(
        telegram_user_id=telegram_user_id
    ).exclude(user_id=user_id).first() or TelegramAccount.objects.filter(
        telegram_chat_id=telegram_chat_id
    ).exclude(user_id=user_id).first()


@transaction.atomic
def link_with_token(token: str, telegram_user_id: int, telegram_chat_id: int) -> TelegramAccount:
    if not isinstance(token, str) or not token or len(token) > 256:
        raise TelegramLinkError(_("This Telegram link is invalid. Please request a new link from your profile."))
    token_digest = _token_digest(token)
    link_token = (
        TelegramLinkToken.objects.select_for_update()
        .select_related("user")
        .filter(token_digest=token_digest)
        .first()
    )
    if (
        not link_token
        or not secrets.compare_digest(token_digest, link_token.token_digest)
        or link_token.used_at
        or link_token.revoked_at
    ):
        raise TelegramLinkError(_("This Telegram link has already been used or revoked. Please request a new link."))

    user = _lock_user(link_token.user_id)
    if not is_eligible_user(user):
        raise TelegramLinkError(_("This LMS account is inactive or no longer eligible. Contact support."))
    if _account_conflict(user.pk, telegram_user_id, telegram_chat_id):
        raise TelegramLinkError(_("This Telegram account is already linked to another LMS account. Contact support."))

    account = TelegramAccount.objects.select_for_update().filter(user=user).first()
    if account and account.is_active:
        raise TelegramLinkError(_("This LMS account is already linked to Telegram. Contact support."))
    try:
        if account:
            account.telegram_user_id = telegram_user_id
            account.telegram_chat_id = telegram_chat_id
            account.is_active = True
            account.last_inbound_at = timezone.now()
            account.save(update_fields=["telegram_user_id", "telegram_chat_id", "is_active", "last_inbound_at"])
        else:
            account = TelegramAccount.objects.create(
                user=user,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                last_inbound_at=timezone.now(),
            )
    except IntegrityError as exc:
        raise TelegramLinkError(_("This Telegram account is already linked to an LMS account. Contact support.")) from exc

    link_token.used_at = timezone.now()
    link_token.linked_account = account
    link_token.save(update_fields=["used_at", "linked_account"])
    return account


def _eligible_phone_matches(phone: str) -> list[User]:
    normalized = normalize_phone(phone)
    if not normalized:
        raise TelegramLinkError(_("The shared phone number is invalid or ambiguous. Contact support."))
    matches = []
    candidates = (
        User.objects.filter(
            phone__isnull=False,
            is_active=True,
            application_status="active",
            role__role__in=TELEGRAM_LINKABLE_ROLES,
        )
        .select_related("role")
        .only("id", "phone", "is_active", "application_status", "role_id")
        .iterator(chunk_size=500)
    )
    for candidate in candidates:
        if normalize_phone(candidate.phone) == normalized:
            matches.append(candidate)
    return matches


def link_with_phone(phone: str, telegram_user_id: int, telegram_chat_id: int) -> TelegramAccount:
    matches = _eligible_phone_matches(phone)
    if not matches:
        raise TelegramLinkError(_("No eligible LMS account matches this phone number. Contact support."))
    if len(matches) > 1:
        raise TelegramLinkError(_("More than one LMS account matches this phone number. Contact support."))
    user = matches[0]
    token, _raw = ensure_current_link_token(user)
    return link_with_token(_raw, telegram_user_id, telegram_chat_id)


@transaction.atomic
def unlink_own_telegram_account(user: User) -> None:
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    account = TelegramAccount.objects.select_for_update().filter(user=locked_user, is_active=True).first()
    if account:
        account.is_active = False
        account.save(update_fields=["is_active"])
