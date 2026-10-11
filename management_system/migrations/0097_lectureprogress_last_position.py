from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0096_signup_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="lectureprogress",
            name="last_position_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
