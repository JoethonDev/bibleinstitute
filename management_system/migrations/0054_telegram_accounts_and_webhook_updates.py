from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0053_telegram_configuration_and_lesson_description"),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramAccount",
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
                ("telegram_user_id", models.BigIntegerField(unique=True)),
                ("telegram_chat_id", models.BigIntegerField(unique=True)),
                ("linked_at", models.DateTimeField(auto_now_add=True)),
                ("last_inbound_at", models.DateTimeField(blank=True, null=True)),
                ("last_outbound_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="telegram_account",
                        to="management_system.user",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["is_active", "user"],
                        name="telegram_account_active_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TelegramLinkToken",
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
                ("token_digest", models.CharField(max_length=64, unique=True)),
                ("token_ciphertext", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "linked_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="link_tokens",
                        to="management_system.telegramaccount",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="telegram_link_tokens",
                        to="management_system.user",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "created_at"],
                        name="telegram_link_user_created_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
                        fields=("user",),
                        name="telegram_one_current_link_token",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TelegramWebhookUpdate",
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
                ("bot_id", models.BigIntegerField()),
                ("update_id", models.BigIntegerField()),
                ("payload", models.JSONField(default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("processing_error", models.TextField(blank=True, default="")),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["bot_id", "processed_at"],
                        name="telegram_webhook_pending_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("bot_id", "update_id"),
                        name="telegram_webhook_bot_update_unique",
                    ),
                ],
            },
        ),
    ]
