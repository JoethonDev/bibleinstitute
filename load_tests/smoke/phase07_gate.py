#!/usr/bin/env python3
"""Phase 7 edge-gate smoke checks. Contract-only mode does not validate runtime gates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def check_rotation_task_hides_tokens() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")
    import django

    django.setup()

    from management_system import media_gate_tasks
    from management_system.media_gate import RotationResult

    result = RotationResult(
        "rotated",
        token="AbCdEfGhIjKlMnOp",
        previous="QwErTyUiOpAsDfGh",
    )
    with patch.object(media_gate_tasks, "rotate_token", return_value=result):
        task_result = media_gate_tasks.rotate_media_gate_token.run()
    if task_result != {"status": "rotated", "reason": ""}:
        raise AssertionError("rotation task result exposed token values")


def check_start_response_keeps_session_token() -> None:
    import django

    django.setup()

    from django.test import RequestFactory

    from management_system import views

    media = SimpleNamespace(
        part_id="part-a",
        file_index=0,
        offering=SimpleNamespace(pk=7),
        lesson=SimpleNamespace(pk=8),
        media_key="course/lesson.m3u8",
    )
    session = SimpleNamespace(
        session_id="session-a",
        expires_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
    )
    progress = SimpleNamespace(values=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None))
    request = RequestFactory().get("/start/")
    request.user = SimpleNamespace(is_authenticated=True, pk=42)

    def fake_reverse(name, *_args, **_kwargs):
        return "/gate/" if name == "media-gate-refresh" else "/manifest/"

    with (
        patch.object(views, "resolve_media_link", return_value=media),
        patch.object(views, "create_viewing_session", return_value=session),
        patch.object(views, "sign_session", return_value="session-signature"),
        patch.object(views.LectureProgress.objects, "filter", return_value=progress),
        patch.object(views, "reverse", side_effect=fake_reverse),
        patch.object(views, "gate_payload", return_value={
            "token": "edge-token-value",
            "refresh_at": "2026-11-17T12:00:00+00:00",
            "valid_until": "2026-11-17T12:30:00+00:00",
        }),
    ):
        response = views.start_viewing_session.__wrapped__(request, 7, 8, 0)

    payload = json.loads(response.content)
    if payload.get("token") != "session-signature":
        raise AssertionError("edge token replaced the viewing-session token")
    expected = {
        "gate_token": "edge-token-value",
        "gate_refresh_at": "2026-11-17T12:00:00+00:00",
        "gate_valid_until": "2026-11-17T12:30:00+00:00",
        "gate_url": "/gate/",
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise AssertionError(f"start response omitted its separate {field}")


def check_gate_payload_rejects_expired_pair() -> None:
    from datetime import timedelta

    from management_system import media_gate

    started = datetime(2026, 11, 17, tzinfo=timezone.utc)
    now = started + timedelta(seconds=2701)
    with (
        patch.object(media_gate, "_read_publication", return_value=("AbCdEfGhIjKlMnOp", None, started)),
        patch.object(media_gate, "period_seconds", return_value=1800),
    ):
        try:
            media_gate.gate_payload(now)
        except media_gate.MediaGateUnavailable as exc:
            if exc.code != "published_token_expired":
                raise AssertionError("expired token failed with an unexpected code") from exc
        else:
            raise AssertionError("gate payload returned an expired token")


def check_edge_rule_contract() -> None:
    """The rewrite order and the token-free deny rule are the whole gate."""
    import django

    django.setup()

    from management_system import media_gate

    host = "cdn.example.test"
    current, previous = "AbCdEfGhIjKlMnOp", "QwErTyUiOpAsDfGh"
    sentinel, strip = media_gate.transform_rules(host, current, previous)
    if sentinel["ref"] != media_gate.SENTINEL_REF or strip["ref"] != media_gate.GATE_RULE_REF:
        raise AssertionError("rewrite order changed; the sentinel must be evaluated first")
    if media_gate.SENTINEL_TARGET != 'concat("/__lms_media_denied__", "/missing.ts")':
        raise AssertionError("the sentinel target must be a constant concat() expression")
    for token in (current, previous):
        if f"/g/{token}/" not in strip["expression"]:
            raise AssertionError("the rewrite filter dropped a live token")
    target = strip["action_parameters"]["uri"]["path"]["expression"]
    if target != "substring(http.request.uri.path, 19)":
        raise AssertionError("the gate prefix offset no longer matches a 16 character token")
    deny = media_gate.static_waf_rule(host)["expression"]
    if 'starts_with(http.request.uri.path, "/g/")' not in deny:
        raise AssertionError("the deny rule no longer blocks an unstripped gate path")
    for token in (current, previous):
        if token in deny:
            raise AssertionError("the deny rule must stay free of token state")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--confirm-disposable", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.contract_only:
        if not args.confirm_disposable or os.environ.get("PHASE07_DISPOSABLE") != "1":
            parser.error("full mode requires --confirm-disposable and PHASE07_DISPOSABLE=1")
        if args.report is None:
            parser.error("full mode requires --report")
        parser.error("disposable database checks are not implemented yet")

    try:
        check_rotation_task_hides_tokens()
        check_start_response_keeps_session_token()
        check_gate_payload_rejects_expired_pair()
        check_edge_rule_contract()
    except Exception as exc:  # noqa: BLE001 - report a concise smoke failure
        print(f"Phase 7 contract smoke failed: {exc}", file=sys.stderr)
        return 1
    print("Phase 7 response and token contracts passed; database, Redis, provider and player gates were not run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
