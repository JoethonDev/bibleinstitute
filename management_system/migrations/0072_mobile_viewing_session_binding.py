import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0071_mobile_biometric_credential"),
    ]

    operations = [
        migrations.AddField(
            model_name="viewingsession",
            name="mobile_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="viewing_sessions",
                to="management_system.studentmobilesession",
            ),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="access_channel",
            field=models.CharField(
                choices=[("web", "Web"), ("mobile", "Mobile")],
                default="web",
                max_length=12,
            ),
        ),
        migrations.AddConstraint(
            model_name="viewingsession",
            constraint=models.CheckConstraint(
                condition=(
                    Q(access_channel="web", mobile_session__isnull=True)
                    | Q(access_channel="mobile", mobile_session__isnull=False)
                ),
                name="viewing_session_channel_binding",
            ),
        ),
    ]
