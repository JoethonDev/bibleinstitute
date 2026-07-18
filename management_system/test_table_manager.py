from django.test import TestCase
from django.template import Template, Context


class TableColumnSelectorTest(TestCase):
    def test_column_options_use_flex_layout(self):
        template = Template(
            "{% load i18n %}{% include 'partials/table_and_pagination.html' %}"
        )
        columns = ["Name", "Email", "Role"]
        context = Context({
            "columns": columns,
            "view": "user",
            "page_obj": None,
            "has_filter": True,
        })
        html = template.render(context)

        self.assertIn("column-option", html)
        self.assertIn('class="column-option dropdown-item d-flex align-items-center gap-2"', html)
        self.assertIn('class="form-check-input m-0"', html)
        self.assertIn('x-model="col.visible"', html)
        self.assertIn('@change="saveColumnPreferences()"', html)

    def test_column_selector_includes_all_columns(self):
        template = Template(
            "{% load i18n %}{% include 'partials/table_and_pagination.html' %}"
        )
        columns = ["Name", "Email", "Role"]
        context = Context({
            "columns": columns,
            "view": "user",
            "page_obj": None,
            "has_filter": True,
        })
        html = template.render(context)

        for name in columns:
            self.assertIn(name, html)

    def test_column_selector_includes_reset_button(self):
        template = Template(
            "{% load i18n %}{% include 'partials/table_and_pagination.html' %}"
        )
        context = Context({
            "columns": ["Name"],
            "view": "user",
            "page_obj": None,
            "has_filter": True,
        })
        html = template.render(context)

        self.assertIn("Reset to Default", html)
        self.assertIn("resetColumns", html)
