import json
from datetime import date, timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Course, Grade, Lesson, Question, Quiz, Role, Submission, User


def lesson_links():
    return json.dumps([
        {
            "name": "lesson.m3u8",
            "id": "course/lesson/lesson.m3u8",
            "file_type": "video",
        }
    ])


class Phase0StabilizationTests(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role="admin")
        self.teacher_role = Role.objects.create(role="teacher")
        self.junior_role = Role.objects.create(role="junior")
        self.senior_role = Role.objects.create(role="senior")

        self.admin = User.objects.create_user(username="admin", password="1", role=self.admin_role)
        self.teacher = User.objects.create_user(username="teacher", password="1", role=self.teacher_role)
        self.junior = User.objects.create_user(
            username="junior",
            password="1",
            role=self.junior_role,
            joined_date=date(2024, 8, 1),
        )
        self.senior = User.objects.create_user(
            username="senior",
            password="1",
            role=self.senior_role,
            joined_date=date(2023, 8, 1),
        )

        self.level_1_course = Course.objects.create(name="Foundations", level=1)
        self.level_2_course = Course.objects.create(name="Advanced", level=2)

        self.visible_lesson = Lesson.objects.create(
            name="Visible lesson",
            course=self.level_1_course,
            links=lesson_links(),
            created_date=date(2024, 9, 1),
        )
        self.out_of_window_lesson = Lesson.objects.create(
            name="Old lesson",
            course=self.level_1_course,
            links=lesson_links(),
            created_date=date(2022, 9, 1),
        )
        self.level_2_lesson = Lesson.objects.create(
            name="Second year lesson",
            course=self.level_2_course,
            links=lesson_links(),
            created_date=date(2024, 9, 1),
        )

    def login_client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def create_quiz(self, course, opening_date, closing_date):
        quiz = Quiz.objects.create(
            name=f"{course.name} Quiz",
            course=course,
            opening_date=opening_date,
            closing_date=closing_date,
            total_grade=1,
        )
        Question.objects.create(
            quiz=quiz,
            title="Question",
            correct_answer="A",
            question_type="complete",
            choices=json.dumps([]),
            grade=1,
            auto_grade=True,
        )
        return quiz

    def test_lesson_detail_and_stream_require_same_course_and_date_access(self):
        client = self.login_client(self.junior)

        visible_detail = client.get(reverse("lesson-details", args=[self.level_1_course.pk, self.visible_lesson.pk]))
        self.assertEqual(visible_detail.status_code, 200)

        old_detail = client.get(reverse("lesson-details", args=[self.level_1_course.pk, self.out_of_window_lesson.pk]))
        self.assertEqual(old_detail.status_code, 401)

        level_2_detail = client.get(reverse("lesson-details", args=[self.level_2_course.pk, self.level_2_lesson.pk]))
        self.assertEqual(level_2_detail.status_code, 401)

        old_stream = client.get(reverse("lesson-stream", args=[self.out_of_window_lesson.pk, 0]))
        self.assertEqual(old_stream.status_code, 401)

        level_2_stream = client.get(reverse("lesson-stream", args=[self.level_2_lesson.pk, 0]))
        self.assertEqual(level_2_stream.status_code, 401)

    def test_management_users_can_view_quiz_status_without_course_level_errors(self):
        now = timezone.now()
        quiz = self.create_quiz(self.level_2_course, now - timedelta(hours=1), now + timedelta(hours=1))

        for user in [self.admin, self.teacher]:
            client = self.login_client(user)
            response = client.get(reverse("api-quiz-status", args=[self.level_2_course.pk]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["quizzes"], [{"id": quiz.pk, "status": "exam"}])

    def test_quiz_status_and_take_page_both_require_student_academic_window(self):
        now = timezone.now()
        reopened_for_newer_cohort = self.create_quiz(
            self.level_1_course,
            now - timedelta(hours=1),
            now + timedelta(hours=1),
        )
        self.senior.joined_date = date(2020, 8, 1)
        self.senior.save(update_fields=["joined_date"])

        client = self.login_client(self.senior)

        status_response = client.get(reverse("api-quiz-status", args=[self.level_1_course.pk]))
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["quizzes"], [{"id": reopened_for_newer_cohort.pk, "status": "closed_unsolved"}])

        take_response = client.get(reverse("quiz-details", args=[self.level_1_course.pk, reopened_for_newer_cohort.pk]))
        self.assertEqual(take_response.status_code, 200)
        self.assertEqual(take_response.context["mode"], "closed_unsolved")

        post_response = client.post(
            reverse("quiz-details", args=[self.level_1_course.pk, reopened_for_newer_cohort.pk]),
            data={"questions[0][id]": reopened_for_newer_cohort.questions.first().pk, "questions[0][answer]": "A"},
        )
        self.assertEqual(post_response.status_code, 200)
        self.assertContains(post_response, "submission is closed")
        self.assertFalse(Grade.objects.filter(user=self.senior, quiz=reopened_for_newer_cohort).exists())

    def test_student_cannot_retake_after_submission(self):
        now = timezone.now()
        quiz = self.create_quiz(self.level_1_course, now - timedelta(hours=1), now + timedelta(hours=1))
        question = quiz.questions.first()
        Submission.objects.create(user=self.junior, question=question, submitted_answer="A", grade=1)
        Grade.objects.create(user=self.junior, quiz=quiz, total_grade=1)

        client = self.login_client(self.junior)
        response = client.get(reverse("quiz-details", args=[self.level_1_course.pk, quiz.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["mode"], "view")

        post_response = client.post(
            reverse("quiz-details", args=[self.level_1_course.pk, quiz.pk]),
            data={"questions[0][id]": question.pk, "questions[0][answer]": "A"},
        )
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(Grade.objects.filter(user=self.junior, quiz=quiz).count(), 1)
        self.assertEqual(Submission.objects.filter(user=self.junior, question=question).count(), 1)
