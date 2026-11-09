"""Read-only, SQL-paged projection of the existing background operation records."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from django.db.models import BigIntegerField, Case, CharField, DateTimeField, F, Q, QuerySet, TextField, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, NullIf, Substr
from django.db.models.fields.json import KeyTextTransform
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import get_language, gettext as _, gettext_lazy

from .models import (
    AdminOperationJob, Announcement, GraduationGalleryJob, MediaProcessingJob,
    MediaProcessingPhase, MediaAttachmentStatus, MediaCleanupStatus, QuizOperationJob, TelegramBroadcast, StudentNotification, User,
)
from .utils.search import normalize_search_text, normalized_contains_q

from .notification_history import notification_history_queryset


PAGE_SIZE = 25
FAMILIES = (
    ("management", gettext_lazy("Management operations")),
    ("quiz", gettext_lazy("Quiz operations")),
    ("media", gettext_lazy("Media processing")),
    ("gallery", gettext_lazy("Gallery uploads")),
    ("announcement", gettext_lazy("Announcement generation")),
    ("telegram", gettext_lazy("Telegram broadcasts")),
    ("notification", gettext_lazy("Notification history")),
)
STATUSES = (
    ("awaiting_upload", gettext_lazy("Awaiting upload")),
    ("queued", gettext_lazy("Queued")),
    ("running", gettext_lazy("Running")),
    ("waiting", gettext_lazy("Waiting for delivery")),
    ("completed", gettext_lazy("Completed")),
    ("partial", gettext_lazy("Completed with failures")),
    ("failed", gettext_lazy("Failed")),
    ("cancelled", gettext_lazy("Cancelled")),
    ("withdrawn", gettext_lazy("Withdrawn")),
)
ACTIVE_STATUSES = frozenset({"awaiting_upload", "queued", "running", "waiting"})
MODELS = {
    "management": AdminOperationJob, "quiz": QuizOperationJob,
    "media": MediaProcessingJob, "gallery": GraduationGalleryJob,
    "announcement": Announcement, "telegram": TelegramBroadcast,
    "notification": StudentNotification,
}


def operation_choices() -> list[tuple[str, str]]:
    return [
        *[(f"management:{value}", str(label)) for value, label in AdminOperationJob.Kind.choices],
        *[(f"quiz:{value}", str(label)) for value, label in QuizOperationJob.Kind.choices],
        *[(f"notification:{value}", str(label)) for value, label in StudentNotification.NotificationType.choices],
        *[(family, str(label)) for family, label in FAMILIES if family not in {"management", "quiz", "notification"}],
    ]


def valid_job_id(family: str, value: str) -> str:
    if family not in MODELS:
        raise ValueError("Unknown job family")
    if family in {"management", "quiz", "media", "gallery"}:
        return str(uuid.UUID(value))
    if not value.isascii() or not value.isdigit() or len(value) > 19 or not 0 < int(value) <= 9_223_372_036_854_775_807:
        raise ValueError("Invalid job ID")
    return str(int(value))


def _projection(family: str, *, ids: list[str] | None = None, search: str = "", operation: str = "",
                status: str = "", since: date | None = None, until: date | None = None,
                notification_announcement=None, redelivery_job=None) -> QuerySet:
    model = MODELS[family]
    query = model.objects.order_by()
    actor = "actor__username" if family in {"management", "quiz"} else "created_by__username"
    identity = "public_id" if family in {"media", "gallery"} else "pk"
    kind = F("kind") if family in {"management", "quiz"} else Value(family)
    processed = total = Value(0)
    succeeded = failed = skipped = Value(None, output_field=BigIntegerField())
    target = Value("")
    next_poll = Value(None, output_field=DateTimeField())
    phase = attachment = cleanup = Value("")
    updated = F("updated_at")
    error_field = "error"
    status_map = {"running": ["processing", "uploading", "verifying", "sending"], "completed": ["succeeded"]}
    monitor_status = None
    if family == "management":
        processed, total = F("processed"), F("total")
        succeeded, failed, skipped = F("succeeded"), F("failed"), F("skipped")
        level_name = "name_en" if (get_language() or "ar").startswith("en") else "name_ar"
        level_fallback = "name_ar" if level_name == "name_en" else "name_en"
        target = Concat(
            "academic_year_level__academic_year__name", Value(" / "),
            Coalesce(NullIf(F(f"academic_year_level__level__{level_name}"), Value("")),
                     NullIf(F(f"academic_year_level__level__{level_fallback}"), Value("")),
                     Cast("academic_year_level__level__ordering", TextField()), output_field=TextField()),
            Value(" / "), "course_offering__course__name", output_field=TextField(),
        )
        target = Case(
            When(kind="legacy_user_import", then=KeyTextTransform("filename", "input")),
            When(kind="historical_intake", then=KeyTextTransform("source_file", "input")),
            When(kind="attendance_reconcile", then=KeyTextTransform("date", "input")),
            When(kind="storage_rename", then=Concat(KeyTextTransform("old_key", "input"), Value(" -> "), KeyTextTransform("new_key", "input"), output_field=TextField())),
            When(kind="storage_delete", then=KeyTextTransform("folder_path", "input")),
            When(kind="storage_usage_refresh", then=Coalesce(NullIf(KeyTextTransform("prefix", "input"), Value("")), Value(str(_("All storage"))), output_field=TextField())),
            default=target, output_field=TextField(),
        )
    elif family == "quiz":
        processed, total, skipped = F("processed"), F("total"), F("skipped")
        target = Value(str(_("Selected submissions")))
    elif family == "media":
        processed, total = F("progress"), Value(100)
        target = F("original_filename")
        error_field = "error_message"
        phase, attachment, cleanup = F("phase"), F("attachment_status"), F("cleanup_status")
        updated = Greatest(*[Coalesce(field, "created_at") for field in (
            "finished_at", "last_heartbeat_at", "source_acknowledged_at", "last_dispatched_at",
            "cleanup_requested_at", "cleanup_started_at", "cleanup_finished_at", "stop_requested_at",
        )])
    elif family == "gallery":
        processed, total, failed = F("processed_files"), F("total_files"), F("failed_files")
        target, error_field = F("original_filename"), "last_error"
    elif family == "announcement":
        total = F("recipient_count")
        processed = Case(When(status="completed", then=F("recipient_count")), default=Value(0))
        target, error_field = F("title"), "last_error"
    elif family == "telegram":
        query = query.exclude(status=TelegramBroadcast.Status.DRAFT)
        processed, total = F("sent_count") + F("failed_count") + F("skipped_count"), F("recipient_count")
        succeeded, failed, skipped = F("sent_count"), F("failed_count"), F("skipped_count")
        target, error_field = F("academic_year__name"), "last_error"
        monitor_status = Case(When(status="completed", skipped_count__gt=0, then=Value("partial")), default=F("status"))
    elif family == "notification":

        query = notification_history_queryset().order_by()
        actor = "redelivery_job__actor__username"
        kind = F("notification_type")
        processed, total = F("history_processed"), F("history_total")
        succeeded, failed, skipped = F("history_succeeded"), F("history_failed"), F("history_skipped")
        title = "title_en" if (get_language() or "ar").startswith("en") else "title_ar"
        target = Concat(title, Value(" - "), "student__username", output_field=TextField())
        error_field = Value("")
        monitor_status = F("history_status")
        next_poll = F("scheduled_for")
        phase = Case(
            When(scheduled_for__gt=timezone.now(), then=Value("scheduled")),
            When(history_materializing=True, then=Value("preparing")),
            default=Value(""),
        )
        if notification_announcement:
            query = query.filter(announcement_id=notification_announcement)
        if redelivery_job:
            query = query.filter(redelivery_job_id=redelivery_job)
    if ids is not None:
        query = query.filter(**{f"{identity}__in": ids})
    if since:
        query = query.filter(created_at__gte=timezone.make_aware(datetime.combine(since, time.min)))
    if until and until < date.max:
        query = query.filter(created_at__lt=timezone.make_aware(datetime.combine(until + timedelta(days=1), time.min)))
    query = query.annotate(
        monitor_family=Value(family, output_field=CharField()),
        monitor_id=Cast(identity, TextField()),
        monitor_kind=Cast(kind, TextField()),
        monitor_actor=Coalesce(actor, Value(""), output_field=TextField()),
        monitor_target=Substr(Cast(target, TextField()), 1, 1024),
        monitor_status=monitor_status if monitor_status is not None else Case(*[
            When(status__in=values, then=Value(value)) for value, values in status_map.items()
        ], default=F("status"), output_field=CharField()),
        monitor_processed=Cast(processed, BigIntegerField()),
        monitor_total=Cast(total, BigIntegerField()),
        monitor_succeeded=Cast(succeeded, BigIntegerField()),
        monitor_failed=Cast(failed, BigIntegerField()),
        monitor_skipped=Cast(skipped, BigIntegerField()),
        monitor_error=Substr(error_field, 1, 2000),
        monitor_phase=phase, monitor_attachment=attachment, monitor_cleanup=cleanup,
        monitor_next_poll=next_poll,
        monitor_created=F("created_at"), monitor_updated=updated,
    )
    if operation:
        selected_family, _separator, selected_kind = operation.partition(":")
        if selected_family != family:
            query = query.none()
        elif selected_kind:
            query = query.filter(**{"notification_type" if family == "notification" else "kind": selected_kind})
    if status:
        query = query.filter(history_failed__gt=0) if family == "notification" and status == "delivery_failed" else query.filter(monitor_status=status)
    if search:
        matched_kinds = [value.split(":")[-1] for value, label in operation_choices()
                         if value.split(":")[0] == family and search in normalize_search_text(label)]
        family_match = search in normalize_search_text(str(dict(FAMILIES)[family]))
        if not family_match:
            if family == "notification":
                search_q = normalized_contains_q(("title_ar", "title_en"), search) | Q(student_id__in=User.objects.filter(
                    normalized_contains_q(("username", "first_name", "last_name"), search),
                ).values("pk"))
                if search.isascii() and search.isdigit() and len(search) < 19:
                    search_q |= Q(pk=int(search))
            else:
                search_q = normalized_contains_q((actor, "monitor_target", "monitor_id"), search)
            if family in {"management", "quiz", "notification"} and matched_kinds:
                search_q |= Q(**{"notification_type__in" if family == "notification" else "kind__in": matched_kinds})
            query = query.filter(search_q)
    return query.values(
        "monitor_family", "monitor_id", "monitor_kind", "monitor_actor", "monitor_target",
        "monitor_status", "monitor_processed", "monitor_total", "monitor_succeeded",
        "monitor_failed", "monitor_skipped", "monitor_error", "monitor_created", "monitor_updated",
        "monitor_phase", "monitor_attachment", "monitor_cleanup", "monitor_next_poll",
    )


def monitored_jobs(*, selection: dict[str, list[str]] | None = None, **filters) -> QuerySet:
    """One UNION ALL query; callers must paginate or supply at most 25 exact IDs."""
    families = [filters.pop("family")] if filters.get("family") else [name for name in MODELS if name != "notification"]
    filters.pop("family", None)
    if filters.get("operation", "").startswith("notification:"):
        families = ["notification"]
    if selection is not None:
        families = list(selection)
    queries = [_projection(family, ids=selection[family] if selection is not None else None, **filters)
               for family in families]
    first, *rest = queries
    return first.union(*rest, all=True).order_by("-monitor_created", "monitor_family", "monitor_id") if rest else first.order_by("-monitor_created", "monitor_id")


def readable_job(row: dict) -> dict:
    job = {key.removeprefix("monitor_"): value for key, value in row.items()}
    family = job["family"]
    labels = dict(AdminOperationJob.Kind.choices if family == "management" else QuizOperationJob.Kind.choices if family == "quiz" else StudentNotification.NotificationType.choices) if family in {"management", "quiz", "notification"} else {}
    job["label"] = str(labels.get(job["kind"], dict(FAMILIES)[family]))
    job["family_label"] = str(dict(FAMILIES)[family])
    job["status_label"] = str(dict(STATUSES).get(job["status"], job["status"]))
    job["active"] = job["status"] in ACTIVE_STATUSES
    notes = []
    if family == "media":
        notes.append(str(dict(MediaProcessingPhase.choices).get(job["phase"], "")))
        if job["attachment"] != MediaAttachmentStatus.NOT_REQUESTED:
            notes.append(_("Lesson attachment: %(status)s") % {"status": dict(MediaAttachmentStatus.choices).get(job["attachment"], "")})
        if job["cleanup"] != MediaCleanupStatus.NOT_REQUESTED:
            notes.append(_("Cleanup: %(status)s") % {"status": dict(MediaCleanupStatus.choices).get(job["cleanup"], "")})
        job["active"] = (job["active"] or job["cleanup"] in {"queued", "running"}
                         or (job["status"] == "completed" and job["attachment"] == "pending"))
    job["phase_label"] = " / ".join(note for note in notes if note)
    if family == "announcement":
        job["phase_label"] = str(_("Inbox generation; view notification history for delivery receipts."))
    if family == "notification" and job["phase"] == "scheduled":
        job["phase_label"] = str(_("Scheduled for later"))
    job["target"] = (job["target"] or "").strip(" / ") or str(_("Not specified"))
    job["actor"] = job["actor"] or str(_("System"))
    total, processed = job["total"], job["processed"]
    job["percent"] = min(100, round(processed * 100 / total)) if total else (100 if job["status"] == "completed" else 0)
    job["progress_label"] = (f"{processed}%" if family == "media" else
                             _("%(processed)s of %(total)s") % {"processed": processed, "total": total}
                             if total else str(_("No progress reported")))
    if family == "notification" and job["phase"] == "preparing":
        job["progress_label"] = str(_("Preparing delivery records"))
        job["percent"] = 0
    job["detail_url"] = reverse("background-job-detail", args=[family, job["id"]])
    job["updated_label"] = formats.date_format(timezone.localtime(job["updated"]), "SHORT_DATETIME_FORMAT")
    return job
