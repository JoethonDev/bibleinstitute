from django.db import migrations


def clear_quiz_time_limits(apps, schema_editor):
    """Give every existing quiz the maximum time.

    ``duration_minutes = 0`` means "no time limit" in the attempt window, the
    student API payload and every notification string, so a single bounded
    UPDATE removes the old per-quiz limits without touching answers, attempts or
    results.
    """
    Quiz = apps.get_model("management_system", "Quiz")
    Quiz.objects.exclude(duration_minutes=0).update(duration_minutes=0)


class Migration(migrations.Migration):
    """Data migration: existing quizzes become untimed (0 = no limit).

    Irreversible by nature - the previous per-quiz durations are not recorded
    anywhere - so the reverse is a no-op rather than restoring a guess.
    """

    dependencies = [
        ("management_system", "0110_alter_quiz_duration_minutes_default"),
    ]

    operations = [
        migrations.RunPython(clear_quiz_time_limits, migrations.RunPython.noop),
    ]
