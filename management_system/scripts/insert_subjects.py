from management_system.models import Course, AcademicYear

def run():
    courses = []
    Course.objects.all().delete()
    # Create AcademicYears for levels that don't exist yet
    from datetime import date
    for lvl in range(1, 4):
        if not AcademicYear.objects.filter(level=lvl, is_current=True).exists():
            AcademicYear.objects.create(
                name=f"Year {lvl}",
                level=lvl,
                starts_on=date(date.today().year if date.today().month >= 8 else date.today().year - 1, 8, 1),
                ends_on=date((date.today().year if date.today().month >= 8 else date.today().year - 1) + 1, 7, 31),
                is_current=not AcademicYear.objects.filter(is_current=True).exists(),
            )
    text = """
3 مادة عهد قديم
العهد الجديد ترم اول – 1
العهد الجديد ترم اول – 2
العهد القديم ترم الاول – 1
مادة الابائيات
مادة الابائيات 2
مادة الاتصال
مادة الاسرار
مادة الامتحان
مادة العهد الجديد 2
مادة العهد الجديد 4
مادة العهد القديم 2
مادة العهد القديم 4
مادة العهد القديم الترم الاول – 2
مادة الفنون الكتابية 1
مادة الفنون الكتابية 2
مادة القراءات الكنسية – القطمارس
مادة تاريخ الكتاب المقدس
"""
    levels = sorted(set(
        list(AcademicYear.objects.values_list("level", flat=True).distinct())
        + list(Course.objects.values_list("level", flat=True).distinct())
    )) or [1, 2, 3]
    level_idx = 0
    for course in text.split("\n"):
        if course:
            course = course.strip()
            courses.append(
                Course(name=course, level=levels[level_idx % len(levels)])
            )
            level_idx += 1
    
    Course.objects.bulk_create(courses)
