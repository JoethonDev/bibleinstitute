from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0097_lectureprogress_last_position"),
    ]

    operations = [
        migrations.AddField(
            model_name="viewingsession",
            name="media_key",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
