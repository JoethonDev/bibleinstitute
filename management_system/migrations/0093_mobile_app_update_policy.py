# Generated for backend-controlled mobile build versioning (force + optional).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0092_portal_extras"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileAppUpdatePolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "platform",
                    models.CharField(
                        choices=[("android", "Android"), ("ios", "iOS")],
                        max_length=16,
                        unique=True,
                    ),
                ),
                ("is_enabled", models.BooleanField(default=True)),
                ("min_version_code", models.PositiveIntegerField(default=0)),
                ("latest_version_code", models.PositiveIntegerField(default=0)),
                (
                    "min_version_name",
                    models.CharField(blank=True, default="", max_length=50),
                ),
                (
                    "latest_version_name",
                    models.CharField(blank=True, default="", max_length=50),
                ),
                (
                    "update_url",
                    models.CharField(blank=True, default="", max_length=2048),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="app_update_policies",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Mobile App Update Policy",
                "verbose_name_plural": "Mobile App Update Policies",
            },
        ),
    ]
