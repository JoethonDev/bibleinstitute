from django.shortcuts import render

from ..models import StudentMode
from .mode import resolve_student_mode
from .navigation import student_navigation


def student_shell_context(page: str) -> dict:
    """Return the keys every new student shell template needs."""
    return {
        "student_mode": StudentMode.NEW,
        "student_page": page,
        "student_nav": student_navigation(page),
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
    page_context = {**context, **student_shell_context(page)}
    return render(request, template, page_context, status=status)
