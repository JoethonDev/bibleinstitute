from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from .models import CourseOffering, Enrollment, PublicationStatus, User


READABLE_ENROLLMENT_STATUSES = (Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED)
TARGETED_ENROLLMENT_TYPES = (
    Enrollment.Type.REPEAT,
    Enrollment.Type.REMEDIAL,
    Enrollment.Type.MANUAL,
)


def _management_user(user: User) -> bool:
    return bool(user.is_authenticated and user.role and user.role.role in {"admin", "staff"})


def readable_offering_access(user: User) -> dict[str, Exists]:
    """Enrollment predicates shared by access filtering and student access labels."""
    normal_access = Enrollment.objects.filter(
        student=user,
        status__in=READABLE_ENROLLMENT_STATUSES,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
    )
    targeted_access = Enrollment.objects.filter(
        student=user,
        status__in=READABLE_ENROLLMENT_STATUSES,
        enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
        course_offering_id=OuterRef("pk"),
    )
    return {
        "student_has_normal_access": Exists(normal_access),
        "student_has_targeted_access": Exists(targeted_access),
    }


def accessible_offerings(user: User, include_management: bool = False) -> QuerySet[CourseOffering]:
    """Return readable offerings; callers choose related data and content loading."""
    if not user.is_authenticated:
        return CourseOffering.objects.none()
    if _management_user(user):
        return CourseOffering.objects.all() if include_management else CourseOffering.objects.none()
    return _student_accessible_offerings(user, CourseOffering.objects.all())


def _student_accessible_offerings(
    user: User,
    offerings: QuerySet[CourseOffering],
) -> QuerySet[CourseOffering]:
    if not user.is_authenticated:
        return offerings.none()
    return offerings.alias(**readable_offering_access(user)).filter(
        Q(student_has_normal_access=True) | Q(student_has_targeted_access=True),
        status=PublicationStatus.PUBLISHED,
    )


def active_year_offerings_for_student(student: User, include_targeted: bool = False) -> QuerySet[CourseOffering]:
    """Return published offerings available to a student in the active year."""
    normal_access = Enrollment.objects.filter(
        student=student,
        status=Enrollment.Status.ACTIVE,
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
        academic_year_level__academic_year__is_active=True,
    )
    access_query = Exists(normal_access)
    if include_targeted:
        targeted_access = Enrollment.objects.filter(
            student=student,
            status=Enrollment.Status.ACTIVE,
            enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
            course_offering_id=OuterRef("pk"),
            course_offering__academic_year_level__academic_year__is_active=True,
        )
        access_query |= Exists(targeted_access)
    return CourseOffering.objects.filter(status=PublicationStatus.PUBLISHED).filter(access_query)


def active_year_published_offerings_for_user(user: User) -> QuerySet[CourseOffering]:
    """Return the current published offerings visible to a student or manager."""
    if not user.is_authenticated:
        return CourseOffering.objects.none()
    if _management_user(user):
        return CourseOffering.objects.filter(
            status=PublicationStatus.PUBLISHED,
            academic_year_level__academic_year__is_active=True,
        )
    return active_year_offerings_for_student(user, include_targeted=True)


def user_can_read_offering(user: User, offering: CourseOffering) -> bool:
    if _management_user(user):
        return True
    if not user.is_authenticated or offering.status != PublicationStatus.PUBLISHED:
        return False
    return accessible_offerings(user).filter(pk=offering.pk).exists()


def user_can_write_offering_activity(
    user: User,
    offering: CourseOffering,
    *,
    allow_management: bool = False,
) -> bool:
    if not user.is_authenticated or (_management_user(user) and not allow_management):
        return False
    if offering.status != PublicationStatus.PUBLISHED or not offering.academic_year_level.academic_year.is_active:
        return False
    return Enrollment.objects.filter(
        student=user,
        status=Enrollment.Status.ACTIVE,
    ).filter(
        Q(
            enrollment_type=Enrollment.Type.NORMAL,
            academic_year_level_id=offering.academic_year_level_id,
        )
        | Q(
            enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
            course_offering_id=offering.pk,
        )
    ).exists()


def get_accessible_offering_or_403(
    user: User,
    offering_id: int,
    write: bool = False,
    *,
    allow_management: bool = False,
    offering_queryset: QuerySet[CourseOffering] | None = None,
) -> CourseOffering:
    queryset = (
        offering_queryset
        if offering_queryset is not None
        else CourseOffering.objects.all()
    )
    queryset = queryset.select_related(
        "course", "academic_year_level__academic_year", "academic_year_level__level"
    )
    if write:
        offering = queryset.filter(pk=offering_id).first()
        allowed = bool(
            offering
            and user_can_write_offering_activity(
                user, offering, allow_management=allow_management
            )
        )
    elif _management_user(user):
        offering = queryset.filter(pk=offering_id).first()
        allowed = offering is not None
    else:
        offering = _student_accessible_offerings(user, queryset).filter(
            pk=offering_id
        ).first()
        allowed = offering is not None
    if not allowed or offering is None:
        raise PermissionDenied(_("You do not have access to this offering."))
    return offering
