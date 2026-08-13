from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0058_historical_academic_intake"),
    ]

    operations = [
        migrations.AddField(
            model_name="promotionhistory",
            name="promotion_method",
            field=models.CharField(
                choices=[
                    ("system", "System"),
                    ("manual_historical", "Manual historical"),
                ],
                default="system",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="promotionhistory",
            name="reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddConstraint(
            model_name="promotionhistory",
            constraint=models.CheckConstraint(
                condition=~models.Q(promotion_method="manual_historical", reason=""),
                name="promotion_history_manual_reason_required",
            ),
        ),
    ]
