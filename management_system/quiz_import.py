"""Normalize and validate admin-authored quiz JSON payloads.

The admin JSON importer previews and pre-fills the quiz form. The payload is
validated here and the question objects are built by the same canonical
``build_question_instance`` used by the quiz create/update POST handlers, so a
payload that previews successfully always saves the same Question rows as a
manually filled form. This module never writes to the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from django.utils import formats
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext as _

from .models import CourseOffering, PublicationStatus, QUIZ_TYPE_CODES, Question, QuizType
from .quiz_questions import build_question_instance
from .utils.timezones import ensure_aware, format_application_datetime

MAX_PAYLOAD_BYTES = 512 * 1024
MAX_QUESTIONS = 200
MAX_QUESTION_TITLE = 255
MAX_QUIZ_NAME = 64
MAX_QUESTION_GRADE = 1000
MAX_TOTAL_GRADE = 32767

QUESTION_TYPE_ALIASES = {
    "mcq": "mcq",
    "multiple_choice": "mcq",
    "multiple choice": "mcq",
    "multiplechoice": "mcq",
    "choice": "mcq",
    "choices": "mcq",
    "written": "written",
    "essay": "written",
    "text": "written",
    "open": "written",
    "complete": "complete",
    "completion": "complete",
    "fill": "complete",
    "fill_in_the_blank": "complete",
    "fill in the blank": "complete",
    "blank": "complete",
    "order_events": "order_events",
    "order events": "order_events",
    "order": "order_events",
    "ordering": "order_events",
    "sequence": "order_events",
    "match_related": "match_related",
    "match related": "match_related",
    "match": "match_related",
    "matching": "match_related",
    "pairs": "match_related",
}

QUIZ_FIELD_ALIASES = {
    "name": ("name", "quiz_name", "title"),
    "course_offering": ("course_offering", "course_offering_id", "offering", "offering_id"),
    "quiz_type": ("quiz_type", "quiz_type_id", "type"),
    "status": ("status", "publication_status"),
    "opening_date": ("opening_date", "opening", "start_date", "start"),
    "closing_date": ("closing_date", "closing", "end_date", "end"),
}

QUESTION_FIELD_ALIASES = {
    "type": ("type", "question_type"),
    "name": ("question", "name", "title", "text"),
    "grade": ("grade", "points", "score"),
    "choices": ("choices", "options"),
    "answer": ("answer", "correct_answer", "correct"),
    "items": ("items", "order", "events"),
    "pairs": ("pairs", "matches", "match"),
    "config": ("config",),
}


@dataclass
class QuizImportResult:
    """Validated import data plus everything the preview panel renders."""

    quiz: dict[str, Any] = field(default_factory=dict)
    questions: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_grade: int = 0


def parse_quiz_import(payload: str, *, mode: str = "create") -> QuizImportResult:
    """Parse one admin JSON payload into validated form data.

    ``mode`` is ``create`` when the payload targets the quiz create form (the
    offering must be in the active academic year) and ``update`` when it
    targets an existing quiz edit form (any existing offering is allowed).
    """
    result = QuizImportResult()

    if not isinstance(payload, str) or not payload.strip():
        result.errors.append(_("The JSON payload is empty."))
        return result

    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        result.errors.append(
            _("The JSON payload is larger than the %(limit)s KB limit.")
            % {"limit": MAX_PAYLOAD_BYTES // 1024}
        )
        return result

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        result.errors.append(
            _("The payload is not valid JSON: %(error)s at line %(line)s.")
            % {"error": exc.msg, "line": exc.lineno}
        )
        return result
    except (ValueError, RecursionError):
        # Python rejects integer literals beyond its digit limit and aborts on
        # absurdly deep nesting; both must fail as a payload error, not a 500.
        result.errors.append(_("The payload is not valid JSON."))
        return result

    if isinstance(raw, list):
        questions_raw = raw
        quiz_raw = {}
    elif isinstance(raw, dict):
        for key in sorted(set(raw) - {"quiz", "questions"}):
            result.warnings.append(
                _('Unknown field "%(field)s" was ignored.') % {"field": key}
            )
        quiz_value = raw.get("quiz")
        if quiz_value is None:
            quiz_raw = {}
        elif isinstance(quiz_value, dict):
            quiz_raw = quiz_value
        else:
            result.errors.append(_('"quiz" must be an object.'))
            quiz_raw = {}
        questions_raw = raw.get("questions")
        if not isinstance(questions_raw, list):
            result.errors.append(_('"questions" must be a list of question objects.'))
            questions_raw = []
    else:
        result.errors.append(_("The JSON root must be an object or a list of questions."))
        return result

    quiz_fields, quiz_display = _parse_quiz_block(quiz_raw, result, mode)

    if not questions_raw:
        result.errors.append(_("At least one question is required."))
    elif len(questions_raw) > MAX_QUESTIONS:
        result.errors.append(
            _("Too many questions: the maximum is %(limit)s.") % {"limit": MAX_QUESTIONS}
        )
    else:
        for number, raw_question in enumerate(questions_raw, start=1):
            question = _parse_question(raw_question, number, result)
            if question is not None:
                result.questions.append(question)

    if result.errors:
        return result

    result.quiz = quiz_fields
    result.total_grade = sum(question["grade"] for question in result.questions)
    if result.total_grade > MAX_TOTAL_GRADE:
        result.errors.append(
            _("The total grade %(total)s exceeds the maximum %(limit)s.")
            % {"total": result.total_grade, "limit": MAX_TOTAL_GRADE}
        )
        return result

    result.summary = {
        "name": quiz_display.get("name", ""),
        "offering_label": quiz_display.get("offering_label", ""),
        "quiz_type_label": quiz_display.get("quiz_type_label", ""),
        "status_label": quiz_display.get("status_label", ""),
        "opening_display": quiz_display.get("opening_display", ""),
        "closing_display": quiz_display.get("closing_display", ""),
        "total_grade": result.total_grade,
        "questions_count": len(result.questions),
    }
    return result


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]):
    """Return the first present alias key and its non-null value."""
    for alias in aliases:
        if alias in raw and raw[alias] is not None:
            return alias, raw[alias]
    return None, None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_label(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


MAX_IDENTIFIER_DIGITS = 18


def _parse_int_text(value: Any, *, max_digits: int) -> int | None:
    """Parse a digit string without tripping Python's int-conversion limits.

    Returns ``None`` for anything that is not a plain non-negative integer
    (a leading ``+`` is ignored) with at most ``max_digits`` digits, so
    oversized identifiers are reported as unknown values instead of raising
    or overflowing bigint.
    """
    text = _text(value)
    digits = text.lstrip("+")
    if not digits.isdigit() or len(digits) > max_digits:
        return None
    return int(digits)


def offering_label(offering: CourseOffering) -> str:
    """The exact offering label rendered by the quiz form select options."""
    return (
        f"{offering.course.name} — "
        f"{offering.academic_year_level.level.display_name} — "
        f"{offering.academic_year_level.academic_year.name}"
    )


def _parse_quiz_block(
    raw: dict[str, Any], result: QuizImportResult, mode: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    client: dict[str, Any] = {}
    display: dict[str, Any] = {}

    if not raw:
        return client, display
    if not isinstance(raw, dict):
        result.errors.append(_('"quiz" must be an object.'))
        return client, display

    known_aliases = {alias for aliases in QUIZ_FIELD_ALIASES.values() for alias in aliases}
    for key in sorted(set(raw) - known_aliases):
        result.warnings.append(_('Unknown field "%(field)s" was ignored.') % {"field": key})

    _alias, name_value = _pick(raw, QUIZ_FIELD_ALIASES["name"])
    if name_value is not None:
        name = _text(name_value)
        if not name:
            result.errors.append(_("The quiz name cannot be empty."))
        elif len(name) > MAX_QUIZ_NAME:
            result.errors.append(
                _("The quiz name must be at most %(limit)s characters.")
                % {"limit": MAX_QUIZ_NAME}
            )
        else:
            client["name"] = name
            display["name"] = name

    _alias, offering_value = _pick(raw, QUIZ_FIELD_ALIASES["course_offering"])
    if offering_value is not None:
        offering = _resolve_offering(offering_value, mode, result)
        if offering is not None:
            client["course_offering"] = offering.pk
            display["offering_label"] = offering_label(offering)

    _alias, quiz_type_value = _pick(raw, QUIZ_FIELD_ALIASES["quiz_type"])
    if quiz_type_value is not None:
        quiz_type = _resolve_quiz_type(quiz_type_value, result)
        if quiz_type is not None:
            client["quiz_type"] = quiz_type.pk
            display["quiz_type_label"] = (
                f"{quiz_type.name_en} — {quiz_type.name_ar}"
                if quiz_type.name_ar
                else quiz_type.name_en
            )

    _alias, status_value = _pick(raw, QUIZ_FIELD_ALIASES["status"])
    if status_value is not None:
        status = _text(status_value).casefold()
        if status in PublicationStatus.values:
            client["status"] = status
            display["status_label"] = PublicationStatus(status).label
        else:
            result.errors.append(
                _('Unknown status "%(value)s". Use one of: %(allowed)s.')
                % {"value": _text(status_value), "allowed": ", ".join(PublicationStatus.values)}
            )

    opening = _parse_import_datetime(
        raw, QUIZ_FIELD_ALIASES["opening_date"], _("opening date"), result
    )
    closing = _parse_import_datetime(
        raw, QUIZ_FIELD_ALIASES["closing_date"], _("closing date"), result
    )
    if opening is not None:
        client["opening_date"] = format_application_datetime(opening, "%Y-%m-%dT%H:%M:%S")
        display["opening_display"] = formats.date_format(opening, "DATETIME_FORMAT")
    if closing is not None:
        client["closing_date"] = format_application_datetime(closing, "%Y-%m-%dT%H:%M:%S")
        display["closing_display"] = formats.date_format(closing, "DATETIME_FORMAT")
    if opening is not None and closing is not None and closing <= opening:
        result.errors.append(_("Closing date must be after opening date."))

    return client, display


def _resolve_offering(value: Any, mode: str, result: QuizImportResult):
    if isinstance(value, dict):
        value = value.get("id") or value.get("pk") or value.get("name")
    if value is None or isinstance(value, bool):
        result.errors.append(
            _('Unknown course offering "%(value)s". Use an offering id or the exact label from the form.')
            % {"value": _text(value)}
        )
        return None

    queryset = CourseOffering.objects.select_related(
        "course", "academic_year_level__level", "academic_year_level__academic_year"
    )
    if mode == "create":
        queryset = queryset.filter(academic_year_level__academic_year__is_active=True)

    text = _text(value)
    unknown_message = _('Unknown course offering "%(value)s". Use an offering id or the exact label from the form.') % {"value": text}

    identifier = _parse_int_text(text, max_digits=MAX_IDENTIFIER_DIGITS)
    if identifier is not None:
        offering = queryset.filter(pk=identifier).first()
        if offering is None:
            if mode == "create" and CourseOffering.objects.filter(pk=identifier).exists():
                result.errors.append(
                    _("The selected course offering is not available in the active academic year.")
                )
            else:
                result.errors.append(unknown_message)
        return offering

    normalized = _normalized_label(text)
    matches = [
        offering
        for offering in queryset.order_by("pk")[:1000]
        if _normalized_label(offering_label(offering)) == normalized
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        result.errors.append(unknown_message)
    else:
        result.errors.append(
            _('More than one course offering matches "%(value)s". Use the offering id instead.')
            % {"value": text}
        )
    return None


def _resolve_quiz_type(value: Any, result: QuizImportResult):
    if isinstance(value, dict):
        value = value.get("code") or value.get("id") or value.get("name")
    if value is None or isinstance(value, bool):
        result.errors.append(
            _('Unknown quiz type "%(value)s". Use %(codes)s or a quiz type id.')
            % {"value": _text(value), "codes": ", ".join(sorted(QUIZ_TYPE_CODES))}
        )
        return None

    text = _text(value)
    identifier = _parse_int_text(text, max_digits=MAX_IDENTIFIER_DIGITS)
    if identifier is not None:
        quiz_type = QuizType.objects.filter(pk=identifier).first()
    else:
        code = text.casefold()
        quiz_type = QuizType.objects.filter(code=code).first() if code in QUIZ_TYPE_CODES else None

    if quiz_type is None or quiz_type.code not in QUIZ_TYPE_CODES:
        result.errors.append(
            _('Unknown quiz type "%(value)s". Use %(codes)s or a quiz type id.')
            % {"value": text, "codes": ", ".join(sorted(QUIZ_TYPE_CODES))}
        )
        return None
    return quiz_type


def _parse_import_datetime(raw: dict[str, Any], aliases: tuple[str, ...], label, result: QuizImportResult):
    alias, value = _pick(raw, aliases)
    if alias is None:
        return None

    invalid_message = _('Invalid date "%(value)s" for %(field)s. Use ISO format like 2026-09-20T10:00.') % {
        "value": _text(value),
        "field": label,
    }

    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        result.errors.append(invalid_message)
        return None

    text = _text(value)
    if not text:
        result.errors.append(invalid_message)
        return None

    parsed = parse_datetime(text)
    if parsed is None:
        date_value = parse_date(text)
        if date_value is None:
            result.errors.append(invalid_message)
            return None
        parsed = datetime.combine(date_value, time.min)

    return ensure_aware(parsed)


def _parse_question(raw: Any, number: int, result: QuizImportResult):
    if not isinstance(raw, dict):
        result.errors.append(
            _("Question %(number)s must be an object.") % {"number": number}
        )
        return None

    known_aliases = {alias for aliases in QUESTION_FIELD_ALIASES.values() for alias in aliases}
    for key in sorted(set(raw) - known_aliases):
        result.warnings.append(
            _('Question %(number)s: unknown field "%(field)s" was ignored.')
            % {"number": number, "field": key}
        )

    _alias, type_value = _pick(raw, QUESTION_FIELD_ALIASES["type"])
    question_type = (
        _canonical_question_type(type_value) if type_value is not None else None
    )
    if question_type is None:
        result.errors.append(
            _('Question %(number)s: unsupported or missing question type "%(type)s". Allowed types: %(allowed)s.')
            % {
                "number": number,
                "type": _text(type_value),
                "allowed": ", ".join(key for key, _label in Question.QUESTION_TYPES),
            }
        )
        return None

    _alias, title_value = _pick(raw, QUESTION_FIELD_ALIASES["name"])
    title = _text(title_value)
    if not title:
        result.errors.append(
            _("Question %(number)s: the question text is required.") % {"number": number}
        )
        return None
    if len(title) > MAX_QUESTION_TITLE:
        result.errors.append(
            _("Question %(number)s: the question text must be at most %(limit)s characters.")
            % {"number": number, "limit": MAX_QUESTION_TITLE}
        )
        return None

    _alias, grade_value = _pick(raw, QUESTION_FIELD_ALIASES["grade"])
    grade = _parse_grade(grade_value, number, result)
    if grade is None:
        return None

    question_data: dict[str, Any] = {
        "name": title,
        "type": question_type,
        "grade": grade,
    }
    _alias, answer_value = _pick(raw, QUESTION_FIELD_ALIASES["answer"])

    if question_type == "mcq":
        choices = _parse_string_list(
            _pick(raw, QUESTION_FIELD_ALIASES["choices"])[1],
            number,
            _("choices"),
            result,
        )
        if choices is None:
            return None
        if len(choices) < 2:
            result.errors.append(
                _("Question %(number)s: multiple choice questions need at least 2 choices.")
                % {"number": number}
            )
            return None

        answer = ""
        if answer_value is not None:
            answer = _resolve_mcq_answer(answer_value, choices, number, result)
            if answer is None:
                return None
        question_data["choices"] = choices
        question_data["answer"] = answer
        if not answer:
            result.warnings.append(
                _("Question %(number)s: no correct answer was provided, so it will be graded manually.")
                % {"number": number}
            )

    elif question_type == "complete":
        answer = _text(answer_value)
        question_data["choices"] = []
        question_data["answer"] = answer
        if not answer:
            result.warnings.append(
                _("Question %(number)s: no correct answer was provided, so it will be graded manually.")
                % {"number": number}
            )

    elif question_type == "written":
        question_data["choices"] = []
        question_data["answer"] = ""

    elif question_type == "order_events":
        items_value = _pick(raw, QUESTION_FIELD_ALIASES["items"])[1]
        if items_value is None:
            config = raw.get("config")
            if isinstance(config, dict):
                items_value = config.get("items")
        items = _parse_string_list(
            items_value, number, _("order event items"), result
        )
        if items is None:
            return None
        if len(items) < 2:
            result.errors.append(
                _("Question %(number)s: order events need at least 2 items.")
                % {"number": number}
            )
            return None
        question_data["choices"] = []
        question_data["answer"] = ""
        question_data["config"] = {"items": items}

    elif question_type == "match_related":
        pairs_value = _pick(raw, QUESTION_FIELD_ALIASES["pairs"])[1]
        if pairs_value is None:
            config = raw.get("config")
            if isinstance(config, dict):
                pairs_value = config.get("pairs")
        pairs = _parse_pairs(pairs_value, number, result)
        if pairs is None:
            return None
        if len(pairs) < 2:
            result.errors.append(
                _("Question %(number)s: match related questions need at least 2 pairs.")
                % {"number": number}
            )
            return None
        question_data["choices"] = []
        question_data["answer"] = ""
        question_data["config"] = {"pairs": pairs}

    try:
        instance = build_question_instance(question_data, None)
    except ValueError as exc:
        result.errors.append(
            _("Question %(number)s: %(message)s") % {"number": number, "message": str(exc)}
        )
        return None

    serialized = instance.serialize()
    serialized["id"] = ""
    return serialized


def _canonical_question_type(value: Any):
    key = _normalized_label(value).replace("-", "_")
    return QUESTION_TYPE_ALIASES.get(key)


def _parse_grade(value: Any, number: int, result: QuizImportResult):
    invalid_message = _(
        "Question %(number)s: the grade must be an integer between 0 and %(limit)s."
    ) % {"number": number, "limit": MAX_QUESTION_GRADE}

    if value is None:
        return 1
    if isinstance(value, bool):
        result.errors.append(invalid_message)
        return None
    if isinstance(value, int):
        grade = value
    elif isinstance(value, float) and float(value).is_integer():
        grade = int(value)
    else:
        grade = _parse_int_text(value, max_digits=4)
        if grade is None:
            result.errors.append(invalid_message)
            return None

    if not 0 <= grade <= MAX_QUESTION_GRADE:
        result.errors.append(invalid_message)
        return None
    return grade


def _parse_string_list(value: Any, number: int, label, result: QuizImportResult):
    required_message = _("Question %(number)s: %(label)s are required.") % {
        "number": number,
        "label": label,
    }
    if value is None:
        result.errors.append(required_message)
        return None
    if isinstance(value, str) or not isinstance(value, list):
        result.errors.append(
            _("Question %(number)s: %(label)s must be a list of text values.")
            % {"number": number, "label": label}
        )
        return None

    items: list[str] = []
    for item in value:
        if isinstance(item, (dict, list)):
            result.errors.append(
                _("Question %(number)s: %(label)s must contain text values only.")
                % {"number": number, "label": label}
            )
            return None
        text = _text(item)
        if text:
            items.append(text)

    if not items:
        result.errors.append(required_message)
        return None
    return items


def _resolve_mcq_answer(value: Any, choices: list[str], number: int, result: QuizImportResult):
    if isinstance(value, (dict, list, bool)):
        result.errors.append(
            _('Question %(number)s: the correct answer "%(answer)s" is not one of the choices.')
            % {"number": number, "answer": _text(value)}
        )
        return None

    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float) and float(value).is_integer():
        text = str(int(value))
    else:
        text = _text(value)

    if text in choices:
        return text

    position = _parse_int_text(text, max_digits=9)
    if position is not None and 1 <= position <= len(choices):
        return choices[position - 1]

    result.errors.append(
        _('Question %(number)s: the correct answer "%(answer)s" is not one of the choices.')
        % {"number": number, "answer": text}
    )
    return None


def _parse_pairs(value: Any, number: int, result: QuizImportResult):
    required_message = _("Question %(number)s: match related pairs are required.") % {
        "number": number
    }
    if value is None:
        result.errors.append(required_message)
        return None

    if isinstance(value, dict):
        if set(value) & {"left", "right", "key", "value"}:
            value = [value]
        else:
            value = [{"left": left, "right": right} for left, right in value.items()]

    if not isinstance(value, list):
        result.errors.append(
            _("Question %(number)s: pairs must be a list of left/right pairs.")
            % {"number": number}
        )
        return None

    pairs: list[dict[str, str]] = []
    for item in value:
        left = right = ""
        if isinstance(item, dict):
            left = _text(item.get("left") or item.get("key"))
            right = _text(item.get("right") or item.get("value"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            left = _text(item[0])
            right = _text(item[1])
        if left and right:
            pairs.append({"left": left, "right": right})

    if not pairs:
        result.errors.append(required_message)
        return None
    return pairs
