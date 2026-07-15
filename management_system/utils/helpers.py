"""
General helper functions for views and data processing.
Provides reusable utilities for common operations.
"""
import json
import re
from datetime import date, datetime, timedelta
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.timezone import now
from django.http import HttpResponse
from logging import getLogger
from management_system.utils.decorators import check_role_permission
from management_system.models import User, Enrollment, MANAGEMENT_ROLES, Course, Grade

logger = getLogger(__name__)


def get_datetime(datetime_string):
    """
    Parse datetime string in format YYYY-MM-DDTHH:MM to datetime object.
    
    Args:
        datetime_string: String in format "YYYY-MM-DDTHH:MM"
    
    Returns:
        datetime object
    """
    return datetime.strptime(datetime_string, "%Y-%m-%dT%H:%M")


def parse_json_value(value, default=None):
    """
    Safely parse a JSON string while preserving plain text values.
    """
    if value in (None, ""):
        return default

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def is_quiz_in_user_window(quiz, user) -> bool:
    """
    Determine whether a quiz's opening date falls within a student's academic window.

    A student's academic window spans from their joined_date up to
    joined_date + Course.MAXIMUM_LEVEL years. This lets us distinguish between:
      - A quiz originally opened for this student's level (in-window)
      - A quiz re-opened for a newer level (out-of-window)

    Management roles always return True (no restriction).

    Args:
        quiz: Quiz model instance
        user: User model instance

    Returns:
        bool – True if the quiz is within the user's academic window
    """
    if user.role and user.role.role in MANAGEMENT_ROLES:
        return True

    if quiz.course_offering_id and Enrollment.objects.filter(
        student=user,
        academic_year__course_offerings__pk=quiz.course_offering_id,
        status="active",
    ).exists():
        return True

    joined: date = user.joined_date
    try:
        window_end = joined.replace(year=joined.year + Course.MAXIMUM_LEVEL)
    except ValueError:
        # Handle Feb 29 edge case
        window_end = joined.replace(year=joined.year + Course.MAXIMUM_LEVEL, day=28)

    quiz_open_date = quiz.opening_date.date() if hasattr(quiz.opening_date, "date") else quiz.opening_date
    return joined <= quiz_open_date <= window_end


def user_has_management_role(user) -> bool:
    return bool(user.role and user.role.role in MANAGEMENT_ROLES)


def user_can_access_course(user, course) -> bool:
    if user_has_management_role(user):
        return True

    if not user.role:
        return False

    active_enrollments = Enrollment.objects.filter(
        student=user,
        status='active',
    )

    if not active_enrollments.exists():
        # ponytail: legacy fallback for pre-backfill users; remove in LMS-REV-002
        return course.can_access(user.role.role)

    # Normal enrollment grants access to all courses in the AcademicYear.
    if active_enrollments.filter(
        enrollment_type="normal",
        academic_year__course_offerings__course=course,
    ).exists():
        return True

    # Targeted enrollment (repeat/remedial/manual) grants access only to its specific offering.
    if active_enrollments.filter(
        enrollment_type__in=["repeat", "remedial", "manual"],
        course_offering__course=course,
    ).exists():
        return True

    return False


def get_student_quiz_status(quiz, user, current_time=None):
    """
    Return the student's current quiz mode using the same rule set everywhere.
    """
    grade = Grade.objects.filter(user=user, quiz=quiz).first()
    if grade:
        return "view", grade

    current_time = current_time or now()
    quiz_open = quiz.opening_date <= current_time <= quiz.closing_date + timedelta(minutes=30)

    if quiz_open and is_quiz_in_user_window(quiz, user):
        return "exam", None

    return "closed_unsolved", None


def paginate_obj(request, obj, page_size=15):
    """
    Paginate a queryset or list of objects.
    
    Args:
        request: Django request object
        obj: Queryset or list to paginate
        page_size: Number of items per page (default: 15)
    
    Returns:
        Page object with serialized items
    """
    paginator = Paginator(obj, page_size)
    page_number = request.GET.get("page", 1)
    page = paginator.get_page(page_number)
    
    # Serialize items if they have the method
    if hasattr(page.object_list[0] if page.object_list else None, 'serialize_pagination'):
        page.object_list = [obj.serialize_pagination() for obj in page.object_list]
    
    return page


def render_dashboard(request, obj, view, context, parameters=[]):
    """
    Render dashboard with pagination and filters.
    Handles both full page and HTMX partial rendering.
    
    Args:
        request: Django request object
        obj: Queryset or list of objects to display
        view: View name/identifier
        context: Additional context dictionary
        parameters: URL parameters for the dashboard
    
    Returns:
        Rendered template response
    """
    # Check permission
    try:
        user = User.objects.get(username=request.user)
    except User.DoesNotExist:
        return HttpResponse(_("Unauthorized"), status=401)
    
    if not check_role_permission(user, 'management'):
        logger.warning(f"User: {user} attempted to access {view} dashboard")
        return HttpResponse(_("Unauthorized"), status=401)
    
    # Paginate objects
    page_obj = paginate_obj(request, obj)
    
    # Prepare filters
    filters = ["year_filter.html", "naming_filter.html"]
    if "filters" in context:
        context['filters'].extend(filters)
    else:
        context['filters'] = filters
    
    logger.info(f"User: {user} accesses page {page_obj.number} in {view} dashboard")
    
    # Handle HTMX partial rendering
    if request.headers.get("HX-Target") == "table-container":
        return render(request, "partials/table_and_pagination.html", {
            "page_obj": page_obj,
            "header": _(view.capitalize()),
            "view": view,
            "url": reverse(f"{view}-dashboard", args=parameters),
            "parameters": parameters,
            **context
        })
    
    # Full page rendering
    return render(request, "dashboard.html", {
        "page_obj": page_obj,
        "header": _(view.capitalize()),
        "view": view,
        "url": reverse(f"{view}-dashboard", args=parameters),
        "parameters": parameters,
        **context
    })


def unpack_quiz_form(form_dict):
    """
    Unpack quiz form data into questions and quiz data dictionaries.
    
    Args:
        form_dict: Form data dictionary (typically request.POST)
    
    Returns:
        Tuple of (questions dict, quiz_data dict)
    """
    questions = dict()
    quiz_data = dict()
    
    for key in form_dict.keys():
        values = form_dict.getlist(key)
        val = values if len(values) > 1 else values[0]

        # Handle nested question data
        if key.startswith("questions"):
            parts = re.findall(r"\[([^\]]*)\]", key)
            if len(parts) < 2:
                continue

            index = parts[0]
            key_value = parts[1]
            
            # Calculate total grade from individual question grades
            if key_value == "grade":
                quiz_data['total_grade'] = quiz_data.get("total_grade", 0) + int(val)
            
            # Handle multiple choices (getlist for multi-select)
            if key_value == "choices":
                val = form_dict.getlist(key)
            elif key_value == "config":
                val = parse_json_value(val, {})
            
            # Add to questions dictionary
            if index in questions:
                questions[index][key_value] = val
            else:
                questions[index] = {key_value: val}
        
        # Handle top-level quiz fields
        else:
            quiz_data[key] = val[-1] if isinstance(val, list) and len(val) == 1 else val
    
    return questions, quiz_data


def format_file_size(size_bytes):
    """
    Format file size in bytes to human-readable format.
    
    Args:
        size_bytes: Size in bytes
    
    Returns:
        Formatted string (e.g., "1.5 MB", "342 KB")
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1048576:  # 1024 * 1024
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1073741824:  # 1024 * 1024 * 1024
        return f"{size_bytes / 1048576:.2f} MB"
    else:
        return f"{size_bytes / 1073741824:.2f} GB"


def generate_breadcrumb(items):
    """
    Generate breadcrumb navigation items.
    
    Args:
        items: List of tuples [(title, url), ...]
    
    Returns:
        List of dictionaries for template rendering
    """
    breadcrumb_items = []
    for title, url in items:
        breadcrumb_items.append({
            'title': title,
            'url': url
        })
    return breadcrumb_items


def safe_get_user(request):
    """
    Safely get the authenticated user object.
    
    Args:
        request: Django request object
    
    Returns:
        User object or None if not found
    """
    if not request.user.is_authenticated:
        return None
    
    try:
        return User.objects.get(username=request.user)
    except User.DoesNotExist:
        logger.warning(f"Authenticated user {request.user} not found in database")
        return None


def is_ajax(request):
    """
    Check if request is an AJAX request.
    Compatible with Django 3.1+ (X-Requested-With header).
    
    Args:
        request: Django request object
    
    Returns:
        Boolean indicating if request is AJAX
    """
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def is_htmx(request):
    """
    Check if request is an HTMX request.
    
    Args:
        request: Django request object
    
    Returns:
        Boolean indicating if request is HTMX
    """
    return request.headers.get('HX-Request') == 'true'
