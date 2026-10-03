# Generated for backend-controlled mobile download switches.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0093_mobile_app_update_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileDownloadPolicy",
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
                    "singleton",
                    models.CharField(
                        default="default", editable=False, max_length=20, unique=True
                    ),
                ),
                ("in_app_download_enabled", models.BooleanField(default=True)),
                ("device_download_enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="download_policy_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Mobile Download Policy",
                "verbose_name_plural": "Mobile Download Policies",
            },
        ),
    ]
