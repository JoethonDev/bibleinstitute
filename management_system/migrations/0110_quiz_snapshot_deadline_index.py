from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("management_system", "0109_notification_withdrawal_telegram_lifecycle"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="quizanswersnapshot",
            index=models.Index(fields=["deadline_at"], name="quiz_snapshot_deadline_idx"),
        ),
    ]
