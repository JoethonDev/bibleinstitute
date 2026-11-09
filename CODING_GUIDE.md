# Coding guide - Bible Institute LMS

This is the permanent implementation guide required by [AGENTS.md](AGENTS.md).
Use [project_memory.md](project_memory.md) to locate existing owners and reusable
operations. Use [IMPLEMENTATION_MEMORY.md](IMPLEMENTATION_MEMORY.md) for the
current handoff, not for reconstructing the architecture from session history.

## 1. Reading order and scope

1. Read AGENTS.md, this guide, project_memory.md and the compact current handoff.
2. Open the approved task in [docs/current](docs/current/README.md).
3. Inspect the owning code and connected callers identified by the project map.
4. Consult a focused reference only when its contract is relevant. Archived
   instructions are historical evidence, never the current implementation plan.

These rules apply to features and bug fixes. They describe requirements; some
existing code still violates them and is listed in the remediation checklist.
Do not claim a runtime fix because its intended behavior is documented.

An audit, proposal or documentation request permits those artifacts, not
application implementation. Approved remediation remains available for an
implementation task; do not execute unrelated checklist entries opportunistically.

## 2. Start with a small, explicit contract

Before changing code, identify:

| Question | Required answer |
|---|---|
| Outcome | Concrete current and intended behavior; preserve which existing cases? |
| Owner | Existing model/domain function/query helper that should own the rule. |
| Callers | Web, API, task, form, template, admin, command and export consumers. |
| Trust | Actor capability, resource ownership, relationship consistency and input bounds. |
| State | Transaction/locking/idempotency, concurrent edits and external cleanup. |
| Work | Expected population/page/batch, requested fields and response/query/memory budget. |
| Evidence | Smallest runnable reproduction and acceptance checks on the relevant backend. |

For a local change this can be a short note. Do not create a separate design
document for every button or helper. Ask only about an unresolved consequential
business decision; do not reopen settled user policies or routine engineering
choices already covered here.

### Feature workflow

- Find the related feature in project_memory.md before introducing a new path.
- Extend its canonical rule and change all required adapters together.
- Add only constraints/indexes needed by actual invariants and query paths.
- Include forms, invalid states, templates, API/job responses, permissions and
  translations in the same implementation unit.
- Verify realistic success, rejection, retries and relevant concurrency.
- Update the project map only if ownership, model relationships, entrypoints or
  reusable contracts changed. Record outcomes/blockers in the compact handoff.

### Bug workflow

- Reproduce with a focused scenario or trace the exact source path; identify
  what is proven versus a hypothesis and whether current code already fixed it.
- Find the root rule and affected consumers. Fixing only the visible page while
  leaving mobile/export/task callers inconsistent is incomplete.
- Preserve unrelated approved behavior. Name any intentional policy change.
- Re-run the reproduction, relevant adjacent workflow and performance evidence.
- Remove the obsolete path after its consumers move. Do not add compatibility
  fallbacks or preserve a defect merely because an old test expected it.

## 3. Code that is simple and reusable

- Prefer explicit names, plain functions, focused queryset helpers, concise
  type hints, enums, database constraints and transactions.
- Shared code owns a complete rule or operation with named inputs/results.
  Actual multiple callers or a critical invariant justify extraction; line
  count does not. Avoid forwarding aliases and one-use helper layers.
- Keep HTTP parsing/status/redirects in views and delivery/recovery in tasks.
  Both call the same domain operation. Never construct a fake request, mutate
  request.POST or decode another view's HttpResponse to reuse business logic.
- Keep different policies distinct: offering readability differs from current
  writable enrollment; channel eligibility differs between notifications and
  Telegram; historical intake differs from legacy account CSV atomicity.
- Do not introduce repositories, factories, interfaces, generic workflow
  engines, service hierarchies, universal form engines or new frontend stacks.
- Keep views.py intact apart from genuine shared academic-domain extraction.
  A focused new endpoint module for an approved feature does not authorize a
  broad views-module reorganization.
- Normalize external input once into an explicit contract. A delayed task,
  independent API or resource lookup still rechecks current authorization;
  sharing the predicate does not eliminate those trust boundaries.
- Use callable time-dependent defaults and Django/Cairo-local time. Preserve
  serialized migration imports and the exact approved academic-date meaning.

### Error handling

- Validate booleans strictly: a string such as "false" must not become true via
  bool(value). Normalize IDs, enums, signatures, file sizes and row bounds
  before reads, work or mutations.
- Keep invalid bound forms and entered values. Use translated field/nonfield
  errors instead of success-shaped redirects or discarded errors.
- Catch expected failures where they can be handled. Background jobs may have
  a final failure boundary, but must persist a truthful terminal/partial state.
- Do not reference variables that were never assigned when parsing failed.
  Log safe context, never passwords, capabilities, signed URLs or raw private
  documents. A failed command exits nonzero.

## 4. Query and memory design

Decide what this response needs before evaluating its queryset. Authentication,
authorization and requested data have costs; "zero overhead" is not a budget.

| Need | Implementation direction |
|---|---|
| Permission boolean | Canonical access predicate/Exists; do not hydrate child content. |
| Count | Aggregate/annotation/subquery; do not instantiate lessons/questions. |
| Navigation | Narrow ID/name/ordering projection for authorized content. |
| Related label/object | select_related for consumed FKs; use the FK ID if no object is needed. |
| Child collection | Conditional bounded Prefetch using the actual fields/flags. |
| Admin/results list | SQL access/filter/order/pagination before materialization. |
| Whole population | Keyset/bounded batches with stable selection/cursor and explicit outcomes. |
| Large output | Stream/spool per batch or produce a private export job artifact. |
| HTMX fragment | Explicit fragment data/root contract without unrelated shell queries. |

Important checks:

- A queryset is lazy. One query supplying a comprehension is not inherently
  N+1. Related/deferred lookups inside each iteration can be. Inspect SQL.
- Prefetch followed by a new related-manager filter/order query can discard
  its benefit. Use the cached bounded collection when that is the contract.
- only()/defer() can introduce deferred-field queries if a serializer/template
  consumes omitted fields. Trace every field consumer before narrowing.
- Do not issue repeated exists/count/list evaluations for facts already loaded
  in the current bounded batch. Do not fetch optional sections just in case.
- Pagination/iterator/StreamingHttpResponse is not bounded if later code builds
  a population-sized dictionary, provider-key list or output buffer.
- Batch read facts, then perform required locked atomic writes. Per-student
  promotion transactions do not justify repeated formula/payment/rule lookups.
- Check existing PK/unique/covering indexes before adding one. Use actual
  filters/joins/order and PostgreSQL plans, not speculative indexing.

For scale-sensitive changes, record queries and peak memory at 1/50/100/500 rows
within a batch and the 10,000-student workload. Reads should follow the number
of batches/required domain dimensions, rather than one read per returned row.
Keep student pages around the established small offering/content limits.
EXPLAIN ANALYZE executes the statement: use disposable representative read
queries, not casual writes or production data.

## 5. Background execution is an execution boundary

Use jobs for population-sized management operations, not ordinary reads,
authentication, form validation or a small single-record correction. The
current checklist contains the exact job matrix and foundation tasks; do not
assume its proposed AdminOperationJob already exists in code.

- Use existing Celery/Redis, specialized media/quiz/delivery paths and isolated
  heavy-admin capacity. Begin admin concurrency/prefetch at 1 and measure before
  increasing. No per-row thread/process pool.
- Validate/freeze normalized input and selection before enqueue. Capture actor,
  scope, language and relevant input revision; return queued/accepted, not done.
- Durable state, leases, idempotency, conflict exclusion and recovery must
  survive restart/duplicate delivery. Commit progress with the DB effects.
- Recheck current capability/ownership at delayed mutations and artifact reads.
  Keep status responses lightweight and row errors paged/bounded.
- One in-flight active poll, paused while hidden/detached and stopped on terminal
  state; slower idle discovery and bounded backoff. No population query per poll.
- Keep private artifacts/manifests bounded, owner scoped, expiring and cleaned.
  External side effects need recorded phases; DB rollback cannot undo storage.

Preserve approved commit semantics: coherent partial formula batches may become
visible, with incomplete state labeled and dependent promotion blocked;
promotion per student; historical intake per row; legacy account CSV and
existing copy units all-or-nothing. Do not change these because a task is async.

## 6. Academic, security and localization invariants

- Reuse canonical offering access at every list/detail/media/quiz/progress/API
  boundary. Validate submitted relationship IDs, not merely each ID separately.
- Keep application roles separate from academic level/year/enrollment. Content
  staff/report access does not grant account privilege management.
- Preserve quiz answer secrecy, deadlines/exceptional windows, snapshot revision
  checks, immutable retries and idempotent finalization. Untouched structured
  inputs remain unanswered until interaction/restoration explicitly sets them.
- Formula applicability/weights must work for supported study modes. Reject a
  zero applicable total; existing affected calculations are unevaluable. Never
  invent zero/fail scores or rewrite promoted historical snapshots.
- Check storage source and destination ownership; job IDs, key names and signed
  selections are not substitutes for actor/resource authorization.
- Support Cloudflare and direct VPS traffic through validated ingress trust.
  Never trust arbitrary CF/XFF/X-Real-IP headers. Use the same validated client
  identity for existing limiters and diagnostics.
- Arabic defaults despite English browser/device settings. Explicit English
  persists per browser/device, with existing language mechanisms and no new
  account preference field. Keep translated errors, RTL/LTR and job/API language
  behavior consistent; note native-client dependencies outside this checkout.
- Source and documentation text is ASCII plus Arabic script only. Use ASCII
  punctuation and arrows (`-`, `...`, `"`, `'`, `->`), never typographic
  punctuation, box drawing, emoji or mojibake sequences. Arabic text stays in
  Arabic. Vendored/minified assets and compiled catalogs are exempt. The
  `audit_text_encoding` management command enforces this and exits nonzero on
  violations; run it with the other source checks.
- Renew a 7-day browser session at most every rolling 24 hours, with the accepted
  approximately 6-7-day inactivity granularity. Do not mark read-only sessions
  modified between renewals solely to refresh expiry.
- No secret rotation or private-data rewrite hidden in cleanup. Preserve CSRF,
  cookie security, credentials and encryption keys.

## 7. Validation and completion

No new unit suite or test_*.py modules. Approved test removal belongs to T01;
retain focused audit, ORM, HTTP, browser, worker and migration verification.
Do not disguise a replacement unit suite under a smoke filename.

Choose the smallest meaningful checks, then integrate the complete change:

| Change | Required relevant evidence |
|---|---|
| Docs only | Links, source ownership, authority/status consistency and unchanged app scope. |
| Python/templates/JS | Syntax/template compilation, system checks and affected workflow smoke. |
| Shared domain/auth | All callers, allowed/denied actors, relationships, error and historical cases. |
| Query/bulk/export | SQL count/plan, bounded memory, realistic data and output parity. |
| Jobs | Kill/restart, duplicates, lease takeover, partial failure, conflicts and queue isolation. |
| Models/migrations | Backup, verified local/disposable DB, forward/reversal rehearsal where supported, counts/FKs/sequences/constraints. |
| UI/HTMX | Direct/fragment navigation, bound errors, lifecycle, EN/AR, mobile and accessibility. |

Missing PostgreSQL, target runtime, Redis, browser or provider evidence leaves
the corresponding gate open. Syntax and mocks do not prove concurrency, load or
live integration. Do not rerun broad checks after they pass without a relevant
new change/failure. Never run a production migration or delete data volumes.

Close a unit only when its intended behavior, related callers, preservation and
applicable checks have evidence. Report exact files/migrations, commands/results,
performance/data evidence and remaining blockers. Update the project map in
place for structural changes and the compact handoff for current status. Move
superseded plans/evidence to the archive without labeling unresolved work done.
