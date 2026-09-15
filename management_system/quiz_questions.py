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


def build_question_instance(question_data, quiz):
    """Build a Question instance from parsed form data."""
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
