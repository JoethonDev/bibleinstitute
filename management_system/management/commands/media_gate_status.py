"""Read-only operator report for the Cloudflare edge-gate publication state.

The command reads the committed ``MediaGateToken`` row and local settings only.
It never creates the token row, never contacts Cloudflare and never mutates
database state. The expected edge gate expression is printed with token
segments masked so a terminal or CI log never receives a complete path token.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone
from django.utils.translation import gettext as _

from management_system.media_delivery_mode import effective_segment_delivery_mode
from management_system.media_gate import (
    TOKEN_RE,
    MediaGateError,
    MediaGateUnavailable,
    cache_ttl_seconds,
    period_seconds,
    transform_expression,
)
from management_system.models import MediaDeliverySettings, MediaGateToken

REQUIRED_ENVIRONMENT_NAMES = (
    "MEDIA_EDGE_BASE_URL",
    "CLOUDFLARE_ZONE_ID",
    "CLOUDFLARE_WAF_HOST",
    "CLOUDFLARE_API_TOKEN",
)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    return moment.isoformat()


def _mask_token(token: str | None) -> str:
    if not token:
        return ""
    return f"{token[:4]}..."


def _mask_expression(expression: str, tokens: tuple[str | None, ...]) -> str:
    masked = expression
    for token in tokens:
        if token:
            masked = masked.replace(token, _mask_token(token))
    return masked


def build_gate_report() -> dict[str, object]:
    """Collect the read-only gate status without creating or pushing state."""
    mode = effective_segment_delivery_mode()
    missing_settings = [
        name for name in REQUIRED_ENVIRONMENT_NAMES if not getattr(settings, name, "")
    ]

    row: MediaGateToken | None = None
    gate_error = ""
    try:
        row = MediaGateToken.load()
    except DatabaseError:
        gate_error = "database_unavailable"

    if row is None:
        gate_state = gate_error or "not_published"
    else:
        gate_state = "published"

    current = row.current_token if row is not None else ""
    previous = (row.previous_token or None) if row is not None else None
    current_valid = bool(current and TOKEN_RE.fullmatch(current))
    previous_valid = previous is None or bool(
        TOKEN_RE.fullmatch(previous) and previous != current
    )

    period: int | None = None
    period_error = ""
    try:
        period = period_seconds()
    except MediaGateUnavailable as exc:
        period_error = exc.code

    expression_available = False
    expression_text = ""
    expression_error = ""
    if row is not None:
        if current_valid and previous_valid:
            try:
                expression = transform_expression(
                    str(getattr(settings, "CLOUDFLARE_WAF_HOST", "") or ""),
                    current,
                    previous,
                )
            except MediaGateError as exc:
                expression_error = exc.code
            else:
                expression_available = True
                expression_text = _mask_expression(expression, (current, previous))
        else:
            expression_error = "published_token_invalid"

    due_at = (
        row.current_started_at + timedelta(seconds=period)
        if row is not None and period is not None
        else None
    )
    cache_ttl = cache_ttl_seconds(period) if period is not None else None
    ready = bool(
        row is not None
        and not missing_settings
        and current_valid
        and previous_valid
        and period is not None
        and expression_available
    )

    return {
        "effective_delivery_mode": mode,
        "edge_gate_enabled": mode
        == MediaDeliverySettings.SegmentDeliveryMode.EDGE_GATE,
        "gate_state": gate_state,
        "gate_row_exists": row is not None,
        "current_token_set": bool(current),
        "current_token_valid": current_valid,
        "current_token_masked": _mask_token(current) or None,
        "current_token_length": len(current) if current else None,
        "previous_token_set": previous is not None,
        "previous_token_valid": previous_valid,
        "previous_token_masked": _mask_token(previous) or None,
        "previous_token_length": len(previous) if previous else None,
        "current_started_at": (
            _iso(row.current_started_at) if row is not None else None
        ),
        "rotation_due_at": _iso(due_at),
        "rotation_period_seconds": period,
        "period_error": period_error,
        "cache_ttl_seconds": cache_ttl,
        "missing_settings": missing_settings,
        "gate_expression_available": expression_available,
        "gate_expression": expression_text,
        "gate_expression_error": expression_error,        "last_attempt_at": _iso(row.last_attempt_at) if row is not None else None,
        "last_error": row.last_error if row is not None else "",
        "ready": ready,
    }


def _report_lines(report: dict[str, object]) -> list[str]:
    lines = [_("Media gate status")]
    lines.append(
        _("Effective delivery mode: %(mode)s")
        % {"mode": report["effective_delivery_mode"]}
    )
    gate_state = report["gate_state"]
    if gate_state == "published":
        lines.append(_("Gate row: published"))
    elif gate_state == "database_unavailable":
        lines.append(_("Gate row: unavailable (database error)"))
    else:
        lines.append(_("Gate row: not published"))

    if report["current_token_set"]:
        lines.append(
            _("Current token: %(prefix)s (length %(length)s)")
            % {
                "prefix": report["current_token_masked"],
                "length": report["current_token_length"],
            }
        )
    else:
        lines.append(_("Current token: not set"))
    if report["previous_token_set"]:
        lines.append(
            _("Previous token: %(prefix)s (length %(length)s)")
            % {
                "prefix": report["previous_token_masked"],
                "length": report["previous_token_length"],
            }
        )
    else:
        lines.append(_("Previous token: not set"))

    for label, key in (
        (_("Current token started at"), "current_started_at"),
        (_("Rotation due at"), "rotation_due_at"),
        (_("Last attempt at"), "last_attempt_at"),
    ):
        value = report[key]
        lines.append("%s: %s" % (label, value if value else _("not available")))

    if report["rotation_period_seconds"] is None:
        lines.append(
            _("Rotation period: invalid (%(reason)s)")
            % {"reason": report["period_error"]}
        )
    else:
        lines.append(
            _("Rotation period: %(seconds)s seconds")
            % {"seconds": report["rotation_period_seconds"]}
        )
    if report["cache_ttl_seconds"] is None:
        lines.append(_("Media gate cache TTL: not available"))
    else:
        lines.append(
            _("Media gate cache TTL: %(seconds)s seconds")
            % {"seconds": report["cache_ttl_seconds"]}
        )

    missing = report["missing_settings"]
    lines.append(
        _("Missing settings: %(names)s")
        % {"names": ", ".join(missing) if missing else _("none")}
    )
    if report["gate_expression_available"]:
        lines.append(_("Expected gate expression (token segments masked):"))
        lines.append(str(report["gate_expression"]))
    else:
        reason = report["gate_expression_error"] or _("not published")
        lines.append(
            _("Expected gate expression: not computable (%(reason)s)")
            % {"reason": reason}
        )
    lines.append(
        _("Last error: %(error)s")
        % {"error": report["last_error"] or _("none")}
    )
    lines.append(
        _("Ready: %(state)s")
        % {"state": _("yes") if report["ready"] else _("no")}
    )
    return lines


class Command(BaseCommand):
    help = _("Report the read-only status of the Cloudflare edge gate.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help=_("Write the status as JSON for machine consumption."),
        )
        parser.add_argument(
            "--require-ready",
            action="store_true",
            help=_("Exit non-zero when the edge gate is not ready."),
        )

    def handle(self, *args, **options):
        report = build_gate_report()
        if options["json"]:
            self.stdout.write(
                json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)
            )
        else:
            for line in _report_lines(report):
                self.stdout.write(line)
        if options["require_ready"] and not report["ready"]:
            raise CommandError(_("Media gate is not ready."))
