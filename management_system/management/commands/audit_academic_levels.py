from django.core.management.base import BaseCommand, CommandError
from django.apps import apps
from django.db.models import Count, F, Q, Sum

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
    VerifiedSegmentRequest,
    ViewingSession,
    User,
)


class Command(BaseCommand):
    help = "Run a read-only academic migration audit; exit non-zero on errors."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        errors = []
        warnings = []
        from django.db import connections

        db_alias = options["database"]
        if db_alias not in connections.databases:
            raise CommandError(f"Database alias {db_alias!r} is not configured.")
        for model in apps.get_app_config("management_system").get_models():
            model.objects._db = db_alias
        db_connection = connections[db_alias]
        with db_connection.cursor() as cursor:
            table_columns = {
                column.name
                for column in db_connection.introspection.get_table_description(
                    cursor, AcademicYearLevel._meta.db_table
                )
            }
            offering_columns = {
                column.name
                for column in db_connection.introspection.get_table_description(
                    cursor, CourseOffering._meta.db_table
                )
            }
            enrollment_columns = {
                column.name
                for column in db_connection.introspection.get_table_description(
                    cursor, Enrollment._meta.db_table
                )
            }
            attendance_columns = {
                column.name
                for column in db_connection.introspection.get_table_description(
                    cursor, AttendanceRecord._meta.db_table
                )
            }
        has_scope_fields = (
            "meeting_weekdays" in table_columns
            and "academic_year_level_id" in offering_columns
            and "academic_year_level_id" in enrollment_columns
            and "academic_year_level_id" in attendance_columns
        )
        has_canonical_scope = has_scope_fields and not {
            "academic_year_id" in offering_columns,
            "academic_year_id" in enrollment_columns,
            "academic_year_id" in attendance_columns,
        }.__contains__(True)
        has_transition_scope = has_scope_fields and not has_canonical_scope

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

        for name in (
            AcademicYear.objects.values("name")
            .annotate(row_count=Count("id"))
            .filter(row_count__gt=1)
        ):
            dates = set(
                AcademicYear.objects.filter(name=name["name"])
                .values_list("starts_on", "ends_on")
            )
            if len(dates) > 1:
                error(f"academic year name {name['name']!r} has conflicting date ranges: {sorted(dates)}")

        current_years = AcademicYear.objects.filter(**{"is_active" if has_canonical_scope else "is_current": True})
        if current_years.count() > 1:
            error(f"multiple active/current academic years: {list(current_years.values_list('id', flat=True))}")

        invalid_levels = Level.objects.filter(Q(ordering__isnull=True) | Q(ordering__lt=1))
        for level in invalid_levels:
            error(f"level pk={level.pk} has invalid ordering={level.ordering!r}")
        duplicate_orderings = Level.objects.values("ordering").annotate(n=Count("id")).filter(n__gt=1)
        for row in duplicate_orderings:
            error(f"duplicate level ordering={row['ordering']}")

        links = set(AcademicYearLevel.objects.values_list("academic_year_id", "level_id"))
        offerings = (
            CourseOffering.objects.select_related("course", "academic_year_level")
            if has_scope_fields
            else CourseOffering.objects.values("id", "academic_year_id", "course__level_id")
        )
        for offering in offerings:
            if not has_scope_fields:
                if (offering["academic_year_id"], offering["course__level_id"]) not in links:
                    error(
                        f"offering pk={offering['id']} has no matching year-level scope"
                    )
                continue
            if offering.academic_year_level_id:
                scope_valid = (
                    (has_canonical_scope or offering.academic_year_level.academic_year_id == offering.academic_year_id)
                    and offering.academic_year_level.level_id == offering.course.level_id
                )
            else:
                scope_valid = (offering.academic_year_id, offering.course.level_id) in links
            if not scope_valid:
                error(
                    f"offering pk={offering.pk} course.level_id={offering.course.level_id} "
                    f"has no matching level link for academic_year pk={offering.academic_year_id}"
                )

        enrollments = (
            Enrollment.objects.select_related("student", "course_offering", "academic_year_level")
            if has_scope_fields
            else Enrollment.objects.values(
                "id", "student_id", "academic_year_id", "course_offering_id",
                "course_offering__academic_year_id",
            )
        )
        for enrollment in enrollments:
            if not has_scope_fields:
                if enrollment["course_offering_id"]:
                    if enrollment["course_offering__academic_year_id"] != enrollment["academic_year_id"]:
                        error(f"targeted enrollment pk={enrollment['id']} has a mismatched academic year")
                else:
                    level_count = AcademicYearLevel.objects.filter(
                        academic_year_id=enrollment["academic_year_id"]
                    ).count()
                    if level_count != 1:
                        error(
                            f"normal enrollment pk={enrollment['id']} student={enrollment['student_id']} "
                            f"has ambiguous level mapping ({level_count} levels in year)"
                        )
                continue
            if enrollment.academic_year_level_id:
                if not has_canonical_scope and enrollment.academic_year_level.academic_year_id != enrollment.academic_year_id:
                    error(f"enrollment pk={enrollment.pk} has a mismatched academic-year scope")
                if enrollment.course_offering_id and (
                    enrollment.course_offering.academic_year_level_id != enrollment.academic_year_level_id
                ):
                    error(f"targeted enrollment pk={enrollment.pk} has a mismatched offering scope")
                continue
            if enrollment.course_offering_id:
                if enrollment.course_offering.academic_year_id != enrollment.academic_year_id:
                    error(
                        f"targeted enrollment pk={enrollment.pk} offering/year mismatch: "
                        f"offering year={enrollment.course_offering.academic_year_id}, "
                        f"enrollment year={enrollment.academic_year_id}"
                    )
            else:
                level_count = AcademicYearLevel.objects.filter(
                    academic_year_id=enrollment.academic_year_id
                ).count()
                if level_count != 1:
                    error(
                        f"normal enrollment pk={enrollment.pk} student={enrollment.student_id} "
                        f"has ambiguous level mapping ({level_count} levels in year)"
                    )

        attendance_records = (
            AttendanceRecord.objects.select_related("student", "academic_year_level")
            if has_scope_fields
            else AttendanceRecord.objects.values("id", "student_id", "academic_year_id")
        )
        for attendance in attendance_records:
            if not has_scope_fields:
                candidate_count = Enrollment.objects.filter(
                    student_id=attendance["student_id"],
                    academic_year_id=attendance["academic_year_id"],
                    course_offering__isnull=True,
                ).count()
                if candidate_count != 1:
                    error(
                        f"attendance pk={attendance['id']} student={attendance['student_id']} "
                        f"has ambiguous year enrollment mapping ({candidate_count} candidates)"
                    )
                continue
            if attendance.academic_year_level_id:
                if not has_canonical_scope and attendance.academic_year_level.academic_year_id != attendance.academic_year_id:
                    error(f"attendance pk={attendance.pk} has a mismatched academic-year scope")
                if not Enrollment.objects.filter(
                    student_id=attendance.student_id,
                    academic_year_level_id=attendance.academic_year_level_id,
                    course_offering__isnull=True,
                ).exists():
                    error(f"attendance pk={attendance.pk} has no matching normal enrollment scope")
                continue
            candidate_count = Enrollment.objects.filter(
                student_id=attendance.student_id,
                academic_year_id=attendance.academic_year_id,
                course_offering__isnull=True,
            ).count()
            if candidate_count != 1:
                error(
                    f"attendance pk={attendance.pk} student={attendance.student_id} "
                    f"has ambiguous year enrollment mapping ({candidate_count} candidates)"
                )

        lessons = (
            Lesson.objects.select_related("course_offering")
            if has_canonical_scope
            else Lesson.objects.select_related("course", "course_offering")
            if has_scope_fields
            else Lesson.objects.values("id", "course_id", "course_offering_id", "course_offering__course_id")
        )
        for lesson in lessons:
            if has_canonical_scope:
                if lesson.course_offering_id is None:
                    error(f"lesson pk={lesson.pk} has no course offering")
                continue
            if not has_scope_fields:
                if lesson["course_offering_id"] is None:
                    error(f"lesson pk={lesson['id']} has no course offering")
                elif lesson["course_id"] != lesson["course_offering__course_id"]:
                    error(f"lesson pk={lesson['id']} course/offering mismatch")
                continue
            if lesson.course_offering_id is None:
                error(f"lesson pk={lesson.pk} has no course offering")
            elif lesson.course_id != lesson.course_offering.course_id:
                error(f"lesson pk={lesson.pk} course/offering mismatch")
        quizzes = (
            Quiz.objects.select_related("course_offering")
            if has_canonical_scope
            else Quiz.objects.select_related("course", "course_offering")
            if has_scope_fields
            else Quiz.objects.values("id", "course_id", "course_offering_id", "course_offering__course_id")
        )
        for quiz in quizzes:
            if has_canonical_scope:
                if quiz.course_offering_id is None:
                    error(f"quiz pk={quiz.pk} has no course offering")
                continue
            if not has_scope_fields:
                if quiz["course_offering_id"] is None:
                    error(f"quiz pk={quiz['id']} has no course offering")
                elif quiz["course_id"] != quiz["course_offering__course_id"]:
                    error(f"quiz pk={quiz['id']} course/offering mismatch")
                continue
            if quiz.course_offering_id is None:
                error(f"quiz pk={quiz.pk} has no course offering")
            elif quiz.course_id != quiz.course_offering.course_id:
                error(f"quiz pk={quiz.pk} course/offering mismatch")

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

        duplicate_scope_field = "academic_year_level_id" if has_canonical_scope else "academic_year_id"
        duplicate_active_normal = (
            Enrollment.objects.filter(
                status=Enrollment.Status.ACTIVE,
                enrollment_type=Enrollment.Type.NORMAL,
            )
            .values("student_id", duplicate_scope_field)
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )
        for row in duplicate_active_normal:
            error(
                f"multiple active normal enrollments for student={row['student_id']} "
                f"scope={row[duplicate_scope_field]}: {row['n']}"
            )

        orphan_attendance = (
            AttendanceRecord.objects.select_related("student")
            if has_scope_fields
            else AttendanceRecord.objects.values("id", "student_id", "academic_year_id")
        )
        for attendance in orphan_attendance:
            student_id = attendance.student_id if has_scope_fields else attendance["student_id"]
            if has_canonical_scope:
                orphan = not Enrollment.objects.filter(
                    student_id=student_id,
                    academic_year_level_id=attendance.academic_year_level_id,
                    course_offering__isnull=True,
                ).exists()
            else:
                academic_year_id = attendance.academic_year_id if has_scope_fields else attendance["academic_year_id"]
                orphan = not Enrollment.objects.filter(
                    student_id=student_id,
                    academic_year_id=academic_year_id,
                    course_offering__isnull=True,
                ).exists()
            if orphan:
                error(f"orphan attendance record pk={attendance.pk if has_scope_fields else attendance['id']}")
        orphan_progress = (
            LectureProgress.objects.select_related("student", "lesson__course_offering")
            if has_scope_fields
            else LectureProgress.objects.values("id", "student_id", "lesson__course_offering__academic_year_id")
        )
        for progress in orphan_progress:
            student_id = progress.student_id if has_scope_fields else progress["student_id"]
            academic_year_id = (
                progress.lesson.course_offering.academic_year_level.academic_year_id
                if has_scope_fields
                else progress["lesson__course_offering__academic_year_id"]
            )
            if has_canonical_scope:
                orphan = not Enrollment.objects.filter(
                    student_id=student_id,
                    academic_year_level_id=progress.lesson.course_offering.academic_year_level_id,
                ).exists()
            else:
                orphan = not Enrollment.objects.filter(
                    student_id=student_id,
                    academic_year_id=academic_year_id,
                ).exists()
            if orphan:
                error(f"orphan lecture progress pk={progress.pk if has_scope_fields else progress['id']}")

        self.stdout.write(f"Summary: {len(warnings)} warning(s), {len(errors)} error(s)")
        if errors:
            raise CommandError(f"Academic audit failed with {len(errors)} error(s).")
