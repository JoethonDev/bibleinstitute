from itertools import islice

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

BATCH_SIZE = 500


def _batched(iterable, size=BATCH_SIZE):
    iterator = iter(iterable)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


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

        def qs(model):
            """Return the model manager bound to the requested audit alias."""

            return model.objects.db_manager(db_alias)

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
            "users": qs(User).count(),
            "levels": qs(Level).count(),
            "academic_years": qs(AcademicYear).count(),
            "year_level_links": qs(AcademicYearLevel).count(),
            "courses": qs(Course).count(),
            "offerings": qs(CourseOffering).count(),
            "enrollments": qs(Enrollment).count(),
            "holidays": qs(AcademicHoliday).count(),
            "lessons": qs(Lesson).count(),
            "quizzes": qs(Quiz).count(),
            "questions": qs(Question).count(),
            "submissions": qs(Submission).count(),
            "grades": qs(Grade).count(),
            "attendance": qs(AttendanceRecord).count(),
            "viewing_sessions": qs(ViewingSession).count(),
            "verified_segments": qs(VerifiedSegmentRequest).count(),
            "lecture_progress": qs(LectureProgress).count(),
        }
        self.stdout.write("Row counts:")
        for name, count in counts.items():
            self.stdout.write(f"  {name}={count}")

        self.stdout.write("Academic-year/level links:")
        for link in qs(AcademicYearLevel).values(
            "id", "academic_year_id", "academic_year__name", "level_id", "level__ordering"
        ):
            self.stdout.write(
                f"  pk={link['id']} year={link['academic_year_id']} ({link['academic_year__name']!r}) "
                f"level={link['level_id']} (ordering={link['level__ordering']})"
            )

        for year in qs(AcademicYear).values("id", "name").annotate(level_count=Count("levels")):
            if year["level_count"] == 0:
                error(f"academic year pk={year['id']} name={year['name']!r} has no levels")

        duplicate_groups = (
            qs(AcademicYear).values("name", "starts_on", "ends_on")
            .annotate(row_count=Count("id"))
            .filter(row_count__gt=1)
        )
        for group in duplicate_groups:
            warning(
                "duplicate candidate year group: "
                f"name={group['name']!r}, starts_on={group['starts_on']}, "
                f"ends_on={group['ends_on']}, rows={group['row_count']}"
            )

        for name in qs(AcademicYear).values("name").annotate(row_count=Count("id")).filter(row_count__gt=1):
            dates = set(
                qs(AcademicYear).filter(name=name["name"]).values_list("starts_on", "ends_on")
            )
            if len(dates) > 1:
                error(f"academic year name {name['name']!r} has conflicting date ranges: {sorted(dates)}")

        current_years = qs(AcademicYear).filter(is_active=True)
        if current_years.count() > 1:
            error(f"multiple active academic years: {list(current_years.values_list('id', flat=True))}")

        invalid_levels = qs(Level).filter(Q(ordering__isnull=True) | Q(ordering__lt=1))
        for level in invalid_levels:
            error(f"level pk={level.pk} has invalid ordering={level.ordering!r}")
        duplicate_orderings = qs(Level).values("ordering").annotate(n=Count("id")).filter(n__gt=1)
        for row in duplicate_orderings:
            error(f"duplicate level ordering={row['ordering']}")

        links = set(qs(AcademicYearLevel).values_list("academic_year_id", "level_id"))
        for offering in qs(CourseOffering).select_related("course", "academic_year_level"):
            scope = offering.academic_year_level
            if (scope.academic_year_id, scope.level_id) not in links:
                error(f"offering pk={offering.pk} has no matching year-level scope")
            if scope.level_id != offering.course.level_id:
                error(
                    f"offering pk={offering.pk} course.level_id={offering.course.level_id} "
                    f"does not match scope level_id={scope.level_id}"
                )

        for enrollment in qs(Enrollment).select_related(
            "course_offering__academic_year_level", "academic_year_level"
        ):
            if enrollment.course_offering_id and (
                enrollment.course_offering.academic_year_level_id != enrollment.academic_year_level_id
            ):
                error(f"enrollment pk={enrollment.pk} has a mismatched offering scope")

        # One bounded fact query per audit run instead of one query per row.
        normal_enrollment_counts = dict(
            qs(Enrollment)
            .filter(
                status=Enrollment.Status.ACTIVE,
                enrollment_type=Enrollment.Type.NORMAL,
            )
            .values("student_id")
            .annotate(total=Count("id"))
            .values_list("student_id", "total")
        )
        enrollment_pairs = set(
            qs(Enrollment).values_list("student_id", "academic_year_level_id")
        )

        for attendance in qs(AttendanceRecord).select_related("course_offering__academic_year_level"):
            if attendance.course_offering_id is None:
                candidates = normal_enrollment_counts.get(attendance.student_id, 0)
                if candidates != 1:
                    error(
                        f"attendance pk={attendance.pk} student={attendance.student_id} "
                        f"has ambiguous normal enrollment mapping ({candidates} candidates)"
                    )

        for lesson in qs(Lesson).select_related("course_offering"):
            if lesson.course_offering_id is None:
                error(f"lesson pk={lesson.pk} has no course offering")
            elif lesson.course_offering.course_id is None:
                error(f"lesson pk={lesson.pk} has an offering without a course")

        for quiz in qs(Quiz).select_related("course_offering"):
            if quiz.course_offering_id is None:
                error(f"quiz pk={quiz.pk} has no course offering")
            elif quiz.course_offering.course_id is None:
                error(f"quiz pk={quiz.pk} has an offering without a course")

        for submission in qs(Submission).filter(is_graded=False).select_related("user", "question"):
            warning(
                f"unresolved manual grading: submission pk={submission.pk}, "
                f"student={submission.user_id}, question={submission.question_id}"
            )

        question_totals = qs(Question).values("quiz_id").annotate(question_total=Sum("grade"))
        totals = dict(qs(Quiz).values_list("id", "total_grade"))
        for row in question_totals:
            if totals.get(row["quiz_id"]) != row["question_total"]:
                error(
                    f"quiz pk={row['quiz_id']} total_grade={totals.get(row['quiz_id'])} "
                    f"does not equal question total={row['question_total']}"
                )

        duplicate_active_normal = (
            qs(Enrollment)
            .filter(
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

        for attendance in qs(AttendanceRecord).filter(course_offering__isnull=True):
            if normal_enrollment_counts.get(attendance.student_id, 0) == 0:
                error(f"orphan attendance record pk={attendance.pk}")

        progress_rows = qs(LectureProgress).select_related("lesson__course_offering").values_list(
            "pk", "student_id", "lesson__course_offering__academic_year_level_id"
        )
        for batch in _batched(progress_rows):
            missing = [
                pk for pk, student_id, scope_id in batch
                if (student_id, scope_id) not in enrollment_pairs
            ]
            for pk in missing:
                error(f"orphan lecture progress pk={pk}")

        self.stdout.write(f"Summary: {len(warnings)} warning(s), {len(errors)} error(s)")
        if errors:
            raise CommandError(f"Academic audit failed with {len(errors)} error(s).")
