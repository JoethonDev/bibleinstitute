"""Bounded, authorization-aware data functions shared by student clients."""

from __future__ import annotations

import json
import random
from datetime import timedelta

from django.core.paginator import Page, Paginator
from django.db.models import Exists, F, OuterRef, Prefetch, QuerySet, Window
from django.db.models.functions import RowNumber
from django.utils.timezone import now

from ..academic_access import (
    TARGETED_ENROLLMENT_TYPES,
    active_year_published_offerings_for_user,
    accessible_offerings,
    user_can_write_offering_activity,
)
from ..models import (
    AcademicHoliday,
    AcademicYearLevelMeeting,
    Enrollment,
    Grade,
    LectureProgress,
    Lesson,
    PublicationStatus,
    Question,
    Quiz,
    QuizStudentOpening,
    StudentNotification,
    Submission,
    User,
)
from .timezones import ensure_aware


PAGE_SIZE = 20


def student_offerings_queryset(user: User) -> QuerySet:
    """Return bounded published offerings readable by a mobile account."""
    if getattr(getattr(user, "role", None), "role", None) in {"admin", "staff"}:
        offerings = active_year_published_offerings_for_user(user)
    else:
        offerings = accessible_offerings(user)
    return offerings.prefetch_related(None).order_by(
        "-academic_year_level__academic_year__ordering",
        "academic_year_level__level__ordering",
        "course__name",
        "pk",
    )


def _offering_access_annotations(queryset: QuerySet, user: User) -> QuerySet:
    normal = Enrollment.objects.filter(
        student=user,
        status__in=(Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED),
        enrollment_type=Enrollment.Type.NORMAL,
        academic_year_level_id=OuterRef("academic_year_level_id"),
    )
    targeted = Enrollment.objects.filter(
        student=user,
        status__in=(Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED),
        enrollment_type__in=TARGETED_ENROLLMENT_TYPES,
        course_offering_id=OuterRef("pk"),
    )
    return queryset.annotate(
        student_has_normal_access=Exists(normal),
        student_has_targeted_access=Exists(targeted),
    )


def _access_kind(offering) -> str:
    if getattr(offering, "student_has_targeted_access", False) and not getattr(
        offering, "student_has_normal_access", False
    ):
        return "targeted"
    if offering.academic_year_level.academic_year.is_active:
        return "current"
    return "historical"


def _parse_links(lesson: Lesson) -> list[dict]:
    try:
        links = json.loads(lesson.links or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return links if isinstance(links, list) else []


def _media_data(link: dict, index: int) -> dict:
    file_type = link.get("file_type") or link.get("type") or ""
    return {
        "index": index,
        "type": file_type,
        "part_id": str(link.get("part_id") or ""),
        "name": link.get("name") or link.get("title") or "",
        "streamable": file_type in {"video", "audio"} and bool(link.get("id")),
        "downloadable": file_type in {"audio", "book"},
    }


def _lesson_data(lesson: Lesson) -> dict:
    links = _parse_links(lesson)
    return {
        "id": lesson.pk,
        "name": lesson.name,
        "description": lesson.description or "",
        "status": lesson.status,
        "updated_at": lesson.updated_date.isoformat() if hasattr(lesson, "updated_date") else None,
        "media": [_media_data(link, index) for index, link in enumerate(links) if isinstance(link, dict)],
    }


def _quiz_summary(quiz: Quiz) -> dict:
    quiz_type = quiz.quiz_type
    return {
        "id": quiz.pk,
        "name": quiz.name,
        "status": quiz.status,
        "quiz_type": quiz_type.code if quiz_type else None,
        "quiz_type_name": quiz_type.name_en if quiz_type else "",
        "opening_at": quiz.opening_date.isoformat() if quiz.opening_date else None,
        "closing_at": quiz.closing_date.isoformat() if quiz.closing_date else None,
        "total_grade": quiz.total_grade,
    }


def _localized_quiz_summary(quiz: Quiz, language: str) -> dict:
    result = _quiz_summary(quiz)
    if quiz.quiz_type:
        result["quiz_type_name"] = quiz.quiz_type.name_ar if language == "ar" else quiz.quiz_type.name_en
    return result


def _offering_data(offering, language: str, include_content: bool = True) -> dict:
    year = offering.academic_year_level.academic_year
    level = offering.academic_year_level.level
    result = {
        "id": offering.pk,
        "course": {
            "id": offering.course_id,
            "name": offering.course.name,
            "description": offering.course.description or "",
        },
        "academic_year": {"id": year.pk, "name": year.name, "is_active": year.is_active},
        "level": {
            "id": level.pk,
            "ordering": level.ordering,
            "name": level.name_ar if language == "ar" else level.name_en,
        },
        "access_kind": _access_kind(offering),
        "instructor": offering.instructor or "",
    }
    if include_content:
        lessons = getattr(offering, "published_lessons", None)
        quizzes = getattr(offering, "published_quizzes", None)
        if lessons is None:
            lessons = list(Lesson.objects.filter(
                course_offering=offering, status=PublicationStatus.PUBLISHED
            ).order_by("created_date", "pk")[:PAGE_SIZE])
        if quizzes is None:
            quizzes = list(Quiz.objects.filter(
                course_offering=offering, status=PublicationStatus.PUBLISHED
            ).select_related("quiz_type").order_by("opening_date", "pk")[:PAGE_SIZE])
        result["lessons"] = [_lesson_data(lesson) for lesson in lessons[:PAGE_SIZE]]
        result["quizzes"] = [_localized_quiz_summary(quiz, language) for quiz in quizzes[:PAGE_SIZE]]
    return result


def student_courses_data(user: User, language: str) -> list[dict]:
    lesson_queryset = Lesson.objects.filter(status=PublicationStatus.PUBLISHED).annotate(
        mobile_row=Window(
            expression=RowNumber(),
            partition_by=[F("course_offering_id")],
            order_by=[F("created_date").asc(), F("pk").asc()],
        )
    ).filter(mobile_row__lte=PAGE_SIZE).only(
        "id", "name", "description", "links", "course_offering_id", "status", "updated_date"
    ).order_by("course_offering_id", "created_date", "pk")
    quiz_queryset = Quiz.objects.filter(status=PublicationStatus.PUBLISHED).select_related("quiz_type").annotate(
        mobile_row=Window(
            expression=RowNumber(),
            partition_by=[F("course_offering_id")],
            order_by=[F("opening_date").asc(), F("pk").asc()],
        )
    ).filter(mobile_row__lte=PAGE_SIZE).only(
        "id", "name", "quiz_type_id", "course_offering_id", "status", "opening_date", "closing_date", "total_grade",
        "quiz_type__code", "quiz_type__name_en", "quiz_type__name_ar",
    ).order_by("course_offering_id", "opening_date", "pk")
    offerings = _offering_access_annotations(
        student_offerings_queryset(user).prefetch_related(
            Prefetch("lessons", queryset=lesson_queryset, to_attr="published_lessons"),
            Prefetch("quizzes", queryset=quiz_queryset, to_attr="published_quizzes"),
        ), user
    )[:PAGE_SIZE]
    return [_offering_data(offering, language) for offering in offerings]


def student_course_data(user: User, offering_id: int, language: str) -> dict | None:
    lesson_queryset = Lesson.objects.filter(status=PublicationStatus.PUBLISHED).annotate(
        mobile_row=Window(
            expression=RowNumber(),
            partition_by=[F("course_offering_id")],
            order_by=[F("created_date").asc(), F("pk").asc()],
        )
    ).filter(mobile_row__lte=PAGE_SIZE).only(
        "id", "name", "description", "links", "course_offering_id", "status", "updated_date"
    ).order_by("course_offering_id", "created_date", "pk")
    quiz_queryset = Quiz.objects.filter(status=PublicationStatus.PUBLISHED).select_related("quiz_type").annotate(
        mobile_row=Window(
            expression=RowNumber(),
            partition_by=[F("course_offering_id")],
            order_by=[F("opening_date").asc(), F("pk").asc()],
        )
    ).filter(mobile_row__lte=PAGE_SIZE).only(
        "id", "name", "quiz_type_id", "course_offering_id", "status", "opening_date", "closing_date", "total_grade",
        "quiz_type__code", "quiz_type__name_en", "quiz_type__name_ar",
    ).order_by("course_offering_id", "opening_date", "pk")
    offering = _offering_access_annotations(
        student_offerings_queryset(user).filter(pk=offering_id).prefetch_related(
            Prefetch("lessons", queryset=lesson_queryset, to_attr="published_lessons"),
            Prefetch("quizzes", queryset=quiz_queryset, to_attr="published_quizzes"),
        ), user
    ).first()
    return _offering_data(offering, language) if offering else None


def _authorized_lesson(user: User, offering_id: int, lesson_id: int) -> Lesson | None:
    if not student_offerings_queryset(user).filter(pk=offering_id).exists():
        return None
    return Lesson.objects.filter(
        pk=lesson_id,
        course_offering_id=offering_id,
        status=PublicationStatus.PUBLISHED,
    ).select_related(
        "course_offering__course",
        "course_offering__academic_year_level__academic_year",
        "course_offering__academic_year_level__level",
    ).first()


def student_lesson_data(user: User, offering_id: int, lesson_id: int) -> dict | None:
    lesson = _authorized_lesson(user, offering_id, lesson_id)
    return _lesson_data(lesson) if lesson else None


def _question_payload(question: Question, exam_mode: bool) -> dict:
    choices = question.get_choices_list()
    config = question.get_config()
    payload = {
        "id": question.pk,
        "title": question.title,
        "type": question.question_type,
        "grade": question.grade,
        "choices": choices,
        "config": {},
    }
    if exam_mode and question.question_type == "order_events":
        payload["items"] = list(config.get("items", []))
        random.shuffle(payload["items"])
    if exam_mode and question.question_type == "match_related":
        payload["pairs"] = [{"left": pair.get("left", "")} for pair in config.get("pairs", [])]
        payload["right_options"] = [pair.get("right", "") for pair in config.get("pairs", [])]
        random.shuffle(payload["right_options"])
    if not exam_mode and question.question_type == "order_events":
        payload["items"] = list(config.get("items", []))
    if not exam_mode and question.question_type == "match_related":
        payload["pairs"] = [{"left": pair.get("left", "")} for pair in config.get("pairs", [])]
    return payload


def _submitted_question_payload(question: Question, submission: Submission | None) -> dict:
    payload = _question_payload(question, False)
    payload.update({
        "submitted_answer": (
            question.get_submitted_answer_payload(submission.submitted_answer)
            if submission is not None
            else None
        ),
        "grade_awarded": submission.grade if submission is not None and submission.is_graded else None,
        "is_graded": bool(submission is not None and submission.is_graded),
    })
    return payload


def _quiz_mode(
    quiz: Quiz,
    user: User,
    grade: Grade | None,
    opening=None,
    can_write: bool | None = None,
) -> str:
    if grade:
        return "view"
    if quiz.status == PublicationStatus.PUBLISHED and opening:
        current = now()
        if ensure_aware(opening.opening_date) <= current <= ensure_aware(opening.closing_date) + timedelta(minutes=30):
            if can_write is None:
                can_write = user_can_write_offering_activity(
                    user, quiz.course_offering, allow_management=True
                )
            if can_write:
                return "exam"
    return "closed_unsolved"


def student_quiz_data(user: User, offering_id: int, quiz_id: int, language: str) -> dict | None:
    if not student_offerings_queryset(user).filter(pk=offering_id).exists():
        return None
    quiz = Quiz.objects.filter(
        pk=quiz_id,
        course_offering_id=offering_id,
        status=PublicationStatus.PUBLISHED,
    ).select_related(
        "course_offering__academic_year_level__academic_year",
        "course_offering__academic_year_level__level",
        "quiz_type",
    ).first()
    if quiz is None:
        return None
    grade = Grade.objects.filter(user=user, quiz=quiz).first()
    opening = QuizStudentOpening.objects.filter(quiz=quiz, student=user).first()
    if opening is None:
        opening = type("Opening", (), {"opening_date": quiz.opening_date, "closing_date": quiz.closing_date})()
    can_write = user_can_write_offering_activity(
        user, quiz.course_offering, allow_management=True
    )
    mode = _quiz_mode(quiz, user, grade, opening, can_write)
    questions = list(Question.objects.filter(quiz=quiz).order_by("pk"))
    if grade:
        submitted = {
            row.question_id: row
            for row in Submission.objects.filter(user=user, question__quiz=quiz).select_related("question")
        }
        question_data = [_submitted_question_payload(question, submitted.get(question.pk)) for question in questions]
    else:
        question_data = [_question_payload(question, mode == "exam") for question in questions]
    return {
        "quiz": _localized_quiz_summary(quiz, language),
        "mode": mode,
        "grade": grade.total_grade if grade else None,
        "effective_opening_at": opening.opening_date.isoformat(),
        "effective_closing_at": opening.closing_date.isoformat(),
        "questions": question_data,
    }


def student_quiz_statuses(user: User, offering_id: int) -> list[dict] | None:
    if not student_offerings_queryset(user).filter(pk=offering_id).exists():
        return None
    quizzes = list(Quiz.objects.filter(
        course_offering_id=offering_id,
        status=PublicationStatus.PUBLISHED,
    ).select_related(
        "course_offering__academic_year_level__academic_year",
        "course_offering__academic_year_level__level",
    ))
    grades = {
        row.quiz_id: row
        for row in Grade.objects.filter(user=user, quiz_id__in=[quiz.pk for quiz in quizzes])
    }
    openings = {
        row.quiz_id: row
        for row in QuizStudentOpening.objects.filter(student=user, quiz_id__in=[quiz.pk for quiz in quizzes])
    }
    can_write = (
        user_can_write_offering_activity(
            user, quizzes[0].course_offering, allow_management=True
        )
        if quizzes
        else False
    )
    result = []
    for quiz in quizzes:
        opening = openings.get(quiz.pk)
        if opening is None:
            opening = type("Opening", (), {"opening_date": quiz.opening_date, "closing_date": quiz.closing_date})()
        result.append({"id": quiz.pk, "status": _quiz_mode(quiz, user, grades.get(quiz.pk), opening, can_write)})
    return result


def student_profile_data(user: User) -> dict:
    role = getattr(getattr(user, "role", None), "role", None)
    return {
        "id": user.pk,
        "username": user.username,
        "role": role,
        "academic_activity_writable": Enrollment.objects.filter(
            student=user,
            status=Enrollment.Status.ACTIVE,
        ).exists(),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name(),
        "phone": user.phone or "",
        "country": user.country or "",
        "city": user.city or "",
        "education_or_job": user.education_or_job or "",
        "service": user.service or "",
        "study_mode": user.study_mode,
        "time_zone": user.time_zone,
        "identity_type": user.identity_type,
        "identity_number": user.identity_number or "",
        "documents": {
            "identity_front": bool(user.identity_front_key),
            "identity_back": bool(user.identity_back_key),
            "payment": bool(user.payment_key),
            "profile_image": bool(user.profile_image_key),
        },
        "qr_available": bool(user.qr_token),
    }


def student_calendar_data(user: User, language: str = "en") -> dict:
    enrollments = Enrollment.objects.filter(
        student=user,
        status__in=(Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED),
        enrollment_type=Enrollment.Type.NORMAL,
    ).select_related("academic_year_level__academic_year", "academic_year_level__level")
    active_year = enrollments.filter(
        academic_year_level__academic_year__is_active=True
    ).order_by("-enrolled_at").first()
    selected = active_year or enrollments.order_by(
        "-academic_year_level__academic_year__ordering", "-enrolled_at"
    ).first()
    if selected is None:
        return {"scope": None, "meetings": [], "holidays": []}
    scope = selected.academic_year_level
    meetings = AcademicYearLevelMeeting.objects.filter(
        academic_year_level=scope
    ).select_related("course_offering__course").order_by("meeting_date", "pk")
    holidays = AcademicHoliday.objects.filter(
        academic_year=scope.academic_year
    ).order_by("date", "pk")
    return {
        "scope": {
            "id": scope.pk,
            "academic_year": scope.academic_year.name,
            "level": scope.level.name_ar if language == "ar" else scope.level.name_en,
            "level_ordering": scope.level.ordering,
            "meeting_weekdays": scope.meeting_weekdays or [],
        },
        "meetings": [
            {"date": row.meeting_date.isoformat(), "offering_id": row.course_offering_id, "course": row.course_offering.course.name}
            for row in meetings
        ],
        "holidays": [{"date": holiday.date.isoformat(), "name": holiday.name} for holiday in holidays],
    }


def student_progress_data(
    user: User,
    offering_ids: list[int] | None = None,
    page: int = 1,
) -> tuple[Page, list[dict]]:
    offerings = student_offerings_queryset(user)
    if offering_ids:
        offerings = offerings.filter(pk__in=offering_ids)
    rows = LectureProgress.objects.filter(
        student=user,
        lesson__course_offering__in=offerings,
    ).select_related("lesson__course_offering__course").order_by("lesson_id", "part_id")
    page_obj = Paginator(rows, PAGE_SIZE).get_page(page)
    return page_obj, [
        {
            "lesson_id": row.lesson_id,
            "lesson": row.lesson.name,
            "offering_id": row.lesson.course_offering_id,
            "part_id": row.part_id,
            "percent": max(0, min(int(row.percent or 0), 100)),
            "unique_seconds": max(0, int(row.unique_seconds or 0)),
            "completed": row.completed_at is not None,
        }
        for row in page_obj.object_list
    ]


def student_grades_queryset(user: User) -> QuerySet:
    offerings = student_offerings_queryset(user).values("pk")
    return Grade.objects.filter(
        user=user,
        quiz__course_offering_id__in=offerings,
    ).select_related("quiz__course_offering__course", "quiz__quiz_type").order_by("-submitted_at", "-pk")


def student_notification_data(user: User, language: str, page: int) -> tuple[Page, int]:
    queryset = StudentNotification.objects.filter(
        student=user,
        cancelled_at__isnull=True,
        scheduled_for__lte=now(),
    ).order_by("-created_at", "-pk")
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(page)
    unread_count = StudentNotification.objects.filter(
        student=user,
        cancelled_at__isnull=True,
        scheduled_for__lte=now(),
        read_at__isnull=True,
    ).count()
    return page_obj, unread_count


def notification_payload(notification: StudentNotification, language: str) -> dict:
    return {
        "id": notification.pk,
        "type": notification.notification_type,
        "title": notification.title_ar if language == "ar" else notification.title_en,
        "body": notification.body_ar if language == "ar" else notification.body_en,
        "navigation": {
            "type": notification.navigation_type,
            "offering_id": notification.offering_id,
            "entity_id": notification.entity_id,
        },
        "scheduled_for": notification.scheduled_for.isoformat(),
        "effective_opening_at": (
            notification.effective_opening_at.isoformat()
            if notification.effective_opening_at else None
        ),
        "effective_closing_at": (
            notification.effective_closing_at.isoformat()
            if notification.effective_closing_at else None
        ),
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat(),
    }
