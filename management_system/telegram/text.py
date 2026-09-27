"""Sanitize untrusted text received from Telegram users before persistence."""

from __future__ import annotations

import re


_LINK_PATTERN = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://|mailto:|www\.|(?:[\w-]+\.)+[\w]{2,}(?:/[^\s]*)?|t\.me/)[^\s]*"
)


def strip_links(value: str) -> str:
    """Remove URL-like text without retaining it in Telegram chat records."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[ \t]{2,}", " ", _LINK_PATTERN.sub("", value)).strip()


def strip_telegram_links(value: str, entities: object = None) -> str:
    """Remove visible URL entities and hidden text-link labels from Telegram input."""
    if not isinstance(value, str):
        return ""
    if not isinstance(entities, list) or not entities:
        return strip_links(value)

    utf16_offsets = [0]
    for character in value:
        utf16_offsets.append(utf16_offsets[-1] + (2 if ord(character) > 0xFFFF else 1))
    offset_indexes = {offset: index for index, offset in enumerate(utf16_offsets)}
    spans = []
    for entity in entities:
        if not isinstance(entity, dict) or entity.get("type") not in {"url", "text_link"}:
            continue
        raw_offset = entity.get("offset")
        raw_length = entity.get("length")
        if not isinstance(raw_offset, (int, str)) or not isinstance(raw_length, (int, str)):
            continue
        try:
            start = int(raw_offset)
            end = start + int(raw_length)
        except ValueError:
            continue
        if start in offset_indexes and end in offset_indexes and end > start:
            spans.append((offset_indexes[start], offset_indexes[end]))

    for start, end in sorted(spans, reverse=True):
        value = value[:start] + value[end:]
    return strip_links(value)
