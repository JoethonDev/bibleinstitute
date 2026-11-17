# Phase 7 forward migration: committed edge-gate token state.
# Additive only. Nothing reads this table while the delivery mode stays worker.

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0126_media_delivery_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaGateToken",
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
                        default="default", editable=False, max_length=20, unique=True
                    ),
                ),
                ("current_token", models.CharField(max_length=64)),
                (
                    "current_started_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "previous_token",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_error",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Media Gate Token",
                "verbose_name_plural": "Media Gate Tokens",
            },
        ),
        migrations.AlterField(
            model_name="mediadeliverysettings",
            name="segment_delivery_mode",
            field=models.CharField(
                choices=[
                    ("direct_r2", "Direct R2 presigned redirect (302)"),
                    ("worker", "Cloudflare Worker proxy (legacy)"),
                    ("edge_gate", "Cloudflare edge gate"),
                ],
                default="worker",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="mediagatetoken",
            constraint=models.CheckConstraint(
                condition=models.Q(("current_token", ""), _negated=True),
                name="media_gate_current_token_nonempty",
            ),
        ),
    ]
