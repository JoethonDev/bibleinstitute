from django.db import migrations, models


def collapse_announcement_copy(apps, schema_editor):
    Announcement = apps.get_model("management_system", "Announcement")
    StudentNotification = apps.get_model("management_system", "StudentNotification")

    for announcement in Announcement.objects.all().iterator():
        if (
            announcement.title_ar != announcement.title_en
            or announcement.body_ar != announcement.body_en
        ):
            raise RuntimeError(
                "Announcement %s has different Arabic and English copy; resolve it before migration 0082."
                % announcement.pk
            )

        StudentNotification.objects.filter(announcement_id=announcement.pk).update(
            title_ar=announcement.title_en,
            body_ar=announcement.body_en,
            title_en=announcement.title_en,
            body_en=announcement.body_en,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0081_announcements_and_web_notifications"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="announcement",
            name="announcement_content_not_empty",
        ),
        migrations.RunPython(collapse_announcement_copy, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="announcement",
            old_name="title_en",
            new_name="title",
        ),
        migrations.RenameField(
            model_name="announcement",
            old_name="body_en",
            new_name="body",
        ),
        migrations.RemoveField(
            model_name="announcement",
            name="title_ar",
        ),
        migrations.RemoveField(
            model_name="announcement",
            name="body_ar",
        ),
        migrations.AddConstraint(
            model_name="announcement",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(title="")
                    & ~models.Q(body="")
                ),
                name="announcement_content_not_empty",
            ),
        ),
    ]
