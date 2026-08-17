from __future__ import annotations

from django.core.paginator import Page
from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When

from .grade_matrix import grade_matrix_student_queryset
from .models import AcademicPayment, Enrollment


PAYMENT_MATRIX_PAGE_SIZE = 50
PAYMENT_STATUS_VALUES = frozenset({"all", "paid", "unpaid"})


def payment_matrix_student_queryset(scope, *, name: str = "", status: str = "all"):
    """Return a paginated-report-ready roster with payment state in SQL."""
    earlier_normal = Enrollment.objects.filter(
        student=OuterRef("pk"),
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level__academic_year__ordering__lt=scope.academic_year.ordering,
    )
    academic_payment = AcademicPayment.objects.filter(
        student=OuterRef("pk"),
        academic_year_level_id=scope.pk,
    )
    queryset = grade_matrix_student_queryset(scope, name=name).annotate(
        has_academic_payment=Exists(academic_payment),
        has_earlier_normal_enrollment=Exists(earlier_normal),
    ).annotate(
        payment_uploaded=Case(
            When(has_academic_payment=True, then=Value(True)),
            When(
                payment_key__isnull=False,
                has_earlier_normal_enrollment=False,
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        )
    )
    if status == "paid":
        queryset = queryset.filter(payment_uploaded=True)
    elif status == "unpaid":
        queryset = queryset.filter(payment_uploaded=False)
    return queryset


def payment_matrix_page(page: Page, scope) -> list[dict]:
    """Shape one database-paginated payment batch without row queries."""
    student_ids = [student.pk for student in page.object_list]
    receipts = {
        receipt.student_id: receipt
        for receipt in AcademicPayment.objects.filter(
            student_id__in=student_ids,
            academic_year_level=scope,
        )
    }
    rows = []
    for student in page.object_list:
        receipt = receipts.get(student.pk)
        rows.append({
            "student": student,
            "receipt": receipt,
            "paid": bool(student.payment_uploaded),
            "legacy_signup_payment": bool(
                student.payment_uploaded and receipt is None and student.payment_key
            ),
        })
    return rows
