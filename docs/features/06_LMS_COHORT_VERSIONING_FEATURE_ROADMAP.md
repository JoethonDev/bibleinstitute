# LMS Academic Structure and Operations Roadmap — Alignment Record

**Status:** Merged and superseded as an implementation plan

**Aligned:** 14 July 2026

**Authoritative implementation plan:** ../LMS_EXPANSION_REQUIREMENTS_PLAN.md

## Purpose

This document records how the original cohort/content-versioning roadmap was reconciled with the approved LMS expansion requirements and the current staged code.

Do not implement tasks or model examples from an older revision of this file. Use docs/LMS_EXPANSION_REQUIREMENTS_PLAN.md for phases, task cards, delegation, tests, and acceptance criteria.

## Repository Finding

The current branch contains staged Phase 1/2 work that follows the old design:

- AcademicYear plus Cohort(level).
- CourseOffering linked to Cohort.
- Enrollment linked to Cohort.
- LessonVersion, QuizVersion, and QuestionVersion.
- Runtime helpers/views and tests for those version models.
- Migrations 0019 through 0022.

Inspection on 14 July 2026 established:

- Migrations 0019-0022 are staged additions.
- They are absent from git history.
- The local SQLite database has migrations only through 0016 and has none of the new tables.
- The WIP adds roughly 3,700 lines and is already partially integrated into helpers/views.

Decision:

- Confirm no external environment manually applied these uncommitted migrations.
- If confirmed, replace the WIP before commit instead of adding more migrations on top of a rejected architecture.
- Preserve useful Phase 0 access/security fixes.
- If any external environment did apply 0019-0022, stop and create forward migrations; never rewrite applied history.

## Final Shared Vision

### Academic structure

There is no Cohort or generic Version model.

~~~text
Level 1
  ├── AcademicYear 2025/2026
  └── AcademicYear 2026/2027

Level 2
  ├── AcademicYear 2025/2026
  └── AcademicYear 2026/2027
~~~

AcademicYear contains:

- level.
- name.
- starts_on.
- ends_on.
- is_current, unique per level.
- recurring meeting weekdays.

The same year name may exist once per level.

### Final relationships

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

Rules:

- CourseOffering connects Course directly to AcademicYear.
- Course.level must match AcademicYear.level.
- Normal Enrollment grants the student's level-specific AcademicYear.
- Repeat/remedial/manual Enrollment may target one CourseOffering.
- A second-year student keeps their historical first-year Enrollment; they do not automatically receive the new first-year offering.
- Lesson and Quiz attach directly to CourseOffering.
- Question remains directly under Quiz.

### Roles

Role controls authorization, not academic placement:

- admin.
- staff.
- moderator.
- student.

Migration:

- teacher becomes moderator.
- junior/senior determine initial Enrollment during backfill.
- after Enrollment is verified, junior/senior become student.
- no permanent role/Enrollment dual source of level.

### Content history without version tables

Do not create:

- LessonVersion.
- QuizVersion.
- QuestionVersion.

The Lesson/Quiz row inside a CourseOffering is already the academic-year-specific copy.

Historical safety:

- Draft content may be edited.
- Published content may be edited until student activity exists.
- Quiz with Submission/Grade is locked; duplicate it instead.
- Lesson with LectureProgress is locked; duplicate it instead.
- Duplication creates new database rows, starts draft, and reuses immutable R2 keys.
- Never copy submissions, grades, attendance, or progress.
- Shared R2 objects cannot be deleted while referenced.

Existing Grade and Submission remain. Add QuizAttempt/AnswerSubmission only if a later approved requirement needs multiple attempts.

### Attendance

The old AttendanceSession/status proposal is rejected for the approved requirement.

Use:

- AcademicYear start/end dates.
- recurring Sunday/Tuesday defaults.
- AcademicHoliday exclusions.
- one Entrance and one Exit AttendanceRecord per offline student/date.
- Valid = both; Invalid = one; Absent = neither.

Admin, staff, and moderator may scan.

- Admin can correct/delete.
- Staff can scan/view reports and create/update academic calendar data but cannot delete.
- Moderator is scanner-only.
- Online students can be previewed but no attendance is recorded.

### Online progress

The old client-only watched-seconds model is replaced.

LectureProgress attaches directly to Lesson and stable media part ID.

Credit requires both:

1. Player-reported played ranges.
2. Cloudflare Worker proof that matching HLS segments were served for the same signed session.

Audio/video alternatives share one timeline. Replayed ranges count once. Completion is 80%. Downloads do not add progress.

### Reports

Filter by:

- Level.
- AcademicYear.
- optional CourseOffering/student/study mode.

Do not filter by cohort/version.

Outputs:

- Summary CSV.
- Detailed XLSX with Grades, Attendance Summary, and Attendance Daily.

### Calendar

The attendance calendar source is:

- AcademicYear dates/weekdays.
- AcademicHoliday.

A future generic CalendarEvent may target AcademicYear and optional CourseOffering for exams/deadlines/announcements. It must not replace the attendance denominator.

Admin and staff may create/update academic calendar data; only admin may delete.

### Upload/storage paths

Do not use cohort/version path segments.

New organized R2 paths use:

~~~text
level/academic-year/course/folder
~~~

Existing duplicated content continues to share current R2 object keys.

## Contradiction Resolution Table

| Old roadmap | Final decision |
|---|---|
| Global AcademicYear + Cohort(level) | AcademicYear contains level |
| One global current AcademicYear | One current AcademicYear per level |
| CourseOffering → Cohort | CourseOffering → AcademicYear |
| Enrollment → Cohort | Enrollment → AcademicYear |
| Junior/senior role is student level | Enrollment is student level/year; role becomes student |
| LessonVersion/QuizVersion/QuestionVersion | Direct Lesson/Quiz/Question records |
| Edit published content by creating version wrapper | Lock used content; duplicate direct row |
| Progress per LessonVersion, client-reported seconds | Verified unique ranges per Lesson |
| AttendanceSession with status | Academic schedule plus Entrance/Exit |
| Teacher/admin management | Staff/admin management; moderator scanning |
| Cohort/version transcript | Level/AcademicYear report |
| R2 year/course/version path | level/year/course/folder path |
| Calendar scoped to Cohort | AcademicYear and optional CourseOffering |

## Original Roadmap Capability Mapping

### Included in the authoritative plan

- Baseline/access stabilization.
- Explicit academic structure and enrollments.
- Academic-year-specific content.
- Draft/publish/archive workflow.
- Safe content duplication.
- Verified lecture progress.
- QR Entrance/Exit attendance.
- Attendance/grade reports.
- Public/SEO pages.
- UI/theme consolidation.
- Upload-to-lecture quick action.
- Release/security/migration review.

### Deferred but still compatible

These require explicit product approval and new task cards:

1. Quiz special openings and multiple attempts.
2. Persistent notifications.
3. CourseOffering discussions.
4. Promotion/qualification.
5. Full audit log.
6. Bulk Enrollment import/export.
7. Read-only admin view-as-student.
8. Generic CalendarEvent and notifications.
9. Per-user theme selection/showcase governance.

When implemented, each deferred feature must use AcademicYear(level), CourseOffering, Enrollment, direct Lesson/Quiz, and the final roles. It must not reintroduce Cohort or content-version tables.

## Obsolete Supporting Documents

These files describe the abandoned WIP and are historical only:

- 07_PHASE_1_ACADEMIC_YEAR_COHORT_ENROLLMENT_COURSE_OFFERING_STANDALONE_PLAN.md
- 08_PHASE_1_IMPLEMENTATION_REVIEW.md
- 09_PHASE_2_CONTENT_VERSIONING_STANDALONE_PLAN.md
- 10_PHASE_2_IMPLEMENTATION_REVIEW.md

They must not be used as implementation prompts.

## Immediate Next Step

Run Phase 0 from the authoritative plan:

1. Confirm 0019-0022 were never manually applied outside the inspected local database.
2. Preserve unrelated and Phase 0 fixes.
3. Remove/replace the unpublished Cohort and content-version WIP.
4. Implement AcademicYear(level), CourseOffering(academic_year), Enrollment(academic_year), and direct Lesson/Quiz offering fields.
5. Backfill and verify access equivalence before continuing.
