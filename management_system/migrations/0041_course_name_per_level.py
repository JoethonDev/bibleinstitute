from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0040_make_academic_scope_canonical"),
    ]

    operations = [
        migrations.AlterField(
            model_name="course",
            name="name",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="course",
            constraint=models.UniqueConstraint(
                fields=("name", "level"),
                name="course_unique_name_level",
            ),
        ),
    ]
