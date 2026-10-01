"""Web views for the graduation gallery (student albums + management)."""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from . import graduation_gallery as gg
from .gallery_tasks import enqueue_gallery_job
from .models import AcademicYearLevel, GraduationGalleryItem, GraduationGalleryJob
from .utils.decorators import capability_required, can_manage_content
from .utils.helpers import generate_breadcrumb, pagination_query_string, render_page
from .utils.storage_operations import get_r2_client
from django.conf import settings


@login_required
def graduates(request):
    scopes = gg.graduation_scopes_for_user(request.user).annotate(
        items_count=Count("gallery_items")
    )
    albums = [s for s in scopes if getattr(s, "items_count", 0)]
    covers = gg.album_covers([s.pk for s in albums])
    cards = []
    for scope in albums:
        cover = covers.get(scope.pk)
        display_url = None
        if cover is not None:
            display_url, _cover_download = gg.gallery_item_urls(cover)
        cards.append({"scope": scope, "items_count": scope.items_count, "cover_url": display_url, "cover_kind": getattr(cover, "kind", "image")})
    return render_page(request, "graduation_gallery.html", "partials/graduation_gallery_content.html", {
        "albums": cards,
        "breadcrumb_items": generate_breadcrumb([(_("Home"), reverse("home")), (_("Graduates"), None)]),
    })


@login_required
def graduation_gallery_detail(request, scope_id: int):
    scope = get_object_or_404(
        AcademicYearLevel.objects.select_related("academic_year", "level"), pk=scope_id
    )
    if not gg.gallery_has_access(request.user, scope):
        raise PermissionDenied
    items = (
        GraduationGalleryItem.objects.filter(academic_year_level=scope)
        .order_by("-created_at", "-pk")
        .only("kind", "display_key", "original_key", "width", "height", "created_at", "original_filename")
    )
    page_obj = Paginator(items, gg.GALLERY_PAGE_SIZE).get_page(request.GET.get("page", 1))
    for row in page_obj.object_list:
        display_url, download_url = gg.gallery_item_urls(row)
        row.display_url = display_url
        row.download_url = download_url
    return render_page(request, "graduation_gallery_detail.html", "partials/graduation_gallery_detail_content.html", {
        "scope": scope,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "breadcrumb_items": generate_breadcrumb([
            (_("Home"), reverse("home")),
            (_("Graduates"), reverse("graduates")),
            (f"{scope.academic_year.name} — {scope.level.display_name}", None),
        ]),
    })


@capability_required(can_manage_content)
def graduation_gallery_manage(request):
    from .models import AcademicYearLevel as Scope

    picker = (
        Scope.objects.filter(academic_year__graduation_level_id=F("level_id"))
        .select_related("academic_year", "level")
        .annotate(items_count=Count("gallery_items"))
        .order_by("-academic_year__ordering", "level__ordering")
    )
    raw_scope = request.GET.get("scope", "")
    scope = None
    if raw_scope.isdigit():
        scope = picker.filter(pk=int(raw_scope)).first()
    if scope is None:
        scope = next((s for s in picker if getattr(s, "items_count", 0)), None) or picker.first()
    items = GraduationGalleryItem.objects.none()
    jobs = GraduationGalleryJob.objects.none()
    page_obj = None
    if scope is not None:
        items = (
            GraduationGalleryItem.objects.filter(academic_year_level=scope)
            .order_by("-created_at", "-pk")
            .select_related("uploaded_by")
        )
        page_obj = Paginator(items, 25).get_page(request.GET.get("page", 1))
        for row in page_obj.object_list:
            display_url, _row_download = gg.gallery_item_urls(row)
            row.display_url = display_url
        jobs = (
            GraduationGalleryJob.objects.filter(academic_year_level=scope)
            .order_by("-created_at", "-pk")
            .select_related("created_by")[:15]
        )
    return render_page(request, "graduation_gallery_manage.html", "partials/graduation_gallery_manage_content.html", {
        "picker": picker,
        "scope": scope,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "jobs": jobs,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Graduation Gallery"), None),
        ]),
    })


@capability_required(gg.can_upload_gallery)
@require_POST
def gallery_upload_authorize(request):
    scope_id = (request.POST.get("scope_id") or "").strip()
    filename = (request.POST.get("filename") or "").strip()[:180]
    content_type = (request.POST.get("content_type") or "").strip()[:100]
    try:
        size = int(request.POST.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if not scope_id.isdigit() or not filename or size <= 0:
        return JsonResponse({"error": {"code": "invalid_request", "message": str(_("Invalid upload request."))}}, status=400)
    scope = get_object_or_404(
        AcademicYearLevel.objects.select_related("academic_year", "level"), pk=int(scope_id)
    )
    if scope.academic_year.graduation_level_id != scope.level_id:
        return JsonResponse({"error": {"code": "invalid_scope", "message": str(_("This level is not the graduation level for its year."))}}, status=400)
    kind = gg.classify_upload(filename, content_type)
    if not kind:
        return JsonResponse({"error": {"code": "invalid_type", "message": str(_("Only JPG, PNG, WebP images, MP4 videos, or a zip of those is allowed."))}}, status=400)
    try:
        gg.validate_single_upload(filename, content_type or ("application/zip" if kind == "zip" else ""), size)
    except ValueError as exc:
        return JsonResponse({"error": {"code": "invalid_file", "message": str(exc)}}, status=400)
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    client = get_r2_client()
    if not bucket or client is None:
        return JsonResponse({"error": {"code": "storage_unavailable", "message": str(_("Storage is not configured."))}}, status=503)
    job_uuid = uuid.uuid4().hex
    staging_key = gg.build_staging_key(job_uuid, filename)
    mime = content_type or ("application/zip" if kind == "zip" else "application/octet-stream")
    try:
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": staging_key, "ContentType": mime},
            ExpiresIn=3600,
        )
    except Exception:
        return JsonResponse({"error": {"code": "storage_unavailable", "message": str(_("Could not authorize the upload."))}}, status=503)
    job = GraduationGalleryJob.objects.create(
        academic_year_level=scope,
        staging_key=staging_key,
        original_filename=filename[:255],
        content_type=mime,
        is_zip=(kind == "zip"),
        created_by=request.user,
    )
    transaction.on_commit(lambda: enqueue_gallery_job(str(job.public_id)))
    return JsonResponse({
        "upload_url": upload_url,
        "method": "PUT",
        "headers": {"Content-Type": mime},
        "job_id": str(job.public_id),
        "staging_key": staging_key,
    })


@capability_required(gg.can_upload_gallery)
@require_GET
def gallery_job_status(request, job_id: str):
    job = get_object_or_404(GraduationGalleryJob, public_id=job_id)
    return JsonResponse({
        "job_id": str(job.public_id),
        "status": job.status,
        "total_files": job.total_files,
        "processed_files": job.processed_files,
        "failed_files": job.failed_files,
        "last_error": job.last_error,
    })


@capability_required(gg.can_delete_gallery)
@require_POST
def gallery_item_delete(request, item_id: int):
    item = get_object_or_404(GraduationGalleryItem, pk=item_id)
    scope_id = item.academic_year_level_id
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    with transaction.atomic():
        locked = (
            GraduationGalleryItem.objects.select_for_update().filter(pk=item.pk).first()
        )
        if locked is None:
            messages.error(request, _("Photo not found."))
            return redirect("graduation-gallery-manage")
        display_key, original_key = locked.display_key, locked.original_key
        locked.delete()
    gg.invalidate_gallery_urls(display_key, original_key)
    client = get_r2_client()
    if client is not None and bucket:
        for key in {display_key, original_key}:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                continue
    messages.success(request, _("Photo deleted."))
    url = reverse("graduation-gallery-manage")
    return redirect(f"{url}?scope={scope_id}")
