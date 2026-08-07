from django.db import migrations, models
import django.db.models.deletion


def ensure_pgcrypto(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        raise RuntimeError(
            "Telegram configuration requires PostgreSQL with the pgcrypto extension."
        )
    with connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0052_signup_profile_fields"),
    ]

    operations = [
        migrations.RunPython(ensure_pgcrypto, migrations.RunPython.noop),
        migrations.AddField(
            model_name="lesson",
            name="description",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="TelegramBotConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "singleton",
                    models.CharField(
                        default="default",
                        editable=False,
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("token_ciphertext", models.TextField(blank=True, default="")),
                (
                    "webhook_secret_ciphertext",
                    models.TextField(blank=True, default=""),
                ),
                ("bot_id", models.BigIntegerField(blank=True, null=True)),
                ("bot_username", models.CharField(blank=True, default="", max_length=255)),
                ("is_active", models.BooleanField(default=False)),
                ("webhook_url", models.URLField(blank=True, default="")),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("deactivated_at", models.DateTimeField(blank=True, null=True)),
                ("last_validated_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "activated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="telegram_activated_configs",
                        to="management_system.user",
                    ),
                ),
                (
                    "deactivated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="telegram_deactivated_configs",
                        to="management_system.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "Telegram Bot Configuration",
                "verbose_name_plural": "Telegram Bot Configurations",
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(is_active=True),
                        fields=("is_active",),
                        name="telegram_one_active_config",
                    ),
                ],
            },
        ),
    ]
