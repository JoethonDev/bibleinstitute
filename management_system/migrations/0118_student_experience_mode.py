from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_default_mode(apps, schema_editor):
    settings_model = apps.get_model("management_system", "StudentExperienceSettings")
    settings_model.objects.using(schema_editor.connection.alias).get_or_create(
        singleton="default",
        defaults={"mode": "legacy"},
    )


def remove_default_mode(apps, schema_editor):
    settings_model = apps.get_model("management_system", "StudentExperienceSettings")
    settings_model.objects.using(schema_editor.connection.alias).filter(
        singleton="default"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0117_alter_adminoperationjob_kind"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="student_experience_override",
            field=models.CharField(
                choices=[
                    ("inherit", "Use global student view"),
                    ("legacy", "Legacy student view"),
                    ("new", "New student view"),
                ],
                default="inherit",
                editable=False,
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name="StudentExperienceSettings",
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
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("legacy", "Legacy student view"),
                            ("new", "New student view"),
                        ],
                        default="legacy",
                        max_length=10,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="student_experience_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Student Experience Settings",
                "verbose_name_plural": "Student Experience Settings",
            },
        ),
        migrations.RunPython(create_default_mode, remove_default_mode),
    ]
