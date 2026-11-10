from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("management_system", "0115_mediaprocessingjob_worker_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="interrupted_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
