# Task 01 — Tenant-scope `ApplicationCapabilityCoverage`

- **Bucket:** `capability-coverage-tenant-leak`
- **Order:** first. Lands and deploys before task 02.
- **Handoff target:** `builder` → `refuter`
- **Branch:** `fix/capability-coverage-tenant-leak` (do not create a new one)

## Objective

Close the live cross-tenant read/write leak on `application_capability_coverage`
by making `ApplicationCapabilityCoverage` a tenant-owned model, backfilling
`organization_id` for every existing production row from a *verified* source, and
proving that every one of the ~25 call sites is now org-scoped — including the
ones the mixin does not automatically cover.

## Context

- Full analysis, including the mechanism, the FK reading and the call-site
  enumeration: `docs/buckets/capability-coverage-tenant-leak/investigation.md`.
  Read it before writing code; the design decisions in it are made, not open.
- **Existing component this extends — it is not new:**
  - the model `ApplicationCapabilityCoverage` in
    `app/models/business_capabilities.py:500` gains the existing
    `TenantMixin` (`app/models/mixins/core.py:55`) already used by its two
    parents and by every other model in the same file
  - the backfill extends `app/commands/reconcile_schema.py`, following the
    existing `_backfill_roadmap_organizations` (`:956-1038`) — same shape, same
    counters, same failure reporting
  - the regression test extends `tests/test_tenant_isolation.py`, using the
    shared fixtures in `tests/conftest.py` (`db_session`, `make_org`,
    `tenant_ctx`) — not a hand-rolled module-scoped `app` fixture
  - the browser check extends `tests/smoke/test_authorisation_matrix.py` and/or
    `tests/smoke/test_archetype_journeys.py` — not a bespoke smoke file
- Enforcement is `app/middleware/tenant_isolation.py`; it is a no-op without
  `g.current_org_id`, and it does not see raw SQL.

## Constraints

1. **Do not touch** `app/models/archimate_core.py`,
   `app/commands/project_capabilities.py`, or anything under
   `app/modules/interface_register/` — concurrent work in other worktrees.
2. **No Alembic revision.** Deploys never run `flask db upgrade` (ADR 0002).
   The `reconcile-schema` backfill is the migration. Writing an Alembic file
   would read to the next person as the authoritative mechanism and would be
   false.
3. **Backfill source is `business_capability.organization_id`.** Decided in the
   investigation §2.1 with reasoning; do not substitute
   `application_components`, do not `COALESCE` the two.
4. **Disagreements and orphans are quarantined, never resolved.** A row whose
   application-org and capability-org differ, or whose parent is missing, keeps
   `organization_id = NULL` (invisible to every tenant, fail-closed, reversible)
   and is reported into `reconcile-schema`'s `failed` list so the command exits 1
   and `schema-drift` goes red. Do not pick a side. Do not delete the row.
5. **Measure production before you deploy.** Run both queries from the
   investigation §2.1 against production (`flask --app manage db-query "..."`,
   read-only) and against the local test DB, and record the four counts in the
   build report. A non-zero disagreement or orphan count is a decision to
   surface, not to code around — it means real cross-tenant links already exist
   in the data.
6. `TenantMixin` declares `nullable=False`; `reconcile-schema` strips NOT NULL
   (`_column_clause`, `:929-953`). The live column will be nullable. That is
   expected and correct — do not add `nullable=True` to the model to "match".
7. `raw-sql-tenancy` is ratcheted at **0**. The raw DELETE at
   `app/modules/applications/routes/_helpers.py:160` targets a tenant table after
   this change. Add `AND organization_id = :org_id` to its predicate, or a
   `tenancy-ok: <reason>` marker with a real reason. The ratchet must not move.
8. **Prefer proof over reasoning for the column-only queries** (investigation
   §4.2). This repo has already shipped a "TenantMixin added but it still leaks"
   regression tonight in another bucket via exactly this shape. Each of the five
   sites gets an assertion; any that does not come back org-scoped gets an
   explicit `.filter(ApplicationCapabilityCoverage.organization_id == g.current_org_id)`.
9. Staging: `git add <file>` per file, never `git add -A`.

## Deliverable

1. `ApplicationCapabilityCoverage(TenantMixin, db.Model)` in
   `app/models/business_capabilities.py`.
2. `_backfill_application_capability_coverage_organizations()` in
   `app/commands/reconcile_schema.py`, wired into `_reconcile()` alongside the
   existing `_backfill_roadmap_organizations` call (`:1252`), reporting
   `before` / `updated` / `unresolved` / `conflicts` in the same format, no-op on
   `--dry-run`, idempotent.
3. The five column-only/aggregate sites in investigation §4.2 either proven safe
   by test or explicitly filtered.
4. Raw SQL site `_helpers.py:160` tenant-scoped or marked.
5. Comments at `app/routes/unified_low_priority_routes.py:276` and
   `app/modules/capabilities/routes/mapping_routes.py:1386` corrected — they
   assert the absence of `TenantMixin`. **Keep the `BusinessCapability` lookups
   at `mapping_routes.py:1390` and `:1436`**: those are authorization checks with
   load-bearing 404 behaviour, not redundant scoping.
6. Grep `app/commands/`, `manage.py` and the scheduler wiring for
   `CapabilityMappingService` constructions; scope explicitly in any
   out-of-request caller found, or record in the report that none exist.
7. Regression test in `tests/test_tenant_isolation.py`: org A creates coverage
   rows, org B reads, asserts zero cross-org rows — covering `.query.all()`,
   `filter_by`, `.count()`, the aggregate/group_by shape, and the two DELETE
   endpoints (`api_delete_mapping`, `api_delete_mapping_by_pair`) returning 404
   for a foreign mapping id.
8. A backfill test: rows with NULL org, a disagreeing row and an orphan row;
   assert the agreeing rows are populated, the other two stay NULL, and the
   command reports them as failures.
9. Browser check extending `tests/smoke/`: two personas in two different orgs,
   capability-mapping screen, each sees only their own mappings — clicking the
   real control and asserting persistence after reload.

## Acceptance criteria

- The four production/local counts from constraint 5 are in the build report,
  with the disagreement and orphan counts stated explicitly (a number, not
  "none expected").
- `flask --app manage reconcile-schema --dry-run` then `reconcile-schema` on a
  database with pre-existing coverage rows: column added, agreeing rows
  backfilled, unresolved/conflicting rows reported and left NULL, second run is a
  clean no-op.
- New tenant-isolation tests pass; the existing `tests/test_tenant_isolation.py`
  suite still passes **when run alone** as well as in a full run (order-dependent
  bugs hide in full-run greens).
- Playwright two-org check passes and is committed under `tests/smoke/`.
- `python scripts/verify.py` (bare, **not** `--tag static`) is green, with
  `raw-sql-tenancy`, `tenant-scoping` and `schema-drift` specifically green.
- No call site from investigation §4 is left unaddressed: each is either listed
  as covered-by-mixin-and-tested, explicitly filtered, or marked.
- Deployed to production in the same session per *Done means DEMONSTRATED*, with
  `scripts/deploy_verified.sh` and a post-deploy two-org browser confirmation.

## Handoff target

`refuter` — read-only-on-code review, with specific instruction to attack the
five column-only query sites (§4.2) and the backfill's conflict path, which are
where this class of fix has failed before.
