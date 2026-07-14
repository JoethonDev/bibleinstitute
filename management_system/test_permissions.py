import json

from django.test import Client, TestCase
from django.urls import reverse
from .models import Course, Lesson, Quiz, Role, User


class PermissionRegressionTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="admin")
        Role.objects.get_or_create(role="staff")
        Role.objects.get_or_create(role="moderator")
        Role.objects.get_or_create(role="student")

        self.admin = User.objects.create_user(username="admin_u", password="1", role=Role.objects.get(role="admin"))
        self.staff = User.objects.create_user(username="staff_u", password="1", role=Role.objects.get(role="staff"))
        self.moderator = User.objects.create_user(username="mod_u", password="1", role=Role.objects.get(role="moderator"))
        self.student = User.objects.create_user(username="stud_u", password="1", role=Role.objects.get(role="student"))

        self.course = Course.objects.create(name="Test Course", level=1)
        self.lesson = Lesson.objects.create(
            name="Test Lesson", course=self.course,
            links=json.dumps([{"name": "test.m3u8", "id": "test/test.m3u8", "file_type": "video"}]),
        )
        self.quiz = Quiz.objects.create(name="Test Quiz", course=self.course)

        self.management_urls = [
            ("manage_content", reverse("course-dashboard")),
            ("manage_content", reverse("lesson-dashboard")),
            ("manage_content", reverse("user-dashboard")),
            ("manage_content", reverse("course-create")),
            ("manage_content", reverse("quiz-create")),
            ("manage_content", reverse("course-update", args=[self.course.id])),
            ("manage_content", reverse("quiz-update", args=[self.quiz.id])),
        ]
        self.manage_only = []
        self.delete_urls = [
            ("delete", reverse("course-delete", args=[self.course.id])),
            ("delete", reverse("lesson-delete", args=[self.lesson.id])),
            ("delete", reverse("quiz-delete", args=[self.quiz.id])),
        ]
        self.grade_urls = [
            ("grade", reverse("submission-dashboard", args=[self.quiz.id])),
        ]
        self.report_urls = [
            ("reports", reverse("yearly-transcript-dashboard")),
        ]

    def _assert_status(self, url, username, expected):
        c = Client()
        c.login(username=username, password="1")
        resp = c.get(url)
        self.assertEqual(resp.status_code, expected, f"{username} @ {url}: got {resp.status_code}, expected {expected}")

    def test_admin_access(self):
        for _, url in self.management_urls + self.manage_only + self.delete_urls + self.grade_urls + self.report_urls:
            self._assert_status(url, "admin_u", 200)

    def test_staff_manage_content(self):
        for _, url in self.management_urls + self.manage_only + self.grade_urls + self.report_urls:
            self._assert_status(url, "staff_u", 200)

    def test_staff_cannot_delete(self):
        for _, url in self.delete_urls:
            self._assert_status(url, "staff_u", 401)

    def test_moderator_denied_all(self):
        for _, url in self.management_urls + self.manage_only + self.delete_urls + self.grade_urls + self.report_urls:
            self._assert_status(url, "mod_u", 401)

    def test_student_denied_all(self):
        for _, url in self.management_urls + self.manage_only + self.delete_urls + self.grade_urls + self.report_urls:
            self._assert_status(url, "stud_u", 401)

    def test_unauthenticated_redirected(self):
        c = Client()
        resp = c.get(reverse("course-dashboard"))
        self.assertIn(resp.status_code, (302, 401))

    def test_public_route_unrestricted(self):
        c = Client()
        resp = c.get(reverse("courses"))
        self.assertEqual(resp.status_code, 302)
        c.login(username="stud_u", password="1")
        resp = c.get(reverse("courses"))
        self.assertEqual(resp.status_code, 200)
