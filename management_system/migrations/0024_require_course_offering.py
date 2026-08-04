from django.db import migrations, models


def validate_no_null_offerings(apps, schema_editor):
    Lesson = apps.get_model("management_system", "Lesson")
    Quiz = apps.get_model("management_system", "Quiz")
    if Lesson.objects.filter(course_offering__isnull=True).exists():
        raise ValueError("Cannot make course_offering required: some lessons have NULL course_offering. Run backfill first.")
    if Quiz.objects.filter(course_offering__isnull=True).exists():
        raise ValueError("Cannot make course_offering required: some quizzes have NULL course_offering. Run backfill first.")


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0023_seed_offline_cities"),
    ]

    operations = [
        migrations.RunPython(validate_no_null_offerings, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lesson",
            name="course_offering",
            field=models.ForeignKey(on_delete=models.deletion.PROTECT, related_name="lessons", to="management_system.courseoffering"),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="quiz",
            name="course_offering",
            field=models.ForeignKey(on_delete=models.deletion.PROTECT, related_name="quizzes", to="management_system.courseoffering"),
            preserve_default=False,
        ),
    ]
