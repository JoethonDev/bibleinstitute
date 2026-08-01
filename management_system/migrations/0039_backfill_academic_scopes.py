from collections import defaultdict

from django.db import migrations


def backfill_academic_scopes(apps, schema_editor):
    """Merge duplicate years and populate every Task 1 transition scope."""
    db_alias = schema_editor.connection.alias
    AcademicYear = apps.get_model("management_system", "AcademicYear")
    AcademicYearLevel = apps.get_model("management_system", "AcademicYearLevel")
    AcademicHoliday = apps.get_model("management_system", "AcademicHoliday")
    CourseOffering = apps.get_model("management_system", "CourseOffering")
    Enrollment = apps.get_model("management_system", "Enrollment")
    AttendanceRecord = apps.get_model("management_system", "AttendanceRecord")
    Level = apps.get_model("management_system", "Level")
    Lesson = apps.get_model("management_system", "Lesson")
    Quiz = apps.get_model("management_system", "Quiz")
    Question = apps.get_model("management_system", "Question")
    Submission = apps.get_model("management_system", "Submission")
    Grade = apps.get_model("management_system", "Grade")
    ViewingSession = apps.get_model("management_system", "ViewingSession")
    VerifiedSegmentRequest = apps.get_model("management_system", "VerifiedSegmentRequest")
    LectureProgress = apps.get_model("management_system", "LectureProgress")

    content_models = (
        CourseOffering,
        Enrollment,
        Lesson,
        Quiz,
        Question,
        Submission,
        Grade,
        AttendanceRecord,
        ViewingSession,
        VerifiedSegmentRequest,
        LectureProgress,
    )
    before_counts = {model._meta.label: model.objects.using(db_alias).count() for model in content_models}

    def fail(message):
        raise RuntimeError(f"Academic scope backfill stopped: {message}")

    def normalized_name(name):
        return (name or "").strip()

    def validated_weekdays(value, source_year_id):
        if value is None:
            fail(f"academic year pk={source_year_id} has no meeting weekdays")
        if not isinstance(value, list):
            fail(f"academic year pk={source_year_id} meeting weekdays are not a list")
        if any(not isinstance(day, int) or isinstance(day, bool) or day < 0 or day > 6 for day in value):
            fail(f"academic year pk={source_year_id} has invalid meeting weekdays: {value!r}")
        if len(value) != len(set(value)):
            fail(f"academic year pk={source_year_id} has duplicate meeting weekdays: {value!r}")
        return list(value)

    years = list(AcademicYear.objects.using(db_alias).order_by("pk"))
    if not years:
        return

    groups = defaultdict(list)
    names_to_dates = defaultdict(set)
    for year in years:
        name = normalized_name(year.name)
        groups[(name, year.starts_on, year.ends_on)].append(year)
        names_to_dates[name].add((year.starts_on, year.ends_on))
    conflicting_names = {name: dates for name, dates in names_to_dates.items() if len(dates) > 1}
    if conflicting_names:
        fail(f"year names have conflicting date ranges: {conflicting_names!r}")
    if sum(1 for year in years if year.is_current) > 1:
        fail("multiple source academic years are marked is_current")

    levels = list(Level.objects.using(db_alias).order_by("ordering", "pk"))
    if any(level.ordering is None or level.ordering < 1 for level in levels):
        fail("a level has a missing or invalid ordering")
    level_orderings = [level.ordering for level in levels]
    if len(level_orderings) != len(set(level_orderings)):
        fail("level ordering is not unique")

    source_to_survivor = {}
    source_links = {}
    source_year_scopes = {}
    canonical_links = {}
    survivors = {}
    for key, source_years in groups.items():
        survivor = min(source_years, key=lambda year: year.pk)
        survivors[survivor.pk] = survivor
        for source_year in source_years:
            source_to_survivor[source_year.pk] = survivor

        source_level_links = {
            year.pk: list(
                AcademicYearLevel.objects.using(db_alias)
                .filter(academic_year_id=year.pk)
                .order_by("level__ordering", "pk")
            )
            for year in source_years
        }
        if any(not links for links in source_level_links.values()):
            fail(
                "academic years have no level links: "
                f"{[year_id for year_id, links in source_level_links.items() if not links]}"
            )

        source_year_by_id = {year.pk: year for year in source_years}
        for year_id, links in source_level_links.items():
            meeting_days = validated_weekdays(source_year_by_id[year_id].meeting_weekdays, year_id)
            canonical_scopes = []
            for link in links:
                link_key = (survivor.pk, link.level_id)
                canonical = canonical_links.get(link_key)
                if canonical is None:
                    canonical, _ = AcademicYearLevel.objects.using(db_alias).get_or_create(
                        academic_year_id=survivor.pk,
                        level_id=link.level_id,
                        defaults={"meeting_weekdays": meeting_days},
                    )
                    if canonical.meeting_weekdays is not None and canonical.meeting_weekdays != meeting_days:
                        fail(
                            f"conflicting existing meeting weekdays for canonical year={survivor.pk}, "
                            f"level={link.level_id}"
                        )
                    canonical.meeting_weekdays = meeting_days
                    canonical.save(update_fields=["meeting_weekdays"], using=db_alias)
                    canonical_links[link_key] = canonical
                elif canonical.meeting_weekdays != meeting_days:
                    fail(
                        f"conflicting meeting weekdays for canonical year={survivor.pk}, "
                        f"level={link.level_id}"
                    )
                source_links[link.pk] = canonical
                canonical_scopes.append(canonical)
            source_year_scopes[year_id] = canonical_scopes

    for offering in CourseOffering.objects.using(db_alias).select_related("course"):
        survivor = source_to_survivor[offering.academic_year_id]
        matching = [
            link for (year_id, level_id), link in canonical_links.items()
            if year_id == survivor.pk and level_id == offering.course.level_id
        ]
        if len(matching) != 1:
            fail(f"offering pk={offering.pk} has {len(matching)} possible year-level scopes")
        offering.academic_year_id = survivor.pk
        offering.academic_year_level_id = matching[0].pk
        offering.save(update_fields=["academic_year", "academic_year_level"], using=db_alias)

    # Attendance is resolved before enrollment academic_year IDs are repointed,
    # so the source-year relationship remains unambiguous during the check.
    for attendance in AttendanceRecord.objects.using(db_alias):
        survivor = source_to_survivor[attendance.academic_year_id]
        candidates = list(
            Enrollment.objects.using(db_alias).filter(
                student_id=attendance.student_id,
                academic_year_id=attendance.academic_year_id,
                course_offering__isnull=True,
            )
        )
        if len(candidates) != 1:
            fail(
                f"attendance pk={attendance.pk} has {len(candidates)} normal enrollment "
                f"candidates in source academic year pk={attendance.academic_year_id}"
            )
        scope_candidates = source_year_scopes[attendance.academic_year_id]
        if len(scope_candidates) != 1:
            fail(
                f"attendance pk={attendance.pk} has {len(scope_candidates)} possible "
                f"levels in source academic year pk={attendance.academic_year_id}"
            )
        attendance.academic_year_id = survivor.pk
        attendance.academic_year_level_id = scope_candidates[0].pk
        attendance.save(update_fields=["academic_year", "academic_year_level"], using=db_alias)

    for enrollment in Enrollment.objects.using(db_alias).select_related("course_offering"):
        survivor = source_to_survivor[enrollment.academic_year_id]
        if enrollment.course_offering_id:
            offering = CourseOffering.objects.using(db_alias).get(pk=enrollment.course_offering_id)
            scope_id = offering.academic_year_level_id
            if scope_id is None:
                fail(f"targeted enrollment pk={enrollment.pk} points to an unscoped offering")
        else:
            possible = source_year_scopes[enrollment.academic_year_id]
            if len(possible) != 1:
                fail(
                    f"normal enrollment pk={enrollment.pk} has {len(possible)} possible "
                    f"levels in source academic year pk={enrollment.academic_year_id}"
                )
            scope_id = possible[0].pk
        enrollment.academic_year_id = survivor.pk
        enrollment.academic_year_level_id = scope_id
        enrollment.save(update_fields=["academic_year", "academic_year_level"], using=db_alias)

    for holiday in AcademicHoliday.objects.using(db_alias).order_by("pk"):
        holiday.academic_year_id = source_to_survivor[holiday.academic_year_id].pk
        holiday.save(update_fields=["academic_year"], using=db_alias)

    for survivor in survivors.values():
        holidays = AcademicHoliday.objects.using(db_alias).filter(
            academic_year_id=survivor.pk
        ).order_by("date", "pk")
        seen_dates = {}
        for holiday in holidays:
            existing = seen_dates.get(holiday.date)
            if existing is None:
                seen_dates[holiday.date] = holiday
            elif existing.name != holiday.name:
                fail(
                    f"holiday date {holiday.date} has conflicting names in academic year "
                    f"pk={survivor.pk}"
                )
            else:
                holiday.delete(using=db_alias)

    canonical_years = sorted(
        survivors.values(), key=lambda year: (year.starts_on, year.ends_on, year.pk)
    )
    for ordering, year in enumerate(canonical_years, start=1):
        year.name = normalized_name(year.name)
        year.ordering = ordering
        year.save(update_fields=["name", "ordering"], using=db_alias)

    active_source = next((year for year in years if year.is_current), None)
    if active_source is not None:
        AcademicYear.objects.using(db_alias).filter(
            pk=source_to_survivor[active_source.pk].pk
        ).update(is_current=True)

    duplicate_link_ids = [
        link_id for link_id, canonical in source_links.items() if link_id != canonical.pk
    ]
    if duplicate_link_ids:
        AcademicYearLevel.objects.using(db_alias).filter(pk__in=duplicate_link_ids).delete()
    duplicate_year_ids = [
        year.pk for year in years if year.pk != source_to_survivor[year.pk].pk
    ]
    if duplicate_year_ids:
        AcademicYear.objects.using(db_alias).filter(pk__in=duplicate_year_ids).delete()

    for model in content_models:
        after_count = model.objects.using(db_alias).count()
        if after_count != before_counts[model._meta.label]:
            fail(
                f"{model._meta.label} count changed from "
                f"{before_counts[model._meta.label]} to {after_count}"
            )


class Migration(migrations.Migration):
    dependencies = [("management_system", "0038_add_academic_scope_transition_and_evaluation")]

    operations = [migrations.RunPython(backfill_academic_scopes, migrations.RunPython.noop)]
