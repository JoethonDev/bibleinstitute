# Phase 2 Standalone Plan: Content Versioning For Lessons And Quizzes

> **SUPERSEDED — DO NOT IMPLEMENT.** LessonVersion/QuizVersion/QuestionVersion were rejected. Use direct Lesson/Quiz/Question records under CourseOffering as defined in `docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md`.

Created: 2026-07-14 16:14:47 +03:00

Status: Ready for implementation

Parent roadmap: `06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md`

Depends on: Phase 0 and Phase 1 complete

Application area: `management_system`

## Purpose

Phase 2 makes lesson and quiz content version-safe while preserving current LMS behavior.

The current app mutates these records directly:

- `Lesson`
- `Quiz`
- `Question`

That makes historical reporting unsafe. If a teacher edits a quiz after students submit, old students may see the new question set instead of the one they actually answered.

Phase 2 adds version models that attach content to `CourseOffering`, without deleting or replacing the old models yet.

This phase should answer:

- Which lesson version belongs to this course offering?
- Which quiz version belongs to this course offering?
- Which question version belonged to that quiz version?
- Can old students still see old lesson/quiz/question content after newer edits?
- Can admin/teacher inspect version history?

This phase should not yet solve:

- Multiple quiz attempts.
- Special quiz openings.
- Retakes.
- Recalculation workflows.
- Transcript calculations.
- Attendance.
- Full publish/draft workflow beyond minimal version status.

Those belong to later phases.

## Cheap Model Instructions

Use these rules if implementation is delegated to a cheaper or less context-aware model.

1. Implement Phase 2 only.
2. Do not delete `Lesson`, `Quiz`, `Question`, `Submission`, or `Grade`.
3. Do not implement `QuizAttempt` or `AnswerSubmission`; that is Phase 3.
4. Add version models first.
5. Add schema migration, then data migration.
6. Preserve all Phase 0 and Phase 1 behavior.
7. Keep compatibility fallback to old models where version data is missing.
8. Do not change quiz form field names unless tests are updated in the same change.
9. Do not change lesson stream URL behavior unless a compatibility route remains.
10. Do not change upload/R2 behavior.
11. Do not redesign templates.
12. Do not add custom dashboards unless Django admin is not enough.
13. Do not edit published version rows in place once student data exists.
14. Make data migration idempotent with `get_or_create`.
15. Create review items instead of silently guessing ambiguous mappings.
16. Use existing `CourseOffering` and `Enrollment` from Phase 1.
17. Keep management users broad-access.
18. Add tests before changing access behavior.
19. Run focused tests after each major step.
20. All new imports must be added at the top of the file. Do not add imports inside functions, methods, conditionals, loops, or migration helper bodies. If a circular import appears, restructure the code instead of hiding the import locally.

Recommended cheap-model prompt:

```text
Implement Phase 2 only. Add LessonVersion, QuizVersion, and QuestionVersion models tied to CourseOffering. Preserve existing Lesson, Quiz, Question, Submission, and Grade models. Add schema and idempotent data migrations from existing Lesson/Quiz/Question rows into version rows using Phase 1 CourseOffering mappings. Update course detail, lesson display/stream, and quiz display to prefer version rows with fallback to old rows if missing. Do not implement QuizAttempt, AnswerSubmission, retakes, special openings, recalculation, upload changes, or UI redesign. Add every new import at the top of its file only; never add local imports inside functions or methods. Add tests that old version content remains visible after source content changes.
```

## Scope Boundary

In scope:

- `LessonVersion` model.
- `QuizVersion` model.
- `QuestionVersion` model.
- Admin registration for version models.
- Schema migration.
- Data migration from existing `Lesson`, `Quiz`, and `Question`.
- Minimal helper functions for resolving content versions.
- Course detail page can list versioned lessons/quizzes for the selected/accessed offering.
- Lesson display/stream can load from `LessonVersion`.
- Quiz display can load from `QuizVersion` and `QuestionVersion`.
- Compatibility fallback when version rows are missing.
- Tests for historical immutability and fallback.

Out of scope:

- `QuizAttempt`.
- `AnswerSubmission`.
- Special quiz openings.
- Retake policy changes.
- Grade recalculation.
- Full publish workflow UI.
- Upload Content refactor.
- R2 path changes.
- Lesson progress.
- Discussions.

## Strict Import Rule

All implementation tasks in this plan must follow this rule:

- Add every new import at the top of the file.
- Do not add imports inside functions.
- Do not add imports inside methods.
- Do not add imports inside conditionals.
- Do not add imports inside loops.
- Do not add imports inside migration helper functions.
- If moving an import to the top creates a circular import, move shared logic into a neutral module.
- Do not use local imports as a shortcut.

This rule applies to models, views, helpers, admin files, tests, migrations, and follow-up code.

## Current Code Areas To Inspect

Before implementation, inspect:

- `management_system/models.py`
- `management_system/views.py`
- `management_system/admin.py`
- `management_system/urls.py`
- `management_system/utils/helpers.py`
- `management_system/test_phase0_stabilization.py`
- `management_system/test_phase1_academic_structure.py`
- `management_system/test_course_lesson.py`
- `management_system/templates/course_detail.html`
- `management_system/templates/lesson_stream.html`
- `management_system/templates/display_quiz.html`
- `management_system/templates/questions_rendering.html`

Do not assume behavior. Read the current flow first.

## Data Model

### LessonVersion

Purpose:

- Represents lesson content attached to a `CourseOffering`.
- Keeps historical lesson content stable for old cohorts.

Recommended fields:

```python
class LessonVersion(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"

    course_offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, related_name="lesson_versions")
    source_lesson = models.ForeignKey(Lesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="versions")
    title = models.CharField(max_length=255)
    links = models.TextField()
    version_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="created_lesson_versions")
    published_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by")
    migration_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- Unique `(course_offering, source_lesson, version_number)` when `source_lesson` is not null.
- Unique `(course_offering, title, version_number)` if needed for generated/manual rows.
- `version_number >= 1`.

Minimum behavior:

- Migrated rows use `version_number=1`.
- Migrated rows use `status="published"`.
- `links` is copied from `Lesson.links`.

### QuizVersion

Purpose:

- Represents quiz metadata attached to a `CourseOffering`.
- Old submissions/grades still point to old `Quiz` during Phase 2, but display should prefer the version row.

Recommended fields:

```python
class QuizVersion(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"

    course_offering = models.ForeignKey(CourseOffering, on_delete=models.PROTECT, related_name="quiz_versions")
    source_quiz = models.ForeignKey(Quiz, on_delete=models.SET_NULL, null=True, blank=True, related_name="versions")
    name = models.CharField(max_length=64)
    opening_date = models.DateTimeField()
    closing_date = models.DateTimeField()
    total_grade = models.PositiveSmallIntegerField(default=50)
    version_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PUBLISHED)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="created_quiz_versions")
    published_at = models.DateTimeField(null=True, blank=True)
    supersedes = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by")
    migration_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- Unique `(course_offering, source_quiz, version_number)` when `source_quiz` is not null.
- `version_number >= 1`.
- `closing_date > opening_date`.

Minimum behavior:

- Migrated rows use `version_number=1`.
- Migrated rows use `status="published"`.
- Dates and grade copy from `Quiz`.

### QuestionVersion

Purpose:

- Immutable question copy attached to `QuizVersion`.
- Prevents old quiz displays from changing when `Question` is later edited.

Recommended fields:

```python
class QuestionVersion(models.Model):
    quiz_version = models.ForeignKey(QuizVersion, on_delete=models.CASCADE, related_name="question_versions")
    source_question = models.ForeignKey(Question, on_delete=models.SET_NULL, null=True, blank=True, related_name="versions")
    title = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=255, null=True, blank=True)
    question_type = models.CharField(max_length=512, choices=Question.QUESTION_TYPES)
    choices = models.TextField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)
    grade = models.PositiveSmallIntegerField(default=1)
    auto_grade = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
```

Constraints:

- Unique `(quiz_version, source_question)` when `source_question` is not null.
- Unique `(quiz_version, display_order)`.

Minimum behavior:

- Copy every existing `Question` into a `QuestionVersion`.
- Preserve order using current question queryset order by primary key unless an explicit order field already exists.
- Copy `config`, `choices`, `correct_answer`, `grade`, and `auto_grade`.

## Migration Strategy

### Migration 1: Schema

Tasks:

1. Add `LessonVersion`.
2. Add `QuizVersion`.
3. Add `QuestionVersion`.
4. Add constraints and indexes.
5. Register models in admin.

Acceptance:

- Migration only adds version models and related constraints.
- No old model fields are removed.

### Migration 2: Data

Tasks:

1. For each `Lesson`, determine academic year from `Lesson.created_date`.
2. Find matching `CourseOffering` by `lesson.course` and academic year.
3. Create `LessonVersion`.
4. For each `Quiz`, determine academic year from `Quiz.opening_date`.
5. Find matching `CourseOffering` by `quiz.course` and academic year.
6. Create `QuizVersion`.
7. Copy each `Question` into `QuestionVersion`.
8. If grades for one quiz span multiple academic years, create one `QuizVersion` per academic year needed and review item.
9. Create `MigrationReviewItem` for ambiguous rows.

Data migration must:

- Use `get_or_create`.
- Never delete old data.
- Never mutate old lesson/quiz/question content.
- Be safe to rerun.

## Detailed Implementation Tasks

### Task 1: Add Models

Files touched:

- `management_system/models.py`
- New migration file

Steps:

1. Add `LessonVersion`, `QuizVersion`, `QuestionVersion`.
2. Keep imports at top.
3. Add `__str__`.
4. Add `Meta.ordering`.
5. Add constraints.
6. Reuse existing constants where possible.

Acceptance:

- `python manage.py check` passes except unrelated warnings.
- `makemigrations` does not alter unrelated models.

### Task 2: Admin Registration

Files touched:

- `management_system/admin.py`

Minimum admin:

- `LessonVersionAdmin`
- `QuizVersionAdmin`
- `QuestionVersionAdmin`

Useful fields:

- LessonVersion list: `title`, `course_offering`, `version_number`, `status`, `source_lesson`, `published_at`
- QuizVersion list: `name`, `course_offering`, `version_number`, `status`, `source_quiz`, `opening_date`, `closing_date`
- QuestionVersion list: `title`, `quiz_version`, `question_type`, `grade`, `display_order`, `source_question`

Acceptance:

- Admin can inspect versions.
- Admin can filter by status/course offering.

### Task 3: Version Resolution Helpers

Files touched:

- `management_system/utils/helpers.py`

Recommended helpers:

```python
def get_course_offering_for_user_course(user, course):
    ...

def get_lesson_versions_for_user_course(user, course):
    ...

def get_quiz_versions_for_user_course(user, course):
    ...

def get_lesson_version_for_user(user, lesson_or_version_id):
    ...

def get_quiz_version_for_user(user, quiz_or_version_id):
    ...
```

Keep it minimal. Do not build a generic content resolver framework.

Compatibility rule:

- Prefer version rows.
- If no version rows exist, fall back to existing `Lesson` / `Quiz` rows.
- Management users can see all published/draft/archived versions.
- Students see published versions only.

Acceptance:

- Existing pages render when version rows exist.
- Existing pages still render if version rows are missing.

### Task 4: Data Migration For Lesson Versions

Steps:

1. For each `Lesson`, find academic year from `created_date`.
2. Find `CourseOffering(course=lesson.course, cohort.academic_year=year)`.
3. If missing, create `MigrationReviewItem` and skip or use current offering fallback.
4. Create `LessonVersion`.
5. Copy `Lesson.name` to `title`.
6. Copy `Lesson.links` to `links`.

Acceptance:

- Every mappable lesson has a version row.
- No duplicates after migration rerun.

### Task 5: Data Migration For Quiz And Question Versions

Steps:

1. For each `Quiz`, find academic year from `opening_date`.
2. Find `CourseOffering(course=quiz.course, cohort.academic_year=year)`.
3. Create `QuizVersion`.
4. Copy all related `Question` rows into `QuestionVersion`.
5. Use `display_order` from ordered queryset.

Important edge:

- If one `Quiz` has grades across multiple academic years, create versions for those years too.
- Record `quiz_reused_across_years` or equivalent review item.

Acceptance:

- Every mappable quiz has a version row.
- Every question of that quiz has a question version row.
- Historical question copies do not change when source question changes.

### Task 6: Update Course Detail

Files touched:

- `management_system/views.py`
- `management_system/templates/course_detail.html` only if needed

Target:

- Students see lesson/quiz versions for the course offering they can access.
- Management sees versions, ideally scoped by selected/current offering.
- Keep old context shape if possible:
  - `lessons`
  - `quizzes`

Lazy compatibility:

- Version objects can expose properties/methods matching old template usage.
- Or build lightweight view data objects.
- Avoid template rewrite unless necessary.

Acceptance:

- Course detail page works for old data and versioned data.
- Existing lesson visibility rules do not leak unavailable old content.

### Task 7: Update Lesson Display / Stream

Files touched:

- `management_system/views.py`
- `management_system/urls.py` only if needed
- `lesson_stream.html` only if needed

Target:

- Lesson detail loads `LessonVersion.links` when version id/content is selected.
- Direct old lesson URLs remain compatible.
- Stream URL can keep old route if it resolves through source lesson fallback.

Minimum acceptable approach:

- Keep existing `lesson-details` route.
- In the view, if a `LessonVersion` exists for the user/course/source lesson, render that version's links.
- Otherwise fall back to old `Lesson.links`.

Acceptance:

- Old lesson URL still works.
- Edited source `Lesson.links` does not change old version render when version exists.

### Task 8: Update Quiz Display

Files touched:

- `management_system/views.py`
- `display_quiz.html` / `questions_rendering.html` only if needed

Target:

- Quiz display prefers `QuizVersion`.
- Questions render from `QuestionVersion`.
- Existing `Grade` and `Submission` remain old-model based until Phase 3.

Important:

- Do not change POST submission storage to version rows yet.
- POST may still map submitted `QuestionVersion.source_question_id` back to `Question`.
- If no source question exists, deny submission with a clear error.

Acceptance:

- Existing quiz-taking tests still pass.
- Existing submissions still work.
- Old version question text remains stable if source `Question.title` changes.

### Task 9: Version History / Comparison

Keep minimal.

Minimum:

- Django admin can show old and new versions.

Optional if simple:

- Add a read-only admin/helper page comparing source row to latest version.

Do not build a custom visual diff unless explicitly requested.

Acceptance:

- Admin can inspect version rows and source links.

### Task 10: Tests

Add focused tests:

1. LessonVersion migration copies lesson title/links.
2. QuizVersion migration copies quiz metadata.
3. QuestionVersion migration copies question fields/config.
4. Course detail lists version rows when present.
5. Course detail falls back to old rows when versions missing.
6. Lesson render uses version links, not changed source lesson links.
7. Quiz render uses question version title, not changed source question title.
8. Existing quiz submission still creates `Submission` and `Grade`.
9. Management can see version rows.
10. Student cannot see draft/archived versions.
11. Re-running migration does not duplicate versions.

Preserve:

- Phase 0 tests.
- Phase 1 tests.
- Existing course/lesson tests.

## Acceptance Criteria

Phase 2 is complete when:

1. `LessonVersion`, `QuizVersion`, and `QuestionVersion` exist.
2. Admin can inspect version rows.
3. Data migration creates version rows from existing content.
4. Version migration is idempotent.
5. Course detail uses version rows when available.
6. Lesson display uses version rows when available.
7. Quiz display uses version/question rows when available.
8. Existing fallback behavior works when version rows are missing.
9. Editing old source `Lesson` does not mutate rendered old version content.
10. Editing old source `Question` does not mutate rendered old version quiz display.
11. Existing `Submission` and `Grade` behavior remains intact.
12. Management users retain broad inspection access.
13. Students see published versions only.
14. Tests cover migration, fallback, historical immutability, and access.
15. Full available local test suite passes.

## Validation Checklist

Run before marking complete:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test management_system
```

If PostgreSQL test startup times out locally, run the in-memory SQLite validation used in prior reviews and clearly report PostgreSQL was not completed.

Manual validation:

1. Open a course as a student.
2. Confirm lessons/quizzes appear from versions.
3. Edit source `Lesson.links`.
4. Confirm old student view still shows old version links.
5. Edit source `Question.title`.
6. Confirm old quiz display still shows question version title.
7. Confirm quiz submission still works.
8. Confirm admin can inspect LessonVersion/QuizVersion/QuestionVersion.

## Edge Cases

- Lesson has no matching CourseOffering.
- Quiz has no matching CourseOffering.
- CourseOffering exists but is archived.
- Lesson created before student's joined date.
- Quiz reused across academic years.
- Grade submitted in a different academic year than quiz opening date.
- Question has structured `config`.
- Question has null `choices`.
- Question has manual grading.
- Source lesson is deleted later.
- Source quiz is deleted later.
- Source question is deleted later.
- Version row exists but source row is null.
- Version rows exist for draft and published statuses.

## Risk Controls

High risk: breaking quiz submissions.

Control:

- Keep `Submission` and `Grade` old-model based in Phase 2.
- Only map version question back to `source_question` for POST.

High risk: content leak across cohorts.

Control:

- Resolve versions through `CourseOffering`.
- Students see published versions only.

High risk: mutating history.

Control:

- Copy fields into version rows.
- Do not render mutable source fields when a version exists.

High risk: overbuilding.

Control:

- Use Django admin for version inspection.
- Skip custom comparison page unless tiny and explicitly needed.

## Implementation Order

1. Add tests for model constraints and simple helper behavior.
2. Add version models.
3. Add schema migration.
4. Register admin.
5. Add data migration.
6. Add version resolution helpers.
7. Update course detail to prefer versions.
8. Update lesson display to prefer version links.
9. Update quiz display to prefer quiz/question versions.
10. Add historical immutability tests.
11. Add fallback tests.
12. Run full tests.
13. Update parent roadmap Phase 2 status and completion notes.

Before each implementation step, check that imports are at the top of edited files only.

## Do Not Do In Phase 2

- Do not add `QuizAttempt`.
- Do not add `AnswerSubmission`.
- Do not add special quiz openings.
- Do not add retake behavior.
- Do not add recalculation.
- Do not change upload/R2 paths.
- Do not redesign UI.
- Do not remove old models.
- Do not remove old URLs.
- Do not add local imports inside functions or methods.

## Final Completion Note Template

When Phase 2 is complete, add this to the parent roadmap:

```md
Completion notes:

- Added LessonVersion, QuizVersion, and QuestionVersion models.
- Migrated existing Lesson/Quiz/Question rows into versioned content attached to CourseOffering.
- Updated course detail, lesson display, and quiz display to prefer version rows with old-model fallback.
- Preserved existing Submission and Grade behavior for Phase 3.
- Added tests for migration idempotency, fallback behavior, and historical content immutability.
```
