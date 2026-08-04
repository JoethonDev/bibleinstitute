from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0044_course_offering_attendance_and_evaluation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="promotionhistory",
            name="outcome",
            field=models.CharField(max_length=32),
        ),
    ]
