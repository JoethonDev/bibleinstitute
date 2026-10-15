"""Level-grouped course/offering selectors shared by every filter and form.

Courses and offerings are grouped under their level (first year to last year)
and listed by course name within each group, for both Django widgets and
hand-rendered ``<select>`` controls.
"""

from django.forms.models import (
    ModelChoiceField,
    ModelChoiceIterator,
    ModelMultipleChoiceField,
)


def level_of_offering(offering):
    return offering.academic_year_level.level


class GroupedByLevelIterator(ModelChoiceIterator):
    """Emit ``(level label, [(value, label), ...])`` groups in queryset order.

    The caller's queryset must already be ordered by level then by the display
    name so that a level never reappears as a separate group.
    """

    def __iter__(self):
        empty_label = getattr(self.field, "empty_label", None)
        if empty_label is not None:
            yield ("", empty_label)
        queryset = self.queryset
        if not getattr(queryset, "_prefetch_related_lookups", None):
            queryset = queryset.iterator()
        groups: list[tuple[str, list]] = []
        index: dict = {}
        for obj in queryset:
            level = self.field.level_of(obj)
            key = getattr(level, "pk", None)
            if key not in index:
                index[key] = len(groups)
                groups.append((level.display_name, []))
            groups[index[key]][1].append(self.choice(obj))
        for label, options in groups:
            yield (label, options)


class GroupedByLevelModelChoiceField(ModelChoiceField):
    """A ``ModelChoiceField`` rendered with one ``<optgroup>`` per level."""

    iterator = GroupedByLevelIterator

    def __init__(self, *args, level_of, **kwargs):
        self.level_of = level_of
        super().__init__(*args, **kwargs)


class GroupedByLevelModelMultipleChoiceField(ModelMultipleChoiceField):
    """A multi-select counterpart of :class:`GroupedByLevelModelChoiceField`."""

    iterator = GroupedByLevelIterator

    def __init__(self, *args, level_of, **kwargs):
        self.level_of = level_of
        super().__init__(*args, **kwargs)


def offering_option_groups(offerings, *, include_year=False) -> list[dict]:
    """Build ``[{label, options}]`` groups for a hand-rendered offering select.

    ``offerings`` must be ordered by level ordering, then course name. Each
    option is ``{"value": <pk>, "label": <course name> (+ " - <year>")}``.
    """
    groups: list[dict] = []
    index: dict = {}
    for offering in offerings:
        level = offering.academic_year_level.level
        key = level.pk
        if key not in index:
            index[key] = len(groups)
            groups.append({"label": level.display_name, "options": []})
        label = offering.course.name
        if include_year:
            label = f"{label} - {offering.academic_year_level.academic_year.name}"
        groups[index[key]]["options"].append(
            {"value": str(offering.pk), "label": label}
        )
    return groups
