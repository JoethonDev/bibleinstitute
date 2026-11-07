"""Seed the starter subject catalog idempotently; never deletes existing rows."""
from datetime import date

from management_system.models import Course, Level, AcademicYear, AcademicYearLevel


def run():
    # Ensure levels 1..3 exist
    level_objs = {}
    for ordering in range(1, 4):
        level, _ = Level.objects.get_or_create(
            ordering=ordering,
            defaults={"name_en": f"Level {ordering}", "name_ar": f"المستوى {ordering}"},
        )
        level_objs[ordering] = level

    # Create or reuse one active AcademicYear
    year_start = date(date.today().year if date.today().month >= 8 else date.today().year - 1, 8, 1)
    year_end = date(year_start.year + 1, 7, 31)
    year, _ = AcademicYear.objects.get_or_create(
        ordering=1,
        defaults={
            "name": f"{year_start.year}/{year_end.year}",
            "starts_on": year_start,
            "ends_on": year_end,
            "is_active": not AcademicYear.objects.filter(is_active=True).exists(),
        },
    )

    # Create AcademicYearLevel links for each level
    for ordering in range(1, 4):
        AcademicYearLevel.objects.get_or_create(
            academic_year=year,
            level=level_objs[ordering],
        )

    text = """
3 مادة عهد قديم
العهد الجديد ترم اول - 1
العهد الجديد ترم اول - 2
العهد القديم ترم الاول - 1
مادة الابائيات
مادة الابائيات 2
مادة الاتصال
مادة الاسرار
مادة الامتحان
مادة العهد الجديد 2
مادة العهد الجديد 4
مادة العهد القديم 2
مادة العهد القديم 4
مادة العهد القديم الترم الاول - 2
مادة الفنون الكتابية 1
مادة الفنون الكتابية 2
مادة القراءات الكنسية - القطمارس
مادة تاريخ الكتاب المقدس
"""
    level_idx = 0
    level_list = [level_objs[1], level_objs[2], level_objs[3]]
    created = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        name = line.strip()
        level = level_list[level_idx % len(level_list)]
        _course, was_created = Course.objects.get_or_create(name=name, level=level)
        created += 1 if was_created else 0
        level_idx += 1
    return {"catalog_entries": level_idx, "created": created}
