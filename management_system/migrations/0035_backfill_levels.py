from django.db import migrations
from django.db.models import F, Max


def backfill_levels(apps, schema_editor):
    Level = apps.get_model("management_system", "Level")
    LevelName = apps.get_model("management_system", "LevelName")
    Course = apps.get_model("management_system", "Course")
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    Enrollment = apps.get_model("management_system", "Enrollment")

    course_levels = set(
        Course.objects.values_list("level", flat=True).distinct()
    )
    ay_levels = set(
        AcademicYear.objects.values_list("level", flat=True).distinct()
    )
    enroll_levels = set(
        v for v in Enrollment.objects.values_list("level", flat=True).distinct()
        if v is not None
    )
    all_levels = course_levels | ay_levels | enroll_levels

    if not all_levels:
        return

    non_positive = {v for v in all_levels if v < 1}
    if non_positive:
        raise ValueError(
            f"Cannot backfill: non-positive level values found: {sorted(non_positive)}"
        )

    existing = set(Level.objects.values_list("ordering", flat=True))
    level_names = {
        ln.level: (ln.name_en, ln.name_ar)
        for ln in LevelName.objects.all()
    }

    for lvl in sorted(all_levels):
        if lvl in existing:
            continue
        name_en, name_ar = level_names.get(lvl, ("", ""))
        Level.objects.create(
            ordering=lvl,
            name_en=name_en,
            name_ar=name_ar,
        )

    # Backfill Course.level_ref_id
    for course in Course.objects.filter(level_ref__isnull=True).select_related("level_ref"):
        level = Level.objects.filter(ordering=course.level).first()
        if level is None:
            raise ValueError(
                f"Course pk={course.pk} name={course.name!r} "
                f"has level={course.level} but no Level exists with that ordering."
            )
        Course.objects.filter(pk=course.pk).update(level_ref=level)

    # Backfill AcademicYear.level_ref_id
    for year in AcademicYear.objects.filter(level_ref__isnull=True).select_related("level_ref"):
        level = Level.objects.filter(ordering=year.level).first()
        if level is None:
            raise ValueError(
                f"AcademicYear pk={year.pk} name={year.name!r} "
                f"has level={year.level} but no Level exists with that ordering."
            )
        AcademicYear.objects.filter(pk=year.pk).update(level_ref=level)

    # Verify every enrollment's integer level matches its academic year's level
    mismatched = Enrollment.objects.select_related("academic_year").exclude(
        level=F("academic_year__level"),
    ).filter(level__isnull=False)
    if mismatched.exists():
        details = "; ".join(
            f"pk={e.pk} student={e.student.username} "
            f"enrollment.level={e.level} ay.level={e.academic_year.level}"
            for e in mismatched[:10]
        )
        raise ValueError(
            f"Found {mismatched.count()} enrollment(s) with level mismatch: {details}"
        )


def reverse_backfill(apps, schema_editor):
    Level = apps.get_model("management_system", "Level")
    Course = apps.get_model("management_system", "Course")
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    Course.objects.all().update(level_ref=None)
    AcademicYear.objects.all().update(level_ref=None)
    Level.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0034_add_level_foreign_keys"),
    ]

    operations = [
        migrations.RunPython(backfill_levels, reverse_backfill),
    ]
