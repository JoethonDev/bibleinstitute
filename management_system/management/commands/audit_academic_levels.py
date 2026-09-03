from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.models import Count, Q, Sum

from management_system.models import (
    AcademicHoliday,
    AcademicYear,
    AcademicYearLevel,
    AttendanceRecord,
    Course,
    CourseOffering,
    Enrollment,
    Grade,
    LectureProgress,
    Lesson,
    Level,
    Question,
    Quiz,
    Submission,
    User,
    VerifiedSegmentRequest,
    ViewingSession,
)


class Command(BaseCommand):
    help = "Run a read-only academic migration audit; exit non-zero on errors."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        errors = []
        warnings = []
        db_alias = options["database"]
        if db_alias not in connections.databases:
            raise CommandError(f"Database alias {db_alias!r} is not configured.")

        for model in apps.get_app_config("management_system").get_models():
            model.objects._db = db_alias

        connection = connections[db_alias]
        with connection.cursor() as cursor:
            required_columns = {
                AcademicYearLevel._meta.db_table: {"academic_year_id", "level_id", "meeting_weekdays"},
                CourseOffering._meta.db_table: {"academic_year_level_id"},
                Enrollment._meta.db_table: {"academic_year_level_id"},
                AttendanceRecord._meta.db_table: {"course_offering_id"},
            }
            for table_name, expected_columns in required_columns.items():
                actual_columns = {
                    column.name
                    for column in connection.introspection.get_table_description(cursor, table_name)
                }
                missing_columns = expected_columns - actual_columns
                if missing_columns:
                    errors.append(
                        f"table {table_name} is missing canonical columns: {sorted(missing_columns)}"
                    )

        def error(message):
            errors.append(message)
            self.stderr.write(self.style.ERROR(f"ERROR: {message}"))

        def warning(message):
            warnings.append(message)
            self.stdout.write(self.style.WARNING(f"WARNING: {message}"))

        counts = {
            "users": User.objects.count(),
            "levels": Level.objects.count(),
            "academic_years": AcademicYear.objects.count(),
            "year_level_links": AcademicYearLevel.objects.count(),
            "courses": Course.objects.count(),
            "offerings": CourseOffering.objects.count(),
            "enrollments": Enrollment.objects.count(),
            "holidays": AcademicHoliday.objects.count(),
            "lessons": Lesson.objects.count(),
            "quizzes": Quiz.objects.count(),
            "questions": Question.objects.count(),
            "submissions": Submission.objects.count(),
            "grades": Grade.objects.count(),
            "attendance": AttendanceRecord.objects.count(),
            "viewing_sessions": ViewingSession.objects.count(),
            "verified_segments": VerifiedSegmentRequest.objects.count(),
            "lecture_progress": LectureProgress.objects.count(),
        }
        self.stdout.write("Row counts:")
        for name, count in counts.items():
            self.stdout.write(f"  {name}={count}")

        self.stdout.write("Academic-year/level links:")
        for link in AcademicYearLevel.objects.values(
            "id", "academic_year_id", "academic_year__name", "level_id", "level__ordering"
        ):
            self.stdout.write(
                f"  pk={link['id']} year={link['academic_year_id']} ({link['academic_year__name']!r}) "
                f"level={link['level_id']} (ordering={link['level__ordering']})"
            )

        for year in AcademicYear.objects.values("id", "name").annotate(level_count=Count("levels")):
            if year["level_count"] == 0:
                error(f"academic year pk={year['id']} name={year['name']!r} has no levels")

        duplicate_groups = (
            AcademicYear.objects.values("name", "starts_on", "ends_on")
            .annotate(row_count=Count("id"))
            .filter(row_count__gt=1)
        )
        for group in duplicate_groups:
            warning(
                "duplicate candidate year group: "
                f"name={group['name']!r}, starts_on={group['starts_on']}, "
                f"ends_on={group['ends_on']}, rows={group['row_count']}"
            )

        for name in AcademicYear.objects.values("name").annotate(row_count=Count("id")).filter(row_count__gt=1):
            dates = set(
                AcademicYear.objects.filter(name=name["name"]).values_list("starts_on", "ends_on")
            )
            if len(dates) > 1:
                error(f"academic year name {name['name']!r} has conflicting date ranges: {sorted(dates)}")

        current_years = AcademicYear.objects.filter(is_active=True)
        if current_years.count() > 1:
            error(f"multiple active academic years: {list(current_years.values_list('id', flat=True))}")

        invalid_levels = Level.objects.filter(Q(ordering__isnull=True) | Q(ordering__lt=1))
        for level in invalid_levels:
            error(f"level pk={level.pk} has invalid ordering={level.ordering!r}")
        duplicate_orderings = Level.objects.values("ordering").annotate(n=Count("id")).filter(n__gt=1)
        for row in duplicate_orderings:
            error(f"duplicate level ordering={row['ordering']}")

        links = set(AcademicYearLevel.objects.values_list("academic_year_id", "level_id"))
        for offering in CourseOffering.objects.select_related("course", "academic_year_level"):
            scope = offering.academic_year_level
            if (scope.academic_year_id, scope.level_id) not in links:
                error(f"offering pk={offering.pk} has no matching year-level scope")
            if scope.level_id != offering.course.level_id:
                error(
                    f"offering pk={offering.pk} course.level_id={offering.course.level_id} "
                    f"does not match scope level_id={scope.level_id}"
                )

        for enrollment in Enrollment.objects.select_related(
            "course_offering__academic_year_level", "academic_year_level"
        ):
            if enrollment.course_offering_id and (
                enrollment.course_offering.academic_year_level_id != enrollment.academic_year_level_id
            ):
                error(f"enrollment pk={enrollment.pk} has a mismatched offering scope")

        for attendance in AttendanceRecord.objects.select_related("course_offering__academic_year_level"):
            if attendance.course_offering_id is None:
                candidates = Enrollment.objects.filter(
                    student_id=attendance.student_id,
                    status=Enrollment.Status.ACTIVE,
                    enrollment_type=Enrollment.Type.NORMAL,
                ).count()
                if candidates != 1:
                    error(
                        f"attendance pk={attendance.pk} student={attendance.student_id} "
                        f"has ambiguous normal enrollment mapping ({candidates} candidates)"
                    )

        for lesson in Lesson.objects.select_related("course_offering"):
            if lesson.course_offering_id is None:
                error(f"lesson pk={lesson.pk} has no course offering")
            elif lesson.course_offering.course_id is None:
                error(f"lesson pk={lesson.pk} has an offering without a course")

        for quiz in Quiz.objects.select_related("course_offering"):
            if quiz.course_offering_id is None:
                error(f"quiz pk={quiz.pk} has no course offering")
            elif quiz.course_offering.course_id is None:
                error(f"quiz pk={quiz.pk} has an offering without a course")

        for submission in Submission.objects.filter(is_graded=False).select_related("user", "question"):
            warning(
                f"unresolved manual grading: submission pk={submission.pk}, "
                f"student={submission.user_id}, question={submission.question_id}"
            )

        question_totals = Question.objects.values("quiz_id").annotate(question_total=Sum("grade"))
        totals = dict(Quiz.objects.values_list("id", "total_grade"))
        for row in question_totals:
            if totals.get(row["quiz_id"]) != row["question_total"]:
                error(
                    f"quiz pk={row['quiz_id']} total_grade={totals.get(row['quiz_id'])} "
                    f"does not equal question total={row['question_total']}"
                )

        duplicate_active_normal = (
            Enrollment.objects.filter(
                status=Enrollment.Status.ACTIVE,
                enrollment_type=Enrollment.Type.NORMAL,
            )
            .values("student_id", "academic_year_level_id")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )
        for row in duplicate_active_normal:
            error(
                f"multiple active normal enrollments for student={row['student_id']} "
                f"scope={row['academic_year_level_id']}: {row['n']}"
            )

        for attendance in AttendanceRecord.objects.filter(course_offering__isnull=True):
            if not Enrollment.objects.filter(
                student_id=attendance.student_id,
                status=Enrollment.Status.ACTIVE,
                enrollment_type=Enrollment.Type.NORMAL,
            ).exists():
                error(f"orphan attendance record pk={attendance.pk}")

        for progress in LectureProgress.objects.select_related("lesson__course_offering"):
            if not Enrollment.objects.filter(
                student_id=progress.student_id,
                academic_year_level_id=progress.lesson.course_offering.academic_year_level_id,
            ).exists():
                error(f"orphan lecture progress pk={progress.pk}")

        self.stdout.write(f"Summary: {len(warnings)} warning(s), {len(errors)} error(s)")
        if errors:
            raise CommandError(f"Academic audit failed with {len(errors)} error(s).")
