# Django Core Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, FileResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, FormView, DetailView
from django.utils.translation import gettext as _
from django.utils import translation
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import views, update_session_auth_hash
from django.db.models import Avg, BooleanField, Case, Count, Exists, F, IntegerField, OuterRef, Prefetch, Q, Sum, Value, When
from django.db import IntegrityError, transaction
from django.contrib import messages
from django.contrib.messages import success, error, info
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils import formats
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.forms import formset_factory
import hashlib
import hmac
import io
import math
import secrets
import uuid
import random
import posixpath
from collections import defaultdict
from itertools import islice
from urllib.parse import quote

# Third Party
from logging import getLogger
import boto3
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
from .forms import CSVUploadForm, CourseForm, UserCreationForm, UserUpdateForm, SignupForm, SignupDetailsForm, MissingApplicationDocumentsForm, AcademicPaymentForm, ApplicationAdminForm, QuizExceptionalOpeningForm, AcademicYearForm, CourseOfferingForm, AcademicYearLevelWeekdayForm, OfferingCopyForm, PromotionFormulaForm, PromotionRuleForm, HistoricalIntakeForm, ExceptionalCourseAssignmentForm, HistoricalBulkIntakeForm

# Internal Imports - Utilities
from .utils.csv_export import export_users_to_csv, export_quiz_with_submissions_to_csv, export_single_submission_to_csv, export_quiz_summary_to_csv, _csv_safe_cell, _safe_filename
from .grade_matrix import (
    GRADE_MATRIX_PAGE_SIZE,
    grade_matrix_courses,
    grade_matrix_page,
    grade_matrix_selection,
    grade_matrix_student_queryset,
    grade_matrix_workbook,
)
from .utils.reports import build_report_data, build_report_page_rows, iter_report_data, report_enrollments
from .utils.r2_filters import R2FileFilter, FileFilterConfig, get_filter_preset, FILTER_PRESETS
from .utils.r2_manager import R2Manager
from .utils.cloudflare_provider import CloudflareR2Client
from .utils.file_validator import (
    downloadable_audio_key,
    validate_downloadable_audio_key,
    validate_hls_object_key,
    FileValidator,
)
from .utils.storage_operations import list_current_folder, list_current_folder_page, download_from_bucket, generate_unique_url, get_r2_client
from .utils.helpers import get_datetime, paginate_obj, render_dashboard, select_content_academic_year, unpack_quiz_form, safe_get_user, parse_json_value, get_student_quiz_status, is_quiz_in_user_window, is_quiz_open, user_has_management_role, pagination_query_string, generate_breadcrumb, hx_target_id, render_page
from .utils.decorators import capability_required, can_manage_content, can_delete_content, can_grade, can_view_reports, can_manage_applications, can_manage_academic_setup, can_scan_attendance, can_correct_attendance
from .utils.email import send_application_received, send_application_activated, send_application_declined
from .utils.application_uploads import upload_application_file
from .utils.r2_references import rewrite_lesson_r2_references
from .utils.attendance import is_expected_date, get_expected_dates, get_student_attendance_context, assign_unassigned_attendance
from .utils.timezones import ensure_aware, format_user_datetime
from .utils.search import normalize_search_text, normalized_contains_q
from .utils.quiz_access import grant_quiz_openings, quiz_window
from .public_content import get_institute_copy
from .academic_enrollment import (
    activate_academic_year,
    accept_application,
    decline_application,
    reopen_application,
    bulk_set_application_status,
    set_application_status,
    set_user_normal_enrollment_scope,
    promote_evaluation_result,
    promote_evaluation_results,
    promote_historical_summary,
)
from .historical_intake import (
    INTAKE_COLUMNS,
    assign_exceptional_courses,
    intake_historical_row,
    parse_intake_upload,
    preview_intake_rows,
)
from .academic_copy import copy_offerings
from .academic_formula import normalize_formula_rules, save_promotion_formula
from .academic_evaluation import (
    build_evaluation_plan,
    evaluation_enrollments,
    evaluate_enrollment,
    override_evaluation_result,
    save_formula_and_results,
)
from .evaluation_export import build_evaluation_workbook
from .academic_access import READABLE_ENROLLMENT_STATUSES, accessible_offerings, active_year_offerings_for_student, get_accessible_offering_or_403, user_can_read_offering, user_can_write_offering_activity
from .utils.hls_parser import get_lesson_segments, get_segment_number
from .utils.progress_merge import intersect_verified, merge_ranges, unique_seconds, calculate_percent
from .media_processing import (
    claim_attachment_retry,
    initialize_deadlines,
    job_is_visible_to,
    queue_verified_job,
    safe_output_base_name,
    schedule_attachment_retry_after_commit,
    schedule_media_job_after_commit,
    validate_browser_part_id,
    validate_requested_folder,
    validate_source_descriptor,
)
from .media_storage import (
    MediaStorageError,
    build_staging_key,
    content_type_for_key,
    create_staging_upload_url,
)
from .telegram.linking import TelegramLinkError, current_telegram_link
from .student_notifications import (
    cancel_future_quiz_opening_events,
    create_lesson_publication_event,
    schedule_quiz_opening_events,
)
from .payments import academic_payment_scopes_for_student
from .payment_matrix import PAYMENT_MATRIX_PAGE_SIZE, PAYMENT_STATUS_VALUES, payment_matrix_page, payment_matrix_student_queryset

# Standard Library (moved from function-local)
import calendar as py_calendar
from datetime import datetime
from io import BytesIO
import openpyxl
import qrcode

# Django
from django.core.paginator import Paginator

# Constants
LOGIN_URL = reverse_lazy("user_login")
logger = getLogger(__name__)
# DRIVE_CLIENT = get_drive_client()
CLOUD_CLIENT = boto3.client(
    's3',
    endpoint_url=getattr(settings, "R2_ENDPOINT_URL", None),
    aws_access_key_id=getattr(settings, "R2_ACCESS_KEY_ID", None),
    aws_secret_access_key=getattr(settings, "R2_SECRET_ACCESS_KEY", None),
    region_name='auto'
)

bucket_name = getattr(settings, "R2_BUCKET_NAME", "")

cf_account_id = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "")
cf_api_token = getattr(settings, "CLOUDFLARE_API_TOKEN", "")
cloudflare_client = CloudflareR2Client(cf_account_id, cf_api_token) if cf_account_id and cf_api_token else None

PromotionRuleFormSet = formset_factory(PromotionRuleForm, extra=0, can_delete=True)


def _lesson_links_from_form(request) -> list[dict]:
    """Build lesson link metadata while preserving downloadable MP3 siblings."""
    videos = request.POST.getlist("videos", [])
    videos_name = request.POST.getlist("videos_name", [])
    files_type = request.POST.getlist("files_type", [])
    download_ids = request.POST.getlist("download_ids", [])
    if not videos or len(videos) != len(videos_name) or len(videos) != len(files_type):
        raise ValidationError(_("Lesson files are incomplete."))

    links = []
    for index, video_key in enumerate(videos):
        download_key = download_ids[index].strip() if index < len(download_ids) else ""
        if download_key:
            valid, errors = validate_downloadable_audio_key(download_key)
            if not valid or download_key != downloadable_audio_key(video_key):
                raise ValidationError(errors or [_('Downloadable audio key is invalid.')])
            if files_type[index] == "book":
                raise ValidationError(_("Book media cannot have a downloadable audio file."))
        link = {
            "file_type": files_type[index],
            "name": videos_name[index],
            "id": video_key,
        }
        if download_key:
            link["download_id"] = download_key
        links.append(link)
    return links

# Initialize R2 Manager
R2_MANAGER = R2Manager(CLOUD_CLIENT, bucket_name, cloudflare_client)

# Helper functions moved to utils/storage_operations.py and utils/helpers.py
# Google Drive legacy code moved to utils/google_drive_manager.py

def build_question_instance(question_data, quiz):
    """Build a Question instance from parsed form data."""
    title = question_data.get("name", "")
    question_type = question_data.get("type", "")
    grade = int(question_data.get("grade", 1) or 1)
    config = parse_json_value(question_data.get("config", {}), {}) or {}
    choices = question_data.get("choices", [])
    correct_answer = (question_data.get("answer", "") or "").strip()
    auto_grade = False

    if question_type in Question.STRUCTURED_QUESTION_TYPES and not isinstance(config, dict):
        raise ValueError(_("Structured question config is invalid for question: %(title)s") % {"title": title})

    def _unique_values(values, label):
        normalized = []
        seen = set()

        for value in values:
            text = str(value).strip()
            if not text:
                continue

            if text in seen:
                raise ValueError(_("Each %(label)s must be unique for question: %(title)s") % {"label": label, "title": title})

            seen.add(text)
            normalized.append(text)

        if not normalized:
            raise ValueError(_("%(label)s are required for question: %(title)s") % {"label": label.capitalize(), "title": title})

        return normalized

    if question_type == "mcq":
        choices = [choice.strip() for choice in choices if str(choice).strip()]
        if correct_answer and correct_answer not in choices:
            raise ValueError(_("Correct answer is not in choices for question : %(title)s") % {'title': title})
        choices = json.dumps(choices)
        auto_grade = bool(correct_answer)

    elif question_type == "written":
        correct_answer = ""
        choices = json.dumps([])
        auto_grade = False

    elif question_type == "complete":
        choices = json.dumps([])
        auto_grade = bool(correct_answer)

    elif question_type == "order_events":
        items = config.get("items") or choices or []
        items = _unique_values(items, _("order event item"))
        config = {"items": items}
        choices = json.dumps(items)
        correct_answer = ""
        auto_grade = True

    elif question_type == "match_related":
        pairs = config.get("pairs") or []
        normalized_pairs = []
        seen_left_values = set()
        seen_right_values = set()

        for pair in pairs:
            left = right = ""
            if isinstance(pair, dict):
                left = str(pair.get("left", "")).strip()
                right = str(pair.get("right", "")).strip()
            elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                left = str(pair[0]).strip()
                right = str(pair[1]).strip()

            if left and right:
                if left in seen_left_values:
                    raise ValueError(_("Each match related left item must be unique for question: %(title)s") % {"title": title})
                if right in seen_right_values:
                    raise ValueError(_("Each match related right item must be unique for question: %(title)s") % {"title": title})

                seen_left_values.add(left)
                seen_right_values.add(right)
                normalized_pairs.append({"left": left, "right": right})

        if not normalized_pairs:
            raise ValueError(_("Match Related questions need at least one pair"))

        config = {"pairs": normalized_pairs}
        choices = json.dumps([pair["left"] for pair in normalized_pairs])
        correct_answer = ""
        auto_grade = True

    else:
        choices = json.dumps([choice.strip() for choice in choices]) if choices else json.dumps([])
        auto_grade = bool(correct_answer) and question_type != "written"

    return Question(
        title=title,
        quiz=quiz,
        correct_answer=correct_answer,
        question_type=question_type,
        choices=choices,
        config=config,
        grade=grade,
        auto_grade=auto_grade,
    )

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
        return HttpResponse(_("Unauthorized"), status=403)

class FormBase(AdminPermissionView, FormView):
    view_name = ""
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
        success(self.request, self.get_success_msg(), extra_tags="alert-success")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view_name"] = self.view_name  # Example shared context
        context["object_id"] = self.object.pk if self.object else 'new'
        return context

class UserBaseView(FormBase):
    model = User
    pk_url_kwarg = "user_id"
    view_name = _("user") # Translate view_name
  
class CourseBaseView(FormBase):
    model = Course
    pk_url_kwarg = "course_id"
    view_name = _("course") # Translate view_name
    form_class = CourseForm

class LessonBaseView(FormBase):
    model = Lesson
    pk_url_kwarg = "lesson_id"
    view_name = _("lesson") # Translate view_name

class QuizBaseView(FormBase):
    model = Quiz
    pk_url_kwarg = "quiz_id"
    view_name = _("quiz") # Translate view_name

# # Create your views here.
class LoginView(views.LoginView):
    template_name = "login.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(reverse("home"))
        logger.info("Display login page")
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        translation.activate('ar')  # Activating Arabic language
        response =  super().post(request, *args, **kwargs)
        username = request.POST.get("username")

        if response.status_code != 302:
            logger.warning(f"Invalid user access for account : {username}")
        else:
            logger.info(f"User : {username} is successfully logged in")
        
        return response

def index(request):
    return render(request, "home.html", get_institute_copy(translation.get_language(), "home"))

@login_required(login_url=LOGIN_URL)
def portal(request):
    return redirect("home")

@login_required(login_url=LOGIN_URL)
def student_ui_proposal(request):
    """Read-only design proposal page presenting all student screens with the Psalmodia theme."""
    return render(request, "theme_showcase/student_proposal.html")


@login_required(login_url=LOGIN_URL)
def admin_ui_proposal(request):
    """Interactive admin-dashboard design proposal with live bounded data (admin only)."""
    role = getattr(getattr(request.user, "role", None), "role", "")
    if role != "admin":
        raise PermissionDenied

    today = timezone.localdate()

    # --- People ---
    role_counts = {
        row["role__role"]: row["n"]
        for row in User.objects.values("role__role").annotate(n=Count("id"))
    }
    pending_applications = User.objects.filter(application_status="pending")
    recent_users = User.objects.select_related("role").order_by("-date_joined")[:6]

    # --- Content ---
    courses = (
        Course.objects.select_related("level")
        .annotate(
            offering_count=Count("offerings", distinct=True),
            lesson_count=Count("offerings__lessons", distinct=True),
            quiz_count=Count("offerings__quizzes", distinct=True),
        )
        .order_by("level__ordering", "name")[:8]
    )
    lesson_status_counts = {
        row["status"]: row["n"]
        for row in Lesson.objects.values("status").annotate(n=Count("id"))
    }
    recent_lessons = (
        Lesson.objects.select_related(
            "course_offering__course",
            "course_offering__academic_year_level__level",
        )
        .order_by("-updated_date")[:6]
    )
    quiz_status_counts = {
        row["status"]: row["n"]
        for row in Quiz.objects.values("status").annotate(n=Count("id"))
    }
    recent_quizzes = (
        Quiz.objects.select_related("course_offering__course", "quiz_type")
        .annotate(question_count=Count("questions", distinct=True))
        .order_by("-created_date")[:6]
    )
    draft_lessons = (
        Lesson.objects.filter(status=PublicationStatus.DRAFT)
        .select_related("course_offering__course")
        .order_by("-updated_date")[:5]
    )
    lessons_total = Lesson.objects.count()
    lessons_with_media = sum(
        1 for links in Lesson.objects.values_list("links", flat=True) if links
    )

    # --- Academic setup ---
    levels = (
        Level.objects.order_by("ordering")
        .annotate(
            course_count=Count("courses", distinct=True),
            offering_count=Count("courses__offerings", distinct=True),
            enrollment_count=Count("year_links__enrollments", distinct=True),
        )
    )
    years = (
        AcademicYear.objects.annotate(
            scope_count=Count("level_links", distinct=True),
            offering_count=Count("level_links__course_offerings", distinct=True),
            enrollment_count=Count("level_links__enrollments", distinct=True),
        ).order_by("-ordering")
    )
    weekday_labels = [str(label) for label in (
        _("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"),
        _("Friday"), _("Saturday"), _("Sunday"),
    )]
    active_year = AcademicYear.objects.filter(is_active=True).first()
    active_scopes = (
        AcademicYearLevel.objects.filter(academic_year__is_active=True)
        .select_related("academic_year", "level")
        .order_by("level__ordering")
    )
    calendar_scopes = [
        {
            "scope": scope,
            "weekdays": [
                weekday_labels[i] for i in (scope.meeting_weekdays or [])
                if 0 <= i < 7
            ],
        }
        for scope in active_scopes
    ]
    holidays = (
        AcademicHoliday.objects.filter(academic_year__is_active=True)
        .order_by("date")[:6]
    )

    meeting_weekday_numbers = sorted({
        weekday
        for scope in active_scopes
        for weekday in (scope.meeting_weekdays or [])
    })
    month_holidays = {
        holiday.date: holiday.name
        for holiday in AcademicHoliday.objects.filter(
            academic_year__is_active=True,
            date__year=today.year,
            date__month=today.month,
        )
    }
    calendar_cells = []
    for week in py_calendar.Calendar(firstweekday=6).monthdatescalendar(
        today.year, today.month
    ):
        for day in week:
            in_year = bool(
                active_year and active_year.starts_on <= day <= active_year.ends_on
            )
            calendar_cells.append({
                "day": day.day,
                "in_month": day.month == today.month,
                "is_today": day == today,
                "is_meeting": in_year and day.weekday() in meeting_weekday_numbers,
                "holiday_name": month_holidays.get(day, ""),
            })
    calendar_month = today.replace(day=1)

    detail_offering = (
        CourseOffering.objects.filter(status=PublicationStatus.PUBLISHED)
        .select_related(
            "course", "academic_year_level__level",
            "academic_year_level__academic_year",
        )
        .prefetch_related("lessons", "quizzes")
        .order_by("course__name")
        .first()
    )

    # --- Participation ---
    attendance_by_action = {
        row["action"]: row["n"]
        for row in AttendanceRecord.objects.values("action").annotate(n=Count("id"))
    }
    recent_attendance = (
        AttendanceRecord.objects.select_related("student", "course_offering__course")
        .order_by("-scanned_at")[:6]
    )
    progress_total = LectureProgress.objects.count()
    progress_avg = LectureProgress.objects.aggregate(avg=Avg("percent"))["avg"] or 0
    progress_completed = LectureProgress.objects.filter(
        completed_at__isnull=False
    ).count()
    recent_progress = (
        LectureProgress.objects.select_related("student", "lesson")
        .order_by("-id")[:5]
    )

    # --- Analytics ---
    formulas = (
        PromotionFormula.objects.select_related(
            "academic_year_level", "academic_year_level__level",
            "academic_year_level__academic_year", "course_offering__course",
        )
        .annotate(rule_count=Count("rules", distinct=True))
        .order_by("-updated_at")[:5]
    )
    result_counts = {
        row["final_status"]: row["n"]
        for row in EvaluationResult.objects.values("final_status").annotate(n=Count("id"))
    }
    recent_history = (
        PromotionHistory.objects.select_related("student")
        .order_by("-id")[:5]
    )
    report_scopes = []
    for scope in active_scopes:
        report_scopes.append({
            "scope": scope,
            "students": Enrollment.objects.filter(
                academic_year_level=scope,
                enrollment_type=Enrollment.Type.NORMAL,
            ).count(),
            "grade_avg": Grade.objects.filter(
                quiz__course_offering__academic_year_level=scope
            ).aggregate(avg=Avg("total_grade"))["avg"],
        })
    recent_grades = (
        Grade.objects.select_related(
            "user", "quiz", "quiz__course_offering__course",
        )
        .order_by("-submitted_at")[:6]
    )
    grades_average = Grade.objects.aggregate(avg=Avg("total_grade"))["avg"] or 0

    context = {
        "users_total": User.objects.count(),
        "role_counts": role_counts,
        "pending_applications": pending_applications[:5],
        "pending_applications_count": pending_applications.count(),
        "recent_users": recent_users,
        "courses_total": Course.objects.count(),
        "courses": courses,
        "offerings_total": CourseOffering.objects.count(),
        "offerings_published": CourseOffering.objects.filter(
            status=PublicationStatus.PUBLISHED,
        ).count(),
        "lessons_total": lessons_total,
        "lesson_status_counts": lesson_status_counts,
        "recent_lessons": recent_lessons,
        "quizzes_total": Quiz.objects.count(),
        "quiz_status_counts": quiz_status_counts,
        "recent_quizzes": recent_quizzes,
        "draft_lessons": draft_lessons,
        "lessons_with_media": lessons_with_media,
        "levels": levels,
        "years": years,
        "calendar_scopes": calendar_scopes,
        "holidays": holidays,
        "calendar_cells": calendar_cells,
        "calendar_month": calendar_month,
        "meeting_weekday_numbers": meeting_weekday_numbers,
        "detail_offering": detail_offering,
        "enrollments_total": Enrollment.objects.count(),
        "recent_enrollments": (
            Enrollment.objects.select_related(
                "student", "academic_year_level", "academic_year_level__level",
            ).order_by("-enrolled_at")[:5]
        ),
        "grades_total": Grade.objects.count(),
        "submissions_total": Submission.objects.count(),
        "attendance_by_action": attendance_by_action,
        "attendance_today": AttendanceRecord.objects.filter(
            attendance_date=today,
        ).count(),
        "recent_attendance": recent_attendance,
        "students_with_qr": User.objects.filter(qr_token__isnull=False).count(),
        "progress_total": progress_total,
        "progress_avg": progress_avg,
        "progress_completed": progress_completed,
        "recent_progress": recent_progress,
        "formulas": formulas,
        "result_counts": result_counts,
        "recent_history": recent_history,
        "report_scopes": report_scopes,
        "recent_grades": recent_grades,
        "grades_average": grades_average,
    }
    return render(request, "theme_showcase/admin_proposal.html", context)

def about_page(request):
    return render(request, "about.html", get_institute_copy(translation.get_language(), "about"))

def program_page(request):
    return render(request, "program.html", get_institute_copy(translation.get_language(), "courses"))

# Detail Class [handles with and without pk routes]
class ProfileDetail(LoginProtection, DetailView):
    model = User
    pk_url_kwarg = "user_id"
    template_name = "profile.html"

    def get_context_data(self, **kwargs):
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
        context["can_upload_missing_documents"] = user == self.request.user
        missing_documents_form = MissingApplicationDocumentsForm(instance=user)
        context["missing_documents_form"] = missing_documents_form
        academic_payment_scopes = list(academic_payment_scopes_for_student(user)) if profile_editable else []
        context["academic_payment_scopes"] = academic_payment_scopes
        context["academic_payment_form"] = AcademicPaymentForm(student=user) if profile_editable else None
        missing_profile_items = []
        if profile_editable:
            details_form = SignupDetailsForm(instance=user)
            for field_name in sorted(SignupDetailsForm.SIGNUP_REQUIRED_FIELDS | {"full_name"}):
                field = details_form.fields.get(field_name)
                if field and not field.disabled and field.required:
                    missing_profile_items.append(str(field.label))
            missing_profile_items.extend(str(field.label) for field in missing_documents_form.fields.values())
            missing_profile_items.extend(
                f"{scope.level.display_name} — {scope.academic_year.name}"
                for scope in academic_payment_scopes
            )
        context["missing_profile_items"] = missing_profile_items
        context["has_profile_gaps"] = bool(missing_profile_items)
        context["academic_payments"] = list(
            AcademicPayment.objects.filter(student=user)
            .select_related("academic_year_level__academic_year", "academic_year_level__level")
        )
        document_fields = (
            ("identity_front", "identity_front_key", _("Identity Front")),
            ("identity_back", "identity_back_key", _("Identity Back")),
            ("payment", "payment_key", _("Payment")),
            ("profile", "profile_image_key", _("Profile")),
        )
        application_documents = []
        for document_type, model_field, label in document_fields:
            key = getattr(user, model_field, None)
            if not key:
                continue
            application_documents.append({
                "type": document_type,
                "label": label,
                "url": reverse("application-document", args=[user.pk, document_type]),
                "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                "is_image": (mimetypes.guess_type(key)[0] or "").startswith("image/"),
            })
        context["application_documents"] = application_documents
        if user_has_management_role(user):
            offerings = CourseOffering.objects.select_related(
                "course", "academic_year_level__academic_year", "academic_year_level__level"
            ).filter(
                academic_year_level__academic_year__is_active=True,
            ).order_by("academic_year_level__level__ordering", "course__name")
        else:
            offerings = accessible_offerings(user).order_by(
                "academic_year_level__academic_year__ordering",
                "academic_year_level__level__ordering",
                "course__name",
            )
        offerings = list(offerings)
        offering_ids = [offering.pk for offering in offerings]
        course_progress = {
            offering_id: {
                "watch_time_minutes": 0,
                "watch_time_seconds": 0,
                "watch_percent": 0,
                "watched_parts": 0,
                "watchable_parts": 0,
                "attendance_scanned": 0,
                "attendance_total": 0,
                "weekly_taken": 0,
                "weekly_total": 0,
                "weekly_grades": [],
                "final_taken": 0,
                "final_total": 0,
                "final_grades": [],
            }
            for offering_id in offering_ids
        }

        if offering_ids:
            media_parts = {}
            if user.study_mode == "online":
                lesson_rows = Lesson.objects.filter(
                    course_offering_id__in=offering_ids,
                    **({} if user_has_management_role(user) else {"status": PublicationStatus.PUBLISHED}),
                ).values("id", "course_offering_id", "links")
                for lesson in lesson_rows:
                    try:
                        links = json.loads(lesson["links"] or "[]")
                    except (TypeError, json.JSONDecodeError):
                        links = []
                    for link in links if isinstance(links, list) else []:
                        if not isinstance(link, dict):
                            continue
                        if link.get("file_type") not in {"video", "audio"}:
                            continue
                        part_id = link.get("part_id")
                        if part_id:
                            media_parts[(lesson["id"], part_id)] = lesson["course_offering_id"]

            if media_parts and user.study_mode == "online":
                progress_rows = LectureProgress.objects.filter(
                    student=user,
                    lesson_id__in=[lesson_id for lesson_id, _part_id in media_parts],
                ).values("lesson_id", "part_id", "merged_ranges", "unique_seconds", "percent")
                progress_by_part = {
                    (row["lesson_id"], row["part_id"]): row
                    for row in progress_rows
                }
                for part_key, offering_id in media_parts.items():
                    metric = course_progress[offering_id]
                    metric["watchable_parts"] += 1
                    progress = progress_by_part.get(part_key)
                    if not progress:
                        continue
                    percent = min(int(progress["percent"] or 0), 100)
                    ranges = progress["merged_ranges"] if isinstance(progress["merged_ranges"], list) else []
                    seconds = progress["unique_seconds"] or unique_seconds(ranges)
                    metric["watch_time_seconds"] += seconds
                    metric["watch_percent"] += percent
                    if percent >= 80:
                        metric["watched_parts"] += 1

                for metric in course_progress.values():
                    metric["watch_time_minutes"] = int(round(metric["watch_time_seconds"] / 60))
                    if metric["watchable_parts"]:
                        metric["watch_percent"] = round(
                            metric["watch_percent"] / metric["watchable_parts"]
                        )

            meeting_dates = defaultdict(set)
            for row in AcademicYearLevelMeeting.objects.filter(
                course_offering_id__in=offering_ids
            ).values("course_offering_id", "meeting_date"):
                meeting_dates[row["course_offering_id"]].add(row["meeting_date"])

            scanned_dates = defaultdict(lambda: {"entrance": set(), "exit": set()})
            if user.study_mode != "online":
                for row in AttendanceRecord.objects.filter(
                    student=user,
                    course_offering_id__in=offering_ids,
                ).values("course_offering_id", "attendance_date", "action"):
                    scanned_dates[row["course_offering_id"]][row["action"]].add(row["attendance_date"])

            for offering_id, dates in meeting_dates.items():
                attendance = scanned_dates[offering_id]
                course_progress[offering_id]["attendance_total"] = len(dates)
                course_progress[offering_id]["attendance_scanned"] = len(
                    attendance["entrance"] & attendance["exit"]
                )

            quiz_filter = {} if user_has_management_role(user) else {"status": PublicationStatus.PUBLISHED}
            quizzes = list(
                Quiz.objects.filter(course_offering_id__in=offering_ids, **quiz_filter)
                .select_related("quiz_type")
                .order_by("opening_date", "pk")
            )
            quiz_ids = [quiz.pk for quiz in quizzes]
            for quiz in quizzes:
                if not quiz.quiz_type:
                    continue
                metric = course_progress[quiz.course_offering_id]
                if quiz.quiz_type.code == "weekly":
                    metric["weekly_total"] += 1
                elif quiz.quiz_type.code == "final":
                    metric["final_total"] += 1

            for grade in Grade.objects.filter(user=user, quiz_id__in=quiz_ids).select_related(
                "quiz", "quiz__quiz_type"
            ).order_by("quiz__opening_date", "quiz_id"):
                if not grade.quiz.quiz_type:
                    continue
                grade_data = {
                    "name": grade.quiz.name,
                    "score": grade.total_grade,
                    "total": grade.quiz.total_grade,
                }
                metric = course_progress[grade.quiz.course_offering_id]
                if grade.quiz.quiz_type.code == "weekly":
                    metric["weekly_taken"] += 1
                    metric["weekly_grades"].append(grade_data)
                elif grade.quiz.quiz_type.code == "final":
                    metric["final_taken"] += 1
                    metric["final_grades"].append(grade_data)

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
                    "section_name": f"{lvl.display_name} — {scope.academic_year.name}",
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

        return context

    def get(self, request, *args, **kwargs):
        if self.kwargs.get(self.pk_url_kwarg) and not can_manage_content(request.user):
            return HttpResponse(_("Unauthorized"), status=403) # Translate "Unauthorized"
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object != request.user:
            return HttpResponse(_("Unauthorized"), status=403)

        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=request.user.pk)
            form = SignupDetailsForm(request.POST, instance=locked_user)
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
    user = User.objects.get(pk=request.user.pk)
    management_preview = user_has_management_role(user)
    offerings_queryset = accessible_offerings(user, include_management=management_preview).order_by(
        "-academic_year_level__academic_year__ordering",
        "-academic_year_level__level__ordering",
        "course__name",
    )
    if course_id is not None:
        get_object_or_404(Course, pk=course_id)
        offerings_queryset = offerings_queryset.filter(course_id=course_id)
    offerings = list(offerings_queryset)
    normal_scopes = set()
    targeted_offerings = set()
    if not management_preview:
        enrollments = Enrollment.objects.filter(
            student=user,
            status__in=(Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED),
        ).values_list("enrollment_type", "academic_year_level_id", "course_offering_id")
        normal_scopes = {scope_id for enrollment_type, scope_id, _ in enrollments if enrollment_type == Enrollment.Type.NORMAL}
        targeted_offerings = {offering_id for enrollment_type, _, offering_id in enrollments if enrollment_type != Enrollment.Type.NORMAL and offering_id}

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
    user = User.objects.get(pk=request.user.pk)
    offering = get_accessible_offering_or_403(user, offering_id)
    management_preview = user_has_management_role(user)
    lessons = offering.lessons.all() if management_preview else offering.lessons.filter(status=PublicationStatus.PUBLISHED)
    quizzes = offering.quizzes.all() if management_preview else offering.quizzes.filter(status=PublicationStatus.PUBLISHED)
    return render(request, "course_detail.html", {
        "offering": offering,
        "offering_id": offering.pk,
        "course": offering.course,
        "lessons": lessons,
        "quizzes": quizzes,
        "management_preview": management_preview,
    })

@login_required(login_url=LOGIN_URL)
def view_lesson_details(request, offering_id, lesson_id):
    user = User.objects.get(pk=request.user.pk)
    offering = get_accessible_offering_or_403(user, offering_id)
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))
    lesson_links = json.loads(lesson.links)
    return render(request, "lesson_stream.html", {
        "offering_id": offering.pk,
        "lesson_id": lesson_id,
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
    })

@login_required(login_url=LOGIN_URL)
def stream_lesson(request, offering_id, lesson_id, file_index):
    user = User.objects.get(pk=request.user.pk)
    offering = get_accessible_offering_or_403(user, offering_id)
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))
    lesson_links = json.loads(lesson.links)
    if file_index < 0 or file_index >= len(lesson_links):
        raise Http404
    file_data = lesson_links[file_index]

    file_name = file_data.get("name")
    file_key = file_data.get("id")
    file_type = file_data.get("file_type")

    if file_type == "book":
        return JsonResponse({"url": generate_unique_url(CLOUD_CLIENT, bucket_name, file_key, expires_in=3600)})

    return JsonResponse(
        {"error": _("Media playback requires a viewing session.")},
        status=410,
    )
    

# @login_required(login_url=LOGIN_URL)
# def retrieve_segment(request, lesson_id, segment_id):
#     try:
#         username = request.user
#         lesson = get_object_or_404(Lesson, pk=lesson_id)

#         if not lesson.has_segment(segment_id):
#             logger.error("Segment with id : {segment_id} is not found in {lesson.name}")
#             return HttpResponse('Not Found!', status=404)

#         file_metadata = DRIVE_CLIENT.files().get(fileId=segment_id, fields="name").execute()
#         file_name = file_metadata.get("name")

#         video_segment = DRIVE_CLIENT.files().get_media(fileId=segment_id)
#         ts_file = download_from_drive(video_segment)
#         logger.info(f"{file_name} TS file of {lesson.name} is loaded!")

#         return FileResponse(ts_file)
    
#     except Http404:
#         logger.error(f"Retrieving segments for lesson with id : {lesson_id} is not found for user: {username}")
#         raise Http404

#     except HttpError as e:
#         logger.error(f"Downloading {file_name} segments file is failed for user : {username}")
#         logger.error(f"Stack Trace : {str(e)}")

@login_required(login_url=LOGIN_URL)
def take_exam(request, offering_id, quiz_id, *, allow_management=False):
    try:
        user = User.objects.get(pk=request.user.pk)
        offering = get_accessible_offering_or_403(
            user,
            offering_id,
            write=request.method == "POST",
            allow_management=allow_management,
        )
        quiz = get_object_or_404(Quiz, pk=quiz_id, course_offering=offering)

        submission_datetime = now()
        # Quiz can be submitted from opening time through the 30-minute closing buffer
        can_submit = is_quiz_open(quiz, submission_datetime, user)
        _, effective_closing_date = quiz_window(quiz, user)

        # Check if user has previously taken this quiz
        quiz_mode, grade = get_student_quiz_status(quiz, user, submission_datetime)
        total_grade = grade.total_grade if grade else 0

        if request.method == "GET":
            logger.info(f"User : {user} is accessing {quiz.name} in {offering.course.name} offering")

            # Determine quiz mode using cohort-year window logic
            if user_has_management_role(user):
                # Admins/teachers always see in "view" mode for student quizzes
                quiz_mode = "view"
                query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user) if grade else Question.objects.filter(quiz_id=quiz_id)
            elif grade:
                # Student has already submitted – always show their submission
                quiz_mode = "view"
                query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user)
            elif quiz_mode == "exam":
                quiz_mode = "exam"
                query_set = Question.objects.filter(quiz_id=quiz_id)
            else:
                # Quiz is closed or re-opened for a newer cohort; student never submitted
                quiz_mode = "closed_unsolved"
                query_set = Question.objects.filter(quiz_id=quiz_id)

            # Serialize questions; strip answer data to prevent leakage
            if quiz_mode == "exam":
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

            return render(request, "display_quiz.html", {
                "quiz_name": quiz.name,
                "username": request.user.username,
                "questions": questions,
                "is_student": True,
                "mode": quiz_mode,
                "extended_view": "base.html",
                "id": "container",
                "total_grade": total_grade,
                "closing_date": ensure_aware(effective_closing_date).timestamp(),
                "exam_taken": True if grade else False,
                "back_url": reverse("course-details", args=[offering_id]),
                "quiz_closing_date_str": format_user_datetime(effective_closing_date, user, "%d/%m/%Y %H:%M"),
            })
        
        elif request.method == "POST":
            # Prevent another submission
            if not grade:
                # Guard: reject if quiz is no longer submittable
                if not can_submit or not is_quiz_in_user_window(quiz, user):
                    # Send back to main page with error message TODO
                    return HttpResponse(_("Invalid Request, submission is closed!")) # Translate
                
                logger.info(f"User : {user} has submitted {quiz} at {submission_datetime.strftime('%d/%m/%Y, %H:%M:%S')}")

                questions_data, not_used = unpack_quiz_form(request.POST)
                question_by_id = {
                    question.pk: question
                    for question in Question.objects.filter(quiz_id=quiz_id)
                }
                valid_question_ids = set(question_by_id)

                # Create Quesitons
                submissions = []
                repeated_submissions = set()
                total_grade = 0
                for data in questions_data.values():
                    question_id = int(data['id'])
                    if question_id not in valid_question_ids:
                        continue
                    submitted_answer = data.get("answer", "")
                    if question_id in repeated_submissions:
                        continue
                    repeated_submissions.add(question_id)
                    # Create instance
                    submission = Submission(
                        question=question_by_id[question_id],
                        submitted_answer=submitted_answer,
                        user=user,
                    )
                    submission.assign_grade()

                    # Sum grades
                    total_grade += submission.grade

                    # Append for bulk create!
                    submissions.append(submission)
                
                # For leaved questions or error of not submitting all questions
                unanswered_questions = valid_question_ids - repeated_submissions
                for qid in unanswered_questions:
                    submissions.append(
                        Submission(
                            question=question_by_id[qid],
                            submitted_answer="-",
                            user=user,
                        )
                    )

                try:
                    with transaction.atomic():
                        locked_user = User.objects.select_for_update().get(pk=user.pk)
                        if Grade.objects.filter(user=locked_user, quiz=quiz).exists():
                            return redirect(reverse("quiz-details", args=[offering_id, quiz_id]))
                        for submission in submissions:
                            submission.user = locked_user
                        Submission.objects.bulk_create(submissions)
                        Grade.objects.create(
                            quiz=quiz,
                            user=locked_user,
                            submitted_at=submission_datetime,
                            total_grade=total_grade,
                        )
                        logger.info(f"{user}'s submission is added successfully to {quiz}")
                    success(request, _("Quiz is sent successfully!"), extra_tags="alert-success") # Translate

                except Exception as e:
                    error(request, _("Sending quiz has failed, Please Try again!"), extra_tags="alert-danger") # Translate
                    logger.error(f"{user}'s submission failed for {quiz.name}")
                    logger.error(f"Stack Traceback: {e}")
            
            return redirect(reverse("quiz-details", args=[offering_id, quiz_id]))
        
        else:
            return HttpResponse(_("Not allowed method"), 400) # Translate
        
    except Http404:
            logger.error(f"Offering with id: {offering_id} or Quiz with id: {quiz_id} not found for user: {user.username}")
            raise Http404
    
# Admin Views
@capability_required(can_manage_content)
def admin_panel(request):
    logger.info(f"User : {request.user} accesses admin panel successfully")

    today = timezone.now().date()
    first_of_month = today.replace(day=1)
    week_ago = today - timedelta(days=7)

    # ====== 1. USER ANALYTICS ======
    users_total = User.objects.count()

    users_by_role = list(
        User.objects.values('role__role').annotate(count=Count('id'))
    )
    users_by_app_status = list(
        User.objects.values('application_status').annotate(count=Count('id'))
    )
    users_by_study_mode = list(
        User.objects.values('study_mode').annotate(count=Count('id'))
    )

    new_users_month = User.objects.filter(joined_date__gte=first_of_month).count()
    new_users_week = User.objects.filter(joined_date__gte=week_ago).count()

    top_cities = list(
        User.objects.values('city')
        .annotate(count=Count('id'))
        .filter(city__isnull=False)
        .exclude(city='')
        .order_by('-count')[:10]
    )

    # ====== 2. COURSE & LESSON ANALYTICS ======
    courses_total = Course.objects.count()
    courses_by_level = list(
        Course.objects.values('level__ordering').annotate(count=Count('id')).order_by('level__ordering')
    )

    lessons_total = Lesson.objects.count()
    lessons_by_status = list(
        Lesson.objects.values('status').annotate(count=Count('id'))
    )
    avg_lessons_per_course = round(lessons_total / courses_total, 1) if courses_total else 0

    # ====== 3. OFFERING & ACADEMIC YEAR ANALYTICS ======
    offerings_total = CourseOffering.objects.count()
    offerings_by_status = list(
        CourseOffering.objects.values('status').annotate(count=Count('id'))
    )
    academic_years_total = AcademicYear.objects.count()
    current_academic_years = AcademicYear.objects.filter(is_active=True)

    # ====== 4. ENROLLMENT ANALYTICS ======
    enrollments_total = Enrollment.objects.count()
    enrollments_by_status = list(
        Enrollment.objects.values('status').annotate(count=Count('id'))
    )
    enrollments_by_type = list(
        Enrollment.objects.values('enrollment_type').annotate(count=Count('id'))
    )
    enrollments_by_year = list(
        Enrollment.objects.values('academic_year_level__academic_year__name', 'academic_year_level__academic_year__pk')
        .annotate(count=Count('id'))
        .order_by('-academic_year_level__academic_year__starts_on')
    )
    enrollments_by_level = list(
        Enrollment.objects.values('academic_year_level__level__ordering')
        .annotate(count=Count('id'))
        .order_by('academic_year_level__level__ordering')
    )

    # ====== 5. QUIZ & GRADE ANALYTICS ======
    quizzes_total = Quiz.objects.count()
    quizzes_by_status = list(
        Quiz.objects.values('status').annotate(count=Count('id'))
    )
    total_submissions = Grade.objects.count()
    avg_grade = Grade.objects.aggregate(avg=Avg('total_grade'))['avg'] or 0
    max_quiz_grade = Quiz.objects.aggregate(max=Sum('total_grade'))['max'] or 0

    # ====== 6. LECTURE PROGRESS ANALYTICS ======
    progress_total = LectureProgress.objects.count()
    students_with_progress = (
        LectureProgress.objects.values('student').distinct().count()
    )
    completed_lectures = LectureProgress.objects.filter(
        completed_at__isnull=False
    ).count()
    avg_completion = (
        LectureProgress.objects.aggregate(avg=Avg('percent'))['avg'] or 0
    )

    # ====== 7. ATTENDANCE ANALYTICS ======
    attendance_total = AttendanceRecord.objects.count()
    today_attendance = AttendanceRecord.objects.filter(
        attendance_date=today
    ).count()
    today_students = (
        AttendanceRecord.objects.filter(attendance_date=today)
        .values('student')
        .distinct()
        .count()
    )

    # ====== 8. RECENT ACTIVITY ======
    recent_enrollments = Enrollment.objects.select_related(
        'student', 'academic_year_level__academic_year'
    ).order_by('-enrolled_at')[:5]

    recent_submissions = Grade.objects.select_related(
        'user', 'quiz__course_offering__course'
    ).order_by('-submitted_at')[:5]

    return render(request, "admin_panel.html", {
        "today": today,
        "admin_metrics": {
            "users": users_total,
            "courses": courses_total,
            "lessons": lessons_total,
            "quizzes": quizzes_total,
            "academic_years": academic_years_total,
            "offerings": offerings_total,
        },
        # User Analytics
        "users_by_role": users_by_role,
        "users_by_app_status": users_by_app_status,
        "users_by_study_mode": users_by_study_mode,
        "new_users_month": new_users_month,
        "new_users_week": new_users_week,
        "top_cities": top_cities,
        # Course & Lesson Analytics
        "courses_by_level": courses_by_level,
        "lessons_by_status": lessons_by_status,
        "avg_lessons_per_course": avg_lessons_per_course,
        # Offering & Academic Year Analytics
        "offerings_by_status": offerings_by_status,
        "current_academic_years": current_academic_years,
        # Enrollment Analytics
        "enrollments_total": enrollments_total,
        "enrollments_by_status": enrollments_by_status,
        "enrollments_by_type": enrollments_by_type,
        "enrollments_by_year": enrollments_by_year,
        "enrollments_by_level": enrollments_by_level,
        # Quiz & Grade Analytics
        "quizzes_by_status": quizzes_by_status,
        "total_submissions": total_submissions,
        "avg_grade": round(avg_grade, 1),
        # Progress Analytics
        "progress_total": progress_total,
        "students_with_progress": students_with_progress,
        "completed_lectures": completed_lectures,
        "avg_completion": round(avg_completion, 1),
        # Attendance Analytics
        "attendance_total": attendance_total,
        "today_attendance": today_attendance,
        "today_students": today_students,
        # Recent Activity
        "recent_enrollments": recent_enrollments,
        "recent_submissions": recent_submissions,
    })

@capability_required(can_manage_content)
def export_users_csv(request):
    return export_users_to_csv()

@capability_required(can_manage_content)
def user_dashboard(request):
    view = "user"
    
    name = request.GET.get("name", None)
    role_value = request.GET.get("filtering", None)
    user = request.user

    query = Q()
    if name:
        name = normalize_search_text(name)
        query &= normalized_contains_q(
            ("username", "email", "first_name", "last_name", "phone", "identity_number"),
            name,
        )
    if role_value:
        role_name = Role.get_by_readable_value(role_value)
        query &= Q(role=role_name)
    users = User.objects.filter(query)
    
    logger.info(f"User : {user} filters {view}s using {name} name and {role_value} role")

    # Apply sorting
    sort_by = request.GET.get('sort', 'joined_date')
    order = request.GET.get('order', 'asc')
    
    # Simple explicit map for safety
    column_mapping = {
        'Username': 'username',
        'First Name': 'first_name',
        'Last Name': 'last_name',
        'Email': 'email',
        'Role': 'role__role',
        'Joined Date': 'joined_date'
    }
    
    model_sort_by = column_mapping.get(sort_by, sort_by)  
    if order == 'desc':
        users = users.order_by(f'-{model_sort_by}')
    else:
        users = users.order_by(model_sort_by)

    context = {
        "name_value" : name or "",
        "filtering" : role_value or "",
        "columns" : User.get_columns(),
        "options" : [_('Choose Role'), *Role.get_readable_values()],
        "search_placeholder": _("Search username, email, name, phone, or national ID"),
    }

    return render_dashboard(request, users, view, context)


@capability_required(can_manage_content)
def user_profile(request, user_id):
    user = get_object_or_404(
        User.objects.select_related("role", "decided_by"),
        pk=user_id,
    )
    form = UserUpdateForm(request.POST or None, instance=user)
    _restrict_user_update_form(form, request.user)

    if request.method == "POST" and form.is_valid():
        original = User.objects.get(pk=user.pk)
        actor_role = getattr(getattr(request.user, "role", None), "role", None)
        try:
            with transaction.atomic():
                updated_user = form.save()
                if (
                    original.application_status != updated_user.application_status
                    or original.is_active and not updated_user.is_active
                    or original.role_id != updated_user.role_id
                ):
                    revoke_user_mobile_access(updated_user)
                scope = form.cleaned_data.get("enrollment_scope")
                if actor_role == "admin" and scope:
                    set_user_normal_enrollment_scope(updated_user, request.user, scope)
        except ValidationError as exc:
            form.add_error("enrollment_scope", exc)
        else:
            if user.pk == request.user.pk and form.cleaned_data.get("password"):
                update_session_auth_hash(request, updated_user)
            messages.success(request, _("User data updated successfully."))
            return redirect("user-profile", user_id=user.pk)

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
        for document_type, field_name, label in (
            ("identity_front", "identity_front_key", _("Identity Front")),
            ("identity_back", "identity_back_key", _("Identity Back")),
            ("payment", "payment_key", _("Payment")),
            ("profile", "profile_image_key", _("Profile")),
        ):
            if getattr(user, field_name, None):
                application_documents.append({
                    "label": label,
                    "url": reverse("application-document", args=[user.pk, document_type]),
                    "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                })

    return render(request, "user_detail.html", {
        "profile_user": user,
        "edit_form": form,
        "active_enrollment": active_enrollment,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Users"), reverse("user-dashboard")),
            (user.get_full_name() or user.username, None),
        ]),
        "can_view_application_data": can_view_application_data,
        "application_documents": application_documents,
    })


@capability_required(can_manage_academic_setup)
def historical_intake(request):
    form = HistoricalIntakeForm(request.POST or None)
    exceptional_form = ExceptionalCourseAssignmentForm(request.POST or None)
    bulk_form = HistoricalBulkIntakeForm(request.POST or None)
    preview = []
    upload_digest = ""
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
        elif request.POST.get("action") == "bulk_historical" and bulk_form.is_valid():
            cleaned = bulk_form.cleaned_data
            identifiers = cleaned["student_identifiers"]
            rows = []
            for identifier in identifiers:
                user = User.objects.filter(pk=int(identifier)).first() if identifier.isdigit() else User.objects.filter(username=identifier).first()
                if user is None or not user.role or user.role.role != "student":
                    bulk_form.add_error("student_identifiers", _("Student account not found: %(identifier)s") % {"identifier": identifier})
                    break
                rows.append({
                    "source_name": user.get_full_name() or user.username,
                    "source_level": str(cleaned["source_year_level"].level.ordering),
                    "source_academic_year": cleaned["source_year_level"].academic_year.name,
                    "historical_outcome": cleaned["historical_outcome"],
                    "account_action": "find",
                    "lms_user_id": str(user.pk),
                    "lms_username": user.username,
                    "lms_email": user.email,
                    "destination_academic_year": cleaned["destination_scope"].academic_year.name if cleaned["destination_scope"] else "",
                    "destination_level": str(cleaned["destination_scope"].level.ordering) if cleaned["destination_scope"] else "",
                    "promote_now": "yes" if cleaned["promote_now"] else "no",
                    "failed_course_offering_ids": cleaned["exceptional_offering_ids"],
                    "promotion_reason": cleaned["promotion_reason"],
                    "admin_note": cleaned["notes"],
                })
            if rows and not bulk_form.errors:
                try:
                    with transaction.atomic():
                        for index, row in enumerate(rows):
                            intake_historical_row(row=row, actor=request.user, source_key=f"bulk:{request.user.pk}:{uuid.uuid4().hex}:{index}", source_file="admin-bulk")
                    messages.success(request, _("Historical intake applied for %(count)d student(s).") % {"count": len(rows)})
                    return redirect("historical-intake")
                except ValidationError as exc:
                    bulk_form.add_error(None, str(exc))
        elif request.FILES.get("intake_file"):
            try:
                rows, upload_digest = parse_intake_upload(request.FILES["intake_file"])
                preview = preview_intake_rows(rows)
                if request.POST.get("apply_upload") == "yes":
                    errors = [item for item in preview if item["action"] == "error"]
                    if errors:
                        raise ValidationError(_("Resolve all upload errors before applying the intake."))
                    for index, row in enumerate(rows, start=2):
                        intake_historical_row(
                            row=row,
                            actor=request.user,
                            source_key=f"upload:{upload_digest}:{index}",
                            source_file=request.FILES["intake_file"].name,
                            source_row=index,
                        )
                    messages.success(request, _("Historical intake applied for %(count)d student(s).") % {"count": len(rows)})
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
    summaries = HistoricalAcademicSummary.objects.select_related(
        "student", "academic_year_level__academic_year", "academic_year_level__level", "promotion_history__actor"
    ).order_by("-created_at")
    summary_page_obj = Paginator(summaries, 25).get_page(request.GET.get("page", 1))
    return render_page(request, "historical_intake.html", "partials/historical_intake_content.html", {
        "form": form,
        "exceptional_form": exceptional_form,
        "bulk_form": bulk_form,
        "summaries": summary_page_obj.object_list,
        "summary_page_obj": summary_page_obj,
        "pagination_query": pagination_query_string(request),
        "preview": preview,
        "upload_digest": upload_digest,
        "intake_columns": INTAKE_COLUMNS,
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

@capability_required(can_manage_content)
def user_bulk_create(request):
    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_file = request.FILES['csv_file']
            if not csv_file.name.endswith('.csv'):
                error(request, _('This is not a CSV file'), extra_tags="alert-danger")
                return redirect('user-bulk-create')

            try:
                decoded_file = csv_file.read().decode('utf-8').splitlines()
                reader = csv.reader(decoded_file)
                # Skip header
                next(reader, None)

                users_to_create = []
                errors_list = []
                line_number = 1

                with transaction.atomic():
                    for row in reader:
                        line_number += 1
                        if not row: continue # skip empty rows
                        
                        try:
                            username, first_name, last_name, password, role_name = row
                            
                            if User.objects.filter(username=username).exists():
                                errors_list.append(_("Line %(line)d: User '%(username)s' already exists.") % {
                                    "line": line_number,
                                    "username": username,
                                })
                                continue

                            try:
                                role = Role.objects.get(role=role_name.lower().strip())
                            except Role.DoesNotExist:
                                errors_list.append(_("Line %(line)d: Role '%(role)s' does not exist.") % {
                                    "line": line_number,
                                    "role": role_name,
                                })
                                continue

                            user = User(
                                username=username.strip(),
                                first_name=first_name.strip(),
                                last_name=last_name.strip(),
                                role=role
                            )
                            user.set_password(password)
                            user.full_clean()
                            user.save()
                            users_to_create.append(user)

                        except ValueError:
                            errors_list.append(_("Line %(line)d: Incorrect number of columns. Expected 5, got %(count)d.") % {
                                "line": line_number,
                                "count": len(row),
                            })
                        except ValidationError as e:
                            errors_list.append(_("Line %(line)d: Validation error for user '%(username)s': %(errors)s") % {
                                "line": line_number,
                                "username": username,
                                "errors": ", ".join(e.messages),
                            })
                        except Exception as e:
                             errors_list.append(_("Line %(line)d: An unexpected error occurred: %(error)s") % {
                                 "line": line_number,
                                 "error": e,
                             })

                    if errors_list:
                        # If there are errors, raise an exception to trigger a rollback of the transaction
                        raise Exception("Errors found in CSV file.")

                success(request, _("%(count)d users have been created successfully!") % {
                    "count": len(users_to_create),
                }, extra_tags="alert-success")
                return redirect('user-dashboard')

            except Exception as e:
                # This will catch the explicit raise and any other exceptions
                for err in errors_list:
                    error(request, err, extra_tags="alert-danger")
                if not errors_list:
                     error(request, _("An error occurred: %(error)s") % {"error": e}, extra_tags="alert-danger")
                return redirect('user-bulk-create')

    else:
        form = CSVUploadForm()
    
    return render(request, 'user_bulk_form.html', {'form': form})

# User Dashboard
class CreateUser(UserBaseView, CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy("user-create")
    action = _("create") # Translate action

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        user = User.objects.get(username=self.request.user)
        if user.role and user.role.role != "admin":
            form.fields.pop("role", None)
            form.fields.pop("password", None)
            form.fields.pop("time_zone", None)
        return form


def _restrict_user_update_form(form, actor):
    actor_role = getattr(getattr(actor, "role", None), "role", None)
    if actor_role != "admin":
        for field_name in UserUpdateForm.admin_only_fields:
            form.fields.pop(field_name, None)
        form.fields.pop("password", None)
    return form


class UpdateUser(UserBaseView, UpdateView):
    form_class = UserUpdateForm
    action = _("update") # Translate action

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        return _restrict_user_update_form(form, self.request.user)

    def form_valid(self, form):
        try:
            original = User.objects.get(pk=self.object.pk)
            with transaction.atomic():
                response = super().form_valid(form)
                user = User.objects.get(username=self.request.user)
                if (
                    original.application_status != self.object.application_status
                    or original.is_active and not self.object.is_active
                    or original.role_id != self.object.role_id
                ):
                    revoke_user_mobile_access(self.object)
                scope = form.cleaned_data.get("enrollment_scope")
                if user.role and user.role.role == "admin" and scope:
                    set_user_normal_enrollment_scope(self.object, user, scope)
        except ValidationError as exc:
            form.add_error("enrollment_scope", exc)
            return self.form_invalid(form)
        # If the password field was changed, update the session to keep user logged in
        if self.object.pk == self.request.user.pk and "password" in form.cleaned_data and form.cleaned_data["password"]:
            update_session_auth_hash(self.request, self.object)

        return response

    def get_success_url(self):
        return reverse_lazy("user-update", args=[self.kwargs.get(self.pk_url_kwarg)])
    
class DeleteUser(UserBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=403)
        return super(UserBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("user-dashboard")

# Course Dashboard
@capability_required(can_manage_content)
def course_dashboard(request):   
    name = request.GET.get("name", None)
    year = request.GET.get("filtering", None)
    user = request.user
    view = "course"
    query = Q()
    if name:
        name = normalize_search_text(name)
        query &= normalized_contains_q(("name",), name)
    if year:
        for level_obj in Level.objects.order_by("ordering"):
            if level_obj.display_name == year:
                query &= Q(level__ordering=level_obj.ordering)
    courses = Course.objects.filter(query)
    
    logger.info(f"User : {user} filters users using {name} name and {year} level")

    courses = courses.order_by("name")

    context = {
        "name_value" : name or "",
        "filtering" : year or "",
        "columns" : Course.get_columns(),
        "options" : [_("Choose Academic Year"), *[l.display_name for l in Level.objects.order_by("ordering")]],
    }

    return render_dashboard(request, courses, view, context)

class CreateCourse(CourseBaseView, CreateView):
    action = _("create") # Translate action
    success_url = reverse_lazy("course-create")

class UpdateCourse(CourseBaseView, UpdateView):
    action = _("update") # Translate action

    def get_success_url(self):
        return reverse_lazy("course-update", args=[self.kwargs.get(self.pk_url_kwarg)])
    
class DeleteCourse(CourseBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=403)
        return super(CourseBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("course-dashboard")


def _content_offering_filter_options(academic_year_id=None):
    offerings = CourseOffering.objects.select_related(
        "course", "academic_year_level__level", "academic_year_level__academic_year"
    ).order_by(
        "course__name",
        "academic_year_level__level__ordering",
        "academic_year_level__academic_year__ordering",
    )
    if academic_year_id is not None:
        offerings = offerings.filter(academic_year_level__academic_year_id=academic_year_id)
    return [
        {
            "value": str(offering.pk),
            "label": (
                f"{offering.course.name} — "
                f"{offering.academic_year_level.level.display_name} — "
                f"{offering.academic_year_level.academic_year.name}"
            ),
        }
        for offering in offerings
    ]


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
    return render(request, "content_detail.html", {
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
    exception_form = QuizExceptionalOpeningForm(quiz=quiz) if can_manage_academic_setup(request.user) else None
    return render(request, "content_detail.html", {
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
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Quizzes"), reverse("quiz-dashboard")),
            (quiz.name or _("(untitled)"), None),
        ]),
    })


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
    name = request.GET.get("name", None)
    course = request.GET.get("course", None)
    user = request.user
    view = "lesson"
    try:
        selected_year, academic_years = select_content_academic_year(request)
    except ValidationError as exc:
        return HttpResponse("; ".join(str(message) for message in exc.messages), status=400)

    query = Q(course_offering__academic_year_level__academic_year_id=selected_year.pk)
    if name:
        name = normalize_search_text(name)
        query &= normalized_contains_q(("name",), name)
    if course:
        if course.isdigit() and CourseOffering.objects.filter(
            pk=int(course), academic_year_level__academic_year_id=selected_year.pk
        ).exists():
            query &= Q(course_offering_id=int(course))
        else:
            course = None
    lessons = Lesson.objects.filter(query).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
    )
    
    logger.info(
        "User %s filters lessons using %s name, academic year %s, and %s course",
        user,
        name,
        selected_year.pk,
        course,
    )

    lessons = lessons.order_by("name")

    context = {
        "name_value" : name or "",
        "course_value" : course or "",
        "columns" : Lesson.get_columns(),
        "academic_year_filter": True,
        "academic_years": academic_years,
        "selected_academic_year_id": selected_year.pk,
        "subjects" : _content_offering_filter_options(selected_year.pk),
        "filters" : ["course_filter.html"],
    }

    return render_dashboard(request, lessons, view, context)

@capability_required(can_manage_content)
def create_lesson(request):
    if request.method == "GET":
        course_offerings = CourseOffering.objects.filter(
            academic_year_level__academic_year__is_active=True
        ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year")

        return render(request, "lesson_form.html", {
            "course_offerings" : course_offerings,
            "drive" : list_current_folder(CLOUD_CLIENT, bucket_name)[0],
            "is_root" : True,
            "content_status" : PublicationStatus.DRAFT,
            "content_status_display" : PublicationStatus.DRAFT.label,
            "lesson_description": "",
            "publication_statuses" : PublicationStatus.choices,
        })
    elif request.method == "POST":
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            return HttpResponse(_("Invalid publication status."), status=400)
        lesson_name = request.POST.get("lesson_name", "")
        lesson_description = request.POST.get("description", "").strip()
        offering_id = request.POST.get("course_offering")
        videos = request.POST.getlist("videos", [])
        if lesson_name and offering_id and videos:
            try:
                links = _lesson_links_from_form(request)

                course_offering = get_object_or_404(
                    CourseOffering,
                    pk=offering_id,
                    academic_year_level__academic_year__is_active=True,
                )
                with transaction.atomic():
                    lesson = Lesson.objects.create(
                        name=lesson_name,
                        description=lesson_description or None,
                        course_offering=course_offering,
                        links=json.dumps(links),
                        status=publication_status,
                        publication_event_version=1 if publication_status == PublicationStatus.PUBLISHED else 0,
                    )
                    if publication_status == PublicationStatus.PUBLISHED:
                        create_lesson_publication_event(lesson)
                success(request, _("Lesson is created successfully"), extra_tags="alert-success") # Translate
                logger.info(
                    "Lesson %s is added in offering %s with media length of %s",
                    lesson_name,
                    course_offering.pk,
                    len(links),
                )

            except (Http404, ValidationError) as exc:
                error(request, _("Create lesson has failed, Try again Please!"), extra_tags="alert-danger") # Translate
                logger.error("Lesson link validation failed: %s", exc)
                logger.error("Offering %s is not found to create a lesson!", offering_id)
        else:
            error(request, _("Create lesson has failed, offering, name, and files are required!"), extra_tags="alert-danger")

        return redirect(reverse("lesson-create"))

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
        root = False
        drive, parent_folder = list_current_folder(CLOUD_CLIENT, bucket_name, folder_id, folders_only=folders_only)
    else:
        drive, _ = list_current_folder(CLOUD_CLIENT, bucket_name, folders_only=folders_only)

    return render(request, "drive_files.html", {
        "drive" : drive,
        "parent_folder" : parent_folder,
        "is_root" : root,
        "folders_only" : folders_only  # Pass it to template for subsequent navigations
    })

@capability_required(can_manage_content)
def update_lesson(request, lesson_id):
    if request.method == "GET":
        try:
            lesson = get_object_or_404(Lesson, pk=lesson_id)
            course_offerings = CourseOffering.objects.filter(
                Q(academic_year_level__academic_year__is_active=True) | Q(pk=lesson.course_offering_id)
            ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year")
            return render(request, "lesson_form.html", {
                "course_offerings" : course_offerings,
                "drive" : list_current_folder(CLOUD_CLIENT, bucket_name)[0],
                "is_root" : True,
                "selected_offering_id" : lesson.course_offering_id,
                "lesson_name" : lesson.name,
                "lesson_description": lesson.description or "",
                "videos" : json.loads(lesson.links),
                "content_status" : lesson.status,
                "content_status_display" : lesson.get_status_display(),
                "publication_statuses" : PublicationStatus.choices,
            })
        except Http404:
            logger.error(f"Lesson with id : {lesson_id} is not found!")
            return redirect(reverse("lesson-dashboard"))

    elif request.method == "POST":
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            return HttpResponse(_("Invalid publication status."), status=400)
        lesson = get_object_or_404(Lesson, pk=lesson_id)
        if not lesson.can_edit:
            if publication_status == lesson.status:
                return HttpResponse(_("Published or archived lesson cannot be edited."), status=403)
            previous_status = lesson.status
            with transaction.atomic():
                lesson.status = publication_status
                if previous_status != PublicationStatus.PUBLISHED and publication_status == PublicationStatus.PUBLISHED:
                    lesson.publication_event_version += 1
                lesson.save(update_fields=["status", "updated_date", "publication_event_version"])
                if previous_status != PublicationStatus.PUBLISHED and publication_status == PublicationStatus.PUBLISHED:
                    create_lesson_publication_event(lesson)
            success(request, _("Lesson status is updated successfully"), extra_tags="alert-success")
            return redirect(reverse("lesson-update", args=[lesson_id]))
        lesson_name = request.POST.get("lesson_name", "")
        lesson_description = request.POST.get("description", "").strip()
        offering_id = request.POST.get("course_offering")
        videos = request.POST.getlist("videos", [])
        if lesson_name and offering_id and videos:
            try:
                links = _lesson_links_from_form(request)

                previous_status = lesson.status
                lesson.name = lesson_name
                lesson.description = lesson_description or None
                lesson.course_offering = get_object_or_404(
                    CourseOffering, pk=offering_id
                )
                lesson.links = json.dumps(links)
                lesson.status = publication_status
                with transaction.atomic():
                    if previous_status != PublicationStatus.PUBLISHED and publication_status == PublicationStatus.PUBLISHED:
                        lesson.publication_event_version += 1
                    lesson.save()
                    if previous_status != PublicationStatus.PUBLISHED and publication_status == PublicationStatus.PUBLISHED:
                        create_lesson_publication_event(lesson)

                logger.info(
                    "Lesson %s is updated successfully in offering %s with media length of %s",
                    lesson_name,
                    offering_id,
                    len(links),
                )
                success(request, _("Lesson is updated successfully"), extra_tags="alert-success") # Translate

            except (Http404, ValidationError) as exc:
                error(request, _("Update lesson has failed, Try again Please!"), extra_tags="alert-danger") # Translate
                logger.error("Lesson link validation failed: %s", exc)
            logger.error("Offering %s or Lesson with id %s was not found", offering_id, lesson_id)
        else:
            error(request, _("Update lesson has failed, offering, name, and files are required!"), extra_tags="alert-danger")

        return redirect(reverse("lesson-update", args=[lesson_id]))

class DeleteLesson(LessonBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=403)
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
    if not lesson.can_edit:
        raise ValidationError(_("Only an editable draft lesson can receive new media."))
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


def _serialize_media_job(job, live=None):
    live = live or {}
    return {
        "id": str(job.public_id),
        "filename": job.original_filename,
        "requested_folder": job.requested_folder,
        "source_kind": job.source_kind,
        "status": live.get("status", job.status),
        "phase": live.get("phase", job.phase),
        "progress": int(live.get("progress", job.progress)),
        "attempt_count": job.attempt_count,
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
        with transaction.atomic():
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
                )
                authorization = create_staging_upload_url(
                    source_key,
                    content_type=content_type_for_key(filename),
                    expires_in=3600,
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
        queued, _ = queue_verified_job(
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
    if job.status != MediaProcessingStatus.FAILED or not job.source_acknowledged_at:
        return JsonResponse({"message": _("Only failed jobs with a retained source can be retried.")}, status=409)
    try:
        queued, _ = queue_verified_job(
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
    try:
        pending = claim_attachment_retry(job.public_id)
        schedule_attachment_retry_after_commit(pending.public_id)
    except ValidationError as exc:
        return JsonResponse({"message": "; ".join(str(value) for value in getattr(exc, "messages", [str(exc)]))}, status=409)
    return JsonResponse({"job": _serialize_media_job(pending)}, status=202)

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
    return render(request, "upload_video.html", {
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
    queryset = MediaProcessingJob.objects.select_related("lesson", "created_by")
    if getattr(getattr(request.user, "role", None), "role", None) != "admin":
        queryset = queryset.filter(created_by=request.user)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if search:
        queryset = queryset.filter(
            normalized_contains_q(
                ("original_filename", "lesson__name", "created_by__username"),
                search,
            )
        )
    paginator = Paginator(queryset, 25)
    jobs_page = paginator.get_page(request.GET.get("page", "1"))
    return render_page(request, "media_processing_status.html", "partials/media_processing_status_content.html", {
        "jobs_page": jobs_page,
        "pagination_query": pagination_query_string(request),
        "status_choices": MediaProcessingStatus.choices,
        "status_labels_json": json.dumps(
            {value: str(label) for value, label in MediaProcessingStatus.choices},
            ensure_ascii=False,
        ),
        "phase_labels_json": json.dumps(
            {value: str(label) for value, label in MediaProcessingPhase.choices},
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
        selected_scope = get_object_or_404(
            AcademicYearLevel.objects.select_related("academic_year", "level"),
            pk=scope_id,
            academic_year__is_active=True,
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
                    saved, _result_counts = save_formula_and_results(
                        scope=formula_form.cleaned_data["academic_year_level"],
                        course_offering=formula_form.cleaned_data["course_offering"],
                        overall_pass_percent=formula_form.cleaned_data["overall_pass_percent"],
                        evaluation_starts_on=formula_form.cleaned_data["evaluation_starts_on"],
                        evaluation_ends_on=formula_form.cleaned_data["evaluation_ends_on"],
                        failed_courses_repeat_threshold=formula_form.cleaned_data["failed_courses_repeat_threshold"],
                        rules=rules,
                        actor=request.user,
                    )
                    messages.success(request, _("Promotion formula and evaluation results saved."))
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
        preview_rows = [evaluate_enrollment(enrollment, plan) for enrollment in preview_page_obj] if plan else []
        preview_grading_errors = plan.grading_errors if plan else ()
        saved_results = EvaluationResult.objects.filter(
            formula=formula,
            course_offering__isnull=formula.course_offering_id is None,
        ).select_related(
            "enrollment__student", "enrollment__academic_year_level__level", "overridden_by", "promotion_history"
        ).order_by("enrollment__student__last_name", "enrollment__student__first_name", "enrollment__student__username")
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

    return render_page(request, "promotion_evaluation.html", "partials/promotion_evaluation_content.html", {
        "formula_form": formula_form,
        "rule_formset": rule_formset,
        "selected_scope": selected_scope,
        "selected_offering": selected_offering,
        "selected_offering_id": selected_offering.pk if selected_offering else None,
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
    result_ids = [value for value in request.POST.getlist("result_ids") if value.isdigit()]
    try:
        histories, errors = promote_evaluation_results(result_ids=[int(value) for value in result_ids], actor=request.user)
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(exc))
        histories, errors = [], []
    if histories:
        messages.success(request, _("%(count)d student promotion(s) were recorded.") % {"count": len(histories)})
    if errors:
        messages.error(request, _("%(count)d promotion(s) could not be recorded.") % {"count": len(errors)})
    scope_id = request.POST.get("scope", "")
    return redirect(f"{reverse('promotion-formula')}?scope={scope_id}")


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
    name = request.GET.get("name", None)
    course = request.GET.get("course", None)
    user = request.user
    view = "quiz"
    try:
        selected_year, academic_years = select_content_academic_year(request)
    except ValidationError as exc:
        return HttpResponse("; ".join(str(message) for message in exc.messages), status=400)

    query = Q(
        course_offering__isnull=False,
        course_offering__academic_year_level__academic_year_id=selected_year.pk,
    )
    if name:
        name = normalize_search_text(name)
        query &= normalized_contains_q(("name",), name)
    if course:
        if course.isdigit() and CourseOffering.objects.filter(
            pk=int(course), academic_year_level__academic_year_id=selected_year.pk
        ).exists():
            query &= Q(course_offering_id=int(course))
        else:
            course = None
    quizzes = Quiz.objects.filter(query).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__level",
        "course_offering__academic_year_level__academic_year",
        "quiz_type",
    )
    
    logger.info(
        "User %s filters quizzes using %s name, academic year %s, and %s course",
        user,
        name,
        selected_year.pk,
        course,
    )

    quizzes = quizzes.order_by("name")

    context = {
        "name_value" : name or "",
        "course_value" : course or "",
        "columns" : Quiz.get_columns(),
        "academic_year_filter": True,
        "academic_years": academic_years,
        "selected_academic_year_id": selected_year.pk,
        "subjects" : _content_offering_filter_options(selected_year.pk),
        "filters" : ["course_filter.html"],
        "submission_view" : True,
        "page_size" : 10,
    }

    return render_dashboard(request, quizzes, view, context)

@capability_required(can_manage_content)
def create_quiz(request):
    if request.method == "GET":
        course_offerings = CourseOffering.objects.filter(
            academic_year_level__academic_year__is_active=True
        ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year")

        return render(request, "quiz_form.html", {
            "course_offerings" : course_offerings,
            "quiz_types": QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
            "question_types" : Question.QUESTION_TYPES,
            "questions" : request.session.pop("questions", []),
            **request.session.pop("quiz", {}),
            "content_status" : PublicationStatus.DRAFT,
            "content_status_display" : PublicationStatus.DRAFT.label,
            "publication_statuses" : PublicationStatus.choices,
        })
    elif request.method == "POST":
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            return HttpResponse(_("Invalid publication status."), status=400)
        questions, quiz = unpack_quiz_form(request.POST)

        try:
            opening_date = get_datetime(quiz.get("opening_date", ""))
            closing_date = get_datetime(quiz.get("closing_date", ""))
            
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
                request.session['questions'] = [question.serialize() for question in questions_obj]
                request.session['quiz'] = quiz.serialize()
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
                success(request, _("Quiz is created successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Create quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error("Offering %s is not found to create a quiz!", request.POST.get("course_offering"))
        
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
            ).select_related("course", "academic_year_level__level", "academic_year_level__academic_year")
            return render(request, "quiz_form.html", {
                "course_offerings" : course_offerings,
                "selected_offering_id" : quiz.course_offering_id,
                "selected_quiz_type_id" : quiz.quiz_type_id,
                "quiz_types": QuizType.objects.filter(code__in=QUIZ_TYPE_CODES).order_by("code"),
                "question_types" : Question.QUESTION_TYPES,
                "questions" : [question.serialize() for question in quiz.questions.all()],
                **quiz.serialize(),
                "content_status" : quiz.status,
                "content_status_display" : quiz.get_status_display(),
                "publication_statuses" : PublicationStatus.choices,
            })
        except Http404:
            logger.error(f"Quiz with id : {quiz_id} is not found!")
            return redirect(reverse("quiz-dashboard"))

    elif request.method == "POST":
        publication_status = _requested_publication_status(request)
        if publication_status is None:
            return HttpResponse(_("Invalid publication status."), status=400)
        questions, quiz_data = unpack_quiz_form(request.POST)
        try:
            # Update Quiz
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            if not quiz.can_edit:
                if publication_status == quiz.status:
                    return HttpResponse(_("Published or archived quiz cannot be edited."), status=403)
                with transaction.atomic():
                    quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
                    if publication_status == quiz.status:
                        return HttpResponse(_("Published or archived quiz cannot be edited."), status=403)
                    previous_status = quiz.status
                    quiz.status = publication_status
                    quiz.save(update_fields=["status"])
                    if previous_status == PublicationStatus.PUBLISHED and publication_status != PublicationStatus.PUBLISHED:
                        cancel_future_quiz_opening_events(quiz)
                    if previous_status != PublicationStatus.PUBLISHED and publication_status == PublicationStatus.PUBLISHED:
                        schedule_quiz_opening_events(quiz)
                success(request, _("Quiz status is updated successfully"), extra_tags="alert-success")
                return redirect(reverse("quiz-update", args=[quiz_id]))
            requested_name = quiz_data['quiz_name']
            requested_opening_date = get_datetime(quiz_data['opening_date'])
            requested_closing_date = get_datetime(quiz_data['closing_date'])
            requested_total_grade = quiz_data['total_grade']
            quiz_type_id = request.POST.get("quiz_type")
            requested_quiz_type = get_object_or_404(QuizType, pk=quiz_type_id, code__in=QUIZ_TYPE_CODES)
            offering_id = request.POST.get("course_offering")
            requested_offering = get_object_or_404(CourseOffering, pk=offering_id)

            quiz.name = requested_name
            quiz.opening_date = requested_opening_date
            quiz.closing_date = requested_closing_date
            quiz.total_grade = requested_total_grade
            quiz.status = publication_status
            quiz.quiz_type = requested_quiz_type
            quiz.course_offering = requested_offering

            # Create Questions
            questions_obj = []
            questions_exists = []
            questions_id = []

            valid_question_ids = set(quiz.questions.values_list("pk", flat=True))
            for question in questions.values():
                question_instance = build_question_instance(question, quiz)

                question_id = str(question.get("id", "")).strip()
                if question_id and int(question_id) in valid_question_ids:
                    question_instance.pk = int(question_id)
                    questions_id.append(int(question_id))
                    questions_exists.append(question_instance)
                else:
                    questions_obj.append(question_instance)
            
            with transaction.atomic():
                quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
                if not quiz.can_edit:
                    return HttpResponse(_("Published or archived quiz cannot be edited."), status=403)
                previous_status = quiz.status
                previous_opening_date = quiz.opening_date
                previous_closing_date = quiz.closing_date
                previous_offering_id = quiz.course_offering_id
                quiz.name = requested_name
                quiz.opening_date = requested_opening_date
                quiz.closing_date = requested_closing_date
                quiz.total_grade = requested_total_grade
                quiz.status = publication_status
                quiz.quiz_type = requested_quiz_type
                quiz.course_offering = requested_offering
                # Delete Removed Questions
                Question.objects.filter(quiz=quiz).exclude(pk__in=questions_id).delete()

                # Update Current Questions
                Question.objects.bulk_update(questions_exists, ["title", "correct_answer", "question_type", "choices", "config", "grade", "auto_grade"])

                # Current New Questions
                Question.objects.bulk_create(questions_obj)
                quiz.save()
                published_window_changed = (
                    previous_opening_date != quiz.opening_date
                    or previous_closing_date != quiz.closing_date
                    or previous_offering_id != quiz.course_offering_id
                )
                if publication_status == PublicationStatus.PUBLISHED and (
                    previous_status != PublicationStatus.PUBLISHED or published_window_changed
                ):
                    schedule_quiz_opening_events(quiz)

            logger.info(
                "Quiz %s is updated successfully in offering %s with %s new and %s existing questions",
                quiz.name,
                offering_id,
                len(questions_obj),
                len(questions_exists),
            )
            success(request, _("Quiz is updated successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error("Offering or Quiz with id %s was not found", quiz_id)

        except Exception as e:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error(f"Quiz update has failed : {e}")
        
        return redirect(reverse("quiz-update", args=[quiz_id]))

class DeleteQuiz(QuizBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=403)
        return super(QuizBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("lesson-dashboard")

# Submission Dashboard
@capability_required(can_grade)
def export_quiz_submissions_csv(request, quiz_id):
    return export_quiz_with_submissions_to_csv(quiz_id)

@capability_required(can_grade)
def export_quiz_summary_csv(request, quiz_id):
    return export_quiz_summary_to_csv(quiz_id)

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
        "all_count": all_students.count(),
        "paid_count": all_students.filter(payment_uploaded=True).count(),
        "unpaid_count": all_students.filter(payment_uploaded=False).count(),
    }
    return render_page(request, "payment_matrix.html", "partials/payment_matrix_content.html", context)


@login_required(login_url=LOGIN_URL)
def academic_payment_document(request, payment_id):
    payment = get_object_or_404(AcademicPayment, pk=payment_id)
    if payment.student_id != request.user.pk and not can_view_reports(request.user):
        raise PermissionDenied
    try:
        storage_response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=payment.receipt_key)
        response = FileResponse(
            storage_response["Body"],
            content_type=storage_response.get("ContentType")
            or mimetypes.guess_type(payment.receipt_key)[0]
            or "application/octet-stream",
            as_attachment=request.GET.get("download") == "1",
            filename=os.path.basename(payment.receipt_key),
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

    # User
    name = request.GET.get("name", None)
    # Year of submission
    year = request.GET.get("filtering", now().year)
    # Degree Range!
    min_grade = request.GET.get("min-grade", 1)
    max_grade = request.GET.get("max-grade", quiz.total_grade)
    user = request.user
    view = "submission"
    # Start with an empty Q object (matches all)
    query = Q()

    # Dynamically add conditions if filters are present
    if name:
        name = normalize_search_text(name)
        query &= normalized_contains_q(("user__first_name", "user__last_name"), name)
    
    query &= Q(submitted_at__contains=year)
    query &= Q(total_grade__gte=min_grade)
    query &= Q(total_grade__lte=max_grade)
    query &= Q(quiz_id=quiz_id)

    logger.info(f"User : {user} filters submissions using {name} username and grades range between ( {min_grade} , {max_grade} )")

    grades = Grade.objects.filter(query).order_by("submitted_at")

    context = {
        "name_value" : name or "",
        "max_grade" : max_grade,
        "min_grade" : min_grade,
        "filtering" : year or "",
        "columns" : Grade.get_columns(),
        "options" : [_("Choose Academic Year"), *[str(value) for value in Grade.get_years(quiz_id)]], # Translate "Choose Academic Year"
        "submission_user" : True,
        "template_name" : "submission_dashboard.html",
        "quiz_id" : quiz_id,
        "quiz" : quiz,
        "view_name" : view,
    }

    return render_dashboard(request, grades, view, context, parameters=[quiz_id, ])

@capability_required(can_grade)
def submission_user(request, quiz_id, user_id):
    submissions = Submission.objects.filter(question__quiz_id=quiz_id, user_id=user_id)
    grade = Grade.objects.filter(quiz_id=quiz_id, user_id=user_id).first()
    quiz = get_object_or_404(Quiz, pk=quiz_id)

    if not submissions:
        logger.error(f"Submission for quiz id : {quiz_id} with user id : {user_id} is not found to get submissions!")
        return HttpResponse(_("Not Found!"), 404) # Translate "Not Found!"
    
    if not grade:
        logger.error(f"Grades for quiz id : {quiz_id} with user id : {user_id} is not found!")
        return HttpResponse(_("Not Found!"), 404) # Translate "Not Found!"

    if request.method == "GET":
        questions = [s.serialize() for s in submissions]

        return render(request, "display_quiz.html", {
            "quiz_name" : quiz.name,
            "username" : request.user.username,
            "questions" : questions,
            "is_student" : False,
            "mode" : "view",
            "extended_view" : "admin_panel.html",
            "id" : "content",
            "total_grade" : grade.total_grade,
            "back_url" : reverse("submission-dashboard", args=[quiz_id,])
        })
    
    elif request.method == "POST":
        questions, _ = unpack_quiz_form(request.POST)
        
        manual_graded_questions = 0
        modified_questions = []
        # print([s.pk for s in submissions])
        for question in questions.values():
            try:
                if "manual_grade" not in question:
                    continue
                question_submission = submissions.get(question_id=question['id'])
                question_submission.assign_grade(True, int(question['manual_grade']))
                modified_questions.append(question_submission)
                manual_graded_questions += 1

            except Exception as e:
                logger.error(f"{question['id']} is not found for {quiz} and user id : {user_id}")
                logger.error(f"Stack Traceback : {e}")

        try:
            with transaction.atomic() :
                Submission.objects.bulk_update(modified_questions, ['is_graded', 'grade'])
                grade.total_grade = Submission.objects.filter(user_id=user_id, question__quiz_id=quiz_id).aggregate(total=Sum("grade"))['total']
                grade.save()
                logger.info(f"Grades are updated to user {user_id} to be {grade.total_grade} with manual grading of {manual_graded_questions} questions")
    
        except Exception as e:
            logger.error(f"Something went wrong while updating grades!")
            logger.error(f"Stack Traceback : {e}")
        
        return redirect(reverse("submission-user", args=[quiz_id, user_id]))
    
    else:
        return HttpResponse(_("Not allowed method"), 400) # Translate

@login_required(login_url=LOGIN_URL)
def generate_audio_download(request, offering_id, lesson_id):
    user = User.objects.get(pk=request.user.pk)
    offering = get_accessible_offering_or_403(user, offering_id)
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))

    try:
        file_index = int(request.GET.get("file_index", "-1"))
    except (TypeError, ValueError):
        raise Http404
    lesson_links = json.loads(lesson.links or "[]")
    if file_index < 0 or file_index >= len(lesson_links):
        raise Http404
    link = lesson_links[file_index]
    if link.get("file_type") != "audio":
        raise Http404

    manifest_key = link.get("id", "")
    download_key = link.get("download_id") or downloadable_audio_key(manifest_key)
    valid, errors = validate_downloadable_audio_key(download_key or "")
    if not valid or download_key != downloadable_audio_key(manifest_key):
        logger.warning("Invalid downloadable audio metadata for lesson %s link %s: %s", lesson.pk, file_index, errors)
        raise Http404

    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", posixpath.basename(download_key)) or "audio.mp3"
    try:
        download_url = CLOUD_CLIENT.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket_name,
                "Key": download_key,
                "ResponseContentType": "audio/mpeg",
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=3600,
        )
    except Exception:
        logger.exception("Error generating downloadable audio URL for lesson %s", lesson.pk)
        return JsonResponse({"error": _("Download is temporarily unavailable.")}, status=503)
    return redirect(download_url)

# Bulk Operations
@capability_required(can_delete_content)
def bulk_delete_users(request):
    """Bulk delete users endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    try:

        data = json.loads(request.body)
        ids = data.get('ids', [])
        
        if not ids:
            return JsonResponse({'error': _('No IDs provided')}, status=400)
        
        deleted_count = User.objects.filter(id__in=ids).delete()[0]
        logger.info(f"User {request.user} deleted {deleted_count} users")
        
        return JsonResponse({'success': True, 'deleted': deleted_count})
    except Exception as e:
        logger.error(f"Bulk delete error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def bulk_delete_courses(request):
    """Bulk delete courses endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    try:

        data = json.loads(request.body)
        ids = data.get('ids', [])
        
        if not ids:
            return JsonResponse({'error': _('No IDs provided')}, status=400)
        
        deleted_count = Course.objects.filter(id__in=ids).delete()[0]
        logger.info(f"User {request.user} deleted {deleted_count} courses")
        
        return JsonResponse({'success': True, 'deleted': deleted_count})
    except Exception as e:
        logger.error(f"Bulk delete error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def bulk_delete_lessons(request):
    """Bulk delete lessons endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    try:

        data = json.loads(request.body)
        ids = data.get('ids', [])
        
        if not ids:
            return JsonResponse({'error': _('No IDs provided')}, status=400)
        
        deleted_count = Lesson.objects.filter(id__in=ids).delete()[0]
        logger.info(f"User {request.user} deleted {deleted_count} lessons")
        
        return JsonResponse({'success': True, 'deleted': deleted_count})
    except Exception as e:
        logger.error(f"Bulk delete error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def bulk_delete_quizzes(request):
    """Bulk delete quizzes endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    try:

        data = json.loads(request.body)
        ids = data.get('ids', [])
        
        if not ids:
            return JsonResponse({'error': _('No IDs provided')}, status=400)
        
        deleted_count = Quiz.objects.filter(id__in=ids).delete()[0]
        logger.info(f"User {request.user} deleted {deleted_count} quizzes")
        
        return JsonResponse({'success': True, 'deleted': deleted_count})
    except Exception as e:
        logger.error(f"Bulk delete error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

# ============================================================================
# R2 FILE MANAGEMENT API ENDPOINTS
# ============================================================================

@capability_required(can_delete_content)
def api_delete_file(request):
    """
    API endpoint to delete a single file from R2
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        file_key = data.get('file_key')
        
        if not file_key:
            return JsonResponse({'error': _('File key required')}, status=400)
        
        referencing = Lesson.objects.filter(Q(links__contains=file_key))
        if referencing.exists():
            names = list(referencing.values_list("name", flat=True)[:5])
            return JsonResponse({
                "error": _("File is referenced by lessons: %(names)s. Delete lessons first or reupload.") % {"names": ", ".join(names)}
            }, status=409)

        success = R2_MANAGER.delete_file(file_key)
        
        if success:
            logger.info(f"User {request.user} deleted file: {file_key}")
            return JsonResponse({'success': True, 'message': _('File deleted successfully')})
        else:
            return JsonResponse({'error': _('Failed to delete file')}, status=500)
    
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def api_delete_m3u8_file(request):
    """
    API endpoint to delete an m3u8 file and all its related .ts segment files
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        file_key = data.get('file_key')
        
        if not file_key:
            return JsonResponse({'error': _('File key required')}, status=400)
        
        if not file_key.endswith('.m3u8'):
            return JsonResponse({'error': _('File must be an m3u8 file')}, status=400)
        
        successful, failed = R2_MANAGER.delete_m3u8_with_segments(file_key)
        
        logger.info(f"User {request.user} deleted m3u8 file {file_key} with {len(successful)} related files")
        
        return JsonResponse({
            'success': True,
            'message': _('M3U8 file and segments deleted successfully'),
            'deleted_count': len(successful),
            'failed_count': len(failed),
            'deleted_files': successful,
            'failed_files': failed
        })
    
    except Exception as e:
        logger.error(f"Error deleting m3u8 file: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_delete_content)
def api_delete_files_batch(request):
    """
    API endpoint to delete multiple files in batch
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        file_keys = data.get('file_keys', [])
        
        if not file_keys:
            return JsonResponse({'error': _('No files specified')}, status=400)
        
        successful, failed = R2_MANAGER.delete_files_batch(file_keys)
        
        logger.info(f"User {request.user} batch deleted {len(successful)} files")
        
        return JsonResponse({
            'success': True,
            'message': _('Files deleted successfully'),
            'deleted_count': len(successful),
            'failed_count': len(failed),
            'deleted_files': successful,
            'failed_files': failed
        })
    
    except Exception as e:
        logger.error(f"Error in batch delete: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_manage_content)
def api_rename_file(request):
    """
    API endpoint to rename a file
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    try:

        data = json.loads(request.body)
        old_key = data.get('old_key')
        new_key = data.get('new_key')
        is_folder = bool(data.get('is_folder', False))
        
        if not old_key or not new_key:
            return JsonResponse({'error': _('Both old and new keys required')}, status=400)
        
        if is_folder:
            success = R2_MANAGER.rename_folder(old_key, new_key)
            if success:
                updated_count = rewrite_lesson_r2_references(old_key, new_key, is_folder=True)
                logger.info(f"User {request.user} renamed folder from {old_key} to {new_key}. Updated {updated_count} lesson references.")
                return JsonResponse({'success': True, 'message': _('Folder renamed successfully and %(count)d database references updated') % {'count': updated_count}, 'new_key': new_key})
            else:
                return JsonResponse({'error': _('Failed to rename folder')}, status=500)

        success = R2_MANAGER.rename_file(old_key, new_key)
        
        if success:
            updated_count = rewrite_lesson_r2_references(old_key, new_key)

            logger.info(f"User {request.user} renamed file from {old_key} to {new_key}. Updated {updated_count} lesson references.")
            return JsonResponse({
                'success': True, 
                'message': _('File renamed successfully and %(count)d database references updated') % {'count': updated_count}, 
                'new_key': new_key
            })
        else:
            return JsonResponse({'error': _('Failed to rename file')}, status=500)
    
    except Exception as e:
        logger.error(f"Error renaming file: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

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
        
        new_key = R2_MANAGER.move_file(file_key, destination_folder)
        
        if new_key:
            updated_count = rewrite_lesson_r2_references(file_key, new_key)
            logger.info(f"User {request.user} moved file from {file_key} to {new_key}. Updated {updated_count} lesson references.")
            return JsonResponse({'success': True, 'message': _('File moved successfully and %(count)d database references updated') % {'count': updated_count}, 'new_key': new_key})
        else:
            return JsonResponse({'error': _('Failed to move file')}, status=500)
    
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
        
        success = R2_MANAGER.create_folder(folder_path)
        
        if success:
            logger.info(f"User {request.user} created folder: {folder_path}")
            return JsonResponse({'success': True, 'message': _('Folder created successfully')})
        else:
            return JsonResponse({'error': _('Failed to create folder')}, status=500)
    
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
        folder_path = data.get('folder_path')
        recursive = data.get('recursive', False)
        
        if not folder_path:
            return JsonResponse({'error': _('Folder path required')}, status=400)
        
        deleted_count, errors = R2_MANAGER.delete_folder(folder_path, recursive)
        
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

@capability_required(can_manage_content)
def api_get_storage_stats(request):
    """
    API endpoint to get storage statistics
    """
    prefix = request.GET.get('prefix', '')
    
    try:
        stats = R2_MANAGER.get_storage_stats(prefix)
        
        # Format sizes
        stats['total_size_formatted'] = R2_MANAGER.format_file_size(stats['total_size'])
        
        # Format extension stats
        for ext, data in stats['by_extension'].items():
            data['size_formatted'] = R2_MANAGER.format_file_size(data['size'])
        
        # Format largest files
        for file_data in stats['largest_files']:
            file_data['size_formatted'] = R2_MANAGER.format_file_size(file_data['size'])
            if 'last_modified' in file_data and file_data['last_modified']:
                file_data['last_modified'] = file_data['last_modified'].isoformat()
        
        return JsonResponse({'success': True, 'stats': stats})
    
    except Exception as e:
        logger.error(f"Error getting storage stats: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@capability_required(can_manage_content)
def api_search_files(request):
    """
    API endpoint to search for files
    """
    query = request.GET.get('q', '')
    prefix = request.GET.get('prefix', '')
    extensions = request.GET.get('extensions', '')
    continuation_token = request.GET.get('continuation') or None
    
    if not query:
        return JsonResponse({'error': _('Search query required')}, status=400)
    
    try:
        ext_list = [e.strip() for e in extensions.split(',') if e.strip()] if extensions else None
        
        results, next_token = R2_MANAGER.search_files_page(
            query,
            prefix,
            ext_list,
            continuation_token=continuation_token,
            page_size=200,
        )
        
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
    API endpoint – returns quiz mode/status for every quiz in a course
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
        quizzes = offering.quizzes.all() if user_has_management_role(user) else offering.quizzes.filter(status=PublicationStatus.PUBLISHED)
        result = []

        for quiz in quizzes:
            status, _ = get_student_quiz_status(quiz, user, current_time)

            result.append({"id": quiz.pk, "status": status})

        return JsonResponse({"quizzes": result})

    except Exception as e:
        logger.error(f"api_quiz_status error: {e}")
        return JsonResponse({"error": str(e)}, status=500)

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
        
        # List files with filter
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, folder_name, filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
        
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
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, folder_id, filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
    else:
        contents, parent_folder, next_token = list_current_folder_page(
            CLOUD_CLIENT, bucket_name, "", filter_config,
            continuation_token=continuation_token, search_query=search_query,
        )
    
    # Get storage statistics with error handling - ONLY if requested via AJAX
    # Skip initial page load to improve performance
    load_stats = request.GET.get('load_stats', 'false') == 'true'
    
    if load_stats:
        try:
            stats = R2_MANAGER.get_storage_stats()
        except Exception as e:
            logger.error(f"Error getting storage stats: {str(e)}")
            # Provide default stats if R2 is unavailable
            stats = {
                'total_files': 0,
                'total_size': 0,
                'by_extension': {},
                'largest_files': []
            }
    else:
        # Provide placeholder stats for initial page load
        stats = {
            'total_files': '...',
            'total_size': 0,
            'by_extension': {},
            'largest_files': [],
            'loading': True
        }
    
    context = {
        'drive': contents,
        'parent_folder': parent_folder,
        'is_root': not folder_id or folder_id == "None",
        'current_filter': filter_preset,
        'search_query': search_query,
        'filter_presets': list(FILTER_PRESETS.keys()),
        'storage_stats': stats,
        'total_size_formatted': R2_MANAGER.format_file_size(stats.get('total_size', 0)) if isinstance(stats.get('total_size'), int) else '...',
        'load_stats': load_stats,
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
    
    return render(request, 'r2_management.html', context)

# ============================================================================
# PHASE 3 — Student Applications
# ============================================================================

def signup(request):
    copy = get_institute_copy(translation.get_language(), "courses")["copy"]
    if request.method == "POST":
        form = SignupForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_keys = []
            try:
                with transaction.atomic():
                    user = form.save()
                    if user.city:
                        is_offline = OfflineCity.objects.filter(name__iexact=user.city, is_active=True).exists()
                        user.study_mode = "offline" if is_offline else "online"
                    file_type_map = {"identity_front": "identity_front_key", "identity_back": "identity_back_key", "payment": "payment_key", "profile": "profile_image_key"}
                    required_upload_types = {"identity_front", "profile"}
                    if user.identity_type == "national_id":
                        required_upload_types.add("identity_back")
                    for upload_type, model_field in file_type_map.items():
                        if upload_type in request.FILES:
                            key = upload_application_file(CLOUD_CLIENT, bucket_name, user.id, request.FILES[upload_type], upload_type)
                            if key:
                                setattr(user, model_field, key)
                                uploaded_keys.append(key)
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
                        user.save(update_fields=[v for v in file_type_map.values() if getattr(user, v, None)] + ["study_mode"])
                    elif user.study_mode:
                        user.save(update_fields=["study_mode"])
                    send_application_received(user)
            except ValidationError as exc:
                for key in uploaded_keys:
                    try:
                        CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
                    except Exception:
                        pass
                form.add_error(None, "; ".join(str(message) for message in exc.messages))
                return render(request, "signup.html", {"form": form, "copy": copy})
            except Exception:
                for key in uploaded_keys:
                    try:
                        CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
                    except Exception:
                        pass
                raise
            return render(request, "signup_success.html")
    else:
        form = SignupForm()
    return render(request, "signup.html", {"form": form, "copy": copy})

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
        "-date_joined",
        "-pk",
    ]
    if status_filter == "all":
        users = User.objects.filter(
            Q(application_status__in=["pending", "active", "declined"])
        ).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "date_joined",
        ).order_by(*application_order)
    elif status_filter in ("pending", "active", "declined"):
        users = User.objects.filter(application_status=status_filter).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "date_joined",
        ).order_by(*application_order)
    else:
        status_filter = "all"
        users = User.objects.filter(
            Q(application_status__in=["pending", "active", "declined"])
        ).only(
            "id", "username", "first_name", "last_name", "phone", "city",
            "application_status", "date_joined",
        ).order_by(*application_order)
    if application_search:
        users = users.filter(_application_search_query(application_search))
    paginator = Paginator(users, 15)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    return {
        "page_obj": page_obj,
        "current_status": status_filter,
        "application_search": application_search,
        "pagination_query": pagination_params.urlencode(),
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
    return render(request, "applications_dashboard.html", _applications_dashboard_context(request))

@capability_required(can_manage_applications)
def application_review(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    document_fields = {
        "identity_front": ("identity_front_key", _("Identity Front")),
        "identity_back": ("identity_back_key", _("Identity Back")),
        "payment": ("payment_key", _("Payment")),
        "profile": ("profile_image_key", _("Profile")),
    }
    if request.method == "POST":
        form = ApplicationAdminForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            uploaded_keys = []
            old_keys = []
            original_status = user.application_status
            original_role_id = user.role_id
            original_is_active = user.is_active
            desired_status = form.cleaned_data["application_status"]
            try:
                with transaction.atomic():
                    if desired_status != original_status:
                        form.instance.application_status = original_status
                        form.instance.is_active = user.is_active
                    user = form.save()
                    if (
                        original_role_id != user.role_id
                        or original_is_active and not user.is_active
                    ):
                        revoke_user_mobile_access(user)
                    for upload_type, (model_field, document_label) in document_fields.items():
                        previous_key = getattr(user, model_field, None)
                        if form.cleaned_data.get(f"clear_{upload_type}"):
                            if previous_key:
                                old_keys.append(previous_key)
                            setattr(user, model_field, None)
                        uploaded_file = form.cleaned_data.get(upload_type)
                        if uploaded_file:
                            new_key = upload_application_file(CLOUD_CLIENT, bucket_name, user.id, uploaded_file, upload_type)
                            if not new_key:
                                raise ValidationError(_("The %(document)s could not be uploaded.") % {"document": document_label})
                            if previous_key and previous_key != new_key:
                                old_keys.append(previous_key)
                            setattr(user, model_field, new_key)
                            uploaded_keys.append(new_key)
                    user.save()

                    if desired_status != original_status:
                        user, _enrollment = set_application_status(
                            user, request.user, desired_status, original_status
                        )
                for old_key in set(old_keys):
                    try:
                        CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=old_key)
                    except Exception:
                        logger.exception("Could not delete replaced application file %s", old_key)
                if desired_status != original_status:
                    if desired_status == "active":
                        send_application_activated(user)
                    elif desired_status == "declined":
                        send_application_declined(user)
                messages.success(request, _("Application and student data updated."))
                return redirect("application-review", user_id=user.pk)
            except (ValidationError, PermissionDenied) as exc:
                for key in uploaded_keys:
                    try:
                        CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
                    except Exception:
                        logger.exception("Could not clean up application file %s", key)
                form.add_error(None, str(exc))
            except Exception:
                for key in uploaded_keys:
                    try:
                        CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
                    except Exception:
                        logger.exception("Could not clean up application file %s", key)
                raise
    else:
        form = ApplicationAdminForm(instance=user)

    documents = []
    for document_type, (field, label) in document_fields.items():
        key = getattr(user, field, None)
        if key:
            content_type = mimetypes.guess_type(key)[0] or ""
            documents.append({
                "type": document_type,
                "label": label,
                "url": reverse("application-document", args=[user.pk, document_type]),
                "download_url": reverse("application-document", args=[user.pk, document_type]) + "?download=1",
                "is_image": content_type.startswith("image/"),
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
        "COURSE_LEVELS": Level.objects.order_by("ordering"),
    }
    return render(request, "application_review.html", context)


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
                ) if key
            ]
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
    document_fields = {
        "identity_front": "identity_front_key",
        "identity_back": "identity_back_key",
        "payment": "payment_key",
        "profile": "profile_image_key",
    }
    model_field = document_fields.get(document_type)
    if not model_field:
        raise Http404
    user = get_object_or_404(User, pk=user_id)
    key = getattr(user, model_field, None)
    if not key:
        raise Http404
    try:
        storage_response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=key)
        response = FileResponse(
            storage_response["Body"],
            content_type=storage_response.get("ContentType") or mimetypes.guess_type(key)[0] or "application/octet-stream",
            as_attachment=request.GET.get("download") == "1",
            filename=os.path.basename(key),
        )
        if storage_response.get("ContentLength") is not None:
            response["Content-Length"] = str(storage_response["ContentLength"])
        # Avatars/documents are polled repeatedly by live pages (Telegram chat,
        # dashboards); let the browser reuse a fresh copy instead of re-downloading.
        if request.GET.get("download") != "1":
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
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
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

            update_fields = []
            for document_type, model_field in form.document_types.items():
                uploaded_file = form.cleaned_data.get(document_type)
                if not uploaded_file:
                    continue
                key = upload_application_file(
                    CLOUD_CLIENT,
                    bucket_name,
                    user.pk,
                    uploaded_file,
                    document_type,
                )
                if not key:
                    raise ValidationError(
                        _("The %(document)s could not be uploaded.")
                        % {"document": form.fields[document_type].label}
                    )
                setattr(user, model_field, key)
                update_fields.append(model_field)
                uploaded_keys.append(key)
            if not update_fields:
                raise ValidationError(_("Select at least one missing document."))
            user.save(update_fields=update_fields)
    except (ValidationError, User.DoesNotExist) as exc:
        for key in uploaded_keys:
            try:
                CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
            except Exception:
                logger.exception("Could not clean up profile document %s", key)
        if isinstance(exc, User.DoesNotExist):
            raise Http404 from exc
        messages.error(request, str(exc))
        return redirect("view-profile")
    except Exception:
        for key in uploaded_keys:
            try:
                CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=key)
            except Exception:
                logger.exception("Could not clean up profile document %s", key)
        raise

    messages.success(request, _("Missing signup documents uploaded successfully."))
    return redirect("view-profile")


@login_required(login_url=LOGIN_URL)
@require_POST
def profile_academic_payment(request):
    """Accept one new, non-replaceable receipt for an eligible academic scope."""
    uploaded_key = None
    try:
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            form = AcademicPaymentForm(request.POST, request.FILES, student=user)
            if not form.is_valid():
                raise ValidationError(form.errors.as_text())
            scope = form.cleaned_data["academic_year_level"]
            if AcademicPayment.objects.filter(
                student=user,
                academic_year_level=scope,
            ).exists():
                raise ValidationError(_("A payment receipt already exists for this academic level."))
            uploaded_key = upload_application_file(
                CLOUD_CLIENT,
                bucket_name,
                user.pk,
                form.cleaned_data["payment"],
                "academic_payment",
            )
            if not uploaded_key:
                raise ValidationError(_("The payment receipt could not be uploaded."))
            AcademicPayment.objects.create(
                student=user,
                academic_year_level=scope,
                receipt_key=uploaded_key,
            )
    except (ValidationError, User.DoesNotExist) as exc:
        if uploaded_key:
            try:
                CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=uploaded_key)
            except Exception:
                logger.exception("Could not clean up academic payment %s", uploaded_key)
        if isinstance(exc, User.DoesNotExist):
            raise Http404 from exc
        messages.error(request, str(exc))
        return redirect("view-profile")
    except Exception:
        if uploaded_key:
            try:
                CLOUD_CLIENT.delete_object(Bucket=bucket_name, Key=uploaded_key)
            except Exception:
                logger.exception("Could not clean up academic payment %s", uploaded_key)
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
                send_application_activated(user)
            success_message = _("%(name)s activated.") % {"name": user.get_full_name() or user.username}
        elif decision == "decline":
            user = decline_application(user, request.user, expected_status)
            if original_status != user.application_status:
                send_application_declined(user)
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

    select_all = bool(data.get("select_all"))
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
                    if decision == "activate":
                        send_application_activated(changed_user)
                    elif decision == "decline":
                        send_application_declined(changed_user)
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
        try:
            copied = copy_offerings(
                form.cleaned_data["source_year_level"],
                form.cleaned_data["target_year_level"],
                list(form.cleaned_data["offerings"].values_list("pk", flat=True)),
                copy_lessons=form.cleaned_data["copy_lessons"],
                copy_quizzes=form.cleaned_data["copy_quizzes"],
                actor=request.user,
            )
            messages.success(request, _("Copied %(count)d offering(s) as drafts.") % {"count": len(copied)})
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, str(exc))
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
    return render(request, "levels.html", {
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

@capability_required(can_manage_content)
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
    offering_page_obj = Paginator(offerings.order_by("course__name"), 15).get_page(request.GET.get("offering_page", 1))
    scope_courses = Course.objects.filter(level=selected_scope.level).order_by("name") if selected_scope else Course.objects.none()
    year_form = AcademicYearForm()
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
        "COURSE_LEVELS": Level.objects.order_by("ordering"),
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
            year = form.save()
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
            year = form.save()
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
        return redirect("academic-setup")


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
    year_id = offering.academic_year_level.academic_year_id
    try:
        offering.delete()
        messages.success(request, _("Course offering deleted."))
    except ProtectedError as e:
        messages.error(request, _("Cannot delete this course offering: it is referenced by other records (%s).") % str(e))
    return redirect(f"{reverse('academic-setup')}?scope={offering.academic_year_level_id}")

@require_POST
@capability_required(can_manage_content)
def duplicate_course(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    new_name = request.POST.get("new_name", "")
    if not new_name:
        return HttpResponse(_("New course name is required."), status=400)
    target_scope_id = request.POST.get("academic_year_level_id")
    target_scope = get_object_or_404(AcademicYearLevel, pk=target_scope_id) if target_scope_id else None
    with transaction.atomic():
        new_course = Course.objects.create(name=new_name, description=course.description, instructor=course.instructor, level=course.level)
        if target_scope:
            new_offering = CourseOffering.objects.create(course=new_course, academic_year_level=target_scope, status="draft")
            for lesson in Lesson.objects.filter(course_offering__course=course):
                lesson.pk = None
                lesson.course_offering = new_offering
                lesson.status = PublicationStatus.DRAFT
                lesson.save()
            for quiz in Quiz.objects.filter(course_offering__course=course):
                questions = list(quiz.questions.all())
                quiz.pk = None
                quiz.course_offering = new_offering
                quiz.status = PublicationStatus.DRAFT
                quiz.save()
                for q in questions:
                    q.pk = None
                    q.quiz = quiz
                Question.objects.bulk_create(questions)
    messages.success(request, _("Course duplicated."))
    return redirect("course-dashboard")

# ============================================================================
# PHASE 5 — Attendance Calendar, QR, and Scanning
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


def _build_month_grid(*, year_start, year_end, cur_year, cur_month, today,
                      holidays, meetings_by_date, locked_weekdays,
                      meeting_offerings_by_weekday=None):
    """Shared month-grid builder: one canonical day-dict shape consumed by
    partials/calendar_month_grid.html for both admin and student calendars."""
    cal = py_calendar.Calendar()
    month_days = cal.monthdatescalendar(cur_year, cur_month)
    month_grid = []
    for week in month_days:
        week_data = []
        for d in week:
            in_year = year_start <= d <= year_end
            day_meetings = meetings_by_date.get(d, [])
            is_meeting = d.weekday() in locked_weekdays
            is_holiday = d in holidays
            week_data.append({
                "day": d.day,
                "date": d.isoformat(),
                "in_year": in_year,
                "is_meeting": is_meeting,
                "is_today": d == today,
                "is_holiday": is_holiday,
                "holiday_name": holidays[d].name if is_holiday else "",
                "holiday_id": holidays[d].id if is_holiday else None,
                "meetings": day_meetings,
                "meeting_offerings": (meeting_offerings_by_weekday or {}).get(d.weekday(), []),
                "disabled": not in_year,
                "outside_month": d.month != cur_month,
            })
        month_grid.append(week_data)
    return month_grid


def _resolve_calendar_month(year, request):
    """Parse and clamp the requested display month within an academic year.
    Returns (cur_year, cur_month, prev_query, next_query)."""
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
        prev_month = f"year={((pm - 1) // 12)}&month={((pm - 1) % 12) + 1}"
    if current_cell < last_month:
        nm = current_cell + 1
        next_month = f"year={((nm - 1) // 12)}&month={((nm - 1) % 12) + 1}"
    return cur_year, cur_month, prev_month, next_month


@capability_required(can_manage_content)
def calendar_management(request):
    years = AcademicYear.objects.all().order_by("-starts_on")
    selected_year_id = request.GET.get("academic_year")
    if selected_year_id and not selected_year_id.isdigit():
        selected_year_id = None
    year = get_object_or_404(AcademicYear, pk=selected_year_id) if selected_year_id else None
    month_grid = None
    prev_month = None
    next_month = None
    today = timezone.localdate()
    cur_month = today.month
    cur_year = today.year

    if year:
        year_id = year.id
        year_start = year.starts_on
        year_end = year.ends_on
        cur_year, cur_month, prev_month, next_month = _resolve_calendar_month(year, request)

        holidays = {
            h.date: h
            for h in AcademicHoliday.objects.filter(academic_year=year, date__year=cur_year, date__month=cur_month)
        }
        scope_links = list(year.level_links.select_related("level").order_by("level__ordering"))
        locked_weekdays = {
            weekday
            for scope in scope_links
            for weekday in (scope.meeting_weekdays or [])
        }

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
        offerings_by_weekday = defaultdict(list)
        for offering in meeting_offerings:
            for weekday in offering.academic_year_level.meeting_weekdays or []:
                offerings_by_weekday[weekday].append(offering)

        month_grid = _build_month_grid(
            year_start=year_start,
            year_end=year_end,
            cur_year=cur_year,
            cur_month=cur_month,
            today=today,
            holidays=holidays,
            meetings_by_date=meetings_by_date,
            locked_weekdays=locked_weekdays,
            meeting_offerings_by_weekday=offerings_by_weekday,
        )
    else:
        year_id = None

    months_list = []
    for i in range(1, 13):
        d = date(2000, i, 1)
        months_list.append({
            "value": i,
            "name": formats.date_format(d, "F"),
        })

    # Generate year options within the academic year range
    year_options = []
    if year:
        for y in range(year.starts_on.year, year.ends_on.year + 1):
            year_options.append(y)

    return render(request, "calendar_management.html", {
        "years": years,
        "year": year,
        "year_id": year_id,
        "breadcrumb_items": generate_breadcrumb([
            (_("Admin"), reverse("admin-panel")),
            (_("Calendar"), None),
        ]),
        "weekday_names": _calendar_weekday_abbreviations(),
        "month_grid": month_grid,
        "prev_month": prev_month,
        "next_month": next_month,
        "cur_month": cur_month,
        "cur_year": cur_year,
        "today": today,
        "month_name": formats.date_format(date(cur_year, cur_month, 1), "F Y") if month_grid else "",
        "months": months_list,
        "year_options": year_options,
        "can_edit_meetings": can_manage_academic_setup(request.user),
    })


@require_POST
@capability_required(can_manage_academic_setup)
def add_meeting(request):
    year_id = request.POST.get("academic_year_id")
    date_value = request.POST.get("date", "")
    offering_id = request.POST.get("course_offering_id")
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
    offering = CourseOffering.objects.filter(
        pk=offering_id,
        academic_year_level__academic_year=year,
    ).select_related("academic_year_level").first()
    if not offering:
        messages.error(request, _("Invalid course offering."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}")
    if meeting_date.weekday() not in (offering.academic_year_level.meeting_weekdays or []):
        messages.error(request, _("The selected course does not meet on this weekday."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}")
    try:
        with transaction.atomic():
            meeting = AcademicYearLevelMeeting.objects.create(
                academic_year_level=offering.academic_year_level,
                meeting_date=meeting_date,
                course_offering=offering,
            )
            assigned_count = assign_unassigned_attendance(meeting)
    except IntegrityError:
        messages.error(request, _("A meeting already exists for this level and date."))
        return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}")
    messages.success(request, _("Meeting added. %(count)s pending attendance record(s) assigned.") % {"count": assigned_count})
    return redirect(f"{reverse('calendar-management')}?academic_year={year.pk}&year={meeting_date.year}&month={meeting_date.month}")


@require_POST
@capability_required(can_manage_academic_setup)
def delete_meeting(request, meeting_id):
    meeting = get_object_or_404(
        AcademicYearLevelMeeting.objects.select_related("academic_year_level__academic_year"),
        pk=meeting_id,
    )
    year_id = meeting.academic_year_level.academic_year_id
    meeting.delete()
    messages.success(request, _("Meeting deleted."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}&year={meeting.meeting_date.year}&month={meeting.meeting_date.month}")

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
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

@require_POST
@capability_required(can_manage_content)
def delete_holiday(request, holiday_id):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id)
    year_id = holiday.academic_year_id
    holiday.delete()
    messages.success(request, _("Holiday deleted."))
    return redirect(f"{reverse('calendar-management')}?academic_year={year_id}")

@login_required
def student_calendar(request):
    enrollments = Enrollment.objects.filter(
        student=request.user,
        status__in=["active", "completed"],
        enrollment_type="normal",
    ).select_related("academic_year_level__academic_year", "academic_year_level__level")
    active_year = AcademicYear.objects.filter(is_active=True).first()
    selected_enrollment = (
        enrollments.filter(academic_year_level__academic_year=active_year)
        .order_by("-enrolled_at")
        .first()
        if active_year else None
    )
    if selected_enrollment is None:
        selected_enrollment = enrollments.order_by(
            "-academic_year_level__academic_year__ordering", "-enrolled_at"
        ).first()

    scopes = []
    month_grid = None
    prev_month = None
    next_month = None
    month_name = ""
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
        holidays = {
            h.date: h
            for h in year.holidays.filter(date__year=cur_year, date__month=cur_month)
        }
        meetings_by_date = defaultdict(list)
        for meeting in AcademicYearLevelMeeting.objects.filter(
            academic_year_level=scope,
            meeting_date__year=cur_year,
            meeting_date__month=cur_month,
        ).select_related(
            "academic_year_level__level",
            "course_offering__course",
        ).order_by("meeting_date", "course_offering__course__name"):
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
        month_name = formats.date_format(date(cur_year, cur_month, 1), "F Y")
    return render(request, "student_calendar.html", {
        "scopes": scopes,
        "weekday_names": _calendar_weekday_abbreviations(),
        "month_grid": month_grid,
        "prev_month": prev_month,
        "next_month": next_month,
        "month_name": month_name,
    })

@login_required
def download_qr(request):
    if not request.user.qr_token:
        request.user.qr_token = secrets.token_urlsafe(32)
        request.user.save(update_fields=["qr_token"])
    qr_data = request.build_absolute_uri(reverse("scan-preview", args=[request.user.qr_token]))
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
    return render(request, "scanner.html")

@capability_required(can_scan_attendance)
def student_lookup(request):
    if request.method != "POST":
        return JsonResponse({"error": _("Method not allowed")}, status=405)

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
            user = User.objects.get(username__iexact=query)
        except User.DoesNotExist:
            pass

    if not user:
        messages.error(request, _("Student not found."))
        return redirect("scanner")

    if user.role and user.role.role != "student":
        messages.error(request, _("Student not found."))
        return redirect("scanner")

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
    academic_year_level, scheduled_offering = get_student_attendance_context(user, today)
    academic_year = academic_year_level.academic_year if academic_year_level else None
    already_recorded_actions = list(AttendanceRecord.objects.filter(
        student=user,
        attendance_date=today,
    ).values_list("action", flat=True))
    can_record = bool(academic_year_level and is_expected_date(academic_year_level, today) and user.study_mode != "online")
    return render(request, "scan_preview.html", {
        "student": user,
        "academic_year": academic_year,
        "scheduled_offering": scheduled_offering,
        "today": today,
        "already_recorded_actions": already_recorded_actions,
        "can_record": can_record,
        "token": token,
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
    if user.study_mode == "online":
        return JsonResponse({"error": _("Online students cannot record attendance.")}, status=400)
    today = timezone.localdate()
    academic_year_level, offering = get_student_attendance_context(user, today)
    if academic_year_level is None:
        return JsonResponse({"error": _("The student has no active academic-year enrollment.")}, status=400)
    if not is_expected_date(academic_year_level, today):
        return JsonResponse({"error": _("Today is not an expected attendance day.")}, status=400)
    with transaction.atomic():
        rec, created = AttendanceRecord.objects.get_or_create(
            student=user,
            course_offering=offering,
            attendance_date=today,
            action=action,
            defaults={"scanned_by": request.user},
        )
    if created:
        return JsonResponse({"status": "recorded", "action": action})
    return JsonResponse({"status": "already_recorded", "action": action})

@capability_required(can_scan_attendance)
def attendance_management(request):
    year_id = request.GET.get("academic_year")
    if year_id and not year_id.isdigit():
        year_id = None
    level_id = request.GET.get("level")
    action = request.GET.get("action")
    offering_id = request.GET.get("course_offering")
    attendance_date = request.GET.get("date", "")
    student_search = normalize_search_text(request.GET.get("student", "").strip()[:100])
    years = AcademicYear.objects.all()
    levels = Level.objects.filter(
        year_links__academic_year_id=year_id
    ).order_by("ordering") if year_id else Level.objects.none()
    course_offerings = CourseOffering.objects.filter(
        academic_year_level__academic_year_id=year_id
    ).select_related(
        "course", "academic_year_level__academic_year", "academic_year_level__level"
    ).order_by("course__name", "pk") if year_id else CourseOffering.objects.none()
    if year_id:
        records = AttendanceRecord.objects.all().select_related(
            "student", "course_offering__course", "course_offering__academic_year_level__academic_year",
            "course_offering__academic_year_level__level", "scanned_by"
        ).filter(
            Q(course_offering__academic_year_level__academic_year_id=year_id)
            | Q(
                student__enrollments__academic_year_level__academic_year_id=year_id,
                student__enrollments__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
                student__enrollments__enrollment_type=Enrollment.Type.NORMAL,
            )
        ).distinct()
        if level_id and level_id.isdigit():
            records = records.filter(
                Q(course_offering__academic_year_level__level_id=int(level_id))
                | Q(
                    student__enrollments__academic_year_level__level_id=int(level_id),
                    student__enrollments__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
                    student__enrollments__enrollment_type=Enrollment.Type.NORMAL,
                )
            ).distinct()
        if offering_id and offering_id.isdigit():
            records = records.filter(course_offering_id=int(offering_id))
        if action in ("entrance", "exit"):
            records = records.filter(action=action)
        if attendance_date:
            try:
                parsed_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
                records = records.filter(attendance_date=parsed_date)
            except (TypeError, ValueError):
                records = records.none()
        if student_search:
            records = records.filter(
                normalized_contains_q(
                    ("student__username", "student__first_name", "student__last_name", "student__email"),
                    student_search,
                )
            )
        records = records.order_by("-attendance_date", "-scanned_at")
    else:
        records = AttendanceRecord.objects.none()
    page_obj = Paginator(records, 25).get_page(request.GET.get("page", 1))
    return render_page(request, "attendance_management.html", "partials/attendance_management_content.html", {
        "records": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
        "years": years,
        "levels": levels,
        "course_offerings": course_offerings,
        "selected_year": int(year_id) if year_id and year_id.isdigit() else None,
        "selected_level": int(level_id) if level_id and level_id.isdigit() else None,
        "selected_offering": int(offering_id) if offering_id and offering_id.isdigit() else None,
        "selected_action": action if action in ("entrance", "exit") else "",
        "selected_date": attendance_date,
        "student_search": student_search,
        "has_filter": bool(year_id),
    })

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
    return render(request, "attendance_correction.html", {
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
# PHASE 6 — Online Lecture Progress Tracking
# ============================================================================

def create_viewing_session(student, lesson, part_id, *, mobile_session=None):
    session_id = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(hours=2)
    access_channel = ViewingSession.AccessChannel.MOBILE if mobile_session is not None else ViewingSession.AccessChannel.WEB
    if mobile_session is not None and mobile_session.user_id != student.pk:
        raise PermissionDenied(_("The viewing session is invalid."))
    session = ViewingSession.objects.create(
        student=student,
        lesson=lesson,
        mobile_session=mobile_session,
        access_channel=access_channel,
        part_id=part_id,
        session_id=session_id,
        expires_at=expires_at,
    )
    return session


MEDIA_TOKEN_VERSION = "v2"
MEDIA_AUDIENCES = frozenset({"web", "mobile"})
MEDIA_SEGMENT_TOKEN_LIFETIME = timedelta(minutes=10)


def _media_binding(audience, mobile_session_id=None):
    """Return the opaque bearer-session binding carried by mobile tokens."""
    if audience != "mobile":
        return ""
    if mobile_session_id is None:
        raise ValueError("Mobile media tokens require a mobile session binding.")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"mobile-media:{mobile_session_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _validate_media_audience(audience):
    if audience not in MEDIA_AUDIENCES:
        raise ValueError("Unsupported media audience.")
    return audience


def _sign_media_message(message):
    return hmac.new(
        settings.MEDIA_WORKER_HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def sign_session(session_id, expires_at, *, audience="web", mobile_session_id=None):
    audience = _validate_media_audience(audience)
    expires_at_value = int(expires_at.timestamp())
    binding = _media_binding(audience, mobile_session_id)
    message = f"{MEDIA_TOKEN_VERSION}|{audience}|{session_id}|{expires_at_value}|{binding}"
    signature = _sign_media_message(message)
    return f"{MEDIA_TOKEN_VERSION}:{audience}:{session_id}:{expires_at_value}:{binding}:{signature}"


def sign_media_token(session_id, expires_at, segment_key, *, audience="web", token_expires_at=None):
    audience = _validate_media_audience(audience)
    token_expiry = token_expires_at or expires_at
    expires_at_value = int(token_expiry.timestamp())
    message = f"{MEDIA_TOKEN_VERSION}|{audience}|{session_id}|{expires_at_value}|{segment_key}"
    return f"{MEDIA_TOKEN_VERSION}:{audience}:{session_id}:{expires_at_value}:{_sign_media_message(message)}"


def _valid_session_token(token, session, *, audience="web", mobile_session_id=None):
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 6 or parts[0] != MEDIA_TOKEN_VERSION or parts[1] != audience:
        return False
    if parts[2] != session.session_id:
        return False
    try:
        expires_at = int(parts[3])
    except (TypeError, ValueError):
        return False
    if expires_at != int(session.expires_at.timestamp()) or timezone.now().timestamp() >= expires_at:
        return False
    binding = parts[4]
    if audience == "mobile":
        if not binding:
            return False
        if mobile_session_id is not None:
            try:
                expected_binding = _media_binding(audience, mobile_session_id)
            except ValueError:
                return False
            if not hmac.compare_digest(binding, expected_binding):
                return False
    elif binding:
        return False
    expected = _sign_media_message(
        f"{MEDIA_TOKEN_VERSION}|{audience}|{session.session_id}|{expires_at}|{binding}"
    )
    return hmac.compare_digest(parts[5], expected)

@login_required
def start_viewing_session(request, offering_id, lesson_id, file_index):
    offering = get_accessible_offering_or_403(request.user, offering_id)
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(request.user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))
    links = json.loads(lesson.links)
    if file_index < 0 or file_index >= len(links):
        return JsonResponse({"error": _("Invalid media file.")}, status=400)
    file_info = links[file_index]
    if file_info.get("file_type") not in {"video", "audio"} or not file_info.get("id"):
        return JsonResponse({"error": _("Invalid media file.")}, status=400)

    part_id = file_info.get("part_id", "")
    session = ViewingSession.objects.filter(
        student=request.user,
        lesson=lesson,
        part_id=part_id,
        expires_at__gt=timezone.now(),
    ).order_by("-expires_at").first()
    if session is None:
        session = create_viewing_session(request.user, lesson, part_id)
    token = sign_session(session.session_id, session.expires_at, audience="web")
    progress_percent = LectureProgress.objects.filter(
        student=request.user,
        lesson=lesson,
        part_id=part_id,
    ).values_list("percent", flat=True).first() or 0
    return JsonResponse({
        "session_id": session.session_id,
        "token": token,
        "expires_at": session.expires_at.isoformat(),
        "manifest_url": reverse("lesson-manifest", args=[offering.pk, lesson.pk, file_index]),
        "progress_percent": progress_percent,
    })

@login_required
def lesson_manifest(request, offering_id, lesson_id, file_index):
    offering = get_accessible_offering_or_403(request.user, offering_id)
    lesson = get_object_or_404(Lesson, pk=lesson_id, course_offering=offering)
    if not user_has_management_role(request.user) and lesson.status != PublicationStatus.PUBLISHED:
        raise PermissionDenied(_("You do not have access to this lesson."))
    links = json.loads(lesson.links)
    if file_index < 0 or file_index >= len(links):
        return HttpResponse(status=404)
    file_info = links[file_index]
    if file_info.get("file_type") not in {"video", "audio"} or not file_info.get("id"):
        return HttpResponse(status=404)
    key = file_info["id"]
    session_id = request.GET.get("session_id", "")
    token = request.GET.get("token", "")
    if not session_id or not token:
        return JsonResponse({"error": _("Viewing session is required.")}, status=401)
    session = get_object_or_404(
        ViewingSession,
        session_id=session_id,
        student=request.user,
        lesson=lesson,
        part_id=file_info.get("part_id", ""),
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
    if not _valid_session_token(token, session, audience=audience):
        return JsonResponse({"error": _("Invalid viewing session token.")}, status=403)
    try:
        response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=key)
        playlist = response["Body"].read().decode()
    except Exception:
        return HttpResponse(status=404)

    playlist_lines = []
    manifest_folder = posixpath.dirname(key).strip("/")
    for line in playlist.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.lower().endswith(".ts"):
            if stripped.startswith(("/", "http://", "https://")):
                return HttpResponse(status=404)
            segment_key = posixpath.normpath(posixpath.join(posixpath.dirname(key), stripped))
            if segment_key in {".", ".."} or segment_key.startswith("../"):
                return HttpResponse(status=404)
            if manifest_folder and not segment_key.startswith(f"{manifest_folder}/"):
                return HttpResponse(status=404)
            if get_segment_number(segment_key) is None:
                return HttpResponse(status=404)
            segment_expires_at = min(
                session.expires_at,
                timezone.now() + MEDIA_SEGMENT_TOKEN_LIFETIME,
            )
            signed_token = sign_media_token(
                session.session_id,
                session.expires_at,
                segment_key,
                audience=audience,
                token_expires_at=segment_expires_at,
            )
            worker_base = settings.CLOUD_WORKER.rstrip("/")
            playlist_lines.append(
                f"{worker_base}/media/{quote(session.session_id, safe='')}/"
                f"{quote(segment_key, safe='')}?token={quote(signed_token, safe='')}"
            )
        else:
            playlist_lines.append(line)
    return HttpResponse(
        "\n".join(playlist_lines) + ("\n" if playlist.endswith("\n") else ""),
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
    expected = _sign_media_message(
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
    if not request.user.is_authenticated:
        return JsonResponse({"error": _("Authentication required")}, status=401)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Invalid request format.")}, status=400)

    session_id = data.get("session_id")
    ranges = data.get("ranges", [])

    if not session_id or not isinstance(ranges, list):
        return JsonResponse({"error": _("Missing session identifier or time ranges.")}, status=400)

    if len(ranges) > 200:
        return JsonResponse({"error": _("Too many time ranges.")}, status=400)

    # Validate ranges are finite, ordered, non-negative, and bounded before
    # intersecting them with the verified media timeline.
    try:
        ranges = [[float(s), float(e)] for s, e in ranges]
    except (ValueError, TypeError):
        return JsonResponse({"error": _("Invalid time range format.")}, status=400)
    max_duration = float(getattr(settings, "MEDIA_MAX_DURATION_SECONDS", 3 * 60 * 60))
    if any(
        not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
        or end > max_duration
        for start, end in ranges
    ):
        return JsonResponse({"error": _("Invalid time range format.")}, status=400)

    session = get_object_or_404(ViewingSession, session_id=session_id)

    # Reject another user's session
    if session.student != request.user:
        return JsonResponse({"error": _("Session belongs to another user.")}, status=403)

    if timezone.now() >= session.expires_at:
        return JsonResponse({"error": _("Session expired.")}, status=403)

    if not user_can_write_offering_activity(
        request.user,
        session.lesson.course_offering,
        allow_management=allow_management,
    ):
        return JsonResponse({"status": "ok", "note": _("Historical content is read-only")})

    session.last_heartbeat = timezone.now()
    session.save(update_fields=["last_heartbeat"])

    # Get verified segment requests for this session
    verified = set(session.verified_requests.values_list("segment_number", flat=True))
    if not verified:
        return JsonResponse({"status": "ok", "note": _("No verified segments yet")})

    # Get segment time ranges from lesson and filter to verified ones
    lesson_segments = get_lesson_segments(
        session.lesson,
        CLOUD_CLIENT,
        bucket_name,
    ).get(session.part_id, [])
    all_ranges = [
        [float(sr.get("start", 0)), float(sr.get("end", 0))]
        for sr in lesson_segments
        if float(sr.get("end", 0)) > float(sr.get("start", 0))
    ]
    verified_ranges = []
    for sr in lesson_segments:
        if sr.get("number") in verified:
            verified_ranges.append([float(sr.get("start", 0)), float(sr.get("end", 0))])

    if not verified_ranges:
        return JsonResponse({"status": "ok", "note": _("No verified segment ranges")})

    intersected = intersect_verified(ranges, verified_ranges)
    merged = merge_ranges(intersected)
    unique_secs = unique_seconds(merged)
    total_secs = sum(end - start for start, end in all_ranges)
    percent = calculate_percent(unique_secs, total_secs) if total_secs > 0 else 0

    progress, created = LectureProgress.objects.update_or_create(
        student=request.user,
        lesson=session.lesson,
        part_id=session.part_id,
        defaults={
            "percent": percent,
        }
    )

    # Merge with existing ranges
    if not created and progress.merged_ranges:
        all_ranges = merge_ranges(list(progress.merged_ranges) + merged)
        progress.merged_ranges = all_ranges
        unique_secs = unique_seconds(all_ranges)
        percent = calculate_percent(unique_secs, total_secs) if total_secs > 0 else 0
        progress.percent = percent
    else:
        progress.merged_ranges = merged

    progress.unique_seconds = int(round(unique_secs))
    COMPLETION_THRESHOLD = 80
    if not progress.completed_at and percent >= COMPLETION_THRESHOLD:
        progress.completed_at = timezone.now()

    progress.save()

    return JsonResponse({
        "status": "ok",
        "percent": percent,
        "completed": progress.completed_at is not None,
    })

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
    if academic_year_level_id and str(academic_year_level_id).isdigit():
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

    progress = progress.select_related("student", "lesson").order_by("-lesson__name", "student__username")
    page_obj = Paginator(progress, 25).get_page(request.GET.get("page", 1))

    return render_page(request, "progress_dashboard.html", "partials/progress_dashboard_content.html", {
        "progress": page_obj,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
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
    })

@capability_required(can_view_reports)
def report_dashboard(request):
    academic_year_level_id = request.GET.get("academic_year_level") or request.GET.get("academic_year")
    student_search = normalize_search_text(request.GET.get("student", "").strip()[:100])
    study_mode = request.GET.get("study_mode") or None
    course_offering_id = request.GET.get("course_offering") or None

    levels = Level.objects.order_by("ordering")
    academic_years = AcademicYearLevel.objects.filter(academic_year__is_active=True).select_related("academic_year", "level").order_by(
        "-academic_year__ordering", "level__ordering"
    )
    selected_year = None
    rows = []
    page_obj = None

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
            course_offering_id=int(course_offering_id) if course_offering_id and str(course_offering_id).isdigit() else None,
        )
        page_obj = Paginator(report_query, 25).get_page(request.GET.get("page", 1))
        rows = build_report_page_rows(
            page_obj.object_list,
            selected_year,
            course_offering_id=int(course_offering_id) if course_offering_id and str(course_offering_id).isdigit() else None,
        )

    context = {
        "title": _("Combined Report"),
        "levels": levels,
        "academic_years": academic_years,
        "selected_year": selected_year,
        "selected_level": request.GET.get("level", ""),
        "selected_student": student_search,
        "selected_study_mode": study_mode or "",
        "selected_course_offering": course_offering_id or "",
        "students": [],
        "course_offerings": course_offerings if academic_year_level_id else [],
        "rows": rows,
        "row_count": page_obj.paginator.count if page_obj else 0,
        "page_obj": page_obj,
        "pagination_query": pagination_query_string(request),
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
            _("Expected"), _("Valid"), _("Invalid"), _("Absent"), _("Attendance %"), _("Absence %"),
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
                row["grade_percent"], row["expected"], row["valid"], row["invalid"], row["absent"],
                row["attendance_rate"], row["absence_rate"],
            ])
            yield output.getvalue()

    safe_name = _safe_filename(scope.academic_year.name)
    response = StreamingHttpResponse(csv_rows(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="report_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    return response

@capability_required(can_view_reports)
def export_report_xlsx(request):
    academic_year_level_id = request.GET.get("academic_year_level") or request.GET.get("academic_year")
    if not academic_year_level_id:
        return HttpResponse(_("Academic year is required."), status=400)
    scope = get_object_or_404(AcademicYearLevel, pk=academic_year_level_id)

    report_kwargs = {
        "student_id": request.GET.get("student") or None,
        "study_mode": request.GET.get("study_mode") or None,
        "course_offering_id": request.GET.get("course_offering") or None,
    }

    wb = openpyxl.Workbook(write_only=True)

    ws1 = wb.create_sheet(_("Grades Summary"))
    headers = [_("Student ID"), _("Username"), _("First Name"), _("Last Name"), _("Course"), _("Quiz"),
               _("Grade"), _("Total"), _("Percent")]
    ws1.append(headers)
    for row in iter_report_data(scope, **report_kwargs):
        for detail in row["grade_details"]:
            pct = round(detail["grade"] / detail["total"] * 100, 1) if detail["total"] else 0
            ws1.append([
                row["student_id"], row["username"], row["first_name"], row["last_name"],
                detail["course_name"], detail["quiz_name"],
                detail["grade"], detail["total"], pct,
            ])

    ws2 = wb.create_sheet(_("Attendance Summary"))
    ws2.append([_("Student ID"), _("Username"), _("First Name"), _("Last Name"),
                _("Expected"), _("Valid"), _("Invalid"), _("Absent"),
                _("Attendance %"), _("Absence %")])
    for row in iter_report_data(scope, **report_kwargs):
        ws2.append([
            row["student_id"], row["username"], row["first_name"], row["last_name"],
            row["expected"], row["valid"], row["invalid"], row["absent"],
            row["attendance_rate"], row["absence_rate"],
        ])

    ws3 = wb.create_sheet(_("Attendance Daily"))
    ws3.append([_("Student ID"), _("Username"), _("First Name"), _("Last Name"),
                _("Date"), _("Day"), _("Entrance"), _("Exit"), _("Status")])
    expected = get_expected_dates(scope)
    report_rows = iter_report_data(scope, **report_kwargs)
    while True:
        batch = list(islice(report_rows, 500))
        if not batch:
            break
        student_ids = [row["student_id"] for row in batch]
        records_by_student = defaultdict(list)
        attendance_queryset = AttendanceRecord.objects.filter(
            course_offering__academic_year_level=scope, student_id__in=student_ids
        )
        if report_kwargs["course_offering_id"]:
            attendance_queryset = attendance_queryset.filter(course_offering_id=report_kwargs["course_offering_id"])
        for rec in attendance_queryset.order_by("student_id", "attendance_date"):
            records_by_student[rec.student_id].append(rec)
        for row in batch:
            entrance_by_date = {rec.attendance_date for rec in records_by_student[row["student_id"]] if rec.action == "entrance"}
            exit_by_date = {rec.attendance_date for rec in records_by_student[row["student_id"]] if rec.action == "exit"}
            for day in expected:
                has_entrance = day in entrance_by_date
                has_exit = day in exit_by_date
                status = _("Valid") if has_entrance and has_exit else (_("Invalid") if has_entrance or has_exit else _("Absent"))
                ws3.append([
                    row["student_id"], row["username"], row["first_name"], row["last_name"],
                    day.isoformat(), formats.date_format(day, "l"),
                    day.isoformat() if has_entrance else "", day.isoformat() if has_exit else "", status,
                ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_name = _safe_filename(scope.academic_year.name)
    response = HttpResponse(buf.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="report_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    return response


@capability_required(can_view_reports)
def export_evaluation_xlsx(request):
    formula_id = request.GET.get("formula")
    scope_id = request.GET.get("scope")
    offering_id = request.GET.get("course_offering")
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
    workbook = build_evaluation_workbook(formula)
    response = HttpResponse(
        workbook.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    safe_name = _safe_filename(
        f"{formula.academic_year_level.academic_year.name}-{formula.academic_year_level.level.display_name}"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="evaluation_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    )
    return response

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
