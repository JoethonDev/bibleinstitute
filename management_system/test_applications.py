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
            "phone": "+201234567890",
            "priest_name": "Fr. John",
            "priest_phone": "+201234567891",
            "church": "St. Mary Church",
            "city": "Cairo",
            "identity_type": "national_id",
            "identity_number": "12345678901234",
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

    def _full_signup_data(self, username="newstudent", **kw):
        data = {
            "username": username, "password": "testpass123", "first_name": "Test",
            "phone": "+201234567890", "priest_name": "Fr. John", "priest_phone": "+201234567891",
            "church": "St. Mary", "city": "Cairo", "identity_type": "national_id",
            "identity_number": "12345678901234", "agree_terms": True,
        }
        data.update(kw)
        return data

    def test_post_valid_signup(self):
        c = Client()
        resp = c.post(reverse("signup"), self._full_signup_data())
        self.assertIn(resp.status_code, (200, 302))
        user = User.objects.get(username="newstudent")
        self.assertEqual(user.application_status, "pending")
        self.assertFalse(user.is_active)

    def test_duplicate_username_rejected(self):
        User.objects.create_user(username="existing", password="1")
        c = Client()
        resp = c.post(reverse("signup"), self._full_signup_data(username="existing"))
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

    # --- Workstream 1 regression checks ---

    def test_direct_applications_dashboard_has_admin_shell(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("applications-dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "css/app.css")
        self.assertContains(resp, "admin-layout")
        self.assertContains(resp, "id=\"content\"")

    def test_direct_application_review_has_admin_shell(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("application-review", args=[self.applicant.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "css/app.css")
        self.assertContains(resp, "admin-layout")
        self.assertContains(resp, "id=\"content\"")

    def test_get_decision_returns_405(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("application-decision", args=[self.applicant.id, "activate"]))
        self.assertEqual(resp.status_code, 405)

    def test_invalid_status_falls_back_to_pending(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.get(reverse("applications-dashboard") + "?status=invalid")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_decision_returns_400(self):
        c = Client()
        c.login(username="admin", password="1")
        resp = c.post(reverse("application-decision", args=[self.applicant.id, "invalid"]))
        self.assertEqual(resp.status_code, 400)
