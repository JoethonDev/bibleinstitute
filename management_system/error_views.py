"""Neutral browser error responses that do not inherit the application shell."""

from __future__ import annotations

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _


def _is_json_request(request: HttpRequest) -> bool:
    return request.path_info.startswith("/api/") or "application/json" in request.headers.get("Accept", "")


def _error_response(request: HttpRequest, template_name: str, status: int, code: str, message: str) -> HttpResponse:
    if _is_json_request(request):
        response = JsonResponse({"error": {"code": code, "message": message}}, status=status)
    else:
        response = render(request, template_name, status=status)
    response["Cache-Control"] = "no-store"
    return response


def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return _error_response(request, "400.html", 400, "bad_request", _("Bad request."))


def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return _error_response(request, "403.html", 403, "forbidden", _("Access denied."))


def server_error(request: HttpRequest) -> HttpResponse:
    return _error_response(request, "500.html", 500, "server_error", _("Server error."))
