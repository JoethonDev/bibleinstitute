from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0051_attendance_calendar_and_quiz_openings"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="country",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="education_or_job",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="service",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
