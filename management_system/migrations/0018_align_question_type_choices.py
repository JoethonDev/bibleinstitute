from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0017_fix_phase0_date_defaults"),
    ]

    operations = [
        migrations.AlterField(
            model_name="question",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("mcq", "Multiple Choice"),
                    ("written", "Written"),
                    ("complete", "Complete"),
                    ("order_events", "Order Events"),
                    ("match_related", "Match Related"),
                ],
                max_length=512,
            ),
        ),
    ]
