from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0067_student_notification_effective_closing"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="studentnotification",
            name="student_notification_source_type_match",
        ),
        migrations.AddConstraint(
            model_name="studentnotification",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        notification_type="lesson_published",
                        quiz__isnull=True,
                        navigation_type="lesson",
                        effective_opening_at__isnull=True,
                        effective_closing_at__isnull=True,
                    )
                    | models.Q(
                        notification_type="quiz_opening",
                        lesson__isnull=True,
                        navigation_type="quiz",
                        effective_opening_at__isnull=False,
                        effective_closing_at__isnull=False,
                    )
                ),
                name="student_notification_source_type_match",
            ),
        ),
    ]
