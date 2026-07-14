# Bible Institute LMS — Phased Implementation and DeepSeek Orchestration Plan

**Status:** Authoritative architecture; implementation-ready after external migration-state confirmation

**Updated:** 14 July 2026

**Target stack:** Django 5.1, Django templates, Bootstrap 5, HTMX/Alpine, Video.js, Cloudflare R2/Worker

**Purpose:** One authoritative plan that DeepSeek can orchestrate, delegate to smaller models, review, test, and deliver incrementally.

This file is the only implementation source of truth. The older feature roadmap at `docs/features/06_LMS_COHORT_VERSIONING_FEATURE_ROADMAP.md` is now an alignment/decision record that points here. Its earlier Cohort and content-version model proposals must not be implemented.

## 1. Confirmed Architecture

### 1.1 Academic structure

There is no user-facing Version or Cohort concept.

Use this structure:

~~~text
Level 1
  ├── Academic Year 2025/2026
  └── Academic Year 2026/2027

Level 2
  ├── Academic Year 2025/2026
  └── Academic Year 2026/2027
~~~

Implementation:

- Reuse the existing positive integer level used by Course.
- AcademicYear belongs to one level.
- Only one AcademicYear row may exist for the same level and year name.
- Only one AcademicYear may be current for a given level.
- CourseOffering connects a Course to an AcademicYear.
- Course.level must match CourseOffering.academic_year.level.
- Enrollment connects a student to an AcademicYear, optionally to a specific CourseOffering for repeat/remedial/manual access.
- Lesson and Quiz belong to CourseOffering so content is controlled per level and academic year.
- Reports filter by Level then AcademicYear.
- Attendance calendars belong to AcademicYear, which already identifies the level.

Final relationships:

~~~text
User -- Enrollment -- AcademicYear(level, year)
                         |
Course -- CourseOffering-+
             |          |
             |          +-- Lesson
             |          +-- Quiz -- Question
             |
             +-- optional repeat/remedial Enrollment

Quiz -- Grade
Question -- Submission
Lesson -- LectureProgress
AcademicYear -- AcademicHoliday
AcademicYear -- AttendanceRecord
~~~

`Lesson`, `Quiz`, and `Question` are the content records; there are no parallel LessonVersion, QuizVersion, or QuestionVersion tables. Historical safety comes from copying content into a new CourseOffering and locking published content after student activity exists.

Do not create:

- A Version model.
- A Cohort model in the final schema.
- LessonVersion, QuizVersion, or QuestionVersion models.
- A separate Level model unless the existing integer level becomes measurably insufficient.

### 1.2 Roles versus academic placement

Role controls authorization only. The final roles are:

- admin
- staff
- moderator
- student

Student level and academic year come only from active Enrollment records. Existing junior/senior roles are temporary migration inputs: backfill their Enrollment first, then migrate both to student. Do not keep role and Enrollment as two permanent sources of academic truth.

### 1.3 Attendance permissions

These roles may scan attendance:

- Admin
- Staff
- Moderator

Differences:

- Admin can scan, correct, and delete attendance.
- Staff can scan and view reports but cannot delete attendance or other data.
- Moderator can only scan, view scan previews, and view their own scan result.
- Students cannot scan.

### 1.4 Current branch reconciliation

Repository inspection on 14 July 2026 found:

- Migrations 0019 through 0022 are staged additions, not present in git history.
- The local SQLite migration table ends at 0016; 0019 through 0022 are not applied there.
- Current staged code implements Cohort plus LessonVersion/QuizVersion/QuestionVersion and therefore conflicts with this plan.
- Phase 0 access/security fixes remain useful and must be preserved.

Default implementation decision: after confirming no external environment manually applied these uncommitted migrations, replace the staged 0019-0022 design before it is committed. Remove the WIP Cohort/content-version runtime code and tests, then create the simpler Level/AcademicYear/CourseOffering schema and direct Lesson/Quiz relationships. If an external environment did apply them manually, stop and design forward migrations instead of rewriting history.

### 1.5 Content immutability without version tables

- Draft Lesson/Quiz/Question content can be edited.
- Published content can be edited only while no student activity refers to it.
- Once a Quiz has a Submission/Grade, its questions, answers, grade weights, and total cannot be mutated; duplicate the Quiz into the target CourseOffering.
- Once a Lesson has LectureProgress, its media/part structure cannot be mutated; duplicate the Lesson into the target CourseOffering.
- Opening access for selected students later uses a QuizSpecialOpening tied directly to Quiz; it does not require QuizVersion.
- Existing Grade and Submission remain the assessment history until a separate multi-attempt requirement justifies QuizAttempt/AnswerSubmission.

### 1.6 Other confirmed requirements

- Username is required and remains the login identifier.
- Email is optional.
- Required application fields: name, username, password, phone, priest name, priest number, church, city, identity type, identity number, and Terms checkbox.
- National ID and passport numbers share one unique identity-number field.
- Egyptian national ID is 14 digits; passport accepts international alphanumeric formats.
- Identity front/back and payment images are optional, private R2 files visible only to admin.
- Profile image is optional; use a fallback avatar.
- Cairo and Giza default to offline; admin manages the offline-city list and may override study mode.
- Applications create inactive accounts with pending status.
- Admin can activate/decline individually or in bulk from an Applications view separate from Users.
- Gmail sends received, activated, and declined emails when email exists.
- Terms open in a popup; no Terms version/history is stored.
- Existing teacher users migrate to moderator.
- Existing junior/senior users migrate to student after Enrollment backfill is verified.
- Staff can create/read/update academic, content, and grading data but cannot delete.
- Attendance is recorded only for offline students.
- AcademicYear contains start/end dates, meeting weekdays, and holidays.
- Initial meeting days are Sunday and Tuesday; dates remain configurable.
- Students can view the calendar.
- QR scan flow is Scan → Preview → Entrance, Exit, or Cancel.
- Duplicate same-day actions are discarded.
- Online progress merges equivalent audio/video coverage and counts unique media seconds.
- A lecture part completes at 80%; incomplete progress is visible but does not block access.
- Audio downloads remain available and do not add progress.
- Course/lecture/quiz duplication copies database data, reuses R2 keys, starts draft/inactive, and never copies submissions, grades, attendance, or progress.
- Summary report is CSV.
- Detailed report is XLSX with Grades, Attendance Summary, and Attendance Daily sheets.
- Home and About are public, server-rendered, static Django templates with SEO.
- Upload flow gets a quick action to create a lecture from selected uploaded files.

## 2. Role Capability Matrix

| Capability | Admin | Staff | Moderator | Student |
|---|---:|---:|---:|---:|
| Decide applications | Yes | No | No | No |
| View identity/payment files | Yes | No | No | Own uploads only if later required |
| Manage roles/offline cities | Yes | No | No | No |
| Create/read/update content | Yes | Yes | No | No |
| Delete content | Yes | No | No | No |
| Grade written answers | Yes | Yes | No | No |
| Scan attendance | Yes | Yes | Yes | No |
| Correct/delete attendance | Yes | No | No | No |
| Create/update academic calendar | Yes | Yes | No | No |
| Delete academic calendar entries | Yes | No | No | No |
| View management reports | Yes | Yes | No | Own data only |
| View student calendar | Yes | Yes | Schedule context | Yes |
| View courses/lectures | Yes | Yes | No unless separately enrolled | Yes |

Every permission must be enforced by Django. Hiding a button is not authorization.

## 3. Orchestration Rules

DeepSeek must follow these rules for every phase:

1. Inspect relevant code and every caller before editing.
2. Read git status first; preserve all unrelated user changes.
3. Work on one task card at a time unless tasks are explicitly marked parallel-safe.
4. Never let two delegated models edit models.py, migrations, URLs, or shared permission helpers concurrently.
5. Each task must have one bounded objective, explicit allowed files, acceptance checks, and a returned diff summary.
6. The orchestrator reviews every delegated diff before marking the task complete.
7. A task is not complete until its smallest relevant test/check passes.
8. Do not add abstractions, services, dependencies, APIs, or background queues unless this plan explicitly requires them.
9. Reuse existing helpers, templates, R2 manager, CSV safety, pagination, i18n, and UI patterns.
10. Use database constraints for uniqueness/idempotency, not client-side checks.
11. Keep all user-facing strings translatable and verify Arabic RTL.
12. Use additive migrations first. The only exception is replacing confirmed-unapplied, uncommitted WIP migrations 0019-0022 before they enter history.
13. Never rewrite an applied migration. Confirm external migration state before replacing 0019-0022.
14. Do not commit, push, deploy, or delete production data unless the human explicitly requests it.
15. Stop and report if a task would overwrite unrelated dirty changes or requires missing production secrets/Worker source.

## 4. Task Card Contract

Before delegating any task, DeepSeek must create a child prompt with:

~~~text
Task ID:
Objective:
Why this task exists:
Dependencies already completed:
Relevant current behavior:
Allowed files:
Files that must not be touched:
Exact implementation requirements:
Out of scope:
Acceptance checks:
Commands to run:
Required response:
  - Summary
  - Files changed
  - Tests/checks and results
  - Assumptions
  - Risks or blockers
~~~

Small models may implement bounded forms, views, templates, exports, tests, or isolated helpers. The orchestrator or strongest available model must own schema design, migrations, permission integration, progress trust validation, and final cross-feature review.

## 5. Phase 0 — Baseline and Migration Decision

**Goal:** Establish a safe baseline before building features.

### P0-T01 — Capture repository state

- **Delegate:** Small model, read-only.
- **Work:** Record git branch/status, Django settings used locally, installed migrations, available tests, and current failures.
- **Checks:** Run Django system check where environment permits; do not fix unrelated failures.
- **Done when:** Baseline report clearly separates existing failures from later regressions.

### P0-T02 — Map current academic callers

- **Delegate:** Small model, read-only.
- **Work:** Find every reference to AcademicYear, Cohort, CourseOffering, Enrollment, Course.level, joined_date, and role-based course access.
- **Output:** File/line inventory and dependency notes.
- **Done when:** Orchestrator knows all code paths affected by removing Cohort.

### P0-T03 — Determine migration strategy

- **Owner:** Orchestrator; do not delegate schema decision.
- **Work:**
  - Run showmigrations.
  - Inspect migrations 0019 through 0022.
  - Determine whether they are applied in any shared/staging/production environment.
  - Record the known repository facts: they are staged, absent from git history, and unapplied in the local SQLite database.
  - If never deployed externally: replace all four unfinished migrations and their Cohort/content-version runtime code before merge.
  - If deployed anywhere: stop and design forward migrations that move data safely and remove Cohort/content-version tables later.
- **Done when:** Migration approach is written and approved before model edits.

### P0-T04 — Protect the baseline

- **Delegate:** Small model.
- **Work:** Add or identify one runnable smoke test for login, course access, and quiz submission if no suitable tests exist.
- **Done when:** Later phases can detect breakage in existing core workflows.

**Phase gate:** Baseline recorded, migration strategy chosen, no unexplained test regression.

## 6. Phase 1 — Level-Based Academic Years

**Goal:** Replace cohort/version semantics with Level → AcademicYear.

### Target schema

#### AcademicYear

- name, such as 2026/2027.
- level, positive integer.
- starts_on.
- ends_on.
- is_current.
- meeting_weekdays JSON list, initially Sunday and Tuesday.
- Unique level + name.
- One current AcademicYear per level.
- End date after start date.

#### CourseOffering

- course.
- academic_year.
- instructor.
- status.
- Unique course + academic_year.
- Course level must equal AcademicYear level.

#### Enrollment

- student.
- academic_year.
- optional course_offering.
- enrollment_type.
- status.
- enrolled_at/enrolled_by.
- Full AcademicYear access when course_offering is null and type is normal.

### P1-T01 — Remove the unpublished conflicting WIP

- **Owner:** Orchestrator.
- **Depends on:** P0-T03.
- **Work:** If external non-deployment is confirmed, replace staged migrations 0019-0022 and remove Cohort, LessonVersion, QuizVersion, QuestionVersion, their admin registrations, version-selection helpers/views, and Phase 1/2 WIP tests. Preserve unrelated Phase 0 access/security fixes.
- **Done when:** No runtime import/reference to the rejected models remains and the legacy baseline still passes.

### P1-T02 — Implement final academic models and schema migration

- **Owner:** Orchestrator.
- **Work:** Implement AcademicYear(level), CourseOffering(academic_year), Enrollment(academic_year, optional course_offering), and MigrationReviewItem. Add Lesson/Quiz CourseOffering as nullable for backfill and add their draft/published/archived status.
- **Done when:** Migration works on an empty database and a copy of existing data.

### P1-T03 — Backfill academic data

- **Delegate:** Strong model.
- **Work:** Map existing User, Course, Lesson, Quiz, Grade, and Submission data directly into level-specific AcademicYear, CourseOffering, Enrollment, and Lesson/Quiz offering fields. Do not create intermediate Cohort or content-version rows.
- **Rule:** Ambiguous rows become MigrationReviewItem records; never guess.
- **Done when:** Row counts and relationships reconcile before/after.

### P1-T04 — Move runtime access to Enrollment and CourseOffering

- **Delegate:** Small model after schema lands.
- **Work:** Update helpers and course-access queries to use Enrollment → AcademicYear and CourseOffering → AcademicYear.
- **Done when:** Existing access tests pass; students see offerings from enrollments; no runtime code imports rejected WIP models.

### P1-T05 — Update Django admin

- **Delegate:** Small model, parallel-safe with P1-T06 after P1-T04.
- **Work:** AcademicYear, CourseOffering, Enrollment filters/forms/list displays use level and academic year.
- **Done when:** Admin can create Level 1 2026/2027 and Level 2 2026/2027 independently.

### P1-T06 — Update management dashboards

- **Delegate:** Small model.
- **Work:** Replace cohort/version labels and filters with Level then AcademicYear.
- **Done when:** No user-facing Cohort or Version text remains.

### P1-T07 — Academic constraints tests

- **Delegate:** Small model.
- **Tests:**
  - Same academic year name allowed across different levels.
  - Duplicate level + name rejected.
  - Only one current AcademicYear per level.
  - CourseOffering level mismatch rejected.
  - Enrollment grants intended access.
  - A second-year student retains historical first-year Enrollment without seeing the new first-year offering.
  - A repeat/remedial Enrollment grants only its selected CourseOffering.
- **Done when:** Focused tests pass.

### P1-T08 — Remove role/date access fallbacks

- **Owner:** Orchestrator.
- **Depends on:** Backfill and runtime verification.
- **Work:** After Enrollment equivalence is proven, remove junior/senior and joined-date access fallbacks. Keep joined_date only as historical/application data if still useful.
- **Done when:** Migration rehearsal and full tests pass.

**Phase gate:** No Cohort in final runtime schema; all access goes through Level/AcademicYear.

## 7. Phase 2 — Roles and Authorization

**Goal:** Add staff/moderator roles and explicit capability checks.

### P2-T01 — Add role data migration

- **Delegate:** Small model.
- **Work:** Create staff, moderator, and student roles; migrate teacher users to moderator; after P1 Enrollment verification, migrate junior/senior users to student.
- **Done when:** Migration is idempotent, admin remains unchanged, and academic placement no longer depends on role.

### P2-T02 — Add capability helpers

- **Owner:** Strong model.
- **Work:** Replace broad admin/teacher checks with small explicit helpers:
  - can_manage_applications
  - can_manage_content
  - can_delete_content
  - can_grade
  - can_scan_attendance
  - can_correct_attendance
  - can_view_reports
- **Done when:** Helpers are the single authorization source for new and existing management routes.

### P2-T03 — Audit every management route

- **Delegate:** Small model, read/patch bounded route groups.
- **Work:** Apply capability checks to user, course, lesson, quiz, grading, R2, and report views.
- **Done when:** No route relies only on hidden navigation or old MANAGEMENT_ROLES.

### P2-T04 — Update role navigation

- **Delegate:** Small model.
- **Work:** Admin full navigation; staff management without delete; moderator scanner-only navigation.
- **Done when:** Navigation matches the role matrix in English and Arabic.

### P2-T05 — Permission regression tests

- **Delegate:** Small model.
- **Tests:** Each role against read/create/update/delete/scan/correct/private-file routes.
- **Done when:** Direct URL access is forbidden correctly, not merely hidden.

**Phase gate:** Authorization matrix passes before registration or attendance is exposed.

## 8. Phase 3 — Student Applications

**Goal:** Public signup creates a pending inactive student and admin decides it.

### P3-T01 — Add application/user fields

- **Owner:** Strong model.
- **Fields:** phone, priest name/number, church, city, study mode, override flag, identity type/number, private R2 keys, optional profile key, application status/decision data, QR token.
- **Migration:** Existing users become active; legacy missing profile fields remain allowed until manually completed.
- **Done when:** Existing users can still authenticate.

### P3-T02 — Add OfflineCity model and seed data

- **Delegate:** Small model.
- **Work:** Add normalized unique names and active flag; seed Cairo and Giza.
- **Done when:** City matching is case-insensitive and deterministic.

### P3-T03 — Build application validators

- **Delegate:** Small model.
- **Work:** Username/password, normalized unique phone, identity type/number, JPG/PNG/PDF signature, and 10 MB checks.
- **Done when:** National ID is 14 digits; international passport formats work; unsafe files fail.

### P3-T04 — Build public signup form

- **Delegate:** Small model.
- **Work:** Required/optional fields, identity-type conditional UI, Terms popup, password errors, accessible labels, RTL.
- **Done when:** Form matches confirmed fields and works without JavaScript except popup enhancement.

### P3-T05 — Implement private R2 upload helper

- **Owner:** Strong model.
- **Work:** Validate then upload to private/application-prefixed keys; return keys only; clean uploaded files if user creation fails.
- **Done when:** No permanent public URL is stored and no orphan remains after transaction failure.

### P3-T06 — Implement signup view

- **Delegate:** Strong model.
- **Work:** Determine online/offline from OfflineCity, create pending inactive User atomically, upload optional files, show success.
- **Done when:** Applicant cannot log in and duplicate username/phone/identity is rejected safely.

### P3-T07 — Configure Gmail email templates

- **Delegate:** Small model.
- **Work:** Environment-only SMTP settings and translatable received/activated/declined templates.
- **Rule:** Missing email skips sending; delivery failure does not roll back a valid application.
- **Done when:** Console/test backend verifies all three messages.

### P3-T08 — Build Applications list

- **Delegate:** Small model.
- **Work:** Separate route/view, pending/active/declined filters, search, pagination, selection.
- **Done when:** Users dashboard and Applications dashboard are distinct.

### P3-T09 — Build individual review

- **Delegate:** Small model.
- **Work:** Show application data, short-lived admin-only private previews, study-mode override, AcademicYear choice.
- **Done when:** Non-admin direct access is forbidden.

### P3-T10 — Implement individual activation/decline

- **Owner:** Strong model.
- **Work:** Idempotent status update, is_active sync, assign the student role, create Enrollment for the chosen level-specific AcademicYear on activation, record decision audit fields, and send email after commit.
- **Done when:** Repeating an action creates no duplicate Enrollment/email.

### P3-T11 — Implement bulk activation/decline

- **Delegate:** Strong model.
- **Work:** Validate all selected IDs and use transactions; return per-row failures without corrupting successful rows.
- **Done when:** Bulk and individual results are identical.

### P3-T12 — Application tests

- **Delegate:** Small model.
- **Coverage:** Fields, uniqueness, city mode, override, files, permissions, emails, individual/bulk idempotency.
- **Done when:** Focused suite passes.

**Phase gate:** A public applicant can register and admin can safely decide the application.

## 9. Phase 4 — Academic-Year Content and Duplication

**Goal:** Existing Lesson/Quiz/Question records belong directly to one CourseOffering and can be copied safely without parallel version tables.

### P4-T01 — Attach Lesson and Quiz to CourseOffering

- **Owner:** Orchestrator.
- **Work:** Add nullable CourseOffering FK directly to existing Lesson and Quiz, backfill, record ambiguous mappings, update reads/writes, then make required. Question remains linked directly to Quiz.
- **Done when:** Student content comes only from enrolled AcademicYear offerings.

### P4-T02 — Add draft/published status

- **Delegate:** Small model.
- **Work:** Add draft/published/archived status directly to Lesson and Quiz. Existing content becomes published; all copies start draft.
- **Done when:** Draft content is invisible to students.

### P4-T03 — Update lesson/quiz forms and filters

- **Delegate:** Small model.
- **Work:** Select Level → AcademicYear → CourseOffering and prevent level mismatch.
- **Done when:** Admin/staff can deliberately place content in the correct year.

### P4-T04 — Duplicate lecture

- **Delegate:** Small model.
- **Work:** New Lesson PK, copied JSON by value, same R2 keys, target offering, draft status; no progress.
- **Done when:** Editing the copy does not mutate source database data.

### P4-T05 — Duplicate quiz

- **Delegate:** Small model.
- **Work:** New Quiz and Question PKs; copy choices/config/answers; do not copy dates, submissions, or grades; target offering; draft.
- **Done when:** Source quiz history remains unchanged.

### P4-T06 — Copy course offering

- **Delegate:** Strong model.
- **Work:** Reuse Course catalog, create target AcademicYear offering, optionally copy lessons/quizzes using P4-T04/P4-T05 logic.
- **Done when:** One transaction creates a complete draft copy.

### P4-T07 — Duplicate as new course

- **Delegate:** Small model.
- **Work:** Require a new unique course name, target AcademicYear, optional content copy.
- **Done when:** New catalog Course is independent except shared R2 objects.

### P4-T08 — Protect shared R2 media

- **Owner:** Strong model.
- **Work:** Before R2 deletion, scan Lesson media references; block referenced deletion and show references; only admin may explicitly force.
- **Done when:** Deleting a duplicate cannot break the original.

### P4-T09 — Prevent historical reassignment

- **Delegate:** Small model.
- **Rule:** Draft/no-activity content may move or be edited. A Quiz with Submission/Grade and a Lesson with LectureProgress are immutable in their historical fields; duplicate them instead. No LessonVersion/QuizVersion/QuestionVersion wrapper is allowed.
- **Done when:** Historical academic-year reporting cannot be silently rewritten.

### P4-T10 — Upload-to-lecture quick action

- **Delegate:** Small model.
- **Work:** After upload, select files, open prefilled lecture form, revalidate R2 keys, choose offering/name/status, save without reupload. New organized paths use level/academic-year/course/folder; do not add cohort/version path segments.
- **Done when:** One upload can become a draft lecture with minimal navigation.

### P4-T11 — Duplication/content tests

- **Delegate:** Small model.
- **Coverage:** New PKs, draft status, shared media, no history copy, target year, R2 deletion guard.
- **Done when:** Focused suite passes.

**Phase gate:** Academic-year content is isolated, copies cannot damage existing records, and no parallel content-version models exist.

## 10. Phase 5 — Attendance Calendar, QR, and Scanning

**Goal:** Scheduled offline attendance with explicit preview/action flow.

### Target data

#### AcademicHoliday

- academic_year.
- date.
- name.
- Unique academic_year + date.

#### AttendanceRecord

- student.
- academic_year.
- attendance_date in Cairo timezone.
- action: entrance or exit.
- scanned_at.
- scanned_by.
- corrected_at/corrected_by nullable.
- Unique student + academic_year + attendance_date + action.

Expected dates are derived, not stored:

- Inside AcademicYear start/end.
- Weekday is configured.
- Not a holiday.
- Not future.
- Student was active/enrolled by that date.

### P5-T01 — Add holiday and attendance models

- **Owner:** Strong model.
- **Work:** Models, constraints, indexes, admin registration.
- **Done when:** Duplicate concurrent action is impossible at database level.

### P5-T02 — Build expected-date helper

- **Delegate:** Small model.
- **Work:** One pure function for dates/status denominator using Cairo timezone.
- **Checks:** Sunday/Tuesday, holidays, leap dates, enrollment start, future dates, zero expected days.
- **Done when:** Small focused tests pass.

### P5-T03 — Build calendar management

- **Delegate:** Small model.
- **Work:** Admin and staff create/update start/end/weekdays/holidays; only admin can delete. Moderator/student are read-only where their workflow needs the schedule.
- **Done when:** Invalid date ranges/duplicate holidays are rejected.

### P5-T04 — Build student calendar

- **Delegate:** Small model.
- **Work:** Show schedule, holidays, and past valid/invalid/absent states.
- **Done when:** Online students see the calendar but no attendance expectation/status penalty.

### P5-T05 — Generate/download QR

- **Delegate:** Small model.
- **Dependency:** qrcode[pil].
- **Work:** QR contains opaque token URL; student/admin can download PNG; admin can regenerate.
- **Done when:** Old token fails after regeneration and no private identity data appears in QR.

### P5-T06 — Build scanner shell

- **Delegate:** Small model.
- **Work:** Mobile-first camera UI plus manual token fallback, accessible controls, permission error state.
- **Done when:** Admin/staff/moderator can open; others cannot.

### P5-T07 — Implement scan preview endpoint

- **Delegate:** Strong model.
- **Work:** Resolve token and return name, phone, profile/fallback, level/year, study mode, day eligibility, recorded actions.
- **Done when:** Preview never records attendance.

### P5-T08 — Implement entrance/exit endpoint

- **Owner:** Strong model.
- **Work:** Recheck permission, token, current scheduled day, offline mode, enrollment; get_or_create under unique constraint.
- **Done when:** Duplicate scan returns already-recorded and creates one row.

### P5-T09 — Admin attendance correction

- **Delegate:** Small model.
- **Work:** Admin create/update/delete with correction metadata; staff/moderator forbidden.
- **Done when:** Corrections immediately affect calendar/report results.

### P5-T10 — Attendance management views

- **Delegate:** Small model.
- **Work:** Filters by level, AcademicYear, date, student, status, scanner.
- **Done when:** Admin/staff can inspect data; moderator cannot browse reports.

### P5-T11 — Attendance tests

- **Delegate:** Small model.
- **Coverage:** Permissions, preview-only, scheduled dates, online no-record, duplicates, correction, report categories.
- **Done when:** Focused suite passes.

**Phase gate:** Admin, staff, and moderator can scan; only admin can correct/delete.

## 11. Phase 6 — Verified Online Lecture Progress

**Goal:** Count unique played ranges only when backed by Worker-served HLS segments.

### Trust rule

Credit equals the intersection of:

1. Player-reported played ranges.
2. Segments verified as served by Cloudflare Worker for the same short-lived viewing session.

Client-only or Worker-only evidence earns nothing.

### Target data

- Stable part_id shared by equivalent audio/video links.
- LessonMediaSegment: lesson, part_id, object key, start/end seconds.
- ViewingSession: student, lesson, part_id, opaque ID, expiry, last heartbeat.
- VerifiedSegmentRequest: session, segment, request time; unique pair.
- LectureProgress: student, lesson, part_id, merged ranges, unique seconds, percent, completed_at.

### P6-T01 — Add stable media part IDs

- **Delegate:** Strong model.
- **Work:** Add/backfill part_id in existing Lesson links JSON without changing R2 references.
- **Done when:** Equivalent audio/video for one part share the same ID.

### P6-T02 — Parse HLS segment timelines

- **Delegate:** Strong model.
- **Work:** Parse EXTINF durations into object key/start/end metadata during lesson create/update.
- **Done when:** Segment timeline total matches manifest duration within tolerance.

### P6-T03 — Add tracking models/migrations

- **Owner:** Orchestrator.
- **Work:** Add constraints/indexes and cleanup-friendly timestamps.
- **Done when:** Schema supports idempotent receipts and one progress row per student/part.

### P6-T04 — Build interval merge helper

- **Delegate:** Small model.
- **Work:** Merge overlapping/touching ranges, clamp to duration, calculate unique seconds/percentage.
- **Tests:** Replay, overlap, seek gap, audio/video shared range, invalid bounds, 80%.
- **Done when:** Pure helper tests pass.

### P6-T05 — Create signed viewing sessions

- **Owner:** Strong model.
- **Work:** Verify online student enrollment/content, create opaque short-lived session, sign allowed R2 prefix and expiry with HMAC.
- **Done when:** Token reveals no student identity and cannot access another object prefix.

### P6-T06 — Rewrite manifest URLs

- **Delegate:** Strong model.
- **Work:** Existing manifest endpoint emits Worker segment URLs bound to viewing session token.
- **Done when:** Existing playback works only with valid signed session.

### P6-T07 — Implement Worker receipt endpoint

- **Owner:** Strong model.
- **Work:** Verify Worker signature/timestamp, resolve opaque session and segment key, store idempotent receipt.
- **Done when:** Forged, stale, wrong-prefix, and duplicate receipts behave correctly.

### P6-T08 — Write Worker integration contract

- **Delegate:** Small model, documentation only until Worker source arrives.
- **Include:** Secret names, canonical HMAC payload, expiry/skew, CORS, object-prefix check, receipt body/header, ctx.waitUntil behavior, retry/idempotency.
- **Done when:** Worker implementation can be completed without guessing Django semantics.

### P6-T09 — Add player heartbeat JS

- **Delegate:** Small model.
- **Work:** Send played ranges about every 10 seconds and on pause, seek, ended, page hide, and disposal.
- **Done when:** HTMX fragment/player reinitialization does not duplicate listeners.

### P6-T10 — Credit verified progress

- **Owner:** Strong model/orchestrator review required.
- **Work:** Validate session/user, reject impossible jumps, intersect heartbeat with verified segments, merge with existing progress transactionally.
- **Done when:** Replays do not increase unique seconds and unverified ranges earn zero.

### P6-T11 — Student progress UI

- **Delegate:** Small model.
- **Work:** Shared audio/video progress bar, unique time, percentage, complete at 80%.
- **Done when:** Switching media type preserves one progress value.

### P6-T12 — Admin/staff progress dashboard

- **Delegate:** Small model.
- **Work:** Filter level/year/student/course/completion; show below-80% and last activity.
- **Done when:** No access blocking or automatic disciplinary action is added.

### P6-T13 — Cleanup command

- **Delegate:** Small model.
- **Work:** Delete expired sessions/old segment receipts while retaining aggregate progress.
- **Done when:** Command supports dry-run and bounded age.

### P6-T14 — Progress security/integration tests

- **Owner:** Strong model review.
- **Coverage:** Signed token, prefix, expiry, receipt, heartbeat, intersection, replay, seek, 80%, download no-credit.
- **Done when:** Focused suite and existing lesson playback tests pass.

**Phase gate:** Server progress requires both player activity and verified R2 delivery.

## 12. Phase 7 — Combined Reports

**Goal:** Export grades and attendance by Level and AcademicYear.

### P7-T01 — Build report query/filter form

- **Delegate:** Small model.
- **Work:** Required Level and AcademicYear; optional student, study mode, CourseOffering.
- **Done when:** AcademicYear choices are limited to selected level.

### P7-T02 — Build attendance aggregation

- **Delegate:** Strong model.
- **Work:** Expected/valid/invalid/absent counts from P5-T02 and AttendanceRecord.
- **Formula:** attendance rate = valid / expected; absence rate = absent / expected; zero denominator returns zero.
- **Done when:** Totals reconcile per student.

### P7-T03 — Build academic-year grade aggregation

- **Delegate:** Strong model.
- **Work:** Traverse Quiz → CourseOffering → AcademicYear; distinguish zero grade from not submitted.
- **Done when:** No filtering by submission calendar year.

### P7-T04 — Summary CSV export

- **Delegate:** Small model.
- **Columns:** identity basics, mode, level/year, grade earned/available/percent, expected/valid/invalid/absent, attendance/absence rates.
- **Rule:** Reuse existing CSV formula-injection protection.
- **Done when:** One student per row and totals match aggregates.

### P7-T05 — Detailed XLSX export

- **Delegate:** Small model.
- **Dependency:** openpyxl.
- **Sheets:** Grades, Attendance Summary, Attendance Daily.
- **Work:** Frozen headers, filters, safe text, Cairo timestamps, level/year filename.
- **Done when:** Workbook opens and contains exactly the confirmed sheets.

### P7-T06 — Report dashboard

- **Delegate:** Small model.
- **Work:** Summary preview, export buttons, empty/error/loading states.
- **Done when:** Admin/staff access only.

### P7-T07 — Export tests

- **Delegate:** Small model.
- **Coverage:** Filters, formulas, zero expected, not submitted, CSV safety, workbook sheet names/rows.
- **Done when:** Focused suite passes.

**Phase gate:** CSV/XLSX reconcile with source grades and attendance.

## 13. Phase 8 — Public Pages and UI/UX

**Goal:** Public SEO pages and faster role-specific navigation without a frontend rewrite.

### P8-T01 — Separate public and authenticated home

- **Delegate:** Small model.
- **Work:** Root becomes public Home; authenticated student landing moves to /portal/; keep admin dashboard distinct.
- **Done when:** Home/About anonymous; private routes still protected.

### P8-T02 — Build public Home

- **Delegate:** Small model.
- **Work:** Server-rendered semantic template, clear institute/program/application/login actions, responsive/RTL.
- **Done when:** Final content placeholders are clearly isolated for later copy replacement.

### P8-T03 — Build public About

- **Delegate:** Small model.
- **Work:** Static server-rendered template; no CMS.
- **Done when:** Accessible in English/Arabic route structure.

### P8-T04 — Add SEO

- **Delegate:** Small model.
- **Work:** Titles/descriptions, canonical, hreflang, Open Graph, JSON-LD, robots.txt, sitemap.xml.
- **Done when:** Private/admin pages are excluded from sitemap/indexing.

### P8-T05 — Consolidate navigation/components

- **Delegate:** Small model.
- **Work:** Reuse production-worthy theme-showcase patterns in real base/partials; role-specific nav; breadcrumbs; primary actions.
- **Done when:** No second frontend system or new JS framework is introduced.

### P8-T06 — Accessibility/RTL pass

- **Delegate:** Small model.
- **Work:** Labels, keyboard focus, modal focus, scanner buttons, table/card responsive behavior, contrast, Arabic direction.
- **Done when:** Critical flows are keyboard usable and RTL readable.

### P8-T07 — Query/performance pass

- **Delegate:** Strong model.
- **Work:** Add select_related/prefetch_related, pagination, indexes already justified by measured/list-page queries.
- **Done when:** No obvious N+1 on Applications, Attendance, Progress, or Reports.

### P8-T08 — UI/public tests

- **Delegate:** Small model.
- **Coverage:** Anonymous routes, redirects, role nav, SEO endpoints, RTL templates, upload quick action.
- **Done when:** Focused checks pass.

**Phase gate:** Public pages are indexable and management/student flows are consistent.

## 14. Phase 9 — Release Hardening

### P9-T01 — Full permission audit

- **Owner:** Orchestrator.
- **Work:** Test direct URLs and state-changing POSTs for all four roles.
- **Done when:** Capability matrix is fully enforced.

### P9-T02 — Migration rehearsal

- **Owner:** Orchestrator.
- **Work:** Restore production-like database copy, run all migrations, verify counts/relationships/review items, test rollback procedure.
- **Done when:** No unexplained data loss or orphaned relationship.

### P9-T03 — Private-file security review

- **Owner:** Strong model.
- **Work:** Confirm no public R2 URLs, permission before presigning, masked logs, upload validation, object-prefix safety.
- **Done when:** Non-admin access tests fail closed.

### P9-T04 — Worker end-to-end test

- **Owner:** Orchestrator after Worker source is supplied.
- **Work:** Deploy to staging, validate HMAC, playback, receipts, retries, CORS, expired token, wrong prefix.
- **Done when:** Verified progress works without Django proxying media.

### P9-T05 — Production configuration check

- **Delegate:** Small model.
- **Work:** DEBUG false, hosts/CSRF, secure cookies, database, R2, Gmail, Worker secrets, static files.
- **Done when:** Missing required production configuration fails clearly.

### P9-T06 — Full test and manual acceptance pass

- **Owner:** Orchestrator.
- **Work:** Run full suite plus manual signup, activation, content copy, scan, progress, export, public pages in English/Arabic/mobile.
- **Done when:** No phase-gate regression.

### P9-T07 — Deployment/rollback checklist

- **Owner:** Orchestrator.
- **Include:** DB backup, migration order, static collection, secrets, Worker deploy, smoke checks, rollback boundary, monitoring.
- **Done when:** Human approves release.

## 15. Legacy Roadmap Integration

The earlier roadmap contained useful future capabilities but attached several of them to the rejected Cohort/content-version schema. They are classified here so they are not accidentally implemented with old relations.

### Replaced by this plan

| Older roadmap area | Final direction |
|---|---|
| Global AcademicYear + Cohort(level) | AcademicYear contains level; no Cohort |
| CourseOffering → Cohort | CourseOffering → AcademicYear |
| Enrollment → Cohort | Enrollment → AcademicYear, optionally CourseOffering |
| LessonVersion/QuizVersion/QuestionVersion | Existing Lesson/Quiz/Question attach directly to CourseOffering |
| Client-only LessonProgress | Verified unique-range LectureProgress tied directly to Lesson |
| AttendanceSession with status | AcademicYear schedule/holidays plus Entrance/Exit AttendanceRecord |
| Transcript by cohort/version | Report by Level/AcademicYear/CourseOffering/Quiz |
| Editing published content creates a version | Lock content with activity; duplicate into target offering |
| R2 academic-year/course/version path | level/academic-year/course/folder path |
| Teacher management role | Staff manages; moderator scans; teacher is removed |

### Deferred capabilities that remain compatible

These are not part of the currently approved implementation scope. If approved later, they must use the final relationships above:

1. Quiz special openings and multiple attempts.
   - QuizSpecialOpening points directly to Quiz.
   - Add QuizAttempt/AnswerSubmission only when multiple attempts are actually approved.
   - Existing Grade/Submission remain until then.
2. Persistent in-app notifications.
   - Target students through Enrollment → AcademicYear/CourseOffering.
3. Course discussions.
   - Discussion belongs to CourseOffering; no cohort/version reference.
4. Promotion/qualification.
   - Promote by creating Enrollment in the next level-specific AcademicYear.
   - Repeat failures use course-specific Enrollment.
5. Audit log.
   - Keep as a later cross-cutting security phase for grades, applications, attendance corrections, enrollments, and destructive actions.
6. Bulk enrollment import/export.
   - Rows identify Level + AcademicYear and optional CourseOffering.
7. Read-only admin view-as-student.
   - Resolve access through Enrollment and remain strictly non-mutating.
8. Generic calendar events and notifications.
   - CalendarEvent may point to AcademicYear and optional CourseOffering.
   - It does not replace the weekly schedule/holiday source used for absence.
9. Theme selection/showcase governance.
   - Continue only after production pages use one namespaced component system.

Deferred features must receive explicit product approval and new task cards before implementation. Their presence in the old roadmap is not authorization to build them now.

## 16. Parallelization Map

Parallel work is allowed only after dependencies land:

- P1-T05 and P1-T06 may run together after P1-T04.
- P3-T04 and P3-T07 may run together after validators/settings contracts are agreed.
- P3-T08 and P3-T09 may run together after application schema exists.
- P4-T04 and P4-T05 may run together only if they edit separate modules/functions.
- P5-T04 and P5-T06 may run together after models/helpers exist.
- P6-T08 documentation may run while Django tracking models are implemented.
- P6-T11 and P6-T12 may run together after progress API contract is stable.
- P7-T04 and P7-T05 may run together after shared aggregators are complete.
- P8-T02, P8-T03, and P8-T04 may run together if URLs/base-template ownership is assigned to one model.

Never parallelize:

- Schema migrations.
- Shared permission helpers.
- URL routing ownership.
- The progress credit algorithm.
- Final integration/release review.

## 17. DeepSeek Master Orchestration Prompt

Copy the prompt below into the primary DeepSeek orchestrator. Give it repository access and this plan file.

~~~text
You are the primary implementation orchestrator for an existing Django LMS.

Repository:
D:\Bible Institue\LMS\lms\bibleinstitute

Authoritative plan:
docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md

Your job is to implement the plan safely by delegating small, bounded tasks to child DeepSeek models, reviewing every returned change, integrating it, and running the required checks. You are accountable for correctness; child-model output is untrusted until reviewed.

Core architecture:
- Django 5.1 custom User model.
- Django templates, Bootstrap 5, HTMX/Alpine, Video.js.
- Cloudflare R2 and an HLS Worker.
- Level is an existing positive integer.
- AcademicYear belongs to one Level.
- There is no final Cohort, LessonVersion, QuizVersion, or QuestionVersion model.
- CourseOffering connects Course to AcademicYear.
- Existing Lesson and Quiz belong directly to CourseOffering; Question remains directly under Quiz.
- Enrollment connects Student to AcademicYear, optionally CourseOffering.
- Role is authorization only: Admin, Staff, Moderator, Student. Student level/year comes from Enrollment.
- Admin, Staff, and Moderator may scan attendance.
- Only Admin may correct/delete attendance and decide applications.
- Staff may create/read/update academic/content/grading data but may never delete.
- Moderator is scanner-only.

Non-negotiable behavior:
- Preserve unrelated dirty worktree changes.
- Never edit an applied migration; inspect migration state first.
- Replace staged 0019-0022 only after confirming they were never applied externally; otherwise use forward migrations.
- Preserve useful Phase 0 fixes while removing rejected Cohort/content-version WIP.
- Use Django/database authorization and constraints, not hidden buttons.
- Keep i18n and Arabic RTL.
- Do not introduce a SPA, CMS, Celery, Redis, or new abstractions.
- Only justified new dependencies are qrcode[pil] and openpyxl.
- Do not copy R2 objects during duplication.
- Never copy submissions, grades, attendance, or progress.
- Do not claim DRM-grade watch verification.
- Do not commit, push, deploy, or delete production data without explicit human approval.

Operating loop:
1. Read the full authoritative plan.
2. Inspect AGENTS.md if present, git status, project map, relevant code, tests, migrations, and every caller affected by the next task.
3. Start with Phase 0. Do not skip phase gates.
4. Maintain a task ledger containing task ID, state, delegate, changed files, checks, and review result.
5. Select only the next dependency-ready task.
6. Decide whether it is safe for a small child model:
   - Delegate isolated forms, templates, views, tests, exports, and pure helpers.
   - Keep schema decisions, migrations, shared permissions, progress trust/credit logic, and integration review under your direct control or the strongest model.
7. Create the child prompt using the Task Card Contract from the plan. Specify exact allowed files and acceptance checks.
8. Never assign overlapping files to concurrent children.
9. When a child returns:
   - Inspect the actual diff, not only its summary.
   - Check every caller and permission boundary.
   - Reject speculative abstractions or unrelated edits.
   - Run the focused check.
   - Repair or send one precise follow-up task if required.
10. Mark the task complete only after review and passing checks.
11. At each phase gate:
   - Run focused phase tests plus core smoke tests.
   - Review migrations, authorization, data integrity, i18n, and UI regressions.
   - Report completed tasks, files, checks, known risks, and the next phase.
12. Stop for human input when:
   - Migration deployment history is unknown and changes the safe strategy.
   - Existing dirty changes conflict with required edits.
   - Final Terms/public copy, Gmail secrets, or Worker source is required.
   - A destructive data action or deployment approval is needed.

Required child response format:
- Task ID and result.
- Concise implementation summary.
- Exact files changed.
- Tests/checks run with results.
- Assumptions.
- Risks/blockers.
- No commit.

Review standards:
- Root-cause change in the shared path, not repeated guards.
- Minimum files and code needed.
- Input validation at public/upload/Worker boundaries.
- Database uniqueness for phone, identity, attendance actions, and Worker receipts.
- transaction.atomic for activation, enrollment, duplication, and progress merges.
- select_related/prefetch_related where list/report queries need them.
- Formula-injection-safe CSV.
- Private R2 keys only, short-lived URLs after admin authorization.
- Direct URL permission tests for Admin, Staff, Moderator, and Student.
- One focused runnable test for every non-trivial branch/algorithm.

Begin by performing Phase 0 read-only discovery. Return:
1. Baseline git/migration/test state.
2. Whether migrations 0019-0022 were applied anywhere beyond the known local SQLite database, and the safe no-Cohort/no-content-version migration strategy.
3. The first three dependency-ready task cards.
4. Any blocker requiring human input.

Do not begin implementation until the migration strategy is explicit.
~~~

## 18. Reusable Child-Model Prompt

The orchestrator should fill this template for each delegated task:

~~~text
You are a child implementation model working on one bounded task in an existing Django LMS.

Task ID: [ID]
Objective: [one outcome]

Repository context:
- Existing Django 5.1 application.
- Preserve all unrelated worktree changes.
- Follow current project patterns and i18n.

Dependencies already completed:
[list]

Relevant current behavior:
[brief facts and exact files/functions]

Allowed files:
[exact list]

Do not touch:
[exact list, especially models/migrations/URLs when not assigned]

Implementation requirements:
[small numbered requirements]

Out of scope:
[explicit exclusions]

Acceptance checks:
[specific assertions]

Run:
[focused commands]

Return only:
1. Task ID and status.
2. Summary.
3. Files changed.
4. Checks and results.
5. Assumptions.
6. Risks/blockers.

Do not commit, push, deploy, rewrite unrelated code, or add dependencies.
~~~

## 19. Final Acceptance Checklist

The orchestrator may declare the project complete only when:

- No final Cohort or generic academic Version model/user-facing wording remains.
- No LessonVersion, QuizVersion, or QuestionVersion model/runtime path remains.
- Each Level can have its own 2025/2026, 2026/2027, and later AcademicYear.
- Only one current AcademicYear exists per level.
- Existing junior/senior users are migrated to student only after their Enrollment is verified.
- Applications are separate from Users and support individual/bulk decisions.
- Pending/declined students cannot log in.
- Private identity/payment files are admin-only.
- Teacher users are migrated to moderator.
- Staff cannot delete through UI or direct URLs.
- Admin, staff, and moderator can scan.
- Online students preview but create no attendance.
- Duplicate entrance/exit is idempotent.
- Calendar holidays do not count as absence.
- Content is tied to CourseOffering/AcademicYear.
- Published content with student activity is locked and copied rather than mutated.
- Duplication creates new DB records, shares R2 keys, starts draft, and copies no history.
- Shared referenced R2 media cannot be accidentally deleted.
- Online progress counts unique verified ranges across audio/video and completes at 80%.
- Summary CSV and three-sheet XLSX reconcile with source data.
- Public Home/About are anonymous and SEO-ready.
- Upload-to-lecture quick action works.
- English/Arabic, mobile, accessibility, permissions, migrations, and production checks pass.

## 20. Deferred Inputs

Implementation may begin without these, but the related phase cannot release until supplied:

- Final Terms and Conditions text.
- Gmail address and App Password in deployment secrets.
- Cloudflare Worker source/deployment process.
- Final Home/About copy, institute details, domain, logo, and social image.

Skipped intentionally: separate Level, Cohort, Version, CMS, SPA, queue, physical R2 duplication, Terms history, attendance for online students, and progress credit for downloaded audio.
