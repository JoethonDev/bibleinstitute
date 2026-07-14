from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .models import AcademicHoliday, AcademicYear, AttendanceRecord, Enrollment, Role, User


class AcademicHolidayTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30),
        )

    def test_create_holiday(self):
        holiday = AcademicHoliday.objects.create(
            academic_year=self.year, date=date(2026, 12, 25), name="Christmas"
        )
        self.assertEqual(holiday.name, "Christmas")

    def test_holiday_uniqueness(self):
        AcademicHoliday.objects.create(
            academic_year=self.year, date=date(2026, 12, 25), name="Christmas"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AcademicHoliday.objects.create(
                academic_year=self.year, date=date(2026, 12, 25), name="Duplicate"
            )


class AttendanceRecordTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        Role.objects.get_or_create(role="admin")
        self.year = AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30),
        )
        self.student = User.objects.create_user(
            username="stud", password="1", role=Role.objects.get(role="student"),
            study_mode="offline",
        )

    def test_create_attendance_record(self):
        record = AttendanceRecord.objects.create(
            student=self.student, academic_year=self.year,
            attendance_date=date(2026, 9, 6), action="entrance",
        )
        self.assertEqual(record.action, "entrance")

    def test_duplicate_entrance_rejected(self):
        AttendanceRecord.objects.create(
            student=self.student, academic_year=self.year,
            attendance_date=date(2026, 9, 6), action="entrance",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(
                student=self.student, academic_year=self.year,
                attendance_date=date(2026, 9, 6), action="entrance",
            )

    def test_entrance_and_exit_allowed_same_day(self):
        AttendanceRecord.objects.create(
            student=self.student, academic_year=self.year,
            attendance_date=date(2026, 9, 6), action="entrance",
        )
        record = AttendanceRecord.objects.create(
            student=self.student, academic_year=self.year,
            attendance_date=date(2026, 9, 6), action="exit",
        )
        self.assertEqual(record.action, "exit")


class ExpectedDateHelperTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date.today() - timedelta(days=30),
            ends_on=date.today() + timedelta(days=30),
            meeting_weekdays=[0, 1, 2, 3, 4, 5, 6],  # every day
        )

    def test_expected_dates_returned(self):
        from .utils.attendance import get_expected_dates
        dates = get_expected_dates(self.year)
        self.assertGreater(len(dates), 0)

    def test_weekday_filter(self):
        from .utils.attendance import get_expected_dates
        year2 = AcademicYear.objects.create(
            name="Filter Year", level=2, is_current=True,
            starts_on=date.today() - timedelta(days=30),
            ends_on=date.today() + timedelta(days=30),
            meeting_weekdays=[0, 1, 2, 3, 4],  # Mon-Fri
        )
        dates = get_expected_dates(year2)
        for d in dates:
            self.assertIn(d.weekday(), [0, 1, 2, 3, 4])

    def test_holiday_excludes_date(self):
        from .utils.attendance import get_expected_dates, is_expected_date
        holiday_date = date.today()
        AcademicHoliday.objects.create(
            academic_year=self.year, date=holiday_date, name="Test Holiday"
        )
        self.assertFalse(is_expected_date(self.year, holiday_date))
        dates = get_expected_dates(self.year)
        self.assertNotIn(holiday_date, dates)


class AttendanceRecordViewTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="admin")
        Role.objects.get_or_create(role="staff")
        Role.objects.get_or_create(role="moderator")
        Role.objects.get_or_create(role="student")

        self.admin = User.objects.create_user(
            username="admin", password="1", role=Role.objects.get(role="admin"),
        )
        self.staff = User.objects.create_user(
            username="staff", password="1", role=Role.objects.get(role="staff"),
        )
        self.moderator = User.objects.create_user(
            username="mod", password="1", role=Role.objects.get(role="moderator"),
        )
        self.offline_student = User.objects.create_user(
            username="offline", password="1", role=Role.objects.get(role="student"),
            study_mode="offline",
        )
        self.online_student = User.objects.create_user(
            username="online", password="1", role=Role.objects.get(role="student"),
            study_mode="online",
        )

        self.year = AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date.today() - timedelta(days=30),
            ends_on=date.today() + timedelta(days=30),
            meeting_weekdays=[0, 1, 2, 3, 4, 5, 6],  # every day
        )

        Enrollment.objects.create(
            student=self.offline_student, academic_year=self.year,
        )
        Enrollment.objects.create(
            student=self.online_student, academic_year=self.year,
        )

        self.offline_student.qr_token = "offline_token_test"
        self.offline_student.save()
        self.online_student.qr_token = "online_token_test"
        self.online_student.save()

    def test_unauthorized_role_gets_401_on_scanner(self):
        c = Client()
        c.login(username="offline", password="1")
        resp = c.get(reverse("scanner"))
        self.assertEqual(resp.status_code, 401)

    def test_scanner_works_for_authorized_roles(self):
        for username in ["admin", "staff", "mod"]:
            c = Client()
            c.login(username=username, password="1")
            resp = c.get(reverse("scanner"))
            self.assertEqual(resp.status_code, 200, f"{username} should get 200 on scanner")

    def test_scan_preview_works_for_authorized(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("scan-preview", args=["offline_token_test"]))
        self.assertEqual(resp.status_code, 200)

    def test_scan_preview_denied_student(self):
        c = Client()
        c.login(username="offline", password="1")
        resp = c.get(reverse("scan-preview", args=["offline_token_test"]))
        self.assertEqual(resp.status_code, 401)

    def test_online_student_cannot_record_attendance(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("record-attendance", args=["online_token_test", "entrance"]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("online", resp.json()["error"].lower())

    def test_admin_correct_attendance(self):
        record = AttendanceRecord.objects.create(
            student=self.offline_student, academic_year=self.year,
            attendance_date=date.today(), action="entrance",
        )
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("attendance-correction", args=[record.id]), {
            "action": "exit",
        })
        self.assertEqual(resp.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.action, "exit")
        self.assertIsNotNone(record.corrected_at)

    def test_admin_delete_attendance(self):
        record = AttendanceRecord.objects.create(
            student=self.offline_student, academic_year=self.year,
            attendance_date=date.today(), action="entrance",
        )
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("attendance-delete", args=[record.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(AttendanceRecord.objects.filter(pk=record.pk).exists())


class QRTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")
        self.student = User.objects.create_user(
            username="studqr", password="1", role=Role.objects.get(role="student"),
        )

    def test_download_qr_returns_png(self):
        c = Client()
        c.login(username="studqr", password="1")
        resp = c.get(reverse("download-qr"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertGreater(len(resp.content), 100)

    def test_regenerate_qr_changes_token(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="adminqr", password="1", role=Role.objects.get(role="admin"),
        )
        old_token = self.student.qr_token
        c = Client()
        c.login(username="adminqr", password="1")
        resp = c.get(reverse("regenerate-qr", args=[self.student.id]))
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.qr_token, old_token)
