from django.test import TestCase, Client
from .models import AcademicYear, Course, CourseOffering, Lesson, User, Role
import json
from datetime import date
from django.urls import reverse


class CourseTest(TestCase):

    def setUp(self):
        lesson_links = json.dumps([{
            "name": "lesson.m3u8",
            "id": "course/lesson/lesson.m3u8",
            "file_type": "video",
        }])
        base_date = date(2025, 6, 11)

        # Role
        self.admin_role, _ = Role.objects.get_or_create(role="admin")
        self.teacher_role, _ = Role.objects.get_or_create(role="staff")
        self.senior_role, _ = Role.objects.get_or_create(role="senior")
        self.junior_role, _ = Role.objects.get_or_create(role="junior")

        # User
        self.admin = User.objects.create(username="admin", password="1", role=self.admin_role)
        self.teacher = User.objects.create(username="staff_user", password="1", role=self.teacher_role)
        self.senior = User.objects.create(username="senior", password="1", role=self.senior_role, joined_date=date(2023, 8, 11))
        self.grad = User.objects.create(username="senior2", password="1", role=self.senior_role, joined_date=date(2022, 8, 11))
        self.junior = User.objects.create(username="junior", password="1", role=self.junior_role, joined_date=date(2024, 8, 11))

        # Course
        self.course_level_1 = Course.objects.create(name="Course 1", level=1)
        self.course_level_2 = Course.objects.create(name="Course 2", level=2)

        year1 = AcademicYear.objects.create(name="2026/2027", level=1, is_current=True, starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30))
        year2 = AcademicYear.objects.create(name="2026/2027", level=2, is_current=True, starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30))
        off1 = CourseOffering.objects.create(course=self.course_level_1, academic_year=year1)
        off2 = CourseOffering.objects.create(course=self.course_level_2, academic_year=year2)

        # Lesson
        self.lesson_junior_course_1 = Lesson.objects.create(name="lesson 1 for course 1", course=self.course_level_1, course_offering=off1, links=lesson_links, created_date=base_date)
        self.lesson_senior_course_1 = Lesson.objects.create(name="lesson 1 for course 1", course=self.course_level_1, course_offering=off1, links=lesson_links, created_date=date(2024, 6, 11))
        self.lesson_2_junior_course_1 = Lesson.objects.create(name="lesson 2 for course 1", course=self.course_level_1, course_offering=off1, links=lesson_links, created_date=date(2024, 10, 11))
        self.lesson_2_senior_course_1 = Lesson.objects.create(name="lesson 2 for course 1", course=self.course_level_1, course_offering=off1, links=lesson_links, created_date=date(2023, 10, 11))

        self.lesson_senior_course_2 = Lesson.objects.create(name="lesson 1 for course 2", course=self.course_level_2, course_offering=off2, links=lesson_links, created_date=base_date)
        self.lesson_grad_course_2 = Lesson.objects.create(name="lesson 1 for course 2", course=self.course_level_2, course_offering=off2, links=lesson_links, created_date=date(2022, 10, 11))

        # Clients
        self.admin = self.login_client(self.admin)
        self.teacher = self.login_client(self.teacher)
        self.junior = self.login_client(self.junior)
        self.senior = self.login_client(self.senior)
        self.grad = self.login_client(self.grad)


    def login_client(self, user): 
        client = Client()
        client.force_login(user=user)
        
        return client


    def test_course_list_access(self):
        """
            Test checks available courses for each role 
        """
        # Course URL
        course_url = reverse("courses")
        courses_1 = Course.retrieve_courses_for_level(Course, 1)
        courses_2 = Course.retrieve_courses_for_level(Course, 2)

        # Admin&Teacher Check
        admin_res = self.admin.get(course_url)
        teacher_res = self.teacher.get(course_url)
        admin_context = admin_res.context['courses']
        teacher_context = teacher_res.context['courses']

        self.assertEqual(len(admin_context), 2)
        self.assertEqual(len(teacher_context), 2)

        self.assertEqual(admin_context, courses_2)
        self.assertEqual(teacher_context, courses_2)

        # Senior & Grad — no enrollment yet, expect empty
        senior_res = self.senior.get(course_url)
        grad_res = self.grad.get(course_url)
        senior_context = senior_res.context['courses']
        grad_context = grad_res.context['courses']

        self.assertEqual(len(senior_context), 0)
        self.assertEqual(len(grad_context), 0)

        # Junior — no enrollment yet, expect empty
        junior_res = self.junior.get(course_url)
        junior_context = junior_res.context['courses']
        self.assertEqual(len(junior_context), 0)


    def test_lessons_list_access_level_1(self):
        """
            Test checks available lessons for course in level 1
        """
        course_1 = reverse("course-details", args=[self.course_level_1.pk])
        all_lessons = set(self.course_level_1.lessons.all())

        # Admin&Teacher Check
        admin_res = self.admin.get(course_1)
        teacher_res = self.teacher.get(course_1)
        admin_context = set(admin_res.context['lessons'])
        teacher_context = set(teacher_res.context['lessons'])

        self.assertEqual(len(admin_context), 4)
        self.assertEqual(len(teacher_context), 4)

        self.assertEqual(admin_context, all_lessons)
        self.assertEqual(teacher_context, all_lessons)

        # Senior & Grad — no enrollment, expect 401
        senior_res = self.senior.get(course_1)
        grad_res = self.grad.get(course_1)
        self.assertEqual(senior_res.status_code, 401)
        self.assertEqual(grad_res.status_code, 401)

        # Junior — no enrollment, expect 401
        junior_res = self.junior.get(course_1)
        self.assertEqual(junior_res.status_code, 401)
        
    def test_lessons_list_access_level_2(self):
        """
            Test checks available lessons for course in level 1
        """
        course_2 = reverse("course-details", args=[self.course_level_2.pk])
        all_lessons = set(self.course_level_2.lessons.all())

        # Admin&Teacher Check
        admin_res = self.admin.get(course_2)
        teacher_res = self.teacher.get(course_2)
        admin_context = set(admin_res.context['lessons'])
        teacher_context = set(teacher_res.context['lessons'])

        self.assertEqual(len(admin_context), 2)
        self.assertEqual(len(teacher_context), 2)

        self.assertEqual(admin_context, all_lessons)
        self.assertEqual(teacher_context, all_lessons)

        # Senior & Grad — no enrollment, expect 401
        senior_res = self.senior.get(course_2)
        grad_res = self.grad.get(course_2)
        self.assertEqual(senior_res.status_code, 401)
        self.assertEqual(grad_res.status_code, 401)

        # Junior
        junior_res = self.junior.get(course_2)
        self.assertEqual(junior_res.status_code, 401)

    def test_view_course_details_missing_course_returns_404_not_500(self):
        missing_pk = self.course_level_2.pk + 1000
        url = reverse("course-details", args=[missing_pk])

        response = self.junior.get(url)

        self.assertEqual(response.status_code, 404)
