from django.db import migrations, models


class Migration(migrations.Migration):
    """Default an exam duration to 0, which means "no time limit".

    The file keeps its 0110 prefix because it was created before the later
    Telegram migrations were present locally; its dependency chain places it
    after 0113, so the graph stays linear and this is the single leaf.
    """

    dependencies = [
        ("management_system", "0113_telegram_attachment_duration"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quiz",
            name="duration_minutes",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
