# Project memory - architecture and navigation map

Structural map of the current Django repository, reviewed 5 November 2026.
This answers **where a feature lives, what models mean, and what to reuse before
implementing**. Open the relevant owner and callers instead of rescanning the
whole app. Source remains authoritative when a map entry drifts.

Read [AGENTS.md](AGENTS.md) for mandatory rules, [CODING_GUIDE.md](CODING_GUIDE.md)
for the feature/bug workflow, [current handoff](IMPLEMENTATION_MEMORY.md) for
status and [active work](docs/current/README.md) for unimplemented tasks. Historical
session logs and old proposals are in [the archive](docs/archive/README.md).

## 1. Project boundaries

- Server-rendered Bible Institute LMS: Django 5.1 target, PostgreSQL authority,
  Bootstrap/Django templates, HTMX, Alpine and existing media/PDF integrations.
- Arabic/English and RTL/LTR; approved default is Arabic until explicit English
  per browser/device. Some current English fallbacks still need remediation.
- Target approximately 10,000 students, three ordered levels and hundreds of
  lessons/quizzes per year. Preserve bounded page/batch behavior.
- SQLite contains real migration-source data, not the production behavior
  reference. Never recreate it. Applied database state is not implied by this map.
- Celery/Redis provide existing quiz/media/gallery/delivery tasks. Cloudflare R2
  stores media/private artifacts; worker.js participates in protected playback.
- This checkout provides mobile APIs; native student/admin client UI is outside
  this server repository. prototype/admin-redesign is a standalone study, not
  deployed Django UI and not authority to redesign the app.

## 2. Directory map

| Path | Purpose / first place to inspect |
|---|---|
| [learning_platform](learning_platform) | Settings, root routing, middleware registration, Celery startup/routes/Beat, WSGI/ASGI. |
| [management_system/models.py](management_system/models.py) | All current model definitions; source catalog below. |
| [management_system/views.py](management_system/views.py) | Main web endpoints, forms orchestration, student/media/attendance/report adapters. Keep intact except approved domain extraction. |
| [management_system/urls.py](management_system/urls.py) | Main web route-to-handler map. |
| [management_system/forms.py](management_system/forms.py) | Signup/profile/content/academic forms and validation adapters. |
| [management_system/admin.py](management_system/admin.py) | Django-admin registration, role gates and list query behavior. |
| [management_system/academic_access.py](management_system/academic_access.py) | Read/write offering eligibility and canonical enrollment predicates. |
| management_system/academic_*.py | Enrollment/decisions, copy, formula normalization and academic evaluation/promotion. See reuse map. |
| [management_system/utils](management_system/utils) | Validators, attendance/report/query/localization/storage helpers. The adjacent all-comment utils.py is not this active package. |
| [management_system/mobile_api.py](management_system/mobile_api.py) | Mobile endpoint adapters; mobile_urls.py maps their routes. |
| [management_system/telegram](management_system/telegram) | Telegram configuration/linking/policy/recipients/support/broadcast/delivery internals. |
| management_system/*_tasks.py; tasks.py | Specialized task entrypoints, retries/recovery and delivery. Domain logic stays in owning functions. |
| [management_system/templates](management_system/templates) | Full shells/pages, partials/components and preserved theme_showcase references. |
| [management_system/static](management_system/static) | Existing CSS system, JS/Alpine components/stores and vendored assets. |
| [management_system/migrations](management_system/migrations) | Historical schema/data transitions. Inspect current graph and DB applied state before adding a forward migration. |
| [management_system/management/commands](management_system/management/commands) | Academic/image audits, import/fixture/bootstrap commands. Not all old scripts are safe on real data. |
| [management_system/scripts](management_system/scripts) | Legacy manual utilities; caller/data-safety inspection required before use. |
| [locale](locale) | Arabic/English PO/MO catalogs; maintain translated messages and catalog consistency. |
| [worker.js](worker.js) | Cloudflare playback/public-resource/signature/receipt boundary. Keep public/ namespace and Django authorization distinct. |
| compose.*.yml; Dockerfile; [deploy](deploy); [nginx](nginx) | Local/deployment topology, queues, healthchecks, proxy/TLS/runtime includes. Never infer production status from config files. |
| [docs/current](docs/current) | Only active approved work and current release gates. |
| [docs/pending](docs/pending) | Preserved unfinished/unverified external-client/operator/UI plans; explicit status index. |
| [docs/reference](docs/reference) | Focused current-use contracts and source catalogs, consulted as needed. |
| [docs/archive](docs/archive) | Completed/superseded plans, old reports and full session history. Not startup instructions. |

## 3. Academic model meaning

The complete [model catalog](docs/reference/MODEL_MAP.md) covers all 61 current
model classes, declared fields, 121 relationships, enums and named constraints/
indexes without reading a database. Use it for exact field navigation; inspect
models.py clean/save/constraints and the domain owner before writing.

```mermaid
flowchart LR
  Y[AcademicYear] --> S[AcademicYearLevel]
  L[Level] --> S
  L --> C[Course]
  S --> O[CourseOffering]
  C --> O
  U[User] --> E[Enrollment]
  E --> S
  E -. targeted .-> O
  O --> LE[Lesson]
  O --> Q[Quiz]
  Q --> QU[Question]
  U --> G[Grade / QuizAnswerSnapshot]
  Q --> G
  QU --> SU[Submission]
  U --> SU
  S --> F[PromotionFormula / Rule]
  E --> R[EvaluationResult]
  F --> R
  R --> H[PromotionHistory]
```

Arrows show ownership/reference direction, not permission by themselves.

| Model | Definition / important boundary |
|---|---|
| Role / User | Roles admin/staff/moderator/student are capability categories, not academic levels. User extends AbstractUser with application/identity/private-file/study-mode/timezone fields; CanonicalUserManager owns natural-key username normalization. |
| Level | Ordered reusable academic level with Arabic/English names; ordering is unique. |
| Course | Catalog item attached to Level; name+level unique. instructor is text, not a User FK. A catalog course is not a particular year's delivered content. |
| AcademicYear | Named/ordered date range; at most one active year; optional graduation level. Levels are a through relationship. |
| AcademicYearLevel | AcademicYear+Level scope, unique pair, with locked meeting weekdays. Most placement/calendar/formula/payment work is scoped here. |
| CourseOffering | Course delivered inside one AcademicYearLevel; owns publication state and content. Check catalog level matches scope; do not infer a year from Course alone. |
| Enrollment | User student + scope, with optional offering. Normal grants a full scope and requires no offering; repeat/remedial/manual target an offering. Status active/inactive/completed/withdrawn. Type/target and uniqueness constraints matter. |
| Lesson | Belongs to offering with draft/published/archived state; links is encoded text describing media entries/part IDs. Reuse current parsing/storage logic, not a new schema assumption. |
| Quiz / QuizType | Quiz belongs to offering, optional type, publication state, opening/closing windows and total. QuizStudentOpening supplies a per-student exceptional window. |
| Question | Quiz question type/text/grade/answer/configuration and serializers/grading helpers. `quiz_questions.py` owns normalized validation for manual create/update and JSON import. Student-safe serialization must preserve answer secrecy. |
| Submission | User+question answer/grade/is_graded; question can become null. It is not a FK to Grade. Structured answers are represented through the existing payload helpers. |
| Grade | Final total per user+quiz, unique pair; do not create duplicate finals on retries. |
| QuizAnswerSnapshot | Unique quiz+user normalized answer snapshot/revision for autosave/recovery; not the final grade. |
| QuizOperationJob | Existing durable quiz recalculation/reconciliation job, selection/cursor/counters/lease/status/language. Not a general admin-operation schema. |
| PromotionFormula / PromotionRule | Formula scoped to year-level, optional specific offering; weighted quiz/attendance rules, date window and thresholds. Shared validation/evaluation own mode applicability. |
| EvaluationResult | Formula+enrollment, optional offering for aggregate; computed versus final status/score, snapshots and override metadata. Course and aggregate uniqueness differ. |
| HistoricalAcademicSummary | Reviewed imported student+scope outcome when no LMS quiz history exists; source_key and student+scope uniqueness; review/progression metadata. |
| PromotionHistory | Protected immutable transition history with exactly one result or historical-summary source, source/destination enrollment/scope, actor and snapshots. Historical manual transitions need a reason. |
| AcademicPayment | Student+year-level receipt, unique pair; private receipt/preview keys. Not a generic billing/order system. |
| AcademicYearLevelMeeting / AcademicHoliday | Scope/date meeting points at an offering; holidays belong to an academic year. Calendar and attendance validity use scope weekdays/year bounds. |
| AttendancePolicy / AttendanceRecord | Central timing/classification policy; student/offering/date entrance/exit with scan/auto/manual source and correction metadata. Unassigned legacy rows can have no offering. |

### Supporting model domains

Use the model catalog for exact fields/relationships; these groups locate the
relevant records without creating another ORM layer:

- Playback: ViewingSession, LectureProgressEvent, VerifiedSegmentRequest,
  LectureProgress, LectureWatchSession, LectureWatchDay. Coverage/completion and
  active watch time are separate facts; do not replace one with the other.
- Applications/study mode: SignupSettings, OfflineCity, user application fields
  and MigrationReviewItem. Missing/ambiguous legacy mapping is reviewed, not guessed.
- Mobile identity/delivery: StudentMobileSession, MobileBiometricCredential,
  MobileOtpChallenge, MobilePushDevice and PushDelivery.
- In-app/Telegram messages: Announcement, StudentNotification, TelegramBotConfig,
  TelegramAccount, TelegramLinkToken, TelegramWebhookUpdate,
  TelegramNotificationDelivery, TelegramConversation, TelegramMessage,
  TelegramAttachment, TelegramBroadcast, TelegramBroadcastAttachment,
  TelegramBroadcastRecipient.
- Durable admin operations: AdminOperationJob ledger for formula recalculation, promotion,
  historical intake, legacy CSV, exports, course copy, attendance reconcile and storage work;
  QuizOperationJob/media jobs remain canonical for their domains.
- Media/gallery/site: MediaProcessingJob, GraduationGalleryJob/Item,
  MobileAppRelease, MobileAppUpdatePolicy, MobileDownloadPolicy and StudentBanner.

## 4. Reuse before implementation

| Responsibility | Existing owner / useful entrypoints | Inspect with it |
|---|---|---|
| Roles/capabilities | [utils/decorators.py](management_system/utils/decorators.py): can_manage_*, can_view_reports, capability_required | View/admin/API/worker boundary and object relationship checks; role is not object authorization. |
| Offering access | [academic_access.py](management_system/academic_access.py): readable_offering_access, accessible_offerings, user_can_read_offering, user_can_write_offering_activity, get_accessible_offering_or_403 | Lists/detail/media/quiz/progress/recipients; readable active/completed history differs from active writable enrollment. |
| Count/navigation reads | [course_content.py](management_system/course_content.py): with_offering_content_counts, course_navigation | Already-authorized offering input; optional draft flags; card/sidebar fields. Helpers do not independently authorize arbitrary IDs. |
| Student response data | [utils/student_data.py](management_system/utils/student_data.py): student_courses_data, student_course_data, student_quiz_data, student_progress_data, student_next_actions | Web/mobile adapters, pagination, safe quiz payloads and optional sections. Do not build a read DTO just to execute a command. |
| Management student overview | [student_overview.py](management_system/student_overview.py): build_student_overview | Profile/overview templates, current access labels and count helper. |
| Application/enrollment transitions | [academic_enrollment.py](management_system/academic_enrollment.py): set_application_status, bulk_set_application_status, set_user_normal_enrollment_scope, activate_academic_year | Locks, forms, application review/bulk callers, payments and delivery callbacks. |
| Historical intake/promotion | [historical_intake.py](management_system/historical_intake.py), academic_enrollment.py: promote_historical_summary/promote_evaluation_result(s), is_last_academic_level | Preview/apply normalization, source_key idempotency, destination scope, failed-course access and immutable histories. One canonical find/create action plan and one last-level rule are shared by preview and apply. Uploaded intake rows are validated in bounded pages and applied per row by the `HISTORICAL_INTAKE` job executor. |
| Legacy user CSV | [legacy_user_import.py](management_system/legacy_user_import.py): validate_legacy_import_rows, enqueue_legacy_user_import, execute_legacy_user_import | Whole-file all-or-nothing import on the J01 ledger; bounded private artifact, pre-enqueue validation without hashing, then hashing/apply on the admin worker. Deliberately separate from historical intake and its per-row contract. |
| Formula/evaluation | [academic_formula.py](management_system/academic_formula.py): normalize/validate/save; [academic_evaluation.py](management_system/academic_evaluation.py): build_evaluation_plan, evaluation_enrollments, evaluate_enrollment_page, save_formula_and_enqueue, execute_formula_recalculation | Preview/pagination/save/export/promotion; overrides and mode weights. Saving queues a scope-wide J01 recalculation with token-fenced per-batch commits; retry preserves actual timestamped overrides only. Scope locking excludes override writes during active calculation; promotion is blocked while the latest calculation is incomplete. |
| Content copying | [academic_copy.py](management_system/academic_copy.py): copy_offerings, duplicate_offering, execute_course_copy | duplicate_course/lesson/quiz adapters, source offering, content flags, draft state and transaction contract. Full offering/scope copies run as `COURSE_COPY` jobs with one all-or-nothing unit; one lesson/quiz duplicate stays direct, and course duplication requires an explicit source offering. |
| Quiz questions/import | [quiz_questions.py](management_system/quiz_questions.py), [quiz_import.py](management_system/quiz_import.py) | Manual create/update, JSON import/manual, bulk write validation and input limits. Existing builder is not proof A18/A19 are fixed. |
| Quiz attempts/grading | [quiz_attempts.py](management_system/quiz_attempts.py): normalize_answers, save_snapshot, submit_answers, reconcile_snapshots; [quiz_grading.py](management_system/quiz_grading.py) | Student exam template/mobile submit/tasks; [utils/quiz_access.py](management_system/utils/quiz_access.py) for windows, exceptional openings and answer release. |
| Attendance/calendar | [utils/attendance.py](management_system/utils/attendance.py): grade_attendance_day, pair/merge_attendance_pairs, attendance_scores_for_students, reconcile_missing_exits, get_expected_dates | Calendar/scan/correction web views, attendance_matrix.py and attendance_tasks.py. Missing-exit reconciliation is idempotent keyset batches; manual population runs use the `ATTENDANCE_RECONCILE` job while the scheduled task keeps calling the same operation. |
| Reports/matrices | [utils/reports.py](management_system/utils/reports.py): report_enrollments, load_report_facts, iter_report_batches, build_report_page_rows, iter_report_data; grade_matrix.py/attendance_matrix.py/payment_matrix.py; report_export.py three-sheet workbook; evaluation_export.py saved-result export | HTML/CSV/XLSX and evaluation job/artifact; shared facts/rounding/filter semantics, bounded output and xlsx_export.py row rollover. |
| Private uploads/replacement | [utils/application_uploads.py](management_system/utils/application_uploads.py), utils/storage_operations.py | Signup, profile, UpdateUser, application_review, missing-document and payment-receipt writes reserve `applications/<user_id>` before taking user/payment row locks; document references swap transactionally and replaced objects clean up after commit. Generic storage rename/delete must use the same path-lock-before-row-update order. |
| Media/storage ownership | [media_storage.py](management_system/media_storage.py), [storage_ownership.py](management_system/storage_ownership.py), [storage_coordination.py](management_system/storage_coordination.py), utils/r2_references.py, utils/r2_manager.py, storage_jobs.py | `storage_ownership`: one key/prefix rule for generic R2 list/search/sign/move/rename/delete (private `applications/` stays admin-only and transitions remain within the same owner); `storage_coordination` uses shared ancestor/exclusive exact-path transaction locks for direct writes and durable storage/media/gallery reservations, plus a separate active-job-cap lock. Folder rename and recursive delete run as bounded STORAGE_RENAME/STORAGE_DELETE jobs with committed cursors; rename updates lesson, application/payment, gallery, app-release and active viewing references, and delete keeps referenced objects. Gallery upload authorization reserves its staging/output namespaces before creating a job. Incomplete rename restores originals before retaining old references. STORAGE_USAGE_REFRESH owns provider statistics work; dashboard/API GET reads timestamped shared job snapshots and filters private samples, while CSRF POST queues refresh. Media storage stays exact-key. |
| Media conversion | [media_processing.py](management_system/media_processing.py), media_tasks.py, automation_api.py | MediaProcessingJob, browser media job routes, private staging, FFmpeg queue/recovery and automation contract. |
| Notification facts | [student_notifications.py](management_system/student_notifications.py), announcements.py, mobile_push.py | Channel-specific task delivery; enrollment predicate may be shared, channel policy/rechecks must remain distinct. |
| Telegram | [telegram/recipients.py](management_system/telegram/recipients.py), policy.py, linking.py, support.py, broadcasts.py, notifications.py | telegram_views.py/forms/tasks; configuration, attachments/media and independent webhook/delivery trust boundaries. |
| Input identity | [utils/validators.py](management_system/utils/validators.py), [managers.py](management_system/managers.py) | Signup/profile/admin forms, canonical usernames, phone/identity normalization and unique constraints. |
| Study mode/payment | [study_mode.py](management_system/study_mode.py), [payments.py](management_system/payments.py) | OfflineCity/user override, AcademicPayment and attendance/evaluation eligibility. |
| Management selections | [admin_tables.py](management_system/admin_tables.py): scope_queryset, create/load_selection_token | Signed actor/view/filter/exclusions, strict boolean parsing and delete endpoints; the loader owns view/actor binding. Durable quiz selections consume the same contract. |
| Durable management jobs | [admin_operations.py](management_system/admin_operations.py), admin_operation_tasks.py, admin_operation_views.py | AdminOperationJob ledger: savepoint-safe creation with frozen-input equivalence, fresh active actor/capability checks, lease claim/takeover and task heartbeat, token-fenced progress/terminal commits, owner-bound status with shared calculation visibility, bounded private input/export artifacts (XLSX/CSV) with TTL cleanup and owner/admin streaming download, and the dedicated `admin` queue. Operation executors stay in their owning domain modules. |
| Background jobs and notification history | [job_monitor.py](management_system/job_monitor.py), [job_monitor_views.py](management_system/job_monitor_views.py), [notification_history.py](management_system/notification_history.py), background_jobs.html and backgroundJobsMonitor.js | Admin-only SQL-paged history for management, quiz, media, gallery, announcement and confirmed Telegram batches, plus per-recipient StudentNotification inbox/push/Telegram attempts. Poll only visible IDs in bounded batches; shared pause/resume preference; explicit server-localized toggle labels. Saved Arabic/English content and exact Telegram snapshots are shown without provider credentials/IDs. |
| Notification retry/redelivery | [notification_redelivery.py](management_system/notification_redelivery.py), student_notifications.py, mobile_push.py, telegram/notifications.py and telegram/broadcasts.py | AdminOperationJob-backed failed-only or resend-all copies; freeze the original audience, serialize overlapping event retries, and share bounded StudentNotification row locks with normal channel materializers so resend-all cannot race initial fan-out. Recheck actor/source/recipient eligibility at dispatch and preserve original attempts. Failed-only retries latest failed channel attempts without an inbox copy; resend-all makes a new visible inbox copy. Expo receipt and Telegram outcomes determine completion. Broadcast retries point to the original root and compare latest recipient outcomes. Missing historical Telegram text snapshots are rebuilt through the normal channel formatter; existing provider workers and inbox materialization remain the delivery owners. |
| Web page/fragment adapter | [utils/helpers.py](management_system/utils/helpers.py): render_page, hx_target_id, paginate_obj, render_dashboard | Base/admin/course shells, partial roots, message toasts, context_processors.py and HTMX callers. |

For complete function/class navigation consult [MODULE_MAP.md](docs/reference/MODULE_MAP.md).
It is an index of source declarations, not a guarantee each legacy helper is
approved or efficient. Prefer the owners above; inspect callers before reuse.

## 5. Feature entrypoints and flow

| Work | Start here | Then follow |
|---|---|---|
| Public/signup/login/profile | views.py, forms.py, urls.py; password_reset.py; public_content.py | SignupSettings/User, application transitions/private uploads, login/base/public templates. |
| Academic management/promotion | views.py promotion_formula/bulk_promotion_results/calendar handlers | academic_* domain modules, scope forms/templates, saved result/export consumers; bulk promotion preloads PromotionFacts per chunk and keeps per-student locks. |
| Student course/quiz | views.py course/lesson/take_exam handlers; utils/student_data.py | academic_access -> content/attempt operation -> course_detail/display_quiz/questions partials; mobile readers share data/domain functions. |
| Mobile API | learning_platform/urls.py -> mobile_urls.py -> mobile_api.py | mobile_auth.py, mobile_http.py, mobile_otp.py/tasks, mobile_versioning.py, mobile_downloads.py, localization helper. |
| Protected playback/progress | views.py start_viewing_session/lesson_manifest/progress_heartbeat; mobile_api.py media/progress endpoints; worker.js | media_delivery.py owns media link/session/token/manifest/audio/book resolution; progress_activity.apply_progress_heartbeat owns merged progress/events; web and mobile keep their own transport auth/HTTP formatting. |
| Media management/automation | views.py media_job_*; automation_urls.py/automation_api.py | media_processing/storage/tasks and MediaProcessingJob; client reference manual. |
| Attendance | views.py scanner/scan_preview/record_attendance/correction | capability, token, meeting scope, utils.attendance, policy and schedule. |
| Reporting/exports | views.py export_* / report dashboard | utils.reports, report_export.py, xlsx_export.py and matrix modules; whole-scope evaluation and report workbook exports run on the J01 ledger with owner/admin private-artifact downloads, while CSV stays a bounded streaming response. |
| Announcements/Telegram/push | notification_views.py, telegram_views.py; gallery_views.py for gallery | student_notifications.py and announcement fan-out; notification_history.py for saved per-recipient content/outcomes; notification_redelivery.py for admin retry/resend ledger work; telegram/broadcasts.py and channel-specific provider tasks. Recheck source/recipient eligibility at enqueue and delayed provider boundaries. |
| App releases/banners | portal_views.py | MobileAppRelease/UpdatePolicy/DownloadPolicy/StudentBanner, portal templates, API version/download response contracts. |

Root routes in [learning_platform/urls.py](learning_platform/urls.py): localized
web routes include management_system.urls; /admin/ is Django admin;
/api/mobile/v1/ is mobile; /api/automation/ is automation; Telegram webhook and
i18n/jsi18n are separate. Consult actual route modules for exact names/payloads.

## 6. Frontend locations and contracts

- Shell/page/partial ownership lives under templates. admin_panel.html owns
  management runtimes; course_detail.html owns the learning shell and lesson
  destination; display_quiz.html/questions_rendering.html own the student exam.
  Keep the authoring quiz_form.html/quizForm.js workflow separate.
- Static CSS: app.css, app_admin.css, app_student.css, psalmodia.css,
  public_site.css and reference design styles. Repeated layers are intentional
  unless an exact stale override is proved; no mass cascade consolidation.
- Alpine stores/components under static/js/alpine; admin_shell.js registers the
  management components. HTMX navigation can need components loaded before a
  destination exists. Do not strip all global scripts as unused.
- Shared partials/components hold table/pagination/filter/form/toast primitives.
  Match root IDs, targets, swap behavior, Alpine teardown and bound errors before
  extracting a common component. Do not invent a new generic frontend loader.
- Keep stable media part IDs, form field names and public static URLs unless
  the approved task updates all consumers. Vendor assets are not first-party
  cleanup candidates. Prototype source/builders remain separate references.

## 7. Runtime boundaries

This map describes current owners and contracts; release state belongs in the
[implementation handoff](IMPLEMENTATION_MEMORY.md), approved
[remediation checklist](docs/current/REMEDIATION_CHECKLIST.md) and
[completion report](docs/current/REMEDIATION_COMPLETION_REPORT.md).

- `AdminOperationJob` owns durable management operations and notification
  redelivery. Quiz, media, gallery and channel-delivery jobs remain specialized
  owners; do not add a second path for a domain that already has one.
- Population-sized changes use the owning durable job. Preserve each operation's
  established transaction, partial-progress and recovery contract.
- `utils/localization.py`, `middleware.py` and `utils/client_identity.py` own the
  Arabic default/explicit English, session renewal and trusted client identity
  rules. Do not add request-local copies of those policies.
- Academic access, formula/promotion, question validation, signed selections
  and private storage ownership each have a shared owner listed above. Route
  every related caller through that owner.
- Storage writers and generic R2 rename/delete acquire the shared path lock
  before database row locks, and hold the namespace reservation through the
  object write plus reference commit. Keep gallery staging/public and APK
  release writes on the same coordinated path; avoid global locks on unrelated
  objects.
- Application unit-test modules were removed under the approved policy. Use the
  retained audit, browser, PostgreSQL and worker checks documented in the
  checklist and current handoff; a removed test is not verification.

## 8. Maintaining this map

Update the affected section in place when a model/relationship, domain owner,
public API/route family, shared operation or structural dependency changes.
Regenerate/review model/module reference catalogs after corresponding structural
changes. Remove stale entries when old paths disappear.

Do not append session logs, button-level changes, color changes, test results,
transient deployment state or an endless "new updates" list here. Put current
execution evidence/blockers in the compact handoff and completed history in the
archive. A new button only belongs in this map if it introduces a new workflow,
API boundary or reusable owner that agents need to locate.
