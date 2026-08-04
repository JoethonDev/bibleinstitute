from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0045_expand_promotion_outcome"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="enrollment",
            index=models.Index(
                fields=["academic_year_level", "enrollment_type", "course_offering"],
                name="enrollment_scope_type_idx",
            ),
        ),
    ]
