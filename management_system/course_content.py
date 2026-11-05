"""Response-specific reads for already-authorized course offerings."""

from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from .models import CourseOffering, Lesson, PublicationStatus, Quiz


def with_offering_content_counts(
    offerings: QuerySet[CourseOffering], *, include_drafts: bool = False
) -> QuerySet[CourseOffering]:
    """Count each child relation separately, without a lesson-by-quiz join."""
    lessons = Lesson.objects.filter(course_offering_id=OuterRef("pk"))
    quizzes = Quiz.objects.filter(course_offering_id=OuterRef("pk"))
    if not include_drafts:
        lessons = lessons.filter(status=PublicationStatus.PUBLISHED)
        quizzes = quizzes.filter(status=PublicationStatus.PUBLISHED)
    lesson_counts = lessons.order_by().values("course_offering_id").annotate(
        total=Count("pk")
    ).values("total")
    quiz_counts = quizzes.order_by().values("course_offering_id").annotate(
        total=Count("pk")
    ).values("total")
    return offerings.annotate(
        published_lesson_count=Coalesce(
            Subquery(lesson_counts, output_field=IntegerField()), Value(0)
        ),
        published_quiz_count=Coalesce(
            Subquery(quiz_counts, output_field=IntegerField()), Value(0)
        ),
    )


def course_navigation(
    offering: CourseOffering, *, include_drafts: bool = False
) -> dict[str, QuerySet]:
    lessons = Lesson.objects.filter(course_offering=offering)
    quizzes = Quiz.objects.filter(course_offering=offering)
    if not include_drafts:
        lessons = lessons.filter(status=PublicationStatus.PUBLISHED)
        quizzes = quizzes.filter(status=PublicationStatus.PUBLISHED)
    return {
        "lessons": lessons.only("id", "name", "course_offering_id"),
        "quizzes": quizzes.only("id", "name", "course_offering_id"),
    }
