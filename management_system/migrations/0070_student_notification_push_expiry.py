from datetime import timedelta

from django.db import migrations, models
from django.db.models import DateTimeField, ExpressionWrapper, F, Value


def backfill_push_expiry(apps, schema_editor):
    notification_model = apps.get_model("management_system", "StudentNotification")
    if notification_model.objects.filter(
        notification_type="quiz_opening",
        effective_closing_at__isnull=True,
    ).exists():
        raise RuntimeError(
            "Cannot backfill push expiry: a quiz notification has no effective closing time."
        )
    notification_model.objects.filter(
        notification_type="quiz_opening",
        effective_closing_at__isnull=False,
    ).update(push_expires_at=F("effective_closing_at"))
    notification_model.objects.filter(push_expires_at__isnull=True).update(
        push_expires_at=ExpressionWrapper(
            F("scheduled_for") + Value(timedelta(days=1)),
            output_field=DateTimeField(),
        )
    )


def clear_push_expiry(apps, schema_editor):
    apps.get_model("management_system", "StudentNotification").objects.update(
        push_expires_at=None,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0069_mobile_device_installation_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentnotification",
            name="push_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="studentnotification",
            index=models.Index(
                fields=["scheduled_for", "push_expires_at", "cancelled_at"],
                name="student_notif_push_due_idx",
            ),
        ),
        migrations.RunPython(backfill_push_expiry, clear_push_expiry),
        migrations.AlterField(
            model_name="studentnotification",
            name="push_expires_at",
            field=models.DateTimeField(),
        ),
        migrations.AddConstraint(
            model_name="studentnotification",
            constraint=models.CheckConstraint(
                condition=models.Q(push_expires_at__gt=models.F("scheduled_for")),
                name="student_notification_push_expiry_after_schedule",
            ),
        ),
    ]
