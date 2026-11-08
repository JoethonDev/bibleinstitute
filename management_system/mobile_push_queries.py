"""Bounded device query scopes shared by push delivery and history reads."""

from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Window
from django.db.models.functions import Coalesce, RowNumber

from .models import MobilePushDevice, PushDelivery


MAX_DEVICES_PER_STUDENT = 10


def missing_push_device_exists():
    """Devices still uncovered for a notification within the ranked send scope."""
    ranked_newer_devices = (
        MobilePushDevice.objects.filter(
            user_id=OuterRef("user_id"),
            is_active=True,
            disabled_at__isnull=True,
        )
        .filter(
            Q(last_seen_at__gt=OuterRef("last_seen_at"))
            | Q(last_seen_at=OuterRef("last_seen_at"), pk__gt=OuterRef("pk"))
        )
        .values("user_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return (
        MobilePushDevice.objects.filter(
            user_id=OuterRef("student_id"),
            is_active=True,
            disabled_at__isnull=True,
        )
        .annotate(
            newer_device_count=Coalesce(
                Subquery(ranked_newer_devices[:1]),
                0,
            )
        )
        .filter(newer_device_count__lt=MAX_DEVICES_PER_STUDENT)
        .annotate(_sn_notif_id=OuterRef("pk"))
        .filter(
            ~Exists(
                PushDelivery.objects.filter(
                    notification_id=OuterRef("_sn_notif_id"),
                    device_id=OuterRef("pk"),
                )
            )
        )
        .order_by()
    )


def active_push_devices():
    """Return the newest bounded active-device scope for each student."""
    return MobilePushDevice.objects.filter(
        is_active=True,
        disabled_at__isnull=True,
    ).annotate(
        mobile_device_row=Window(
            expression=RowNumber(),
            partition_by=[F("user_id")],
            order_by=[F("last_seen_at").desc(), F("pk").desc()],
        ),
    ).filter(mobile_device_row__lte=MAX_DEVICES_PER_STUDENT)
