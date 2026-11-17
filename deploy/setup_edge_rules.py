#!/usr/bin/env python3
"""Create the LMS media-edge rules (WAF deny, gate rewrite, cache, rate limit).

Run against the zone named by ``CLOUDFLARE_WAF_HOST``; every rule is scoped to
that host so the main site is untouched. This is an operator tool: it never
prints the API token, and the only token it writes is the short-lived media
token inside the WAF expression held by Cloudflare.

Free-plan note: ``regex_replace`` is not available below Business, so the gate
prefix is stripped with ``substring(list, 19)`` - ``/g/`` plus a 16 character
token. Keep ``media_gate.TOKEN_RE`` and this offset in step if the token length
ever changes.

Usage:
    python deploy/setup_edge_rules.py --apply-waf
    python deploy/setup_edge_rules.py --apply-rewrite
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

API = "https://api.cloudflare.com/client/v4"
GATE_PREFIX_LENGTH = 19  # len("/g/") + 16 character token
TOKEN_MASK = re.compile(r"/g/[A-Za-z0-9_-]{16}/")
WAF_RULE_REF = "lms_media_gate"
REWRITE_RULE_REF = "lms_media_gate_rewrite"

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _api_token() -> str:
    value = (os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()
    if not value:
        sys.exit("CLOUDFLARE_API_TOKEN is not set")
    return value


def _call(method: str, path: str, **kwargs) -> dict:
    response = requests.request(
        method,
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {_api_token()}",
            "Content-Type": "application/json",
        },
        timeout=20,
        **kwargs,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"success": False, "errors": [{"message": f"HTTP {response.status_code}"}]}
    payload["_status"] = response.status_code
    return payload


def _errors(payload: dict) -> str:
    parts = []
    for item in payload.get("errors") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("message") or item.get("code") or "error"))
        else:
            parts.append(str(item))
    return "; ".join(parts)[:300] or f"HTTP {payload.get('_status')}"


def _edge_host() -> str:
    host = (os.getenv("CLOUDFLARE_WAF_HOST") or "").strip().lower()
    if not host or "." not in host:
        sys.exit("CLOUDFLARE_WAF_HOST must name the media edge host")
    return host


def _zone_id(zone_name: str) -> str:
    payload = _call("GET", "/zones", params={"name": zone_name})
    rows = payload.get("result") or []
    if not rows:
        sys.exit(f"zone not readable: {_errors(payload)}")
    return rows[0]["id"]


def _entrypoint(zone_id: str, phase: str) -> dict | None:
    payload = _call("GET", f"/zones/{zone_id}/rulesets/phases/{phase}/entrypoint")
    return payload.get("result") if payload.get("success") else None


def _zone_name_from_host(host: str) -> str:
    """Return the registrable zone for the edge host from configuration."""

    configured = (os.getenv("DJANGO_ALLOWED_HOSTS") or "").split(",")[0].strip().lstrip(".")
    if configured and "." in configured:
        return configured
    return ".".join(host.split(".")[-2:])


def _waf_rule(host: str, placeholder: str) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")
    import django

    django.setup()
    from management_system.media_gate import waf_expression

    return {
        "action": "block",
        "description": "LMS media gate: only current or previous token segment paths",
        "enabled": True,
        "ref": WAF_RULE_REF,
        "expression": waf_expression(host, placeholder, None),
    }


def apply_waf(zone_id: str, host: str, placeholder: str) -> int:
    entry = _entrypoint(zone_id, "http_request_firewall_custom")
    rule = _waf_rule(host, placeholder)
    kept = [
        item for item in ((entry or {}).get("rules") or [])
        if item.get("ref") != WAF_RULE_REF
    ]
    payload = _call(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint",
        json={"rules": [*kept, rule]},
    )
    if not payload.get("success"):
        print(f"waf_apply: FAILED {_errors(payload)}")
        return 1
    result = payload.get("result") or {}
    created = [item for item in (result.get("rules") or []) if item.get("ref") == WAF_RULE_REF]
    print(f"waf_apply: OK ruleset_id={result.get('id')} rule_id={(created[0] or {}).get('id') if created else ''}")
    return 0


def apply_rewrite(zone_id: str, host: str) -> int:
    entry = _entrypoint(zone_id, "http_request_transform")
    rule = {
        "action": "rewrite",
        "description": "LMS media gate: strip the gate prefix",
        "enabled": True,
        "ref": REWRITE_RULE_REF,
        "expression": f'(http.host eq "{host}" and starts_with(http.request.uri.path, "/g/"))',
        "action_parameters": {
            "uri": {
                "path": {
                    "expression": f"substring(http.request.uri.path, {GATE_PREFIX_LENGTH})"
                }
            }
        },
    }
    kept = [
        item for item in ((entry or {}).get("rules") or [])
        if item.get("ref") != REWRITE_RULE_REF
    ]
    payload = _call(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_request_transform/entrypoint",
        json={"rules": [*kept, rule]},
    )
    if not payload.get("success"):
        print(f"rewrite_apply: FAILED {_errors(payload)}")
        return 1
    print(f"rewrite_apply: OK ruleset_id={(payload.get('result') or {}).get('id')}")
    return 0


def apply_cache(zone_id: str, host: str) -> int:
    """Cache gate segment responses at the edge instead of re-fetching R2."""

    entry = _entrypoint(zone_id, "http_request_cache_settings")
    rule = {
        "action": "set_cache_settings",
        "description": "LMS media gate: cache gated segments at the edge",
        "enabled": True,
        "ref": "lms_media_gate_cache",
        "expression": (
            f'(http.host eq "{host}" and starts_with(raw.http.request.uri.path, "/g/"))'
        ),
        "action_parameters": {
            "cache": True,
            "edge_ttl": {"mode": "override_origin", "default": 86400},
        },
    }
    kept = [
        item for item in ((entry or {}).get("rules") or [])
        if item.get("ref") != "lms_media_gate_cache"
    ]
    payload = _call(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_request_cache_settings/entrypoint",
        json={"rules": [*kept, rule]},
    )
    if not payload.get("success"):
        print(f"cache_apply: FAILED {_errors(payload)}")
        return 1
    print(f"cache_apply: OK ruleset_id={(payload.get('result') or {}).get('id')}")
    return 0


def apply_rate_limit(zone_id: str, host: str) -> int:
    """Bound one client to 300 segment requests per 10 seconds, block 10s."""

    entry = _entrypoint(zone_id, "http_ratelimit")
    rule = {
        "action": "block",
        "description": "LMS media gate: per-IP segment request ceiling",
        "enabled": True,
        "ref": "lms_media_gate_ratelimit",
        "expression": (
            f'(http.host eq "{host}" and starts_with(raw.http.request.uri.path, "/g/"))'
        ),
        "ratelimit": {
            # Free plan counts per colocation, so cf.colo.id is mandatory; the
            # effective ceiling is therefore per IP per colo.
            "characteristics": ["cf.colo.id", "ip.src"],
            "period": 10,
            "requests_per_period": 300,
            "mitigation_timeout": 10,
            "requests_to_origin": False,
        },
    }
    kept = [
        item for item in ((entry or {}).get("rules") or [])
        if item.get("ref") != "lms_media_gate_ratelimit"
    ]
    payload = _call(
        "PUT",
        f"/zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint",
        json={"rules": [*kept, rule]},
    )
    if not payload.get("success"):
        print(f"rate_apply: FAILED {_errors(payload)}")
        return 1
    print(f"rate_apply: OK ruleset_id={(payload.get('result') or {}).get('id')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-waf", action="store_true", help="create or update the deny rule")
    parser.add_argument("--apply-rewrite", action="store_true", help="create or update the gate rewrite")
    parser.add_argument("--apply-cache", action="store_true", help="create or update the segment cache rule")
    parser.add_argument("--apply-rate", action="store_true", help="create or update the rate-limit rule")
    parser.add_argument(
        "--placeholder-token",
        default="",
        help="16 url-safe characters used until the app publishes its first real token",
    )
    args = parser.parse_args()
    host = _edge_host()
    zone_id = _zone_id(_zone_name_from_host(host))
    print(f"zone_id_resolved: yes ({len(zone_id)} chars)")
    requested = (
        args.apply_waf or args.apply_rewrite or args.apply_cache or args.apply_rate
    )
    if not requested:
        print("no action requested; pass --apply-waf/--apply-rewrite/--apply-cache/--apply-rate")
        return 0
    if args.apply_waf:
        placeholder = args.placeholder_token or secrets.token_urlsafe(12)[:16]
        if not re.fullmatch(r"[A-Za-z0-9_-]{16}", placeholder):
            sys.exit("placeholder token must be 16 url-safe characters")
        if apply_waf(zone_id, host, placeholder):
            return 1
    if args.apply_rewrite and apply_rewrite(zone_id, host):
        return 1
    if args.apply_cache and apply_cache(zone_id, host):
        return 1
    if args.apply_rate and apply_rate_limit(zone_id, host):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
