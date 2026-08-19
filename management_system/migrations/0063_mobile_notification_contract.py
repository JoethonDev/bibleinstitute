import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0062_academicpayment"),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentMobileSession",
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
                ("token_digest", models.CharField(editable=False, max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "revoked_at", "expires_at"],
                        name="mobile_session_user_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="MobilePushDevice",
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
                    "expo_push_token",
                    models.CharField(max_length=255, unique=True),
                ),
                ("installation_id", models.CharField(max_length=128)),
                (
                    "platform",
                    models.CharField(
                        choices=[("android", "Android"), ("ios", "iOS")],
                        max_length=16,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("disabled_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mobile_push_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["user", "is_active"],
                        name="mobile_device_user_active_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "installation_id"),
                        name="mobile_device_user_install_unique",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(is_active=True, disabled_at__isnull=True)
                            | models.Q(is_active=False, disabled_at__isnull=False)
                        ),
                        name="mobile_device_active_state_consistent",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StudentNotification",
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
                    "notification_type",
                    models.CharField(
                        choices=[
                            ("lesson_published", "Lesson published"),
                            ("quiz_opening", "Exam opening"),
                        ],
                        max_length=32,
                    ),
                ),
                ("effective_opening_at", models.DateTimeField(blank=True, null=True)),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("title_ar", models.CharField(max_length=255)),
                ("body_ar", models.TextField()),
                ("title_en", models.CharField(max_length=255)),
                ("body_en", models.TextField()),
                (
                    "navigation_type",
                    models.CharField(
                        choices=[("lesson", "Lesson"), ("quiz", "Exam")],
                        max_length=16,
                    ),
                ),
                ("offering_id", models.PositiveBigIntegerField()),
                ("entity_id", models.PositiveBigIntegerField()),
                ("scheduled_for", models.DateTimeField()),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "lesson",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="student_notifications",
                        to="management_system.lesson",
                    ),
                ),
                (
                    "quiz",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="student_notifications",
                        to="management_system.quiz",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["student", "read_at", "created_at"],
                        name="student_notification_inbox_idx",
                    ),
                    models.Index(
                        fields=["scheduled_for", "notification_type"],
                        name="student_notification_due_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                notification_type="lesson_published",
                                quiz__isnull=True,
                                navigation_type="lesson",
                                effective_opening_at__isnull=True,
                            )
                            | models.Q(
                                notification_type="quiz_opening",
                                lesson__isnull=True,
                                navigation_type="quiz",
                                effective_opening_at__isnull=False,
                            )
                        ),
                        name="student_notification_source_type_match",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(offering_id__gt=0) & models.Q(entity_id__gt=0),
                        name="student_notification_route_ids_positive",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PushDelivery",
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
                ("expo_ticket_id", models.CharField(blank=True, default="", max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("sending", "Sending"),
                            ("ticketed", "Ticketed"),
                            ("delivered", "Delivered"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("error_code", models.CharField(blank=True, default="", max_length=80)),
                ("error_message", models.CharField(blank=True, default="", max_length=500)),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("ticketed_at", models.DateTimeField(blank=True, null=True)),
                ("receipt_checked_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_deliveries",
                        to="management_system.mobilepushdevice",
                    ),
                ),
                (
                    "notification",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_deliveries",
                        to="management_system.studentnotification",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="push_delivery_due_idx",
                    ),
                    models.Index(
                        fields=["notification", "status"],
                        name="push_delivery_notification_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("notification", "device"),
                        name="push_delivery_notification_device_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(attempt_count__gte=0) & models.Q(attempt_count__lte=3),
                        name="push_delivery_attempts_bounded",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="telegramnotificationdelivery",
            name="student_notification",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="telegram_deliveries",
                to="management_system.studentnotification",
            ),
        ),
    ]
