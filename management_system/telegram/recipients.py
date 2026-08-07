"""Centralized Telegram recipient authorization query boundaries."""

from __future__ import annotations

from django.db.models import Exists, OuterRef, QuerySet

from ..models import Enrollment, User


TELEGRAM_TARGETED_ENROLLMENT_TYPES = (
    Enrollment.Type.REPEAT,
    Enrollment.Type.REMEDIAL,
    Enrollment.Type.MANUAL,
)


def eligible_student_queryset(
    *,
    academic_year_id: int,
    level_id: int | None = None,
    academic_year_level_id: int | None = None,
    course_offering_id: int | None = None,
) -> QuerySet[User]:
    """Return active linked students authorized within one current scope.

    Normal enrollments grant the complete year-level scope. Targeted
    enrollments grant only their exact offering. Callers choose a year/level
    target for broadcasts or an exact scope/offering pair for notifications.
    """
    normal_filters = {
        "academic_year_level__academic_year_id": academic_year_id,
        "enrollment_type": Enrollment.Type.NORMAL,
    }
    targeted_filters = {
        "course_offering__academic_year_level__academic_year_id": academic_year_id,
        "enrollment_type__in": TELEGRAM_TARGETED_ENROLLMENT_TYPES,
    }
    if level_id is not None:
        normal_filters["academic_year_level__level_id"] = level_id
        targeted_filters["course_offering__academic_year_level__level_id"] = level_id
    if academic_year_level_id is not None:
        normal_filters["academic_year_level_id"] = academic_year_level_id
    if course_offering_id is not None:
        targeted_filters["course_offering_id"] = course_offering_id

    normal_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        **normal_filters,
    )
    targeted_access = Enrollment.objects.filter(
        student_id=OuterRef("pk"),
        status=Enrollment.Status.ACTIVE,
        **targeted_filters,
    )
    return (
        User.objects.filter(
            role__role="student",
            is_active=True,
            application_status="active",
            telegram_account__is_active=True,
        )
        .filter(Exists(normal_access) | Exists(targeted_access))
        .select_related("telegram_account")
        .order_by("pk")
    )
