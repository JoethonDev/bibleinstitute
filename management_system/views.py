# Django Core Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, FileResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, FormView, DetailView
from django.utils.translation import gettext as _
from django.utils import translation
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import views, update_session_auth_hash
from django.db.models import F, Q, Sum
from django.db import transaction
from django.contrib import messages
from django.contrib.messages import success, error, info
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
import hashlib
import hmac
import io
import secrets
import random

# Third Party
from logging import getLogger
import boto3
import json
import re
import os
import csv
import requests

# Internal Imports - Models
from .models import *

# Internal Imports - Forms
from .forms import CSVUploadForm, CourseForm, UserCreationForm, UserUpdateForm, ProfileUpdateForm, SignupForm

# Internal Imports - Utilities
from .utils.csv_export import export_users_to_csv, export_quiz_with_submissions_to_csv, export_single_submission_to_csv, export_quiz_summary_to_csv, export_yearly_transcript_to_csv, build_yearly_transcript_rows, _csv_safe_cell, _safe_filename
from .utils.reports import build_report_data
from .utils.r2_filters import R2FileFilter, FileFilterConfig, get_filter_preset, FILTER_PRESETS
from .utils.r2_manager import R2Manager
from .utils.file_validator import validate_upload_filename, FileValidator
from .utils.storage_operations import list_current_folder, download_from_bucket, generate_unique_url
from .utils.helpers import get_datetime, paginate_obj, render_dashboard, unpack_quiz_form, safe_get_user, parse_json_value, get_student_quiz_status, is_quiz_in_user_window, user_can_access_course, user_has_management_role
from .utils.decorators import capability_required, can_manage_content, can_delete_content, can_grade, can_view_reports, can_manage_applications, can_scan_attendance, can_correct_attendance
from .utils.email import send_application_received, send_application_activated, send_application_declined
from .utils.application_uploads import upload_application_file
from .utils.attendance import is_expected_date, get_attendance_summary
from .theme_catalog import THEMES, get_theme, SHOWCASE_PAGES

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

# Initialize R2 Manager
R2_MANAGER = R2Manager(CLOUD_CLIENT, bucket_name)

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
        return HttpResponse(_("Unauthorized"), status=401)

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
    if request.user.is_authenticated:
        user = User.objects.get(username=request.user)
        role = user.role.role if user.role else "junior"

        if role in ("admin", "staff"):
            return redirect("user-dashboard")

        return portal(request)

    return render(request, "home.html")


@login_required(login_url=LOGIN_URL)
def portal(request):
    user = User.objects.get(username=request.user)
    role = user.role.role if user.role else "junior"

    courses = Course.fetch_courses_by_role(role)
    course_count = sum(len(level["courses"]) for level in courses)

    current_time = now()
    open_quiz_count = 0
    for level in courses:
        for course in level["courses"]:
            for quiz in course.quizzes.all():
                quiz_status, __ = get_student_quiz_status(quiz, user, current_time)
                if quiz_status == "exam":
                    open_quiz_count += 1

    return render(request, "index.html", {
        "course_count": course_count,
        "open_quiz_count": open_quiz_count,
        "full_name": f"{user.first_name} {user.last_name}".strip() or user.username,
        "role_display": str(_(user.role.get_role_display())) if user.role else "",
    })


def about_page(request):
    return render(request, "about.html")

# Detail Class [handles with and without pk routes]
class ProfileDetail(LoginProtection, DetailView):
    model = User
    pk_url_kwarg = "user_id"
    template_name = "profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.object
        form = ProfileUpdateForm(instance=user)
        for field in form.fields.values():
            field.disabled = True

        context['form'] = form
        context['courses'] = Course.fetch_courses_by_role(user.role.role)

        # Recent quiz grade submissions (last 5)
        context['recent_grades'] = (
            Grade.objects
            .filter(user=user)
            .select_related('quiz', 'quiz__course')
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
        # Check request has user_id route
        # If user is not admin and pk in url
        if not request.get_full_path().endswith("/profile/") and not can_manage_content(request.user):
            return HttpResponse(_("Unauthorized"), status=401) # Translate "Unauthorized"
        return super().get(request, *args, **kwargs)


    def get_object(self, queryset = None):
        # if not pk in route
        if not self.kwargs.get(self.pk_url_kwarg):
            return self.request.user
        return super().get_object(queryset)

# Update
class ProfileUpdate(LoginProtection, UpdateView):
    form_class = ProfileUpdateForm
    model = User
    pk_url_kwarg = "user_id"
    success_url = reverse_lazy("view-profile")
    template_name = "update_profile.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        success(self.request, _("Profile is updated successfully!"), extra_tags="alert-success") # Translate
        # If the password field was changed, update the session to keep user logged in
        if "password" in form.cleaned_data and form.cleaned_data["password"]:
            update_session_auth_hash(self.request, self.object)

        return response

    def get_object(self, queryset = None):
        return self.request.user

# Course Routes
@login_required(login_url=LOGIN_URL)
def view_courses(request):
    username = request.user
    logger.info(f"fetching available course for user : {username}")

    user = User.objects.get(username=username)

    if user_has_management_role(user):
        courses = Course.fetch_courses_by_role("management")
        logger.info(f"{username} access all academic years")
    else:
        enrolled_offerings = CourseOffering.objects.filter(
            academic_year__enrollments__student=user,
            academic_year__enrollments__status="active",
            status="published",
        ).select_related("course", "academic_year")

        if enrolled_offerings.exists():
            levels_map = {}
            for offering in enrolled_offerings:
                level = offering.course.level
                if level not in levels_map:
                    levels_map[level] = {
                        "level_name": str(_(Course.LEVELS_NAME.get(level, level))),
                        "courses": [],
                    }
                levels_map[level]["courses"].append(offering.course)
            courses = list(levels_map.values())
            logger.info(f"{username} access {len(courses)} academic years via enrollment")
        else:
            courses = Course.fetch_courses_by_role(user.role.role)
            logger.info(f"{username} access {len(courses)} academic years via role fallback")

    for level in courses:
        for course in level["courses"]:
            course.lesson_count = course.lessons.count()
            course.quiz_count = course.quizzes.count()

    return render(request, "course_view.html", {
        "courses": courses
    })


@login_required(login_url=LOGIN_URL)
def view_course_details(request, course_id):
    user = User.objects.get(username=request.user)

    try:
        course = get_object_or_404(Course, pk=course_id)

        logger.info(f"User : {user} is accessing {course.name} course")

        context = {
            "lessons": [],
            "quizzes": course.fetch_quizzes(user),
            "course_id": course_id,
            "course": course,
        }
        if user_has_management_role(user):
            context['lessons'] = course.lessons.all()
            logger.info(f"User : {user} is accessing all lessons")

        else:
            if not user_can_access_course(user, course):
                return HttpResponse(_("Unauthorized"), status=401) # Translate "Unauthorized"
            offerings = CourseOffering.objects.filter(
                course=course,
                academic_year__enrollments__student=user,
                academic_year__enrollments__status="active",
                status="published",
            )
            if offerings.exists():
                context['lessons'] = Lesson.objects.filter(
                    course_offering__in=offerings,
                    status="published",
                )
                logger.info(f"User : {user} is accessing lessons via enrollment")
            else:
                # Fallback to date-range access for pre-backfill users
                join_date = user.joined_date
                try:
                    end_date = join_date.replace(year=join_date.year + course.level)
                except ValueError:
                    end_date = join_date.replace(year=join_date.year + course.level, day=28)
                context['lessons'] = course.lessons.filter(created_date__range=(join_date, end_date))
                logger.info(f"User : {user} is accessing lessons within range {join_date} and {end_date}")


    except Http404:
        logger.error(f"Course with id: {course_id} not found for user: {user.username}")
        raise Http404
    
    return render(request, 'course_detail.html', context)


@login_required(login_url=LOGIN_URL)
def view_lesson_details(request, course_id, lesson_id):
    try:
        user = User.objects.get(username=request.user)
        course = get_object_or_404(Course, pk=course_id)
        lesson = get_object_or_404(Lesson, pk=lesson_id, course=course)
        lesson_links = json.loads(lesson.links)
        can_access = user_can_access_course(user, course) and (
            user_has_management_role(user)
            or (lesson.course_offering_id and user.enrollments.filter(
                academic_year__course_offerings__pk=lesson.course_offering_id,
                status="active",
            ).exists())
            or lesson.can_access(user.joined_date)
        )
        if can_access:
            logger.info(f"User : {user} is accessing {lesson.name} lesson from {course.name} course")
            return render(request, "lesson_stream.html", {
                "lesson_id": lesson_id,
                "links" : [{
                    "url" : reverse("lesson-stream", args=[lesson_id, file_index]),
                    "name" : file.get("name", ""),
                    "type" : file['file_type'],
                    "part_id" : file.get("part_id", ""),
                } for file_index, file in enumerate(lesson_links)]
            })
        
        else:
            logger.error(f"{user.username} is not authorized to access lesson : {lesson.name}")
            return HttpResponse(_('Unauthorized'), status=401) # Translate 'Unauthorized'

    except Http404:
        logger.error(f"Course with id: {course_id} or Lesson with id : {lesson_id} not found for user: {user.username}")
        raise Http404


@login_required(login_url=LOGIN_URL)
def stream_lesson(request, lesson_id, file_index):
    try:
        username = request.user
        user = User.objects.get(username=username)
        lesson = get_object_or_404(Lesson, pk=lesson_id)
        can_stream = user_has_management_role(user) or (
            lesson.course_offering_id
            and user.enrollments.filter(
                academic_year__course_offerings__pk=lesson.course_offering_id,
                status="active",
            ).exists()
        ) or lesson.can_access(user.joined_date)
        if not user_can_access_course(user, lesson.course) or not can_stream:
            logger.error(f"{user.username} is not authorized to stream lesson : {lesson.name}")
            return HttpResponse(_('Unauthorized'), status=401)

        logger.info(f"User : {username} is streaming video from {lesson.name} lesson")

        lesson_links = json.loads(lesson.links)
        if file_index < 0 or file_index >= len(lesson_links):
            raise Http404
        file_data = lesson_links[file_index]

        file_name = file_data.get("name")
        file_key = file_data.get("id")
        file_type = file_data.get("file_type")

        if file_type == "book":
            return JsonResponse({"url" : f"{settings.CLOUD_WORKER}{file_key}"})
        
        else:
            m3u8_content = download_from_bucket(CLOUD_CLIENT, bucket_name, file_key).read().decode("utf-8")

            # Build Segments
            segments_names = re.findall(r"^.*\.ts$", m3u8_content, re.MULTILINE)
            folder = "/".join(file_key.split("/")[:-1])
            for segment in segments_names:
                segment_key = f"{folder}/{segment}" if folder else segment
                m3u8_content = m3u8_content.replace(segment, f"{settings.CLOUD_WORKER}{segment_key}")
                
            logger.info(f"{file_name} HLS file of {lesson.name} is loaded!")

            # Stream the file content as response
            logger.info(f"Sending {file_name} HLS file of {lesson.name} to {username}")
            return HttpResponse(
                m3u8_content,
                content_type='application/vnd.apple.mpegurl'
            )

    except Http404:
        logger.error(f"Lesson with id : {lesson_id} not found for user: {username}")
        raise Http404
    

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
def take_exam(request, course_id, quiz_id):
    try:
        user = User.objects.get(username=request.user)
        course = get_object_or_404(Course, pk=course_id)
        quiz = get_object_or_404(Quiz, pk=quiz_id, course=course)

        submission_datetime = now()
        # Quiz can be submitted from opening time through the 30-minute closing buffer
        can_submit = quiz.opening_date <= submission_datetime <= quiz.closing_date + timedelta(minutes=30)

        # Check if user has previously taken this quiz
        quiz_mode, grade = get_student_quiz_status(quiz, user, submission_datetime)
        total_grade = grade.total_grade if grade else 0

        if request.method == "GET":
            logger.info(f"User : {user} is accessing {quiz.name} in {course.name} course")

            if not user_can_access_course(user, course):
                return HttpResponse(_("Unauthorized"), status=401)

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

            # Serialize questions; strip correct_answer for closed_unsolved to avoid leaking via HTML
            if quiz_mode == "closed_unsolved":
                questions = []
                for q in query_set:
                    serialized = q.serialize()
                    serialized.pop("correct_answer", None)
                    questions.append(serialized)
            else:
                questions = [q.serialize() for q in query_set]

            if quiz_mode == "exam":
                random.shuffle(questions)
                questions = [prepare_exam_question(question) for question in questions]

            return render(request, "display_quiz.html", {
                "quiz_name": quiz.name,
                "username": request.user.username,
                "questions": questions,
                "is_student": True,
                "mode": quiz_mode,
                "extended_view": "base.html",
                "id": "container",
                "total_grade": total_grade,
                "closing_date": quiz.closing_date.timestamp(),
                "exam_taken": True if grade else False,
                "back_url": reverse("course-details", args=[course_id,]),
                "quiz_closing_date_str": quiz.closing_date.strftime("%d/%m/%Y %H:%M"),
            })
        
        elif request.method == "POST":
            # Prevent another submission
            if not grade:
                if not user_can_access_course(user, course):
                    return HttpResponse(_("Unauthorized"), status=401)

                # Guard: reject if quiz is no longer submittable
                if not can_submit or not is_quiz_in_user_window(quiz, user):
                    # Send back to main page with error message TODO
                    return HttpResponse(_("Invalid Request, submission is closed!")) # Translate
                
                logger.info(f"User : {user} has submitted {quiz} at {submission_datetime.strftime('%d/%m/%Y, %H:%M:%S')}")

                questions_data, not_used = unpack_quiz_form(request.POST)

                # Create Quesitons
                submissions = []
                repeated_submissions = set()
                total_grade = 0
                for data in questions_data.values():
                    question_id = int(data['id'])
                    submitted_answer = data.get("answer", "")
                    if question_id in repeated_submissions:
                        continue
                    repeated_submissions.add(question_id)
                    # Create instance
                    submission = Submission(question_id=question_id, submitted_answer=submitted_answer, user=user)
                    submission.assign_grade()

                    # Sum grades
                    total_grade += submission.grade

                    # Append for bulk create!
                    submissions.append(submission)
                
                # For leaved questions or error of not submitting all questions
                unanswered_questions = Question.objects.filter(quiz_id=quiz_id).exclude(pk__in=repeated_submissions)
                for data in unanswered_questions:
                    submissions.append(
                        Submission(question_id=data.pk, submitted_answer="-", user=user)
                    )

                try:
                    with transaction.atomic():
                        Submission.objects.bulk_create(submissions)
                        Grade.objects.create(quiz=quiz, user=user, submitted_at=submission_datetime, total_grade=total_grade)
                        logger.info(f"{user}'s submission is added successfully to {quiz}")
                    success(request, _("Quiz is sent successfully!"), extra_tags="alert-success") # Translate

                except Exception as e:
                    error(request, _("Sending quiz has failed, Please Try again!"), extra_tags="alert-danger") # Translate
                    logger.error(f"{user}'s submission failed for {quiz.name}")
                    logger.error(f"Stack Traceback: {e}")
            
            return redirect(reverse("quiz-details", args=[course_id, quiz_id]))
        
        else:
            return HttpResponse(_("Not allowed method"), 400) # Translate
        
    except Http404:
            logger.error(f"Course with id: {course_id} or Quiz with id: {quiz_id} not found for user: {user.username}")
            raise Http404
    
# Admin Views
@capability_required(can_manage_content)
def admin_panel(request):
    logger.info(f"User : {request.user} accesses admin panel successfully")
    return render(request, "admin_panel.html")

@capability_required(can_manage_content)
def export_users_csv(request):
    return export_users_to_csv()

@capability_required(can_manage_content)
def user_dashboard(request):
    view = "user"
    
    name = request.GET.get("name", None)
    role_value = request.GET.get("filtering", None)
    user = request.user
    # Start with an empty Q object (matches all)
    query = Q()

    # Dynamically add conditions if filters are present
    if name:
        query &= Q(username__icontains=name) | Q(first_name__icontains=name) | Q(last_name__icontains=name)
    if role_value:
        role_name = Role.get_by_readable_value(role_value)
        query &= Q(role=role_name)  # Assuming `role` is a field in the User model
    
    logger.info(f"User : {user} filters {view}s using {name} name and {role_value} role")

    users = User.objects.filter(query)

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
        "options" : [_("Choose Role"), *Role.get_readable_values()] # Translate "Choose Role"
    }

    return render_dashboard(request, users, view, context)

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
                                errors_list.append(f"Line {line_number}: User '{username}' already exists.")
                                continue

                            try:
                                role = Role.objects.get(role=role_name.lower().strip())
                            except Role.DoesNotExist:
                                errors_list.append(f"Line {line_number}: Role '{role_name}' does not exist.")
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
                            errors_list.append(f"Line {line_number}: Incorrect number of columns. Expected 5, got {len(row)}.")
                        except ValidationError as e:
                            errors_list.append(f"Line {line_number}: Validation error for user '{username}': {', '.join(e.messages)}")
                        except Exception as e:
                             errors_list.append(f"Line {line_number}: An unexpected error occurred: {e}")

                    if errors_list:
                        # If there are errors, raise an exception to trigger a rollback of the transaction
                        raise Exception("Errors found in CSV file.")

                success(request, _(f'{len(users_to_create)} users have been created successfully!'), extra_tags="alert-success")
                return redirect('user-dashboard')

            except Exception as e:
                # This will catch the explicit raise and any other exceptions
                for err in errors_list:
                    error(request, err, extra_tags="alert-danger")
                if not errors_list:
                     error(request, _(f"An error occurred: {e}"), extra_tags="alert-danger")
                return redirect('user-bulk-create')

    else:
        form = CSVUploadForm()
    
    return render(request, 'user_bulk_form.html', {'form': form})

# User Dashboard
class CreateUser(UserBaseView, CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy("user-create")
    action = _("create") # Translate action

class UpdateUser(UserBaseView, UpdateView):
    form_class = UserUpdateForm
    action = _("update") # Translate action

    def form_valid(self, form):
        response = super().form_valid(form)
        # If the password field was changed, update the session to keep user logged in
        print(f"condition : {self.object.pk == self.request.user.pk and 'password' in form.cleaned_data and form.cleaned_data['password']}")
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
            return HttpResponse(_("Unauthorized"), status=401)
        return super(UserBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("user-dashboard")

# Course Dashboard
@capability_required(can_manage_content)
def course_dashboard(request):   
    name = request.GET.get("name", None)
    year = request.GET.get("filtering", None)
    user = request.user
    view = "course"
    # Start with an empty Q object (matches all)
    query = Q()

    # Dynamically add conditions if filters are present
    if name:
        query &= Q(name__icontains=name)
    if year:
        for key, val in Course.LEVELS_NAME.items():
            if val == year:
                query &= Q(level=key)
    
    logger.info(f"User : {user} filters users using {name} name and {year} level")

    courses = Course.objects.filter(query).order_by("name")

    context = {
        "name_value" : name or "",
        "filtering" : year or "",
        "columns" : Course.get_columns(),
        "options" : [_("Choose Academic Year"), *[str(_(value)) for value in Course.LEVELS_NAME.values()]] # Translate "Choose Academic Year" and values
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
            return HttpResponse(_("Unauthorized"), status=401)
        return super(CourseBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("course-dashboard")

# Lesson Dashboard
@capability_required(can_manage_content)
def lesson_dashboard(request):   
    name = request.GET.get("name", None)
    year = request.GET.get("filtering", None)
    course = request.GET.get("course", None)
    user = request.user
    view = "lesson"
    # Start with an empty Q object (matches all)
    query = Q()

    # Dynamically add conditions if filters are present
    if name:
        query &= Q(name__icontains=name)
    if year:
        for key, val in Course.LEVELS_NAME.items():
            if val == year:
                query &= Q(course__level=key)
    if course:
        course_obj = Course.objects.filter(name=course).first()
        query &= Q(course=course_obj)
    
    logger.info(f"User : {user} filters users using {name} name and {year} level and {course} course")

    lessons = Lesson.objects.filter(query).order_by("name")

    context = {
        "name_value" : name or "",
        "filtering" : year or "",
        "course_value" : course or "",
        "columns" : Lesson.get_columns(),
        "options" : [_("Choose Academic Year"), *[str(_(value)) for value in Course.LEVELS_NAME.values()]], # Translate "Choose Academic Year" and values
        "subjects" : [_("Choose Course"), *[value for value in Course.objects.values_list("name", flat=True)]], # Translate "Choose Course"
        "filters" : ["course_filter.html"]
    }

    return render_dashboard(request, lessons, view, context)

@capability_required(can_manage_content)
def create_lesson(request):
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]
        course_offerings = CourseOffering.objects.filter(academic_year__is_current=True).select_related('course', 'academic_year')

        return render(request, "lesson_form.html", {
            "courses" : courses,
            "course_offerings" : course_offerings,
            "drive" : list_current_folder(CLOUD_CLIENT, bucket_name)[0],
            "is_root" : True
        })
    elif request.method == "POST":
        lesson_name = request.POST.get("lesson_name", "")
        course_name = request.POST.get("course", "")
        videos = request.POST.getlist("videos", [])
        videos_name = request.POST.getlist("videos_name", [])
        files_type = request.POST.getlist("files_type", [])
        if lesson_name and course_name and videos:
            try:
                links = [
                    {
                        "file_type": files_type[file_no],
                        "name" : videos_name[file_no],
                        "id" : videos[file_no],
                        # "prefix" : videos[file_no].split("/")[:-1]
                    } for file_no in range(len(videos))
                ]

                course = get_object_or_404(Course, name=course_name)
                offering_id = request.POST.get("course_offering")
                if offering_id:
                    course_offering = get_object_or_404(CourseOffering, pk=offering_id)
                else:
                    course_offering = CourseOffering.objects.filter(course=course, academic_year__is_current=True).first()
                Lesson.objects.create(name=lesson_name, course=course, course_offering=course_offering, links=json.dumps(links))
                success(request, _("Lesson is created successfully"), extra_tags="alert-success") # Translate
                logger.info(f"Lesson {lesson_name} is added in course {course_name} with media length of {len(links)}")

            except Http404:
                error(request, _("Create lesson has failed, Try again Please!"), extra_tags="alert-danger") # Translate
                logger.error(f"Course {course_name} is not found to create a lesson!")
        else:
            error(request, _("Create lesson has failed, Name and Videos can not be empty!"), extra_tags="alert-danger") # Translate

        return redirect(reverse("lesson-create"))

@capability_required(can_manage_content)
def navigate_folder(request, folder_id=None):
    # Check if folders_only mode is requested (for upload_video page)
    folders_only = request.GET.get('folders_only', 'false').lower() == 'true'
    
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
        courses = [course.name for course in Course.objects.all()]
        course_offerings = CourseOffering.objects.filter(academic_year__is_current=True).select_related('course', 'academic_year')
        try:
            lesson = get_object_or_404(Lesson, pk=lesson_id)
            return render(request, "lesson_form.html", {
                "courses" : courses,
                "course_offerings" : course_offerings,
                "drive" : list_current_folder(CLOUD_CLIENT, bucket_name)[0],
                "is_root" : True,
                "selected_course" : lesson.course.name,
                "selected_offering_id" : lesson.course_offering_id,
                "lesson_name" : lesson.name,
                "videos" : json.loads(lesson.links)
            })
        except Http404:
            logger.error(f"Lesson with id : {lesson_id} is not found!")
            return redirect(reverse("lesson-dashboard"))

    elif request.method == "POST":
        lesson_name = request.POST.get("lesson_name", "")
        course_name = request.POST.get("course", "")
        videos = request.POST.getlist("videos", [])
        videos_name = request.POST.getlist("videos_name", [])
        files_type = request.POST.getlist("files_type", [])
        if lesson_name and course_name and videos:
            try:
                links = [
                    {
                        "file_type": files_type[file_no],
                        "name" : videos_name[file_no],
                        "id" : videos[file_no],
                    } for file_no in range(len(videos))
                ]

                course = get_object_or_404(Course, name=course_name)
                lesson = get_object_or_404(Lesson, pk=lesson_id)
                if not lesson.can_edit:
                    return HttpResponse(_("Published or archived lesson cannot be edited."), status=403)

                lesson.name = lesson_name
                lesson.course = course
                lesson.links = json.dumps(links)
                lesson.save()

                logger.info(f"Lesson {lesson_name} is updated successfully in course {course_name} with media length of {len(links)}")
                success(request, _("Lesson is updated successfully"), extra_tags="alert-success") # Translate

            except Http404:
                error(request, _("Update lesson has failed, Try again Please!"), extra_tags="alert-danger") # Translate
                logger.error(f"Course {course_name} or Lesson with id {lesson_id} is not found to create a lesson!")
        else:
            error(request, _("Update lesson has failed, Name and Videos can not be empty!"), extra_tags="alert-danger") # Translate

        return redirect(reverse("lesson-update", args=[lesson_id]))

class DeleteLesson(LessonBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=401)
        return super(LessonBaseView, self).dispatch(request, *args, **kwargs)
    success_url = reverse_lazy("lesson-dashboard")

# Upload Videos
def ffmpeg_headers(view_func):
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        # Set required security headers
        response['Cross-Origin-Embedder-Policy'] = 'require-corp'
        response['Cross-Origin-Opener-Policy'] = 'same-origin'
        response['Cross-Origin-Resource-Policy'] = 'same-origin'
        return response
    return wrapper

@ffmpeg_headers
@capability_required(can_manage_content)
def upload_file(request):
    return render(request, "upload_video.html", {
        "drive" : list_current_folder(CLOUD_CLIENT, bucket_name, folders_only=True)[0],
        "is_root" : True
    })

@capability_required(can_manage_content)
def upload_link(request):
    body = json.loads(request.body)
    filename = body.get("filename", "")
    
    # Validate filename before generating presigned URL
    is_valid, errors = validate_upload_filename(filename)
    if not is_valid:
        return JsonResponse({
            "message": _("Invalid file"),
            "errors": errors
        }, status=400)
    
    presigned_url = CLOUD_CLIENT.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': bucket_name,
            'Key': filename,
        },
        ExpiresIn=3600  # 1 hour expiration
    )
    return JsonResponse({
        'url': presigned_url,
        'method': 'PUT',
    })

# Quiz Dashboard
@capability_required(can_manage_content)
def quiz_dashboard(request):   
    name = request.GET.get("name", None)
    year = request.GET.get("filtering", None)
    course = request.GET.get("course", None)
    user = request.user
    view = "quiz"
    # Start with an empty Q object (matches all)
    query = Q(course__isnull=False)

    # Dynamically add conditions if filters are present
    if name:
        query &= Q(name__icontains=name)
    if year:
        for key, val in Course.LEVELS_NAME.items():
            if val == year:
                query &= Q(course__level=key)
    if course:
        course_obj = Course.objects.filter(name=course).first()
        query &= Q(course=course_obj)
    
    logger.info(f"User : {user} filters users using {name} name and {year} level and {course} course")

    quizzes = Quiz.objects.filter(query).order_by("name")

    context = {
        "name_value" : name or "",
        "filtering" : year or "",
        "course_value" : course or "",
        "columns" : Quiz.get_columns(),
        "options" : [_("Choose Academic Year"), *[str(_(value)) for value in Course.LEVELS_NAME.values()]], # Translate "Choose Academic Year" and values
        "subjects" : [_("Choose Course"), *[value for value in Course.objects.values_list("name", flat=True)]], # Translate "Choose Course"
        "filters" : ["course_filter.html"],
        "submission_view" : True
    }

    return render_dashboard(request, quizzes, view, context)

@capability_required(can_manage_content)
def create_quiz(request):
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]
        course_offerings = CourseOffering.objects.filter(academic_year__is_current=True).select_related('course', 'academic_year')

        return render(request, "quiz_form.html", {
            "courses" : courses,
            "course_offerings" : course_offerings,
            "question_types" : Question.QUESTION_TYPES,
            "questions" : request.session.pop("questions", []),
            **request.session.pop("quiz", {})
        })
    elif request.method == "POST":
        questions, quiz = unpack_quiz_form(request.POST)

        try:
            # Course
            course = get_object_or_404(Course, name=quiz.get("course", ""))
            opening_date = get_datetime(quiz.get("opening_date", ""))
            closing_date = get_datetime(quiz.get("closing_date", ""))
            
            offering_id = request.POST.get("course_offering")
            if offering_id:
                course_offering = get_object_or_404(CourseOffering, pk=offering_id)
            else:
                course_offering = CourseOffering.objects.filter(course=course, academic_year__is_current=True).first()
            quiz = Quiz(name=quiz.get("quiz_name", ""), course=course, course_offering=course_offering, total_grade=quiz.get("total_grade", 0), opening_date=opening_date, closing_date=closing_date)
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

                logger.info(f"Quiz {quiz.name} is added in course {course.name} with {len(questions_obj)} questions")
                success(request, _("Quiz is created successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Create quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error(f"Course {questions.get('course', '')} is not found to create a quiz!")
        
        except Exception as e:
            error(request, _("Create quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate

            logger.error(f"Create Quiz has failed : {e}")


        return redirect(reverse("quiz-create"))

@capability_required(can_manage_content)
def update_quiz(request, quiz_id):
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]
        course_offerings = CourseOffering.objects.filter(academic_year__is_current=True).select_related('course', 'academic_year')
        try:
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            return render(request, "quiz_form.html", {
                "courses" : courses,
                "course_offerings" : course_offerings,
                "selected_offering_id" : quiz.course_offering_id,
                "question_types" : Question.QUESTION_TYPES,
                "questions" : [question.serialize() for question in quiz.questions.all()],
                **quiz.serialize()
            })
        except Http404:
            logger.error(f"Quiz with id : {quiz_id} is not found!")
            return redirect(reverse("quiz-dashboard"))

    elif request.method == "POST":
        questions, quiz_data = unpack_quiz_form(request.POST)
        try:
            # Course
            course = get_object_or_404(Course, name=quiz_data['course'])

            # Update Quiz
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            if not quiz.can_edit:
                return HttpResponse(_("Published or archived quiz cannot be edited."), status=403)
            quiz.name = quiz_data['quiz_name']
            quiz.opening_date = get_datetime(quiz_data['opening_date'])
            quiz.closing_date = get_datetime(quiz_data['closing_date'])
            quiz.total_grade = quiz_data['total_grade']
            quiz.course = course

            # Create Questions
            questions_obj = []
            questions_exists = []
            questions_id = []

            for question in questions.values():
                question_instance = build_question_instance(question, quiz)

                question_id = str(question.get("id", "")).strip()
                if question_id:
                    question_instance.pk = int(question_id)
                    questions_id.append(int(question_id))
                    questions_exists.append(question_instance)
                else:
                    questions_obj.append(question_instance)
            
            with transaction.atomic():
                # Delete Removed Questions
                Question.objects.filter(quiz=quiz).exclude(pk__in=questions_id).delete()

                # Update Current Questions
                Question.objects.bulk_update(questions_exists, ["title", "correct_answer", "question_type", "choices", "config", "grade", "auto_grade"])

                # Current New Questions
                Question.objects.bulk_create(questions_obj)
                quiz.save()

            logger.info(f"Quiz {quiz.name} is updated successfully in course {course.name} with new {len(questions_obj)} questions and existing {len(questions_exists)} questions")
            success(request, _("Quiz is updated successfully"), extra_tags="alert-success") # Translate

        except Http404:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error(f"Course {quiz_data['course']} or Quiz with id {quiz_id} is not found ")

        except Exception as e:
            error(request, _("Update quiz is failed, Try again Please"), extra_tags="alert-danger") # Translate
            logger.error(f"Quiz update has failed : {e}")
        
        return redirect(reverse("quiz-update", args=[quiz_id]))

class DeleteQuiz(QuizBaseView, DeleteView):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
        if not can_delete_content(request.user):
            return HttpResponse(_("Unauthorized"), status=401)
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


@capability_required(can_view_reports)
def export_yearly_transcript_csv(request):
    year = request.GET.get("year", now().year)
    name = request.GET.get("name")
    course = request.GET.get("course")
    role = request.GET.get("role") or None
    return export_yearly_transcript_to_csv(year, name=name, course=course, role=role)


@capability_required(can_view_reports)
def yearly_transcript_dashboard(request):
    year = request.GET.get("year", now().year)
    name = request.GET.get("name", "").strip()
    course = request.GET.get("course", "").strip()
    role = request.GET.get("role", "").strip()

    transcript = build_yearly_transcript_rows(year, name=name or None, course=course or None, role=role or None)

    context = {
        "title": _("Yearly Transcript"),
        "year_value": transcript["year"],
        "name_value": name,
        "course_value": course,
        "selected_role": role,
        "role_options": Role.ROLES,
        "transcript_rows": transcript["rows"],
        "student_count": len({row["user_id"] for row in transcript["rows"]}),
        "result_count": len(transcript["rows"]),
    }

    return render(request, "yearly_transcript_dashboard.html", context)

@capability_required(can_grade)
def submission_dashboard(request, quiz_id):
    try:
        quiz = get_object_or_404(Quiz, pk=quiz_id)
    except:
        logger.error(f"Quiz : {quiz_id} is not found to get submissions!")
        return quiz

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
        query &= Q(user__first_name__icontains=name) | Q(user__last_name__icontains=name)
    
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
        "filters" : ["submission_filter.html"],
        "submission_user" : True,
        "templates" : [""]
    }

    return render_dashboard(request, grades, view, context, parameters=[quiz_id, ])

@capability_required(can_grade)
def submission_user(request, quiz_id, user_id):
    submissions = Submission.objects.filter(question__quiz_id=quiz_id, user_id=user_id)
    grade = Grade.objects.filter(quiz_id=quiz_id, user_id=user_id).first()
    try:
        quiz = get_object_or_404(Quiz, pk=quiz_id)
    except Exception as e:
        logger.error(f"Quiz : {quiz_id} is not found! there is not submission page!")
        return quiz

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
def generate_audio_download(request, lesson_id):
    try:
        # Fetch the lesson object
        lesson = get_object_or_404(Lesson, pk=lesson_id)

        # Extract the m3u8 file URL from the lesson links
        m3u8_url = lesson.links

        # Fetch the m3u8 file content
        response = requests.get(m3u8_url)
        if response.status_code != 200:
            return JsonResponse({"error": "Failed to fetch m3u8 file."}, status=500)

        # Parse the m3u8 file to extract .ts file URLs
        ts_files = []
        base_url = os.path.dirname(m3u8_url)
        for line in response.text.splitlines():
            if line.endswith(".ts"):
                ts_files.append(os.path.join(base_url, line))

        # Return the .ts file URLs to the client for concatenation
        return JsonResponse({"ts_files": ts_files})

    except Exception as e:
        logger.error(f"Error generating audio download: {str(e)}")
        return JsonResponse({"error": "An error occurred while processing the request."}, status=500)

# Bulk Operations
@capability_required(can_delete_content)
def bulk_delete_users(request):
    """Bulk delete users endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    try:
        import json
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
        import json
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
        import json
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
        import json
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
        import json
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
        import json
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
        import json
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
        import json
        data = json.loads(request.body)
        old_key = data.get('old_key')
        new_key = data.get('new_key')
        is_folder = bool(data.get('is_folder', False))
        
        if not old_key or not new_key:
            return JsonResponse({'error': _('Both old and new keys required')}, status=400)
        
        if is_folder:
            success = R2_MANAGER.rename_folder(old_key, new_key)
            if success:
                logger.info(f"User {request.user} renamed folder from {old_key} to {new_key}.")
                return JsonResponse({'success': True, 'message': _('Folder renamed successfully'), 'new_key': new_key})
            else:
                return JsonResponse({'error': _('Failed to rename folder')}, status=500)

        success = R2_MANAGER.rename_file(old_key, new_key)
        
        if success:
            # Update database references in Lesson model
            # Lesson.links is a JSON string containing file IDs (which are the R2 keys)
            # Find lessons that might contain this specific file key
            # Since it's JSON, we look for the key inside the text
            lessons_to_update = Lesson.objects.filter(links__contains=old_key)
            updated_count = 0
            
            for lesson in lessons_to_update:
                try:
                    links = json.loads(lesson.links)
                    modified = False
                    for item in links:
                        if item.get('file_id') == old_key:
                            item['file_id'] = new_key
                            modified = True
                        # Also check in segments for HLS
                        if 'segments' in item and old_key in item['segments']:
                            item['segments'] = [s.replace(old_key, new_key) if s == old_key else s for s in item['segments']]
                            modified = True
                    
                    if modified:
                        lesson.links = json.dumps(links)
                        lesson.save()
                        updated_count += 1
                except Exception as db_err:
                    logger.error(f"Failed to update Lesson {lesson.id} during rename: {str(db_err)}")

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
        import json
        data = json.loads(request.body)
        file_key = data.get('file_key')
        destination_folder = data.get('destination_folder')
        
        if not file_key or not destination_folder:
            return JsonResponse({'error': _('File key and destination folder required')}, status=400)
        
        new_key = R2_MANAGER.move_file(file_key, destination_folder)
        
        if new_key:
            logger.info(f"User {request.user} moved file from {file_key} to {new_key}")
            return JsonResponse({'success': True, 'message': _('File moved successfully'), 'new_key': new_key})
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
        import json
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
        import json
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
    
    if not query:
        return JsonResponse({'error': _('Search query required')}, status=400)
    
    try:
        ext_list = [e.strip() for e in extensions.split(',') if e.strip()] if extensions else None
        
        results = R2_MANAGER.search_files(query, prefix, ext_list)
        
        # Format sizes and dates
        for file_data in results:
            file_data['size_formatted'] = R2_MANAGER.format_file_size(file_data['size'])
            if 'last_modified' in file_data and file_data['last_modified']:
                file_data['last_modified'] = file_data['last_modified'].isoformat()
        
        return JsonResponse({
            'success': True,
            'results': results,
            'count': len(results)
        })
    
    except Exception as e:
        logger.error(f"Error searching files: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url=LOGIN_URL)
def api_quiz_status(request, course_id):
    """
    API endpoint – returns quiz mode/status for every quiz in a course
    for the requesting user. Used by the course-detail sidebar to show
    status badges without reloading the page.

    Response: { "quizzes": [ { "id": int, "status": "exam"|"view"|"closed_unsolved" }, ... ] }
    """
    try:
        user = User.objects.get(username=request.user)
        course = get_object_or_404(Course, pk=course_id)

        if not user_can_access_course(user, course):
            return JsonResponse({"error": "Unauthorized"}, status=401)

        current_time = now()
        quizzes = course.quizzes.all()
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
        contents, parent_folder = list_current_folder(folder_name, filter_config)
        
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
            'current_folder': folder_name
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
    
    # Get filter configuration
    filter_config = get_filter_preset(filter_preset)
    
    # List files with filter
    if folder_id and folder_id != "None":
        contents, parent_folder = list_current_folder(CLOUD_CLIENT, bucket_name, folder_id, filter_config)
    else:
        contents, parent_folder = list_current_folder(CLOUD_CLIENT, bucket_name, "", filter_config)
    
    # Apply search filter on the fetched contents
    if search_query:
        contents = [item for item in contents if search_query.lower() in item['name'].lower()]
    
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
    }
    
    # Return partial ONLY when specifically targeting file list container
    # This prevents returning partial when navigating from menu
    hx_target = request.headers.get('HX-Target')
    if hx_target in ['drive-files', 'file-list-container']:
        return render(request, 'partials/r2_file_list.html', context)
    
    return render(request, 'r2_management.html', context)


# ---------------------------------------------------------------------------
# Design Theme Showcase
# ---------------------------------------------------------------------------
# Read-only gallery of the main user-side pages and the admin dashboard,
# re-skinned for every theme in the catalog. Useful for reviewing the
# refactor direction before any production template changes are merged.

_SHOWCASE_PAGE_IDS = {p["id"] for p in SHOWCASE_PAGES}


def _showcase_context(request, theme_id, page_id):
    theme = get_theme(theme_id)
    if page_id not in _SHOWCASE_PAGE_IDS:
        page_id = "home"
    return {
        "theme": theme,
        "theme_id": theme_id,
        "page_id": page_id,
        "themes": THEMES,
        "showcase_pages": SHOWCASE_PAGES,
        "active_page_id": page_id,
    }


def theme_catalog(request):
    guard = _staff_guard(request)
    if guard: return guard
    return render(request, "theme_showcase/landing.html", {
        "theme": get_theme("sapphire"),
        "themes": THEMES,
        "showcase_pages": SHOWCASE_PAGES,
        "active_page_id": "catalog",
        "page_id": "catalog",
    })


def theme_showcase(request, theme_id, page_id):
    guard = _staff_guard(request)
    if guard: return guard
    return render(
        request,
        f"theme_showcase/page_{page_id}.html",
        _showcase_context(request, theme_id, page_id),
    )


def _staff_guard(request):
    if not request.user.is_authenticated:
        return redirect(f"{LOGIN_URL}?next={request.get_full_path()}")
    if not request.user.is_staff:
        return HttpResponseForbidden(
            "Theme Showcase is restricted to staff users. "
            "Sign in with a staff account (is_staff=True) to view it."
        )
    return None


# ============================================================================
# PHASE 3 — Student Applications
# ============================================================================

def signup(request):
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
                    for file_type in ["identity_front", "identity_back", "payment", "profile"]:
                        if file_type in request.FILES:
                            key = upload_application_file(CLOUD_CLIENT, bucket_name, user.id, request.FILES[file_type], file_type)
                            if key:
                                setattr(user, f"{file_type}_key", key)
                                uploaded_keys.append(key)
                    if uploaded_keys:
                        user.save(update_fields=[f"{t}_key" for t in ["identity_front", "identity_back", "payment", "profile"] if getattr(user, f"{t}_key", None)] + ["study_mode"])
                    elif user.study_mode:
                        user.save(update_fields=["study_mode"])
                    send_application_received(user)
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
    return render(request, "signup.html", {"form": form})


@capability_required(can_manage_applications)
def applications_dashboard(request):
    status_filter = request.GET.get("status", "pending")
    users = User.objects.filter(application_status=status_filter).select_related("role")
    return render(request, "applications_dashboard.html", {
        "users": users,
        "current_status": status_filter,
    })


@capability_required(can_manage_applications)
def application_review(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    return render(request, "application_review.html", {"app_user": user})


@capability_required(can_manage_applications)
def application_decision(request, user_id, decision):
    if decision not in ("activate", "decline"):
        return HttpResponse(_("Invalid decision"), status=400)
    level = request.POST.get("level", 1)
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    user = get_object_or_404(User, pk=user_id)
    with transaction.atomic():
        if decision == "activate":
            user.application_status = "active"
            user.is_active = True
            student_role = Role.objects.get(role="student")
            user.role = student_role
            user.decided_by = request.user
            user.decided_at = now()
            user.save()
            current_year = AcademicYear.objects.filter(is_current=True, level=level).first()
            if current_year:
                Enrollment.objects.get_or_create(
                    student=user,
                    academic_year=current_year,
                    defaults={"enrolled_by": request.user}
                )
            send_application_activated(user)
            messages.success(request, _("%(name)s activated.") % {"name": user.get_full_name() or user.username})
        else:
            user.application_status = "declined"
            user.is_active = False
            user.decided_by = request.user
            user.decided_at = now()
            user.save()
            send_application_declined(user)
            messages.success(request, _("%(name)s declined.") % {"name": user.get_full_name() or user.username})
    return redirect("applications-dashboard")


@capability_required(can_manage_applications)
def bulk_application_decision(request):
    if request.method != "POST":
        return JsonResponse({"error": _("Method not allowed")}, status=405)
    data = json.loads(request.body)
    decision = data.get("decision")
    user_ids = data.get("user_ids", [])
    level = int(data.get("level", 1))
    if decision not in ("activate", "decline"):
        return JsonResponse({"error": _("Invalid decision")}, status=400)
    results = {"success": [], "errors": []}
    for uid in user_ids:
        try:
            user = User.objects.get(pk=uid)
            with transaction.atomic():
                if decision == "activate":
                    user.application_status = "active"
                    user.is_active = True
                    user.role = Role.objects.get(role="student")
                    user.decided_by = request.user
                    user.decided_at = now()
                    user.save()
                    current_year = AcademicYear.objects.filter(is_current=True, level=level).first()
                    if current_year:
                        Enrollment.objects.get_or_create(student=user, academic_year=current_year, defaults={"enrolled_by": request.user})
                    send_application_activated(user)
                else:
                    user.application_status = "declined"
                    user.is_active = False
                    user.decided_by = request.user
                    user.decided_at = now()
                    user.save()
                    send_application_declined(user)
                results["success"].append(uid)
        except Exception as e:
            results["errors"].append({"id": uid, "error": str(e)})
    return JsonResponse(results)


@capability_required(can_manage_content)
def duplicate_lesson(request, lesson_id):
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    offering_id = request.POST.get("course_offering_id")
    target_offering = get_object_or_404(CourseOffering, pk=offering_id) if offering_id else lesson.course_offering
    with transaction.atomic():
        lesson.pk = None
        lesson.course_offering = target_offering
        lesson.course = target_offering.course
        lesson.status = PublicationStatus.DRAFT
        lesson.created_date = date.today()
        lesson.save()
    messages.success(request, _("Lesson duplicated."))
    return redirect("lesson-dashboard")


@capability_required(can_manage_content)
def duplicate_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, pk=quiz_id)
    offering_id = request.POST.get("course_offering_id")
    target_offering = get_object_or_404(CourseOffering, pk=offering_id) if offering_id else quiz.course_offering
    with transaction.atomic():
        questions = list(quiz.questions.all())
        quiz.pk = None
        quiz.course_offering = target_offering
        quiz.course = target_offering.course
        quiz.status = PublicationStatus.DRAFT
        quiz.save()
        for q in questions:
            q.pk = None
            q.quiz = quiz
        Question.objects.bulk_create(questions)
    messages.success(request, _("Quiz duplicated."))
    return redirect("quiz-dashboard")


@capability_required(can_manage_content)
def copy_course_offering(request, offering_id):
    offering = get_object_or_404(CourseOffering, pk=offering_id)
    target_year_id = request.POST.get("academic_year_id")
    target_year = get_object_or_404(AcademicYear, pk=target_year_id) if target_year_id else offering.academic_year
    with transaction.atomic():
        new_offering = CourseOffering.objects.create(
            course=offering.course,
            academic_year=target_year,
            instructor=offering.instructor,
            status="draft",
        )
        for lesson in offering.lessons.all():
            lesson.pk = None
            lesson.course_offering = new_offering
            lesson.status = PublicationStatus.DRAFT
            lesson.save()
        for quiz in offering.quizzes.all():
            questions = list(quiz.questions.all())
            quiz.pk = None
            quiz.course_offering = new_offering
            quiz.status = PublicationStatus.DRAFT
            quiz.save()
            for q in questions:
                q.pk = None
                q.quiz = quiz
            Question.objects.bulk_create(questions)
    messages.success(request, _("Course offering copied."))
    return redirect("course-dashboard")


@capability_required(can_manage_content)
def duplicate_course(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    new_name = request.POST.get("new_name", "")
    if not new_name:
        return HttpResponse(_("New course name is required."), status=400)
    target_year_id = request.POST.get("academic_year_id")
    target_year = get_object_or_404(AcademicYear, pk=target_year_id) if target_year_id else None
    with transaction.atomic():
        new_course = Course.objects.create(name=new_name, description=course.description, instructor=course.instructor, level=course.level)
        if target_year:
            new_offering = CourseOffering.objects.create(course=new_course, academic_year=target_year, status="draft")
            for lesson in Lesson.objects.filter(course=course):
                lesson.pk = None
                lesson.course = new_course
                lesson.course_offering = new_offering
                lesson.status = PublicationStatus.DRAFT
                lesson.save()
            for quiz in Quiz.objects.filter(course=course):
                questions = list(quiz.questions.all())
                quiz.pk = None
                quiz.course = new_course
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


@capability_required(can_manage_content)
def calendar_management(request):
    years = AcademicYear.objects.filter(is_current=True).prefetch_related("holidays")
    return render(request, "calendar_management.html", {"years": years})


@capability_required(can_manage_content)
def add_holiday(request):
    if request.method == "POST":
        year_id = request.POST.get("academic_year_id")
        date_val = request.POST.get("date")
        name = request.POST.get("name")
        year = get_object_or_404(AcademicYear, pk=year_id)
        AcademicHoliday.objects.create(academic_year=year, date=date_val, name=name)
        messages.success(request, _("Holiday added."))
        return redirect("calendar-management")
    years = AcademicYear.objects.filter(is_current=True).prefetch_related("holidays")
    return render(request, "calendar_management.html", {"years": years})


@capability_required(can_correct_attendance)
def delete_holiday(request, holiday_id):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id)
    holiday.delete()
    messages.success(request, _("Holiday removed."))
    return redirect("calendar-management")


@login_required
def student_calendar(request):
    enrollments = Enrollment.objects.filter(student=request.user).select_related("academic_year")
    years = [e.academic_year for e in enrollments]
    return render(request, "student_calendar.html", {"years": years})


@login_required
def download_qr(request):
    if not request.user.qr_token:
        request.user.qr_token = secrets.token_urlsafe(32)
        request.user.save(update_fields=["qr_token"])
    qr_data = request.build_absolute_uri(reverse("scan-preview", args=[request.user.qr_token]))
    import qrcode
    from io import BytesIO
    img = qrcode.make(qr_data)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return HttpResponse(buf, content_type="image/png")


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
def scan_preview(request, token):
    user = get_object_or_404(User, qr_token=token)
    academic_year = AcademicYear.objects.filter(
        level__in=Enrollment.objects.filter(student=user).values("academic_year__level"),
        is_current=True,
    ).first()
    today = now().date()
    already_recorded = AttendanceRecord.objects.filter(
        student=user, attendance_date=today
    ).values_list("action", flat=True)
    return render(request, "scan_preview.html", {
        "student": user,
        "academic_year": academic_year,
        "today": today,
        "already_recorded": list(already_recorded),
    })


@require_POST
@capability_required(can_scan_attendance)
def record_attendance(request, token, action):
    if action not in ("entrance", "exit"):
        return JsonResponse({"error": _("Invalid action.")}, status=400)
    user = get_object_or_404(User, qr_token=token)
    if user.study_mode == "online":
        return JsonResponse({"error": _("Online students cannot record attendance.")}, status=400)
    academic_year = AcademicYear.objects.filter(
        level__in=Enrollment.objects.filter(student=user).values("academic_year__level"),
        is_current=True,
    ).first()
    if not academic_year:
        return JsonResponse({"error": _("No active enrollment.")}, status=400)
    today = now().date()
    if not is_expected_date(academic_year, today):
        return JsonResponse({"error": _("Today is not an expected attendance day.")}, status=400)
    rec, created = AttendanceRecord.objects.get_or_create(
        student=user,
        academic_year=academic_year,
        attendance_date=today,
        action=action,
        defaults={"scanned_by": request.user},
    )
    if created:
        return JsonResponse({"status": "recorded", "action": action})
    return JsonResponse({"status": "already_recorded", "action": action})


@capability_required(can_correct_attendance)
def attendance_management(request):
    year_id = request.GET.get("academic_year")
    records = AttendanceRecord.objects.all().select_related("student", "academic_year", "scanned_by")
    if year_id:
        records = records.filter(academic_year_id=year_id)
    years = AcademicYear.objects.all()
    return render(request, "attendance_management.html", {
        "records": records,
        "years": years,
        "selected_year": int(year_id) if year_id else None,
    })


@capability_required(can_correct_attendance)
def attendance_correction(request, record_id):
    record = get_object_or_404(AttendanceRecord, pk=record_id)
    if request.method == "POST":
        new_date = request.POST.get("attendance_date")
        new_action = request.POST.get("action")
        record.attendance_date = new_date or record.attendance_date
        record.action = new_action or record.action
        record.corrected_by = request.user
        record.corrected_at = now()
        record.save()
        messages.success(request, _("Attendance record corrected."))
        return redirect("attendance-management")
    return render(request, "attendance_correction.html", {"record": record})


@capability_required(can_correct_attendance)
def delete_attendance(request, record_id):
    record = get_object_or_404(AttendanceRecord, pk=record_id)
    record.delete()
    messages.success(request, _("Attendance record deleted."))
    return redirect("attendance-management")


# ============================================================================
# PHASE 6 — Online Lecture Progress Tracking
# ============================================================================


def create_viewing_session(student, lesson, part_id):
    session_id = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(hours=2)
    session = ViewingSession.objects.create(
        student=student,
        lesson=lesson,
        part_id=part_id,
        session_id=session_id,
        expires_at=expires_at,
    )
    return session


def sign_session(session_id, expires_at):
    message = f"{session_id}:{int(expires_at.timestamp())}"
    secret = settings.SECRET_KEY.encode()
    signature = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    return f"{message}:{signature}"


@capability_required(can_manage_content)
def start_viewing_session(request, lesson_id, part_id):
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    session = create_viewing_session(request.user, lesson, part_id)
    token = sign_session(session.session_id, session.expires_at)
    return JsonResponse({
        "session_id": session.session_id,
        "token": token,
        "expires_at": session.expires_at.isoformat(),
    })


@login_required
def lesson_manifest(request, lesson_id, file_index):
    lesson = get_object_or_404(Lesson, pk=lesson_id)
    links = json.loads(lesson.links)
    if file_index >= len(links):
        return HttpResponse(status=404)
    file_info = links[file_index]
    key = file_info["id"]
    try:
        response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=key)
        playlist = response["Body"].read().decode()
    except Exception:
        return HttpResponse(status=404)
    return HttpResponse(playlist, content_type="application/vnd.apple.mpegurl")


@csrf_exempt
@require_POST
def worker_receipt(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = data.get("session_id")
    segment_key = data.get("segment_key")
    signature = data.get("signature")

    if not all([session_id, segment_key, signature]):
        return JsonResponse({"error": "Missing fields"}, status=400)

    session = get_object_or_404(ViewingSession, session_id=session_id)
    expected = sign_session(session.session_id, session.expires_at).split(":")[-1]
    if not hmac.compare_digest(signature, expected):
        return JsonResponse({"error": "Invalid signature"}, status=403)

    if timezone.now() > session.expires_at:
        return JsonResponse({"error": "Session expired"}, status=410)

    VerifiedSegmentRequest.objects.get_or_create(session=session, segment_key=segment_key)
    return JsonResponse({"status": "recorded"})


@csrf_exempt
@require_POST
def progress_heartbeat(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    data = json.loads(request.body)
    session_id = data.get("session_id")
    ranges = data.get("ranges", [])

    session = get_object_or_404(ViewingSession, session_id=session_id, student=request.user)

    session.last_heartbeat = timezone.now()
    session.save(update_fields=["last_heartbeat"])

    return JsonResponse({"status": "ok"})


@capability_required(can_view_reports)
def progress_dashboard(request):
    lesson_id = request.GET.get("lesson")
    lessons = Lesson.objects.all()
    progress = LectureProgress.objects.all().select_related("student", "lesson")
    if lesson_id:
        progress = progress.filter(lesson_id=lesson_id)
    return render(request, "progress_dashboard.html", {
        "progress": progress,
        "lessons": lessons,
        "selected_lesson": int(lesson_id) if lesson_id else None,
    })


@capability_required(can_view_reports)
def report_dashboard(request):
    academic_year_id = request.GET.get("academic_year")
    student_id = request.GET.get("student") or None
    study_mode = request.GET.get("study_mode") or None
    course_offering_id = request.GET.get("course_offering") or None

    levels = AcademicYear.objects.values_list("level", flat=True).distinct().order_by("level")
    academic_years = AcademicYear.objects.all().order_by("-level", "-name")
    selected_year = None
    rows = []

    if academic_year_id:
        selected_year = get_object_or_404(AcademicYear, pk=academic_year_id)
        students = User.objects.filter(enrollments__academic_year=selected_year).distinct()
        course_offerings = CourseOffering.objects.filter(academic_year=selected_year)
        rows = build_report_data(
            selected_year,
            student_id=int(student_id) if student_id else None,
            study_mode=study_mode,
            course_offering_id=int(course_offering_id) if course_offering_id else None,
        )

    context = {
        "title": _("Combined Report"),
        "levels": levels,
        "academic_years": academic_years,
        "selected_year": selected_year,
        "selected_level": request.GET.get("level", ""),
        "selected_student": student_id,
        "selected_study_mode": study_mode or "",
        "selected_course_offering": course_offering_id or "",
        "students": students if academic_year_id else [],
        "course_offerings": course_offerings if academic_year_id else [],
        "rows": rows,
        "row_count": len(rows),
    }
    return render(request, "report_dashboard.html", context)


@capability_required(can_view_reports)
def export_report_csv(request):
    academic_year_id = request.GET.get("academic_year")
    if not academic_year_id:
        return HttpResponse("Missing academic_year", status=400)
    year = get_object_or_404(AcademicYear, pk=academic_year_id)

    rows = build_report_data(
        year,
        student_id=request.GET.get("student") or None,
        study_mode=request.GET.get("study_mode") or None,
        course_offering_id=request.GET.get("course_offering") or None,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Identity", "Username", "First Name", "Last Name", "Study Mode",
                      "Level", "Academic Year",
                      "Grade Earned", "Grade Available", "Grade %",
                      "Expected", "Valid", "Invalid", "Absent",
                      "Attendance %", "Absence %"])
    for row in rows:
        writer.writerow([
            row["student_id"],
            _csv_safe_cell(row["username"]),
            _csv_safe_cell(row["first_name"]),
            _csv_safe_cell(row["last_name"]),
            _csv_safe_cell(row["study_mode"]),
            row["level"],
            _csv_safe_cell(row["year_name"]),
            row["grade_earned"],
            row["grade_available"],
            row["grade_percent"],
            row["expected"],
            row["valid"],
            row["invalid"],
            row["absent"],
            row["attendance_rate"],
            row["absence_rate"],
        ])

    output.seek(0)
    safe_name = _safe_filename(year.name)
    response = HttpResponse(output.read(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="report_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    return response


@capability_required(can_view_reports)
def export_report_xlsx(request):
    academic_year_id = request.GET.get("academic_year")
    if not academic_year_id:
        return HttpResponse("Missing academic_year", status=400)
    year = get_object_or_404(AcademicYear, pk=academic_year_id)

    rows = build_report_data(
        year,
        student_id=request.GET.get("student") or None,
        study_mode=request.GET.get("study_mode") or None,
        course_offering_id=request.GET.get("course_offering") or None,
    )

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "Grades Summary"
    headers = ["Student ID", "Username", "First Name", "Last Name", "Course", "Quiz",
               "Grade", "Total", "Percent"]
    ws1.append(headers)
    for row in rows:
        for detail in row["grade_details"]:
            pct = round(detail["grade"] / detail["total"] * 100, 1) if detail["total"] else 0
            ws1.append([
                row["student_id"], row["username"], row["first_name"], row["last_name"],
                detail["course_name"], detail["quiz_name"],
                detail["grade"], detail["total"], pct,
            ])

    ws1.freeze_panes = "A2"
    ws1.auto_filter.ref = ws1.dimensions

    ws2 = wb.create_sheet("Attendance Summary")
    ws2.append(["Student ID", "Username", "First Name", "Last Name",
                "Expected", "Valid", "Invalid", "Absent",
                "Attendance %", "Absence %"])
    for row in rows:
        ws2.append([
            row["student_id"], row["username"], row["first_name"], row["last_name"],
            row["expected"], row["valid"], row["invalid"], row["absent"],
            row["attendance_rate"], row["absence_rate"],
        ])

    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions

    ws3 = wb.create_sheet("Attendance Daily")
    ws3.append(["Student ID", "Username", "First Name", "Last Name",
                "Date", "Day", "Action"])
    for row in rows:
        records = AttendanceRecord.objects.filter(
            student_id=row["student_id"], academic_year=year
        ).order_by("attendance_date")
        for rec in records:
            ws3.append([
                row["student_id"], row["username"], row["first_name"], row["last_name"],
                rec.attendance_date.isoformat(),
                rec.attendance_date.strftime("%A"),
                rec.action,
            ])
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = ws3.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    safe_name = _safe_filename(year.name)
    response = HttpResponse(buf.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="report_{safe_name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    return response


def robots_txt(request):
    return HttpResponse(
        "User-agent: *\n"
        "Disallow: /dashboard/\n"
        "Disallow: /portal/\n"
        "Disallow: /scanner/\n"
        "Disallow: /api/\n"
        "Sitemap: https://bibleinstitute-eg.org/sitemap.xml\n",
        content_type="text/plain",
    )


def sitemap_xml(request):
    urls = [
        (reverse("home"), "weekly", "1.0"),
        (reverse("about"), "monthly", "0.8"),
        (reverse("courses"), "daily", "0.9"),
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
