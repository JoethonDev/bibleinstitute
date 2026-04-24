# Django Core Imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, FileResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, FormView, DetailView
from django.utils.translation import gettext as _
from django.utils import translation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views, update_session_auth_hash
from django.db.models import Q, Sum
from django.db import transaction
from django.contrib.messages import success, error, info
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.timezone import now

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
from .forms import CSVUploadForm, CourseForm, UserCreationForm, UserUpdateForm, ProfileUpdateForm

# Internal Imports - Utilities
from .utils.csv_export import export_users_to_csv, export_quiz_with_submissions_to_csv, export_single_submission_to_csv
from .utils.r2_filters import R2FileFilter, FileFilterConfig, get_filter_preset, FILTER_PRESETS
from .utils.r2_manager import R2Manager
from .utils.file_validator import validate_upload_filename, FileValidator
from .utils.storage_operations import list_current_folder, download_from_bucket, generate_unique_url
from .utils.helpers import get_datetime, paginate_obj, render_dashboard, unpack_quiz_form, safe_get_user
from .utils.decorators import management_required

# Constants
LOGIN_URL = reverse_lazy("user_login")
logger = getLogger(__name__)
# DRIVE_CLIENT = get_drive_client()
CLOUD_CLIENT = boto3.client(
    's3',
    endpoint_url=os.getenv("endpoint") or "https://da59dca47179969defd66c61b710bbdb.r2.cloudflarestorage.com",
    aws_access_key_id=os.getenv("key_id") or "34aab6f5a4a4e832bf2619260e0dbaea",
    aws_secret_access_key=os.getenv("access_key") or "ac0690c0799ff35373e92d8ee6c1d6a986798e6578de992fe39965ea90df038e",
    region_name='auto'
)

bucket_name = os.getenv("bucket") or "lecture-storage"

# Initialize R2 Manager
R2_MANAGER = R2Manager(CLOUD_CLIENT, bucket_name)

# Helper functions moved to utils/storage_operations.py and utils/helpers.py
# Google Drive legacy code moved to utils/google_drive_manager.py

# Permission checking helper functions (backward compatibility)
def is_managerial(request):
    """Check if user has management permissions (admin or teacher)"""
    from .utils.decorators import check_role_permission
    user = safe_get_user(request)
    if not user or not check_role_permission(user, 'management'):
        logger.warning(f"User: {request.user} is trying to access admin panel")
        return HttpResponse(_("Unauthorized"), status=401)
    return HttpResponse(_("authorized"), status=200)


def has_admin_permission(request, view):
    """Check if user has admin permissions for a specific view"""
    from .utils.decorators import check_role_permission
    user = safe_get_user(request)
    if not user or not check_role_permission(user, 'management'):
        logger.warning(f"User: {request.user} is trying to access {view}")
        return HttpResponse(_("Unauthorized"), status=401)
    return HttpResponse(_("authorized"), status=200)

# Class Base
class LoginProtection(object):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        return redirect(f"{reverse('user_login')}?next={request.path}")

class AdminPermissionView(LoginProtection):
    def dispatch(self, request, *args, **kwargs):
        authenticated_response = super().dispatch(request, *args, **kwargs) #get response from LoginProtection

        if isinstance(authenticated_response, HttpResponse): #check if LoginProtection returned an HttpResponse, which means login failed.
            return authenticated_response
        user = User.objects.get(username=request.user)
        if user.role.role in MANAGEMENT_ROLES:
            return super().dispatch(request, *args, **kwargs)
        return HttpResponse(_("Unauthorized"), status=401)    # Translate "Unauthorized"

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


@login_required(login_url=LOGIN_URL)
def index(request):
    return render(request, "index.html")

# Detail Class [handles with and without pk routes]
class ProfileDetail(LoginProtection, DetailView):
    model = User
    pk_url_kwarg = "user_id"
    template_name = "profile.html"

    def get_context_data(self, **kwargs):
        context =  super().get_context_data(**kwargs)
        user = self.object
        form = ProfileUpdateForm(instance=user)
        for field in form.fields.values():
            field.disabled = True

        context['form'] = form
        context['courses'] = Course.fetch_courses_by_role(user.role.role)

        return context

    def get(self, request, *args, **kwargs):
        # Check request has user_id route
        # If user is not admin and pk in url
        if not request.get_full_path().endswith("/profile/") and request.user.role.role not in MANAGEMENT_ROLES:
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
    courses = Course.fetch_courses_by_role(user.role.role)
    logger.info(f"{username} access {len(courses)} academic years ")


    return render(request, "course_view.html", {
        "courses" : courses
    })


@login_required(login_url=LOGIN_URL)
def view_course_details(request, course_id):
    # Get Course
    try:
        course = get_object_or_404(Course, pk=course_id)
        user = User.objects.get(username=request.user)

        logger.info(f"User : {user} is accessing {course.name} course")

        context = {
            "lessons" : [],
            "quizzes" : course.fetch_quizzes(user)
        }
        if user.role.role in MANAGEMENT_ROLES:
            context['lessons'] = course.lessons.all()
            logger.info(f"User : {user} is accessing all lessons")

        else:
            if not course.can_access(user.role.role):
                return HttpResponse(_("Unauthorized"), status=401) # Translate "Unauthorized"
            # Set Range of lesson created date
            join_date = user.joined_date
            end_date = join_date.replace(year=join_date.year + course.level)
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
        if user.role.role in MANAGEMENT_ROLES or lesson.can_access(user.joined_date):
            logger.info(f"User : {user} is accessing {lesson.name} lesson from {course.name} course")
            return render(request, "lesson_stream.html", {
                # Add Courses here
                "links" : [{
                    "url" : reverse("lesson-stream", args=[lesson_id, file_index]),
                    "type" : file['file_type']
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
        lesson = get_object_or_404(Lesson, pk=lesson_id)
        logger.info(f"User : {username} is streaming video from {lesson.name} lesson")

        lesson_links = json.loads(lesson.links)
        # Refacor TODO
        file_data = [file_link for file_link in lesson_links][file_index]

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
        from .utils.helpers import is_quiz_in_user_window
        quiz = get_object_or_404(Quiz, pk=quiz_id)
        user = User.objects.get(username=request.user)

        submission_datetime = now()
        # Buffer of 30 minutes after closing time for late submissions
        can_submit = quiz.closing_date + timedelta(minutes=30) >= submission_datetime
        # Quiz is currently open for taking (no buffer - just real window)
        quiz_currently_open = quiz.opening_date <= submission_datetime <= quiz.closing_date + timedelta(minutes=30)

        # Check if user has previously taken this quiz
        grade = Grade.objects.filter(user=user, quiz_id=quiz_id).first()
        total_grade = grade.total_grade if grade else 0

        if request.method == "GET":
            course = get_object_or_404(Course, pk=course_id)
            logger.info(f"User : {user} is accessing {quiz.name} in {course.name} course")

            # Management roles can always access any quiz in any mode
            is_management = user.role.role in MANAGEMENT_ROLES
            if not is_management and not course.can_access(user.role.role):
                return HttpResponse(_("Unauthorized"), status=401)

            # Determine quiz mode using cohort-year window logic
            if is_management:
                # Admins/teachers always see in "view" mode for student quizzes
                quiz_mode = "view"
                query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user) if grade else Question.objects.filter(quiz_id=quiz_id)
            elif grade:
                # Student has already submitted – always show their submission
                quiz_mode = "view"
                query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user)
            elif quiz_currently_open and is_quiz_in_user_window(quiz, user):
                # Quiz is open right now AND falls within this student's academic window
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
                    question_id = data['id']
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
                    logger.error(f"{user}'s submission is added successfully to {quiz}")
                    logger.error(f"Stack Traceback: {e}")
            
            return redirect(reverse("quiz-details", args=[course_id, quiz_id]))
        
        else:
            return HttpResponse(_("Not allowed method"), 400) # Translate
        
    except Http404:
            logger.error(f"Course with id: {course_id} or Quiz with id: {quiz_id} not found for user: {user.username}")
            raise Http404
    
# Admin Views
@login_required(login_url=LOGIN_URL)
def admin_panel(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    logger.info(f"User : {request.user} accesses admin panel successfully")
    return render(request, "admin_panel.html")

@login_required(login_url=LOGIN_URL)
def export_users_csv(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    return export_users_to_csv()

@login_required(login_url=LOGIN_URL)
def user_dashboard(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
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

@login_required(login_url=LOGIN_URL)
def user_bulk_create(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager

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
        if self.object.pk == self.request.user.pk and "password" in form.cleaned_data and form.cleaned_data["password"]:
            update_session_auth_hash(self.request, self.object)

        return response

    def get_success_url(self):
        return reverse_lazy("user-update", args=[self.kwargs.get(self.pk_url_kwarg)])
    
class DeleteUser(UserBaseView, DeleteView):
    success_url = reverse_lazy("user-dashboard")

# Course Dashboard
@login_required(login_url=LOGIN_URL)
def course_dashboard(request):   
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
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
    success_url = reverse_lazy("course-dashboard")

# Lesson Dashboard
@login_required(login_url=LOGIN_URL)
def lesson_dashboard(request):   
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
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

@login_required(login_url=LOGIN_URL)
def create_lesson(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]

        return render(request, "lesson_form.html", {
            "courses" : courses,
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
                Lesson.objects.create(name=lesson_name, course=course, links=json.dumps(links))
                success(request, _("Lesson is created successfully"), extra_tags="alert-success") # Translate
                logger.info(f"Lesson {lesson_name} is added in course {course_name} with media length of {len(links)}")

            except Http404:
                error(request, _("Create lesson has failed, Try again Please!"), extra_tags="alert-danger") # Translate
                logger.error(f"Course {course_name} is not found to create a lesson!")
        else:
            error(request, _("Create lesson has failed, Name and Videos can not be empty!"), extra_tags="alert-danger") # Translate

        return redirect(reverse("lesson-create"))

@login_required(login_url=LOGIN_URL)
def navigate_folder(request, folder_id=None):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager

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

@login_required(login_url=LOGIN_URL)
def update_lesson(request, lesson_id):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]
        try:
            lesson = get_object_or_404(Lesson, pk=lesson_id)
            return render(request, "lesson_form.html", {
                "courses" : courses,
                "drive" : list_current_folder(CLOUD_CLIENT, bucket_name)[0],
                "is_root" : True,
                "selected_course" : lesson.course.name,
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
@login_required(login_url=LOGIN_URL)
def upload_file(request):
    is_manager = is_managerial(request)
    if not is_manager:
        return is_manager

    # request.META['Cross-Origin-Resource-Policy'] = 'same-origin'
    return render(request, "upload_video.html", {
        "drive" : list_current_folder(CLOUD_CLIENT, bucket_name, folders_only=True)[0],
        "is_root" : True
    })

@login_required(login_url=LOGIN_URL)
def upload_link(request):
    is_manager = is_managerial(request)
    if not is_manager:
        return JsonResponse({"message" : _("unauthorized!")}, status=401) # Translate "unauthorized!"
    
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
@login_required(login_url=LOGIN_URL)
def quiz_dashboard(request):   
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
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

@login_required(login_url=LOGIN_URL)
def create_quiz(request):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]

        return render(request, "quiz_form.html", {
            "courses" : courses,
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
            
            quiz = Quiz(name=quiz.get("quiz_name", ""), course=course, total_grade=quiz.get("total_grade", 0), opening_date=opening_date, closing_date=closing_date)
            # Create Questions
            raise_exception = False
            exceptions_messages = []
            questions_obj = []
            for question in questions.values():
                title = question.get("name", "")
                correct_answer = question.get("answer", "").strip()
                question_type = question.get("type", "")
                choices = question.get("choices", [])
                if correct_answer and correct_answer not in choices and question_type == "mcq":
                    error_message = _("Correct answer is not in choices for question : %(title)s") % {'title': title} # Translate
                    error(request, error_message, extra_tags="alert-danger")
                    exceptions_messages.append(error_message)
                    raise_exception = True

                choices = json.dumps([choice.strip() for choice in choices])
                grade = question.get("grade")
                auto_grade = False if not correct_answer or question_type == "written" else True

                questions_obj.append(Question(
                    title=title,
                    quiz=quiz,
                    correct_answer=correct_answer,
                    question_type=question_type,
                    choices=choices,
                    grade=grade,
                    auto_grade=auto_grade
                ))

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

@login_required(login_url=LOGIN_URL)
def update_quiz(request, quiz_id):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    if request.method == "GET":
        courses = [course.name for course in Course.objects.all()]
        try:
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            return render(request, "quiz_form.html", {
                "courses" : courses,
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
                title = question['name']
                correct_answer = question.get("answer", None)
                question_type = question['type']
                choices = question.get("choices", [])
                if choices:
                    if correct_answer not in choices:
                        raise Exception(_("Correct answer is not in choices")) # Translate
                    choices = json.dumps(choices)
                grade = question['grade']
                auto_grade = False if not correct_answer or question_type == "written" else True
                #print(f"Question title : {title} Type : {question_type} Auto grade : {auto_grade}")
                
                question_instance = Question(
                    title=title,
                    quiz=quiz,
                    correct_answer=correct_answer,
                    question_type=question_type,
                    choices=choices,
                    grade=grade,
                    auto_grade=auto_grade
                )

                if "id" in question:
                    question_instance.pk = question['id']
                    questions_id.append(question['id'])
                    questions_exists.append(question_instance)
                else:
                    questions_obj.append(question_instance)
            
            with transaction.atomic():
                # Delete Removed Questions
                Question.objects.filter(quiz=quiz).exclude(pk__in=questions_id).delete()

                # Update Current Questions
                Question.objects.bulk_update(questions_exists, ["title", "correct_answer", "question_type", "choices", "grade", "auto_grade"])

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
    success_url = reverse_lazy("lesson-dashboard")

# Submission Dashboard
@login_required(login_url=LOGIN_URL)
def export_quiz_submissions_csv(request, quiz_id):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    return export_quiz_with_submissions_to_csv(quiz_id)

@login_required(login_url=LOGIN_URL)
def export_submission_csv(request, grade_id):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
    return export_single_submission_to_csv(grade_id)

@login_required(login_url=LOGIN_URL)
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

@login_required(login_url=LOGIN_URL)
def submission_user(request, quiz_id, user_id):
    is_manager = is_managerial(request)
    if is_manager.status_code == 401:
        return is_manager
    
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
@login_required(login_url=LOGIN_URL)
def bulk_delete_users(request):
    """Bulk delete users endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    has_permission = has_admin_permission(request, 'users')
    if has_permission.status_code == 401:
        return has_permission
    
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

@login_required(login_url=LOGIN_URL)
def bulk_delete_courses(request):
    """Bulk delete courses endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    has_permission = has_admin_permission(request, 'courses')
    if has_permission.status_code == 401:
        return has_permission
    
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

@login_required(login_url=LOGIN_URL)
def bulk_delete_lessons(request):
    """Bulk delete lessons endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    has_permission = has_admin_permission(request, 'lessons')
    if has_permission.status_code == 401:
        return has_permission
    
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

@login_required(login_url=LOGIN_URL)
def bulk_delete_quizzes(request):
    """Bulk delete quizzes endpoint for HTMX"""
    if request.method != 'DELETE':
        return HttpResponse(_("Method not allowed"), status=405)
    
    has_permission = has_admin_permission(request, 'quizzes')
    if has_permission.status_code == 401:
        return has_permission
    
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

@login_required(login_url=LOGIN_URL)
def api_delete_file(request):
    """
    API endpoint to delete a single file from R2
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
    try:
        import json
        data = json.loads(request.body)
        file_key = data.get('file_key')
        
        if not file_key:
            return JsonResponse({'error': _('File key required')}, status=400)
        
        success = R2_MANAGER.delete_file(file_key)
        
        if success:
            logger.info(f"User {request.user} deleted file: {file_key}")
            return JsonResponse({'success': True, 'message': _('File deleted successfully')})
        else:
            return JsonResponse({'error': _('Failed to delete file')}, status=500)
    
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required(login_url=LOGIN_URL)
def api_delete_m3u8_file(request):
    """
    API endpoint to delete an m3u8 file and all its related .ts segment files
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_delete_files_batch(request):
    """
    API endpoint to delete multiple files in batch
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_rename_file(request):
    """
    API endpoint to rename a file
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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
            from django.db.models import F
            import json
            
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


@login_required(login_url=LOGIN_URL)
def api_move_file(request):
    """
    API endpoint to move a file to a different folder
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_create_folder(request):
    """
    API endpoint to create a new folder
    """
    if request.method != 'POST':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_delete_folder(request):
    """
    API endpoint to delete a folder
    """
    if request.method != 'DELETE':
        return JsonResponse({'error': _('Method not allowed')}, status=405)
    
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_get_file_metadata(request):
    """
    API endpoint to get file metadata
    """
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_get_storage_stats(request):
    """
    API endpoint to get storage statistics
    """
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_search_files(request):
    """
    API endpoint to search for files
    """
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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
    from .utils.helpers import is_quiz_in_user_window
    try:
        user = User.objects.get(username=request.user)
        course = get_object_or_404(Course, pk=course_id)

        if not course.can_access(user.role.role) and user.role.role not in MANAGEMENT_ROLES:
            return JsonResponse({"error": "Unauthorized"}, status=401)

        current_time = now()
        quizzes = course.quizzes.all()
        result = []

        for quiz in quizzes:
            grade = Grade.objects.filter(user=user, quiz_id=quiz.pk).first()
            quiz_open = quiz.opening_date <= current_time <= quiz.closing_date + timedelta(minutes=30)

            if grade:
                status = "view"
            elif quiz_open and is_quiz_in_user_window(quiz, user):
                status = "exam"
            else:
                status = "closed_unsolved"

            result.append({"id": quiz.pk, "status": status})

        return JsonResponse({"quizzes": result})

    except Exception as e:
        logger.error(f"api_quiz_status error: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required(login_url=LOGIN_URL)
def api_list_files(request):
    """
    API endpoint to list files with filtering
    """
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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


@login_required(login_url=LOGIN_URL)
def api_download_file(request):
    """
    API endpoint to generate download URL for a file
    """
    has_permission = has_admin_permission(request, 'files')
    if has_permission.status_code == 401:
        return JsonResponse({'error': _('Unauthorized')}, status=401)
    
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

@login_required(login_url=LOGIN_URL)
def r2_management_dashboard(request):
    """
    Main R2 management dashboard view
    """
    has_permission = has_admin_permission(request, 'r2-management')
    if has_permission.status_code == 401:
        return has_permission
    
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
