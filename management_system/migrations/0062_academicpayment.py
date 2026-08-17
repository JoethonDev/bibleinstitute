from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0061_mediaprocessingjob_audio_manifest"),
    ]

    operations = [
        migrations.CreateModel(
            name="AcademicPayment",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("receipt_key", models.CharField(max_length=500)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "academic_year_level",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="academic_payments",
                        to="management_system.academicyearlevel",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="academic_payments",
                        to="management_system.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["academic_year_level", "student"],
                        name="academic_payment_scope_student_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("student", "academic_year_level"),
                        name="academic_payment_student_scope_unique",
                    ),
                ],
            },
        ),
    ]
