"""Shared JSON envelope for the role-neutral mobile API.

Leaf module: it depends only on Django and the localization helper, so every
mobile module can import it statically without cycles.
"""

from __future__ import annotations

from django.http import JsonResponse

from .utils.localization import normalize_language


def json_api_response(request, payload: dict, status: int = 200) -> JsonResponse:
    body = dict(payload)
    body.setdefault("language", normalize_language(request))
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "private, no-store"
    return response
