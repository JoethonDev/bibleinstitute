import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0063_mobile_notification_contract"),
    ]

    operations = [
        migrations.CreateModel(
            name="MobileOtpChallenge",
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
                    "challenge_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("installation_id", models.CharField(blank=True, default="", max_length=128)),
                ("otp_digest", models.CharField(editable=False, max_length=64)),
                ("purpose", models.CharField(default="login", max_length=16)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("consumed", "Consumed"),
                            ("expired", "Expired"),
                            ("locked", "Locked"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("telegram_message_id", models.BigIntegerField(blank=True, null=True)),
                ("delivery_error", models.CharField(blank=True, default="", max_length=500)),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_otp_challenges",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["student", "purpose", "status", "expires_at"],
                        name="mobile_otp_student_status_idx",
                    ),
                    models.Index(
                        fields=["status", "expires_at"],
                        name="mobile_otp_due_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(status__in=["queued", "sending", "sent"]),
                        fields=("student", "purpose", "installation_id"),
                        name="mobile_otp_one_active_challenge",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(attempt_count__gte=0) & models.Q(attempt_count__lte=5),
                        name="mobile_otp_attempts_bounded",
                    ),
                ],
            },
        ),
    ]
