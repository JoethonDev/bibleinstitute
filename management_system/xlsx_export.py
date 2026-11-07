"""Shared XLSX cell, worksheet-title and row-rollover helpers.

Excel worksheets hold at most 1,048,576 rows including the heading row. Any
export sheet whose data can reach that limit must continue on a new worksheet
instead of truncating or failing. The helper below owns that single rule so the
combined report and future bounded exports cannot drift apart.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable

from django.utils.translation import gettext as _

XLSX_MAX_ROWS = 1_048_576
XLSX_SHEET_TITLE_MAX = 31
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FORMULA_CELL = re.compile(r"^[\s]*[=+\-@]")


def xlsx_safe_cell(value):
    """Return an Excel-supported, formula-safe primitive for one export cell.

    Lazy translation proxies and ``Decimal`` values are rejected by openpyxl;
    strings that look like formulas are prefixed to stay inert in the sheet.
    """
    if value is None:
        return ""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    text = str(value)
    return f"'{text}" if _FORMULA_CELL.match(text) else text


def unique_sheet_title(raw: str, seen: set[str]) -> str:
    """Return a valid unique worksheet title within Excel's 31-character limit."""

    cleaned = re.sub(r"[\[\]*?:/\\]", "", str(raw or "")).strip() or "Sheet"
    cleaned = cleaned[:XLSX_SHEET_TITLE_MAX].rstrip()
    candidate = cleaned
    suffix = 2
    while candidate in seen:
        suffix_text = f" ({suffix})"
        candidate = f"{cleaned[:XLSX_SHEET_TITLE_MAX - len(suffix_text)].rstrip()}{suffix_text}"
        suffix += 1
    seen.add(candidate)
    return candidate


class RolloverSheet:
    """Append rows to one or more worksheets without crossing the row limit.

    The heading row is written on every worksheet and counts toward the limit.
    Continuation worksheets repeat the same headings, keep the caller's row
    order and receive a translated continuation title.
    """

    def __init__(
        self,
        workbook,
        title: str,
        headings: Iterable,
        seen_titles: set[str],
    ):
        self.workbook = workbook
        self.base_title = str(title)
        self.headings = [xlsx_safe_cell(value) for value in headings]
        self.seen_titles = seen_titles
        self.sheet = None
        self.part = 0
        self.rows_in_sheet = 0
        self.parts: list[str] = []
        self._open_sheet()

    def _open_sheet(self) -> None:
        self.part += 1
        if self.part == 1:
            title = self.base_title
        else:
            title = _("%(title)s (part %(number)d)") % {
                "title": self.base_title,
                "number": self.part,
            }
        self.sheet = self.workbook.create_sheet(unique_sheet_title(title, self.seen_titles))
        self.parts.append(self.sheet.title)
        self.sheet.append(self.headings)
        self.rows_in_sheet = 1

    def append(self, values: Iterable) -> None:
        if self.rows_in_sheet >= XLSX_MAX_ROWS:
            self._open_sheet()
        self.sheet.append([xlsx_safe_cell(value) for value in values])
        self.rows_in_sheet += 1
