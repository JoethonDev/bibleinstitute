from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.db.models import Sum, Prefetch, Q, prefetch_related_objects
from django.utils.timezone import now
from datetime import date, datetime, timedelta
import json
from django.utils.translation import gettext_lazy as _ # Import gettext_lazy

# Constants
MANAGEMENT_ROLES = ["admin", "teacher"]

# Helper Function
def assign_academic_date():
    today = date.today()
    if today.month < 8 :
        today = today.replace(year=today.year-1)
    return today.replace(month=8)


def default_meeting_weekdays():
    return [6, 1]  # Sunday and Tuesday using datetime.date.weekday().


class PublicationStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    ARCHIVED = "archived", _("Archived")


# Create your models here.
class Role(models.Model):
    ROLES = [
        ("admin", _("Admin")),
        ("teacher", _("Teacher")),
        ("junior", _("First Year")),
        ("senior", _("Second Year"))
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
            if readable_value == str(_(role[1])): # Compare with translated value
                role_value = role[0]
                break
        return Role.objects.get(role=role_value)

    @staticmethod
    def get_readable_values():
        return [str(_(role.get_role_display())) for role in Role.objects.all()] # Translate display values

class User(AbstractUser):
    role = models.ForeignKey(Role, on_delete=models.DO_NOTHING, null=True, blank=True)
    joined_date = models.DateField(null=False, default=assign_academic_date)
    
    def save(self, *args, **kwargs):
        if not self.role_id:
            try:
                default_role = Role.objects.get(role='junior')
                self.role = default_role
            except Role.DoesNotExist:
                pass  # Role will be None, handle this in your application logic
        super().save(*args, **kwargs)
    
    def serialize_pagination(self):
        return {
            "rows" : [self.username, f"{self.first_name} {self.last_name}", str(_(self.role.get_role_display())), self.joined_date.strftime("%d/%m/%Y"), self.last_login],
            "url" : reverse_lazy("user-profile", args=[self.pk,])
        }

    @staticmethod
    def get_columns():
        return [_("Username"), _("Name"), _("Role"), _("Joined Date"), _("Last Login")]

class Course(models.Model):
    MAXIMUM_LEVEL = 2

    LEVELS = {
        "junior" : 1,
        "senior" : MAXIMUM_LEVEL,
        "management" : MAXIMUM_LEVEL,
        "admin" : MAXIMUM_LEVEL,
    }


    LEVELS_NAME = {
        1 : _("First Academic Year"),
        2 : _("Second Academic Year"),
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
            "rows" : [self.name, self.description, str(_(self.LEVELS_NAME[self.level])), self.instructor],
            "url" : reverse_lazy("course-view", args=[self.pk,])
        }
    
    @staticmethod
    def get_columns():
            return [_("Name"), _("Description"), _("Level"), _("Instructor")]

    def get_name_year(self):
        return f"{self.name} - {self.LEVELS_NAME[self.level]}"

    def can_access(self, role: str):
        return self.level <= self.LEVELS.get(role, 0)

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
                "level_name" : str(_(self.LEVELS_NAME[current_level])), # Translate level name here
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
        # Show ALL quizzes for this course regardless of open/closed state.
        # Access-level filtering is done in take_exam; quiz-mode logic
        # (exam / view / closed_unsolved) is determined per student in that view.
        return self.quizzes.all()


class AcademicYear(models.Model):
    name = models.CharField(max_length=20)
    level = models.PositiveIntegerField()
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current = models.BooleanField(default=False)
    meeting_weekdays = models.JSONField(default=default_meeting_weekdays)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_on", "level"]
        constraints = [
            models.UniqueConstraint(
                fields=["level", "name"],
                name="academic_year_unique_level_name",
            ),
            models.UniqueConstraint(
                fields=["level"],
                condition=models.Q(is_current=True),
                name="academic_year_one_current_per_level",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on__gt=models.F("starts_on")),
                name="academic_year_ends_after_starts",
            ),
        ]

    def __str__(self):
        return f"Level {self.level} - {self.name}"

    def clean(self):
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": _("End date must be after start date.")})

        if self.is_current and self.level:
            current_years = AcademicYear.objects.filter(level=self.level, is_current=True)
            if self.pk:
                current_years = current_years.exclude(pk=self.pk)
            if current_years.exists():
                raise ValidationError({"is_current": _("Only one academic year can be current per level.")})


class CourseOffering(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="course_offerings")
    instructor = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-academic_year__starts_on", "course__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "academic_year"],
                name="course_offering_unique_course_year",
            ),
        ]

    def __str__(self):
        return f"{self.course.name} @ {self.academic_year}"

    def clean(self):
        super().clean()
        if self.course_id and self.academic_year_id and self.course.level != self.academic_year.level:
            raise ValidationError(_("Course level must match the academic year level."))

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Enrollment(models.Model):
    class Type(models.TextChoices):
        NORMAL = "normal", _("Normal")
        REPEAT = "repeat", _("Repeat")
        REMEDIAL = "remedial", _("Remedial")
        MANUAL = "manual", _("Manual")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        INACTIVE = "inactive", _("Inactive")
        COMPLETED = "completed", _("Completed")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="enrollments")
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enrollments",
    )
    enrollment_type = models.CharField(max_length=20, choices=Type.choices, default=Type.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    enrolled_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_enrollments",
    )

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(enrollment_type="normal", course_offering__isnull=True)
                    | models.Q(
                        enrollment_type__in=["repeat", "remedial", "manual"],
                        course_offering__isnull=False,
                    )
                ),
                name="enrollment_target_matches_type",
            ),
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                condition=models.Q(course_offering__isnull=True),
                name="enrollment_unique_full_year",
            ),
            models.UniqueConstraint(
                fields=["student", "course_offering"],
                condition=models.Q(course_offering__isnull=False),
                name="enrollment_unique_course_offering",
            ),
        ]

    def __str__(self):
        target = self.course_offering or self.academic_year
        return f"{self.student.username} -> {target} ({self.enrollment_type})"

    def clean(self):
        super().clean()
        if self.course_offering_id:
            if self.course_offering.academic_year_id != self.academic_year_id:
                raise ValidationError({"course_offering": _("Course offering must belong to the selected academic year.")})
            if self.enrollment_type == self.Type.NORMAL:
                raise ValidationError({"enrollment_type": _("Normal enrollment grants the full academic year.")})
        elif self.enrollment_type != self.Type.NORMAL:
            raise ValidationError({"course_offering": _("This enrollment type requires a course offering.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MigrationReviewItem(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", _("Info")
        WARNING = "warning", _("Warning")
        ERROR = "error", _("Error")

    item_type = models.CharField(max_length=50)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    message = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.INFO)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.severity}] {self.item_type}"

class Lesson(models.Model):
    name = models.CharField(max_length=255, null=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    links = models.TextField() # Null must be false
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lessons",
    )
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    created_date = models.DateField(null=False, default=date.today)
    updated_date = models.DateField(null=False, auto_now=True)


    def __str__(self):
        return f"{self.name} for course : {self.course.name}"

    def clean(self):
        super().clean()
        if self.course_offering_id and self.course_offering.course_id != self.course_id:
            raise ValidationError({"course_offering": _("Course offering must belong to the lesson course.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def serialize_pagination(self):
        return {
            "rows" : [self.name, self.course.get_name_year(), _("Updated on %(date)s") % {'date': self.updated_date.strftime('%d/%m/%Y')}],
            "url" : reverse_lazy("lesson-view", args=[self.pk,])
        }
    
    @staticmethod
    def get_columns():
        return [_("Name"), _("Course"), _("Last Updated")]

    def can_access(self, user_join_date: date):
        try:
            end_range = user_join_date.replace(year=user_join_date.year + self.course.level)
        except ValueError:
            end_range = user_join_date.replace(year=user_join_date.year + self.course.level, day=28)
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
    course_offering = models.ForeignKey(
        CourseOffering,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quizzes",
    )
    status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    total_grade = models.PositiveSmallIntegerField(default=50)
    opening_date = models.DateTimeField(default=now)
    closing_date = models.DateTimeField(default=now)
    created_date = models.DateField(auto_now=True)

    def __str__(self):
        return f"Quiz {self.name}"

    def clean(self):
        super().clean()
        if self.course_offering_id and self.course_offering.course_id != self.course_id:
            raise ValidationError({"course_offering": _("Course offering must belong to the quiz course.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def serialize(self):
        return {
            "quiz_name" : self.name,
            "selected_course" : self.course.name,
            "opening_date" : self.opening_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "closing_date" : self.closing_date.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def serialize_pagination(self):
        if self.course:
            return {
                "rows" : [self.name, self.course.get_name_year(), self.total_grade, self.opening_date.strftime("%H:%M:%S, %d/%m/%Y"), self.closing_date.strftime("%H:%M:%S, %d/%m/%Y")],
                "url" : reverse_lazy("quiz-view", args=[self.pk,])
            }

    @staticmethod
    def get_columns():
        return [_("Name"), _("Course"), _("Grades"), _("Opening Date"), _("Closing Date"), _("Submissions")]

class Question(models.Model):
    QUESTION_TYPES = [
        ("mcq", _("Multiple Choice")),
        ("written", _("Written")),
        ("complete", _("Complete")),
        ("order_events", _("Order Events")),
        ("match_related", _("Match Related"))
    ]

    STRUCTURED_QUESTION_TYPES = {"order_events", "match_related"}

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    title = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=255, null=True)
    question_type = models.CharField(max_length=512, choices=QUESTION_TYPES)
    choices = models.TextField(null=True)
    config = models.JSONField(default=dict, blank=True)
    grade = models.PositiveSmallIntegerField(default=1)
    auto_grade = models.BooleanField(default=True)

    def get_config(self):
        return self.config if isinstance(self.config, dict) else {}

    def get_choices_list(self):
        config = self.get_config()

        if self.question_type == "mcq":
            try:
                return json.loads(self.choices) if self.choices else []
            except (TypeError, json.JSONDecodeError):
                return []

        if self.question_type == "order_events":
            return config.get("items", [])

        if self.question_type == "match_related":
            return [pair.get("left", "") for pair in config.get("pairs", []) if isinstance(pair, dict)]

        return []

    def get_correct_answer_payload(self):
        config = self.get_config()

        if self.question_type == "order_events":
            return config.get("items", [])

        if self.question_type == "match_related":
            pairs = config.get("pairs", [])
            return {
                str(pair.get("left", "")).strip(): str(pair.get("right", "")).strip()
                for pair in pairs
                if isinstance(pair, dict) and pair.get("left") is not None and pair.get("right") is not None
            }

        return self.correct_answer or ""

    @staticmethod
    def _normalize_order_events_answer(answer):
        if answer in (None, "", "-"):
            return []

        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except (TypeError, json.JSONDecodeError):
                return [segment.strip() for segment in answer.split("|") if segment.strip()]

        if isinstance(answer, dict):
            answer = answer.get("order") or answer.get("items") or answer.get("answer") or []

        if isinstance(answer, list):
            return [str(item).strip() for item in answer if str(item).strip()]

        return [str(answer).strip()]

    @staticmethod
    def _normalize_match_related_answer(answer):
        if answer in (None, "", "-"):
            return {}

        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except (TypeError, json.JSONDecodeError):
                return {}

        if isinstance(answer, dict):
            return {str(key).strip(): str(value).strip() for key, value in answer.items() if str(key).strip()}

        if isinstance(answer, list):
            normalized = {}
            for pair in answer:
                if isinstance(pair, dict) and pair.get("left") is not None and pair.get("right") is not None:
                    normalized[str(pair["left"]).strip()] = str(pair["right"]).strip()
                elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    normalized[str(pair[0]).strip()] = str(pair[1]).strip()
            return normalized

        return {}

    def get_submitted_answer_payload(self, submitted_answer):
        if self.question_type == "order_events":
            return self._normalize_order_events_answer(submitted_answer)

        if self.question_type == "match_related":
            return self._normalize_match_related_answer(submitted_answer)

        return submitted_answer

    def get_auto_grade(self, submitted_answer):
        if not self.auto_grade:
            return 0

        if self.question_type in {"mcq", "complete"}:
            return self.grade if self.is_answer_correct(submitted_answer) else 0

        if self.question_type == "order_events":
            correct_answer = self.get_correct_answer_payload()
            submitted_items = self._normalize_order_events_answer(submitted_answer)

            if not correct_answer:
                return 0

            matched_items = sum(
                1
                for index, expected_item in enumerate(correct_answer)
                if index < len(submitted_items) and submitted_items[index] == expected_item
            )
            return max(0, min(self.grade, int(((matched_items / len(correct_answer)) * self.grade) + 0.5)))

        if self.question_type == "match_related":
            correct_answer = self.get_correct_answer_payload()
            submitted_pairs = self._normalize_match_related_answer(submitted_answer)

            if not correct_answer:
                return 0

            matched_pairs = sum(
                1 for left_item, right_item in correct_answer.items()
                if submitted_pairs.get(left_item) == right_item
            )
            return max(0, min(self.grade, int(((matched_pairs / len(correct_answer)) * self.grade) + 0.5)))

        return self.grade if self.is_answer_correct(submitted_answer) else 0

    def is_answer_correct(self, submitted_answer):
        if not self.auto_grade:
            return False

        if self.question_type in {"mcq", "complete"}:
            return str(submitted_answer).strip() == str(self.correct_answer or "").strip()

        if self.question_type == "order_events":
            return self._normalize_order_events_answer(submitted_answer) == self.get_correct_answer_payload()

        if self.question_type == "match_related":
            return self._normalize_match_related_answer(submitted_answer) == self.get_correct_answer_payload()

        return False

    def serialize(self):
        config = self.get_config()
        answer_payload = self.get_correct_answer_payload()
        return {
            "id" : self.pk,
            "name" : self.title,
            "type" : self.question_type,
            "grade" : self.grade,
            "choices" : self.get_choices_list(),
            "config" : config,
            "config_json" : json.dumps(config, ensure_ascii=False),
            "answer_payload" : answer_payload,
            "answer_payload_json" : json.dumps(answer_payload, ensure_ascii=False) if isinstance(answer_payload, (dict, list)) else (answer_payload or ""),
            "auto_grade" : self.auto_grade,
            "correct_answer" : self.correct_answer,
        }

    @staticmethod
    def get_types():
        return [str(_(question_type[1])) for question_type in Question.QUESTION_TYPES] # Translate display values

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
            if self.is_graded:
                self.grade = self.question.get_auto_grade(self.submitted_answer)
        return

    def serialize(self):
        submitted_answer_payload = self.question.get_submitted_answer_payload(self.submitted_answer)
        return {
            "submitted_answer" : self.submitted_answer,
            "submitted_answer_payload" : submitted_answer_payload,
            "submitted_answer_payload_json" : json.dumps(submitted_answer_payload, ensure_ascii=False) if isinstance(submitted_answer_payload, (dict, list)) else (submitted_answer_payload or ""),
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
            "url" : reverse_lazy("submission-user", args=[self.quiz.pk, self.user.pk]),
            "submission_id" : self.pk  # Add submission_id for CSV export
        }

    @staticmethod
    def get_columns():
        return [_("Name"), _("Grades"), _("Submission Date")]
    
    @staticmethod
    def get_years(quiz_id):
        years_options = set()
        years = Grade.objects.filter(quiz__id=quiz_id).only("submitted_at").distinct()
        for year in years:
            years_options.add(year.submitted_at.year)
        return years_options
    
    def __str__(self):
        return f"{self.user.username} - {self.quiz.name} ({self.total_grade})"
        
