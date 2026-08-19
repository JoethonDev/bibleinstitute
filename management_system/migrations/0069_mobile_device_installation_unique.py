from django.db import migrations, models
from django.db.models import Count


def validate_installation_ownership(apps, schema_editor):
    device_model = apps.get_model("management_system", "MobilePushDevice")
    duplicates = list(
        device_model.objects.values("installation_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .values_list("installation_id", "row_count")[:20]
    )
    if duplicates:
        formatted = ", ".join(f"{installation_id!r} ({count})" for installation_id, count in duplicates)
        raise RuntimeError(
            "Cannot enforce unique mobile installation ownership; duplicate "
            f"installation IDs require explicit operator resolution: {formatted}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0068_student_notification_window_constraint"),
    ]

    operations = [
        migrations.RunPython(validate_installation_ownership, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="mobilepushdevice",
            name="mobile_device_user_install_unique",
        ),
        migrations.AddConstraint(
            model_name="mobilepushdevice",
            constraint=models.UniqueConstraint(
                fields=("installation_id",),
                name="mobile_device_install_unique",
            ),
        ),
    ]
