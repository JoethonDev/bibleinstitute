import json
import secrets

from django.db import migrations


def backfill_missing_lesson_part_ids(apps, schema_editor):
    Lesson = apps.get_model("management_system", "Lesson")
    database = schema_editor.connection.alias
    batch = []

    lessons = Lesson.objects.using(database).only("pk", "links").order_by("pk")
    for lesson in lessons.iterator(chunk_size=500):
        try:
            links = json.loads(lesson.links or "[]")
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Lesson {lesson.pk} has invalid media-link JSON; part IDs were not backfilled."
            ) from exc

        if not isinstance(links, list):
            raise RuntimeError(
                f"Lesson {lesson.pk} media links are not a list; part IDs were not backfilled."
            )

        changed = False
        for index, link in enumerate(links):
            if not isinstance(link, dict):
                raise RuntimeError(
                    f"Lesson {lesson.pk} media link {index} is not an object; "
                    "part IDs were not backfilled."
                )
            part_id = link.get("part_id")
            if not isinstance(part_id, str) or not part_id.strip():
                link["part_id"] = secrets.token_urlsafe(8)
                changed = True

        if changed:
            lesson.links = json.dumps(links, ensure_ascii=False)
            batch.append(lesson)

        if len(batch) == 500:
            Lesson.objects.using(database).bulk_update(batch, ["links"], batch_size=500)
            batch.clear()

    if batch:
        Lesson.objects.using(database).bulk_update(batch, ["links"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0087_lecture_watch_sessions"),
    ]

    operations = [
        migrations.RunPython(
            backfill_missing_lesson_part_ids,
            migrations.RunPython.noop,
        ),
    ]
