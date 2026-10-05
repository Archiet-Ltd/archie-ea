# Investigation — cross-tenant leak in ApplicationCapabilityCoverage / FunctionalRequirement / NonFunctionalRequirement

- **Role:** tech-lead
- **Date:** 2026-09-17
- **Branch:** `fix/capability-coverage-tenant-leak` (worktree `archie-oss-tenant-leak`)
- **Input:** `docs/buckets/capability-coverage-tenant-leak/brief.md`
- **Output:** `tasks/01-application-capability-coverage.md`, `tasks/02-requirement-models.md`

Everything below was read out of the tree on this branch. Where I could not
measure something (no DB access in this session), it is marked
**UNMEASURED** and converted into a step the builder must execute against a
real database rather than into an assumption.

---

## 1. Mechanism — what `TenantMixin` actually does, and where it stops

`app/models/mixins/core.py:55` — `TenantMixin` contributes exactly two things:

- `organization_id` — `db.Column(db.Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True, default=_default_org_id)`
- an `organization` relationship

`nullable=False` is declared **on the model**. Note this against ADR 0002 — see §3.

`app/middleware/tenant_isolation.py` provides the enforcement:

- `_add_tenant_filter` (`do_orm_execute`) applies
  `with_loader_criteria(TenantMixin, lambda cls: cls.organization_id == g.current_org_id, include_aliases=True)`
  to SELECT, ORM-enabled UPDATE and ORM-enabled DELETE.
- `_set_tenant_on_new` (`before_flush`) stamps `organization_id` on new instances.
- **Both are no-ops when `g.current_org_id` is None** — CLI, scheduler, importers,
  tests, unauthenticated requests.

What the mixin therefore does **not** cover, and what the call-site audit in §4
is looking for:

1. raw SQL (`db.session.execute(db.text(...))`) — never sees `do_orm_execute`
2. `Query.get()` / `Session.get()` on an identity-map **hit** — no SQL emitted, no filter
3. any code path running outside a request context
4. column-only / aggregate selects — `with_loader_criteria` targets mapped
   entities; whether it attaches to `db.session.query(Model.some_column)` is
   version- and shape-dependent. **I am not asserting either way.** This is the
   precise shape that produced tonight's "TenantMixin added but it still leaks"
   repeat in another bucket, so it is handled by proof, not by reasoning.
5. a `NULL` `organization_id` — the predicate is `= g.current_org_id`, so a NULL
   row is invisible to **every** tenant. That is fail-closed (good) but it is
   data disappearance, not a leak, and it must be measured rather than shipped
   blind. See §3.

---

## 2. Backfill sources — read from the actual foreign keys, not assumed

### 2.1 `ApplicationCapabilityCoverage` (`business_capabilities.py:500`)

```
application_component_id -> application_components.id   NOT NULL
capability_id            -> business_capability.id      NOT NULL
```

Both parents are tenant-owned and both carry a non-nullable org:

- `ApplicationComponent` — `app/models/application_portfolio.py:80`,
  `class ApplicationComponent(TenantMixin, db.Model, OptimisticLockMixin)`
- `BusinessCapability` — `app/models/business_capabilities.py:29`,
  `class BusinessCapability(TenantMixin, db.Model)`

So there are **two** candidate backfill sources, and the brief is right to
refuse to let either be assumed.

**Can they disagree?** Structurally, yes — nothing prevents it:

- there is no FK, CHECK constraint or DB trigger tying
  `application_capability_coverage`'s two parents to the same organization
  (the membership-trigger machinery in `app/commands/reconcile_schema.py`
  covers the transformation tables only; this table is not in
  `_MEMBERSHIP_TABLES`)
- the write paths (`mapping_routes.py:1320`, `mapping_routes.py:2059`,
  `capability_mapping_service.py:254`) construct the row from two separately
  resolved ids and never compare their orgs
- the bulk/auto-map paths at `mapping_routes.py:2015-2019` and `:2167` select
  candidate pairs from column-only queries whose scoping is itself unproven (§4)
- some of this data arrived by import/seed, i.e. outside a request context,
  where neither tenant layer ran at all

**UNMEASURED — this session has no DB access.** I am not going to guess a row
count. The measurement is mandatory and is step 1 of task 01, to be run against
**production** (and the local test DB) before any DDL:

```sql
-- disagreement between the two candidate sources
SELECT count(*)
FROM application_capability_coverage cov
JOIN application_components app ON app.id = cov.application_component_id
JOIN business_capability     cap ON cap.id = cov.capability_id
WHERE app.organization_id IS DISTINCT FROM cap.organization_id;

-- rows with no resolvable parent at all (orphans survive because the FKs are
-- NO ACTION and _cascade_delete_application does the cleanup in app code)
SELECT count(*)
FROM application_capability_coverage cov
LEFT JOIN application_components app ON app.id = cov.application_component_id
LEFT JOIN business_capability     cap ON cap.id = cov.capability_id
WHERE app.id IS NULL OR cap.id IS NULL;
```

**Decision — chosen source: `business_capability.organization_id`.**
Reasoning, not preference:

- The two places in the tree that already hand-scope this table both scope it
  **through the capability**: `app/routes/unified_low_priority_routes.py:276-285`
  ("restricting to THIS organisation's capability ids") and
  `app/modules/capabilities/routes/mapping_routes.py:1386-1394` ("BusinessCapability
  IS tenant-filtered, so resolving the parent capability is the check").
  Backfilling from the capability makes the post-fix visibility of every row
  identical to its pre-fix visibility on those two surfaces — the change closes
  the leak without silently re-parenting anything a user can currently see.
- `capability_id` is the mapping's semantic anchor (the `capability` relationship
  and the `application_coverage_mappings` backref are declared on it;
  `__repr__` and `to_dict()` both dereference it).

**Decision — disagreements are NOT silently resolved.** A row whose two parents
sit in different organizations is *itself* a cross-tenant link; picking a side
completes the leak in one direction under cover of a security fix. Such rows are
left with `organization_id` NULL — invisible to every tenant, fail-closed,
non-destructive and fully reversible (the row and both FKs are untouched; only
the new column is unset) — and reported as a **blocking failure** so the deploy
goes red and a human decides. Same for orphans.

### 2.2 `FunctionalRequirement` (`business_capabilities.py:300`)

```
function_id   -> business_function.id     NOT NULL
capability_id -> business_capability.id   NULLABLE
```

`BusinessFunction` is `TenantMixin` (`business_capabilities.py:228`).
**Chosen source: `business_function.organization_id`** — it is the only
non-nullable tenant-owned parent, so it resolves every row. `capability_id` is
nullable and therefore cannot be the primary source; where it *is* set it serves
as a cross-check (`function.organization_id <> capability.organization_id` is the
same disagreement finding as §2.1 and gets the same quarantine treatment).

### 2.3 `NonFunctionalRequirement` (`business_capabilities.py:400`)

```
capability_id -> business_capability.id   NOT NULL
```

Single non-nullable tenant-owned parent. **Chosen source:
`business_capability.organization_id`.** Unambiguous — no disagreement case
exists, only the orphan case.

---

## 3. Migration / backfill design under ADR 0002

ADR 0002's live state (not its target state) governs here:

- deploys run `init-db && reconcile-schema && gunicorn`. **`flask db upgrade` is
  never run.** An Alembic revision is therefore documentation, not an executable
  migration. Writing one and calling it the migration would be the failure this
  ADR exists to name.
- `reconcile-schema` is ADD-COLUMN-only and **strips NOT NULL**:
  `_column_clause()` (`app/commands/reconcile_schema.py:929-953`)
  `re.sub(r"\s+NOT\s+NULL\b", "", rendered)`. So `TenantMixin`'s
  `nullable=False` yields a **nullable** live column with every existing row
  NULL. The ORM still enforces NOT NULL on write.
- ADR 0002 §"Consequences": *"Backfills have nowhere to live."* — except that
  since the ADR was written, one precedent has been built.

**There is a working precedent in-tree and it is the pattern to follow:**
`_backfill_roadmap_organizations` (`app/commands/reconcile_schema.py:956-1038`)
retrofits `organization_id` onto `strategic_roadmap_items` from its programme
parent. It does exactly the right things and the new backfills should be
structurally identical to it:

- guards on table presence and on the live column existing
- counts `before`, `eligible`, `unresolved`, `conflicts` **first**
- `UPDATE ... FROM parent` only for the eligible set
- reports `unresolved` and `conflicts` into `failed`, which makes
  `reconcile-schema` exit 1 and the `schema-drift` gate go red
- is a no-op on `--dry-run` and idempotent on re-run

**Ordering is already correct and must not be disturbed:** `_reconcile()` runs
the ADD COLUMN loop (`:1198-1224`) before the backfill helpers (`:1252`). So a
single `reconcile-schema` run adds the column and populates it. The NULL window
is intra-command, not cross-deploy — but it is still real for the duration of the
run, which is one more reason the pre-deploy measurement in §2.1 is not optional.

**Decision — the migration is:**

1. `TenantMixin` added to the model class (this is the whole schema change)
2. a `_backfill_*_organizations` function per table in
   `app/commands/reconcile_schema.py`, modelled on
   `_backfill_roadmap_organizations`, wired into `_reconcile()`
3. **no Alembic revision** — it would never run and would read to the next person
   as the authoritative mechanism. The `reconcile-schema` backfill *is* the
   record. (If a reviewer wants an Alembic revision for the eventual ADR 0002
   baseline, it is additive and can be written later against the baseline; it is
   not what makes this fix work.)
4. the measurement queries in §2.1 run against production **before** the deploy,
   so the deploy's red/green is predicted rather than discovered

**Explicitly rejected alternatives:**

- *Backfill from `application_components`* — rejected, see §2.1.
- *`COALESCE(cap.organization_id, app.organization_id)`* — rejected: it converts
  a disagreement into a silent choice, which is precisely what the brief forbids.
- *Delete unresolvable rows* — rejected. Quarantine-by-NULL achieves the same
  security posture and is reversible; deletion is not, and nothing here requires
  it. (`CLAUDE.md` permits destructive remediation when it is the correct fix;
  it is not the correct fix when a reversible variant is of equal quality.)
- *Make the column nullable on the model to avoid the drift question* — rejected:
  it would permanently license org-less rows in a tenant table.

---

## 4. Call-site audit

The brief lists "10+". The real count is **~25 across 10 files**, and it includes
two raw-SQL sites and one whole file the brief does not mention
(`app/routes/unified_low_priority_routes.py`). Full enumeration:

### 4.1 Made safe automatically by the mixin (ORM entity queries)

These load the mapped entity; `with_loader_criteria` attaches. No code change
needed beyond the mixin — but each is covered by the regression test in task 01.

| File | Lines |
|---|---|
| `app/modules/capabilities/routes/export_routes.py` | 53 |
| `app/modules/capabilities/routes/mapping_routes.py` | 793, 1097, 1253, 1382, 1440 |
| `app/modules/capabilities/routes/map_views.py` | 314 |
| `app/modules/capabilities/routes/roadmap_routes.py` | 62, 236, 1950 |
| `app/modules/capabilities/routes/process_routes.py` | 668 |
| `app/modules/capabilities/routes/enterprise_crud_routes.py` | 167-169, 183 |
| `app/modules/capabilities/services/capability_mapping_service.py` | 50, 67, 242, 275, 291-292, 301 |
| `app/modules/architecture_assistant/capability_derivation.py` | 124-126 |
| `app/services/capability_fact_sheet.py` | 87-90 |
| `app/routes/unified_low_priority_routes.py` | 283-285 |

### 4.2 Column-only / aggregate selects — UNPROVEN, must be proven per site

Not "probably fine". Each needs a test asserting zero cross-org rows; any that
fails gets an explicit `.filter(Model.organization_id == g.current_org_id)`.

| File | Lines | Shape |
|---|---|---|
| `mapping_routes.py` | 1624 | `db.session.query(ACC.application_component_id)` |
| `mapping_routes.py` | 1795 | `...ACC.capability_id` subquery |
| `mapping_routes.py` | 2015-2019 | two-column select + `.in_(...)` |
| `mapping_routes.py` | 2167 | `...ACC.application_component_id` subquery |
| `enterprise_crud_routes.py` | 90-93 | `query(ACC.capability_id, func.count(ACC.id)).group_by(...)` |

### 4.3 Raw SQL — the mixin cannot help, and the `raw-sql-tenancy` ratchet is @ 0

| File | Line | Statement |
|---|---|---|
| `app/modules/applications/routes/_helpers.py` | 160 | `DELETE FROM application_capability_coverage WHERE application_component_id = :id` |
| `app/modules/solutions_strategic/v2/routes/solution_design_routes.py` | 4936 | `DELETE FROM functional_requirement WHERE archimate_element_id IN (...)` |
| `app/modules/solutions_strategic/v2/routes/solution_design_routes.py` | 4946 | `DELETE FROM non_functional_requirement WHERE archimate_element_id IN (...)` |

These three become gate-relevant the moment these tables are tenant-owned. Each
needs `AND organization_id = :org_id` in the predicate (defence-in-depth per
`CLAUDE.md`, even where the id was resolved through a tenant-filtered read
upstream), or a justified `tenancy-ok: <reason>` marker. The ratchet must not move.

### 4.4 `.get()` on a warm identity map

**None found** on any of the three models. Searched the whole of `app/` for these
class names; every read is `.query...` or `db.session.query(...)`. Recorded so the
next reader knows it was checked rather than skipped.

### 4.5 Now-false comments that must be corrected in the same diff

- `app/routes/unified_low_priority_routes.py:276` — *"ApplicationCapabilityCoverage
  has no TenantMixin, so it carries no organization_id"*
- `app/modules/capabilities/routes/mapping_routes.py:1386` — *"has no TenantMixin,
  so nothing scopes it to the caller's organisation"*

Both will be lies after this change, and a stale comment asserting the absence of
a safeguard is how the next person reintroduces a manual workaround. **Keep the
capability lookups themselves** — at `:1390` and `:1436` the `BusinessCapability`
resolution is an *authorization* check (does this caller own the thing being
deleted) whose 404 behaviour is load-bearing, not just redundant scoping. Update
the prose only.

### 4.6 Out-of-request callers

`CapabilityMappingService` is a plain service class with no `g` dependency; if any
CLI command or scheduler job constructs it, both tenant layers are off for that
path. Builder must grep `app/commands/`, `manage.py` and any scheduler wiring for
constructors and scope explicitly there. Unverified in this session.

---

## 5. Findings raised, not resolved

### F-1 — globally unique `requirement_id` collides across tenants (pre-existing)

`FunctionalRequirement.requirement_id` (`:317`) and
`NonFunctionalRequirement.requirement_id` (`:416`) are both `unique=True` with no
org in the key, and the ids are generated from capability/function ordinals
(`CAP-001-FUN-002-REQ-003`). Two organizations generating the same ordinal
collide and the second org's write fails with an IntegrityError.
`BusinessCapability.code` (`:46`) has the identical shape.

This is **pre-existing** — not introduced here — but adding `TenantMixin` is what
makes it obviously wrong rather than merely odd. **Out of scope:** the fix is a
composite unique index `(organization_id, requirement_id)`, which requires
DROP + CREATE INDEX. `reconcile-schema` cannot express a drop (ADD-only by
contract) and ADR 0002 forbids the DDL until the Alembic baseline lands. Correct
home: the ADR 0002 step-7 work, or its own bucket with a maintenance window.

### F-2 — `before_update` listener gap on `BusinessCapability` → `ArchiMateElement`

`create_capability_archimate_element` (`business_capabilities.py:572-596`) is
`before_insert` only and stamps `organization_id=target.organization_id` onto the
mirrored element. Nothing re-stamps it if the capability later moves org.

**Decision: out of scope for this task; documented follow-up.** Reasoning, since
the brief requires it stated either way:

1. Different model and different failure mode. This bucket closes a *read* leak on
   three join/child tables; F-2 is a *write-time staleness* bug on a fourth model's
   mirror element. Bundling them widens the diff on a security fix that needs the
   tightest possible review.
2. It is not currently reachable. `BusinessCapability.organization_id` is set by
   the tenant `before_flush` on insert and by nothing else — there is no org-move
   route or CLI command in the tree. Builder should confirm with a grep for
   assignments to `.organization_id` on `BusinessCapability`; if one exists, this
   promotes to a real defect and gets its own bucket immediately.
3. Its natural fix pattern (the P0 per-row-skip + explicit change detection) lives
   in `app/models/archimate_core.py`, which this branch is **explicitly barred from
   touching** (concurrent work in another worktree tonight). Doing it here would
   guarantee a merge conflict on a security fix.
4. It is a staleness bug, not an exposure: the element stays in the *old* org,
   which under-exposes rather than over-exposes. Fail-closed.

Recommended bucket slug: `capability-org-move-archimate-staleness`, to start after
both the P0 archimate work and this task have merged.

### F-3 — `BusinessFunction.archimate_element_id` has no automated tenant-scoped linkage

Carried through from the brief, unchanged. Theoretical, unconfirmed-reachable, not
touched here.

---

## 6. Task split — and why two, not one

Split, because the three models have genuinely different backfill sources,
different reachability, and therefore different honest acceptance criteria:

- **Task 01 — `ApplicationCapabilityCoverage`.** Live, reachable, user-visible
  leak across ~25 call sites. Two candidate backfill sources requiring a
  disagreement measurement. Has real UI, so "Done means DEMONSTRATED" applies in
  full: a two-org Playwright check on the capability-mapping screen.
- **Task 02 — `FunctionalRequirement` + `NonFunctionalRequirement`.** One
  unambiguous non-nullable tenant parent each, and — measured in §1's grep —
  **zero ORM read call sites anywhere in `app/`**. The only references are the
  model definitions, `app/models/__init__.py` registration, a `dead-code-ok`
  import in `unified_capability.py`, and the two raw-SQL DELETEs. The leak is
  therefore **latent, not currently reachable**. Its acceptance criterion is a
  unit test, and claiming a browser demonstration for a surface that does not
  exist would be a fabricated proof.

Task 01 lands and deploys first — it is the live exposure. Task 02 follows on the
same branch.
