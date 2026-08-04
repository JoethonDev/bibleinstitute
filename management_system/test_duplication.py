import json
from datetime import date, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from .models import AcademicYear, Course, CourseOffering, Grade, Lesson, PublicationStatus, Question, Quiz, Role, User


class DuplicationTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="admin")
        self.admin = User.objects.create_user(username="admin", password="1", role=Role.objects.get(role="admin"))
        year = AcademicYear.objects.create(name="2026/2027", level=1, is_current=True, starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30))
        course = Course.objects.create(name="Original", level=1)
        self.offering = CourseOffering.objects.create(course=course, academic_year=year)
        self.lesson = Lesson.objects.create(name="Original Lesson", course=course, course_offering=self.offering, links=json.dumps([{"name": "vid.m3u8", "id": "orig/vid.m3u8", "file_type": "video"}]))
        self.quiz = Quiz.objects.create(name="Original Quiz", course=course, course_offering=self.offering, opening_date=timezone.now(), closing_date=timezone.now() + timedelta(hours=1))
        self.question = Question.objects.create(quiz=self.quiz, title="Q1", correct_answer="A", question_type="complete", choices=json.dumps([]), grade=1, auto_grade=True)

    def test_duplicate_lesson_creates_new_pk(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("lesson-duplicate", args=[self.lesson.id]))
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(Lesson.objects.count(), 2)
        new_lesson = Lesson.objects.exclude(pk=self.lesson.pk).first()
        self.assertEqual(new_lesson.status, PublicationStatus.DRAFT)
        self.assertEqual(new_lesson.course_offering_id, self.lesson.course_offering_id)

    def test_duplicate_quiz_creates_new_pk_and_questions(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("quiz-duplicate", args=[self.quiz.id]))
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(Quiz.objects.count(), 2)
        self.assertEqual(Question.objects.count(), 2)
        new_quiz = Quiz.objects.exclude(pk=self.quiz.pk).first()
        self.assertEqual(new_quiz.status, PublicationStatus.DRAFT)
        self.assertEqual(new_quiz.questions.count(), 1)

    def test_duplicate_preserves_r2_key(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("lesson-duplicate", args=[self.lesson.id]))
        self.assertIn(resp.status_code, (200, 302))
        new_lesson = Lesson.objects.exclude(pk=self.lesson.pk).first()
        self.assertIn("orig/vid.m3u8", new_lesson.links)

    def test_duplicate_does_not_copy_grades(self):
        Grade.objects.create(user=self.admin, quiz=self.quiz, total_grade=1)
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("quiz-duplicate", args=[self.quiz.id]))
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(Grade.objects.count(), 1)

    def test_copy_course_offering(self):
        c = Client()
        c.login(username="admin", password="1")
        year2 = AcademicYear.objects.create(name="2027/2028", level=1, starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30))
        resp = c.post(reverse("copy-course-offering", args=[self.offering.id]), {"academic_year_id": year2.id})
        self.assertIn(resp.status_code, (200, 302))
        self.assertEqual(CourseOffering.objects.count(), 2)
        self.assertEqual(Lesson.objects.count(), 2)
        self.assertEqual(Quiz.objects.count(), 2)
        new_offering = CourseOffering.objects.exclude(pk=self.offering.pk).first()
        self.assertEqual(new_offering.academic_year_id, year2.id)
