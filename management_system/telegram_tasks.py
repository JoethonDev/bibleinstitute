"""Celery task for idempotent Telegram intake and read-only navigation."""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from urllib.parse import parse_qs

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import translation, timezone
from django.utils.translation import gettext as _

import telebot

from .academic_access import active_year_published_offerings_for_user
from .models import Lesson, PublicationStatus, Quiz, TelegramAccount, TelegramBotConfig, TelegramWebhookUpdate
from .telegram.configuration import stored_token
from .telegram.linking import TelegramLinkError, is_eligible_user, link_with_phone, link_with_token
from .telegram.media import audio_links, download_audio_asset
from .telegram.navigation import (
    PAGE_SIZE,
    format_home,
    format_lesson,
    format_offering,
    format_quiz,
    lesson_detail_keyboard,
    lesson_list_keyboard,
    offerings_keyboard,
    offering_keyboard,
    parse_callback,
    quiz_detail_keyboard,
    quiz_list_keyboard,
    home_keyboard,
    back_home_keyboard,
)
from .telegram.broadcasts import process_due_broadcast
from .telegram.notifications import process_due_notifications
from .telegram.support import handle_admin_message, handle_student_message, handle_support_callback, is_support_callback
from .utils.quiz_access import quiz_window
from .utils.storage_operations import get_r2_client


logger = logging.getLogger(__name__)


class TelegramDeliveryError(Exception):
    """Safe retryable failure that never carries a Telegram token or API URL."""


def _private_message(payload: dict) -> dict | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return None
    return message


def _sender_ids(message: dict) -> tuple[int, int] | None:
    sender = message.get("from")
    chat = message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    try:
        user_id = int(sender["id"])
        chat_id = int(chat["id"])
    except (KeyError, TypeError, ValueError):
        return None
    return user_id, chat_id


def _phone_keyboard():
    keyboard = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    keyboard.add(
        telebot.types.KeyboardButton(
            _("Share my phone number"),
            request_contact=True,
        )
    )
    return keyboard


def _r2_client():
    if not getattr(settings, "R2_BUCKET_NAME", ""):
        return None
    return get_r2_client()


def _page(queryset, page: int | str):
    try:
        requested = int(page)
    except (TypeError, ValueError):
        requested = 1
    total = queryset.count()
    page_count = max(1, math.ceil(total / PAGE_SIZE))
    current = min(max(1, requested), page_count)
    start = (current - 1) * PAGE_SIZE
    return list(queryset[start:start + PAGE_SIZE]), current, page_count


def _active_offering(user, offering_id: str):
    try:
        pk = int(offering_id)
    except (TypeError, ValueError):
        return None
    return active_year_published_offerings_for_user(user).filter(pk=pk).first()


def _active_lesson(user, offering_id: str, lesson_id: str):
    offering = _active_offering(user, offering_id)
    if offering is None:
        return None, None
    try:
        lesson_pk = int(lesson_id)
    except (TypeError, ValueError):
        return offering, None
    lesson = Lesson.objects.select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
    ).filter(
        pk=lesson_pk,
        course_offering_id=offering.pk,
        status=PublicationStatus.PUBLISHED,
    ).first()
    return offering, lesson


def _active_quiz(user, offering_id: str, quiz_id: str):
    offering = _active_offering(user, offering_id)
    if offering is None:
        return None, None
    try:
        quiz_pk = int(quiz_id)
    except (TypeError, ValueError):
        return offering, None
    quiz = Quiz.objects.select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
        "quiz_type",
    ).filter(
        pk=quiz_pk,
        course_offering_id=offering.pk,
        status=PublicationStatus.PUBLISHED,
    ).first()
    return offering, quiz


def _linked_user(sender_id: int, chat_id: int):
    account = TelegramAccount.objects.select_related("user", "user__role").filter(
        telegram_user_id=sender_id,
        telegram_chat_id=chat_id,
        is_active=True,
    ).first()
    if account is None or not is_eligible_user(account.user):
        return None
    return account.user


def _answer_callback(bot, callback_id: str, text: str = "") -> None:
    try:
        bot.answer_callback_query(callback_id, text=text[:190] if text else None)
    except Exception:
        logger.debug("Telegram callback acknowledgement failed.")


def _present(bot, chat_id: int, text: str, keyboard, callback: dict | None = None) -> None:
    if callback:
        message = callback.get("message") or {}
        try:
            bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=int(message["message_id"]),
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.debug("Telegram callback message edit failed; sending a new message.")
    bot.send_message(chat_id, text, reply_markup=keyboard)


def _list_text(title: str, rows, empty_text: str) -> str:
    lines = [title]
    if rows:
        lines.extend(f"• {row}" for row in rows)
    else:
        lines.append(empty_text)
    return "\n".join(lines)


def _linked_prompt(bot, chat_id: int) -> None:
    bot.send_message(
        chat_id,
        _("To link your LMS account, share your phone number using the button below."),
        reply_markup=_phone_keyboard(),
    )


def _telegram_command(text: object) -> tuple[str, str] | None:
    """Normalize Telegram command text and return its command and payload."""
    if not isinstance(text, str):
        return None
    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized:
        return None
    parts = normalized.split(None, 1)
    raw_command = parts[0]
    payload = parts[1] if len(parts) == 2 else ""
    if raw_command.startswith("/"):
        raw_command = raw_command[1:]
    command_part, separator, query = raw_command.partition("?")
    command = command_part.casefold().split("@", 1)[0]
    if separator and command == "start":
        token_values = parse_qs(query, keep_blank_values=True).get("token", [])
        payload = token_values[0] if len(token_values) == 1 and token_values[0] else payload
    return command, payload.strip()


def _looks_like_link_token(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{32,64}", value))


def _handle_audio(bot, user, chat_id: int, offering_id: str, lesson_id: str) -> None:
    offering, lesson = _active_lesson(user, offering_id, lesson_id)
    if offering is None or lesson is None:
        bot.send_message(chat_id, _("This action is no longer available."))
        return
    try:
        links = audio_links(json.loads(lesson.links))
    except (TypeError, ValueError, json.JSONDecodeError):
        links = []
    client = _r2_client()
    delivered = 0
    if client is not None:
        for link in links:
            audio = download_audio_asset(link, client, settings.R2_BUCKET_NAME)
            if audio is None:
                continue
            stream, filename, _content_type = audio
            try:
                bot.send_audio(
                    chat_id,
                    telebot.types.InputFile(stream, file_name=filename),
                    caption=f"{lesson.name}\n{filename}",
                )
                delivered += 1
            except Exception:
                logger.warning("Telegram lesson audio delivery failed.")
            finally:
                stream.close()
    if not delivered:
        text = format_lesson(lesson, bool(links), user)
        text += "\n" + _("Audio is unavailable here. Open the lesson on the website instead.")
        bot.send_message(chat_id, text)


def _handle_callback(bot, callback: dict) -> None:
    callback_id = str(callback.get("id") or "")
    message = callback.get("message") or {}
    sender = callback.get("from") or {}
    try:
        chat_id = int((message.get("chat") or {})["id"])
        sender_id = int(sender["id"])
    except (KeyError, TypeError, ValueError):
        _answer_callback(bot, callback_id, _("This action is no longer available."))
        return

    parsed = parse_callback(callback.get("data", ""))
    if parsed is None:
        _answer_callback(bot, callback_id, _("This button is invalid. Please open the menu again."))
        return
    action, *parts = parsed
    if action == "noop":
        _answer_callback(bot, callback_id)
        return
    if action == "back":
        action, *parts = parts
    user = _linked_user(sender_id, chat_id)
    if user is None:
        _answer_callback(bot, callback_id, _("Your Telegram account is not linked yet."))
        _linked_prompt(bot, chat_id)
        return

    try:
        if action == "home":
            text, keyboard = format_home(user), home_keyboard()
        elif action == "support":
            text = _(
                "Send your message to support. You can send text, photos, documents, or videos."
            )
            keyboard = back_home_keyboard("home")
        elif action == "offerings":
            offerings, current, page_count = _page(
                active_year_published_offerings_for_user(user).order_by("course__name", "pk"),
                parts[0],
            )
            text = _list_text(_("Available courses"), [offering.course.name for offering in offerings], _("No courses are currently available."))
            keyboard = offerings_keyboard(offerings, current, page_count)
        elif action == "offering":
            offering = _active_offering(user, parts[0])
            if offering is None:
                raise LookupError
            text, keyboard = format_offering(offering), offering_keyboard(offering.pk)
        elif action == "lessons":
            offering = _active_offering(user, parts[0])
            if offering is None:
                raise LookupError
            lessons, current, page_count = _page(
                Lesson.objects.filter(course_offering=offering, status=PublicationStatus.PUBLISHED).order_by("created_date", "pk"),
                parts[1],
            )
            text = _list_text(_("Lessons"), [lesson.name for lesson in lessons], _("No published lessons are currently available."))
            keyboard = lesson_list_keyboard(offering.pk, lessons, current, page_count)
        elif action == "lesson":
            offering, lesson = _active_lesson(user, parts[0], parts[1])
            if offering is None or lesson is None:
                raise LookupError
            try:
                has_audio = bool(audio_links(json.loads(lesson.links)))
            except (TypeError, ValueError, json.JSONDecodeError):
                has_audio = False
            text, keyboard = format_lesson(lesson, has_audio, user), lesson_detail_keyboard(offering.pk, lesson.pk, has_audio)
        elif action == "lesson_audio":
            _answer_callback(bot, callback_id, _("Preparing the audio."))
            _handle_audio(bot, user, chat_id, parts[0], parts[1])
            return
        elif action == "quizzes":
            offering = _active_offering(user, parts[0])
            if offering is None:
                raise LookupError
            quizzes, current, page_count = _page(
                Quiz.objects.filter(course_offering=offering, status=PublicationStatus.PUBLISHED).select_related("quiz_type").order_by("opening_date", "pk"),
                parts[1],
            )
            text = _list_text(_("Exams"), [quiz.name for quiz in quizzes], _("No published exams are currently available."))
            keyboard = quiz_list_keyboard(offering.pk, quizzes, current, page_count)
        elif action == "quiz":
            offering, quiz = _active_quiz(user, parts[0], parts[1])
            if offering is None or quiz is None:
                raise LookupError
            opening, closing = quiz_window(quiz, user)
            text, keyboard = format_quiz(quiz, user, opening, closing), quiz_detail_keyboard(offering.pk, quiz.pk)
        else:
            raise LookupError
        _present(bot, chat_id, text, keyboard, callback)
        _answer_callback(bot, callback_id)
    except (LookupError, IndexError, ValueError, TypeError):
        _answer_callback(bot, callback_id, _("This action is no longer available."))


def _start_reply(bot, chat_id: int, sender_id: int, text: str) -> None:
    command = _telegram_command(text)
    payload = command[1] if command and command[0] == "start" else ""
    if payload:
        try:
            link_with_token(payload, sender_id, chat_id)
        except TelegramLinkError as exc:
            bot.send_message(chat_id, str(exc))
        else:
            bot.send_message(chat_id, _("Your LMS account is now linked to Telegram."), reply_markup=home_keyboard())
        return
    user = _linked_user(sender_id, chat_id)
    if user is not None:
        bot.send_message(chat_id, format_home(user), reply_markup=home_keyboard())
        return
    bot.send_message(
        chat_id,
        _("To link your LMS account, share your phone number using the button below."),
        reply_markup=_phone_keyboard(),
    )


def _message_reply(bot, message: dict, sender_id: int, chat_id: int, update_id: int) -> None:
    text = message.get("text")
    command = _telegram_command(text)
    command_name = command[0] if command else ""
    if command_name == "start":
        _start_reply(bot, chat_id, sender_id, text.strip())
        return
    if command_name in {"menu", "courses"}:
        user = _linked_user(sender_id, chat_id)
        if user is None:
            _linked_prompt(bot, chat_id)
        else:
            bot.send_message(chat_id, format_home(user), reply_markup=home_keyboard())
        return
    if command_name == "help":
        bot.send_message(chat_id, _("Use your LMS profile link to connect your account, or share your phone number."), reply_markup=home_keyboard())
        return
    if command_name == "status":
        linked = TelegramAccount.objects.filter(
            telegram_user_id=sender_id,
            telegram_chat_id=chat_id,
            is_active=True,
        ).exists()
        bot.send_message(
            chat_id,
            _("Your Telegram account is linked.") if linked else _("Your Telegram account is not linked yet."),
        )
        return

    contact = message.get("contact")
    if isinstance(contact, dict):
        try:
            contact_user_id = int(contact["user_id"])
        except (KeyError, TypeError, ValueError):
            bot.send_message(chat_id, _("Please share your own phone number using the button below."), reply_markup=_phone_keyboard())
            return
        if contact_user_id != sender_id:
            bot.send_message(chat_id, _("Please share your own phone number using the button below."), reply_markup=_phone_keyboard())
            return
        try:
            # Telegram names this Contact field ``phone_number``.  ``phone``
            # is not part of the Bot API payload and always produced an empty
            # value here, even when the user shared their own contact.
            link_with_phone(contact.get("phone_number", ""), sender_id, chat_id)
        except TelegramLinkError as exc:
            bot.send_message(chat_id, str(exc))
        else:
            bot.send_message(chat_id, _("Your LMS account is now linked to Telegram."))
        return

    linked_user = _linked_user(sender_id, chat_id)
    role_code = getattr(getattr(linked_user, "role", None), "role", None)
    if linked_user is not None and role_code == "student":
        handle_student_message(bot, user=linked_user, update_id=update_id, message=message)
        return
    if linked_user is not None and role_code == "admin":
        handle_admin_message(bot, admin=linked_user, message=message)
        return

    if linked_user is None and isinstance(text, str) and _looks_like_link_token(text.strip()):
        try:
            link_with_token(text.strip(), sender_id, chat_id)
        except TelegramLinkError as exc:
            bot.send_message(chat_id, str(exc))
        else:
            bot.send_message(chat_id, _("Your LMS account is now linked to Telegram."), reply_markup=home_keyboard())
        return

    if linked_user is None:
        _linked_prompt(bot, chat_id)
        return
    bot.send_message(chat_id, _("This message type is not supported for account linking."))


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def process_telegram_update(update_id: int) -> None:
    with transaction.atomic():
        update = TelegramWebhookUpdate.objects.select_for_update().get(pk=update_id)
        if update.processed_at:
            return
        config = TelegramBotConfig.objects.filter(
            is_active=True,
            bot_id=update.bot_id,
        ).first()
        payload = update.payload

    if config is None:
        with transaction.atomic():
            TelegramWebhookUpdate.objects.filter(pk=update_id, processed_at__isnull=True).update(
                processed_at=timezone.now(),
                processing_error=_("The Telegram bot is inactive."),
            )
        return

    with translation.override("ar"):
        message = _private_message(payload)
        callback = payload.get("callback_query") if isinstance(payload, dict) else None
        if message is None and not isinstance(callback, dict):
            TelegramWebhookUpdate.objects.filter(pk=update_id, processed_at__isnull=True).update(
                processed_at=timezone.now(),
            )
            return
        if isinstance(callback, dict):
            callback_message = callback.get("message")
            callback_chat = callback_message.get("chat") if isinstance(callback_message, dict) else None
            if not isinstance(callback_message, dict) or not isinstance(callback_chat, dict) or callback_chat.get("type") != "private":
                TelegramWebhookUpdate.objects.filter(pk=update_id, processed_at__isnull=True).update(
                    processed_at=timezone.now(),
                )
                return
        sender_ids = None
        if not isinstance(callback, dict):
            sender_ids = _sender_ids(message) if isinstance(message, dict) else None
        if isinstance(callback, dict):
            callback_message = callback.get("message") or {}
            sender = callback.get("from") or {}
            message = dict(callback_message)
            message["from"] = sender
            sender_ids = _sender_ids(message)
        if sender_ids is None:
            TelegramWebhookUpdate.objects.filter(pk=update_id, processed_at__isnull=True).update(
                processed_at=timezone.now(),
            )
            return
        sender_id, chat_id = sender_ids
        try:
            bot = telebot.TeleBot(stored_token(config), parse_mode=None, threaded=False)
            if isinstance(callback, dict):
                if is_support_callback(callback.get("data")):
                    handle_support_callback(bot, callback)
                else:
                    _handle_callback(bot, callback)
            elif isinstance(message, dict):
                _message_reply(bot, message, sender_id, chat_id, update_id)
        except Exception:
            TelegramWebhookUpdate.objects.filter(pk=update_id).update(
                processing_error=_("Telegram message delivery failed."),
            )
            raise TelegramDeliveryError(_("Telegram message delivery failed.")) from None
        TelegramWebhookUpdate.objects.filter(pk=update_id, processed_at__isnull=True).update(
            processed_at=timezone.now(),
            processing_error="",
        )


@shared_task(ignore_result=True)
def process_due_telegram_notifications(limit: int = 500) -> None:
    """Recover and send due Telegram notifications without a second worker."""
    with translation.override("ar"):
        process_due_notifications(limit=limit)


@shared_task(ignore_result=True)
def process_due_telegram_broadcasts(broadcast_id: int, limit: int = 100) -> None:
    """Deliver one bounded broadcast recipient batch and recover remaining rows."""
    with translation.override("ar"):
        process_due_broadcast(broadcast_id, limit=limit)
