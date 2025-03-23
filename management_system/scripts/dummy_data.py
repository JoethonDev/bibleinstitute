from management_system.models import *
from datetime import date

def run():
    # Creating Users
    junior = Role.objects.get(role="junior")
    senior = Role.objects.get(role="senior")
    teacher = Role.objects.get(role="teacher")
    user_junior = User.objects.create(username="junior_user", role=junior, joined_date=date.today().replace(year=2024, month=8))
    user_senior = User.objects.create(username="senior_user", role=senior, joined_date=date.today().replace(year=2023, month=8))
    user_teacher = User.objects.create(username="teacher_user", role=teacher)

    # Creating Courses
    course_1 = Course.objects.create(name="First Year Course", level=1)
    course_2 = Course.objects.create(name="Second Year Course", level=2)

    # Creating Lessons for Course 1
    for i in range(1, 6):
        Lesson.objects.create(name=f"Lesson {i} for Course 1", course=course_1)

    # Creating Lessons for Course 2
    for i in range(1, 6):
        Lesson.objects.create(name=f"Lesson {i} for Course 2", course=course_2)