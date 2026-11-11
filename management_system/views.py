# Django Core Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpRequest, HttpResponse, FileResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, FormView, DetailView
from django.utils.translation import gettext as _, gettext_lazy
from django.utils import translation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views, update_session_auth_hash
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Sum, When
from django.db import IntegrityError, transaction
from django.contrib import messages
from django.contrib.messages import success, error
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.core.cache import cache
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils import formats
from django.utils.dateparse import parse_date
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.forms import formset_factory
import hmac
import io
import secrets
import uuid
import random
from collections import defaultdict
from itertools import islice
from urllib.parse import quote, urlencode

# Third Party
from logging import getLogger
import json
import re
import os
import csv
import mimetypes
import redis
from datetime import date, timedelta
from functools import wraps

# Internal Imports - Models
from .models import *

# Internal Imports - Forms
from .forms import LessonEditorForm, CSVUploadForm, CourseForm, UserCreationForm, UserUpdateForm, SignupForm, SignupDetailsForm, MissingApplicationDocumentsForm, AcademicPaymentForm, ApplicationAdminForm, QuizExceptionalOpeningForm, AcademicYearForm, CourseOfferingForm, AcademicYearLevelWeekdayForm, AttendancePolicyForm, OfferingCopyForm, PromotionFormulaForm, PromotionRuleForm, HistoricalIntakeForm, ExceptionalCourseAssignmentForm, HistoricalExistingBulkForm, SignupSettingsForm, apply_cleaned_data_to_locked_instance

# Internal Imports - Utilities
from .utils.csv_export import export_users_to_csv, export_single_submission_to_csv, _csv_safe_cell, _safe_filename, enqueue_quiz_export
from .grade_matrix import (
    GRADE_MATRIX_PAGE_SIZE,
    grade_matrix_courses,
    grade_matrix_page,
    grade_matrix_selection,
    grade_matrix_student_queryset,
    grade_matrix_workbook,
)
from .quiz_grading import regrade_quiz_submissions
from .tasks import recalculate_quizzes_task
from .quiz_attempts import (
    reconcile_snapshots,
    quiz_snapshot_result,
    start_quiz_attempt,
    submit_answers,
)
from .attendance_matrix import (
    ATTENDANCE_MATRIX_PAGE_SIZE,
    ATTENDANCE_MATRIX_STATUS_VALUES,
    attendance_matrix_meetings,
    attendance_matrix_page,
    attendance_matrix_student_queryset,
)
from .application_analytics import (
    ANALYTICS_CSV_HEADERS,
    build_analytics_context,
    build_analytics_dataset,
    iter_analytics_csv_rows,
)
from .utils.reports import build_report_page_rows, iter_report_data, report_enrollments
from .utils.student_data import student_course_progress_data, student_next_actions
from .student_experience.calendar import build_calendar_context
from .student_experience.course import build_course_context
from .student_experience.lesson import build_lesson_context
from .student_experience.learning import build_learning_context
from .student_experience.quiz import build_quiz_presentation_context
from .student_experience.profile import (
    build_student_profile_context,
    profile_signup_details_form_class,
)
from .student_experience.mode import resolve_student_mode
from .student_experience.render import render_student_page, student_shell_context
from .passkey_auth import has_fresh_auth
from .calendar_grid import build_month_agenda as _build_month_agenda
from .calendar_grid import build_month_grid as _build_month_grid
from .models import StudentMode
from .forms import (
    StudentExperienceOverrideForm,
)
from .command_palette import PALETTE_TYPES, palette_results
from .admin_today import build_today_context
from .student_overview import build_student_overview
from .content_qa import qa_counts, qa_samples
from .utils.r2_filters import FileFilterConfig, filter_preset_choices, get_filter_preset
from .utils.r2_manager import R2Manager
from .storage_clients import CLOUD_CLIENT, bucket_name, cloudflare_client
from .utils.file_validator import (
    downloadable_audio_key,
    validate_downloadable_audio_key,
    validate_hls_object_key,
)
from .utils.storage_operations import list_current_folder, list_current_folder_page, search_files_page, generate_unique_url, get_r2_client
from .utils.helpers import build_scan_url, render_dashboard, select_content_academic_year, unpack_quiz_form, get_student_quiz_status, get_student_quiz_status_map, user_has_management_role, pagination_query_string, generate_breadcrumb, hx_target_id, is_htmx, render_page
from .utils.decorators import ajax_login_required, capability_required, can_manage_content, can_delete_content, can_grade, can_view_reports, can_manage_applications, can_manage_academic_setup, can_scan_attendance, can_correct_attendance
from .utils.email import send_application_received, send_application_status_notification
from .progress_activity import (
    ProgressHeartbeatError,
    apply_progress_heartbeat,
    format_active_duration,
    part_display_name,
    progress_activity_for_rows,
    watch_sessions_for_day,
)
from .utils.application_uploads import (
    APPLICATION_DOCUMENT_FIELDS,
    APPLICATION_DOCUMENT_LABELS,
    APPLICATION_PREVIEW_FIELDS,
    apply_application_document_changes,
    cleanup_replaced_application_documents,
    delete_application_files,
    lock_application_document_namespace,
    rollback_staged_application_documents,
    stage_application_documents,
    upload_application_file,
)
from .utils.egyptian_cities import EGYPTIAN_CITIES
from .utils.r2_references import (
    application_referenced_storage_keys,
    lesson_referenced_storage_keys,
    lesson_rows_matching_storage_values,
    lesson_storage_keys,
    other_referenced_storage_keys,
    rewrite_storage_references,
    storage_reference_transition_conflicts,
)
from .utils.attendance import (
    assign_unassigned_attendance,
    attendance_scan_availability,
    is_expected_date,
)
from .utils.timezones import (
    parse_application_datetime,
    format_human_datetime,
    timezone_name,
)
from .utils.lesson_access import lesson_is_available
from .utils.validators import normalize_username, parse_strict_bool
from .utils.search import normalize_search_text, normalized_contains_q
from .admin_navigation import quick_create_actions_for
from .admin_tables import (
    load_selection_token,
    MAX_EXPLICIT_SELECTION,
    bulk_delete_records,
    content_scope,
    content_status_counts,
    course_queryset,
    course_scope,
    lesson_queryset,
    quiz_queryset,
    user_queryset,
    user_scope,
    user_tab_counts,
)
from .utils.quiz_access import answers_releasable, grant_quiz_openings, quiz_window
from .quiz_questions import MAX_QUESTIONS, build_question_instance
from .quiz_import import parse_quiz_import
from .public_content import get_institute_copy
from .study_mode import apply_study_mode_from_city
from .payments import academic_payment_scopes_for_student
from .academic_enrollment import (
    _resolve_intake_scope,
    activate_academic_year,
    accept_application,
    decline_application,
    reopen_application,
    bulk_set_application_status,
    set_application_status,
    lock_application_status_for_edit,
    set_user_normal_enrollment_scope,
    promote_evaluation_result,
    promote_historical_summary,
)
from .historical_intake import (
    INTAKE_COLUMNS,
    MAX_EXISTING_BULK_STUDENTS,
    IntakeLookups,
    assign_exceptional_courses,
    build_existing_intake_row,
    enqueue_historical_intake,
    intake_historical_row,
    parse_intake_upload,
    preview_intake_rows,
    summaries_for_users_scopes,
)
from .legacy_user_import import (
    enqueue_legacy_user_import,
    read_legacy_import_upload,
    validate_legacy_import_rows,
)
from .academic_formula import normalize_formula_rules, validate_formula_rules
from .academic_evaluation import (
    build_evaluation_plan,
    evaluation_enrollments,
    evaluate_enrollment_page,
    override_evaluation_result,
    save_formula_and_enqueue,
)
from .evaluation_export import build_evaluation_workbook, enqueue_evaluation_export
from .report_export import (
    enqueue_report_export,
    normalize_report_filters,
    report_export_conflict_key,
)
from .xlsx_export import XLSX_CONTENT_TYPE
from .academic_access import READABLE_ENROLLMENT_STATUSES, accessible_offerings, get_accessible_offering_or_403
from .media_delivery import (
    MEDIA_AUDIENCES,
    MEDIA_SEGMENT_TOKEN_LIFETIME,
    MEDIA_TOKEN_VERSION,
    MediaAccessError,
    build_manifest_playlist,
    create_viewing_session,
    resolve_audio_download,
    resolve_book_url,
    resolve_media_link,
    sign_media_message,
    sign_session,
    valid_session_token,
)
from .course_content import course_navigation, with_offering_content_counts
from .mobile_auth import revoke_user_mobile_access
from .utils.hls_parser import get_segment_number
from .utils.grouped_choices import offering_option_groups
from .media_processing import (
    ACTIVE_WORKER_STATUSES,
    claim_attachment_retry,
    initialize_deadlines,
    job_is_visible_to,
    request_media_job_cleanup,
    request_automation_publication_retry,
    request_media_job_stop,
    queue_verified_job,
    safe_output_base_name,
    schedule_attachment_retry_after_commit,
    schedule_media_job_after_commit,
    MAX_MEDIA_ATTEMPTS,
    validate_browser_part_id,
    validate_requested_folder,
    validate_source_descriptor,
    is_automation_job,
    PUBLICATION_FAILED_ERROR,
    PUBLICATION_PENDING_ERROR,
)
from .storage_jobs import enqueue_storage_delete, enqueue_storage_rename, enqueue_storage_usage, storage_usage_snapshot
from .storage_coordination import (
    assert_storage_namespace_available,
    guarded_storage_write,
    lock_storage_namespace,
    reserve_media_output_folder,
)
from .storage_ownership import (
    filter_content_storage_entries,
    filter_visible_storage_entries,
    is_private_storage_key,
    storage_key_transition_allowed,
    user_may_manage_storage_key,
)
from .media_storage import (
    MediaStorageError,
    STAGING_UPLOAD_URL_TTL_SECONDS,
    build_staging_key,
    content_type_for_key,
    create_staging_upload_url,
)
from .telegram.linking import TelegramLinkError, current_telegram_link
from .student_notifications import (
    cancel_future_quiz_opening_events,
    cancel_future_lesson_publication_events,
    create_lesson_publication_event,
    schedule_quiz_opening_events,
)
from .payment_matrix import PAYMENT_MATRIX_PAGE_SIZE, PAYMENT_STATUS_VALUES, payment_matrix_page, payment_matrix_student_queryset

# Standard Library (moved from function-local)
from datetime import datetime
from io import BytesIO
import qrcode

# Django
from django.core.paginator import Paginator

from .admin_operations import create_admin_operation, enqueue_admin_operation
from django.http import QueryDict


# Constants
LOGIN_URL = reverse_lazy("user_login")
logger = getLogger(__name__)

PromotionRuleFormSet = formset_factory(PromotionRuleForm, extra=0, can_delete=True)


def _lesson_part_base(link_id) -> str:
    """Canonical grouping key so a video manifest and its `_audio` sibling share one part.

    The key (an R2 object key) is lowercased, its extension is dropped, and a
    trailing `_audio` is stripped: `.../lecture3_2.m3u8` and
    `.../lecture3_2_audio.m3u8` both reduce to the same base. A bare `audio`
    name falls back to the full key so it still gets its own part.
    """
    stem = str(link_id or "").rsplit(".", 1)[0].lower()
    if stem.endswith("_audio"):
        stem = stem[: -len("_audio")]
    return stem or str(link_id or "").lower()


def _lesson_links_from_form(request, existing_links=()) -> list[dict]:
    """Build lesson link metadata while preserving downloadable MP3 siblings."""
    videos = request.POST.getlist("videos", [])
    videos_name = request.POST.getlist("videos_name", [])
    files_type = request.POST.getlist("files_type", [])
    download_ids = request.POST.getlist("download_ids", [])
    part_groups = request.POST.getlist("part_groups", [])
    if not videos or len(videos) != len(videos_name) or len(videos) != len(files_type):
        raise ValidationError(_("Lesson files are incomplete."))
    if any(file_type not in {"video", "audio", "book"} for file_type in files_type):
        raise ValidationError(_("Lesson files are incomplete."))
    if any(is_private_storage_key(key) for key in videos):
        raise ValidationError(_("The lesson media links are invalid."))
    existing_links = [
        link for link in existing_links
        if isinstance(link, dict)
        and not is_private_storage_key(link.get("id"))
        and not is_private_storage_key(link.get("download_id"))
    ]

    existing_part_ids = {
        link["id"]: link["part_id"]
        for link in existing_links
        if isinstance(link, dict) and link.get("id") and link.get("part_id")
    }
    # Reconciliation map: an already-paired base keeps its part when a key is
    # re-uploaded or renamed. First stored pairing wins deterministically.
    existing_base_part_ids = {}
    for link in existing_links:
        if isinstance(link, dict) and link.get("id") and link.get("part_id"):
            existing_base_part_ids.setdefault(_lesson_part_base(link["id"]), link["part_id"])

    # Explicit grouping from the lesson editor: every card submits a part
    # number, and cards sharing a number become one part (so the operator can
    # merge or split video/audio directly). A group keeps an existing part id
    # when one of its members already had it; otherwise it gets a fresh one.
    group_tokens: dict[str, str] = {}
    used_tokens: set[str] = set()
    for index, video_key in enumerate(videos):
        group = part_groups[index].strip() if index < len(part_groups) else ""
        if not group or group in group_tokens:
            continue
        preferred = (
            existing_part_ids.get(video_key)
            or existing_base_part_ids.get(_lesson_part_base(video_key))
        )
        token = preferred if (preferred and preferred not in used_tokens) else ""
        while not token or token in used_tokens:
            token = secrets.token_urlsafe(8)
        group_tokens[group] = token
        used_tokens.add(token)

    # Cards without an explicit group still pair by filename base (video + its
    # `_audio` sibling), which is the no-JavaScript fallback.
    new_base_part_ids = {}
    links = []
    for index, video_key in enumerate(videos):
        download_key = download_ids[index].strip() if index < len(download_ids) else ""
        if download_key:
            # The operator may point the download at a differently named MP3
            # (a mis-named or relocated file), so only the key shape is
            # validated here, not equality with the canonical sibling name.
            valid, errors = validate_downloadable_audio_key(download_key)
            if not valid:
                raise ValidationError(errors or [_('Downloadable audio key is invalid.')])
            if is_private_storage_key(download_key):
                raise ValidationError(_("The lesson media links are invalid."))
            if files_type[index] == "book":
                raise ValidationError(_("Book media cannot have a downloadable audio file."))
        group = part_groups[index].strip() if index < len(part_groups) else ""
        if group:
            part_id = group_tokens[group]
        else:
            base = _lesson_part_base(video_key)
            part_id = (
                existing_part_ids.get(video_key)
                or existing_base_part_ids.get(base)
                or new_base_part_ids.get(base)
            )
            while not part_id or part_id in used_tokens:
                part_id = secrets.token_urlsafe(8)
            new_base_part_ids.setdefault(base, part_id)
        link = {
            "file_type": files_type[index],
            "name": videos_name[index],
            "id": video_key,
            "part_id": part_id,
        }
        if download_key:
            link["download_id"] = download_key
        links.append(link)
    return links

# Initialize R2 Manager
R2_MANAGER = R2Manager(CLOUD_CLIENT, bucket_name, cloudflare_client)

# Helper functions moved to utils/storage_operations.py and utils/helpers.py

def prepare_exam_question(question_data):
    """Attach randomized display payloads for exam mode without mutating answer keys."""
    question_type = question_data.get("type")

    if question_type == "order_events":
        shuffled_items = list(question_data.get("answer_payload") or [])
        random.shuffle(shuffled_items)
        question_data["exam_items"] = shuffled_items

    elif question_type == "match_related":
        pairs = list((question_data.get("answer_payload") or {}).items())
        random.shuffle(pairs)

        right_options = [right for _, right in pairs]
        random.shuffle(right_options)

        question_data["exam_pairs"] = [{"left": left} for left, _ in pairs]
        question_data["exam_right_options"] = right_options
        question_data["exam_right_options_json"] = json.dumps(right_options)

    return question_data

# Class Base
class LoginProtection(object):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        return redirect(f"{reverse('user_login')}?next={request.path}")

class AdminPermissionView(LoginProtection):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        user = User.objects.get(username=request.user)
        if can_manage_content(user):
            return super(LoginProtection, self).dispatch(request, *args, **kwargs)
        raise PermissionDenied

class FormBase(AdminPermissionView, FormView):
    view_name = ""
    # Stable URL slug; view_name is a translated label and must never be used
    # to reverse a URL name.
    view_slug = ""
    action = ""
    template_name = "dashboard_form.html"

    def get_error_msg(self):
        return _("%(action)s %(view_name)s is failed, Try again Please!") % {'action': _(self.action), 'view_name': _(self.view_name)} # Translate
    
    def get_success_msg(self):
        return _("%(view_name)s has %(action)sd successfully!") % {'action': _(self.action), 'view_name': _(self.view_name)} # Translate

    def form_invalid(self, form):
        """ If the form is vaild, redirect to the supplied URL. 
            Display success_message
        """
        error(self.request, self.get_error_msg(), extra_tags="alert-danger")
        return super().form_invalid(form)
    
    def form_valid(self, form):
        """If the form is invalid, render the invalid form.
           Display error_message 
        """
        self.request.session["saved_dashboard_form"] = str(self.view_name)
        success(self.request, self.get_success_msg(), extra_tags="alert-success")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["draft_saved"] = self.request.session.get("saved_dashboard_form") == str(self.view_name)
        if context["draft_saved"]:
            self.request.session.pop("saved_dashboard_form", None)
        context["view_name"] = self.view_name  # Example shared context
        context["view_slug"] = self.view_slug or self.view_name
        context["action"] = self.action
        context["object_id"] = self.object.pk if self.object else 'new'
        return context

    def render_to_response(self, context, **response_kwargs):
        """Return the page-region fragment for HTMX form navigation.

        Direct requests keep the full shell; HTMX page navigation and invalid
        bound forms return the #page-region wrapper so the shell is not
        re-rendered and bound values/errors stay at the page root.
        """
        if not is_htmx(self.request):
            return super().render_to_response(context, **response_kwargs)
        return render_page(
            self.request,
            self.get_template_names()[0],
            "partials/dashboard_form_content.html",
            context,
            status=response_kwargs.get("status", 200),
        )

class UserBaseView(FormBase):
    model = User
    pk_url_kwarg = "user_id"
    view_name = gettext_lazy("user")
    view_slug = "user"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_manage_applications(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
  
class CourseBaseView(FormBase):
    model = Course
    pk_url_kwarg = "course_id"
    view_name = gettext_lazy("course")
    view_slug = "course"
    form_class = CourseForm

class LessonBaseView(FormBase):
    model = Lesson
    pk_url_kwarg = "lesson_id"
    view_name = gettext_lazy("lesson")
    view_slug = "lesson"

class QuizBaseView(FormBase):
    model = Quiz
    pk_url_kwarg = "quiz_id"
    view_name = gettext_lazy("quiz")
    view_slug = "quiz"

# # Create your views here.
class LoginView(views.LoginView):
    template_name = "login.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if resolve_student_mode(request) == StudentMode.NEW:
                redirect_url = self.get_redirect_url()
                if redirect_url:
                    return redirect(redirect_url)
                return redirect(reverse("courses"))
            return redirect(reverse("view-profile"))
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(reverse("view-profile"))
        response =  super().post(request, *args, **kwargs)
        username = request.POST.get("username")

        if response.status_code != 302:
            logger.warning(
                "web login failed",
                extra={"event": "login_failed", "attempted_username": username or ""},
            )
        else:
            logger.info(
                "web login succeeded",
                extra={"event": "login_succeeded", "username": username or ""},
            )
            if resolve_student_mode(request) == StudentMode.NEW:
                response = redirect(self.get_redirect_url() or reverse("courses"))

        return response


class LogoutView(views.LogoutView):
    """Audit one event when a browser session is ended."""

    def post(self, request, *args, **kwargs):
        user_id = getattr(request.user, "pk", None)
        response = super().post(request, *args, **kwargs)
        logger.info("user logged out", extra={"event": "logout", "user_id": user_id})
        return response

def index(request):
    return render(request, "home.html", get_institute_copy(translation.get_language(), "home"))

@login_required(login_url=LOGIN_URL)
def portal(request):
    if resolve_student_mode(request) == StudentMode.NEW:
        return redirect("courses")
    return redirect("home")

def about_page(request):
    return render(request, "about.html", get_institute_copy(translation.get_language(), "about"))

def program_page(request):
    return render(request, "program.html", get_institute_copy(translation.get_language(), "courses"))

# Detail Class [handles with and without pk routes]
class ProfileDetail(LoginProtection, DetailView):
    model = User
    pk_url_kwarg = "user_id"
    template_name = "profile.html"

    def get_template_names(self):
        if resolve_student_mode(self.request) == StudentMode.NEW:
            return ["student_next/profile.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        if resolve_student_mode(self.request) == StudentMode.NEW:
            context = build_student_profile_context(
                self.request,
                self.object,
                form=kwargs.get("form"),
            )
            context.update(student_shell_context("profile"))
            context["passkeys"] = list(
                WebAuthnCredential.objects.filter(
                    user=self.object,
                    is_active=True,
                ).order_by("created_at")[:10]
            )
            context["passkey_fresh"] = has_fresh_auth(self.request)
            return context
        context = super().get_context_data(**kwargs)
        user = self.object
        signup_details_form = kwargs.get("form") or SignupDetailsForm(instance=user)
        profile_editable = user == self.request.user
        if not profile_editable:
            for field in signup_details_form.fields.values():
                field.disabled = True
                field.required = False
        context["signup_details_form"] = signup_details_form
        context["form"] = signup_details_form
        context["profile_editable"] = profile_editable
        context["passkeys"] = (
            list(
                WebAuthnCredential.objects.filter(
                    user=user,
                    is_active=True,
                ).order_by("created_at")[:10]
            )
            if profile_editable
            else []
        )
        context["passkey_fresh"] = has_fresh_auth(self.request)
        context["can_upload_missing_documents"] = user == self.request.user
        missing_documents_form = MissingApplicationDocumentsForm(instance=user)
        context["missing_documents_form"] = missing_documents_form
        academic_payment_scopes = list(academic_payment_scopes_for_student(user)) if profile_editable else []
        context["academic_payment_scopes"] = academic_payment_scopes
        context["academic_payment_form"] = AcademicPaymentForm(student=user) if profile_editable else None
        missing_profile_items = []
        if profile_editable:
            details_form = signup_details_form
            for field_name in sorted(SignupDetailsForm.SIGNUP_REQUIRED_FIELDS | {"full_name"}):
                field = details_form.fields.get(field_name)
                if field and not field.disabled and field.required:
                    missing_profile_items.append(str(field.label))
            missing_profile_items.extend(str(field.label) for field in missing_documents_form.fields.values())
            missing_profile_items.extend(
                f"{scope.level.display_name} - {scope.academic_year.name}"
                for scope in academic_payment_scopes
            )
        context["missing_profile_items"] = missing_profile_items
        context["has_profile_gaps"] = bool(missing_profile_items)
        context["academic_payments"] = list(
            AcademicPayment.objects.filter(student=user)
            .select_related("academic_year_level__academic_year", "academic_year_level__level")
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
                "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                "is_image": (mimetypes.guess_type(key)[0] or "").startswith("image/"),
            })
        context["application_documents"] = application_documents
        management_preview = user_has_management_role(user)
        if management_preview:
            offerings = CourseOffering.objects.select_related(
                "course", "academic_year_level__academic_year", "academic_year_level__level"
            ).filter(
                academic_year_level__academic_year__is_active=True,
            ).order_by("academic_year_level__level__ordering", "course__name")
        else:
            offerings = accessible_offerings(user).select_related(
                "course", "academic_year_level__academic_year", "academic_year_level__level"
            ).order_by(
                "academic_year_level__academic_year__ordering",
                "academic_year_level__level__ordering",
                "course__name",
            )
        offerings = list(offerings)
        course_progress = student_course_progress_data(
            user,
            offerings,
            include_unpublished=management_preview,
        )

        for offering in offerings:
            offering.profile_progress = course_progress[offering.pk]

        levels_map = {}
        for o in offerings:
            lvl = o.academic_year_level.level
            scope = o.academic_year_level
            group_key = (scope.academic_year_id, scope.level_id)
            if group_key not in levels_map:
                levels_map[group_key] = {
                    "level_name": lvl.display_name,
                    "academic_year_name": scope.academic_year.name,
                    "section_name": f"{lvl.display_name} - {scope.academic_year.name}",
                    "offerings": [],
                }
            levels_map[group_key]["offerings"].append(o)
        context['courses'] = list(levels_map.values())
        context['show_profile_qr'] = bool(
            user == self.request.user
            and user.role
            and user.role.role in {"student", "admin"}
        )
        context["telegram_account"] = None
        context["telegram_link_url"] = None
        context["telegram_can_unlink"] = False
        if user == self.request.user:
            try:
                telegram_account, telegram_link_url = current_telegram_link(user)
            except TelegramLinkError:
                telegram_account, telegram_link_url = None, None
            context["telegram_account"] = telegram_account
            context["telegram_link_url"] = telegram_link_url
            context["telegram_can_unlink"] = bool(
                telegram_account
                and user.role
                and user.role.role == "admin"
            )

        # Recent quiz grade submissions (last 5)
        context['recent_grades'] = (
            Grade.objects
            .filter(user=user)
            .select_related('quiz', 'quiz__course_offering__course')
            .order_by('-submitted_at')[:5]
        )

        # Avatar initials
        initials = ""
        if user.first_name:
            initials += user.first_name[0].upper()
        if user.last_name:
            initials += user.last_name[0].upper()
        if not initials:
            initials = user.username[0].upper() if user.username else "?"
        context['initials'] = initials
        if user == self.request.user:
            try:
                context["next_actions"] = student_next_actions(user)
            except Exception:
                context["next_actions"] = {"resume": None, "next_quizzes": [], "gaps": []}

        return context

    def get(self, request, *args, **kwargs):
        if self.kwargs.get(self.pk_url_kwarg) and not can_manage_content(request.user):
            raise PermissionDenied
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object != request.user:
            raise PermissionDenied

        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)
            form = profile_signup_details_form_class(request)(
                request.POST,
                instance=locked_user,
            )
            if form.is_valid():
                self.object = form.save()
                password_changed = bool(form.cleaned_data.get("password"))
                form_valid = True
            else:
                password_changed = False
                form_valid = False

        if not form_valid:
            return self.render_to_response(self.get_context_data(form=form))
        if password_changed:
            update_session_auth_hash(request, self.object)
        success(request, _("Profile is updated successfully!"), extra_tags="alert-success")
        return redirect("view-profile")

    def get_object(self, queryset = None):
        # if not pk in route
        if not self.kwargs.get(self.pk_url_kwarg):
            return self.request.user
        return super().get_object(queryset)

# Course Routes
@login_required(login_url=LOGIN_URL)
def view_courses(request, course_id=None):
    user = request.user
    if course_id is None and resolve_student_mode(request) == StudentMode.NEW:
        context = build_learning_context(request)
        if request.headers.get("HX-Request") == "true":
            return render(
                request,
                "student_next/partials/learning_list.html",
                context,
            )
        return render_student_page(
            request,
            page="courses",
            template="student_next/courses.html",
            context=context,
        )
    management_preview = user_has_management_role(user)
    offerings_queryset = with_offering_content_counts(
        accessible_offerings(user, include_management=management_preview),
        include_drafts=management_preview,
    ).select_related(
        "course", "academic_year_level__academic_year", "academic_year_level__level"
    ).order_by(
        "-academic_year_level__academic_year__ordering",
        "-academic_year_level__level__ordering",
        "course__name",
    )
    if course_id is not None:
        get_object_or_404(Course, pk=course_id)
        offerings_queryset = offerings_queryset.filter(course_id=course_id)
    offerings = list(offerings_queryset)
    normal_scopes = set()
    if not management_preview:
        normal_scopes = set(Enrollment.objects.filter(
            student=user,
            status__in=READABLE_ENROLLMENT_STATUSES,
            enrollment_type=Enrollment.Type.NORMAL,
        ).values_list("academic_year_level_id", flat=True))

    grouped = defaultdict(list)
    for offering in offerings:
        scope = offering.academic_year_level
        grouped[(scope.academic_year_id, scope.level_id)].append(offering)
    sections = []
    for (year_id, level_id), section_offerings in grouped.items():
        first = section_offerings[0]
        scope = first.academic_year_level
        if management_preview:
            access_kind = "management"
        elif first.academic_year_level_id in normal_scopes:
            access_kind = "current" if scope.academic_year.is_active else "historical"
        else:
            access_kind = "exceptional"
        sections.append({
            "academic_year": scope.academic_year,
            "level": scope.level,
            "level_name": scope.level.display_name,
            "access_kind": access_kind,
            "offerings": section_offerings,
        })

    return render(request, "course_view.html", {
        "sections": sections
    })

@login_required(login_url=LOGIN_URL)
def view_course_details(request, offering_id):
    user = request.user
    management_preview = user_has_management_role(user)
    student_next = (
        resolve_student_mode(request) == StudentMode.NEW and not management_preview
    )
    offering_queryset = (
        with_offering_content_counts(
            CourseOffering.objects.all(),
            include_drafts=False,
        )
        if student_next else None
    )
    offering = get_accessible_offering_or_403(
        user,
        offering_id,
        offering_queryset=offering_queryset,
    )
    if student_next:
        return render_student_page(
            request,
            page="course",
            template="student_next/course.html",
            context=build_course_context(
                user,
                offering,
                management_preview=False,
            ),
        )
    return render(request, "course_detail.html", {
        "offering": offering,
        "offering_id": offering.pk,
        "course": offering.course,
        **course_navigation(offering, include_drafts=management_preview),
        "management_preview": management_preview,
    })

@login_required(login_url=LOGIN_URL)
def view_lesson_details(request, offering_id, lesson_id):
    user = request.user
    management_preview = user_has_management_role(user)
    student_next = (
        resolve_student_mode(request) == StudentMode.NEW and not management_preview
    )
    offering_queryset = (
        with_offering_content_counts(
            CourseOffering.objects.all(),
            include_drafts=False,
        )
        if student_next else None
    )
    offering = get_accessible_offering_or_403(
        user,
        offering_id,
        offering_queryset=offering_queryset,
    )
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))
    if not management_preview and not lesson_is_available(lesson):
        raise PermissionDenied(_("This lesson is not available yet."))
    lesson_links = json.loads(lesson.links)
    context = {
        "offering": offering,
        "offering_id": offering.pk,
        "course": offering.course,
        "lesson_id": lesson_id,
        **course_navigation(offering, include_drafts=management_preview),
        "management_preview": management_preview,
        "links": [{
            "url": reverse(
                "lesson-manifest" if file.get("file_type") in {"video", "audio"} else "lesson-stream",
                args=[offering.pk, lesson_id, file_index],
            ),
            "name": file.get("name", ""),
            "type": file.get("file_type", ""),
            "part_id": file.get("part_id", ""),
            "file_index": file_index,
            "download_url": (
                f"{reverse('audio-download', args=[offering.pk, lesson_id])}?file_index={file_index}"
                if file.get("file_type") == "audio" else ""
            ),
        } for file_index, file in enumerate(lesson_links)]
    }
    if student_next:
        next_context = build_lesson_context(
            user,
            offering,
            lesson,
            context["links"],
            management_preview=False,
        )
        target = (request.headers.get("HX-Target") or "").strip()
        target_id = target.rsplit("#", 1)[-1].strip() if "#" in target else target
        history_restore = (
            request.headers.get("HX-History-Restore-Request") or ""
        ).strip().lower() == "true"
        if history_restore or (
            request.headers.get("HX-Request") == "true"
            and target_id == "display-page"
        ):
            return render(
                request,
                "student_next/partials/lesson_content.html",
                next_context,
            )
        return render_student_page(
            request,
            page="lesson",
            template="student_next/lesson.html",
            context=next_context,
        )
    # Inner lesson navigation swaps #display-page; return only the content
    # wrapper there and keep the complete shell for direct/deep-link requests.
    target = (request.headers.get("HX-Target") or "").strip()
    target_id = target.rsplit("#", 1)[-1].strip() if "#" in target else target
    history_restore = (
        request.headers.get("HX-History-Restore-Request") or ""
    ).strip().lower() == "true"
    if history_restore or (
        request.headers.get("HX-Request") == "true" and target_id == "display-page"
    ):
        return render(request, "partials/lesson_stream_content.html", context)
    return render(request, "lesson_stream.html", context)

@login_required(login_url=LOGIN_URL)
def stream_lesson(request, offering_id, lesson_id, file_index):
    try:
        media = resolve_media_link(
            user=request.user,
            offering_id=offering_id,
            lesson_id=lesson_id,
            file_index=file_index,
            allow_unpublished=True,
        )
    except MediaAccessError as exc:
        if exc.code == "forbidden":
            raise PermissionDenied(exc.message)
        raise Http404
    if media.file_type == "book":
        return JsonResponse({
            "url": resolve_book_url(
                media=media,
                cloud_client=CLOUD_CLIENT,
                bucket_name=bucket_name,
            )
        })
    return JsonResponse(
        {"error": _("Media playback requires a viewing session.")},
        status=410,
    )
    

@login_required(login_url=LOGIN_URL)
@require_POST
def quiz_start(request, offering_id: int, quiz_id: int) -> HttpResponse:
    user = User.objects.get(pk=request.user.pk)
    offering = get_accessible_offering_or_403(user, offering_id, write=True)
    quiz = get_object_or_404(
        Quiz,
        pk=quiz_id,
        course_offering=offering,
        status=PublicationStatus.PUBLISHED,
    )
    start_quiz_attempt(quiz.pk, user)
    return redirect("quiz-details", offering_id=offering.pk, quiz_id=quiz.pk)


@login_required(login_url=LOGIN_URL)
def take_exam(request, offering_id, quiz_id, *, allow_management=False):
    preview_requested = request.GET.get("preview") == "1"
    if request.method == "POST" and preview_requested:
        # Reject crafted preview submissions before loading the offering or
        # touching any submission/grade workflow.
        raise PermissionDenied(_("Quiz preview cannot be submitted."))

    try:
        user = request.user
        if preview_requested and not can_manage_academic_setup(user):
            raise PermissionDenied
        offering = get_accessible_offering_or_403(
            user,
            offering_id,
            write=request.method == "POST",
            allow_management=allow_management,
        )
        quiz_filters = {"pk": quiz_id, "course_offering": offering}
        if not user_has_management_role(user):
            # Unpublished quizzes are not student resources.  Apply the same
            # visibility boundary as the course-detail list so a guessed
            # quiz URL cannot bypass Draft/Archived visibility.
            quiz_filters["status"] = PublicationStatus.PUBLISHED
        quiz = get_object_or_404(
            Quiz.objects.select_related(
                "quiz_type",
                "course_offering__academic_year_level__academic_year",
                "course_offering__academic_year_level__level",
            ).annotate(question_total=Count("questions")),
            **quiz_filters,
        )

        submission_datetime = now()
        opening = QuizStudentOpening.objects.filter(
            quiz=quiz,
            student=user,
        ).first()
        effective_opening_date = opening.opening_date if opening else quiz.opening_date
        effective_closing_date = opening.closing_date if opening else quiz.closing_date
        quiz_mode, grade, snapshot = get_student_quiz_status(
            quiz,
            user,
            submission_datetime,
            opening=opening,
            opening_loaded=True,
        )
        if not preview_requested and not user_has_management_role(user) and grade is None:
            if snapshot is not None and snapshot.deadline_at <= submission_datetime:
                reconcile_snapshots(quiz.pk, user_id=user.pk)
                grade = Grade.objects.filter(quiz=quiz, user=user).first()
                snapshot = None
                quiz_mode = "view" if grade is not None else "closed_unsolved"

        total_grade = grade.total_grade if grade else 0

        if request.method == "GET":
            # Determine quiz mode using cohort-year window logic
            if preview_requested:
                quiz_mode = "preview"
                query_set = Question.objects.filter(quiz_id=quiz_id)
            elif user_has_management_role(user):
                # Admins/teachers always see in "view" mode for student quizzes
                quiz_mode = "view"
                query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user) if grade else Question.objects.filter(quiz_id=quiz_id)
            elif grade:
                # Student has already submitted - always show their submission
                quiz_mode = "view"
                query_set = Submission.objects.filter(
                    question__quiz_id=quiz_id,
                    user=user,
                ).select_related("question")
            elif quiz_mode == "exam":
                quiz_mode = "exam"
                query_set = Question.objects.filter(quiz_id=quiz_id)
            elif quiz_mode in {"ready", "upcoming"}:
                query_set = Question.objects.none()
            else:
                # Quiz is closed or re-opened for a newer cohort; student never submitted
                quiz_mode = "closed_unsolved"
                query_set = Question.objects.filter(quiz_id=quiz_id)

            # Serialize questions; strip answer data to prevent leakage
            if quiz_mode in {"exam", "preview"}:
                questions = [q.serialize() for q in query_set]
                random.shuffle(questions)
                questions = [prepare_exam_question(q) for q in questions]
                for q in questions:
                    q.pop("answer_payload", None)
                    q.pop("answer_payload_json", None)
                    q.pop("config_json", None)
                    q.pop("correct_answer", None)
                    q.pop("config", None)
            elif quiz_mode == "closed_unsolved":
                questions = [q.serialize_student() for q in query_set]
            else:
                questions = [q.serialize() for q in query_set]
                if not user_has_management_role(user):
                    # Solved students see the answer keys. Closed-and-unsolved
                    # students render through serialize_student() above, which
                    # strips every answer field.
                    if not answers_releasable(grade):
                        for q in questions:
                            q.pop("correct_answer", None)
                            q.pop("answer_payload", None)
                            q.pop("answer_payload_json", None)

            active_snapshot = snapshot if quiz_mode == "exam" else None
            server_time = now()
            quiz_context = {
                "snapshot_data": {
                    "answers": active_snapshot.answers if active_snapshot else {},
                    "revision": active_snapshot.revision if active_snapshot else 0,
                },
                "snapshot_url": reverse("quiz-snapshot", args=[offering_id, quiz_id]),
                "quiz_name": quiz.name,
                "username": request.user.username,
                "questions": questions,
                "is_student": True,
                "mode": quiz_mode,
                "extended_view": "base.html",
                "id": "container",
                "total_grade": total_grade,
                "attempt_deadline": active_snapshot.deadline_at.timestamp() if active_snapshot else None,
                "server_now_ms": int(server_time.timestamp() * 1000),
                "quiz_duration_minutes": quiz.duration_minutes,
                "exam_taken": True if grade else False,
                "back_url": reverse("quiz-view", args=[quiz_id]) if preview_requested else reverse("course-details", args=[offering_id]),
                "quiz_opening_date_str": format_human_datetime(effective_opening_date, user),
                "quiz_closing_date_str": format_human_datetime(effective_closing_date, user),
                "effective_opening_date": effective_opening_date,
                "effective_closing_date": effective_closing_date,
                "quiz_start_url": reverse("quiz-start", args=[offering_id, quiz_id]),
                "offering": offering,
                "quiz": quiz,
            }
            if resolve_student_mode(request) == StudentMode.NEW and not user_has_management_role(user):
                return render_student_page(
                    request,
                    page="quiz",
                    template="student_next/quiz.html",
                    context=build_quiz_presentation_context(
                        quiz_context,
                        user=request.user,
                    ),
                )
            return render(request, "display_quiz.html", quiz_context)
        
        elif request.method == "POST":
            # Prevent another submission
            if not grade:
                questions_data, not_used = unpack_quiz_form(request.POST)
                try:
                    answers = {str(data["id"]): data.get("answer", "") for data in questions_data.values()}
                    submit_answers(quiz_id, user, answers)
                    success(request, _("Quiz is sent successfully!"), extra_tags="alert-success") # Translate

                except PermissionDenied:
                    raise
                except Exception:
                    error(request, _("Sending quiz has failed, Please Try again!"), extra_tags="alert-danger") # Translate
                    logger.exception(
                        "quiz submission failed",
                        extra={
                            "event": "quiz_submission_failed",
                            "quiz_id": quiz.pk,
                            "offering_id": offering.pk,
                            "student_id": user.pk,
                        },
                    )
            
            return redirect(reverse("quiz-details", args=[offering_id, quiz_id]))
        
        else:
            return HttpResponse(_("Not allowed method"), 400) # Translate
        
    except Http404:
        raise Http404

@require_POST
@ajax_login_required
def quiz_snapshot(request, offering_id, quiz_id):
    if len(request.body) > 1100000:
        return JsonResponse({"error": _("Invalid quiz answers.")}, status=400)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        reconcile = payload.get("reconcile") is True
        offering = get_accessible_offering_or_403(request.user, offering_id, write=not reconcile)
        quiz = get_object_or_404(Quiz, pk=quiz_id, course_offering=offering, status=PublicationStatus.PUBLISHED)
        return JsonResponse(quiz_snapshot_result(quiz=quiz, user=request.user, payload=payload))
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=409 if getattr(exc, "code", None) == "snapshot_conflict" else 400)
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": _("Invalid quiz answers.")}, status=400)


# Admin Views
#: The dashboard runs ~20 population aggregates. One short cache window keeps a
#: refresh storm (and the blue/green healthcheck) from re-scanning the whole
#: database while still showing fresh-enough numbers to an operator.
ADMIN_PANEL_KPI_CACHE_SECONDS = 60


def _admin_panel_cache_key(today) -> str:
    return f"admin-panel:kpis:v1:{today.isoformat()}"


def _admin_panel_cached_context(cache_key: str):
    try:
        return cache.get(cache_key)
    except Exception:
        logger.warning("admin dashboard cache read failed", extra={"event": "admin_dashboard_cache_error"})
        return None


def _admin_panel_store_context(cache_key: str, context: dict) -> None:
    try:
        cache.set(cache_key, context, ADMIN_PANEL_KPI_CACHE_SECONDS)
    except Exception:
        logger.warning("admin dashboard cache write failed", extra={"event": "admin_dashboard_cache_error"})


def _build_admin_panel_shared_context(today):
    """Shared, actor-independent Today aggregates.

    These are population-wide read aggregates with no actor-owned rows, so
    they stay behind the short shared cache. Actor-scoped queues are built
    fresh per request by ``admin_today.build_today_context``.
    """
    users_total = User.objects.count()
    courses_total = Course.objects.count()
    lessons_total = Lesson.objects.count()
    quizzes_total = Quiz.objects.count()
    offerings_total = CourseOffering.objects.count()
    academic_years_total = AcademicYear.objects.count()
    current_academic_years = list(AcademicYear.objects.filter(is_active=True))
    today_attendance = AttendanceRecord.objects.filter(
        attendance_date=today
    ).count()
    recent_enrollments = list(Enrollment.objects.select_related(
        'student', 'academic_year_level__academic_year'
    ).order_by('-enrolled_at')[:5])
    recent_submissions = list(Grade.objects.select_related(
        'user', 'quiz__course_offering__course'
    ).order_by('-submitted_at')[:5])

    return {
        "today": today,
        "admin_metrics": {
            "users": users_total,
            "courses": courses_total,
            "lessons": lessons_total,
            "quizzes": quizzes_total,
            "academic_years": academic_years_total,
            "offerings": offerings_total,
        },
        "current_academic_years": current_academic_years,
        "today_attendance": today_attendance,
        "recent_enrollments": recent_enrollments,
        "recent_submissions": recent_submissions,
    }


def _admin_panel_today_context(request, shared_context):
    """Build the actor-scoped Today cockpit contract.

    Queues are capability-filtered with the same canonical functions as their
    destination views. Staff media ownership is enforced inside the adapter.
    """
    user = request.user
    active_year = (shared_context.get("current_academic_years") or [None])[0]
    today_context = build_today_context(
        actor=user,
        active_year=active_year,
        can_see_admin_queues=can_manage_academic_setup(user),
        can_grade=can_grade(user),
    )
    metrics = shared_context.get("admin_metrics", {})
    queue_by_key = {queue["key"]: queue for queue in today_context["queues"]}

    def _queue_kpi(key, label, icon, tone, meta):
        queue = queue_by_key.get(key)
        return {
            "key": key,
            "label": label,
            "value": queue["count"] if queue else 0,
            "icon": icon,
            "tone": tone,
            "meta": meta,
            "url": queue["url"] if queue else None,
        }

    # Decision cockpit, as in the prototype: the first four tiles are the live
    # queues an operator acts on, not content inventory. Values come from the
    # bounded queue counts already built above.
    today_kpis = [
        _queue_kpi("applications", _("Pending applications"), "i-file", "amber",
                   _("Waiting on a decision")),
        _queue_kpi("media", _("Failed media jobs"), "i-upload", "red",
                   _("Need a retry")),
        _queue_kpi("grading", _("Ungraded answers"), "i-check-square", "blue",
                   _("Waiting for a grade")),
        _queue_kpi("support", _("Unread Telegram"), "i-message", "teal",
                   _("Unanswered conversations")),
        {
            "key": "active_students",
            "label": _("Active students"),
            "value": today_context["stats"]["active_students"],
            "icon": "i-users",
            "tone": "green",
            "meta": _("Across all levels"),
            "url": None,
        },
        {
            "key": "attendance_today",
            "label": _("Attendance today"),
            "value": shared_context.get("today_attendance", 0),
            "icon": "i-check-square",
            "tone": "gold",
            "meta": _("Scans recorded today"),
            "url": reverse("attendance-management"),
        },
    ]
    actions = [
        {
            "label": _("Calendar"),
            "url": reverse("calendar-management"),
            "icon": "i-calendar",
            "variant": "ghost",
        },
    ]
    if can_manage_academic_setup(user):
        actions.append({
            "label": _("Academic Setup"),
            "url": reverse("academic-setup"),
            "icon": "i-layers",
            "variant": "ghost",
        })
        actions.append({
            "label": _("New announcement"),
            "url": reverse("notifications-management"),
            "icon": "i-plus",
            "variant": "primary",
        })
    return {
        **today_context,
        "active_academic_year": active_year,
        "today_kpis": today_kpis,
        "today_actions": actions,
        "quick_actions": quick_create_actions_for(user),
    }


@capability_required(can_manage_content)
def admin_panel(request):
    """Today cockpit: shared cached aggregates plus fresh actor-scoped queues."""
    today = timezone.now().date()
    cache_key = _admin_panel_cache_key(today)
    shared_context = _admin_panel_cached_context(cache_key)
    if shared_context is None:
        shared_context = _build_admin_panel_shared_context(today)
        _admin_panel_store_context(cache_key, shared_context)

    context = dict(shared_context)
    context["today"] = today
    context.update(_admin_panel_today_context(request, shared_context))
    return render_page(
        request,
        "admin_panel.html",
        "partials/today_content.html",
        context,
    )

@capability_required(can_manage_content)
def export_users_csv(request):
    return export_users_to_csv(user_scope(request))


def _user_filter_fields(scope):
    """Return the Students filter bar contract for the normalized scope."""
    application_options = [
        {"value": code, "label": label}
        for code, label in User.APPLICATION_STATUSES
    ]
    mode_options = [
        {"value": code, "label": label}
        for code, label in User.STUDY_MODES
    ]
    role_options = [
        {"value": code, "label": label}
        for code, label in Role.ROLES
    ]
    return [
        {
            "name": "filtering",
            "label": _("Role"),
            "value": scope["role"],
            "all_label": _("All roles"),
            "options": role_options,
        },
        {
            "name": "application_status",
            "label": _("Application"),
            "value": scope["application_status"],
            "all_label": _("All application statuses"),
            "options": application_options,
        },
        {
            "name": "study_mode",
            "label": _("Mode"),
            "value": scope["study_mode"],
            "all_label": _("All study modes"),
            "options": mode_options,
        },
        {
            "name": "account",
            "label": _("Account"),
            "value": scope["account"],
            "all_label": _("All account states"),
            "options": [
                {"value": "active", "label": _("Active")},
                {"value": "inactive", "label": _("Inactive")},
            ],
        },
    ]


def _user_status_tabs(request, scope, counts):
    """Build the application-decision tabs with accurate bounded counts."""
    tabs = []
    current = scope["application_status"]
    for code, label in (("", _("All")),) + tuple(User.APPLICATION_STATUSES):
        params = request.GET.copy()
        params.pop("page", None)
        if code:
            params["application_status"] = code
        else:
            params.pop("application_status", None)
        tabs.append({
            "key": code or "all",
            "label": label,
            "url": f"{reverse('user-dashboard')}?{params.urlencode()}",
            "is_active": current == code,
            "count": counts["application"]["total"] if not code else counts["application"][code],
        })
    return tabs


def _content_filter_fields(*, scope, academic_years, selected_year, subjects):
    """Return the lesson/quiz filter bar contract for the normalized scope."""
    year_options = [
        {"value": str(year.pk), "label": year.name}
        for year in academic_years
    ]
    return [
        {
            "name": "academic_year",
            "label": _("Academic Year"),
            "value": str(selected_year.pk),
            "all_label": "",
            "options": year_options,
            "page_target": True,
        },
        {
            "name": "course",
            "label": _("Course Filter"),
            "value": str(scope["offering"]) if scope["offering"] else "",
            "all_label": _("All Courses"),
            "groups": subjects,
        },
    ]


def _content_status_tabs(request, *, view, scope, counts):
    """Build the publication-status tabs with accurate bounded counts."""
    tabs = []
    options = (("", _("All")),) + tuple(PublicationStatus.choices)
    for code, label in options:
        params = request.GET.copy()
        params.pop("page", None)
        if code:
            params["status"] = code
        else:
            params.pop("status", None)
        tabs.append({
            "key": code or "all",
            "label": label,
            "url": f"{reverse(f'{view}-dashboard')}?{params.urlencode()}",
            "is_active": scope["status"] == code,
            "count": counts["total"] if not code else counts[code],
        })
    return tabs


@capability_required(can_manage_content)
def user_dashboard(request):
    view = "user"
    user = request.user

    scope = user_scope(request)
    users = user_queryset(scope)
    counts = user_tab_counts(scope)

    logger.info(
        "User : %s filters users using search=%s role=%s application=%s mode=%s account=%s",
        user,
        scope["search"],
        scope["role"],
        scope["application_status"],
        scope["study_mode"],
        scope["account"],
    )

    export_query = request.GET.copy()
    export_query.pop("page", None)
    export_url = reverse("export-users-csv")
    if export_query:
        export_url = f"{export_url}?{export_query.urlencode()}"

    page_actions = []
    if can_manage_applications(request.user):
        page_actions.append({
            "label": _("Create student"),
            "url": reverse("user-create"),
            "icon": "i-user-plus",
            "variant": "primary",
        })
        page_actions.append({
            "label": _("Bulk Import"),
            "url": reverse("user-bulk-create"),
            "icon": "i-upload",
            "variant": "ghost",
        })
    page_actions.append({
        "label": _("Export CSV"),
        "url": export_url,
        "icon": "i-download",
        "variant": "ghost",
        "download": True,
    })

    context = {
        "name_value": scope["search"],
        "search_placeholder": _("Search username, email, name, phone, or national ID"),
        "filter_fields": _user_filter_fields(scope),
        "filter_tabs": _user_status_tabs(request, scope, counts),
        "table_columns": [
            {"label": _("Students"), "key": "student"},
            {"label": _("Application"), "key": "application_status"},
            {"label": _("Mode"), "key": "study_mode"},
            {"label": _("Role"), "key": "role"},
            {"label": _("Joined Date"), "key": "joined_date"},
            {"label": _("Last Login"), "key": "last_login"},
        ],
        "sort_fields": {
            "student": ("last_name", "first_name", "username"),
            "application_status": "application_status",
            "study_mode": "study_mode",
            "role": "role__role",
            "joined_date": "joined_date",
            "last_login": "last_login",
        },
        "default_sort": "student",
        "default_ordering": ["last_name", "first_name", "username", "pk"],
        "table_scope": scope,
        "row_template": "partials/tables/user_rows.html",
        "page_eyebrow": _("People"),
        "page_title": _("Students"),
        "page_description": _(
            "Manage student accounts, application decisions and study mode access."
        ),
        "page_actions": page_actions,
    }

    return render_dashboard(request, users, view, context)


@capability_required(can_manage_content)
def user_profile(request, user_id):
    if request.method == "POST" and not can_manage_applications(request.user):
        raise PermissionDenied
    user = get_object_or_404(
        User.objects.select_related("role", "decided_by"),
        pk=user_id,
    )
    form = UserUpdateForm(request.POST or None, request.FILES or None, instance=user)
    _restrict_user_update_form(form, request.user)

    if request.method == "POST" and form.is_valid():
        original = User.objects.get(pk=user.pk)
        actor_role = getattr(getattr(request.user, "role", None), "role", None)
        desired_status = form.cleaned_data.get("application_status", original.application_status)
        staged = None
        change = None
        database_committed = False
        try:
            with transaction.atomic():
                lock_application_document_namespace(user.id)
                staged = stage_application_documents(
                    cloud_client=CLOUD_CLIENT,
                    bucket_name=bucket_name,
                    user_id=user.id,
                    cleaned_data=form.cleaned_data,
                )
                locked_user = lock_application_status_for_edit(user.pk, original.application_status)
                if desired_status != original.application_status:
                    form.cleaned_data["application_status"] = original.application_status
                apply_cleaned_data_to_locked_instance(form, locked_user)
                updated_user = form.save()
                change = apply_application_document_changes(
                    updated_user, staged, form.cleaned_data
                )
                if actor_role == "admin" and desired_status != original.application_status:
                    updated_user, _enrollment = set_application_status(
                        updated_user, request.user, desired_status, original.application_status
                    )
                if (
                    actor_role == "admin"
                    and form.cleaned_data.get("study_mode", original.study_mode) == original.study_mode
                    and apply_study_mode_from_city(updated_user)
                ):
                    updated_user.save(update_fields=["study_mode"])
                if desired_status == original.application_status and (
                    original.is_active and not updated_user.is_active
                    or original.role_id != updated_user.role_id
                ):
                    revoke_user_mobile_access(updated_user)
                scope = form.cleaned_data.get("enrollment_scope")
                if actor_role == "admin" and scope and desired_status == "active":
                    set_user_normal_enrollment_scope(updated_user, request.user, scope)
            database_committed = True
            cleanup_replaced_application_documents(change, CLOUD_CLIENT, bucket_name)
        except ValidationError as exc:
            if not database_committed and staged is not None:
                rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
            form.add_error(None, exc)
        except Exception:
            if not database_committed and staged is not None:
                rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
            raise
        else:
            if user.pk == request.user.pk and form.cleaned_data.get("password"):
                update_session_auth_hash(request, updated_user)
            if desired_status != original.application_status:
                send_application_status_notification(updated_user, desired_status)
            messages.success(request, _("User data updated successfully."))
            return redirect("user-profile", user_id=user.pk)

    if request.method == "POST":
        user.refresh_from_db()
    active_enrollment = (
        Enrollment.objects.filter(
            student=user,
            academic_year_level__academic_year__is_active=True,
            enrollment_type=Enrollment.Type.NORMAL,
            course_offering__isnull=True,
            status=Enrollment.Status.ACTIVE,
        )
        .select_related("academic_year_level__academic_year", "academic_year_level__level")
        .first()
    )
    can_view_application_data = can_manage_applications(request.user)
    application_documents = []
    if can_view_application_data:
        for document_type, field_name in APPLICATION_DOCUMENT_FIELDS.items():
            if getattr(user, field_name, None):
                application_documents.append({
                    "label": APPLICATION_DOCUMENT_LABELS[document_type],
                    "url": reverse("application-document", args=[user.pk, document_type]),
                    "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                })

    telegram_account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    can_manage_student_experience = (
        can_manage_academic_setup(request.user)
        and getattr(user.role, "role", None) == "student"
    )
    student_experience_effective_mode_label = (
        dict(StudentMode.choices)[resolve_student_mode(user)]
        if can_manage_student_experience else None
    )
    return render_page(request, "user_detail.html", "partials/user_detail_content.html", {
        "profile_user": user,
        "edit_form": form,
        "can_edit_user": can_manage_applications(request.user),
        "can_manage_student_experience": can_manage_student_experience,
        "student_experience_override_form": (
            StudentExperienceOverrideForm(
                initial={"student_experience_override": user.student_experience_override}
            )
            if can_manage_student_experience else None
        ),
        "student_experience_effective_mode_label": student_experience_effective_mode_label,
        "active_enrollment": active_enrollment,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Users"), reverse("user-dashboard")),
            (user.get_full_name() or user.username, None),
        ]),
        "can_view_application_data": can_view_application_data,
        "application_documents": application_documents,
        "telegram_account": telegram_account,
        "telegram_can_unlink": bool(
            telegram_account
            and getattr(getattr(request.user, "role", None), "role", None) == "admin"
        ),
        "telegram_unlink_url": reverse("telegram-admin-unlink", args=[user.pk]),
    })


@capability_required(can_manage_academic_setup)
def historical_intake(request):
    form = HistoricalIntakeForm(request.POST or None)
    exceptional_form = ExceptionalCourseAssignmentForm(request.POST or None)
    existing_q = str(request.GET.get("student_search") or request.GET.get("existing_q") or request.POST.get("student_search") or "")[:64].strip()
    # The picker accepts every student-role account, exactly like _find_user().
    # Inactive accounts are included: historical records are often added for
    # students whose accounts are no longer active.
    base_students = User.objects.filter(role__role="student").select_related("role")
    if request.method == "POST":
        # Validation runs only against the submitted IDs, so the check stays
        # bounded no matter how many student accounts exist. The queryset must
        # NOT be sliced: ModelMultipleChoiceField validates by filtering it,
        # and filtering a sliced queryset fails every submitted value.
        posted_ids = [int(value) for value in request.POST.getlist("students") if value.isdigit()][: MAX_EXISTING_BULK_STUDENTS + 5]
        student_options = base_students.filter(pk__in=posted_ids).order_by("last_name", "first_name", "username", "pk")
        existing_form = HistoricalExistingBulkForm(request.POST, student_options=student_options)
    else:
        existing_form = HistoricalExistingBulkForm(
            student_options=base_students.none(),
            initial={"student_search": existing_q},
        )
    preview = []
    upload_digest = ""
    existing_preview_rows = []
    if request.method == "POST":
        if request.POST.get("action") == "assign_exceptional":
            if exceptional_form.is_valid():
                try:
                    assign_exceptional_courses(
                        student_id=exceptional_form.cleaned_data["student_identifier"].pk,
                        offering_ids=exceptional_form.cleaned_data["course_offerings"].values_list("pk", flat=True),
                        actor=request.user,
                    )
                    messages.success(request, _("Exceptional course access was assigned."))
                    return redirect("historical-intake")
                except (ValidationError, PermissionDenied) as exc:
                    exceptional_form.add_error(None, str(exc))
        elif request.POST.get("action") == "existing_preview" and existing_form.is_valid():
            cleaned = existing_form.cleaned_data
            users = list(cleaned["students"])[:MAX_EXISTING_BULK_STUDENTS]
            rows = []
            for user in users:
                row = build_existing_intake_row(
                    user=user,
                    source_scope=cleaned["source_year_level"],
                    outcome=cleaned["historical_outcome"],
                    destination=cleaned["destination_scope"],
                    promote=bool(cleaned["promote_now"]),
                    failed_ids=[],
                    reason=cleaned["promotion_reason"] or "",
                    notes=cleaned["notes"] or "",
                )
                row["failed_course_offering_ids"] = cleaned["exceptional_offering_ids"] or ""
                rows.append(row)
            known = summaries_for_users_scopes(
                users,
                [cleaned["source_year_level"]] + ([cleaned["destination_scope"]] if cleaned["destination_scope"] else []),
            )
            for user, row, item in zip(users, rows, preview_intake_rows(rows)):
                summary = known.get((user.pk, cleaned["source_year_level"].pk))
                existing_preview_rows.append({
                    "user": user,
                    "source_id": cleaned["source_year_level"].pk,
                    "outcome": cleaned["historical_outcome"],
                    "dest_id": cleaned["destination_scope"].pk if cleaned["destination_scope"] else "",
                    "failed": cleaned["exceptional_offering_ids"] or "",
                    "account": item["account"],
                    "message": item["message"],
                    "action": item["action"],
                    "has_record": summary is not None,
                    "already_promoted": bool(summary and summary.promoted_at),
                })
        elif request.POST.get("action") == "existing_apply" and existing_form.is_valid():
            cleaned = existing_form.cleaned_data
            users = list(cleaned["students"])[:MAX_EXISTING_BULK_STUDENTS]
            promote = bool(cleaned["promote_now"])
            reason = cleaned["promotion_reason"] or ""
            notes = cleaned["notes"] or ""
            raw_sources = {}
            raw_outcomes = {}
            raw_dests = {}
            raw_failed = {}
            for user in users:
                key = str(user.pk)
                raw_sources[user.pk] = request.POST.get(f"er_source_{key}") or str(cleaned["source_year_level"].pk)
                raw_outcomes[user.pk] = request.POST.get(f"er_outcome_{key}") or cleaned["historical_outcome"]
                raw_dests[user.pk] = request.POST.get(f"er_dest_{key}", "")
                if not raw_dests[user.pk] and cleaned["destination_scope"]:
                    raw_dests[user.pk] = str(cleaned["destination_scope"].pk)
                raw_failed[user.pk] = request.POST.get(f"er_failed_{key}", cleaned["exceptional_offering_ids"] or "")
            source_map = {
                scope.pk: scope
                for scope in AcademicYearLevel.objects.select_related("academic_year", "level").filter(
                    pk__in=[int(value) for value in set(raw_sources.values()) if value.isdigit()],
                    academic_year__is_active=False,
                )
            }
            dest_map = {
                scope.pk: scope
                for scope in AcademicYearLevel.objects.select_related("academic_year", "level").filter(
                    pk__in=[int(value) for value in set(raw_dests.values()) if value.isdigit()],
                    academic_year__is_active=True,
                )
            }
            rows = []
            row_errors = []
            for user in users:
                source = source_map.get(int(raw_sources[user.pk])) if raw_sources[user.pk].isdigit() else None
                outcome = raw_outcomes[user.pk]
                dest = dest_map.get(int(raw_dests[user.pk])) if raw_dests[user.pk].isdigit() else None
                if source is None:
                    row_errors.append(_("Row for %(student)s has no valid source academic scope.") % {"student": user.username})
                    continue
                if outcome not in HistoricalAcademicSummary.Outcome.values:
                    row_errors.append(_("Row for %(student)s has an invalid historical outcome.") % {"student": user.username})
                    continue
                rows.append((user, build_existing_intake_row(
                    user=user,
                    source_scope=source,
                    outcome=outcome,
                    destination=dest,
                    promote=promote,
                    failed_ids=[],
                    reason=reason,
                    notes=notes,
                )))
                rows[-1][1]["failed_course_offering_ids"] = raw_failed[user.pk] or ""
            # No separate pre-validation here: intake_historical_row() validates
            # each row inside the transaction, so concurrent changes between
            # preview and apply cannot slip through, and failures below are
            # re-labeled with their student before the rollback.
            if row_errors:
                for error in row_errors:
                    existing_form.add_error(None, error)
                known = summaries_for_users_scopes(users, list(source_map.values()))
                for user in users:
                    existing_preview_rows.append({
                        "user": user,
                        "source_id": raw_sources[user.pk] if raw_sources[user.pk].isdigit() else "",
                        "outcome": raw_outcomes[user.pk],
                        "dest_id": raw_dests[user.pk] if raw_dests[user.pk].isdigit() else "",
                        "failed": raw_failed[user.pk] or "",
                        "account": user.username,
                        "message": "; ".join(row_errors),
                        "action": "error",
                        "has_record": any(key[0] == user.pk for key in known),
                        "already_promoted": False,
                    })
            else:
                try:
                    with transaction.atomic():
                        # One bounded context for the selected page instead of
                        # re-resolving identity/scope facts per row.
                        lookups = IntakeLookups.from_rows([row for _user, row in rows])
                        for index, (user, row) in enumerate(rows):
                            try:
                                intake_historical_row(
                                    row=row,
                                    actor=request.user,
                                    source_key=f"existing:{request.user.pk}:{uuid.uuid4().hex}:{index}",
                                    source_file="admin-existing-bulk",
                                    lookups=lookups,
                                )
                            except ValidationError as row_exc:
                                raise ValidationError(
                                    _("Row for %(student)s: %(message)s") % {"student": user.username, "message": "; ".join(row_exc.messages)}
                                ) from row_exc
                    messages.success(request, _("Historical intake applied for %(count)d student(s).") % {"count": len(rows)})
                    return redirect("historical-intake")
                except ValidationError as exc:
                    existing_form.add_error(None, "; ".join(exc.messages))
                    known = summaries_for_users_scopes(users, list(source_map.values()))
                    for (user, row), item in zip(rows, preview_intake_rows([row for _, row in rows])):
                        source_pk = int(raw_sources[user.pk]) if raw_sources[user.pk].isdigit() else -1
                        summary = known.get((user.pk, source_pk))
                        existing_preview_rows.append({
                            "user": user,
                            "source_id": raw_sources[user.pk] if raw_sources[user.pk].isdigit() else "",
                            "outcome": raw_outcomes[user.pk],
                            "dest_id": raw_dests[user.pk] if raw_dests[user.pk].isdigit() else "",
                            "failed": raw_failed[user.pk] or "",
                            "account": item["account"],
                            "message": item["message"],
                            "action": item["action"],
                            "has_record": summary is not None,
                            "already_promoted": bool(summary and summary.promoted_at),
                        })
        elif request.FILES.get("intake_file"):
            try:
                upload = request.FILES["intake_file"]
                rows, upload_digest = parse_intake_upload(upload)
                preview = preview_intake_rows(rows)
                if request.POST.get("apply_upload") == "yes":
                    errors = [item for item in preview if item["action"] == "error"]
                    if errors:
                        raise ValidationError(_("Resolve all upload errors before applying the intake."))
                    intake_job = enqueue_historical_intake(
                        rows=rows,
                        actor=request.user,
                        source_file=upload.name,
                        digest=upload_digest,
                        language=translation.get_language() or "ar",
                    )
                    try:

                        enqueue_admin_operation(intake_job.pk, queue="admin")
                    except Exception:
                        # The queued row is picked up by the recovery schedule.
                        logger.warning("historical intake awaits worker recovery", exc_info=True)
                    messages.success(
                        request,
                        _("Historical intake was queued for %(count)d row(s). Rows are applied one at a time; review progress below.")
                        % {"count": len(rows)},
                    )
                    return redirect("historical-intake")
            except (ValidationError, UnicodeDecodeError, ValueError) as exc:
                messages.error(request, str(exc))
        elif form.is_valid():
            cleaned = form.cleaned_data
            row = {
                "source_name": cleaned["source_name"],
                "source_level": str(cleaned["source_year_level"].level.ordering),
                "source_academic_year": cleaned["source_year_level"].academic_year.name,
                "historical_outcome": cleaned["historical_outcome"],
                "account_action": cleaned["account_action"],
                "lms_user_id": str(cleaned["lms_user_id"] or ""),
                "lms_username": cleaned["lms_username"] or cleaned["username"],
                "lms_email": cleaned["lms_email"],
                "username": cleaned["username"],
                "first_name": cleaned["first_name"],
                "last_name": cleaned["last_name"],
                "destination_academic_year": cleaned["destination_scope"].academic_year.name if cleaned["destination_scope"] else "",
                "destination_level": str(cleaned["destination_scope"].level.ordering) if cleaned["destination_scope"] else "",
                "promote_now": "yes" if cleaned["promote_now"] else "no",
                "failed_course_offering_ids": cleaned["exceptional_offering_ids"],
                "promotion_reason": cleaned["promotion_reason"],
                "admin_note": cleaned["notes"],
            }
            try:
                summary = intake_historical_row(
                    row=row,
                    actor=request.user,
                    source_key=f"manual:{uuid.uuid4().hex}",
                    source_file="admin-manual",
                )
                messages.success(request, _("Historical record saved for %(student)s.") % {"student": summary.student.username})
                return redirect("historical-intake")
            except (ValidationError, User.DoesNotExist) as exc:
                form.add_error(None, str(exc))
    # Checkbox picker display lists for every non-redirect response. Selected
    # students stay ticked across searches; unticking removes them. Both lists
    # are bounded: at most ~50 selected plus 20 search results.
    if request.method == "GET":
        picker_ids = [int(value) for value in request.GET.getlist("students") if value.isdigit()][: MAX_EXISTING_BULK_STUDENTS + 5]
    elif request.POST.get("action") in ("existing_preview", "existing_apply") and existing_form.is_valid():
        picker_ids = [user.pk for user in existing_form.cleaned_data["students"]]
    elif request.POST.get("action") in ("existing_preview", "existing_apply"):
        picker_ids = [int(value) for value in request.POST.getlist("students") if value.isdigit()][: MAX_EXISTING_BULK_STUDENTS + 5]
    else:
        picker_ids = []
    picker_selected = list(base_students.filter(pk__in=picker_ids).order_by("last_name", "first_name", "username", "pk")[: MAX_EXISTING_BULK_STUDENTS + 5])
    picker_results: list = []
    if existing_q:
        search_filter = normalized_contains_q(("username", "email", "first_name", "last_name"), existing_q)
        if existing_q.isdigit():
            search_filter |= Q(pk=int(existing_q))
        picker_results = list(
            base_students.filter(search_filter).exclude(pk__in=[user.pk for user in picker_selected]).order_by("last_name", "first_name", "username", "pk")[:20]
        )
    summaries = HistoricalAcademicSummary.objects.select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level", "promotion_history__actor"
    ).order_by("-created_at")
    record_search = str(request.GET.get("record_search") or "")[:64].strip()
    if record_search:
        record_filter = normalized_contains_q(
            ("student__username", "student__email", "student__first_name", "student__last_name", "source_name"),
            record_search,
        )
        if record_search.isdigit():
            record_filter |= Q(student__pk=int(record_search))
        summaries = summaries.filter(record_filter)
    summary_page_obj = Paginator(summaries, 25).get_page(request.GET.get("page", 1))
    intake_job = (
        AdminOperationJob.objects.filter(
            actor=request.user,
            kind=AdminOperationJob.Kind.HISTORICAL_INTAKE,
        )
        .order_by("-created_at")
        .first()
    )
    return render_page(request, "historical_intake.html", "partials/historical_intake_content.html", {
        "form": form,
        "exceptional_form": exceptional_form,
        "existing_form": existing_form,
        "existing_preview_rows": existing_preview_rows,
        "existing_q": existing_q,
        "selected_users": picker_selected,
        "student_search_results": picker_results,
        "record_search": record_search,
        "outcome_choices": HistoricalAcademicSummary.Outcome.choices,
        "summaries": summary_page_obj.object_list,
        "summary_page_obj": summary_page_obj,
        "pagination_query": pagination_query_string(request),
        "preview": preview,
        "upload_digest": upload_digest,
        "intake_columns": INTAKE_COLUMNS,
        "intake_job": intake_job,
        "intake_status_url": (
            reverse("admin-operation-status", args=[intake_job.pk]) if intake_job else ""
        ),
    })


@require_POST
@capability_required(can_manage_academic_setup)
def historical_promote(request, summary_id):
    summary = get_object_or_404(HistoricalAcademicSummary, pk=summary_id)
    destination_id = request.POST.get("destination_scope")
    try:
        destination = AcademicYearLevel.objects.get(pk=destination_id) if destination_id else None
        exceptional_ids = [int(value) for value in request.POST.getlist("exceptional_offering_ids") if value.isdigit()]
        reason = request.POST.get("promotion_reason", "").strip()
        promote_historical_summary(
            summary_id=summary.pk,
            destination_scope_id=destination.pk if destination else None,
            exceptional_offering_ids=exceptional_ids,
            reason=reason,
            actor=request.user,
        )
        messages.success(request, _("Historical student promotion was recorded."))
    except (ValidationError, PermissionDenied, AcademicYearLevel.DoesNotExist) as exc:
        messages.error(request, str(exc))
    return redirect("historical-intake")


@require_POST
@capability_required(can_manage_academic_setup)
def exceptional_course_assign(request, user_id):
    try:
        values = [int(value) for value in request.POST.getlist("course_offering_ids") if value.isdigit()]
        assign_exceptional_courses(student_id=user_id, offering_ids=values, actor=request.user)
        messages.success(request, _("Exceptional course access was assigned."))
    except (ValidationError, PermissionDenied, User.DoesNotExist) as exc:
        messages.error(request, str(exc))
    return redirect("historical-intake")

@capability_required(can_manage_applications)
def user_bulk_create(request):
    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['csv_file']
            if not csv_file.name.lower().endswith('.csv'):
                error(request, _('This is not a CSV file'), extra_tags="alert-danger")
                return redirect('user-bulk-create')

            try:
                content = read_legacy_import_upload(csv_file)
                # Bounded pre-check without hashing: the authoritative pass,
                # including password hashing and the atomic apply, runs on the
                # admin worker.
                precheck = validate_legacy_import_rows(content, hash_passwords=False)
            except ValidationError as exc:
                for message in exc.messages:
                    error(request, message, extra_tags="alert-danger")
                return redirect('user-bulk-create')

            if precheck["errors"]:
                for message in precheck["errors"]:
                    error(request, message["message"], extra_tags="alert-danger")
                error(
                    request,
                    _("No users were created because the file has %(count)d invalid row(s).") % {
                        "count": precheck["error_count"],
                    },
                    extra_tags="alert-danger",
                )
                return redirect('user-bulk-create')

            job, storage_error = enqueue_legacy_user_import(
                actor=request.user,
                content=content,
                filename=csv_file.name,
                language=translation.get_language() or "ar",
            )
            if storage_error:
                error(request, storage_error, extra_tags="alert-danger")
                return redirect('user-bulk-create')
            try:

                enqueue_admin_operation(job.pk, queue="admin")
            except Exception:
                # The queued row is picked up by the recovery schedule.
                logger.warning("legacy user import awaits worker recovery", exc_info=True)
            success(
                request,
                _("The user import was queued for %(count)d row(s). Accounts are created only after the whole file validates; progress is available below.") % {
                    "count": len(precheck["rows"]),
                },
                extra_tags="alert-success",
            )
            return redirect('user-bulk-create')

    else:
        form = CSVUploadForm()

    bulk_job = (
        AdminOperationJob.objects.filter(
            actor=request.user,
            kind=AdminOperationJob.Kind.LEGACY_USER_IMPORT,
        )
        .order_by("-created_at")
        .first()
    )
    bulk_errors = []
    if bulk_job is not None and isinstance(bulk_job.result, dict):
        bulk_errors = list(bulk_job.result.get("row_errors") or [])[:20]
    return render_page(request, 'user_bulk_form.html', "partials/user_bulk_form_content.html", {
        'form': form,
        'bulk_job': bulk_job,
        'bulk_status_url': (
            reverse("admin-operation-status", args=[bulk_job.pk]) if bulk_job else ""
        ),
        'bulk_errors': bulk_errors,
    })

# User Dashboard
class CreateUser(UserBaseView, CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy("user-create")
    action = gettext_lazy("create")

def _restrict_user_update_form(form, actor):
    actor_role = getattr(getattr(actor, "role", None), "role", None)
    if actor_role != "admin":
        for field_name in UserUpdateForm.admin_only_fields:
            form.fields.pop(field_name, None)
        form.fields.pop("password", None)
    return form


class UpdateUser(UserBaseView, UpdateView):
    form_class = UserUpdateForm
    action = gettext_lazy("update")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        return _restrict_user_update_form(form, self.request.user)

    def form_valid(self, form):
        actor = self.request.user
        actor_role = getattr(getattr(actor, "role", None), "role", None)
        staged = None
        change = None
        database_committed = False
        try:
            original = User.objects.get(pk=self.object.pk)
            desired_status = form.cleaned_data.get("application_status", original.application_status)
            with transaction.atomic():
                lock_application_document_namespace(self.object.pk)
                staged = stage_application_documents(
                    cloud_client=CLOUD_CLIENT,
                    bucket_name=bucket_name,
                    user_id=self.object.pk,
                    cleaned_data=form.cleaned_data,
                )
                locked_user = lock_application_status_for_edit(self.object.pk, original.application_status)
                self.object = locked_user
                if desired_status != original.application_status:
                    form.cleaned_data["application_status"] = original.application_status
                apply_cleaned_data_to_locked_instance(form, locked_user)
                self.object = form.instance
                response = super().form_valid(form)
                change = apply_application_document_changes(
                    self.object, staged, form.cleaned_data
                )
                if actor_role == "admin" and desired_status != original.application_status:
                    self.object, _enrollment = set_application_status(
                        self.object, actor, desired_status, original.application_status
                    )
                if (
                    actor_role == "admin"
                    and form.cleaned_data.get("study_mode", original.study_mode) == original.study_mode
                    and apply_study_mode_from_city(self.object)
                ):
                    self.object.save(update_fields=["study_mode"])
                if desired_status == original.application_status and (
                    original.is_active and not self.object.is_active
                    or original.role_id != self.object.role_id
                ):
                    revoke_user_mobile_access(self.object)
                scope = form.cleaned_data.get("enrollment_scope")
                if actor_role == "admin" and scope and desired_status == "active":
                    set_user_normal_enrollment_scope(self.object, actor, scope)
            database_committed = True
            cleanup_replaced_application_documents(change, CLOUD_CLIENT, bucket_name)
        except ValidationError as exc:
            if not database_committed and staged is not None:
                rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
            form.add_error(None, exc)
            return self.form_invalid(form)
        except Exception:
            if not database_committed and staged is not None:
                rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
            raise
        # If the password field was changed, update the session to keep user logged in
        if self.object.pk == self.request.user.pk and "password" in form.cleaned_data and form.cleaned_data["password"]:
            update_session_auth_hash(self.request, self.object)
        if desired_status != original.application_status:
            send_application_status_notification(self.object, desired_status)

        return response

    def get_success_url(self):
        return reverse_lazy("user-update", args=[self.kwargs.get(self.pk_url_kwarg)])
    
class DeleteUser(UserBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            raise PermissionDenied
        return super(UserBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("user-dashboard")

# Course Dashboard
@capability_required(can_manage_content)
def course_dashboard(request):   
    user = request.user
    view = "course"

    scope = course_scope(request)
    # Distinct counts keep the three content joins from multiplying rows; one
    # page query supplies every course row, no per-row fact lookup.
    courses = course_queryset(scope).annotate(
        offering_count=Count("offerings", distinct=True),
        lesson_count=Count("offerings__lessons", distinct=True),
        quiz_count=Count("offerings__quizzes", distinct=True),
    )

    logger.info(
        "User : %s filters courses using %s name and %s level",
        user,
        scope["search"],
        scope["level_value"],
    )

    level_options = [
        {"value": str(level.ordering), "label": level.display_name}
        for level in Level.objects.order_by("ordering")
    ]
    context = {
        "name_value" : scope["search"],
        "filter_fields": [{
            "name": "filtering",
            "label": _("Level"),
            "value": str(scope["level"]) if scope["level"] is not None else "",
            "all_label": _("All levels"),
            "options": level_options,
        }],
        "columns" : Course.get_columns(),
        "table_columns": [
            {"label": _("Name"), "key": "name"},
            {"label": _("Description"), "key": "description"},
            {"label": _("Level"), "key": "level"},
            {"label": _("Instructor"), "key": "instructor"},
            {"label": _("Offerings"), "key": "offering_count"},
            {"label": _("Lessons"), "key": "lesson_count"},
            {"label": _("Quizzes"), "key": "quiz_count"},
        ],
        "sort_fields": {
            "name": "name",
            "description": "description",
            "level": "level__ordering",
            "instructor": "instructor",
            "offering_count": "offering_count",
            "lesson_count": "lesson_count",
            "quiz_count": "quiz_count",
        },
        "default_sort": "name",
        "default_ordering": ["level__ordering", "name", "pk"],
        "group_by_level": True,
        "table_scope": scope,
        "page_eyebrow": _("Curriculum"),
        "page_title": _("Courses"),
        "page_description": _("Manage the course catalog for each academic level."),
        "page_actions": [{
            "label": _("Create course"),
            "url": reverse("course-create"),
            "icon": "i-plus",
            "variant": "primary",
        }],
    }

    return render_dashboard(request, courses, view, context)

class CreateCourse(CourseBaseView, CreateView):
    action = gettext_lazy("create")
    success_url = reverse_lazy("course-create")

class UpdateCourse(CourseBaseView, UpdateView):
    action = gettext_lazy("update")

    def get_success_url(self):
        return reverse_lazy("course-update", args=[self.kwargs.get(self.pk_url_kwarg)])
    
class DeleteCourse(CourseBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            raise PermissionDenied
        return super(CourseBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("course-dashboard")


def _content_offering_filter_options(academic_year_id=None):
    """Grouped-by-level offering options for the lesson/quiz course filter."""
    offerings = CourseOffering.objects.select_related(
        "course", "academic_year_level__level", "academic_year_level__academic_year"
    ).order_by(
        "academic_year_level__level__ordering",
        "course__name",
        "academic_year_level__academic_year__ordering",
        "pk",
    )
    if academic_year_id is not None:
        offerings = offerings.filter(academic_year_level__academic_year_id=academic_year_id)
    return offering_option_groups(offerings)


def _requested_publication_status(request):
    status = request.POST.get("status")
    return status if status in PublicationStatus.values else None


@capability_required(can_manage_content)
def lesson_detail(request, lesson_id):
    lesson = get_object_or_404(
        Lesson.objects.select_related(
            "course_offering__course",
            "course_offering__academic_year_level__level",
            "course_offering__academic_year_level__academic_year",
        ),
        pk=lesson_id,
    )
    try:
        lesson_links = json.loads(lesson.links) if lesson.links else []
    except (TypeError, json.JSONDecodeError):
        lesson_links = []
    normalized_links = []
    if isinstance(lesson_links, list):
        for link in lesson_links:
            if not isinstance(link, dict):
                continue
            normalized_links.append({
                "name": link.get("name", ""),
                "file_type": link.get("file_type", ""),
                "id": link.get("id", ""),
                "file_id": link.get("file_id", ""),
                "type": link.get("type", ""),
            })
    return render_page(request, "content_detail.html", "partials/content_detail_content.html", {
        "content": lesson,
        "content_kind": "lesson",
        "offering": lesson.course_offering,
        "is_quiz": False,
        "lesson_links": normalized_links,
        "questions": (),
        "back_url": reverse("lesson-dashboard"),
        "edit_url": reverse("lesson-update", args=[lesson.pk]),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Lessons"), reverse("lesson-dashboard")),
            (lesson.name or _("(untitled)"), None),
        ]),
    })


@capability_required(can_manage_content)
def quiz_detail(request, quiz_id):
    quiz = get_object_or_404(
        Quiz.objects.select_related(
            "course_offering__course",
            "course_offering__academic_year_level__level",
            "course_offering__academic_year_level__academic_year",
            "quiz_type",
        ).prefetch_related(
            "questions",
            Prefetch(
                "student_openings",
                queryset=QuizStudentOpening.objects.select_related("student", "granted_by").order_by(
                    "student__last_name", "student__first_name", "student__username"
                ),
                to_attr="exceptional_openings",
            ),
        ),
        pk=quiz_id,
    )
    can_preview_quiz = can_manage_academic_setup(request.user)
    exception_form = QuizExceptionalOpeningForm(quiz=quiz) if can_preview_quiz else None
    return render_page(request, "content_detail.html", "partials/content_detail_content.html", {
        "content": quiz,
        "content_kind": "quiz",
        "offering": quiz.course_offering,
        "is_quiz": True,
        "lesson_links": (),
        "questions": quiz.questions.all(),
        "back_url": reverse("quiz-dashboard"),
        "edit_url": reverse("quiz-update", args=[quiz.pk]),
        "exception_form": exception_form,
        "exceptional_openings": getattr(quiz, "exceptional_openings", []),
        "can_preview_quiz": can_preview_quiz,
        "application_timezone": timezone_name(),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Quizzes"), reverse("quiz-dashboard")),
            (quiz.name or _("(untitled)"), None),
        ]),
    })


@require_POST
@capability_required(can_manage_academic_setup)
def regrade_quiz_question(request, quiz_id, question_id):
    with transaction.atomic():
        get_object_or_404(Quiz.objects.select_for_update().only("pk"), pk=quiz_id)
        question = get_object_or_404(Question, pk=question_id, quiz_id=quiz_id, auto_grade=True)
        evaluated = regrade_quiz_submissions(quiz_id, [question.pk])
    messages.success(request, _("%(count)s saved answers regraded.") % {"count": evaluated})
    return redirect(reverse("quiz-view", args=[quiz_id]))


@require_http_methods(["GET", "POST"])
@ajax_login_required
@capability_required(can_manage_academic_setup)
def recalculate_quizzes(request):
    if request.method == "GET":
        # Discovery resumes a job that is still running for this operator. A
        # finished job must not be re-advertised: doing so replayed its final
        # status message on every page load, even when the operator had not
        # started anything in that browser session.
        jobs = QuizOperationJob.objects.filter(actor=request.user)
        job = jobs.filter(
            status__in=[QuizOperationJob.Status.QUEUED, QuizOperationJob.Status.RUNNING]
        ).order_by("created_at").first()
        response = JsonResponse({"status_url": reverse("quiz-recalculation-status", args=[job.pk]) if job else None})
        response["Cache-Control"] = "no-store"
        return response
    if len(request.body) > 65536:
        return JsonResponse({"error": _("The selection has invalid types.")}, status=400)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError
        selection = load_selection_token(str(payload.get("selection_token", "")), view="quiz", actor_id=request.user.pk)
    except signing.BadSignature:
        return JsonResponse({"error": _("The selection has expired. Reload the list and try again.")}, status=409)
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": _("Invalid JSON")}, status=400)
    try:
        select_all = parse_strict_bool(payload.get("select_all"))
        lists = []
        for name in ("ids", "excluded_ids"):
            raw = payload.get(name, [])
            if not isinstance(raw, list) or len(raw) > MAX_EXPLICIT_SELECTION:
                raise ValueError
            if any(isinstance(value, bool) or not isinstance(value, (str, int))
                   or not re.fullmatch(r"[0-9]{1,19}", str(value)) for value in raw):
                raise ValueError
            values = set(int(value) for value in raw)
            if any(value < 1 or value > 9223372036854775807 for value in values):
                raise ValueError
            lists.append(sorted(values))
        ids, excluded_ids = lists
        target = quiz_queryset(selection["scope"])
        if select_all:
            target = target.exclude(pk__in=excluded_ids)
        else:
            target = target.filter(pk__in=ids)
            if target.count() != len(ids):
                raise ValueError
        total = target.count()
        if not total:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"error": _("Select valid quizzes from the current list.")}, status=400)
    upper_pk = target.order_by("-pk").values_list("pk", flat=True).first()
    kind = payload.get("kind", QuizOperationJob.Kind.RECALCULATE)
    if kind not in QuizOperationJob.Kind.values:
        return JsonResponse({"error": _("Invalid quiz operation.")}, status=400)
    job = QuizOperationJob.objects.create(actor=request.user, kind=kind, total=total, upper_pk=upper_pk,
        selection={"scope": selection["scope"], "ids": ids, "excluded_ids": excluded_ids, "select_all": select_all},
        language=translation.get_language() or "en")
    try:
        recalculate_quizzes_task.delay(str(job.pk))
    except Exception:
        # The durable queued record is picked up by recovery when the broker returns.
        logger.exception("Quiz operation %s awaits worker recovery", job.pk)
    return JsonResponse({"status_url": reverse("quiz-recalculation-status", args=[job.pk])}, status=202)


@require_GET
@ajax_login_required
@capability_required(can_manage_academic_setup)
def quiz_recalculation_status(request, job_id):
    job = get_object_or_404(QuizOperationJob, pk=job_id, actor=request.user)
    if job.status == job.Status.QUEUED or (job.status == job.Status.RUNNING and job.lease_until and job.lease_until <= timezone.now()):
        try:
            recalculate_quizzes_task.delay(str(job.pk))
        except Exception:
            logger.warning("Quiz operation recovery awaits broker", exc_info=True)
    response = JsonResponse({key: getattr(job, key) for key in
        ("status", "kind", "total", "processed", "answers", "skipped", "error", "updated_at")})
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@capability_required(can_manage_academic_setup)
def quiz_exceptional_opening(request, quiz_id):
    invalid_form = False
    try:
        with transaction.atomic():
            quiz = get_object_or_404(
                # of=("self",): quiz_type is a nullable FK, and PostgreSQL
                # rejects FOR UPDATE across a nullable LEFT JOIN side.
                Quiz.objects.select_for_update(of=("self",)).select_related(
                    "course_offering__course",
                    "course_offering__academic_year_level__level",
                    "course_offering__academic_year_level__academic_year",
                    "quiz_type",
                ),
                pk=quiz_id,
            )
            form = QuizExceptionalOpeningForm(request.POST, quiz=quiz)
            if not form.is_valid():
                invalid_form = True
            else:
                count = grant_quiz_openings(
                    quiz,
                    form.cleaned_data["students"],
                    form.cleaned_data["opening_date"],
                    form.cleaned_data["closing_date"],
                    request.user,
                )
                schedule_quiz_opening_events(quiz, locked=True)
    except ValidationError as exc:
        messages.error(request, "; ".join(str(message) for message in exc.messages))
    else:
        if invalid_form:
            messages.error(request, _("Please correct the exceptional opening form."))
        else:
            messages.success(request, _("Exceptional opening saved for %(count)s student(s).") % {"count": count})
    return redirect("quiz-view", quiz_id=quiz.pk)


# Lesson Dashboard
@capability_required(can_manage_content)
def lesson_dashboard(request):   
    user = request.user
    view = "lesson"
    try:
        selected_year, academic_years = select_content_academic_year(request)
    except ValidationError as exc:
        return HttpResponse("; ".join(str(message) for message in exc.messages), status=400)

    scope = content_scope(request, year_id=selected_year.pk)
    lessons = lesson_queryset(scope).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
    )
    status_counts = content_status_counts(view, scope)
    subjects = _content_offering_filter_options(selected_year.pk)

    logger.info(
        "User %s filters lessons using %s name, academic year %s, %s course and %s status",
        user,
        scope["search"],
        selected_year.pk,
        scope["offering"],
        scope["status"],
    )

    context = {
        "name_value" : scope["search"],
        "filter_fields": _content_filter_fields(
            scope=scope,
            academic_years=academic_years,
            selected_year=selected_year,
            subjects=subjects,
        ),
        "filter_tabs": _content_status_tabs(
            request, view=view, scope=scope, counts=status_counts
        ),
        "columns" : Lesson.get_columns(),
        "table_columns": [
            {"label": _("Name"), "key": "name"},
            {"label": _("Course"), "key": "course"},
            {"label": _("Level"), "key": "level"},
            {"label": _("Academic Year"), "key": "academic_year"},
            {"label": _("Last Updated"), "key": "updated_date"},
        ],
        "sort_fields": {
            "name": "name",
            "course": "course_offering__course__name",
            "level": "course_offering__academic_year_level__level__ordering",
            "academic_year": "course_offering__academic_year_level__academic_year__ordering",
            "updated_date": "updated_date",
        },
        "default_sort": "updated_date",
        "default_ordering": [
            "course_offering__academic_year_level__level__ordering",
            "course_offering__course__name",
            "name",
            "pk",
        ],
        "group_by_level": True,
        "table_scope": scope,
        "page_eyebrow": _("Curriculum"),
        "page_title": _("Lessons"),
        "page_description": _("Author lessons, attach media and control publication."),
        "page_actions": [{
            "label": _("Create lesson"),
            "url": reverse("lesson-create"),
            "icon": "i-plus",
            "variant": "primary",
        }],
    }

    return render_dashboard(request, lessons, view, context)

def _render_lesson_editor(request: HttpRequest, lesson: Lesson | None, form: LessonEditorForm) -> HttpResponse:
    if form.is_bound:
        keys = request.POST.getlist("videos")
        names = request.POST.getlist("videos_name")
        types = request.POST.getlist("files_type")
        downloads = request.POST.getlist("download_ids")
        groups = request.POST.getlist("part_groups")
        videos = [{
            "id": key,
            "name": names[index] if index < len(names) else key,
            "file_type": types[index] if index < len(types) else "video",
            "download_key": downloads[index] if index < len(downloads) else "",
            "part_number": groups[index] if index < len(groups) else "",
        } for index, key in enumerate(keys)]
    else:
        videos = json.loads(lesson.links or "[]") if lesson else []
        if not isinstance(videos, list):
            videos = []
        videos = [
            dict(video) for video in videos
            if isinstance(video, dict)
            and not is_private_storage_key(video.get("id"))
            and not is_private_storage_key(video.get("download_id"))
        ]
        part_numbers = {}
        for video in videos:
            part_id = video.get("part_id")
            if part_id:
                part_numbers.setdefault(part_id, len(part_numbers) + 1)
            video["part_number"] = part_numbers.get(part_id, 0)
            if video.get("file_type") == "audio":
                video["download_key"] = video.get("download_id") or downloadable_audio_key(video.get("id", "")) or ""
    form_key = f"lesson-{lesson.pk if lesson else 'new'}"
    draft_saved = request.session.get("saved_content_form") == form_key
    if draft_saved:
        request.session.pop("saved_content_form", None)
    return render_page(request, "lesson_form.html", "partials/lesson_editor_content.html", {
        "offering_groups": offering_option_groups(form.fields["course_offering"].queryset, include_year=True),
        "drive": filter_content_storage_entries(list_current_folder(CLOUD_CLIENT, bucket_name)[0]),
        "is_root": True,
        "is_edit": lesson is not None,
        "selected_offering_id": form["course_offering"].value(),
        "lesson_name": form["lesson_name"].value() or "",
        "lesson_description": form["description"].value() or "",
        "opening_date": form["opening_date"].value() or "",
        "application_timezone": timezone_name(),
        "videos": videos,
        "content_status": form["status"].value(),
        "publication_statuses": PublicationStatus.choices,
        "editor_form": form,
        "has_form_errors": form.is_bound,
        "draft_saved": draft_saved,
        "lesson_id": lesson.pk if lesson else None,
    })


def _edit_lesson(request: HttpRequest, lesson: Lesson | None = None) -> HttpResponse:
    initial = {
        "lesson_name": lesson.name if lesson else "",
        "description": lesson.description if lesson else "",
        "course_offering": lesson.course_offering_id if lesson else None,
        "opening_date": format_application_datetime(lesson.opening_date, "%Y-%m-%dT%H:%M") if lesson and lesson.opening_date else "",
        "status": lesson.status if lesson else PublicationStatus.DRAFT,
    }
    form = LessonEditorForm(request.POST if request.method == "POST" else None,
                            initial=initial, current_offering_id=lesson.course_offering_id if lesson else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                saved = Lesson.objects.select_for_update().get(pk=lesson.pk) if lesson else Lesson()
                links = _lesson_links_from_form(request, json.loads(saved.links or "[]") if saved.pk else [])
                storage_keys = lesson_storage_keys(links)
                if storage_keys:
                    lock_storage_namespace(*storage_keys)
                    assert_storage_namespace_available(
                        *storage_keys,
                        check_media_outputs=False,
                        check_media_cleanup=False,
                    )
                previous_status = saved.status
                previous_notification_facts = (
                    saved.name,
                    saved.course_offering_id,
                    saved.opening_date,
                )
                saved.name = form.cleaned_data["lesson_name"]
                saved.description = form.cleaned_data["description"] or None
                saved.course_offering = form.cleaned_data["course_offering"]
                saved.links = json.dumps(links)
                saved.opening_date = form.cleaned_data["opening_date"]
                saved.status = form.cleaned_data["status"]
                current_notification_facts = (
                    saved.name,
                    saved.course_offering_id,
                    saved.opening_date,
                )
                reschedule = (
                    bool(saved.pk)
                    and previous_status == PublicationStatus.PUBLISHED
                    and saved.status == PublicationStatus.PUBLISHED
                    and previous_notification_facts != current_notification_facts
                )
                publish = saved.status == PublicationStatus.PUBLISHED and (
                    not saved.pk or previous_status != PublicationStatus.PUBLISHED or reschedule
                )
                if publish:
                    saved.publication_event_version += 1
                saved.save()
                if publish:
                    if reschedule:
                        cancel_future_lesson_publication_events(saved)
                    create_lesson_publication_event(saved)
            request.session["saved_content_form"] = f"lesson-{lesson.pk if lesson else 'new'}"
            success(request, _("Lesson is updated successfully") if lesson else _("Lesson is created successfully"), extra_tags="alert-success")
            return redirect(reverse("lesson-update", args=[lesson.pk]) if lesson else reverse("lesson-create"))
        except ValidationError as exc:
            form.add_error(None, exc)
    return _render_lesson_editor(request, lesson, form)


@capability_required(can_manage_content)
def create_lesson(request):
    return _edit_lesson(request)

@capability_required(can_manage_content)
def navigate_folder(request, folder_id=None):
    # Check if folders_only mode is requested (for upload_video page)
    folders_only = request.GET.get('folders_only', 'false').lower() == 'true'
    
    # Redirect raw (non-HTMX) requests to the parent page rather than rendering a fragment
    if not request.headers.get("HX-Request"):
        return redirect("r2-management-dashboard")

    root = True
    parent_folder = None
    if folder_id and folder_id != "None":
        if is_private_storage_key(folder_id):
            raise PermissionDenied
        root = False
        drive, parent_folder = list_current_folder(CLOUD_CLIENT, bucket_name, folder_id, folders_only=folders_only)
    else:
        drive, _parent_folder = list_current_folder(CLOUD_CLIENT, bucket_name, folders_only=folders_only)
    drive = filter_content_storage_entries(drive)

    return render(request, "drive_files.html", {
        "drive" : drive,
        "parent_folder" : parent_folder,
        "is_root" : root,
        "folders_only" : folders_only  # Pass it to template for subsequent navigations
    })

@capability_required(can_manage_content)
def update_lesson(request, lesson_id):
    return _edit_lesson(request, get_object_or_404(Lesson, pk=lesson_id))

class DeleteLesson(LessonBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            raise PermissionDenied
        return super(LessonBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("lesson-dashboard")

def _media_job_lesson_target(lesson_id, part_id):
    if lesson_id in (None, ""):
        if part_id not in (None, ""):
            raise ValidationError(_("A media part requires a lesson target."))
        return None, ""
    try:
        lesson_pk = int(lesson_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(_("The selected lesson is invalid.")) from exc
    lesson = get_object_or_404(Lesson.objects.select_related("course_offering"), pk=lesson_pk)
    return lesson, validate_browser_part_id(part_id) or f"p{uuid.uuid4().hex[:32]}"


def _media_api_required(view_func):
    """Use API authentication semantics without weakening browser POST CSRF."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"message": _("Authentication required.")}, status=401)
        if not can_manage_content(request.user):
            return JsonResponse({"message": _("Unauthorized")}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def _media_job_visible_or_403(request, job):
    if not job_is_visible_to(request.user, job):
        return JsonResponse({"message": _("You are not allowed to access this media job.")}, status=403)
    return None


def _media_cleanup_error(job):
    if job.cleanup_status != MediaCleanupStatus.FAILED:
        return ""
    if (job.cleanup_result or {}).get("objects_check_failed"):
        return str(_("R2 cleanup could not verify object existence; no R2 objects were deleted. Retry cleanup."))
    return str(_("Upload cleanup failed. You can retry cleanup."))


def _media_job_ui_state(job, *, phase=None, now=None):
    now = now or timezone.now()
    phase = phase or job.phase
    lesson_status = job.lesson.status if job.lesson_id else None
    terminal_statuses = {
        MediaProcessingStatus.CANCELLED,
        MediaProcessingStatus.FAILED,
        MediaProcessingStatus.SUCCEEDED,
    }
    cleanup_in_flight = job.cleanup_status in {MediaCleanupStatus.QUEUED, MediaCleanupStatus.RUNNING}
    cleanup_waiting = bool(
        job.cleanup_status == MediaCleanupStatus.QUEUED
        and job.cleanup_not_before
        and job.cleanup_not_before > now
    )
    stop_pending = bool(job.stop_requested_at and not job.cancel_acknowledged_at)
    publication_pending = bool(
        is_automation_job(job) and job.error_code == PUBLICATION_PENDING_ERROR
    )
    # Offer retry for any failed job that still has its retained source (not
    # only the non-retryable error codes): the operator may have fixed the
    # cause (e.g. an output collision or a mis-named file) and wants to rerun
    # without cleanup. Structurally impossible retries stay hidden.
    can_retry = bool(
        job.status == MediaProcessingStatus.FAILED
        and job.source_acknowledged_at
        and job.cleanup_status == MediaCleanupStatus.NOT_REQUESTED
        and job.attempt_count - job.interrupted_count < MAX_MEDIA_ATTEMPTS
        and not job.staging_deleted_at
    )
    can_retry_attachment = bool(
        job.status == MediaProcessingStatus.SUCCEEDED
        and job.attachment_status == MediaAttachmentStatus.FAILED
        and job.cleanup_status == MediaCleanupStatus.NOT_REQUESTED
    )
    can_publish = bool(
        is_automation_job(job)
        and job.status == MediaProcessingStatus.SUCCEEDED
        and job.error_code == PUBLICATION_FAILED_ERROR
        and job.attachment_status == MediaAttachmentStatus.ATTACHED
        and job.staging_deleted_at
        and lesson_status in {PublicationStatus.DRAFT, PublicationStatus.PUBLISHED}
        and job.cleanup_status == MediaCleanupStatus.NOT_REQUESTED
    )
    can_stop = bool(
        (
            job.status in ACTIVE_WORKER_STATUSES
            or job.status in {MediaProcessingStatus.AWAITING_UPLOAD, MediaProcessingStatus.QUEUED}
        )
        and not cleanup_in_flight
        and not job.stop_requested_at
    )
    can_cleanup = bool(
        job.status in terminal_statuses
        and not (
            job.status == MediaProcessingStatus.SUCCEEDED
            and job.attachment_status == MediaAttachmentStatus.PENDING
        )
        and not publication_pending
        and not (
            job.status == MediaProcessingStatus.CANCELLED
            and job.stop_requested_at is not None
            and job.cancel_acknowledged_at is None
        )
        and job.cleanup_status not in {
            MediaCleanupStatus.QUEUED,
            MediaCleanupStatus.RUNNING,
            MediaCleanupStatus.COMPLETE,
        }
    )

    if cleanup_in_flight or job.cleanup_status in {MediaCleanupStatus.FAILED, MediaCleanupStatus.COMPLETE}:
        phase_key = (
            "cleanup_scheduled" if cleanup_waiting else
            "cleanup_queued" if job.cleanup_status == MediaCleanupStatus.QUEUED else
            "cleanup_running" if job.cleanup_status == MediaCleanupStatus.RUNNING else
            "cleanup_failed" if job.cleanup_status == MediaCleanupStatus.FAILED else
            "cleanup_complete"
        )
        current_phase_label = {
            "cleanup_scheduled": _("Cleanup scheduled"),
            "cleanup_queued": _("Cleanup queued"),
            "cleanup_running": _("Cleaning up"),
            "cleanup_failed": _("Cleanup failed"),
            "cleanup_complete": _("Cleanup complete"),
        }[phase_key]
    elif stop_pending:
        current_phase_label = _("Stopping")
    elif publication_pending:
        current_phase_label = _("Publishing lesson")
    elif can_publish:
        current_phase_label = _("Lesson publication failed")
    elif job.attachment_status == MediaAttachmentStatus.FAILED:
        current_phase_label = _("Lesson attachment failed")
    elif job.status == MediaProcessingStatus.FAILED:
        current_phase_label = _("Failed")
    elif job.status == MediaProcessingStatus.CANCELLED:
        current_phase_label = _("Stopped")
    elif job.status == MediaProcessingStatus.SUCCEEDED:
        current_phase_label = _("Complete")
    else:
        current_phase_label = dict(MediaProcessingPhase.choices).get(phase, phase)

    if job.cleanup_status == MediaCleanupStatus.FAILED:
        next_step_label = _("Retry cleanup")
    elif cleanup_in_flight:
        next_step_label = _("Cleanup scheduled") if cleanup_waiting else _("Cleaning up")
    elif job.cleanup_status == MediaCleanupStatus.COMPLETE:
        next_step_label = _("Cleanup complete")
    elif stop_pending:
        next_step_label = _("Stopping")
    elif publication_pending:
        next_step_label = _("Publishing lesson")
    elif can_publish:
        next_step_label = _("Continue publishing")
    elif can_retry_attachment:
        next_step_label = _("Retry lesson attachment")
    elif can_retry:
        next_step_label = _("Retry processing")
    elif can_cleanup:
        next_step_label = _("Clean up")
    elif job.status == MediaProcessingStatus.AWAITING_UPLOAD:
        next_step_label = _("Waiting for source upload")
    elif job.status in ACTIVE_WORKER_STATUSES or job.status == MediaProcessingStatus.QUEUED:
        next_step_label = _("In progress")
    else:
        next_step_label = _("No action needed")

    if job.cleanup_status == MediaCleanupStatus.COMPLETE:
        completed_steps = [str(_("Upload cleaned up"))]
    else:
        completed_steps = []
        if job.source_acknowledged_at:
            completed_steps.append(str(_("Source uploaded")))
        if job.output_keys or job.manifest_key or job.audio_manifest_key or job.download_key:
            completed_steps.append(str(_("Saved to R2")))
        if job.attachment_status == MediaAttachmentStatus.ATTACHED:
            completed_steps.append(str(_("Attached to lesson")))
        if lesson_status == PublicationStatus.PUBLISHED and not publication_pending and job.error_code != PUBLICATION_FAILED_ERROR:
            completed_steps.append(str(_("Lesson published")))

    percent = max(0, min(100, int(job.progress or 0)))
    if job.status in ACTIVE_WORKER_STATUSES or job.status == MediaProcessingStatus.QUEUED:
        progress_label = _("%(phase)s - %(percent)s%%") % {
            "phase": current_phase_label,
            "percent": percent,
        }
    else:
        progress_label = current_phase_label

    return {
        "can_stop": can_stop,
        "can_cleanup": can_cleanup,
        "can_retry": can_retry,
        "can_retry_attachment": can_retry_attachment,
        "can_publish": can_publish,
        "stop_pending": stop_pending,
        "publication_pending": publication_pending,
        "cleanup_waiting_for_upload_url": cleanup_waiting,
        "status_label": str(job.get_status_display()),
        "percent": percent,
        "progress_label": str(progress_label),
        "current_phase_label": str(current_phase_label),
        "next_step_label": str(next_step_label),
        "completed_steps": completed_steps,
        "lesson_status": lesson_status,
        "lesson_deleted": bool((job.cleanup_result or {}).get("lesson_deleted")),
        "cleanup_preserved_count": len(job.cleanup_preserved_keys or []),
    }


def _live_media_progress(job, live):
    """Return live worker progress only when it matches the current attempt.

    The Redis hash is a worker cache: it can outlive a crashed worker or still
    describe a previous attempt after a retry. Every terminal state and every
    other attempt is reported from the database row, so the UI never shows a
    phase or percentage that contradicts the persisted job.
    """
    if not live or job.status not in ACTIVE_WORKER_STATUSES:
        return {}
    if str(live.get("attempt") or "") != str(job.attempt_count):
        return {}
    return live


def _serialize_media_job(job, live=None):
    live = _live_media_progress(job, live)
    ui_state = _media_job_ui_state(job, phase=live.get("phase", job.phase))
    return {
        "id": str(job.public_id),
        "filename": job.original_filename,
        "requested_folder": job.requested_folder,
        "source_kind": job.source_kind,
        "status": job.status,
        "phase": live.get("phase", job.phase),
        "progress": int(live.get("progress", job.progress)),
        "attempt_count": job.attempt_count,
        "interrupted_count": job.interrupted_count,
        "worker_id": job.worker_id,
        "lesson_id": job.lesson_id,
        "part_id": job.part_id,
        "attachment_status": job.attachment_status,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "output_keys": job.output_keys or [],
        "manifest_key": job.manifest_key,
        "audio_manifest_key": job.audio_manifest_key,
        "download_key": job.download_key,
        "source_acknowledged": bool(job.source_acknowledged_at),
        "staging_deleted": bool(job.staging_deleted_at),
        "stop_requested": bool(job.stop_requested_at),
        "cancel_acknowledged": bool(job.cancel_acknowledged_at),
        "publication_pending": ui_state["publication_pending"],
        "cleanup_status": job.cleanup_status,
        "cleanup_error_message": _media_cleanup_error(job),
        "cleanup_preserved_count": len(job.cleanup_preserved_keys or []),
        "cleanup_not_before": job.cleanup_not_before.isoformat() if job.cleanup_not_before else None,
        "cleanup_waiting_for_upload_url": bool(
            job.cleanup_status == MediaCleanupStatus.QUEUED
            and job.cleanup_not_before
            and job.cleanup_not_before > timezone.now()
        ),
        "cleanup_result": job.cleanup_result or {},
        "cleanup_finished_at": job.cleanup_finished_at.isoformat() if job.cleanup_finished_at else None,
        **ui_state,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@require_POST
@_media_api_required
def media_job_create(request):
    """Create independent jobs and return one direct R2 upload per source."""
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            raise ValidationError(_("Media job files are required."))
        files = payload["files"]
        if not files or len(files) > 100:
            raise ValidationError(_("Select between one and 100 files."))
        folder = validate_requested_folder(payload.get("requested_folder", ""))
        default_lesson_id = payload.get("lesson_id")
        default_part_id = payload.get("part_id")
        default_lesson, default_part_id = _media_job_lesson_target(default_lesson_id, default_part_id)
        jobs = []
        with reserve_media_output_folder(folder):
            for item in files:
                if not isinstance(item, dict):
                    raise ValidationError(_("Each media file descriptor is invalid."))
                filename, source_kind, source_size = validate_source_descriptor(
                    item.get("filename", item.get("name")), item.get("size")
                )
                lesson_id = item.get("lesson_id", default_lesson_id)
                part_id_value = item.get("part_id", default_part_id)
                if lesson_id == default_lesson_id and part_id_value == default_part_id:
                    lesson, part_id = default_lesson, default_part_id
                else:
                    lesson, part_id = _media_job_lesson_target(lesson_id, part_id_value)
                public_id = uuid.uuid4()
                source_key = build_staging_key(str(public_id), filename)
                ack_deadline, staging_expires = initialize_deadlines()
                job = MediaProcessingJob.objects.create(
                    public_id=public_id,
                    created_by=request.user,
                    requested_folder=folder,
                    original_filename=filename,
                    output_base_name=safe_output_base_name(filename),
                    source_kind=source_kind,
                    source_key=source_key,
                    source_size=source_size,
                    lesson=lesson,
                    part_id=part_id,
                    attachment_status=(MediaAttachmentStatus.PENDING if lesson else MediaAttachmentStatus.NOT_REQUESTED),
                    upload_ack_deadline_at=ack_deadline,
                    staging_expires_at=staging_expires,
                    source_upload_url_expires_at=(
                        timezone.now() + timedelta(seconds=STAGING_UPLOAD_URL_TTL_SECONDS)
                    ),
                )
                authorization = create_staging_upload_url(
                    source_key,
                    content_type=content_type_for_key(filename),
                    expires_in=STAGING_UPLOAD_URL_TTL_SECONDS,
                )
                jobs.append({
                    "id": str(job.public_id),
                    "filename": job.original_filename,
                    "source_kind": job.source_kind,
                    "source_size": job.source_size,
                    "source_key": job.source_key,
                    "output_base_name": job.output_base_name,
                    "requested_folder": job.requested_folder,
                    "lesson_id": job.lesson_id,
                    "part_id": job.part_id,
                    "upload": authorization,
                    "ack_deadline": job.upload_ack_deadline_at.isoformat(),
                })
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError, ValidationError, MediaStorageError) as exc:
        message = "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))
        return JsonResponse({"message": message}, status=400)
    return JsonResponse({"jobs": jobs}, status=201)


@require_POST
@_media_api_required
def media_job_source_complete(request, job_uuid):
    """Verify one staged R2 object and enqueue its independent media task."""
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    if job.status in {
        MediaProcessingStatus.QUEUED,
        MediaProcessingStatus.PROCESSING,
        MediaProcessingStatus.UPLOADING,
        MediaProcessingStatus.VERIFYING,
        MediaProcessingStatus.SUCCEEDED,
    }:
        return JsonResponse({"job": _serialize_media_job(job), "duplicate": True})
    if job.status != MediaProcessingStatus.AWAITING_UPLOAD:
        return JsonResponse({"message": _("This media job is no longer awaiting its source upload.")}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict) or payload.get("source_key") != job.source_key:
            raise ValidationError(_("The staging object key does not match the media job."))
        client_size = payload.get("size")
        if client_size is not None and client_size != job.source_size:
            raise ValidationError(_("The uploaded source size does not match the media job."))
        queued, _was_queued = queue_verified_job(
            job.public_id,
            expected_etag=payload.get("etag") or None,
            acknowledged_at=timezone.now(),
        )
        schedule_media_job_after_commit(queued.public_id)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError, MediaStorageError) as exc:
        message = "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))
        return JsonResponse({"message": message}, status=400)
    return JsonResponse({"job": _serialize_media_job(queued)}, status=202)


@_media_api_required
def media_job_status(request, job_uuid):
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    live = {}
    try:
        live = redis.Redis.from_url(
            getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        ).hgetall(f"media:job:{job.public_id}")
    except Exception:
        live = {}
    return JsonResponse({"job": _serialize_media_job(job, live)})


@require_POST
@_media_api_required
def media_job_stop(request, job_uuid):
    """Request a worker-acknowledged stop for this file/upload group."""
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    try:
        jobs, stopping = request_media_job_stop(job.public_id)
    except ValidationError as exc:
        message = "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))
        return JsonResponse({"message": message}, status=409)
    return JsonResponse({
        "status": "stopping" if stopping else "cancelled",
        "jobs": {str(item.public_id): _serialize_media_job(item) for item in jobs},
    }, status=202)


@require_POST
@_media_api_required
def media_job_cleanup(request, job_uuid):
    """Queue full exact-key cleanup after processing has stopped/finished."""
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    try:
        jobs, queued = request_media_job_cleanup(job.public_id)
    except ValidationError as exc:
        message = "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))
        return JsonResponse({"message": message}, status=409)
    return JsonResponse({
        "status": "cleanup_queued" if queued else jobs[0].cleanup_status,
        "cleanup_not_before": jobs[0].cleanup_not_before.isoformat() if jobs[0].cleanup_not_before else None,
        "jobs": {str(item.public_id): _serialize_media_job(item) for item in jobs},
    }, status=202)


@_media_api_required
def media_job_status_batch(request):
    """Return a bounded batch of visible media-job statuses for polling."""
    raw_ids = [value.strip() for value in request.GET.get("ids", "").split(",") if value.strip()]
    if not raw_ids or len(raw_ids) > 100:
        return JsonResponse({"message": _("Provide between one and 100 media job IDs.")}, status=400)
    try:
        job_ids = [uuid.UUID(value) for value in raw_ids]
    except (ValueError, AttributeError):
        return JsonResponse({"message": _("One or more media job IDs are invalid.")}, status=400)
    jobs = MediaProcessingJob.objects.select_related("lesson", "created_by").filter(public_id__in=job_ids)
    payload = {}
    visible_jobs = [job for job in jobs if job_is_visible_to(request.user, job)]
    live_values = []
    if visible_jobs:
        try:
            redis_client = redis.Redis.from_url(
                getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0"),
                decode_responses=True,
            )
            pipeline = redis_client.pipeline(transaction=False)
            for job in visible_jobs:
                pipeline.hgetall(f"media:job:{job.public_id}")
            live_values = pipeline.execute()
        except Exception:
            live_values = [{} for _job in visible_jobs]
    for job, live in zip(visible_jobs, live_values):
        payload[str(job.public_id)] = _serialize_media_job(job, live)
    return JsonResponse({"jobs": payload})


@_media_api_required
def media_job_list(request):
    try:
        page_number = max(1, int(request.GET.get("page", "1")))
        page_size = min(100, max(1, int(request.GET.get("page_size", "25"))))
    except (TypeError, ValueError):
        return JsonResponse({"message": _("Invalid pagination.")}, status=400)
    queryset = MediaProcessingJob.objects.select_related("lesson", "created_by")
    if getattr(getattr(request.user, "role", None), "role", None) != "admin":
        queryset = queryset.filter(created_by=request.user)
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(page_number)
    return JsonResponse({
        "results": [_serialize_media_job(job) for job in page.object_list],
        "page": page.number,
        "page_size": page_size,
        "pages": paginator.num_pages,
        "total": paginator.count,
    })


@_media_api_required
def media_jobs_collection(request):
    """Expose the proposal's collection URL for POST create and GET list."""
    if request.method == "POST":
        return media_job_create(request)
    if request.method == "GET":
        return media_job_list(request)
    return JsonResponse({"message": _("Method not allowed.")}, status=405)


@require_POST
@_media_api_required
def media_job_retry(request, job_uuid):
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    if job.cleanup_status != MediaCleanupStatus.NOT_REQUESTED:
        return JsonResponse({"message": str(_("This media job is already being cleaned up."))}, status=409)
    if job.status != MediaProcessingStatus.FAILED or not job.source_acknowledged_at:
        return JsonResponse({"message": _("Only failed jobs with a retained source can be retried.")}, status=409)
    try:
        queued, _was_queued = queue_verified_job(
            job.public_id,
            acknowledged_at=job.source_acknowledged_at,
        )
        schedule_media_job_after_commit(queued.public_id)
    except (ValidationError, MediaStorageError) as exc:
        return JsonResponse({"message": "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))}, status=400)
    return JsonResponse({"job": _serialize_media_job(queued)}, status=202)


@require_POST
@_media_api_required
def media_job_attachment_retry(request, job_uuid):
    """Retry only a failed lesson attachment; the verified source is not needed."""
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    if job.cleanup_status != MediaCleanupStatus.NOT_REQUESTED:
        return JsonResponse({"message": str(_("This media job is already being cleaned up."))}, status=409)
    try:
        pending = claim_attachment_retry(job.public_id)
        schedule_attachment_retry_after_commit(pending.public_id)
    except ValidationError as exc:
        return JsonResponse({"message": "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))}, status=409)
    return JsonResponse({"job": _serialize_media_job(pending)}, status=202)


@require_POST
@_media_api_required
def media_job_publish_retry(request, job_uuid):
    """Continue a failed automation lesson publication in the media worker."""
    job = get_object_or_404(MediaProcessingJob, public_id=job_uuid)
    denied = _media_job_visible_or_403(request, job)
    if denied:
        return denied
    try:
        jobs = request_automation_publication_retry(job.public_id)
    except ValidationError as exc:
        return JsonResponse({
            "message": "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))
        }, status=409)
    except Exception:
        logger.exception(
            "Could not queue automation publication retry",
            extra={"event": "automation_publication_retry_dispatch_failed", "public_id": str(job.public_id)},
        )
        return JsonResponse({"message": _("Could not start publication. Please try again.")}, status=503)
    return JsonResponse({"job": _serialize_media_job(jobs[0]), "status": "queued"}, status=202)

@capability_required(can_manage_content)
def upload_file(request):
    course_offerings = CourseOffering.objects.filter(
        academic_year_level__academic_year__is_active=True,
    ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year")
    editable_lessons = Lesson.objects.filter(
        status=PublicationStatus.DRAFT,
    ).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
    ).order_by(
        "course_offering__academic_year_level__academic_year__starts_on",
        "course_offering__course__name",
        "name",
    )
    return render_page(request, "upload_video.html", "partials/upload_video_content.html", {
        "drive" : list_current_folder(CLOUD_CLIENT, bucket_name, folders_only=True)[0],
        "is_root" : True,
        "course_offerings": course_offerings,
        "editable_lessons": editable_lessons,
        "media_job_collection_url": reverse("media-job-create"),
        "media_job_source_complete_url_template": reverse(
            "media-job-source-complete",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
        "media_job_status_url_template": reverse(
            "media-job-status",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
        "media_job_retry_url_template": reverse(
            "media-job-retry",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
        "media_job_attachment_retry_url_template": reverse(
            "media-job-attachment-retry",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
    })


@capability_required(can_manage_content)
def media_processing_status(request):
    """Render the SQL-paginated operational media-job status page."""
    status_filter = request.GET.get("status", "").strip()
    search = normalize_search_text(request.GET.get("q", "").strip()[:100])
    valid_statuses = {value for value, _label in MediaProcessingStatus.choices}
    if status_filter not in valid_statuses:
        status_filter = ""
    queryset = MediaProcessingJob.objects.select_related("lesson", "created_by").order_by("-created_at", "-pk")
    if getattr(getattr(request.user, "role", None), "role", None) != "admin":
        queryset = queryset.filter(created_by=request.user)

    # Drawer fragment: one job peek for the shared right-hand drawer. The
    # trigger keeps a full-page fallback, and this endpoint applies the same
    # actor scoping as the list.
    if request.GET.get("fragment") == "job":
        from uuid import UUID

        # A direct browser hit renders the owning page instead of a bare
        # fragment; only the drawer's fetch carries the HTMX marker.
        if request.headers.get("HX-Request") != "true":
            return redirect("media-processing-status")
        try:
            job_uuid = UUID(request.GET.get("job", "").strip())
        except ValueError:
            return HttpResponse(status=404)
        job = MediaProcessingJob.objects.select_related("lesson", "created_by").filter(
            public_id=job_uuid
        ).first()
        if job is None or (
            getattr(getattr(request.user, "role", None), "role", None) != "admin"
            and job.created_by_id != request.user.pk
        ):
            return HttpResponse(status=404)
        for key, value in _media_job_ui_state(job, now=timezone.now()).items():
            setattr(job, key, value)
        job.cleanup_error_message = _media_cleanup_error(job)
        return render(
            request,
            "partials/drawer/media_job_detail.html",
            {"job": job, "max_attempts": MAX_MEDIA_ATTEMPTS},
        )

    if search:
        queryset = queryset.filter(
            normalized_contains_q(
                ("original_filename", "lesson__name", "created_by__username"),
                search,
            )
        )
    # KPI strip scope: the actor's jobs under the current search, before the
    # status filter narrows it. One aggregate statement for the whole strip.
    status_counts = {
        row["status"]: row["count"]
        for row in queryset.order_by().values("status").annotate(count=Count("pk"))
    }
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    paginator = Paginator(queryset, 25)
    jobs_page = paginator.get_page(request.GET.get("page", "1"))
    now = timezone.now()
    for job in jobs_page.object_list:
        for key, value in _media_job_ui_state(job, now=now).items():
            setattr(job, key, value)
        job.cleanup_error_message = _media_cleanup_error(job)
    media_kpis = [
        {
            "key": "queued",
            "label": _("Queued"),
            "value": status_counts.get("queued", 0),
            "icon": "i-clock",
            "tone": "amber",
        },
        {
            "key": "processing",
            "label": _("Processing"),
            "value": sum(
                status_counts.get(key, 0)
                for key in ("processing", "uploading", "verifying")
            ),
            "icon": "i-refresh",
            "tone": "blue",
        },
        {
            "key": "completed",
            "label": _("Completed"),
            "value": status_counts.get("succeeded", 0),
            "icon": "i-check",
            "tone": "green",
        },
        {
            "key": "failed",
            "label": _("Failed"),
            "value": status_counts.get("failed", 0),
            "icon": "i-alert",
            "tone": "red",
        },
    ]
    return render_page(request, "media_processing_status.html", "partials/media_processing_status_content.html", {
        "jobs_page": jobs_page,
        "status_counts": status_counts,
        "total_count": sum(status_counts.values()),
        "media_kpis": media_kpis,
        "pagination_query": pagination_query_string(request),
        "status_choices": MediaProcessingStatus.choices,
        "status_labels_json": json.dumps(
            {value: str(label) for value, label in MediaProcessingStatus.choices},
            ensure_ascii=False,
        ),
        "status_filter": status_filter,
        "search": search,
        "media_job_retry_url_template": reverse(
            "media-job-retry",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
        "media_job_attachment_retry_url_template": reverse(
            "media-job-attachment-retry",
            args=["00000000-0000-0000-0000-000000000000"],
        ),
    })

# Quiz Dashboard
@capability_required(can_view_reports)
def promotion_formula(request):
    scope_id = request.GET.get("scope") or request.POST.get("academic_year_level")
    offering_id = request.GET.get("course_offering") or request.POST.get("course_offering")
    selected_scope = None
    if scope_id and str(scope_id).isdigit():
        scopes = AcademicYearLevel.objects.select_related("academic_year", "level")
        visible_scopes = Q(academic_year__is_active=True)
        # Promotion can consume a scope after its academic year is archived.
        # Keep the submitting administrator's exact job redirect reviewable.
        if request.method == "GET" and request.GET.get("promotion_job"):

            try:
                job_id = uuid.UUID(request.GET["promotion_job"])
            except (ValueError, TypeError):
                job_id = None
            if job_id and AdminOperationJob.objects.filter(
                pk=job_id, actor=request.user,
                kind=AdminOperationJob.Kind.BULK_PROMOTION,
                academic_year_level_id=scope_id,
            ).exists():
                visible_scopes |= Q(pk=scope_id)
        selected_scope = get_object_or_404(
            scopes.filter(visible_scopes),
            pk=scope_id,
        )

    selected_offering = None
    if selected_scope and offering_id and str(offering_id).isdigit():
        selected_offering = get_object_or_404(
            CourseOffering.objects.select_related("course", "academic_year_level"),
            pk=int(offering_id),
            academic_year_level=selected_scope,
            status=PublicationStatus.PUBLISHED,
        )
    formula = None
    if selected_scope:
        formula = PromotionFormula.objects.filter(
            academic_year_level=selected_scope,
            course_offering=selected_offering,
        ).prefetch_related("rules").first()
    preview_formula = formula
    if request.method == "POST":
        if not request.user.role or request.user.role.role != "admin":
            raise PermissionDenied(_("Only administrators can save promotion formulas."))
        formula_form = PromotionFormulaForm(request.POST)
        rule_formset = PromotionRuleFormSet(request.POST, prefix="rules")
        if formula_form.is_valid() and rule_formset.is_valid():
            submitted_rules = [
                form.cleaned_data
                for form in rule_formset
                if form.cleaned_data and not form.cleaned_data.get("DELETE")
            ]
            try:
                rules = normalize_formula_rules(submitted_rules)
                validate_formula_rules(rules)
            except ValidationError as exc:
                formula_form.add_error(None, exc)
                rules = []
            if rules and request.POST.get("action") == "preview":
                preview_formula = PromotionFormula(
                    academic_year_level=formula_form.cleaned_data["academic_year_level"],
                    course_offering=formula_form.cleaned_data["course_offering"],
                    overall_pass_percent=formula_form.cleaned_data["overall_pass_percent"],
                    evaluation_starts_on=formula_form.cleaned_data["evaluation_starts_on"],
                    evaluation_ends_on=formula_form.cleaned_data["evaluation_ends_on"],
                    failed_courses_repeat_threshold=formula_form.cleaned_data["failed_courses_repeat_threshold"],
                )
                preview_formula._preview_rules = tuple(
                    PromotionRule(
                        pk=100000000 + index,
                        formula=preview_formula,
                        metric=rule["metric"],
                        quiz_type=rule.get("quiz_type"),
                        weight_percent=rule["weight_percent"],
                        minimum_percent=rule["minimum_percent"],
                        ordering=index,
                    )
                    for index, rule in enumerate(rules, start=1)
                )
                messages.info(request, _("Preview generated without saving the formula or results."))
            elif rules:
                try:
                    saved, calculation_job = save_formula_and_enqueue(
                        scope=formula_form.cleaned_data["academic_year_level"],
                        course_offering=formula_form.cleaned_data["course_offering"],
                        overall_pass_percent=formula_form.cleaned_data["overall_pass_percent"],
                        evaluation_starts_on=formula_form.cleaned_data["evaluation_starts_on"],
                        evaluation_ends_on=formula_form.cleaned_data["evaluation_ends_on"],
                        failed_courses_repeat_threshold=formula_form.cleaned_data["failed_courses_repeat_threshold"],
                        rules=rules,
                        actor=request.user,
                        language=translation.get_language() or "ar",
                    )

                    try:
                        enqueue_admin_operation(calculation_job.pk, queue="admin")
                    except Exception:
                        # The queued row is picked up by the recovery schedule.
                        logger.warning("formula calculation awaits worker recovery", exc_info=True)
                    messages.success(
                        request,
                        _("Promotion formula saved. Recalculation was queued; promotion stays blocked until it completes."),
                    )
                    query = f"scope={saved.academic_year_level_id}"
                    if saved.course_offering_id:
                        query += f"&course_offering={saved.course_offering_id}"
                    return redirect(f"{reverse('promotion-formula')}?{query}")
                except (ValidationError, PermissionDenied) as exc:
                    formula_form.add_error(None, str(exc))
    else:
        if formula:
            formula_form = PromotionFormulaForm(initial={
                "academic_year_level": formula.academic_year_level_id,
                "course_offering": formula.course_offering_id,
                "overall_pass_percent": formula.overall_pass_percent,
                "evaluation_starts_on": formula.evaluation_starts_on,
                "evaluation_ends_on": formula.evaluation_ends_on,
                "failed_courses_repeat_threshold": formula.failed_courses_repeat_threshold,
            })
            rule_formset = PromotionRuleFormSet(
                prefix="rules",
                initial=[{
                    "metric": rule.metric,
                    "quiz_type": rule.quiz_type_id,
                    "weight_percent": rule.weight_percent,
                    "minimum_percent": rule.minimum_percent,
                } for rule in formula.rules.all()],
            )
        else:
            initial_date = timezone.localdate()
            if selected_scope:
                initial_date = min(max(initial_date, selected_scope.academic_year.starts_on), selected_scope.academic_year.ends_on)
            formula_form = PromotionFormulaForm(initial={
                "academic_year_level": selected_scope.pk if selected_scope else None,
                "course_offering": selected_offering.pk if selected_offering else None,
                "overall_pass_percent": 50,
                "evaluation_starts_on": selected_scope.academic_year.starts_on if selected_scope else initial_date,
                "evaluation_ends_on": initial_date,
                "failed_courses_repeat_threshold": 3,
            })
            rule_formset = PromotionRuleFormSet(prefix="rules", initial=[{
                "metric": PromotionRule.Metric.QUIZ,
                "weight_percent": 100,
                "minimum_percent": 50,
            }])

    preview_rows = None
    preview_page_obj = None
    preview_grading_errors = ()
    results_page_obj = None
    result_search = normalize_search_text(request.GET.get("result_search", "").strip()[:100])
    result_computed_status = request.GET.get("computed_status", "")
    result_final_status = request.GET.get("final_status", "")
    result_override = request.GET.get("override", "")
    result_study_mode = request.GET.get("study_mode", "")
    result_grading_errors = request.GET.get("grading_errors", "")
    preview_search = normalize_search_text(request.GET.get("search", "").strip()[:100])
    preview_offering_id = request.GET.get("preview_offering") or offering_id
    preview_offering = None
    if preview_formula:
        if preview_formula.course_offering_id:
            preview_offering = preview_formula.course_offering
        elif preview_offering_id and str(preview_offering_id).isdigit():
            preview_offering = CourseOffering.objects.filter(
                pk=int(preview_offering_id),
                academic_year_level=preview_formula.academic_year_level,
                status=PublicationStatus.PUBLISHED,
            ).select_related("course").first()
    if preview_formula:
        plan = build_evaluation_plan(
            preview_formula,
            getattr(preview_formula, "_preview_rules", None),
            course_offering=preview_offering,
        ) if preview_offering else None
        enrollments = evaluation_enrollments(plan) if plan else Enrollment.objects.none()
        if preview_search:
            enrollments = enrollments.filter(
                normalized_contains_q(
                    ("student__username", "student__first_name", "student__last_name", "student__email"),
                    preview_search,
                )
            )
        enrollments = enrollments.order_by("student__last_name", "student__first_name", "student__username")
        preview_page_obj = Paginator(enrollments, 25).get_page(request.GET.get("page", 1))
        preview_rows = evaluate_enrollment_page(preview_page_obj, plan) if plan else []
        preview_grading_errors = plan.grading_errors if plan else ()
        if formula is not None:
            saved_results = EvaluationResult.objects.filter(
                formula=formula,
                course_offering__isnull=formula.course_offering_id is None,
            ).select_related(
                "enrollment__student", "enrollment__academic_year_level__level", "overridden_by", "promotion_history"
            ).order_by("enrollment__student__last_name", "enrollment__student__first_name", "enrollment__student__username")
        else:
            # Preview without a saved formula must render an empty results
            # section instead of dereferencing the missing formula.
            saved_results = EvaluationResult.objects.none()
        if result_search:
            saved_results = saved_results.filter(
                normalized_contains_q(
                    (
                        "enrollment__student__username",
                        "enrollment__student__first_name",
                        "enrollment__student__last_name",
                        "enrollment__student__email",
                    ),
                    result_search,
                )
            )
        if result_computed_status in EvaluationResult.Status.values:
            saved_results = saved_results.filter(computed_status=result_computed_status)
        if result_final_status in EvaluationResult.Status.values:
            saved_results = saved_results.filter(final_status=result_final_status)
        if result_override == "overridden":
            saved_results = saved_results.filter(overridden_by__isnull=False)
        elif result_override == "not_overridden":
            saved_results = saved_results.filter(overridden_by__isnull=True)
        if result_study_mode in ("online", "offline"):
            saved_results = saved_results.filter(enrollment__student__study_mode=result_study_mode)
        if result_grading_errors == "yes":
            saved_results = saved_results.filter(metric_snapshot__grading_errors__0__isnull=False)
        elif result_grading_errors == "no":
            saved_results = saved_results.filter(metric_snapshot__grading_errors__0__isnull=True)
        results_page_obj = (
            Paginator(saved_results, 25).get_page(request.GET.get("result_page", 1))
            if formula else None
        )

    calculation_job = None
    promotion_job = None
    export_job = None
    if selected_scope:

        calculation_job = (
            AdminOperationJob.objects.filter(
                kind=AdminOperationJob.Kind.FORMULA_RECALCULATION,
                academic_year_level=selected_scope,
            )
            .order_by("-created_at")
            .first()
        )
        promotion_jobs = AdminOperationJob.objects.filter(
            actor=request.user, kind=AdminOperationJob.Kind.BULK_PROMOTION,
            academic_year_level=selected_scope,
        )
        promotion_job_id = request.GET.get("promotion_job")
        if promotion_job_id:
            try:
                promotion_jobs = promotion_jobs.filter(pk=uuid.UUID(promotion_job_id))
            except (ValueError, TypeError):
                promotion_jobs = promotion_jobs.none()
        promotion_job = promotion_jobs.order_by("-created_at").first()
        export_job = (
            AdminOperationJob.objects.filter(
                actor=request.user,
                kind=AdminOperationJob.Kind.EVALUATION_EXPORT,
                academic_year_level=selected_scope,
                course_offering=selected_offering,
            )
            .order_by("-created_at")
            .first()
        )

    # Scope size for the context strip: distinct students with an active
    # enrollment in the selected scope, the same notion of "scope students"
    # the attendance matrix uses.
    scope_student_count = 0
    if selected_scope is not None:
        scope_student_count = (
            Enrollment.objects.filter(
                course_offering__academic_year_level=selected_scope,
                status=Enrollment.Status.ACTIVE,
            )
            .values("student_id")
            .distinct()
            .count()
        )

    return render_page(request, "promotion_evaluation.html", "partials/promotion_evaluation_content.html", {
        "formula_form": formula_form,
        "rule_formset": rule_formset,
        "selected_scope": selected_scope,
        "scope_student_count": scope_student_count,
        "selected_offering": selected_offering,
        "selected_offering_id": selected_offering.pk if selected_offering else None,
        "promotion_job": promotion_job,
        "promotion_status_url": (
            reverse("admin-operation-status", args=[promotion_job.pk]) if promotion_job else ""
        ),
        "calculation_job": calculation_job,
        "calculation_status_url": (
            reverse("admin-operation-status", args=[calculation_job.pk])
            if calculation_job else ""
        ),
        "export_job": export_job,
        "export_status_url": (
            reverse("admin-operation-status", args=[export_job.pk])
            if export_job else ""
        ),
        "export_download_url": (
            reverse("admin-operation-download", args=[export_job.pk])
            if export_job else ""
        ),
        "course_offerings": CourseOffering.objects.filter(
            academic_year_level=selected_scope,
            status=PublicationStatus.PUBLISHED,
        ).select_related("course").order_by("course__name", "pk") if selected_scope else CourseOffering.objects.none(),
        "scopes": AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by(
            "academic_year__ordering", "level__ordering"
        ),
        "quiz_types": QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
        "preview_rows": preview_rows,
        "preview_page_obj": preview_page_obj,
        "preview_search": preview_search,
        "preview_scope_id": selected_scope.pk if selected_scope else None,
        "preview_offering": preview_offering,
        "preview_offering_id": preview_offering.pk if preview_offering else None,
        "preview_grading_errors": preview_grading_errors,
        "results_page_obj": results_page_obj,
        "results_pagination_query": pagination_query_string(request, exclude=("result_page",)),
        "preview_pagination_query": pagination_query_string(request),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Promotion Formula"), None),
        ]),
        "result_search": result_search,
        "result_computed_status": result_computed_status,
        "result_final_status": result_final_status,
        "result_override": result_override,
        "result_study_mode": result_study_mode,
        "result_grading_errors": result_grading_errors,
        "is_formula_admin": bool(request.user.role and request.user.role.role == "admin"),
        "is_aggregate_formula": bool(formula and formula.course_offering_id is None),
    })


@require_POST
@capability_required(can_manage_academic_setup)
def evaluation_result_override(request, result_id):
    result = get_object_or_404(EvaluationResult.objects.select_related("formula"), pk=result_id)
    final_status = request.POST.get("final_status", "")
    note = request.POST.get("override_note", "")
    try:
        override_evaluation_result(
            result_id=result.id,
            final_status=final_status,
            note=note,
            actor=request.user,
        )
        messages.success(request, _("Evaluation result override saved."))
    except (ValidationError, ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect(f"{reverse('promotion-formula')}?scope={result.formula.academic_year_level_id}")


@require_POST
@capability_required(can_manage_academic_setup)
def promotion_result(request, result_id):
    result = get_object_or_404(
        EvaluationResult.objects.select_related("formula__academic_year_level"), pk=result_id
    )
    try:
        promote_evaluation_result(
            result_id=result.pk,
            actor=request.user,
        )
        messages.success(request, _("Student promotion was recorded."))
    except (ValidationError, PermissionDenied, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect(f"{reverse('promotion-formula')}?scope={result.formula.academic_year_level_id}")


@require_POST
@capability_required(can_manage_academic_setup)
def bulk_promotion_results(request):
    raw_ids = request.POST.getlist("result_ids")
    if len(raw_ids) > 1000 or any(
        not value.isascii() or not value.isdigit() or len(value) > 10
        or not 0 < int(value) <= 2_147_483_647
        for value in raw_ids
    ):
        return HttpResponse(_("Invalid selection flags."), status=400)
    result_ids = sorted({int(value) for value in raw_ids})
    scope_id = request.POST.get("scope", "")
    if not result_ids:
        messages.error(request, _("Select at least one student result."))
        return redirect(f"{reverse('promotion-formula')}?scope={scope_id}")

    if (not scope_id.isascii() or not scope_id.isdigit() or len(scope_id) > 10
            or not 0 < int(scope_id) <= 2_147_483_647):
        return HttpResponse(_("Invalid selection flags."), status=400)
    promotion_scope = get_object_or_404(AcademicYearLevel, pk=int(scope_id))
    if EvaluationResult.objects.filter(
        pk__in=result_ids, formula__academic_year_level=promotion_scope,
        course_offering__isnull=True,
    ).count() != len(result_ids):
        return HttpResponse(_("Invalid selection flags."), status=400)
    try:
        job = create_admin_operation(
            actor=request.user,
            kind=AdminOperationJob.Kind.BULK_PROMOTION,
            input={"result_ids": result_ids},
            conflict_key=f"promotion:{scope_id}",
            scope=promotion_scope,
            language=translation.get_language() or "ar",
            total=len(result_ids),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(f"{reverse('promotion-formula')}?scope={scope_id}")
    try:
        enqueue_admin_operation(job.pk, queue="admin")
    except Exception:
        # The queued row is picked up by the recovery schedule.
        logger.warning("bulk promotion awaits worker recovery", exc_info=True)
    messages.success(
        request,
        _("Promotion was queued for %(count)d student(s). Progress is available while it runs.")
        % {"count": len(result_ids)},
    )
    return redirect(f"{reverse('promotion-formula')}?scope={scope_id}&promotion_job={job.pk}")


@capability_required(can_view_reports)
def promotion_history(request):
    history = PromotionHistory.objects.select_related(
        "student",
        "source_year_level__academic_year",
        "source_year_level__level",
        "destination_year_level__academic_year",
        "destination_year_level__level",
        "actor",
    ).order_by("-created_at", "-pk")
    search = normalize_search_text(request.GET.get("search", "").strip()[:100])
    outcome = request.GET.get("outcome", "")
    if search:
        history = history.filter(
            normalized_contains_q(
                ("student__username", "student__first_name", "student__last_name", "student__email"),
                search,
            )
        )
    if outcome in {"passed", "passed_with_exceptions", "repeated", "graduated", "last_level_exceptional"}:
        history = history.filter(outcome=outcome)
    page_obj = Paginator(history, 25).get_page(request.GET.get("page", 1))
    return render_page(request, "promotion_history.html", "partials/promotion_history_content.html", {
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Promotion History"), None),
        ]),
        "search": search,
        "outcome": outcome,
    })


@capability_required(can_manage_content)
def quiz_dashboard(request):   
    user = request.user
    view = "quiz"
    try:
        selected_year, academic_years = select_content_academic_year(request)
    except ValidationError as exc:
        return HttpResponse("; ".join(str(message) for message in exc.messages), status=400)

    scope = content_scope(request, year_id=selected_year.pk)
    quizzes = quiz_queryset(scope).annotate(
        submissions_count=Count("grade", distinct=True),
        question_count=Count("questions", distinct=True),
    ).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
        "quiz_type",
    )
    status_counts = content_status_counts(view, scope)
    subjects = _content_offering_filter_options(selected_year.pk)

    logger.info(
        "User %s filters quizzes using %s name, academic year %s, %s course and %s status",
        user,
        scope["search"],
        selected_year.pk,
        scope["offering"],
        scope["status"],
    )

    context = {
        "name_value" : scope["search"],
        "filter_fields": _content_filter_fields(
            scope=scope,
            academic_years=academic_years,
            selected_year=selected_year,
            subjects=subjects,
        ),
        "filter_tabs": _content_status_tabs(
            request, view=view, scope=scope, counts=status_counts
        ),
        "columns" : Quiz.get_columns(),
        "table_columns": [
            {"label": _("Name"), "key": "name"},
            {"label": _("Course"), "key": "course"},
            {"label": _("Level"), "key": "level"},
            {"label": _("Academic Year"), "key": "academic_year"},
            {"label": _("Quiz Type"), "key": "quiz_type"},
            {"label": _("Grades"), "key": "grades"},
            {"label": _("Opening Date"), "key": "opening_date"},
            {"label": _("Closing Date"), "key": "closing_date"},
            {"label": _("Duration (minutes)"), "key": "duration_minutes"},
            {"label": _("Questions"), "key": "questions"},
            {"label": _("Submissions"), "key": "submissions"},
        ],
        "sort_fields": {
            "name": "name",
            "course": "course_offering__course__name",
            "level": "course_offering__academic_year_level__level__ordering",
            "academic_year": "course_offering__academic_year_level__academic_year__ordering",
            "quiz_type": "quiz_type__name_en",
            "grades": "total_grade",
            "opening_date": "opening_date",
            "closing_date": "closing_date",
            "duration_minutes": "duration_minutes",
            "questions": "question_count",
            "submissions": "submissions_count",
        },
        "default_sort": "opening_date",
        "default_ordering": [
            "course_offering__academic_year_level__level__ordering",
            "course_offering__course__name",
            "opening_date",
            "name",
            "pk",
        ],
        "group_by_level": True,
        "submission_view" : True,
        "page_size" : 10,
        "table_scope": scope,
        "page_eyebrow": _("Curriculum"),
        "page_title": _("Quizzes"),
        "page_description": _(
            "Build quizzes, review attempts and keep grading consistent."
        ),
        "page_actions": [
            {
                "label": _("Create quiz"),
                "url": reverse("quiz-create"),
                "icon": "i-plus",
                "variant": "primary",
            },
            {
                "label": _("Import JSON"),
                "url": f"{reverse('quiz-create')}?import=1",
                "icon": "i-file",
                "variant": "ghost",
            },
        ],
    }

    return render_dashboard(request, quizzes, view, context)

@capability_required(can_manage_content)
@require_POST
def quiz_json_preview(request):
    """Validate an admin quiz JSON payload and render the import preview fragment.

    The payload fills the existing quiz form through the returned fragment; it
    never creates or changes database rows.
    """
    mode = request.POST.get("mode")
    if mode not in {"create", "update"}:
        mode = "create"
    result = parse_quiz_import(request.POST.get("payload", ""), mode=mode)
    return render(request, "partials/quiz_import_result.html", {
        "import_errors": result.errors,
        "import_warnings": result.warnings,
        "import_questions": result.questions,
        "import_summary": result.summary,
        "import_payload": {
            "quiz": result.quiz,
            "total_grade": result.total_grade,
            "questions_count": len(result.questions),
        },
        "question_types": Question.QUESTION_TYPES,
    })


def _rejected_quiz_context(request, key):
    raw = request.session.pop(key, None)
    if not raw:
        return {}
    post = QueryDict("", mutable=True)
    for name, values in raw.items():
        post.setlist(name, values)
    # Unpack without letting an invalid numeric grade discard the remaining input.
    questions = {}
    fields = {}
    for name in post:
        match = re.fullmatch(r"questions\[([^]]+)\]\[([^]]+)\]", name)
        if match:
            index, field = match.groups()
            value = post.getlist(name) if field == "choices" else post.get(name)
            questions.setdefault(index, {})[field] = value
        else:
            fields[name] = post.get(name)
    rows = []
    for row in questions.values():
        try:
            config = json.loads(row.get("config") or "{}")
            if not isinstance(config, dict):
                config = {}
        except (ValueError, TypeError):
            config = {}
        rows.append({**row, "config": config, "config_json": json.dumps(config),
                     "correct_answer": row.get("answer", ""), "answer_payload_json": row.get("answer", "")})
    return {**{key: fields.get(key, "") for key in ("quiz_name", "opening_date", "closing_date", "duration_minutes")}, "questions": rows, "selected_offering_id": fields.get("course_offering"),
            "selected_quiz_type_id": int(fields["quiz_type"]) if str(fields.get("quiz_type", "")).isdigit() else None,
            "content_status": fields.get("status", PublicationStatus.DRAFT), "form_errors": True}


@capability_required(can_manage_content)
def create_quiz(request):
    if request.method == "GET":
        course_offerings = CourseOffering.objects.filter(
            academic_year_level__academic_year__is_active=True
        ).select_related(
            "course", "academic_year_level__level", "academic_year_level__academic_year"
        ).order_by(
            "academic_year_level__level__ordering", "course__name",
            "academic_year_level__academic_year__ordering", "pk",
        )

        return render_page(request, "quiz_form.html", "partials/quiz_form_content.html", {
            "offering_groups" : offering_option_groups(course_offerings, include_year=True),
            "quiz_types": QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
            "question_types" : Question.QUESTION_TYPES,
            "questions" : [],
            "duration_minutes": 0,
            "application_timezone": timezone_name(),
            "content_status" : PublicationStatus.DRAFT,
            "content_status_display" : PublicationStatus.DRAFT.label,
            "publication_statuses" : PublicationStatus.choices,
            "quiz_editable" : True,
            "quiz_mode" : "create",
            "draft_saved": request.session.pop("saved_content_form", None) == "quiz-new",
            **_rejected_quiz_context(request, "rejected-quiz-new"),
        })
    elif request.method == "POST":
        request.session["rejected-quiz-new"] = {name: request.POST.getlist(name) for name in request.POST if name != "csrfmiddlewaretoken"}
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            error(request, _("Invalid publication status."), extra_tags="alert-danger")
            return redirect(reverse("quiz-create"))
        try:
            questions, quiz = unpack_quiz_form(request.POST)
            if len(questions) > MAX_QUESTIONS:
                error(request, _("Too many questions: the maximum is %(limit)s.") % {"limit": MAX_QUESTIONS}, extra_tags="alert-danger")
                return redirect(reverse("quiz-create"))
            opening_date = parse_application_datetime(quiz.get("opening_date", ""))
            closing_date = parse_application_datetime(quiz.get("closing_date", ""))
            duration_minutes = Quiz._meta.get_field("duration_minutes").clean(
                quiz.get("duration_minutes", 0), None
            )
            
            offering_id = request.POST.get("course_offering")
            course_offering = get_object_or_404(
                CourseOffering,
                pk=offering_id,
                academic_year_level__academic_year__is_active=True,
            )
            quiz_type_id = request.POST.get("quiz_type")
            quiz_type = get_object_or_404(QuizType, pk=quiz_type_id, code__in=QUIZ_TYPE_CODES)
            quiz = Quiz(
                name=quiz.get("quiz_name", ""),
                quiz_type=quiz_type,
                course_offering=course_offering,
                status=publication_status,
                total_grade=quiz.get("total_grade", 0),
                opening_date=opening_date,
                closing_date=closing_date,
                duration_minutes=duration_minutes,
                publication_event_version=1 if publication_status == PublicationStatus.PUBLISHED else 0,
            )
            # Create Questions
            raise_exception = False
            exceptions_messages = []
            questions_obj = []
            for question in questions.values():
                try:
                    questions_obj.append(build_question_instance(question, quiz))
                except ValueError as validation_error:
                    error_message = str(validation_error)
                    error(request, error_message, extra_tags="alert-danger")
                    exceptions_messages.append(error_message)
                    raise_exception = True

            if closing_date <= opening_date:
                error(request, _("Closing Date can not be before or same as Openning Date!"), extra_tags="alert-danger") # Translate
                exceptions_messages.append(_("Closing Date can not be before or same as Openning Date!")) # Translate
                raise_exception = True

            if raise_exception:
                raise Exception(*exceptions_messages)
            
            with transaction.atomic():
                # Create Quiz
                quiz.save()
                Question.objects.bulk_create(questions_obj)

                if publication_status == PublicationStatus.PUBLISHED:
                    schedule_quiz_opening_events(quiz)

                logger.info(
                    "Quiz %s is added in offering %s with %s questions",
                    quiz.name,
                    course_offering.pk,
                    len(questions_obj),
                )
                request.session.pop("rejected-quiz-new", None)
                request.session["saved_content_form"] = "quiz-new"
                success(request, _("Quiz is created successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Create quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error("Offering %s is not found to create a quiz!", request.POST.get("course_offering"))

        except ValidationError as exc:
            error(request, "; ".join(str(message) for message in exc.messages), extra_tags="alert-danger")
        
        except Exception as e:
            error(request, _("Create quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate

            logger.error(f"Create Quiz has failed : {e}")

        return redirect(reverse("quiz-create"))

@capability_required(can_manage_content)
def update_quiz(request, quiz_id):
    if request.method == "GET":
        try:
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            course_offerings = CourseOffering.objects.filter(
                Q(academic_year_level__academic_year__is_active=True) | Q(pk=quiz.course_offering_id)
            ).select_related(
                "course", "academic_year_level__level", "academic_year_level__academic_year"
            ).order_by(
                "academic_year_level__level__ordering", "course__name",
                "academic_year_level__academic_year__ordering", "pk",
            )
            return render_page(request, "quiz_form.html", "partials/quiz_form_content.html", {
                "offering_groups" : offering_option_groups(course_offerings, include_year=True),
                "selected_offering_id" : quiz.course_offering_id,
                "selected_quiz_type_id" : quiz.quiz_type_id,
                "quiz_types": QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
                "question_types" : Question.QUESTION_TYPES,
                "questions" : [question.serialize() for question in quiz.questions.all()],
                **quiz.serialize(),
                "content_status" : quiz.status,
                "content_status_display" : quiz.get_status_display(),
                "publication_statuses" : PublicationStatus.choices,
                "quiz_editable" : (
                    quiz.status == PublicationStatus.DRAFT
                    or can_manage_academic_setup(request.user)
                ),
                "quiz_mode" : "update",
                "draft_saved": request.session.pop("saved_content_form", None) == f"quiz-{quiz_id}",
                "application_timezone": timezone_name(),
                **_rejected_quiz_context(request, f"rejected-quiz-{quiz_id}"),
            })
        except Http404:
            logger.error(f"Quiz with id : {quiz_id} is not found!")
            return redirect(reverse("quiz-dashboard"))

    elif request.method == "POST":
        request.session[f"rejected-quiz-{quiz_id}"] = {name: request.POST.getlist(name) for name in request.POST if name != "csrfmiddlewaretoken"}
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            error(request, _("Invalid publication status."), extra_tags="alert-danger")
            return redirect(reverse("quiz-update", args=[quiz_id]))
        try:
            questions, quiz_data = unpack_quiz_form(request.POST)
            if len(questions) > MAX_QUESTIONS:
                error(request, _("Too many questions: the maximum is %(limit)s.") % {"limit": MAX_QUESTIONS}, extra_tags="alert-danger")
                return redirect(reverse("quiz-update", args=[quiz_id]))
            # Update Quiz
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            can_edit_published_quiz = can_manage_academic_setup(request.user)
            if quiz.status != PublicationStatus.DRAFT and not can_edit_published_quiz:
                with transaction.atomic():
                    quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
                    if quiz.status != PublicationStatus.DRAFT:
                        if publication_status == quiz.status:
                            error(request, _("Published or archived quiz cannot be edited."), extra_tags="alert-danger")
                            return redirect(reverse("quiz-update", args=[quiz_id]))
                        previous_status = quiz.status
                        quiz.status = publication_status
                        update_fields = ["status"]
                        if previous_status == PublicationStatus.PUBLISHED and publication_status != PublicationStatus.PUBLISHED:
                            cancel_future_quiz_opening_events(quiz)
                        elif publication_status == PublicationStatus.PUBLISHED:
                            quiz.publication_event_version += 1
                            update_fields.append("publication_event_version")
                        quiz.save(update_fields=update_fields)
                        if publication_status == PublicationStatus.PUBLISHED:
                            schedule_quiz_opening_events(quiz, locked=True)
                        request.session.pop(f"rejected-quiz-{quiz_id}", None)
                        request.session["saved_content_form"] = f"quiz-{quiz_id}"
                        success(request, _("Quiz status is updated successfully"), extra_tags="alert-success")
                        return redirect(reverse("quiz-update", args=[quiz_id]))
            requested_name = quiz_data['quiz_name']
            requested_opening_date = parse_application_datetime(quiz_data['opening_date'])
            requested_closing_date = parse_application_datetime(quiz_data['closing_date'])
            requested_duration_minutes = Quiz._meta.get_field("duration_minutes").clean(
                quiz_data.get("duration_minutes", 0), None
            )
            if requested_closing_date <= requested_opening_date:
                raise ValidationError(_("Closing date must be after opening date."))
            requested_total_grade = quiz_data['total_grade']
            quiz_type_id = request.POST.get("quiz_type")
            requested_quiz_type = get_object_or_404(QuizType, pk=quiz_type_id, code__in=QUIZ_TYPE_CODES)
            offering_id = request.POST.get("course_offering")
            requested_offering = get_object_or_404(CourseOffering, pk=offering_id)

            with transaction.atomic():
                quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
                if quiz.status != PublicationStatus.DRAFT and not can_edit_published_quiz:
                    error(request, _("Published or archived quiz cannot be edited."), extra_tags="alert-danger")
                    return redirect(reverse("quiz-update", args=[quiz_id]))
                if not can_edit_published_quiz and (
                    Grade.objects.filter(quiz_id=quiz_id).exists()
                    or Submission.objects.filter(question__quiz_id=quiz_id).exists()
                ):
                    raise PermissionDenied

                current_questions = {
                    question.pk: question
                    for question in Question.objects.filter(quiz_id=quiz_id)
                }
                questions_obj = []
                questions_exists = []
                questions_id = []
                regrade_ids = set()
                reset_manual_ids = set()
                clamp_manual_ids = set()
                for question_data in questions.values():
                    question_instance = build_question_instance(question_data, quiz)
                    question_id = str(question_data.get("id", "")).strip()
                    if not question_id:
                        questions_obj.append(question_instance)
                        continue
                    if not question_id.isdigit() or int(question_id) not in current_questions:
                        raise ValidationError(_("Invalid quiz question."))
                    question_pk = int(question_id)
                    if question_pk in questions_id:
                        raise ValidationError(_("Invalid quiz question."))
                    previous = current_questions[question_pk]
                    question_instance.pk = question_pk
                    questions_id.append(question_pk)
                    questions_exists.append(question_instance)
                    scoring_changed = any(
                        getattr(previous, field) != getattr(question_instance, field)
                        for field in ("correct_answer", "question_type", "config", "grade", "auto_grade")
                    )
                    if scoring_changed:
                        if previous.auto_grade or question_instance.auto_grade:
                            regrade_ids.add(question_pk)
                            if previous.auto_grade and not question_instance.auto_grade:
                                reset_manual_ids.add(question_pk)
                        elif previous.question_type != question_instance.question_type:
                            regrade_ids.add(question_pk)
                            reset_manual_ids.add(question_pk)
                        elif previous.grade != question_instance.grade:
                            regrade_ids.add(question_pk)
                            clamp_manual_ids.add(question_pk)

                removed_ids = set(current_questions) - set(questions_id)
                previous_status = quiz.status
                previous_notification_facts = (
                    quiz.name,
                    quiz.course_offering_id,
                    quiz.quiz_type_id,
                    quiz.total_grade,
                    quiz.opening_date,
                    quiz.closing_date,
                    quiz.duration_minutes,
                )
                quiz.name = requested_name
                quiz.opening_date = requested_opening_date
                quiz.closing_date = requested_closing_date
                quiz.duration_minutes = requested_duration_minutes
                quiz.total_grade = requested_total_grade
                quiz.status = publication_status
                quiz.quiz_type = requested_quiz_type
                quiz.course_offering = requested_offering
                current_notification_facts = (
                    quiz.name,
                    quiz.course_offering_id,
                    quiz.quiz_type_id,
                    quiz.total_grade,
                    quiz.opening_date,
                    quiz.closing_date,
                    quiz.duration_minutes,
                )
                notification_facts_changed = previous_notification_facts != current_notification_facts
                if publication_status == PublicationStatus.PUBLISHED and (
                    previous_status != PublicationStatus.PUBLISHED or notification_facts_changed
                ):
                    quiz.publication_event_version += 1
                # Delete Removed Questions
                Question.objects.filter(quiz=quiz).exclude(pk__in=questions_id).delete()

                # Update Current Questions
                Question.objects.bulk_update(questions_exists, ["title", "correct_answer", "question_type", "choices", "config", "grade", "auto_grade"])

                # Current New Questions
                Question.objects.bulk_create(questions_obj)
                quiz.save()
                if regrade_ids or removed_ids:
                    regrade_quiz_submissions(
                        quiz_id,
                        regrade_ids,
                        reset_manual_ids=reset_manual_ids,
                        clamp_manual_ids=clamp_manual_ids,
                    )
                # Refresh only pending opening events when their copied exam
                # facts change; already-delivered messages remain historical.
                if publication_status == PublicationStatus.PUBLISHED and previous_status != PublicationStatus.PUBLISHED:
                    schedule_quiz_opening_events(quiz, locked=True)
                elif previous_status == PublicationStatus.PUBLISHED and publication_status != PublicationStatus.PUBLISHED:
                    cancel_future_quiz_opening_events(quiz)
                elif previous_status == PublicationStatus.PUBLISHED and notification_facts_changed:
                    cancel_future_quiz_opening_events(quiz)
                    schedule_quiz_opening_events(quiz, locked=True)

            logger.info(
                "Quiz %s is updated successfully in offering %s with %s new and %s existing questions",
                quiz.name,
                offering_id,
                len(questions_obj),
                len(questions_exists),
            )
            request.session.pop(f"rejected-quiz-{quiz_id}", None)
            request.session["saved_content_form"] = f"quiz-{quiz_id}"
            success(request, _("Quiz is updated successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error("Offering or Quiz with id %s was not found", quiz_id)

        except PermissionDenied:
            raise

        except ValidationError as exc:
            error(request, "; ".join(str(message) for message in exc.messages), extra_tags="alert-danger")

        except Exception as e:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error(f"Quiz update has failed : {e}")
        
        return redirect(reverse("quiz-update", args=[quiz_id]))

class DeleteQuiz(QuizBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            raise PermissionDenied
        return super(QuizBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("lesson-dashboard")

# Submission Dashboard
def _queue_quiz_export(request, quiz_id, kind):
    if request.method != "POST":
        return HttpResponse(_("Start the quiz export from the submissions page."), status=400)
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    job = enqueue_quiz_export(
        actor=request.user,
        quiz=quiz,
        kind=kind,
        language=translation.get_language() or "ar",
    )
    try:

        enqueue_admin_operation(job.pk, queue="admin")
    except Exception:
        logger.warning("quiz export awaits worker recovery", exc_info=True)
    messages.success(
        request,
        _("Quiz export was queued. The download link appears here when it is ready."),
    )
    return redirect("submission-dashboard", quiz_id=quiz.pk)


@require_http_methods(["GET", "POST"])
@capability_required(can_grade)
def export_quiz_submissions_csv(request, quiz_id):
    return _queue_quiz_export(request, quiz_id, AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT)


@require_http_methods(["GET", "POST"])
@capability_required(can_grade)
def export_quiz_summary_csv(request, quiz_id):
    return _queue_quiz_export(request, quiz_id, AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT)


@capability_required(can_grade)
def export_submission_csv(request, grade_id):
    return export_single_submission_to_csv(grade_id)

def _grade_matrix_request_state(request):
    academic_year_id = request.GET.get("academic_year")
    level_id = request.GET.get("level")
    selected_year_id = int(academic_year_id) if academic_year_id and academic_year_id.isdigit() else None
    selected_level_id = int(level_id) if level_id and level_id.isdigit() else None
    years, selected_year, levels, selected_scope = grade_matrix_selection(
        academic_year_id=selected_year_id,
        level_id=selected_level_id,
    )

    course_value = request.GET.get("course", "").strip()
    selected_course_id = int(course_value) if course_value.isdigit() else None
    course_options = grade_matrix_courses(selected_scope)
    valid_course_ids = {course["id"] for course in course_options}
    if selected_course_id not in valid_course_ids:
        selected_course_id = None

    name = request.GET.get("name", "").strip()[:100]
    return {
        "years": years,
        "selected_year": selected_year,
        "levels": levels,
        "selected_scope": selected_scope,
        "course_options": course_options,
        "selected_course_id": selected_course_id,
        "name_value": name,
    }


@capability_required(can_view_reports)
def export_grades_matrix_xlsx(request):
    state = _grade_matrix_request_state(request)
    selected_scope = state["selected_scope"]
    selected_course_id = state["selected_course_id"]
    courses = (
        state["course_options"]
        if selected_course_id is None
        else grade_matrix_courses(selected_scope, course_id=selected_course_id)
    )
    offering_ids = tuple(course["offering_id"] for course in courses)
    students = grade_matrix_student_queryset(
        selected_scope,
        name=state["name_value"],
        course_offering_ids=offering_ids if selected_course_id else (),
    )
    workbook = grade_matrix_workbook(students, courses)
    response = FileResponse(
        workbook,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    safe_name = _safe_filename(
        f"{state['selected_year'].name}-{selected_scope.level.display_name}"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="grades_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    )
    return response


@capability_required(can_view_reports)
def grades_matrix_dashboard(request):
    state = _grade_matrix_request_state(request)
    selected_scope = state["selected_scope"]
    selected_course_id = state["selected_course_id"]
    matrix_courses = (
        state["course_options"]
        if selected_course_id is None
        else grade_matrix_courses(selected_scope, course_id=selected_course_id)
    )
    offering_ids = tuple(course["offering_id"] for course in matrix_courses)
    students = grade_matrix_student_queryset(
        selected_scope,
        name=state["name_value"],
        course_offering_ids=offering_ids if selected_course_id else (),
    )
    page_obj = Paginator(students, GRADE_MATRIX_PAGE_SIZE).get_page(request.GET.get("page", 1))
    matrix_rows = grade_matrix_page(page_obj, matrix_courses)

    level_counts = {
        row["academic_year_level_id"]: row["student_count"]
        for row in Enrollment.objects.filter(
            academic_year_level_id__in=[level.pk for level in state["levels"]],
            status__in=READABLE_ENROLLMENT_STATUSES,
            student__role__role="student",
        ).values("academic_year_level_id").annotate(
            student_count=Count("student", distinct=True),
        )
    }
    level_tabs = [
        {
            "level_id": level.level_id,
            "name": level.level.display_name,
            "student_count": level_counts.get(level.pk, 0),
            "selected": level.pk == selected_scope.pk,
        }
        for level in state["levels"]
    ]

    context = {
        "title": _("Grades matrix"),
        **state,
        "level_tabs": level_tabs,
        "matrix_courses": matrix_courses,
        "matrix_rows": matrix_rows,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "student_count": page_obj.paginator.count,
        "quiz_count": sum(len(course["quizzes"]) for course in matrix_courses),
        "total_matrix_columns": 1 + sum(course["detail_span"] for course in matrix_courses),
        "active_year": state["selected_year"].is_active,
    }
    return render_page(request, "yearly_transcript_dashboard.html", "partials/grade_matrix_content.html", context)


@capability_required(can_view_reports)
def payments_matrix_dashboard(request):
    years, selected_year, levels, selected_scope = grade_matrix_selection(
        academic_year_id=int(request.GET["academic_year"]) if request.GET.get("academic_year", "").isdigit() else None,
        level_id=int(request.GET["level"]) if request.GET.get("level", "").isdigit() else None,
    )
    name_value = request.GET.get("name", "").strip()
    status = request.GET.get("status", "all")
    if status not in PAYMENT_STATUS_VALUES:
        status = "all"

    all_students = payment_matrix_student_queryset(selected_scope, name=name_value)
    page_obj = Paginator(
        payment_matrix_student_queryset(selected_scope, name=name_value, status=status),
        PAYMENT_MATRIX_PAGE_SIZE,
    ).get_page(request.GET.get("page", 1))
    payment_rows = payment_matrix_page(page_obj, selected_scope)
    for row in payment_rows:
        receipt = row["receipt"]
        if receipt:
            row["receipt_url"] = reverse("academic-payment-document", args=[receipt.pk])
            row["download_url"] = f"{row['receipt_url']}?download=1"
        elif row["legacy_signup_payment"]:
            row["receipt_url"] = reverse(
                "application-document", args=[row["student"].pk, "payment"]
            )
            row["download_url"] = f"{row['receipt_url']}?download=1"
        else:
            row["receipt_url"] = None
            row["download_url"] = None

    level_counts = {
        level.pk: payment_matrix_student_queryset(level).count()
        for level in levels
    }
    level_tabs = [
        {
            "level_id": level.level_id,
            "name": level.level.display_name,
            "student_count": level_counts[level.pk],
            "selected": level.pk == selected_scope.pk,
        }
        for level in levels
    ]
    # One aggregate replaces three population COUNTs on every render.
    payment_counts = all_students.aggregate(
        all_count=Count("pk"),
        paid_count=Count("pk", filter=Q(payment_uploaded=True)),
        unpaid_count=Count("pk", filter=Q(payment_uploaded=False)),
    )
    context = {
        "title": _("Payments matrix"),
        "years": years,
        "selected_year": selected_year,
        "levels": levels,
        "selected_scope": selected_scope,
        "level_tabs": level_tabs,
        "name_value": name_value,
        "status": status,
        "payment_rows": payment_rows,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "student_count": page_obj.paginator.count,
        **payment_counts,
    }
    return render_page(request, "payment_matrix.html", "partials/payment_matrix_content.html", context)


@login_required(login_url=LOGIN_URL)
def academic_payment_document(request, payment_id):
    payment = get_object_or_404(AcademicPayment, pk=payment_id)
    if payment.student_id != request.user.pk and not can_view_reports(request.user):
        raise PermissionDenied
    is_download = request.GET.get("download") == "1"
    key = payment.receipt_key if is_download or not payment.receipt_preview_key else payment.receipt_preview_key
    try:
        storage_response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=key)
        response = FileResponse(
            storage_response["Body"],
            content_type=storage_response.get("ContentType")
            or mimetypes.guess_type(key)[0]
            or "application/octet-stream",
            as_attachment=is_download,
            filename=os.path.basename(payment.receipt_key if is_download else key),
        )
        if storage_response.get("ContentLength") is not None:
            response["Content-Length"] = str(storage_response["ContentLength"])
        return response
    except Exception as exc:
        logger.warning("Academic payment document unavailable for payment=%s: %s", payment_id, exc)
        raise Http404 from exc


@capability_required(can_grade)
def submission_dashboard(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    name = normalize_search_text(request.GET.get("name", "").strip()[:100])
    available_years = sorted(Grade.get_years(quiz_id), reverse=True)
    requested_year = request.GET.get("filtering")
    if requested_year is None:
        current_year = now().year
        year = str(
            current_year
            if current_year in available_years
            else available_years[0] if available_years else ""
        )
    elif (
        len(requested_year) == 4
        and requested_year.isascii()
        and requested_year.isdigit()
        and int(requested_year) in available_years
    ):
        year = requested_year
    else:
        year = ""

    def grade_bound(value, default):
        try:
            return min(max(int(value), 0), quiz.total_grade)
        except (TypeError, ValueError):
            return default

    min_grade = grade_bound(request.GET.get("min-grade", 0), 0)
    max_grade = grade_bound(request.GET.get("max-grade", quiz.total_grade), quiz.total_grade)
    user = request.user
    view = "submission"
    query = Q(quiz_id=quiz_id)
    if name:
        student_filter = normalized_contains_q(
            ("user__username", "user__first_name", "user__last_name", "user__email"),
            name,
        )
        if name.isascii() and name.isdigit() and len(name) <= 18:
            student_filter |= Q(user_id=int(name))
        query &= student_filter
    if year:
        query &= Q(submitted_at__year=int(year))
    query &= Q(total_grade__gte=min_grade, total_grade__lte=max_grade)

    logger.info(
        "User %s filters quiz submissions (search=%s, year=%s, grades=%s-%s)",
        user,
        bool(name),
        year or "all",
        min_grade,
        max_grade,
    )

    grades = (
        Grade.objects.filter(query)
        .select_related("user")
        .only(
            "id",
            "user_id",
            "quiz_id",
            "total_grade",
            "submitted_at",
            "user__id",
            "user__username",
        )
        .order_by("submitted_at", "pk")
    )
    quiz_export_job = (
        AdminOperationJob.objects.filter(
            actor=request.user,
            kind__in=[
                AdminOperationJob.Kind.QUIZ_DETAIL_EXPORT,
                AdminOperationJob.Kind.QUIZ_SUMMARY_EXPORT,
            ],
            input__quiz_id=quiz_id,
        )
        .order_by("-created_at")
        .first()
    )

    context = {
        "name_value" : name or "",
        "max_grade" : max_grade,
        "min_grade" : min_grade,
        "filtering" : year or "",
        "columns" : Grade.get_columns(),
        "table_columns": [
            {"label": _("Name"), "key": "name"},
            {"label": _("Grades"), "key": "grades"},
            {"label": _("Submission Date"), "key": "submitted_at"},
        ],
        "sort_fields": {
            "name": ("user__last_name", "user__first_name", "user__username"),
            "grades": "total_grade",
            "submitted_at": "submitted_at",
        },
        "default_sort": "name",
        "default_ordering": ["user__last_name", "user__first_name", "user__username", "pk"],
        "options" : [_("Choose Academic Year"), *[str(value) for value in available_years]],
        "submission_user" : True,
        "can_delete_submission": can_manage_academic_setup(request.user),
        "submission_filter_query": request.GET.urlencode(),
        "template_name" : "submission_dashboard.html",
        "quiz_id" : quiz_id,
        "quiz" : quiz,
        "view_name" : view,
        "quiz_export_job": quiz_export_job,
        "quiz_export_status_url": (
            reverse("admin-operation-status", args=[quiz_export_job.pk])
            if quiz_export_job else ""
        ),
        "quiz_export_download_url": (
            reverse("admin-operation-download", args=[quiz_export_job.pk])
            if quiz_export_job else ""
        ),
    }

    context["content_template_name"] = "partials/submission_dashboard_content.html"
    return render_dashboard(request, grades, view, context, parameters=[quiz_id, ])


@capability_required(can_manage_academic_setup)
@require_POST
def delete_quiz_submission(request, quiz_id, grade_id):
    student_id = Grade.objects.filter(pk=grade_id, quiz_id=quiz_id).values_list("user_id", flat=True).first()
    if student_id is None:
        raise Http404

    try:
        with transaction.atomic():
            Quiz.objects.select_for_update().only("pk").get(pk=quiz_id)
            # The quiz lock serializes deletion with submission and reconciliation.
            User.objects.select_for_update().get(pk=student_id)
            grade = Grade.objects.select_for_update().filter(
                pk=grade_id,
                quiz_id=quiz_id,
                user_id=student_id,
            ).first()
            if grade is None:
                raise Http404
            Submission.objects.filter(
                user_id=student_id,
                question__quiz_id=quiz_id,
            ).delete()
            grade.delete()
    except User.DoesNotExist as exc:
        raise Http404 from exc

    messages.success(request, _("Quiz submission deleted."))
    dashboard_url = reverse("submission-dashboard", args=[quiz_id])
    query_string = request.GET.urlencode()
    return redirect(f"{dashboard_url}?{query_string}" if query_string else dashboard_url)

@capability_required(can_grade)
def submission_user(request, quiz_id, user_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    if request.method == "GET":
        grade = get_object_or_404(Grade, quiz_id=quiz_id, user_id=user_id)
        submissions = list(
            Submission.objects.filter(question__quiz_id=quiz_id, user_id=user_id)
            .select_related("question")
            .order_by("question_id", "pk")
        )
        if not submissions:
            return HttpResponse(_("Not Found!"), 404)
        questions = [submission.serialize() for submission in submissions]
        # Admin-only surface: its own template, so the student exam shell never
        # has to be reused (or changed) for management grading.
        grade_student = grade.user
        student_label = (
            grade_student.get_full_name() or grade_student.username
        )

        return render(request, "submission_detail.html", {
            "quiz_name": quiz.name,
            "username": student_label,
            "questions": questions,
            "is_student": False,
            "can_correct_grade": can_manage_academic_setup(request.user),
            "mode": "view",
            "id": "content",
            "total_grade": grade.total_grade,
            "back_url": reverse("submission-dashboard", args=[quiz_id, ]),
        })
    
    elif request.method == "POST":
        if not can_manage_academic_setup(request.user):
            raise PermissionDenied
        questions, _parsed_form_data = unpack_quiz_form(request.POST)
        validation_errors = []
        modified_count = 0
        try:
            with transaction.atomic():
                Quiz.objects.select_for_update().only("pk").get(pk=quiz_id)
                grade = Grade.objects.select_for_update().filter(
                    quiz_id=quiz_id,
                    user_id=user_id,
                ).first()
                if grade is None:
                    return HttpResponse(_("Not Found!"), 404)
                submissions = list(
                    Submission.objects.select_for_update(of=("self",))
                    .select_related("question")
                    .filter(question__quiz_id=quiz_id, user_id=user_id)
                )
                if not submissions:
                    return HttpResponse(_("Not Found!"), 404)
                submissions_by_question = {
                    str(submission.question_id): submission
                    for submission in submissions
                }
                corrections = []
                for question in questions.values():
                    if "manual_grade" not in question:
                        continue
                    question_submission = submissions_by_question.get(str(question.get("id", "")))
                    if question_submission is None:
                        validation_errors.append(
                            _("The grading form contains a question that does not belong to this submission.")
                        )
                        continue
                    try:
                        manual_grade = int(question["manual_grade"])
                    except (TypeError, ValueError):
                        validation_errors.append(
                            _("Question %(question)s: enter a whole number.") % {
                                "question": question_submission.question_id,
                            }
                        )
                        continue
                    maximum = question_submission.question.grade
                    if manual_grade < 0 or manual_grade > maximum:
                        validation_errors.append(
                            _("Question %(question)s: the grade must be between 0 and %(maximum)s.") % {
                                "question": question_submission.question_id,
                                "maximum": maximum,
                            }
                        )
                        continue
                    corrections.append((question_submission, manual_grade))

                if not validation_errors:
                    for question_submission, manual_grade in corrections:
                        question_submission.assign_grade(True, manual_grade)
                    Submission.objects.bulk_update(
                        [item for item, _value in corrections], ["is_graded", "grade"]
                    )
                    grade.total_grade = Submission.objects.filter(
                        user_id=user_id,
                        question__quiz_id=quiz_id,
                    ).aggregate(total=Sum("grade"))["total"] or 0
                    grade.save(update_fields=["total_grade"])
                    modified_count = len(corrections)
                    logger.info(
                        "Grades updated for quiz=%s user=%s questions=%s total=%s",
                        quiz_id,
                        user_id,
                        modified_count,
                        grade.total_grade,
                    )
        except Exception:
            logger.exception(
                "Manual grading failed for quiz=%s user=%s",
                quiz_id,
                user_id,
            )
            messages.error(request, _("Grades could not be saved. No changes were made."))
            return redirect(reverse("submission-user", args=[quiz_id, user_id]))

        if validation_errors:
            for error in validation_errors[:5]:
                messages.error(request, error)
            if len(validation_errors) > 5:
                messages.error(
                    request,
                    _("%(count)s more grading errors were not shown.") % {
                        "count": len(validation_errors) - 5,
                    },
                )
            return redirect(reverse("submission-user", args=[quiz_id, user_id]))

        messages.success(
            request,
            _("%(count)s question grade(s) saved.") % {"count": modified_count},
        )
        return redirect(reverse("submission-user", args=[quiz_id, user_id]))
    
    else:
        return HttpResponse(_("Not allowed method"), 400) # Translate

@login_required(login_url=LOGIN_URL)
def generate_audio_download(
    request,
    offering_id,
    lesson_id,
    *,
    expires_in: int = 3600,
    file_index: int | None = None,
):
    try:
        file_index = int(
            file_index if file_index is not None else request.GET.get("file_index", "-1")
        )
    except (TypeError, ValueError):
        raise Http404
    try:
        media = resolve_media_link(
            user=request.user,
            offering_id=offering_id,
            lesson_id=lesson_id,
            file_index=file_index,
            allowed_types=("audio",),
            allow_unpublished=True,
        )
    except MediaAccessError as exc:
        if exc.code == "forbidden":
            raise PermissionDenied(exc.message)
        raise Http404
    try:
        download = resolve_audio_download(
            media=media,
            cloud_client=CLOUD_CLIENT,
            bucket_name=bucket_name,
            expires_in=expires_in,
        )
    except MediaAccessError as exc:
        if exc.code == "unavailable":
            return JsonResponse({"error": exc.message}, status=503)
        raise Http404
    return redirect(download.url)

# Bulk Operations
@capability_required(can_delete_content)
def bulk_delete_users(request):
    """Delete the signed selection scope for the users table."""
    return bulk_delete_records(request, "user")

@capability_required(can_delete_content)
def bulk_delete_courses(request):
    """Delete the signed selection scope for the courses table."""
    return bulk_delete_records(request, "course")

@capability_required(can_delete_content)
def bulk_delete_lessons(request):
    """Delete the signed selection scope for the lessons table."""
    return bulk_delete_records(request, "lesson")

@capability_required(can_delete_content)
def bulk_delete_quizzes(request):
    """Delete the signed selection scope for the quizzes table."""
    return bulk_delete_records(request, "quiz")

# ============================================================================
# R2 FILE MANAGEMENT API ENDPOINTS
# ============================================================================

def _storage_delete_reference_conflict(file_key: str) -> JsonResponse | None:
    """Adapt exact lesson/application storage references to the existing API response."""

    lesson_references = lesson_referenced_storage_keys([file_key])
    if file_key in lesson_references:
        names = list(
            lesson_rows_matching_storage_values([file_key]).values_list("name", flat=True)[:5]
        )
        if names:
            return JsonResponse({
                "error": _("File is referenced by lessons: %(names)s. Delete lessons first or reupload.")
                % {"names": ", ".join(names)}
            }, status=409)
    if (
        file_key in lesson_references
        or file_key in application_referenced_storage_keys([file_key])
        or file_key in other_referenced_storage_keys([file_key])
    ):
        return JsonResponse(
            {'error': _('File is still linked to a record and cannot be deleted.')},
            status=409,
        )
    return None


@capability_required(can_delete_content)
def api_delete_file(request):
    """
    API endpoint to delete a single file from R2
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    file_key = data.get('file_key')
    if not isinstance(file_key, str) or not file_key.strip():
        return JsonResponse({'error': _('File key required')}, status=400)
    file_key = file_key.strip()
    if not R2_MANAGER._is_safe_object_key(file_key):
        return JsonResponse({'error': _('Invalid file key')}, status=400)
    if not user_may_manage_storage_key(request.user, file_key):
        return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)

    try:
        if file_key.lower().endswith('.m3u8'):
            # A manifest must clean its segment children first, otherwise the
            # .ts objects are orphaned. Children are deleted before the parent.
            with guarded_storage_write(file_key):
                conflict = _storage_delete_reference_conflict(file_key)
                if conflict is not None:
                    return conflict
                successful, failed = R2_MANAGER.delete_m3u8_with_segments(file_key)
            logger.info(
                "r2 manifest deleted",
                extra={
                    "event": "r2_file_deleted",
                    "file_key": file_key,
                    "deleted_count": len(successful),
                    "failed_count": len(failed),
                },
            )
            if file_key in successful:
                return JsonResponse({
                    'success': True,
                    'message': _('M3U8 file and segments deleted successfully'),
                    'deleted_count': len(successful),
                    'failed_count': len(failed),
                    'deleted_files': successful,
                    'failed_files': failed,
                })
            return JsonResponse({'error': _('Failed to delete file')}, status=500)

        with guarded_storage_write(file_key):
            conflict = _storage_delete_reference_conflict(file_key)
            if conflict is not None:
                return conflict
            success = R2_MANAGER.delete_file(file_key)

        if success:
            logger.info(
                "r2 file deleted",
                extra={"event": "r2_file_deleted", "file_key": file_key},
            )
            return JsonResponse({'success': True, 'message': _('File deleted successfully')})
        return JsonResponse({'error': _('Failed to delete file')}, status=500)

    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception:
        logger.exception(
            "r2 file delete failed",
            extra={"event": "r2_delete_failed", "file_key": file_key},
        )
        return JsonResponse({'error': _('Failed to delete file')}, status=500)

@capability_required(can_delete_content)
def api_delete_m3u8_file(request):
    """
    API endpoint to delete an m3u8 file and all its related .ts segment files.
    Segment children are deleted before the parent manifest.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    file_key = data.get('file_key')
    if not isinstance(file_key, str) or not file_key.strip():
        return JsonResponse({'error': _('File key required')}, status=400)
    file_key = file_key.strip()
    if not R2_MANAGER._is_safe_object_key(file_key):
        return JsonResponse({'error': _('Invalid file key')}, status=400)

    if not file_key.lower().endswith('.m3u8'):
        return JsonResponse({'error': _('File must be an m3u8 file')}, status=400)
    if not user_may_manage_storage_key(request.user, file_key):
        return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)

    try:
        with guarded_storage_write(file_key):
            conflict = _storage_delete_reference_conflict(file_key)
            if conflict is not None:
                return conflict
            successful, failed = R2_MANAGER.delete_m3u8_with_segments(file_key)

        logger.info(
            "r2 manifest deleted",
            extra={
                "event": "r2_file_deleted",
                "file_key": file_key,
                "deleted_count": len(successful),
                "failed_count": len(failed),
            },
        )

        return JsonResponse({
            'success': True,
            'message': _('M3U8 file and segments deleted successfully'),
            'deleted_count': len(successful),
            'failed_count': len(failed),
            'deleted_files': successful,
            'failed_files': failed
        })

    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception:
        logger.exception(
            "r2 manifest delete failed",
            extra={"event": "r2_delete_failed", "file_key": file_key},
        )
        return JsonResponse({'error': _('Failed to delete file')}, status=500)

@capability_required(can_delete_content)
def api_delete_files_batch(request):
    """
    API endpoint to delete multiple files in batch.
    Manifest entries clean their segment children before the parent manifest;
    lesson-referenced keys are reported as failed instead of orphaning lessons.
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': _('Invalid JSON')}, status=400)
    raw_keys = data.get('file_keys')
    if not isinstance(raw_keys, list) or not raw_keys:
        return JsonResponse({'error': _('No files specified')}, status=400)
    if len(raw_keys) > MAX_EXPLICIT_SELECTION:
        return JsonResponse(
            {'error': _('Select at most %(limit)s files per request.') % {'limit': MAX_EXPLICIT_SELECTION}},
            status=400,
        )
    file_keys: list[str] = []
    seen: set[str] = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, str) or not raw_key.strip():
            return JsonResponse({'error': _('Every file key must be a non-empty string.')}, status=400)
        key = raw_key.strip()
        if not R2_MANAGER._is_safe_object_key(key):
            return JsonResponse({'error': _('Invalid file key')}, status=400)
        if not user_may_manage_storage_key(request.user, key):
            return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)
        if key not in seen:
            seen.add(key)
            file_keys.append(key)

    try:
        successful: list = []
        failed: list = []
        plain_keys: list = []
        with guarded_storage_write(*file_keys):
            referenced_keys = (
                lesson_referenced_storage_keys(file_keys)
                | application_referenced_storage_keys(file_keys)
                | other_referenced_storage_keys(file_keys)
            )
            for key in file_keys:
                if key in referenced_keys:
                    failed.append(key)
                    continue
                if key.lower().endswith('.m3u8'):
                    ok, bad = R2_MANAGER.delete_m3u8_with_segments(key)
                    successful.extend(ok)
                    failed.extend(bad)
                    # A manifest whose parent delete failed is already in `bad`;
                    # do not also queue it for the plain batch below.
                else:
                    plain_keys.append(key)

            if plain_keys:
                ok, bad = R2_MANAGER.delete_files_batch(plain_keys)
                successful.extend(ok)
                failed.extend(bad)

        logger.info(
            "r2 files deleted in batch",
            extra={
                "event": "r2_files_deleted",
                "deleted_count": len(successful),
                "failed_count": len(failed),
            },
        )

        return JsonResponse({
            'success': True,
            'message': _('Files deleted successfully'),
            'deleted_count': len(successful),
            'failed_count': len(failed),
            'deleted_files': successful,
            'failed_files': failed
        })

    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception:
        logger.exception(
            "r2 batch delete failed",
            extra={"event": "r2_delete_failed", "requested_count": len(file_keys)},
        )
        return JsonResponse({'error': _('Failed to delete file')}, status=500)

@capability_required(can_manage_content)
def api_rename_file(request):
    """Queue a tracked R2 rename so large moves do not hold an HTTP request."""
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({'error': _('Invalid rename request')}, status=400)
        old_key = data.get('old_key')
        new_key = data.get('new_key')
        is_folder = data.get('is_folder', False)
        if not isinstance(old_key, str) or not isinstance(new_key, str) or not old_key or not new_key:
            return JsonResponse({'error': _('Both old and new keys required')}, status=400)
        if not isinstance(is_folder, bool):
            return JsonResponse({'error': _('Invalid rename request')}, status=400)

        if is_folder:
            old_prefix = old_key.rstrip('/')
            new_prefix = new_key.rstrip('/')
            if (
                not R2_MANAGER._is_safe_object_key(old_prefix)
                or not R2_MANAGER._is_safe_object_key(new_prefix)
                or old_prefix == new_prefix
                or new_prefix.startswith(old_prefix + '/')
                or old_prefix.startswith(new_prefix + '/')
            ):
                return JsonResponse({'error': _('Invalid folder rename')}, status=400)
            old_key = old_prefix + '/'
            new_key = new_prefix + '/'
        else:
            if not R2_MANAGER._is_safe_object_key(old_key) or not R2_MANAGER._is_safe_object_key(new_key):
                return JsonResponse({'error': _('Invalid file key')}, status=400)
            if old_key == new_key:
                return JsonResponse({'error': _('The new name must differ')}, status=400)
        if not is_folder and (
            old_key.lower().endswith('.ts') or new_key.lower().endswith('.ts')
        ):
            return JsonResponse(
                {'error': _('Segment files cannot be renamed directly. Rename the video file instead.')},
                status=400,
            )
        old_manifest = old_key.lower().endswith('.m3u8')
        new_manifest = new_key.lower().endswith('.m3u8')
        if not is_folder and old_manifest != new_manifest:
            return JsonResponse({'error': _('Both names must be video manifests')}, status=400)
        if not user_may_manage_storage_key(request.user, old_key) or not user_may_manage_storage_key(
            request.user, new_key
        ) or not storage_key_transition_allowed(old_key, new_key):
            return JsonResponse(
                {'error': _('You do not have permission to manage this storage key.')},
                status=403,
            )

        try:
            job = enqueue_storage_rename(
                actor=request.user,
                old_key=old_key,
                new_key=new_key,
                is_folder=is_folder,
                language=translation.get_language() or "ar",
            )
        except ValidationError as exc:
            return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
        try:

            enqueue_admin_operation(job.pk, queue="admin")
        except Exception:
            logger.warning("storage rename awaits worker recovery", exc_info=True)
        status_url = reverse('admin-operation-status', args=[job.pk])
        logger.info(
            "R2 rename queued",
            extra={"event": "r2_rename_queued", "job_id": str(job.pk), "user_id": request.user.pk},
        )
        return JsonResponse({
            'status': 'queued',
            'job_id': str(job.pk),
            'status_url': status_url,
        }, status=202)
    except json.JSONDecodeError:
        return JsonResponse({'error': _('Invalid rename request')}, status=400)
    except Exception:
        logger.exception("Could not queue R2 rename")
        return JsonResponse({'error': _('Could not start the rename. Please try again.')}, status=503)


@capability_required(can_manage_content)
def api_move_file(request):
    """
    API endpoint to move a file to a different folder
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        file_key = data.get('file_key')
        destination_folder = data.get('destination_folder')
        
        if not file_key or not destination_folder:
            return JsonResponse({'error': _('File key and destination folder required')}, status=400)
        if not isinstance(file_key, str) or not isinstance(destination_folder, str):
            return JsonResponse({'error': _('File key and destination folder required')}, status=400)
        if not user_may_manage_storage_key(request.user, file_key) or not user_may_manage_storage_key(
            request.user, destination_folder
        ) or not storage_key_transition_allowed(file_key, destination_folder):
            return JsonResponse(
                {'error': _('You do not have permission to manage this storage key.')},
                status=403,
            )

        with guarded_storage_write(file_key, destination_folder):
            new_filename = file_key.rsplit("/", 1)[-1]
            normalized_destination = destination_folder.rstrip("/")
            anticipated_new_key = f"{normalized_destination}/{new_filename}"
            if storage_reference_transition_conflicts(file_key, anticipated_new_key):
                raise ValidationError(_("A record already uses a destination file name."))
            new_key = R2_MANAGER.move_file(file_key, destination_folder)
            if new_key:
                # The move has already happened; the DB reference update is
                # best-effort and must not turn a successful move into a failure.
                try:
                    updated_count = rewrite_storage_references(file_key, new_key)
                except Exception:
                    logger.exception("File-move reference update failed")
                    updated_count = 0
            else:
                updated_count = 0
        
        if new_key:
            logger.info(f"User {request.user} moved file from {file_key} to {new_key}. Updated {updated_count} storage references.")
            return JsonResponse({'success': True, 'message': _('File moved successfully and %(count)d database references updated') % {'count': updated_count}, 'new_key': new_key})
        else:
            return JsonResponse({'error': _('Failed to move file')}, status=500)
    
    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception as e:
        logger.error(f"Error moving file: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_manage_content)
def api_create_folder(request):
    """
    API endpoint to create a new folder
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        folder_path = data.get('folder_path')
        
        if not folder_path:
            return JsonResponse({'error': _('Folder path required')}, status=400)
        if not isinstance(folder_path, str) or not user_may_manage_storage_key(request.user, folder_path):
            return JsonResponse(
                {'error': _('You do not have permission to manage this storage key.')},
                status=403,
            )
        
        with guarded_storage_write(folder_path):
            success = R2_MANAGER.create_folder(folder_path)
        
        if success:
            logger.info(f"User {request.user} created folder: {folder_path}")
            return JsonResponse({'success': True, 'message': _('Folder created successfully')})
        else:
            return JsonResponse({'error': _('Failed to create folder')}, status=500)
    
    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception as e:
        logger.error(f"Error creating folder: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def api_delete_folder(request):
    """
    API endpoint to delete a folder
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        if not isinstance(data, dict):
            return JsonResponse({'error': _('Invalid folder request')}, status=400)
        folder_path = data.get('folder_path')
        try:
            recursive = parse_strict_bool(data.get('recursive'))
        except ValueError:
            return JsonResponse({'error': _('Invalid folder request')}, status=400)
        
        if not folder_path or not isinstance(folder_path, str):
            return JsonResponse({'error': _('Folder path required')}, status=400)
        if not user_may_manage_storage_key(request.user, folder_path):
            return JsonResponse(
                {'error': _('You do not have permission to manage this storage key.')},
                status=403,
            )

        if recursive:
            try:
                job = enqueue_storage_delete(
                    actor=request.user,
                    folder_path=folder_path,
                    language=translation.get_language() or "ar",
                )
            except ValidationError as exc:
                return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
            try:

                enqueue_admin_operation(job.pk, queue="admin")
            except Exception:
                logger.warning("storage delete awaits worker recovery", exc_info=True)
            logger.info(
                "R2 recursive delete queued",
                extra={"event": "r2_delete_queued", "job_id": str(job.pk), "user_id": request.user.pk},
            )
            return JsonResponse({
                "status": "queued",
                "job_id": str(job.pk),
                "status_url": reverse("admin-operation-status", args=[job.pk]),
                "message": _("Folder deletion was queued."),
            }, status=202)

        folder_prefix = folder_path.rstrip("/") + "/"
        with guarded_storage_write(folder_prefix):
            page = R2_MANAGER.list_folder_page(folder_prefix, max_keys=2)
            contents = page.get("Contents", [])
            if len(contents) > 1:
                deleted_count, errors = 0, 1
            elif not contents:
                deleted_count, errors = 0, 0
            else:
                object_key = contents[0]["Key"]
                referenced = (
                    lesson_referenced_storage_keys([object_key])
                    | application_referenced_storage_keys([object_key])
                    | other_referenced_storage_keys([object_key])
                )
                if object_key in referenced:
                    deleted_count, errors = 0, 1
                else:
                    deleted_count, errors = (
                        (1, 0) if R2_MANAGER.delete_file(object_key) else (0, 1)
                    )
        if errors > 0:
            return JsonResponse({
                'success': False,
                'error': _('Failed to delete some files'),
                'deleted_count': deleted_count,
                'error_count': errors
            }, status=500)
        
        logger.info(f"User {request.user} deleted folder: {folder_path} ({deleted_count} files)")
        return JsonResponse({
            'success': True,
            'message': _('Folder deleted successfully'),
            'deleted_count': deleted_count
        })
    
    except ValidationError as exc:
        return JsonResponse({'error': "; ".join(exc.messages)}, status=409)
    except Exception as e:
        logger.error(f"Error deleting folder: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_manage_content)
def api_get_file_metadata(request):
    """
    API endpoint to get file metadata
    """
    file_key = request.GET.get('file_key')
    
    if not file_key:
        return JsonResponse({'error': _('File key required')}, status=400)
    if not user_may_manage_storage_key(request.user, file_key):
        return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)
    
    try:
        metadata = R2_MANAGER.get_file_metadata(file_key)
        
        if metadata:
            # Convert datetime to string for JSON serialization
            if 'last_modified' in metadata and metadata['last_modified']:
                metadata['last_modified'] = metadata['last_modified'].isoformat()
            
            # Add formatted size
            metadata['size_formatted'] = R2_MANAGER.format_file_size(metadata.get('size', 0))
            
            return JsonResponse({'success': True, 'metadata': metadata})
        else:
            return JsonResponse({'error': _('File not found')}, status=404)
    
    except Exception as e:
        logger.error(f"Error getting file metadata: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@require_http_methods(["GET", "POST"])
@capability_required(can_manage_content)
def api_get_storage_stats(request):
    prefix = request.GET.get("prefix", "").strip()
    if len(prefix) > 1024 or (prefix and not R2_MANAGER._is_safe_object_key(prefix.rstrip("/"))):
        return JsonResponse({"error": _("Invalid file key")}, status=400)
    if prefix and not user_may_manage_storage_key(request.user, prefix):
        return JsonResponse({"error": _("You do not have permission to manage this storage key.")}, status=403)
    if request.method == "POST":
        job = enqueue_storage_usage(
            actor=request.user, prefix=prefix, language=translation.get_language() or "ar",
        )
        try:
            enqueue_admin_operation(job.pk, queue="admin")
        except Exception:
            logger.warning("storage usage awaits worker recovery", exc_info=True)
        response = JsonResponse({
            "status": job.status, "status_url": reverse("admin-operation-status", args=[job.pk]),
        }, status=202)
    else:
        stats = storage_usage_snapshot(prefix)
        if stats is None:
            response = JsonResponse({"success": False, "available": False, "refresh_required": True})
        else:
            # Filtering the private sample is per-request; never cache actor visibility.
            stats["largest_files"] = [
                entry for entry in stats.get("largest_files", [])
                if user_may_manage_storage_key(request.user, entry.get("key", ""))
            ]
            if not stats.get("available", True):
                response = JsonResponse({
                    "success": False, "available": False,
                    "unavailable_reason": stats.get("unavailable_reason", "unknown"),
                    "error": _("Storage statistics are unavailable right now."),
                }, status=503)
            else:
                response = JsonResponse({"success": True, "stats": stats})
    response["Cache-Control"] = "no-store"
    return response

@capability_required(can_manage_content)
def api_search_files(request):
    """
    API endpoint to search for files (recursive under the given prefix)
    """
    query = request.GET.get('q', '')
    prefix = request.GET.get('prefix', '')
    extensions = request.GET.get('extensions', '')
    continuation_token = request.GET.get('continuation') or None
    
    if not query:
        return JsonResponse({'error': _('Search query required')}, status=400)
    
    try:
        filter_config = None
        if extensions:
            ext_list = [e.strip() for e in extensions.split(',') if e.strip()]
            filter_config = FileFilterConfig(
                allowed_extensions=set(ext_list),
                exclude_extensions={'ts'},
                include_folders=True,
            )
        if prefix.strip() and not user_may_manage_storage_key(request.user, prefix):
            return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)

        results, next_token = search_files_page(
            CLOUD_CLIENT,
            bucket_name,
            prefix,
            query,
            filter_config,
            continuation_token=continuation_token,
        )
        results = filter_visible_storage_entries(request.user, results)
        
        # Format sizes and dates
        for file_data in results:
            file_data['size_formatted'] = R2_MANAGER.format_file_size(file_data['size'])
            if 'last_modified' in file_data and file_data['last_modified']:
                file_data['last_modified'] = file_data['last_modified'].isoformat()
        
        return JsonResponse({
            'success': True,
            'results': results,
            'count': len(results),
            'next_continuation': next_token,
        })
    
    except Exception as e:
        logger.error(f"Error searching files: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

def api_quiz_status(request, offering_id):
    """
    API endpoint - returns quiz mode/status for every quiz in a course
    for the requesting user. Used by the course-detail sidebar to show
    status badges without reloading the page.

    Response: { "quizzes": [ { "id": int, "status": "exam"|"view"|"closed_unsolved" }, ... ] }
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": _("Authentication required")}, status=401)

    try:
        user = User.objects.get(pk=request.user.pk)
        offering = get_accessible_offering_or_403(user, offering_id)

        current_time = now()
        quizzes = list(
            offering.quizzes.all()
            if user_has_management_role(user)
            else offering.quizzes.filter(status=PublicationStatus.PUBLISHED)
        )
        statuses = get_student_quiz_status_map(quizzes, user, current_time)
        result = [
            {"id": quiz.pk, "status": statuses[quiz.pk][0]} for quiz in quizzes
        ]

        return JsonResponse({"quizzes": result})

    except PermissionDenied:
        return JsonResponse(
            {"error": _("You do not have access to this offering.")},
            status=403,
        )
    except Exception:
        logger.exception("api_quiz_status_failed")
        return JsonResponse({"error": _("Server error.")}, status=500)

@capability_required(can_manage_content)
def api_list_files(request):
    """
    API endpoint to list files with filtering
    """
    folder_name = request.GET.get('folder', '')
    continuation_token = request.GET.get('continuation') or None
    search_query = request.GET.get('search', '').strip()
    filter_preset = request.GET.get('filter', 'media')
    extensions = request.GET.get('extensions', '')
    
    try:
        # Get filter configuration
        if extensions:
            ext_list = [e.strip() for e in extensions.split(',') if e.strip()]
            filter_config = FileFilterConfig(
                allowed_extensions=set(ext_list),
                exclude_extensions={'ts'},
                include_folders=True
            )
        else:
            filter_config = get_filter_preset(filter_preset)
        if folder_name.strip() and not user_may_manage_storage_key(request.user, folder_name):
            return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)
        
        # List files with filter
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, folder_name, filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
        contents = filter_visible_storage_entries(request.user, contents)
        
        # Format sizes and dates
        for item in contents:
            if item['type'] == 'file':
                item['size_formatted'] = R2_MANAGER.format_file_size(item.get('size', 0))
                if 'last_modified' in item and item['last_modified']:
                    item['last_modified'] = item['last_modified'].isoformat()
        
        return JsonResponse({
            'success': True,
            'contents': contents,
            'parent_folder': parent_folder,
            'current_folder': folder_name,
            'next_continuation': next_token,
        })
    
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_manage_content)
def api_download_file(request):
    """
    API endpoint to generate download URL for a file
    """
    file_key = request.GET.get('file_key')
    
    if not file_key:
        return JsonResponse({'error': _('File key required')}, status=400)
    if not user_may_manage_storage_key(request.user, file_key):
        return JsonResponse({'error': _('You do not have permission to manage this storage key.')}, status=403)
    
    try:
        # Generate presigned URL for download
        download_url = generate_unique_url(CLOUD_CLIENT, bucket_name, file_key)
        
        # Redirect to the presigned URL
        return redirect(download_url)
    
    except Exception as e:
        logger.error(f"Error generating download URL: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

# ============================================================================
# R2 MANAGEMENT DASHBOARD VIEW
# ============================================================================

@capability_required(can_manage_content)
def r2_management_dashboard(request):
    """
    Main R2 management dashboard view
    """
    folder_id = request.GET.get('folder', None)
    filter_preset = request.GET.get('filter', '') or 'media'
    search_query = request.GET.get('search', '').strip()
    continuation_token = request.GET.get('continuation') or None
    
    # Get filter configuration
    filter_config = get_filter_preset(filter_preset)
    
    # List files with filter
    if folder_id and folder_id != "None":
        if not user_may_manage_storage_key(request.user, folder_id):
            raise PermissionDenied
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, folder_id, filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
    else:
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, "", filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
    contents = filter_visible_storage_entries(request.user, contents)
    
    stats = storage_usage_snapshot("") or {
        "total_files": "...", "total_size": 0, "by_extension": {},
        "largest_files": [], "loading": True,
    }

    context = {
        'drive': contents,
        'parent_folder': parent_folder,
        'is_root': not folder_id or folder_id == "None",
        'current_filter': filter_preset,
        'search_query': search_query,
        'filter_presets': filter_preset_choices(),
        'storage_stats': stats,
        'total_size_formatted': R2_MANAGER.format_file_size(stats.get('total_size', 0)) if isinstance(stats.get('total_size'), int) else '...',
        'next_page_url': None,
    }
    if next_token:
        next_params = request.GET.copy()
        next_params['continuation'] = next_token
        context['next_page_url'] = f"{request.path}?{next_params.urlencode()}"
    
    # Return partial for HTMX folder navigation (breadcrumb + back + grid)
    hx_target = hx_target_id(request)
    if hx_target == 'file-list-container':
        return render(request, 'partials/r2_browse.html', context)
    if hx_target == 'drive-files':
        return render(request, 'partials/r2_file_list.html', context)
    
    return render_page(request, 'r2_management.html', "partials/r2_management_content.html", context)

# ============================================================================
# PHASE 3 - Student Applications
# ============================================================================

def signup(request):
    if request.user.is_authenticated:
        return redirect("view-profile")
    signup_config = SignupSettings.load()
    language_is_arabic = (translation.get_language() or "en").lower().startswith("ar")
    if signup_config is not None and not signup_config.is_open:
        if language_is_arabic:
            closed_title = signup_config.closed_title_ar or _("Applications are closed")
            closed_message = signup_config.closed_message_ar or _(
                "Application is currently closed. Please apply next year."
            )
        else:
            closed_title = signup_config.closed_title_en or _("Applications are closed")
            closed_message = signup_config.closed_message_en or _(
                "Application is currently closed. Please apply next year."
            )
        return render(request, "signup_closed.html", {
            "closed_title": closed_title,
            "closed_message": closed_message,
        })
    if signup_config is not None:
        if language_is_arabic:
            page_title = signup_config.title_ar or _("Student Application")
            page_subtitle = signup_config.subtitle_ar or _("Fill in your details to apply")
        else:
            page_title = signup_config.title_en or _("Student Application")
            page_subtitle = signup_config.subtitle_en or _("Fill in your details to apply")
    else:
        page_title = _("Student Application")
        page_subtitle = _("Fill in your details to apply")
    copy = get_institute_copy(translation.get_language(), "courses")["copy"]
    if request.method == "POST":
        form = SignupForm(request.POST, request.FILES)
        if form.is_valid():
            user = None
            uploaded_keys = []
            try:
                with transaction.atomic():
                    user = form.save()
                    lock_application_document_namespace(user.pk)
                    study_mode_changed = apply_study_mode_from_city(user)
                    if study_mode_changed:
                        user.save(update_fields=["study_mode"])

                    file_type_map = APPLICATION_DOCUMENT_FIELDS
                    required_upload_types = {"identity_front", "profile"}
                    if user.identity_type == "national_id":
                        required_upload_types.add("identity_back")
                    for upload_type, model_field in file_type_map.items():
                        if upload_type not in request.FILES:
                            continue
                        uploaded = upload_application_file(
                            CLOUD_CLIENT,
                            bucket_name,
                            user.id,
                            request.FILES[upload_type],
                            upload_type,
                        )
                        if uploaded:
                            setattr(user, model_field, uploaded.original_key)
                            setattr(user, APPLICATION_PREVIEW_FIELDS[upload_type], uploaded.preview_key)
                            uploaded_keys.extend(uploaded.keys)

                    missing_documents = [
                        upload_type for upload_type in required_upload_types
                        if not getattr(user, file_type_map[upload_type], None)
                    ]
                    if missing_documents:
                        document_labels = {
                            "identity_front": _("Identity Front"),
                            "identity_back": _("Identity Back"),
                            "profile": _("Profile Photo"),
                        }
                        named = ", ".join(str(document_labels[t]) for t in file_type_map if t in missing_documents)
                        raise ValidationError(_("Missing required document(s): %(documents)s.") % {"documents": named})
                    if uploaded_keys:
                        user.save(
                            update_fields=[
                                *[v for v in file_type_map.values() if getattr(user, v, None)],
                                *[v for v in APPLICATION_PREVIEW_FIELDS.values() if getattr(user, v, None)],
                            ]
                        )
            except ValidationError as exc:
                delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
                if user is not None:
                    user.delete()
                form.add_error(None, "; ".join(str(message) for message in exc.messages))
                return render(request, "signup.html", {
                    "form": form,
                    "copy": copy,
                    "egyptian_cities": EGYPTIAN_CITIES,
                    "page_title": page_title,
                    "page_subtitle": page_subtitle,
                })
            except Exception:
                delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
                if user is not None:
                    user.delete()
                raise
            send_application_received(user)
            return render(request, "signup_success.html")
    else:
        form = SignupForm()
    return render(request, "signup.html", {
        "form": form,
        "copy": copy,
        "egyptian_cities": EGYPTIAN_CITIES,
        "page_title": page_title,
        "page_subtitle": page_subtitle,
    })

def _application_search_query(value):
    return normalized_contains_q(
        ("username", "email", "first_name", "last_name", "phone", "identity_number"),
        value,
    )


def _applications_dashboard_context(request):
    status_filter = request.GET.get("status", "all")
    application_search = normalize_search_text(request.GET.get("name", "").strip()[:100])
    application_order = [
        Case(
            When(application_status="pending", then=0),
            default=1,
            output_field=IntegerField(),
        ),
        "last_name",
        "first_name",
        "username",
        "pk",
    ]
    if status_filter == "all":
        users = User.objects.filter(
            Q(application_status__in=["pending", "active", "declined"])
        ).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "study_mode", "date_joined",
        ).order_by(*application_order)
    elif status_filter in ("pending", "active", "declined"):
        users = User.objects.filter(application_status=status_filter).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "study_mode", "date_joined",
        ).order_by(*application_order)
    else:
        status_filter = "all"
        users = User.objects.filter(
            Q(application_status__in=["pending", "active", "declined"])
        ).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "study_mode", "date_joined",
        ).order_by(*application_order)
    if application_search:
        users = users.filter(_application_search_query(application_search))
    paginator = Paginator(users, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    # Accurate decision-tab counts: one aggregate over the search-filtered
    # population with the status filter removed, so every tab count equals the
    # destination list count.
    counts_base = User.objects.filter(
        application_status__in=["pending", "active", "declined"]
    )
    if application_search:
        counts_base = counts_base.filter(_application_search_query(application_search))
    status_counts = counts_base.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(application_status="pending")),
        active=Count("id", filter=Q(application_status="active")),
        declined=Count("id", filter=Q(application_status="declined")),
    )
    filter_tabs = []
    for code, label in (("", _("All")),) + tuple(User.APPLICATION_STATUSES):
        params = request.GET.copy()
        params.pop("page", None)
        if code:
            params["status"] = code
        else:
            params.pop("status", None)
        filter_tabs.append({
            "key": code or "all",
            "label": label,
            "url": f"{reverse('applications-dashboard')}?{params.urlencode()}",
            "is_active": status_filter == (code or "all"),
            "count": status_counts["total"] if not code else status_counts[code],
        })

    return {
        "page_obj": page_obj,
        "current_status": status_filter,
        "application_search": application_search,
        "pagination_query": pagination_params.urlencode(),
        "filter_tabs": filter_tabs,
        "page_eyebrow": _("People"),
        "page_title": _("Student Applications"),
        "page_description": _(
            "Review applications, record decisions and open the student record."
        ),
        "page_actions": [{
            "label": _("Signup Settings"),
            "url": reverse("signup-settings"),
            "icon": "i-settings",
            "variant": "ghost",
        }],
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Applications"), None),
        ]),
        "COURSE_LEVELS": Level.objects.order_by("ordering"),
        "bulk_selection_token": signing.dumps(
            {
                "status": status_filter,
                "search": application_search,
                "actor": request.user.pk,
            },
            salt="bulk-application-selection",
        ),
    }


@capability_required(can_manage_applications)
def applications_dashboard(request):
    return render_page(
        request,
        "applications_dashboard.html",
        "partials/applications_dashboard_content.html",
        _applications_dashboard_context(request),
    )

@capability_required(can_manage_applications)
def signup_settings(request):
    """Admin control over the public signup form and the acceptance intake scope."""
    settings_row = SignupSettings.load()
    if request.method == "POST":
        form = SignupSettingsForm(request.POST, instance=settings_row)
        if form.is_valid():
            form.instance.updated_by = request.user
            form.save()
            messages.success(request, _("Signup settings saved."))
            return redirect("signup-settings")
    else:
        form = SignupSettingsForm(instance=settings_row)
    active_years = list(AcademicYear.objects.filter(is_active=True).order_by("pk"))
    has_single_active_year = len(active_years) == 1
    effective_scope = _resolve_intake_scope(active_years[0]) if has_single_active_year else None
    return render_page(request, "signup_settings.html", "partials/signup_settings_content.html", {
        "form": form,
        "effective_scope": effective_scope,
        "has_single_active_year": has_single_active_year,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Signup Settings"), None),
        ]),
    })

@capability_required(can_manage_applications)
def application_review(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    original_study_mode = user.study_mode
    if request.method == "POST":
        original_status = user.application_status
        original_role_id = user.role_id
        original_is_active = user.is_active
        form = ApplicationAdminForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            staged = None
            change = None
            database_committed = False
            desired_status = form.cleaned_data["application_status"]
            try:
                with transaction.atomic():
                    lock_application_document_namespace(user.id)
                    staged = stage_application_documents(
                        cloud_client=CLOUD_CLIENT,
                        bucket_name=bucket_name,
                        user_id=user.id,
                        cleaned_data=form.cleaned_data,
                    )
                    locked_user = lock_application_status_for_edit(user.pk, original_status)
                    original_role_id = locked_user.role_id
                    original_is_active = locked_user.is_active
                    if desired_status != original_status:
                        form.cleaned_data["application_status"] = original_status
                    apply_cleaned_data_to_locked_instance(form, locked_user)
                    user = form.save()
                    change = apply_application_document_changes(
                        user, staged, form.cleaned_data
                    )

                    if desired_status != original_status:
                        user, _enrollment = set_application_status(
                            user, request.user, desired_status, original_status
                        )
                    if desired_status == original_status and (
                        original_role_id != user.role_id
                        or original_is_active and not user.is_active
                    ):
                        revoke_user_mobile_access(user)
                    if (
                        form.cleaned_data.get("study_mode", original_study_mode) == original_study_mode
                        and apply_study_mode_from_city(user)
                    ):
                        user.save(update_fields=["study_mode"])
                    scope = form.cleaned_data.get("enrollment_scope")
                    if scope and user.application_status == "active":
                        set_user_normal_enrollment_scope(user, request.user, scope)
                database_committed = True
                cleanup_replaced_application_documents(change, CLOUD_CLIENT, bucket_name)
                if desired_status != original_status:
                    try:
                        send_application_status_notification(user, desired_status)
                    except Exception:
                        logger.exception("Could not queue application status notification for user %s", user.pk)
                messages.success(request, _("Application and student data updated."))
                return redirect("application-review", user_id=user.pk)
            except (ValidationError, PermissionDenied) as exc:
                if not database_committed and staged is not None:
                    rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
                form.add_error(None, str(exc))
            except User.DoesNotExist as exc:
                if not database_committed and staged is not None:
                    rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
                raise Http404 from exc
            except Exception:
                if not database_committed and staged is not None:
                    rollback_staged_application_documents(staged, CLOUD_CLIENT, bucket_name)
                raise
    else:
        form = ApplicationAdminForm(instance=user)

    if request.method == "POST":
        user.refresh_from_db()
    documents = []
    for document_type, field in APPLICATION_DOCUMENT_FIELDS.items():
        key = getattr(user, field, None)
        if key:
            content_type = mimetypes.guess_type(key)[0] or ""
            documents.append({
                "type": document_type,
                "label": APPLICATION_DOCUMENT_LABELS[document_type],
                "url": reverse("application-document", args=[user.pk, document_type]),
                "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                "is_image": content_type.startswith("image/"),
                "is_pdf": content_type == "application/pdf" or key.lower().endswith(".pdf"),
            })
    context = {
        "app_user": user,
        "edit_form": form,
        "documents": documents,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Applications"), reverse("applications-dashboard")),
            (user.get_full_name() or user.username, None),
        ]),
        # The detail trail lives inside #content because the decision forms swap
        # that root; the page region must not render a second copy.
        "page_region_breadcrumbs": False,
        "page_eyebrow": _("People"),
        "page_title": _("Application Review"),
        "page_description": _(
            "Review the application, documents and the decision for this student."
        ),
        "page_actions": [{
            "label": _("Applications"),
            "url": reverse("applications-dashboard"),
            "icon": "i-chevron-left",
            "variant": "ghost",
        }],
        "COURSE_LEVELS": Level.objects.order_by("ordering"),
    }
    return render_page(
        request,
        "application_review.html",
        "partials/application_review_content.html",
        context,
    )


def _delete_user_storage(document_keys: list[str], telegram_keys: list[str]) -> None:
    for key in set(document_keys):
        try:
            CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
        except Exception:
            logger.exception("Could not delete application document after user deletion")
    if telegram_keys:
        try:
            telegram_client = get_r2_client()
            for key in set(telegram_keys):
                telegram_client.delete_object(
                    Bucket=getattr(settings, "TELEGRAM_R2_BUCKET_NAME", ""),
                    Key=key,
                )
        except Exception:
            logger.exception("Could not delete Telegram attachments after user deletion")


@capability_required(can_manage_applications)
@require_POST
def application_delete(request, user_id):
    """Permanently delete an application and its user-owned records."""
    if request.user.pk == user_id:
        messages.error(request, _("You cannot delete your own administrator account."))
        return redirect("applications-dashboard")
    document_keys = []
    telegram_keys = []
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user_id)
            document_keys = [
                key for key in (
                    user.identity_front_key,
                    user.identity_back_key,
                    user.payment_key,
                    user.profile_image_key,
                    user.identity_front_preview_key,
                    user.identity_back_preview_key,
                    user.payment_preview_key,
                    user.profile_image_preview_key,
                ) if key
            ]
            document_keys.extend(
                key
                for receipt_key, preview_key in AcademicPayment.objects.filter(student_id=user.pk).values_list(
                    "receipt_key", "receipt_preview_key"
                )
                for key in (receipt_key, preview_key)
                if key
            )
            telegram_keys = list(
                TelegramAttachment.objects.filter(
                    message__conversation__user_id=user.pk,
                ).exclude(r2_key="").values_list("r2_key", flat=True)
            )
            user.delete()
            transaction.on_commit(
                lambda: _delete_user_storage(document_keys, telegram_keys)
            )
    except User.DoesNotExist:
        messages.error(request, _("The application no longer exists."))
        return redirect("applications-dashboard")
    except ProtectedError:
        messages.error(
            request,
            _("This user cannot be permanently deleted because other records depend on the account."),
        )
        return redirect("applications-dashboard")
    messages.success(request, _("The application and student account were permanently deleted."))
    return redirect("applications-dashboard")


@login_required(login_url=LOGIN_URL)
def application_document(request, user_id, document_type):
    if request.user.pk != user_id and not can_manage_applications(request.user):
        raise PermissionDenied
    if document_type not in APPLICATION_DOCUMENT_FIELDS:
        raise Http404
    user = get_object_or_404(User, pk=user_id)
    original_field = APPLICATION_DOCUMENT_FIELDS[document_type]
    preview_field = APPLICATION_PREVIEW_FIELDS[document_type]
    original_key = getattr(user, original_field, None)
    if not original_key:
        raise Http404
    is_download = request.GET.get("download") == "1"
    key = original_key if is_download or not getattr(user, preview_field, None) else getattr(user, preview_field)
    try:
        storage_response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=key)
        response = FileResponse(
            storage_response["Body"],
            content_type=storage_response.get("ContentType") or mimetypes.guess_type(key)[0] or "application/octet-stream",
            as_attachment=is_download,
            filename=os.path.basename(original_key if is_download else key),
        )
        if storage_response.get("ContentLength") is not None:
            response["Content-Length"] = str(storage_response["ContentLength"])
        # Avatars/documents are polled repeatedly by live pages (Telegram chat,
        # dashboards); let the browser reuse a fresh copy instead of re-downloading.
        if not is_download:
            response["Cache-Control"] = "private, max-age=300"
        return response
    except Exception as exc:
        logger.warning("Application document unavailable for user=%s type=%s: %s", user_id, document_type, exc)
        raise Http404 from exc


@login_required(login_url=LOGIN_URL)
@require_POST
def profile_missing_documents(request):
    """Accept only currently missing signup documents for the logged-in user."""
    uploaded_keys = []
    uploaded_by_type = {}
    try:
        user = User.objects.get(pk=request.user.pk)
        form = MissingApplicationDocumentsForm(
            request.POST,
            request.FILES,
            instance=user,
        )
        submitted_types = set(request.FILES)
        allowed_types = set(form.document_types)
        if not submitted_types:
            raise ValidationError(_("Select at least one missing document."))
        if not submitted_types.issubset(allowed_types):
            raise ValidationError(_("Only missing signup documents can be uploaded."))
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())

        with transaction.atomic():
            lock_application_document_namespace(request.user.pk)
            user = User.objects.select_for_update().get(pk=request.user.pk)
            current_form = MissingApplicationDocumentsForm(instance=user)
            current_document_types = current_form.document_types
            allowed_types = set(current_document_types)
            if not submitted_types.issubset(allowed_types):
                raise ValidationError(_("Only missing signup documents can be uploaded."))
            for document_type in current_document_types:
                uploaded_file = form.cleaned_data.get(document_type)
                if not uploaded_file:
                    continue
                uploaded = upload_application_file(
                    CLOUD_CLIENT,
                    bucket_name,
                    user.pk,
                    uploaded_file,
                    document_type,
                )
                if not uploaded:
                    raise ValidationError(
                        _("The %(document)s could not be uploaded.")
                        % {"document": form.fields[document_type].label}
                    )
                uploaded_by_type[document_type] = uploaded
                uploaded_keys.extend(uploaded.keys)
            if not uploaded_by_type:
                raise ValidationError(_("Select at least one missing document."))
            update_fields = []
            for document_type, model_field in current_document_types.items():
                uploaded = uploaded_by_type.get(document_type)
                if not uploaded:
                    continue
                preview_field = APPLICATION_PREVIEW_FIELDS[document_type]
                setattr(user, model_field, uploaded.original_key)
                setattr(user, preview_field, uploaded.preview_key)
                update_fields.extend([model_field, preview_field])
            if not update_fields:
                raise ValidationError(_("Select at least one missing document."))
            user.save(update_fields=update_fields)
    except (ValidationError, User.DoesNotExist) as exc:
        delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
        if isinstance(exc, User.DoesNotExist):
            raise Http404 from exc
        if resolve_student_mode(request) == StudentMode.NEW:
            messages.error(request, str(exc))
            context = build_student_profile_context(
                request,
                request.user,
                missing_documents_form=form,
            )
            return render(
                request,
                "student_next/profile.html",
                {**context, **student_shell_context("profile")},
            )
        messages.error(request, str(exc))
        return redirect("view-profile")
    except Exception:
        delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
        raise

    messages.success(request, _("Missing signup documents uploaded successfully."))
    return redirect("view-profile")


@login_required(login_url=LOGIN_URL)
@require_POST
def profile_academic_payment(request):
    """Accept one new, non-replaceable receipt for an eligible academic scope."""
    uploaded_keys = []
    try:
        user = User.objects.get(pk=request.user.pk)
        form = AcademicPaymentForm(request.POST, request.FILES, student=user)
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())
        scope = form.cleaned_data["academic_year_level"]
        with transaction.atomic():
            lock_application_document_namespace(request.user.pk)
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if not academic_payment_scopes_for_student(user).filter(pk=scope.pk).exists():
                raise ValidationError(_("The selected academic scope is no longer available for payment."))
            if AcademicPayment.objects.filter(
                student=user,
                academic_year_level=scope,
            ).exists():
                raise ValidationError(_("A payment receipt already exists for this academic level."))
            uploaded = upload_application_file(
                CLOUD_CLIENT,
                bucket_name,
                user.pk,
                form.cleaned_data["payment"],
                "academic_payment",
            )
            if not uploaded:
                raise ValidationError(_("The payment receipt could not be uploaded."))
            uploaded_keys.extend(uploaded.keys)
            AcademicPayment.objects.create(
                student=user,
                academic_year_level=scope,
                receipt_key=uploaded.original_key,
                receipt_preview_key=uploaded.preview_key,
            )
    except (ValidationError, User.DoesNotExist) as exc:
        delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
        if isinstance(exc, User.DoesNotExist):
            raise Http404 from exc
        if resolve_student_mode(request) == StudentMode.NEW:
            messages.error(request, str(exc))
            context = build_student_profile_context(
                request,
                request.user,
                academic_payment_form=form,
            )
            return render(
                request,
                "student_next/profile.html",
                {**context, **student_shell_context("profile")},
            )
        messages.error(request, str(exc))
        return redirect("view-profile")
    except Exception:
        delete_application_files(CLOUD_CLIENT, bucket_name, uploaded_keys)
        raise

    messages.success(request, _("Academic payment receipt uploaded successfully."))
    return redirect("view-profile")


@capability_required(can_manage_applications)
def application_decision(request, user_id, decision):
    if decision not in ("activate", "decline", "pending"):
        return HttpResponse(_("Invalid decision"), status=400)
    if request.method != "POST":
        return HttpResponse(_("Method not allowed"), status=405)
    user = get_object_or_404(User, pk=user_id)
    expected_status = request.POST.get("expected_status") or None
    success_message = None

    try:
        original_status = expected_status or user.application_status
        if decision == "activate":
            user, _enrollment = accept_application(user, request.user, expected_status)
            if original_status != user.application_status:
                send_application_status_notification(user, user.application_status)
            success_message = _("%(name)s activated.") % {"name": user.get_full_name() or user.username}
        elif decision == "decline":
            user = decline_application(user, request.user, expected_status)
            if original_status != user.application_status:
                send_application_status_notification(user, user.application_status)
            success_message = _("%(name)s declined.") % {"name": user.get_full_name() or user.username}
        else:
            user = reopen_application(user, request.user, expected_status)
            success_message = _("%(name)s returned to pending.") % {"name": user.get_full_name() or user.username}
    except (ValidationError, PermissionDenied) as exc:
        message = exc.message if isinstance(exc, ValidationError) and hasattr(exc, "message") else str(exc)
        messages.error(request, message)
        return redirect("applications-dashboard")
    messages.success(request, success_message)
    return redirect("applications-dashboard")

@capability_required(can_manage_applications)
def bulk_application_decision(request):
    if request.method != "POST":
        return JsonResponse({"error": _("Method not allowed")}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid JSON")}, status=400)
    decision = data.get("decision")
    user_ids = data.get("user_ids", [])
    excluded_ids = data.get("excluded_user_ids", [])
    if not isinstance(user_ids, list) or not isinstance(excluded_ids, list):
        return JsonResponse({"error": _("Selection fields have invalid types.")}, status=400)
    if decision not in ("activate", "decline", "pending"):
        return JsonResponse({"error": _("Invalid decision")}, status=400)

    try:
        excluded_ids = [int(value) for value in excluded_ids]
    except (TypeError, ValueError):
        return JsonResponse({"error": _("excluded_user_ids must contain numeric IDs.")}, status=400)

    try:
        selection = signing.loads(
            data.get("selection_token", ""),
            salt="bulk-application-selection",
            max_age=3600,
        )
    except signing.BadSignature:
        return JsonResponse({"error": _("The selection has expired. Reload the applications list and try again.")}, status=409)
    if selection.get("actor") != request.user.pk:
        return JsonResponse({"error": _("The selection is not valid for this operator.")}, status=403)
    selected_status = selection.get("status")
    status_filter = (
        [selected_status]
        if selected_status in ("pending", "active", "declined")
        else ["pending", "active", "declined"]
        if selected_status == "all"
        else None
    )
    if status_filter is None:
        return JsonResponse({"error": _("Invalid application status")}, status=400)

    application_search = str(selection.get("search", "")).strip()[:100]

    try:
        select_all = parse_strict_bool(data.get("select_all"))
    except ValueError:
        return JsonResponse({"error": _("Invalid selection flags.")}, status=400)
    if select_all:
        selection_queryset = User.objects.filter(
            application_status__in=status_filter,
        ).exclude(pk__in=excluded_ids).order_by("pk")
        if application_search:
            selection_queryset = selection_queryset.filter(_application_search_query(application_search))
        selected_ids = selection_queryset.values_list("id", flat=True).iterator(chunk_size=100)
        requested = selection_queryset.count()
    else:
        if len(user_ids) > 1000:
            return JsonResponse({"error": _("Select all matching records for selections larger than 1,000.")}, status=400)
        try:
            user_ids = list(dict.fromkeys(int(value) for value in user_ids))
        except (TypeError, ValueError):
            return JsonResponse({"error": _("user_ids must contain numeric IDs.")}, status=400)
        user_ids = [user_id for user_id in user_ids if user_id not in set(excluded_ids)]
        selected_ids = iter(user_ids)
        requested = len(user_ids)

    results = {"success": [], "stale": [], "errors": [], "notification_failures": []}
    while True:
        chunk = list(islice(selected_ids, 100))
        if not chunk:
            break
        expected_queryset = User.objects.filter(
            pk__in=chunk,
            application_status__in=status_filter,
        )
        if application_search:
            expected_queryset = expected_queryset.filter(
                _application_search_query(application_search)
            )
        expected_statuses = dict(
            expected_queryset.values_list("pk", "application_status")
        )
        for user_id in chunk:
            if user_id not in expected_statuses:
                results["stale"].append({
                    "id": user_id,
                    "error": str(_("The application is no longer in the selected status.")),
                })
        operation_ids = [user_id for user_id in chunk if user_id in expected_statuses]
        if not operation_ids:
            continue
        target_status = "active" if decision == "activate" else "declined" if decision == "decline" else "pending"
        try:
            batch_results = bulk_set_application_status(
                operation_ids, request.user, target_status, expected_statuses,
            )
            results["success"].extend(batch_results["success"])
            results["stale"].extend(batch_results["stale"])
            results["errors"].extend(batch_results["errors"])
            for changed_user in batch_results["changed_users"]:
                try:
                    send_application_status_notification(changed_user, target_status)
                except Exception as exc:
                    logger.exception(
                        "Application decision notification failed for user %s",
                        changed_user.pk,
                    )
                    results["notification_failures"].append({
                        "id": changed_user.pk,
                        "error": str(exc),
                    })
        except (ValidationError, PermissionDenied) as exc:
            message = exc.message if hasattr(exc, "message") else str(exc)
            results["errors"].extend({"id": user_id, "error": message} for user_id in operation_ids)
        except Exception as exc:
            logger.exception("Bulk application decision failed for batch")
            results["errors"].extend({"id": user_id, "error": str(exc)} for user_id in operation_ids)
    results["processed"] = len(results["success"])
    results["stale_count"] = len(results["stale"])
    results["failed"] = len(results["errors"])
    results["notification_failed_count"] = len(results["notification_failures"])
    results["requested"] = requested
    return JsonResponse(
        results,
        status=207
        if results["errors"] or results["stale"] or results["notification_failures"]
        else 200,
    )

@require_POST
@capability_required(can_manage_content)
def duplicate_lesson(request, lesson_id):
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    offering_id = request.POST.get("course_offering_id")
    target_offering = get_object_or_404(CourseOffering, pk=offering_id) if offering_id else lesson.course_offering
    with transaction.atomic():
        lesson.pk = None
        lesson.course_offering = target_offering
        lesson.status = PublicationStatus.DRAFT
        lesson.created_date = date.today()
        lesson.save()
    messages.success(request, _("Lesson duplicated."))
    return redirect("lesson-dashboard")

@require_POST
@capability_required(can_manage_content)
def duplicate_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    offering_id = request.POST.get("course_offering_id")
    target_offering = get_object_or_404(CourseOffering, pk=offering_id) if offering_id else quiz.course_offering
    with transaction.atomic():
        questions = list(quiz.questions.all())
        quiz.pk = None
        quiz.course_offering = target_offering
        quiz.status = PublicationStatus.DRAFT
        quiz.save()
        for q in questions:
            q.pk = None
            q.quiz = quiz
        Question.objects.bulk_create(questions)
    messages.success(request, _("Quiz duplicated."))
    return redirect("quiz-dashboard")

@require_POST
@capability_required(can_manage_academic_setup)
def copy_course_offerings(request):
    form = OfferingCopyForm(request.POST)
    if form.is_valid():
        source_scope = form.cleaned_data["source_year_level"]
        target_scope = form.cleaned_data["target_year_level"]
        offerings = list(form.cleaned_data["offerings"])
        offering_ids = sorted(offering.pk for offering in offerings)
        # Bounded pre-enqueue conflict check: the executor rechecks the whole
        # all-or-nothing copy unit before mutating.
        conflict = CourseOffering.objects.filter(
            academic_year_level=target_scope,
            course_id__in=[offering.course_id for offering in offerings],
        ).exists()
        if conflict:
            messages.error(request, _("The target scope already contains one or more selected courses."))
            return redirect(f"{reverse('academic-setup')}?scope={target_scope.pk}")

        try:
            job = create_admin_operation(
                actor=request.user,
                kind=AdminOperationJob.Kind.COURSE_COPY,
                input={
                    "mode": "offerings",
                    "source_scope_id": source_scope.pk,
                    "target_scope_id": target_scope.pk,
                    "offering_ids": offering_ids,
                    "copy_lessons": bool(form.cleaned_data["copy_lessons"]),
                    "copy_quizzes": bool(form.cleaned_data["copy_quizzes"]),
                },
                conflict_key=f"course-copy:{source_scope.pk}:{target_scope.pk}",
                scope=target_scope,
                language=translation.get_language() or "ar",
                total=len(offering_ids),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(f"{reverse('academic-setup')}?scope={target_scope.pk}")
        try:
            enqueue_admin_operation(job.pk, queue="admin")
        except Exception:
            # The queued row is picked up by the recovery schedule.
            logger.warning("course offering copy awaits worker recovery", exc_info=True)
        messages.success(
            request,
            _("Copying %(count)d offering(s) as drafts was queued. Progress is available while it runs; the copy commits all offerings together.")
            % {"count": len(offering_ids)},
        )
    else:
        for error in form.non_field_errors():
            messages.error(request, error)
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    target = request.POST.get("target_year_level")
    return redirect(f"{reverse('academic-setup')}?scope={target}" if target else "academic-setup")

@capability_required(can_manage_content)
def levels_dashboard(request):
    levels = Level.objects.order_by("ordering").annotate(
        year_count=Count("year_links__academic_year", distinct=True),
        offering_count=Count("year_links__course_offerings", distinct=True),
        course_count=Count("courses", distinct=True),
        enrollment_count=Count("year_links__enrollments", distinct=True),
    )
    level_list = []
    for entry in levels:
        level_list.append({
            "level": entry.ordering,
            "name": entry.display_name,
            "name_en": entry.name_en,
            "name_ar": entry.name_ar,
            "year_count": entry.year_count,
            "offering_count": entry.offering_count,
            "course_count": entry.course_count,
            "enrollment_count": entry.enrollment_count,
        })
    return render_page(request, "levels.html", "partials/levels_content.html", {
        "levels": level_list,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Academic Levels"), None),
        ]),
    })

@require_POST
@capability_required(can_manage_academic_setup)
def level_create(request):
    try:
        ordering = int(request.POST.get("level", 0))
    except (TypeError, ValueError):
        ordering = 0
    name_en = request.POST.get("name_en", "").strip()
    name_ar = request.POST.get("name_ar", "").strip()
    if ordering < 1:
        messages.error(request, _("Invalid level ordering."))
        return redirect("levels-dashboard")
    if Level.objects.filter(ordering=ordering).exists():
        messages.error(request, _("Level %(ordering)d already exists.") % {"ordering": ordering})
        return redirect("levels-dashboard")
    Level.objects.create(ordering=ordering, name_en=name_en, name_ar=name_ar)
    messages.success(request, _("Level %(ordering)d created.") % {"ordering": ordering})
    return redirect("levels-dashboard")

@require_POST
@capability_required(can_manage_academic_setup)
def level_edit(request, level):
    try:
        entry = Level.objects.get(ordering=level)
    except Level.DoesNotExist:
        messages.error(request, _("Level %(level)d not found.") % {"level": level})
        return redirect("levels-dashboard")
    entry.name_en = request.POST.get("name_en", "").strip()
    entry.name_ar = request.POST.get("name_ar", "").strip()
    entry.save()
    messages.success(request, _("Level %(level)d updated.") % {"level": level})
    return redirect("levels-dashboard")

@require_POST
@capability_required(can_manage_academic_setup)
def level_delete(request, level):
    try:
        level_obj = Level.objects.get(ordering=level)
    except Level.DoesNotExist:
        messages.error(request, _("Level not found."))
        return redirect("levels-dashboard")
    has_academic_years = level_obj.academic_years.exists()
    has_courses = level_obj.courses.exists()
    if has_academic_years:
        messages.error(
            request,
            _("Cannot delete level %(level)d: it has academic years.") % {"level": level},
        )
    elif has_courses:
        messages.error(
            request,
            _("Cannot delete level %(level)d: it has courses.") % {"level": level},
        )
    else:
        level_obj.delete()
        messages.success(request, _("Level %(level)d deleted.") % {"level": level})
    return redirect("levels-dashboard")

@capability_required(can_manage_academic_setup)
def academic_setup(request):
    search = request.GET.get("q", "").strip()
    active_filter = request.GET.get("active", "all")
    years = AcademicYear.objects.all().prefetch_related("levels").order_by("ordering")
    if search:
        years = years.filter(normalized_contains_q(("name",), search))
    if active_filter == "active":
        years = years.filter(is_active=True)
    elif active_filter == "inactive":
        years = years.filter(is_active=False)
    else:
        active_filter = "all"
    year_page_obj = Paginator(years, 10).get_page(request.GET.get("page", 1))
    course_levels = list(Level.objects.order_by("ordering"))
    for year in year_page_obj.object_list:
        year.edit_form = AcademicYearForm(
            instance=year,
            auto_id=f"edit-year-{year.pk}-%s",
            level_choices=course_levels,
        )

    selected_scope_id = request.GET.get("scope")
    selected_year_id = request.GET.get("academic_year")
    selected_scope = None
    if selected_scope_id:
        selected_scope = get_object_or_404(
            AcademicYearLevel.objects.select_related("academic_year", "level"),
            pk=selected_scope_id,
            **({"academic_year_id": selected_year_id} if selected_year_id else {}),
        )
    selected_year = selected_year_id
    if selected_scope is None and selected_year:
        selected_year = get_object_or_404(AcademicYear, pk=selected_year)
    elif selected_scope is not None:
        selected_year = selected_scope.academic_year

    year_level_links = AcademicYearLevel.objects.none()
    if selected_year:
        year_level_links = AcademicYearLevel.objects.filter(academic_year=selected_year).select_related("level").order_by("level__ordering")

    offering_search = normalize_search_text(request.GET.get("offering_q", "").strip()[:100])
    offering_status = request.GET.get("offering_status", "all")
    offerings = CourseOffering.objects.none()
    if selected_scope:
        offerings = CourseOffering.objects.filter(academic_year_level=selected_scope).select_related("course", "academic_year_level__academic_year", "academic_year_level__level")
        if offering_search:
            offerings = offerings.filter(
                normalized_contains_q(("course__name", "instructor"), offering_search)
            )
        if offering_status in dict(PublicationStatus.choices):
            offerings = offerings.filter(status=offering_status)
        else:
            offering_status = "all"
    offering_page_obj = Paginator(offerings.order_by("course__name", "pk"), 15).get_page(request.GET.get("offering_page", 1))
    scope_courses = Course.objects.filter(level=selected_scope.level).order_by("name") if selected_scope else Course.objects.none()
    year_form = AcademicYearForm(level_choices=course_levels)
    offering_form = CourseOfferingForm()
    copy_form = OfferingCopyForm(initial={
        "target_year_level": selected_scope.pk
        if selected_scope and selected_scope.academic_year.is_active
        else None,
    })
    year_pagination_params = request.GET.copy()
    year_pagination_params.pop("page", None)
    year_pagination_params.pop("offering_page", None)
    offering_pagination_params = request.GET.copy()
    offering_pagination_params.pop("page", None)
    offering_pagination_params.pop("offering_page", None)
    copy_job = None
    if can_manage_academic_setup(request.user):
        copy_job = (
            AdminOperationJob.objects.filter(
                actor=request.user,
                kind=AdminOperationJob.Kind.COURSE_COPY,
            )
            .order_by("-created_at")
            .first()
        )
    return render_page(request, "academic_setup.html", "partials/academic_setup_content.html", {
        "years": year_page_obj.object_list,
        "year_page_obj": year_page_obj,
        "year_level_links": year_level_links,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Academic Setup"), None),
        ]),
        "selected_year": selected_year.pk if selected_year else None,
        "selected_scope": selected_scope,
        "selected_scope_id": selected_scope.pk if selected_scope else None,
        "offerings": offering_page_obj.object_list,
        "offering_page_obj": offering_page_obj,
        "scope_courses": scope_courses,
        "search": search,
        "active_filter": active_filter,
        "offering_search": offering_search,
        "offering_status": offering_status,
        "year_pagination_query": year_pagination_params.urlencode(),
        "offering_pagination_query": offering_pagination_params.urlencode(),
        "year_form": year_form,
        "offering_form": offering_form,
        "copy_form": copy_form,
        "copy_job": copy_job,
        "copy_status_url": (
            reverse("admin-operation-status", args=[copy_job.pk]) if copy_job else ""
        ),
        "COURSE_LEVELS": course_levels,
        "meeting_weekday_choices": [(0, _("Monday")), (1, _("Tuesday")), (2, _("Wednesday")), (3, _("Thursday")), (4, _("Friday")), (5, _("Saturday")), (6, _("Sunday"))],
    })


@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_level_weekdays(request, scope_id):
    scope = get_object_or_404(AcademicYearLevel, pk=scope_id)
    form = AcademicYearLevelWeekdayForm(request.POST, instance=scope)
    if form.is_valid():
        form.save()
        messages.success(request, _("Meeting weekdays updated."))
    else:
        for error in form.errors.values():
            for message in error:
                messages.error(request, message)
    return redirect(f"{reverse('academic-setup')}?scope={scope.pk}")


@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_level_delete(request, scope_id):
    scope = get_object_or_404(AcademicYearLevel, pk=scope_id)
    year_id = scope.academic_year_id
    try:
        scope.delete()
        messages.success(request, _("Academic level removed from the year."))
    except ProtectedError:
        messages.error(request, _("Cannot remove a level referenced by academic records."))
    return redirect(f"{reverse('academic-setup')}?academic_year={year_id}")


@capability_required(can_manage_academic_setup)
def academic_offerings_by_scope(request, scope_id):
    scope = get_object_or_404(AcademicYearLevel, pk=scope_id)
    offerings = CourseOffering.objects.filter(academic_year_level=scope).select_related("course").order_by("course__name")
    return JsonResponse({
        "offerings": [{"id": offering.pk, "name": offering.course.name} for offering in offerings]
    })

@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_create(request):
    if request.method == "POST":
        form = AcademicYearForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                form.save()
            messages.success(request, _("Academic year created."))
        else:
            for err in form.errors.get("__all__", []):
                messages.error(request, err)
            for field in form.errors:
                if field == "__all__":
                    continue
                label = form.fields[field].label if field in form.fields else field
                for err in form.errors[field]:
                    messages.error(request, _("%(label)s: %(error)s") % {"label": label, "error": err})
        return redirect("academic-setup")

@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_edit(request, year_id):
    year = get_object_or_404(AcademicYear, pk=year_id)
    if request.method == "POST":
        form = AcademicYearForm(request.POST, instance=year)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
            except ProtectedError:
                messages.error(request, _("Cannot remove a level referenced by academic records."))
            else:
                messages.success(request, _("Academic year updated."))
        else:
            for err in form.errors.get("__all__", []):
                messages.error(request, err)
            for field in form.errors:
                if field == "__all__":
                    continue
                label = form.fields[field].label if field in form.fields else field
                for err in form.errors[field]:
                    messages.error(request, _("%(label)s: %(error)s") % {"label": label, "error": err})
        return redirect(f"{reverse('academic-setup')}?academic_year={year.pk}")


@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_activate(request, year_id):
    year = get_object_or_404(AcademicYear, pk=year_id)
    try:
        activate_academic_year(year, request.user)
        messages.success(request, _("Academic year activated."))
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
    return redirect("academic-setup")

@require_POST
@capability_required(can_manage_academic_setup)
def academic_year_delete(request, year_id):
    year = get_object_or_404(AcademicYear, pk=year_id)
    enrollments_count = Enrollment.objects.filter(academic_year_level__academic_year=year).count()
    if year.is_active:
        messages.error(request, _("Cannot delete the active academic year."))
    elif CourseOffering.objects.filter(academic_year_level__academic_year=year).exists():
        messages.error(request, _("Cannot delete a year that has course offerings."))
    elif enrollments_count:
        messages.error(request, _("Cannot delete a year that has %(count)d enrollment(s).") % {"count": enrollments_count})
    else:
        try:
            year.delete()
            messages.success(request, _("Academic year deleted."))
        except ProtectedError:
            messages.error(request, _("Cannot delete an academic year referenced by historical records."))
    return redirect("academic-setup")

@require_POST
@capability_required(can_manage_academic_setup)
def course_offering_create(request):
    if request.method == "POST":
        scope_id = request.POST.get("academic_year_level")
        form = CourseOfferingForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Course offering created."))
        else:
            for err in form.errors.get("__all__", []):
                messages.error(request, err)
            for field in form.errors:
                if field == "__all__":
                    continue
                label = form.fields[field].label if field in form.fields else field
                for err in form.errors[field]:
                    messages.error(request, _("%(label)s: %(error)s") % {"label": label, "error": err})
        return redirect(f"{reverse('academic-setup')}?scope={scope_id}" if scope_id else "academic-setup")

@require_POST
@capability_required(can_manage_academic_setup)
def course_offering_edit(request, offering_id):
    offering = get_object_or_404(CourseOffering, pk=offering_id)
    if request.method == "POST":
        # The offering scope is immutable through this edit path and is not
        # rendered as an editable field in the modal. Bind it server-side so
        # the required model form field remains validated without trusting a
        # client-supplied scope.
        post_data = request.POST.copy()
        post_data["academic_year_level"] = str(offering.academic_year_level_id)
        form = CourseOfferingForm(post_data, instance=offering)
        if form.is_valid():
            form.save()
            messages.success(request, _("Course offering updated."))
        else:
            for err in form.errors.get("__all__", []):
                messages.error(request, err)
            for field in form.errors:
                if field == "__all__":
                    continue
                label = form.fields[field].label if field in form.fields else field
                for err in form.errors[field]:
                    messages.error(request, _("%(label)s: %(error)s") % {"label": label, "error": err})
        return redirect(f"{reverse('academic-setup')}?scope={offering.academic_year_level_id}")

@require_POST
@capability_required(can_manage_academic_setup)
def course_offering_delete(request, offering_id):
    offering = get_object_or_404(CourseOffering, pk=offering_id)
    try:
        offering.delete()
        messages.success(request, _("Course offering deleted."))
    except ProtectedError as e:
        messages.error(request, _("Cannot delete this course offering: it is referenced by other records (%s).") % str(e))
    return redirect(f"{reverse('academic-setup')}?scope={offering.academic_year_level_id}")

_COPY_FLAG_TRUE = frozenset({"1", "true", "yes", "on"})
_COPY_FLAG_FALSE = frozenset({"0", "false", "no", "off"})


def _post_copy_flag(value, *, default: bool = True) -> bool:
    """Parse one explicit copy flag; an unknown value is not silently accepted."""

    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in _COPY_FLAG_TRUE:
        return True
    if normalized in _COPY_FLAG_FALSE:
        return False
    raise ValueError("invalid copy flag")


@require_POST
@capability_required(can_manage_content)
def duplicate_course(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    new_name = (request.POST.get("new_name") or "").strip()
    if not new_name:
        return HttpResponse(_("New course name is required."), status=400)
    source_offering_id = request.POST.get("source_offering_id", "")
    if not source_offering_id.isdigit():
        return HttpResponse(_("Select the source offering to duplicate."), status=400)
    source_offering = CourseOffering.objects.filter(
        pk=int(source_offering_id),
        course=course,
    ).first()
    if source_offering is None:
        return HttpResponse(_("The source offering does not belong to this course."), status=400)
    target_scope_id = request.POST.get("academic_year_level_id", "")
    if not target_scope_id.isdigit():
        return HttpResponse(_("Select a valid target academic scope."), status=400)
    target_scope = AcademicYearLevel.objects.filter(pk=int(target_scope_id)).first()
    if target_scope is None:
        return HttpResponse(_("The selected academic scope is not available."), status=400)
    try:
        copy_lessons = _post_copy_flag(request.POST.get("copy_lessons"))
        copy_quizzes = _post_copy_flag(request.POST.get("copy_quizzes"))
    except ValueError:
        return HttpResponse(_("Invalid copy option."), status=400)
    if not copy_lessons and not copy_quizzes:
        return HttpResponse(_("Select lessons, quizzes, or both to copy."), status=400)
    if source_offering.course.level_id != target_scope.level_id:
        return HttpResponse(_("The source offering level does not match the target academic scope."), status=400)


    try:
        job = create_admin_operation(
            actor=request.user,
            kind=AdminOperationJob.Kind.COURSE_COPY,
            input={
                "mode": "duplicate",
                "source_offering_id": source_offering.pk,
                "target_scope_id": target_scope.pk,
                "new_course_name": new_name[:255],
                "copy_lessons": copy_lessons,
                "copy_quizzes": copy_quizzes,
            },
            conflict_key=f"course-copy:duplicate:{target_scope.pk}:{source_offering.pk}",
            scope=target_scope,
            language=translation.get_language() or "ar",
            total=1,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(f"{reverse('academic-setup')}?scope={target_scope.pk}")
    try:
        enqueue_admin_operation(job.pk, queue="admin")
    except Exception:
        # The queued row is picked up by the recovery schedule.
        logger.warning("course duplication awaits worker recovery", exc_info=True)
    messages.success(
        request,
        _("Course duplication was queued. The source offering content is copied as drafts and progress is available while it runs."),
    )
    return redirect(f"{reverse('academic-setup')}?scope={target_scope.pk}")

# ============================================================================
# PHASE 5 - Attendance Calendar, QR, and Scanning
# ============================================================================

def _calendar_weekday_labels():
    return [
        _("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"),
        _("Friday"), _("Saturday"), _("Sunday"),
    ]


def _calendar_weekday_abbreviations():
    """Locale-aware Mon..Sun labels for the shared month-grid weekday header."""
    return [formats.date_format(date(2000, 1, day), "D") for day in range(3, 10)]


def _calendar_meeting_row(meeting):
    return {
        "id": meeting.pk,
        "meeting_date": meeting.meeting_date,
        "weekday_name": formats.date_format(meeting.meeting_date, "l"),
        "level_name": meeting.academic_year_level.level.display_name,
        "course_name": meeting.course_offering.course.name,
    }


def _resolve_calendar_month(year, request):
    """Parse and clamp the requested display month within an academic year.

    The canonical query shape is ``month=YYYY-M``; the optional ``year``
    parameter is honored for deep links. Returns
    ``(cur_year, cur_month, prev_query, next_query)`` where the query
    fragments are canonical ``month=YYYY-M`` strings, or None at the edges.
    """
    year_start, year_end = year.starts_on, year.ends_on
    today = timezone.localdate()
    if year_start <= today <= year_end:
        cur_month, cur_year = today.month, today.year
    else:
        cur_month, cur_year = year_start.month, year_start.year
    year_param = request.GET.get("year")
    month_param = request.GET.get("month")
    if year_param:
        try:
            cur_year = int(year_param)
        except (ValueError, TypeError):
            pass
    if month_param:
        try:
            cur_month = int(month_param)
        except (ValueError, TypeError):
            try:
                cur_year, cur_month = [int(x) for x in month_param.split("-")]
            except (ValueError, IndexError):
                pass
    if cur_month < 1 or cur_month > 12:
        cur_month = 1

    first_month = year_start.year * 12 + year_start.month
    last_month = year_end.year * 12 + year_end.month
    current_cell = cur_year * 12 + cur_month
    if current_cell < first_month:
        cur_year = year_start.year
        cur_month = year_start.month
        current_cell = cur_year * 12 + cur_month
    elif current_cell > last_month:
        cur_year = year_end.year
        cur_month = year_end.month
        current_cell = cur_year * 12 + cur_month

    prev_month = None
    next_month = None
    if current_cell > first_month:
        pm = current_cell - 1
        prev_month = f"month={((pm - 1) // 12)}-{((pm - 1) % 12) + 1}"
    if current_cell < last_month:
        nm = current_cell + 1
        next_month = f"month={((nm - 1) // 12)}-{((nm - 1) % 12) + 1}"
    return cur_year, cur_month, prev_month, next_month


def _calendar_month_options(year, cur_year, cur_month):
    """Valid month choices for one academic year as canonical YYYY-M values."""
    first_cell = year.starts_on.year * 12 + year.starts_on.month
    last_cell = year.ends_on.year * 12 + year.ends_on.month
    options = []
    for cell in range(first_cell, last_cell + 1):
        cell_year, cell_month = divmod(cell - 1, 12)
        cell_month += 1
        options.append({
            "value": f"{cell_year}-{cell_month}",
            "label": formats.date_format(date(cell_year, cell_month, 1), "F Y"),
            "is_current": cell_year == cur_year and cell_month == cur_month,
        })
    return options


def _calendar_today_query(year):
    """Canonical month fragment for today's month, or None when out of year."""
    today = timezone.localdate()
    if year.starts_on <= today <= year.ends_on:
        return f"month={today.year}-{today.month}"
    return None


@capability_required(can_manage_content)
def calendar_management(request):
    years = AcademicYear.objects.all().order_by("-starts_on")
    selected_year_id = request.GET.get("academic_year")
    if selected_year_id and not selected_year_id.isdigit():
        selected_year_id = None
    if not selected_year_id:
        default_year = years.filter(is_active=True).first() or years.first()
        if default_year is not None:
            params = request.GET.copy()
            params["academic_year"] = default_year.pk
            return redirect(f"{reverse('calendar-management')}?{params.urlencode()}")
    year = get_object_or_404(AcademicYear, pk=selected_year_id) if selected_year_id else None

    today = timezone.localdate()
    year_id = None
    month_grid = None
    prev_month = None
    next_month = None
    month_options = []
    today_query = None
    cur_year = today.year
    cur_month = today.month
    year_weekday_names = []
    year_scope_count = 0
    year_offering_count = 0
    month_meeting_count = 0
    month_course_count = 0
    month_holiday_count = 0

    if year:
        year_id = year.pk
        cur_year, cur_month, prev_month, next_month = _resolve_calendar_month(year, request)
        month_options = _calendar_month_options(year, cur_year, cur_month)
        today_query = _calendar_today_query(year)

        holidays = {
            holiday.date: {"id": holiday.pk, "name": holiday.name}
            for holiday in AcademicHoliday.objects.filter(
                academic_year=year,
                date__year=cur_year,
                date__month=cur_month,
            )
        }
        scope_links = list(year.level_links.select_related("level").order_by("level__ordering"))
        weekday_labels = _calendar_weekday_labels()
        weekday_numbers = sorted({
            weekday
            for scope in scope_links
            for weekday in (scope.meeting_weekdays or [])
            if 0 <= weekday <= 6
        })
        year_weekday_names = [weekday_labels[weekday] for weekday in weekday_numbers]
        year_scope_count = len(scope_links)

        meetings = list(
            AcademicYearLevelMeeting.objects.filter(
                academic_year_level__academic_year=year,
                meeting_date__year=cur_year,
                meeting_date__month=cur_month,
            ).select_related(
                "academic_year_level__level",
                "course_offering__course",
            ).order_by("meeting_date", "academic_year_level__level__ordering", "course_offering__course__name")
        )
        meetings_by_date = defaultdict(list)
        for meeting in meetings:
            meetings_by_date[meeting.meeting_date].append(_calendar_meeting_row(meeting))
        meeting_offerings = list(CourseOffering.objects.filter(
            academic_year_level__academic_year=year,
        ).select_related(
            "course",
            "academic_year_level__level",
        ).order_by("academic_year_level__level__ordering", "course__name", "pk"))
        year_offering_count = len(meeting_offerings)
        offerings_by_weekday = defaultdict(list)
        for offering in meeting_offerings:
            for weekday in offering.academic_year_level.meeting_weekdays or []:
                offerings_by_weekday[weekday].append(offering)

        month_grid = _build_month_grid(
            year_start=year.starts_on,
            year_end=year.ends_on,
            cur_year=cur_year,
            cur_month=cur_month,
            today=today,
            holidays=holidays,
            meetings_by_date=meetings_by_date,
            locked_weekdays=set(weekday_numbers),
            meeting_offerings_by_weekday=offerings_by_weekday,
        )
        month_meeting_count = len(meetings)
        month_course_count = len({meeting.course_offering_id for meeting in meetings})
        month_holiday_count = len(holidays)

    return render_page(request, "calendar_management.html", "partials/calendar_management_content.html", {
        "years": years,
        "year": year,
        "year_id": year_id,
        "year_weekday_names": year_weekday_names,
        "year_scope_count": year_scope_count,
        "year_offering_count": year_offering_count,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Calendar"), None),
        ]),
        "weekday_names": _calendar_weekday_abbreviations(),
        "month_grid": month_grid,
        "month_agenda": _build_month_agenda(month_grid),
        "month_options": month_options,
        "prev_month": prev_month,
        "next_month": next_month,
        "today_query": today_query,
        "cur_month": cur_month,
        "month_meeting_count": month_meeting_count,
        "month_course_count": month_course_count,
        "month_holiday_count": month_holiday_count,
        "can_edit_meetings": can_manage_academic_setup(request.user),
        "can_manage_holidays": can_manage_content(request.user),
    })


MAX_MEETINGS_PER_REQUEST = 20


@require_POST
@capability_required(can_manage_academic_setup)
def add_meeting(request):
    year_id = request.POST.get("academic_year_id")
    date_value = request.POST.get("date", "")
    offering_ids = [value for value in request.POST.getlist("course_offering_id") if value]
    year = AcademicYear.objects.filter(pk=year_id).first() if year_id else None
    if not year:
        messages.error(request, _("Invalid academic year."))
        return redirect("calendar-management")
    try:
        meeting_date = date.fromisoformat(date_value)
    except (TypeError, ValueError):
        messages.error(request, _("Invalid date format."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}")
    if not year.starts_on <= meeting_date <= year.ends_on:
        messages.error(request, _("Date is outside the academic year range."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}")
    month_query = f"month={meeting_date.year}-{meeting_date.month}"
    if not offering_ids:
        messages.error(request, _("Select at least one course offering."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}&{month_query}")
    if len(offering_ids) > MAX_MEETINGS_PER_REQUEST:
        messages.error(
            request,
            _("Too many offerings selected at once (maximum %(max)s).") % {"max": MAX_MEETINGS_PER_REQUEST},
        )
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}&{month_query}")

    offerings = {
        str(offering.pk): offering
        for offering in CourseOffering.objects.filter(
            pk__in=offering_ids,
            academic_year_level__academic_year=year,
        ).select_related("academic_year_level")
    }

    added_count = 0
    assigned_count = 0
    skipped_existing = 0
    skipped_invalid = 0
    with transaction.atomic():
        existing_level_ids = set(
            AcademicYearLevelMeeting.objects.filter(
                academic_year_level__academic_year=year,
                meeting_date=meeting_date,
            ).values_list("academic_year_level_id", flat=True)
        )
        for offering_id in offering_ids:
            offering = offerings.get(offering_id)
            if offering is None:
                skipped_invalid += 1
                continue
            academic_year_level = offering.academic_year_level
            if meeting_date.weekday() not in (academic_year_level.meeting_weekdays or []):
                skipped_invalid += 1
                continue
            if academic_year_level.pk in existing_level_ids:
                skipped_existing += 1
                continue
            try:
                with transaction.atomic():
                    meeting = AcademicYearLevelMeeting.objects.create(
                        academic_year_level=academic_year_level,
                        meeting_date=meeting_date,
                        course_offering=offering,
                    )
            except (IntegrityError, ValidationError):
                skipped_existing += 1
                continue
            existing_level_ids.add(academic_year_level.pk)
            added_count += 1
            assigned_count += assign_unassigned_attendance(meeting)

    if added_count:
        messages.success(request, _("Meetings added: %(count)s.") % {"count": added_count})
        if assigned_count:
            messages.success(
                request,
                _("Pending attendance records assigned: %(count)s.") % {"count": assigned_count},
            )
    if skipped_existing:
        messages.warning(
            request,
            _("Some selected offerings were skipped because their level already has a meeting on this date."),
        )
    if skipped_invalid:
        messages.warning(request, _("Some selected offerings do not meet on the selected weekday."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}&{month_query}")


@require_POST
@capability_required(can_manage_academic_setup)
def delete_meeting(request, meeting_id):
    meeting = get_object_or_404(
        AcademicYearLevelMeeting.objects.select_related("academic_year_level__academic_year"),
        pk=meeting_id,
    )
    year_id = meeting.academic_year_level.academic_year_id
    meeting_date = meeting.meeting_date
    meeting.delete()
    messages.success(request, _("Meeting deleted."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}&month={meeting_date.year}-{meeting_date.month}")

@require_POST
@capability_required(can_manage_content)
def add_holiday(request):
    year_id = request.POST.get("academic_year_id")
    date_val = request.POST.get("date")
    name = request.POST.get("name", "").strip()

    if not year_id or not date_val:
        messages.error(request, _("Missing required fields."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year_id or ''}")

    try:
        year = AcademicYear.objects.get(pk=year_id)
    except (AcademicYear.DoesNotExist, ValueError):
        messages.error(request, _("Invalid academic year."))
        return redirect("calendar-management")

    if not name:
        messages.error(request, _("Holiday name is required."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

    try:
        holiday_date = datetime.strptime(date_val, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        messages.error(request, _("Invalid date format."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

    if holiday_date < year.starts_on or holiday_date > year.ends_on:
        messages.error(request, _("Date is outside the academic year range."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

    if AcademicHoliday.objects.filter(academic_year=year, date=holiday_date).exists():
        messages.error(request, _("A holiday already exists on this date."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

    AcademicHoliday.objects.create(academic_year=year, date=holiday_date, name=name)
    messages.success(request, _("Holiday added."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}&month={holiday_date.year}-{holiday_date.month}")

@require_POST
@capability_required(can_manage_content)
def delete_holiday(request, holiday_id):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id)
    year_id = holiday.academic_year_id
    holiday_date = holiday.date
    holiday.delete()
    messages.success(request, _("Holiday deleted."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}&month={holiday_date.year}-{holiday_date.month}")

@login_required
def student_calendar(request):
    if resolve_student_mode(request) == StudentMode.NEW:
        return render_student_page(
            request,
            page="calendar",
            template="student_next/calendar.html",
            context=build_calendar_context(request),
        )
    enrollments = list(
        Enrollment.objects.filter(
            student=request.user,
            status__in=["active", "completed"],
            enrollment_type="normal",
        ).select_related(
            "academic_year_level__academic_year",
            "academic_year_level__level",
        ).order_by(
            "-academic_year_level__academic_year__ordering",
            "-enrolled_at",
        )
    )
    selected_enrollment = None
    scope_param = request.GET.get("scope")
    if scope_param and scope_param.isdigit():
        selected_enrollment = next(
            (enrollment for enrollment in enrollments if enrollment.pk == int(scope_param)),
            None,
        )
    if selected_enrollment is None:
        active_year = AcademicYear.objects.filter(is_active=True).first()
        if active_year is not None:
            selected_enrollment = next(
                (e for e in enrollments if e.academic_year_level.academic_year_id == active_year.pk),
                None,
            )
        if selected_enrollment is None and enrollments:
            selected_enrollment = enrollments[0]

    scope_options = [
        {
            "value": enrollment.pk,
            "label": (
                f"{enrollment.academic_year_level.academic_year.name} - "
                f"{enrollment.academic_year_level.level.display_name}"
            ),
            "is_current": selected_enrollment is not None and enrollment.pk == selected_enrollment.pk,
        }
        for enrollment in enrollments
    ]

    scopes = []
    month_grid = None
    prev_month = None
    next_month = None
    month_options = []
    today_query = None
    cur_year = None
    cur_month = None
    month_meeting_count = 0
    month_course_count = 0
    month_holiday_count = 0

    if selected_enrollment:
        scope = selected_enrollment.academic_year_level
        weekday_labels = _calendar_weekday_labels()
        scope.locked_meeting_weekdays = [
            weekday_labels[weekday]
            for weekday in sorted(scope.meeting_weekdays or [])
            if 0 <= weekday < 7
        ]
        scope.calendar_meeting_rows = [
            _calendar_meeting_row(meeting)
            for meeting in AcademicYearLevelMeeting.objects.filter(
                academic_year_level=scope,
            ).select_related(
                "academic_year_level__level",
                "course_offering__course",
            )
        ]
        scope.calendar_holidays = list(scope.academic_year.holidays.order_by("date"))
        scopes.append(scope)

        year = scope.academic_year
        today = timezone.localdate()
        cur_year, cur_month, prev_month, next_month = _resolve_calendar_month(year, request)
        month_options = _calendar_month_options(year, cur_year, cur_month)
        today_query = _calendar_today_query(year)
        holidays = {
            holiday.date: {"id": holiday.pk, "name": holiday.name}
            for holiday in year.holidays.filter(
                date__year=cur_year,
                date__month=cur_month,
            )
        }
        month_meetings = list(
            AcademicYearLevelMeeting.objects.filter(
                academic_year_level=scope,
                meeting_date__year=cur_year,
                meeting_date__month=cur_month,
            ).select_related(
                "academic_year_level__level",
                "course_offering__course",
            ).order_by("meeting_date", "course_offering__course__name")
        )
        meetings_by_date = defaultdict(list)
        for meeting in month_meetings:
            meetings_by_date[meeting.meeting_date].append(_calendar_meeting_row(meeting))
        month_grid = _build_month_grid(
            year_start=year.starts_on,
            year_end=year.ends_on,
            cur_year=cur_year,
            cur_month=cur_month,
            today=today,
            holidays=holidays,
            meetings_by_date=meetings_by_date,
            locked_weekdays=set(scope.meeting_weekdays or []),
        )
        month_meeting_count = len(month_meetings)
        month_course_count = len({meeting.course_offering_id for meeting in month_meetings})
        month_holiday_count = len(holidays)

    return render(request, "student_calendar.html", {
        "scopes": scopes,
        "scope_id": selected_enrollment.pk if selected_enrollment else None,
        "scope_options": scope_options,
        "weekday_names": _calendar_weekday_abbreviations(),
        "month_grid": month_grid,
        "month_agenda": _build_month_agenda(month_grid),
        "month_options": month_options,
        "prev_month": prev_month,
        "next_month": next_month,
        "today_query": today_query,
        "cur_month": cur_month,
        "month_meeting_count": month_meeting_count,
        "month_course_count": month_course_count,
        "month_holiday_count": month_holiday_count,
    })

@login_required
def download_qr(request):
    if not request.user.qr_token:
        request.user.qr_token = secrets.token_urlsafe(32)
        request.user.save(update_fields=["qr_token"])
    qr_data = build_scan_url(request, request.user.qr_token)
    img = qrcode.make(qr_data)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    response = HttpResponse(buf, content_type="image/png")
    if request.GET.get("download") == "1":
        display_name = re.sub(
            r"[\x00-\x1f\x7f/\\]+",
            " ",
            request.user.get_full_name().strip() or request.user.username,
        ).strip() or "Profile"
        filename = f"{display_name} - {request.user.pk}.png"
        response["Content-Disposition"] = (
            f'attachment; filename="profile-qr-{request.user.pk}.png"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
    return response

@require_POST
@capability_required(can_correct_attendance)
def regenerate_qr(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    user.qr_token = secrets.token_urlsafe(32)
    user.save(update_fields=["qr_token"])
    messages.success(request, _("QR token regenerated."))
    return redirect("application-review", user_id=user.id)

@capability_required(can_scan_attendance)
def scanner(request):
    # Bounded recent-activity feed so the scanner page has real "last scan"
    # context instead of static copy. Six rows, one query.
    recent_scans = list(
        AttendanceRecord.objects.select_related("student")
        .order_by("-scanned_at", "-pk")[:6]
    )
    return render_page(
        request,
        "scanner.html",
        "partials/scanner_content.html",
        {
            "recent_scans": recent_scans,
            "last_scan": recent_scans[0] if recent_scans else None,
        },
    )

@capability_required(can_scan_attendance)
def student_lookup(request):
    if request.method != "POST":
        return JsonResponse({"error": _("Method not allowed")}, status=405)

    # The scanner posts this form with fetch so it can record immediately and
    # keep the camera open; a plain form post keeps the redirect fallback.
    wants_json = (
        request.headers.get("X-Requested-With") == "fetch"
        or "application/json" in request.headers.get("Accept", "")
    )

    query = request.POST.get("query", "").strip()
    if not query:
        return JsonResponse({"error": _("Query is required.")}, status=400)

    user = None
    if query.isdigit():
        try:
            user = User.objects.get(pk=int(query))
        except User.DoesNotExist:
            pass

    if not user:
        try:
            user = User.objects.get(username=normalize_username(query))
        except User.DoesNotExist:
            pass

    if not user or (user.role and user.role.role != "student"):
        if wants_json:
            return JsonResponse({"error": _("Student not found.")}, status=404)
        messages.error(request, _("Student not found."))
        return redirect("scanner")

    if wants_json:
        return JsonResponse({
            "token": user.qr_token or str(user.pk),
            "student": {
                "name": user.get_full_name() or user.username,
                "username": user.username,
            },
        })

    return redirect("scan-preview", token=user.qr_token or user.id)

@capability_required(can_scan_attendance)
def scan_preview(request, token):
    if not token:
        return HttpResponse(_("Token or ID required."), status=400)
    user = User.objects.filter(qr_token=token).first()
    if not user:
        try:
            user = get_object_or_404(User, pk=int(token))
        except (ValueError, TypeError):
            raise Http404
    today = timezone.localdate()
    scan_availability = attendance_scan_availability(user, today)
    academic_year_level = scan_availability.academic_year_level
    scheduled_offering = scan_availability.course_offering
    academic_year = academic_year_level.academic_year if academic_year_level else None
    return render(request, "scan_preview.html", {
        "student": user,
        "academic_year": academic_year,
        "scheduled_offering": scheduled_offering,
        "today": today,
        "can_record": scan_availability.can_record,
        "entrance_reason": scan_availability.reason_for(AttendanceRecord.Action.ENTRANCE),
        "exit_reason": scan_availability.reason_for(AttendanceRecord.Action.EXIT),
        "token": token,
        "policy": AttendancePolicy.load(),
    })

@require_POST
@capability_required(can_scan_attendance)
def record_attendance(request, token, action):
    if action not in ("entrance", "exit"):
        return JsonResponse({"error": _("Invalid action.")}, status=400)
    try:
        user = User.objects.get(Q(qr_token=token) | Q(pk=token))
    except (ValueError, TypeError):
        user = get_object_or_404(User, qr_token=token)
    # The scanner keeps the camera running and prints the response in place, so
    # every answer names the student it resolved instead of only an action code.
    student_info = {
        "name": user.get_full_name() or user.username,
        "username": user.username,
    }
    today = timezone.localdate()
    scan_availability = attendance_scan_availability(user, today, include_recorded_actions=False)
    course_name = (
        scan_availability.course_offering.course.name
        if scan_availability.course_offering is not None
        else None
    )
    reason = scan_availability.reason_for(action)
    if reason:
        already_recorded = action in scan_availability.recorded_actions
        return JsonResponse(
            {
                "error": reason,
                "code": "already_recorded" if already_recorded else "attendance_unavailable",
                "action": action,
                "student": student_info,
                "course": course_name,
            },
            status=409 if already_recorded else 400,
        )
    with transaction.atomic():
        rec, created = AttendanceRecord.objects.get_or_create(
            student=user,
            course_offering=scan_availability.course_offering,
            attendance_date=today,
            action=action,
            defaults={"scanned_by": request.user, "source": AttendanceRecord.Source.SCAN},
        )
    if not created:
        return JsonResponse(
            {
                "error": scan_availability.duplicate_reason(action),
                "code": "already_recorded",
                "action": action,
                "student": student_info,
                "course": course_name,
            },
            status=409,
        )
    return JsonResponse({
        "status": "recorded",
        "action": action,
        "source": rec.source,
        "source_label": rec.get_source_display(),
        "scanned_at": timezone.localtime(rec.scanned_at).strftime("%H:%M:%S"),
        "student": student_info,
        "course": course_name,
    })

@capability_required(can_view_reports)
def attendance_management(request):
    academic_year_id = int(request.GET["academic_year"]) if request.GET.get("academic_year", "").isdigit() else None
    level_id = int(request.GET["level"]) if request.GET.get("level", "").isdigit() else None
    years, selected_year, levels, selected_scope = grade_matrix_selection(
        academic_year_id=academic_year_id,
        level_id=level_id,
    )

    course_offerings = CourseOffering.objects.filter(
        academic_year_level_id=selected_scope.pk,
    ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year").order_by(
        "course__name", "pk"
    )
    requested_offering_id = int(request.GET["course_offering"]) if request.GET.get("course_offering", "").isdigit() else None
    selected_offering_obj = course_offerings.filter(pk=requested_offering_id).first() if requested_offering_id else None
    offering_id = selected_offering_obj.pk if selected_offering_obj else None

    date_error = False
    try:
        parsed_date_from = datetime.strptime(request.GET.get("date_from", ""), "%Y-%m-%d").date() if request.GET.get("date_from") else selected_year.starts_on
        parsed_date_to = datetime.strptime(request.GET.get("date_to", ""), "%Y-%m-%d").date() if request.GET.get("date_to") else selected_year.ends_on
    except (TypeError, ValueError):
        parsed_date_from, parsed_date_to = selected_year.starts_on, selected_year.ends_on
        date_error = True
    if parsed_date_from > parsed_date_to:
        parsed_date_from, parsed_date_to = parsed_date_to, parsed_date_from
    parsed_date_from = max(parsed_date_from, selected_year.starts_on)
    parsed_date_to = min(parsed_date_to, selected_year.ends_on)
    date_from = parsed_date_from.isoformat()
    date_to = parsed_date_to.isoformat()

    action = request.GET.get("action", "")
    if action not in {AttendanceRecord.Action.ENTRANCE, AttendanceRecord.Action.EXIT}:
        action = ""
    source = request.GET.get("source", "")
    if source not in {choice for choice, _label in AttendanceRecord.Source.choices}:
        source = ""
    scan_status = request.GET.get("scan_status", "all")
    if scan_status not in ATTENDANCE_MATRIX_STATUS_VALUES:
        scan_status = "all"
    student_search = normalize_search_text(request.GET.get("student", "").strip()[:100])
    policy = AttendancePolicy.load()
    meetings = attendance_matrix_meetings(
        selected_scope,
        offering_id=offering_id,
        starts_on=parsed_date_from,
        ends_on=parsed_date_to,
    )
    students = attendance_matrix_student_queryset(
        selected_scope,
        meetings,
        name=student_search,
        source=source,
        action=action,
        status=scan_status,
    )
    page_obj = Paginator(students, ATTENDANCE_MATRIX_PAGE_SIZE).get_page(request.GET.get("page", 1))
    matrix_rows = attendance_matrix_page(
        page_obj,
        meetings,
        source=source,
        action=action,
        policy=policy,
    )

    # KPI strip: two bounded aggregates over the filtered scope, not the page.
    # `attendance_matrix_meetings` returns column dicts, so read the key.
    meeting_dates = [meeting["date"] for meeting in meetings]
    scanned_student_count = 0
    if meeting_dates:
        scanned_student_count = (
            AttendanceRecord.objects.filter(
                attendance_date__in=meeting_dates,
                student_id__in=students.values("pk"),
            )
            .values("student_id")
            .distinct()
            .count()
        )
    total_students = page_obj.paginator.count
    attendance_kpis = [
        {
            "key": "students",
            "label": _("Students"),
            "value": total_students,
            "icon": "i-users",
            "tone": "teal",
            "meta": _("In this scope"),
        },
        {
            "key": "scanned",
            "label": _("Scanned"),
            "value": scanned_student_count,
            "icon": "i-check-square",
            "tone": "green",
            "meta": _("At least one scan"),
        },
        {
            "key": "not_scanned",
            "label": _("Not scanned"),
            "value": max(0, total_students - scanned_student_count),
            "icon": "i-alert",
            "tone": "amber",
            "meta": _("No scan recorded"),
        },
        {
            "key": "meetings",
            "label": _("Meetings"),
            "value": len(meetings),
            "icon": "i-calendar",
            "tone": "blue",
            "meta": _("In this window"),
        },
    ]

    selected_date = date_from if date_from == date_to else ""
    level_counts = {
        scope.pk: attendance_matrix_student_queryset(
            scope,
            attendance_matrix_meetings(scope, starts_on=scope.academic_year.starts_on, ends_on=scope.academic_year.ends_on),
        ).count()
        for scope in levels
    }
    level_tabs = [
        {
            "level_id": scope.level_id,
            "name": scope.level.display_name,
            "student_count": level_counts[scope.pk],
            "selected": scope.pk == selected_scope.pk,
        }
        for scope in levels
    ]
    reconcile_job = None
    if can_correct_attendance(request.user):
        reconcile_job = (
            AdminOperationJob.objects.filter(
                actor=request.user,
                kind=AdminOperationJob.Kind.ATTENDANCE_RECONCILE,
            )
            .order_by("-created_at")
            .first()
        )
    return render_page(request, "attendance_management.html", "partials/attendance_management_content.html", {
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request, exclude=("page",)),
        "date_error": date_error,
        "years": years,
        "levels": levels,
        "course_offerings": course_offerings,
        "selected_year": selected_year,
        "selected_scope": selected_scope,
        "selected_level": selected_scope.level_id,
        "selected_offering": offering_id,
        "selected_action": action,
        "selected_source": source,
        "selected_scan_status": scan_status,
        "selected_date_from": date_from,
        "selected_date_to": date_to,
        "selected_date": selected_date,
        "student_search": student_search,
        "level_tabs": level_tabs,
        "matrix_rows": matrix_rows,
        "meetings": meetings,
        "meeting_count": len(meetings),
        "student_count": page_obj.paginator.count,
        "attendance_kpis": attendance_kpis,
        "policy": policy,
        "policy_form": AttendancePolicyForm(instance=policy),
        "today": timezone.localdate(),
        "can_manage_policy": can_correct_attendance(request.user),
        "reconcile_job": reconcile_job,
        "reconcile_status_url": (
            reverse("admin-operation-status", args=[reconcile_job.pk]) if reconcile_job else ""
        ),
    })

@require_POST
@capability_required(can_correct_attendance)
def attendance_policy_save(request):
    policy = AttendancePolicy.load()
    form = AttendancePolicyForm(request.POST, instance=policy)
    if form.is_valid():
        policy = form.save(commit=False)
        policy.updated_by = request.user
        policy.save()
        messages.success(request, _("Attendance settings updated."))
    else:
        for error in form.errors.values():
            for message in error:
                messages.error(request, message)
    return redirect("attendance-management")

@require_POST
@capability_required(can_correct_attendance)
def attendance_reconcile(request):
    raw_date = request.POST.get("date", "")
    target_date = timezone.localdate()
    if raw_date:
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            messages.error(request, _("Invalid date format."))
            return redirect("attendance-management")

    job = create_admin_operation(
        actor=request.user,
        kind=AdminOperationJob.Kind.ATTENDANCE_RECONCILE,
        input={"date": target_date.isoformat()},
        conflict_key=f"attendance-reconcile:{target_date.isoformat()}",
        language=translation.get_language() or "ar",
    )
    try:
        enqueue_admin_operation(job.pk, queue="admin")
    except Exception:
        # The queued row is picked up by the recovery schedule.
        logger.warning("attendance reconciliation awaits worker recovery", exc_info=True)
    messages.success(
        request,
        _("Attendance reconciliation for %(date)s was queued. Missing exits are recorded in bounded batches.")
        % {"date": target_date},
    )
    return redirect("attendance-management")

@capability_required(can_correct_attendance)
def attendance_correction(request, record_id):
    record = get_object_or_404(AttendanceRecord, pk=record_id)
    if not record.course_offering_id:
        messages.error(request, _("Assign a calendar course before correcting this record."))
        return redirect("attendance-management")
    if request.method == "POST":
        new_date = request.POST.get("attendance_date")
        new_action = request.POST.get("action")
        if new_action and new_action not in ("entrance", "exit"):
            messages.error(request, _("Invalid action."))
        academic_year_level = record.course_offering.academic_year_level
        academic_year = academic_year_level.academic_year
        if new_date:
            try:
                new_date = datetime.strptime(new_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                messages.error(request, _("Invalid date format."))
            if new_date < academic_year.starts_on or new_date > academic_year.ends_on:
                messages.error(request, _("Date outside academic year bounds."))
        final_date = new_date or record.attendance_date
        final_action = new_action or record.action
        if not is_expected_date(academic_year_level, final_date):
            messages.error(request, _("Date is not a scheduled day or is a holiday."))
        if AttendanceRecord.objects.filter(
            student=record.student, course_offering=record.course_offering,
            attendance_date=final_date, action=final_action,
        ).exclude(pk=record.pk).exists():
            messages.error(request, _("A record already exists for this student, date, and action."))
        record.attendance_date = final_date
        record.action = final_action
        record.corrected_by = request.user
        record.corrected_at = now()
        record.save()
        messages.success(request, _("Attendance record corrected."))
        return redirect("attendance-management")
    return render_page(request, "attendance_correction.html", "partials/attendance_correction_content.html", {
        "record": record,
        "breadcrumb_items": generate_breadcrumb([
            (_("Attendance"), reverse("attendance-management")),
            (_("Correct Attendance Record"), None),
        ]),
    })

@require_POST
@capability_required(can_correct_attendance)
def delete_attendance(request, record_id):
    record = get_object_or_404(AttendanceRecord, pk=record_id)
    record.delete()
    messages.success(request, _("Attendance record deleted."))
    return redirect("attendance-management")

# ============================================================================
# PHASE 6 - Online Lecture Progress Tracking
# ============================================================================

@login_required
def start_viewing_session(request, offering_id, lesson_id, file_index):
    try:
        media = resolve_media_link(
            user=request.user,
            offering_id=offering_id,
            lesson_id=lesson_id,
            file_index=file_index,
            allowed_types=("video", "audio"),
            allow_unpublished=True,
        )
    except MediaAccessError as exc:
        if exc.code == "forbidden":
            raise PermissionDenied(exc.message)
        if exc.code == "not_found":
            raise Http404
        return JsonResponse({"error": exc.message}, status=400)
    # Each player/tab gets an independent authorization session and sequence.
    # Logical study sessions are shared and serialized by the progress row lock.
    session = create_viewing_session(
        request.user, media.lesson, media.part_id, media_key=media.media_key
    )
    token = sign_session(session.session_id, session.expires_at, audience="web")
    progress_row = LectureProgress.objects.filter(
        student=request.user,
        lesson=media.lesson,
        part_id=media.part_id,
    ).values("percent", "last_position_seconds").first()
    progress_percent = int(progress_row["percent"] or 0) if progress_row else 0
    resume_seconds = int(progress_row["last_position_seconds"] or 0) if progress_row else 0
    return JsonResponse({
        "session_id": session.session_id,
        "token": token,
        "expires_at": session.expires_at.isoformat(),
        "segment_token_lifetime_seconds": int(MEDIA_SEGMENT_TOKEN_LIFETIME.total_seconds()),
        "manifest_url": reverse(
            "lesson-manifest",
            args=[media.offering.pk, media.lesson.pk, media.file_index],
        ),
        "progress_percent": progress_percent,
        "resume_seconds": resume_seconds,
    })

@login_required
def lesson_manifest(request, offering_id, lesson_id, file_index):
    try:
        media = resolve_media_link(
            user=request.user,
            offering_id=offering_id,
            lesson_id=lesson_id,
            file_index=file_index,
            allowed_types=("video", "audio"),
            allow_unpublished=True,
        )
    except MediaAccessError as exc:
        if exc.code == "forbidden":
            raise PermissionDenied(exc.message)
        return HttpResponse(status=404)
    session_id = request.GET.get("session_id", "")
    token = request.GET.get("token", "")
    if not session_id or not token:
        return JsonResponse({"error": _("Viewing session is required.")}, status=401)
    session = get_object_or_404(
        ViewingSession,
        session_id=session_id,
        student=request.user,
        lesson=media.lesson,
        part_id=media.part_id,
    )
    if timezone.now() >= session.expires_at:
        return JsonResponse({"error": _("Viewing session expired.")}, status=401)
    audience = getattr(request, "media_audience", "web")
    expected_channel = (
        ViewingSession.AccessChannel.MOBILE
        if audience == "mobile"
        else ViewingSession.AccessChannel.WEB
    )
    if session.access_channel != expected_channel:
        return JsonResponse({"error": _("Invalid viewing session token.")}, status=403)
    if not valid_session_token(token, session, audience=audience):
        return JsonResponse({"error": _("Invalid viewing session token.")}, status=403)
    if session.media_key and session.media_key != media.media_key:
        return JsonResponse({"error": _("Invalid viewing session token.")}, status=403)
    try:
        playlist = build_manifest_playlist(
            user=request.user,
            media=media,
            session=session,
            audience=audience,
            cloud_client=CLOUD_CLIENT,
            bucket_name=bucket_name,
        )
    except MediaAccessError as exc:
        if exc.code == "expired":
            return JsonResponse({"error": exc.message}, status=401)
        if exc.code == "forbidden":
            return JsonResponse({"error": exc.message}, status=403)
        return HttpResponse(status=404)
    return HttpResponse(
        playlist,
        content_type="application/vnd.apple.mpegurl",
    )


@csrf_exempt
@require_POST
def worker_receipt(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)

    session_id = data.get("session_id")
    segment_key = data.get("segment_key")
    segment_number = data.get("segment_number")
    signature = data.get("signature")
    audience = data.get("audience")
    token_expires_at = data.get("expires_at")

    if (
        not isinstance(session_id, str)
        or not isinstance(segment_key, str)
        or not isinstance(signature, str)
        or not isinstance(audience, str)
        or isinstance(token_expires_at, bool)
        or not session_id
        or not segment_key
        or audience not in MEDIA_AUDIENCES
        or segment_number is None
        or token_expires_at is None
    ):
        return JsonResponse({"error": _("Missing required fields.")}, status=400)
    if request.headers.get("X-Worker-Secret") != settings.WORKER_RECEIPT_SECRET:
        return JsonResponse({"error": _("Invalid worker credentials.")}, status=403)
    if not validate_hls_object_key(segment_key)[0]:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)
    if isinstance(segment_number, bool):
        return JsonResponse({"error": _("Invalid segment number.")}, status=400)
    try:
        segment_number = int(segment_number)
    except (TypeError, ValueError, OverflowError):
        return JsonResponse({"error": _("Invalid segment number.")}, status=400)
    try:
        token_expires_at = int(token_expires_at)
    except (TypeError, ValueError, OverflowError):
        return JsonResponse({"error": _("Invalid request format.")}, status=400)

    session = get_object_or_404(ViewingSession, session_id=session_id)

    expected_audience = (
        "mobile"
        if session.access_channel == ViewingSession.AccessChannel.MOBILE
        else "web"
    )
    if audience != expected_audience:
        return JsonResponse({"error": _("Invalid request signature.")}, status=403)

    if timezone.now() >= session.expires_at:
        return JsonResponse({"error": _("Session expired.")}, status=410)

    if token_expires_at > int(session.expires_at.timestamp()) or token_expires_at <= 0:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)

    if get_segment_number(segment_key) != segment_number:
        return JsonResponse({"error": _("Invalid segment number.")}, status=400)
    expected = sign_media_message(
        f"{MEDIA_TOKEN_VERSION}|{audience}|{session_id}|"
        f"{token_expires_at}|{segment_key}"
    )
    if not hmac.compare_digest(signature, expected):
        return JsonResponse({"error": _("Invalid request signature.")}, status=403)

    _receipt, created = VerifiedSegmentRequest.objects.get_or_create(
        session=session,
        segment_number=segment_number,
        defaults={
            "segment_key": segment_key,
            "signature": signature,
            "expires_at": session.expires_at,
        },
    )
    return JsonResponse({"status": "recorded" if created else "already_recorded"})

@require_POST
def progress_heartbeat(request, *, allow_management=False):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)
    try:
        result = apply_progress_heartbeat(
            user=request.user,
            payload=payload,
            cloud_client=CLOUD_CLIENT,
            bucket_name=bucket_name,
            allow_management=allow_management,
        )
    except ProgressHeartbeatError as exc:
        if exc.code == "session_not_found":
            raise Http404
        status = {
            "unauthenticated": 401,
            "forbidden": 403,
            "expired": 403,
        }.get(exc.code, 400)
        return JsonResponse({"error": exc.message}, status=status)
    return JsonResponse(result.as_payload())


@capability_required(can_manage_content)
def progress_dashboard(request):
    academic_year_level_id = request.GET.get("academic_year_level") or request.GET.get("academic_year")
    offering_id = request.GET.get("course_offering")
    lesson_id = request.GET.get("lesson")
    student_search = normalize_search_text(request.GET.get("student", "").strip()[:100])

    scopes = AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by(
        "-academic_year__ordering", "level__ordering"
    )
    offerings = CourseOffering.objects.none()
    lessons = Lesson.objects.none()
    students = User.objects.none()
    progress = LectureProgress.objects.none()

    selected_scope = None
    if academic_year_level_id and str(academic_year_level_id).isdigit() and len(str(academic_year_level_id)) <= 18:
        selected_scope = get_object_or_404(
            AcademicYearLevel,
            pk=int(academic_year_level_id),
            academic_year__is_active=True,
        )
        offerings = CourseOffering.objects.filter(
            academic_year_level=selected_scope
        ).select_related("course", "academic_year_level")
        progress = LectureProgress.objects.filter(
            lesson__course_offering__academic_year_level=selected_scope
        )

    if offering_id:
        progress = progress.filter(lesson__course_offering_id=offering_id)
        lessons = Lesson.objects.filter(course_offering_id=offering_id).order_by("name")

    if lesson_id:
        progress = progress.filter(lesson_id=lesson_id)

    if student_search:
        student_filter = (
            normalized_contains_q(
                ("student__username", "student__first_name", "student__last_name", "student__email"),
                student_search,
            )
        )
        if student_search.isdigit():
            student_filter |= Q(student_id=int(student_search))
        progress = progress.filter(student_filter)

    progress = progress.select_related(
        "student", "lesson", "lesson__course_offering__course",
    ).order_by("student__last_name", "student__first_name", "student__username", "lesson__name", "pk")

    detail_id = request.GET.get("activity_detail")
    detail_day = request.GET.get("activity_day")
    if detail_id and str(detail_id).isdigit() and len(str(detail_id)) <= 18 and detail_day:
        study_date = parse_date(detail_day)
        if study_date is None or study_date > timezone.localdate() or study_date < timezone.localdate() - timedelta(days=29):
            raise Http404
        row = get_object_or_404(progress, pk=int(detail_id))
        session_page = Paginator(
            watch_sessions_for_day(row, study_date), 50,
        ).get_page(request.GET.get("session_page", 1))
        for watch_session in session_page.object_list:
            watch_session.duration_label = format_active_duration(watch_session.day_active_seconds)
        return render(request, "partials/progress_activity_sessions.html", {
            "progress_row": row,
            "activity_day": study_date,
            "session_page": session_page,
            "session_query": pagination_query_string(request, exclude=("session_page",)),
            "session_list_id": f"progress-session-list-{row.pk}-{study_date:%Y%m%d}",
            "session_list_selector": f"#progress-session-list-{row.pk}-{study_date:%Y%m%d}",
        })

    page_obj = Paginator(progress, 25).get_page(request.GET.get("page", 1))
    page_rows = list(page_obj.object_list)
    progress_activity_for_rows(page_rows)
    for row in page_rows:
        row.part_display_name = part_display_name(row.lesson, row.part_id)
    page_obj.object_list = page_rows

    return render_page(request, "progress_dashboard.html", "partials/progress_dashboard_content.html", {
        "progress": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "activity_query": pagination_query_string(request, exclude=("page", "activity_detail", "activity_day")),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Progress"), None),
        ]),
        "academic_years": scopes,
        "scopes": scopes,
        "offerings": offerings,
        "lessons": lessons,
        "students": students,
        "selected_year": selected_scope.pk if selected_scope else None,
        "selected_offering": int(offering_id) if offering_id else None,
        "selected_lesson": int(lesson_id) if lesson_id else None,
        "selected_student": student_search,
        "progress_window_start": timezone.localdate() - timedelta(days=29),
        "progress_window_end": timezone.localdate(),
    })

@capability_required(can_view_reports)
def report_dashboard(request):
    academic_year_level_id = request.GET.get("academic_year_level") or request.GET.get("academic_year")
    student_search = normalize_search_text(request.GET.get("student", "").strip()[:100])
    study_mode = request.GET.get("study_mode") or None
    course_offering_id = request.GET.get("course_offering") or None
    report_filters = normalize_report_filters(student_search, study_mode, course_offering_id)
    offering_pk = report_filters["course_offering_id"]

    levels = Level.objects.order_by("ordering")
    academic_years = AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by(
        "-academic_year__ordering", "level__ordering"
    )
    selected_year = None
    rows = []
    page_obj = None
    report_export_job = None

    if academic_year_level_id:
        selected_year = get_object_or_404(
            AcademicYearLevel,
            pk=academic_year_level_id,
            academic_year__is_active=True,
        )
        course_offerings = CourseOffering.objects.filter(
            academic_year_level=selected_year
        ).select_related("academic_year_level__level", "course")
        report_query = report_enrollments(
            selected_year,
            student_search=student_search,
            study_mode=study_mode,
            course_offering_id=offering_pk,
        )
        page_obj = Paginator(report_query, 25).get_page(request.GET.get("page", 1))
        rows = build_report_page_rows(
            page_obj.object_list,
            selected_year,
            course_offering_id=offering_pk,
        )

        report_export_job = (
            AdminOperationJob.objects.filter(
                actor=request.user,
                kind=AdminOperationJob.Kind.GRADES_REPORT_EXPORT,
                conflict_key=report_export_conflict_key(
                    selected_year,
                    student=report_filters["student"],
                    study_mode=report_filters["study_mode"],
                    course_offering_id=offering_pk,
                ),
            )
            .order_by("-created_at")
            .first()
        )

    context = {
        "title": _("Combined Report"),
        "levels": levels,
        "academic_years": academic_years,
        "selected_year": selected_year,
        "selected_level": request.GET.get("level", ""),
        "selected_student": student_search,
        "selected_study_mode": study_mode or "",
        "selected_course_offering": offering_pk or "",
        "students": [],
        "course_offerings": course_offerings if academic_year_level_id else [],
        "rows": rows,
        "row_count": page_obj.paginator.count if page_obj else 0,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "report_export_job": report_export_job,
        "report_export_status_url": (
            reverse("admin-operation-status", args=[report_export_job.pk])
            if report_export_job else ""
        ),
        "report_export_download_url": (
            reverse("admin-operation-download", args=[report_export_job.pk])
            if report_export_job else ""
        ),
    }
    return render_page(request, "report_dashboard.html", "partials/report_dashboard_content.html", context)

@capability_required(can_view_reports)
def export_report_csv(request):
    academic_year_level_id = request.GET.get("academic_year_level") or request.GET.get("academic_year")
    if not academic_year_level_id:
        return HttpResponse(_("Academic year is required."), status=400)
    scope = get_object_or_404(AcademicYearLevel, pk=academic_year_level_id)

    row_iterator = iter_report_data(
        scope,
        student_id=request.GET.get("student") or None,
        study_mode=request.GET.get("study_mode") or None,
        course_offering_id=request.GET.get("course_offering") or None,
    )

    def csv_rows():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            _("Student ID"), _("Username"), _("First Name"), _("Last Name"), _("Study Mode"),
            _("Level"), _("Academic Year"), _("Grade Earned"), _("Grade Available"), _("Grade %"),
            _("Expected"), _("Full days"), _("Fully scanned days"), _("Partial"), _("Absent"), _("Attendance Score"),
            _("Attendance %"), _("Absence %"),
        ])
        yield output.getvalue()
        for row in row_iterator:
            output.seek(0)
            output.truncate(0)
            writer.writerow([
                row["student_id"], _csv_safe_cell(row["username"]), _csv_safe_cell(row["first_name"]),
                _csv_safe_cell(row["last_name"]),
                _csv_safe_cell(_("Online") if row["study_mode"] == "online" else _("Offline") if row["study_mode"] == "offline" else row["study_mode"]),
                row["level"],
                _csv_safe_cell(row["year_name"]), row["grade_earned"], row["grade_available"],
                row["grade_percent"], row["expected"], row["valid"], row["fully_scanned"], row["invalid"], row["absent"],
                row["attendance_score"], row["attendance_rate"], row["absence_rate"],
            ])
            yield output.getvalue()

    safe_name = _safe_filename(scope.academic_year.name)
    response = StreamingHttpResponse(csv_rows(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="report_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    return response

@require_http_methods(["GET", "POST"])
@capability_required(can_view_reports)
def export_report_xlsx(request):
    if request.method != "POST":
        # All report workbooks are population-sized; the durable job owns the
        # only workbook path and the HTML page submits a POST action.
        return HttpResponse(_("Start the report export from the reports page."), status=400)
    academic_year_level_id = request.POST.get("academic_year_level") or request.POST.get("academic_year")
    if not academic_year_level_id or not str(academic_year_level_id).isdigit():
        return HttpResponse(_("Academic year is required."), status=400)
    scope = get_object_or_404(AcademicYearLevel, pk=int(academic_year_level_id))
    filters = normalize_report_filters(
        request.POST.get("student"),
        request.POST.get("study_mode"),
        request.POST.get("course_offering"),
    )
    try:
        job = enqueue_report_export(
            actor=request.user,
            academic_year_level=scope,
            student=filters["student"],
            study_mode=filters["study_mode"],
            course_offering_id=filters["course_offering_id"],
            language=translation.get_language() or "ar",
        )
    except ValidationError as exc:
        return HttpResponse("; ".join(exc.messages), status=400)
    try:

        enqueue_admin_operation(job.pk, queue="admin")
    except Exception:
        # The queued row is picked up by the recovery schedule.
        logger.warning("report export awaits worker recovery", exc_info=True)
    messages.success(
        request,
        _("Report export was queued. The download link appears here when it is ready."),
    )
    query = {"academic_year": scope.pk}
    if filters["student"]:
        query["student"] = filters["student"]
    if filters["study_mode"]:
        query["study_mode"] = filters["study_mode"]
    if filters["course_offering_id"]:
        query["course_offering"] = filters["course_offering_id"]
    return redirect(f"{reverse('report-dashboard')}?{urlencode(query)}")


@capability_required(can_view_reports)
def application_analytics_dashboard(request):
    return render_page(
        request,
        "application_analytics.html",
        "partials/application_analytics_content.html",
        build_analytics_context(request),
    )


@capability_required(can_view_reports)
def export_application_analytics_csv(request):
    dataset = build_analytics_dataset(request)

    def csv_rows():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([_(header) for header in ANALYTICS_CSV_HEADERS])
        yield output.getvalue()
        for row in iter_analytics_csv_rows(dataset["queryset"]):
            output.seek(0)
            output.truncate(0)
            writer.writerow(row)
            yield output.getvalue()

    response = StreamingHttpResponse(csv_rows(), content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="application_analytics_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    )
    return response


@require_http_methods(["GET", "POST"])
@capability_required(can_view_reports)
def export_evaluation_xlsx(request):
    source = request.POST if request.method == "POST" else request.GET
    formula_id = source.get("formula")
    scope_id = source.get("scope")
    offering_id = source.get("course_offering")
    if formula_id and str(formula_id).isdigit():
        formula = get_object_or_404(
            PromotionFormula.objects.select_related("academic_year_level__academic_year", "academic_year_level__level"),
            pk=int(formula_id),
        )
    else:
        if not scope_id or not str(scope_id).isdigit():
            return HttpResponse(_("Promotion formula is required."), status=400)
        formula = PromotionFormula.objects.filter(
            academic_year_level_id=int(scope_id),
            course_offering_id=int(offering_id) if offering_id and str(offering_id).isdigit() else None,
        ).select_related("academic_year_level__academic_year", "academic_year_level__level").first()
        if formula is None:
            return HttpResponse(_("Promotion formula is required."), status=404)

    if formula.course_offering_id is None:
        # Whole-scope exports are population-sized and always run through the
        # durable job; a GET must not keep a synchronous full-population path.
        if request.method != "POST":
            return HttpResponse(
                _("Start a whole-scope export from the promotion formula page."),
                status=400,
            )
        try:
            job = enqueue_evaluation_export(
                actor=request.user,
                formula=formula,
                language=translation.get_language() or "ar",
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(f"{reverse('promotion-formula')}?scope={formula.academic_year_level_id}")
        try:

            enqueue_admin_operation(job.pk, queue="admin")
        except Exception:
            # The queued row is picked up by the recovery schedule.
            logger.warning("evaluation export awaits worker recovery", exc_info=True)
        messages.success(
            request,
            _("Evaluation export was queued. The download link appears here when it is ready."),
        )
        return redirect(f"{reverse('promotion-formula')}?scope={formula.academic_year_level_id}")

    workbook = build_evaluation_workbook(formula)
    safe_name = _safe_filename(
        f"{formula.academic_year_level.academic_year.name}-{formula.academic_year_level.level.display_name}"
    )
    return FileResponse(
        workbook,
        as_attachment=True,
        filename=f"evaluation_{safe_name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        content_type=XLSX_CONTENT_TYPE,
    )

def robots_txt(request):
    return HttpResponse(
        "User-agent: *\n"
        "Disallow: /dashboard/\n"
        "Disallow: /en/dashboard/\n"
        "Disallow: /ar/dashboard/\n"
        "Disallow: /portal/\n"
        "Disallow: /en/portal/\n"
        "Disallow: /ar/portal/\n"
        "Disallow: /scanner/\n"
        "Disallow: /en/scanner/\n"
        "Disallow: /ar/scanner/\n"
        "Disallow: /api/\n"
        "Disallow: /en/api/\n"
        "Disallow: /ar/api/\n"
        "Disallow: /profile/\n"
        "Disallow: /en/profile/\n"
        "Disallow: /ar/profile/\n"
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}\n",
        content_type="text/plain",
    )

def sitemap_xml(request):
    urls = [
        (reverse("home"), "weekly", "1.0"),
        (reverse("about"), "monthly", "0.8"),
        (reverse("program"), "monthly", "0.9"),
        (reverse("user_login"), "monthly", "0.3"),
        (reverse("signup"), "monthly", "0.5"),
    ]
    entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{loc}</loc>\n"
        f"    <changefreq>{freq}</changefreq>\n"
        f"    <priority>{prio}</priority>\n"
        f"  </url>"
        for loc, freq, prio in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml")


# ---- Smooth/easy batch: command palette, 360, content QA ----

@login_required(login_url=LOGIN_URL)
@require_GET
def command_palette_search(request):
    """Management-only JSON jump search for the Ctrl+K palette (GET only)."""
    if not user_has_management_role(request.user):
        raise PermissionDenied
    raw_type = str(request.GET.get("type", "") or "").strip().lower()
    type_filter = raw_type if raw_type in PALETTE_TYPES else None
    return JsonResponse({"results": palette_results(
        request.GET.get("q", ""),
        type_filter=type_filter,
        show_phone=can_manage_academic_setup(request.user),
    )})


@login_required(login_url=LOGIN_URL)
def student_overview(request, user_id):
    """Management 360 page: enrollments, grades, attendance, progress, Telegram, notifications, devices.

    Read-only by construction (no POST branch, no mutations). Every view is
    audit-logged; Telegram phone numbers are masked for non-admin viewers
    (Telegram data is otherwise admin-only).
    """
    if not (can_view_reports(request.user) or can_manage_applications(request.user)):
        raise PermissionDenied
    payload = build_student_overview(user_id)
    if payload is None:
        raise Http404
    student = payload["student"]
    logger.info(
        "event=student_360_view actor=%s actor_id=%s student=%s student_id=%s",
        getattr(request.user, "username", "?"),
        getattr(request.user, "pk", "?"),
        getattr(student, "username", "?"),
        student.pk,
    )
    show_phone = can_manage_academic_setup(request.user)
    payload["show_phone"] = show_phone
    account = payload.get("telegram_account")
    if account is not None and getattr(account, "phone", None) and not show_phone:
        digits = "".join(ch for ch in str(account.phone) if ch.isdigit())
        payload["telegram_phone_display"] = ("---" + digits[-4:]) if digits else "---"
    else:
        payload["telegram_phone_display"] = getattr(account, "phone", "") if account is not None else ""
    payload["breadcrumb_items"] = generate_breadcrumb([
        (_("Admin"), reverse("admin-panel")),
        (_("Users"), reverse("user-dashboard")),
        (student.get_full_name() or student.username, reverse("user-profile", args=[student.pk])),
        (_("Overview"), None),
    ])
    return render_page(request, "student_overview.html", "partials/student_overview_content.html", payload)


@capability_required(can_manage_content)
def content_qa_dashboard(request):
    """Read-only content QA: counts + bounded samples, no writes/R2."""
    context = {
        "counts": qa_counts(),
        "samples": qa_samples(),
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Content QA"), None),
        ]),
    }
    return render_page(request, "content_qa.html", "partials/content_qa_content.html", context)
