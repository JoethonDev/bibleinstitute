"""Shared Telegram message deletion rules for bot-owned private messages."""

from __future__ import annotations

from datetime import datetime, timedelta

import telebot
from django.utils import timezone
from django.utils.translation import gettext as _


TELEGRAM_MESSAGE_DELETE_WINDOW = timedelta(hours=48)


def delete_bot_message(
    bot: telebot.TeleBot,
    *,
    chat_id: int | None,
    message_id: int | None,
    sent_at: datetime | None,
) -> tuple[str, str]:
    """Delete one bot-sent private message and return its durable outcome."""
    if chat_id is None or message_id is None or sent_at is None:
        return "failed", _("Telegram message identifiers are unavailable.")
    if timezone.now() - sent_at >= TELEGRAM_MESSAGE_DELETE_WINDOW:
        return "expired", _("Telegram only allows deleting messages for 48 hours.")
    try:
        bot.delete_message(chat_id, message_id)
    except Exception as exc:
        if is_missing_message_error(exc):
            return "deleted", ""
        return "failed", _("Telegram could not delete this message.")
    return "deleted", ""


def is_missing_message_error(exc: Exception) -> bool:
    """Return whether Telegram already removed the target message."""
    message = str(exc).lower()
    return "message to delete not found" in message or "message to edit not found" in message
