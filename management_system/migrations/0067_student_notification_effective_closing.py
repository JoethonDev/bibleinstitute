from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0066_student_notification_cancellation"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentnotification",
            name="effective_closing_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
