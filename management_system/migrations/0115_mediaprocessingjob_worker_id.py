from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("management_system", "0113_telegram_attachment_duration"),
        ("management_system", "0114_quiz_duration_zero_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="worker_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
