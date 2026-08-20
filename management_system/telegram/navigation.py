"""Phase 3 Telegram read-only navigation and Arabic formatting helpers.

This module builds callback data, inline keyboards, trusted website links, and
Arabic message text for the read-only Telegram navigator. It is a pure helper
layer:

- It never queries the database, never calls the Telegram API, and never writes.
- Callers must pass already-authorized, appropriately ``select_related`` /
  ``prefetch_related`` objects.
- Callback payloads carry only a fixed prefix, a short action, and server-side
  integer identifiers or page values. The eventual handler must re-query and
  re-authorize every offering/lesson/quiz before acting on a callback.
- Website links are built from ``settings.DJANGO_SITE_DOMAIN`` (HTTPS only) and
  Django ``reverse()`` for verified named routes under the Arabic locale.
- Output is Arabic-only plain text (no HTML), so it is safe under any Telegram
  parse mode and requires no escaping.
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

import telebot
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import translation
from django.utils.translation import gettext as _

from ..utils.timezones import ensure_aware, format_user_datetime

# Page size used by callers when slicing paginated lists.
PAGE_SIZE = 5

# ---------------------------------------------------------------------------
# Callback data
# ---------------------------------------------------------------------------

_CALLBACK_PREFIX = "tg1"
_SEPARATOR = ":"
_MAX_CALLBACK_LENGTH = 64
_ACTION_PATTERN = re.compile(r"^[a-z_]{1,16}$")
_COMPONENT_PATTERN = re.compile(r"^[a-z0-9_]{1,16}$")

# action -> number of integer/opaque components it must carry.
# "back" is handled separately because it embeds a destination action.
_ACTION_ARITY = {
    "home": 0,
    "offerings": 1,  # page
    "offering": 1,  # offering_id
    "lessons": 2,  # offering_id, page
    "lesson": 2,  # offering_id, lesson_id
    "lesson_audio": 3,  # offering_id, lesson_id, audio index
    "quizzes": 2,  # offering_id, page
    "quiz": 2,  # offering_id, quiz_id
    "support": 0,
}
_BACK_ACTION = "back"


def make_callback(action: str, *parts: int | str) -> str:
    """Build a short opaque callback string from an action and integer parts.

    Raises ValueError for unknown actions, wrong component counts, invalid
    character sets, or payloads longer than Telegram's 64-byte limit.
    """
    if not isinstance(action, str) or not _ACTION_PATTERN.match(action):
        raise ValueError("Invalid callback action.")
    if action not in _ACTION_ARITY and action != _BACK_ACTION:
        raise ValueError("Unknown callback action.")

    components = []
    for part in parts:
        if isinstance(part, bool):
            raise ValueError("Callback components must be integers or opaque strings.")
        if isinstance(part, int):
            if part < 0:
                raise ValueError("Callback integer components must be non-negative.")
            components.append(str(part))
        elif isinstance(part, str):
            if not _COMPONENT_PATTERN.match(part):
                raise ValueError("Invalid callback component.")
            components.append(part)
        else:
            raise ValueError("Callback components must be integers or opaque strings.")

    if action == _BACK_ACTION:
        if not components or components[0] not in _ACTION_ARITY:
            raise ValueError("Back callback must name a known destination action.")
        if len(components) - 1 != _ACTION_ARITY[components[0]]:
            raise ValueError("Back callback destination has the wrong number of components.")
    elif len(components) != _ACTION_ARITY[action]:
        raise ValueError("Wrong number of callback components.")

    data = _SEPARATOR.join([_CALLBACK_PREFIX, action, *components])
    if len(data) > _MAX_CALLBACK_LENGTH:
        raise ValueError("Callback data exceeds 64 bytes.")
    return data


def parse_callback(data: str) -> tuple[str, ...] | None:
    """Validate external callback data and return ``(action, *components)``.

    Returns None for malformed, oversized, or unknown payloads. Numeric
    components stay as digit strings; callers convert and re-validate them and
    must re-authorize the referenced objects.
    """
    if not isinstance(data, str) or not data or len(data) > _MAX_CALLBACK_LENGTH:
        return None
    parts = data.split(_SEPARATOR)
    if len(parts) < 2 or parts[0] != _CALLBACK_PREFIX:
        return None
    action = parts[1]
    if not _ACTION_PATTERN.match(action):
        return None
    components = parts[2:]
    if action == _BACK_ACTION:
        if not components or components[0] not in _ACTION_ARITY:
            return None
        if len(components) - 1 != _ACTION_ARITY[components[0]]:
            return None
    elif action not in _ACTION_ARITY or len(components) != _ACTION_ARITY[action]:
        return None
    for component in components:
        if not _COMPONENT_PATTERN.match(component):
            return None
    return (action, *components)


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------

_ROW = telebot.types.InlineKeyboardButton
_KB = telebot.types.InlineKeyboardMarkup


def _short_label(text: str, limit: int = 40) -> str:
    """Truncate a button label to stay within Telegram's button text limit."""
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _pagination_row(action: str, page: int, page_count: int, offering_id: int | None = None) -> list:
    """Return only real Previous/Next buttons for one paginated list."""
    page = max(1, page)
    page_count = max(1, page_count)
    if page > page_count:
        page = page_count
    if page_count <= 1:
        return []
    buttons = []
    if page > 1:
        prev_args = (offering_id, page - 1) if offering_id is not None else (page - 1,)
        buttons.append(_ROW(_("Previous"), callback_data=make_callback(action, *prev_args)))
    if page < page_count:
        next_args = (offering_id, page + 1) if offering_id is not None else (page + 1,)
        buttons.append(_ROW(_("Next"), callback_data=make_callback(action, *next_args)))
    return buttons


def _add_paired(keyboard: telebot.types.InlineKeyboardMarkup, buttons: list) -> None:
    """Append buttons in two-column rows, leaving only an unavoidable last item alone."""
    for index in range(0, len(buttons), 2):
        keyboard.row(*buttons[index:index + 2])


def audio_label(link: dict, index: int) -> str:
    """Return a short human-readable label without storage/media terminology."""
    raw_name = link.get("name") if isinstance(link, dict) else ""
    name = posixpath.basename(raw_name) if isinstance(raw_name, str) and raw_name.strip() else ""
    name = re.sub(r"\.(?:m3u8|ts|mp3|m4a|ogg|wav)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[_-]+", " ", name)
    name = re.sub(r"\b(?:audio|ts)\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return _short_label(name or _("Part") + f" {index + 1}")


def home_keyboard() -> telebot.types.InlineKeyboardMarkup:
    """Top-level keyboard for courses and student support."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_("My courses"), callback_data=make_callback("offerings", 1)),
            _ROW(_("Contact support"), callback_data=make_callback("support")),
        ],
    )
    return keyboard


def offerings_keyboard(offerings, page: int, page_count: int) -> telebot.types.InlineKeyboardMarkup:
    """Offering buttons in paired rows, followed by pagination and Home."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_short_label(offering.course.name), callback_data=make_callback("offering", offering.pk))
            for offering in offerings
        ],
    )
    pagination = _pagination_row("offerings", page, page_count)
    if pagination:
        keyboard.row(*pagination)
    _add_paired(keyboard, [_ROW(_("Home"), callback_data=make_callback("home"))])
    return keyboard


def offering_keyboard(offering_id: int) -> telebot.types.InlineKeyboardMarkup:
    """Menu for one offering: lessons, quizzes, back, and home."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_("Lessons"), callback_data=make_callback("lessons", offering_id, 1)),
            _ROW(_("Exams"), callback_data=make_callback("quizzes", offering_id, 1)),
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, "offerings", 1)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


def lesson_list_keyboard(offering_id: int, lessons, page: int, page_count: int) -> telebot.types.InlineKeyboardMarkup:
    """Lesson buttons in paired rows, followed by pagination and navigation."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_short_label(lesson.name), callback_data=make_callback("lesson", offering_id, lesson.pk))
            for lesson in lessons
        ],
    )
    pagination = _pagination_row("lessons", page, page_count, offering_id=offering_id)
    if pagination:
        keyboard.row(*pagination)
    _add_paired(
        keyboard,
        [
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, "offering", offering_id)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


def lesson_detail_keyboard(offering_id: int, lesson_id: int, audio_entries) -> telebot.types.InlineKeyboardMarkup:
    """Audio choices in paired rows, followed by Back and Home."""
    keyboard = _KB(row_width=2)
    audio_buttons = [
        _ROW(
            audio_label(link, index),
            callback_data=make_callback("lesson_audio", offering_id, lesson_id, index),
        )
        for index, link in enumerate(audio_entries or [])
    ]
    _add_paired(keyboard, audio_buttons)
    _add_paired(
        keyboard,
        [
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, "lessons", offering_id, 1)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


def quiz_list_keyboard(offering_id: int, quizzes, page: int, page_count: int) -> telebot.types.InlineKeyboardMarkup:
    """Quiz buttons in paired rows, followed by pagination and navigation."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_short_label(quiz.name), callback_data=make_callback("quiz", offering_id, quiz.pk))
            for quiz in quizzes
        ],
    )
    pagination = _pagination_row("quizzes", page, page_count, offering_id=offering_id)
    if pagination:
        keyboard.row(*pagination)
    _add_paired(
        keyboard,
        [
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, "offering", offering_id)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


def quiz_detail_keyboard(offering_id: int, quiz_id: int) -> telebot.types.InlineKeyboardMarkup:
    """Quiz detail actions: Back and Home."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, "quizzes", offering_id, 1)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


def back_home_keyboard(back_action: str, *parts: int | str) -> telebot.types.InlineKeyboardMarkup:
    """Generic Back + Home keyboard to a caller-chosen destination action."""
    keyboard = _KB(row_width=2)
    _add_paired(
        keyboard,
        [
            _ROW(_("Back"), callback_data=make_callback(_BACK_ACTION, back_action, *parts)),
            _ROW(_("Home"), callback_data=make_callback("home")),
        ],
    )
    return keyboard


# ---------------------------------------------------------------------------
# Trusted website links
# ---------------------------------------------------------------------------

# Only named routes verified in management_system/urls.py. Each accepts the
# integer IDs a formatter has at hand; lesson-stream/lesson-manifest require a
# file index and are therefore intentionally not used here.
_TRUSTED_ROUTES = frozenset({"course-details", "lesson-details", "quiz-details"})


def website_url(route_name: str, args: list[int]) -> str | None:
    """Return an HTTPS, Arabic-locale website URL for a trusted route.

    Returns None when the site domain is missing/not an HTTPS origin, the route
    is not in the trusted set, or reversing fails.
    """
    if route_name not in _TRUSTED_ROUTES or not isinstance(args, (list, tuple)):
        return None
    domain = (getattr(settings, "DJANGO_SITE_DOMAIN", "") or "").strip().rstrip("/")
    if not domain:
        return None
    parts = urlsplit(domain)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        return None
    try:
        with translation.override("ar"):
            path = reverse(route_name, args=[int(arg) for arg in args])
    except (NoReverseMatch, TypeError, ValueError):
        return None
    return f"{domain}{path}"


# ---------------------------------------------------------------------------
# Arabic formatters (plain text, no HTML)
# ---------------------------------------------------------------------------


def _level_label(level) -> str:
    return level.name_ar or level.name_en or (_("Level %(ordering)s") % {"ordering": level.ordering})


def _hours(count: int) -> str:
    if count == 2:
        return _("two hours")
    if 3 <= count <= 10:
        return _("hours")
    return _("hour")


def _minutes(count: int) -> str:
    if count == 2:
        return _("two minutes")
    if 3 <= count <= 10:
        return _("minutes")
    return _("minute")


def _human_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return _("%(hours)s %(hour_unit)s and %(minutes)s %(minute_unit)s") % {
            "hours": hours,
            "hour_unit": _hours(hours),
            "minutes": minutes,
            "minute_unit": _minutes(minutes),
        }
    if hours:
        return _("%(hours)s %(hour_unit)s") % {"hours": hours, "hour_unit": _hours(hours)}
    return _("%(minutes)s %(minute_unit)s") % {"minutes": minutes, "minute_unit": _minutes(minutes)}


def format_home(user) -> str:
    """Arabic welcome message for the bot's home screen."""
    name = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()
    lines = []
    if name:
        lines.append(_("Welcome %(name)s.") % {"name": name})
    lines.append(_("Welcome to the institute."))
    lines.append(_("Use the buttons below to browse your available courses."))
    return "\n".join(lines)


def offering_line(offering) -> str:
    """One concise Arabic list line for an offering."""
    return offering.course.name


def lesson_line(lesson) -> str:
    """One concise Arabic list line for a lesson."""
    return lesson.name


def quiz_line(quiz) -> str:
    """One concise Arabic list line for a quiz (name plus type)."""
    quiz_type = quiz.quiz_type.name_ar if quiz.quiz_type and quiz.quiz_type.name_ar else "غير محدد"
    return _("%(name)s (%(type)s)") % {"name": quiz.name, "type": quiz_type}


def format_offering(offering) -> str:
    """Arabic detail text for one already-authorized offering."""
    lines = [_('Course: %(name)s') % {"name": offering.course.name}]
    lines.append(_('Level: %(name)s') % {"name": _level_label(offering.academic_year_level.level)})
    lines.append(_('Academic year: %(name)s') % {"name": offering.academic_year_level.academic_year.name})
    if offering.instructor:
        lines.append(_('Instructor: %(name)s') % {"name": offering.instructor})
    link = website_url("course-details", [offering.pk])
    if link:
        lines.append(_('Open course on the website: %(link)s') % {"link": link})
    return "\n".join(lines)


def format_lesson(lesson, audio_available: bool, user) -> str:
    """Arabic detail text for one already-authorized lesson.

    ``audio_available`` is supplied by the caller from the lesson/R2
    representation; it is never inferred from video/PDF presence. ``user`` is
    part of the stable interface for callers that render user-specific
    context; lesson dates are calendar dates and do not require a timezone.
    Empty description fields are omitted.
    """
    offering = lesson.course_offering
    lines = [
        _('Course: %(name)s') % {"name": offering.course.name},
        _('Lesson: %(name)s') % {"name": lesson.name},
    ]
    if lesson.description and lesson.description.strip():
        lines.append(_('Description: %(description)s') % {"description": lesson.description.strip()})
    if lesson.created_date:
        lines.append(_('Publication date: %(date)s') % {"date": lesson.created_date.strftime('%d/%m/%Y')})
    link = website_url("lesson-details", [offering.pk, lesson.pk])
    if link:
        lines.append(_('Open lesson on the website: %(link)s') % {"link": link})
    return "\n".join(lines)


def format_quiz(quiz, user, opening=None, closing=None) -> str:
    """Arabic detail text for one already-authorized quiz.

    ``opening``/``closing`` are the effective quiz window datetimes — pass the
    result of ``utils.quiz_access.quiz_window(quiz, user)`` when available so
    per-student exceptional openings are reflected. When omitted, the
    already-loaded ``quiz.opening_date``/``quiz.closing_date`` are used. This
    formatter never queries. Durations are clamped to be non-negative, and
    naive values are handled safely by the shared timezone helper.
    """
    offering = quiz.course_offering
    lines = [
        _('Course: %(name)s') % {"name": offering.course.name},
        _('Exam: %(name)s') % {"name": quiz.name},
    ]
    if quiz.quiz_type and quiz.quiz_type.name_ar:
        lines.append(_('Type: %(type)s') % {"type": quiz.quiz_type.name_ar})
    else:
        lines.append(_('Type: Unassigned'))
    lines.append(_('Total grade: %(grade)s') % {"grade": quiz.total_grade})

    opening_value = opening if opening is not None else quiz.opening_date
    closing_value = closing if closing is not None else quiz.closing_date
    if opening_value is not None and closing_value is not None:
        time_format = "%d/%m/%Y %H:%M"
        lines.append(_('Opens: %(date)s') % {"date": format_user_datetime(opening_value, user, time_format)})
        lines.append(_('Closes: %(date)s') % {"date": format_user_datetime(closing_value, user, time_format)})
        try:
            duration_seconds = (ensure_aware(closing_value) - ensure_aware(opening_value)).total_seconds()
        except TypeError:
            duration_seconds = None
        if duration_seconds is not None:
            lines.append(_('Duration: %(duration)s') % {"duration": _human_duration(duration_seconds)})

    link = website_url("quiz-details", [offering.pk, quiz.pk])
    if link:
        lines.append(_('Open exam on the website: %(link)s') % {"link": link})
    return "\n".join(lines)
