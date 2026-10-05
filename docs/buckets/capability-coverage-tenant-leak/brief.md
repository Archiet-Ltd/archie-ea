# Task Brief: Fix Cross-Tenant Leak in ApplicationCapabilityCoverage / Requirement Models

## Objective
Close a confirmed, live cross-tenant data leak: `ApplicationCapabilityCoverage`,
`FunctionalRequirement`, and `NonFunctionalRequirement` (`app/models/
business_capabilities.py`) do not inherit `TenantMixin`, despite mapping
between tenant-scoped entities (`ApplicationComponent`, `BusinessCapability`).
Any authenticated user reaching the routes that query these tables sees
every organization's data, not just their own.

## Context
Found by an Aider `researcher` (Gemini 2.5 Pro) sweep of `app/models/
business_capabilities.py` tonight, independently verified against the code
before treating it as real (not taken on the sweep's word):

- `class ApplicationCapabilityCoverage(db.Model)` — no `TenantMixin`, no
  `organization_id` column. Confirmed reachable, unfiltered, in 10+ live
  call sites:
  - `app/modules/capabilities/routes/export_routes.py:53` — `.query.all()`
  - `app/modules/capabilities/routes/mapping_routes.py:793` — `.query.limit(500).all()`
  - `app/modules/capabilities/routes/mapping_routes.py:1097,1253,1382,1440` — `.filter_by(...)`
  - `app/modules/capabilities/routes/map_views.py:314` — `.query.count()`
  - `app/modules/capabilities/routes/roadmap_routes.py:62,236,1950` — `.query.all()`
  - `app/modules/capabilities/routes/process_routes.py:668` — `.filter_by(...)`
  - `app/modules/capabilities/services/capability_mapping_service.py:50,67` — `.query.all()`/`.filter_by(...)`
  - `app/modules/architecture_assistant/capability_derivation.py:124` — `.filter_by(...)`
- `class FunctionalRequirement(db.Model)` (`business_capabilities.py:300`) —
  no `TenantMixin`.
- `class NonFunctionalRequirement(db.Model)` (`business_capabilities.py:400`)
  — no `TenantMixin`.

Also flagged by the same sweep, lower severity, real but not this task's
primary scope:
- `create_capability_archimate_element` (`business_capabilities.py:715-738`,
  a `before_insert` listener) has no `before_update` counterpart — if a
  `BusinessCapability` moves to a new org, its linked `ArchiMateElement`
  stays in the old org's scope. This is the SAME bug class Task P0
  (unified-capabilities-producer, already fixed tonight) closed for a
  different listener — worth checking whether P0's fix pattern
  (per-row skip + explicit org/code-change detection) applies here too.
- `BusinessFunction.archimate_element_id` (`business_capabilities.py:301`)
  has no automated tenant-scoped linkage at all (manual management,
  theoretical risk, not confirmed-reachable).

## Constraints
- **This is a real security fix on live production tables with existing
  data.** Adding `TenantMixin`/`organization_id` to a table with existing
  rows needs a migration/backfill plan — per ADR 0002, `reconcile-schema`
  is ADD-COLUMN-only and nullable-only; existing rows need a backfill
  strategy for `organization_id`, not just a schema change. Read ADR 0002
  before designing the migration.
- **Determine backfill source before writing any migration.** For
  `ApplicationCapabilityCoverage`, the org can likely be derived from either
  side of the mapping (`ApplicationComponent.organization_id` or
  `BusinessCapability.organization_id`) — confirm these actually agree for
  every existing row before trusting either as the backfill source; if they
  ever disagree, that's a separate data-integrity finding to report, not
  silently resolve by picking one side.
- For `FunctionalRequirement`/`NonFunctionalRequirement`, find what they're
  scoped to today (a parent capability? a solution? read the actual foreign
  keys on these classes) — the backfill source depends on this.
- Every route call site listed above needs updating for correct tenant
  scoping once the mixin is added (`TenantMixin`'s ORM event listener
  handles automatic filtering on `.query`, but any `filter_by(id=...)`-only
  call, `.get()` call, or raw SQL touching these tables needs the same
  scrutiny this repo has needed all night for this exact bug class).
- Follow this repo's "Done means DEMONSTRATED" rule: a live browser check
  as two different org personas is the actual acceptance criterion, not a
  passing unit test alone.
- This bucket is separate from P0 (unified-capabilities-producer, already
  merged/deployed) and P2 (model dedup/layout retirement, owned by another
  session) — do not touch `app/models/archimate_core.py`,
  `app/commands/project_capabilities.py`, or anything under
  `app/modules/interface_register/`.

## Deliverable
1. Confirm the backfill source for `organization_id` on all three models
   (verify, don't assume, per the constraint above).
2. Add `TenantMixin` + a migration/backfill plan consistent with ADR 0002.
3. Fix or confirm-already-safe every call site listed above.
4. Decide whether the `before_update` listener gap (capability org-move
   leaving `ArchiMateElement` stale) is in this task's scope or a
   documented follow-up — state the reasoning either way, don't silently
   drop it.
5. A real regression test proving cross-org isolation for all three models
   (adapt the pattern from `tests/test_tenant_isolation.py`, which already
   covers this exact scenario for other models).

## Acceptance Criteria
- A test creating data in org A, logging in as an org B user, and
  confirming `ApplicationCapabilityCoverage`/`FunctionalRequirement`/
  `NonFunctionalRequirement` queries return zero cross-org rows.
- Live browser check: as two different real personas (different orgs),
  confirm a capability-mapping screen shows only that org's data.
- `python scripts/verify.py --tag static` clean.
- No regression to any of the 10+ call sites listed above — each either
  confirmed already-safe (via the mixin's automatic filtering) or
  explicitly fixed if it bypasses that filtering (raw SQL, `.get()` on a
  warm identity-map hit, etc.).

## Handoff Target
`tech-lead` first — this needs a real migration/backfill design decision
before any code, per ADR 0002's constraints on schema changes to tables
with existing data. Then standard `builder` → `refuter` cycle. Given this
is a genuine security fix, no shortcuts on review rigor even under time
pressure — this bucket should get the same multi-round treatment as P0
and P1 tonight, not less.
