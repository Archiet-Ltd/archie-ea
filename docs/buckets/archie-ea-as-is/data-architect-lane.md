# As-Is: Data Architecture lane

Author: `data-architect`. Input: `current-state.md`, ADR 0002.

## Schema management: three mechanisms, one source of truth in name only

- `create_all()` via `flask init-db` — tables only, no column-level power.
- `flask reconcile-schema` — the actual drift answer today: ADD-only,
  nullable-only, idempotent, runs on every container boot. This is a runtime
  mutator with no review step, which ADR 0002 names directly as the problem:
  "every deploy silently mutates production schema... there is no plan, no
  review, and no record of what changed."
- Alembic (130+ revisions, multiple merge heads) — **not run on deploy**.
  Effectively historical/inert.

**Real consequence already incurred once:** 47 drifted columns across ~20
tables led to a single `UndefinedColumn` cascading into
`InFailedSqlTransaction` for every later query in the same transaction,
500-ing whole pages (`docs/known-issues/schema-drift-on-existing-databases.md`).

**Structural constraint this creates:** the system currently *cannot* ship a
non-nullable column or a column needing backfill without a maintenance
window, because `reconcile-schema` mechanically can't do either. This
constraint is emergent, not chosen — a real risk for any future data model
that needs a NOT NULL invariant from day one (e.g. anything security- or
compliance-load-bearing).

## Target state already agreed (ADR 0002), not yet executed

Alembic becomes the single source of truth; `flask db upgrade` runs on
deploy; `reconcile-schema --dry-run` demotes to a CI-only drift detector
required to report zero. The **detector half is done** (`schema-drift` gate,
enforced with `--require-db` in CI). The **migration half — squashing 130+
revisions and changing the deploy command — is deliberately not done**,
correctly flagged in the ADR as irreversible-in-practice and needing a
maintenance window plus verified backup.

## One system of record (ADR 0008) — data-architect framing

Beyond the capabilities case: any new data-owning feature must name its
system of record *before* a table is added, and a copy must self-declare via
`source_table`/`source_id`. This is process discipline, not tooling —
nothing currently gates a PR from adding a 7th capability-shaped store other
than review vigilance. Worth a to-be recommendation: a lightweight gate that
flags a new model with a name/shape resembling an existing tracked concept
(fuzzy match against `unified_capabilities`'s known aliases) rather than
relying purely on reviewer memory.

## To-be direction (this lane's opinion)

1. Schedule the ADR 0002 migration (squash + `flask db upgrade` on deploy)
   as its own bucket, gated on a maintenance window — do not fold it into
   the module-layout consolidation work; they're independent and each
   deserves its own rollback plan.
2. Until that lands, any new column added anywhere in the codebase **must**
   be nullable or defaulted — this should be a written constraint on
   `builder`/`ml-engineer`/`nlp-engineer`'s task briefs whenever a migration
   is involved, not just tribal knowledge.

**Handoff:** to `tech-lead`, alongside the other three lane docs.
