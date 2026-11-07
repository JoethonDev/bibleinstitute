"""Neutral browser error responses that do not inherit the application shell.

Mobile API errors use the shared JSON envelope (language field + translated
message) so native clients always receive one stable shape; browser responses
keep rendering the neutral templates and non-mobile JSON API errors keep the
legacy shape.
"""

from __future__ import annotations

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import translation
from django.utils.translation import gettext as _

from .mobile_http import json_api_response
from .utils.localization import normalize_language


def _is_mobile_api(request: HttpRequest) -> bool:
    return request.path_info.startswith("/api/mobile/")


def _is_json_request(request: HttpRequest) -> bool:
    return request.path_info.startswith("/api/") or "application/json" in request.headers.get("Accept", "")


def _error_response(request: HttpRequest, template_name: str, status: int, code: str, message: str) -> HttpResponse:
    if _is_mobile_api(request):
        with translation.override(normalize_language(request)):
            payload_message = str(_(message))
        response = json_api_response(
            request,
            {"error": {"code": code, "message": payload_message}},
            status=status,
        )
    elif _is_json_request(request):
        response = JsonResponse({"error": {"code": code, "message": str(_(message))}}, status=status)
    else:
        response = render(request, template_name, status=status)
    response["Cache-Control"] = "no-store"
    return response


def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return _error_response(request, "400.html", 400, "bad_request", "Bad request.")


def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return _error_response(request, "403.html", 403, "forbidden", "Access denied.")


def server_error(request: HttpRequest) -> HttpResponse:
    return _error_response(request, "500.html", 500, "server_error", "Server error.")
