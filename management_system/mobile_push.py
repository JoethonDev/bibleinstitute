"""Bounded Expo Push Service delivery for durable student notifications."""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Exists, F, Prefetch, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from .notification_redelivery import eligible_redelivery_notification_ids, redelivery_source_queryset

from .models import MobilePushDevice, PushDelivery, StudentNotification
from .mobile_push_queries import (
    active_push_devices,
    missing_push_device_exists,
)

logger = logging.getLogger(__name__)


MAX_PUSH_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)
DELIVERY_SCAN_LIMIT = 1000
DELIVERY_BATCH_SIZE = 500
ANDROID_CHANNEL_ID = "academic-events"
RECEIPT_LEASE_SECONDS = 300
SENDING_LEASE_SECONDS = 300
SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _configured() -> bool:
    return bool(getattr(settings, "EXPO_PUSH_ACCESS_TOKEN", "").strip())


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.EXPO_PUSH_ACCESS_TOKEN}",
    }


def _provider_post(url: str, payload: dict | list[dict]) -> tuple[str, dict | None]:
    """Return a safe outcome code and decoded JSON without exposing response data."""
    try:
        response = requests.post(
            url,
            headers=_headers(),
            json=payload,
            timeout=settings.EXPO_PUSH_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return "network_error", None

    status_code = response.status_code
    if status_code == 429 or 500 <= status_code <= 599:
        return f"http_{status_code}", None
    if status_code < 200 or status_code >= 300:
        return f"http_{status_code}", None
    try:
        data = response.json()
    except ValueError:
        return "invalid_response", None
    return "ok", data if isinstance(data, dict) else None


def _retry_delay(attempt_count: int) -> int:
    return RETRY_DELAYS[min(max(attempt_count - 1, 0), len(RETRY_DELAYS) - 1)]


def _safe_provider_code(value: object, fallback: str = "provider_rejected") -> str:
    code = str(value or "").strip()
    return code if SAFE_PROVIDER_CODE.fullmatch(code) else fallback


def _mark_retryable(deliveries: list[PushDelivery], error_code: str, now) -> None:
    delivery_ids = [delivery.pk for delivery in deliveries]
    notification_state = {
        delivery_id: (cancelled_at, withdrawn_at)
        for delivery_id, cancelled_at, withdrawn_at in PushDelivery.objects.filter(
            pk__in=delivery_ids,
        ).values_list(
            "pk", "notification__cancelled_at", "notification__withdrawn_at",
        )
    }
    for delivery in deliveries:
        cancelled_at, withdrawn_at = notification_state.get(delivery.pk, (None, None))
        if withdrawn_at is not None:
            delivery.status = PushDelivery.Status.SKIPPED
            delivery.error_code = "notification_withdrawn"
            delivery.error_message = _("The notification was withdrawn before delivery.")
            delivery.next_attempt_at = now
        elif cancelled_at is not None:
            delivery.status = PushDelivery.Status.SKIPPED
            delivery.error_code = "notification_cancelled"
            delivery.error_message = _("The notification was cancelled before delivery.")
            delivery.next_attempt_at = now
        elif delivery.attempt_count >= MAX_PUSH_ATTEMPTS:
            delivery.status = PushDelivery.Status.FAILED
            delivery.error_code = error_code
            delivery.error_message = "Expo delivery failed after bounded retries."
            delivery.next_attempt_at = now
        else:
            delivery.status = PushDelivery.Status.QUEUED
            delivery.error_code = error_code
            delivery.error_message = "Expo delivery will be retried."
            delivery.next_attempt_at = now + timedelta(seconds=_retry_delay(delivery.attempt_count))
        delivery.updated_at = now
    if deliveries:
        PushDelivery.objects.bulk_update(
            deliveries,
            ["status", "error_code", "error_message", "next_attempt_at", "updated_at"],
        )
    # Close the race where withdrawal commits between the state read and retry
    # write. A later withdrawal sees QUEUED and skips it itself.
    PushDelivery.objects.filter(
        pk__in=delivery_ids,
        status=PushDelivery.Status.QUEUED,
        notification__withdrawn_at__isnull=False,
    ).update(
        status=PushDelivery.Status.SKIPPED,
        error_code="notification_withdrawn",
        error_message=_("The notification was withdrawn before delivery."),
        next_attempt_at=now,
        updated_at=now,
    )
    PushDelivery.objects.filter(
        pk__in=delivery_ids,
        status=PushDelivery.Status.QUEUED,
        notification__cancelled_at__isnull=False,
    ).update(
        status=PushDelivery.Status.SKIPPED,
        error_code="notification_cancelled",
        error_message=_("The notification was cancelled before delivery."),
        next_attempt_at=now,
        updated_at=now,
    )


def _mark_permanent_failure(deliveries: list[PushDelivery], error_code: str, message: str, now) -> None:
    for delivery in deliveries:
        delivery.status = PushDelivery.Status.FAILED
        delivery.error_code = error_code
        delivery.error_message = message
        delivery.next_attempt_at = now
        delivery.updated_at = now
    if deliveries:
        PushDelivery.objects.bulk_update(
            deliveries,
            ["status", "error_code", "error_message", "next_attempt_at", "updated_at"],
        )


def _mark_receipt_retryable(deliveries: list[PushDelivery], error_code: str, now) -> None:
    for delivery in deliveries:
        if delivery.attempt_count >= MAX_PUSH_ATTEMPTS:
            delivery.status = PushDelivery.Status.FAILED
            delivery.error_code = error_code
            delivery.error_message = "Expo receipt failed after bounded retries."
            delivery.receipt_checked_at = now
            delivery.next_attempt_at = now
        else:
            delivery.status = PushDelivery.Status.TICKETED
            delivery.attempt_count += 1
            delivery.error_code = error_code
            delivery.error_message = "Expo receipt will be checked again."
            delivery.next_attempt_at = now + timedelta(seconds=_retry_delay(delivery.attempt_count))
        delivery.updated_at = now
    if deliveries:
        PushDelivery.objects.bulk_update(
            deliveries,
            [
                "status", "attempt_count", "error_code", "error_message",
                "receipt_checked_at", "next_attempt_at", "updated_at",
            ],
        )


def _notification_payload(delivery: PushDelivery) -> dict:
    notification = delivery.notification
    if delivery.device.language == MobilePushDevice.Language.ENGLISH:
        title = notification.title_en or notification.title_ar
        body = notification.body_en or notification.body_ar
    else:
        title = notification.title_ar or notification.title_en
        body = notification.body_ar or notification.body_en
    return {
        "to": delivery.device.expo_push_token,
        "title": title,
        "body": body,
        "data": {
            "notification_id": notification.pk,
            "type": notification.notification_type,
            "navigation_type": notification.navigation_type,
            "offering_id": notification.offering_id,
            "entity_id": notification.entity_id,
            "action": (
                {
                    "label": notification.action_label,
                    "url": notification.action_url,
                }
                if notification.action_url
                else None
            ),
        },
        "sound": "default",
        "priority": "high",
        "channelId": ANDROID_CHANNEL_ID,
    }


@transaction.atomic
def _materialize_due_deliveries(limit: int) -> int:
    now = timezone.now()
    scan_limit = max(1, min(int(limit), DELIVERY_SCAN_LIMIT))
    # Exclude fully-materialized notifications in SQL. Without this, the
    # bounded [:scan_limit] window keeps returning the oldest already-covered
    # rows and starves newer notifications once the delivered backlog exceeds
    # the scan window. The nested anti-join keeps notifications that still
    # have at least one active device without a delivery, so late-registered
    # devices and partial batches are still picked up.
    missing_device_exists = missing_push_device_exists()
    active_devices = active_push_devices().only(
        "id", "user_id", "expo_push_token", "is_active", "disabled_at"
    )
    notifications = list(
        StudentNotification.objects.select_for_update(skip_locked=True, of=("self",)).filter(
            is_manual_redelivery=False,
            scheduled_for__lte=now,
            push_expires_at__gt=now,
            cancelled_at__isnull=True,
            withdrawn_at__isnull=True,
            student__is_active=True,
            student__application_status="active",
            student__role__role="student",
        )
        .filter(Exists(missing_device_exists))
        .select_related("student")
        .prefetch_related(
            Prefetch(
                "student__mobile_push_devices",
                queryset=active_devices,
                to_attr="active_mobile_push_devices",
            ),
        )
        .order_by("scheduled_for", "pk")[:scan_limit]
    )
    if not notifications:
        return 0

    existing_pairs = set(
        PushDelivery.objects.filter(
            notification_id__in=[notification.pk for notification in notifications],
        ).values_list("notification_id", "device_id")
    )
    rows: list[PushDelivery] = []
    for notification in notifications:
        for device in getattr(notification.student, "active_mobile_push_devices", []):
            if len(rows) >= scan_limit:
                break
            if (notification.pk, device.pk) in existing_pairs:
                continue
            rows.append(
                PushDelivery(
                    notification_id=notification.pk,
                    device_id=device.pk,
                    next_attempt_at=now,
                )
            )
        if len(rows) >= scan_limit:
            break

    rows = rows[:scan_limit]
    for start in range(0, len(rows), DELIVERY_BATCH_SIZE):
        PushDelivery.objects.bulk_create(
            rows[start:start + DELIVERY_BATCH_SIZE],
            ignore_conflicts=True,
        )
    return len(rows)


def _claim_due_deliveries(limit: int) -> list[PushDelivery]:
    now = timezone.now()
    claim_limit = max(1, min(int(limit), DELIVERY_SCAN_LIMIT))
    with transaction.atomic():
        eligible = PushDelivery.objects.filter(
            status=PushDelivery.Status.QUEUED,
            next_attempt_at__lte=now,
            attempt_count__lt=MAX_PUSH_ATTEMPTS,
            notification__cancelled_at__isnull=True,
            notification__withdrawn_at__isnull=True,
            notification__push_expires_at__gt=now,
            notification__student__is_active=True,
            notification__student__application_status="active",
            notification__student__role__role="student",
            device__user_id=F("notification__student_id"),
            device__is_active=True,
            device__disabled_at__isnull=True,
        )
        candidate_device_ids = list(dict.fromkeys(
            eligible.order_by("next_attempt_at", "pk")
            .values_list("device_id", flat=True)[:claim_limit]
        ))
        if not candidate_device_ids:
            return []

        # Device ownership changes and dispatch claiming lock devices first.
        # Logout can therefore delete a registration only after any in-flight
        # claim has become visible as SENDING.
        locked_device_ids = list(
            MobilePushDevice.objects.select_for_update(skip_locked=True)
            .filter(
                pk__in=candidate_device_ids,
                is_active=True,
                disabled_at__isnull=True,
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not locked_device_ids:
            return []
        deliveries = list(
            eligible.filter(device_id__in=locked_device_ids)
            .select_for_update(
                of=("self",),
                skip_locked=True,
            )
            .select_related("notification", "device")
            .order_by("next_attempt_at", "pk")[:claim_limit]
        )
        for delivery in deliveries:
            delivery.status = PushDelivery.Status.SENDING
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            delivery.updated_at = now
        if deliveries:
            PushDelivery.objects.bulk_update(
                deliveries,
                ["status", "attempt_count", "last_attempt_at", "updated_at"],
            )
    return deliveries


def _deactivate_devices(device_ids: set[int], now) -> None:
    if device_ids:
        MobilePushDevice.objects.filter(pk__in=device_ids).update(
            is_active=False,
            disabled_at=now,
            updated_at=now,
        )


def _expire_pending_deliveries(limit: int = DELIVERY_SCAN_LIMIT) -> int:
    now = timezone.now()
    ids = list(
        PushDelivery.objects.filter(
            status=PushDelivery.Status.QUEUED,
            notification__push_expires_at__lte=now,
        ).values_list("pk", flat=True)[: max(1, min(int(limit), DELIVERY_SCAN_LIMIT))]
    )
    if not ids:
        return 0
    return PushDelivery.objects.filter(pk__in=ids).update(
        status=PushDelivery.Status.SKIPPED,
        error_code="notification_expired",
        error_message="The push notification expired before delivery.",
        next_attempt_at=now,
        updated_at=now,
    )


def _send_chunk(deliveries: list[PushDelivery]) -> dict[str, int]:
    if not deliveries:
        return {"ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": 0}
    manual_skipped = 0
    manual_ids = {d.notification_id for d in deliveries if d.notification.is_manual_redelivery}
    if manual_ids:
        allowed = eligible_redelivery_notification_ids(list(redelivery_source_queryset().filter(pk__in=manual_ids)))
        rejected = [d.pk for d in deliveries if d.notification_id in manual_ids and d.notification_id not in allowed]
        PushDelivery.objects.filter(pk__in=rejected, status="sending").update(
            status="skipped", error_code="redelivery_not_authorized",
            error_message="The notification is no longer eligible for redelivery.", updated_at=timezone.now(),
        )
        rejected_ids = set(rejected)
        manual_skipped = len(rejected_ids)
        deliveries = [d for d in deliveries if d.pk not in rejected_ids]
        if not deliveries:
            return {"ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": len(rejected)}
    now = timezone.now()
    owned_ids = set(
        PushDelivery.objects.filter(
            pk__in=[delivery.pk for delivery in deliveries],
            status=PushDelivery.Status.SENDING,
            device__user_id=F("notification__student_id"),
        ).values_list("pk", flat=True)
    )
    reassigned = [delivery for delivery in deliveries if delivery.pk not in owned_ids]
    if reassigned:
        PushDelivery.objects.filter(
            pk__in=[delivery.pk for delivery in reassigned],
            status=PushDelivery.Status.SENDING,
        ).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="device_account_changed",
            error_message="Device account changed before delivery.",
            next_attempt_at=now,
            updated_at=now,
        )
        deliveries = [delivery for delivery in deliveries if delivery.pk in owned_ids]
    if not deliveries:
        return {"ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": len(reassigned) + manual_skipped}
    blocked = list(
        PushDelivery.objects.filter(
            pk__in=[delivery.pk for delivery in deliveries],
            status=PushDelivery.Status.SENDING,
        ).filter(
            Q(notification__withdrawn_at__isnull=False)
            | Q(notification__cancelled_at__isnull=False),
        ).values_list("pk", "notification__withdrawn_at")
    )
    withdrawn_ids = {delivery_id for delivery_id, withdrawn_at in blocked if withdrawn_at is not None}
    cancelled_ids = {delivery_id for delivery_id, withdrawn_at in blocked if withdrawn_at is None}
    now = timezone.now()
    if withdrawn_ids:
        PushDelivery.objects.filter(
            pk__in=withdrawn_ids,
            status=PushDelivery.Status.SENDING,
        ).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="notification_withdrawn",
            error_message=_("The notification was withdrawn before delivery."),
            next_attempt_at=now,
            updated_at=now,
        )
    if cancelled_ids:
        PushDelivery.objects.filter(
            pk__in=cancelled_ids,
            status=PushDelivery.Status.SENDING,
        ).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="notification_cancelled",
            error_message=_("The notification was cancelled before delivery."),
            next_attempt_at=now,
            updated_at=now,
        )
    blocked_ids = withdrawn_ids | cancelled_ids
    if blocked_ids:
        manual_skipped += len(blocked_ids)
        deliveries = [delivery for delivery in deliveries if delivery.pk not in blocked_ids]
    if not deliveries:
        return {"ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": len(reassigned) + manual_skipped}
    expired = [delivery for delivery in deliveries if delivery.notification.push_expires_at <= now]
    if expired:
        PushDelivery.objects.filter(pk__in=[delivery.pk for delivery in expired]).update(
            status=PushDelivery.Status.SKIPPED,
            error_code="notification_expired",
            error_message="The push notification expired before delivery.",
            next_attempt_at=now,
            updated_at=now,
        )
        deliveries = [delivery for delivery in deliveries if delivery not in expired]
    if not deliveries:
        return {"ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": len(expired) + len(reassigned) + manual_skipped}
    outcome, data = _provider_post(
        settings.EXPO_PUSH_SEND_URL,
        [_notification_payload(delivery) for delivery in deliveries],
    )
    now = timezone.now()
    if outcome != "ok":
        if outcome == "network_error" or outcome == "invalid_response" or outcome.startswith("http_5") or outcome == "http_429":
            _mark_retryable(deliveries, outcome, now)
            return {"ticketed": 0, "failed": 0, "retried": len(deliveries), "deactivated": 0, "skipped": len(expired) + len(reassigned) + manual_skipped}
        _mark_permanent_failure(deliveries, outcome, "Expo rejected the push request.", now)
        return {"ticketed": 0, "failed": len(deliveries), "retried": 0, "deactivated": 0, "skipped": len(expired) + len(reassigned) + manual_skipped}

    tickets = data.get("data") if isinstance(data, dict) else None
    if not isinstance(tickets, list) or len(tickets) != len(deliveries):
        _mark_retryable(deliveries, "invalid_response", now)
        return {"ticketed": 0, "failed": 0, "retried": len(deliveries), "deactivated": 0, "skipped": len(expired) + len(reassigned) + manual_skipped}

    deactivated_ids: set[int] = set()
    retryable: list[PushDelivery] = []
    ticketed = failed = retried = 0
    for delivery, ticket in zip(deliveries, tickets):
        if not isinstance(ticket, dict):
            delivery.status = PushDelivery.Status.FAILED
            delivery.error_code = "invalid_ticket"
            delivery.error_message = "Expo returned an invalid ticket."
            delivery.updated_at = now
            failed += 1
            continue
        if ticket.get("status") == "ok" and ticket.get("id"):
            delivery.status = PushDelivery.Status.TICKETED
            delivery.expo_ticket_id = str(ticket["id"])[:255]
            delivery.ticketed_at = now
            delivery.next_attempt_at = now + timedelta(seconds=60)
            delivery.error_code = ""
            delivery.error_message = ""
            delivery.updated_at = now
            ticketed += 1
            continue

        details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
        error_code = _safe_provider_code(details.get("error"), "provider_rejected")
        if error_code == "MessageRateExceeded":
            retryable.append(delivery)
            retried += 1
            continue
        delivery.status = PushDelivery.Status.FAILED
        delivery.error_code = error_code
        delivery.error_message = (
            "Expo device is no longer registered."
            if error_code == "DeviceNotRegistered"
            else "Expo rejected the push notification."
        )
        delivery.next_attempt_at = now
        delivery.updated_at = now
        failed += 1
        if error_code == "DeviceNotRegistered":
            deactivated_ids.add(delivery.device_id)

    PushDelivery.objects.bulk_update(
        deliveries,
        [
            "status", "expo_ticket_id", "ticketed_at", "next_attempt_at",
            "error_code", "error_message", "updated_at",
        ],
    )
    if retryable:
        _mark_retryable(retryable, "MessageRateExceeded", now)
    _deactivate_devices(deactivated_ids, now)
    return {
        "ticketed": ticketed,
        "failed": failed,
        "retried": retried,
        "deactivated": len(deactivated_ids),
        "skipped": len(expired) + len(reassigned) + manual_skipped,
    }


def _recover_stale_sending() -> None:
    cutoff = timezone.now() - timedelta(seconds=SENDING_LEASE_SECONDS)
    now = timezone.now()
    with transaction.atomic():
        stale = list(
            PushDelivery.objects.select_for_update(of=("self",), skip_locked=True).select_related(
                "notification",
            ).filter(
                status=PushDelivery.Status.SENDING,
                updated_at__lt=cutoff,
            )[:DELIVERY_SCAN_LIMIT]
        )
        for delivery in stale:
            if delivery.notification.withdrawn_at is not None:
                delivery.status = PushDelivery.Status.SKIPPED
                delivery.error_code = "notification_withdrawn"
                delivery.error_message = _("The notification was withdrawn before delivery.")
                delivery.next_attempt_at = now
            elif delivery.notification.cancelled_at is not None:
                delivery.status = PushDelivery.Status.SKIPPED
                delivery.error_code = "notification_cancelled"
                delivery.error_message = _("The notification was cancelled before delivery.")
                delivery.next_attempt_at = now
            elif delivery.notification.push_expires_at <= now:
                delivery.status = PushDelivery.Status.SKIPPED
                delivery.error_code = "notification_expired"
                delivery.error_message = "The push notification expired before delivery."
                delivery.next_attempt_at = now
            elif delivery.attempt_count >= MAX_PUSH_ATTEMPTS:
                delivery.status = PushDelivery.Status.FAILED
                delivery.error_code = "stale_sending"
                delivery.error_message = "Expo delivery worker lease expired."
                delivery.next_attempt_at = now
            else:
                delivery.status = PushDelivery.Status.QUEUED
                delivery.error_code = "stale_sending"
                delivery.error_message = "Expo delivery was returned to the queue."
                delivery.next_attempt_at = now
            delivery.updated_at = now
        if stale:
            PushDelivery.objects.bulk_update(
                stale,
                ["status", "error_code", "error_message", "next_attempt_at", "updated_at"],
            )


def _log_push_dispatch_totals(totals: dict[str, int]) -> None:
    if not any(totals.values()):
        return
    logger.info(
        "mobile push dispatched",
        extra={"event": "mobile_push_dispatched", **totals},
    )


def _acquire_provider_slot(requests_per_second: int) -> None:
    """Throttle all web workers through the shared Redis cache when available."""
    while True:
        bucket = int(time.time())
        key = f"expo-push-rate:{bucket}"
        if cache.add(key, 1, timeout=2):
            return
        try:
            count = cache.incr(key)
        except ValueError:
            count = requests_per_second + 1
        if count <= requests_per_second:
            return
        time.sleep(max(0.01, bucket + 1 - time.time()))


def dispatch_due_mobile_push(*, limit: int = DELIVERY_SCAN_LIMIT) -> dict[str, int]:
    """Materialize and send one bounded set of due mobile push deliveries."""
    if not _configured():
        return {"created": 0, "ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": 0}

    expired = _expire_pending_deliveries()
    _recover_stale_sending()
    created = _materialize_due_deliveries(limit)
    deliveries = _claim_due_deliveries(limit)
    totals = {"created": created, "ticketed": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": expired}
    if not deliveries:
        _log_push_dispatch_totals(totals)
        return totals

    batch_size = max(1, min(int(getattr(settings, "EXPO_PUSH_SEND_BATCH_SIZE", 100)), 100))
    requests_per_second = max(1, int(getattr(settings, "EXPO_PUSH_REQUESTS_PER_SECOND", 5)))
    request_interval = 1.0 / requests_per_second
    last_request_at = None
    for start in range(0, len(deliveries), batch_size):
        _acquire_provider_slot(requests_per_second)
        if last_request_at is not None:
            time.sleep(max(0.0, request_interval - (time.monotonic() - last_request_at)))
        last_request_at = time.monotonic()
        counts = _send_chunk(deliveries[start:start + batch_size])
        for key in ("ticketed", "failed", "retried", "deactivated"):
            totals[key] += counts[key]
        totals["skipped"] += counts["skipped"]
    _log_push_dispatch_totals(totals)
    return totals


def _claim_receipt_deliveries(limit: int) -> list[PushDelivery]:
    now = timezone.now()
    claim_limit = max(1, min(int(limit), settings.EXPO_PUSH_RECEIPT_BATCH_SIZE))
    with transaction.atomic():
        deliveries = list(
            PushDelivery.objects.select_for_update(skip_locked=True)
            .select_related("device")
            .filter(
                status=PushDelivery.Status.TICKETED,
                expo_ticket_id__gt="",
                receipt_checked_at__isnull=True,
                next_attempt_at__lte=now,
            )
            .order_by("next_attempt_at", "pk")[:claim_limit]
        )
        for delivery in deliveries:
            delivery.next_attempt_at = now + timedelta(seconds=RECEIPT_LEASE_SECONDS)
            delivery.updated_at = now
        if deliveries:
            PushDelivery.objects.bulk_update(deliveries, ["next_attempt_at", "updated_at"])
    return deliveries


def _process_receipt_chunk(deliveries: list[PushDelivery]) -> dict[str, int]:
    if not deliveries:
        return {"delivered": 0, "failed": 0, "retried": 0, "deactivated": 0}
    outcome, data = _provider_post(
        settings.EXPO_PUSH_RECEIPTS_URL,
        {"ids": [delivery.expo_ticket_id for delivery in deliveries]},
    )
    now = timezone.now()
    if outcome != "ok":
        if outcome == "network_error" or outcome == "invalid_response" or outcome.startswith("http_5") or outcome == "http_429":
            _mark_receipt_retryable(deliveries, outcome, now)
            return {"delivered": 0, "failed": 0, "retried": len(deliveries), "deactivated": 0}
        _mark_permanent_failure(deliveries, outcome, "Expo receipt request failed.", now)
        for delivery in deliveries:
            delivery.receipt_checked_at = now
        PushDelivery.objects.bulk_update(deliveries, ["receipt_checked_at"])
        return {"delivered": 0, "failed": len(deliveries), "retried": 0, "deactivated": 0}

    receipts = data.get("data") if isinstance(data, dict) else None
    if not isinstance(receipts, dict):
        _mark_receipt_retryable(deliveries, "invalid_response", now)
        return {"delivered": 0, "failed": 0, "retried": len(deliveries), "deactivated": 0}

    deactivated_ids: set[int] = set()
    delivered = failed = retried = 0
    for delivery in deliveries:
        receipt = receipts.get(delivery.expo_ticket_id)
        if not isinstance(receipt, dict):
            if delivery.attempt_count >= MAX_PUSH_ATTEMPTS:
                delivery.status = PushDelivery.Status.FAILED
                delivery.error_code = "receipt_missing"
                delivery.error_message = "Expo did not return a receipt."
                delivery.receipt_checked_at = now
                failed += 1
            else:
                delivery.attempt_count += 1
                delivery.next_attempt_at = now + timedelta(seconds=_retry_delay(delivery.attempt_count))
                delivery.error_code = "receipt_missing"
                delivery.error_message = "Expo receipt will be checked again."
                retried += 1
            delivery.updated_at = now
            continue
        if receipt.get("status") == "ok":
            delivery.status = PushDelivery.Status.DELIVERED
            delivery.delivered_at = now
            delivery.receipt_checked_at = now
            delivery.error_code = ""
            delivery.error_message = ""
            delivery.updated_at = now
            delivered += 1
            continue

        details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
        error_code = _safe_provider_code(details.get("error"), "receipt_rejected")
        delivery.status = PushDelivery.Status.FAILED
        delivery.error_code = error_code
        delivery.error_message = (
            "Expo device is no longer registered."
            if error_code == "DeviceNotRegistered"
            else "Expo rejected the push receipt."
        )
        delivery.receipt_checked_at = now
        delivery.next_attempt_at = now
        delivery.updated_at = now
        failed += 1
        if error_code == "DeviceNotRegistered":
            deactivated_ids.add(delivery.device_id)

    PushDelivery.objects.bulk_update(
        deliveries,
        [
            "status", "attempt_count", "error_code", "error_message",
            "next_attempt_at", "receipt_checked_at", "delivered_at", "updated_at",
        ],
    )
    _deactivate_devices(deactivated_ids, now)
    return {"delivered": delivered, "failed": failed, "retried": retried, "deactivated": len(deactivated_ids)}


def process_mobile_push_receipts(*, limit: int = 1000) -> dict[str, int]:
    """Poll bounded Expo receipts without changing inbox read state."""
    if not _configured():
        return {"delivered": 0, "failed": 0, "retried": 0, "deactivated": 0, "skipped": 1}
    deliveries = _claim_receipt_deliveries(limit)
    if deliveries:
        _acquire_provider_slot(
            max(1, int(getattr(settings, "EXPO_PUSH_REQUESTS_PER_SECOND", 5)))
        )
    counts = _process_receipt_chunk(deliveries)
    if any(counts.values()):
        logger.info(
            "mobile push receipts processed",
            extra={"event": "mobile_push_receipts_processed", **counts},
        )
    return counts
