from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def backfill_known_completion_events(apps, schema_editor):
    LectureProgress = apps.get_model("management_system", "LectureProgress")
    LectureProgressEvent = apps.get_model("management_system", "LectureProgressEvent")
    events = [
        LectureProgressEvent(
            student_id=progress.student_id,
            lesson_id=progress.lesson_id,
            part_id=progress.part_id,
            event_type="completed",
            occurred_at=progress.completed_at,
            percent=progress.percent,
            unique_seconds=progress.unique_seconds,
        )
        for progress in LectureProgress.objects.filter(completed_at__isnull=False).iterator()
    ]
    if events:
        LectureProgressEvent.objects.bulk_create(events, ignore_conflicts=True, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0085_private_application_image_previews"),
    ]

    operations = [
        migrations.AddField(
            model_name="viewingsession",
            name="ended_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="LectureProgressEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("part_id", models.CharField(max_length=100)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("started", "Started"),
                            ("activity", "Activity"),
                            ("completed", "80% completed"),
                            ("ended", "Ended"),
                        ],
                        max_length=16,
                    ),
                ),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("bucket_start", models.DateTimeField(blank=True, null=True)),
                ("percent", models.PositiveSmallIntegerField(default=0)),
                ("unique_seconds", models.PositiveIntegerField(default=0)),
                (
                    "lesson",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecture_progress_events",
                        to="management_system.lesson",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecture_progress_events",
                        to="management_system.user",
                    ),
                ),
                (
                    "viewing_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="progress_events",
                        to="management_system.viewingsession",
                    ),
                ),
            ],
            options={
                "ordering": ["occurred_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["student", "lesson", "part_id", "occurred_at"],
                        name="lecture_event_timeline_idx",
                    ),
                    models.Index(
                        fields=["event_type", "occurred_at"],
                        name="lecture_event_type_time_idx",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="lectureprogressevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(event_type__in=["started", "ended"]),
                fields=["viewing_session", "event_type"],
                name="lecture_event_session_boundary_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="lectureprogressevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(event_type="completed"),
                fields=["student", "lesson", "part_id", "event_type"],
                name="lecture_event_completion_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="lectureprogressevent",
            constraint=models.UniqueConstraint(
                condition=models.Q(event_type="activity"),
                fields=["viewing_session", "event_type", "bucket_start"],
                name="lecture_event_activity_bucket_uniq",
            ),
        ),
        migrations.RunPython(backfill_known_completion_events, migrations.RunPython.noop),
    ]
