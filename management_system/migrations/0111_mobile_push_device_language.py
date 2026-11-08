from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("management_system", "0110_quiz_snapshot_deadline_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="mobilepushdevice",
            name="language",
            field=models.CharField(
                choices=[("ar", "Arabic"), ("en", "English")],
                default="ar",
                max_length=2,
            ),
        ),
    ]
