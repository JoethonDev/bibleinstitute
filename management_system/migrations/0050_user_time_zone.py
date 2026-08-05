from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0049_make_calendar_meetings_date_specific"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="time_zone",
            field=models.CharField(default=settings.TIME_ZONE, max_length=64),
        ),
    ]
