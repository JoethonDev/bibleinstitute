from datetime import date

from django.test import Client, TestCase
from django.urls import reverse
from .models import AcademicYear, OfflineCity, Role, User
from .forms import SignupForm


class ValidatorTests(TestCase):
    def test_national_id_14_digits(self):
        from .utils.validators import validate_egyptian_national_id
        validate_egyptian_national_id("12345678901234")
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_egyptian_national_id("12345")
        with self.assertRaises(ValidationError):
            validate_egyptian_national_id("12345678901234a")

    def test_passport_format(self):
        from .utils.validators import validate_passport
        validate_passport("AB123456")
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_passport("ab")

    def test_phone_normalization(self):
        from .utils.validators import normalize_phone
        self.assertEqual(normalize_phone("+201234567890"), "+201234567890")
        self.assertEqual(normalize_phone("01234567890"), "+01234567890")
        self.assertEqual(normalize_phone("+20 123 456 7890"), "+201234567890")


class SignupFormTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")

    def valid_data(self):
        return {
            "username": "newstudent",
            "password": "testpass123",
            "first_name": "Test",
            "last_name": "Student",
            "agree_terms": True,
        }

    def test_valid_form(self):
        form = SignupForm(data=self.valid_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_terms_required(self):
        data = self.valid_data()
        data.pop("agree_terms")
        form = SignupForm(data=data)
        self.assertFalse(form.is_valid())

    def test_password_set_on_save(self):
        form = SignupForm(data=self.valid_data())
        self.assertTrue(form.is_valid())
        user = form.save()
        self.assertEqual(user.application_status, "pending")
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("testpass123"))


class SignupViewTests(TestCase):
    def setUp(self):
        Role.objects.get_or_create(role="student")

    def test_get_signup_page(self):
        c = Client()
        resp = c.get(reverse("signup"))
        self.assertEqual(resp.status_code, 200)

    def test_post_valid_signup(self):
        c = Client()
        resp = c.post(reverse("signup"), {
            "username": "newstudent",
            "password": "testpass123",
            "first_name": "Test",
            "agree_terms": True,
        })
        self.assertIn(resp.status_code, (200, 302))
        user = User.objects.get(username="newstudent")
        self.assertEqual(user.application_status, "pending")
        self.assertFalse(user.is_active)

    def test_duplicate_username_rejected(self):
        User.objects.create_user(username="existing", password="1")
        c = Client()
        resp = c.post(reverse("signup"), {
            "username": "existing",
            "password": "testpass123",
            "first_name": "Test",
            "agree_terms": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists", status_code=200)


class ApplicationAdminTests(TestCase):
    def setUp(self):
        self.admin_role, _ = Role.objects.get_or_create(role="admin")
        self.student_role, _ = Role.objects.get_or_create(role="student")
        self.admin = User.objects.create_user(
            username="admin", password="1", role=self.admin_role,
            application_status="active", is_active=True,
        )
        self.applicant = User.objects.create_user(
            username="applicant", password="1", role=self.student_role,
            application_status="pending", is_active=False,
        )
        AcademicYear.objects.create(
            name="2026/2027", level=1, is_current=True,
            starts_on=date(2026, 9, 1), ends_on=date(2027, 6, 30),
        )

    def test_applications_dashboard_admin_access(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("applications-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_applications_dashboard_requires_admin(self):
        c = Client()
        User.objects.create_user(
            username="student", password="1", role=self.student_role,
            application_status="active", is_active=True,
        )
        c.login(username="student", password="1")
        resp = c.get(reverse("applications-dashboard"))
        self.assertEqual(resp.status_code, 401)

    def test_activate_applicant(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("application-decision", args=[self.applicant.id, "activate"]))
        self.assertIn(resp.status_code, (200, 302))
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.application_status, "active")
        self.assertTrue(self.applicant.is_active)
        self.assertEqual(self.applicant.role.role, "student")

    def test_decline_applicant(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("application-decision", args=[self.applicant.id, "decline"]))
        self.assertIn(resp.status_code, (200, 302))
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.application_status, "declined")
        self.assertFalse(self.applicant.is_active)

    def test_bulk_activate(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(
            reverse("bulk-application-decision"),
            {"decision": "activate", "user_ids": [self.applicant.id]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.application_status, "active")
