from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from unittest import skipIf

from .models import AcademicYear, Course, CourseOffering, Enrollment, Lesson, Quiz, Role, User


class AcademicModelTests(TestCase):
    def setUp(self):
        self.level_1_year = AcademicYear.objects.create(
            name="2026/2027",
            level=1,
            starts_on=date(2026, 9, 20),
            ends_on=date(2027, 5, 20),
            is_current=True,
        )
        self.level_2_year = AcademicYear.objects.create(
            name="2026/2027",
            level=2,
            starts_on=date(2026, 9, 20),
            ends_on=date(2027, 5, 20),
            is_current=True,
        )
        self.level_1_course = Course.objects.create(name="Foundations", level=1)
        self.level_2_course = Course.objects.create(name="Advanced", level=2)
        self.level_1_offering = CourseOffering.objects.create(
            course=self.level_1_course,
            academic_year=self.level_1_year,
        )
        student_role, _ = Role.objects.get_or_create(role="junior")
        self.student = User.objects.create(username="student", role=student_role)

    def test_same_year_name_is_allowed_for_different_levels(self):
        self.assertEqual(AcademicYear.objects.filter(name="2026/2027").count(), 2)

    def test_year_constraints_reject_duplicates_and_second_current_year(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.create(
                name="2026/2027",
                level=1,
                starts_on=date(2026, 9, 20),
                ends_on=date(2027, 5, 20),
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.create(
                name="2027/2028",
                level=1,
                starts_on=date(2027, 9, 20),
                ends_on=date(2028, 5, 20),
                is_current=True,
            )

    def test_academic_year_end_must_be_after_start(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicYear.objects.create(
                name="Invalid",
                level=1,
                starts_on=date(2027, 5, 20),
                ends_on=date(2026, 9, 20),
            )

    def test_course_offering_rejects_level_mismatch(self):
        with self.assertRaises(ValidationError):
            CourseOffering.objects.create(
                course=self.level_2_course,
                academic_year=self.level_1_year,
            )

    def test_enrollment_target_must_match_type_and_academic_year(self):
        with self.assertRaises(ValidationError):
            Enrollment.objects.create(
                student=self.student,
                academic_year=self.level_1_year,
                enrollment_type=Enrollment.Type.REPEAT,
            )

        with self.assertRaises(ValidationError):
            Enrollment.objects.create(
                student=self.student,
                academic_year=self.level_2_year,
                course_offering=self.level_1_offering,
                enrollment_type=Enrollment.Type.REPEAT,
            )

    def test_normal_enrollment_grants_the_full_academic_year(self):
        enrollment = Enrollment.objects.create(
            student=self.student,
            academic_year=self.level_1_year,
        )

        self.assertIsNone(enrollment.course_offering)
        self.assertEqual(enrollment.enrollment_type, Enrollment.Type.NORMAL)

    def test_historical_enrollment_preserved_without_new_offering(self):
        previous_year = AcademicYear.objects.create(
            name="2025/2026",
            level=1,
            starts_on=date(2025, 9, 20),
            ends_on=date(2026, 5, 20),
        )
        previous_offering = CourseOffering.objects.create(
            course=self.level_1_course,
            academic_year=previous_year,
        )
        Enrollment.objects.create(
            student=self.student,
            academic_year=self.level_1_year,
        )

        offerings = CourseOffering.objects.filter(
            academic_year__enrollments__student=self.student,
            academic_year__enrollments__status="active",
        )
        self.assertIn(self.level_1_offering, offerings)
        self.assertNotIn(previous_offering, offerings)

    def test_repeat_enrollment_grants_only_selected_offering(self):
        remedial_course = Course.objects.create(name="Remedial Math", level=1)
        remedial_offering = CourseOffering.objects.create(
            course=remedial_course,
            academic_year=self.level_1_year,
        )
        Enrollment.objects.create(
            student=self.student,
            academic_year=self.level_1_year,
            course_offering=remedial_offering,
            enrollment_type=Enrollment.Type.REPEAT,
        )

        active_enrollments = Enrollment.objects.filter(
            student=self.student, status="active",
        )
        repeat_enf = active_enrollments.get(enrollment_type=Enrollment.Type.REPEAT)
        self.assertEqual(repeat_enf.course_offering, remedial_offering)

    def test_lesson_rejects_offering_from_different_course(self):
        other_course = Course.objects.create(name="Other Course", level=1)
        other_offering = CourseOffering.objects.create(
            course=other_course,
            academic_year=self.level_1_year,
        )
        with self.assertRaises(ValidationError):
            Lesson.objects.create(
                name="Misplaced",
                course=self.level_1_course,
                links="[]",
                course_offering=other_offering,
            )

    def test_quiz_rejects_offering_from_different_course(self):
        other_course = Course.objects.create(name="Other Quiz Course", level=1)
        other_offering = CourseOffering.objects.create(
            course=other_course,
            academic_year=self.level_1_year,
        )
        with self.assertRaises(ValidationError):
            Quiz.objects.create(
                name="Misplaced Quiz",
                course=self.level_1_course,
                course_offering=other_offering,
                opening_date=timezone.now(),
                closing_date=timezone.now(),
            )


class AcademicSchemaMigrationTests(TransactionTestCase):
    migrate_from = [("management_system", "0018_align_question_type_choices")]
    migrate_to = [("management_system", "0019_academic_year_schema")]

    @skipIf(connection.vendor == 'postgresql', 'MigrationExecutor DDL conflicts with PostgreSQL transactions')
    def test_existing_content_is_published_without_guessing_an_offering(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        course = old_apps.get_model("management_system", "Course").objects.create(
            name="Legacy Course",
            level=1,
        )
        lesson = old_apps.get_model("management_system", "Lesson").objects.create(
            name="Legacy Lesson",
            course=course,
            links="[]",
        )
        quiz = old_apps.get_model("management_system", "Quiz").objects.create(
            name="Legacy Quiz",
            course=course,
            opening_date=timezone.now(),
            closing_date=timezone.now(),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps

        migrated_lesson = new_apps.get_model("management_system", "Lesson").objects.get(pk=lesson.pk)
        migrated_quiz = new_apps.get_model("management_system", "Quiz").objects.get(pk=quiz.pk)
        self.assertEqual(migrated_lesson.status, "published")
        self.assertEqual(migrated_quiz.status, "published")
        self.assertIsNone(migrated_lesson.course_offering_id)
        self.assertIsNone(migrated_quiz.course_offering_id)
