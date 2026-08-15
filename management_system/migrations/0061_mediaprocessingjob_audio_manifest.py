from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0060_mediaprocessingjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="audio_manifest_key",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
    ]
