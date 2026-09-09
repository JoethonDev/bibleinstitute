"""
General helper functions for views and data processing.
Provides reusable utilities for common operations.
"""
import json
import re
from datetime import date, datetime, timedelta
from django.contrib import messages as django_messages
from django.core.paginator import Paginator
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.timezone import now
from django.http import HttpResponse
from django.core.exceptions import ValidationError
from logging import getLogger
from urllib.parse import unquote
from management_system.utils.decorators import check_role_permission
from management_system.models import AcademicYear, User, MANAGEMENT_ROLES, Grade
from management_system.academic_access import user_can_write_offering_activity
from .timezones import ensure_aware, parse_application_datetime
from .quiz_access import quiz_window

logger = getLogger(__name__)


def hx_target_id(request):
    """Return the target element id from the HTMX HX-Target header.

    HTMX 4 serializes the header as ``tag#id`` (with an URI-encoded id);
    older bare-id values are accepted as-is so the comparison stays a
    single canonical point for every HTMX partial branch."""
    target = request.headers.get("HX-Target", "")
    if not target:
        return ""
    if "#" in target:
        return unquote(target.rsplit("#", 1)[1])
    return target


def get_datetime(datetime_string):
    """
    Parse datetime string in format YYYY-MM-DDTHH:MM to datetime object.
    
    Args:
        datetime_string: String in format "YYYY-MM-DDTHH:MM"
    
    Returns:
        datetime object
    """
    return parse_application_datetime(datetime_string)


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
    if user.role and user.role.role in MANAGEMENT_ROLES:
        return True

    return bool(quiz.course_offering_id and user_can_write_offering_activity(user, quiz.course_offering))


def user_has_management_role(user) -> bool:
    return bool(user.role and user.role.role in MANAGEMENT_ROLES)


def get_student_quiz_status(quiz, user, current_time=None):
    """
    Return the student's current quiz mode using the same rule set everywhere.
    """
    grade = Grade.objects.filter(user=user, quiz=quiz).first()
    if grade:
        return "view", grade

    current_time = current_time or now()
    quiz_open = is_quiz_open(quiz, current_time, user)

    if quiz_open and is_quiz_in_user_window(quiz, user):
        return "exam", None

    return "closed_unsolved", None


def is_quiz_open(quiz, current_time=None, user=None) -> bool:
    opening_date, closing_date = quiz_window(quiz, user)
    current_time = ensure_aware(current_time or now())
    return ensure_aware(opening_date) <= current_time <= ensure_aware(closing_date) + timedelta(minutes=30)


def pagination_query_string(request, exclude=("page",)):
    """
    Build the URL-encoded filter query preserved by canonical pagination links.
    The named page parameters are excluded so links can append their own page value.
    """
    params = request.GET.copy()
    for key in exclude:
        params.pop(key, None)
    return params.urlencode()


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
        return HttpResponse(_("Unauthorized"), status=403)
    
    if not check_role_permission(user, 'management'):
        logger.warning(f"User: {user} attempted to access {view} dashboard")
        return HttpResponse(_("Unauthorized"), status=403)
    
    # Paginate objects
    page_obj = paginate_obj(request, obj, context.get("page_size", 15))
    
    # Prepare filters
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    context["pagination_query"] = pagination_params.urlencode()
    sort_params = request.GET.copy()
    sort_params.pop("page", None)
    sort_params.pop("sort", None)
    sort_params.pop("order", None)
    context["sort_query"] = sort_params.urlencode()
    year_filter = "academic_year_filter.html" if context.get("academic_year_filter") else "year_filter.html"
    filters = [year_filter, "naming_filter.html"]
    if "filters" in context:
        context['filters'].extend(filters)
    else:
        context['filters'] = filters
    
    logger.info(f"User: {user} accesses page {page_obj.number} in {view} dashboard")
    
    # Handle HTMX partial rendering
    if hx_target_id(request) == "table-container":
        return render(request, "partials/table_and_pagination.html", {
            "page_obj": page_obj,
            "header": _(view.capitalize()),
            "view": view,
            "url": reverse(f"{view}-dashboard", args=parameters),
            "parameters": parameters,
            **context
        })
    
    # Full page rendering
    return render(request, context.get("template_name", "dashboard.html"), {
        "page_obj": page_obj,
        "header": _(view.capitalize()),
        "view": view,
        "url": reverse(f"{view}-dashboard", args=parameters),
        "parameters": parameters,
        **context
    })


def select_content_academic_year(request):
    """Resolve the requested management content year, defaulting to active."""
    active_year = AcademicYear.objects.get(is_active=True)
    requested_id = request.GET.get("academic_year")
    if not requested_id:
        selected_year = active_year
    elif not str(requested_id).isdigit():
        raise ValidationError(_("Invalid academic year."))
    else:
        selected_year = AcademicYear.objects.filter(pk=int(requested_id)).first()
        if selected_year is None:
            raise ValidationError(_("Invalid academic year."))

    academic_years = AcademicYear.objects.order_by("-is_active", "-ordering")
    return selected_year, academic_years


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


def render_page(request, full_template, partial_template, context):
    """
    Render the content partial for HTMX requests, the full shell otherwise.
    The partial must render the page's root #content element so the client
    fragment swap is identical in both modes.

    HTMX partial responses cannot render the shell toast stack, so any
    pending Django messages are appended as an out-of-band swap of the
    shell's #toast-stack element.
    """
    if is_htmx(request):
        response = render(request, partial_template, context)
        storage = django_messages.get_messages(request)
        toast_html = ""
        if storage:
            toast_html = render_to_string(
                "partials/toast_oob.html", request=request
            )
        if toast_html:
            encoding = response.charset or "utf-8"
            response.content += toast_html.encode(encoding)
        return response
    return render(request, full_template, context)
