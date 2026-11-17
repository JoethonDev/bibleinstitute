# Phase 8 forward migration: durable progress windows, replay receipts,
# online-attendance policy and bounded-cleanup indexes.
# Additive only; no existing row is rewritten or reinterpreted.

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0127_phase07_media_gate_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancepolicy",
            name="online_full_days",
            field=models.PositiveSmallIntegerField(
                default=7, verbose_name="Full score within days"
            ),
        ),
        migrations.AddField(
            model_name="attendancepolicy",
            name="online_partial_days",
            field=models.PositiveSmallIntegerField(
                default=14, verbose_name="Partial score within days"
            ),
        ),
        migrations.AddField(
            model_name="attendancepolicy",
            name="online_partial_value",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.50"),
                max_digits=3,
                verbose_name="Partial score value",
            ),
        ),
        migrations.AddField(
            model_name="lectureprogress",
            name="edge_slack_remaining_seconds",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("15.00"), max_digits=4
            ),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="activity_cursor_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="edge_earned_seconds_consumed",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=7
            ),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="last_acknowledgement",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="last_payload_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="segment_delivery_mode",
            field=models.CharField(
                choices=[
                    ("legacy", "Legacy verified delivery"),
                    ("direct_r2", "Direct R2 presigned redirect (302)"),
                    ("worker", "Cloudflare Worker proxy (legacy)"),
                    ("edge_gate", "Cloudflare edge gate"),
                ],
                default="legacy",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="superseded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="viewingsession",
            index=models.Index(fields=["expires_at"], name="vs_expiry_idx"),
        ),
        migrations.AddIndex(
            model_name="viewingsession",
            index=models.Index(
                condition=models.Q(("superseded_at__isnull", False)),
                fields=["superseded_at"],
                name="vs_superseded_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="viewingsession",
            index=models.Index(
                condition=models.Q(("ended_at__isnull", False)),
                fields=["ended_at"],
                name="vs_ended_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="viewingsession",
            index=models.Index(
                condition=models.Q(
                    ("ended_at__isnull", True),
                    ("started_at__isnull", False),
                    ("superseded_at__isnull", True),
                ),
                fields=["student", "lesson", "part_id", "expires_at"],
                name="vs_open_progress_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancepolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(("online_full_days__gte", 1)),
                name="attendance_online_full_days_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancepolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("online_partial_days__gte", models.F("online_full_days"))
                ),
                name="attendance_online_partial_days_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancepolicy",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("online_partial_value__gte", 0), ("online_partial_value__lte", 1)
                ),
                name="attendance_online_partial_value_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="lectureprogress",
            constraint=models.CheckConstraint(
                condition=models.Q(("edge_slack_remaining_seconds__gte", 0)),
                name="lecture_edge_slack_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="viewingsession",
            constraint=models.CheckConstraint(
                condition=models.Q(("edge_earned_seconds_consumed__gte", 0)),
                name="vs_edge_earned_nonneg",
            ),
        ),
    ]
