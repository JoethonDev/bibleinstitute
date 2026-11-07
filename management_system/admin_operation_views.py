"""Authorized status and private-artifact download adapters for jobs."""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from .admin_operations import (
    ARTIFACT_TTL_HOURS,
    DOWNLOADABLE_EXPORT_KINDS,
    AdminOperationError,
    admin_operation_status_payload,
    open_admin_operation_export,
)
from .models import AdminOperationJob
from .utils.decorators import can_manage_academic_setup, can_view_reports, capability_required
from .xlsx_export import XLSX_CONTENT_TYPE


@require_GET
@capability_required(can_view_reports)
def admin_operation_status(request, job_id):
    """Return owner-bound status or shared formula progress for report viewers."""

    visible = Q(actor=request.user) | Q(kind=AdminOperationJob.Kind.FORMULA_RECALCULATION)
    job = get_object_or_404(AdminOperationJob.objects.filter(visible), pk=job_id)
    response = JsonResponse(admin_operation_status_payload(job))
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@capability_required(can_view_reports)
def admin_operation_download(request, job_id):
    """Stream one finished export artifact to its owner or an administrator."""

    job = get_object_or_404(AdminOperationJob, pk=job_id)
    if job.kind not in DOWNLOADABLE_EXPORT_KINDS:
        raise Http404
    if job.actor_id != request.user.pk and not can_manage_academic_setup(request.user):
        raise PermissionDenied
    if job.status not in {
        AdminOperationJob.Status.COMPLETED,
        AdminOperationJob.Status.PARTIAL,
    } or not job.artifact_key:
        return HttpResponse(_("The export is not ready yet."), status=409)
    if job.finished_at and job.finished_at < timezone.now() - timedelta(hours=ARTIFACT_TTL_HOURS):
        return HttpResponse(_("This export has expired. Start a new export."), status=410)
    try:
        stream, size = open_admin_operation_export(job)
    except AdminOperationError as exc:
        return HttpResponse(exc.message, status=410)
    filename = str((job.result or {}).get("filename") or "export.bin")
    content_type = str((job.result or {}).get("content_type") or XLSX_CONTENT_TYPE)
    response = FileResponse(
        stream,
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )
    if size:
        response["Content-Length"] = str(size)
    response["Cache-Control"] = "private, no-store"
    return response
