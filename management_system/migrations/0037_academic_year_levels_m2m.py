from django.db import migrations, models
import django.db.models.deletion


def copy_existing_levels(apps, schema_editor):
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    AcademicYearLevel = apps.get_model("management_system", "AcademicYearLevel")
    for year in AcademicYear.objects.exclude(level__isnull=True).iterator():
        AcademicYearLevel.objects.get_or_create(
            academic_year=year,
            level_id=year.level_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0036_make_level_foreign_keys_canonical"),
    ]

    operations = [
        migrations.CreateModel(
            name="AcademicYearLevel",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
            ],
            options={
                "verbose_name": "Academic Year Level",
                "verbose_name_plural": "Academic Year Levels",
            },
        ),
        migrations.AlterModelOptions(
            name="academicyear",
            options={"ordering": ["-starts_on"]},
        ),
        migrations.RemoveConstraint(
            model_name="academicyear",
            name="academic_year_unique_level_name",
        ),
        migrations.RemoveConstraint(
            model_name="academicyear",
            name="academic_year_one_current_per_level",
        ),
        migrations.AddField(
            model_name="academicyearlevel",
            name="academic_year",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="level_links",
                to="management_system.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="academicyearlevel",
            name="level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="year_links",
                to="management_system.level",
            ),
        ),
        migrations.RunPython(copy_existing_levels, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="academicyear",
            name="level",
        ),
        migrations.AddField(
            model_name="academicyear",
            name="levels",
            field=models.ManyToManyField(
                related_name="academic_years",
                through="management_system.AcademicYearLevel",
                to="management_system.level",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="academicyearlevel",
            unique_together={("academic_year", "level")},
        ),
    ]
