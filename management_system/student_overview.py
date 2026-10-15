"""Canonical Student 360 aggregation — one bounded read per section.

Reused by the admin 360 page only. No writes, no R2, no provider calls.
Every list is bounded (<=25) with select_related; counts use SQL aggregation.
"""
from __future__ import annotations

from django.db.models import Count, Q

from .academic_access import accessible_offerings
from .models import (
    AcademicPayment,
    AttendanceRecord,
    Enrollment,
    EvaluationResult,
    Grade,
    LectureProgress,
    MobilePushDevice,
    PromotionHistory,
    PublicationStatus,
    StudentMobileSession,
    StudentNotification,
    TelegramAccount,
    TelegramConversation,
    TelegramMessage,
    User,
)

BOUND = 25


def build_student_overview(student_id: int) -> dict | None:
    """Return bounded 360 payload for a user pk, or None when missing."""
    student = User.objects.select_related("role").filter(pk=student_id).first()
    if student is None:
        return None

    enrollments = list(
        Enrollment.objects.filter(student_id=student_id)
        .select_related("academic_year_level__academic_year", "academic_year_level__level", "course_offering__course")
        .order_by("-academic_year_level__academic_year__ordering", "academic_year_level__level__ordering")[:BOUND]
    )
    grades = list(
        Grade.objects.filter(user_id=student_id)
        .select_related("quiz", "quiz__course_offering__course")
        .order_by("-submitted_at")[:BOUND]
    )
    progress = list(
        LectureProgress.objects.filter(student_id=student_id)
        .select_related("lesson", "lesson__course_offering__course")
        .order_by("-id")[:BOUND]
    )
    attendance = list(
        AttendanceRecord.objects.filter(student_id=student_id)
        .select_related("course_offering__course")
        .order_by("-attendance_date", "-id")[:BOUND]
    )
    attendance_counts = dict(
        AttendanceRecord.objects.filter(student_id=student_id)
        .values("action")
        .annotate(n=Count("id"))
        .values_list("action", "n")
    )
    notifications = list(
        StudentNotification.objects.filter(student_id=student_id)
        .order_by("-created_at")[:BOUND]
    )
    conversations = list(
        TelegramConversation.objects.filter(user_id=student_id)
        .order_by("-last_message_at", "-id")[:BOUND]
    )
    conv_ids = [c.pk for c in conversations]
    messages = list(
        TelegramMessage.objects.filter(conversation_id__in=conv_ids)
        .select_related("conversation")
        .order_by("-created_at")[:BOUND]
    ) if conv_ids else []
    devices = list(
        MobilePushDevice.objects.filter(user_id=student_id).order_by("-last_seen_at")[:BOUND]
    )
    sessions = list(
        StudentMobileSession.objects.filter(user_id=student_id).order_by("-created_at")[:BOUND]
    )
    payments = list(
        AcademicPayment.objects.filter(student_id=student_id)
        .select_related("academic_year_level__academic_year", "academic_year_level__level")[:BOUND]
    )
    promotion_history = list(
        PromotionHistory.objects.filter(student_id=student_id)
        .select_related(
            "source_year_level__academic_year", "source_year_level__level",
            "destination_year_level__academic_year", "destination_year_level__level",
            "actor",
        )[:BOUND]
    )
    evaluation_results = list(
        EvaluationResult.objects.filter(enrollment__student_id=student_id)
        .select_related(
            "formula__academic_year_level__academic_year",
            "formula__academic_year_level__level",
            "course_offering__course",
            "enrollment",
        )
        .order_by("-saved_at")[:BOUND]
    )
    telegram_account = TelegramAccount.objects.select_related("user").filter(user_id=student_id).first()
    # Read-only student-view preview: same authorized offerings the student
    # sees, with published content counts. Rendered as plain text (no links,
    # no actions) so an admin can never act as the student from here.
    readonly_offerings = list(
        accessible_offerings(student)
        .select_related("course", "academic_year_level__academic_year", "academic_year_level__level")
        .annotate(
            published_lesson_count=Count(
                "lessons",
                filter=Q(lessons__status=PublicationStatus.PUBLISHED),
                distinct=True,
            ),
            published_quiz_count=Count(
                "quizzes",
                filter=Q(quizzes__status=PublicationStatus.PUBLISHED),
                distinct=True,
            ),
        )
        .order_by(
            "academic_year_level__academic_year__ordering",
            "academic_year_level__level__ordering",
            "course__name",
        )[:20]
    )
    return {
        "student": student,
        "enrollments": enrollments,
        "grades": grades,
        "progress": progress,
        "attendance": attendance,
        "attendance_counts": attendance_counts,
        "notifications": notifications,
        "conversations": conversations,
        "messages": messages,
        "devices": devices,
        "sessions": sessions,
        "payments": payments,
        "promotion_history": promotion_history,
        "evaluation_results": evaluation_results,
        "telegram_account": telegram_account,
        "readonly_offerings": readonly_offerings,
    }
