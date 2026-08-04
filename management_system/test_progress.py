import json
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicYear, Course, CourseOffering, LectureProgress, Lesson,
    Role, User, VerifiedSegmentRequest, ViewingSession,
)
from .utils.progress_merge import calculate_percent, intersect_verified, merge_ranges, unique_seconds


def _make_course_and_lesson():
    course = Course.objects.create(name="TestCourse", level=1)
    year = AcademicYear.objects.create(
        name="2026/2027", level=1, is_current=True,
        starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30),
    )
    offering = CourseOffering.objects.create(course=course, academic_year=year)
    lesson = Lesson.objects.create(name="TestLesson", course=course, links="[]", course_offering=offering)
    return course, lesson


class MergeRangesTests(TestCase):
    def test_disjoint_ranges(self):
        self.assertEqual(merge_ranges([(0, 5), (10, 15)]), [(0, 5), (10, 15)])

    def test_overlapping_ranges(self):
        self.assertEqual(merge_ranges([(0, 10), (5, 15)]), [(0, 15)])

    def test_touching_ranges(self):
        self.assertEqual(merge_ranges([(0, 10), (10, 20)]), [(0, 20)])

    def test_nested_ranges(self):
        self.assertEqual(merge_ranges([(0, 20), (5, 10)]), [(0, 20)])

    def test_empty(self):
        self.assertEqual(merge_ranges([]), [])


class UniqueSecondsTests(TestCase):
    def test_single_range(self):
        self.assertEqual(unique_seconds([(0, 10)]), 10)

    def test_overlapping(self):
        self.assertEqual(unique_seconds([(0, 10), (5, 15)]), 15)

    def test_disjoint(self):
        self.assertEqual(unique_seconds([(0, 5), (10, 15)]), 10)


class CalculatePercentTests(TestCase):
    def test_zero(self):
        self.assertEqual(calculate_percent(0, 100), 0)

    def test_fifty(self):
        self.assertEqual(calculate_percent(50, 100), 50)

    def test_complete(self):
        self.assertEqual(calculate_percent(100, 100), 100)

    def test_capped(self):
        self.assertEqual(calculate_percent(150, 100), 100)

    def test_zero_duration(self):
        self.assertEqual(calculate_percent(50, 0), 0)


class IntersectVerifiedTests(TestCase):
    def test_full_overlap(self):
        player = [(0, 100)]
        verified = [{"key": "s1.ts", "start": 0, "end": 100}]
        self.assertEqual(intersect_verified(player, verified), [(0, 100)])

    def test_partial_overlap(self):
        player = [(0, 50)]
        verified = [{"key": "s1.ts", "start": 25, "end": 75}]
        self.assertEqual(intersect_verified(player, verified), [(25, 50)])

    def test_no_overlap(self):
        player = [(0, 10)]
        verified = [{"key": "s1.ts", "start": 20, "end": 30}]
        self.assertEqual(intersect_verified(player, verified), [])

    def test_empty_player(self):
        self.assertEqual(intersect_verified([], [{"key": "s1.ts", "start": 0, "end": 10}]), [])

    def test_empty_verified(self):
        self.assertEqual(intersect_verified([(0, 10)], []), [])


class ViewingSessionTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        Role.objects.get_or_create(role="admin")
        self.student = User.objects.create_user(username="stud", password="1", role=Role.objects.get(role="student"))
        _, self.lesson = _make_course_and_lesson()

    def test_create_session(self):
        session = ViewingSession.objects.create(
            student=self.student, lesson=self.lesson, part_id="abc",
            session_id="sess1", expires_at=timezone.now() + timedelta(hours=2),
        )
        self.assertEqual(session.student, self.student)

    def test_session_expiry(self):
        session = ViewingSession.objects.create(
            student=self.student, lesson=self.lesson, part_id="abc",
            session_id="sess2", expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(timezone.now() > session.expires_at)


class VerifiedSegmentRequestTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        self.student = User.objects.create_user(username="stud2", password="1", role=Role.objects.get(role="student"))
        _, self.lesson = _make_course_and_lesson()
        self.session = ViewingSession.objects.create(
            student=self.student, lesson=self.lesson, part_id="abc",
            session_id="sess3", expires_at=timezone.now() + timedelta(hours=2),
        )

    def test_create_verified_request(self):
        vr = VerifiedSegmentRequest.objects.create(session=self.session, segment_key="seg1.ts")
        self.assertEqual(vr.segment_key, "seg1.ts")

    def test_duplicate_segment_key(self):
        VerifiedSegmentRequest.objects.create(session=self.session, segment_key="seg1.ts")
        with self.assertRaises(Exception):
            VerifiedSegmentRequest.objects.create(session=self.session, segment_key="seg1.ts")


class LectureProgressTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        self.student = User.objects.create_user(username="stud3", password="1", role=Role.objects.get(role="student"))
        _, self.lesson = _make_course_and_lesson()

    def test_create_progress(self):
        lp = LectureProgress.objects.create(
            student=self.student, lesson=self.lesson, part_id="abc",
            merged_ranges=[[0, 50]], unique_seconds=50, percent=50,
        )
        self.assertEqual(lp.percent, 50)

    def test_unique_together(self):
        LectureProgress.objects.create(student=self.student, lesson=self.lesson, part_id="abc")
        with self.assertRaises(Exception):
            LectureProgress.objects.create(student=self.student, lesson=self.lesson, part_id="abc")


class ProgressHeartbeatEndpointTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        self.student = User.objects.create_user(username="stud4", password="1", role=Role.objects.get(role="student"))
        _, self.lesson = _make_course_and_lesson()
        self.session = ViewingSession.objects.create(
            student=self.student, lesson=self.lesson, part_id="abc",
            session_id="sess-heartbeat", expires_at=timezone.now() + timedelta(hours=2),
        )
        self.client = Client()
        self.client.login(username="stud4", password="1")

    def test_heartbeat_returns_200(self):
        resp = self.client.post(reverse("progress-heartbeat"),
            json.dumps({"session_id": "sess-heartbeat", "ranges": [[0, 10]]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


class ProgressDashboardAccessTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        Role.objects.get_or_create(role="admin")
        Role.objects.get_or_create(role="moderator")
        self.admin = User.objects.create_user(username="admin1", password="1", role=Role.objects.get(role="admin"))
        self.moderator = User.objects.create_user(username="mod1", password="1", role=Role.objects.get(role="moderator"))
        self.student = User.objects.create_user(username="stud5", password="1", role=Role.objects.get(role="student"))

    def test_admin_can_access(self):
        self.client.login(username="admin1", password="1")
        resp = self.client.get(reverse("progress-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_moderator_denied(self):
        self.client.login(username="mod1", password="1")
        resp = self.client.get(reverse("progress-dashboard"))
        self.assertEqual(resp.status_code, 401)

    def test_student_denied(self):
        self.client.login(username="stud5", password="1")
        resp = self.client.get(reverse("progress-dashboard"))
        self.assertEqual(resp.status_code, 401)
