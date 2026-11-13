"""Public-page guard for accounts that use the new student experience."""

from django.http import Http404
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from ..models import StudentMode
from .mode import resolve_student_mode

PUBLIC_STUDENT_URL_NAMES = {
    "home",
    "about",
    "program",
    "signup",
    "user_login",
    "student-portal",
}
ALLOWED_ALWAYS = {
    "logout",
    "set_language",
    "javascript-catalog",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
}
SKIP_PREFIXES = (
    "/admin/",
    "/api/",
    "/static/",
    "/media/",
    "/i18n/",
    "/telegram/",
    "/automation/",
)


class StudentExperienceMiddleware:
    """Send new-mode students from public pages to their learning space.

    This is a navigation rule only. It runs after authentication and never
    replaces the login/capability checks owned by the views themselves.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("GET", "HEAD") and self._should_redirect(request):
            return redirect("courses")
        return self.get_response(request)

    def _should_redirect(self, request) -> bool:
        path = request.path_info or ""
        if path.startswith(SKIP_PREFIXES):
            return False
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if getattr(getattr(user, "role", None), "role", None) != "student":
            return False
        if resolve_student_mode(request) != StudentMode.NEW:
            return False
        try:
            match = resolve(path)
        except (Resolver404, Http404):
            return False
        url_name = match.url_name
        if url_name in ALLOWED_ALWAYS:
            return False
        return url_name in PUBLIC_STUDENT_URL_NAMES
