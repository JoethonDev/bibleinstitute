# Current implementation handoff

Read [AGENTS.md](AGENTS.md), [CODING_GUIDE.md](CODING_GUIDE.md) and
[project_memory.md](project_memory.md). The
[approved checklist](docs/current/REMEDIATION_CHECKLIST.md) owns cleanup policy.
Earlier detailed evidence is preserved in the
[pre-correction handoff](docs/archive/2026-11-06/history/IMPLEMENTATION_MEMORY_BEFORE_REVIEW_FIXES.md)
and the [5 November archive](docs/archive/2026-11-05/history/IMPLEMENTATION_MEMORY.md).
Historical open/done statements there are superseded by the current disposition.

## Student UI course content cards and full-page lesson navigation

Reported:
- The new student UI (`student_next`) needed separate card containers for lectures/lessons and quizzes (matching the rounded inner row cards and pill badges in the prototype screenshot).
- Pressing a lesson on the course page had to open a dedicated full page (`student_next/lesson.html`) instead of swapping inline into `#display-page`.

Changes and decisions:
- `management_system/student_experience/course.py`: added `_lesson_media_summary()` (loaded `links` via `.only(..., "links")` with zero extra queries) and one bounded `Question` count query (`question_count`) for the visible `<= 20` quizzes.
- `management_system/templates/student_next/partials/course_workspace.html`, `partials/ring.html`: split the single course-content section into two `<section class="sn-card sn-content-card">` containers (Lectures & lessons, Quizzes) with `.sn-section-head` and `.sn-card-body > .sn-content-list`, replaced `.sn-content-label` + `.sn-content-go` with `.sn-pill` status badges, added media summary / question counts, removed inline `#display-page`, changed lesson links to standard full-page `<a href="...">` navigation without `hx-get`/`hx-target="#display-page"`, hid `.sn-course-hero-progress` under `management_preview`, and handled `value=None` in `ring.html` without rendering a bare `%`.
- `management_system/templates/student_next/course.html`, `lesson.html`, `partials/lesson_body.html`, `views.py`: removed `lesson_runtime.html` from `course.html`, rendered `lesson.description` in `lesson.html`, removed the unused `show_nav` / `HX-Target == "display-page"` -> `lesson_content.html` branch in `views.py::view_lesson_details`, and deleted obsolete `student_next/partials/lesson_content.html`. Legacy `course_detail.html` and `lesson_stream.html` remain untouched.
- `management_system/static/css/student_next.css`: styled `.sn-content-card`, `.sn-content-row` rounded cards, `.sn-content-state` icon boxes, mobile pill layout, and `.sn-reading + .sn-lesson-parts` spacing; removed obsolete `.sn-content-group*`, `.sn-content-label`, `.sn-content-go`, `.sn-content-empty-hint`, `.sn-lesson-content`, `.sn-lesson-navigation`, and `.sn-course-page .sn-content-panel` rules.
- `locale/ar/LC_MESSAGES/django.po`, `django.mo`: added `"Lectures and lessons"` -> `"المحاضرات والدروس"` and compiled with `compilemessages -l ar`.
- Verification (PostgreSQL unavailable on `localhost:5432`, so ORM execution against live PostgreSQL was not run):
  - `python manage.py compilemessages -l ar`, `python manage.py audit_text_encoding` (843 text files), `python manage.py check` (0 issues), `python manage.py makemigrations --check --dry-run` (No changes detected), and `python -m compileall management_system/student_experience management_system/views.py` all passed.
  - Focused verification script (`_lesson_media_summary`, EN/AR template rendering of `student_next/course.html` and `student_next/lesson.html`, `TemplateDoesNotExist` check on `lesson_content.html`, legacy template loading, and mocked `view_lesson_details` dispatch under `HX-Target=body#display-page`) exited 0 (`VERIFICATION_ALL_PASSED`).

## Self-hosted Bootstrap and Font Awesome vendor assets

Reported:
- Every external/online frontend script, stylesheet, and font request needed to be downloaded, served from the server, and cached cleanly.

Changes and decisions:
- Audited `management_system/templates/`, `management_system/static/`, `learning_platform/`, `nginx/`, and `worker.js`: the only remaining online frontend script/stylesheet/font dependencies were Bootstrap 5.3.3 (`cdn.jsdelivr.net`) and Font Awesome 6.7.2 (`cdnjs.cloudflare.com`) in `management_system/templates/base.html` and `management_system/templates/admin_panel.html` (`htmx`, `alpine`, `chartjs`, `jsqr`, `pdfjs`, and `videojs` were already vendored locally).
- Vendored SRI-verified upstream assets under `management_system/static/vendor/`:
  - `vendor/bootstrap/5.3.3/css/bootstrap.rtl.min.css` (232,911 bytes), `vendor/bootstrap/5.3.3/css/bootstrap.min.css` (232,803 bytes), `vendor/bootstrap/5.3.3/js/bootstrap.bundle.min.js` (80,721 bytes).
  - `vendor/fontawesome/6.7.2/css/all.min.css` (73,890 bytes) and all 8 `webfonts/fa-*.{ttf,woff2}` files (`fa-brands-400`, `fa-regular-400`, `fa-solid-900`, `fa-v4compatibility`).
- Replaced CDN `<link>`/`<script>` tags in `management_system/templates/base.html` and `management_system/templates/admin_panel.html` with `{% static 'vendor/...' %}?v={{ static_asset_version }}` and dropped `integrity`/`crossorigin`/`referrerpolicy`. Versioned `?v=` URLs receive `expires 7d` + `Cache-Control: public, immutable` in `nginx/default.conf`, while unversioned `../webfonts/*` font fetches from `all.min.css` receive `expires 7d` + `public, max-age=0, must-revalidate`; `service_worker.js` also caches `/static/` on demand for PWA clients.
- Verification (local Django / staticfiles checks; no live Nginx/Docker container run here):
  - `python manage.py findstatic ...` resolved all 12 new vendored files under `management_system/static/vendor/`.
  - `git grep` confirmed zero remaining `cdn.jsdelivr`/`cdnjs.cloudflare`/`unpkg.com`/`fonts.googleapis`/`fonts.gstatic` references or external `<script src>` / `<link rel="stylesheet">` tags in `management_system/templates/`.
  - Rendered `base.html` and `admin_panel.html` in both `ar` (RTL) and `en` (LTR) and served all 12 files through `django.contrib.staticfiles.views.serve` (HTTP 200, `?v=18e7acdebb9c8dd4-45`, zero external scripts/stylesheets).
  - Ran `tmp/htmx_history/harness.py` (7/7), `tmp/quiz_spacing/harness.py` (6/6), and `tmp/submission_detail/smoke.py` (14/14).

## Scheduled notifications, announcement scheduling, Expo push toggle and offline PDF fonts

Reported:
- Scheduled lesson/quiz publishing timing needed end-to-end verification and any gaps closed (unpublishing a future published lesson left pending notifications alive).
- Admin announcements (`Announcement`) needed an optional future `scheduled_for` date/time plus cancellation before dispatch.
- Admins needed an admin-dashboard switch to disable Expo mobile push without disrupting Web Push, Telegram, or the student bell.
- Offline PDF viewer font assets (`standard_fonts/`) needed service-worker precaching parity with the online pdf.js configuration for Arabic/standard PDF rendering.

Changes and decisions:
- Lesson unpublish fix (`views.py::_edit_lesson`): moving an already-published lesson to `draft` or `archived` now calls `cancel_future_lesson_publication_events(saved)`, cancelling pending future `StudentNotification`, `PushDelivery`, and `TelegramNotificationDelivery` rows consistently with `update_quiz`.
- Scheduled announcements (`models.py::Announcement`, `announcements.py`, `forms.py::AnnouncementForm`, `notification_views.py`, `urls.py`, `admin_navigation.py`, `job_monitor.py`, `aw_ui.py`, `notifications_management_content.html`, `learning_platform/settings.py`):
  - Migration `0125_announcement_schedule_and_mobile_push_settings` adds `Announcement.scheduled_for` (`DateTimeField`, nullable), `Announcement.Status.CANCELLED`, `announcement_sched_idx`, and `MobilePushSettings`.
  - `AnnouncementForm.scheduled_for` accepts an optional Cairo local `datetime-local` value validated strictly in the future via `parse_application_datetime`. When set, `send_announcement` stores `scheduled_for` and does not enqueue immediate fan-out; `recover-pending-announcements` runs every 15s and `pending_announcement_ids` selects due `QUEUED` scheduled announcements without waiting for `ANNOUNCEMENT_RECOVERY_GRACE`. `_claim_announcement` refuses rows whose `scheduled_for > now` or status is `CANCELLED`.
  - `cancel_announcement` + `announcement-cancel` POST route (`can_manage_academic_setup`, CSRF, `select_for_update`) cancel `QUEUED` announcements before fan-out starts.
- Expo mobile push switch (`models.py::MobilePushSettings`, `admin.py`, `mobile_push_queries.py`, `mobile_push.py`, `notification_redelivery.py`, `portal_views.py`, `app_release_manage_content.html`):
  - `MobilePushSettings` singleton (`expo_push_enabled`, default `True`) managed from `Site & Apps -> App Releases` (`app-release-manage` POST `push_policy=1` and `/admin/`).
  - `is_expo_push_enabled()` gates `mobile_push._configured()` so `dispatch_due_mobile_push` and `process_mobile_push_receipts` become no-ops when disabled, and `expo_push_toggle_enabled()` stops `notification_redelivery` from queueing `PushDelivery` rows while keeping Web Push, Telegram, and in-app inbox active.
- Offline PDF standard fonts (`templates/pwa/service_worker.js`):
  - `PRECACHE_URLS` now precaches all 14 files under `vendor/pdfjs/3.11.174/standard_fonts/` using unversioned paths matching `window.pdfViewerAssets.standardFontDataUrl` so `caches.match(request)` hits offline.
- Arabic translation catalog (`locale/ar/LC_MESSAGES/django.po`, `django.mo`): updated with the 16 new UI/validation strings and compiled via `compilemessages`.
- Verification (disposable SQLite via `tmp/` harnesses; PostgreSQL/Docker/Redis unavailable offline):
  - `tmp/scheduled_notifications/harness.py`: 34/34 checks passed (quiz/lesson opening scheduling across inbox/Telegram/mobile/web push, immediate lesson without `opening_date`, admin `quiz-create` and `lesson-create` HTTP POSTs, HTTP `lesson-update` unpublish cancellation, moving future date cancellation, scheduled `Announcement` HTTP create/hold-before-due/recovery-and-fanout-after-due/HTTP cancel, `app-release-manage` HTTP Expo push flag off/on blocking `PushDelivery` & receipts while keeping Web Push/Telegram/inbox, and `/service-worker.js` unversioned `standard_fonts/` precache check).
  - Re-ran existing harnesses: `tmp/offline_pdf/harness.py` 15/15, `tmp/pdf_quiz_filters/smoke.py` 27/27, `tmp/telegram_digest/smoke.py` 24/24, `tmp/submission_detail/smoke.py` 14/14, `tmp/pwa_save_media/smoke.py` 16/16, `tmp/htmx_history/harness.py` 7/7, `tmp/quiz_spacing/harness.py` 6/6.

## Offline lesson PDFs open inside the installed app

Reported: lesson PDFs had to be saved like audio and then opened only inside
the app while offline. Saving already worked (`pwa_save_media` stores the PDF
as an encrypted blob), but opening used an `<iframe src="blob:...">` plus an
"open file" link with `target="_blank"`, so on phones the document went to the
device PDF viewer or a new tab and the reader could fail on a blank frame.

- `static/pwa/offline-library.js`: a saved document is decrypted into memory and
  rendered page by page on a canvas by the vendored pdf.js reader, with in-viewer
  previous/next controls and a page counter. There is no blob URL, iframe, new
  tab or `target="_blank"` link for documents; `closeViewer()` cancels the render
  and destroys the document so the decrypted buffer is released. Audio keeps its
  existing `<audio>` path.
- `templates/pwa/offline_library.html` loads
  `static/vendor/pdfjs/3.11.174/pdf.min.js` and supplies
  `window.pdfViewerAssets` (worker, cmaps, standard fonts).
- `templates/pwa/service_worker.js` precaches `pdf.min.js` and
  `pdf.worker.min.js` with `pwa_version` and serves `/static/` cache-first, so
  the reader itself is available offline; images, CSS and the library script
  keep the same on-demand refresh.
- `static/pwa/pwa.css`: `.pwa-pdf*` viewer styles replace the removed iframe and
  open-link rules.
- `views.lesson_document` keeps `allow_unpublished=True` (management only inside
  `resolve_media_link`) so the lesson editor can still preview a draft PDF; the
  endpoint is otherwise restricted to `allowed_types=("book",)` and streams the
  same-origin document.
- Verification: `tmp/offline_pdf/harness.py` 15/15 - saves the PDF through
  `PWALibrary.save`, registers the real service worker, reloads with the network
  forced offline, opens the item and asserts a rendering canvas (11585 dark
  pixels), "Page 1 of 2" -> next page re-renders, no iframe, no link, no popup,
  and the document is released on close.
  `tmp/pwa_save_media/smoke.py` 16/16 (PDF bytes, attachment name, media header,
  413/403/404/503 paths, audio unchanged) and `tmp/pdf_quiz_filters/smoke.py`
  27/27 still pass. `manage.py check`, `makemigrations --check --dry-run`,
  `compileall`, `audit_text_encoding`, `node --check` (including the rendered
  service worker) and `git diff --check` are clean.
- Known limits: cmaps (169 CJK files) are not precached; `standard_fonts/`
  (14 files) are precached on install without query strings to match
  `standardFontDataUrl`. iOS Safari and a real device were not exercised
  (no runnable stack), and no PostgreSQL, Redis, or live Telegram bot was used.

## Support digest media, quiz spacing, submission detail and HTMX history

Reported: support alerts arriving in Telegram showed only text instead of the
student's image/audio; the MCQ answer rows in the quiz editor had no spacing;
the admin submissions view was broken; modal buttons stopped responding after
HTMX navigation; all admin POST actions needed a readable audit.

- `telegram/support.py`: `send_support_digest` now delivers one attachment
  through `_send_digest`. A Telegram-hosted attachment reuses its
  `telegram_file_id`; a website upload has no file id, so its bounded private
  bytes are read from `TELEGRAM_R2_BUCKET_NAME` and re-uploaded as an
  `InputFile`. The digest text becomes the caption (truncated to
  `CAPTION_MAX_LENGTH`). A rejected media endpoint retries the same bytes as a
document and any other failure still sends the text digest, so an alert is
  never lost. The support bucket has no media-bucket fallback.
- `static/css/admin_workspace.css`: `.mcq-choices` is a column flex list with an
  8px gap and collapses when empty, so stacked answer rows are separated in both
  the server-rendered and `buildChoiceRow()` editors.
- `views.submission_user` (GET) answers through `render_page(...)` with
  `partials/submission_detail_content.html` instead of a bare `render()`. The
  row button targets `#display-page` and selects `#page-region`; the detail page
  previously returned the whole shell, so the swap found no region and the row
  appeared to do nothing. `page_region_breadcrumbs=False` keeps the full page
  and the fragment consistent (the template renders no breadcrumbs).
- `partials/page_region.html` and `admin_panel.html` mark `#page-region` with
  `hx-history-elt`. HTMX 4 restore requests carry no `HX-Request` header, so the
  server answers with the full page; without the marker the runtime replaced the
  whole `<body>`, dropping the head scripts (`admin_shell.js`, Alpine) and every
  cached shell reference, which left the confirmation modal and drawer dead
  after Back/Forward.
- Audit report: `docs/current/ADMIN_POST_ACTIONS_2026-11-13.md` lists all 76
  POST-capable admin routes with gate, effect and control, generated by
  `tmp/admin_post_audit/inventory.py` and `tmp/admin_post_audit/facts.py`.
- Verification (no database needed): `tmp/telegram_digest/smoke.py` 23/23
  (file-id reuse, R2 re-upload, voice endpoint, document fallback, oversize and
  provider failures, unsupported types, caption truncation);
  `tmp/quiz_spacing/harness.py` 6/6 browser measurements (block layout 0px ->
  8px gaps, 8px input/button gap, empty list hidden);
  `tmp/submission_detail/smoke.py` 14/14 (render_page contract, context, and a
  rendered `#page-region` carrying the review form and no shell);
  `tmp/htmx_history/harness.py` 7/7 (shell script runs once with the marker, the
  rail and modal survive, confirmations still record after Back).
  `manage.py check`, `makemigrations --check --dry-run`, `compileall`,
  `audit_text_encoding` (841 files), `node --check` and `git diff --check` clean;
  `tmp/pdf_quiz_filters/smoke.py` still 25/25.
- Known gaps: the history restore replaces only `#page-region`, so the rail's
  active highlight can lag after browser Back until the next navigation; the
  student shell (`student_next/base.html`) has no `hx-history-elt` because its
  navigation targets `#display-page` inside page templates, and no runnable
  stack was available to verify a student-side change. Nothing here was
  verified against PostgreSQL or a live Telegram bot.

## Secure lesson-PDF streaming and quiz dashboard filters (13 November 2026)

Reported bug: opening a lesson PDF handed the browser a presigned R2 URL, so
the reader fetched the document straight from object storage instead of the
app authorizing and streaming it.

- `stream_lesson` and `media_delivery.resolve_book_url` are removed. The book
  link in `view_lesson_details` now reverses `lesson-document`
  (`offerings/<offering>/lesson/<lesson>/<file_index>/document/`).
- New `views.lesson_document` (GET-only) authorizes through the canonical
  `resolve_media_link(allowed_types=("book",))`, so the published and
  availability gates apply, then reads the object with `open_book_document`
  (bounded Range) and streams it from the app origin with
  `Content-Disposition: inline`, `Accept-Ranges`, `X-Content-Type-Options:
  nosniff`, `Cache-Control: private, no-store`. Forbidden/not-found/invalid/
  unavailable map to 403/404/400/503 and an unsatisfiable range to 416.
- `static/js/lesson_media.js` passes `viewer.dataset.url` to pdf.js directly
  (pdf.js sends same-origin credentials) - no JSON hop and no storage URL.
  The PWA save path already streamed through `pwa_save_media`; it is unchanged.
- Quizzes dashboard filters: `admin_tables.content_scope(..., view=...)`
  normalizes the new `level` (`Level.ordering`, validated against stored rows)
  and `quiz_type` (`QuizType.code`, validated against `QUIZ_TYPE_CODES`) for the
  quiz view only, and `quiz_queryset` applies both predicates. Counts, sorting,
  signed bulk selections and durable quiz jobs therefore see the same rows.
  `views._quiz_content_filter_fields` adds the two selects to the shared
  `aw_filter_bar`; option labels match the existing Quiz Type column
  (`name_en`) and level labels use `Level.display_name`.
- Verification: `manage.py check` clean, `makemigrations --check --dry-run` no
  changes, `compileall` and `node --check` clean, `audit_text_encoding` passed
  (840 files). `tmp/pdf_quiz_filters/smoke.py` runs 25 checks: route reversal,
  streamed bytes/headers/range/closed body, 206 response, 403/404/400/503/416
  mapping, no object-store URL in the response, compiled filter SQL for the
  level/quiz-type predicates, and the rendered filter bar selects/selected
  options. The four filter labels already exist in the Arabic catalog.
- Not verified: no PostgreSQL or Docker engine was available (`Docker Desktop`
  did not start its VM and local SQLite migrations are blocked by the pgcrypto
  guard in migration 0053), so a live lesson-PDF open, filter clicks and
  `EXPLAIN` were not run. Next safe step with the dev stack up: open a book
  lesson and confirm the network panel shows only same-origin `document/`
  requests (with 206 ranges) and no `r2`/presigned host, then apply both quiz
  filters and check the row count matches the tab counts.

## Quiz edits only re-announce real scope changes (11 November 2026)

Editing a published quiz used to bump `Quiz.publication_event_version` and
re-schedule opening events whenever any copied fact changed (name, quiz type,
total grade, opening date, closing date, duration), so fixing a typo pushed a
new notification to every enrolled student.

- `student_notifications.classify_quiz_announcement_change` is now the one
  rule. It returns `reannounce` for a different course offering or a closing
  date extended at minute precision, `withdraw` for a shortened or moved
  window, and `refresh` for name/type/total-grade/duration edits. Minute
  precision matters: the admin form submits `%Y-%m-%dT%H:%M`, so a stored
  timestamp with seconds must not read as a shortened window.
- `update_quiz` (`views.py`) bumps the publication version only on a publish
  transition or `reannounce`; publishing still schedules, unpublishing still
  cancels, `withdraw` cancels quietly, and `refresh` calls
  `refresh_pending_quiz_event_snapshots`, which rewrites only the queued
  `title_ar/body_ar/title_en/body_en` of future, uncancelled events in bounded
  batches. No new `StudentNotification` row, no push, no key change.
- The queued text keeps its own `effective_opening_at`/`effective_closing_at`
  window, so a refresh never rewrites the window or the idempotency key.
- The quiz create form already defaults the duration to `0`
  (`create_quiz` context `duration_minutes: 0`, template
  `default_if_none:0`, model `default=0`); verified in the browser and left
  unchanged.
- Verification (`tmp/quiz_notify/smoke_quiz_notifications.py`, disposable
  PostgreSQL 18 on port 55449): 37 checks, all passing - rename and duration
  edits create no rows and keep the three pending events with refreshed text;
  extending the closing date, moving the offering and republishing each bump
  the version and add one event per student; shortening withdraws the pending
  events without adding rows; the create form renders `value="0"`. Browser
  pass on the create and update pages (including structured questions):
  duration `0` on create, no console errors.

## Shared graded-answer review for students and management (11 November 2026)

The management quiz-submission page used to render the legacy exam markup
(`questions_rendering.html`) while a new-UI student saw the graded review
cards, so an administrator reviewing a submission did not see what the
student saw.

- `management_system/templates/partials/quiz_review_cards.html` is now the one
  review implementation. `student_next/partials/quiz_questions.html`
  (`mode == "view"`) and `partials/submission_detail_content.html`
  (`submission_user`) both include it, so per-question verdicts
  (Correct / Partially correct / Incorrect / Under review), awarded/points,
  given answer, correct answer and structured answer lists are identical on
  both surfaces.
- The partial takes one optional flag, `review_can_grade`. When set it also
  emits the hidden `questions[n][id]` and the `questions[n][manual_grade]`
  input, which is still the `unpack_quiz_form` / `submission_user` POST
  contract. The student page passes nothing and renders no grading control.
- Styles moved out of `static/css/student_next.css` into
  `static/css/quiz_review.css`, loaded by `student_next/base.html` and by
  `submission_detail.html` (`{% block style %}`). `.sn-review-embed` bridges
  the `--sn-*` tokens to the `--aw-*` workspace palette, so one rule set
  serves both shells; pills are re-styled only inside the embed.
- Dead `.sn-review`, `.sn-review-row`, `.sn-review-num`, `.sn-review-points`,
  `.sn-review-warn` and `.sn-review-explanation` rules (no template used them)
  were deleted rather than moved.
- Management can now correct an auto-graded question too, because every card
  carries a grade input; the POST handler already validated `0..question.grade`
  and recomputed `Grade.total_grade`.
- Verification (`tmp/quiz_review/`): 23 checks against disposable PostgreSQL
  18 on port 55445 - both pages render 6 identical review cards, the legacy
  `sd-q-card` markup is gone, grading fields exist only on the management page,
  and a correction moved the MCQ to 0, the written question to 4 and the quiz
  total to 13. Browser pass on the same database: 6 cards each, computed styles
  applied in both shells, no console errors; screenshots in
  `tmp/quiz_review/shots/` at 1280 and 390 width. `manage.py check` clean,
  `makemigrations --check --dry-run` no changes, `audit_text_encoding` passed.

## In-app support chat for the new student UI (11 November 2026)

The new student experience now has one support conversation (`/support/`)
that replaces contacting support outside the LMS while staying unified with
the existing Telegram support thread. Both surfaces write the same rows:
`TelegramConversation` / `TelegramMessage` / `TelegramAttachment` remain the
single source of truth, so a message sent from Telegram appears in the app and
a message sent in the app appears in the admin chat workspace.

- New domain module `management_system/support_chat.py` owns the whole rule:
  `conversation_for_student`, `visible_messages` (excludes DIGEST rows and
  student-deleted rows), `newest_messages`/`older_messages`/`messages_after`
  (`(created_at, pk)` cursor, 20 per page, 30 per poll),
  `message_presentation` (opaque "Bible Institute team" identity),
  `send_student_message`, `delete_student_message`, `student_attachment`
  and `stream_support_attachment`.
- New views `management_system/support_chat_views.py` and routes
  `student-support`, `student-support-send`,
  `student-support-message-delete`, `student-support-attachment`. The page
  requires student role and `StudentMode.NEW`; a legacy-mode student is
  redirected to the notification inbox and any POST is refused with 403.
- Student input is text (4000 chars), one JPEG/PNG image, or one audio
  recording capped at 60 seconds. Attachment rules stay canonical in
  `management_system/telegram/attachments.py`:
  `classify_uploaded_file` and `probe_audio_seconds` were made public and now
  accept the audio cap, so the chat passes `CHAT_AUDIO_MAX_SECONDS = 60` while
  the Telegram reply keeps 300. Recorded WebM has no container duration, and a
  video-only file yields no duration, so the probe falls back to the last audio
  packet and a browser recording is only reclassified as voice once a duration
  proves it holds audio.
- Admin replies work without a Telegram account: `reply_to_conversation`,
  `reply_with_attachment_to_conversation` accept `bot=None` and finalize
  web-only as SENT with no `telegram_chat_id`; the admin workspace no longer
  requires an active bot unless the student is Telegram-linked. Every reply
  calls `student_notifications.create_support_reply_notification`, which stores
  a `support_message` notification with a generic title/body and an
  idempotency key per outbound message.
- Web Push reuses the PWA worker: `StudentNotification.NavigationType.SUPPORT`
  plus `SUPPORT_MESSAGE` type, and `notification_views._notification_target_url`
  / `web_push._notification_url` resolve to the chat. The lock-screen payload
  stays generic ("New message from the Bible Institute team") and never
  contains conversation text. Notification inbox/panel now show a message icon
  for this kind.
- App-originated messages alert admins through
  `telegram_tasks.send_web_support_digest`, which reuses `send_support_digest`
  and skips silently when no bot is configured.
- Front end: `templates/student_next/support.html` is a standalone
  full-screen document with only a back-to-learning control; it does not
  extend the student shell, so no topbar, navigation, footer, push or biometric
  prompt appears. `support_chat.js` owns the 5s poll/backoff, older-page load,
  fetch send, MediaRecorder with a 60s cap, image preparation, and delete.
  The `.sn-chat*` styles remain shared; the support shell fills `100dvh`.
- Support attachments prefer `TELEGRAM_R2_BUCKET_NAME` and fall back to the
  existing `R2_BUCKET_NAME` when the dedicated bucket is not configured.
  Images are normalized to JPEG/PNG in the browser (including large and
  non-JPEG/PNG images), and recordings use the actual MediaRecorder MIME type
  (`.webm`, `.m4a`, `.ogg`, or `.mp3`) instead of always claiming WebM.
- Student deletion still hides the durable row from the student immediately;
  `telegram_tasks.delete_web_support_digest` then best-effort deletes the
  Telegram digest copies (and the original Telegram message when applicable)
  after commit.
- Migration `0124_support_chat` adds `TelegramMessage.student_deleted_at`,
  the `support_message`/`support` choices and their constraint branches.
  Reversing it requires no `support_message` notifications to exist, because
  the older route-ids constraint cannot represent them.
- Verification on disposable PostgreSQL (`scc_smoke`, removed afterwards):
  70 functional checks (`tmp/support_chat/smoke_support_chat.py`) covering
  page/poll/older cursors, sends, MIME spoof rejection, real ffprobe duration
  caps, soft delete, role/CSRF/mode authorization, notification and push
  payloads, linked-Telegram replies and the admin thread, plus a 28-check
  Playwright walk (`tmp/support_chat/browser_check.py`) for infinite scroll,
  text/image/voice sending, delete, polling, inbox link, English/RTL and the
  390x844 mobile layout. Chat page costs 9 queries. `manage.py check`,
  `makemigrations --check`, migration forward/backward rehearsal and
  `audit_text_encoding` pass; Arabic and English catalogs are compiled.

## PWA, offline library, Web Push and passkeys (11 November 2026)

Implemented `docs/pending/PWA_OFFLINE_LIBRARY_PUSH_PASSKEY_SPEC.md`:

- Root `/manifest.webmanifest`, `/service-worker.js` and localized
  `/offline-library/`, `/pwa/offline-shell/` routes. The service worker caches
  only public shell assets and serves a generic offline shell; every other
  navigation/API/media request is network-only. Student shell, login page and
  app page register the PWA. The old APK/Download-the-app UI is removed from
  both student shells; the release record and admin history remain, but no
  student link points at the APK.
- Audio/PDF offline saves use `management_system.pwa_views.pwa_save_media`,
  which reuses `media_delivery.resolve_media_link`,
  `open_audio_download`/`open_book_document`, and
  `MobileDownloadPolicy.in_app_download_enabled`. The browser encrypts bytes
  with a non-extractable AES-GCM key in IndexedDB, stores only ciphertext in a
  per-account Cache Storage bucket, and requires a local WebAuthn user
  verification before reads. Metadata stays in IndexedDB; `/offline-library/`
  revalidates access online and removes revoked entries. Logout clears local
  media and deactivates server push subscriptions; account switches clear the
  prior partition.
- Browser Web Push is independent of Expo: `WebPushSubscription` and
  `WebPushDelivery`, VAPID settings, authenticated subscribe/unsubscribe/status
  endpoints, a generic lock-screen payload, and a 15-second Celery beat task.
  `StudentNotification` stays the logical source; no grades/content are sent.
- Passkeys use Duo Labs `webauthn`: discoverable resident credentials, one-use
  cached challenges, exact RP ID/origin verification, user verification,
  signature/counter checks, multiple named credentials per account and a
  fresh-auth gate for add/revoke. The login page offers passkey sign-in for all
  browser roles; profile/account security has enrollment and revocation. The
  password path and password recovery are unchanged.
- Dependencies approved by the user: `pywebpush==2.5.0` and
  `webauthn==3.0.1`; settings/env/compose fields added. Migration
  `0120_pwa_push_passkey` is additive and was applied/reversed/re-applied on a
  disposable PostgreSQL 16 database.
- Query budgets were re-measured on a disposable PostgreSQL dataset with
  100,002 users and 120,003 notifications: notifications fell from 8 to 6
  total statements (3 view statements, target <=4) and the cursor fragment
  from 7 to 4 (1 view statement, target <=3); quiz ready/result fell from
  18/17 to 10/10 (7 view statements, target <=10). Evidence:
  `docs/pending/STUDENT_VIEW_QUERY_BUDGET_EVIDENCE.md`. Migration `0121`
  adds student/history indexes.
- Structured ordering/matching answers no longer require JavaScript: the
  exam templates render textarea fallbacks (`A | B` order, `Left = Right`
  lines); the shared runtime upgrades them to the hidden JSON field when JS
  runs. No-JS submission was graded 10/10 on the seeded quiz.
- Alpine 3.13.3 is vendored locally at
  `static/vendor/alpine/alpine-3.13.3.min.js`; the old jsDelivr URLs were
  removed from all three shells. Browser verification showed Alpine 3.13.3
  active and zero CDN requests.
- `PWA_MEDIA_MAX_BYTES=0` means no application-level per-file cap; the browser
  and operating-system storage quota remains the hard limit. Offline files are
  still stored only on the device (encrypted Cache Storage plus IndexedDB
  metadata/key), and the browser is asked for persistent storage. The current
  encryption path buffers one complete file before encryption.
- Student UI polish: the header notification panel now has a capped flex
  layout with a scrollable item list and fixed footer; progress-ring labels
  are absolutely centered; profile/course/lesson next-action cards show a
  Lesson/Quiz kind and no underlined links; and student file pickers use a
  localized themed visual layer over the native input. A student-only dialog
  automatically asks for notification permission when the device has no active
  browser subscription; the native backdrop covers the full viewport and both
  `Later` and successful enablement remove the page scroll lock. The
  notification page keeps its inline toggle and suppresses the dialog. The
  new shell navigation and the legacy student navbar expose an Offline tab to
  `/offline-library/`; the service worker serves the cached shell for that
  route while offline, and the generic offline fallback links to the localized
  offline library.
- Web Push no longer backfills the historical inbox. `WebPushSubscription`
  stores `enabled_at`; materialization and claim both require
  `notification.created_at >= subscription.enabled_at` and a due
  `scheduled_for`, so enabling or re-enabling notifications only delivers
  events created after opt-in. Migration `0122_web_push_enabled_at` adds the
  field. Payloads now show the real localized `StudentNotification` title and
  body (body capped at 500 characters); the offline library renders saved
  audio and PDF inline instead of opening a modal. The installed PWA shows an
  "Installed on this device" state and hides the install action when running
  standalone, and the auto notification prompt is dismissed for the rest of
  the browser/app session only. The biometric prompt is new-shell/profile-only
  and registers a default-label passkey; the old passkey management section is
  removed, legacy student pages expose neither notification nor biometric
  prompts, and the legacy notification page no longer shows the browser-push
  toggle.
- Admin HTMX search persistence: the shared `aw_filter_bar` and every
  self-replacing search input now carry `hx-preserve` (applications,
  application analytics city search, notification recipients, Telegram
  searches). The Applications page keeps its value and focus across the
  workspace `outerHTML` swap. All HTMX search inputs were audited; inputs that
  swap only a result list (not their form) are unaffected.
- Global student-view forcing: `StudentExperienceSettings.force_all` lets the
  admin force the selected global mode for every student, ignoring per-account
  overrides. The Student Experience settings page has a force/release toggle
  and the mode cache now stores `{mode, force_all}` under a v2 key. Migration
  `0123_student_experience_force_all` adds the field.
- `deploy/setup_env.sh` copies the non-secret PWA/WebAuthn settings and the
  public VAPID key into `.compose.env`; the private VAPID key remains only in
  `.env`. `compose.application.yml` no longer overrides the private key from
  Compose interpolation, so it reaches the app through the `.env` service
  env_file and is not blanked when `.compose.env` is used. The setup script
  also prompts for the RP/origin/display/contact values and enables both
  features by default; it generates a VAPID key pair with OpenSSL and a small
  stdlib Python encoder when either half is missing, without printing the
  private key.

Query-budget evidence (disposable PostgreSQL 16, 100,002 users, 120,003
notifications for one student):

| Path | Before | After | Median | View statements | Target |
|---|---:|---:|---:|---:|---:|
| `/en/notifications/` | 8 | 6 | 35.6 ms | 3 | <=4 |
| `?fragment=1` cursor | 7 | 4 | 9.1 ms | 1 | <=3 |
| notification panel | 5 | 5 | 27.4 ms | 2 | n/a |
| quiz ready | 18 | 10 | 16.0 ms | 7 | <=10 |
| quiz result | 17 | 10 | 15.7 ms | 7 | <=10 |

Total counts include session, auth user and role; the notification page also
includes one shell banner. The plan budgets are view statements. Migration
`0121_notification_student_indexes` adds `(student, -created_at, -id)` and
`(student, notification_type, -created_at)` indexes. `EXPLAIN ANALYZE` on the
adversarial 120k-one-student count used a parallel sequential scan at 23.3 ms
(4163 shared buffers); the 20-row page used the new student index at 0.039 ms
(4 buffers). The full disposable audit report is under
`%TEMP%\student-audit\`; the tracked summary is in
`docs/pending/STUDENT_VIEW_IMPLEMENTATION_AUDIT.md`.
- Verification: `manage.py check`, `makemigrations --check`, template compile,
  JS syntax, `msgfmt -c`, `audit_text_encoding`, disposable PostgreSQL
  migration rehearsal, browser service-worker/manifest checks, encrypted
  offline save with a synthetic push/media route and virtual authenticator,
  offline navigation/decryption, passkey registration/login, and mocked Web
  Push delivery all pass. Real provider delivery, production HTTPS origins,
  iOS Safari, and real R2 media still require device/staging verification.

Known limits: audio/PDF decryption is best-effort device protection, not DRM;
the APK backend route still exists but is not linked; VAPID keys must be
generated per environment before enabling push; passkey endpoints require the
exact configured origin and RP ID; no PostgreSQL `EXPLAIN` was needed because
the new delivery/credential reads are bounded by limit/index.

## Repair unit (11 November 2026) - opt-in new student UI

Repaired the new `student_next` experience after the audit above. The global mode
remains `legacy`; only accounts with an explicit `new` override render the new
shell. Legacy templates and legacy CSS/assets remain the default for every other
student.

Changed areas: `templates/student_next/*` (shell, learning/course/lesson, quiz,
calendar, profile, notifications, graduates/album, download), new `sn-*` CSS and
`static/js/student_next/*`, `student_experience/{learning,course,lesson,calendar,
profile,quiz,navigation,render}.py`, `student_experience/middleware.py`,
`context_processors.py`, `Calendar` firstweekday, `StudentBanner.severity` +
migration 0119, admin global-mode page and Site & Apps nav, and the Arabic
catalog. Shared behavior-only changes are `lesson_media.js`,
`gallery_lightbox.js`, the `display_quiz.html`/`quiz_runtime.html` shared quiz
runtime (pre-existing in this tree), and `take_exam`'s `select_related`.

Repair outcome: new pages are responsive at 1440/1024/768/390 in English and
Arabic, use no legacy shell assets, and have no duplicate IDs, unlabelled visible
controls or horizontal overflow. Course outline -> lesson HTMX, lesson media
runtime, notification panel, alert dismissal, calendar selection, quiz
start/submit dialog, profile tabs, language switch and gallery lightbox were
verified live. Legacy pages still return 200 with Bootstrap and three banners.

Second review: three independent reviewers (code/security/legacy, live UI/UX,
plan conformance) found no authorization blocker. Their blocking UI defects were
fixed: mobile quiz result now shows answers, mobile notification popover is
viewport-fixed, Arabic scores/ordering use bidi isolation, profile tabs switch
and support arrow keys, alert close stays top-end, album/photo/video plurals are
correct, calendar controls mirror in RTL, and the empty course stage is compact.
Code-review fixes: announcement cards now use the canonical per-student inbox
(`student_notifications_queryset`), profile health derives from the same gaps as
the canonical completeness list, hero CTA follows the next-action kind, help-text
IDs match `aria-describedby`, form widget classes match CSS, the icon settings
loader uses `get_or_create`, the shell context helper is shared, quiz has a native
submit fallback, and invalid document/payment uploads preserve the bound form and
show an error.

Verification: `manage.py check` clean; `makemigrations --check --dry-run` no
changes; `audit_text_encoding` passed (800 files); `msgfmt -c` clean; JS syntax
checked; 60-page browser matrix across the four widths/EN-AR passed with only the
disposable lesson fixture's expected R2 400/404 media errors. Query counts
(including auth/context): courses 16, course 14, lesson 15, quiz 18, calendar 9,
profile 18, notifications 8, graduates 7, album 9, download 5; no per-row N+1
found.

Remaining risk / next safe step: do not set the global mode to `new` until the
following are either accepted or closed: (1) `display_quiz.html` is a legacy
template changed by the pre-existing shared quiz-runtime extraction; it was
verified functionally but no normalized baseline exists; (2) staff/admin preview
through an override is deliberately not supported - use a test student account;
(3) notification/quiz query counts exceed the plan budgets (8 vs 4, 18 vs 10 with
middleware/context); (4) structured ordering/matching quiz answers still require
JavaScript even though a native submit fallback now exists; (5) PostgreSQL
`EXPLAIN`, 1/20/50-offering scale runs and the plan's in-repo B13 audit scripts
were not produced - the browser/query evidence is disposable SQLite in `%TEMP%`.
Re-run the matrix against PostgreSQL before global enablement.

## Audit unit (10 November 2026) - new student UI against the plan and prototype

Audited the opt-in authenticated student experience against
`docs/pending/STUDENT_VIEW_TRANSFORMATION_PLAN.md` and
`prototype/student-redesign`. Report:
[docs/pending/STUDENT_VIEW_IMPLEMENTATION_AUDIT.md](docs/pending/STUDENT_VIEW_IMPLEMENTATION_AUDIT.md).
No application, template, static asset, schema or data change was made.

Method: disposable SQLite schema in `%TEMP%`, seeded two academic years, six
offerings, nine lessons, four quizzes, grades/submissions, banners,
notifications, meetings, gallery items and an app release; live Django server on
127.0.0.1:8911; Chromium at 1440 and 390, English and Arabic; a second student
account in legacy mode; HTTP checks with the Django test client. R2, real
SQLite and PostgreSQL were not used, so media playback and production query
plans are not part of this evidence.

Result: the mode resolver, routes, access checks, quiz state machine and
notification filters work, and legacy pages still render with their own shell
and assets. The presentation layer is not a prototype port. The stylesheet is a
direct `st-*` -> `sn-*` copy of the prototype, while many templates use older
or invented class names. The audit counted 74 template class tokens with no CSS
rule and 157 defined component classes with no template consumer.

Blocking defects: calendar month grid has no layout; notification popover is
unstyled; the course workspace cannot play a lesson opened through HTMX because
the lesson runtime is only loaded on the direct lesson page; quiz, graduates,
album and download have no `.sn-page` container; course/lesson overflow at
390px; progress rings have no SVG; filter chips are unstyled links; profile
has no top navigation because `ProfileDetail.get_context_data` bypasses
`render_student_page`, plus a duplicate `id_payment`; alerts render all three
banners as info and do not persist dismissal; the public-page guard, global
admin mode page and `StudentBanner.severity` are not implemented.

Superseded by the repair unit above; keep this audit as historical evidence. The
global setting remains `legacy`.

## Current implementation attempt (10 November 2026) - authenticated student experience

Implemented the authenticated student redesign from
`prototype/student-redesign`, following
`docs/pending/STUDENT_VIEW_TRANSFORMATION_PLAN.md`. The student shell applies
only to authenticated accounts selected by the mode resolver; public pages
remain unchanged. Admin surfaces keep their normal shell, with the one
per-account override control now authorized on the existing Users detail page.
The plan file was not edited; its SHA-256 remained
`A2BE058FCAAABF6ABFB888E0EA1840614B8C6CA721988BD446F6E070E6DAA2DA` at final
check. In a follow-up, the user explicitly authorized the per-account
override selector on the existing admin Users detail page. The current unit
adds only that control; the global settings UI and the plan's public-page guard
remain out of scope.

Review follow-up completed: all five findings were addressed without changing
the senior-owned plan or schema. Course progress still computes full offering
totals, but consumes lessons and progress in 100-lesson batches and retains
lesson details only for the visible outline IDs (at most 20). New and legacy
live exams now use one snapshot/structured-answer/timer runtime. Both calendar
surfaces use the extracted canonical grid and agenda builders. New-profile
context is built by `student_experience/profile.py`; the legacy context body,
authorization, locking and POST transaction remain intact. Unused course quiz
context values and the calendar's unused translation import were removed.
Preserved the public-page guard, account rollout behavior, admin quiz/correction
path, and the Users-detail account override.

Final connected-code review found no remaining actionable security, query-bound,
plan-reuse or runtime-duplication findings in the student rollout. It removed a
broad exception that hid profile recommendation failures and an unused
notification-query import. The unrelated attendance scanner edits currently in
the worktree are preserved and excluded from the student-experience commit.

Changed files:

- `management_system/models.py`,
  `management_system/migrations/0118_student_experience_mode.py`, and
  `management_system/management/commands/student_experience_mode.py` add a
  legacy-by-default global setting, an inherit/legacy/new user override that
  is not editable in forms, and a rollout command. Mode resolution only
  selects the new mode for student-role accounts. Global cache invalidation
  runs after the settings transaction commits.
- `management_system/student_experience/` contains mode resolution, shell
  navigation/rendering, and bounded learning, course, lesson, and calendar
  contexts. `management_system/views.py`, `notification_views.py`,
  `gallery_views.py`, and `portal_views.py` choose the new shell only for an
  opted-in student. `management_system/academic_access.py` accepts a bounded,
  annotated offering queryset while applying the same canonical read access
  predicate in SQL. `management_system/utils/student_data.py` exposes the
  authorized-offering quiz read used by the course context while preserving
  the access-checked legacy/mobile entrypoint.
- `management_system/forms.py` adds student-next widget classes without
  changing the inherited form validation contracts.
- The B2 per-account selector uses `StudentExperienceOverrideForm`,
  `student_experience_views.py`, the dedicated
  `student-experience-override` route and an `aw-*` card on the existing Users
  detail page. Only an admin with `can_manage_academic_setup` can change a
  student account; the endpoint validates the enum, locks the target user and
  writes only `student_experience_override`. The separate user-edit form and
  application/document transaction remain unchanged.
- `management_system/templates/student_next/`,
  `management_system/static/css/student_next.css`, and
  `management_system/static/js/student_next/` implement the isolated shell,
  pages, fragments and interactions. The new CSS is scoped to the student
  shell; legacy templates and static assets remain the default.
- `locale/ar/LC_MESSAGES/django.po` and `django.mo` include Arabic catalog
  entries for new shell labels, placeholder messages, and six-form Arabic
  plural messages.
- `project_memory.md` now maps the mode, command, context package and shell.
- `management_system/utils/student_data.py` now streams the course-wide lesson
  and progress aggregates per 100-lesson batch. `course.py` requests detail
  only for its first 20 visible lesson IDs; `learning.py` no longer requests
  lesson-level detail. Existing helper callers in profile/overview paths were
  checked and retain aggregate behavior.
- `management_system/calendar_grid.py` owns the canonical day shape and
  agenda filter. Both admin calendar callers normalize holidays to `{id,name}`
  records before invoking it; the student builder adds only its `selected`
  presentation field.
- `management_system/student_experience/quiz.py` owns new-mode quiz display
  values. `templates/partials/quiz_runtime.html` and
  `static/js/student_experience/quiz_runtime.js` are included by both student
  exam templates; the new `student_next/quiz.js` now owns navigator UI only.
- `management_system/student_experience/profile.py` owns new-mode profile
  context and shared form selection. `ProfileDetail` dispatches to the builder;
  its legacy context implementation is restored to the pre-transformation
  behavior.

Access and behavior invariants preserved: course/offering access still uses
`academic_access`; lessons keep published and availability gates; quiz state,
timers, snapshots, submission and answer secrecy stay in `take_exam` and
existing quiz helpers; protected media, progress, profile writes, gallery
authorization and notification actions keep their existing owners. Upcoming
quiz pages expose metadata only. The public home and about pages retain their
public templates. `/portal/` routes an opted-in student to My Learning and
keeps the legacy destination otherwise. New-mode login honors safe `next`
values and defaults to My Learning.

Rollout commands (not executed against a real account or global setting):

- `python manage.py student_experience_mode --username <username> --override new`
- `python manage.py student_experience_mode --global-mode new`

The global setting remains `legacy`; this unit changed no account override.
The first per-account selection is an explicit admin action from the Users
detail page. A global rollout remains a separate decision.

Verification on 10 November 2026:

- Migration 0118 was reversed to 0117 and reapplied on the isolated tmpfs
  PostgreSQL container; full local `manage.py migrate` then applied Django
  auth/admin/session tables needed for HTTP smoke. `showmigrations` reports
  0118 applied. No SQLite source or production database was accessed.
- `python manage.py check`: passed. `python manage.py makemigrations --check
  --dry-run`: no changes. `python -m compileall -q management_system`, Node
  `--check` on all three new JavaScript files, `python manage.py
  audit_text_encoding` (784 text files), and `git diff --check`: passed.
- Per-account admin control: Python compilation, template loading, route
  reversal, form isolation/choices, invalid-choice rejection, Arabic catalog
  lookup, and `compilemessages -l ar` passed. On the disposable tmpfs
  PostgreSQL database with 0118 applied, HTTP smoke verified admin card
  visibility, valid save/redirect, missing-CSRF 403, invalid-choice 400,
  staff denial, non-student target denial and GET 405. All fixtures rolled
  back; user count was zero before and after. No schema or production data was
  changed. The B2 `makemigrations --check --dry-run` found no model changes but
  warned that the configured default host `postgres` could not resolve.
- Arabic catalog audit found and translated 248 previously untranslated
  student-shell labels plus the new placeholder/plural messages. `python
  manage.py compilemessages -l ar` passed; runtime catalog audit found no
  untranslated shell strings or missing plural entries.
- Rollback-only PostgreSQL HTTP smoke passed for new and legacy course pages,
  lesson page and HTMX fragment, `/portal/`, login with safe/unsafe/missing
  `next`, an authenticated public about page, and the anonymous public home.
  The per-account rollout command changed only its fixture user. All smoke
  fixtures rolled back; global mode remained legacy. The new lesson request
  used 8 view SQL statements (excluding auth/session middleware; budget <=10).
- Earlier in the same implementation, the new course workspace measured 10
  view SQL statements (budget <=10), My Learning measured 7 for one offering
  (budget <=12), and profile, notifications, calendar, graduates, and
  download-app new-mode routes returned HTTP 200. Upcoming quiz rendering
  returned no questions or answer key. The CSS/template/icon isolation audits
  passed, and all 21 new templates loaded.
- Review-fix verification: Python compilation, Node syntax checks for both
  quiz scripts, `manage.py check`, five changed-template loads,
  `audit_text_encoding` (792 files), and `git diff --check` passed. The shared
  calendar builder passed a day-shape/agenda smoke. `makemigrations --check
  --dry-run` reported no changes; its migration-history probe warned that the
  configured default hostname `postgres` does not resolve from the host.
- Final review verification: `manage.py check`, AST parsing of 25 changed Python
  files, Node syntax checks for the shared quiz runtime and all three student
  scripts, eight affected template loads, the 792-file encoding audit, and
  `git diff --check` passed. `makemigrations --check --dry-run` reported no
  model drift with the same unreachable-host warning. The remaining line-ending
  warning in `git diff --check` belongs to the unrelated scanner.js edit.
- A rollback-only PostgreSQL smoke on the confirmed tmpfs container used 205
  lessons and three progress rows: aggregate totals covered all 205 parts,
  lesson details contained only the 20 requested IDs, and the helper issued
  three bounded progress queries plus the lesson scan (4 queries total).
  Post-rollback counts were zero for users, levels, offerings, lessons and
  lecture progress. No authenticated browser walkthrough or representative
  production-cardinality `EXPLAIN` was run.
- Post-smoke PostgreSQL counts remain zero for users, levels, academic years,
  year-level links, offerings, enrollments, lessons, quizzes, questions,
  submissions, grades, attendance records, and lecture progress. The seeded
  mode row is still legacy; the override field is `editable=False`.

Database assumption/evidence: verification used only the disposable
`lms-student-next-pg-tmpfs` PostgreSQL container on 127.0.0.1:55434, mounted as
tmpfs. The configured Docker service hostname `postgres` is unreachable from
the Windows host. Redis is also unreachable from this session; request-mode
resolution falls back to PostgreSQL and cache invalidation commit/rollback was
checked with LocMemCache. No populated-data PostgreSQL EXPLAIN or visual browser
walkthrough was run, so large-table plans and pixel-level browser parity remain
open release evidence. Do not run the feature against production or alter the
real SQLite database.

Next safe step: an admin can select the first named student from that student's
Users detail page, then review the account's authenticated course, lesson,
calendar, profile and live quiz pages. The global mode remains legacy until
separately authorized. Repeat query/`EXPLAIN` review against representative
PostgreSQL population data before a broad rollout.

## Plan unit (10 November 2026) - student view transformation plan

Created [the student view transformation plan](docs/pending/STUDENT_VIEW_TRANSFORMATION_PLAN.md)
and indexed it in `docs/pending/README.md`. It proposes keeping the legacy
student UI beside a new `student_next` UI, with a global legacy/new setting and
an admin-controlled per-account override, defaulting to legacy. New mode hides
the public/static pages for signed-in students until logout and uses the admin
workspace component/form/Bootstrap-removal discipline as its reference. The
plan requires strict legacy/new template, CSS and JS separation with a frozen
legacy baseline, new-mode `student_experience` read models, mode-aware
rendering, `SnFormMixin` widget classes instead of crispy, no
Bootstrap/Font Awesome in the new shell, preservation of the existing HTMX
targets and Alpine behavior, HTMX fragment/root best practices, bounded query
budgets, a migration/rollback rehearsal, and a separate legacy removal gate.
No application, template, static asset, schema, permission, provider or
dependency change was made as part of writing the plan. `docs/**` is ignored by the current
`.gitignore`, so the new plan is available locally but not tracked until the
repository policy changes.

## Latest implementation unit (10 November 2026) - media worker CPU utilization

The media worker was capped at 2 CPUs but used about one: `process_media_job`
ran one job (prefetch 1) and `run_media_job` sequenced every FFmpeg stage, the
encoder had no thread budget (FFmpeg auto-detected host cores, not the CFS
quota), and `MEDIA_WORKER_CONCURRENCY` was defined but read by nothing.

Changed:

- `management_system/media_processing.py`: `run_media_job` starts the audio
  deliverables (AAC intermediate, audio HLS, MP3) on one worker thread beside
  the video HLS encode (real video sources only). The audio thread touches no
  DB/Redis: it checks the job's shared stop events, while the main thread keeps
  polling the job and reporting progress/heartbeats. Uploads stay in the main
  thread and keep the existing order (video segments, audio segments, MP3).
  Sibling failure/cancellation stops the other stage within about one progress
  tick and the primary failure (or `MediaJobCancelled`) is re-raised; the
  internal abort signal never reaches the job record. `media_encode_threads()`
  derives the encoder `-threads` and `-filter_threads` budget from cgroup v2
  `cpu.max` / cgroup v1 CFS quota (rounded up, capped at 16), with
  `MEDIA_ENCODE_THREADS` as an override.
- `compose.application.yml`: media-worker runs
  `--concurrency=${MEDIA_WORKER_CONCURRENCY:-2}` with prefetch 1 and exports
  `MEDIA_ENCODE_THREADS`; comments record the 2-CPU/2-job contract and the
  memory/disk assumptions.
- `learning_platform/settings.py`, `.env.example`: default
  `MEDIA_WORKER_CONCURRENCY=2` and new `MEDIA_ENCODE_THREADS=0` (0 = derive from
  the container quota). Compose owns the worker's actual concurrency; the
  setting documents the default and is not read by application code.

Invariants preserved: one atomic claim per job, `select_for_update` lifecycle
transitions, private staging verification, exact output keys/cleanup, the
`MediaProcessingPhase.UPLOAD_OUTPUT` transition, staging deletion and
publication flow, and per-job temporary directories (the thread is joined
before cleanup). No new dependency.

Verification (10 November 2026): `python manage.py check` passed;
`makemigrations --check --dry-run` reported no changes; changed files pass
`ast.parse`; `audit_text_encoding` reports no violation in the changed files
(it still fails on pre-existing `docs/pending/*` PRD lines, untouched here).
A disposable harness (`tmp_media_smoke`, deleted after use) ran the real
`run_media_job` with real FFmpeg audio stages and a libopenh264 stand-in for
the unavailable local libx264: parallel success (audio overlapped the video
window, 1.9 s wall), video-only and MP3-only branches, `-threads 2` in the
generated command, thread-budget override, audio-failure and video-failure
propagation (0.7 s), and operator cancellation. Local limits: the machine has
12 cores and no cgroup quota (budget fell back to 12), so the 2-core server
behavior and real x264 speed are not measured here.

### Chain and exposure audit (10 November 2026)

Media job exposure was showing less than the database holds: the detail drawer
rendered an empty status pill and hid the progress bar because
`_media_job_ui_state` never set `status_label`/`percent`/`progress_label`; a
failed job displayed the phase it died in ("Encoding") instead of "Failed"; the
live Redis hash could describe a previous attempt (or a crashed worker) and
override DB phase/progress; and the drawer always offered Retry/Stop even when
the row hid them.

Fixed in `management_system/views.py` and
`partials/drawer/media_job_detail.html`: the UI state now exposes the database
status label, a bounded percentage and a phase/percent label; terminal jobs
report Failed/Stopped/Complete; `_live_media_progress` applies Redis values only
when the job is active and the hash attempt equals `attempt_count`; and the
drawer shows the persisted row in detail (outputs/manifests/MP3/count with a
bounded 50-key list, attachment and lesson status, staging timestamps, attempts
and failure history, cleanup state) with the same can_* gates as the row.
New drawer labels are in the Arabic catalogue and `django.mo` was recompiled.

Chain audit on a disposable PostgreSQL 18 database (real ORM paths, no storage
calls; audit script deleted after the run): concurrent `_attach_outputs` from
two workers keeps all links and both media types (3 runs), a video part and an
audio part sharing a curated `part_id` coexist without either type replacing
the other, re-attaching the same outputs fails with `attachment_collision` and
leaves the lesson unchanged, automation parts stay in p01/p02 order when p02
finishes first, concurrent publication publishes the lesson exactly once
(version 1) and the fan-out stays idempotent (one StudentNotification row).
Known harmless artifact: two concurrent fan-outs can both log
`student_count=1` because `bulk_create(ignore_conflicts=True)` hides conflicts
from the pre-insert count; rows remain unique by `idempotency_key`.

### Deploy continuity and durable job ownership (10 November 2026)

A redeploy recreates the media worker, so an in-flight FFmpeg job previously
waited for the 120-second heartbeat recovery before it was queued again, and a
worker killed during an upload could leave exact R2 keys that made the retry
fail as an `output_collision`.

Changed:

- Migration `0115_mediaprocessingjob_worker_id`: `MediaProcessingJob.worker_id`
  records the claiming worker instance (`MEDIA_WORKER_ID`, the container
  hostname by default). `claim_queued_job` writes it; `requeue_stalled_job`
  clears it; the row and the admin drawer/API expose it.
- `media_processing.release_worker_jobs()` locks and returns one stopping
  worker's active jobs to `queued`, clears the claim/heartbeat and leaves
  `last_dispatched_at = NULL` so the next recovery dispatches them. A job with
  a pending operator stop is completed as cancelled instead of restarted.
- `learning_platform/celery.py`: `worker_shutting_down` (SIGTERM sent by Docker
  before the container exits) releases this worker's jobs and deletes their
  live Redis hashes; `worker_ready` enqueues one bounded
  `recover_pending_media_jobs` pass on the media queue, guarded by a 20-second
  Redis `SET NX` key so the containers starting in one deploy do not each add a
  pass. Queued-but-never-dispatched jobs are therefore picked up immediately
  instead of waiting for the next beat tick.
- `media_tasks.process_media_job`: before a fresh attempt re-encodes, it deletes
  the job's own recorded `output_keys` (bounded exact-key helper). Leftovers
  from a killed upload can no longer collide with the retry; a storage failure
  only logs a warning and never blocks the attempt.
- Compose media-worker: `stop_grace_period: 60s` so the released tasks can stop
  FFmpeg and delete their partial outputs before Docker sends SIGKILL;
  `MEDIA_WORKER_ID` is passed through (empty means container hostname).
- Migration `0116_mediaprocessingjob_interrupted_count`: worker releases are
  recorded separately from processing failures. `claim_queued_job`,
  `record_job_failure`, the operator retry gate and the admin `can_retry` gate
  compare effective attempts (`attempt_count - interrupted_count`), so any
  number of deploys/crashes leaves the three real processing attempts intact.
- Redis worker liveness: `media:worker:<worker_id>` (45-second TTL, refreshed by
  a daemon thread every 15 seconds, deleted on graceful shutdown). Recovery
  requeues an active job immediately when its owner's key is gone, guarded by
  the requirement that the recovering worker's own key exists (so a Redis
  restart cannot misread every key as dead); the 120-second heartbeat rule
  remains the fallback.
- `run_media_job` logs a bounded capacity snapshot per job (free disk and
  `MemAvailable`) and warns below 4 GiB free disk, so the two-job headroom is
  visible in `docker logs` instead of being assumed.
- `deploy/blue_green_deploy.sh`: after the admin-worker check, the deploy now
  verifies the recreated media worker is running and answering as a consumer of
  the `media` queue.

Hard kills without SIGTERM (OOM, host crash) are detected by the missing
liveness key (within its 45-second TTL plus the 60-second beat tick); if Redis
was also restarted, the heartbeat rule (120 seconds) still recovers the job.
Interrupted runs never consume a processing attempt.

The drawer now shows every persisted operational field: job id, source key,
ETag, SHA-256, upload deadline, last dispatch, stop request/acknowledgement,
interrupted runs, cleanup groups/result and the complete failure history, with
up to 200 output keys listed. `docs/pending/lms_student_intelligence_architecture_prd.md`
was normalized to ASCII punctuation so `audit_text_encoding` passes for the
whole tree (746 files).

Verification (disposable PostgreSQL 18, container removed afterwards): the
migration applies, claim stores worker/attempt, a foreign worker cannot release
the claim, shutdown release yields `queued` with no worker/dispatch/heartbeat,
the next worker resumes at attempt 2, a stop-pending job is cancelled not
requeued, beat recovery clears the worker, the retry deletes exactly its own
recorded keys (and a storage failure does not block it), and the API/drawer
expose the worker. The follow-up audit on a second disposable database covered
four deploy interruptions without consuming an attempt, the attempt limit after
three real failures, the Redis liveness mark/guard, immediate resumption of a
dead worker's fresh-heartbeat job, the live worker's job being left alone, and
the full drawer fields. `manage.py check`, `makemigrations --check --dry-run`
("No changes detected"), `audit_text_encoding` (746 files) and compose YAML
parsing pass.

Encoder scaling was measured in a `debian:bookworm-slim` container capped at
`--cpus=2` with the production profile (854x480, 120 s, `-preset fast`,
CRF 23, 448k cap, HLS 6 s): `-threads 1` took 22.8 s and `-threads 2` took
13.1 s, a 1.74x gain. The production server still needs its own measurement.

Risks / next safe step: two concurrent jobs need disk headroom for two staged
sources plus segments (the new per-job capacity log reports it) and 2 GiB
memory headroom (the same log reports `MemAvailable`); deployments that never
set the new env vars keep the compose defaults. Deploy, then measure one real
lesson with `docker exec app-media-worker-1 ps -o pid,nlwp,pcpu,cmd -C ffmpeg`
and `cat /sys/fs/cgroup/cpu.stat` before/after to confirm two encoder threads
and low throttling; tune `MEDIA_ENCODE_THREADS`/concurrency from that evidence.

## Implementation unit (10 November 2026) - chat workspace and stale-asset root cause

### Reported 1: empty band under the admin body on short pages

Not reproducible on the current tree. Measured in Chromium on the disposable
schema at 1919x1031 and 1366x768 for `background-jobs` and
`attendance-management`: `.aw-shell` and `.aw-rail` bottoms equal the viewport
bottom in every case, and the deployed
`https://bibleinstitute-eg.org/static/css/admin_workspace.css` is byte-identical
to the working tree (it already carries `.aw-shell { min-height: 100vh }`). The
operator screenshots therefore show an older stylesheet, not a layout defect.

Root cause of "a fix that never reaches the browser": nginx serves `/static/`
with `Cache-Control: public, immutable` for any non-empty `?v=` value
(`map $arg_v` in `nginx/default.conf`) while `STATIC_ASSET_VERSION` was the
constant `dev` from `.env`. An immutable response is never revalidated, so every
client kept the pre-fix CSS and JS for the 7-day `expires` window.

Fix: `learning_platform/settings.py` now derives `STATIC_ASSET_VERSION` from the
newest modification time plus file count of the owned `css`/`js` trees
(`management_system/static`, `static`) when the env var is unset or `dev`; an
explicit non-`dev` value still wins for release pipelines. The token changes on
the next deploy that ships a changed asset and stays stable otherwise. No
template, static path or nginx rule changed.

### Reported 2: Telegram chat broken under HTMX/Alpine and on refresh

Proven root causes:

- `partials/telegram_chat_panel.html` included
  `partials/telegram_conversation_list.html` while every trigger swapped the
  panel with `hx-swap="innerHTML"`. Opening a conversation injected a **second**
  conversation list (duplicate id) inside the panel, so the panel grid received
  three children (list, thread, context) in a two-column track.
- The conversation page used a separate `aw-chat-panel--full` grid with a third
  copy of the list, so HTMX and a hard refresh produced different DOM.
- No `hx-push-url` on a row: the address bar stayed on the inbox, so refresh
  lost the open conversation.
- The start-conversation modal search fetched the whole inbox page on every
  keystroke and used `hx-select` to throw it away.
- The post-swap handler only re-initialised Alpine for `#display-page`,
  `#page-region` and `#content`, never for the swapped chat panel.

Reworked contract (one canonical structure):

- New `partials/telegram_chat_layout.html`: conversations column plus the
  panel. Both the inbox and the conversation page include it; the column is a
  page sibling of the panel on both routes, so a panel swap cannot duplicate it.
  `chat_push_url` is `true` on the inbox (filters are page state) and `false`
  on a conversation page (the URL keeps the conversation).
- `partials/telegram_chat_panel.html` owns its root
  `<section id="telegram-chat-panel" class="aw-chat-panel is-open">`; the empty
  state is the same root with `is-empty`. Every panel trigger (row link, claim,
  release, mark-read, reply composer, message reply/delete) now uses
  `hx-target="#telegram-chat-panel"` + `hx-select="#telegram-chat-panel"` +
  `hx-swap="outerHTML"`, so the open/empty class always matches its content and
  a full-page response can never be poured into the panel.
- Conversation rows push the clean detail URL (`hx-push-url`), so refresh and
  Back render the same workspace; Back restores the empty inbox.
- `telegram_conversations` gained `?fragment=linked-users` returning only
  `partials/telegram_linked_user_list.html`; the modal search and its clear
  action use it.
- `active` conversation id: the list marks the open row (`is-active` +
  `aria-current`) and carries `active` in its poll URL, so a poll keeps the
  highlight. `active` is excluded from `pagination_query`.
- `_conversation_detail_context` gained `include_conversation_list` (default
  false); only the page render builds the list context. Panel 12 queries (was
  17), items 12 (was 17), poll 10 (was 15).
- `admin_shell.js` re-initialises Alpine on the swapped panel and pins the
  thread to the newest message; `telegram_chat_pagination.js` disconnects
  observers whose scroll root was detached by a swap.
- CSS section 20 rewritten: `.aw-chat-layout` is grid `300px | 1fr` with
  `flex: 1 1 auto` inside a viewport-height `.aw-page--chat`; the panel is
  `1fr | 320px` when open and one column when empty (2 columns on screen when
  empty, 3 when a conversation is open). `.aw-chat-list`, `.aw-chat-thread` and
  `.aw-chat-context` each own `overflow-y: auto` with hidden scrollbars and
  `overscroll-behavior: contain`. Chat head no longer squeezes the name
  (wrapping actions, truncated identity); composer uses a 2-row textarea and a
  single truncated limits line whose full text stays in `title` and
  `aria-describedby`.
- `TelegramSupportReplyForm.message` textarea `rows` 4 -> 2.
- Message bubble anatomy (10 November follow-up): one bubble now reads
  head -> text -> timestamp -> flags -> actions. `.aw-msg-head` carries the
  author (`telegram_display_identity` for a student, "Admin (@handle)" for an
  operator), a separator and the delivery/origin pills; `.aw-msg-text` is the
  body; `.aw-msg-time` is its own muted line with `created_at` and `sent_at`;
  `.aw-msg-flags` holds the awaiting/answered, digest-alert, Telegram-deletion,
  delivery-error and removed-by states; `.aw-msg-actions` holds reply and the two
  delete forms. The old `.aw-msg-meta` soup (sender, timestamp, both pills,
  handle, sent time and every alert on one line) is gone. Empty flag/action rows
  collapse via `:not(:has(> *))`, so a plain sent reply has no dead row.
- Page band under `#display-page` (10 November follow-up): `.aw-workspace`
  gained `flex: 1 1 auto`, so the page claims the rest of the `.aw-main` column
  instead of ending above a bare canvas band. Measured on `background-jobs`:
  workspace bottom == viewport bottom at 950x900, 1280x800 and 1919x959 (before:
  862/900, 790/800, 681/959). No element overflows the page at those widths
  (`wideElements` 0 at 950; at wider sizes only the rail tooltips, which are
  invisible and clipped by `html { overflow-x: hidden }`); the wide table keeps
  scrolling inside `.aw-table-wrap`. The canvas that remains below a short page
  card is the page's own bottom padding plus the prototype's empty canvas - the
  card is content-height by design and was not stretched.
- Same check applied to `notification-history` (18 seeded notifications with
  long Arabic titles, 950x900 / 1280x800 / 1919x959): no horizontal overflow
  (0 elements wider than the page at 950; only the invisible rail tooltips
  above), the long title wraps inside its `.aw-break` cell, and the page is
  taller than the viewport so the fill question does not apply. The table is
  capped at `max-height: 68vh` exactly like the prototype `.table-wrap`, so a
  full page scrolls rows inside the table and scrolls the page for the pager;
  the cap is already removed below 991.98px. Not changed: it is prototype
  parity and shared by every admin table.
- Real defect found there and fixed: `partials/background_jobs_content.html`
  wrapped the whole table card in `{% if jobs_page %}`. A `Page` with zero
  objects is falsy, so any filter that matched nothing rendered the head and the
  filters and then nothing at all - no table, no empty state (reproduced with
  `notification-history?q=no-such-notification-zzz`). The guard is now
  `jobs_page is not None`, and the page renders its table card with the empty
  row. The same template also gained notification-specific labels instead of
  job wording: "No notifications match these filters." (with the bell icon),
  "Matching notifications: %(count)s", caption "Notification history" and pager
  aria "Notification history pages"; the jobs page keeps its own four labels.
  Arabic entries were added and the catalogue recompiled (`msgfmt -c` clean).

### Notification history grouped by message (10 November 2026)

The page listed one row per recipient, so a broadcast to 11,219 students was
11,219 rows. It now lists one row per **message**; opening a row lists that
message's recipients.

- Group key: `NOTIFICATION_GROUP_FIELDS` in `notification_history.py`
  (notification_type, entity_id, offering_id, announcement_id, both effective
  windows, title/body in both languages). `notification_message_identity()` is
  the one helper for that tuple and `original_audience()` now uses it too, so
  grouping and redelivery target the same set.
- `notification_history_groups()` groups in SQL and returns one row per message:
  `group_id=Min(pk)`, recipients, distinct inbox/push/Telegram totals by status,
  created first/last, scheduled/cancelled/withdrawn/redelivery counts and a
  `group_status` Case (`withdrawn`/`cancelled` when every recipient is,
  `queued` when all are still scheduled, `running`, `failed`, `partial`,
  `completed`). Filters: operation, group status (including
  `delivery_failed`), title-or-kind search, since/until, announcement and
  redelivery job.
- New page partial `partials/notification_history_groups.html`: one flex row per
  message (2-line title + kind, recipient count and outcome badges, status pill,
  progress bar, created) and a "View recipients" button. The recipients open in
  the **shared peek drawer** (`data-aw-drawer` on the button, message title as
  the drawer title), not inline.
- New fragment route `notification-group-recipients`
  (`/dashboard/notification-history/group/<id>/recipients/`) answers three
  shapes from one bounded page of 25 recipients: the drawer body
  (`partials/notification_group_recipients.html`, search box + list container),
  the search result (`?fragment=list`, `<ul>` + rows + sentinel) and the next
  scroll page (`?fragment=more&page=N`, `<li>` rows + sentinel only, so it
  appends inside the existing list). `?search=` narrows **within** that message
  by name/username/email; non-HTMX hits redirect and unknown ids 404.
- Scroll pagination lives in the shared shell: `admin_shell.js` observes
  `[data-aw-load-more][data-url]` and swaps the sentinel (`outerHTML`) when it
  scrolls into view, then rescans after every swap and after the drawer body is
  populated. Requests are serialized per sentinel, so the drawer never fires a
  second page while one is in flight. No pager is rendered inside the drawer.
- Projections `readable_notification_group` and
  `readable_notification_recipient` live in `job_monitor.py` beside
  `readable_job`; each recipient row links to the existing per-recipient detail
  page.
- The jobs route is untouched: it still renders the flat table (and the
  `is not None` empty-state fix above). The notification route no longer renders
  that table.
- Drawer head titles are clamped to two lines (`#aw-drawer-title`), so a long
  message title never eats the list below it.
- Fixed while verifying: `NormalizedContains` emitted `LIKE %s` without an
  ESCAPE clause on non-PostgreSQL, so any search containing `_` (usernames,
  storage keys) matched nothing locally while working on PostgreSQL, whose LIKE
  defaults to backslash escaping. The non-PostgreSQL branch now emits
  `ESCAPE '\\'`, so both backends agree.
- Arabic: "Search messages", "Delivery progress", "Each row is one message...",
  "View recipients", "View the recipients of %(title)s", "Recipients:
  %(count)s", "Search recipients", "Loading more recipients...", "This message
  has no saved recipients.", "No recipients match this search.", "cancelled",
  "pending", "redelivered", "withdrawn" added and compiled.

Verification: `tmp/ui-audit/verify_notification_groups.py` (3 seeded messages /
49 recipient rows, 31 in the largest): 3 grouped rows and no flat rows, one
drawer trigger per message, drawer body with 25 rows + search + a
`fragment=more&page=2` sentinel, next page appending 6 rows with no second list
and no trailing sentinel, list-shaped search responses (name, username, literal
`%`), guards (302/404), filters (title, queued, delivery_failed, operation) and
the untouched jobs page. `tmp/ui-audit/verify_notification_drawer.py` (Chromium):
opens the drawer from the row, 25 rows, scrolling the drawer body appends to 31
and clears the sentinel, search narrows to 1 without closing, clearing restores
25, Escape closes, no console errors. Query budget: grouped page 8 statements,
drawer body 6. `manage.py check` clean; `makemigrations --check` no changes;
195 templates compile; `node --check` on `admin_shell.js`; admin sweep
36/36 -> 200; scoped encoding audit clean. Screenshots:
`tmp/ui-audit/notif-groups-1919.png`, `notif-drawer-1919.png`,
`notif-drawer-scrolled-1512.png`, `notif-drawer-390.png`.

Known cost: the group key includes the two body texts, so one page load
aggregates the whole `StudentNotification` table (11,219 rows on production) and
sorts the groups. That is a bounded admin read, but if the table grows by an
order of magnitude the next step is a content-hash group column with an index
(a migration), not a wider query.

### Notification withdrawal ("unsend") from the history (10 November 2026)

Withdrawal already existed as a per-recipient admin action, but its only button
lived on a recipient's delivery detail page, so it was invisible from the
notification history. The history now exposes it.

- Per message: each grouped row carries a **Withdraw** action
  (`notification-group-withdraw`, POST) that hides once every recipient is
  withdrawn. It counts the pending recipients, creates the durable
  `AdminOperationJob` kind `NOTIFICATION_WITHDRAWAL`
  (new migration `0117_alter_adminoperationjob_kind.py`), enqueues it on the
  admin queue, and redirects to that job's status page so progress is visible.
  Repeating it while a job runs returns the same job (conflict key
  `notification_withdrawal:<message id>`); with nothing left it returns to the
  history with an informational message.
- Per recipient: `execute_notification_withdrawal` in
  `notification_withdrawal.py` walks the message's not-yet-withdrawn rows by
  primary-key batch (100) and calls the existing canonical
  `withdraw_student_notification` for each, so inbox hiding, queued
  push/Telegram cancellation and 48-hour bot deletion keep exactly one rule. It
  commits progress and the cursor through `assert_admin_operation_progress`,
  resumes from `job.cursor`, and finishes COMPLETED, PARTIAL or FAILED with the
  withdrawn/skipped/failed and Telegram deleted/expired/failed counters.
- Keep per-recipient withdrawal available from the recipient detail page; it is
  the same service, so a single student can still be withdrawn without a job.
- Provider reality (unchanged): the inbox row disappears, pending sends are
  cancelled, Telegram copies are removed only inside the 48-hour window, and a
  delivered mobile push is dismissed by the app on its next sync.
- Arabic labels added and compiled: "Withdraw", "Notification withdrawal", the
  confirmation, "Withdrawing this notification for %(count)s recipient(s)...",
  "Every recipient of this notification is already withdrawn.", "You are no
  longer allowed to withdraw notifications.", "Invalid notification message."

Verification: `tmp/ui-audit/verify_notification_withdrawal.py` (disposable
schema) - the row exposes the action, the POST creates the job with the
recipient total and hands it to the admin queue, `run_admin_operation` finishes
it COMPLETED with 31/31 (and 8/8 on the queued message in an earlier run), every
recipient row gains `withdrawn_at`/`withdrawn_by`, queued push and Telegram rows
become `skipped`, the group reports its withdrawn count and drops the action,
a repeat does not queue a second job, and a student gets 403. No broker exists
in the disposable environment, so the verifier stubs `enqueue_admin_operation`
and runs the real task function; the view path is the same one every other
admin operation uses. `manage.py check` clean, `makemigrations --check` no
changes, 197 templates compile, chat/notification/drawer/overflow suites green,
admin sweep 36/36 -> 200, scoped encoding audit clean.

### Scanner records immediately and phones keep their labels (10 November 2026)

Two units: the admin scanner no longer asks for a mode per scan, and phone
layouts no longer drop control labels.

**Phone labels (option A1).** `admin_workspace.css` used to hide every
`.aw-page-actions .aw-btn span` at `max-width: 720px`, leaving icon-only
buttons whose icons are `aria-hidden`, so the controls had no visible and no
accessible name (measured: 3 unnamed page actions on attendance and students,
2 on background jobs, 1 on notification history; labels returned at 721px).
The rule now keeps the label in a compact form (10.5px, tighter padding, icon
14px, wrap allowed); the same treatment keeps the promotion stepper step names
(previously `display: none` under 720px), and the quiz table-tools
`.column-controls` wraps instead of clipping the second action. The topbar
`Create`/notifications/background-jobs buttons already carried `aria-label`, so
the intentional topbar compaction (breadcrumb, user name, search placeholder,
Ctrl-K hint) is unchanged. Audit: `tmp/ui-audit/audit_admin_mobile.py` walks all
admin routes at 390px and 720px; unnamed visible controls are now 0 on every
page audited, and the phone screenshots keep every action readable.

**Scanner (immediate save, exit as a one-shot toggle).** The camera opens on
load (the previous "Open Camera" button only appears when the browser blocks
auto-start, e.g. a permission or gesture rule). A decoded `/scan/<token>/`
code is posted with fetch to the existing `record_attendance` endpoint
(`POST /scan/<token>/entrance|exit/`) instead of navigating to
`scan_preview`; the response prints in `#scan-feedback` over the running
camera (name, action, course, time; error tone for 400/409), a short WebAudio
tone and vibration fire, and the recent-scans table gets the new row prepended
(bounded to 6). `#scan-mode-toggle` arms exit for exactly one scan, shows the
armed state in the chip/toggle/frame, and returns to entrance after the attempt
whether it succeeded or was rejected. The same decoded code is skipped for five
seconds client-side (`CODE_CACHE_MS`), a single request is kept in flight with a
one-slot pending queue, and the detection downscale is 720px. `scan_preview`
stays unchanged for direct links; the manual lookup form now posts with fetch,
gets `{token, student}` from a JSON branch in `student_lookup`, and records
immediately with the current mode. `record_attendance` answers now include
`student {name, username}` and `course`, including on 400/409 payloads, so the
banner can name the student for rejections.

**JavaScript catalogue language.** `/jsi18n/` sat outside `i18n_patterns`, so
its language came from the session cookie: an English page received the Arabic
catalogue and every `gettext()` string drawn by JS (scanner feedback included)
rendered Arabic. The route now lives inside the localized patterns;
`{% url 'javascript-catalog' %}` yields `/en/jsi18n/` or `/ar/jsi18n/` and the
catalogue matches the page language (verified: "Entrance" -> "Entrance" in en,
-> Arabic in ar).

Files: `views.py` (record_attendance payload, student_lookup JSON branch),
`static/js/scanner.js` (rewritten), `templates/partials/scanner_content.html`,
`static/css/admin_workspace.css` (A1 labels, stepper labels, tool wrapping,
scanner feedback/mode styles), `learning_platform/urls.py` (catalogue route),
`locale/ar/LC_MESSAGES/django.po` + compiled `.mo` (13 new entries).

Verification: `tmp/ui-audit/verify_scanner_flow.py` drives a fake Chromium
camera with a stubbed `jsQR` queue at 1440x900 and 390x844: camera starts with
no button press, one entrance POST per code, the same code is not posted again
inside five seconds, exit arms and posts `exit` then resets to entrance, the
duplicate entrance returns the error banner and keeps one row, the manual
lookup records and stays on the scanner, recent rows prepend and stay bounded,
and page actions/stepper keep their labels on phones. Also green: `manage.py
check`, `makemigrations --check` (no changes), chat/notification/drawer/
overflow/withdrawal suites, admin sweep 35 routes -> 200, `node --check` on both
admin scripts, scoped encoding audit (536 files). Screenshots:
`tmp/ui-audit/scanner-immediate-1440.png`, `phone-quiz-labels.png`,
`phone-scanner-ar.png`.

Risk: iOS Safari can require a gesture before `getUserMedia`, which is why the
fallback button remains; the five-second code cache is per page load and does
not replace the server's duplicate rule.

Review pass after the first push (same day) found and fixed four defects the
browser test could not catch:

- a failed `video.play()` or a second start request left an orphan camera
  stream running (the camera indicator stayed on); every start now stops an
  existing stream and a failure stops the tracks it just opened;
- an HTML answer (login redirect for an expired session) was parsed as `{}`
  and counted as a recorded scan; only a `status=recorded` JSON answer is
  treated as success, and a 403 shows "Your session ended. Sign in again and
  reload the scanner." without writing anything;
- the live recent row printed the raw `scan` enum value instead of the display
  label; the response now carries `source_label`;
- a deliberate lookup inside the five-second cache window failed silently; it
  now reports "This student was scanned a moment ago." while a camera re-read
  of the same code stays quiet.

Also hardened: a malformed percent-escaped token skips the rejection message
(now caught), the action placeholder is replaced before the token so a crafted
token cannot consume it, the lookup error path distinguishes a refused session
from an unknown student, and `get_student_attendance_context` prefetches
`course_offering__course` so naming the course in the banner adds no query per
scan.

### Fixed: opening the drawer left a gap under the shell (10 November 2026)

Reported as an empty band and a stray scrollbar when the drawer opens on the
attendance and notification-history pages.

Root cause: the drawer locked the page with `body.aw-drawer-open
{ overflow: hidden }`, which removes the scrollport the sticky rail and sidebar
pin to. The page kept its scroll offset, so both columns were laid out at that
offset (measured: rail top -206, bottom 724 in a 930px viewport) and the shell
stopped 206px above the bottom - the visible band. `scrollbar-gutter: stable`
then also reserved the 17px gutter while the scrollbar was gone, shrinking the
shell to 1909px under a 1926px overlay (a seam at the inline end).

Fix: the page is locked by pinning it to the top instead of hiding the overflow
in place. `admin_shell.js` remembers `scrollY`, adds `html.aw-scroll-locked`,
scrolls to 0 while the drawer covers the page, and restores the offset on close;
the CSS locks `overflow` on the html/body pair and releases the gutter while
locked (`scrollbar-gutter: auto`), so the shell keeps the full window width and
the rail/sidebar keep spanning the viewport. No template or query changed.

Evidence (`tmp/ui-audit/verify_all_drawers.py`, Chromium, page scrolled to
180-240px before opening, at 1919x987 and 1440x900): every drawer entry point -
attendance row, notification recipients, app-release form, banner form, media
job, Telegram broadcast and the calendar day agenda - opens with `scrollY` 0,
rail and sidebar spanning the full viewport, `documentElement.scrollWidth ==
innerWidth` (no horizontal overflow), drawer content `scrollWidth <=
clientWidth`, and no descendant painting outside the drawer; Escape restores the
operator's scroll position and the rail still spans the viewport.
`tmp/ui-audit/verify_drawer_overflow.py` covers the two reported pages with the
same assertions plus a horizontal-overflow check after close. `manage.py check`
clean, `makemigrations --check` no changes, `node --check admin_shell.js`, all
other suites green, admin sweep 36/36 -> 200, scoped encoding audit clean.

### Fixed: table rows leaked the clipped table height into the page scroll (10 November 2026)

Reported as "adding rows to `tbody` adds empty space instead of scrolling
inside the table" on background jobs and attendance (any list table).

Root cause: `app.css` defines `.visually-hidden { position: absolute; ... }`,
and the shared table markup renders one hidden label per row
(`<span class="visually-hidden">: <row label></span>`) plus a hidden `<caption>`.
Nothing inside `.aw-table-wrap` was a positioned ancestor, so those boxes took
the viewport as their containing block: an absolutely positioned box is **not
clipped by an ancestor that is not in its containing-block chain**, so their
document-positioned 1x1 boxes extended the page scroll height by the hidden part
of every row. The wrap itself scrolled correctly all along - the page simply
grew underneath it, which is why the "space" scaled with the row count and the
page could scroll far past the card.

Fix: `body.aw-admin .aw-table-wrap` and `body.aw-admin .aw-matrix-wrap` are now
`position: relative`, so the scroll region is the containing block for its
absolute descendants and clips them like everything else. No markup, JS or
query changed.

Evidence (`tmp/ui-audit/verify_table_overflow.py`, background jobs with 25 rows):
`documentElement.scrollHeight` went 1754 -> **1133** at 1919x1034 (content
height), 1815 -> **1103** at 1440x900 and 2530 -> **1208** at 1150x1034; the
wrap still scrolls internally (scrollHeight 1429 vs clientHeight 703 at 1919),
scrolling it to the end reveals the last row, and `window.scrollY` stays 0.
Forcing `position: static` on the wrap in the console re-creates 1815 and
restoring the rule returns 1103, which pins the cause to that rule. The users
page (1059/1027) and the attendance/grade matrix wrapper (styled
`position: relative`) are covered too. `manage.py check` clean,
`makemigrations --check` no changes, 197 templates compile, admin sweep
36/36 -> 200, scoped encoding audit clean.
- Mobile: `.aw-page--chat` height is `calc(100dvh - var(--aw-chat-offset))`
  where the token is the top bar plus the active breakpoint's workspace padding
  (116 default, 134 at 991.98, 122 at 720). Below 1180px the student rail is
  hidden; below 900px the workspace shows **one pane at a time** - the inbox
  fills the screen with the conversations column and an open conversation fills
  it with the thread. The empty panel is hidden, the page head yields its height
  while a conversation is open, and a phone-only back control in the chat head
  (`.aw-chat-back`, chevron mirrored for RTL, canonical `#display-page` +
  `#page-region` navigation) returns to the inbox. The page head description is
  hidden at 720 and chat inputs use 16px there so iOS does not zoom on focus.
  The width queries are declared after every base chat rule, because rules that
  set `display` (rail, empty panel, head) otherwise win on source order.

Arabic: added "Conversations", "Quick links", "Search conversations",
"Student context" and "Back to conversations"; the chat template labels now
resolve in Arabic (`tmp/ui-audit/check_chat_msgids.py` reports 0 missing).
`django.mo` was compiled with `manage.py compilemessages -l ar` after
`msgfmt -c` passed, which also clears the previously stale catalog.

Verification (host Python 3.11 / Django 5.2, disposable SQLite
`chat_repro.sqlite3`, Chromium 157):

- `manage.py check` clean; `makemigrations --check --dry-run` -> no changes;
  193/193 templates compile; scoped `audit_text_encoding` clean
  (`management_system` 485, `locale` 2, `learning_platform` 6 files). The
  repo-wide audit remains red only on the pre-existing
  `docs/pending/lms_student_intelligence_architecture_prd.md`.
- `tmp/ui-audit/verify_chat_layout.py`: 60 assertions over the inbox, the
  conversation page, panel/items/poll fragments, list pagination (25/page),
  active-row survival, linked-user fragment and the non-HTMX redirects.
- `tmp/ui-audit/verify_chat_dom.py`: AR and EN, 0 duplicate ids, 0 unlabelled
  fields, 0 nameless buttons, no multi-line `{# #}` comment.
- `tmp/ui-audit/verify_chat_layout.py` also asserts the bubble anatomy (head,
  text, time order; a delivery pill and a `<time>` on every bubble; no
  `.aw-msg-meta`), and `tmp/ui-audit/probe_jobs_overflow.py` reports the
  page/viewport bottoms and any overflowing element.
- `tmp/ui-audit/smoke_chat_posts.py`: reply, mark-read, claim and release each
  return exactly one panel root, no list and no full page.
- `tmp/ui-audit/review_chat_queries.py`: query budget above.
- `tmp/ui-audit/verify_chat_browser.py` (Chromium, real dev server): shell and
  rail reach the viewport bottom at 1919x1031 and 1366x768; inbox is two
  columns with an internally scrolling list and no document scroll; opening the
  45-message conversation yields three non-overlapping columns in one row,
  thread scrolls and opens at the newest message, URL follows the conversation;
  the message poller actually fires after the panel swap and never injects a
  full page; Back restores the empty inbox; hard refresh renders the same three
  columns; search narrows and restores the list; 1919/1280/1024 widths keep the
  list and thread side by side, and at 390x844 and 768x1024 the inbox fills the
  screen with the conversations pane, tapping a row swaps to the thread (page
  head hidden, back control visible, composer inside the viewport), the thread
  scrolls to the newest message, the document does not scroll, and the back
  control restores the conversations pane. No horizontal overflow at any width.
- Admin route sweep 36/36 -> 200.

Database: none. No model, migration, schema, data, permission, route or
dependency change. The disposable `chat_repro.sqlite3` (gitignored) is local
verification data seeded by `tmp/ui-audit/seed_chat.py`; re-running the chat
scripts needs that seed.

Known limits: real Telegram send/receive, media and attachments were not
exercised (no bot token; that path is unchanged). The conversation list stops
polling after loading page 2+ (pre-existing bounded behaviour). Filtering the
list on a conversation page intentionally does not push a URL, so the active
row highlight is not restored by that later list swap (the panel head still
identifies the student).

## Latest documentation unit (9 November 2026) - student PWA specification

Created and indexed [the PWA specification](docs/pending/PWA_OFFLINE_LIBRARY_PUSH_PASSKEY_SPEC.md).
It maps current Django/mobile owners and describes the student-facing scope:
installability, Web Push, a page for explicitly downloaded audio/PDFs and
WebAuthn passkey login. The document includes diagrams, proposed source
changes, browser/server tools, candidate dependencies requiring explicit
approval, proposed .env variables and self-test steps.

Follow-up (10 November 2026): the user confirmed that the PWA/offline library
remain student-only while passkeys cover all browser accounts. Offline media is
encrypted at rest and unlocked by one local user-verification step; biometric
or OS PIN/passcode is accepted. Keep access available through backgrounding and
inactivity until explicit logout/account switch; disable offline media if
secure encryption/unlock support is unavailable. Best-effort protection does
not prevent screenshots or extraction after decryption. Clear downloads on
logout/account switch and remove revoked media on the next online access check.
Recheck server media authorization on every request; password login may access
online media. Push covers all eligible student notifications with global opt-in,
generic lock-screen text and authenticated related-page navigation. Use native
PDF viewing where available and browser quota reporting. Production WebAuthn
origin: https://bibleinstitute-eg.org; staging origin remains to be supplied.
VAPID subject URI is optional. The user has not approved candidate dependency
additions.

No application code, dependency, .env, migration, deployment or database was
changed. The pending spec is git-ignored by the repository policy. The scoped
encoding audit ran on docs/pending and found no violation in this spec; it
exited nonzero for 60 existing typographic-character lines in
lms_student_intelligence_architecture_prd.md. The file-specific audit argument
is unsupported because --path requires a directory. Next safe step: review
candidate libraries and browser/authenticator local encryption support before
requesting package approval or implementing; obtain the staging origin.
The related pending index is docs/pending/README.md.

## Previous documentation unit (8 November 2026) - student intelligence review

Created [the review](docs/pending/STUDENT_INTELLIGENCE_PRD_REVIEW.md), indexed in
`docs/pending/README.md`. It fact-checks the PRD against current source, records
the owner's decisions D1-D10, and measures the revised gate, pgvector, PG18
Arabic FTS and a Celery burst in a disposable sandbox. Decisions: weekly quizzes
lock from attempt start until submission while finals lock while visible;
submission unlocks; exact scan plus `arabic` FTS for MVP; single web slot outside
deploys; subject-scoped canonical reuse with review; transcripts blocked with the
assistant during assessments; 4 questions per message and 12 per hour; one-subject
pilot. The gate is one SQL statement (1.55 ms p50 weekly scope, 1.71 ms diploma
scope) and matched a Python reference 300/300. No implementation was started.

The implementation branch lives in a separate worktree:
`D:\Bible Institue\LMS\lms\bibleinstitute-student-intelligence`, branch
`feature/student-intelligence` at 8f45e13, with the instruction files, docs and
sandbox copied in and `.env` deliberately not copied. Sandbox evidence is
`tmp/study_intel_sandbox/` (disposable, not tests). No application, schema,
dependency, provider, commit or migration change. The repo-wide
`audit_text_encoding` remains red only on the PRD's typographic punctuation
(pre-existing).

## Student experience prototype (9 November 2026)

Created the standalone [student-redesign prototype](prototype/student-redesign/README.md)
with [route/contract audit](prototype/student-redesign/CONTRACT-AUDIT.md). It
covers ten dynamic authenticated student views (My learning, course workspace,
lesson, quiz states, calendar, profile/QR, notifications, graduates, graduation
album and Android app download) plus a review hub. Public
Home/About/Program, the admin workspace and all Django code were left untouched.
The prototype is static, uses no external dependency and introduces no route,
schema, data, permission or provider change. Generated pages are built by
`prototype/student-redesign/_build/build.py`; real Arabic pages are generated by
`_build/translate_ar.py` into `prototype/student-redesign/ar/`.

Resolved decisions: `/courses/` remains the landing page; graduates and app
download are included; the later production port keeps the existing `sd-*` /
Psalmodia layer; progress/average values use existing bounded facts or fall back
to counts/omit; mobile uses bottom tabs at 720px and below.

Review pass (9 November 2026): all 16 operator comments are included. Arabic now
uses a larger Tahoma/Segoe UI reading stack; filled buttons and pills use
stronger contrast; My learning has collapsible year groups with previous years;
the wide next-meeting card is present; course content is direct lectures plus a
separate quiz group; lesson parts are stacked with no
tabs and a PDF viewer with production-style controls; the quiz has no exit X or
Save and exit, has high-contrast selection, unanswered-question validation and
detailed question/answer review; calendar stays on the active scope.
Second review: lesson About/Lesson outline removed; quiz navigator is side-column
on desktop and below-question on mobile; ordering/matching review is line-by-line
with the student answer; warning/info student alerts added with per-banner
dismiss; course row separators strengthened and Arabic content text enlarged.

Validation: 11 English and 11 Arabic pages at 1440/1024/768/390/360 with no body
overflow or console errors; Arabic pages are `lang="ar"` / `dir="rtl"` with no
untranslated visible English except technical tokens; language switch, quiz
states, gallery lightbox and other interaction probes pass; HTML parser finds no
duplicate IDs or missing local links; `node --check` passes; scoped
`audit_text_encoding --path prototype/student-redesign` passes all prototype text
files. The repo-wide encoding audit remains red only on the pre-existing
`docs/pending/lms_student_intelligence_architecture_prd.md` typography.

## Admin UI rebuild program (started 9 November 2026)

The operator approved the full audit recommendations: rework the admin UI/UX to
match `prototype/admin-redesign` exactly, admin only, with business logic,
schema, routes and permissions unchanged. Two settled decisions from that
approval:

- `crispy-bootstrap5` is removed from the admin rendering path and replaced by
  one `aw_field.html` partial plus one form mixin that assigns `aw-*` widget
  classes in Python. The package stays installed only until the non-admin
  surfaces migrate; that removal is its own tracked step.
- Every prototype surface that is absent today must exist in its prototype
  place (drawers, context strips, KPI strips, tabs, steppers), and page content
  must not absorb what belongs in a drawer.

Controlling documents: [the phased plan](docs/current/ADMIN_UI_REBUILD_PLAN.md)
and [the parity audit](docs/current/ADMIN_UI_PARITY_AUDIT_2026-11-09.md).
Phases: P0 foundations (typography, primitives, form contract), P1 shared shell,
P2 Telegram, P3 People, P4 Curriculum, P5 Academic and Attendance, P6 Media,
Reports and Site plus UI kit, P7 removal and full review.

### Completed unit: P0.1 Arabic typography layer (9 November 2026)

- `management_system/static/css/admin_workspace.css` gained section 1b
  "Language typography": 30 rule blocks, 55 `html[lang="ar"]` selectors.
- Arabic now uses the approved reading stack (Tahoma, Segoe UI, Arial) - the
  same decision as the student redesign - with a 15px base and 1.75 line-height.
- Tracking and uppercase are reset for Arabic on every selector that set them:
  eyebrow, sidebar label, sidebar heading, field labels, KPI label, table head,
  matrix head, meta label, storage stat, level group row, message meta.
- Title tracking is neutralised (`aw-page-title` `letter-spacing: 0`,
  `line-height: 1.35`, clamp 26-34px); `aw-kpi-value` is 30px with zero tracking.
- Small labels move above the Arabic legibility floor: eyebrow, side label, KPI
  label and table head 11px; field labels 11.5px.
- Reading and control sizes step up: page description 13.5px, table 12.5px,
  buttons 13/12/13.5, inputs min-height 40px at 13.5px, sidebar links 40px at
  13.5px, chat conversation and message text 12.5px.
- The Latin scale is untouched: base 13.5px, the Latin UI and display stacks and
  every prototype token remain exactly as ported.

Verification: `manage.py check` reports no issues; stylesheet braces balance
(791/791); the new block is Arabic-scoped only (0 Arabic characters added to the
stylesheet); `audit_text_encoding` does not flag the stylesheet.

Next safe step: P0.3 form-rendering contract (`partials/components/aw_field.html`
plus the form mixin in `management_system/forms.py`), then remove crispy from the
nine admin templates that load it. P0.2 primitives are added with their first
consumer rather than in advance, so no unused CSS is introduced.

### Completed unit: P0.3 admin form-rendering contract (9 November 2026)

- `management_system/forms.py` owns widget classes now: `AwFormMixin` walks
  `self.fields` and assigns `aw-input` / `aw-select` / `aw-textarea` /
  `aw-check-input` by widget type through `apply_aw_widget_classes`, preserving
  existing attrs (placeholder, `dir`, `min`, `step`, `accept`, `type`) and
  replacing retired Bootstrap names (`form-control`, `form-select`,
  `form-control-sm`, `form-select-sm`, `form-check-input`). Hidden inputs are
  skipped. Canonical bases `AwForm` and `AwModelForm` sit on the mixin.
- New partials: `partials/components/aw_field.html` (one bound field: label,
  required marker, control, hint, errors; checkbox branch renders the control
  beside its label as `.aw-field--check` + `.aw-check`) and
  `partials/components/aw_form_fields.html` (all visible fields, then hidden).
- Converted forms: `UserCreationForm` and `CourseForm` to `AwModelForm`
  (`UserUpdateForm` inherits), `ApplicationAdminForm` and `SignupSettingsForm`
  to `AwModelForm`, `CSVUploadForm` to `AwForm`. Student and public forms
  (`UserLoginForm`, `SignupForm`, `SignupDetailsForm`,
  `MissingApplicationDocumentsForm`, `AcademicPaymentForm`) are untouched.
  `CanonicalUserAdminForm` is used only by Django admin (`admin.py`), not the
  workspace, and was left alone.
- Converted templates: `signup_settings_content`, `dashboard_form_content`,
  `application_review_content`, `user_detail_content`,
  `user_bulk_form_content`. Each renders
  `<div class="aw-form-grid">{% include "partials/components/aw_form_fields.html" with form=... %}</div>`
  and its action row now uses `.aw-form-actions` instead of Bootstrap
  `mt-3 d-flex`. `{% load crispy_forms_tags %}`, `as_crispy_field` and
  `form|crispy` are gone from the admin shell and the five wrapper templates.
- CSS: `.aw-field` label now sets `margin-bottom: 0` so Bootstrap's reboot
  margin no longer doubles the 4px label gap; added `.aw-field--check`,
  `.aw-check`, `.aw-check input` and `.aw-check-list` beside the existing field
  rules. No new legacy class was introduced.

Verification: `manage.py check` clean; all 179 project templates compile;
`verify_forms.py` reports every sampled widget carrying an `aw-*` class with no
retired class; `render_field.py` renders text, textarea, select and checkbox
fields with the canonical structure, the required marker only on required
fields and the checkbox branch inline; zero `crispy` references remain outside
student/public templates; the five converted partials contain zero
`form-control`/`form-select`/`form-label`/`as_crispy` references;
`audit_text_encoding` clean.

Known open item: `display_quiz.html` still loads `crispy_forms_tags` without
using it. It is the shared student exam template, so it is deferred to the
student surface pass rather than touched here.

Next safe step: Phase 1 shared shell components (pager footer always visible
with info first, bulk bar under the table, drawer trigger contract, real tab
and stepper states, filter-bar consolidation, icon unification in shared
partials).

### Completed unit: P1 shared shell - pager and bulk bar (9 November 2026)

- `partials/pagination.html` rewritten to the prototype anatomy:
  - the footer always renders when `page_obj` exists, so a single-page list
    still reports "Showing 1-5 of 5"; previously the whole footer was gated on
    `has_other_pages`.
  - `.aw-page-info` now precedes the `<nav>` (prototype renders the range on the
    start side and the pager on the end side).
  - chevrons come from the shared sprite (`#i-chevron-left` / `-right`, RTL
    mirrored through `request.LANGUAGE_CODE`) instead of Font Awesome, and the
    "Previous"/"Next" words and Bootstrap `d-none d-md-inline me-1` utilities are
    gone.
  - the ellipsis is a non-interactive `.is-gap` span, not a disabled button.
  - every existing argument is preserved: `pagination_query`, `page_param`,
    `base_url`, `item_label`, `aria_label`, `wrapper_class`, and the HTMX
    pass-through set.
- `partials/table_and_pagination.html`: the `.aw-bulkbar` moved out of the white
  `aw-table-tools` row to directly after the pagination include, so it is a
  sticky bar at the table foot as in the prototype. It is guarded by
  `{% if view != "application" %}` because the applications branch has no
  Alpine root yet; selection, delete, clear, quiz recalculation and the signed
  token are unchanged. The selection label now uses `.aw-count`, and the
  clear/delete controls use sprite icons.
- `admin_workspace.css`: `.aw-bulkbar` is `position: sticky; bottom: 0;
  z-index: 4` with an inline `.aw-count`; the duplicate mobile sticky rule and
  the unused `.aw-bulkbar .aw-clear` rule were removed. Added
  `.aw-table-foot--spaced` for standalone footers and replaced the two
  Bootstrap `wrapper_class="mt-3"` callers (attendance, grade matrix).
- Drawer trigger contract recorded in the plan: `data-aw-drawer` plus optional
  title/subtitle, response body inserted into `#aw-drawer-body` with HTMX and
  Alpine re-initialised; 403 and session-expiry states already handled by
  `admin_shell.js`.

Verification: `manage.py check` clean; 179/179 templates compile;
`render_pager.py` asserts the always-visible footer, info-before-pager order,
sprite chevrons, `is-gap` ellipsis, single-page range and RTL chevron
direction; `assert_bulkbar.py` asserts the bar sits after the pager, outside the
tools row, declared once and guarded for applications; `audit_text_encoding`
clean.

Next safe step: Phase 2 Telegram rebuild (three columns with the student context
rail, list search plus All/Unread/Pending chips wired to the existing
`search`/`status` parameters, reduced row and bubble anatomy, composer inside the
thread column, chat head reduced to the prototype actions).

### Completed unit: P2 Telegram rebuild (9 November 2026)

- Layout is now the prototype surface: `.aw-chat-layout` (290px conversation
  column + fluid panel) with the panel itself split into thread and a 285px
  student context rail. The rail lives inside `#telegram-chat-panel`, so the
  existing panel fragment keeps owning it with no new endpoint.
- Conversation column: `.aw-chat-list-head` carries a search field and the
  All / Unread / Processed chips. Search is live (`input changed delay:250ms`)
  and both controls target `#telegram-conversation-list` with
  `hx-select`/`hx-swap`/`hx-push-url`, using the `search` and `status` query
  parameters the view already supported. Unread shows `unread_count`.
- Row anatomy reduced to the prototype set: avatar, name + time, one-line
  preview, one status pill. Username, level, message count, chevron and the
  second pill are gone; the status branch logic is unchanged.
- Thread: `.aw-chat-thread`, `.aw-msg--in/out` bubbles with a `.aw-msg-meta`
  line, `{% ifchanged %}` day separators, attachments as image/video/audio/
  link. The per-message header row (role + content type + timestamp) and the
  `text-white` direction utilities are gone.
- Composer: `.aw-composer` with `.aw-composer-row` (textarea + send),
  `.aw-composer-actions` (attach + voice recorder) and a hidden `.aw-file-input`.
  Every hx-*, csrf, `reply_to_message_id`, `supportVoiceRecorder` hook and the
  polling controller are byte-for-byte preserved.
- Chat head reduced to avatar + name/level/@username + Student 360 link + the
  existing claim/release/mark-read forms. The Refresh and Back links and the
  two metadata stacks moved into the rail as real `aw-kv` rows (Phone, Email,
  Level, Created, Last message) plus quick links (Student 360, Attendance,
  Grades, Payments) and the admin-only note.
- `telegram_forms.py`: reply widgets moved off Bootstrap names to `aw-textarea`
  and `aw-file-input` (centralized in Python, as agreed).
- JS: `telegram_chat_pagination.js` now dedupes on `[data-chat-user-id]`;
  the conversation poller queries `.aw-chat-list-ul`, which the markup emits.
- Icons: added sprite symbols `i-paperclip`, `i-microphone`, `i-stop`, `i-reply`
  and replaced the Font Awesome paperclip/microphone/stop/reply/alert icons in
  the chat surfaces. Added `.aw-avatar > img` (object-fit round image) and
  `.aw-spin`, which removed all inline `style` attributes from the chat markup.
- Filter-chip filtering, row selection and claim/read/delete endpoints are
  unchanged; no view, model, url, queryset or permission changed.

Verification: `manage.py check` clean; 179/179 templates compile; `node --check`
passes on the chat JS; `verify_telegram.py` reports no `tg-chat-*` in templates
or JS, all five required ids present, the aw-chat class contract present in
markup and zero Bootstrap utility classes; `audit_text_encoding` clean;
stylesheet braces 836/836.

Deferred cleanup (tracked, not a runtime defect): the legacy `tg-chat-*` rules
in `app_admin.css:1274-2130` (855 lines) are now dead and must be deleted in P7.
Templates and JS no longer reference them.

### Completed unit: P3 People (9 November 2026)

Applications
- `applications_dashboard_content.html`: the two extra filter cards are gone.
  The tab strip is a bare `aw-tabs` on the canvas and one `aw-card` with
  `aw-card-body--flush` owns the search row (`aw-table-tools`), the
  `#applications-workspace` body, the table and the footer. The hidden
  `#application-bulk-result` block and every hx/data hook are unchanged.
- `application_review_content.html`: identity band (`aw-identity` with an
  `aw_avatar` xl, name, username/email/phone meta, status + study mode +
  identity chips), an `aw-context-strip` (application number, submitted,
  reviewed by), then `aw-grid--main-rail`: main = edit card, application
  summary (`aw-kv` rows) and documents; rail = Decision card (note and
  enrollment scope always visible, out of the Alpine edit toggle) plus a
  Review checklist. Decision/activate/decline/reopen/delete form contracts are
  unchanged.
- `table_and_pagination.html` **application branches only**: checkbox header
  and cells use `aw-cell-check`, the name cell is an `aw-cell-main` identity
  block, status renders through `aw_status_pill`, actions use `aw-row-actions`.
  Every other view branch is untouched and still carries its legacy classes for
  Phases 4-6.

Students and settings
- `tables/user_rows.html`: `aw-cell-main` identity, `aw-cell-check` and
  `aw-row-actions` replace `column-checkbox` / `column-actions`.
- `dashboard_form_content.html`: `aw-editor-grid` with the form card in the main
  column and an `aw-editor-rail` summary built only from context that exists.
- `signup_settings_content.html`: three named cards (Signup state, Public form
  copy, Closed page) with the new `.aw-switch` toggle for `is_open`; the rail is
  omitted because the view supplies no preview data (noted in the template).
- `historical_intake_content.html`: every Bootstrap row/gutter/column/form-check
  construction replaced by `aw-form-grid` + `aw-field`; the records table uses
  `aw-cell-main` plus pills and keeps the inline promotion form compact.
- `user_detail_content.html`: identity band plus one merged summary card, with
  the actions moved into the page head slot.

CSS added (first consumers in this phase): `.aw-switch` + `.aw-switch-track`
(RTL-aware), `.aw-identity`, `.aw-identity-main`, `.aw-identity-meta`,
`.aw-identity-chips`, `.aw-mt-2`, `.aw-mt-3`, `.aw-doc-image`, `.aw-doc-frame`.

Integration fixes applied by the lead after the two workers returned: replaced
the remaining `ps-inline-form` with `aw-inline-form` in the review page,
converted the document utilities (`img-fluid rounded border`,
`ratio ratio-4x3`, `mt-2`) to `aw-doc-image` / `aw-doc-frame` / `aw-mt-2`,
swapped the signup-settings inline `style="grid-column"` for a plain
`aw-stack`, replaced the last Font Awesome spinners on these pages with
`aw-spin` sprite icons, and removed the stacked legacy column classes from
`user_rows.html`.

Deliberately not built (no data or endpoint exists; reported instead of faked):
applications Export action and pending-count warning strip; signup public-page
preview rail; a historical-record View drawer (needs a new fragment endpoint).

Verification: `manage.py check` clean; 179/179 templates compile;
`verify_people.py` reports no legacy utility classes in the eight People
templates, the required ids present, and the aw contract present (`aw-tabs`,
`aw-table-tools`, `aw-cell-main`, `aw-switch`, `aw-editor-grid`, `aw-identity`,
`aw-form-grid`); `audit_text_encoding` clean; stylesheet braces 854/854.

### Completed unit: P4 Curriculum (9 November 2026)

Lesson editor
- The editor form now wraps the page head so the primary submit renders in
  `.aw-page-actions`; the two stacked autosave banners became one compact head
  indicator bound to the same `isSaving` / `lastSaved` state.
- Left column is a stack of "Lesson Details" and "Media parts". Each attached
  media link renders `.video-container.aw-part-card` > `.aw-part-head` (part
  badge, type tag, move up/down, remove) > `.aw-part-body` (`.aw-file-row`
  icon/name/key plus type, part and download fields). The Drive browser moved
  inside the card as a `<details>` whose summary is the `.aw-dropzone`; an
  action row offers "Attach existing file" and "Upload files".
- `lessonForm.js` changed only the `appendLessonFile` markup string so the
  JS-generated card and the server card are element-for-element identical
  (36 elements, same tag/class/attribute order). No method, event, selector or
  data flow changed.
- Rail keeps Publish checklist and Danger zone. Media-jobs and student-preview
  cards were omitted because the view context supplies no such data.

Quiz editor and lists
- `quiz_form_content.html`: head actions (Save) plus a compact autosave chip,
  one details card with the field grid, and rail cards for schedule, summary
  and checklist. The standalone Submit/Reset card is gone.
- `questions_rendering.html`: question cards are `.aw-q-card` >
  `.aw-q-head` (`.aw-q-num`, type select, points input, remove icon) >
  `.aw-q-body` (name, choices, add choice) **inside admin branches only**. The
  shared student exam branch is untouched: a guard-interval check proves every
  changed line lies inside an `{% if not is_student %}` block.
- `submission_dashboard_content.html`: page description, `aw-context-strip`
  (quiz, close date, submission count) and the canonical single-row filter bar.
- `content_qa_content.html`: each KPI now pairs its own label with its own
  value; issue groups render as cards with a count, pill and sample rows.
- `table_and_pagination.html` curriculum branches: `aw-cell-main` identity,
  sprite sort icons, `aw-row-actions`; the application branches were frozen.

CSS added with their first consumers: `.aw-dropzone` (container + hover/drag
state), `.aw-tag`, `.aw-q-card`, `.aw-q-head`, `.aw-q-num`, `.aw-q-body`,
`.aw-input--points`.

Arabic catalogue: 14 new msgids were appended to
`locale/ar/LC_MESSAGES/django.po` with Arabic translations (Media parts,
Add part, Attach existing file, dropzone hints, quiz-status note, total-grade
note, and the Content QA labels). A duplicate entry was detected and removed.
The catalogue parses cleanly (`msgfmt -c` reaches the write step with no format
error), but `.mo` compilation is blocked by this session's process-write policy,
so the compiled catalog is stale until someone runs
`python manage.py compilemessages` (no deploy step runs it today).

Deliberately not built: the admin submission detail still renders the student
exam shell (`display_quiz.html`); converting it needs its own change because it
is shared with the live student exam. Media-jobs and student-preview rail cards
have no view data. "Add part" has no JS method today, so the head control links
to the existing Drive picker instead of inventing behaviour.

Verification: `manage.py check` clean; 179/179 templates compile;
`verify_curriculum.py` reports the editor primitives present and no legacy
residue in the two editors; `node --check` passes on `lessonForm.js`; the
student-branch guard check reports every changed line in the shared question
partial inside an admin-only block; `audit_text_encoding` clean; stylesheet
braces 864/864.

### Completed unit: four reported admin defects (9 November 2026)

1. Stale quiz-operation message shown on entry without an operator action.
   - Root cause: the discovery endpoint (`GET /dashboard/quizzez/recalculate/`)
     fell back to the operator's **last completed** job, so every page load
     re-polled it and re-rendered the finished message. A browser without the
     sessionStorage marker replayed it forever.
   - Fix: discovery now advertises only `queued`/`running` jobs
     (`views.py`, `recalculate_quizzes`). A finished job stays reachable through
     its status URL for a client that is already tracking it.
   - UX: the status strip gained a dismiss button and auto-clears 12s after a
     terminal state (`tableManager.js`: `dismissRecalculationStatus()`,
     `clearStatusLater()`, `statusTimer` cleared in `destroy`).

2. Quiz row actions were a wall of wide text buttons that wrapped.
   - `table_and_pagination.html` action cell is now one compact `.aw-row-actions`
     cluster of `.aw-icon-btn` controls (9 icon buttons, 0 wide buttons),
     each with `title` + `aria-label`; destructive controls use
     `.aw-icon-btn.is-danger` (new CSS rule). Every action, `hx-*`, confirm
     message, CSRF token and Alpine handler is preserved, and the duplicate
     delete row for submissions was merged into the same cluster.

3. Blank area and horizontal scrollbar below the shell on short pages.
   - `admin_panel.html` marks the document root `class="aw-html"`; the stylesheet
     paints that root with the admin canvas colour, sets `min-height: 100%` and
     `overflow-x: hidden`, so a short page no longer shows a white band below
     the shell and no element can put a horizontal scrollbar on the window
     (wide tables still scroll inside `.aw-table-wrap`).

4. `Duration (minutes)`: 0 now means "no time limit", and the default is 0.
   - `Quiz.duration_minutes` default 60 -> 0 plus
     `migrations/0110_alter_quiz_duration_minutes_default.py` (chained after
     0113 because a later migration already existed locally; `makemigrations
     --check` reports no changes).
   - `quiz_attempts.py`: a zero duration sets `deadline = closing` instead of
     `now + 0`, so the attempt is bounded only by the quiz window.
   - Import/create defaults moved from 60 to 0 (`views.py` three call sites,
     `quiz_import.py` display projection).
   - Admin quiz form: value defaults to 0, a hint explains "0 means no time
     limit: the attempt is bounded only by the quiz closing date", and the rail
     summary renders "No limit" instead of 0.
   - Student-facing copy: the notification body and the Telegram quiz briefing
     now say "No time limit..." when the duration is 0 instead of "0 minutes".

Verification: `manage.py check` clean; `makemigrations --check` -> "No changes
detected"; 179/179 templates compile; `node --check` passes on `tableManager.js`;
`verify_four_fixes.py` asserts all four fixes in source (discovery filter,
dismiss/auto-hide, icon cluster with every label preserved, aw-html guard,
model default + migration + deadline branch + form presentation); stylesheet
braces 866/866; `audit_text_encoding` clean.

Open notes: six new msgids were added to `locale/ar/LC_MESSAGES/django.po`
("No limit", the two no-time-limit sentences, the 0-means-no-limit hint,
"Please enter a duration", "Dismiss"); `django.mo` is still stale because this
session's process-write policy blocks `msgfmt`/`compilemessages`. The student API
payload still sends `duration_minutes: 0`; the mobile app should treat 0 as
"no limit" when it renders that field.

### Completed unit: admin submission detail separated from the student exam (9 November 2026)

- New `templates/submission_detail.html` (extends `admin_panel.html`) and
  `partials/submission_detail_content.html`. The page renders the workspace
  head, a context strip (student, quiz, final grade), an answers card that
  includes `questions_rendering.html` with the unchanged `questions[...]` field
  names, a correction submit action when the operator may grade, and a rail with
  the submission summary.
- `submission_user` (views.py) now renders `submission_detail.html`. It also
  fixes a data bug: the page used `request.user.username` as the *student* label,
  so the admin saw their own username; it now uses the graded student's full
  name or username.
- The POST branch, `unpack_quiz_form`, the grading transaction and the
  correction field names are untouched. `display_quiz.html` is byte-identical
  (`git status` clean), so the student exam, preview and taken-view routes are
  unaffected.
- Two new msgids: "Quiz submission", "Back to submissions" (plus "Answers",
  "Submission" and the manual-correction note). Arabic entries still need to be
  added and compiled.

Verification: seeded a quiz, question, submission and grade on the disposable
schema and requested the route as an admin. `/ar/...` and `/en/...` both return
200; the response contains `aw-dashboard-page`, `aw-context-strip`,
`aw-editor-rail`, `id="quiz-form"`, `questions[0]` and the "Submit Correction"
action, and contains **no** `sd-frame` and **no** `app_student.css`, i.e. the
student exam shell is gone from the admin page.

Follow-up for the review pass: `display_quiz.html` still carries an
`extended_view == 'admin_panel.html'` branch that no caller reaches now
(`extended_view` is only passed as `base.html`); it can be deleted safely
because it never matches on the student path.

### Completed unit: scanner result moved into the admin shell (9 November 2026)

- `templates/scan_preview.html` used to `{% extends 'base.html' %}` and render
  the public/student `sd-page` + `container` layout, so scanning an ID dropped
  the operator out of the workspace.
- It now extends `admin_panel.html` and renders an `aw-dashboard-page` with a
  page head, a context strip (username, study mode, academic year, date), two
  cards (course, record attendance) and the policy note.
- Preserved exactly: the `record-attendance` POST URLs for entrance and exit,
  both CSRF tokens, the ids `entrance-form`, `exit-form`, `entrance-btn`,
  `exit-btn`, `record-error`, `record-status`, the disabled reasons, and the
  inline recorder script contract. The script's class toggles now use
  `aw-btn--primary` / `aw-btn--gold` / `aw-btn--outline` instead of the
  Bootstrap/sd button classes.
- Decision recorded: the graduation gallery is **not** an admin surface. The
  admin navigation points at `graduation-gallery-manage` (already on the admin
  shell); `graduates/<scope_id>/` is the student/public album, so converting it
  would break the student experience and was deliberately left alone.

Verification: template compiles; rendered `/en/scan/<token>/` and
`/ar/scan/<token>/` as an admin on the disposable schema, both 200, both
containing `aw-dashboard-page`, `aw-context-strip`, `entrance-form` and
`record-status`, and containing neither `sd-page` nor `container py-4`.

### Completed unit: JS-created question cards match the server card (9 November 2026)

- `quizForm.js` `buildQuestionCard` now emits the same anatomy the server
  renders: `.aw-q-card` > `.aw-q-head` (`.aw-q-num`, `.aw-select.question-type`,
  `.aw-input--points` grade, `.aw-icon-btn.is-danger` remove) > `.aw-q-body`
  (hidden id, `.aw-field` name, answers section, correct-answer section).
- `buildChoiceRow` emits `.aw-choice-row` with `aw-input` + an icon remove
  button. Every functional hook is unchanged: `.question-card`,
  `[data-question-card]`, `.question-type`, `[data-choice-row]`, the
  `questions[...]` names and `data-action` values.
- `reindexQuestions` now also rewrites `.aw-q-num` so the visible number follows
  DOM order after add/remove, and the one `.card-body` parent lookup became
  `.aw-q-body, .card-body` so server cards and JS cards both resolve.
- Structured editors (order/match) keep their existing markup; they are already
  skinned by the admin stylesheet.

Verification: `node --check` passes; hook census confirms `aw-q-card`,
`aw-q-head`, `aw-q-num` (2), `aw-q-body` (2), `aw-choice-row`, plus the preserved
`question-card` (12), `data-choice-row` (4) and `question-type` (6) hooks.

### Completed unit: P5 Academic and Attendance (9 November 2026)

Shared primitive added first: a delegated tab controller in `admin_shell.js`
(`[data-aw-tabs]` root, `[data-aw-tab]` buttons, `[data-aw-tabpanel]` panels;
toggles `is-active`, `aria-selected`, `tabindex`, `hidden`). No page needs an
inline script and HTMX swaps keep working. CSS gained `.aw-tabpanel[hidden]`,
`.aw-drawer-table` / `.aw-drawer-scroll`, `.aw-th-sub`, `.aw-dl`, the
`.aw-cal-joined` joined month surface (with a phone min-height override), and the
calendar's inline-form rule now targets `.aw-inline-form`.

Academic
- `academic_setup_content.html`: real tabs with counts and panels; the previous
  `href="#..."` anchors are buttons wired to the controller. Default tab is
  derived from context (scope -> offerings, year -> levels, else years).
  Existing ids `#academic-years`, `#year-levels`, `#course-offerings` stayed on
  the inner cards, so every HTMX link and redirect target is unchanged. Year
  snapshot cards use `aw-kv`; weekday cells render `.aw-chip--teal` from real
  `meeting_weekdays` data.
- `levels_content.html`: prototype level cards (head, level pill, meta grid,
  divider, enrollments row); dropdown `href="#"` triggers became buttons bound
  to the same modal targets. Weekday chips were omitted because the levels view
  projects counts only - reported, not invented.

Promotion
- `promotion_evaluation_content.html`: real stepper. Each stage is a button with
  `data-aw-tab`; exactly one carries `is-active` and completed stages carry
  `is-done`, derived from context (`selected_scope`, `preview_rows`,
  `results_page_obj`). Promote is never marked done because no scope-level
  completion flag exists. The five stage sections became panels; every form,
  id, hx attribute and field name is unchanged. Added the context strip
  (year, level, formula kind, evaluation end) and made the results override form
  compact. The results action bar moved into the promote panel with its ids.
- `promotion_history_content.html`: context strip, `aw-cell-main` identity with
  an avatar and a Student 360 link, outcome pills, and the canonical filter
  tools. No KPI grid because the view supplies no aggregates.

Attendance, calendar, scanner
- `attendance_management_content.html`: the matrix now has **one column per
  meeting** (`Student` + N meeting columns + 5 counters + Score, colspan
  `meetings|length|add:7`) with `.aw-attend-cell--full/--partial/--none/--absent`
  and accessible status labels. The day detail renders per student in a
  full-width `<details>` with `.aw-drawer-table` (Date, Course, Entrance, Exit,
  By, Grade, Action) instead of the old Bootstrap collapse columns. The
  duplicated guidance line is printed once. Head actions (Scanner, Calendar,
  Reconcile, Settings) and one `aw-filter-bar` replaced the eight scattered
  filter fields. The 4-KPI grid was not built: only `student_count` and
  `meeting_count` exist.
- `calendar_month_grid.html` / `calendar_management_content.html`: admin month
  renders inside `.aw-cal-joined` (one bordered surface, collapsed interior
  borders); toolbar regrouped; every `.aw-calmg-*` hook, data attribute and
  modal kept. `calendar_day_sheet.js` no longer bails above 575.98px, so the day
  sheet opens on desktop (still a Bootstrap modal; a true drawer needs a
  fragment endpoint).
- `scanner_content.html`: camera card with frame/box/hint, checklist, aw form
  controls, same ids and handler contract.

Python-side widget centralization finished: `AcademicYearForm`,
`CourseOfferingForm`, `AcademicYearLevelWeekdayForm`, `AttendancePolicyForm`,
`HistoricalIntakeForm`, `ExceptionalCourseAssignmentForm`,
`HistoricalExistingBulkForm`, `OfferingCopyForm`, `QuizTypeForm`,
`PromotionFormulaForm`, `PromotionRuleForm`, `QuizExceptionalOpeningForm`,
`AnnouncementForm` and `LessonEditorForm` now inherit `AwForm`/`AwModelForm`.
Removed the post-`super()` re-assignment of `form-select`/`form-check-input` in
`OfferingCopyForm`, which was overriding the mixin.

Verification: `manage.py check` clean; 181/181 templates compile; the disposable
schema now carries a seeded academic year, so the **whole admin route sweep
passes 36/36 with status 200** (previously five routes raised
`AcademicYear.DoesNotExist` on an unconfigured database); `node --check` passes
on `calendar_day_sheet.js` and `admin_shell.js`; stylesheet braces 880/880;
`audit_text_encoding` clean; all phase verifiers (Phase 5, Curriculum, People,
four fixes, Telegram) report green.

Known items: four new scanner msgids need Arabic entries ("Scanner checklist",
"Align the QR inside the frame", "If the QR code is rejected, use the manual
lookup.", "A repeated scan is rejected and the recorded entrance or exit is
kept."); `views.py` still passes `matrix_column_count` which no template
consumes now; a true attendance-day drawer still needs a fragment endpoint.

### Fixed: admin dashboard 500 on the pending-applications queue (9 November 2026)

- Symptom: `GET /dashboard/` raised `FieldError: Field User.role cannot be both
  deferred and traversed using select_related at the same time` from
  `admin_today._pending_applications_queue`.
- Cause: the queue queryset combined `.select_related("role")` with
  `.only("id", "first_name", "last_name", "username", "date_joined", "city")`.
  Django refuses to traverse a relation whose FK column was deferred by `only()`.
  The queue never reads `role`, so the join was unnecessary.
- Why earlier smoke tests missed it: the query only executes when at least one
  pending application exists (`if count:`), and the disposable database had
  none.
- Fix: dropped `.select_related("role")`; the queue reads identity, city and
  date columns only. No other queue pairs `select_related` with an `only()`
  that omits the joined key (audited all five queues).
- Evidence: reproduced the exact exception with the old query shape and
  confirmed the new query returns rows; seeded a pending applicant and
  requested `/en/dashboard/` and `/ar/dashboard/` -> both 200 with the
  applicant rendered in the queue; full admin route sweep **36/36 -> 200**;
  `manage.py check` clean.

### Completed unit: P6 Media, Reports and Site (9 November 2026)

- Rebuilt: media jobs, media upload, R2 management and file list, gallery
  manage, combined report, grades matrix, payments matrix, progress, analytics,
  the transcript wrapper, app releases and banners.
- Media: upload now uses `.aw-dropzone` (three files reference it), file and
  folder grids use `.aw-file-grid` / `.aw-folder-grid`, R2 keeps every
  `#stat-*`, rename/info/create-folder and `data-r2-*` hook.
- Reports: `aw-context-strip` present across the report pages (14 partials now
  use it), `aw-kpi` strips where the views supply counts, canonical
  `aw-filter-bar` toolbars, `aw-cell-main` identity cells, and the grades matrix
  two-row header offset moved out of inline styles into
  `.aw-table thead th.aw-th-subhead`.
- Analytics city filter: kept the Bootstrap dropdown because the multi-select
  behaviour is owned by `application_analytics.js`; its row markup now uses
  `aw-check` instead of `form-check`. This is the one remaining Bootstrap
  component hook in an admin template and is recorded as accepted.
- Two speculative primitives (`.aw-chart` CSS bars and `.aw-donut`) were added
  during this phase and then removed: no template used them, and the existing
  report charts remain canvas-based. No dead CSS was left behind.
- Not built: the UI-kit page (a review artefact, not an admin destination) and a
  dedicated transcripts context (the route still wraps the grades matrix until a
  transcript-specific view exists).

Verification: all 13 Phase 6 templates compile; `verify_phase6.py` reports no
legacy utility classes outside the accepted analytics dropdown, no inline
sticky offsets, and the dropzone/KPI/context-strip/file-grid contract present;
`manage.py check` clean; **181/181 templates compile** and the admin route sweep
is **36/36 -> 200**; stylesheet braces 884/884; `audit_text_encoding` clean.

Integration fixes after the workers returned (found in their reports):

- Added the missing `.aw-score--good/--mid/--bad` tones the grades matrix now
  emits (three percentage bands plus a legend).
- Fixed `.aw-th-subhead`: the rule was scoped to `.aw-table`, but the grades
  matrix is `.aw-matrix`, so the two-row sticky header overlapped on scroll.
  The selector now covers both tables.
- Removed the dead `.analytics-city-option .form-check-input` / `.form-check-label`
  rules from `app_admin.css` (the city rows use `aw-check` now).
- `BannerForm` (`portal_views.py`) moved from `form-control`/`form-check` widget
  classes to `aw-input`/`aw-textarea`, so the banner publish form no longer
  renders Bootstrap controls.
- Added the two new head-copy msgids to the Arabic catalogue ("Publish Android
  builds, enforce a minimum version, and control saving." and "Short,
  dismissible messages that appear above student pages.").

Recorded decisions from this phase:

- The analytics city multi-select keeps the Bootstrap dropdown because the
  multi-select behaviour lives in `application_analytics.js`; its rows are
  `aw-check` now. Accepted as the only Bootstrap component hook in an admin
  template.
- Two breadcrumb trails still render per page: the top bar owns domain/section
  crumbs and the body renders the resource trail from `partials/breadcrumbs.html`
  (many wrappers override the block with the same include). This is a
  whole-app navigation decision, not a per-page defect, so it is deferred to the
  review pass rather than changed silently mid-phase.
- The UI-kit page is not built; a transcript-specific view/context does not
  exist, so the transcripts route keeps wrapping the grades matrix.

Media integration notes (from the media worker's handoff):

- Jobs/Upload are plain `aw-tab` anchors, not the delegated panel controller:
  the upload workspace is a separate view with its own context and there is no
  merged route or fragment to switch between them client-side.
- `r2Manager.js` deletes nodes via `.closest('.col')` and drives the Bootstrap
  modal API, so `.col` wrappers and the three modal dialogs stay; the file and
  folder menus were converted to `aw-menu` + `data-aw-menu-trigger` because
  `r2Manager.js` never calls the Bootstrap dropdown API.
- `r2_browse.html` (an include the worker could not edit) was cleaned by the
  lead: `mb-3`/`mt-3` utilities replaced with `aw-mt-2`/`aw-mt-3` and the Font
  Awesome chevron replaced with the sprite.
- `drive_files.html` deliberately keeps `.card-title` and `.card-body`: both are
  JS hooks read by `addFile()`, not styling. Removing them would break file
  selection, so the Bootstrap-looking classes stay with a documented reason.
- Follow-up (JS, not done): `media_upload.js` still generates
  `list-group-item`, `.progress` and `badge` markup for selected files and job
  cards; converting it is the same kind of change already applied to
  `quizForm.js`.
- Arabic catalogue: eleven media/gallery msgids added ("Jobs", the upload
  dropzone copy, "Processing jobs", "Queue rules", "Pipeline", the gallery
  publish line, "Album rules" and the four album rule sentences). `msgfmt -c`
  reaches the write step with no parse or duplicate error; `.mo` still needs
  `compilemessages` outside this sandbox.

### Completed unit: Engagement and Today cockpit (9 November 2026)

Today (Overview)
- `views.py` `today_kpis` is now a decision cockpit: four tiles are the live
  queues (pending applications, failed media, ungraded answers, unread Telegram)
  read from the bounded queue counts, plus active students and attendance today.
  Each tile carries a `meta` line and an optional destination `url`;
  content-inventory tiles (courses/lessons/quizzes/offerings) are gone.
- `admin_today._queue` gained `tone` and `action_label`, and the four queues set
  amber/red/blue/teal with "Review queue", "Open pipeline", "Open Content QA",
  "Open inbox" instead of a generic "View".
- `today_content.html` renders `.aw-kpi-meta`, wraps linked tiles in
  `.aw-kpi-link`, and uses the per-queue tone class and action label.
- CSS: `.aw-attn-icon--teal/--blue/--gold` tone variants and `.aw-kpi-link`.

Engagement
- Announcements and delivery detail rebuilt: real Compose/History tab panels via
  the delegated controller, `.aw-form-grid` composer with `aw-check` level rows,
  canonical filter bar and sortable history, delivery detail on `aw-kv` rows,
  `aw_status_pill`, `.aw-progress` and recipient `.aw-table` rows.
- Telegram accounts: context strip, KPI tiles, `aw-cell-main` identity and the
  canonical filter bar.
- Broadcasts (content/list/detail/confirm): KPI strip where aggregates exist,
  status tabs, filter bar, identity-first columns, status pills.
- Bot configuration: status card, settings grid and `aw-switch` booleans.
- All nine Engagement templates compile with no legacy utility classes and the
  full aw contract (tab panels, context strip, KPI, identity cells, filter bar,
  switch).

Verification: `manage.py check` clean; 181/181 templates compile;
`verify_engagement.py` 9/9 compile with no legacy residue; admin route sweep
**36/36 -> 200**; Today renders 200 with the new decision KPIs and queue labels;
stylesheet braces 889/889; encoding clean; Arabic catalogue parses (11 new
Today/queue msgids added).

Remaining admin surfaces (from the coverage matrix): `content_detail_content`
(lesson/quiz detail, 6 legacy hits), `student_overview_content` (Student 360
identity hero and real tabs), `background_job_detail_content` (1 legacy hit).
Structural gaps unchanged: definition/endpoint-dependent drawers, UI-kit page,
transcripts view, two breadcrumb trails, `media_upload.js` generated markup.

### Backend unblocking and shared-component fixes (9 November 2026)

These were the "blocked by backend" gaps that stopped pages from matching the
prototype. All resolved with bounded queries; no route, model or migration
change.

- Media jobs: the view now returns one aggregate of job counts by status for the
  actor's search scope (`status_counts`, `total_count`) and builds a `media_kpis`
  list. The template renders Total + Queued / Processing (processing + uploading
  + verifying) / Completed (succeeded) / Failed, replacing the previous
  Total + Page tiles. One extra statement; route still 5 queries.
- Attendance: the view computes `scanned_student_count` (distinct students with
  at least one attendance record for the scoped meeting dates) and an
  `attendance_kpis` list; the page gained the 4-tile strip Students / Scanned /
  Not scanned / Meetings with meta lines. Route stays at 13 queries.
- Scanner: the view used to pass `{}`, which is why the page was static copy. It
  now passes a bounded `recent_scans` feed (six rows, one query) and `last_scan`;
  the template renders a Recent scans table with identity cells and action pills,
  plus an honest empty state. Route +1 query.
- Shared status pill: `aw_status_tone` remapped **explicit** tones through the
  status dictionary, so every `tone="green|red|amber|..."` override rendered
  muted. The filter now passes known tone names through unchanged and only maps
  raw status values. Verified: tone green/red/amber -> green/red/amber, status
  `active` -> green, unknown -> muted.
- Widget classes moved out of Bootstrap for the remaining admin forms:
  `TelegramBroadcastForm` textarea/select (`aw-textarea` / `aw-select`, keeping
  the `tg-broadcast-*` hooks), `JobFilters` search/date/select fields and
  `RedeliveryForm.audience` in `job_monitor_views.py` (`aw-input` / `aw-select`).
- `backgroundJobsMonitor.js` now mirrors the job percentage onto the workspace
  `.aw-progress` bar (inner span width + `aria-valuenow`) as well as the legacy
  native `<progress>`, so polling still animates the new bar.
- Arabic catalogue: eight media/attendance/scanner labels and thirteen Telegram
  labels added (Jobs for this search, In this scope, At least one scan, In this
  window, Recent scans, Time, No scans recorded yet., Admin-only report,
  Eligible accounts, Needs review, of accounts, Broadcast, Confirmed, Activated,
  Admin-only config, Bot ID, Deactivated, Last validated, Not connected, Private
  chats only).

Verification: `manage.py check` clean; 181/181 templates compile; admin route
sweep 36/36 -> 200; query-count review unchanged (max 24, same two repeated-
statement routes); stylesheet braces 889/889; encoding clean; Engagement
verifier green; `node --check` passes on the jobs monitor.

Still blocked (need a product decision or a new endpoint, not a data query):
report population aggregates (average grade, attendance %, at-risk, unevaluable,
distribution buckets) have no canonical definition or scope in the report view;
a transcript view needs its own context; the remaining right-hand drawers
(attendance day, broadcast detail, R2 rename, banner create, release publish)
each need their own fragment.

Resolved in the follow-up pass:

- Promotion scope size: `promotion_formula` now passes `scope_student_count`
  (distinct students with an active enrollment in the selected scope, the same
  notion the attendance matrix uses) and the context strip shows it. Verified
  with `?scope=<pk>` -> 200 and the Students chip present.
- Drawer pattern proven end to end on the media job peek:
  - `media_processing_status` gained a `?fragment=job&job=<uuid>` branch that
    returns `partials/drawer/media_job_detail.html`, applies the same actor
    scoping as the list (staff see only their own jobs), redirects to the owning
    page when the request is not an HTMX fetch, and returns 404 for a missing or
    malformed id.
  - The row gained a `data-aw-drawer` icon trigger with title/subtitle, so the
    shared drawer component and `admin_shell.js` controller are used by a page
    for the first time.
  - Verified: page 200 with the trigger, fragment 200 with content, direct hit
    302, bad uuid 404.
- New template: `partials/drawer/media_job_detail.html` (read-only summary,
  progress and retry/stop actions reusing the existing endpoints and hooks).

Verification: `manage.py check` clean; 182/182 templates compile; admin route
sweep 36/36 -> 200; query review max 25 (Today; the +1 came from the seeded
failed-media row being rendered), media page 6, promotion 5; stylesheet braces
889/889; encoding clean; Engagement verifier green.

### Last admin surfaces closed (9 November 2026)

- Student 360 (`student_overview_content.html`): identity hero (avatar, name,
  username, role/application/level/account chips, capability-gated User details
  / Message / History actions), one context strip (level, year, enrollment
  status, count, joined, last login, masked phone), and five real tab panels on
  the delegated controller (Overview, Academics, Attendance, Finance,
  Engagement). All 13 existing cards, 12 empty states and the inline pager are
  preserved; no KPI or count was invented because the view supplies none.
  Rendered with a real student id: 200, hero + 5 panels + strip present.
- Content detail and background job detail: both now compile with **zero legacy
  utility classes**. The background job card keeps all five `data-job-*` hooks
  (verified against HEAD: 5 before, 5 after, none lost).
- Correction to the review: `prototype/admin-redesign/` contains no
  `content-detail.html` and no `background-jobs.html`. Content detail is an
  internal detail surface aligned to `submission-detail.html` anatomy, and the
  background jobs page is an admin addition outside the prototype, so neither is
  a parity gap. The review report's parity list should be read with this note.
- UI kit: **not built, by explicit user request** ("no need to implement UI kit
  in case it is for showcase"). The route, view and navigation entry were
  reverted and no reference remains. Two placeholder templates
  (`templates/ui_kit.html`, `templates/partials/ui_kit_content.html`) contain a
  removal note only and should be deleted where the filesystem allows unlinking.

Coverage after this pass: every admin page partial now has zero legacy utility
classes. The seven partials not rebuilt are `background_jobs_content` (addition),
`dashboard_content` (shell), `submission_detail_content` (new), the two student
gallery surfaces, the student lesson stream, and the UI kit placeholder.

Verification: `manage.py check` clean; **184/184 templates compile**; admin route
sweep 36/36 -> 200; query review unchanged (max 25, same two repeated-statement
routes); stylesheet braces 889/889; encoding clean; `"Student 360"` added to the
Arabic catalogue.

### Fixed: collapsed sidebar layout and post-HTMX toggle (9 November 2026)

Reported symptoms: collapsing the sidebar squeezed the whole workspace into a
narrow strip next to an empty column; the toggle stopped working after an HTMX
navigation; console reported `aria-hidden` on a focused sidebar and on the
command palette.

Root causes and fixes:

1. **Grid placement.** `.aw-shell.is-sidebar-collapsed` used
   `grid-template-columns: rail 0 1fr` *and* `display: none` on `.aw-sidebar`.
   Removing the aside from the grid made `.aw-main` auto-place into the second
   (0-width) column, so the workspace rendered in a sliver and the third column
   stayed empty. Collapsed mode now keeps a slim column
   (`--aw-sidebar-collapsed-w`, 56px), sets `.aw-sidebar { width: 56px }`,
   centers the head and hides only the head text, the section label and the
   section nav, so the toggle stays visible and reachable.
2. **Post-swap Alpine.** `admin_shell.js` re-initialized the swapped page and the
   persistent OOB regions inside one `try`. A throw while initializing the page
   skipped `#aw-sidebar-body`, leaving the freshly swapped toggle without an
   Alpine binding (dead button). Each tree now has its own guard. The after-swap
   handler also accepts `#content` (table, form and pagination links), not only
   `#display-page` / `#page-region`, so body-region navigation re-initializes the
   persistent regions too.
3. **ARIA.** The sidebar carried `x-bind:aria-hidden`, which hides an element
   that holds focus (the collapse button lives inside it). The binding was
   removed; the collapsed state is purely visual and the toggle keeps
   `aria-expanded`. A global `hide.bs.modal` capture handler blurs a focused
   descendant before Bootstrap sets `aria-hidden` on the dialog, which clears the
   command-palette warning.

Verification: `manage.py check` clean; `node --check admin_shell.js` passes;
stylesheet braces 891/891; 184/184 templates compile; admin route sweep
36/36 -> 200. Rendered shell confirmed to keep `#content` inside `.aw-main` with
21 sidebar links and the toggle present.

Follow-ups found by the detail-page worker and closed in the same pass:

- `background_jobs_content.html` filter grid moved off Bootstrap
  (`row g-3 align-items-end`, `col-12 col-md-6 col-xl-4`, `col-12`) onto
  `.aw-form-grid` + `.aw-form-actions`; its poll-error line now uses
  `.aw-field-error` instead of `text-danger small`.
- `notification_delivery_content.html` header comment corrected: the monitor now
  mirrors the percentage onto `.aw-progress`, so the note no longer claims it
  only writes the native element.
- Arabic entry added for the new content-detail description msgid
  `%(kind)s in %(course)s` -> `%(kind)s في %(course)s`.

Tracked, not changed: a precise scan
(`tmp/ui-audit/scan_bootstrap_classes.py`) still finds Bootstrap tokens in 25
admin partials. Most are functional JS hooks (`d-none` toggled by
media/scanner/upload scripts), the column-picker Bootstrap dropdown with
`form-check-input` in `table_and_pagination.html`, and `me-1` icon spacing in
`shortcuts_help`. Converting those needs JS changes and should be its own pass.

The column picker was converted after all (it was the last Bootstrap *component*
in the shared table shell, used by every list page): `.dropdown` /
`dropdown-toggle` / `dropdown-menu` / `dropdown-item` / `form-check-input` are
now `.aw-menu-wrap` + `data-aw-menu-trigger` + `.aw-menu` + `.aw-menu-item`,
driven by the existing delegated `admin_shell.js` controller. The Alpine model
(`x-model="col.visible"`, `saveColumnPreferences()`, `resetColumns()`) and the
`i-columns` / `i-refresh` sprite icons are unchanged. Verified on the users,
courses and lessons pages: the menu wrapper, trigger, menu element, Alpine model
and reset action all render, and no Bootstrap dropdown remains.

Cosmetic token pass (same day, after the column picker):

- `academic_setup_content.html`: `aw-icon-btn text-danger` -> `aw-icon-btn
  is-danger`, so destructive row actions use the workspace danger treatment.
- `content_qa_content.html`: dropped the trailing `mb-0` from the `aw-mt-2`
  meta lines.
- `submission_dashboard_content.html`: form-level `mb-0` -> `aw-m-0`.
- `user_bulk_form_content.html`: `fw-bold` -> `aw-section-title`,
  `mb-0 small` -> `aw-small aw-m-0`, `mb-0`/`mb-0 mt-2` -> `aw-m-0`/`aw-m-0 aw-mt-2`.
- New CSS: `.aw-m-0` (declared before the directional utilities so a combined
  `aw-m-0 aw-mt-2` keeps its top spacing), `.aw-section-title`, and
  `.aw-check-input` geometry. The last one matters because `AwFormMixin` maps
  every `CheckboxInput` to `aw-check-input`, but only `.aw-check input` was
  styled, so standalone booleans rendered unstyled.
- Bootstrap-token scan is down from 25 to 21 admin partials. The remainder is
  `d-none` toggled by feature scripts (media, scanner, upload, R2), the bulk-bar
  select-all `form-check-input`, and one `ms-2` on a button. JPG/`text-break`
  family tokens are cosmetic only.

Attendance day drawer: evaluated and deliberately not built. The existing
`<details>` block contains per-record correction links plus two CSRF-protected
delete forms per meeting; loading those into a fetched drawer would leave the
drawer open while `hx-target="#content"` replaced the page underneath. The inline
disclosure is the safer equivalent, so the prototype's drawer is not reproduced
here by design.

### Banner create drawer (9 November 2026)

Second real use of the shared drawer, and the first **form** in one:

- `banner_form_fields.html` is the single definition of the publish form. The
  inline card includes it only when `show_form` is true, and the drawer fragment
  includes the same file, so the two paths can never drift.
- `banner_manage` gained `?fragment=form` (returns
  `partials/drawer/banner_form.html`, redirects to the page when the request is
  not an HTMX fetch) and passes `show_form`, which is true for a POST (validation
  errors stay on the page) or `?new=1`.
- The page head is now the `aw-page-head` wrapper with an `aw-page-actions`
  block: a `data-aw-drawer` "New banner" button plus a `<noscript>` link to
  `?new=1`, so the form is still reachable without JavaScript.
- Because the inline card is rendered only in those cases, the form's field ids
  never appear twice in the same document.

Verified: page 200 with the drawer trigger and no inline card; fragment 200 with
`<form>`; direct hit 302; `?new=1` 200 with the inline card; page head contains
the actions; `<div>` open/close counts balanced (66/66). 186/186 templates
compile; sweep 36/36 -> 200; DOM audit clean; encoding clean.

### Pagination parity verified (9 November 2026)

- All 32 admin templates that carry a page object include
  `partials/pagination.html`; the scan found **no hand-rolled admin pager**. The
  only remaining `pagination` class in the project is `notifications.html`, a
  student page outside admin scope.
- Anatomy against the prototype footer: range text start-side, pager end-side,
  sprite chevrons, disabled prev/next as non-links, `aria-current` on the active
  page, non-interactive ellipsis gap. Ours adds `nav > ul` semantics with an
  accessible label and RTL-aware chevrons.
- Placement verified on users, courses and background-jobs: the footer is inside
  the table card. Lists with no rows render the empty state rather than an empty
  pager, so sampled pages with an empty database showed no footer - that is the
  intended behaviour, not a regression.

### Calendar day agenda moved into the shared drawer (9 November 2026)

The prototype opens a day's agenda in a drawer; the admin calendar used a
Bootstrap modal. `admin_shell.js` now exposes a small programmatic API for
components that already own their markup:

```js
window.awDrawer.open({ title, subtitle, html, trigger });
window.awDrawer.close();
```

It reuses the existing drawer machinery (overlay, Escape, focus return, HTMX and
Alpine init), so no second overlay implementation exists.

`calendar_day_sheet.js` uses that API when present and falls back to the
Bootstrap modal otherwise. This matters because the same script drives the
student calendar, which does not load `admin_shell.js`: the admin calendar now
shows the agenda in the drawer, while the student calendar keeps its dialog
untouched. The cloned markup is **moved**, not copied - the staging node is
emptied first, otherwise every id inside the agenda would exist twice while the
drawer is open.

Verified: admin calendar (after the `?academic_year=` redirect) returns 200 with
the drawer shell, the shell script and the staging modal; the student calendar
returns 200 with the modal and without the drawer shell or the shell script.
DOM and markup audits stay clean; 187/187 templates compile; sweep 36/36 -> 200;
max 23 queries; braces 926/926; JS encoding audit passes.

Fourth drawer, and the first read-only one driven from a list row:

- `telegram_broadcasts` gained `?fragment=broadcast&id=<pk>` returning
  `partials/drawer/broadcast_peek.html`: redirects to the list for a non-HTMX
  hit, and returns 404 for a non-numeric or unknown id.
- The list row's "View" link became a `data-aw-drawer` button with the status as
  the drawer subtitle; the full delivery report (which owns the polling monitor)
  stays reachable through an "Open full report" action inside the drawer, so no
  Alpine component is loaded into a fragment that the drawer cannot initialise.
- Drawer content: status pill, target, academic year, recipients, delivered,
  failed, skipped, created, completed, author, a truncated message body and the
  report link.

Two defects found and fixed while verifying against real data:

- The first label used `{% translate "Audience" %}` while the metrics row used
  `{% translate "Recipients" %}`; both map to the same Arabic word, so the drawer
  showed "المستلمون" twice. The first row is now "Target".
- `{{ broadcast.academic_year }}` rendered empty for a year whose `name` is blank.
  The row now uses `{% firstof ...name ...starts_on|date:"Y" %}`, so it falls back
  to the starting year.

Verified: list 200 with the trigger, fragment 200 with eight kv rows and the
report link, direct hit 302, malformed id 404, unknown id 404. 189/189 templates
compile; sweep 36/36 -> 200; DOM and markup audits clean; braces 926/926;
encoding clean.

### Android release publish drawer (9 November 2026)

Third drawer, reusing the banner pattern:

- `app_release_form_fields.html` is the single definition of the multipart
  publish form (version name, version code, APK upload, notes). The inline card
  includes it only when `show_form`, and the drawer fragment includes the same
  file, so field ids never appear twice.
- `app_release_manage` gained `?fragment=form` (returns
  `partials/drawer/app_release_form.html`, redirects to the page for a non-HTMX
  hit) and passes `show_form`, true only for `?new=1`.
- The page head is the `aw-page-head` wrapper with a "Publish build" drawer
  button plus a `<noscript>` link to `?new=1`.

Verified: page 200 with trigger and no inline form, head actions present;
fragment 200 with `<form>`, `enctype="multipart/form-data"` and a CSRF token;
direct hit 302; `?new=1` 200 with the inline form. 188/188 templates compile;
sweep 36/36 -> 200; DOM and markup audits clean; braces 926/926; encoding clean.

### Quiz durations: existing rows set to 0 (no limit) by data migration (10 November 2026)

`duration_minutes = 0` means "no time limit": `quiz_attempts` sets
`deadline = closing` when the value is falsy, the student payload sends 0 and
every notification/admin label says "No limit". New quizzes already default to 0
via `0110_alter_quiz_duration_minutes_default`; existing rows still carried their
old per-quiz limits.

`migrations/0114_quiz_duration_zero_data.py` closes that gap with one bounded
UPDATE:

```python
Quiz.objects.exclude(duration_minutes=0).update(duration_minutes=0)
```

It depends on `0110_alter_quiz_duration_minutes_default` (the current leaf), so
the graph keeps a single leaf and `makemigrations --check` still reports no
changes. The reverse is `RunPython.noop` on purpose - the previous durations are
not recorded anywhere, so restoring them would be a guess. The UPDATE only
touches `duration_minutes`; attempts, snapshots, answers, grades and deadlines
already stored per attempt are untouched.

Verified by executing the migration's forward function against the disposable
schema: quizzes at 60 and 45 became 0, an existing 0 stayed 0, and a second run
changed nothing (`tmp/ui-audit/check_duration_migration.py`).

### Fixed: attendance 500, filter chrome, chat layout and drawer/agenda styling (10 November 2026)

- **Attendance 500 (regression from the KPI strip).** `attendance_matrix_meetings`
  returns column **dicts**, but the new KPI code read `meeting.date`. With a
  real scope selected the view crashed with
  `AttributeError: 'dict' object has no attribute 'date'`. Now reads
  `meeting["date"]`; verified with `?academic_year=1&level=1` -> 200.
- **Saved filter presets removed.** `static/js/filter_presets.js` injected a
  localStorage preset select plus Save/Delete into every `.aw-filter-bar`; it
  was deleted, its script tag removed from `admin_panel.html`, and
  `.aw-filter-bar` gained `gap: 10px` so stacked filter rows no longer touch.
  No template or view references it.
- **Telegram chat layout.** `.aw-chat-panel` is now height-bounded
  (`calc(100dvh - 208px)`, max `calc(100dvh - 150px)`, `overflow: hidden`), the
  thread scrolls internally (`overscroll-behavior: contain`) and the student
  context stays a 300px side rail down to 1024px instead of being hidden below
  1180px; below 1024 it stacks with a top border. `.aw-chat-context` scrolls on
  its own.
- **Chat media.** Images and video are capped at `max-height: 260px`; images are
  wrapped in a button and both media types open the new full-screen lightbox
  (`window.awDrawer`-independent, delegated in `admin_shell.js`, Escape/backdrop
  close, `aw-lightbox` styles). The photo no longer opens a new tab. One new
  msgid: "Open full screen".
- **Calendar day agenda in the drawer.** The month-cell styles are compact by
  design, so `#aw-drawer-body` now gets a readable variant (row layout, borders,
  uppercase level label, holiday chip, add-action row) instead of inheriting the
  10px cell typography.
- **Modal opening no longer shifts the shell.** Bootstrap pads the body when a
  dialog opens; `body.aw-admin.modal-open { padding-right: 0 !important }` plus
  `scrollbar-gutter: stable` keeps the grid in place.

Verification: `manage.py check` clean; 187/187 templates compile; 36/36 admin
routes 200; attendance with a scope 200; DOM and markup audits clean; stylesheet
braces 948/948; template encoding audit clean; `node --check` on the shell script;
max 25 queries.

### Fixed: multi-line `{# ... #}` comments leaked into the admin shell (10 November 2026)

Reported: raw comment text appeared at the top of every admin page and the shell
layout broke - content pushed to one side with an empty column.

Cause: Django's `{# ... #}` comment closes on the **same line**. A THREE-LINE
`{# ... #}` block added to `partials/components/admin_sidebar.html` (the
"no aria-hidden binding" note) was therefore not a comment at all: the text
rendered inside `.aw-shell`, and that stray text node became an anonymous grid
item in a three-column grid, pushing `.aw-main` out of its column.

Fix and prevention:

- Converted every multi-line hash comment to `{% comment %}...{% endcomment %}`:
  `admin_sidebar.html`, `promotion_evaluation_content.html`,
  `signup_settings_content.html` and two blocks in `user_detail_content.html`.
- Added `tmp/ui-audit/audit_hash_comments.py`, which fails on any `{#` without a
  matching `#}` on the same line (currently 0 findings).
- Extended `tmp/ui-audit/audit_markup.py` to treat a literal `{#` in rendered
  output as a template leak; it did not catch this class before.
- Added `tmp/ui-audit/check_shell_clean.py`, which asserts `.aw-shell` has
  exactly its three element children (rail, sidebar, main), no stray text nodes
  and no leaked template syntax. Clean on all eight sampled pages.

This is the second occurrence of this class in the project; the single-line rule
is now enforced by an audit rather than by memory.

### Telegram chat: duplicate bubbles and wrong-side student text fixed (10 November 2026)

Reported: a student's message appeared on the admin side, and an admin reply
appeared twice.

Cause: the support digest is stored as an **outbound** row whose text contains
the student's message (`Support message from ... / Level: ... / <student text>`),
and after a reply `_digest_answer_text` appends `Answered by @admin` to that same
text. When the signed-in admin had a linked Telegram account, the conversation
view rendered their own digest rows in the timeline, so the student's text
appeared inside a right-side bubble and the reply text appeared twice - once
appended inside the digest, once as the real outbound message.

Fix (presentation only; send/receive logic untouched):

- `_conversation_detail_context` always excludes `content_type=DIGEST` from the
  timeline and annotates each inbound message with `digest_alert` (the newest
  digest announcing it, scoped to the admin's own chat when known). The dead
  `own_admin_chat_id` context key was removed.
- `telegram_message_items.html` no longer renders digest bubbles. Alert state
  (removed / edited / expired / unavailable / failed) appears on the inbound
  message, and "Delete alert from Telegram" posts the existing delete endpoint
  with the digest id, so the alert is still removable from Telegram.

Media verified, not assumed: an inbound photo and an admin photo reply each
render one `img.aw-msg-image`. Admin media sent through the bot is already
archived to R2 by `store_attachment` inside
`reply_with_provider_media_to_conversation`, which is why it has a preview.

Reproduction: `tmp/ui-audit/repro_chat_duplicate.py` builds a conversation with
an inbound photo, a text reply, an admin photo reply and a resolved digest, then
asserts 3 bubbles, student text once, admin reply once, no digest header, 2
images and 1 alert delete action.

Verification: 187/187 templates compile; `manage.py check` clean; 36/36 admin
routes 200; DOM and markup audits clean; Telegram verifier green; encoding clean;
Arabic catalogue recompiled for the two new labels. Shipped as `b379e38`.

### Markup quality gate and the `None` cell fix (9 November 2026)

New audit: `tmp/ui-audit/audit_markup.py` renders all 35 admin routes and checks
for placeholder links (`href=""` / `href="#"`), template syntax leaking into the
output, POST forms without a CSRF token, empty `class=""` attributes, and images
without `alt`.

First run found one real defect: the courses table rendered the literal text
**"None"** in two cells, because the shared cell renderer printed `{{ value }}`
directly and two columns are null in the data. Fixed centrally in
`table_and_pagination.html` with `{{ value|default_if_none:"-" }}`, so every list
page now shows a dash instead of "None" for a null cell.

After the fix: **0 placeholder links, 0 template leaks, 0 POST forms without
CSRF, 0 empty class attributes, 0 images without alt** across all admin routes.

Verification: 186/186 templates compile; `manage.py check` clean; admin route
sweep 36/36 -> 200; DOM audit clean (0/0/0/0); stylesheet braces 926/926; query
review unchanged (max 25); encoding clean.

`tmp/ui-audit/audit_rtl_css.py` scans `admin_workspace.css` for physical,
direction-sensitive declarations (`margin-left/right`, `padding-left/right`,
bare `left:`/`right:`, `text-align: left/right`, `border-left/right`).

Result: **zero hits**. The workspace stylesheet uses logical properties
throughout (`margin-inline-start`, `inset-inline-end`, `text-align: start/end`,
`border-inline`), so the Arabic RTL layout mirrors from the same rules as the
English LTR layout without a separate override sheet. The legacy `app_admin.css`
and `app.css` still contain physical declarations, but they are the files the
rebuild is replacing.

### Cosmetic Bootstrap tokens cleared from the admin surfaces (9 November 2026)

After the `hidden` migration the remaining Bootstrap utility tokens were
presentation-only but still spread over fourteen admin templates. All were
replaced with named workspace utilities, added on demand (each has a live
consumer): `.aw-ta-c`, `.aw-ta-e`, `.aw-text-danger`, `.aw-text-warning`,
`.aw-strong`, `.aw-break`, `.aw-mb-2`, `.aw-mb-3`, `.aw-mt-1`, `.aw-me-2`,
`.aw-ms-1`, `.aw-ps-3`, `.aw-flex--between`, `.aw-p-1`, `.aw-self-start`,
`.aw-self-center`.

Files converted: promotion evaluation, content detail, notification delivery,
table and pagination, lesson editor, background jobs list, quiz form, admin panel,
levels, promotion history, announcements, polling toggle, admin operation status,
quiz import result and the drive picker.

Result: the token scan is down from **14 files / ~200 tokens to 1 file / 57
tokens**. The single remaining file is `templates/questions_rendering.html`, and
its tokens sit in the **student branch** (`{% else %}` of
`{% if not is_student and mode == "edit" %}`, plus one shared `<label>`). The
admin branch of that same partial already uses `aw-input`, so converting the rest
would either alter student markup or leave `aw-*` classes unstyled outside
`body.aw-admin`. It is deliberately left as the student boundary.

Verification: 184/184 templates compile; `manage.py check` clean; admin route
sweep 36/36 -> 200; DOM audit clean (0/0/0/0 across 64 renders); stylesheet braces
926/926; encoding clean; query review unchanged (max 25). A double-prefix scan
(`aw-aw-`) confirmed the mass replacements did not corrupt existing class names.

### Visibility contract unified on the `hidden` attribute (9 November 2026)

Every script-toggled region in the admin now uses the HTML `hidden` attribute
instead of Bootstrap's `d-none` class, and the stylesheet makes that attribute
authoritative:

```css
body.aw-admin [hidden] { display: none !important; }
```

That rule matters because several `aw-*` classes set `display`, which outranks
the UA `[hidden]` rule; without it a hidden element with `display:flex` would
still render.

Converted together (markup and script in the same unit, so no half state):

- Scanner: `scanner.js` (7 toggles) + `scanner_content.html` (4 regions). The
  video's inline `style="position: absolute; inset: 0"` moved into
  `.aw-scan-frame .scanner-video`, so no inline layout style remains.
- R2: `r2Manager.js` (11 toggles, including the optional-chain assignment that
  had to become an explicit `if (panel) panel.hidden = true`) +
  `r2_management_content.html` (3 regions). The operation panel's inline
  `style="flex-direction:column;align-items:stretch"` became `.aw-alert--stack`.
- Applications: `applications.js` (select-all button and the bulk summary) +
  the dashboard partial's status alert.
- Lesson editor: `lessonForm.js` (both the template string and the toggle) + the
  download-key field and the delete form.
- Quiz form: the hidden delete form.
- Media processing: the CSRF form.
- Report dashboard: the row condition now emits `hidden`.
- Scanner preview: both status alerts and the four inline-script toggles.

Result: **zero `d-none` occurrences** in `management_system/templates` and in
`management_system/static/js`. Remaining Bootstrap utility tokens are cosmetic
only (34 `text-center`, 15 `mb-0`, 15 `fw-semibold`, 14 `w-100`, 13
`text-danger`, 13 `text-break`, 13 `mb-2`, ...) spread over 14 files; they render
identically to the aw equivalents and are tracked, not defects.

Verification: `node --check` passes on scanner, applications, lessonForm and
r2Manager; `manage.py check` clean; 184/184 templates compile; admin route sweep
36/36 -> 200; DOM audit clean (0/0/0/0 across 64 renders); stylesheet braces
910/910; encoding clean.

### Command palette moved onto the workspace primitives (9 November 2026)

The quick-jump palette was the last Bootstrap component in the admin shell. It
now renders with aw classes while keeping the Bootstrap *modal* shell (show/hide
and focus trap are Bootstrap's job and are used by other dialogs too).

- Template: input `form-control` -> `aw-input`; results `list-group` ->
  `aw-cmd-results`; hint `form-text text-muted` -> `aw-field-hint`; the title
  icon is the `i-search` sprite. The `#command-palette-input`,
  `#command-palette-results`, `#command-palette-hint` and
  `data-search-url` hooks are unchanged.
- `command_palette.js`: section headers -> `aw-cmd-section`; rows ->
  `aw-cmd-item` with `is-active` instead of `active`; hit link -> `aw-cmd-hit`
  with `aw-cmd-hit-top` / `aw-cmd-title` / `aw-cmd-sub`; pin button ->
  `aw-cmd-star` with `is-pinned` (replacing `text-warning` / `text-muted`);
  empty, loading and error rows -> `aw-cmd-empty` (error adds `is-error`); the
  hint now uses the `hidden` attribute. `rerenderKeep` toggles `is-active`, and
  keyboard navigation still selects rows through `[role="option"]`, so no
  selector depended on the Bootstrap class.
- New CSS block `.aw-cmd-*` (results list, item, active/hover, section,
  empty/error, hit, top row, title row, sub, star, pinned star).

Kept deliberately: the Bootstrap modal wrapper and its `btn-close`, the
`.cmd-type` pill, and the `.cmd-star` transform from `app_admin.css`.

Consequence: the `#command-palette-results .list-group-item*` rules in
`app_admin.css` are now dead. They cannot be unlinked from this session; they are
cosmetic no-ops and should be removed with the `tg-chat-*` block.

Verification: `node --check command_palette.js` passes; the DOM audit stays clean
(0 duplicate ids, 0 unlabelled fields, 0 nameless buttons, 0 tables without `th`);
184/184 templates compile; admin route sweep 36/36 -> 200; query review unchanged
(max 25); stylesheet braces 908/908; encoding clean. The Bootstrap-token scan is
down to 19 admin partials; the only remaining `list-group` is in
`course_detail.html`, which is a student surface.

### Rendered-DOM accessibility audit (9 November 2026)

New reusable check: `tmp/ui-audit/audit_dom.py` renders every admin route in
English and Arabic on the disposable schema and reports duplicate ids, form
fields with no accessible name, buttons with no accessible name, and tables with
no header cell.

First run (64 page renders) found: 14 unlabelled fields, 8 nameless buttons, and
**36 tables with no `th`** - the last one came from the shell-wide
`shortcuts_help.html` modal, which rendered a Bootstrap `table table-sm` with a
`tbody` only, so every page in the app carried an unlabelled table.

Fixed:

- `shortcuts_help.html`: `table table-sm mb-0` -> `aw-table aw-m-0` with a
  `thead` of two visually-hidden `th` (Shortcut / Action); the hint moved to
  `aw-field-hint aw-m-0` and the title icon to the `i-keyboard` sprite.
- `table_and_pagination.html`: the select-all and per-row checkboxes are now
  `aw-check-input` (which the new CSS geometry actually styles) with explicit
  `aria-label`s, and the "select all matching" button has a static
  `aria-label` because its text is filled by Alpine at runtime; its FA icon
  became the `i-check` sprite and the stray `ms-2` was dropped.
- `telegram_accounts_content.html`: the search field gained `aria-label`.

After: **0 duplicate ids, 0 unlabelled fields, 0 nameless buttons, 0 tables
without `th`** across all 64 renders (the only non-200 is `calendar-management`,
which redirects when no month is supplied). Five Arabic entries were added for
the new labels (Shortcut, Select all rows on this page, Select row, Select all
rows matching the filter, Search accounts).

`media_upload.js` produced Bootstrap markup after the template was converted, so
the upload page still showed Bootstrap rows and bars at runtime:

- Selected files were `li.list-group-item.d-flex` with a `badge`; they are now
  `li.aw-file-row` containing an `.aw-cell-main` identity block and an
  `.aw-pill.aw-pill--green` state.
- Job cards were `div.progress > div.progress-bar`; they are now
  `.aw-progress > span` with the percentage mirrored onto the wrapper's
  `aria-valuenow`. `setCardProgress` updates the span width and the wrapper.
- `d-none` toggles became the `hidden` attribute (`validationErrors`,
  `fileList`, retry buttons, job error) and the two template placeholders in
  `upload_video_content.html` use `hidden` instead of `d-none`.
- `text-muted`, `fw-semibold`, `mb-2`, `mt-1`, `small` and `gap-2` were replaced
  with `aw-muted`, `aw-small`, `aw-mt-2` and the `aw-cell-main-text` structure.

Verified: `node --check` passes; the upload page returns 200 with both toggled
regions carrying `hidden`, no `d-none` anywhere in the page, and the dropzone
present. Bootstrap-token scan is now 20 admin partials, and the only remaining
`list-group` is the command palette.

The command palette stays on Bootstrap deliberately: it is a `modal` overlay
whose item markup (`list-group-item`, `btn btn-link`, `active`) is keyed to
custom `.cmd-palette` CSS and its own keyboard navigation. Converting it is a
design task with new CSS, not a class swap, so it is recorded rather than
half-converted.

### Review pass (9 November 2026)

Full report: [admin UI rebuild review](docs/current/ADMIN_UI_REBUILD_REVIEW_2026-11-09.md).
Evidence was produced on the disposable schema with seeded data.

- Performance: 31 routes measured with `CaptureQueriesContext`; **max 24
  statements** (Today), no route above 24. Only two routes repeat a statement
  four or more times: Today (11 counts, one per queue by design) and
  `historical-intake` (4 repeated academic-year-level selects - the one real
  N+1-shaped repetition, recorded as a follow-up).
- Security: capability matrix verified for admin/staff/moderator/student on ten
  routes; no non-admin reaches an admin-only page; `scanner` for moderator is
  correct by policy. A state-changing POST without a CSRF token returns 403.
  Zero `|safe`/`mark_safe` in templates. `promotion-formula` is gated by
  `can_view_reports` (staff read-only) while its write path raises
  `PermissionDenied` internally, so there is no escalation - recorded as a
  product decision, not a defect.
- Parity: shell geometry, page anatomy, context strips (14 partials), canonical
  filter bars, table footers, sticky bulk bar, Telegram three-column layout,
  editor part/question cards, admin submission detail, academic/promotion tab
  panels and stepper, per-meeting attendance matrix, joined calendar surface.
  Data/endpoint-dependent regions remain deliberately unbuilt and are listed.
- Dead code removed in this pass: `matrix_column_count` context key (unused
  after the matrix rewrite) and the dead `extended_view == 'admin_panel.html'`
  branch in `display_quiz.html` (the only caller passes `base.html`, so student
  output cannot change).
- Dead code tracked, not removable from this session (shell writes are denied):
  the contiguous `tg-chat-*` block in `app_admin.css:1274-2130`. The exact
  one-command cleanup is recorded in the review report.
- Still open: `compilemessages` for the new msgids; `media_upload.js` generated
  markup; the two-breadcrumb-trail decision; UI-kit page; transcript view;
  attendance day drawer endpoint.

Verification after the review edits: `manage.py check` clean; 181/181 templates
compile; admin route sweep 36/36 -> 200; query-count re-measurement unchanged
(max 24, two routes with repeats); stylesheet braces 884/884; encoding clean;
Curriculum, Phase 5 and Phase 6 verifiers green.

## Current task disposition

Admin workspace port ([plan revision 1.3](docs/pending/ADMIN_WORKSPACE_PORT_PLAN.md))
is in progress in the current working tree, uncommitted. Completed units:
Phase 0 baseline/guardrails, Phase 1 shell and navigation, Phase 2 shared
primitives, the P-04 Students vertical slice (filters, signed selection,
row projection, scoped export) and the P-01 Today cockpit. Remaining: the
page-conversion phases 3-9, Phase 10 legacy removal and Phase 11 release
acceptance. No model, migration, data, storage, queue, provider or dependency
change was made in these units.

The 8 November documentation unit that revised plan 1.3 is complete and is
preserved below as the earlier disposition. Validation for the overview port
units: encoding audit, Django checks, template rendering, route/fragment smoke,
role matrix and query counts are recorded in the latest unit section.

Latest documentation unit (8 November 2026): created
[TELEGRAM_SUPPORT_CHAT_ENHANCEMENT_SPEC.md](docs/pending/TELEGRAM_SUPPORT_CHAT_ENHANCEMENT_SPEC.md)
and indexed it in docs/pending/README.md. The user confirmed cumulative
message-prefix answering, active-year normal enrollment as the current-level
source with an unassigned label when unavailable/ambiguous, preserving each
admin digest while appending the responder, and website status visibility.
The spec integrates the work into P-33/T08.2 and covers browser voice capture,
Telegram admin voice/media replies, source mapping, bot identity, storage,
authorization, UX, failure handling and acceptance evidence. Source inspection
found the current media handlers are asymmetric and the web polling path does
not refresh older changed digest rows. Official Telegram API documentation
shows the 48-hour restriction is for deletion; the current code also blocks
editing its own bot-sent digests after 48 hours. The spec records that as a
source/API mismatch and requires a non-production provider check. No
application code, schema, database, provider, or dependency changes were made.
Markdown fences and relative links resolve. The encoding audit passed for 678
text files. No app runtime or provider checks were run. Next safe step:
integrate the reviewed contract into the P-33/T08.2 task and settle the media
archive limit, browser audio fallback, recording duration, and bot-rotation
policy before implementation.


Latest completed unit (8 November 2026) - admin workspace base, Students, Today.

Implemented:

- One shell: `admin_panel.html` owns `.aw-shell`, the 9-domain rail, the
  contextual sidebar, the sticky top bar, the off-canvas mobile navigation and
  the single peek drawer. `#display-page`, `#page-region`, `#content`,
  `#admin-sidebar`, `#mobileAdminMenu`, `#toast-stack`, `#globalConfirmModal`
  and every table/palette/notification hook are preserved. The legacy
  `admin-layout`/`ad-shell` shell, the 33-link `admin_nav_links.html` list and
  the raw URL-name `admin_breadcrumbs.html` fallback are deleted; no second
  navigation source remains.
- `management_system/admin_navigation.py` is the only navigation model:
  capability-filtered domains/sections with URL names and route aliases,
  quick-create actions and top-bar actions. It performs no queries and holds no
  role strings. Active state, breadcrumbs and mobile state resolve through it.
- Persistent shell updates: admin page navigation declares
  `hx-select-oob="#aw-rail-nav,#aw-sidebar-body,#aw-mobile-nav,#aw-topbar-crumbs"`;
  `render_page()` appends those regions (from the same model and the resolved
  route) to every `#display-page` response, so cross-domain navigation updates
  the rail, sidebar, mobile nav and top-bar crumb. Filter/table/drawer swaps
  stay single-region responses and never rebuild navigation.
- Shared primitives: `partials/components/aw_*` (icon sprite ported once,
  avatar, status pill, page head, filter bar, empty) plus scoped
  `static/css/admin_workspace.css` tokens/components (admin-only `body.aw-admin`
  theme owner, overlay inheritance, logical properties, 991.98/720/575.98
  breakpoints, documented `!important` set). `proto.css` is not imported.
- Tables: the canonical `table_and_pagination.html` gained `aw-*` skin classes,
  optional `row_template` support and the shared empty state; the Students row
  template renders avatar/name/application pill/mode/role/dates and row actions.
  Pagination keeps its query/target contract with `aw-*` skin classes.
- Students (P-04): `user_scope`/`user_queryset` now normalize search, role,
  application decision, study mode and account state; `user_tab_counts` returns
  accurate per-dimension counts in one aggregate each; the signed selection
  token embeds the same scope; the CSV export consumes that scope; columns are
  Students/Application/Mode/Role/Joined/Last login with SQL sorting.
- Today (P-01): `management_system/admin_today.py` builds capability- and
  ownership-filtered queues (pending applications, failed media jobs, ungraded
  answers, unread Telegram support) with one count plus one <=5-row sample each,
  bounded upcoming meetings/holidays, an active-student count and `as_of`.
  The page head, KPI grid, attention list, upcoming, quick actions and recent
  activity replace the old 20-aggregate KPI wall; the shared aggregates stay
  behind the existing per-date cache and the cockpit renders through
  `render_page()`.
- `admin_shell.js` gained one delegated menu controller, one drawer lifecycle
  (fence stale loads, Escape, focus restore, no detached work across
  navigation) and Alpine initialisation for the OOB navigation regions;
  `shortcuts.js` keeps `g`+letter navigation across the new link classes.

Verification (host Django 5.2.17, disposable PostgreSQL 18.6
`lms-disposable-pg` 127.0.0.1:55432 / `lms_remediation_20261105`, leaf 0111):

- `manage.py check`, `makemigrations --check --dry-run` (no changes),
  `py_compile`/focused Ruff (F401/F811/F821/F841) on changed Python,
  `node --check` on changed JS, encoding audit (680 files), `git diff --check`.
- Route/fragment smoke: Today, Students, Courses, Lessons, Quizzes direct and
  HTMX page navigation return one shell or `#page-region` + OOB regions, no
  `<html>` in fragments, no duplicate ids, no `admin-layout`.
- Role matrix (capability functions, no DB writes): admin 9 domains, staff 7
  domains without admin-only sections, moderator only Scanner, student none;
  quick-create filtered; denied routes fall back to the first permitted section;
  18 route aliases resolve to the correct active section.
- Students slice: filters return only matching rows, invalid values are ignored,
  tab counts equal filtered counts, signed selection round-trips and rejects a
  foreign view, CSV rows equal the filtered population, student role 403,
  staff actions hidden. Full page = 12 SQL statements (count + page + 2 tab
  counts + auth/session), no per-row query.
- Today: queue counts equal their canonical destination counts, queue samples
  are bounded to 5, non-admin actors lose admin queues, staff media ownership
  filtering verified, adapter = 7 SQL statements (4 counts + active students +
  upcoming meetings/holidays), page cold 23 / warm 10 SQL with LocMem cache.

Second wave (same day, still uncommitted):

- Applications list and review now render through `render_page()` with
  `partials/applications_dashboard_content.html` and
  `partials/application_review_content.html`; the review detail trail stays
  inside `#content` (`page_region_breadcrumbs=False`) because its decision forms
  swap that root. The list consumes view `filter_tabs` (counts equal the
  filtered list), page head and Signup Settings action; the shared table
  application branch now renders avatar/name, mode, submitted date and the
  shared status pill (8 columns). `applications.js` bulk-result tones and
  `filter_presets.js` (selector plus appended controls) use the aw components.
- Historical intake and signup settings partials converted; intake gained a
  JS-free anchor tab strip (`historical-single/existing/csv/records`). The
  pre-existing duplicate-id defect (two unprefixed forms on one page) is fixed
  by `HistoricalExistingBulkForm(auto_id="id_existing_%s")`; POST names are
  unchanged and every label `for` resolves.
- Student 360: page head, context strip, anchor tabs and 13 cards; bounded
  client pager, read-only/audit/phone-masking contracts unchanged.
- Scanner now renders inside the admin shell through
  `partials/scanner_content.html` plus `render_page()`; camera ids and the
  `d-none` show/hide contract are unchanged. `/scan/<token>/` intentionally
  keeps the focused base shell.
- Courses/lessons/quizzes: courses annotate distinct offering/lesson/quiz
  counts; quizzes annotate question count; lessons and quizzes gained canonical
  publication-status filters with accurate tab counts and a level/year/offering
  filter bar (year changes swap the page so offering options refresh). The
  legacy `year_filter`, `academic_year_filter`, `naming_filter` and
  `course_filter` partials are deleted; no caller remains.
- Shared surface: `partials/breadcrumbs.html` rewritten to aw classes,
  `partials/admin_operation_status.html` skinned, shortcuts modal skinned, and
  every multi-line `{# ... #}` comment converted to `{% comment %}` (Django
  comments are single-line; multi-line ones leaked visible text into HTML). The
  leak was present in the aw components, file card/menu partials and several
  in-flight page drafts; all are clean now.

Second-wave verification (same disposable environment): route/fragment sweep
passed for applications list (`?status=pending`) and detail, historical intake,
signup settings AR/EN, Student 360 AR/EN and scanner; contract-token diffs kept
every id/hx/name/action (the only replaced applications tokens are four
hardcoded tab `hx-get` URLs now built from `filter_tabs`); every admin GET route
renders 200 with no duplicate ids (POST-only offering edit is skipped); shell,
Students and Today suites rerun clean; no repeated SQL statement appears four or
more times (no N+1). Representative page statement counts: Today 23 cold / 10
warm, Students 9, Courses 8, Lessons 11, Quizzes 10, Applications list 6,
Applications detail 10, Historical intake 11, Signup settings 9, Content QA 11,
Scanner 3, Student 360 21 fixed. `applications.js` and `filter_presets.js` pass
`node --check`; encoding audit 680 files.

Third wave (same day, still uncommitted):

- Student editor and user detail converted; bodies extracted to
  `partials/dashboard_form_content.html` and `partials/user_detail_content.html`.
  `FormBase.render_to_response` now returns the `#page-region` fragment for
  HTMX requests (direct URLs keep the shell), and `user_profile` renders through
  `render_page()`. `FormBase.view_name`/`action` moved to `gettext_lazy` so
  English editor pages no longer render Arabic h1/back labels.
- Content detail and Content QA converted (`partials/content_detail_content.html`,
  `partials/content_qa_content.html`); `lesson_detail` and `quiz_detail` now
  return page-region fragments, and the QA `#content` root moved into the
  partial so direct and fragment responses share one root. Content QA's
  open-all helper uses the aw card selector.
- Lesson editor converted (`partials/lesson_editor_content.html`) and
  `_render_lesson_editor` returns page-region fragments; every media/autosave
  hook (`#videos-input`, `#lectures-display`, part fields, draft key) survived
  the token diff.
- Moved contract tokens verified against HEAD for all extracted partials:
  applications 39/43 (four hardcoded tab URLs replaced by view-built URLs),
  review 31/31, lesson editor 45/45, content detail 18/18, dashboard form 21/21,
  user detail 22/22.
- Browser evidence (Playwright 1.49 / Chromium 131, dev server on disposable DB):
  24 page/viewport/locale combinations (10 AR pages + Today/Students EN at
  1440x900 and 390x844) returned 200 with one shell, one `#content`, zero
  document overflow, desktop rail and mobile toggle present, and zero console
  errors, page errors or failed local requests. Cross-domain HTMX navigation
  from Today to People then Students updated the rail (People active), the
  contextual sidebar (Students active) and the URL. Quick-create and account
  menus, palette open/Escape, and the mobile offcanvas (open, close on
  navigation) also pass. Screenshots: `C:\Users\George\AppData\Local\Temp\
  lms_shots2`.
- Encoding audit note: the repo-wide `audit_text_encoding` is currently red only
  because an unrelated docs file appeared during the session
  (`docs/pending/lms_student_intelligence_architecture_prd.md`, created
  18:27, 60 non-ASCII lines, not authored by this work). It was not modified.
  Scoped audits pass: `management_system` 470 files, `locale` 2 files, and the
  handoff/instruction Markdown files were checked manually (0 violations).

Fourth wave (same day, still uncommitted):

- Quiz editor and quiz-submissions list converted; bodies extracted to
  `partials/quiz_form_content.html` and
  `partials/submission_dashboard_content.html`. `create_quiz`/`update_quiz` now
  return page-region fragments through `render_page()`, and
  `submission_dashboard` sets `content_template_name` so `render_dashboard()`
  returns the same fragment contract. Every editor hook survived (`#quiz-form`
  translations wrapper, `.questions-container` include, import panel ids,
  disabled fieldsets/status select, autosave x-data, export POST forms).
- The lesson editor wrapper/partial split from the third wave was confirmed by
  its owner; create/update routes pass the route/fragment sweep.
- A temporary draft quiz fixture (disposable DB only) proved update and
  submission-detail fragments in AR/EN: direct 200 with one shell/`#content`, no
  duplicate ids, HTMX returns `#page-region` + OOB regions with no `<html>` or
  shell. The fixture was deleted in the same run (0 rows remain).
- Browser check of the new pages (Playwright/Chromium, 1440 and 390):
  `/ar/quizzez/create/`, `/en/quizzez/create/`, `/ar/lessons/create/`,
  `/ar/lessons/668/`, `/ar/content-qa/` -> 200, one shell, one `#content`,
  zero document overflow, zero console/page errors; screenshots in
  `C:\Users\George\AppData\Local\Temp\lms_shots2`.
- New reference document:
  [ADMIN_WORKSPACE_NAVIGATION_AND_SHELL.md](docs/reference/ADMIN_WORKSPACE_NAVIGATION_AND_SHELL.md)
  explains the navigation model, capability funnel, shell/OOB lifecycle,
  `render_page`/`render_dashboard` contract and the extension recipe with
  diagrams; it is linked from `docs/reference/README.md`.

Fifth wave (same day, still uncommitted):

- Academic: `partials/academic_setup_content.html`, `levels.html` split into
  `partials/levels_content.html`, `calendar_management.html` split into
  `partials/calendar_management_content.html`, and
  `partials/calendar_cell_admin.html` icons. `levels_dashboard` and the calendar
  GET path now render through `render_page()`; their fragment sweeps pass. The
  student calendar partials and `calendar_day_sheet.js` selectors were left
  untouched by design.
- Media: `partials/media_processing_status_content.html`,
  `upload_video.html` split into `partials/upload_video_content.html`, and
  `partials/graduation_gallery_manage_content.html`; `upload_file` now renders
  through `render_page()`. Every media/gallery polling, staging, data-media-*,
  scope and delete hook survived the token diff.
- Reports: `partials/report_dashboard_content.html`,
  `partials/grade_matrix_content.html`, `partials/payment_matrix_content.html`.
  Their views already used `render_page()`; sweeps pass for combined/grades/
  payments in AR and EN. No summary metric was invented: grade distribution,
  at-risk, attendance percentage and payments coverage remain reported
  projections.
- Promotion: `partials/promotion_evaluation_content.html` (single `#content`
  root; the previous markup had unbalanced closing tags and the formula/preview
  outside the root) and `partials/promotion_history_content.html`. Five-step
  stepper is anchor-only with no fabricated active/done state; every formset,
  override/promote/export action and pagination contract preserved.
- Attendance: `partials/attendance_management_content.html` and
  `attendance_correction.html` split into
  `partials/attendance_correction_content.html`; the correction GET path now
  uses `render_page()`. The view gate stays `can_view_reports` (source of truth
  over the plan table). Fixed a pre-existing policy paragraph that printed raw
  `%(limit)s` placeholders.
- Backend fix: `promotion_formula` no longer raises a 500 when "preview" is
  posted for a scope without a saved formula; it renders an empty saved-results
  section. This was a pre-existing template-independent defect reproduced
  through the real route.
- Hygiene: stripped UTF-8 BOMs that had been written by earlier shell edits
  from nine changed files (including `admin_panel.html` and
  `pagination.html`), so pages no longer emit U+FEFF before the doctype or
  inside pagination markup. Added the remaining Arabic catalog entries
  ("Fully scanned", "Weight (%)", "Min. Pass (%)", "Total weight: 0%", academic
  descriptions, "Legend") and rebuilt both MO catalogs.
- Verification: all admin GET routes render 200 with one shell/`#content` and no
  duplicate ids; converted-route sweep passes for the full set (shell pages,
  applications, academic, levels, calendar target, promotion, media, upload,
  gallery, attendance, reports, editors); moved-token diffs keep 29/29 levels,
  48/48 calendar, 36/36 upload, 11/11 correction, 66/66 promotion, 19/19 history,
  54/54 quiz editor, 26/26 submissions. Browser check (Chromium, 1440/390)
  covered 14 wave-3 pages at both widths plus EN variants: 200, one shell, one
  `#content`, zero document overflow, zero console/page errors. Screenshots in
  `C:\Users\George\AppData\Local\Temp\lms_shots2`.
- Remaining waves: engagement (announcements + Telegram), R2 storage,
  submission-detail admin branch (`display_quiz`), then Phase 10 legacy
  `ad-*`/dead-CSS removal and Phase 11 full release matrix.

Sixth wave (same day, partially verified):

- Site & Apps converted and integrated: `partials/banner_manage_content.html`
  and `partials/app_release_manage_content.html` (15/15 and 40/40 contract
  tokens preserved). The six pre-existing Arabic gaps for release labels were
  added and both catalogs rebuilt; banners/releases route sweep passed before
  the environment loss.
- Reports part 2 converted: `partials/progress_dashboard_content.html`,
  `partials/progress_activity_sessions.html`,
  `partials/application_analytics_content.html`. Static integration review
  passed (31/31 and 40/40 contract tokens preserved, no missing msgids, all
  changed templates compile) and the fresh-database route/fragment sweep now
  passes for `/dashboard/progress/`, `/dashboard/reports/analytics/` in AR and
  EN, so these pages are runtime-verified as well.
- Fixed a latent template defect found by the compile sweep:
  `partials/admin_operation_status.html` had an unclosed `{% if %}` in its
  x-data attribute, so any page that included it during an active job would
  raise a template error. It now closes correctly and compiles.

Environment restored from scratch (same day, after the user's Docker cleanup):
Docker Desktop had come back empty, so a new disposable PostgreSQL was created
and the whole baseline was re-established instead of trusting earlier evidence.

- Container: `lms-workspace-db` = `postgres:16-alpine`, database
  `lms_workspace_dev`, user `lms_user`, password `local_dev_password`
  (disposable only; never production/SQLite). `manage.py migrate` applied the
  full chain through `0111_mobile_push_device_language`; `setup_db` created the
  canonical roles.
- Port note (2026-11-09): host port 55432 fell into a Windows excluded TCP range
  (55377-55476) for this non-elevated session and could not be bound. The same
  volume is served as `lms-workspace-db-run` on host port 5433, and all
  verification used `POSTGRES_PORT=5433`. Clearing the excluded range needs
  elevation; the database contents are identical.
- Synthetic verification rows (disposable, no real data): `qa_admin`,
  `qa_staff`, `qa_student` (password `LocalDevPass!234`), academic year
  `2026/2027` active, levels First/Second, one published `QA Course` offering
  and one published `QA Lesson`.
- Re-created verification scripts (Temp was also cleaned): `lms_route_sweep2.py`,
  `lms_render_all2.py`, `lms_browser_baseline.py`, `lms_msgid_check2.py`,
  `lms_compile_templates.py`, `lms_token_partial.py`, `lms_runserver2.ps1`.
- Fresh baseline evidence: core converted-route sweep passes (Today, Students,
  Courses, Lessons, Quizzes, Applications, Historical intake, Signup settings,
  Student 360); every admin GET route renders 200 with one shell, one `#content`
  and no duplicate ids; browser (Chromium, 1440/390, AR/EN) on Today/Students is
  200 with zero overflow, zero console/page errors, and cross-domain HTMX
  navigation updates rail + sidebar + URL; screenshots in
  `C:\Users\George\AppData\Local\Temp\lms_shots_baseline`.
- Environment notes: Redis is not running, so the dashboard cache logs
  read/write failures (non-fatal, as before); host Django is 5.2.17 while the
  project target is 5.1.4.

Engagement wave (same day, in progress, uncommitted):

- Converted and integrated: `partials/notifications_management_content.html`,
  `partials/telegram_accounts_content.html`, `telegram_config.html`,
  `partials/telegram_broadcast_list.html`. Token diffs preserve 18/18, 29/29,
  11/11 and 10/10 contract hooks; no new msgids were needed.
- `telegram_config.html` and `telegram_broadcasts.html` were split into
  `partials/telegram_config_content.html` / `partials/telegram_broadcasts_content.html`
  and their GET/POST render paths now use `render_page()`, so
  `/dashboard/telegram/` and `/dashboard/telegram/broadcasts/` pass the
  route/fragment sweep (previously full-shell fragments). Notifications and
  Telegram accounts already passed.
- Authorized structure decision for the Telegram inbox: keep the existing
  column/switch-target structure and `#telegram-chat-panel` innerHTML contract;
  do not add an `aw-chat-layout` three-track grid, a `#telegram-chat-context`
  OOB target, or new hx targets. The aw skin is applied to the existing
  elements, context details stay inside the existing panel, and profile images
  are wrapped rather than replaced by initials.
- R2 storage converted and integrated: `r2_management.html`,
  `partials/r2_browse.html`, `partials/r2_file_list.html`; the page body was
  extracted to `partials/r2_management_content.html` (with `{% load i18n %}`)
  and `r2_management_dashboard` now returns page-region fragments. Token diff
  preserved 45/45, 9/9 and 9/9 hooks; route/fragment sweep passes for
  `/dashboard/r2-management/`.
- Telegram broadcast compose/confirm/detail and the shared polling toggle were
  converted and integrated: `telegram_broadcast.html`,
  `telegram_broadcast_confirm.html`, `telegram_broadcast_detail.html`,
  `partials/background_polling_toggle.html`, plus the new content partials
  `partials/telegram_broadcast_content.html`,
  `partials/telegram_broadcast_confirm_content.html`,
  `partials/telegram_broadcast_detail_content.html`. Their views now use
  `render_page()`, and the broadcast list header was converted to the aw page
  head. The compose route passes the fragment sweep; confirm/detail were
  verified with a temporary broadcast row (created, exercised, deleted).
- Telegram inbox converted and integrated: `telegram_conversations.html`,
  `telegram_conversation_detail.html`, and the six conversation/message/panel/
  linked-user partials. The two page views were split into
  `partials/telegram_conversations_content.html` and
  `partials/telegram_conversation_detail_content.html` and now return
  page-region fragments; the panel/items/message fragment branches are unchanged
  and still render their own partials. The structure decision was honored: no
  three-track `aw-chat-layout`, no `#telegram-chat-context` target, no new hx
  target; aw classes were added alongside the `tg-*` JS hooks, profile images
  are wrapped in `aw-avatar`, and the reply/claim/release/delete/mark-read
  contracts and multi-stage pollers were preserved token-for-token. List sweep
  passes; detail and its HTMX fragment were verified with a temporary
  conversation row (created, exercised, deleted).
- Submission detail admin branch: `display_quiz.html` now adds, only when
  `extended_view == 'admin_panel.html'`, the workspace root class, a page head
  ("Quiz Details", quiz name and student) and a final-grade context strip. The
  shared student exam markup is untouched (token diff 0 removed/0 added; the new
  blocks are inside the admin-only conditional). Verified with temporary
  quiz/question/grade/submission rows (created, exercised, deleted): 200, one
  shell, one `#content`, page head and grade strip present. Accepted exception:
  this shared template still returns the full shell for HTMX page navigation
  (the client selects `#page-region`), because splitting it would risk the
  student exam branch; the strict fragment sweep does not apply to this route.
- Protocol evidence (fresh DB): one consolidated fragment sweep over 40
  converted routes (shell pages, People, Curriculum, Academic/levels/calendar,
  Attendance, Promotion, Media/upload/R2/gallery, Reports/analytics, Engagement
  including the Telegram inbox/config/accounts/broadcasts/compose, Site & Apps,
  Scanner) ends `ROUTE SWEEP PASSED`. One consolidated browser matrix over 38
  routes at 1440x900 and 390x844 (AR, plus Today/Students EN) returns 200 with
  one shell, one `#content`, zero document overflow and zero console/page
  errors; the R2 stats API made `networkidle` time out, and a DOM-load rerun at
  both widths confirms 200/shell/content/overflow 0. Screenshots in
  `C:\Users\George\AppData\Local\Temp\lms_shots_matrix`.
- Background jobs / notification history converted and integrated:
  `partials/background_jobs_content.html`, `partials/background_job_detail_content.html`
  and `partials/notification_delivery_content.html`. The list keeps every
  `backgroundJobsMonitor` row contract (`data-job-record`, `data-job-field`,
  the single `<progress>`, poll error); the detail page keeps the summary
  `data-job-*` root, progress element and polling/redelivery/withdraw forms; the
  delivery sections keep every id, hx chain, form and pagination arguments.
  Verified against an existing job row: detail returns 200 with the aw shell and
  returns a page-region fragment for HTMX, monitor attributes present. The
  student notification inbox partials (`notification_history_items.html`,
  `notification_history_page.html`) were deliberately NOT converted: they are
  student-owned (`ps-notif-*` under `base.html`), and aw CSS is scoped to
  `body.aw-admin`; recorded as a scope decision.
- Leftover converted-page classes cleaned: `partials/application_analytics_content.html`
  and `partials/telegram_message_list.html` no longer carry stray `ad-*` visual
  classes; `ad-table` remains only as the documented table hook.
- Phase 10 scan: 161 distinct `ad-*` names remain across 45 files, but they are
  the preserved theme showcase/reference templates, the shared `ad-table`/
  `ad-table-shell` JS hooks, JS-generated markup (`lessonForm.js`,
  `media_upload.js`, `r2Manager.js`), and two surfaces still being converted
  (`user_bulk_form.html`, `drive_files.html`). Mass CSS deletion is therefore
  not safe until those final surfaces land; the remaining removal is a bounded
  follow-up rather than a wholesale stylesheet delete.
- Final two surfaces converted and integrated: `user_bulk_form.html` (body split
  to `partials/user_bulk_form_content.html`, `user_bulk_create` moved to
  `render_page`) and the shared `drive_files.html` folder/file picker. Token
  diffs preserved 12/12 and 6/6 hooks; the drive picker keeps `#drive-files`,
  `.col`, `.card-body`, `.card-title`, `.file-icon`, `data-id` and
  `@click="addFile"`; bulk-create and the pages that include the picker pass the
  fragment sweep.
- Phase 10 disposition: the remaining `ad-*` names are the documented
  `ad-table`/`ad-table-shell` JS hooks, the preserved `theme_showcase`
  reference templates, calendar/file-picker classes that are JS/CSS hooks, and
  JS-generated markup in `lessonForm.js`/`media_upload.js`/`r2Manager.js`.
  Mass stylesheet deletion is therefore unsafe and was not done. The dead
  `partials/empty_state.html` component (zero callers) was deleted and the last
  stray `ad-icon--danger` class in the shell replaced with a Bootstrap utility.
- Phase 11 evidence: all admin GET routes render 200 with one shell, one
  `#content` and no duplicate ids; a 40-route fragment sweep passes; a 38-page
  browser matrix (1440x900 and 390x844, AR plus EN samples) reports zero console
  and page errors; the final four surfaces (bulk import, background jobs,
  notification history, job detail) pass at both widths with zero body overflow
  and zero console errors. Note on measurement: in RTL, wide tables inside an
  internal `aw-table-wrap` scroll container can make
  `document.documentElement.scrollWidth` exceed the viewport while
  `document.body.scrollWidth` stays exact and `window.scrollX` cannot change
  (body uses `overflow-x: clip`), so the acceptance measure uses body width.
- Remaining: Phase 11 completion for any future page, and then the Telegram
  support chat enhancement spec.

Review pass (2026-11-08): full review report at
[ADMIN_WORKSPACE_REVIEW_2026-11-08.md](docs/current/ADMIN_WORKSPACE_REVIEW_2026-11-08.md).
Fix applied during the review: the five `request.user.role.role == 'admin'`
checks in the shared table partial now use `aw_capabilities` +
`caps.manage_academic_setup` (the exact `recalculate_quizzes` decorator); dead
`partials/components/mobile_menu.html` and `partials/empty_state.html` were
deleted (no callers); the shell's last `ad-icon--danger` became a Bootstrap
utility; leftover `ad-*` visual classes were removed from the analytics and
Telegram message partials; `render_dashboard` lost its mutable default argument;
and the latent unclosed `{% if %}` in `partials/admin_operation_status.html` was
fixed. Prototype parity re-verified token-for-token (tokens, radii, fonts,
rail/sidebar/topbar geometry, no `proto.css` import, overlay token scoping).
Security probe re-ran on the fresh DB: signed selection round trip plus foreign
view/actor rejection, export parity, strict filter parsing, capability matrix
(admin 9 domains, staff without admin domains, student none), 403/200 role
checks - all pass. Query audit over 24 converted routes: 4-23 statements, no
repeated statement four or more times. Review finding P-2 was then implemented:
`aw_page_head_main.html` now owns the eyebrow/title/description (plus an optional
`page_head_note`) and `aw_page_head.html` owns the actions slot; fifteen pages
were migrated with their existing action markup kept byte-for-byte, and only
`display_quiz.html` keeps its own admin head because it is the shared student
exam template. Exactly one `.aw-page-head-main` and one `<h1>` now render per
page (12-render browser spot check at 1440/390, zero console errors). The missing
"Job details" Arabic/English msgid used by the job detail card was added and both
catalogs rebuilt. Remaining review findings, recorded and not silently dropped:
~84 `aw-*` selectors are a reserved component inventory with no current
reference; the dead file components and the unrouted theme showcase were later
deleted, and shared question rows keep their Bootstrap student exam markup and
receive the admin skin through a conditional wrapper.

Admin selector decision and responsive pass (2026-11-09, product-approved):

- Every runtime admin hook was renamed off `ad-*`, including the interactive
  calendar. Tables keep only `aw-table`/`aw-table-wrap`; the promotion editor
  uses `aw-rules-table` with its legacy geometry restored; the calendar moved to
  `aw-calmg-*`/`aw-calendar-*`/`aw-legend-swatch` with a 422-line
  `body.aw-admin` parity block, updated `calendar_day_sheet.js` and no legacy
  fallback; `media_upload.js` emits `aw-card`/`aw-card-body`/`aw-btn`/
  `aw-row-actions`/`aw-alert`; `application_analytics.js` now reads
  `.aw-card-body` (its old selector silently missed); dead `.ad-tab` and
  `.ad-filters` fallbacks were removed, and generated preset ids became
  `aw-preset-*` while the saved-filter storage key stayed unchanged so existing
  presets survive.
- Calendar parity evidence: before/after screenshots pixel-identical at
  1440/1024/834/390, zero computed-style differences on 15 measured elements,
  DOM contract unchanged (ids 132, data-* 261, hx-* 218, names 26, state classes
  70); student calendar still emits only `sd-*`.
- Responsive matrix (2026-11-09): twelve widths - 1920x1080, 1470x900 (MacBook
  Air 13-inch M4 default scaled), 1440x900, 1366x768, 1280x800, 1180x820,
  1024x768, 834x1112, 768x1024, 430x932, 390x844 and 360x800 - across eleven
  representative pages plus the calendar. Every combination returned 200 with
  one shell and one `#content`, zero body overflow and zero console/page errors;
  rail/sidebar show above 991.98px, mobile toggle at or below it; the calendar
  keeps seven columns at every width with working day sheet and modals.
  Screenshots in `C:\Users\George\AppData\Local\Temp\lms_shots_responsive` and
  `lms_cal_shots_after`.
- Defect found and fixed by the matrix: the mobile menu toggle was visible on
  desktop because the later `.aw-icon-btn` display rule beat the base
  `.aw-mobile-toggle` rule at equal specificity. It is now scoped through
  `body.aw-admin .aw-topbar .aw-mobile-toggle` for both hide and mobile-show.
- Environment limit: the R2 stats endpoint waits about 78 seconds when the
  worker/Redis stack is absent, so the provider page is excluded from the timed
  responsive matrix and needs a worker-backed run.

Telegram support chat spec decisions confirmed by the product owner
(2026-11-09, recorded in the spec document): unique active normal full-year
enrollment for the current level with "Level not assigned" otherwise; archive
Telegram-origin media only within the provider `getFile` range; 5-minute voice
cap with OGG/Opus, MP3 and M4A, labeled audio/document fallback and no
transcoding; one media item per reply; old-bot digest copies become uneditable
after rotation; failed admin digests get automatic bounded retry with at most
three attempts and lost-response idempotency handling.

UI leftovers closed (2026-11-09): the shared `questions_rendering.html` now adds
`.aw-quiz-editor` only when `is_student` is false, and a scoped
`body.aw-admin .aw-quiz-editor` block skins its cards, buttons and form controls
without touching the student exam markup; the admin quiz editor, import result
and submission detail therefore match the workspace. Scanner preview alerts are
`aw-alert` with a new success tone, and the lesson media part cards (server
template and `lessonForm.js` generator) use `aw-part-card`/`aw-part-head`/
`aw-part-body`/`aw-part-foot` with `aw-select`/`aw-input`/`aw-pill`/`aw-btn`.
`display_quiz` info alerts only render for student upcoming/ready states, so no
admin change was needed. Verified: 116/116 templates compile, quiz/lesson/
scanner route sweep passes, `manage.py check` clean, encoding clean, and a
browser check at 1440/390 shows one `.aw-quiz-editor`, zero overflow and zero
console errors.

Lesson delete affordance (2026-11-09): the editor danger zone now renders a
Delete lesson button for `caps.delete_content` (admin-only, the same
`can_delete_content` gate the `DeleteLesson` view enforces). Because HTML forbids
nested forms, the button uses `form="aw-lesson-delete-form"` and the actual POST
form is declared after the editor form with CSRF and a form-level confirm
message. `lesson_id` was added to the editor context. Verified with a temporary
lesson: admin sees the button and POST deletes (302, row gone); staff sees
nothing and a direct POST returns 403; CSRF is enforced by the form. The stale
comment claiming a row action existed was replaced.

Dead-code removal (2026-11-09, product instruction: no dead code or legacy):

- Deleted the unused file components, the unrouted `theme_showcase` directory,
  `admin_proposal.html` and `admin_proposal.css` after a repo-wide reference scan
  showed only self-references.
- CSS was pruned from evidence, not guesswork: a live-class audit combined
  rendered HTML from 39 admin routes, all template source classes, JS-emitted
  classes and the dynamic families (`aw-avatar--*`, `aw-btn--*`, `aw-pill--*`,
  `aw-kpi--*`). It removed 78 unreferenced `aw-*` selectors (118 selector
  fragments, 114 rules) from `admin_workspace.css` and every `ad-*` selector
  (672 fragments, 595 rules) from `app_admin.css`, using a tinycss2 parse/rewrite
  with brace validation. Sizes are now 103,442 and 54,624 bytes; both parse with
  zero errors and zero remaining `ad-*` selectors. The saved-filter storage key
  and every live JS hook (`tg-*`, `.row-checkbox`, `.video-container`,
  `.card-title`, `.file-icon`, `data-*` contracts) were kept.
- Visual regression after pruning: 1470x900 Today/Students/Reports/Telegram inbox
  and 1440x900 quiz editor/calendar/lesson editor render with zero console errors
  and zero body overflow. Reports, inbox and quiz editor are pixel-identical;
  Today differs only in its clock label and Students only in last-login
  timestamps (both changed by the test session), while the calendar crop differs
  only because the temporary fixture chips were deleted.

Telegram support chat U1-U5 (2026-11-09, migration 0112):

- U1 level projection: `telegram.support.current_level_labels()` resolves the
  unique active, normal, full-year enrollment in the active academic year with
  one bounded query and returns the translated "Level not assigned" label for
  missing or ambiguous cases. The label feeds the conversation list page, the
  thread header and the digest snapshot line ("Level: ...").
- U2 canonical answer state: `TelegramMessage` gained `answered_by`,
  `answered_at`, `answer_reply`, frozen `covered_message_ids` on queued replies,
  the sending `bot_config`, an `answered_at` index and an `unavailable`
  resolution choice. Migration `0112_telegram_support_answer_state` applied to
  the disposable database.
- U3 shared prefix operation: `telegram/support.py` freezes the still-unanswered
  inbound IDs from conversation start through the selected source, rejects
  overlapping queued/sending coverage, and writes attribution only after the
  student send is confirmed. Replies to an already answered message are
  follow-ups with empty coverage.
- U4 website target: the reply form and view now carry the internal
  `TelegramMessage` primary key; `_valid_reply_target` validates the inbound row
  inside the conversation. Telegram adapters keep chat ID plus provider reply
  target. Provider IDs are never used as the website target.
- U5 digest and refresh: every delivered admin digest for every covered source is
  kept and annotated with "Answered by @admin"; the 48-hour edit gate was
  removed for edits (deletion keeps its own limit), the sending bot config is
  recorded and a rotated bot marks the copy `unavailable`. The message poll
  carries a state token and returns an OOB replacement when answer or digest
  state changed without new message IDs. Deleting a sent answer clears only its
  attributed inbound rows and reopens the conversation only when the latest
  inbound is pending again.
- Verification: fake-bot ORM probe passed all cases - level projection, covered
  IDs [1,2], inbound 1 and 2 answered by the admin, inbound 3 pending,
  conversation stays open/claimed, both digests edited with the original text
  plus the answer line, third digest untouched. HTTP probe passed level in
  header, answered and pending badges, PK reply target, stale-state OOB refresh
  and unchanged-state append-only. `manage.py check`, migration check, template
  compile, route sweep and encoding audit are clean.
- U6 media/voice completed: the validator accepts OGG/Opus, MP3, M4A and WebM
  with magic-byte checks, 20 MB and a server-side ffprobe limit of 300 seconds;
  `TelegramAttachment.duration_seconds` was added (migration 0113 applied).
  Web uploads and Telegram-origin media both use one shared reply operation;
  Telegram admin media requires a digest reply, enforces the document MIME
  allowlist and the 20 MB `getFile` archive cap, archives a private R2 copy and
  preserves the provider file ID server-side. The composer gained a fake-tested
  recorder (start/stop/preview/use/discard, no auto-send, track and object-URL
  cleanup) and the raw file input is visually hidden behind the styled label.
  Review document: docs/current/TELEGRAM_SUPPORT_MEDIA_REVIEW_2026-11-09.md.
- Verification: fake-bot media probe passed all nine cases (voice classification,
  301s rejection, web voice send/content type/attribution, provider voice relay,
  archived attachment, Telegram admin media); Playwright with a fake microphone
  passed start/stop/preview/attach with zero console errors; template compile,
  `manage.py check`, migration check, Telegram route sweep and encoding audit are
  clean.
- Next Telegram work: live provider acceptance with a non-production bot and the
  Redis/worker stack (digest edits beyond 48 hours, delivery/unknown paths,
  Telegram-origin large media, native mobile push).

Infrastructure follow-up (2026-11-09): support audio duration validation runs
synchronously in the web process, so the runtime image now inherits the media
stage (FFmpeg/FFprobe) and `FFPROBE_BINARY` is configurable in settings,
`.env.example`, `.env` and both Compose environment blocks. The native FFmpeg
media pipeline remains limited to the media queue. `docker build --check` passes
and `docker compose ... config` resolves the new variable in every app service.

Email flow hardening (2026-11-09): all four transactional emails
(`password_reset`, `application_received`, `application_activated`,
`application_declined`) now extend one RTL-aware table template with the
workspace palette (cream, teal, gold, paper), the institute emblem, a centered
CTA and a copied-link fallback. Subjects are Arabic for reset, received,
approved and not-approved. `tasks.py` now reads the canonical site domain from
settings instead of a second module-level env read, and application status
emails share one broker-failure fallback in `utils/email.py` so a missing
Celery broker cannot break signup or a decision. Validation used the locmem
backend and eager Celery: password reset returned 302 and sent one Arabic email;
received/approved/not-approved tasks each sent Arabic RTL HTML; HTTP decline and
activate decisions returned 302, changed status and sent the matching Arabic
email; English renders LTR with no Arabic leakage; a forced broker failure still
delivered the application email synchronously. Remaining email gate: `.env`
still has empty `DJANGO_EMAIL_HOST_USER`/`DJANGO_EMAIL_HOST_PASSWORD`, and the
`noreply@bibleinstitute.edu` sender must be an authenticated/verified mailbox or
a transactional provider must be configured; the plain-text alternative is still
empty and should be added before release.

Known follow-ups (not done): provider acceptance (R2 stats/search, Telegram
delivery, media workers) remains
separate from template verification and needs the Redis/worker stack; the
app_admin.css file still carries live shared rules
for Telegram/media/progress components even though all its legacy `ad-*`
selectors were removed.

Decisions recorded: background jobs and notification history live under the
Overview domain; the top-bar bell links to the existing authorized admin queue
pages without an invented unread badge; the sidebar footer shows the actor chip
instead of a fabricated active-year query; page-level invalid forms keep the
existing 200 + bound-error re-render because the vendored HTMX runtime does not
swap non-2xx responses; the students tab strip covers the application decision
only, while account state is an explicit, separately labelled filter.

Open/blocked decisions (not implemented, not silently removed): student
directory scope (student-only versus all-user management); level/enrollment
columns and filters need a canonical current-level definition across years;
student bulk Message/Export-selected/Deactivate need named channel, selection,
limits and authorization contracts; Today missing-scan and receipt-coverage
metrics need numerator/denominator and eligibility decisions; Today system
health and the attendance-week chart have no bounded existing source; Courses
"Updated" needs an update-time definition; lesson media/duration and quiz
average projections remain open; Student 360 standing/year-progress/attendance
metric definitions remain open; the admin bell attention inbox needs its own
persistence contract.

Remaining environment limits: no browser harness is configured in this session,
so visual EN/AR/RTL screenshots and console-error acceptance remain open; no
staff fixture exists in the disposable database (staff path verified through the
capability functions and settings review); Redis is not running, so the
dashboard cache reports read/write failures and warm-cache evidence used LocMem;
the host runtime is Django 5.2.17 while the project target is 5.1.4.

Next safe step: convert the remaining admin pages in dependency order, starting
with the People pages (applications, historical intake, signup settings,
student 360/editor), then Curriculum/Media/Reports/Engagement, keeping the same
shell, primitives and response contracts and reviewing each complete diff.


Latest completed user instruction (8 November 2026): implement B01-B05/F01 from
[the dated bug register](docs/pending/QUIZ_LESSON_NOTIFICATION_BUGS_2026-11-06.md),
then conduct a full pre-merge review. Feature and follow-up changes are applied
in the current working tree; they are not deployed or merged. The R2 dashboard
`load_stats` reference is removed, exception tracebacks are preserved in the
privacy-safe request log, and quiz duration has no 480-minute product cap;
negative durations are rejected. Migration 0108 carries the final duration
field type.
The pre-existing dirty notification/jobs and remediation work described below
was preserved; this turn stayed within the latest quiz/lesson/notification and
attendance request. No stage, commit, push, production migration or real SQLite
operation was performed.

Active follow-up (8 November 2026): user requested top-of-file imports,
post-send notification withdrawal across Telegram, the student inbox and mobile
push, plus a corrected and documented Telegram support lifecycle for multiple
admins. The implementation and source review are complete. Preserve both
repositories' pre-existing dirty state; no staging or commit was performed.

Additional 8 November follow-up: mobile push language is now stored per
MobilePushDevice using the existing normalized Accept-Language request. The
mobile app restores its per-device language before registration and repeats
registration after language changes (including tokenless opt-outs). The push
worker selects the matching Arabic/English notification snapshot with a
non-empty fallback. Migration 0111 defaults existing device rows to Arabic.
Migrations 0109-0111 were applied to the verified disposable PostgreSQL database
after a fresh custom-format backup; production and SQLite were not touched.

Current pre-merge repair (8 November 2026): enforce the current effective quiz
window before reusing a saved attempt; keep upcoming/closed status authoritative
over any snapshot; serialize per-student schedule changes with attempt creation;
remove duplicate web/mobile snapshot reads and the unused quiz-open helper; make
the quiz-status API error generic; and verify the deadline index plan. Preserve
the existing staged changes; no database migration is run outside the verified
disposable PostgreSQL database.

Follow-up backend files: `management_system/models.py`, `mobile_api.py`,
`mobile_urls.py`, `mobile_push.py`, `notification_history.py`,
`notification_redelivery.py`, `notification_views.py`, `job_monitor.py`,
`job_monitor_views.py`, `telegram/broadcasts.py`, `telegram/notifications.py`,
`telegram/support.py`, `telegram_views.py`, `urls.py`, and the notification,
Telegram chat/conversation/message and broadcast templates. New files:
`management_system/mobile_notifications.py`,
`management_system/notification_withdrawal.py`,
`management_system/telegram/deletion.py`, and migrations 0109, 0110 and 0111.
Mobile files: `app/_layout.tsx`, `src/notifications/register.ts`,
`docs/MOBILE_DEVELOPER_CONTRACT.md`,
`docs/MOBILE_AUTHENTICATION_EXTENSION_PLAN.md`,
`docs/STUDENT_WEB_MOBILE_EXTRACTION_PLAN.md`, and
`docs/REACT_NATIVE_EXPO_ARCHITECTURE_AND_BEST_PRACTICES.md`. Handoff docs:
`project_memory.md`, this file, the dated bug register and
`docs/current/TELEGRAM_SUPPORT_LIFECYCLE.md`. No unrelated staged or dirty
files were staged, reset or overwritten.

Implemented contract: upcoming quizzes show a distinct scheduled state and
withhold questions until explicit start; lesson `opening_date` is optional and
gates student/media/Telegram access; quiz duration defaults to 60 minutes and
each attempt has a persisted deadline clamped to its effective close; human
dates include weekday and timezone where appropriate; notification text
distinguishes available window from allowed attempt time; student attendance
counts paired `SCAN` entrance/exit records regardless of timing under the
existing `attendance_scanned` progress key and omits score, while authorized
management reporting shows both count and score.

Preflight/runtime boundary: host Django 5.2.17 while the project target is
5.1.4. Disposable PostgreSQL is `lms-disposable-pg`, localhost:55432, database
`lms_remediation_20261105`, PostgreSQL 18.6. The pre-feature dump is
`C:\Users\George\AppData\Local\Temp\lms-before-quiz-lesson-attendance-20261107.dump`
(430056 bytes; the earlier preflight listed 842 entries). Only this disposable
PostgreSQL database received migrations 0107 and 0108. The follow-up dump
before 0108 is `C:\Users\George\AppData\Local\Temp\lms-before-duration-cap-20261108.dump`
(430553 bytes; SHA256 `12E79F977432B33B89649F886E0F272A388EB305412607C5FFB7CC3A5E35E7D1`).
The original SQLite path and production were not touched.

## Latest completed unit

8 November 2026: implemented the quiz/lesson/notification and attendance fixes
from B01-B05/F01. New quiz attempts start only through the explicit web/mobile
start action; question payloads are withheld before opening; deadlines are
server-owned and capped at effective closing. Future lessons are gated at
student detail, media/progress and Telegram access. Human-readable date strings,
clearer event copy, and student scanned-day count versus authorized score are
wired through their owning callers.

Exact source/document files changed for this unit:

- `management_system/academic_copy.py`, `attendance_matrix.py`,
  `course_content.py`, `forms.py`, `media_delivery.py`, `mobile_api.py`,
  `mobile_urls.py`, `models.py`, `progress_activity.py`, `quiz_attempts.py`,
  `quiz_import.py`, `report_export.py`, `student_notifications.py`,
  `telegram/navigation.py`, `telegram/notifications.py`, `telegram_tasks.py`,
  `urls.py`, `views.py`.
- `management_system/static/css/app.css`, `static/css/app_student.css`,
  `static/js/alpine/components/quizJsonImport.js`,
  `static/js/alpine/components/tableManager.js`.
- `management_system/templates/content_detail.html`, `course_detail.html`,
  `display_quiz.html`, `lesson_form.html`,
  `partials/attendance_management_content.html`,
  `partials/quiz_import_result.html`, `partials/report_dashboard_content.html`,
  `profile.html`, `quiz_form.html`.
- `management_system/utils/attendance.py`, `helpers.py`, `lesson_access.py`,
  `quiz_access.py`, `reports.py`, `student_data.py`, `timezones.py`.
- `locale/ar/LC_MESSAGES/django.po`, `django.mo`,
  `locale/en/LC_MESSAGES/django.po`, `django.mo`, `project_memory.md`,
  `IMPLEMENTATION_MEMORY.md`, and the local ignored report
  `docs/pending/QUIZ_LESSON_NOTIFICATION_BUGS_2026-11-06.md`.
- New migration: `management_system/migrations/0107_scheduled_lessons_timed_quiz_attempts.py`.

Migration 0107 adds optional lesson `opening_date`, 60-minute-default quiz
`duration_minutes`, quiz publication event versioning, and required snapshot
`deadline_at`. Legacy snapshots backfill to their effective close because
historical start times are unknown. It was applied only to disposable
PostgreSQL `lms_remediation_20261105`; there were no snapshots to backfill.
Pre/post counts match: User 8, Level 5, AcademicYear 2, AcademicYearLevel 4,
CourseOffering 1, Enrollment 2, Lesson 3, Quiz 0, Question 0, Submission 0,
Grade 0, AttendanceRecord 0, LectureProgress 0, QuizAnswerSnapshot 0. No null
snapshot deadlines remain.

Validation:

- Target image `lms-fix15-runtime` (Django 5.1.4) with a read-only workspace
  mount: `manage.py check`, `manage.py makemigrations --check --dry-run`, and
  rollback-only PostgreSQL ORM/HTTP probes passed. The 0108 duration probe
  accepted 0 and 481, rejected negative input and rolled back its fixtures.
- Synthetic HTTP 500 probe confirmed `NameError` traceback logging with a
  sanitized request path. Post-0108 schema is PostgreSQL `integer` with a
  non-negative database constraint; migration leaf is 0108. Academic row
  counts before and after 0108 match.
- Focused probe covered upcoming web detail with no question text, no pre-open
  start (403), future lesson DTO/detail denial, strict opening/closing edges,
  22:30 start capped at 23:00, accepted-answer finalization, date formatting,
  bilingual snapshots/Telegram wording, and student overview/report/matrix
  attendance output.
- Host Django 5.2.17: `manage.py check`, migration drift, `compilemessages -l
  ar -l en`, `audit_text_encoding` (649 files), `compileall` and `git diff
  --check` passed. No unit-test module or dependency was added.

Remaining proof after the pre-merge re-review: authenticated mobile API start,
real browser/device and provider rendering, concurrent attempts and worker
restarts, broader edge cases, CSV/XLSX and role-gate parity, and release-volume
measurements on persistent application tables. The synthetic PostgreSQL plan
and disposable migration rehearsal are recorded below. The updated mobile
client passed `node node_modules/typescript/bin/tsc --noEmit` in the sibling
checkout. The project target is Django 5.1.4; the target-image system checks and
migrations are now verified. No production deployment or production migration
was run.

Pre-merge re-review (8 November 2026): fixed the quiz schedule/start race by
locking Quiz only during attempt creation and exceptional-window updates. Save,
submit and reconciliation lock each student's snapshot, and reconciliation
locks due snapshots before reading answers; this avoids serializing every
student's autosave on one quiz row. Migration 0110 adds the
deadline index, and the synthetic PostgreSQL plan uses it for both the periodic
due-quiz scan and the per-quiz locked batch. Lesson redelivery rechecks current
availability. Quiz detail reuses its loaded snapshot, and the unused
`is_quiz_open`, `student_quiz_attempt_status` and `build_report_data` wrappers
are removed. `api_quiz_status` logs exception details and returns a generic
client error while preserving authenticated access denials as HTTP 403.
Telegram retry and stale-send recovery lock the notification before its
delivery row, so withdrawal wins over a later retry; stale support replies
become `UNKNOWN` rather than being resent. Recovery batches are bounded.
The unused `views.py` import found by review was removed. No nested imports were
found in 252 first-party Python files.

The 8 November disposable PostgreSQL backup is
`C:\Users\George\AppData\Local\Temp\lms-before-0110-0111-review-20261108.dump`
(430581 bytes; SHA256
`DD383BDDE38AE43FD7F6713C67111A0EB7BF37A331E998DBE79924103CD5A6C2`).
Migrations 0109-0111 applied successfully on `lms_remediation_20261105` with
the target Django 5.1.4 runtime; migration leaf is 0111. Core pre/post counts
match: User 8, Level 5, AcademicYear 2, AcademicYearLevel 4, CourseOffering 1,
Enrollment 2, Lesson 3, Quiz 0, Question 0, Submission 0, Grade 0,
AttendanceRecord 0, LectureProgress 0, QuizAnswerSnapshot 0, TelegramMessage
0, TelegramBroadcastRecipient 5, and TelegramNotificationDelivery 11. The new
broadcast message table has 0 rows (no old provider message IDs to backfill),
both existing mobile device rows have the new Arabic default, no constraints
are unvalidated, and the deadline index exists.

Target-image `manage.py check --tag models --tag urls --tag security`,
`makemigrations --check --dry-run`, bilingual `msgfmt --check`,
`audit_text_encoding` (659 files), focused Ruff `F401`/`F821`, full first-party
AST/import scan, and `git diff --check HEAD` pass. Repo-wide Ruff still finds
three pre-existing unused imports in historical migrations 0035, 0044 and 0095;
those files were not changed. Synthetic PostgreSQL 18.6 plans with 1,000,000
snapshot rows and 10,000 overdue rows use the deadline index: the 50-quiz scan
returned in 2.4 ms, and the per-quiz locked 100-row batch in 0.7 ms. These are
temporary-table measurements, not a production-table benchmark. Django checks
used a read-only source mount, an internal Docker network, dummy secrets and a
disabled SQLite source path.

Source review found no remaining quiz/attendance authorization blocker. The
staff/admin report gate hides score from students and moderators; paired scans
count regardless of timing. One non-blocking reliability gap remains: failed
Telegram support digests stay visible in the web conversation history but have
no automatic or operator retry action. Automatic retries can duplicate a
message when Telegram accepted it but the response was lost; decide whether to
add a safe manual retry workflow before relying on these alerts. Outbound
Telegram rows also do not retain the sending bot identity, so deletion uses the
active bot and may fail after switching to a different bot. Real two-admin and
browser concurrency, Telegram and mobile-provider behavior, native push
dismissal, and release-volume measurements remain unverified. No live provider
calls, production migration, stage, commit, push or deployment occurred.

## Previous completed unit

The first requested modification is complete: the admin jobs and notification
history page is present with search/filtering, polling pause/resume, readable
saved content and delivery outcomes, and failed-only retry/resend-all actions.
This follow-up review also corrected storage path/row lock order for private
application uploads, included academic payment receipts in reference rewrites,
and protected gallery/APK references, gallery jobs and APK publish/delete paths
from generic storage rename/delete races. These follow-up code changes passed
static checks and the PostgreSQL, worker and focused HTTP checks recorded below.
Runtime used a secret-free source copy in an OS-isolated, loopback-only
container namespace; no host source or SQLite file was mounted into
target-controlled processes.

## Earlier completed unit

7 November 2026: completed the requested saved notification history and
redelivery workflow on top of the background jobs page. Admins can search/filter
all saved inbox notifications and inspect original Arabic/English text, action,
read state and paginated per-channel outcomes. Exact Telegram text is shown when
captured; old attempts without a snapshot are identified. Expo ticketed pushes
remain active until receipt confirmation. Provider IDs, tokens and chat IDs are
not rendered. A browser-level toggle pauses/resumes polling, remembers the
choice across reloads, and resumes immediately. Toggle strings are rendered by
Django so the unprefixed JavaScript catalog cannot show Arabic in an explicit
English session.

Admins can enqueue failed-only retry or resend-all from saved notification and
Telegram broadcast details. Failed-only retries only latest failed channel
attempts and creates no duplicate inbox entry; resend-all creates new visible
inbox copies. Prior attempts and broadcast root outcomes remain unchanged.
Pending/recovered channels are not duplicated. Audience selection is frozen;
actor, source and recipient eligibility are checked again before dispatch.
Provider receipt/send outcomes, not queue acceptance, set terminal progress.
Normal system-notification materialization and existing provider workers remain
the owners of the ordinary send path.

Feature owners: management_system/job_monitor.py, job_monitor_views.py,
notification_history.py, notification_redelivery.py, admin_operation_tasks.py,
admin_operations.py, models.py, mobile_push.py, telegram/broadcasts.py,
telegram/notifications.py and telegram_views.py. UI/routes: urls.py,
templates/background_jobs.html, partials/background_jobs_content.html,
partials/background_job_detail_content.html, partials/notification_delivery_content.html,
partials/background_polling_toggle.html, telegram_broadcast_detail.html and
static/js/alpine/components/backgroundJobsMonitor.js. Updated project_memory.md,
docs/current/README.md and [feature handoff](docs/current/BACKGROUND_JOBS_PAGE.md).

Migrations 0104-0106 store notification redelivery lineage/visibility and
provider text snapshots, broadcast skipped progress and broadcast-root lineage.
They were applied/rehearsed only on disposable PostgreSQL after the pre-0106
backup at `C:\Users\George\AppData\Local\Temp\lms-notification-redelivery-0106.dump`
(SHA256 `598F65AF3F22BBCC4DED1D5F77187B134CA24279E771F86AAF91F3F64D15923C`).
All 62 management-model counts matched during migration rehearsal. Final ORM,
worker and HTTP acceptance used target Django 5.1.4 and fake Expo/Telegram
adapters: failed-only selection, no recovered-channel duplicates, resend-all
inbox and provider completion, ticket-to-receipt transition, source-ineligibility
skips for both channels, broadcast lineage, role denial, CSRF, escaping and
private credential exclusion passed. No real provider sends were made.

Live local WSGI/Chromium passed notification search/detail at EN/AR, 390/1440,
saved message content, responsive overflow, translated toggle labels, paused
state across reload, no status polling while paused and an immediate status
request on resume for both notification-job and Telegram-broadcast details.
Fixture cleanup returned every management-model count to its saved baseline.
Synthetic Telegram dispatch messages from earlier probe runs were identified by
task name and argument and removed from the dedicated validation Redis queue;
the final acceptance probe patches broadcast wake dispatch and leaves no test
messages in that queue.

Target checks after the feature: Django system check clean; no migration drift;
encoding audit passed 646 text files; backgroundJobsMonitor.js passes Node
syntax check; catalog msgfmt validation and scratch Python compilation pass;
locale diff-check is clean. Repository-wide diff-check is being closed with the
final integrated review. No application test module or dependency was added.
Disposable database leaf is 0106. No production migrations, provider calls,
staging, commit, push or deployment.

Scratch verification: TEMP/lms_notification_acceptance.py,
lms_notification_browser.py and lms_notification_acceptance_manifest.json.
See [background jobs and notification history](docs/current/BACKGROUND_JOBS_PAGE.md)
for route, behavior, migration and verification details.

## Previous completed unit

6 November 2026: added the requested searchable/filterable background jobs page.
Admin navigation -> Background jobs (`/ar/dashboard/background-jobs/`, English
counterpart `/en/dashboard/background-jobs/`). Admin-only unified view of
management, quiz, media, gallery, announcement and confirmed Telegram batches.
Search operation labels, requester, reference or target; filter category,
operation, status and inclusive Cairo-local dates. 25 rows per SQL page. Live
status polling reads visible IDs only, pauses hidden, backs off failures and
stops at terminal/teardown. Media stage, attachment and cleanup remain distinct.
The existing list/detail query, 10,000-history EXPLAIN and previous browser
verification are documented in the feature handoff and prior archived memory.

## Previous completed unit

6 November 2026: user requested "Fix all then review" for all 15 findings in the
[cleanup behavior review](docs/current/CLEANUP_REVIEW_2026-11-06.md).
All 15 are corrected and re-reviewed. The complete mapping, caller coverage,
changed contracts, evidence, commands and practical limits are in
[CLEANUP_FIXES_REVIEW_2026-11-06.md](docs/current/CLEANUP_FIXES_REVIEW_2026-11-06.md).

Comparison branch was fetched again after implementation:
`personal/codex/lms-expansion-aligned` remains
`68b41f8118b281d936acaf3902883e61842c970e`.
Target is `upgrade-clean-codebase`, HEAD `dce5a23`, plus the existing dirty tree.
No stage, commit, push, branch, deployment or production migration.

## Corrections and ownership

- `admin_operations.py`: savepoint-safe concurrent creation, frozen-input
  equivalence, current actor/role checks, atomic progress/lease fencing and one
  heartbeat per task. `admin_operation_tasks.py` owns heartbeat lifecycle and
  explicit storage-usage dispatch. `admin_operation_views.py` permits shared
  calculation status to the report-capable users who see it on the page.
- `academic_evaluation.py`: retry preserves actual overrides only; stale
  publication rolls back, current counters survive final cleanup; override
  exclusion locks the academic scope before checking active calculation.
- `academic_enrollment.py`: each promotion's effects/history/cursor share one
  transaction and stale progress raises. `academic_copy.py`: committed outcome
  replay returns success. Historical intake, legacy import, report/evaluation/
  quiz exports, attendance and storage executors recheck fresh active actors.
- `legacy_user_import.py`: duplicate content remains idempotent across display
  filenames/upload keys and a losing duplicate artifact is cleaned up.
- `storage_jobs.py`: incomplete folder rename restores originals, preserving
  changed/late source files; reference-update failure attempts restoration.
  STORAGE_USAGE_REFRESH owns bounded provider work and timestamped snapshots.
  `views.py` uses snapshot GET and CSRF-protected queued refresh POST, validates
  frozen promotion IDs/scope and exposes exact owner-bound promotion progress,
  including the owner's archived-source redirect. Copy/export conflicts surface
  validation errors to the operator.
- `admin.py`: UserAdmin retains both LMS admin and Django permission gates.
- `adminOperationStatus.js`, `r2Manager.js`, status/dashboard templates and every
  status include: terminal error/details, bounded rows, one initial fetch,
  serialized polling, hidden/focus/teardown, cached usage refresh and timestamp.
  Arabic/English django catalogs include the new translated messages.
- `deploy/blue_green_deploy.sh`: build/recreate admin-worker and require its
  Celery node to answer as an admin-queue consumer before traffic switch.
  `requirements.txt`: pin already-installed Kombu to 5.4.2, compatible with the
  existing Celery/Redis/tzdata pins. No new package was introduced.
- Three whitespace-only corrections in models.py, progress_activity.py and
  quizForm.js make diff checking clean. The project map, checklist, completion
  report, current index and review documents now agree on this disposition.

## Verification

- 7 November follow-up review: AST parse passes for seven changed Python modules;
  focused Ruff passes for storage coordination, application uploads, R2
  references, storage jobs, gallery views and portal views. Ruff's focused
  changed-view check reports only the known pre-existing E741 at line 1967 (`l`
  in the academic-year list).
  `git diff --check`, Node syntax for the jobs monitor, and `msgfmt --check
  --check-format` for English and Arabic catalogs pass.
- 7 November isolated runtime follow-up: hardened, fresh PostgreSQL 18 and Redis
  ran without host ports or Docker volumes. `manage.py check` passed,
  `makemigrations --check --dry-run` reported no changes, and the fresh database
  migrated through `management_system.0106_broadcast_redelivery_lineage`. One
  pre-existing warning occurred in migration 0009 for a naive quiz datetime;
  migration completed successfully. PostgreSQL probes passed Arabic escaped
  JSON reference rewriting and updates to lesson links, viewing sessions,
  application documents, academic payment receipts, gallery keys and APK keys.
  The folder rewrite changed 9 references in 18 SQL statements; a 501-lesson
  boundary probe changed 1,002 lesson/session references in 20 SQL statements
  (71 ms on the disposable database). Advisory-lock probes confirmed concurrent
  sibling writes and parent-folder exclusion of child writes. Admin job search,
  status and notification-history queryset projections each executed one SQL
  statement for the synthetic row. These timings/counts are fixture evidence,
  not a production-scale benchmark.
- Focused Django HTTP checks passed: anonymous redirect, staff 403, admin page
  access, staff resend denial and GET method rejection, job search/filter,
  status-batch progress and malformed selection rejection, failed-delivery
  history filtering, and notification detail with bilingual saved content,
  provider failure, exact Telegram snapshot and retry/resend controls. Arabic
  remained the default despite an English Accept-Language header; an explicit
  English URL worked. A prefork Celery worker on the admin queue at concurrency
  1/prefetch 1 consumed the provider-free
  `refresh_notification_redeliveries` task and changed a synthetic ledger row
  from waiting to completed in PostgreSQL. No provider credentials or external
  network were available. Cloudflare R2/provider operations and browser checks
  were not repeated in this follow-up. The Django Test Client query wrapper did
  not capture request-thread statements; direct queryset capture supplied the
  one-statement projection counts above.

- Counterexamples now pass on real PostgreSQL and target Django 5.1.4: competing
  inserts, stale formula writes, stale/live canonical promotion, revoked actors,
  recovery status/override rules, conflicting inputs, Django grants, copy replay.
- Target-runtime HTTP/storage checks pass: changed and late source rollback,
  normal rename/reference rewrite, snapshot-only GET, real queued task dispatch,
  private sample filtering/prefix denial, CSRF, exact promotion redirect/card,
  archived owner-bound redirect and shared staff calculation polling.
- Linux prefork Celery/Redis kill/recover: 2,000 synthetic students, 20 offerings,
  42,000 results. Killed after 2,000 units; final progress/row count 42,000;
  ordinary statuses recomputed, one actual override preserved, completed
  duplicate delivery has no effect. Fixture records and unique container removed.
- Real 120-second heartbeat extends during a 42-second no-progress phase and
  denies duplicate takeover. Its temporary job/account are removed.
- Chromium: EN/AR at 390/1440, terminal details/one fetch/stop, serialized calls,
  hidden/focus/teardown; usage miss -> POST -> poll -> snapshot -> stop.
- Updated 48-group sweep passes. Four scratch probes were adjusted from their
  superseded idempotency/grant/provider expectations; originals retained.
- Query capture: progress/actor/token fence = one UPDATE; snapshot read = one
  SELECT without provider work. EXPLAIN inspected; small job table selects
  sequential scans. This is not a fresh 10,000-student benchmark.
- Local runtime image `lms-fix15-runtime` builds with the exact repository
  requirements via a scratch offline-wheel transport and passes pip check.
  Bash deployment syntax passes. No fleet deployment was attempted.
- Final target check passes, no migration drift; encoding audit passes 632
  files, 336 local links resolve, catalogs/integrity/lint pass, and diff check
  is clean. Terminal errors survive network failure; unexpected worker errors
  retain the frozen job language. Final check log: lms_fix15_final_checks.log.

Scripts/logs are under `C:\Users\George\AppData\Local\Temp\` with prefix
`lms_fix15_`; the report names each. They are focused disposable verification
artifacts, not application test modules. No replacement unit suite was added.

## Database and data safety

Disposable PostgreSQL 18.6: `lms-disposable-pg`, 127.0.0.1:55432,
`lms_remediation_20261105`, applied leaf 0103. Redis: `lms-validation-redis`,
56379; worker validation used isolated broker/cache DBs and unique queues.
No schema changes/migrations/data transformations were needed. Most fixtures
rolled back; committed race/worker/heartbeat fixtures were explicitly removed.
PostgreSQL sequences may advance. No production/provider mutation.

The 7 November storage and notification runtime follow-up used separate fresh
PostgreSQL/Redis containers with tmpfs only, no mounts or published ports, and
read-only, capability-dropped, resource-limited application/worker containers.
All migrations through 0106 applied to the fresh database. Only synthetic
validation rows were created; the database, broker and fixtures were destroyed
with the disposable containers. No real SQLite path, production database or
provider was accessed. The secret-free source copy remains at
`C:\Users\George\AppData\Local\Temp\lms-review-sandbox-0e54010f19c44eaab35083b6abf41597`:
its resolved path was verified inside Temp, but automatic approval review
rejected recursive removal. The copy contains no `.env`, `.compose.env` or
SQLite database.

The real SQLite file was previously deleted at explicit user instruction after
verified backup at
`C:\Users\George\AppData\Local\Temp\lms-sqlite-backup-remediation-20261105.sqlite3`,
md5 `1d2147563fc15fad19b4cf2f0d78e275`. This task did not access or recreate it.
Untracked 0102/0103 and unrelated dirty work remain preserved.

## Preserved policies and deferred work

Arabic default with explicit per-device English; session renewal at most daily;
trusted Cloudflare/direct ingress only; canonical academic/access rules;
promotion per student, intake per row, legacy CSV/copy all-or-nothing; private
storage/artifacts, bounded jobs and promoted-history immutability remain policy.
Do not reintroduce unit suites or add dependencies. Keep all 16 design references.

The [quiz/lesson/notification register](docs/pending/QUIZ_LESSON_NOTIFICATION_BUGS_2026-11-06.md)
(B01-B05/F01, including attendance count-versus-score and timing decisions) is
implemented in the current working tree. Native-client, provider, broader
browser/concurrency and performance evidence remains open as described above.
The separate [admin workspace plan review](docs/pending/ADMIN_WORKSPACE_PORT_PLAN_REVIEW.md)
is addressed by plan revision 1.3 as documentation only; runtime implementation
remains pending. Its prior findings are preserved as dated review evidence.

## Next safe step and limits

The in-app support chat and migration 0124 are in the current working tree,
applied only to a disposable PostgreSQL database and verified there; the
worktree remains unstaged. The next safe step is an admin-side walk of a
website-originated conversation on the real staging stack (real R2 bucket, real
VAPID keys, live Telegram bot) to confirm storage, push delivery and
cross-surface ordering with production-like configuration.

The quiz/lesson/notification fixes and migration 0107 are in the current working
tree, unapplied outside the disposable database; the worktree remains unstaged.
Before release, update/verify the separate native client, run browser/device and
provider checks, test concurrency/recovery and export/authorization parity, and
measure PostgreSQL plans at representative scale. Production rollout remains
separate: verify environment, secrets/ingress, backup/import reconciliation,
worker capacity and the migration plan before any authorized release.
