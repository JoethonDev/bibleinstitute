# Phase 2 Implementation Review

> **HISTORICAL REVIEW ONLY — DO NOT CONTINUE THIS DESIGN.** The content-version model was superseded by `docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md`; see `06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md`.

Review target: `09_PHASE_2_CONTENT_VERSIONING_STANDALONE_PLAN.md`

Review date: 2026-07-14

Status: **Changes requested**

## Follow-Up Review: 2026-07-14

The main fixes are moving in the right direction, but Phase 2 still should not be marked complete.

### Remaining P1: `stream_lesson` can still resolve the wrong offering

File:

- `management_system/views.py`

Problem:

`stream_lesson(lesson_id, file_index)` still has no `course_id` or offering identifier in the route. It tries to infer the course from:

```python
LessonVersion.objects.filter(Q(source_lesson_id=lesson_id) | Q(pk=lesson_id)).first()
```

When the same source lesson has versions in multiple offerings, `.first()` can choose another cohort/year's `LessonVersion`. The later helper call is scoped, but it is scoped using the course inferred from that arbitrary version.

Required fix:

- Make the stream URL carry enough context to resolve the same version selected in `view_lesson_details`.
- Minimal option: add `course_id` to the stream route and call `get_lesson_version_for_user(user, course, lesson_id)`.
- Better if routes can tolerate it: pass the `LessonVersion.pk` from the detail page and validate it belongs to the user's resolved offering.

Validation:

- Add a test that creates two `LessonVersion` rows for the same `source_lesson`, with different links, then verifies each cohort streams its own link.
- Add a direct URL test using the other cohort's `LessonVersion.pk`; it must not stream the other cohort's file.

### Remaining P2: Current-year fallback still silently picks an arbitrary offering

File:

- `management_system/utils/helpers.py`

Problem:

`get_course_offering_for_user_course()` now avoids `MultipleObjectsReturned`, but the docstring says it returns `None` when multiple fallback offerings exist. The code still uses `.first()` for current-year fallback:

```python
CourseOffering.objects.filter(course=course, cohort__academic_year=current_year).first()
```

This prevents a crash, but it can expose an arbitrary cohort's version content to a non-enrolled user or management user without a specific enrollment context.

Required fix:

- For the current-year fallback, count or slice the queryset and return an offering only when exactly one exists.
- If multiple current-year offerings exist, return `None` and let old-model fallback handle the page.

Validation:

- Update the multiple-offering test to assert version content is not arbitrarily selected when no enrollment identifies a single offering.

### Remaining P2: Tests do not prove wrong-offering direct access is denied

File:

- `management_system/test_phase2_content_versioning.py`

Problem:

The current tests use the source lesson/quiz IDs. They do not request the other cohort's `LessonVersion.pk` / `QuizVersion.pk`, so they do not prove direct wrong-offering version access is denied.

Required fix:

- Add direct-access tests using the wrong offering's version primary key.
- Include lesson detail, lesson stream, and quiz detail.

## Summary

The implementation adds the expected Phase 2 model, migration, admin, helper, view, and test surfaces. `python manage.py check` passes with only the pre-existing `staticfiles.W004`, and the isolated SQLite Phase 2 test suite passes 16/16.

Do **not** mark Phase 2 complete yet. The current implementation can resolve the wrong version for a student's cohort/year and can grade a versioned quiz against mutated source questions instead of the frozen version snapshot.

## Blocking Issues

### P1: Version detail helpers are not scoped to the student's CourseOffering

Files:

- `management_system/utils/helpers.py`
- `management_system/views.py`
- `management_system/templates/course_detail.html`

Problem:

`get_lesson_version_for_user(user, lesson_or_version_id)` and `get_quiz_version_for_user(user, quiz_or_version_id)` search globally by `source_lesson_id/source_quiz_id` or version primary key. They do not filter by the `CourseOffering` resolved for the current user and course.

`course_detail.html` links versioned rows using `lesson.id` and `quiz.id`, and the helper dicts set those IDs to `source_lesson_id` / `source_quiz_id` when a source row exists. If the same source lesson or quiz has versions in multiple cohorts or academic years, a student can be shown the wrong cohort/year's content. `stream_lesson` has the same issue because it re-resolves from the URL ID without course/offering context.

Required fix:

- Resolve lesson/quiz detail rows inside the student's selected `CourseOffering`.
- Prefer passing `course` or `course_id` into the detail helper so it can call `get_course_offering_for_user_course(user, course)` and filter by that offering.
- Use a stable URL identity for versioned rows. Either link by `LessonVersion.pk` / `QuizVersion.pk` and validate the version belongs to the user's offering, or keep source IDs but always filter by the user's offering before selecting the version.
- For unauthorized or wrong-offering version access, return 401/404 instead of falling back to another offering.

Acceptance criteria:

- Two cohorts in the same academic year can have different `LessonVersion` rows for the same `Lesson`; each enrolled student sees only their own cohort's lesson title and links.
- Two cohorts can have different `QuizVersion` rows for the same `Quiz`; each enrolled student sees only their own cohort's quiz metadata and questions.
- Direct URL access to another offering's version row is denied.
- `stream_lesson` streams the same version row shown on the lesson detail page.

Validation:

- Add tests for two cohorts, same course/source lesson, different version links.
- Add tests for two cohorts, same course/source quiz, different version questions.
- Add direct-access tests for wrong-offering lesson, stream, and quiz URLs.

### P1: Versioned quiz submissions are graded against current source Question rows

Files:

- `management_system/models.py`
- `management_system/views.py`
- `management_system/test_phase2_content_versioning.py`

Problem:

`QuestionVersion.serialize()` returns `source_question_id` when present, and `take_exam` creates `Submission(question_id=question_id, ...)`. `Submission.assign_grade()` then grades through `self.question.get_auto_grade(...)`, which reads the current mutable `Question` row.

That means a historical `QuestionVersion` may display frozen text/config/answer, but the submitted answer is graded using the edited source `Question`. This breaks the core Phase 2 immutability guarantee.

Required fix:

- When rendering versioned questions, keep enough metadata to identify the `QuestionVersion` submitted.
- On POST for a versioned quiz, grade against the matching `QuestionVersion` snapshot.
- Preserve existing `Submission`/`Grade` compatibility by still storing the source `Question` FK where available, but set the submission grade from the version snapshot instead of calling `assign_grade()` against the source question.
- If a `QuestionVersion` has no `source_question`, either reject it until the legacy submission model is extended, or implement an explicit compatible strategy. Do not silently treat `QuestionVersion.pk` as a `Question.pk`.

Acceptance criteria:

- If `Question.correct_answer`, `grade`, `auto_grade`, `choices`, or structured `config` changes after a `QuestionVersion` is published, students taking that version are graded according to the version snapshot.
- Existing submitted grades and submissions remain unchanged.
- Duplicate/repeated submission protection still works.
- Unanswered questions are created for the versioned question set, not blindly for the current source quiz question set.

Validation:

- Add a test that changes the source question answer after publishing a `QuestionVersion`, then submits the old version answer and receives the version grade.
- Add a test that changes source question grade/config after publishing and verifies the version grade/config is used.
- Add a test that submits a partially answered versioned quiz and confirms unanswered records correspond to the version's source questions only.

### P2: Current-year offering fallback can raise `MultipleObjectsReturned`

File:

- `management_system/utils/helpers.py`

Problem:

`get_course_offering_for_user_course()` falls back with:

```python
CourseOffering.objects.get(course=course, cohort__academic_year=current_year)
```

Phase 1 allows multiple cohorts in one academic year. A course with more than one current-year offering can raise `MultipleObjectsReturned` and produce a 500.

Required fix:

- Avoid `.get()` for the current-year fallback.
- Use deterministic selection only when that fallback is truly acceptable, or return `None` when the user has no enrollment and there are multiple possible offerings.

Acceptance criteria:

- A course with two current-year offerings does not crash course detail, lesson detail, quiz detail, or management views.
- Enrolled users resolve through their enrollment before fallback.
- Non-enrolled users do not accidentally receive an arbitrary cohort's version content.

Validation:

- Add tests with two current-year cohorts for the same course.

### P2: Data migration idempotency misses interrupted partial state

File:

- `management_system/migrations/0022_phase2_populate_content_versions.py`

Problem:

Question versions are created only inside `if created:` for `QuizVersion`. If a migration run creates the `QuizVersion` and then stops before all `QuestionVersion` rows are created, a rerun will skip question creation because the quiz version already exists.

Required fix:

- Always ensure expected `QuestionVersion` rows exist for the resolved `QuizVersion`, even when the quiz version was pre-existing.

Acceptance criteria:

- Rerunning the migration fills missing question versions for an existing quiz version.
- Rerunning remains safe and does not duplicate existing rows.

Validation:

- Add a migration test that pre-creates a `QuizVersion` without questions, runs population, and verifies missing `QuestionVersion` rows are created.

### P2: Strict import rule is still violated in Phase 2 tests

File:

- `management_system/test_phase2_content_versioning.py`

Problem:

The Phase 2 plan requires imports at the top of the file. `_run_population()` contains local imports for `importlib` and `django.apps.apps`.

Required fix:

- Move all imports to the top of the file.
- Keep this rule in every future implementation prompt and checklist.

Acceptance criteria:

- No new Phase 2 code or tests contain local imports.
- Existing local imports touched by the Phase 2 work are moved to top-level imports where practical.

Validation:

- Run a text search for local imports in Phase 2-touched files before marking complete.

## Do Not Mark Complete Until

- The version detail helpers are scoped by the user's course offering.
- Lesson detail, lesson streaming, quiz detail, and quiz submission all use the same resolved version row.
- Versioned quiz grading uses `QuestionVersion` snapshot data, not the mutable source `Question`.
- Current-year fallback cannot crash with multiple offerings.
- The data migration repairs missing question versions on rerun.
- Phase 2 tests include wrong-offering access, historical answer grading, partial versioned submissions, and interrupted migration rerun coverage.
- All imports added or touched by this phase are at the top of their file.

## Verification Performed

- `python manage.py check`: passes with pre-existing `staticfiles.W004`.
- SQLite isolated Phase 2 tests: 16/16 pass.

PostgreSQL `makemigrations --check --dry-run` and full PostgreSQL test runs were not revalidated in this review because this environment has a known timeout issue.
