"""Admin actions for the authenticated student experience rollout."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .forms import StudentExperienceOverrideForm, StudentExperienceSettingsForm
from .models import StudentExperienceOverride, StudentExperienceSettings, StudentMode, User
from .student_experience.mode import resolve_student_mode
from .utils.decorators import can_manage_academic_setup, capability_required
from .utils.helpers import generate_breadcrumb, render_page


def _require_student_account(user: User) -> None:
    if getattr(user.role, "role", None) != "student":
        raise PermissionDenied


@capability_required(can_manage_academic_setup)
def student_experience_settings(request):
    """Set the global student presentation mode for inheriting accounts."""
    settings_row = StudentExperienceSettings.load()
    if settings_row is None:
        settings_row = StudentExperienceSettings(singleton="default")
    if request.method == "POST":
        form = StudentExperienceSettingsForm(request.POST, instance=settings_row)
        if form.is_valid():
            form.instance.updated_by = request.user
            form.save()
            messages.success(request, _("Student experience settings saved."))
            return redirect("student-experience-settings")
    else:
        form = StudentExperienceSettingsForm(instance=settings_row)

    explicit_new_count = User.objects.filter(
        role__role="student",
        student_experience_override=StudentExperienceOverride.NEW,
    ).count()
    return render_page(
        request,
        "student_experience_settings.html",
        "partials/student_experience_settings_content.html",
        {
            "settings_row": settings_row,
            "form": form,
            "explicit_new_count": explicit_new_count,
            "breadcrumb_items": generate_breadcrumb([
                (_("Admin"), reverse("admin-panel")),
                (_("Student experience"), None),
            ]),
        },
    )


@capability_required(can_manage_academic_setup)
@require_POST
def student_experience_override(request, user_id: int):
    """Set one student's override without entering the user-edit transaction."""
    form = StudentExperienceOverrideForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            user = get_object_or_404(
                User.objects.select_related("role").select_for_update(of=("self",)),
                pk=user_id,
            )
            _require_student_account(user)
            user.student_experience_override = form.cleaned_data[
                "student_experience_override"
            ]
            user.save(update_fields=("student_experience_override",))
        messages.success(request, _("Student view setting updated."))
        return redirect("user-profile", user_id=user.pk)

    user = get_object_or_404(User.objects.select_related("role"), pk=user_id)
    _require_student_account(user)
    context = {
        "profile_user": user,
        "can_manage_student_experience": True,
        "student_experience_override_form": form,
        "student_experience_effective_mode_label": dict(StudentMode.choices)[
            resolve_student_mode(user)
        ],
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Users"), reverse("user-dashboard")),
            (user.get_full_name() or user.username, reverse("user-profile", args=[user.pk])),
            (_("Student experience"), None),
        ]),
    }
    return render(
        request,
        "student_experience_override_invalid.html",
        context,
        status=400,
    )
