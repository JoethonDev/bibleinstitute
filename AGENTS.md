# Bible Institute LMS - Implementer Rules

This file is mandatory for every coding-agent session and every delegated
subagent. Read it completely before inspecting or changing code. Then read
the compact `IMPLEMENTATION_MEMORY.md`, [project_memory.md](project_memory.md)
and [CODING_GUIDE.md](CODING_GUIDE.md).

## Authority and permanent standards

- The latest explicit user instruction controls task scope and supersedes
  conflicting older repository guidance. Do not expand an audit, plan, or
  documentation request into application changes.
- This file contains mandatory rules; `CODING_GUIDE.md` explains how to apply
  them to features and bug fixes. Both apply to new code and changed callers.
- `project_memory.md` is the structural model/ownership/reuse map. Consult the
  relevant entry before scanning broadly. Update it in place for architecture,
  models, entrypoints or shared contracts; never use it as a button/session log.
- The [approved remediation checklist](docs/current/REMEDIATION_CHECKLIST.md)
  controls the current cleanup tasks and their settled policies. Audit reports,
  historical plans, and memory are context, not proof of current behavior.
- These are implementation requirements, not claims that the existing app
  already complies. Inspect the owning source and verify the affected behavior.
- Keep one canonical rule. When a lasting policy changes, update these rules,
  the guide, affected documentation/callers, and the handoff together. Do not
  leave two contradictory instruction paths.

## Mission

Stabilize and launch the Django LMS on PostgreSQL without losing the real data
currently stored in SQLite. Correctness, data preservation, authorization, and
query performance are release requirements.

The implementation target is:

- Django 5.1.
- PostgreSQL in Docker for local launch and verification.
- Up to 10,000 students.
- Approximately 350 lessons and 100 quizzes per academic year across three
  levels.
- Arabic and English UI with existing RTL behavior.
- Existing Bootstrap, HTMX, Alpine, and Django template stack.

Do not add dependencies unless the user explicitly approves them.

## OpenCode orchestration

Provider/profile instructions in this section apply in the configured OpenCode
environment. Do not claim an unavailable model/provider is available or silently
substitute it. Other coding clients retain the same architecture, review, and
validation obligations and follow their applicable delegation instructions.

`Luna` is the primary-agent role name in this repository. It must use OpenAI
GPT-5.6 Luna through `openai/gpt-5.6-luna`; do not replace it with the
unsuffixed GPT-5.6 alias or an OpenCode-hosted model. Luna uses medium reasoning
by default. The user may select the `lms-luna-high` profile for high reasoning.

Luna owns architecture, task decomposition, integration, validation, fixes,
and final acceptance. Implementation is delegation-first:

1. Luna reads the relevant code and builds the exact context, architecture,
   input/output contract, acceptance criteria, dependencies, and write scope.
2. Luna must delegate easy, mechanical, repetitive, localized, or otherwise
   low-model-suitable implementation work to `deepseek-implementer`.
3. Luna should launch multiple DeepSeek workers when tasks are independent and
   have disjoint write scopes. Never assign two active workers the same file.
4. Luna continues useful non-overlapping architecture, review preparation, or
   integration work while delegated tasks run.
5. Luna inspects every complete diff, traces affected callers, integrates all
   units, fixes omissions, and performs end-to-end validation.

Luna directly implements only work that is tightly coupled, architecture
defining, security/data critical, on the immediate critical path, or impossible
to specify safely as a bounded contract. Small task size is not a reason for
Luna to implement it when DeepSeek can handle it correctly.

Use the default medium-reasoning Luna profile for normal work. Use the
high-reasoning profile when the task includes schema/data migration,
authorization, concurrency, recovery from failed migrations, ambiguous
cross-cutting behavior, or performance-sensitive architecture. Respect the
user's explicit medium/high selection.

DeepSeek V4 Flash must use OpenCode Go through
`opencode/deepseek-v4-flash` with its provider defaults. It is the default code
worker for suitable bounded tasks, not an architect, integrator, or final
reviewer.

Luna may delegate a bounded implementation task to DeepSeek only when all of
the following are true:

- The task has an explicit input/output contract.
- Its files and database effects are named.
- It does not require the subagent to invent architecture.
- It can be reviewed independently.
- It does not overlap another active task.

Each delegated prompt must include:

- **Objective:** the exact user-visible and internal outcome.
- **Current state:** the relevant existing behavior, defects, and already-applied
  work that must be preserved.
- **Architecture contract:** the models, relationships, shared functions,
  ownership boundaries, and source of truth the implementation must follow.
- **Allowed write scope:** exact files the worker may modify.
- **Forbidden scope:** files, behaviors, migrations, dependencies, and
  unrelated cleanup the worker must not touch.
- **Caller map:** relevant functions, views, forms, templates, routes, commands,
  and tests/checks that call or depend on the changed code.
- **Invariants:** data preservation, authorization, transaction, migration,
  PostgreSQL, performance, localization, and historical-access rules.
- **Implementation instructions:** exact changes expected in each allowed file,
  including inputs, outputs, errors, and edge cases.
- **Integration boundary:** what Luna or another worker will implement, so the
  worker does not duplicate or conflict with it.
- **Verification:** required runnable checks and expected results.
- **Handoff:** every changed file, decision, assumption, command/result, and
  unresolved risk.

The delegated context must be complete for the assigned slice, not a dump of
unrelated repository information. Luna must inspect the current code before
writing the contract; do not delegate based only on a plan document.

DeepSeek must perform a preflight before editing:

1. Read `AGENTS.md`, the compact `IMPLEMENTATION_MEMORY.md`, `project_memory.md`,
   `CODING_GUIDE.md`, the delegated contract, every allowed file, and the named
   callers. Use the map to locate the relevant ownership; do not rescan the
   entire app or archived session history for a bounded slice.
2. Confirm the requested change fits the allowed scope and does not contradict
   repository invariants or current code.
3. If required context is missing, the write scope overlaps another worker, or
   the contract is contradictory, return `BLOCKED` with the exact missing
   information and make no edits.
4. Otherwise implement only the contract. Do not fill architectural gaps with
   guesses.

Luna must not delegate final schema decisions, data-migration strategy,
authorization rules, shared access or promotion semantics, integration,
release validation, or acceptance of delegated changes.

After every delegated task, the primary agent must:

1. Inspect the complete diff, not only the subagent summary.
2. Trace all callers of changed shared code.
3. Check schema, data, authorization, and query implications.
4. Correct or reject incomplete work.
5. Run the relevant verification.
6. Update `IMPLEMENTATION_MEMORY.md`.

Delegated work is never considered complete before Luna review.

## Session protocol

At the start of every session:

1. Read `AGENTS.md`.
2. Read `IMPLEMENTATION_MEMORY.md`, `project_memory.md`, `CODING_GUIDE.md`, and
   the applicable approved task/checklist. Read only relevant focused references;
   do not ingest archived session history as startup context.
3. Run `git status --short`.
4. Inspect the relevant current code, migrations, views, templates, and callers.
5. Confirm the active database and applied migration state before schema work.
6. Update the memory file with the task currently being attempted.

At the end of every completed task, update `IMPLEMENTATION_MEMORY.md` with:

- What changed.
- Exact files and migrations.
- Decisions and invariants.
- Commands run and their results.
- Data counts or migration evidence when relevant.
- Known risks or unresolved work.
- The next safe step.

The compact implementation memory is the current handoff. Update its sections
in place; preserve superseded session evidence in the archive rather than
appending a diary. Keep architectural discovery in project_memory.md and
one-time task progress in the active checklist. Do not mark blocked work done
merely because its original plan was archived.

## Authority and scope

The implementer may modify any repository file required to complete the
approved task. All related callers, forms, views, templates, reports, admin
pages, permissions, migrations, commands, and documentation must be changed
together.

Do not change unrelated code for convenience. Do not perform broad formatting,
mass renaming, file moves, dependency upgrades, or speculative cleanup.

Before implementation, state the observed behavior, intended behavior, owning
rule, affected callers, authorization/transaction boundaries, data volume, and
smallest runnable verification. For a bug, reproduce it or identify the exact
source path; do not patch a hypothesis as if it were proven. Routine bounded
tasks need a compact contract, not a separate architecture document.

Storage/R2, authentication, public-site content, worker code, and unrelated UI
are outside normal scope. Touch them only when the approved feature directly
requires it, and record why in the memory file.

## No compatibility layer

This project is being refactored to one canonical architecture.

- Do not add legacy fallbacks.
- Do not dual-read or dual-write old and new schemas.
- Do not retain old fields, routes, helpers, or conditionals "just in case."
- Do not preserve obsolete behavior merely because an existing test expects it.
- Change every related caller in the same implementation phase, then remove the
  old path.

Temporary fields are allowed only inside a controlled data-migration sequence
and must have an explicit removal step.

## Architecture rules

- A business invariant must have one canonical implementation.
- Student access to offerings and content must be centralized and reused by
  every list, detail, stream, manifest, quiz, submission, progress, and API
  endpoint.
- Academic progress and promotion calculation must be centralized and reused by
  preview, pagination, filtering, saved results, exports, and promotion.
- Shared code stays inside `management_system/`.
- Prefer plain functions, focused queryset helpers, model constraints, and
  transactions.
- Extract shared logic when it has multiple callers or protects a critical
  invariant.
- Extract a complete rule or operation with explicit inputs/results. Do not
  extract arbitrary two-line forwarding functions, repeat the same workflow in
  web/mobile/jobs, or force different policies into a generic helper.
- Views and tasks adapt HTTP/delivery to shared domain functions. Do not invoke
  one HTTP view from another or construct a fake request to reuse business logic.
- Validate and normalize external input once into an explicit contract. Recheck
  authorization/current eligibility at independent or delayed trust boundaries.
- Preserve bound form values/errors on rejection. Catch expected exceptions at
  the boundary; do not swallow failure into a success-shaped response.
- Use timezone-aware/Cairo-local dates and callable time-dependent defaults.
- Before deleting code, check dynamic dispatch, task registration, template/JS
  references, migration imports and public asset URLs. Empty literal search is
  not sufficient evidence that a path is dead.
- Do not create repositories, interfaces, factories, service hierarchies,
  generic engines, or one-use abstractions.
- Do not use Django signals for enrollment, access, formula calculation,
  result-saving, or promotion workflows.
- Use named enums/constants instead of repeating status strings.
- Add concise type hints to shared business functions.
- Add docstrings only where a rule is not obvious from the code.
- Keep `views.py` intact for this urgent launch except for extracting shared
  academic-domain logic. Do not perform a broad views-module split.

Before changing a shared function, search and inspect every caller. Fix the
root rule once and route all callers through it.

## PostgreSQL and performance

PostgreSQL is authoritative. SQLite is a real-data source for migration, not
the production behavior reference.

Required rules:

- Every admin table must use database-backed pagination.
- Never materialize an unbounded queryset.
- Never repeat read/fact lookups per student or result. Load facts per bounded
  batch. Required per-student locks/writes for atomic transitions must be
  explicit, justified and measured; they do not justify an N+1 read workflow.
- Use `select_related`, `prefetch_related`, `Prefetch`, annotations, `Exists`,
  `Subquery`, bulk operations, and database aggregation where they reduce
  queries or memory.
- Use database constraints for invariants.
- Add indexes for real filter, join, uniqueness, and ordering paths.
- Do not add duplicate indexes already covered by a unique constraint or
  primary key.
- Keep student course pages simple: at most about 20 offerings, 20 lessons per
  course, and 20 quizzes per course.
- Progress/result pages for 10,000 students must filter and paginate in SQL.
- Exports may stream or batch; they must not build the full population in
  memory.
- For critical PostgreSQL queries, inspect query count and `EXPLAIN` output
  before launch.
- Avoid PostgreSQL-incompatible SQLite assumptions.
- Apply access/filter/order/page boundaries before materialization. Queryset
  iteration, provider pagination or a streaming response does not bound memory
  if later code accumulates the entire population or output.
- Retrieve only requested response data. Use counts/Exists/narrow projections
  instead of loading children; honor copy/include flags before prefetching.
  HTMX fragments must not compute unrelated full-shell data.
- Define a query/memory/work budget before implementing a list, bulk operation
  or export. Record representative before/after evidence; do not promise zero
  overhead or infer speed from shorter code/background execution.

## Background work and progress

- Population-sized management calculations, promotions, imports, exports,
  full-course copies and recursive storage work use durable jobs through the
  existing Celery/Redis stack. Keep ordinary reads, validation, authorization,
  scans and small actions synchronous. Fix unbounded work/N+1 inside jobs too.
- Keep heavy management work away from media and latency-sensitive delivery.
  Start the dedicated admin pool at concurrency 1/prefetch 1, configurable;
  increase only with resource and PostgreSQL evidence. No per-row thread pools.
- Reuse focused existing job paths. New shared tracking may own status, leases
  and progress; it must not become a configurable generic business engine.
- Validate before enqueue, freeze normalized input/selection, bind actor/scope,
  and acknowledge queued work without claiming completion. Recheck capability
  and resource eligibility before delayed mutations.
- Use leases, idempotency, conflict exclusion and recovery. Commit cursor and
  outcomes with the mutations they describe; stale/duplicate workers must not
  replay effects. External side effects need recorded phases/cleanup.
- Formula recalculation publishes coherent completed student batches and labels
  partial/incomplete results. Block dependent promotion while the affected
  calculation is incomplete; preserve promoted history. Promotion is atomic per
  student; historical intake per row; legacy user CSV and existing copy units
  retain their all-or-nothing contracts.
- Poll one lightweight status request at a time, approximately every 2 seconds
  while active; stop on terminal/hidden/teardown, back off errors/idle discovery,
  and resume on focus. Polling must not rerun the population query.
- Artifacts, row errors, progress and manifests must be bounded, private,
  authorized and cleaned up. Report partial failures honestly.

## Database and migration safety

The local SQLite database contains real data.

- Never delete, overwrite, or recreate it.
- Create and verify a backup before migration work.
- Record pre/post row counts for users, levels, academic years, year-level
  links, offerings, enrollments, lessons, quizzes, questions, submissions,
  grades, attendance, and progress.
- Run schema/data migrations only against local or disposable databases.
- Never run a production migration.
- Never destroy Docker volumes containing migrated data.
- SQLite-to-PostgreSQL import must be repeatable and must verify row counts,
  foreign keys, sequences, constraints, and representative access paths.
- Use `transaction.atomic()` for multi-row state transitions.
- Use `select_for_update()` where concurrent promotion or result replacement
  could race.
- Data migrations must fail clearly on ambiguous mappings; never guess.

Migrations `0033` through `0037` are known to be applied locally and may be
reverted or replaced locally because the user explicitly authorized it. Before
doing so:

1. Verify they have not been applied outside local development.
2. Back up SQLite.
3. Document the rollback and forward path.
4. Preserve every existing record and relationship.

Once a migration is shared or applied outside local development, never rewrite
it; add a forward corrective migration.

## Verification policy

Do not create or reintroduce unit-test suites or application `test_*.py` modules
unless a later explicit user instruction changes this policy. Their removal,
including mixed integration/migration modules and the empty `tests.py` scaffold,
is approved in checklist T01; it is not executed merely by editing documentation.
Retain audit commands and focused smoke/browser/disposable verification tools.
Deleting tests is never verification.

Every non-trivial change still requires a runnable check. Use the smallest
appropriate combination of:

- Python syntax compilation for application code.
- `manage.py check`.
- `makemigrations --check --dry-run`.
- Migration forward/backward rehearsal on a backup or disposable database.
- Academic data audit management command.
- Focused integration or smoke script using real Django ORM paths.
- HTTP smoke checks for student and management flows.
- Query-count inspection.
- PostgreSQL `EXPLAIN`.
- SQLite/PostgreSQL row-count and relationship reconciliation.

Do not claim completion when the configured Python/Django environment cannot run
the checks. Report the exact blocker and leave copy-pasteable commands.

Choose checks appropriate to the change. Documentation-only work needs link,
consistency and scope checks, not database migrations. Significant runtime,
query, job and schema changes require relevant real integration evidence; do
not substitute syntax checks or mocked provider calls for PostgreSQL/worker
behavior, and do not label unrun checks as passing.

## Security and permissions

- `admin`: applications, academic setup, formulas, calculation saving,
  promotion, overrides, corrections, and all management operations.
- `staff`: content management and report viewing only.
- `moderator`: attendance scanning only.
- `student`: own authorized academic content and records only.

Authenticated users denied a resource receive HTTP 403. Unauthenticated browser
requests use login redirects; unauthenticated API requests use HTTP 401.

Never trust a course, lesson, quiz, offering, year-level, enrollment, or student
ID from a URL or form without checking authorization and relationship
consistency.

Keep CSRF protection, validation at trust boundaries, transaction safety, and
answer secrecy for quizzes.

Strictly parse selection booleans/IDs, signed actor/view/scope and request/file
limits before mutation. Storage keys and job/artifact IDs are not authorization;
check both source and destination, including private application documents.

Support both Cloudflare-proxied and direct VPS traffic. Accept forwarded client
identity only from explicitly trusted ingress peers; overwrite the canonical
identity toward Django and validate internal proxy trust there. Direct callers
cannot spoof identity with CF/XFF/X-Real-IP headers. Keep one shared validated
client-identity rule for existing limits and diagnostics.

Browser sessions renew a 7-day lifetime at most once every rolling 24 hours.
The accepted effective inactivity window is approximately 6-7 days. Ordinary
read/poll requests inside that interval must not mark the session modified
solely to refresh expiry. Preserve real session writes and secure cookies.

## UI and localization

- Preserve the existing visual system unless the task explicitly changes it.
- Use Django translations for every new label, heading, status, validation
  message, and action.
- Preserve Arabic RTL and English LTR behavior.
- Arabic is the default despite browser/device English preferences. Honor an
  explicit English selection per browser/device using existing language
  mechanisms; do not add an account preference field or force Arabic over an
  explicit English choice. Apply the same default contract at API/job boundaries.
- Keep accessibility basics: labels, table headings, form errors, button text,
  keyboard-operable controls, and pagination landmarks.
- Use existing Bootstrap/HTMX/Alpine patterns. Do not add a frontend framework.
- Repository text is ASCII plus Arabic script only. Do not add typographic
  punctuation, box drawing, emoji or mojibake characters; use ASCII equivalents
  (`-`, `...`, `"`, `'`, `->`) and keep Arabic text in Arabic. Vendored
  libraries, minified assets and compiled catalogs are exempt. Run
  `python manage.py audit_text_encoding` after changing text; it exits nonzero
  on violations.

## Git and working tree

The working tree may contain user changes.

- Preserve unrelated modifications.
- Never run `git reset --hard`, destructive checkout/restore, or `git clean`.
- Never stage, commit, push, or create a branch unless the user explicitly asks.
- Inspect diffs narrowly and avoid rewriting entire files when a focused patch
  is sufficient.
- Do not commit secrets, `.env`, SQLite databases, backups, logs, generated
  media, or Docker volumes.

## Completion report

Every implementation handoff must state:

- Outcome.
- Changed files.
- Migrations and data transformations.
- Verification performed and results.
- Query/performance evidence for large tables.
- Database assumptions.
- Remaining risks.
- Exact local Docker/PostgreSQL launch and migration commands when relevant.

Do not report "done" while required related callers, templates, migrations, or
data checks remain unfinished.

For each completed unit, demonstrate the intended outcome, preserved behavior,
complete caller coverage and applicable validation. Conditional audit findings
may close as disproved/already fixed only with current evidence. A policy written
in documentation, an enqueued job or a removed test is not a completed runtime fix.
