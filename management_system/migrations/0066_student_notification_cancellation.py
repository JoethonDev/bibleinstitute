from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0065_lesson_publication_event_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentnotification",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="studentnotification",
            index=models.Index(
                fields=["cancelled_at", "scheduled_for"],
                name="student_notif_cancel_idx",
            ),
        ),
    ]
