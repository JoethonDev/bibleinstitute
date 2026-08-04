import json
from datetime import date

from django.test import TestCase

from .models import AcademicYear, Course, CourseOffering, Lesson
from .utils.r2_references import rewrite_lesson_r2_references, rewrite_r2_keys


class R2ReferenceTests(TestCase):
    def test_rewrites_nested_file_keys_without_touching_similar_keys(self):
        data = {
            "id": "media/lesson.m3u8",
            "segments": ["media/lesson_000.ts", "media/lesson_001.ts"],
            "note": "media/lesson.m3u8.bak",
        }

        rewritten = rewrite_r2_keys(data, "media/lesson.m3u8", "archive/lesson.m3u8")

        self.assertEqual(rewritten["id"], "archive/lesson.m3u8")
        self.assertEqual(rewritten["segments"], data["segments"])
        self.assertEqual(rewritten["note"], data["note"])

    def test_rewrites_all_keys_under_a_folder(self):
        data = ["media/lesson.m3u8", "media/lesson_000.ts", "other/file.pdf"]

        self.assertEqual(
            rewrite_r2_keys(data, "media/", "archive/", is_folder=True),
            ["archive/lesson.m3u8", "archive/lesson_000.ts", "other/file.pdf"],
        )

    def test_updates_lesson_json_reference(self):
        course = Course.objects.create(name="R2 Course", level=1)
        year = AcademicYear.objects.create(
            name="R2 Year", level=1, starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30)
        )
        offering = CourseOffering.objects.create(course=course, academic_year=year)
        lesson = Lesson.objects.create(
            name="R2 Lesson",
            course=course,
            course_offering=offering,
            links=json.dumps([{"id": "media/lesson.m3u8", "segments": ["media/lesson_000.ts"]}]),
        )

        self.assertEqual(rewrite_lesson_r2_references("media/", "archive/", is_folder=True), 1)

        lesson.refresh_from_db()
        links = json.loads(lesson.links)
        self.assertEqual(links[0]["id"], "archive/lesson.m3u8")
        self.assertEqual(links[0]["segments"], ["archive/lesson_000.ts"])
