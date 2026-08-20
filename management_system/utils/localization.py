"""Small language-negotiation helpers shared by mobile request boundaries."""

from __future__ import annotations


SUPPORTED_LANGUAGES = frozenset({"ar", "en"})


def normalize_language(request) -> str:
    value = request.headers.get("Accept-Language") or "en"
    candidates = []
    for position, candidate in enumerate(value.lower().split(",")):
        parts = [part.strip() for part in candidate.split(";")]
        language = parts[0].split("-", 1)[0]
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0 and language in SUPPORTED_LANGUAGES:
            candidates.append((-quality, position, language))
    if candidates:
        candidates.sort()
        return candidates[0][2]
    return "en"
