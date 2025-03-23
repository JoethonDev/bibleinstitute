from django.db import models
from django.contrib.auth.models import AbstractUser
from django.urls import reverse_lazy
from django.db.models import Sum, Prefetch, Q, prefetch_related_objects
from django.utils.timezone import now
from datetime import date, datetime, timedelta
import json

# Constants
MANAGEMENT_ROLES = ["admin", "teacher"]

# Helper Function
def assign_academic_date():
    today = date.today()
    if today.month < 8 :
        today = today.replace(year=today.year-1)
    return today.replace(month=8)


# Create your models here.
class Role(models.Model):
    ROLES = [
        ("admin", "مدير"),
        ("teacher", "مصحح"),
        ("junior", "سنه اولي"),
        ("senior", "سنه تانيه")
    ]
    # Fields
    role = models.CharField(
        max_length=10,
        choices=ROLES, 
        unique=True
    )

    def __str__(self):
        return self.get_role_display()
    
    @staticmethod
    def get_default():
        return Role.objects.get_or_create(role='junior')[0]
    
    @staticmethod
    def get_by_readable_value(readable_value):
        role_value = None
        for role in Role.ROLES:
            if readable_value == role[1]:
                role_value = role[0]
                break
        return Role.objects.get(role=role_value)

    @staticmethod
    def get_readable_values():
        return [role.get_role_display() for role in Role.objects.all()]

class User(AbstractUser):
    role = models.ForeignKey(Role, on_delete=models.DO_NOTHING, default=Role.get_default().id)
    joined_date = models.DateField(null=False, default=assign_academic_date())
    
    def serialize_pagination(self):
        return {
            "rows" : [self.username, f"{self.first_name} {self.last_name}", self.role.get_role_display(), self.joined_date.strftime("%d/%m/%Y"), self.last_login],
            "url" : reverse_lazy("user-profile", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
        return ["Username", "Name", "Role", "Joined Date", "Last Login"]

class Course(models.Model):
    MAXIMUM_LEVEL = 2

    LEVELS = {
        "junior" : 1,
        "senior" : MAXIMUM_LEVEL,
        "management" : MAXIMUM_LEVEL
    }


    LEVELS_NAME = {
        1 : "السنه الدراسيه الاولي",
        2 : "السنه الدراسيه الثانيه",
    }

    name = models.CharField(max_length=255, null=False, unique=True)
    description = models.TextField(null=True)
    instructor = models.CharField(max_length=255, null=True)
    level = models.PositiveIntegerField(null=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Course : {self.name} in level : {self.level}"
    
    def serialize_pagination(self):
        return {
            "rows" : [self.name, self.description, self.LEVELS_NAME[self.level], self.instructor],
            "url" : reverse_lazy("course-view", args=[self.pk,])
        }
    
    @staticmethod
    def get_columns():
            return ["Name", "Description", "Level", "Instructor"]

    def get_name_year(self):
        return f"{self.name} - {self.LEVELS_NAME[self.level]}"

    def can_access(self, role: str):
        print(self.level)
        return self.level <= self.LEVELS[role] 

    def retrieve_courses_for_level(self, level: int):
        packed_levels = []
        # Get All levels
        levels = [number for number in range(1, level+1)]
        # Query Optimization for making a single hit to database
        courses = self.objects.filter(level__in=levels)
        # Collect and Sort Course based on level
        packed_courses = {}
        for course in courses:
            current_level = course.level
            if current_level in packed_courses:
                packed_courses[current_level].append(course)
            else:
                packed_courses[current_level] = [course]

        # Format Course to return
        for current_level, courses_list in packed_courses.items():
            packed_levels.insert(0, {
                "level_name" : self.LEVELS_NAME[current_level],
                "courses" : courses_list,
            })
            
        return packed_levels

    @staticmethod
    def fetch_courses_by_role(role: str):
        if role in MANAGEMENT_ROLES:
            role = "management"
               
        current_level = Course.LEVELS.get(role, 0)
        return Course.retrieve_courses_for_level(Course, current_level)
    
    def fetch_quizzes(self, user):
        # Select all quizzes where user took quiz (id) or opening_date < now()

        # Get all quizzes
        return self.quizzes.filter(Q(opening_date__lte=now()) & Q(grade__user=user))


class Lesson(models.Model):
    name = models.CharField(max_length=255, null=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    links = models.TextField() # Null must be false
    created_date = models.DateField(null=False, default=date.today())
    updated_date = models.DateField(null=False, auto_now=True)


    def __str__(self):
        return f"{self.name} for course : {self.course.name}"

    def serialize_pagination(self):
        return {
            "rows" : [self.name, self.course.get_name_year(), f"Update on {self.updated_date.strftime('%d/%m/%Y')}"],
            "url" : reverse_lazy("lesson-view", args=[self.pk,])
        }
    
    @staticmethod
    def get_columns():
        return ["Name", "Course", "Last Updated"]

    def can_access(self, user_join_date: date):
        end_range = user_join_date.replace(year=user_join_date.year + self.course.level)
        return user_join_date <= self.created_date <= end_range

    def has_segment(self, segment: str):
        links = json.loads(self.links)
        segments_collection = [value['segments'] for value in links]
        for collection in segments_collection:
            if segment in collection:
                return True
        return False

    def serialize(self):
        links = json.loads(self.links)
        separated_parts = dict()
        # Type - File_id - Index
        for file in links:
            index = file['index']
            file_data = {
                "type" : file['type'],
                "file_id": file['file_id']
            }
            if index in separated_parts:
                separated_parts[index].append(file_data)
            else:
                separated_parts[index] = [file_data]
        
        return [value for value in separated_parts.values()]
    
class Quiz(models.Model):
    name = models.CharField(max_length=64)
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, related_name="quizzes", null=True)
    total_grade = models.PositiveSmallIntegerField(default=50)
    opening_date = models.DateTimeField(default=now())
    closing_date = models.DateTimeField(default=now())
    created_date = models.DateField(auto_now=True)

    def __str__(self):
        return f"Quiz {self.name}"

    def serialize(self):
        return {
            "quiz_name" : self.name,
            "selected_course" : self.course.name,
            "opening_date" : self.opening_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "closing_date" : self.closing_date.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def serialize_pagination(self):
            return {
            "rows" : [self.name, self.course.get_name_year(), self.total_grade, self.opening_date.strftime("%H:%M:%S, %d/%m/%Y"), self.closing_date.strftime("%H:%M:%S, %d/%m/%Y")],
            "url" : reverse_lazy("quiz-view", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
        return ["Name", "Course", "Grades", "Opening Date", "Closing Date", "Submissions"]

class Question(models.Model):
    QUESTION_TYPES = [
        ("mcq", "اختيار من متعدد"),
        ("written", "مقالي"),
        ("complete", "اكمل")
    ]

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    title = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=255, null=True)
    question_type = models.CharField(max_length=512, choices=QUESTION_TYPES)
    choices = models.TextField(null=True)
    grade = models.PositiveSmallIntegerField(default=1)
    auto_grade = models.BooleanField(default=True)

    def serialize(self):
        return {
            "id" : self.pk,
            "name" : self.title,
            "type" : self.question_type,
            "grade" : self.grade,
            "choices" : json.loads(self.choices) if self.choices else [],
            "auto_grade" : self.auto_grade,
            "correct_answer" : self.correct_answer,
        }

    @staticmethod
    def get_types():
        return [question_type[1] for question_type in Question.QUESTION_TYPES]

class Submission(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="grades")
    question = models.ForeignKey(Question, on_delete=models.SET_NULL, related_name="submitted_answers", null=True)
    submitted_answer = models.TextField()
    grade = models.PositiveSmallIntegerField(default=0)
    is_graded = models.BooleanField(default=True)

    
    def assign_grade(self, is_graded=False, grade=0):
        question_grade = self.question.grade

        if is_graded:
            self.is_graded = is_graded
            self.grade = question_grade if grade > question_grade else grade

        else:
            self.is_graded = self.question.auto_grade
            if self.is_graded and self.submitted_answer == self.question.correct_answer:
                self.grade = question_grade           
        return

    def serialize(self):
        return {
            "submitted_answer" : self.submitted_answer,
            "current_grade" : self.grade,
            "is_graded" : self.is_graded,
            **self.question.serialize()
        }


class Grade(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submitted_quizzes")
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="grade")
    total_grade = models.PositiveSmallIntegerField()
    submitted_at = models.DateTimeField(auto_now_add=True)  # Timestamp

    class Meta:
        unique_together = ('user', 'quiz')  # Prevent duplicate grading per user per quiz

    def serialize_pagination(self):
        return {
            "rows" : [self.user.username, self.total_grade, self.submitted_at.strftime("%H:%M:%S, %d/%m/%Y")],
            "url" : reverse_lazy("submission-user", args=[self.quiz.pk, self.user.pk])
        }

    @staticmethod
    def get_columns():
        return ["Name", "Grades", "Submission Date"]
    
    @staticmethod
    def get_years(quiz_id):
        years_options = set()
        years = Grade.objects.filter(quiz__id=quiz_id).only("submitted_at").distinct()
        for year in years:
            years_options.add(year.submitted_at.year)
        return years_options
    
    def __str__(self):
        return f"{self.user.username} - {self.quiz.name} ({self.total_grade})"
        