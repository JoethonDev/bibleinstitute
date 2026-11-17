"""Scheduled Cloudflare edge token rotation and drift repair."""

import logging

from celery import shared_task

from .media_gate import (
    MediaGateError,
    MediaGateUnavailable,
    _edge_mode_enabled,
    repair_gate_from_committed_pair,
    rotate_token,
    rule_matches_publication,
    stale_publication,
)

logger = logging.getLogger(__name__)


@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=30,
    time_limit=35,
    name="management_system.media_gate_tasks.rotate_media_gate_token",
)
def rotate_media_gate_token() -> dict[str, str]:
    result = rotate_token()
    return {
        "status": result.status,
        "reason": result.reason,
    }


@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=45,
    time_limit=50,
    name="management_system.media_gate_tasks.check_media_gate_drift",
)
def check_media_gate_drift() -> dict[str, str]:
    try:
        if not _edge_mode_enabled():
            return {"status": "skipped", "reason": "edge_gate_not_enabled"}
        if stale_publication():
            logger.error("media_gate_rotation_stale")
            result = rotate_token()
            if result.status == "failed":
                return {"status": "failed", "reason": result.reason}
            return {"status": "rotated_stale_pair", "reason": result.status}
        if rule_matches_publication():
            return {"status": "matched", "reason": ""}
    except (MediaGateError, MediaGateUnavailable) as exc:
        logger.error("media_gate_drift_check_failed code=%s", exc.code)
        return {"status": "failed", "reason": exc.code}

    logger.error("media_gate_edge_rule_drift_detected")
    if not repair_gate_from_committed_pair():
        return {"status": "failed", "reason": "gate_repair_failed"}
    return {"status": "repaired", "reason": "committed_pair_republished"}
