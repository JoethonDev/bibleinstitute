from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Prefetch, Q, QuerySet
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _

from .models import CourseOffering, Enrollment, Lesson, Quiz, PublicationStatus, User


READABLE_ENROLLMENT_STATUSES = (Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED)
TARGETED_ENROLLMENT_TYPES = (
    Enrollment.Type.REPEAT,
    Enrollment.Type.REMEDIAL,
    Enrollment.Type.MANUAL,
)


def _management_user(user: User) -> bool:
    return bool(user.is_authenticated and user.role and user.role.role in {"admin", "staff"})


def accessible_offerings(user: User, include_management: bool = False) -> QuerySet[CourseOffering]:
    """Return offerings readable by a student or explicitly requested manager preview."""
    if not user.is_authenticated:
        return CourseOffering.objects.none()

    offerings = CourseOffering.objects.all()
    if include_management and _management_user(user):
        return _with_content(offerings, include_drafts=True)
    if _management_user(user):
        return CourseOffering.objects.none()

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
    offerings = offerings.filter(status=PublicationStatus.PUBLISHED).filter(
        Exists(normal_access) | Exists(targeted_access)
    )
    return _with_content(offerings)


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
    return _with_content(CourseOffering.objects.filter(status=PublicationStatus.PUBLISHED).filter(access_query))


def active_year_published_offerings_for_user(user: User) -> QuerySet[CourseOffering]:
    """Return the current published offerings visible to a student or manager."""
    if not user.is_authenticated:
        return CourseOffering.objects.none()
    if _management_user(user):
        return _with_content(
            CourseOffering.objects.filter(
                status=PublicationStatus.PUBLISHED,
                academic_year_level__academic_year__is_active=True,
            )
        )
    return active_year_offerings_for_student(user, include_targeted=True)


def _with_content(offerings: QuerySet[CourseOffering], include_drafts: bool = False) -> QuerySet[CourseOffering]:
    lesson_filter = {} if include_drafts else {"lessons__status": PublicationStatus.PUBLISHED}
    quiz_filter = {} if include_drafts else {"quizzes__status": PublicationStatus.PUBLISHED}
    lesson_queryset = Lesson.objects.all() if include_drafts else Lesson.objects.filter(status=PublicationStatus.PUBLISHED)
    quiz_queryset = Quiz.objects.all() if include_drafts else Quiz.objects.filter(status=PublicationStatus.PUBLISHED)
    return offerings.select_related(
        "course", "academic_year_level__academic_year", "academic_year_level__level"
    ).annotate(
        published_lesson_count=Count(
            "lessons", filter=Q(**lesson_filter), distinct=True
        ),
        published_quiz_count=Count(
            "quizzes", filter=Q(**quiz_filter), distinct=True
        ),
    ).prefetch_related(
        Prefetch(
            "lessons",
            queryset=lesson_queryset.only(
                "id", "name", "course_offering_id", "status"
            ),
            to_attr="published_lessons",
        ),
        Prefetch(
            "quizzes",
            queryset=quiz_queryset.only(
                "id", "name", "course_offering_id", "status", "opening_date", "closing_date"
            ),
            to_attr="published_quizzes",
        ),
    )


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
) -> CourseOffering:
    offering = CourseOffering.objects.select_related(
        "course", "academic_year_level__academic_year", "academic_year_level__level"
    ).filter(pk=offering_id).first()
    if offering is None:
        raise PermissionDenied(_("You do not have access to this offering."))
    allowed = (
        user_can_write_offering_activity(
            user, offering, allow_management=allow_management
        )
        if write
        else user_can_read_offering(user, offering)
    )
    if not allowed:
        raise PermissionDenied(_("You do not have access to this offering."))
    return offering
