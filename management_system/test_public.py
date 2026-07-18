from django.test import Client, TestCase
from django.urls import reverse
from django.utils import translation
from .models import Role, User


class PublicViewTests(TestCase):
    def setUp(self):
        self.student_role, _ = Role.objects.get_or_create(role="student")
        self.admin_role, _ = Role.objects.get_or_create(role="admin")
        self.student = User.objects.create_user(
            username="student", password="pass", role=self.student_role,
        )

    def _login(self, user=None):
        c = Client()
        c.force_login(user or self.student)
        return c

    def test_public_home_loads(self):
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)

    def test_about_page_loads(self):
        resp = self.client.get(reverse("about"))
        self.assertEqual(resp.status_code, 200)

    def test_program_page_loads_for_anonymous_user(self):
        resp = self.client.get(reverse("program"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Study System")
        self.assertContains(resp, "EGP 850")

    def test_arabic_public_pages_use_arabic_copy(self):
        with translation.override("ar"):
            for name in ("home", "about", "program"):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "معهد الكتاب المقدس")

    def test_authenticated_user_sees_dashboard_at_root(self):
        c = self._login(self.student)
        resp = c.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Welcome back")

    def test_portal_requires_login(self):
        resp = self.client.get(reverse("student-portal"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("user_login"), resp.url)

    def test_portal_loads_for_student(self):
        c = self._login(self.student)
        resp = c.get(reverse("student-portal"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "student")

    def test_robots_txt(self):
        resp = self.client.get(reverse("robots-txt"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/plain")
        self.assertIn("Disallow: /dashboard/", resp.content.decode())

    def test_sitemap_xml(self):
        resp = self.client.get(reverse("sitemap-xml"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/xml")
        self.assertIn("/about/", resp.content.decode())
        self.assertIn("/program/", resp.content.decode())
        self.assertIn("/signup/", resp.content.decode())
        self.assertNotIn("/courses/", resp.content.decode())

    def test_nav_links_for_anonymous(self):
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, "Login")
        self.assertContains(resp, "Sign Up")
        self.assertContains(resp, reverse("program"))

    def test_nav_links_for_authenticated(self):
        c = self._login(self.student)
        resp = c.get(reverse("home"))
        self.assertContains(resp, "Courses")
        self.assertContains(resp, "Profile")
