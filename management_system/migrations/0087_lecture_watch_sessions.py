from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0086_lecture_progress_timeline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lectureprogressevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("started", "Started"),
                    ("activity", "Activity"),
                    ("progress", "Progress changed"),
                    ("completed", "80% completed"),
                    ("ended", "Ended"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="lectureprogress",
            name="activity_tracking_started_at",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.AddField(
            model_name="lectureprogress",
            name="activity_history_incomplete",
            field=models.BooleanField(default=True, editable=False),
        ),
        migrations.AlterField(
            model_name="lectureprogress",
            name="activity_history_incomplete",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="heartbeat_sequence",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="LectureWatchSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("part_id", models.CharField(max_length=100)),
                ("started_at", models.DateTimeField()),
                ("last_active_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("active_watch_seconds", models.PositiveIntegerField(default=0)),
                (
                    "lesson",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecture_watch_sessions",
                        to="management_system.lesson",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecture_watch_sessions",
                        to="management_system.user",
                    ),
                ),
            ],
            options={
                "ordering": ["started_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["student", "lesson", "part_id", "started_at"],
                        name="lecture_watch_timeline_idx",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="lecturewatchsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(ended_at__isnull=True),
                fields=("student", "lesson", "part_id"),
                name="lecture_watch_one_open_uniq",
            ),
        ),
        migrations.AddField(
            model_name="viewingsession",
            name="watch_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="viewing_sessions",
                to="management_system.lecturewatchsession",
            ),
        ),
        migrations.CreateModel(
            name="LectureWatchDay",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("study_date", models.DateField()),
                ("active_watch_seconds", models.PositiveIntegerField(default=0)),
                ("progress_start_percent", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("progress_end_percent", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "watch_session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="days",
                        to="management_system.lecturewatchsession",
                    ),
                ),
            ],
            options={"ordering": ["study_date", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="lecturewatchday",
            constraint=models.UniqueConstraint(
                fields=("watch_session", "study_date"),
                name="lecture_watch_day_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="lecturewatchday",
            index=models.Index(fields=["study_date", "watch_session"], name="lecture_watch_day_date_idx"),
        ),
    ]
