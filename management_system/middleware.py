"""Request middleware and structured, privacy-safe request diagnostics."""

from __future__ import annotations

from contextvars import ContextVar
from logging import Filter, getLogger
from re import fullmatch
from time import monotonic
from uuid import uuid4

from django.urls import resolve, Resolver404


logger = getLogger(__name__)
_request_context: ContextVar[dict] = ContextVar("lms_request_context", default={})


def _clean(value, *, limit=128):
    if value is None:
        return "-"
    return " ".join(str(value).split())[:limit] or "-"


def _user_agent_details(user_agent):
    value = _clean(user_agent, limit=512).lower()
    if any(marker in value for marker in ("bot", "crawler", "spider", "slurp")):
        device = "bot"
    elif "ipad" in value or "tablet" in value:
        device = "tablet"
    elif any(marker in value for marker in ("mobile", "iphone", "android")):
        device = "mobile"
    else:
        device = "desktop"

    if "edg/" in value:
        browser = "edge"
    elif "opr/" in value or "opera" in value:
        browser = "opera"
    elif "chrome/" in value or "crios/" in value:
        browser = "chrome"
    elif "firefox/" in value or "fxios/" in value:
        browser = "firefox"
    elif "safari/" in value and "chrome/" not in value:
        browser = "safari"
    elif "msie" in value or "trident/" in value:
        browser = "internet_explorer"
    else:
        browser = "unknown"

    if "android" in value:
        operating_system = "android"
    elif "iphone" in value or "ipad" in value or "ios" in value:
        operating_system = "ios"
    elif "windows" in value:
        operating_system = "windows"
    elif "mac os" in value or "macintosh" in value:
        operating_system = "macos"
    elif "linux" in value:
        operating_system = "linux"
    else:
        operating_system = "unknown"
    return device, browser, operating_system


def _route_label(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return "unmatched"
    return _clean(match.url_name or match.route or "unnamed", limit=160)


class RequestContextFilter(Filter):
    """Add fixed searchable fields to every application/Django log record."""

    def filter(self, record):
        values = _request_context.get()
        for name, default in {
            "request_id": "-",
            "route": "-",
            "method": "-",
            "request_path": "-",
            "query_keys": "-",
            "status_code": "-",
            "duration_ms": "-",
            "user_id": "-",
            "username": "-",
            "role_id": "-",
            "remote_ip": "-",
            "device": "-",
            "browser": "-",
            "os": "-",
            "event": "application_log",
        }.items():
            setattr(record, name, values.get(name, default))
        return True


class RequestObservabilityMiddleware:
    """Log one searchable lifecycle record for every HTTP request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if fullmatch(r"[A-Za-z0-9._:-]{1,96}", supplied_request_id)
            else ""
        )
        if not request_id:
            request_id = uuid4().hex
        device, browser, operating_system = _user_agent_details(
            request.headers.get("User-Agent", "")
        )
        user = getattr(request, "user", None)
        context = {
            "request_id": request_id,
            "route": _route_label(request),
            "method": _clean(request.method, limit=16),
            # Never log the raw path: scan tokens and query strings can be secrets.
            "request_path": f"segments:{len([part for part in request.path_info.split('/') if part])}",
            "query_keys": ",".join(sorted(request.GET.keys())) or "-",
            "user_id": getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else "-",
            "username": _clean(getattr(user, "username", None)) if getattr(user, "is_authenticated", False) else "-",
            "role_id": getattr(user, "role_id", None) if getattr(user, "is_authenticated", False) else "-",
            "remote_ip": _clean(request.META.get("REMOTE_ADDR"), limit=64),
            "device": device,
            "browser": browser,
            "os": operating_system,
        }
        token = _request_context.set(context)
        started = monotonic()
        response = None
        try:
            context["event"] = "http_request_started"
            logger.info("event=http_request_started")
            response = self.get_response(request)
            context["status_code"] = response.status_code
            return response
        except Exception as exc:
            context["status_code"] = 500
            context["event"] = "http_request_exception"
            logger.exception(
                "event=http_request_exception exception_type=%s",
                type(exc).__name__,
            )
            raise
        finally:
            context["duration_ms"] = round((monotonic() - started) * 1000, 2)
            if response is not None and response.status_code < 400:
                context["event"] = "http_request_completed"
                logger.info("event=http_request_completed")
            elif response is not None:
                context["event"] = "http_request_failed"
                log_method = logger.error if response.status_code >= 500 else logger.warning
                log_method(
                    "event=http_request_failed reason=%s",
                    "server_error" if response.status_code >= 500 else "client_or_auth_error",
                )
            response_id = getattr(response, "__setitem__", None)
            if response_id is not None:
                response["X-Request-ID"] = request_id
            _request_context.reset(token)

class SessionExpiryUpdate:
    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.
        if request.user.is_authenticated:
            expiry_time = request.session.get_expiry_age()
            if expiry_time > 0:
                try:
                    request.session.set_expiry(request.session.get_session_cookie_age())
                    logger.info("event=session_expiry_refreshed")
                except Exception as e:
                    logger.exception(
                        "event=session_expiry_refresh_failed exception_type=%s",
                        type(e).__name__,
                    )

        response = self.get_response(request)

        # Code to be executed for each request/response after
        # the view is called.

        return response
