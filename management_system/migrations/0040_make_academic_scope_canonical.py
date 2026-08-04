import management_system.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("management_system", "0039_backfill_academic_scopes")]

    operations = [
        migrations.RenameField(
            model_name="academicyear",
            old_name="is_current",
            new_name="is_active",
        ),
        migrations.AlterField(
            model_name="academicyear",
            name="name",
            field=models.CharField(max_length=20, unique=True),
        ),
        migrations.AlterField(
            model_name="academicyear",
            name="ordering",
            field=models.PositiveIntegerField(unique=True),
        ),
        migrations.RemoveField(model_name="academicyear", name="meeting_weekdays"),
        migrations.AlterModelOptions(
            name="academicyear",
            options={"ordering": ["ordering"]},
        ),
        migrations.AddConstraint(
            model_name="academicyear",
            constraint=models.UniqueConstraint(
                condition=models.Q(is_active=True),
                fields=("is_active",),
                name="academic_year_one_active",
            ),
        ),
        migrations.AlterField(
            model_name="academicyearlevel",
            name="meeting_weekdays",
            field=models.JSONField(default=management_system.models.default_meeting_weekdays),
        ),
        migrations.AlterModelOptions(
            name="academicyearlevel",
            options={
                "ordering": ["academic_year__ordering", "level__ordering"],
                "verbose_name": "Academic Year Level",
                "verbose_name_plural": "Academic Year Levels",
            },
        ),
        migrations.RemoveConstraint(
            model_name="courseoffering",
            name="course_offering_unique_course_year",
        ),
        migrations.RemoveField(model_name="courseoffering", name="academic_year"),
        migrations.AlterField(
            model_name="courseoffering",
            name="academic_year_level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="course_offerings",
                to="management_system.academicyearlevel",
            ),
        ),
        migrations.AddConstraint(
            model_name="courseoffering",
            constraint=models.UniqueConstraint(
                fields=("course", "academic_year_level"),
                name="course_offering_unique_course_year_level",
            ),
        ),
        migrations.AlterModelOptions(
            name="courseoffering",
            options={"ordering": ["-academic_year_level__academic_year__starts_on", "course__name"]},
        ),
        migrations.RemoveConstraint(
            model_name="enrollment",
            name="enrollment_unique_full_year",
        ),
        migrations.RemoveField(model_name="enrollment", name="academic_year"),
        migrations.AlterField(
            model_name="enrollment",
            name="academic_year_level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="enrollments",
                to="management_system.academicyearlevel",
            ),
        ),
        migrations.AddConstraint(
            model_name="enrollment",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=True),
                fields=("student", "academic_year_level"),
                name="enrollment_unique_full_year",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="attendancerecord",
            unique_together={("student", "academic_year_level", "attendance_date", "action")},
        ),
        migrations.RemoveField(model_name="attendancerecord", name="academic_year"),
        migrations.AlterField(
            model_name="attendancerecord",
            name="academic_year_level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records",
                to="management_system.academicyearlevel",
            ),
        ),
        migrations.RemoveField(model_name="lesson", name="course"),
        migrations.RemoveField(model_name="quiz", name="course"),
    ]
