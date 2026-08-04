from django.db import migrations
from datetime import date


def backfill_academic_data(apps, schema_editor):
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    CourseOffering = apps.get_model("management_system", "CourseOffering")
    Enrollment = apps.get_model("management_system", "Enrollment")
    MigrationReviewItem = apps.get_model("management_system", "MigrationReviewItem")
    Course = apps.get_model("management_system", "Course")
    User = apps.get_model("management_system", "User")
    Role = apps.get_model("management_system", "Role")
    Lesson = apps.get_model("management_system", "Lesson")
    Quiz = apps.get_model("management_system", "Quiz")

    today = date.today()
    year = today.year if today.month >= 8 else today.year - 1
    year_name = f"{year}/{year + 1}"
    starts_on = date(year, 8, 1)
    ends_on = date(year + 1, 5, 31)

    levels = set(Course.objects.values_list("level", flat=True).distinct())
    year_by_level = {}
    for level in sorted(levels):
        ay, _ = AcademicYear.objects.get_or_create(
            level=level,
            name=year_name,
            defaults={
                "starts_on": starts_on,
                "ends_on": ends_on,
                "is_current": True,
            },
        )
        year_by_level[level] = ay

    for course in Course.objects.all():
        ay = year_by_level.get(course.level)
        if not ay:
            MigrationReviewItem.objects.create(
                item_type="course",
                object_id=course.pk,
                message=f'Course "{course.name}" has level {course.level} with no AcademicYear.',
                severity="warning",
            )
            continue
        CourseOffering.objects.get_or_create(
            course=course,
            academic_year=ay,
            defaults={
                "instructor": course.instructor or "",
                "status": "published",
            },
        )

    role_level = {}
    for role_name, level in [("junior", 1), ("senior", 2)]:
        try:
            role = Role.objects.get(role=role_name)
            role_level[role.pk] = level
        except Role.DoesNotExist:
            pass

    for user in User.objects.filter(role_id__in=list(role_level.keys())):
        level = role_level[user.role_id]
        ay = year_by_level.get(level)
        if not ay:
            MigrationReviewItem.objects.create(
                item_type="user",
                object_id=user.pk,
                message=f'User "{user.username}" has no AcademicYear for level {level}.',
                severity="warning",
            )
            continue
        Enrollment.objects.get_or_create(
            student=user,
            academic_year=ay,
            course_offering=None,
            defaults={
                "enrollment_type": "normal",
                "status": "active",
            },
        )

    offerings_by_course = {
        co.course_id: co
        for co in CourseOffering.objects.select_related("course").all()
    }

    for lesson in Lesson.objects.filter(course_offering__isnull=True).iterator():
        offering = offerings_by_course.get(lesson.course_id)
        if offering:
            Lesson.objects.filter(pk=lesson.pk).update(course_offering=offering)
        else:
            MigrationReviewItem.objects.create(
                item_type="lesson",
                object_id=lesson.pk,
                message=f'Lesson "{lesson.name}" has no CourseOffering.',
                severity="warning",
            )

    for quiz in Quiz.objects.filter(course_offering__isnull=True).iterator():
        offering = offerings_by_course.get(quiz.course_id)
        if offering:
            Quiz.objects.filter(pk=quiz.pk).update(course_offering=offering)
        else:
            MigrationReviewItem.objects.create(
                item_type="quiz",
                object_id=quiz.pk,
                message=f'Quiz "{quiz.name}" has no CourseOffering.',
                severity="warning",
            )


def reverse_func(apps, schema_editor):
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    CourseOffering = apps.get_model("management_system", "CourseOffering")
    Enrollment = apps.get_model("management_system", "Enrollment")
    MigrationReviewItem = apps.get_model("management_system", "MigrationReviewItem")
    Lesson = apps.get_model("management_system", "Lesson")
    Quiz = apps.get_model("management_system", "Quiz")

    Lesson.objects.update(course_offering=None)
    Quiz.objects.update(course_offering=None)
    Enrollment.objects.all().delete()
    CourseOffering.objects.all().delete()
    AcademicYear.objects.all().delete()
    MigrationReviewItem.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("management_system", "0019_academic_year_schema"),
    ]

    operations = [
        migrations.RunPython(backfill_academic_data, reverse_code=reverse_func),
    ]
