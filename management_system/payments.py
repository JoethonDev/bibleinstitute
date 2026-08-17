from django.db.models import Q, QuerySet

from .academic_access import READABLE_ENROLLMENT_STATUSES
from .models import AcademicYear, AcademicYearLevel, Enrollment, User


def academic_payment_scopes_for_student(student: User) -> QuerySet[AcademicYearLevel]:
    """Return missing payment scopes the student may legitimately upload for."""
    active_year = AcademicYear.objects.filter(is_active=True).first()
    if active_year is None:
        return AcademicYearLevel.objects.none()

    current_scope_ids = set(
        Enrollment.objects.filter(
            student=student,
            status__in=READABLE_ENROLLMENT_STATUSES,
            academic_year_level__academic_year=active_year,
        ).values_list("academic_year_level_id", flat=True)
    )
    historical_level_orderings = set(
        Enrollment.objects.filter(
            student=student,
            status__in=READABLE_ENROLLMENT_STATUSES,
            enrollment_type=Enrollment.Type.NORMAL,
            academic_year_level__academic_year__ordering__lt=active_year.ordering,
        ).values_list("academic_year_level__level__ordering", flat=True)
    )
    target_orderings = historical_level_orderings | {
        ordering + 1 for ordering in historical_level_orderings
    }

    candidate_filter = Q(pk__in=current_scope_ids)
    if target_orderings:
        candidate_filter |= Q(level__ordering__in=target_orderings)

    queryset = AcademicYearLevel.objects.filter(
        candidate_filter,
        academic_year=active_year,
    ).exclude(
        academic_payments__student=student,
    ).select_related("academic_year", "level").order_by("level__ordering", "pk")

    # User.payment_key is the intentionally separate signup/initial-payment
    # record. Do not ask a student to upload it again for their first scope.
    if getattr(student, "payment_key", None):
        first_scope = Enrollment.objects.filter(
            student=student,
            enrollment_type=Enrollment.Type.NORMAL,
        ).select_related("academic_year_level").order_by(
            "academic_year_level__academic_year__ordering", "pk"
        ).first()
        if first_scope:
            queryset = queryset.exclude(pk=first_scope.academic_year_level_id)
    return queryset
