"""Canonical quiz question construction shared by every quiz input path.

The quiz create/update POST handlers and the admin JSON import boundary must
agree on what a valid question is. This module owns that single rule set so a
payload that previews through the importer always saves the same Question rows
as a manually filled form.
"""

import json

from django.utils.translation import gettext as _

from .models import Question
from .utils.helpers import parse_json_value

# One supported question contract for the editor, the JSON importer and the
# request parser. Keep these bounds in sync with DATA_UPLOAD_MAX_NUMBER_FIELDS
# (200 questions x fixed fields and choices).
MAX_QUESTIONS = 200
MAX_QUESTION_TITLE = 255
MAX_QUESTION_GRADE = 1000
MAX_QUESTION_CHOICES = 12

SUPPORTED_QUESTION_TYPES = frozenset(key for key, _label in Question.QUESTION_TYPES)


def validate_question_payload(question_data) -> None:
    """Apply the canonical question bounds; raise ``ValueError`` when invalid.

    Both the manual create/update handlers and the JSON import preview call the
    shared builder, so this is the single rule that decides whether a question
    payload may become a ``Question`` row.
    """

    title = str(question_data.get("name") or "").strip()
    if not title:
        raise ValueError(_("The question text is required."))
    if len(title) > MAX_QUESTION_TITLE:
        raise ValueError(
            _("The question text must be at most %(limit)s characters.") % {"limit": MAX_QUESTION_TITLE}
        )

    question_type = question_data.get("type")
    if question_type not in SUPPORTED_QUESTION_TYPES:
        raise ValueError(
            _('Unsupported question type "%(type)s". Allowed types: %(allowed)s.')
            % {
                "type": str(question_type or ""),
                "allowed": ", ".join(key for key, _label in Question.QUESTION_TYPES),
            }
        )

    try:
        grade = int(question_data.get("grade", 1) or 1)
    except (TypeError, ValueError):
        raise ValueError(_("The question grade must be a whole number."))
    if not 0 <= grade <= MAX_QUESTION_GRADE:
        raise ValueError(
            _("The question grade must be between 0 and %(limit)s.") % {"limit": MAX_QUESTION_GRADE}
        )

    if question_type == "mcq":
        choices = [str(choice).strip() for choice in (question_data.get("choices") or []) if str(choice).strip()]
        if len(choices) < 2:
            raise ValueError(_("Multiple choice questions need at least 2 choices."))
        if len(choices) > MAX_QUESTION_CHOICES:
            raise ValueError(
                _("Multiple choice questions can have at most %(limit)s choices.") % {"limit": MAX_QUESTION_CHOICES}
            )


def build_question_instance(question_data, quiz):
    """Build a Question instance from parsed form data."""
    validate_question_payload(question_data)
    title = question_data.get("name", "")
    question_type = question_data.get("type", "")
    grade = int(question_data.get("grade", 1) or 1)
    config = parse_json_value(question_data.get("config", {}), {}) or {}
    choices = question_data.get("choices", [])
    correct_answer = (question_data.get("answer", "") or "").strip()
    auto_grade = False

    if question_type in Question.STRUCTURED_QUESTION_TYPES and not isinstance(config, dict):
        raise ValueError(_("Structured question config is invalid for question: %(title)s") % {"title": title})

    def _unique_values(values, label):
        normalized = []
        seen = set()

        for value in values:
            text = str(value).strip()
            if not text:
                continue

            if text in seen:
                raise ValueError(_("Each %(label)s must be unique for question: %(title)s") % {"label": label, "title": title})

            seen.add(text)
            normalized.append(text)

        if not normalized:
            raise ValueError(_("%(label)s are required for question: %(title)s") % {"label": label.capitalize(), "title": title})

        return normalized

    if question_type == "mcq":
        choices = [choice.strip() for choice in choices if str(choice).strip()]
        if correct_answer and correct_answer not in choices:
            raise ValueError(_("Correct answer is not in choices for question : %(title)s") % {'title': title})
        choices = json.dumps(choices)
        auto_grade = bool(correct_answer)

    elif question_type == "written":
        correct_answer = ""
        choices = json.dumps([])
        auto_grade = False

    elif question_type == "complete":
        choices = json.dumps([])
        auto_grade = bool(correct_answer)

    elif question_type == "order_events":
        items = config.get("items") or choices or []
        items = _unique_values(items, _("order event item"))
        config = {"items": items}
        choices = json.dumps(items)
        correct_answer = ""
        auto_grade = True

    elif question_type == "match_related":
        pairs = config.get("pairs") or []
        normalized_pairs = []
        seen_left_values = set()
        seen_right_values = set()

        for pair in pairs:
            left = right = ""
            if isinstance(pair, dict):
                left = str(pair.get("left", "")).strip()
                right = str(pair.get("right", "")).strip()
            elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                left = str(pair[0]).strip()
                right = str(pair[1]).strip()

            if left and right:
                if left in seen_left_values:
                    raise ValueError(_("Each match related left item must be unique for question: %(title)s") % {"title": title})
                if right in seen_right_values:
                    raise ValueError(_("Each match related right item must be unique for question: %(title)s") % {"title": title})

                seen_left_values.add(left)
                seen_right_values.add(right)
                normalized_pairs.append({"left": left, "right": right})

        if not normalized_pairs:
            raise ValueError(_("Match Related questions need at least one pair"))

        config = {"pairs": normalized_pairs}
        choices = json.dumps([pair["left"] for pair in normalized_pairs])
        correct_answer = ""
        auto_grade = True

    else:
        choices = json.dumps([choice.strip() for choice in choices]) if choices else json.dumps([])
        auto_grade = bool(correct_answer) and question_type != "written"

    return Question(
        title=title,
        quiz=quiz,
        correct_answer=correct_answer,
        question_type=question_type,
        choices=choices,
        config=config,
        grade=grade,
        auto_grade=auto_grade,
    )
