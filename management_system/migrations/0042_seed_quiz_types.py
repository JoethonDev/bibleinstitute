from django.db import migrations


QUIZ_TYPES = (
    ("weekly", "Weekly Quiz", "اختبار أسبوعي"),
    ("final", "Final Quiz", "الاختبار النهائي"),
)


def seed_quiz_types(apps, schema_editor):
    QuizType = apps.get_model("management_system", "QuizType")
    invalid_codes = set(
        QuizType.objects.exclude(code__in=[code for code, _name_en, _name_ar in QUIZ_TYPES])
        .values_list("code", flat=True)
    )
    if invalid_codes:
        raise RuntimeError(
            "Unsupported quiz types already exist; resolve them before applying 0042: "
            + ", ".join(sorted(invalid_codes))
        )
    for code, name_en, name_ar in QUIZ_TYPES:
        QuizType.objects.update_or_create(
            code=code,
            defaults={"name_en": name_en, "name_ar": name_ar},
        )


def unseed_quiz_types(apps, schema_editor):
    QuizType = apps.get_model("management_system", "QuizType")
    QuizType.objects.filter(code__in=[code for code, _name_en, _name_ar in QUIZ_TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0041_course_name_per_level"),
    ]

    operations = [
        migrations.RunPython(seed_quiz_types, unseed_quiz_types),
    ]
