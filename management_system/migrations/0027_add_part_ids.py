import secrets
import json

from django.db import migrations


def add_part_ids(apps, schema_editor):
    Lesson = apps.get_model("management_system", "Lesson")
    for lesson in Lesson.objects.iterator():
        links = json.loads(lesson.links)
        changed = False
        for item in links:
            if "part_id" not in item:
                item["part_id"] = secrets.token_urlsafe(8)
                changed = True
        if changed:
            lesson.links = json.dumps(links)
            lesson.save(update_fields=["links"])


class Migration(migrations.Migration):
    dependencies = [("management_system", "0026_tracking_models")]
    operations = [migrations.RunPython(add_part_ids, migrations.RunPython.noop)]
