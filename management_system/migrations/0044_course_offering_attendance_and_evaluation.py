import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_attendance_offerings(apps, schema_editor):
    AttendanceRecord = apps.get_model("management_system", "AttendanceRecord")
    CourseOffering = apps.get_model("management_system", "CourseOffering")
    for record in AttendanceRecord.objects.all().iterator(chunk_size=500):
        offerings = list(
            CourseOffering.objects.filter(
                academic_year_level_id=record.academic_year_level_id
            ).values_list("id", flat=True)
        )
        if len(offerings) != 1:
            raise RuntimeError(
                "Attendance record %s cannot be mapped deterministically from "
                "academic-year level %s to a course offering; found %s offerings."
                % (record.pk, record.academic_year_level_id, len(offerings))
            )
        record.course_offering_id = offerings[0]
        record.save(update_fields=["course_offering"])


def copy_formula_dates(apps, schema_editor):
    PromotionFormula = apps.get_model("management_system", "PromotionFormula")
    for formula in PromotionFormula.objects.all().iterator(chunk_size=500):
        formula.evaluation_starts_on = formula.evaluation_date
        formula.evaluation_ends_on = formula.evaluation_date
        formula.save(update_fields=["evaluation_starts_on", "evaluation_ends_on"])


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0043_media_session_segment_numbers"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancerecord",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records_transition",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AddField(
            model_name="promotionformula",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promotion_formulas_transition",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AddField(
            model_name="promotionformula",
            name="evaluation_ends_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="promotionformula",
            name="evaluation_starts_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="promotionformula",
            name="failed_courses_repeat_threshold",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AlterField(
            model_name="promotionformula",
            name="academic_year_level",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promotion_formulas",
                to="management_system.academicyearlevel",
            ),
        ),
        migrations.AddField(
            model_name="evaluationresult",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evaluation_results_transition",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AlterField(
            model_name="evaluationresult",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evaluation_results",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AlterField(
            model_name="evaluationresult",
            name="enrollment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="evaluation_results",
                to="management_system.enrollment",
            ),
        ),
        migrations.AlterField(
            model_name="evaluationresult",
            name="computed_status",
            field=models.CharField(
                choices=[("pass", "Pass"), ("fail", "Fail"), ("unevaluable", "Unevaluable")],
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="evaluationresult",
            name="final_status",
            field=models.CharField(
                choices=[("pass", "Pass"), ("fail", "Fail"), ("unevaluable", "Unevaluable")],
                max_length=12,
            ),
        ),
        migrations.RunPython(copy_formula_dates, migrations.RunPython.noop),
        migrations.RunPython(backfill_attendance_offerings, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attendancerecord",
            name="course_offering",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AlterField(
            model_name="promotionformula",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="promotion_formulas",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AlterField(
            model_name="promotionformula",
            name="evaluation_starts_on",
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name="promotionformula",
            name="evaluation_ends_on",
            field=models.DateField(),
        ),
        migrations.AlterUniqueTogether(
            name="attendancerecord",
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name="attendancerecord",
            name="academic_year_level",
        ),
        migrations.RemoveField(
            model_name="promotionformula",
            name="evaluation_date",
        ),
        migrations.AddConstraint(
            model_name="promotionformula",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=True),
                fields=("academic_year_level",),
                name="promotion_formula_one_all_courses",
            ),
        ),
        migrations.AddConstraint(
            model_name="promotionformula",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=False),
                fields=("academic_year_level", "course_offering"),
                name="promotion_formula_unique_offering",
            ),
        ),
        migrations.AddConstraint(
            model_name="evaluationresult",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=False),
                fields=("formula", "enrollment", "course_offering"),
                name="evaluation_result_unique_course",
            ),
        ),
        migrations.AddConstraint(
            model_name="evaluationresult",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=True),
                fields=("formula", "enrollment"),
                name="evaluation_result_unique_aggregate",
            ),
        ),
        migrations.RemoveIndex(
            model_name="evaluationresult",
            name="management__formula_21d2bf_idx",
        ),
        migrations.AddIndex(
            model_name="promotionformula",
            index=models.Index(
                fields=["academic_year_level", "course_offering"],
                name="promo_formula_scope_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="evaluationresult",
            index=models.Index(
                fields=["formula", "course_offering", "final_status"],
                name="eval_result_formula_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="evaluationresult",
            index=models.Index(
                fields=["enrollment", "course_offering"],
                name="eval_result_enrollment_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancerecord",
            constraint=models.UniqueConstraint(
                fields=("student", "course_offering", "attendance_date", "action"),
                name="attendance_unique_student_offering_date_action",
            ),
        ),
        migrations.AddIndex(
            model_name="attendancerecord",
            index=models.Index(
                fields=["course_offering", "attendance_date"],
                name="attendance_offering_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="attendancerecord",
            index=models.Index(
                fields=["student", "course_offering", "attendance_date"],
                name="attendance_student_off_idx",
            ),
        ),
    ]
