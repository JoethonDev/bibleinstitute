"""Serialized publication of short-lived media-edge path tokens."""

from __future__ import annotations

import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .media_delivery_mode import effective_segment_delivery_mode
from .models import MediaDeliverySettings, MediaGateToken
from .media_segment_keys import normalize_segment_key

logger = logging.getLogger(__name__)

GATE_PREFIX = "/g/"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16}$")
GATE_CACHE_KEY = "media_gate:published_pair:v1"
GATE_LOCK_KEY = 0x4D45444941474154
PROVIDER_TIMEOUT_SECONDS = 10
LOAD_DATABASE_MARKER = "lms-load-disposable:v1"
PRIVATE_MEDIA_PREFIXES = (
    "applications",
    "admin-operations",
    "galleries",
    "Raw Files",
    "gallery-staging",
    "direct-upload-staging",
    "Telegram Support",
    "Telegram Broadcast",
    "app-releases",
)


class MediaGateError(Exception):
    """A bounded, secret-free media-gate/provider failure."""

    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


class MediaGateUnavailable(MediaGateError):
    """No valid committed token pair is available for media playback."""


@dataclass(frozen=True)
class RotationResult:
    status: str
    token: str = ""
    previous: str | None = None
    reason: str = ""


def period_seconds() -> int:
    raw = getattr(settings, "MEDIA_GATE_PERIOD_SECONDS", 1800)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise MediaGateUnavailable("invalid_rotation_period") from exc
    if value >= 900:
        return value
    if value != 120 or os_value("MEDIA_GATE_ALLOW_SHORT_PERIOD") != "1":
        raise MediaGateUnavailable("rotation_period_below_production_minimum")
    _require_disposable_load_database()
    return value


def os_value(name: str) -> str:
    """Read a single environment switch without copying secret settings."""
    return os.environ.get(name, "")


def _require_disposable_load_database() -> None:
    database = connection.settings_dict
    database_name = str(database.get("NAME", ""))
    database_user = str(database.get("USER", ""))
    if (
        connection.vendor != "postgresql"
        or not database_name.startswith("lms_load_")
        or not database_user.startswith("lms_load_")
        or os_value("ALLOW_DISPOSABLE_LOAD_SEED") != "1"
    ):
        raise MediaGateUnavailable("short_rotation_requires_phase6_database")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, "
                "shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            )
            row = cursor.fetchone()
    except DatabaseError as exc:
        raise MediaGateUnavailable("disposable_database_marker_unavailable") from exc
    if not row or row[0] != database_name or row[1] != database_user or row[2] != LOAD_DATABASE_MARKER:
        raise MediaGateUnavailable("phase6_database_marker_mismatch")


def rotation_schedule_seconds() -> int:
    """Return the Beat cadence; the task separately checks the DB marker."""
    return (
        30
        if os_value("MEDIA_GATE_PERIOD_SECONDS") == "120"
        and os_value("MEDIA_GATE_ALLOW_SHORT_PERIOD") == "1"
        else 300
    )


def cache_ttl_seconds(period: int | None = None) -> int:
    return max(1, min(300, (period if period is not None else period_seconds()) // 4))


def new_gate_token() -> str:
    token = secrets.token_urlsafe(12)
    if not TOKEN_RE.fullmatch(token):
        raise MediaGateError("generated_token_invalid")
    return token


def rotation_due(row: MediaGateToken | None, moment: datetime) -> bool:
    return row is None or moment >= row.current_started_at + timedelta(seconds=period_seconds())


def _valid_token(token: object) -> bool:
    return isinstance(token, str) and bool(TOKEN_RE.fullmatch(token))


def _publication(row: MediaGateToken) -> dict[str, str | None]:
    if not _valid_token(row.current_token):
        raise MediaGateUnavailable("published_current_token_invalid")
    if row.previous_token and (
        not _valid_token(row.previous_token) or row.previous_token == row.current_token
    ):
        raise MediaGateUnavailable("published_previous_token_invalid")
    if timezone.is_naive(row.current_started_at):
        raise MediaGateUnavailable("published_token_time_invalid")
    return {
        "current": row.current_token,
        "previous": row.previous_token,
        "started_at": row.current_started_at.isoformat(),
    }


def _publication_from_cache(value: object) -> tuple[str, str | None, datetime] | None:
    if not isinstance(value, dict):
        return None
    current = value.get("current")
    previous = value.get("previous")
    started_at = value.get("started_at")
    if not _valid_token(current) or (previous is not None and not _valid_token(previous)):
        return None
    if previous == current:
        return None
    try:
        parsed = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        return None
    return current, previous, parsed


def _cache_publication(
    publication: dict[str, str | None],
    period: int,
    *,
    only_if_missing: bool = False,
) -> None:
    try:
        write = cache.add if only_if_missing else cache.set
        write(GATE_CACHE_KEY, publication, timeout=cache_ttl_seconds(period))
    except Exception:
        logger.warning("media_gate_cache_write_failed")


def _read_publication(
    now: datetime | None = None,
    *,
    period: int | None = None,
) -> tuple[str, str | None, datetime]:
    moment = now or timezone.now()
    period = period if period is not None else period_seconds()
    try:
        cached = cache.get(GATE_CACHE_KEY)
    except Exception:
        cached = None
    resolved = _publication_from_cache(cached)
    if resolved and moment < resolved[2] + timedelta(seconds=period * 1.5):
        return resolved
    try:
        row = MediaGateToken.objects.filter(singleton="default").first()
    except DatabaseError as exc:
        raise MediaGateUnavailable("published_token_database_unavailable") from exc
    if row is None:
        raise MediaGateUnavailable("published_token_missing")
    publication = _publication(row)
    resolved = _publication_from_cache(publication)
    if resolved is None:
        raise MediaGateUnavailable("published_token_invalid")
    if moment >= resolved[2] + timedelta(seconds=period * 1.5):
        raise MediaGateUnavailable("published_token_expired")
    if not connection.in_atomic_block:
        _cache_publication(publication, period, only_if_missing=True)
    return resolved


def published_tokens() -> tuple[str, str | None]:
    current, previous, _started_at = _read_publication()
    return current, previous


def refresh_at(row: MediaGateToken, moment: datetime) -> datetime:
    period = period_seconds()
    return max(
        moment,
        min(
            moment + timedelta(seconds=period / 2),
            row.current_started_at + timedelta(seconds=period * 1.5),
        ),
    )


def gate_payload(now: datetime | None = None) -> dict[str, str]:
    moment = now or timezone.now()
    period = period_seconds()
    current, _previous, started_at = _read_publication(moment, period=period)
    valid_until = started_at + timedelta(seconds=period * 1.5)
    if moment >= valid_until:
        raise MediaGateUnavailable("published_token_expired")
    row = MediaGateToken(current_token=current, current_started_at=started_at)
    return {
        "token": current,
        "refresh_at": refresh_at(row, moment).isoformat(),
        "valid_until": valid_until.isoformat(),
    }


def _validated_host(host: str) -> str:
    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized or not re.fullmatch(r"[a-z0-9.-]+", normalized):
        raise MediaGateError("invalid_cloudflare_host")
    labels = normalized.split(".")
    if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
        raise MediaGateError("invalid_cloudflare_host")
    return normalized


def waf_expression(host: str, current: str, previous: str | None) -> str:
    host = _validated_host(host)
    if not _valid_token(current) or (previous and not _valid_token(previous)):
        raise MediaGateError("invalid_published_token")
    tokens = [f'starts_with(raw.http.request.uri.path, "/g/{current}/")']
    if previous:
        tokens.append(f'starts_with(raw.http.request.uri.path, "/g/{previous}/")')
    token_check = "(" + " or ".join(tokens) + ")"
    segment_check = (
        '(http.request.uri.path wildcard "*/Video%20Segments/*.ts" '
        'or http.request.uri.path wildcard "*/Audio%20Segments/*.ts")'
    )
    private_checks = []
    for prefix in PRIVATE_MEDIA_PREFIXES:
        encoded = quote(prefix, safe="").lower()
        private_checks.append(
            f'not starts_with(lower(http.request.uri.path), "/{encoded}/")'
        )
    allowed = " and ".join([token_check, segment_check, *private_checks])
    return f'(http.host eq "{host}") and not ({allowed})'


def _edge_base_url() -> str:
    raw = str(getattr(settings, "MEDIA_EDGE_BASE_URL", "") or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise MediaGateUnavailable("media_edge_base_url_invalid")
    configured_host = str(getattr(settings, "CLOUDFLARE_WAF_HOST", "") or "").strip().lower()
    if configured_host and parsed.hostname != configured_host:
        raise MediaGateUnavailable("media_edge_host_mismatch")
    return raw


def gated_segment_url(segment_key: str, *, token: str | None = None) -> str:
    normalized = normalize_segment_key(segment_key)
    current = token or published_tokens()[0]
    if not _valid_token(current):
        raise MediaGateUnavailable("published_current_token_invalid")
    return f"{_edge_base_url()}{GATE_PREFIX}{current}/{quote(normalized, safe='/')}"


def _cloudflare_settings() -> tuple[str, str, str, str, str]:
    values = (
        str(getattr(settings, "CLOUDFLARE_ZONE_ID", "") or "").strip(),
        str(getattr(settings, "CLOUDFLARE_WAF_RULESET_ID", "") or "").strip(),
        str(getattr(settings, "CLOUDFLARE_WAF_RULE_ID", "") or "").strip(),
        str(getattr(settings, "CLOUDFLARE_API_TOKEN", "") or "").strip(),
        _validated_host(getattr(settings, "CLOUDFLARE_WAF_HOST", "")),
    )
    if any(not item for item in values[:4]):
        raise MediaGateError("cloudflare_configuration_incomplete")
    return values


def _rule_url(zone_id: str, ruleset_id: str, rule_id: str) -> str:
    return (
        "https://api.cloudflare.com/client/v4/zones/"
        f"{quote(zone_id, safe='')}/rulesets/{quote(ruleset_id, safe='')}"
        f"/rules/{quote(rule_id, safe='')}"
    )


def _provider_request(method: str, url: str, token: str, **kwargs):
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=PROVIDER_TIMEOUT_SECONDS,
            **kwargs,
        )
        body = response.json()
    except requests.RequestException as exc:
        raise MediaGateError("cloudflare_network_error") from exc
    except ValueError as exc:
        raise MediaGateError("cloudflare_invalid_response") from exc
    if not response.ok or not isinstance(body, dict) or body.get("success") is not True:
        errors = body.get("errors", []) if isinstance(body, dict) else []
        code = errors[0].get("code") if errors and isinstance(errors[0], dict) else None
        reason = f"cloudflare_http_{response.status_code}"
        if code is not None:
            reason += f"_code_{code}"
        raise MediaGateError(reason, status=response.status_code)
    return body.get("result")


def push_waf_rule(expression: str) -> None:
    zone_id, ruleset_id, rule_id, api_token, _host = _cloudflare_settings()
    url = _rule_url(zone_id, ruleset_id, rule_id)
    rule = _provider_request("GET", url, api_token)
    if not isinstance(rule, dict) or rule.get("id") != rule_id:
        raise MediaGateError("cloudflare_rule_not_found")
    payload = {
        key: rule[key]
        for key in (
            "action",
            "description",
            "enabled",
            "expression",
            "ref",
            "action_parameters",
            "ratelimit",
            "logging",
        )
        if key in rule
    }
    if not payload.get("action") or "enabled" not in payload:
        raise MediaGateError("cloudflare_rule_shape_invalid")
    payload["expression"] = expression
    _provider_request("PATCH", url, api_token, json=payload)


def fetch_rule_expression() -> str:
    zone_id, ruleset_id, rule_id, api_token, _host = _cloudflare_settings()
    rule = _provider_request("GET", _rule_url(zone_id, ruleset_id, rule_id), api_token)
    expression = rule.get("expression") if isinstance(rule, dict) else None
    if not isinstance(expression, str):
        raise MediaGateError("cloudflare_rule_shape_invalid")
    return expression


def _lock_gate_row() -> None:
    if connection.vendor != "postgresql":
        raise MediaGateUnavailable("postgresql_required")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [GATE_LOCK_KEY])


def _edge_mode_enabled() -> bool:
    return (
        effective_segment_delivery_mode()
        == MediaDeliverySettings.SegmentDeliveryMode.EDGE_GATE
    )


def _save_attempt_error(row: MediaGateToken | None, moment: datetime, code: str) -> None:
    if row is None:
        return
    row.last_attempt_at = moment
    row.last_error = code[:500]
    row.save(update_fields=("last_attempt_at", "last_error", "updated_at"))


def _reconcile_committed_pair() -> None:
    try:
        with transaction.atomic():
            _lock_gate_row()
            row = MediaGateToken.objects.select_for_update().filter(singleton="default").first()
            if row is None:
                return
            _zone, _ruleset, _rule, _api_token, host = _cloudflare_settings()
            push_waf_rule(waf_expression(host, row.current_token, row.previous_token))
            publication = _publication(row)
            period = period_seconds()
            transaction.on_commit(lambda: _cache_publication(publication, period))
    except (DatabaseError, MediaGateError, MediaGateUnavailable):
        logger.exception("media_gate_committed_pair_reconcile_failed")


def rotate_token(now: datetime | None = None) -> RotationResult:
    moment = now or timezone.now()
    if timezone.is_naive(moment):
        return RotationResult("failed", reason="rotation_time_must_be_aware")
    try:
        if not _edge_mode_enabled():
            return RotationResult("skipped", reason="edge_gate_not_enabled")
        period = period_seconds()
    except (DatabaseError, MediaGateError, MediaGateUnavailable, ImproperlyConfigured) as exc:
        reason = getattr(exc, "code", "gate_configuration_invalid")
        return RotationResult("failed", reason=reason)

    provider_pushed = False
    try:
        with transaction.atomic():
            _lock_gate_row()
            row = MediaGateToken.objects.select_for_update().filter(singleton="default").first()
            if row is not None and moment < row.current_started_at + timedelta(seconds=period):
                return RotationResult("skipped", row.current_token, row.previous_token, "not_due")
            current = new_gate_token()
            previous = row.current_token if row else None
            try:
                _zone, _ruleset, _rule, _api_token, host = _cloudflare_settings()
                expression = waf_expression(host, current, previous)
                push_waf_rule(expression)
                provider_pushed = True
            except (MediaGateError, MediaGateUnavailable) as exc:
                _save_attempt_error(row, moment, exc.code)
                logger.error("media_gate_rotation_failed code=%s", exc.code)
                return RotationResult("failed", reason=exc.code)

            if row is None:
                row = MediaGateToken.objects.create(
                    singleton="default",
                    current_token=current,
                    current_started_at=moment,
                    previous_token=previous,
                    last_attempt_at=moment,
                )
            else:
                row.current_token = current
                row.current_started_at = moment
                row.previous_token = previous
                row.last_attempt_at = moment
                row.last_error = ""
                row.save(update_fields=(
                    "current_token",
                    "current_started_at",
                    "previous_token",
                    "last_attempt_at",
                    "last_error",
                    "updated_at",
                ))
            publication = _publication(row)
            transaction.on_commit(lambda: _cache_publication(publication, period))
            return RotationResult("rotated", current, previous)
    except (DatabaseError, MediaGateError, MediaGateUnavailable) as exc:
        if provider_pushed:
            _reconcile_committed_pair()
        reason = getattr(exc, "code", "database_error")
        logger.error("media_gate_rotation_publication_failed code=%s", reason)
        return RotationResult("failed", reason=reason)


def repair_waf_from_committed_pair() -> bool:
    try:
        with transaction.atomic():
            _lock_gate_row()
            row = MediaGateToken.objects.select_for_update().filter(singleton="default").first()
            if row is None:
                raise MediaGateUnavailable("published_token_missing")
            _zone, _ruleset, _rule, _api_token, host = _cloudflare_settings()
            push_waf_rule(waf_expression(host, row.current_token, row.previous_token))
            publication = _publication(row)
            period = period_seconds()
            transaction.on_commit(lambda: _cache_publication(publication, period))
        return True
    except (DatabaseError, MediaGateError, MediaGateUnavailable) as exc:
        logger.error(
            "media_gate_waf_repair_failed code=%s",
            getattr(exc, "code", "database_error"),
        )
        return False


def stale_publication(now: datetime | None = None) -> bool:
    moment = now or timezone.now()
    try:
        row = MediaGateToken.objects.filter(singleton="default").only("current_started_at").first()
        return row is None or moment >= row.current_started_at + timedelta(seconds=period_seconds() * 1.5)
    except DatabaseError as exc:
        raise MediaGateUnavailable("published_token_database_unavailable") from exc


def rule_matches_publication() -> bool:
    """Compare the provider rule with the committed database pair.

    The short-lived publication cache may lag one rotation behind, so drift
    detection reads the committed row directly. This stays a single row read.
    """
    try:
        row = MediaGateToken.objects.filter(singleton="default").first()
    except DatabaseError as exc:
        raise MediaGateUnavailable("published_token_database_unavailable") from exc
    if row is None:
        raise MediaGateUnavailable("published_token_missing")
    _publication(row)
    _zone, _ruleset, _rule, _api_token, host = _cloudflare_settings()
    return fetch_rule_expression() == waf_expression(
        host, row.current_token, row.previous_token
    )
