from management_system.models import Course

def run():
    courses = []
    Course.objects.all().delete()
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
    level = 1
    for course in text.split("\n"):
        if course:
            course = course.strip()
            courses.append(
                Course(name=course, level=level)
            )

        level = 1 if level == 2 else 2
    
    Course.objects.bulk_create(courses)
