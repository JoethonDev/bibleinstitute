"""Encrypted Telegram configuration and bot lifecycle operations."""

from __future__ import annotations

import re
import secrets
from logging import getLogger

import telebot
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from ..models import TelegramBotConfig, User
from .policy import webhook_url


logger = getLogger(__name__)
TOKEN_PATTERN = re.compile(r"(?:bot)?\d{5,}:[A-Za-z0-9_-]{10,}")


class TelegramConfigurationError(Exception):
    """A safe operator-facing Telegram configuration failure."""


def _encryption_key() -> str:
    key = getattr(settings, "TELEGRAM_ENCRYPTION_KEY", "")
    if not key or len(key) < 16:
        raise TelegramConfigurationError(_("Telegram encryption is not configured."))
    return key


def _require_postgresql() -> None:
    if connection.vendor != "postgresql":
        raise TelegramConfigurationError(
            _("Telegram configuration requires the PostgreSQL database.")
        )


def encrypt_secret(value: str) -> str:
    """Encrypt a secret with PostgreSQL pgcrypto and return base64 ciphertext."""
    _require_postgresql()
    if not value:
        raise TelegramConfigurationError(_("The Telegram secret cannot be empty."))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT encode(pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256'), 'base64')",
                [value, _encryption_key()],
            )
            return cursor.fetchone()[0]
    except TelegramConfigurationError:
        raise
    except Exception as exc:
        logger.exception("Telegram pgcrypto encryption failed")
        raise TelegramConfigurationError(
            _("PostgreSQL encryption support is unavailable.")
        ) from exc


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a pgcrypto ciphertext without exposing it in errors or logs."""
    _require_postgresql()
    if not ciphertext:
        raise TelegramConfigurationError(_("The Telegram token has not been saved."))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pgp_sym_decrypt(decode(%s, 'base64'), %s)",
                [ciphertext, _encryption_key()],
            )
            value = cursor.fetchone()[0]
    except TelegramConfigurationError:
        raise
    except Exception as exc:
        logger.exception("Telegram pgcrypto decryption failed")
        raise TelegramConfigurationError(
            _("The saved Telegram token could not be decrypted.")
        ) from exc
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _safe_api_error(exc: Exception, token: str | None = None) -> str:
    message = str(exc)
    if token:
        message = message.replace(token, "[redacted-token]")
    message = TOKEN_PATTERN.sub("[redacted-token]", message)
    return message[:500] or _("Telegram API request failed.")


def _bot(token: str) -> telebot.TeleBot:
    return telebot.TeleBot(token, parse_mode=None, threaded=False)


def _validate_token(token: str):
    try:
        bot = _bot(token)
        identity = bot.get_me()
    except Exception as exc:
        raise TelegramConfigurationError(
            _("Telegram token validation failed: %(error)s")
            % {"error": _safe_api_error(exc, token)}
        ) from exc
    if not identity or not getattr(identity, "id", None):
        raise TelegramConfigurationError(_("Telegram returned an invalid bot identity."))
    return bot, identity


def _record_error(config_id: int, message: str) -> None:
    TelegramBotConfig.objects.filter(pk=config_id).update(last_error=message[:500])


def get_or_create_config() -> TelegramBotConfig:
    config, _ = TelegramBotConfig.objects.get_or_create(singleton="default")
    return config


@transaction.atomic
def save_bot_token(config_id: int, token: str) -> TelegramBotConfig:
    """Save an encrypted token without activating the bot."""
    config = TelegramBotConfig.objects.select_for_update().get(pk=config_id)
    if config.is_active:
        raise TelegramConfigurationError(
            _("Deactivate the active bot before saving a token. Use rotate for replacement.")
        )
    config.token_ciphertext = encrypt_secret(token)
    config.last_error = ""
    config.save(update_fields=["token_ciphertext", "last_error", "updated_at"])
    return config


def _activate(config_id: int, actor: User, replacement_token: str | None) -> TelegramBotConfig:
    old_token = None
    new_token = None
    try:
        with transaction.atomic():
            config = TelegramBotConfig.objects.select_for_update().get(pk=config_id)
            current_token = decrypt_secret(config.token_ciphertext) if config.token_ciphertext else None
            token = (replacement_token or current_token or "").strip()
            if not token:
                raise TelegramConfigurationError(_("Enter a Telegram bot token first."))
            new_token = token
            if config.is_active:
                old_token = current_token

            bot, identity = _validate_token(token)
            target_url = webhook_url(settings.DJANGO_SITE_DOMAIN)
            webhook_secret = secrets.token_urlsafe(32)
            try:
                registered = bot.set_webhook(
                    url=target_url,
                    secret_token=webhook_secret,
                    allowed_updates=["message", "callback_query"],
                    drop_pending_updates=False,
                )
            except Exception as exc:
                raise TelegramConfigurationError(
                    _("Telegram webhook registration failed: %(error)s")
                    % {"error": _safe_api_error(exc, token)}
                ) from exc
            if registered is False:
                raise TelegramConfigurationError(_("Telegram webhook registration failed."))

            config.token_ciphertext = encrypt_secret(token)
            config.webhook_secret_ciphertext = encrypt_secret(webhook_secret)
            config.bot_id = identity.id
            config.bot_username = identity.username or ""
            config.is_active = True
            config.webhook_url = target_url
            config.activated_at = timezone.now()
            config.activated_by = actor
            config.deactivated_at = None
            config.deactivated_by = None
            config.last_validated_at = timezone.now()
            config.last_error = ""
            config.save()
    except TelegramConfigurationError as exc:
        _record_error(config_id, str(exc))
        raise
    except Exception as exc:
        _record_error(config_id, _("Telegram activation failed."))
        raise TelegramConfigurationError(_("Telegram activation failed.")) from exc

    if old_token and old_token != new_token:
        try:
            _bot(old_token).delete_webhook(drop_pending_updates=False)
        except Exception:
            logger.warning("The previous Telegram webhook could not be removed after rotation.")
    return TelegramBotConfig.objects.get(pk=config_id)


def activate_bot(config_id: int, actor: User, token: str | None = None) -> TelegramBotConfig:
    return _activate(config_id, actor, token)


def rotate_bot(config_id: int, actor: User, token: str) -> TelegramBotConfig:
    if not token:
        raise TelegramConfigurationError(_("Enter the replacement Telegram bot token."))
    config = TelegramBotConfig.objects.get(pk=config_id)
    if not config.is_active:
        raise TelegramConfigurationError(_("Only an active bot can be rotated."))
    return _activate(config_id, actor, token)


def deactivate_bot(config_id: int, actor: User) -> TelegramBotConfig:
    try:
        with transaction.atomic():
            config = TelegramBotConfig.objects.select_for_update().get(pk=config_id)
            if not config.is_active:
                return config
            token = decrypt_secret(config.token_ciphertext)
            try:
                removed = _bot(token).delete_webhook(drop_pending_updates=False)
            except Exception as exc:
                raise TelegramConfigurationError(
                    _("Telegram webhook removal failed: %(error)s")
                    % {"error": _safe_api_error(exc, token)}
                ) from exc
            if removed is False:
                raise TelegramConfigurationError(_("Telegram webhook removal failed."))
            config.is_active = False
            config.deactivated_at = timezone.now()
            config.deactivated_by = actor
            config.last_error = ""
            config.save(update_fields=["is_active", "deactivated_at", "deactivated_by", "last_error", "updated_at"])
            return config
    except TelegramConfigurationError as exc:
        _record_error(config_id, str(exc))
        raise
    except Exception as exc:
        _record_error(config_id, _("Telegram deactivation failed."))
        raise TelegramConfigurationError(_("Telegram deactivation failed.")) from exc


def stored_token(config: TelegramBotConfig) -> str:
    """Return the decrypted token only to trusted server-side Telegram code."""
    return decrypt_secret(config.token_ciphertext)


def webhook_secret(config: TelegramBotConfig) -> str:
    """Return the decrypted webhook secret only to the webhook validator."""
    return decrypt_secret(config.webhook_secret_ciphertext)
