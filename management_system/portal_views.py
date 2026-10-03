"""Student download-app page, banner strip, and their admin-only manage pages."""

from __future__ import annotations

import os
import uuid

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .announcements import AnnouncementError, clean_announcement_action
from .mobile_versioning import MAX_VERSION_CODE, is_valid_update_url, normalize_platform
from .models import MobileAppRelease, MobileAppUpdatePolicy, MobileDownloadPolicy, StudentBanner
from .utils.decorators import capability_required, can_manage_academic_setup
from .utils.helpers import generate_breadcrumb, pagination_query_string, render_page
from .utils.storage_operations import get_r2_client, upload_to_bucket

APK_MAX_BYTES = 500 * 1024 * 1024
APK_PREFIX = "app-releases"
APK_MIME_TYPES = {"application/vnd.android.package-archive", "application/octet-stream"}


class BannerForm(forms.ModelForm):
    class Meta:
        model = StudentBanner
        fields = ["title", "body", "action_label", "action_url"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "required": True, "maxlength": 200}),
            "body": forms.Textarea(attrs={"class": "form-control", "required": True, "rows": 3, "maxlength": 500}),
            "action_label": forms.TextInput(attrs={"class": "form-control", "maxlength": 80}),
            "action_url": forms.TextInput(attrs={"class": "form-control", "maxlength": 2048}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].label = _("Title")
        self.fields["body"].label = _("Message")
        self.fields["action_label"].label = _("Button label (optional)")
        self.fields["action_url"].label = _("Button link (optional)")

    def clean(self):
        cleaned = super().clean() or {}
        try:
            label, url = clean_announcement_action(
                cleaned.get("action_label"), cleaned.get("action_url")
            )
        except AnnouncementError as exc:
            raise forms.ValidationError(str(exc))
        cleaned["action_label"] = label
        cleaned["action_url"] = url
        return cleaned


def _validate_apk(upload) -> None:
    """Raise a translated message when the uploaded build is not an APK."""
    name = (getattr(upload, "name", "") or "").lower()
    if not name.endswith(".apk"):
        raise ValueError(_("Only .apk files are allowed."))
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if content_type and content_type not in APK_MIME_TYPES:
        raise ValueError(_("Unexpected file type. Upload the Android .apk build."))
    if (getattr(upload, "size", 0) or 0) > APK_MAX_BYTES:
        raise ValueError(_("The app file must be 500 MB or smaller."))
    header = upload.read(4)
    try:
        upload.seek(0)
    except (AttributeError, OSError):
        pass
    if header[:2] != b"PK":
        raise ValueError(_("The file is not a valid Android package."))


def _safe_version(value: str) -> str:
    slug = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "-" for ch in (value or "").strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-.") or "app"


@login_required
def download_app(request):
    release = MobileAppRelease.objects.order_by("-version_code", "-pk").first()
    return render(request, "download_app.html", {"release": release})


@login_required
def download_app_file(request):
    release = MobileAppRelease.objects.order_by("-version_code", "-pk").first()
    if release is None:
        raise Http404
    bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
    client = get_r2_client()
    if not bucket or client is None:
        raise Http404
    filename = f"BibleInstituteApp-{_safe_version(release.version_name)}.apk"
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": release.apk_key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=3600,
        )
    except Exception:
        raise Http404
    return redirect(url)


def _parse_policy_code(value) -> int | None:
    try:
        code = int((value or "").strip() or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    if code < 0 or code > MAX_VERSION_CODE:
        return None
    return code


def _save_update_policy(request):
    """Validate and save one platform policy from the release-manage form."""
    platform = normalize_platform(request.POST.get("policy_platform"))
    if platform is None:
        messages.error(request, _("Choose a valid platform for the update policy."))
        return redirect("app-release-manage")
    is_enabled = bool(request.POST.get(f"policy_{platform}_enabled"))
    min_code = _parse_policy_code(request.POST.get(f"policy_{platform}_min_code"))
    latest_code = _parse_policy_code(request.POST.get(f"policy_{platform}_latest_code"))
    min_name = (request.POST.get(f"policy_{platform}_min_name") or "").strip()[:50]
    latest_name = (request.POST.get(f"policy_{platform}_latest_name") or "").strip()[:50]
    update_url = (request.POST.get(f"policy_{platform}_update_url") or "").strip()[:2048]
    if min_code is None or latest_code is None:
        messages.error(request, _("Version codes must be zero or a positive number."))
        return redirect("app-release-manage")
    if latest_code < min_code:
        messages.error(request, _("The latest version must be at or above the minimum version."))
        return redirect("app-release-manage")
    if is_enabled and (min_code > 0 or latest_code > 0):
        if not update_url or not is_valid_update_url(update_url):
            messages.error(request, _("A valid https update link is required when versioning is enabled."))
            return redirect("app-release-manage")
    elif update_url and not is_valid_update_url(update_url):
        messages.error(request, _("The update link must be a valid https URL."))
        return redirect("app-release-manage")
    policy, _ = MobileAppUpdatePolicy.objects.get_or_create(platform=platform)
    policy.is_enabled = is_enabled
    policy.min_version_code = min_code
    policy.latest_version_code = latest_code
    policy.min_version_name = min_name
    policy.latest_version_name = latest_name
    policy.update_url = update_url
    policy.updated_by = request.user if getattr(request.user, "is_authenticated", False) else None
    try:
        policy.full_clean()
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("app-release-manage")
    policy.save()
    messages.success(request, _("Update policy for %(platform)s saved.") % {"platform": platform})
    return redirect("app-release-manage")


def _save_download_policy(request):
    """Save the in-app vs on-device download master switches."""
    policy = MobileDownloadPolicy.load()
    policy.in_app_download_enabled = bool(request.POST.get("in_app_download_enabled"))
    policy.device_download_enabled = bool(request.POST.get("device_download_enabled"))
    policy.updated_by = request.user if getattr(request.user, "is_authenticated", False) else None
    policy.save()
    messages.success(request, _("Download policy saved."))
    return redirect("app-release-manage")


@capability_required(can_manage_academic_setup)
def app_release_manage(request):
    if request.method == "POST":
        if request.POST.get("policy_platform"):
            return _save_update_policy(request)
        if request.POST.get("download_policy"):
            return _save_download_policy(request)
        version_name = (request.POST.get("version_name") or "").strip()[:50]
        release_notes = (request.POST.get("release_notes") or "").strip()[:2000]
        try:
            version_code = int(request.POST.get("version_code") or 0)
        except (TypeError, ValueError):
            version_code = 0
        upload = request.FILES.get("apk")
        if not version_name or version_code <= 0:
            messages.error(request, _("Version name and a positive version code are required."))
            return redirect("app-release-manage")
        if MobileAppRelease.objects.filter(version_code=version_code).exists():
            messages.error(request, _("This version code is already published."))
            return redirect("app-release-manage")
        if upload is None:
            messages.error(request, _("Choose the .apk file to publish."))
            return redirect("app-release-manage")
        try:
            _validate_apk(upload)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("app-release-manage")
        bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
        client = get_r2_client()
        if not bucket or client is None:
            messages.error(request, _("Storage is not configured."))
            return redirect("app-release-manage")
        key = f"{APK_PREFIX}/bible-institute-app-{_safe_version(version_name)}-{uuid.uuid4().hex[:6]}.apk"
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass
        if not upload_to_bucket(client, bucket, upload, key, content_type="application/vnd.android.package-archive"):
            messages.error(request, _("The upload failed. Please retry."))
            return redirect("app-release-manage")
        MobileAppRelease.objects.create(
            version_name=version_name,
            version_code=version_code,
            apk_key=key,
            file_size=getattr(upload, "size", 0) or 0,
            release_notes=release_notes,
            uploaded_by=request.user,
        )
        messages.success(request, _("App version %(version)s published.") % {"version": version_name})
        return redirect("app-release-manage")
    releases_page_obj = Paginator(
        MobileAppRelease.objects.order_by("-version_code", "-pk"),
        25,
    ).get_page(request.GET.get("page", 1))
    policies = {}
    for platform in ("android", "ios"):
        policies[platform], _ = MobileAppUpdatePolicy.objects.get_or_create(platform=platform)
    return render_page(request, "app_release_manage.html", "partials/app_release_manage_content.html", {
        "releases_page_obj": releases_page_obj,
        "update_policies": policies,
        "download_policy": MobileDownloadPolicy.load(),
        "pagination_query": pagination_query_string(request),
        "pagination_aria_label": _("Page navigation"),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("App Releases"), None),
        ]),
    })


@capability_required(can_manage_academic_setup)
@require_POST
def app_release_delete(request, release_id: int):
    release = get_object_or_404(MobileAppRelease, pk=release_id)
    others = MobileAppRelease.objects.exclude(pk=release.pk).exists()
    key = release.apk_key
    release.delete()
    # Keep the currently served build downloadable: only remove the R2 object
    # when another release remains published.
    if others:
        client = get_r2_client()
        bucket = getattr(settings, "R2_BUCKET_NAME", "") or ""
        if client is not None and bucket:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
    messages.success(request, _("App release deleted."))
    return redirect("app-release-manage")


@capability_required(can_manage_academic_setup)
def banner_manage(request):
    if request.method == "POST":
        form = BannerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Banner published."))
        else:
            for err in form.errors.get("__all__", []):
                messages.error(request, str(err))
        return redirect("banner-manage")
    page_obj = Paginator(
        StudentBanner.objects.order_by("-created_at", "-pk"),
        25,
    ).get_page(request.GET.get("page", 1))
    return render_page(request, "banner_manage.html", "partials/banner_manage_content.html", {
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "pagination_aria_label": _("Page navigation"),
        "form": BannerForm(),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Banners"), None),
        ]),
    })


@capability_required(can_manage_academic_setup)
@require_POST
def banner_toggle(request, banner_id: int):
    banner = get_object_or_404(StudentBanner, pk=banner_id)
    banner.is_active = not banner.is_active
    banner.save(update_fields=["is_active"])
    messages.success(request, _("Banner updated."))
    return redirect("banner-manage")


@capability_required(can_manage_academic_setup)
@require_POST
def banner_delete(request, banner_id: int):
    get_object_or_404(StudentBanner, pk=banner_id).delete()
    messages.success(request, _("Banner deleted."))
    return redirect("banner-manage")
