"""Bounded inspection of saved inbox, push and Telegram delivery outcomes."""

from datetime import date, datetime, time, timedelta

from django.db.models import (
    Case, CharField, Count, Exists, F, IntegerField, Max, Min, OuterRef, Q, QuerySet, Subquery, TextField, Value, When,
)
from django.db.models.functions import Cast, Coalesce, Concat, Substr
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import MobilePushDevice, PushDelivery, StudentNotification, TelegramAccount, TelegramNotificationDelivery
from .mobile_push_queries import missing_push_device_exists
from .utils.search import normalized_contains_q


# The identity of one notification message. Every recipient row of the same
# event shares these fields; this is the tuple original_audience() already
# targets, so the grouped history and a redelivery always agree on "one message".
NOTIFICATION_GROUP_FIELDS = (
    "notification_type",
    "entity_id",
    "offering_id",
    "announcement_id",
    "effective_opening_at",
    "effective_closing_at",
    "title_ar",
    "title_en",
    "body_ar",
    "body_en",
)

PUSH_PENDING_STATUSES = (PushDelivery.Status.QUEUED, PushDelivery.Status.SENDING, PushDelivery.Status.TICKETED)
TELEGRAM_PENDING_STATUSES = (TelegramNotificationDelivery.Status.QUEUED, TelegramNotificationDelivery.Status.SENDING)


def notification_message_identity(notification: StudentNotification) -> dict[str, object]:
    """Return the filter that selects every recipient of the same message."""
    return {field: getattr(notification, field) for field in NOTIFICATION_GROUP_FIELDS}



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
        query = query.filter(**notification_message_identity(original))
    return query.filter(pk__lte=upper_pk) if upper_pk else query


def notification_history_groups(
    *,
    operation: str = "",
    status: str = "",
    search: str = "",
    kinds: tuple[str, ...] = (),
    since: date | None = None,
    until: date | None = None,
    notification_announcement: int | None = None,
    redelivery_job=None,
) -> QuerySet:
    """One row per notification message: recipients plus channel outcomes.

    Grouping happens in SQL over NOTIFICATION_GROUP_FIELDS, so the history page
    paginates messages instead of the per-recipient rows a broadcast creates.
    Every count is a distinct aggregate over the group's deliveries.
    """
    now = timezone.now()
    query = StudentNotification.objects.all()
    if operation:
        _family, _separator, kind = operation.partition(":")
        if kind:
            query = query.filter(notification_type=kind)
    if notification_announcement:
        query = query.filter(announcement_id=notification_announcement)
    if redelivery_job:
        query = query.filter(redelivery_job_id=redelivery_job)
    if since:
        query = query.filter(created_at__gte=timezone.make_aware(datetime.combine(since, time.min)))
    if until and until < date.max:
        query = query.filter(created_at__lt=timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min)))
    # The search box matches either a message title or the operation label, so a
    # term like the Arabic word for "announcement" finds those messages even when
    # the title itself does not contain it.
    search_match = Q()
    if search:
        search_match |= normalized_contains_q(("title_ar", "title_en"), search)
    if kinds:
        search_match |= Q(notification_type__in=kinds)
    if search or kinds:
        query = query.filter(search_match)
    groups = query.values(*NOTIFICATION_GROUP_FIELDS).annotate(
        group_id=Min("pk"),
        recipients=Count("pk", distinct=True),
        created_first=Min("created_at"),
        created_last=Max("created_at"),
        scheduled_count=Count("pk", filter=Q(scheduled_for__gt=now), distinct=True),
        cancelled_count=Count("pk", filter=Q(cancelled_at__isnull=False), distinct=True),
        withdrawn_count=Count("pk", filter=Q(withdrawn_at__isnull=False), distinct=True),
        redelivered_count=Count("pk", filter=Q(is_manual_redelivery=True), distinct=True),
        inbox_total=Count("pk", filter=Q(inbox_visible=True), distinct=True),
        inbox_done=Count("pk", filter=Q(
            inbox_visible=True,
            scheduled_for__lte=now,
            cancelled_at__isnull=True,
            withdrawn_at__isnull=True,
        ), distinct=True),
        push_total=Count("push_deliveries", distinct=True),
        push_sent=Count("push_deliveries", filter=Q(push_deliveries__status=PushDelivery.Status.DELIVERED), distinct=True),
        push_failed=Count("push_deliveries", filter=Q(push_deliveries__status=PushDelivery.Status.FAILED), distinct=True),
        push_skipped=Count("push_deliveries", filter=Q(push_deliveries__status=PushDelivery.Status.SKIPPED), distinct=True),
        push_pending=Count("push_deliveries", filter=Q(push_deliveries__status__in=PUSH_PENDING_STATUSES), distinct=True),
        telegram_total=Count("telegram_deliveries", distinct=True),
        telegram_sent=Count("telegram_deliveries", filter=Q(
            telegram_deliveries__status=TelegramNotificationDelivery.Status.SENT,
        ), distinct=True),
        telegram_failed=Count("telegram_deliveries", filter=Q(
            telegram_deliveries__status=TelegramNotificationDelivery.Status.FAILED,
        ), distinct=True),
        telegram_skipped=Count("telegram_deliveries", filter=Q(
            telegram_deliveries__status=TelegramNotificationDelivery.Status.SKIPPED,
        ), distinct=True),
        telegram_pending=Count("telegram_deliveries", filter=Q(
            telegram_deliveries__status__in=TELEGRAM_PENDING_STATUSES,
        ), distinct=True),
    ).annotate(
        group_total=F("inbox_total") + F("push_total") + F("telegram_total"),
        group_succeeded=F("inbox_done") + F("push_sent") + F("telegram_sent"),
        group_failed=F("push_failed") + F("telegram_failed"),
        group_skipped=F("push_skipped") + F("telegram_skipped"),
        group_pending=F("push_pending") + F("telegram_pending"),
    ).annotate(
        group_processed=F("group_succeeded") + F("group_failed") + F("group_skipped"),
    ).annotate(
        group_status=Case(
            When(recipients=F("withdrawn_count"), then=Value("withdrawn")),
            When(recipients=F("cancelled_count"), then=Value("cancelled")),
            When(Q(scheduled_count=F("recipients")) & Q(group_processed=0) & Q(group_pending=0), then=Value("queued")),
            When(group_pending__gt=0, then=Value("running")),
            When(Q(group_failed__gt=0) & Q(group_succeeded=0), then=Value("failed")),
            When(Q(group_failed__gt=0) | Q(group_skipped__gt=0), then=Value("partial")),
            When(Q(group_processed=0) & Q(group_total=0), then=Value("queued")),
            default=Value("completed"),
            output_field=CharField(),
        ),
    )
    if status == "delivery_failed":
        return groups.filter(group_failed__gt=0).order_by("-created_last", "-group_id")
    if status:
        return groups.filter(group_status=status).order_by("-created_last", "-group_id")
    return groups.order_by("-created_last", "-group_id")


def notification_message_recipients(group_id: int, *, search: str = "") -> tuple[StudentNotification | None, QuerySet]:
    """Return one message's recipient rows from any notification id in the group.

    The page links use the group's smallest id; any id of the same message
    resolves to the same recipients, so a stale link still lands on the message.
    ``search`` narrows the recipients of that one message, never the group.
    """
    notification = StudentNotification.objects.filter(pk=group_id).only(
        *NOTIFICATION_GROUP_FIELDS, "pk",
    ).first()
    if notification is None:
        return None, StudentNotification.objects.none()
    recipients = notification_history_queryset().filter(
        **notification_message_identity(notification),
    ).select_related("student")
    if search:
        recipients = recipients.filter(normalized_contains_q(
            ("student__username", "student__first_name", "student__last_name", "student__email"),
            search,
        ))
    return notification, recipients.order_by("-created_at", "pk")


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
