"""
Custom decorators for permission checking and access control.
Extends Django's @login_required with permission checking.
"""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.utils.translation import gettext as _
from django.shortcuts import redirect
from django.urls import reverse_lazy
from logging import getLogger
from management_system.models import MANAGEMENT_ROLES, User

logger = getLogger(__name__)


def can_manage_applications(user) -> bool:
    return bool(user.role and user.role.role == "admin")


def can_manage_content(user) -> bool:
    return bool(user.role and user.role.role in ("admin", "staff"))


def can_delete_content(user) -> bool:
    return bool(user.role and user.role.role == "admin")


def can_grade(user) -> bool:
    return bool(user.role and user.role.role in ("admin", "staff"))


def can_scan_attendance(user) -> bool:
    return bool(user.role and user.role.role in ("admin", "staff", "moderator"))


def can_correct_attendance(user) -> bool:
    return bool(user.role and user.role.role == "admin")


def can_view_reports(user) -> bool:
    return bool(user.role and user.role.role in ("admin", "staff"))


def login_required_with_permission(role_type=None, resource=None):
    """
    Decorator that combines @login_required with role-based permission checking.
    
    Usage:
        @login_required_with_permission('admin', 'users')
        def some_view(request):
            # View logic...
    
    Args:
        role_type: The role type required ('admin', 'teacher', etc.)
        resource: The resource being accessed (for logging purposes)
    
    Returns:
        Decorated view function that checks both authentication and permissions
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url=reverse_lazy("user_login"))
        def wrapper(request, *args, **kwargs):
            try:
                user = User.objects.get(username=request.user)
            except User.DoesNotExist:
                logger.warning(f"User {request.user} not found in database")
                return HttpResponse(_("Unauthorized"), status=401)
            
            # Check if role type is specified and validate
            if role_type:
                if not check_role_permission(user, role_type, resource):
                    logger.warning(
                        f"User: {user} (role: {user.role.role}) attempted to access "
                        f"{resource or 'resource'} requiring {role_type} permission"
                    )
                    return HttpResponse(_("Unauthorized"), status=401)
            
            # User is authorized, proceed with view
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def admin_required(view_func):
    """
    Shortcut decorator for admin-only views.
    
    Usage:
        @admin_required
        def admin_only_view(request):
            # Admin-only logic...
    """
    return login_required_with_permission('admin')(view_func)


def management_required(view_func):
    """
    Shortcut decorator for views requiring admin or teacher role.
    
    Usage:
        @management_required
        def management_view(request):
            # Management logic...
    """
    @wraps(view_func)
    @login_required(login_url=reverse_lazy("user_login"))
    def wrapper(request, *args, **kwargs):
        try:
            user = User.objects.get(username=request.user)
        except User.DoesNotExist:
            logger.warning(f"User {request.user} not found in database")
            return HttpResponse(_("Unauthorized"), status=401)
        
        if user.role.role not in MANAGEMENT_ROLES:
            logger.warning(f"User: {user} attempted to access management panel")
            return HttpResponse(_("Unauthorized"), status=401)
        
        return view_func(request, *args, **kwargs)
    
    return wrapper


def check_role_permission(user, role_type, resource=None):
    """
    Check if a user has the required role permission.
    
    Args:
        user: The User object to check
        role_type: The required role type ('admin', 'staff', 'management', etc.)
        resource: The resource being accessed (optional, for logging)
    
    Returns:
        bool: True if user has permission, False otherwise
    """
    if not user.role:
        return False

    # Management role check (admin or staff)
    if role_type == 'management':
        return user.role.role in MANAGEMENT_ROLES

    # Specific role checks
    if role_type == 'admin':
        return user.role.role == 'admin'

    if role_type in ('staff', 'teacher'):
        return user.role.role in ('admin', 'staff')

    # Default: check if user role matches the required role
    return user.role.role == role_type


def capability_required(cap_check):
    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url=reverse_lazy("user_login"))
        def wrapper(request, *args, **kwargs):
            if not cap_check(request.user):
                logger.warning(f"User {request.user} denied by {cap_check.__name__}")
                return HttpResponse(_("Unauthorized"), status=401)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def ajax_login_required(view_func):
    """
    Decorator for AJAX views that require authentication.
    Returns JSON response instead of redirect.
    
    Usage:
        @ajax_login_required
        def ajax_view(request):
            # AJAX logic...
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({
                'error': _('Authentication required'),
                'redirect': str(reverse_lazy('user_login'))
            }, status=401)
        
        return view_func(request, *args, **kwargs)
    
    return wrapper
