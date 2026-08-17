from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.db.models import Sum, Prefetch, Q, prefetch_related_objects
from django.utils import timezone
from django.utils.timezone import now
from datetime import date, datetime, timedelta
import json
from django.utils.translation import gettext_lazy as _
from django.utils import translation
from django.db.models import Max
from django.conf import settings
from .utils.timezones import format_application_datetime

# Constants
MANAGEMENT_ROLES = ["admin", "staff"]

# Helper Function
def assign_academic_date():
    today = date.today()
    if today.month < 8 :
        today = today.replace(year=today.year-1)
    return today.replace(month=8)


def default_meeting_weekdays():
    return [6, 1]  # Sunday and Tuesday using datetime.date.weekday().


class PublicationStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    ARCHIVED = "archived", _("Archived")


class MediaProcessingStatus(models.TextChoices):
    AWAITING_UPLOAD = "awaiting_upload", _("Awaiting upload")
    QUEUED = "queued", _("Queued")
    PROCESSING = "processing", _("Processing")
    UPLOADING = "uploading", _("Uploading")
    VERIFYING = "verifying", _("Verifying")
    SUCCEEDED = "succeeded", _("Succeeded")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class MediaProcessingPhase(models.TextChoices):
    UPLOAD = "upload", _("Upload")
    QUEUED = "queued", _("Queued")
    DOWNLOAD = "download", _("Downloading source")
    PROBE = "probe", _("Inspecting source")
    ENCODE = "encode", _("Processing media")
    UPLOAD_OUTPUT = "upload_output", _("Uploading output")
    VERIFY = "verify", _("Verifying output")
    ATTACH = "attach", _("Attaching to lesson")
    COMPLETE = "complete", _("Complete")
    FAILED = "failed", _("Failed")


class MediaAttachmentStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", _("Not requested")
    PENDING = "pending", _("Pending")
    ATTACHED = "attached", _("Attached")
    FAILED = "failed", _("Failed")


# Create your models here.
class Role(models.Model):
    ROLES = [
        ("admin", _("Admin")),
        ("staff", _("Staff")),
        ("moderator", _("Moderator")),
        ("student", _("Student")),
    ]
    # Fields
    role = models.CharField(
        max_length=20,
        choices=ROLES, 
        unique=True
    )

    def __str__(self):
        return self.get_role_display()
    
    @staticmethod
    def get_default():
        return Role.objects.get_or_create(role='student')[0]
    
    @staticmethod
    def get_by_readable_value(readable_value):
        role_value = None
        for role in Role.ROLES:
            if readable_value == str(_(role[1])): # Compare with translated value
                role_value = role[0]
                break
        return Role.objects.get(role=role_value)

    @staticmethod
    def get_readable_values():
        return [str(_(role.get_role_display())) for role in Role.objects.all()] # Translate display values

class User(AbstractUser):
    role = models.ForeignKey(Role, on_delete=models.DO_NOTHING, null=True, blank=True)
    joined_date = models.DateField(null=False, default=assign_academic_date)
    time_zone = models.CharField(max_length=64, default=settings.TIME_ZONE)

    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    priest_name = models.CharField(max_length=255, null=True, blank=True)
    priest_phone = models.CharField(max_length=20, null=True, blank=True)
    church = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=255, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    education_or_job = models.CharField(max_length=255, null=True, blank=True)
    service = models.CharField(max_length=255, null=True, blank=True)

    STUDY_MODES = [("online", _("Online")), ("offline", _("Offline"))]
    study_mode = models.CharField(max_length=10, choices=STUDY_MODES, null=True, blank=True)
    study_mode_override = models.BooleanField(default=False)

    IDENTITY_TYPES = [("national_id", _("National ID")), ("passport", _("Passport"))]
    identity_type = models.CharField(max_length=20, choices=IDENTITY_TYPES, null=True, blank=True)
    identity_number = models.CharField(max_length=50, null=True, blank=True, unique=True)

    identity_front_key = models.CharField(max_length=500, null=True, blank=True)
    identity_back_key = models.CharField(max_length=500, null=True, blank=True)
    payment_key = models.CharField(max_length=500, null=True, blank=True)
    profile_image_key = models.CharField(max_length=500, null=True, blank=True)

    APPLICATION_STATUSES = [("pending", _("Pending")), ("active", _("Active")), ("declined", _("Declined"))]
    application_status = models.CharField(max_length=10, choices=APPLICATION_STATUSES, default="active")

    decided_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(null=True, blank=True)

    qr_token = models.CharField(max_length=64, unique=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.role_id:
            try:
                default_role = Role.objects.get(role='student')
                self.role = default_role
            except Role.DoesNotExist:
                pass
        super().save(*args, **kwargs)
    
    def serialize_pagination(self):
        return {
            "rows" : [self.username, f"{self.first_name} {self.last_name}", str(_(self.role.get_role_display())), self.joined_date.strftime("%d/%m/%Y"), self.last_login],
            "url" : reverse_lazy("user-profile", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
        return [_("Username"), _("Name"), _("Role"), _("Joined Date"), _("Last Login")]


class TelegramBotConfig(models.Model):
    """The single encrypted Telegram bot configuration for this deployment."""

    singleton = models.CharField(max_length=20, unique=True, default="default", editable=False)
    token_ciphertext = models.TextField(blank=True, default="")
    webhook_secret_ciphertext = models.TextField(blank=True, default="")
    bot_id = models.BigIntegerField(null=True, blank=True)
    bot_username = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=False)
    webhook_url = models.URLField(blank=True, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_activated_configs",
    )
    deactivated_by = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_deactivated_configs",
    )
    last_validated_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="telegram_one_active_config",
            ),
        ]
        verbose_name = _("Telegram Bot Configuration")
        verbose_name_plural = _("Telegram Bot Configurations")

    def __str__(self):
        return self.bot_username or str(_("Telegram Bot Configuration"))


class TelegramAccount(models.Model):
    """A single private Telegram identity linked to one eligible LMS user."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="telegram_account",
    )
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_chat_id = models.BigIntegerField(unique=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_outbound_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "user"], name="telegram_account_active_idx"),
        ]

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class TelegramLinkToken(models.Model):
    """A permanent-until-used one-time LMS-to-Telegram linking token."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="telegram_link_tokens")
    token_digest = models.CharField(max_length=64, unique=True)
    token_ciphertext = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    linked_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="link_tokens",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(used_at__isnull=True, revoked_at__isnull=True),
                name="telegram_one_current_link_token",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"], name="telegram_link_user_created_idx"),
        ]

    def __str__(self):
        return f"Telegram link token for {self.user.username}"


class TelegramWebhookUpdate(models.Model):
    """Durable deduplication record for accepted private-chat webhook updates."""

    bot_id = models.BigIntegerField()
    update_id = models.BigIntegerField()
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bot_id", "update_id"],
                name="telegram_webhook_bot_update_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["bot_id", "processed_at"], name="telegram_webhook_pending_idx"),
        ]

    def __str__(self):
        return f"Telegram update {self.update_id}"


class TelegramNotificationDelivery(models.Model):
    """Durable, idempotent lesson/exam notification delivery state."""

    class NotificationType(models.TextChoices):
        LESSON_PUBLISHED = "lesson_published", _("Lesson published")
        QUIZ_OPENING = "quiz_opening", _("Exam opening")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    idempotency_key = models.CharField(max_length=255, unique=True)
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="telegram_notification_deliveries")
    telegram_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_deliveries",
    )
    lesson = models.ForeignKey(
        "Lesson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_notification_deliveries",
    )
    quiz = models.ForeignKey(
        "Quiz",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_notification_deliveries",
    )
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        notification_type="lesson_published",
                        lesson__isnull=False,
                        quiz__isnull=True,
                    )
                    | models.Q(
                        notification_type="quiz_opening",
                        lesson__isnull=True,
                        quiz__isnull=False,
                    )
                ),
                name="telegram_delivery_source_matches_type",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="tg_delivery_due_idx"),
            models.Index(fields=["user", "status"], name="tg_delivery_user_status_idx"),
            models.Index(fields=["notification_type", "lesson"], name="tg_delivery_lesson_idx"),
            models.Index(fields=["notification_type", "quiz"], name="tg_delivery_quiz_idx"),
        ]

    def __str__(self):
        return self.idempotency_key


class TelegramConversation(models.Model):
    """One durable support conversation per student at a time."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLAIMED = "claimed", _("Claimed")
        HANDLED = "handled", _("Handled")
        BLOCKED = "blocked", _("Blocked")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="telegram_conversations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    claimed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_telegram_conversations",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status__in=["open", "claimed"]),
                name="telegram_one_active_conversation",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "last_message_at"], name="tg_conversation_status_idx"),
            models.Index(fields=["user", "status"], name="tg_conversation_user_idx"),
        ]

    def __str__(self):
        return f"Telegram conversation {self.pk}"


class TelegramMessage(models.Model):
    """Inbound/outbound support history without exposing Telegram identities."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", _("Inbound")
        OUTBOUND = "outbound", _("Outbound")

    class ContentType(models.TextChoices):
        TEXT = "text", _("Text")
        PHOTO = "photo", _("Photo")
        DOCUMENT = "document", _("Document")
        VIDEO = "video", _("Video")
        DIGEST = "digest", _("Digest")
        UNSUPPORTED = "unsupported", _("Unsupported")

    class DeliveryStatus(models.TextChoices):
        RECEIVED = "received", _("Received")
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    conversation = models.ForeignKey(TelegramConversation, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=16, choices=Direction.choices)
    sender_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_messages",
    )
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_update_id = models.BigIntegerField(null=True, blank=True)
    reply_to_telegram_message_id = models.BigIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=16, choices=ContentType.choices)
    text = models.TextField(blank=True, default="")
    delivery_status = models.CharField(max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.RECEIVED)
    delivery_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["telegram_update_id"],
                condition=models.Q(telegram_update_id__isnull=False),
                name="telegram_message_update_unique",
            ),
            models.UniqueConstraint(
                fields=["telegram_chat_id", "telegram_message_id"],
                condition=models.Q(telegram_chat_id__isnull=False, telegram_message_id__isnull=False),
                name="telegram_message_chat_id_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="tg_message_conversation_idx"),
            models.Index(fields=["delivery_status", "created_at"], name="tg_message_delivery_idx"),
        ]

    def __str__(self):
        return f"Telegram message {self.pk}"


class TelegramAttachment(models.Model):
    """Controlled R2 metadata for a supported Telegram attachment."""

    message = models.ForeignKey(TelegramMessage, on_delete=models.CASCADE, related_name="attachments")
    telegram_file_id = models.CharField(max_length=255)
    media_type = models.CharField(max_length=16)
    r2_key = models.CharField(max_length=1024, blank=True, default="")
    original_name = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    is_available = models.BooleanField(default=False)
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "telegram_file_id"],
                name="telegram_attachment_file_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["media_type", "is_available"], name="tg_attachment_media_idx"),
        ]

    def __str__(self):
        return self.original_name or self.telegram_file_id


class TelegramBroadcast(models.Model):
    """An admin-confirmed active-year message sent outside support history."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    academic_year = models.ForeignKey(
        "AcademicYear",
        on_delete=models.PROTECT,
        related_name="telegram_broadcasts",
    )
    level = models.ForeignKey(
        "Level",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="telegram_broadcasts",
    )
    message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_telegram_broadcasts",
    )
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_telegram_broadcasts",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="tg_broadcast_status_idx"),
            models.Index(fields=["academic_year", "level"], name="tg_broadcast_target_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(message=""),
                name="tg_broadcast_message_not_empty",
            ),
        ]

    def __str__(self):
        return f"Telegram broadcast {self.pk}"


class TelegramBroadcastAttachment(models.Model):
    """Private R2 metadata for one broadcast attachment."""

    broadcast = models.ForeignKey(
        TelegramBroadcast,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    r2_key = models.CharField(max_length=1024)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    is_available = models.BooleanField(default=False)
    error = models.CharField(max_length=500, blank=True, default="")
    ordering = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordering", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "r2_key"],
                name="tg_broadcast_attachment_key_unique",
            ),
        ]

    def __str__(self):
        return self.original_name


class TelegramBroadcastRecipient(models.Model):
    """Immutable-at-queue-time recipient snapshot and delivery state."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    broadcast = models.ForeignKey(
        TelegramBroadcast,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_broadcast_recipients",
    )
    telegram_account = models.ForeignKey(
        TelegramAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="broadcast_recipients",
    )
    telegram_chat_id = models.BigIntegerField()
    user_username = models.CharField(max_length=150, blank=True, default="")
    user_display_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    scheduled_for = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "user"],
                name="tg_broadcast_recipient_user_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["broadcast", "status", "scheduled_for"], name="tg_broadcast_due_idx"),
            models.Index(fields=["broadcast", "status"], name="tg_bcast_recipient_status_idx"),
        ]

    def __str__(self):
        return f"Broadcast {self.broadcast_id} → {self.telegram_chat_id}"


class OfflineCity(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class Level(models.Model):
    ordering = models.PositiveIntegerField(unique=True)
    name_en = models.CharField(max_length=255, blank=True, default="")
    name_ar = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordering"]
        verbose_name = _("Level")
        verbose_name_plural = _("Levels")

    def __str__(self):
        lang = translation.get_language()
        name = self.name_ar if lang == "ar" else self.name_en
        if name:
            return name
        return _("Level %(ordering)d") % {"ordering": self.ordering}

    def clean(self):
        super().clean()
        if self.ordering is not None and self.ordering < 1:
            raise ValidationError({"ordering": _("Ordering must be 1 or greater.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.name_en or self.name_ar or str(self.ordering)


class Course(models.Model):
    name = models.CharField(max_length=255, null=False)
    description = models.TextField(null=True)
    instructor = models.CharField(max_length=255, null=True)
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="courses")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "level"],
                name="course_unique_name_level",
            ),
        ]

    def __str__(self):
        return f"Course : {self.name} in level : {self.level.ordering}"

    # ── Instance methods ───────────────────────────────────────────
    def serialize_pagination(self):
        return {
            "rows" : [self.name, self.description, self.level.display_name, self.instructor],
            "url" : reverse_lazy("course-view", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
            return [_("Name"), _("Description"), _("Level"), _("Instructor")]

    def get_name_year(self):
        return f"{self.name} - {self.level.display_name}"

    def fetch_quizzes(self, user):
        return Quiz.objects.filter(course_offering__course=self)


class AcademicYearLevel(models.Model):
    academic_year = models.ForeignKey("AcademicYear", on_delete=models.CASCADE, related_name="level_links")
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="year_links")
    meeting_weekdays = models.JSONField(default=default_meeting_weekdays)

    class Meta:
        unique_together = [("academic_year", "level")]
        ordering = ["academic_year__ordering", "level__ordering"]
        verbose_name = _("Academic Year Level")
        verbose_name_plural = _("Academic Year Levels")

    def __str__(self):
        return f"{self.academic_year.name} → {self.level.display_name}"

    def clean(self):
        super().clean()
        if not isinstance(self.meeting_weekdays, list):
            raise ValidationError({"meeting_weekdays": _("Meeting weekdays must be a list.")})
        if any(not isinstance(day, int) or isinstance(day, bool) or day < 0 or day > 6 for day in self.meeting_weekdays):
            raise ValidationError({"meeting_weekdays": _("Meeting weekdays must be unique values from 0 through 6.")})
        if len(self.meeting_weekdays) != len(set(self.meeting_weekdays)):
            raise ValidationError({"meeting_weekdays": _("Meeting weekdays must not repeat.")})


class AcademicPayment(models.Model):
    """The receipt for one student's academic-year-level payment."""

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="academic_payments")
    academic_year_level = models.ForeignKey(
        AcademicYearLevel,
        on_delete=models.PROTECT,
        related_name="academic_payments",
    )
    receipt_key = models.CharField(max_length=500)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year_level"],
                name="academic_payment_student_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["academic_year_level", "student"],
                name="academic_payment_student_idx",
            ),
        ]

    def __str__(self):
        return f"{self.student.username} — {self.academic_year_level}"


class AcademicYearLevelMeeting(models.Model):
    academic_year_level = models.ForeignKey(
        AcademicYearLevel,
        on_delete=models.CASCADE,
        related_name="calendar_meetings",
    )
    meeting_date = models.DateField()
    course_offering = models.ForeignKey(
        "CourseOffering",
        on_delete=models.PROTECT,
        related_name="calendar_meetings",
    )

    class Meta:
        ordering = ["meeting_date", "academic_year_level__level__ordering"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year_level", "meeting_date"],
                name="academic_scope_meeting_date_unique",
            ),
        ]

    def __str__(self):
        return f"{self.academic_year_level} — {self.course_offering.course.name}"

    def clean(self):
        super().clean()
        academic_year = self.academic_year_level.academic_year
        if not academic_year.starts_on <= self.meeting_date <= academic_year.ends_on:
            raise ValidationError({"meeting_date": _("Meeting date must be inside the academic year.")})
        if self.meeting_date.weekday() not in (self.academic_year_level.meeting_weekdays or []):
            raise ValidationError({"meeting_date": _("Meeting date must use one of this level's locked meeting days.")})
        if self.course_offering.academic_year_level_id != self.academic_year_level_id:
            raise ValidationError({"course_offering": _("Course offering must belong to the selected academic scope.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True)
    levels = models.ManyToManyField(Level, through=AcademicYearLevel, related_name="academic_years")
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=False)
    ordering = models.PositiveIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordering"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gt=models.F("starts_on")),
                name="academic_year_ends_after_starts",
            ),
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="academic_year_one_active",
            ),
        ]

    def __str__(self):
        return f"{self.name}"

    def level_display(self):
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("levels")
        levels = sorted(prefetched, key=lambda level: level.ordering) if prefetched is not None else self.levels.order_by("ordering")
        return " / ".join(lvl.display_name for lvl in levels) if levels else ""

    def clean(self):
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": _("End date must be after start date.")})

        if self.is_active:
            current_years = AcademicYear.objects.filter(is_active=True)
            if self.pk:
                current_years = current_years.exclude(pk=self.pk)
            if current_years.exists():
                raise ValidationError({"is_active": _("Only one academic year can be active at a time.")})


class CourseOffering(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings")
    academic_year_level = models.ForeignKey(AcademicYearLevel, on_delete=models.PROTECT, related_name="course_offerings")
    instructor = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-academic_year_level__academic_year__starts_on", "course__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "academic_year_level"],
                name="course_offering_unique_course_year_level",
            ),
        ]

    def __str__(self):
        return f"{self.course.name} @ {self.academic_year_level}"

    def clean(self):
        super().clean()
        if self.course_id and self.academic_year_level_id:
            if self.course.level_id != self.academic_year_level.level_id:
                raise ValidationError({"course": _("Course level must match the academic-year level.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Enrollment(models.Model):
    class Type(models.TextChoices):
        NORMAL = "normal", _("Normal")
        REPEAT = "repeat", _("Repeat")
        REMEDIAL = "remedial", _("Remedial")
        MANUAL = "manual", _("Manual")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        INACTIVE = "inactive", _("Inactive")
        COMPLETED = "completed", _("Completed")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    academic_year_level = models.ForeignKey(AcademicYearLevel, on_delete=models.PROTECT, related_name="enrollments")
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    enrollment_type = models.CharField(max_length=20, choices=Type.choices, default=Type.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    enrolled_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_enrollments",
    )

    class Meta:
        ordering = ["-enrolled_at"]
        indexes = [
            models.Index(
                fields=["academic_year_level", "enrollment_type", "course_offering"],
                name="enrollment_scope_type_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(enrollment_type="normal", course_offering__isnull=True)
                    | models.Q(
                        enrollment_type__in=["repeat", "remedial", "manual"],
                        course_offering__isnull=False,
                    )
                ),
                name="enrollment_target_matches_type",
            ),
            models.UniqueConstraint(
                fields=["student", "academic_year_level"],
                condition=models.Q(course_offering__isnull=True),
                name="enrollment_unique_full_year",
            ),
            models.UniqueConstraint(
                fields=["student", "course_offering"],
                condition=models.Q(course_offering__isnull=False),
                name="enrollment_unique_course_offering",
            ),
        ]

    def __str__(self):
        target = self.course_offering or self.academic_year_level
        return f"{self.student.username} -> {target} ({self.enrollment_type})"

    def clean(self):
        super().clean()
        if self.course_offering_id and self.academic_year_level_id:
            if self.course_offering.academic_year_level_id != self.academic_year_level_id:
                raise ValidationError(_("Course offering must belong to the enrollment scope."))
        if self.course_offering_id:
            if self.enrollment_type == self.Type.NORMAL:
                raise ValidationError({"enrollment_type": _("Normal enrollment grants the full academic year.")})
        elif self.enrollment_type != self.Type.NORMAL:
            raise ValidationError({"course_offering": _("This enrollment type requires a course offering.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


QUIZ_TYPE_CODES = frozenset({"weekly", "final"})


class QuizType(models.Model):
    code = models.SlugField(unique=True)
    name_en = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255)

    def clean(self):
        super().clean()
        if self.code not in QUIZ_TYPE_CODES:
            raise ValidationError({"code": _("Quiz type must be weekly or final.")})

    def __str__(self):
        return self.name_en or self.code


class PromotionFormula(models.Model):
    academic_year_level = models.ForeignKey(
        AcademicYearLevel,
        on_delete=models.PROTECT,
        related_name="promotion_formulas",
    )
    course_offering = models.ForeignKey(
        "CourseOffering",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_formulas",
    )
    overall_pass_percent = models.DecimalField(max_digits=5, decimal_places=2)
    evaluation_starts_on = models.DateField()
    evaluation_ends_on = models.DateField()
    failed_courses_repeat_threshold = models.PositiveIntegerField(default=3)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="created_promotion_formulas"
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="updated_promotion_formulas"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year_level"],
                condition=models.Q(course_offering__isnull=True),
                name="promotion_formula_one_all_courses",
            ),
            models.UniqueConstraint(
                fields=["academic_year_level", "course_offering"],
                condition=models.Q(course_offering__isnull=False),
                name="promotion_formula_unique_offering",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year_level", "course_offering"], name="promo_formula_scope_idx"),
        ]

    def clean(self):
        super().clean()
        if not 0 <= self.overall_pass_percent <= 100:
            raise ValidationError({"overall_pass_percent": _("Percentage must be between 0 and 100.")})
        if self.failed_courses_repeat_threshold < 1:
            raise ValidationError({"failed_courses_repeat_threshold": _("The repeat threshold must be at least 1.")})
        if self.course_offering_id and self.academic_year_level_id:
            if self.course_offering.academic_year_level_id != self.academic_year_level_id:
                raise ValidationError({"course_offering": _("Course offering must belong to the selected academic scope.")})
        if self.academic_year_level_id and self.evaluation_starts_on and self.evaluation_ends_on:
            year = self.academic_year_level.academic_year
            if self.evaluation_ends_on < self.evaluation_starts_on:
                raise ValidationError({"evaluation_ends_on": _("Evaluation end date must not be before the start date.")})
            if not year.starts_on <= self.evaluation_starts_on <= year.ends_on:
                raise ValidationError({"evaluation_starts_on": _("Evaluation start date must be inside the academic year.")})
            if not year.starts_on <= self.evaluation_ends_on <= year.ends_on:
                raise ValidationError({"evaluation_ends_on": _("Evaluation end date must be inside the academic year.")})
            if self.evaluation_ends_on > min(timezone.localdate(), year.ends_on):
                raise ValidationError({"evaluation_ends_on": _("Evaluation end date cannot be in the future.")})

    @property
    def applies_to_all_courses(self):
        return self.course_offering_id is None


class PromotionRule(models.Model):
    class Metric(models.TextChoices):
        QUIZ = "quiz", _("Quiz")
        ATTENDANCE = "attendance", _("Attendance")

    formula = models.ForeignKey(PromotionFormula, on_delete=models.CASCADE, related_name="rules")
    metric = models.CharField(max_length=20, choices=Metric.choices)
    quiz_type = models.ForeignKey(
        QuizType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_rules",
    )
    weight_percent = models.DecimalField(max_digits=5, decimal_places=2)
    minimum_percent = models.DecimalField(max_digits=5, decimal_places=2)
    ordering = models.PositiveIntegerField()

    class Meta:
        ordering = ["ordering"]
        constraints = [
            models.UniqueConstraint(
                fields=["formula", "ordering"],
                name="promotion_rule_unique_ordering",
            ),
            models.UniqueConstraint(
                fields=["formula"],
                condition=models.Q(metric="attendance"),
                name="promotion_formula_one_attendance_rule",
            ),
            models.UniqueConstraint(
                fields=["formula", "quiz_type"],
                condition=models.Q(metric="quiz", quiz_type__isnull=False),
                name="promotion_formula_unique_quiz_type_rule",
            ),
        ]

    def clean(self):
        super().clean()
        for field in ("weight_percent", "minimum_percent"):
            value = getattr(self, field)
            if not 0 <= value <= 100:
                raise ValidationError({field: _("Percentage must be between 0 and 100.")})
        if self.metric == self.Metric.ATTENDANCE and self.quiz_type_id:
            raise ValidationError({"quiz_type": _("Attendance rules cannot select a quiz type.")})


class EvaluationResult(models.Model):
    class Status(models.TextChoices):
        PASS = "pass", _("Pass")
        FAIL = "fail", _("Fail")
        UNEVALUABLE = "unevaluable", _("Unevaluable")

    formula = models.ForeignKey(PromotionFormula, on_delete=models.PROTECT, related_name="results")
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.PROTECT,
        related_name="evaluation_results",
    )
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="evaluation_results",
    )
    computed_score = models.DecimalField(max_digits=6, decimal_places=2)
    computed_status = models.CharField(max_length=12, choices=Status.choices)
    final_status = models.CharField(max_length=12, choices=Status.choices)
    metric_snapshot = models.JSONField(default=dict)
    override_note = models.TextField(blank=True, default="")
    overridden_by = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="evaluation_overrides"
    )
    overridden_at = models.DateTimeField(null=True, blank=True)
    calculated_at = models.DateTimeField(auto_now=True)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["formula", "course_offering", "final_status"],
                name="eval_result_formula_status_idx",
            ),
            models.Index(
                fields=["enrollment", "course_offering"],
                name="eval_result_enrollment_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["formula", "enrollment", "course_offering"],
                condition=models.Q(course_offering__isnull=False),
                name="evaluation_result_unique_course",
            ),
            models.UniqueConstraint(
                fields=["formula", "enrollment"],
                condition=models.Q(course_offering__isnull=True),
                name="evaluation_result_unique_aggregate",
            ),
        ]

    def clean(self):
        super().clean()
        if self.course_offering_id:
            if self.course_offering.academic_year_level_id != self.formula.academic_year_level_id:
                raise ValidationError(_("Evaluation offering must belong to the formula scope."))
            if self.formula.course_offering_id and self.course_offering_id != self.formula.course_offering_id:
                raise ValidationError(_("Evaluation offering does not match the formula offering."))
        elif self.formula.course_offering_id:
            raise ValidationError(_("A course-specific formula requires a course result."))
        if self.computed_status not in self.Status.values or self.final_status not in self.Status.values:
            raise ValidationError(_("Invalid evaluation result status."))


class HistoricalAcademicSummary(models.Model):
    """Reviewed academic outcome imported when the source year has no LMS exams."""

    class Outcome(models.TextChoices):
        PENDING_REVIEW = "pending_review", _("Pending review")
        COMPLETED = "completed", _("Completed")
        PASSED = "passed", _("Passed")
        PARTIAL = "partial", _("Partial success")
        FAILED = "failed", _("Failed")

    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="historical_academic_summaries",
    )
    academic_year_level = models.ForeignKey(
        AcademicYearLevel,
        on_delete=models.PROTECT,
        related_name="historical_summaries",
    )
    source_key = models.CharField(max_length=255, unique=True)
    source_name = models.CharField(max_length=255)
    source_file = models.CharField(max_length=255, blank=True, default="")
    source_row = models.PositiveIntegerField(null=True, blank=True)
    outcome = models.CharField(
        max_length=20,
        choices=Outcome.choices,
        default=Outcome.PENDING_REVIEW,
    )
    notes = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_historical_summaries",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    certificate_eligible = models.BooleanField(default=False)
    promoted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year_level"],
                name="historical_summary_student_scope_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["academic_year_level", "outcome"], name="hist_summary_scope_outcome_idx"),
            models.Index(fields=["outcome", "promoted_at"], name="hist_summary_outcome_idx"),
        ]

    def __str__(self):
        return f"{self.student.username} — {self.academic_year_level}"


class PromotionHistory(models.Model):
    class Method(models.TextChoices):
        SYSTEM = "system", _("System")
        MANUAL_HISTORICAL = "manual_historical", _("Manual historical")

    evaluation_result = models.OneToOneField(
        EvaluationResult,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_history",
    )
    historical_summary = models.OneToOneField(
        HistoricalAcademicSummary,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="promotion_history",
    )
    source_enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, related_name="source_promotion_history"
    )
    destination_enrollment = models.ForeignKey(
        Enrollment, on_delete=models.PROTECT, null=True, blank=True, related_name="destination_promotion_history"
    )
    student = models.ForeignKey(User, on_delete=models.PROTECT, related_name="promotion_history")
    source_year_level = models.ForeignKey(
        AcademicYearLevel, on_delete=models.PROTECT, related_name="source_promotion_history"
    )
    destination_year_level = models.ForeignKey(
        AcademicYearLevel, on_delete=models.PROTECT, null=True, blank=True, related_name="destination_promotion_history"
    )
    outcome = models.CharField(max_length=32)
    promotion_method = models.CharField(max_length=24, choices=Method.choices, default=Method.SYSTEM)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    computed_status = models.CharField(max_length=20)
    final_status = models.CharField(max_length=20)
    override_note = models.TextField(blank=True, default="")
    override_actor = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name="promotion_override_history"
    )
    exceptional_offering_ids = models.JSONField(default=list)
    formula_snapshot = models.JSONField(default=dict)
    reason = models.TextField(blank=True, default="")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="recorded_promotions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(evaluation_result__isnull=True, historical_summary__isnull=True),
                name="promotion_history_source_required",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(evaluation_result__isnull=False, historical_summary__isnull=True)
                    | models.Q(evaluation_result__isnull=True, historical_summary__isnull=False)
                ),
                name="promotion_history_one_source",
            ),
            models.CheckConstraint(
                condition=~models.Q(promotion_method="manual_historical", reason=""),
                name="promotion_history_manual_reason_required",
            ),
        ]

    def clean(self):
        super().clean()
        if self.promotion_method not in self.Method.values:
            raise ValidationError(_("Invalid promotion method."))
        if self.promotion_method == self.Method.MANUAL_HISTORICAL and not self.reason.strip():
            raise ValidationError(_("A manual historical promotion requires a reason."))


class MigrationReviewItem(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", _("Info")
        WARNING = "warning", _("Warning")
        ERROR = "error", _("Error")

    item_type = models.CharField(max_length=50)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    message = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.severity}] {self.item_type}"


class AcademicHoliday(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField()
    name = models.CharField(max_length=255)

    class Meta:
        unique_together = [("academic_year", "date")]

    def __str__(self):
        return f"{self.name} - {self.date}"


class AttendanceRecord(models.Model):
    class Action(models.TextChoices):
        ENTRANCE = "entrance", _("Entrance")
        EXIT = "exit", _("Exit")

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance_records")
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attendance_records",
    )
    attendance_date = models.DateField()
    action = models.CharField(max_length=10, choices=Action.choices)
    scanned_at = models.DateTimeField(auto_now_add=True)
    scanned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="scanned_records")
    corrected_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="corrected_records")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course_offering", "attendance_date", "action"],
                name="attendance_unique_student_offering_date_action",
            ),
            models.UniqueConstraint(
                fields=["student", "attendance_date", "action"],
                condition=models.Q(course_offering__isnull=True),
                name="attendance_unique_student_unassigned_date_action",
            ),
        ]
        indexes = [
            models.Index(
                fields=["course_offering", "attendance_date"],
                name="attendance_offering_date_idx",
            ),
            models.Index(
                fields=["student", "course_offering", "attendance_date"],
                name="attendance_student_off_idx",
            ),
        ]

    def __str__(self):
        return f"{self.student.username} {self.action} on {self.attendance_date}"

    def clean(self):
        super().clean()
        if self.course_offering_id and self.attendance_date:
            year = self.course_offering.academic_year_level.academic_year
            if not year.starts_on <= self.attendance_date <= year.ends_on:
                raise ValidationError({"attendance_date": _("Attendance date must be inside the academic year.")})


class ViewingSession(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="viewing_sessions")
    lesson = models.ForeignKey("Lesson", on_delete=models.CASCADE, related_name="viewing_sessions")
    part_id = models.CharField(max_length=100)
    session_id = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    last_heartbeat = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["session_id"])]

    def __str__(self):
        return f"{self.student.username} - {self.lesson.name} part {self.part_id}"


class VerifiedSegmentRequest(models.Model):
    session = models.ForeignKey(ViewingSession, on_delete=models.CASCADE, related_name="verified_requests")
    segment_number = models.PositiveIntegerField()
    segment_key = models.CharField(max_length=500)
    signature = models.CharField(max_length=128, blank=True, default='')
    expires_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "segment_number"],
                name="unique_verified_segment_number_per_session",
            ),
        ]

    def __str__(self):
        return f"{self.session.session_id} - {self.segment_key}"


class LectureProgress(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lecture_progress")
    lesson = models.ForeignKey("Lesson", on_delete=models.CASCADE, related_name="lecture_progress")
    part_id = models.CharField(max_length=100)
    merged_ranges = models.JSONField(default=list)
    unique_seconds = models.PositiveIntegerField(default=0)
    percent = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("student", "lesson", "part_id")]

    def __str__(self):
        return f"{self.student.username} - {self.lesson.name} part {self.part_id}: {self.percent}%"


class Lesson(models.Model):
    name = models.CharField(max_length=255, null=False)
    description = models.TextField(null=True, blank=True)
    links = models.TextField() # Null must be false
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="lessons",
    )
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    created_date = models.DateField(null=False, default=date.today)
    updated_date = models.DateField(null=False, auto_now=True)


    def __str__(self):
        return f"{self.name} for course : {self.course_offering.course.name}"

    def clean(self):
        super().clean()
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def serialize_pagination(self):
        offering = self.course_offering
        return {
            "rows" : [
                self.name,
                offering.course.name,
                offering.academic_year_level.level.display_name,
                offering.academic_year_level.academic_year.name,
                _("Updated on %(date)s") % {'date': self.updated_date.strftime('%d/%m/%Y')},
            ],
            "url" : reverse_lazy("lesson-view", args=[self.pk,])
        }
    
    @staticmethod
    def get_columns():
        return [_("Name"), _("Course"), _("Level"), _("Academic Year"), _("Last Updated")]

    def can_access(self, user_join_date: date):
        try:
            end_range = user_join_date.replace(year=user_join_date.year + self.course_offering.course.level.ordering)
        except ValueError:
            end_range = user_join_date.replace(year=user_join_date.year + self.course_offering.course.level.ordering, day=28)
        return user_join_date <= self.created_date <= end_range

    @property
    def can_edit(self):
        return self.status == PublicationStatus.DRAFT

    def has_segment(self, segment: str):
        links = json.loads(self.links)
        segments_collection = [value['segments'] for value in links]
        for collection in segments_collection:
            if segment in collection:
                return True
        return False

    def serialize(self):
        links = json.loads(self.links)
        separated_parts = dict()
        # Type - File_id - Index
        for file in links:
            index = file['index']
            file_data = {
                "type" : file['type'],
                "file_id": file['file_id']
            }
            if index in separated_parts:
                separated_parts[index].append(file_data)
            else:
                separated_parts[index] = [file_data]
        
        return [value for value in separated_parts.values()]
    
class MediaProcessingJob(models.Model):
    """Durable state for one source-file media processing job."""

    public_id = models.UUIDField(unique=True, editable=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="media_processing_jobs",
    )
    requested_folder = models.CharField(max_length=1024)
    original_filename = models.CharField(max_length=255)
    output_base_name = models.CharField(max_length=255)
    source_kind = models.CharField(max_length=16)
    source_key = models.CharField(max_length=1024, unique=True)
    source_size = models.PositiveBigIntegerField()
    source_etag = models.CharField(max_length=255, blank=True, default="")
    source_sha256 = models.CharField(max_length=64, blank=True, default="")
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="media_processing_jobs",
    )
    part_id = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=24,
        choices=MediaProcessingStatus.choices,
        default=MediaProcessingStatus.AWAITING_UPLOAD,
    )
    phase = models.CharField(
        max_length=24,
        choices=MediaProcessingPhase.choices,
        default=MediaProcessingPhase.UPLOAD,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    failure_history = models.JSONField(default=list)
    output_keys = models.JSONField(default=list)
    manifest_key = models.CharField(max_length=1024, blank=True, default="")
    audio_manifest_key = models.CharField(max_length=1024, blank=True, default="")
    download_key = models.CharField(max_length=1024, blank=True, default="")
    attachment_status = models.CharField(
        max_length=20,
        choices=MediaAttachmentStatus.choices,
        default=MediaAttachmentStatus.NOT_REQUESTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    source_acknowledged_at = models.DateTimeField(null=True, blank=True)
    upload_ack_deadline_at = models.DateTimeField()
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_dispatched_at = models.DateTimeField(null=True, blank=True)
    staging_deleted_at = models.DateTimeField(null=True, blank=True)
    staging_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "last_dispatched_at"],
                name="media_job_status_dispatch_idx",
            ),
            models.Index(
                fields=["status", "last_heartbeat_at"],
                name="media_job_status_heartbeat_idx",
            ),
            models.Index(
                fields=["status", "upload_ack_deadline_at"],
                name="media_job_status_upload_idx",
            ),
            models.Index(
                fields=["created_by", "created_at"],
                name="media_job_creator_created_idx",
            ),
            models.Index(
                fields=["lesson", "attachment_status"],
                name="media_job_lesson_attach_idx",
            ),
        ]
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.original_filename} ({self.public_id})"


class Quiz(models.Model):
    name = models.CharField(max_length=64)
    quiz_type = models.ForeignKey(
        QuizType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quizzes",
    )
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        related_name="quizzes",
    )
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    total_grade = models.PositiveSmallIntegerField(default=50)
    opening_date = models.DateTimeField(default=now)
    closing_date = models.DateTimeField(default=now)
    created_date = models.DateField(auto_now=True)

    def __str__(self):
        return f"Quiz {self.name}"

    def clean(self):
        super().clean()
        if self.quiz_type_id and self.quiz_type and self.quiz_type.code not in QUIZ_TYPE_CODES:
            raise ValidationError({"quiz_type": _("Quiz type must be weekly or final.")})
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def can_edit(self):
        return self.status == PublicationStatus.DRAFT

    def serialize(self):
        return {
            "quiz_name" : self.name,
            "selected_offering_id": self.course_offering_id,
            "selected_quiz_type_id": self.quiz_type_id,
            "opening_date" : format_application_datetime(self.opening_date, "%Y-%m-%dT%H:%M:%S"),
            "closing_date" : format_application_datetime(self.closing_date, "%Y-%m-%dT%H:%M:%S"),
        }

    def serialize_pagination(self):
        offering = self.course_offering
        return {
            "rows" : [
                self.name,
                offering.course.name,
                offering.academic_year_level.level.display_name,
                offering.academic_year_level.academic_year.name,
                self.quiz_type.name_en if self.quiz_type else _("Unassigned"),
                self.total_grade,
                format_application_datetime(self.opening_date, "%H:%M:%S, %d/%m/%Y"),
                format_application_datetime(self.closing_date, "%H:%M:%S, %d/%m/%Y"),
            ],
            "url" : reverse_lazy("quiz-view", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
        return [
            _("Name"), _("Course"), _("Level"), _("Academic Year"), _("Quiz Type"),
            _("Grades"), _("Opening Date"), _("Closing Date"), _("Submissions"),
        ]

class QuizStudentOpening(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="student_openings")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="quiz_openings")
    opening_date = models.DateTimeField()
    closing_date = models.DateTimeField()
    granted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="granted_quiz_openings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["quiz", "student"],
                name="quiz_student_opening_unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.opening_date and self.closing_date and self.closing_date <= self.opening_date:
            raise ValidationError({"closing_date": _("Closing date must be after opening date.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Question(models.Model):
    QUESTION_TYPES = [
        ("mcq", _("Multiple Choice")),
        ("written", _("Written")),
        ("complete", _("Complete")),
        ("order_events", _("Order Events")),
        ("match_related", _("Match Related"))
    ]

    STRUCTURED_QUESTION_TYPES = {"order_events", "match_related"}

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    title = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=255, null=True)
    question_type = models.CharField(max_length=512, choices=QUESTION_TYPES)
    choices = models.TextField(null=True)
    config = models.JSONField(default=dict, blank=True)
    grade = models.PositiveSmallIntegerField(default=1)
    auto_grade = models.BooleanField(default=True)

    def get_config(self):
        return self.config if isinstance(self.config, dict) else {}

    def get_choices_list(self):
        config = self.get_config()

        if self.question_type == "mcq":
            try:
                return json.loads(self.choices) if self.choices else []
            except (TypeError, json.JSONDecodeError):
                return []

        if self.question_type == "order_events":
            return config.get("items", [])

        if self.question_type == "match_related":
            return [pair.get("left", "") for pair in config.get("pairs", []) if isinstance(pair, dict)]

        return []

    def get_correct_answer_payload(self):
        config = self.get_config()

        if self.question_type == "order_events":
            return config.get("items", [])

        if self.question_type == "match_related":
            pairs = config.get("pairs", [])
            return {
                str(pair.get("left", "")).strip(): str(pair.get("right", "")).strip()
                for pair in pairs
                if isinstance(pair, dict) and pair.get("left") is not None and pair.get("right") is not None
            }

        return self.correct_answer or ""

    @staticmethod
    def _normalize_order_events_answer(answer):
        if answer in (None, "", "-"):
            return []

        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except (TypeError, json.JSONDecodeError):
                return [segment.strip() for segment in answer.split("|") if segment.strip()]

        if isinstance(answer, dict):
            answer = answer.get("order") or answer.get("items") or answer.get("answer") or []

        if isinstance(answer, list):
            return [str(item).strip() for item in answer if str(item).strip()]

        return [str(answer).strip()]

    @staticmethod
    def _normalize_match_related_answer(answer):
        if answer in (None, "", "-"):
            return {}

        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except (TypeError, json.JSONDecodeError):
                return {}

        if isinstance(answer, dict):
            return {str(key).strip(): str(value).strip() for key, value in answer.items() if str(key).strip()}

        if isinstance(answer, list):
            normalized = {}
            for pair in answer:
                if isinstance(pair, dict) and pair.get("left") is not None and pair.get("right") is not None:
                    normalized[str(pair["left"]).strip()] = str(pair["right"]).strip()
                elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    normalized[str(pair[0]).strip()] = str(pair[1]).strip()
            return normalized

        return {}

    def get_submitted_answer_payload(self, submitted_answer):
        if self.question_type == "order_events":
            return self._normalize_order_events_answer(submitted_answer)

        if self.question_type == "match_related":
            return self._normalize_match_related_answer(submitted_answer)

        return submitted_answer

    def get_auto_grade(self, submitted_answer):
        if not self.auto_grade:
            return 0

        if self.question_type in {"mcq", "complete"}:
            return self.grade if self.is_answer_correct(submitted_answer) else 0

        if self.question_type == "order_events":
            correct_answer = self.get_correct_answer_payload()
            submitted_items = self._normalize_order_events_answer(submitted_answer)

            if not correct_answer:
                return 0

            matched_items = sum(
                1
                for index, expected_item in enumerate(correct_answer)
                if index < len(submitted_items) and submitted_items[index] == expected_item
            )
            return max(0, min(self.grade, int(((matched_items / len(correct_answer)) * self.grade) + 0.5)))

        if self.question_type == "match_related":
            correct_answer = self.get_correct_answer_payload()
            submitted_pairs = self._normalize_match_related_answer(submitted_answer)

            if not correct_answer:
                return 0

            matched_pairs = sum(
                1 for left_item, right_item in correct_answer.items()
                if submitted_pairs.get(left_item) == right_item
            )
            return max(0, min(self.grade, int(((matched_pairs / len(correct_answer)) * self.grade) + 0.5)))

        return self.grade if self.is_answer_correct(submitted_answer) else 0

    def is_answer_correct(self, submitted_answer):
        if not self.auto_grade:
            return False

        if self.question_type in {"mcq", "complete"}:
            return str(submitted_answer).strip() == str(self.correct_answer or "").strip()

        if self.question_type == "order_events":
            return self._normalize_order_events_answer(submitted_answer) == self.get_correct_answer_payload()

        if self.question_type == "match_related":
            return self._normalize_match_related_answer(submitted_answer) == self.get_correct_answer_payload()

        return False

    def serialize(self):
        config = self.get_config()
        answer_payload = self.get_correct_answer_payload()
        return {
            "id" : self.pk,
            "name" : self.title,
            "type" : self.question_type,
            "grade" : self.grade,
            "choices" : self.get_choices_list(),
            "config" : config,
            "config_json" : json.dumps(config, ensure_ascii=False),
            "answer_payload" : answer_payload,
            "answer_payload_json" : json.dumps(answer_payload, ensure_ascii=False) if isinstance(answer_payload, (dict, list)) else (answer_payload or ""),
            "auto_grade" : self.auto_grade,
            "correct_answer" : self.correct_answer,
        }

    def serialize_student(self):
        base = self.serialize()
        base.pop("correct_answer", None)
        base.pop("answer_payload", None)
        base.pop("answer_payload_json", None)
        base.pop("config_json", None)
        return base

    @staticmethod
    def get_types():
        return [str(_(question_type[1])) for question_type in Question.QUESTION_TYPES] # Translate display values

class Submission(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="grades")
    question = models.ForeignKey(Question, on_delete=models.SET_NULL, related_name="submitted_answers", null=True)
    submitted_answer = models.TextField()
    grade = models.PositiveSmallIntegerField(default=0)
    is_graded = models.BooleanField(default=True)

    
    def assign_grade(self, is_graded=False, grade=0):
        question_grade = self.question.grade

        if is_graded:
            self.is_graded = is_graded
            self.grade = question_grade if grade > question_grade else grade

        else:
            self.is_graded = self.question.auto_grade
            if self.is_graded:
                self.grade = self.question.get_auto_grade(self.submitted_answer)
        return

    def serialize(self):
        submitted_answer_payload = self.question.get_submitted_answer_payload(self.submitted_answer)
        return {
            "submitted_answer" : self.submitted_answer,
            "submitted_answer_payload" : submitted_answer_payload,
            "submitted_answer_payload_json" : json.dumps(submitted_answer_payload, ensure_ascii=False) if isinstance(submitted_answer_payload, (dict, list)) else (submitted_answer_payload or ""),
            "current_grade" : self.grade,
            "is_graded" : self.is_graded,
            **self.question.serialize()
        }


class Grade(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submitted_quizzes")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="grade")
    total_grade = models.PositiveSmallIntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)  # Timestamp

    class Meta:
        unique_together = ('user', 'quiz')  # Prevent duplicate grading per user per quiz

    def serialize_pagination(self):
        return {
            "rows" : [self.user.username, self.total_grade, self.submitted_at.strftime("%H:%M:%S, %d/%m/%Y")],
            "url" : reverse_lazy("submission-user", args=[self.quiz.pk, self.user.pk]),
            "submission_id" : self.pk  # Add submission_id for CSV export
        }

    @staticmethod
    def get_columns():
        return [_("Name"), _("Grades"), _("Submission Date")]
    
    @staticmethod
    def get_years(quiz_id):
        years_options = set()
        years = Grade.objects.filter(quiz__id=quiz_id).only("submitted_at").distinct()
        for year in years:
            years_options.add(year.submitted_at.year)
        return years_options
    
    def __str__(self):
        return f"{self.user.username} - {self.quiz.name} ({self.total_grade})"



