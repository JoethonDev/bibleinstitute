from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .models import AcademicHoliday, AcademicYear, AttendanceRecord, Enrollment, Role, User


from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .models import AcademicHoliday, AcademicYear, AttendanceRecord, Enrollment, Role, User


class AcademicHolidayTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="admin")
        Role.objects.get_or_create(role="staff")
        Role.objects.get_or_create(role="moderator")
        self.admin = User.objects.create_user(
            username="caladmin", password="1", role=Role.objects.get(role="admin"),
        )
        self.year = AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30),
            meeting_weekdays=[0, 1, 2, 3, 4],
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

    # --- Calendar view / month-grid tests ---

    def _login_admin(self):
        c = Client()
        c.login(username="caladmin", password="1")
        return c

    def test_page_renders_year_selector(self):
        c = self._login_admin()
        resp = c.get(reverse("calendar-management"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2026/2027")

    def test_month_grid_has_seven_columns(self):
        c = self._login_admin()
        resp = c.get(reverse("calendar-management") + f"?academic_year={self.year.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, ">Mon<")
        self.assertContains(resp, ">Sun<")

    def test_month_grid_includes_leading_blank_days(self):
        c = self._login_admin()
        # September 2026 starts on a Tuesday (weekday 1) — 1 leading blank (Mon)
        resp = c.get(reverse("calendar-management") + f"?academic_year={self.year.id}&month=2026-9")
        self.assertContains(resp, "outside-month")

    def test_navigation_bounded_by_year(self):
        c = self._login_admin()
        # First month = September 2026
        resp = c.get(reverse("calendar-management") + f"?academic_year={self.year.id}&month=2026-9")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "&laquo;")  # no prev link
        self.assertContains(resp, "September 2026")

        # Last month = June 2027
        resp = c.get(reverse("calendar-management") + f"?academic_year={self.year.id}&month=2027-6")
        self.assertNotContains(resp, "&raquo;")  # no next link

    def test_add_holiday_via_post_creates_record(self):
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "2026-12-25",
            "name": "Christmas",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AcademicHoliday.objects.filter(date=date(2026, 12, 25)).exists())

    def test_add_holiday_rejects_out_of_range(self):
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "2025-01-01",
            "name": "Too Early",
        }, follow=True)
        self.assertContains(resp, "outside the academic year range")
        self.assertFalse(AcademicHoliday.objects.filter(date=date(2025, 1, 1)).exists())

    def test_add_holiday_rejects_duplicate(self):
        AcademicHoliday.objects.create(
            academic_year=self.year, date=date(2026, 12, 25), name="Christmas"
        )
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "2026-12-25",
            "name": "Duplicate",
        }, follow=True)
        self.assertContains(resp, "already exists")

    def test_add_holiday_rejects_blank_name(self):
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "2026-12-25",
            "name": "",
        }, follow=True)
        self.assertContains(resp, "Holiday name is required")
        self.assertFalse(AcademicHoliday.objects.filter(date=date(2026, 12, 25)).exists())

    def test_add_holiday_rejects_invalid_year_id(self):
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": 99999,
            "date": "2026-12-25",
            "name": "Ghost",
        }, follow=True)
        self.assertContains(resp, "Invalid academic year")

    def test_add_holiday_rejects_invalid_date(self):
        c = self._login_admin()
        resp = c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "not-a-date",
            "name": "Bad Date",
        }, follow=True)
        self.assertContains(resp, "Invalid date format")

    def test_delete_holiday_removes_and_redirects(self):
        holiday = AcademicHoliday.objects.create(
            academic_year=self.year, date=date(2026, 12, 25), name="Christmas"
        )
        c = self._login_admin()
        resp = c.post(reverse("delete-holiday", args=[holiday.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(AcademicHoliday.objects.filter(pk=holiday.pk).exists())
        self.assertIn("academic_year=", resp.url)

    def test_another_year_cannot_be_substituted_in_post(self):
        year2 = AcademicYear.objects.create(
            name="2027/2028", level=1, is_current=False,
            starts_on=date(2027, 9, 1), ends_on=date(2028, 6, 30),
        )
        c = self._login_admin()
        c.post(reverse("add-holiday"), {
            "academic_year_id": self.year.id,
            "date": "2027-12-25",
            "name": "Cross Year",
        })
        self.assertFalse(AcademicHoliday.objects.filter(name="Cross Year").exists())


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

    def test_online_student_attendance_is_noop(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("record-attendance", args=["online_token_test", "entrance"]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "noop")
        self.assertFalse(AttendanceRecord.objects.filter(student=self.online_student).exists())

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
        resp = c.post(reverse("attendance-delete", args=[record.id]))
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

    def test_student_lookup_by_username(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin", password="1", role=Role.objects.get(role="admin"),
        )
        student = User.objects.create_user(
            username="scanstud", password="1", role=Role.objects.get(role="student"),
            qr_token="qr_lookup_test",
        )
        c = Client()
        c.login(username="scanadmin", password="1")
        resp = c.post(reverse("student-lookup"), {"query": "scanstud"})
        self.assertRedirects(resp, reverse("scan-preview", args=["qr_lookup_test"]))

    def test_student_lookup_by_numeric_id(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin2", password="1", role=Role.objects.get(role="admin"),
        )
        student = User.objects.create_user(
            username="scanstud2", password="1", role=Role.objects.get(role="student"),
            qr_token="qr_lookup_id_test",
        )
        c = Client()
        c.login(username="scanadmin2", password="1")
        resp = c.post(reverse("student-lookup"), {"query": str(student.pk)})
        self.assertRedirects(resp, reverse("scan-preview", args=["qr_lookup_id_test"]))

    def test_student_lookup_unauthorized_returns_401(self):
        Role.objects.get_or_create(role="student")
        student = User.objects.create_user(
            username="scanstud3", password="1", role=Role.objects.get(role="student"),
        )
        c = Client()
        c.login(username="scanstud3", password="1")
        resp = c.post(reverse("student-lookup"), {"query": "anything"})
        self.assertEqual(resp.status_code, 401)

    def test_student_lookup_not_found_redirects(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin3", password="1", role=Role.objects.get(role="admin"),
        )
        c = Client()
        c.login(username="scanadmin3", password="1")
        resp = c.post(reverse("student-lookup"), {"query": "nonexistent"}, follow=True)
        self.assertContains(resp, "Student not found")

    def test_student_lookup_empty_query_returns_error(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin4", password="1", role=Role.objects.get(role="admin"),
        )
        c = Client()
        c.login(username="scanadmin4", password="1")
        resp = c.post(reverse("student-lookup"), {"query": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_student_lookup_get_method_returns_405(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin5", password="1", role=Role.objects.get(role="admin"),
        )
        c = Client()
        c.login(username="scanadmin5", password="1")
        resp = c.get(reverse("student-lookup"))
        self.assertEqual(resp.status_code, 405)

    def test_student_lookup_without_qr_token_redirects(self):
        Role.objects.get_or_create(role="admin")
        User.objects.create_user(
            username="scanadmin6", password="1", role=Role.objects.get(role="admin"),
        )
        student = User.objects.create_user(
            username="noqrstud", password="1", role=Role.objects.get(role="student"),
            qr_token=None,
        )
        c = Client()
        c.login(username="scanadmin6", password="1")
        resp = c.post(reverse("student-lookup"), {"query": "noqrstud"}, follow=True)
        # Can look up by username even without qr_token — redirects to scan-preview with user ID
        self.assertContains(resp, "noqrstud")

    def test_scanner_manual_token_entry_redirects(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="scanadmin7", password="1", role=Role.objects.get(role="admin"),
        )
        User.objects.create_user(
            username="manualstud", password="1", role=Role.objects.get(role="student"),
            qr_token="manual_token_val",
        )
        c = Client()
        c.login(username="scanadmin7", password="1")
        resp = c.get(reverse("scan-preview", args=["manual_token_val"]))
        self.assertEqual(resp.status_code, 200)

    def test_regenerate_qr_changes_token(self):
        Role.objects.get_or_create(role="admin")
        admin = User.objects.create_user(
            username="adminqr", password="1", role=Role.objects.get(role="admin"),
        )
        old_token = self.student.qr_token
        c = Client()
        c.login(username="adminqr", password="1")
        resp = c.post(reverse("regenerate-qr", args=[self.student.id]))
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.qr_token, old_token)
