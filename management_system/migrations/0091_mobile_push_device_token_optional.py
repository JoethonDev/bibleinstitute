from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0090_graduation_gallery"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mobilepushdevice",
            name="expo_push_token",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
    ]
