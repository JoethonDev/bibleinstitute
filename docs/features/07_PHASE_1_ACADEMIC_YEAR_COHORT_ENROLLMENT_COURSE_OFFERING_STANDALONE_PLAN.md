# Phase 1 Standalone Plan: Academic Year, Cohort, Enrollment, Course Offering

> **SUPERSEDED — DO NOT IMPLEMENT.** This documents the abandoned Cohort design. Use `docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md`; see `06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md` for the decision mapping.

Created: 2026-06-11 14:59:58 +03:00

Status: Implementation complete (2026-06-11)

Parent roadmap: `06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md`

Latest implementation review: `08_PHASE_1_IMPLEMENTATION_REVIEW.md`

Application area: `management_system`

## Purpose

Phase 1 introduces explicit academic structure without changing content versioning yet.

The current LMS decides student access mostly from:

- `User.role`
- `User.joined_date`
- `Course.level`
- `Lesson.created_date`
- `Quiz.opening_date` and `Quiz.closing_date`

Phase 1 must add explicit models for academic years, cohorts, course offerings, and enrollments while preserving the current student experience as much as possible.

This phase should answer:

- Which academic year does this student belong to?
- Which cohort is this student enrolled in?
- Which course offerings are available to this student?
- Can a student be enrolled in a failed/repeated course from another level/year?
- Can admins inspect and manage these relationships?

This phase should not yet solve:

- Lesson versioning.
- Quiz/question versioning.
- Quiz retake exceptions.
- Historical immutable content snapshots.
- Attendance.
- Transcripts.
- Promotion rules.

Those belong to later phases.

## Cheap Model Instructions

Use these rules if implementation is delegated to a cheaper or less context-aware model.

1. Do not redesign the whole LMS.
2. Do not delete existing models.
3. Do not remove old role/date access logic immediately.
4. Add new models first, then add compatibility helpers, then migrate usage gradually.
5. Preserve all existing passing tests from Phase 0.
6. Add tests before changing course access behavior.
7. Keep changes small and local.
8. Do not implement lesson/quiz versioning in Phase 1.
9. Do not change quiz submission form field names.
10. Do not change lesson stream URL behavior.
11. Do not change upload/R2 behavior.
12. Do not change dashboard visual design unless needed for new admin pages.
13. If a mapping is ambiguous, preserve access and create a review item.
14. If old behavior and new enrollment behavior disagree, log/report the mismatch before tightening access.
15. Use `get_or_create` in migrations where possible.
16. Use unique constraints to prevent duplicate academic years, cohorts, offerings, and enrollments.
17. Keep management users role-based for now.
18. Use readable helper methods for access decisions.
19. Do not hardcode current academic year from the system date only. Store it in `AcademicYear`.
20. Run tests after each major step.
21. All new imports must be added at the top of the file. Do not add imports inside functions, methods, conditionals, loops, or migration helper bodies. If a circular import appears, restructure the code instead of hiding the import locally.

Recommended cheap-model prompt:

```text
Implement Phase 1 only. Add AcademicYear, Cohort, CourseOffering, Enrollment, and MigrationReviewItem. Preserve existing course, lesson, quiz, and submission behavior. Add data migration from existing User/Course/Lesson/Quiz/Grade data. Add admin registration and simple tests for normal enrollment, repeat enrollment, management access, and preservation of existing course/lesson visibility. Do not implement content versioning or special quiz openings. Add every new import at the top of its file only; never add imports inside functions or methods.
```

## Scope Boundary

In scope:

- New academic/enrollment models.
- Database constraints.
- Admin registration for new models.
- Migration logic to create initial academic years, cohorts, course offerings, and enrollments.
- A review/report mechanism for ambiguous migration records.
- Course listing access through enrollments with fallback compatibility.
- Repeat course enrollment support.
- Tests for access preservation and repeat enrollment.

Out of scope:

- Replacing `Lesson` with `LessonVersion`.
- Replacing `Quiz` with `QuizVersion`.
- Replacing `Grade` with `QuizAttempt`.
- Replacing `Submission` with `AnswerSubmission`.
- Reworking all dashboards.
- Full transcript/promotion logic.
- Bulk import/export.
- UI redesign.

## Current Code Areas To Inspect

Before implementation, inspect these files:

- `management_system/models.py`
- `management_system/views.py`
- `management_system/admin.py`
- `management_system/urls.py`
- `management_system/test_course_lesson.py`
- `management_system/test_phase0_stabilization.py`
- `management_system/templates/course_view.html`
- `management_system/templates/course_detail.html`
- `management_system/templates/dashboard.html`
- `management_system/utils/helpers.py`
- `management_system/utils/decorators.py`

Do not assume undocumented behavior. Read the current logic first.

## Strict Import Rule

All implementation tasks in this plan must follow this rule:

- Add every new import at the top of the file.
- Do not add imports inside functions.
- Do not add imports inside methods.
- Do not add imports inside conditionals.
- Do not add imports inside loops.
- Do not add imports inside migration helper functions.
- If moving an import to the top creates a circular import, fix the module structure or move the shared logic to a neutral module.
- Do not use local imports as a shortcut.

This rule applies to models, views, helpers, admin files, tests, migrations, and any future Phase 1 follow-up code.

## Data Model

### AcademicYear

Purpose:

- Represents a formal academic year such as `2024-2025`.
- Replaces implicit academic-year calculations based only on dates.
- Provides a stable object for filtering dashboards and course offerings.

Recommended fields:

```python
class AcademicYear(models.Model):
    name = models.CharField(max_length=20, unique=True)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- `starts_on < ends_on`.
- Only one row can have `is_current=True`.
- `name` should be unique.
- Optional unique constraint on `(starts_on, ends_on)`.

Validation:

- Reject `ends_on <= starts_on`.
- Reject multiple current academic years.
- Expected name format is `YYYY-YYYY`, but do not depend only on string parsing for logic.

### Cohort

Purpose:

- Represents a student group in one academic year and one level.
- Example: First Year 2024-2025.

Recommended fields:

```python
class Cohort(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("archived", "Archived"),
    ]

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="cohorts")
    level = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- Unique `(academic_year, level)`.
- `level` must map to existing `Course.level` values.
- Status should be one of `draft`, `active`, `archived`.

Validation:

- Do not allow negative or zero levels.
- In current system, valid student levels are `1` and `2`.
- Management roles do not need cohorts.

### CourseOffering

Purpose:

- Represents a course as delivered to a specific cohort.
- This becomes the bridge between existing `Course` and future versioned content.

Recommended fields:

```python
class CourseOffering(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("archived", "Archived"),
    ]

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="offerings")
    cohort = models.ForeignKey(Cohort, on_delete=models.PROTECT, related_name="course_offerings")
    instructor = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="published")
    published_at = models.DateTimeField(null=True, blank=True)
    migration_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- Unique `(course, cohort)`.
- `course.level` should normally match `cohort.level`.

Validation:

- For normal course offerings, `course.level == cohort.level`.
- If a mismatch is ever allowed, it must be explicit and reviewed. Prefer not to allow mismatch in Phase 1.

### Enrollment

Purpose:

- Represents a student's access to a cohort or one specific course offering.
- Supports both normal cohort enrollment and failed/repeated course enrollment.

Recommended fields:

```python
class Enrollment(models.Model):
    ENROLLMENT_TYPES = [
        ("normal", "Normal"),
        ("repeat", "Repeat"),
        ("remedial", "Remedial"),
        ("manual", "Manual"),
    ]

    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("completed", "Completed"),
        ("withdrawn", "Withdrawn"),
    ]

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    cohort = models.ForeignKey(Cohort, on_delete=models.PROTECT, related_name="enrollments")
    course_offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, null=True, blank=True, related_name="enrollments")
    enrollment_type = models.CharField(max_length=20, choices=ENROLLMENT_TYPES, default="normal")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    enrolled_at = models.DateTimeField(auto_now_add=True)
    enrolled_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="created_enrollments")
    migration_notes = models.TextField(blank=True)
```

Interpretation:

- `normal` enrollment with `course_offering=None` grants access to all published course offerings in the cohort.
- `repeat`, `remedial`, or `manual` enrollment with `course_offering` grants access to only that course offering.
- `inactive`, `withdrawn`, or archived relationships should not grant student access.

Constraints:

- Prevent duplicate normal enrollment for `(student, cohort, enrollment_type="normal", course_offering=None)`.
- Prevent duplicate course-specific enrollment for `(student, course_offering, enrollment_type)` where `course_offering` is not null.
- A student should not have two active normal enrollments in the same cohort.

Important implementation detail:

- Django unique constraints with `NULL` fields need careful handling.
- Use conditional `UniqueConstraint` where supported.
- If conditional constraints are too risky for current database support, enforce duplicates in model validation plus migration code.

### MigrationReviewItem

Purpose:

- Records ambiguous migration cases for admin/engineering review.
- This prevents silent wrong mappings.

Recommended fields:

```python
class MigrationReviewItem(models.Model):
    item_type = models.CharField(max_length=50)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField()
    severity = models.CharField(max_length=20, default="info")
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

Suggested item types:

- `missing_user_joined_date`
- `unknown_student_role`
- `course_without_level`
- `content_without_date`
- `quiz_reused_across_years`
- `grade_outside_normal_enrollment`
- `lesson_access_mismatch`
- `course_offering_created_by_fallback`

## Academic Year Mapping

Use the existing August academic-year rule.

Algorithm:

```python
def academic_year_for_date(value):
    if value.month < 8:
        start_year = value.year - 1
    else:
        start_year = value.year

    return {
        "name": f"{start_year}-{start_year + 1}",
        "starts_on": date(start_year, 8, 1),
        "ends_on": date(start_year + 1, 7, 31),
    }
```

Dates to consider:

- `User.joined_date`
- `Lesson.created_date`
- `Quiz.opening_date`
- `Grade.submitted_at`

Fallback:

- If a required date is missing, use the current academic year only as a fallback.
- Create a `MigrationReviewItem`.

## Migration Strategy

### Migration 1: Schema

Tasks:

1. Add `AcademicYear`.
2. Add `Cohort`.
3. Add `CourseOffering`.
4. Add `Enrollment`.
5. Add `MigrationReviewItem`.
6. Add indexes and constraints.
7. Register models in admin.

Validation:

- `python manage.py makemigrations management_system`
- Review migration manually.
- Ensure migration does not alter unrelated models.

### Migration 2: Data

Tasks:

1. Create academic years from users, lessons, quizzes, and grades.
2. Create cohorts from student users and course/content levels.
3. Create normal enrollments for junior/senior students.
4. Create course offerings for existing courses per inferred academic year/cohort.
5. Create repeat/manual enrollments when a student has grades outside their normal cohort/offering access.
6. Write review items for ambiguous mappings.

Data migration must be idempotent where possible:

- Use `get_or_create`.
- Use deterministic names.
- Avoid duplicate enrollments.
- Avoid duplicate course offerings.
- Never delete old data.

### Migration 3: Optional Cleanup

Only use if needed:

- Add missing indexes after data migration.
- Add stricter constraints after bad data is normalized.

Do not overuse this migration. Prefer two migrations unless the database needs a staged constraint rollout.

## Detailed Implementation Tasks

### Task 1: Add Models

Status: Done (2026-06-11)

- New models live in `management_system/models.py` (lines ~488-765):
  `AcademicYear`, `Cohort`, `CourseOffering`, `Enrollment`, `MigrationReviewItem`.
- Existing Phase 0 models untouched.
- Helper `academic_year_bounds_for_date` added alongside the models for shared use.

Files touched:

- `management_system/models.py`
- New migration file under `management_system/migrations/`

Steps:

1. Add model classes near related academic/course models.
2. Use `on_delete=models.PROTECT` for academic structure relationships where deletion would destroy history.
3. Use `on_delete=models.CASCADE` only for student-owned enrollments if student deletion should remove access rows.
4. Add `__str__` methods for readable admin display.
5. Add `Meta.ordering`.
6. Add constraints carefully.

Acceptance criteria:

- New models import successfully.
- `makemigrations` creates only expected schema changes.
- `python manage.py check` passes except unrelated existing warnings.

### Task 2: Add Admin Registration

Status: Done (2026-06-11)

- `management_system/admin.py` rewritten to register each new model with
  list_display, list_filter, search_fields, ordering, and raw_id_fields where
  helpful. Existing model registrations preserved.
- `MigrationReviewItem` admin includes bulk "mark resolved/unresolved" actions.

Files touched:

- `management_system/admin.py`

Admin behavior:

- Admin can list academic years.
- Admin can list cohorts.
- Admin can list course offerings.
- Admin can list enrollments.
- Admin can search by student username, course name, cohort name, academic year name.
- Admin can filter by academic year, cohort status, offering status, enrollment type, enrollment status.

Recommended admin fields:

AcademicYear:

- list display: `name`, `starts_on`, `ends_on`, `is_current`
- filters: `is_current`

Cohort:

- list display: `name`, `academic_year`, `level`, `status`
- filters: `academic_year`, `level`, `status`

CourseOffering:

- list display: `course`, `cohort`, `status`, `published_at`
- filters: `cohort__academic_year`, `cohort__level`, `status`

Enrollment:

- list display: `student`, `cohort`, `course_offering`, `enrollment_type`, `status`, `enrolled_at`
- filters: `enrollment_type`, `status`, `cohort__academic_year`, `cohort__level`

MigrationReviewItem:

- list display: `item_type`, `severity`, `resolved`, `created_at`
- filters: `item_type`, `severity`, `resolved`

Acceptance criteria:

- Staff/admin can inspect new records in Django admin.
- Admin list pages do not trigger excessive queries for simple listing.

### Task 3: Add Access Helper Functions

Status: Done (2026-06-11)

- Added to `management_system/utils/helpers.py`:
  `get_current_academic_year`, `get_user_active_enrollments`,
  `get_course_offerings_for_user`, `user_has_course_offering_access`,
  `get_courses_for_user_with_enrollment_fallback`, plus an internal helper
  `_student_course_access_via_enrollment`.
- `user_can_access_course` now prefers enrollment data but falls back to
  Phase 0 `Course.can_access` when no enrollment exists for the student.
- `Course.fetch_courses_by_role` is intentionally preserved as the fallback.

Files touched:

- `management_system/utils/helpers.py`
- Possibly `management_system/models.py` if model methods are preferred.

Recommended helpers:

```python
def get_current_academic_year():
    ...

def get_user_active_enrollments(user):
    ...

def get_course_offerings_for_user(user):
    ...

def user_has_course_offering_access(user, course_offering):
    ...

def get_courses_for_user_with_enrollment_fallback(user):
    ...
```

Compatibility rule:

- Management users keep role-based access to all courses/course offerings.
- Students should use enrollments if enrollment records exist.
- If no enrollment records exist for a student, fall back to the current Phase 0 role/date behavior.

Important:

- Do not remove `Course.fetch_courses_by_role` yet.
- Use fallback during rollout so a failed migration does not lock students out.

Acceptance criteria:

- Existing course list still works if no Phase 1 data exists.
- Existing course list works from enrollments if Phase 1 data exists.
- Management users still see all courses.

### Task 4: Data Migration For Academic Years

Status: Done (2026-06-11)

- `populate_academic_years` inside
  `management_system/migrations/0020_phase1_populate_academic_structure.py`
  scans `User.joined_date`, `Lesson.created_date`, `Quiz.opening_date`, and
  `Grade.submitted_at`, then `get_or_create`s an `AcademicYear` per matching
  August window.
- Guarantees an academic year exists for today's date and marks exactly one
  row `is_current=True`. Multiple pre-existing `is_current=True` rows raise a
  `multiple_current_academic_years` review item without flipping anything.

Files touched:

- New data migration file.

Steps:

1. Scan student `User.joined_date`.
2. Scan `Lesson.created_date`.
3. Scan `Quiz.opening_date`.
4. Scan `Grade.submitted_at`.
5. Create academic years using August rule.
6. Set one `is_current=True`.

Current academic year rule:

- Prefer the year containing today's date.
- If no matching year exists, create it.
- Ensure only one current academic year.

Acceptance criteria:

- Academic years exist for all relevant old data.
- No duplicate academic years.
- Exactly one current academic year.

### Task 5: Data Migration For Cohorts

Status: Done (2026-06-11)

- `populate_cohorts_and_normal_enrollments` walks every user. Junior users
  are mapped to level 1 cohorts, senior to level 2, all derived from
  `joined_date`. Admin/teacher accounts are skipped entirely. Users with
  missing `joined_date` raise a `missing_user_joined_date` review item and
  fall back to the current academic year. Users with unknown roles raise
  an `unknown_student_role` review item and are skipped.

Steps:

1. For each student user with role `junior`, map to level `1`.
2. For each student user with role `senior`, map to level `2`.
3. Determine academic year from `joined_date`.
4. Create or reuse cohort for `(academic_year, level)`.
5. Name cohort as:
   - `First Year {academic_year.name}` for level `1`
   - `Second Year {academic_year.name}` for level `2`
6. Do not create normal cohorts for admin/teacher users.

Acceptance criteria:

- Junior users have level 1 cohorts.
- Senior users have level 2 cohorts.
- Admin and teacher users are not accidentally enrolled as students.

### Task 6: Data Migration For Course Offerings

Status: Done (2026-06-11)

- `populate_course_offerings` walks every course, builds the set of academic
  years from its lessons and quizzes, and `get_or_create`s a
  `CourseOffering` per `(course, cohort)` pair. Courses with no level raise
  a `course_without_level` review item. Courses with dated content but no
  matching cohort still receive an offering through the safe-cohort helper.
- Cross-year quiz reuse triggers a `quiz_reused_across_years` review item.
- Courses with no lessons and no quizzes receive a current-year fallback
  offering plus a `course_offering_created_by_fallback` review item.

Steps:

1. For each `Course`, find academic years from related lessons.
2. Add academic years from related quizzes.
3. Add academic years from related grades.
4. For each `(course, academic_year)`, find or create the cohort matching `course.level`.
5. Create `CourseOffering(course=course, cohort=cohort)`.
6. Copy `course.instructor` if present.
7. Set `status="published"` for migrated offerings.
8. Add `migration_notes` where fallback was used.

Fallback:

- If a course has no dated content or grades, create offering for current academic year only if needed by an active student cohort.
- Create a review item.

Acceptance criteria:

- Every visible historical course/year has a course offering.
- No duplicate course offerings for the same course/cohort.
- Course offering level matches cohort level.

### Task 7: Data Migration For Enrollments

Status: Done (2026-06-11)

- `populate_cohorts_and_normal_enrollments` creates one active normal
  enrollment per junior/senior user in the matching cohort.
- `populate_repeat_enrollments_from_grades` iterates every Grade. When the
  grade is outside the student's normal cohort access, a `repeat` (lower
  level) or `manual` (different year) enrollment is created against the
  derived `CourseOffering`. Each inferred enrollment writes a
  `grade_outside_normal_enrollment` review item with the original Grade id.

Normal enrollment:

1. For each junior/senior student, create one normal enrollment in the cohort inferred from `joined_date`.
2. Set `course_offering=None`.
3. Set `enrollment_type="normal"`.
4. Set `status="active"`.

Repeat/manual enrollment:

1. For each `Grade`, find the student's normal cohort.
2. Find the quiz course.
3. Find or create course offering based on the quiz/grade academic year.
4. If the course offering is not accessible through the student's normal cohort enrollment, create `Enrollment(enrollment_type="repeat" or "manual", course_offering=offering)`.
5. Create a `MigrationReviewItem` explaining the inferred repeat/manual enrollment.

Suggested rule:

- Use `repeat` when the course level is lower than the student's normal cohort level.
- Use `manual` when the relationship is ambiguous.

Acceptance criteria:

- Every junior/senior student has one active normal enrollment.
- Students with grades outside normal access get explicit course-specific enrollment.
- Ambiguous inferred enrollments are reviewable.

### Task 8: Update Course Listing

Status: Done (2026-06-11)

- `view_courses` and `ProfileDetail.get_context_data` now call
  `get_courses_for_user_with_enrollment_fallback(user)` instead of
  `Course.fetch_courses_by_role(user.role.role)` directly. The fallback
  helper returns the same `{"level_name", "courses"}` shape so
  `course_view.html` keeps rendering without template edits.
- `index` (home dashboard) also uses the same helper so the open-quiz count
  reflects the student's actual enrollment.

Files touched:

- `management_system/views.py`
- `management_system/utils/helpers.py`
- Possibly `management_system/templates/course_view.html`

Current behavior:

- `view_courses` calls `Course.fetch_courses_by_role(user.role.role)`.

Target Phase 1 behavior:

- Management users: see all courses grouped by level, same as today.
- Students with active enrollments: see courses from their normal cohort and course-specific repeat/manual enrollments.
- Students without enrollments: use existing role-based fallback.

Important:

- Keep the output shape expected by `course_view.html` unless intentionally updating the template.
- Existing template expects:

```python
[
    {
        "level_name": "...",
        "courses": [...]
    }
]
```

Cheap implementation option:

- Return `Course` objects in the same grouped shape.
- Attach optional `course.active_offering` for future use if needed.

Acceptance criteria:

- Existing course page renders without template errors.
- A normal junior sees level 1 enrolled courses.
- A normal senior sees level 1 and level 2 courses if that preserves old behavior, or sees enrolled cohort offerings if new data is present. This decision must be tested.
- A repeat enrollment can expose a specific lower-level course even if normal enrollment would not.

Decision needed:

- Should normal cohort enrollment grant all offerings for the student's exact cohort only, or preserve old role behavior where senior users see level 1 and level 2?

Recommended Phase 1 compatibility decision:

- Preserve old role-level behavior for now in listing, but annotate/refactor through offerings.
- Tighten to exact cohort access only after Phase 2 versioning exists and migration equivalence is verified.

### Task 9: Update Course Detail Access

Status: Done (2026-06-11)

- `view_course_details` and `view_lesson_details` keep calling
  `user_can_access_course(user, course)`; the helper is now
  enrollment-aware with Phase 0 fallback. Repeat/manual enrollments grant
  course access automatically.
- Lesson filtering inside the course detail view still uses
  `Lesson.created_date` date-window logic per the Phase 1 plan. Lessons are
  not attached to course offerings yet (Phase 2 work).

Files touched:

- `management_system/views.py`
- `management_system/utils/helpers.py`

Current behavior:

- Course detail checks role/course level.
- Lesson visibility still uses `joined_date` and `Lesson.created_date`.

Target Phase 1 behavior:

- Course detail access should allow:
  - Management users.
  - Students with normal enrollment that grants this course.
  - Students with course-specific repeat/manual enrollment.
  - Students through fallback old behavior if no enrollments exist.

Lesson filtering:

- Keep Phase 0 lesson date filtering during Phase 1.
- Do not attach lessons to course offerings yet.
- Do not show all historical lessons just because a course offering exists.

Acceptance criteria:

- Existing lesson visibility tests still pass.
- Repeat enrollment can allow course detail access.
- Direct lesson detail/stream still requires date-window access until Phase 2 defines content versioning.

### Task 10: Admin Dashboard Pages

Status: Done (2026-06-11) – minimum viable scope

- Django admin registration covers all five new models with filters and
  search. Per the plan's recommended Phase 1 approach, no separate custom
  dashboard pages were added. Existing role/permission decorators continue
  to enforce that students cannot access `/admin/`.

Files touched:

- `management_system/views.py`
- `management_system/urls.py`
- templates, if custom dashboards are added.

Minimum viable implementation:

- Django admin registration is enough for initial inspection.
- If existing custom dashboard pattern is easy to reuse, add simple dashboard pages for:
  - Academic years.
  - Cohorts.
  - Course offerings.
  - Enrollments.

Recommended Phase 1 approach:

- Start with Django admin registration.
- Add custom dashboard pages only if needed by product flow.

Acceptance criteria:

- Admin can view and edit records somewhere.
- No student can access these admin pages.
- Teacher access follows existing management-role policy unless product decides otherwise.

### Task 11: Migration Review Output

Status: Done (2026-06-11)

- `MigrationReviewItem` is registered in admin with filters by item_type,
  severity, and resolved.
- The data migration writes review items for: missing joined_date,
  unknown student role, course without level, dateless courses (fallback
  offering), grades outside normal enrollment, cross-year quiz reuse,
  multiple-current academic year drift.

Files touched:

- `models.py`
- data migration
- optional admin registration

Review item creation cases:

- Missing `joined_date`.
- Student role is neither junior nor senior.
- Course has no level.
- Course has dated content but no matching cohort.
- Grade maps to an offering outside normal access.
- Quiz has grades across multiple academic years.
- Existing behavior grants access but enrollment mapping does not.

Acceptance criteria:

- Ambiguous cases are not silent.
- Admin/engineering can inspect review items.
- Review items do not block migration unless data corruption would occur.

## Testing Plan

Status: Done (2026-06-11)

All Phase 1 tests live in
`management_system/test_phase1_academic_structure.py` (29 tests, all green).
The Phase 0 stabilization suite still passes (4/4). The two pre-existing
errors in `management_system.test_course_lesson` are unrelated to Phase 1
work; they fail on `main` because the tests hardcode `Course.pk=1/2` while
the PostgreSQL backend advances sequence values across tests. This was
verified by running the same suite against the unchanged code (`git stash`).

### Unit Tests

Add tests for:

1. Academic year mapping before August.
2. Academic year mapping in/after August.
3. `AcademicYear` date validation.
4. One current academic year constraint.
5. Cohort uniqueness per academic year and level.
6. CourseOffering uniqueness per course and cohort.
7. Normal enrollment duplicate prevention.
8. Course-specific repeat enrollment duplicate prevention.

### Migration Tests

Add tests or management-command-style assertions for:

1. Junior user creates level 1 cohort and normal enrollment.
2. Senior user creates level 2 cohort and normal enrollment.
3. Admin/teacher users do not create student enrollments.
4. Course with lessons in one academic year creates one offering.
5. Course with quizzes in another academic year creates another offering.
6. Grade outside normal mapping creates repeat/manual enrollment.
7. Ambiguous data creates `MigrationReviewItem`.

### View Tests

Add tests for:

1. Student with normal enrollment sees expected course list.
2. Student without enrollment still uses fallback behavior.
3. Management user still sees all courses.
4. Student with repeat enrollment sees the repeated course.
5. Student without repeat enrollment cannot access a course outside allowed access.
6. Course detail still filters lessons by old date-window behavior.
7. Direct lesson stream remains protected.
8. Quiz status API still works after course access helper changes.

### Regression Tests To Preserve

These tests must continue to pass:

- `management_system.test_course_lesson`
- `management_system.test_phase0_stabilization`

If they fail:

- Do not update expectations until confirming whether Phase 1 intentionally changed behavior.
- Prefer compatibility fallback if possible.

## Acceptance Criteria

Status: All items verified (2026-06-11). See `Validation Checklist` below
for the exact commands run.

Phase 1 is complete when all are true:

1. `AcademicYear`, `Cohort`, `CourseOffering`, `Enrollment`, and `MigrationReviewItem` exist.
2. New models have admin registration.
3. New models have meaningful constraints or duplicate-protection logic.
4. Data migration creates academic years from existing users/content/quizzes/grades.
5. Data migration creates normal cohorts and enrollments for junior/senior students.
6. Data migration creates course offerings for historical course/year combinations.
7. Repeat/manual enrollment is supported for failed or out-of-normal-mapping courses.
8. Ambiguous mappings create review records.
9. Course listing can use enrollments while preserving old behavior through fallback.
10. Course detail access supports enrollment-based access.
11. Existing Phase 0 lesson and quiz access behavior remains intact.
12. Management users retain broad access.
13. Students cannot gain unauthorized access because of a malformed enrollment.
14. Tests cover normal enrollment, repeat enrollment, management access, fallback access, and migration mapping.
15. Full app test suite passes in the available local test configuration.

## Validation Checklist

Status: Executed on 2026-06-11 against the project's configured PostgreSQL
test database (`test_mydatabase`).

Commands run and outcomes:

- `python manage.py check` -> only the unrelated `staticfiles.W004` warning.
- `python manage.py makemigrations --check --dry-run` -> `No changes detected`.
- `python manage.py test management_system.test_phase1_academic_structure`
  -> 29 tests, all OK.
- `python manage.py test management_system.test_phase0_stabilization
  management_system.test_phase1_academic_structure` -> 33 tests, all OK
  (no Phase 0 regression).
- `python manage.py test management_system` -> 34 OK, 2 pre-existing PK
  ordering errors in `test_course_lesson.py` that also fail on `main`.

Run before marking Phase 1 complete:

```powershell
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test management_system
```

If local PostgreSQL is unavailable, use a temporary SQLite test configuration only for code validation, and clearly report that production DB tests were not run.

Manual validation:

1. Create or migrate a junior student.
2. Confirm they have a normal enrollment.
3. Confirm they see expected first-year courses.
4. Create or migrate a senior student.
5. Confirm they have a normal enrollment.
6. Confirm they see expected second-year/compatible courses.
7. Add a repeat enrollment for a senior student into a first-year course offering.
8. Confirm the student can see/access that repeated course.
9. Confirm a student without repeat enrollment cannot access that repeated course by direct URL.
10. Confirm admin can inspect academic years, cohorts, offerings, enrollments, and review items.

## Edge Cases

- Student has no role.
- Student has admin/teacher role but also needs student access.
- Student has null or unexpected `joined_date`.
- Course has no lessons and no quizzes.
- Course has lessons from multiple academic years.
- Quiz was reused across multiple years.
- Grade submitted in a different year than quiz opening date.
- Student changed from junior to senior after initial enrollment.
- Existing senior role currently sees lower-level courses.
- Repeat course belongs to a previous academic year.
- Repeat course belongs to the current offering because the course is being retaught.
- A course is archived but still has historical grades.
- Multiple active academic years accidentally exist.
- No active/current academic year exists.
- Duplicate enrollment rows exist because of repeated migration attempts.
- Migration runs on partially migrated data.

## Risk Controls

High-risk area: locking students out.

Control:

- Keep fallback to Phase 0 access when enrollment data is missing.
- Add review items for mismatches.

High-risk area: duplicate enrollments.

Control:

- Use constraints where possible.
- Use `get_or_create`.
- Test repeated migration behavior.

High-risk area: wrong academic-year mapping.

Control:

- Centralize August mapping helper.
- Test dates in July and August.

High-risk area: repeat course semantics.

Control:

- Store repeat/manual enrollment explicitly.
- Do not infer promotion or transcript rules in Phase 1.

High-risk area: changing course detail too early.

Control:

- Keep lesson filtering by `Lesson.created_date`.
- Do not move lesson content under course offerings until Phase 2.

## Implementation Order

1. Add tests for academic-year mapping helper.
2. Add models and schema migration.
3. Add admin registration.
4. Add data migration helper functions.
5. Add data migration.
6. Run migration locally or in test database.
7. Add access helpers using enrollment with fallback.
8. Update course listing to call new helper.
9. Update course detail access to recognize enrollment/repeat access.
10. Add tests for normal enrollment.
11. Add tests for repeat enrollment.
12. Add tests for management access.
13. Add migration review item tests.
14. Run full test suite.
15. Update parent roadmap Phase 1 status and completion notes.

Before each implementation step, check that any required imports are added at the top of the edited file only.

## Do Not Do In Phase 1

- Do not migrate `Lesson` rows into `LessonVersion`.
- Do not migrate `Quiz` rows into `QuizVersion`.
- Do not change `Question`.
- Do not change quiz submission storage.
- Do not add special openings.
- Do not build transcript calculations.
- Do not add attendance.
- Do not remove `Course.fetch_courses_by_role`.
- Do not remove `User.role`.
- Do not remove `User.joined_date`.
- Do not redesign UI pages.
- Do not add local imports inside functions or methods.

## Final Completion Note Template

When Phase 1 is complete, add this to the parent roadmap:

```md
Completion notes:

- Added explicit academic year, cohort, course offering, and enrollment models.
- Migrated existing users/content/quizzes/grades into academic structures using the August academic-year rule.
- Added migration review records for ambiguous mappings.
- Updated course access helpers to use enrollments with Phase 0 fallback compatibility.
- Added normal and repeat enrollment test coverage.
- Verified existing Phase 0 access behavior remains intact.
```

## Implementation Log

Completed: 2026-06-11

Summary of work delivered:

- New models in `management_system/models.py`: `AcademicYear`, `Cohort`,
  `CourseOffering`, `Enrollment`, `MigrationReviewItem`, plus the
  `academic_year_bounds_for_date` helper and `STUDENT_ROLE_TO_LEVEL` map.
  Each model has Meta ordering, CheckConstraint / UniqueConstraint guards,
  `__str__`, and where applicable a `clean()` for human-facing validation
  (course-level matching cohort-level, single current academic year, etc.).
- Phase 1 access helpers added to `management_system/utils/helpers.py`:
  `get_current_academic_year`, `get_user_active_enrollments`,
  `get_course_offerings_for_user`, `user_has_course_offering_access`, and
  `get_courses_for_user_with_enrollment_fallback`. `user_can_access_course`
  is now enrollment-aware with Phase 0 fallback.
- Admin registration in `management_system/admin.py` for all five new models.
  `MigrationReviewItem` has bulk resolve/unresolve actions.
- Three migrations:
  - `0018_align_question_type_choices.py` (Phase 0 drift cleanup, kept
    separate from Phase 1 schema per plan).
  - `0019_phase1_academic_structure.py` (schema).
  - `0020_phase1_populate_academic_structure.py` (idempotent data
    population from existing User/Lesson/Quiz/Grade rows).
- View updates in `management_system/views.py`:
  - `view_courses`, `index`, and `ProfileDetail.get_context_data` now use
    `get_courses_for_user_with_enrollment_fallback`.
  - `view_course_details` / `view_lesson_details` inherit the new
    `user_can_access_course` behavior automatically.
  - Lesson date-window filtering retained per plan ("do not change lesson
    visibility in Phase 1").
- Tests in `management_system/test_phase1_academic_structure.py` (29 tests
  passing) covering: academic-year helper boundaries, AcademicYear date /
  single-current constraints, Cohort / CourseOffering / Enrollment unique
  constraints, fallback behavior, enrollment-based access, management
  broad access, repeat-enrollment grants single-course access, Phase 0
  lesson visibility regression, and migration mapping including
  management-user skip, dateless-course fallback, idempotency, and
  cross-cohort repeat enrollment from grades.

Files added or modified:

- Modified: `management_system/models.py`
- Modified: `management_system/admin.py`
- Modified: `management_system/utils/helpers.py`
- Modified: `management_system/views.py`
- Added: `management_system/migrations/0018_align_question_type_choices.py`
- Added: `management_system/migrations/0019_phase1_academic_structure.py`
- Added: `management_system/migrations/0020_phase1_populate_academic_structure.py`
- Added: `management_system/test_phase1_academic_structure.py`

Out-of-scope items intentionally NOT touched (Phase 2+ work):

- Lesson / Quiz / Question / Submission / Grade models.
- `Course.fetch_courses_by_role` (retained as fallback).
- `User.role` / `User.joined_date` (retained for fallback).
- Lesson stream behavior, upload/R2, dashboards, and templates.

Notes:

- Two pre-existing failures in `management_system.test_course_lesson`
  (PK-hardcoded URLs vs PostgreSQL sequence advancement) reproduce on
  unchanged `main` and were not introduced by Phase 1 work.
