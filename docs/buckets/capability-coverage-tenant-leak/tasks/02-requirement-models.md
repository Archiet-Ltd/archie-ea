# Task 02 — Tenant-scope `FunctionalRequirement` and `NonFunctionalRequirement`

- **Bucket:** `capability-coverage-tenant-leak`
- **Order:** second — starts only after task 01 has merged and deployed.
- **Handoff target:** `builder` → `refuter`
- **Branch:** `fix/capability-coverage-tenant-leak` (do not create a new one)

## Objective

Close the **latent** cross-tenant exposure on `functional_requirement` and
`non_functional_requirement` by making both models tenant-owned and backfilling
`organization_id` from each one's single non-nullable tenant-owned parent, before
any surface is built that would make the leak reachable.

## Context

- Analysis: `docs/buckets/capability-coverage-tenant-leak/investigation.md`,
  §2.2, §2.3, §4.3, §5 (F-1) and §6.
- **Reachability, measured:** a full grep of `app/` finds **no ORM read call
  site** for either model. The only references are the class definitions
  (`app/models/business_capabilities.py:300`, `:400`), registration in
  `app/models/__init__.py:108-109`, a `dead-code-ok` import in
  `app/models/unified_capability.py:857-858`, and two raw-SQL DELETEs in
  `app/modules/solutions_strategic/v2/routes/solution_design_routes.py:4936,4946`.
  This is a latent defect, not a live one. Say so honestly in the report — do not
  inherit task 01's severity language.
- **Existing components this extends — none are new:** the same `TenantMixin`
  (`app/models/mixins/core.py:55`), the same `reconcile_schema.py` backfill
  pattern (`_backfill_roadmap_organizations`, `:956-1038`), the same
  `tests/test_tenant_isolation.py` suite on the shared `tests/conftest.py`
  fixtures.

## Constraints

1. Same worktree exclusions as task 01: do not touch
   `app/models/archimate_core.py`, `app/commands/project_capabilities.py`, or
   `app/modules/interface_register/`.
2. **No Alembic revision** (ADR 0002 — `flask db upgrade` never runs on deploy).
3. **Backfill sources, read from the actual FKs — do not substitute:**
   - `FunctionalRequirement` → `business_function.organization_id`, joined on the
     **NOT NULL** `function_id`. `capability_id` is nullable and cannot be the
     primary source.
   - `NonFunctionalRequirement` → `business_capability.organization_id`, joined
     on the **NOT NULL** `capability_id`.
4. **Cross-check, quarantine, never resolve.** For `FunctionalRequirement` rows
   where `capability_id IS NOT NULL` and the function's org differs from the
   capability's org, leave `organization_id` NULL and report as a failure — same
   fail-closed policy as task 01. Same for orphans on either table.
5. `raw-sql-tenancy` is ratcheted at **0**. Both DELETEs at
   `solution_design_routes.py:4936` and `:4946` target tenant tables after this
   change. Add an `organization_id` predicate or a justified `tenancy-ok:`
   marker. The ratchet must not move.
6. **F-1 is out of scope and must not be attempted here.**
   `functional_requirement.requirement_id` and
   `non_functional_requirement.requirement_id` are globally `unique=True` with no
   org in the key, so two orgs generating the same ordinal collide. The fix is a
   composite unique index, i.e. DROP + CREATE INDEX — DDL `reconcile-schema`
   cannot express and ADR 0002 forbids until the Alembic baseline lands. Record
   it in the report and leave it. Do **not** quietly widen this task into it.
7. **Do not claim a browser demonstration.** There is no UI for either model. A
   fabricated "demonstrated" claim for a non-existent screen is worse than an
   honest "latent defect, unit-tested, no reachable surface". State the
   reachability measurement instead, and say what would make it reachable.
8. Staging: `git add <file>` per file, never `git add -A`.

## Deliverable

1. `FunctionalRequirement(TenantMixin, db.Model)` and
   `NonFunctionalRequirement(TenantMixin, db.Model)` in
   `app/models/business_capabilities.py`.
2. Two backfill functions in `app/commands/reconcile_schema.py` following
   `_backfill_roadmap_organizations`, wired into `_reconcile()`, reporting
   `before` / `updated` / `unresolved` / `conflicts`, no-op on `--dry-run`,
   idempotent.
3. Pre-deploy measurement, recorded in the report as numbers — row counts on both
   tables in production, orphan counts, and the `FunctionalRequirement`
   function-vs-capability disagreement count.
4. Both raw-SQL DELETEs tenant-scoped or marked.
5. Tenant-isolation tests in `tests/test_tenant_isolation.py` for both models:
   org A writes, org B reads zero rows; plus a backfill test covering the
   populated, orphan and disagreeing cases.

## Acceptance criteria

- Pre-deploy counts present in the report as figures, not assurances.
- `reconcile-schema --dry-run` then `reconcile-schema` on a database holding
  pre-existing rows in both tables behaves as specified; a second run is a clean
  no-op.
- New tests pass; `tests/test_tenant_isolation.py` passes when run alone.
- `python scripts/verify.py` (bare) green, with `raw-sql-tenancy`,
  `tenant-scoping` and `schema-drift` specifically green.
- The report states plainly that this closes a latent, currently-unreachable
  exposure, names F-1 as an open follow-up, and does not assert a browser
  demonstration.
- Deployed to production in the same session per *Done means DEMONSTRATED*, via
  `scripts/deploy_verified.sh`, with the post-deploy evidence being a green
  `reconcile-schema` on the live database (correct evidence for a schema/backfill
  change with no UI) rather than a screen walk.

## Handoff target

`refuter` — with specific instruction to check that F-1 was not quietly attempted
and that the `FunctionalRequirement` backfill really keys on `function_id` (the
NOT NULL parent) and not on the nullable `capability_id`.
