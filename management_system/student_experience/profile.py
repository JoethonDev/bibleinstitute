"""New-shell profile context and form selection."""

import mimetypes

from django.http import HttpRequest
from django.urls import reverse

from ..forms import (
    SignupDetailsForm,
    StudentAcademicPaymentForm,
    StudentMissingApplicationDocumentsForm,
    StudentSignupDetailsForm,
)
from ..models import AcademicPayment, StudentMode, User
from ..payments import academic_payment_scopes_for_student
from ..telegram.linking import TelegramLinkError, current_telegram_link
from ..utils.application_uploads import (
    APPLICATION_DOCUMENT_FIELDS,
    APPLICATION_DOCUMENT_LABELS,
)
from ..utils.student_data import student_next_actions
from .mode import resolve_student_mode


def profile_signup_details_form_class(request: HttpRequest) -> type[SignupDetailsForm]:
    """Return the profile form using the active student presentation widgets."""
    if resolve_student_mode(request) == StudentMode.NEW:
        return StudentSignupDetailsForm
    return SignupDetailsForm


def build_student_profile_context(
    request: HttpRequest,
    user: User,
    *,
    form: SignupDetailsForm | None = None,
) -> dict:
    """Build the bounded profile data consumed by the new student template."""
    profile_editable = user == request.user
    signup_details_form = form or StudentSignupDetailsForm(instance=user)
    if not profile_editable:
        for field in signup_details_form.fields.values():
            field.disabled = True
            field.required = False

    missing_documents_form = StudentMissingApplicationDocumentsForm(instance=user)
    payment_scopes = (
        list(academic_payment_scopes_for_student(user)) if profile_editable else []
    )
    missing_profile_items = []
    if profile_editable:
        for field_name in sorted(SignupDetailsForm.SIGNUP_REQUIRED_FIELDS | {"full_name"}):
            field = signup_details_form.fields.get(field_name)
            if field and not field.disabled and field.required:
                missing_profile_items.append(str(field.label))
        missing_profile_items.extend(
            str(field.label) for field in missing_documents_form.fields.values()
        )
        missing_profile_items.extend(
            f"{scope.level.display_name} - {scope.academic_year.name}"
            for scope in payment_scopes
        )

    academic_payments = list(
        AcademicPayment.objects.filter(student=user)
        .select_related("academic_year_level__academic_year", "academic_year_level__level")
        .order_by("-uploaded_at", "-pk")[:20]
    )
    application_documents = []
    for document_type, model_field in APPLICATION_DOCUMENT_FIELDS.items():
        key = getattr(user, model_field, None)
        if not key:
            continue
        application_documents.append({
            "type": document_type,
            "label": APPLICATION_DOCUMENT_LABELS[document_type],
            "url": reverse("application-document", args=[user.pk, document_type]),
            "download_url": (
                reverse("application-document", args=[user.pk, document_type])
                + "?download=1"
            ),
            "is_image": (mimetypes.guess_type(key)[0] or "").startswith("image/"),
        })

    telegram_account = None
    telegram_link_url = None
    telegram_can_unlink = False
    if profile_editable:
        try:
            telegram_account, telegram_link_url = current_telegram_link(user)
        except TelegramLinkError:
            telegram_account, telegram_link_url = None, None
        telegram_can_unlink = bool(
            telegram_account
            and user.role
            and user.role.role == "admin"
        )

    next_actions = {"resume": None, "next_quizzes": [], "gaps": []}
    if profile_editable:
        next_actions = student_next_actions(user)

    initials = ""
    if user.first_name:
        initials += user.first_name[0].upper()
    if user.last_name:
        initials += user.last_name[0].upper()
    if not initials:
        initials = user.username[0].upper() if user.username else "?"

    return {
        "object": user,
        "signup_details_form": signup_details_form,
        "form": signup_details_form,
        "profile_editable": profile_editable,
        "can_upload_missing_documents": profile_editable,
        "missing_documents_form": missing_documents_form,
        "academic_payment_scopes": payment_scopes,
        "academic_payment_form": (
            StudentAcademicPaymentForm(student=user) if profile_editable else None
        ),
        "missing_profile_items": missing_profile_items,
        "has_profile_gaps": bool(missing_profile_items),
        "academic_payments": academic_payments,
        "application_documents": application_documents,
        "show_profile_qr": bool(
            profile_editable
            and user.role
            and user.role.role in {"student", "admin"}
        ),
        "telegram_account": telegram_account,
        "telegram_link_url": telegram_link_url,
        "telegram_can_unlink": telegram_can_unlink,
        "next_actions": next_actions,
        "initials": initials,
    }
