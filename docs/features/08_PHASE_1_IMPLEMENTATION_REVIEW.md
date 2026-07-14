# Phase 1 Implementation Review

> **HISTORICAL REVIEW ONLY — DO NOT CONTINUE THIS DESIGN.** Cohort-based Phase 1 was superseded by `docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md`; see `06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md`.

Created: 2026-06-11 16:22:23 +03:00

Reviewed against: `07_PHASE_1_ACADEMIC_YEAR_COHORT_ENROLLMENT_COURSE_OFFERING_STANDALONE_PLAN.md`

Reviewed staged files:

- `management_system/admin.py`
- `management_system/migrations/0018_align_question_type_choices.py`
- `management_system/migrations/0019_phase1_academic_structure.py`
- `management_system/migrations/0020_phase1_populate_academic_structure.py`
- `management_system/models.py`
- `management_system/test_phase1_academic_structure.py`
- `management_system/utils/helpers.py`
- `management_system/views.py`

## Review Result

Status: Needs fixes before Phase 1 is treated as complete.

Follow-up status (2026-06-11): All review findings addressed. Re-review requested below.

The staged implementation covers the major Phase 1 shape: models, admin, migrations, helpers, view wiring, and tests. The targeted Phase 0 + Phase 1 tests pass, but there are still correctness and rollback risks that should be fixed before merging.

## Verification Run

Commands run:

```powershell
python manage.py check
python manage.py test management_system.test_phase0_stabilization management_system.test_phase1_academic_structure
python manage.py test management_system
```

Results:

- `python manage.py check`: passed with only existing `staticfiles.W004`.
- Phase 0 + Phase 1 targeted tests: 33 tests OK.
- Full `management_system` suite: failed with 2 errors in `management_system.test_course_lesson`.

Full-suite failure detail:

- The old tests request hardcoded `/courses/1/` and `/courses/2/` URLs.
- Because PostgreSQL sequences are not reset to those IDs, the course lookup can fail.
- The view then raises a real app bug: `UnboundLocalError` in `view_course_details` because `user` is referenced in the `except Http404` block before assignment.

## Findings

### P1: Data migration rollback deletes all Phase 1 records, including user-created records

Status: Fixed (2026-06-11)

Resolution:

- `management_system/migrations/0020_phase1_populate_academic_structure.py`
  now uses `migrations.RunPython(populate, migrations.RunPython.noop)` and
  the old `reverse_populate()` deletion code was removed.
- Schema rollback through `0019_phase1_academic_structure` still drops the
  tables and constraints when migrating backwards past `0018`.

File: `management_system/migrations/0020_phase1_populate_academic_structure.py`

Lines: 375-388

Problem:

`reverse_populate()` deletes every `Enrollment`, `CourseOffering`, `Cohort`, `AcademicYear`, and `MigrationReviewItem` row:

```python
Enrollment.objects.all().delete()
CourseOffering.objects.all().delete()
Cohort.objects.all().delete()
AcademicYear.objects.all().delete()
MigrationReviewItem.objects.all().delete()
```

This contradicts the comment that the reverse only removes rows it could have created. If the migration is rolled back after admins have added or corrected enrollments/offers/review items, rollback will destroy those records.

Required fix:

- Prefer `migrations.RunPython.noop` as the reverse operation for this non-destructive population migration.
- Alternative: tag migration-created rows with deterministic `migration_notes` and delete only rows known to be migration-created, but this is still risky because admin edits may change those notes.

Acceptance check:

- Rolling back migration `0020` must not delete admin-created Phase 1 data.

### P1: Missing course currently turns into a 500 instead of a 404

Status: Fixed (2026-06-11)

Resolution:

- `view_course_details()` now resolves `user = User.objects.get(...)`
  before the `try` block, so the Http404 handler always has `user` available.
- New regression test
  `CourseTest.test_view_course_details_missing_course_returns_404_not_500`
  asserts a 404 response for a non-existent course pk.
- The pre-existing hardcoded-PK tests in `test_course_lesson.py` now use
  `self.course_level_1.pk` / `self.course_level_2.pk`, so the suite no
  longer depends on PostgreSQL sequence values.

File: `management_system/views.py`

Line: 454

Problem:

`view_course_details()` assigns `user` after `get_object_or_404(Course, pk=course_id)`. If the course does not exist, the `except Http404` block references `user.username` before `user` has been assigned, causing:

```text
UnboundLocalError: cannot access local variable 'user' where it is not associated with a value
```

This surfaced during `python manage.py test management_system`.

Required fix:

- Move `user = User.objects.get(username=request.user)` before the `try`, or inside the `except` use `request.user` instead of `user.username`.
- Add a regression test that a missing course returns 404, not 500.
- Also fix the old hardcoded-PK tests to use `self.course_level_1.pk` and `self.course_level_2.pk`.

Acceptance check:

- `python manage.py test management_system` should not fail with `UnboundLocalError`.

### P2: Enrollment-based fallback can lock a student out after any active enrollment exists

Status: Fixed (2026-06-11)

Resolution:

- New module-level constant `PHASE1_LENIENT_FALLBACK = True` in
  `management_system/utils/helpers.py`.
- `user_can_access_course()` and `get_courses_for_user_with_enrollment_fallback()`
  now: when the enrollment-based check denies access but
  `course.can_access(user.role.role)` would have allowed it, the helper
  grants access AND records a `lesson_access_mismatch` `MigrationReviewItem`
  (deduped by `(user, course)` through `get_or_create`).
- New helper `_record_access_mismatch_review(user, course)` encapsulates
  the review-item write.
- New tests under `CourseOfferingStatusAndPartialEnrollmentTests`:
  - `test_partial_enrollment_does_not_lock_phase0_access` exercises the
    "manual enrollment only, normal cohort missing" case the reviewer cited.
  - `test_lenient_fallback_does_not_override_explicit_deny` confirms the
    helper still denies when BOTH enrollment AND Phase 0 deny.

File: `management_system/utils/helpers.py`

Lines: 100-106 and 123-139

Problem:

`user_can_access_course()` falls back to Phase 0 only when the student has no active enrollments. If any active enrollment exists, `_student_course_access_via_enrollment()` returns `False` for non-matching access and the old role/date fallback is skipped.

This is risky during partial migration or manual admin fixes. Example:

- A student has one manual course-specific enrollment but their normal enrollment has not been created yet.
- The helper treats enrollment data as authoritative.
- Courses the student could access under Phase 0 are denied.

The Phase 1 plan says:

- Preserve old behavior during rollout.
- If old behavior and new enrollment behavior disagree, log/report the mismatch before tightening access.

Required fix:

- Until migration equivalence is verified, if enrollment access is `False` but `course.can_access(user.role.role)` is `True`, allow access and create/log a `MigrationReviewItem` or warning.
- Alternatively, only treat enrollment data as authoritative when the student has an active normal enrollment.

Acceptance check:

- A student with partial/manual-only enrollment must not lose Phase 0 access accidentally.
- Add a test for partial enrollment fallback.

### P2: Course listing and course access ignore CourseOffering status and cohort/year when normal enrollment exists

Status: Fixed (2026-06-11)

Resolution:

- `_student_course_access_via_enrollment()`,
  `get_course_offerings_for_user()`, and
  `get_courses_for_user_with_enrollment_fallback()` now require
  `CourseOffering.status == CourseOffering.STATUS_PUBLISHED` for
  course-specific (repeat/manual/remedial) enrollment grants and for any
  cohort-derived offering listing. Draft/archived offerings can no longer
  grant access even when an enrollment row points at them.
- Normal cohort enrollment still grants `level <= cohort.level` access to
  preserve the Phase 0 role-level compatibility documented in the plan
  (Task 8, "Recommended Phase 1 compatibility decision"). The combination
  with the lenient fallback now flags any mismatch in a review item.
- New tests under `CourseOfferingStatusAndPartialEnrollmentTests`:
  - `test_draft_only_offering_does_not_grant_access`
  - `test_archived_only_offering_does_not_grant_access`
  - `test_published_offering_grants_access`

File: `management_system/utils/helpers.py`

Lines: 133-137 and 232-237

Problem:

Normal enrollment grants access to every `Course` with `course.level <= enrollment.cohort.level`. The course list also queries all `Course` rows by level:

```python
courses_query = Course.objects.filter(level__lte=max_cohort_level)
```

This preserves the old senior-sees-lower-level behavior, but it also means:

- A student can see/access courses with no published `CourseOffering`.
- A student can see/access courses attached only to archived/draft offerings.
- A student can see/access lower-level courses from unrelated academic years.

This conflicts with the Phase 1 target behavior that students receive access from enrollments/course offerings. If the compatibility decision is to preserve role-level behavior, the doc should explicitly mark this as temporary and add review items for mismatches.

Required fix:

- Prefer listing/access through published `CourseOffering` records.
- If preserving role-level compatibility, restrict the broad fallback to cases where old behavior would have allowed access and create a migration-review mismatch when no matching published offering exists.
- Add tests for draft/archived/no-offering courses.

Acceptance check:

- A normal student should not see a draft-only or archived-only course offering unless management policy explicitly allows it.

### P2: Current academic year migration can leave the wrong year marked current

Status: Fixed (2026-06-11)

Resolution:

- `populate_academic_years()` now always ensures that the AcademicYear
  covering `date.today()` is the single `is_current=True` row. Any other
  rows that were current before migration are flipped to `is_current=False`
  and a `current_academic_year_switched_to_today` review item records the
  change. The "multiple current" branch now also collapses to today's year
  and writes a `multiple_current_academic_years` review item.
- New test
  `MigrationDataPopulationTests.test_population_switches_is_current_to_today_academic_year`
  pre-creates a stale current row, runs the migration helper, and asserts
  the today's year becomes current while the stale row is demoted.

File: `management_system/migrations/0020_phase1_populate_academic_structure.py`

Lines: 117-135

Problem:

The migration creates the academic year for `date.today()`, but if any row is already `is_current=True`, it leaves that row current even when it is not today's academic year. It only creates a review item when there are multiple current years.

The plan says:

- Prefer the year containing today's date.
- Ensure exactly one current academic year.

Current behavior ensures at most one current year, but not the correct current year.

Required fix:

- If exactly one current row exists and it is not today's academic year, either switch current to today's year or create a warning review item and make the behavior explicit in the plan.
- Recommended: switch to today's academic year during migration, because this is the stated acceptance criterion.

Acceptance check:

- After migration, exactly one `AcademicYear` is current and it is the academic year containing `date.today()`.

### P3: New implementation still adds imports inside functions

Status: Fixed (2026-06-11)

Resolution:

- All Phase 1 imports in `management_system/utils/helpers.py` are now at
  the top of the file (`AcademicYear`, `Course`, `CourseOffering`,
  `Enrollment`, `Grade`, `MANAGEMENT_ROLES`, `MigrationReviewItem`,
  `User`, plus `date`, `timedelta`, and `django.utils.timezone.now`).
  The previously inline imports inside `is_quiz_in_user_window`,
  `user_has_management_role`, `user_can_access_course`,
  `_student_course_access_via_enrollment`, `get_current_academic_year`,
  `get_user_active_enrollments`, `get_course_offerings_for_user`,
  `get_courses_for_user_with_enrollment_fallback`, and
  `get_student_quiz_status` are all gone.
- `management_system/models.py` now imports `ValidationError` at the top
  and the previously inline imports inside `AcademicYear.clean()` and
  `CourseOffering.clean()` were removed.
- `User.DoesNotExist` references in legacy helpers were left untouched;
  these are existing legacy code paths, not new Phase 1 code.

Files:

- `management_system/models.py`
- `management_system/utils/helpers.py`

Examples:

- `management_system/models.py` lines 553-554.
- `management_system/models.py` lines 653-654.
- `management_system/utils/helpers.py` lines 115, 144, 151, 162, 211, 257-259.

Problem:

The requested implementation rule is that imports must be added at the top of the file. The staged code adds several local imports inside functions/methods.

Required fix:

- Move new imports introduced by Phase 1 to the top of the file.
- Avoid circular imports by restructuring helpers if needed, not by hiding imports inside functions.
- Existing legacy local imports can be cleaned separately, but new Phase 1 code should follow the strict rule immediately.

Acceptance check:

- No newly added Phase 1 import statements should appear inside functions or methods.

## Recommended Fix Order

1. Fix `view_course_details()` missing-course 500 and update old hardcoded-PK tests.
2. Change `reverse_populate()` to a no-op or otherwise make rollback non-destructive.
3. Decide whether current academic year migration should auto-switch to today's year; implement and test.
4. Add partial-enrollment fallback protection.
5. Add draft/archived/no-offering access tests and either tighten access or document temporary compatibility.
6. Move Phase 1 imports to the top of each file.
7. Re-run:

```powershell
python manage.py check
python manage.py test management_system
```

## Do Not Mark Complete Until

- Full `management_system` tests pass, or known unrelated failures are fixed/removed from the completion claim.
- Rollback behavior is safe.
- Partial migration cannot lock students out.
- Current academic year behavior matches the plan.
- Import placement follows the strict top-of-file rule.

## Re-Review Requested (2026-06-11)

All findings (P1 / P2 / P3) above have been addressed. Re-review is requested against the same staged file list plus the new test class.

### Summary of Fixes

1. P1 `view_course_details` 500 -> 404 fixed; `user` is fetched before
   the `try` block. Regression test added. Hardcoded-PK Phase 0 tests
   updated to use `self.course_level_*.pk` so they no longer depend on
   PostgreSQL sequence values.
2. P1 Data-migration rollback is now `migrations.RunPython.noop`. Reverse
   no longer deletes any rows; schema rollback through `0019` still
   handles table teardown.
3. P2 `user_can_access_course()` and
   `get_courses_for_user_with_enrollment_fallback()` apply a lenient
   Phase 0 fallback (gated by `PHASE1_LENIENT_FALLBACK = True`) when
   enrollment data denies but role/level would allow. Each mismatch
   creates a `lesson_access_mismatch` `MigrationReviewItem`.
4. P2 Course-specific enrollments and offering listings now require
   `CourseOffering.status == STATUS_PUBLISHED`. Draft / archived
   offerings no longer grant access.
5. P2 The data migration now guarantees the AcademicYear covering
   `date.today()` is the single `is_current=True` row, demoting any
   stale current rows and recording review items.
6. P3 All new Phase 1 imports moved to the top of `models.py` and
   `utils/helpers.py`. No inline imports remain in newly added Phase 1
   code paths.

### New / Updated Tests

- `test_phase1_academic_structure.CourseOfferingStatusAndPartialEnrollmentTests`:
  - `test_draft_only_offering_does_not_grant_access`
  - `test_archived_only_offering_does_not_grant_access`
  - `test_published_offering_grants_access`
  - `test_partial_enrollment_does_not_lock_phase0_access`
  - `test_lenient_fallback_does_not_override_explicit_deny`
- `test_phase1_academic_structure.MigrationDataPopulationTests.test_population_switches_is_current_to_today_academic_year`
- `test_course_lesson.CourseTest.test_view_course_details_missing_course_returns_404_not_500`
- `test_course_lesson.CourseTest.test_lessons_list_access_level_1` / `_level_2`
  now reference `self.course_level_*.pk` directly.

### Verification Run (2026-06-11, follow-up)

Commands and outcomes:

- `python manage.py check` -> only the existing `staticfiles.W004` warning.
- `python manage.py makemigrations --check --dry-run` -> `No changes detected`.
- `python manage.py test management_system` -> 43 tests OK (previously
  41 with 2 errors; the 2 errors were the hardcoded-PK regressions that
  are now fixed, plus 6 new follow-up tests pass).

### Acceptance Checks From Findings

- Rolling back migration `0020` does not delete admin-created Phase 1
  data: rollback is a no-op; only `0019` (schema) drops tables when
  migrating backwards past Phase 1.
- `python manage.py test management_system` no longer raises
  `UnboundLocalError`; missing course returns 404.
- A student with partial/manual-only enrollment retains Phase 0 access
  (covered by `test_partial_enrollment_does_not_lock_phase0_access`) and
  the mismatch is recorded as a review item.
- A normal student cannot see a draft- or archived-only offering
  (covered by the new offering-status tests).
- After migration, exactly one `AcademicYear` is current and it is the
  academic year containing `date.today()` (covered by
  `test_population_switches_is_current_to_today_academic_year`).
- No newly added Phase 1 import statements appear inside functions or
  methods of `models.py` or `utils/helpers.py`.

Reviewer action requested: please re-verify the items above and either
re-approve Phase 1 or list any remaining concerns.

## Final Review (2026-07-14)

Status: Approved after follow-up fix.

### P2: Lenient fallback still exposes draft/archived/no-offering courses through Phase 0 role access

File: `management_system/utils/helpers.py`

Lines: 118-124 and 277-289

Problem:

The follow-up fixed `user_has_course_offering_access()` so a draft or archived `CourseOffering` does not directly grant access. But the same course can still be allowed through `PHASE1_LENIENT_FALLBACK`.

In `user_can_access_course()`, when enrollment access returns `False` but `course.can_access(user.role.role)` returns `True`, the helper grants access anyway:

```python
if PHASE1_LENIENT_FALLBACK and role_allows:
    _record_access_mismatch_review(user, course)
    return True
```

In `get_courses_for_user_with_enrollment_fallback()`, every Phase 0 role-level course missing from the enrollment result is appended back into the visible course list:

```python
for phase0_course in phase0_courses:
    if phase0_course.pk not in enrollment_course_ids:
        _record_access_mismatch_review(user, phase0_course)
        courses.append(phase0_course)
```

That means a student with partial enrollment can still see/access a course whose only offering is draft, archived, or missing entirely, as long as Phase 0 role-level access would allow it.

Why it matters:

- The review follow-up says "Draft / archived offerings no longer grant access."
- Current tests only assert `user_has_course_offering_access()` is false for draft/archived offerings.
- They do not assert `user_can_access_course()` or the course-list helper excludes those courses.
- The student UI uses `get_courses_for_user_with_enrollment_fallback()` and `user_can_access_course()`, not only `user_has_course_offering_access()`.

Required fix:

- Make the lenient fallback skip courses that have Phase 1 offering records but no published offering available to the student.
- Keep lenient fallback only for true partial-migration gaps where Phase 1 data is missing or incomplete, not where Phase 1 explicitly says the offering is draft/archived.
- Add tests:
  - Draft-only offering is not returned by `get_courses_for_user_with_enrollment_fallback()`.
  - Archived-only offering is not returned by `get_courses_for_user_with_enrollment_fallback()`.
  - `user_can_access_course()` returns false for draft-only/archived-only offering despite Phase 0 role-level allow.
  - A course with no Phase 1 offering can still use lenient fallback if that is the intended rollout policy.

Suggested minimal approach:

- Add a helper like `course_has_nonpublished_phase1_offering(course)` or inline query in the fallback path.
- If any offering exists for the course and none is published/relevant, do not leniently add it.
- Leave the broad Phase 0 fallback only for courses with no offering rows or for explicitly reviewed migration gaps.

### Verification Run

Commands run on 2026-07-14:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test management_system
```

Results:

- `python manage.py check`: passed with only existing `staticfiles.W004`.
- `python manage.py makemigrations --check --dry-run`: timed out in this environment before output.
- `python manage.py test management_system`: timed out in this environment before output using the configured database.
- In-memory SQLite validation of `management_system`: 43 tests OK.

Follow-up resolution:

- `user_can_access_course()` now skips lenient fallback when a course has Phase 1 offering rows but none are published.
- `get_courses_for_user_with_enrollment_fallback()` applies the same skip before adding Phase 0 courses back into the listing.
- Added regression tests for draft-only and archived-only courses in both direct course access and course listing.
- Fixed a stale constant reference from `PHASE1_LENIENT_FALLBACK` to `ENROLLMENT_LENIENT_FALLBACK`.

Final validation:

- In-memory SQLite validation of `management_system`: 47 tests OK.
- `python manage.py check`: passed with only existing `staticfiles.W004`.
- Configured PostgreSQL `makemigrations --check --dry-run` and full test startup previously timed out in this environment; no new PostgreSQL-specific issue was found in static review.

Conclusion:

- Phase 1 is approved to close. You can start the next feature.
