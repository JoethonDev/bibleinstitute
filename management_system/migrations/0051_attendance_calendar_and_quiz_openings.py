from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("management_system", "0050_user_time_zone"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuizStudentOpening",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opening_date", models.DateTimeField()),
                ("closing_date", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "granted_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="granted_quiz_openings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "quiz",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_openings",
                        to="management_system.quiz",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quiz_openings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("quiz", "student"), name="quiz_student_opening_unique"),
                ],
            },
        ),
        migrations.AlterField(
            model_name="attendancerecord",
            name="course_offering",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_records",
                to="management_system.courseoffering",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancerecord",
            constraint=models.UniqueConstraint(
                condition=models.Q(course_offering__isnull=True),
                fields=("student", "attendance_date", "action"),
                name="attendance_unique_student_unassigned_date_action",
            ),
        ),
    ]
