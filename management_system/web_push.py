"""Bounded browser Web Push delivery for durable student notifications."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.urls import NoReverseMatch, reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from pywebpush import WebPushException, webpush

from .models import StudentNotification, WebPushDelivery, WebPushSubscription

logger = logging.getLogger(__name__)
SCAN_LIMIT = 1000
MAX_ATTEMPTS = 3
RETRY_DELAYS = (60, 300, 900)
SAFE_URL_PREFIX = "/"


def _configured() -> bool:
    return bool(
        settings.WEB_PUSH_ENABLED
        and settings.WEB_PUSH_VAPID_PRIVATE_KEY
        and settings.WEB_PUSH_VAPID_PUBLIC_KEY
    )


def _retry_delay(attempt_count: int) -> int:
    return RETRY_DELAYS[min(max(attempt_count - 1, 0), len(RETRY_DELAYS) - 1)]


def _notification_url(notification: StudentNotification, language: str) -> str:
    with translation.override(language):
        try:
            if notification.navigation_type == StudentNotification.NavigationType.LESSON:
                return reverse(
                    "lesson-details",
                    args=[notification.offering_id, notification.entity_id],
                )
            if notification.navigation_type == StudentNotification.NavigationType.QUIZ:
                return reverse(
                    "quiz-details",
                    args=[notification.offering_id, notification.entity_id],
                )
        except NoReverseMatch:
            pass
        return reverse("student-notifications")


def _payload(notification: StudentNotification, language: str) -> str:
    # Show the real localized notification title and body. The body is capped
    # to keep lock-screen payloads bounded.
    is_arabic = language == "ar"
    title = (notification.title_ar if is_arabic else notification.title_en).strip()
    body = (notification.body_ar if is_arabic else notification.body_en).strip()
    with translation.override(language):
        if not title:
            title = str(_("Bible Institute"))
        if not body:
            body = str(_("You have a new notification."))
    return json.dumps(
        {
            "title": title,
            "body": body[:500],
            "notification_id": notification.pk,
            "url": _notification_url(notification, language),
        },
        ensure_ascii=False,
    )


def _subscription_info(subscription: WebPushSubscription) -> dict:
    return {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }


def _active_delivery_scan():
    missing_subscription = WebPushSubscription.objects.filter(
        user=OuterRef("student"),
        is_active=True,
        disabled_at__isnull=True,
        enabled_at__lte=OuterRef("created_at"),
    ).exclude(deliveries__notification_id=OuterRef("pk"))
    return StudentNotification.objects.filter(
        student__is_active=True,
        student__role__role="student",
        cancelled_at__isnull=True,
        withdrawn_at__isnull=True,
        scheduled_for__lte=timezone.now(),
        push_expires_at__gt=timezone.now(),
    ).filter(Exists(missing_subscription))


def _materialize_due(limit: int) -> int:
    now = timezone.now()
    notifications = list(
        _active_delivery_scan()
        .select_related("student")
        .prefetch_related("student__web_push_subscriptions")
        .order_by("scheduled_for", "pk")[: max(1, min(limit, SCAN_LIMIT))]
    )
    if not notifications:
        return 0
    existing = set(
        WebPushDelivery.objects.filter(
            notification_id__in=[notification.pk for notification in notifications]
        ).values_list("notification_id", "subscription_id")
    )
    rows = []
    for notification in notifications:
        subscriptions = [
            subscription
            for subscription in notification.student.web_push_subscriptions.all()
            if subscription.is_active and subscription.disabled_at is None
        ]
        for subscription in subscriptions:
            if len(rows) >= SCAN_LIMIT:
                break
            if (notification.pk, subscription.pk) in existing:
                continue
            rows.append(
                WebPushDelivery(
                    notification_id=notification.pk,
                    subscription_id=subscription.pk,
                    next_attempt_at=now,
                )
            )
        if len(rows) >= SCAN_LIMIT:
            break
    if rows:
        WebPushDelivery.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _expire_pending() -> int:
    now = timezone.now()
    return WebPushDelivery.objects.filter(
        status=WebPushDelivery.Status.QUEUED,
        notification__push_expires_at__lte=now,
    ).update(
        status=WebPushDelivery.Status.SKIPPED,
        error_code="notification_expired",
        next_attempt_at=now,
        updated_at=now,
    )


def _recover_stale() -> int:
    cutoff = timezone.now() - timedelta(seconds=settings.WEB_PUSH_DELIVERY_LEASE_SECONDS)
    now = timezone.now()
    return WebPushDelivery.objects.filter(
        status=WebPushDelivery.Status.SENDING,
        updated_at__lt=cutoff,
    ).update(
        status=WebPushDelivery.Status.QUEUED,
        error_code="stale_sending",
        next_attempt_at=now,
        updated_at=now,
    )


def _claim_due(limit: int) -> list[WebPushDelivery]:
    now = timezone.now()
    with transaction.atomic():
        candidate_ids = list(
            WebPushDelivery.objects.filter(
                status=WebPushDelivery.Status.QUEUED,
                next_attempt_at__lte=now,
                attempt_count__lt=MAX_ATTEMPTS,
                notification__cancelled_at__isnull=True,
                notification__withdrawn_at__isnull=True,
                notification__scheduled_for__lte=now,
                notification__push_expires_at__gt=now,
                notification__created_at__gte=F("subscription__enabled_at"),
                notification__student__is_active=True,
                notification__student__role__role="student",
                subscription__is_active=True,
                subscription__disabled_at__isnull=True,
                subscription__user_id=F("notification__student_id"),
            )
            .order_by("next_attempt_at", "pk")
            .values_list("pk", flat=True)[: max(1, min(limit, SCAN_LIMIT))]
        )
        if not candidate_ids:
            return []
        deliveries = list(
            WebPushDelivery.objects.select_for_update(skip_locked=True)
            .select_related("notification", "subscription")
            .filter(
                pk__in=candidate_ids,
                subscription__user_id=F("notification__student_id"),
            )
            .order_by("next_attempt_at", "pk")[: max(1, min(limit, SCAN_LIMIT))]
        )
        for delivery in deliveries:
            delivery.status = WebPushDelivery.Status.SENDING
            delivery.attempt_count += 1
            delivery.last_attempt_at = now
            delivery.updated_at = now
        if deliveries:
            WebPushDelivery.objects.bulk_update(
                deliveries,
                ["status", "attempt_count", "last_attempt_at", "updated_at"],
            )
    return deliveries


def _mark_retry(delivery: WebPushDelivery, code: str, message: str, now) -> None:
    if delivery.attempt_count >= MAX_ATTEMPTS:
        delivery.status = WebPushDelivery.Status.FAILED
        delivery.next_attempt_at = now
    else:
        delivery.status = WebPushDelivery.Status.QUEUED
        delivery.next_attempt_at = now + timedelta(
            seconds=_retry_delay(delivery.attempt_count)
        )
    delivery.error_code = code
    delivery.error_message = message
    delivery.updated_at = now
    delivery.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "next_attempt_at",
            "updated_at",
        ]
    )


def _send_one(delivery: WebPushDelivery) -> bool:
    now = timezone.now()
    try:
        response = webpush(
            subscription_info=_subscription_info(delivery.subscription),
            data=_payload(delivery.notification, delivery.subscription.language),
            vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": settings.WEB_PUSH_VAPID_SUBJECT
                or f"mailto:{settings.DEFAULT_FROM_EMAIL}"
            },
            timeout=settings.WEB_PUSH_TIMEOUT_SECONDS,
            ttl=24 * 60 * 60,
        )
    except WebPushException as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code in (404, 410):
            WebPushSubscription.objects.filter(
                pk=delivery.subscription_id
            ).update(
                is_active=False,
                disabled_at=now,
                updated_at=now,
            )
            delivery.status = WebPushDelivery.Status.FAILED
            delivery.error_code = "subscription_gone"
            delivery.error_message = "The push subscription is no longer registered."
            delivery.next_attempt_at = now
            delivery.updated_at = now
            delivery.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "next_attempt_at",
                    "updated_at",
                ]
            )
            return False
        if status_code == 429 or (status_code and 500 <= status_code <= 599):
            _mark_retry(delivery, f"http_{status_code}", "Web push will be retried.", now)
            return False
        delivery.status = WebPushDelivery.Status.FAILED
        delivery.error_code = f"http_{status_code}" if status_code else "provider_error"
        delivery.error_message = "The push service rejected the notification."
        delivery.next_attempt_at = now
        delivery.updated_at = now
        delivery.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "next_attempt_at",
                "updated_at",
            ]
        )
        return False
    except Exception:
        logger.exception("Unexpected Web Push delivery failure")
        _mark_retry(delivery, "transport_error", "Web push will be retried.", now)
        return False
    delivery.status = WebPushDelivery.Status.DELIVERED
    delivery.delivered_at = now
    delivery.error_code = ""
    delivery.error_message = ""
    delivery.next_attempt_at = now
    delivery.updated_at = now
    delivery.save(
        update_fields=[
            "status",
            "delivered_at",
            "error_code",
            "error_message",
            "next_attempt_at",
            "updated_at",
        ]
    )
    WebPushSubscription.objects.filter(pk=delivery.subscription_id).update(
        last_success_at=now,
        updated_at=now,
    )
    return True


def dispatch_due_web_push(*, limit: int = SCAN_LIMIT) -> dict[str, int]:
    """Materialize and send one bounded batch of browser push deliveries."""
    if not _configured():
        return {
            "materialized": 0,
            "delivered": 0,
            "failed": 0,
            "retried": 0,
            "skipped": 0,
        }
    expired = _expire_pending()
    _recover_stale()
    created = _materialize_due(limit)
    deliveries = _claim_due(
        min(max(1, int(limit)), settings.WEB_PUSH_BATCH_SIZE)
    )
    delivered = failed = retried = 0
    for delivery in deliveries:
        if _send_one(delivery):
            delivered += 1
        elif delivery.status == WebPushDelivery.Status.FAILED:
            failed += 1
        else:
            retried += 1
    totals = {
        "materialized": created,
        "delivered": delivered,
        "failed": failed,
        "retried": retried,
        "skipped": expired,
    }
    if any(totals.values()):
        logger.info(
            "browser web push dispatched",
            extra={"event": "web_push_dispatched", **totals},
        )
    return totals
