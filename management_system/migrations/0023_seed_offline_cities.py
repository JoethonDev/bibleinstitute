from django.db import migrations


def seed_cities(apps, schema_editor):
    OfflineCity = apps.get_model("management_system", "OfflineCity")
    OfflineCity.objects.get_or_create(name="Cairo")
    OfflineCity.objects.get_or_create(name="Giza")



class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0022_add_application_fields"),
    ]

    operations = [
        migrations.RunPython(seed_cities, migrations.RunPython.noop),
    ]
