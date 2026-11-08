"""Bounded inspection of saved inbox, push and Telegram delivery outcomes."""

from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, QuerySet, Subquery, TextField, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, Substr
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import MobilePushDevice, PushDelivery, StudentNotification, TelegramAccount, TelegramNotificationDelivery
from .mobile_push_queries import missing_push_device_exists


def _delivery_count(model, foreign_key: str, statuses=None):
    query = model.objects.filter(**{foreign_key: OuterRef("pk")})
    if statuses:
        query = query.filter(status__in=statuses)
    return Coalesce(Subquery(query.order_by().values(foreign_key).annotate(n=Count("pk")).values("n")[:1]), 0)


def notification_history_queryset() -> QuerySet:
    now = timezone.now()
    query = StudentNotification.objects.annotate(
        history_push_total=_delivery_count(PushDelivery, "notification_id"),
        history_push_sent=_delivery_count(PushDelivery, "notification_id", [PushDelivery.Status.DELIVERED]),
        history_push_failed=_delivery_count(PushDelivery, "notification_id", [PushDelivery.Status.FAILED]),
        history_push_skipped=_delivery_count(PushDelivery, "notification_id", [PushDelivery.Status.SKIPPED]),
        history_push_pending=_delivery_count(PushDelivery, "notification_id", [
            PushDelivery.Status.QUEUED, PushDelivery.Status.SENDING, PushDelivery.Status.TICKETED,
        ]),
        history_telegram_total=_delivery_count(TelegramNotificationDelivery, "student_notification_id"),
        history_telegram_sent=_delivery_count(TelegramNotificationDelivery, "student_notification_id", [TelegramNotificationDelivery.Status.SENT]),
        history_telegram_failed=_delivery_count(TelegramNotificationDelivery, "student_notification_id", [TelegramNotificationDelivery.Status.FAILED]),
        history_telegram_skipped=_delivery_count(TelegramNotificationDelivery, "student_notification_id", [TelegramNotificationDelivery.Status.SKIPPED]),
        history_telegram_pending=_delivery_count(TelegramNotificationDelivery, "student_notification_id", [
            TelegramNotificationDelivery.Status.QUEUED, TelegramNotificationDelivery.Status.SENDING,
        ]),
        history_inbox_total=Case(When(inbox_visible=True, then=Value(1)), default=Value(0), output_field=IntegerField()),
        history_inbox_done=Case(When(
            inbox_visible=True,
            scheduled_for__lte=now,
            cancelled_at__isnull=True,
            withdrawn_at__isnull=True,
            then=Value(1),
        ), default=Value(0), output_field=IntegerField()),
        history_missing_push=Exists(missing_push_device_exists()),
        history_telegram_account=Exists(TelegramAccount.objects.filter(user_id=OuterRef("student_id"), is_active=True)),
    ).annotate(
        history_materializing=Case(When(
            Q(is_manual_redelivery=False, cancelled_at__isnull=True, withdrawn_at__isnull=True, scheduled_for__lte=now,
              student__is_active=True, student__application_status="active", student__role__role="student")
            & (Q(history_missing_push=True, push_expires_at__gt=now)
               | Q(history_telegram_total=0, history_telegram_account=True)), then=Value(True)), default=Value(False)),
        history_total=F("history_inbox_total") + F("history_push_total") + F("history_telegram_total"),
        history_succeeded=F("history_inbox_done") + F("history_push_sent") + F("history_telegram_sent"),
        history_failed=F("history_push_failed") + F("history_telegram_failed"),
        history_skipped=F("history_push_skipped") + F("history_telegram_skipped"),
        history_pending=F("history_push_pending") + F("history_telegram_pending"),
    ).annotate(
        history_processed=F("history_succeeded") + F("history_failed") + F("history_skipped"),
        history_status=Case(
            When(withdrawn_at__isnull=False, then=Value("withdrawn")),
            When(cancelled_at__isnull=False, then=Value("cancelled")),
            When(Q(scheduled_for__gt=now) | Q(history_materializing=True), then=Value("queued")),
            When(history_pending__gt=0, then=Value("running")),
            When(history_failed__gt=0, history_succeeded=0, then=Value("failed")),
            When(Q(history_failed__gt=0) | Q(history_skipped__gt=0), then=Value("partial")),
            default=Value("completed"),
        ),
    )
    return query


def original_notification(notification: StudentNotification) -> StudentNotification:
    if notification.is_manual_redelivery:
        if notification.redelivery_of_id is None:
            raise ValueError(_("The original notification is no longer available."))
        return StudentNotification.objects.get(pk=notification.redelivery_of_id)
    return notification


def original_audience(notification: StudentNotification, *, audience: str = "group", upper_pk: int | None = None) -> QuerySet:
    original = original_notification(notification)
    query = StudentNotification.objects.filter(is_manual_redelivery=False)
    if audience == "recipient":
        query = query.filter(pk=original.pk)
    else:
        if original.notification_type == "announcement" and original.announcement_id is None:
            raise ValueError(_("The original notification is no longer available."))
        query = query.filter(
            notification_type=original.notification_type, entity_id=original.entity_id,
            offering_id=original.offering_id, announcement_id=original.announcement_id,
            effective_opening_at=original.effective_opening_at,
            effective_closing_at=original.effective_closing_at,
            title_ar=original.title_ar, title_en=original.title_en,
            body_ar=original.body_ar, body_en=original.body_en,
        )
    return query.filter(pk__lte=upper_pk) if upper_pk else query


def notification_materializing_ids(notification_ids: list[int]) -> set[int]:
    """Return saved notifications still awaiting normal channel fan-out."""
    if not notification_ids:
        return set()
    return set(notification_history_queryset().filter(
        pk__in=notification_ids,
        history_materializing=True,
    ).values_list("pk", flat=True))


def notification_channel_history(notification_id: int) -> QuerySet:
    fields = (
        "row_id", "row_channel", "row_target", "row_status", "row_error",
        "row_attempts", "row_content", "row_created", "row_deletion_status",
        "row_deletion_error",
    )
    pushes = PushDelivery.objects.filter(notification_id=notification_id).order_by().annotate(
        row_id=Cast("pk", TextField()), row_channel=Value("push"),
        row_target=Concat(
            Case(
                When(device__platform=MobilePushDevice.Platform.IOS, then=Value(str(dict(MobilePushDevice.Platform.choices)[MobilePushDevice.Platform.IOS]))),
                When(device__platform=MobilePushDevice.Platform.ANDROID, then=Value(str(dict(MobilePushDevice.Platform.choices)[MobilePushDevice.Platform.ANDROID]))),
                default=Value(str(_("Device"))), output_field=TextField(),
            ),
            Value(" " + str(_("device")) + " #"), Cast("device_id", TextField()),
            output_field=TextField(),
        ),
        row_status=F("status"), row_error=Substr("error_message", 1, 500), row_attempts=F("attempt_count"),
        row_content=Value("", output_field=TextField()), row_created=F("created_at"),
        row_deletion_status=Value("", output_field=TextField()),
        row_deletion_error=Value("", output_field=TextField()),
    ).values(*fields)
    telegram = TelegramNotificationDelivery.objects.filter(student_notification_id=notification_id).order_by().annotate(
        row_id=Cast("pk", TextField()), row_channel=Value("telegram"), row_target=Value(str(_("Telegram account"))),
        row_status=F("status"), row_error=Substr("last_error", 1, 500), row_attempts=F("attempt_count"),
        row_content=F("content_snapshot"), row_created=F("created_at"),
        row_deletion_status=F("telegram_deletion_status"),
        row_deletion_error=Substr("telegram_deletion_error", 1, 500),
    ).values(*fields)
    return pushes.union(telegram, all=True).order_by("-row_created", "row_channel", "row_id")
