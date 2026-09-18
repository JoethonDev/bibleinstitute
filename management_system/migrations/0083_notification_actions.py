from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0082_single_announcement_copy"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="action_label",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="announcement",
            name="action_url",
            field=models.CharField(blank=True, default="", max_length=2048),
        ),
        migrations.AddField(
            model_name="studentnotification",
            name="action_label",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="studentnotification",
            name="action_url",
            field=models.CharField(blank=True, default="", max_length=2048),
        ),
        migrations.AddConstraint(
            model_name="announcement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(action_label="", action_url="")
                    | (models.Q(action_label__gt="") & models.Q(action_url__gt=""))
                ),
                name="announcement_action_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="studentnotification",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(action_label="", action_url="")
                    | (models.Q(action_label__gt="") & models.Q(action_url__gt=""))
                ),
                name="student_notification_action_pair",
            ),
        ),
    ]
