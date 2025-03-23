from django.test import TestCase, Client
from .models import Course, Lesson, User, Role
import json
from datetime import date
from django.urls import reverse


class CourseTest(TestCase):

    def setUp(self):
        # Role
        self.admin_role = Role.objects.create(role="admin")
        self.teacher_role = Role.objects.create(role="teacher")
        self.senior_role = Role.objects.create(role="senior")
        self.junior_role = Role.objects.create(role="junior")

        # User
        self.admin = User.objects.create(username="admin", password="1", role=self.admin_role)
        self.teacher = User.objects.create(username="teacher", password="1", role=self.teacher_role)
        self.senior = User.objects.create(username="senior", password="1", role=self.senior_role, joined_date=date.today().replace(year=2023, month=8))
        self.grad = User.objects.create(username="senior2", password="1", role=self.senior_role, joined_date=date.today().replace(year=2022, month=8))
        self.junior = User.objects.create(username="junior", password="1", role=self.junior_role, joined_date=date.today().replace(year=2024, month=8))

        # Course
        self.course_level_1 = Course.objects.create(name="Course 1", level=1)
        self.course_level_2 = Course.objects.create(name="Course 2", level=2)

        # Lesson
        self.lesson_junior_course_1 = Lesson.objects.create(name="lesson 1 for course 1", course=self.course_level_1,  created_date=date.today())
        self.lesson_senior_course_1 = Lesson.objects.create(name="lesson 1 for course 1", course=self.course_level_1, created_date=date.today().replace(year=2024))
        self.lesson_2_junior_course_1 = Lesson.objects.create(name="lesson 2 for course 1", course=self.course_level_1, created_date=date.today().replace(year=2024, month=10))
        self.lesson_2_senior_course_1 = Lesson.objects.create(name="lesson 2 for course 1", course=self.course_level_1, created_date=date.today().replace(year=2023, month=10))

        self.lesson_senior_course_2 = Lesson.objects.create(name="lesson 1 for course 2", course=self.course_level_2, created_date=date.today())
        self.lesson_grad_course_2 = Lesson.objects.create(name="lesson 1 for course 2", course=self.course_level_2, created_date=date.today().replace(year=2022, month=10))

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

        # Senior & Grad
        senior_res = self.senior.get(course_url)
        grad_res = self.grad.get(course_url)
        senior_context = senior_res.context['courses']
        grad_context = grad_res.context['courses']

        self.assertEqual(len(senior_context), 2)
        self.assertEqual(len(grad_context), 2)

        self.assertEqual(senior_context, courses_2)
        self.assertEqual(grad_context, courses_2)

        # Junior
        junior_res = self.junior.get(course_url)
        junior_context = junior_res.context['courses']
        self.assertEqual(len(junior_context), 1)
        self.assertEqual(junior_context, courses_1)


    def test_lessons_list_access_level_1(self):
        """
            Test checks available lessons for course in level 1
        """
        course_1 = reverse("course-details", args=[1])
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

        # Senior & Grad
        senior_res = self.senior.get(course_1)
        grad_res = self.grad.get(course_1)
        senior_context = list(senior_res.context['lessons'])
        grad_context = list(grad_res.context['lessons'])

        self.assertEqual(len(senior_context), 2)
        self.assertEqual(len(grad_context), 0)

        self.assertEqual(senior_context, [
            self.lesson_senior_course_1,
            self.lesson_2_senior_course_1
        ])
        self.assertEqual(grad_context, [])

        # Junior
        junior_res = self.junior.get(course_1)
        junior_context = list(junior_res.context['lessons'])
        self.assertEqual(len(junior_context), 2)
        self.assertEqual(junior_context, [
            self.lesson_junior_course_1,
            self.lesson_2_junior_course_1
        ])
        
    def test_lessons_list_access_level_2(self):
        """
            Test checks available lessons for course in level 1
        """
        course_2 = reverse("course-details", args=[2])
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

        # Senior & Grad
        senior_res = self.senior.get(course_2)
        grad_res = self.grad.get(course_2)
        senior_context = list(senior_res.context['lessons'])
        grad_context = list(grad_res.context['lessons'])

        self.assertEqual(len(senior_context), 1)
        self.assertEqual(len(grad_context), 1)

        self.assertEqual(senior_context, [
            self.lesson_senior_course_2
        ])
        self.assertEqual(grad_context, [
            self.lesson_grad_course_2
        ])

        # Junior
        junior_res = self.junior.get(course_2)
        self.assertEqual(junior_res.status_code, 401)
