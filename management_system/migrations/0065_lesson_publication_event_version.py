from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0064_mobile_otp_challenge"),
    ]

    operations = [
        migrations.AddField(
            model_name="lesson",
            name="publication_event_version",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
    ]
