#!/usr/bin/env python3
"""Disposable Phase 8 progress smoke checks; emits no student or secret data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-disposable", action="store_true", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args()
    checks: list[str] = []
    failures: list[str] = []

    try:
        if os.environ.get("PHASE08_DISPOSABLE") != "1":
            raise RuntimeError("PHASE08_DISPOSABLE=1 is required for this smoke check.")

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_platform.settings")
        import django

        django.setup()

        from django.db import connection
        from management_system.models import LectureProgress, ViewingSession
        import management_system.progress_activity as progress_activity
        from management_system.progress_activity import ProgressHeartbeatResult
        from management_system.media_delivery import sign_session, valid_session_token
        from django.utils import timezone as django_timezone

        required_session_fields = {
            "started_at",
            "superseded_at",
            "edge_earned_seconds_consumed",
            "activity_cursor_at",
            "last_payload_digest",
            "last_acknowledgement",
        }
        actual_session_fields = {field.name for field in ViewingSession._meta.get_fields()}
        missing = sorted(required_session_fields - actual_session_fields)
        if missing:
            failures.append(f"ViewingSession is missing Phase 8 fields: {', '.join(missing)}")
        if "edge_slack_remaining_seconds" not in {
            field.name for field in LectureProgress._meta.get_fields()
        }:
            failures.append("LectureProgress is missing edge_slack_remaining_seconds.")
        if not missing and "edge_slack_remaining_seconds" in {
            field.name for field in LectureProgress._meta.get_fields()
        }:
            checks.append("progress schema fields")

        payload = ProgressHeartbeatResult(percent=0, completed=False).as_payload()
        required_payload = {
            "status",
            "session_id",
            "sequence",
            "accepted_ranges",
            "already_saved_ranges",
            "remaining_ranges",
            "credited_active_seconds",
            "reason",
            "terminal",
            "last_sequence",
            "next_range_index",
            "ack_truncated",
        }
        missing = sorted(required_payload - payload.keys())
        if missing:
            failures.append(f"heartbeat acknowledgement is missing fields: {', '.join(missing)}")
        else:
            checks.append("heartbeat acknowledgement shape")
        saved_ack = {"status": "ok", "session_id": "fixture", "sequence": 2, "accepted_ranges": [[0, 1]]}
        try:
            duplicate = ProgressHeartbeatResult(
                percent=0,
                completed=False,
                stored_acknowledgement=saved_ack,
            )
            if duplicate.as_payload() != saved_ack:
                failures.append("duplicate heartbeat did not preserve its exact saved acknowledgement")
            else:
                checks.append("exact duplicate acknowledgement")
        except TypeError:
            failures.append("heartbeat result cannot return its exact saved acknowledgement")

        from management_system.utils import progress_merge

        normalize_ranges = getattr(progress_merge, "normalize_ranges", None)
        subtract_ranges = getattr(progress_merge, "subtract_ranges", None)
        intersect_ranges = getattr(progress_merge, "intersect_ranges", None)
        take_ordered_prefix = getattr(progress_merge, "take_ordered_prefix", None)
        saved_prefix_ack = getattr(progress_merge, "saved_prefix_ack", None)
        if not all(callable(function) for function in (normalize_ranges, subtract_ranges, intersect_ranges, take_ordered_prefix, saved_prefix_ack)):
            failures.append("progress range difference/allocation helpers are missing")
        else:
            if normalize_ranges([(1.005, 3.014)]) != [(1.01, 3.01)]:
                failures.append("range normalization did not use canonical centiseconds")
            else:
                checks.append("centisecond range normalization")
            if subtract_ranges([(0, 4)], [(0, 1), (3, 5)]) != [(1.0, 3.0)]:
                failures.append("range difference did not remove persisted coverage")
            else:
                checks.append("range difference")
            if intersect_ranges([(0, 4), (5, 8)], [(1, 2), (3, 6)]) != [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]:
                failures.append("timeline intersection did not preserve the exact valid ranges")
            else:
                checks.append("timeline intersection")
            accepted, remaining = take_ordered_prefix([(1.01, 3.01), (4, 5)], 2.5)
            if accepted != [(1.01, 3.01), (4.0, 4.5)] or remaining != [(4.5, 5.0)]:
                failures.append("ordered prefix allocation did not preserve centiseconds")
            else:
                checks.append("centisecond ordered-prefix allocation")
            ack = saved_prefix_ack([(0, 5)], [(0, 1), (2, 3)], [(1, 2)])
            if ack != {
                "accepted_ranges": [(1.0, 2.0)],
                "already_saved_ranges": [(0.0, 1.0), (2.0, 3.0)],
                "remaining_ranges": [(3.0, 5.0)],
                "next_range_index": 0,
                "ack_truncated": False,
            }:
                failures.append("acknowledgement did not return only the proven saved prefix")
            else:
                checks.append("partial acknowledgement saved-prefix semantics")
            fragmented = [(index, index + 0.25) for index in range(1000)]
            ack = saved_prefix_ack([(0, 1000)], fragmented, [])
            if len(ack["already_saved_ranges"]) + len(ack["remaining_ranges"]) > 200:
                failures.append("fragmented saved coverage exceeded the acknowledgement bound")
            if ack["remaining_ranges"] != [(0.25, 1000.0)]:
                failures.append("fragmented saved coverage did not preserve its unconfirmed suffix")
            ack = saved_prefix_ack([(0, 2), (4, 6), (8, 10)], [(0, 1)], [])
            if ack["remaining_ranges"] != [(1.0, 2.0), (4.0, 6.0), (8.0, 10.0)]:
                failures.append("acknowledgement dropped later unconfirmed ranges after a gap")
            else:
                checks.append("acknowledgement retains every unconfirmed suffix")
            calculate_percent = getattr(progress_merge, "calculate_percent", None)
            unique_percent = getattr(progress_merge, "unique_seconds", None)
            if not callable(calculate_percent) or not callable(unique_percent):
                failures.append("percent calculation helpers are missing")
            elif (
                calculate_percent(480, 600) != 80
                or calculate_percent(479.99, 600) != 79
                or unique_percent([(0, 0.1), (0.1, 0.2), (0.2, 0.3)]) != 0.3
            ):
                failures.append("progress percent drifted at an exact boundary")
            else:
                checks.append("exact percent boundary")
            fit_acknowledgement = getattr(progress_activity, "_fit_acknowledgement", None)
            if not callable(fit_acknowledgement):
                failures.append("bounded acknowledgement fitting is missing")
            else:
                oversized = {
                    "status": "ok",
                    "accepted_ranges": [[0, 1]],
                    "already_saved_ranges": [[2, 3]],
                    "remaining_ranges": [[index, index + 1] for index in range(4000)],
                    "ack_truncated": False,
                }
                fitted = fit_acknowledgement(oversized)
                encoded_size = len(
                    json.dumps(fitted, separators=(",", ":")).encode("utf-8")
                )
                if (
                    encoded_size > progress_activity.ACK_MAX_BYTES
                    or not fitted["ack_truncated"]
                    or fitted["accepted_ranges"] != [[0, 1]]
                    or fitted["already_saved_ranges"] != [[2, 3]]
                ):
                    failures.append(
                        "oversized acknowledgement was not trimmed safely inside its bound"
                    )
                else:
                    checks.append("bounded acknowledgement fitting")

        normalize_heartbeat = getattr(progress_activity, "normalize_progress_heartbeat", None)
        if not callable(normalize_heartbeat):
            failures.append("canonical heartbeat normalization is missing")
        else:
            first = normalize_heartbeat({
                "session_id": "fixture",
                "sequence": 5,
                "ranges": [[1.005, 3.014]],
                "active_seconds": 2.005,
                "ended": False,
            })
            retry = normalize_heartbeat({
                "session_id": "fixture",
                "sequence": 5,
                "ranges": [[1.01, 3.01]],
                "active_seconds": 2.01,
                "ended": False,
            })
            if first["ranges"] != [(1.01, 3.01)] or first["active_seconds"] != 2.01:
                failures.append("heartbeat input did not normalize to centiseconds")
            elif first["digest"] != retry["digest"]:
                failures.append("equivalent centisecond heartbeats did not share a digest")
            else:
                checks.append("canonical heartbeat normalization and digest")

        edge_budget = getattr(progress_activity, "edge_credit_budget_centiseconds", None)
        budget_window_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        if not callable(edge_budget):
            failures.append("server-window edge credit budget calculation is missing")
        elif edge_budget(
            budget_window_start,
            budget_window_start + timedelta(seconds=10),
            "18.00",
            "15.00",
        ) != (1000, 1500):
            failures.append("1x credit and shared slack were not budgeted independently")
        elif edge_budget(
            budget_window_start,
            budget_window_start + timedelta(seconds=10),
            "18.00",
            "15.00",
            2.0,
        ) != (2000, 1700):
            failures.append("2x credit did not double the earned allowance")
        else:
            checks.append("independent earned-credit and shared-slack budget at 1x and 2x")

        expired_at = django_timezone.now() - timedelta(days=1)
        fixture_session = SimpleNamespace(session_id="fixture", expires_at=expired_at)
        fixture_token = sign_session("fixture", expired_at, audience="web")
        if valid_session_token(fixture_token, fixture_session):
            failures.append("expired progress proof extended ordinary playback access")
        elif not valid_session_token(
            fixture_token,
            fixture_session,
            allow_expired=True,
        ):
            failures.append("signed expired proof was rejected for progress replay")
        else:
            checks.append("replay-only expired session proof")

        if failures:
            raise AssertionError("; ".join(failures))

        if args.contract_only:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                "CONTRACT ONLY PASS - database integration was not checked.\n"
                f"Checks: {', '.join(checks)}\n",
                encoding="utf-8",
            )
            print(f"Phase 8 contract-only checks passed: {', '.join(checks)}")
            return 0

        if connection.vendor != "postgresql":
            raise RuntimeError("Phase 8 progress checks require PostgreSQL.")
        if "phase08" not in str(connection.settings_dict.get("NAME", "")).lower():
            raise RuntimeError("Database name must contain phase08 to prove disposable isolation.")
        connection.ensure_connection()
        checks.append("isolated PostgreSQL connection")

    except Exception as exc:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(f"FAILED: {exc}\nChecks: {', '.join(checks)}\n", encoding="utf-8")
        print(f"Phase 8 progress smoke failed: {exc}", file=sys.stderr)
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(f"PASS\nChecks: {', '.join(checks)}\n", encoding="utf-8")
    print(f"Phase 8 progress smoke passed: {', '.join(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
