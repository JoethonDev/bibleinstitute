"""Admin-only inspection of saved background jobs; no execution or retry actions."""

from django import forms
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from .admin_operations import ARTIFACT_TTL_HOURS, DOWNLOADABLE_EXPORT_KINDS
from .job_monitor import FAMILIES, PAGE_SIZE, STATUSES, monitored_jobs, operation_choices, readable_job, valid_job_id
from .models import AdminOperationJob, Announcement, PushDelivery, StudentNotification, TelegramBroadcast, TelegramNotificationDelivery
from .utils.decorators import can_manage_academic_setup, capability_required
from .utils.helpers import generate_breadcrumb, pagination_query_string, render_page
from .utils.search import normalize_search_text


class JobFilters(forms.Form):
    q = forms.CharField(label=_("Search jobs"), max_length=100, required=False,
                        widget=forms.TextInput(attrs={"type": "search", "class": "form-control"}))
    family = forms.ChoiceField(label=_("Job category"), required=False)
    operation = forms.ChoiceField(label=_("Operation"), required=False)
    status = forms.ChoiceField(label=_("Status"), required=False)
    since = forms.DateField(label=_("Created from"), required=False,
                           widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    until = forms.DateField(label=_("Created through"), required=False,
                           widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    notification_announcement = forms.IntegerField(required=False, min_value=1, max_value=9_223_372_036_854_775_807, widget=forms.HiddenInput)
    redelivery_job = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        notifications_only = kwargs.pop("notifications_only", False)
        super().__init__(*args, **kwargs)
        families = [(value, label) for value, label in FAMILIES if (value == "notification") == notifications_only]
        operations = [(value, label) for value, label in operation_choices() if value.startswith("notification:") == notifications_only]
        for field, choices in (("family", families), ("operation", operations), ("status", STATUSES)):
            self.fields[field].choices = [("", _("All")), *choices]
            self.fields[field].widget.attrs["class"] = "form-select"
        if notifications_only:
            self.fields["family"].widget = forms.HiddenInput()
            self.fields["status"].choices.append(("delivery_failed", _("Has failed deliveries")))

    def clean(self):
        data = super().clean()
        if data.get("since") and data.get("until") and data["since"] > data["until"]:
            self.add_error("until", _("The end date must be on or after the start date."))
        if data.get("family") and data.get("operation") and data["operation"].split(":")[0] != data["family"]:
            self.add_error("operation", _("Choose an operation in the selected category."))
        return data


def _response(request, context, detail=False):
    history = context.get("notification_history") or (detail and context["job"]["family"] == "notification")
    list_url = reverse("notification-history" if history else "background-jobs")
    crumbs = [(_("Admin"), reverse("admin-panel")),
              (_("Notification history") if history else _("Background jobs"), list_url if detail else None)]
    if detail:
        crumbs.append((context["job"]["label"], None))
    response = render_page(request, "background_jobs.html",
                           "partials/background_job_detail_content.html" if detail else "partials/background_jobs_content.html",
                           {**context, "job_detail": detail, "list_url": list_url, "breadcrumb_items": generate_breadcrumb(crumbs)})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@capability_required(can_manage_academic_setup)
def background_jobs(request, notifications_only=False):
    params = request.GET.copy()
    if notifications_only:
        params["family"] = "notification"
    form = JobFilters(params, notifications_only=notifications_only)
    page = None
    if form.is_valid():
        data = dict(form.cleaned_data)
        data["search"] = normalize_search_text(data.pop("q"))
        page = Paginator(monitored_jobs(**data), PAGE_SIZE).get_page(request.GET.get("page", "1"))
        page.object_list = [readable_job(row) for row in page.object_list]
    return _response(request, {"filter_form": form, "jobs_page": page,
                              "pagination_query": pagination_query_string(request), "notification_history": notifications_only})


@require_GET
@capability_required(can_manage_academic_setup)
def background_job_status_batch(request):
    selection = {}
    raw = request.GET.getlist("job")
    if not raw or len(raw) > PAGE_SIZE:
        return JsonResponse({"error": _("Invalid selection flags.")}, status=400)
    try:
        for value in raw:
            family, job_id = value.split(":", 1)
            selection.setdefault(family, []).append(valid_job_id(family, job_id))
    except (ValueError, AttributeError):
        return JsonResponse({"error": _("Invalid selection flags.")}, status=400)
    jobs = [readable_job(row) for row in monitored_jobs(selection=selection)]
    response = JsonResponse({"jobs": jobs})
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@capability_required(can_manage_academic_setup)
def background_job_detail(request, family, job_id):
    return _job_detail_response(request, family, job_id)


def _job_detail_response(request, family, job_id, redelivery_form=None):
    try:
        job_id = valid_job_id(family, job_id)
    except ValueError:
        raise Http404
    row = monitored_jobs(selection={family: [job_id]}).first()
    if row is None:
        raise Http404
    job = readable_job(row)
    errors, warning, download_url = [], "", ""
    if family == "management":
        from django.db.models.fields.json import KeyTextTransform, KeyTransform
        from django.utils import timezone
        from datetime import timedelta

        extra = AdminOperationJob.objects.filter(pk=job_id).annotate(
            monitor_warning=KeyTextTransform("warning", "result"),
            monitor_errors=KeyTransform("row_errors", "result"),
        ).values("monitor_warning", "monitor_errors", "kind", "status", "artifact_key", "finished_at").get()
        warning = str(extra["monitor_warning"] or "")[:2000]
        for item in (extra["monitor_errors"] or [])[:20]:
            if isinstance(item, dict):
                errors.append({"row": str(item.get("row") or item.get("line") or item.get("result_id") or item.get("key") or "")[:300],
                               "message": str(item.get("message") or "")[:300]})
        if (extra["kind"] in DOWNLOADABLE_EXPORT_KINDS and extra["artifact_key"]
                and extra["status"] in {"completed", "partial"} and extra["finished_at"]
                and extra["finished_at"] >= timezone.now() - timedelta(hours=ARTIFACT_TTL_HOURS)):
            download_url = reverse("admin-operation-download", args=[job_id])
    context = {"job": job, "row_errors": errors, "warning": warning, "download_url": download_url}
    try:
        context.update(_notification_detail_context(request, family, job_id))
    except (StudentNotification.DoesNotExist, Announcement.DoesNotExist, TelegramBroadcast.DoesNotExist):
        context["content_unavailable"] = True
    if redelivery_form is not None:
        context["redelivery_form"] = redelivery_form
    response = _response(request, context, detail=True)
    if redelivery_form is not None:
        response["HX-Push-Url"] = reverse("background-job-detail", args=[family, job_id])
    return response


class RedeliveryForm(forms.Form):
    audience = forms.ChoiceField(label=_("Audience"), choices=[
        ("group", _("Original recipients of this saved content")), ("recipient", _("This recipient only")),
    ], widget=forms.Select(attrs={"class": "form-select"}))
    mode = forms.ChoiceField(choices=[("failed", _("Retry failed deliveries")), ("all", _("Resend all"))])


def _notification_detail_context(request, family, job_id):
    from .notification_history import (
        notification_channel_history, notification_materializing_ids,
        original_audience, original_notification,
    )

    source, source_id = family, job_id
    result = {"notification_content": None}
    parent_job = None
    if family == "management":
        parent = AdminOperationJob.objects.filter(pk=job_id, kind="notification_redelivery").values("input", "result__broadcast_id").first()
        if not parent:
            return result
        source, source_id = parent["input"]["source"], parent["input"]["source_id"]
        parent_job = job_id
        result["history_url"] = reverse("notification-history") + f"?redelivery_job={job_id}"
        if source == "telegram" and parent["result__broadcast_id"]:
            source_id = parent["result__broadcast_id"]
    if source == "announcement":
        announcement = Announcement.objects.get(pk=source_id)
        result["notification_content"] = {"title_ar": announcement.title, "body_ar": announcement.body,
                                          "title_en": announcement.title, "body_en": announcement.body,
                                          "action_label": announcement.action_label, "action_url": announcement.action_url}
        result["notification_type_label"] = str(_("Announcement"))
        result["history_url"] = reverse("notification-history") + f"?notification_announcement={source_id}"
        result["announcement_retry_id"] = announcement.pk if announcement.status == "failed" else None
        if announcement.status != "completed":
            return result
        sample = StudentNotification.objects.filter(announcement_id=source_id, is_manual_redelivery=False).order_by("pk").first()
        if sample is None:
            return result
        source, source_id = "notification", sample.pk
    if source == "notification":
        notification = StudentNotification.objects.select_related("student").get(pk=source_id)
        result["notification_content"] = notification
        result["notification_type_label"] = str(dict(StudentNotification.NotificationType.choices)[notification.notification_type])
        result["notification_recipient"] = notification.student.username
        result["inbox_visible"] = notification.inbox_visible
        result["inbox_read_at"] = notification.read_at
        try:
            original = original_notification(notification)
            result["audience_count"] = original_audience(original).count()
            result["redelivery_url"] = reverse("notification-redelivery", args=["notification", original.pk])
            result["redelivery_materializing"] = original.pk in notification_materializing_ids([original.pk])
        except (ValueError, StudentNotification.DoesNotExist):
            pass
        # Never expose Expo tokens, ticket IDs, Telegram chat IDs or provider URLs.
        if parent_job:
            page = Paginator(monitored_jobs(family="notification", redelivery_job=parent_job), 24).get_page(request.GET.get("recipient_page", "1"))
            page.object_list = [readable_job(row) for row in page.object_list]
            result["notification_attempt_page"] = page
        else:
            page = Paginator(notification_channel_history(notification.pk), 25).get_page(request.GET.get("delivery_page", "1"))
            labels = {**dict(PushDelivery.Status.choices), **dict(TelegramNotificationDelivery.Status.choices),
                      "ticketed": _("Accepted, waiting for receipt")}
            page.object_list = [{**row, "status_label": labels.get(row["row_status"], row["row_status"])} for row in page.object_list]
            result["notification_delivery_page"] = page
            result["delivery_pagination_query"] = pagination_query_string(request, exclude=("delivery_page",))
    elif source == "telegram":
        broadcast = TelegramBroadcast.objects.get(pk=source_id)
        root_broadcast = broadcast.redelivery_of or broadcast
        result["notification_content"] = {"title_ar": _("Telegram broadcast"), "body_ar": broadcast.message,
                                          "title_en": _("Telegram broadcast"), "body_en": broadcast.message}
        result["history_url"] = reverse("telegram-broadcast-detail", args=[broadcast.pk])
        result["broadcast_attachments"] = broadcast.attachments.order_by("ordering").values("original_name", "size_bytes")[:25]
        result["redelivery_url"] = reverse("notification-redelivery", args=["telegram", root_broadcast.pk])
        result["audience_count"] = root_broadcast.recipient_count
        result["is_broadcast"] = True
        page = Paginator(broadcast.recipients.select_related("user").order_by("pk"), 25).get_page(request.GET.get("recipient_page", "1"))
        result["recipient_page"] = page
        result["recipient_pagination_query"] = pagination_query_string(request, exclude=("recipient_page",))
    if parent_job:
        result["recipient_pagination_query"] = pagination_query_string(request, exclude=("recipient_page",))
    if result.get("redelivery_url"):
        result["redelivery_form"] = RedeliveryForm(initial={"audience": "group"})
    return result


@require_POST
@capability_required(can_manage_academic_setup)
def notification_redelivery(request, source, source_id):
    from .notification_redelivery import enqueue_notification_redelivery
    from .admin_operation_tasks import run_admin_operation
    from django.utils import translation

    form = RedeliveryForm(request.POST)
    if source not in {"notification", "telegram"}:
        return JsonResponse({"error": _("Invalid notification redelivery selection.")}, status=400)
    try:
        source_id = valid_job_id(source, str(source_id))
    except ValueError:
        raise Http404
    if not form.is_valid():
        return _job_detail_response(request, source, str(source_id), form)
    try:
        job = enqueue_notification_redelivery(actor=request.user, source=source, source_id=source_id,
                                              language=translation.get_language() or "ar", **form.cleaned_data)
    except (StudentNotification.DoesNotExist, TelegramBroadcast.DoesNotExist):
        raise Http404
    except (ValueError, ValidationError) as exc:
        form.add_error(None, exc if isinstance(exc, ValidationError) else str(exc))
        return _job_detail_response(request, source, str(source_id), form)
    try:
        run_admin_operation.apply_async(args=[str(job.pk)], queue="admin")
    except Exception:
        import logging
        logging.getLogger(__name__).warning("notification redelivery awaits job recovery", extra={"job_id": str(job.pk)})
    messages.success(request, _("Redelivery queued. Final progress includes delivery receipts."))
    destination = reverse("background-job-detail", args=["management", job.pk])
    if request.headers.get("HX-Request") == "true":
        import json
        from django.http import HttpResponse
        response = HttpResponse()
        response["HX-Location"] = json.dumps({"path": destination, "target": "#content", "select": "#content", "swap": "outerHTML"})
        return response
    return redirect(destination)
