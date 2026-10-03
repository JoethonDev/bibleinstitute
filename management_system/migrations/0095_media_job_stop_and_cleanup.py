# Generated for cooperative media job cancellation and explicit cleanup.

import django.db.models.indexes
from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0094_mobile_download_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="stop_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cancel_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="source_upload_url_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_status",
            field=models.CharField(
                choices=[
                    ("not_requested", _("Not requested")),
                    ("queued", _("Cleanup queued")),
                    ("running", _("Cleaning up")),
                    ("failed", _("Cleanup failed")),
                    ("complete", _("Cleanup complete")),
                ],
                default="not_requested",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_not_before",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_last_dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_preserved_keys",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_result",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="mediaprocessingjob",
            name="cleanup_group_ids",
            field=models.JSONField(default=list),
        ),
        migrations.AddIndex(
            model_name="mediaprocessingjob",
            index=models.Index(
                fields=["cleanup_status", "cleanup_last_dispatched_at"],
                name="media_cleanup_dispatch_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mediaprocessingjob",
            index=models.Index(
                fields=["cleanup_status", "cleanup_started_at"],
                name="media_cleanup_state_start_idx",
            ),
        ),
        migrations.AlterField(
            model_name="mediaprocessingjob",
            name="phase",
            field=models.CharField(
                choices=[
                    ("upload", _("Upload")),
                    ("queued", _("Queued")),
                    ("download", _("Downloading source")),
                    ("probe", _("Inspecting source")),
                    ("encode", _("Processing media")),
                    ("upload_output", _("Uploading output")),
                    ("verify", _("Verifying output")),
                    ("attach", _("Attaching to lesson")),
                    ("complete", _("Complete")),
                    ("failed", _("Failed")),
                    ("cancelled", _("Cancelled")),
                ],
                default="upload",
                max_length=24,
            ),
        ),
    ]
