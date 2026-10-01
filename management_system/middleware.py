"""Request middleware and structured, privacy-safe request diagnostics."""

from __future__ import annotations

from contextvars import ContextVar
from logging import Filter, Formatter, getLogger
from re import fullmatch
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.urls import resolve, Resolver404
from django.utils import translation
from django.utils.translation import gettext as _
from django.utils.cache import patch_vary_headers

from .utils.localization import normalize_language

logger = getLogger(__name__)
_request_context: ContextVar[dict] = ContextVar("lms_request_context", default={})


def _mobile_cors_origin_allowed(origin):
    if origin in settings.CORS_ALLOWED_ORIGINS:
        return True
    if settings.CORS_ALLOW_ALL_ORIGINS:
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    if not settings.ALLOW_LOCALHOST_ORIGINS:
        return False
    parsed = urlsplit(origin)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and not parsed.username
        and not parsed.password
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


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


# Request-context attributes rendered in a fixed order; all other module
# ``extra`` fields are rendered alphabetically afterwards.
_CONTEXT_FIELDS = (
    ("request_id", "request_id"),
    ("route", "route"),
    ("method", "method"),
    ("request_path", "request_path"),
    ("query_keys", "query_keys"),
    ("status_code", "status"),
    ("duration_ms", "duration_ms"),
    ("user_id", "user_id"),
    ("username", "username"),
    ("role_id", "role_id"),
    ("remote_ip", "ip"),
    ("device", "device"),
    ("browser", "browser"),
    ("os", "os"),
)
_CONTEXT_ATTRIBUTE_NAMES = frozenset(name for name, _label in _CONTEXT_FIELDS)

# Standard ``logging.LogRecord`` attributes that must never be rendered as
# event payload fields.
_LOG_RECORD_BUILTINS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


def _short_logger_name(name: str) -> str:
    prefix = "management_system."
    return name[len(prefix):] if name.startswith(prefix) else name


def _render_value(value) -> str:
    return " ".join(str(value).split())


def _is_empty(value) -> bool:
    return value is None or value == "" or value == "-"


def _slow_request_ms() -> float:
    try:
        return float(getattr(settings, "LOG_SLOW_REQUEST_MS", 2000))
    except (TypeError, ValueError):
        return 2000.0


class KeyValueFormatter(Formatter):
    """Render one compact ``key=value`` line per log record.

    Request-context fields are omitted when absent so lines stay short and
    scannable; module-provided ``extra`` fields (event payloads) follow.
    Exceptions keep their traceback on the same record.
    """

    default_time_format = "%Y-%m-%d %H:%M:%S"

    def format(self, record) -> str:
        parts = [
            f"time={self.formatTime(record, self.default_time_format)}",
            f"level={record.levelname}",
            f"logger={_short_logger_name(record.name)}",
        ]
        event = getattr(record, "event", None)
        if not _is_empty(event):
            parts.append(f"event={_render_value(event)}")
        for attribute, label in _CONTEXT_FIELDS:
            value = getattr(record, attribute, None)
            if not _is_empty(value):
                parts.append(f"{label}={_render_value(value)}")
        for key in sorted(record.__dict__):
            if (
                key in _LOG_RECORD_BUILTINS
                or key == "event"
                or key in _CONTEXT_ATTRIBUTE_NAMES
                or key.startswith("_")
            ):
                continue
            value = record.__dict__[key]
            if not _is_empty(value):
                parts.append(f"{key}={_render_value(value)}")
        message = record.getMessage()
        if message:
            parts.append(f"msg={_render_value(message)}")
        line = " ".join(parts)
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                line = f"{line}\n{record.exc_text}"
        return line


def _log_request_outcome(context: dict, response, log) -> None:
    """Choose the one lifecycle event a finished request deserves.

    Healthy, fast requests are DEBUG only; slow responses and failures are
    the lines operators need to see.
    """
    status = response.status_code
    route = context.get("route") or "unmatched"
    try:
        duration_ms = float(context.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration_ms = 0.0
    if status >= 500:
        context["event"] = "http_request_error"
        log.error("server error")
        return
    if status >= 400:
        if status == 404 and route == "unmatched":
            context["event"] = "http_request_not_found"
            log.info("not found")
        else:
            context["event"] = "http_request_failed"
            log.warning("request failed")
        return
    if duration_ms >= _slow_request_ms():
        context["event"] = "http_request_slow"
        log.info("slow request")
        return
    context["event"] = "http_request_completed"
    log.debug("request completed")


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
        }.items():
            setattr(record, name, values.get(name, default))
        # Module code may attach ``extra={"event": ...}``; only fall back to
        # the request lifecycle event when the record carries none.
        if not getattr(record, "event", None):
            record.event = values.get("event") or "application_log"
        return True


class MobileApiCorsMiddleware:
    """Allow configured browser origins to call the bearer-authenticated API."""

    API_PREFIX = "/api/mobile/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin", "")
        is_mobile_api = request.path_info.startswith(self.API_PREFIX)
        allowed = bool(origin and is_mobile_api and _mobile_cors_origin_allowed(origin))
        if allowed and request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response["Access-Control-Allow-Headers"] = (
                "Accept, Accept-Language, Authorization, Content-Type, "
                "X-CSRFToken, X-Installation-ID, X-Request-ID"
            )
            response["Access-Control-Expose-Headers"] = "Content-Language, X-Request-ID"
            response["Access-Control-Max-Age"] = "600"
            patch_vary_headers(response, ["Origin"])
        return response


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
            logger.debug("request started")
            response = self.get_response(request)
            context["status_code"] = response.status_code
            return response
        except Exception as exc:
            context["status_code"] = 500
            context["event"] = "http_request_exception"
            logger.exception("unhandled exception", extra={"exception_type": type(exc).__name__})
            if request.path_info.startswith("/api/mobile/"):
                language = normalize_language(request)
                with translation.override(language):
                    message = str(_("The server could not complete the request."))
                response = JsonResponse(
                    {"language": language, "error": {"code": "server_error", "message": message}},
                    status=500,
                )
                response["X-Request-ID"] = request_id
                return response
            raise
        finally:
            observed_user = getattr(request, "user", None)
            if observed_user is not None and getattr(observed_user, "is_authenticated", False):
                context["user_id"] = observed_user.pk
                context["username"] = _clean(observed_user.username)
                context["role_id"] = observed_user.role_id
            context["duration_ms"] = round((monotonic() - started) * 1000, 2)
            if response is not None:
                _log_request_outcome(context, response, logger)
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
                    logger.debug("session expiry refreshed")
                except Exception as e:
                    logger.exception(
                        "session expiry refresh failed exception_type=%s",
                        type(e).__name__,
                    )

        response = self.get_response(request)

        # Code to be executed for each request/response after
        # the view is called.

        return response
