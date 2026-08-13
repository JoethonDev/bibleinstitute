from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0057_telegram_broadcasts"),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalAcademicSummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=255, unique=True)),
                ("source_name", models.CharField(max_length=255)),
                ("source_file", models.CharField(blank=True, default="", max_length=255)),
                ("source_row", models.PositiveIntegerField(blank=True, null=True)),
                ("outcome", models.CharField(choices=[("pending_review", "Pending review"), ("completed", "Completed"), ("passed", "Passed"), ("partial", "Partial success"), ("failed", "Failed")], default="pending_review", max_length=20)),
                ("notes", models.TextField(blank=True, default="")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("certificate_eligible", models.BooleanField(default=False)),
                ("promoted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("academic_year_level", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="historical_summaries", to="management_system.academicyearlevel")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_historical_summaries", to=settings.AUTH_USER_MODEL)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="historical_academic_summaries", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "pk"]},
        ),
        migrations.AlterField(
            model_name="promotionhistory",
            name="evaluation_result",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="promotion_history", to="management_system.evaluationresult"),
        ),
        migrations.AddField(
            model_name="promotionhistory",
            name="historical_summary",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="promotion_history", to="management_system.historicalacademicsummary"),
        ),
        migrations.AlterField(
            model_name="promotionhistory",
            name="score",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AlterField(
            model_name="promotionhistory",
            name="computed_status",
            field=models.CharField(max_length=20),
        ),
        migrations.AlterField(
            model_name="promotionhistory",
            name="final_status",
            field=models.CharField(max_length=20),
        ),
        migrations.AddConstraint(
            model_name="historicalacademicsummary",
            constraint=models.UniqueConstraint(fields=("student", "academic_year_level"), name="historical_summary_student_scope_unique"),
        ),
        migrations.AddIndex(
            model_name="historicalacademicsummary",
            index=models.Index(fields=["academic_year_level", "outcome"], name="hist_summary_scope_outcome_idx"),
        ),
        migrations.AddIndex(
            model_name="historicalacademicsummary",
            index=models.Index(fields=["outcome", "promoted_at"], name="hist_summary_outcome_idx"),
        ),
        migrations.AddConstraint(
            model_name="promotionhistory",
            constraint=models.CheckConstraint(condition=~models.Q(("evaluation_result__isnull", True), ("historical_summary__isnull", True)), name="promotion_history_source_required"),
        ),
        migrations.AddConstraint(
            model_name="promotionhistory",
            constraint=models.CheckConstraint(condition=models.Q(("evaluation_result__isnull", False), ("historical_summary__isnull", True)) | models.Q(("evaluation_result__isnull", True), ("historical_summary__isnull", False)), name="promotion_history_one_source"),
        ),
    ]
