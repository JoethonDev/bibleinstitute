from django.db import migrations, models
import django.db.models.deletion


def copy_level_ref(apps, schema_editor):
    Course = apps.get_model("management_system", "Course")
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    Course.objects.all().update(level=models.F("level_ref"))
    AcademicYear.objects.all().update(level=models.F("level_ref"))


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0035_backfill_levels"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="academicyear",
            options={"ordering": ["-starts_on", "level__ordering"]},
        ),
        migrations.RunPython(copy_level_ref, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="enrollment",
            name="level",
        ),
        migrations.RemoveField(
            model_name="academicyear",
            name="level_ref",
        ),
        migrations.RemoveField(
            model_name="course",
            name="level_ref",
        ),
        migrations.AlterField(
            model_name="academicyear",
            name="level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="academic_years",
                to="management_system.level",
            ),
        ),
        migrations.AlterField(
            model_name="course",
            name="level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="courses",
                to="management_system.level",
            ),
        ),
        migrations.DeleteModel(
            name="LevelName",
        ),
    ]
