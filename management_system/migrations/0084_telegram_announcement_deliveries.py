from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0083_notification_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegramnotificationdelivery",
            name="announcement",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="telegram_deliveries",
                to="management_system.announcement",
            ),
        ),
        migrations.AlterField(
            model_name="telegramnotificationdelivery",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("lesson_published", "Lesson published"),
                    ("quiz_opening", "Exam opening"),
                    ("announcement", "Announcement"),
                ],
                max_length=32,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="telegramnotificationdelivery",
            name="telegram_delivery_source_matches_type",
        ),
        migrations.AddConstraint(
            model_name="telegramnotificationdelivery",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        notification_type="lesson_published",
                        quiz__isnull=True,
                        announcement__isnull=True,
                    )
                    | models.Q(
                        notification_type="quiz_opening",
                        lesson__isnull=True,
                        announcement__isnull=True,
                    )
                    | models.Q(
                        notification_type="announcement",
                        lesson__isnull=True,
                        quiz__isnull=True,
                    )
                ),
                name="telegram_delivery_source_matches_type",
            ),
        ),
        migrations.AddIndex(
            model_name="telegramnotificationdelivery",
            index=models.Index(
                fields=["notification_type", "announcement"],
                name="tg_delivery_announcement_idx",
            ),
        ),
    ]
