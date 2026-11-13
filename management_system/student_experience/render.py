from django.shortcuts import render

from ..models import StudentMode
from .mode import is_management_student_view, resolve_student_mode
from .navigation import student_navigation


def student_shell_context(page: str, *, user=None) -> dict:
    """Return the keys every new student shell template needs."""
    is_management = is_management_student_view(user)
    return {
        "student_mode": StudentMode.NEW,
        "student_page": page,
        "is_management_student_view": is_management,
        "student_nav": student_navigation(
            page,
            include_support=not is_management,
            include_offline=not is_management,
        ),
    }


def render_student_page(
    request,
    *,
    page: str,
    template: str,
    context: dict,
    status: int = 200,
):
    if resolve_student_mode(request) != StudentMode.NEW:
        raise ValueError("Student-next templates require the new student mode.")
    page_context = {
        **context,
        **student_shell_context(page, user=getattr(request, "user", None)),
    }
    return render(request, template, page_context, status=status)
