import boto3.s3
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, FileResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView, FormView, DetailView
from django.utils.translation import gettext as _ # Import gettext for runtime translation
from django.utils import translation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.db import transaction
from django.contrib.messages import success, error, info
from django.contrib.auth import update_session_auth_hash
from django.conf import settings


# Third Party
from logging import getLogger
import io
# from googleapiclient.http import MediaIoBaseDownload
# from googleapiclient.errors import HttpError
import boto3
import re
import os

# Internal Import
# from .utils import get_drive_client
from .models import *
from .forms import CourseForm, UserCreationForm, UserUpdateForm, ProfileUpdateForm
from django.utils.timezone import now

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

# Helper Functions
def is_managerial(request):
    user = User.objects.get(username=request.user)
    if user.role.role not in MANAGEMENT_ROLES:
        logger.warning(f"User : {user} is trying to access admin panel")
        return HttpResponse(_("Unauthorized"), status=401)
    return HttpResponse(_("authorized"), status=200)

def get_datetime(datetime_string):
    return datetime.strptime(datetime_string, "%Y-%m-%dT%H:%M")

# def list_current_folder(folder_id=None, root=True):
#     query = f"'{folder_id}' in parents and trashed=false" if not root else "sharedWithMe=true and trashed=false"

#     results = DRIVE_CLIENT.files().list(
#         q=query,
#         fields="files(id, name, mimeType)"
#     ).execute()

#     storage_data = results.get("files", [])
#     drive = []

#     for data in storage_data:
#         file_type = "folder" if data['mimeType'] == "application/vnd.google-apps.folder" else "file"

#         drive.append({
#             "id" : data['id'],
#             "name" : data['name'],
#             "type" : file_type,
#         })

#     if not root:
#         folder_details = DRIVE_CLIENT.files().get(
#             fileId=folder_id,
#             fields="id, name, parents"
#         ).execute()

#         # Check if the queried folder has a parent
#         if "parents" in folder_details:
#             parent_id = folder_details["parents"][0]
#         else:
#             parent_id = ""
#         return drive, parent_id
    
#     return drive

def list_current_folder(folder_name=""):
    # Flag for getting all objects 
    has_objects = True

    parents = folder_name.split("-")
    folder_id = parents or []
    parent_folder = "-".join(folder_id[:-2]) or None
    folder_name = "/".join(folder_id) or ""
    if folder_name and not folder_name.endswith("/"):
        folder_name += "/"

    objects = CLOUD_CLIENT.list_objects_v2(Bucket=bucket_name, Prefix=folder_name, Delimiter="/")
    contents = [

    ]
    while has_objects:
        # Files
        if "Contents" in objects:
            for obj in objects["Contents"]:
                file_name = obj["Key"].split("/")[-1]
                contents.append({
                    "id" : obj["Key"],
                    "name" : file_name,
                    "type" : "file"
                })

        # Folders
        if "CommonPrefixes" in objects:
            for folder in objects["CommonPrefixes"]:
                separated_folder = folder["Prefix"].split("/")
                folder = "-".join(separated_folder)
                contents.insert(0, {
                    "id" : folder,
                    "name" : separated_folder[-2] ,
                    "type" : "folder"
                })
        # More Objects
        has_objects = objects['IsTruncated']
        if has_objects:
            continuation_token = objects['NextContinuationToken']
            objects = CLOUD_CLIENT.list_objects_v2(Bucket=bucket_name, ContinuationToken=continuation_token)

    return contents, parent_folder

# def download_from_drive(file_request):
#     in_memory = io.BytesIO()
#     downloader = MediaIoBaseDownload(in_memory, file_request)

#     done = False
#     while not done:
#         try:
#             _, done = downloader.next_chunk()
#         except:
#             break
    
#     in_memory.seek(0)

#     return in_memory

def download_from_bucket(file_name):
    in_memory = io.BytesIO()
    response = CLOUD_CLIENT.get_object(Bucket=bucket_name, Key=file_name)
    # Write Coming Data
    in_memory.write(response['Body'].read())
    in_memory.seek(0)

    return in_memory

def generate_unique_url(segment_name):
    url = CLOUD_CLIENT.generate_presigned_url(
        'get_object',  # The operation you want to allow (e.g., 'get_object' for downloading)
        Params={'Bucket': bucket_name, 'Key': segment_name},
        ExpiresIn=3600  # Expiration time in seconds (e.g., 3600 = 1 hour)
    )
    return url

def paginate_obj(request, obj, page_size=15):
    paginator = Paginator(obj, page_size)
    page_number = request.GET.get("page", 1)
    page = paginator.get_page(page_number)
    page.object_list = [obj.serialize_pagination() for obj in page.object_list]
    return page

def has_admin_permission(request, view):
    user = User.objects.get(username=request.user)
    if user.role.role not in MANAGEMENT_ROLES:
        logger.warning(f"User : {user} is trying to access admin panel")
        return HttpResponse(_("Unauthorized"), status=401)
    
    return HttpResponse(_("authorized"), status=200)
    
def render_dashboard(request, obj, view, context, parameters=[]):
    has_permission = has_admin_permission(request, view)
    if has_permission.status_code == 401:
        return has_permission

    user = request.user
 
    page_obj = paginate_obj(request, obj)
    filters = ["year_filter.html", "naming_filter.html"]

    if "filters" in context:
        context['filters'].extend(filters)
    else:
        context['filters'] = filters

    logger.info(f"User : {user} accesses page {page_obj.number} in {view} dashboard ")

    return render(request, "dashboard.html", {
        "page_obj" : page_obj,
        "header" : _(view.capitalize()), # Translate header
        "view" : view,
        "url" : reverse(f"{view}-dashboard", args=parameters),
        **context
    })

def unpack_quiz_form(form_dict):
    questions = dict()
    quiz_data = dict()

    for key, val in form_dict.items():
        # Case Dictionary
        if key.startswith("questions"):
            parts = key.split("[")
            index = parts[1][:-1]
            key_value = parts[2][:-1]
            # If grade
            if key_value == "grade":
                quiz_data['total_grade'] = quiz_data.get("total_grade", 0) + int(val)

            # To get all multiple choices!
            if key_value == "choices":
                val = form_dict.getlist(key)
            # Check if added index
            if index in questions:
                questions[index][key_value] = val
            else:
                questions[index] = {
                    key_value : val
                }
            
        # Case Field
        else:
            quiz_data[key] = val
    
    return questions, quiz_data

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
        print(course.fetch_quizzes(user))
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
            m3u8_content = download_from_bucket(file_key).read().decode("utf-8")

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
        quiz = get_object_or_404(Quiz, pk=quiz_id)
        user = User.objects.get(username=request.user)
        # Check closing datetime to ensure after time responses!
        submission_datetime = now()
        can_have_exam = quiz.closing_date + timedelta(minutes=30)  >= submission_datetime
        if not can_have_exam:
            info(request, _("Quiz is closed, You can not take it anymore!"), extra_tags="alert-primary") # Translate
 
        # Check if user has taken exam
        grade = Grade.objects.filter(user=user, quiz_id=quiz_id).first()
        if grade:
            total_grade = grade.total_grade
        else:
            total_grade = 0

        if request.method == "GET":
            course = get_object_or_404(Course, pk=course_id)
            logger.info(f"User : {user} is accessing {quiz.name} in {course.name} course")

            if user.role.role not in MANAGEMENT_ROLES and (not course.can_access(user.role.role) or quiz.opening_date > now()):
                return HttpResponse(_("Unauthorized"), status=401) # Translate "Unauthorized"
            
            quiz_mode = "view" if grade or not can_have_exam else "exam"
            query_set = Submission.objects.filter(question__quiz_id=quiz_id, user=user) if grade else Question.objects.filter(quiz_id=quiz_id)
            questions =  [
                question.serialize() for question in query_set
            ]
        
            return render(request, "display_quiz.html", {
                "quiz_name" : quiz.name,
                "username" : request.user.username,
                "questions" : questions,
                "is_student" : True,
                "mode" : quiz_mode,
                "extended_view" : "base.html",
                "id" : "container",
                "total_grade" : total_grade,
                "closing_date" : quiz.closing_date.timestamp(),
                "exam_taken" : True if grade else False,
                "back_url" : reverse("course-details", args=[course_id,])
            })
        
        elif request.method == "POST":
            # Prevent another submission
            if not grade:
                # Consider making a buffer time and datetime check for submission
                if not can_have_exam:
                    # Send back to main page with error message TODO
                    return HttpResponse(_("Invalid Request, submission is closed!")) # Translate
                
                logger.info(f"User : {user} has submitted {quiz} at {submission_datetime.strftime('%d/%m/%Y, %H:%M:%S')}")

                questions_data, _ = unpack_quiz_form(request.POST)

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

    users = User.objects.filter(query).order_by("joined_date")

    context = {
        "name_value" : name or "",
        "filtering" : role_value or "",
        "columns" : User.get_columns(),
        "options" : [_("Choose Role"), *Role.get_readable_values()] # Translate "Choose Role"
    }

    return render_dashboard(request, users, view, context)

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
            "drive" : list_current_folder()[0],
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

    root = True
    parent_folder = None
    if folder_id and folder_id != "None":
        root = False
        drive, parent_folder = list_current_folder(folder_id)
    else:
        drive, _ = list_current_folder()

    return render(request, "drive_files.html", {
        "drive" : drive,
        "parent_folder" : parent_folder,
        "is_root" : root
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
                "drive" : list_current_folder()[0],
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
        "drive" : list_current_folder()[0],
        "is_root" : True
    })

@login_required(login_url=LOGIN_URL)
def upload_link(request):
    is_manager = is_managerial(request)
    if not is_manager:
        return JsonResponse({"message" : _("unauthorized!")}, status=401) # Translate "unauthorized!"
    
    body = json.loads(request.body)
    presigned_url = CLOUD_CLIENT.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': bucket_name,
            'Key': body["filename"],
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
                print(f"Question title : {title} Type : {question_type} Auto grade : {auto_grade}")
                
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
