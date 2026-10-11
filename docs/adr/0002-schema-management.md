# ADR 0002 — Schema management: reconcile now, Alembic baseline next

- **Status:** Accepted. Steps 2-6 of the plan executed 2026-09-26 (see
  "Executed" below); step 7 and the removal of `init-db`'s legacy block remain.
- **Date:** 2026-07-30, amended 2026-09-26
- **Supersedes discussion in:** `docs/known-issues/schema-drift-on-existing-databases.md`

## Context

Three mechanisms currently define Archie's schema, and the one that looks
authoritative is not:

1. **`create_all()`** via `flask --app manage init-db` — creates missing *tables*.
   It has no concept of `ALTER TABLE ... ADD COLUMN`, so a model that gains a column
   never reaches an existing database.
2. **`flask reconcile-schema`** (`app/commands/reconcile_schema.py`) — diffs mapped
   models against live tables and issues `ADD COLUMN IF NOT EXISTS`. ADD-only, all
   nullable, never drops or retypes. Idempotent. Runs on container boot.
3. **`migrations/`** — Flask-Migrate/Alembic, 130+ revisions, multiple merge heads.
   **Deploys never run `flask db upgrade`.** It is effectively historical.

Additionally, `manage.py init_db` carries ~250 lines of hand-written idempotent
`ALTER TABLE` statements for pre-Alembic columns, marked legacy in its own comments.

### Why this is a problem beyond untidiness

- Every deploy **silently mutates production schema** (`init-db && reconcile-schema
  && gunicorn`). There is no plan, no review, and no record of what changed.
- The design **structurally forbids non-nullable columns**, because
  `reconcile-schema` can only add nullable ones. Nobody chose that constraint; it is
  an emergent property. A developer adding `nullable=False` gets a clean local
  install and a broken upgrade.
- Backfills have nowhere to live. `reconcile-schema` adds a column; it cannot
  populate it.
- The documented incident is the second-order effect: 47 drifted columns across ~20
  tables, one `UndefinedColumn` aborting the transaction and cascading into
  `InFailedSqlTransaction` for every later query, 500-ing whole pages.

## Decision

**Target state:** Alembic is the single source of truth; `flask db upgrade` runs on
deploy; `reconcile-schema --dry-run` is demoted to a **CI drift detector that must
report zero**, rather than a runtime mutator.

**Now (this change):** the detector half only.

- `scripts/verify.py --gate schema-drift` runs `reconcile-schema --dry-run` and fails
  when drift is present. CI runs it with `--require-db`, so it cannot pass by being
  skipped.
- `reconcile-schema` stays in the boot sequence. Removing it before Alembic is
  trustworthy would leave existing deployments with no self-heal.

**Deliberately not done here:** squashing the 130+ revisions and changing the deploy
command. Both are irreversible-in-practice and need a maintenance window plus a
verified backup. Doing them unannounced from a code change would be reckless. The
plan is below so the next person does not have to re-derive it.

## Migration plan (requires a maintenance window)

1. **Freeze.** Announce that no new Alembic revisions land during the squash.
2. **Resolve heads.** `flask db heads` — expect several. Merge to one, or discard
   the pre-baseline history entirely in step 3.
3. **Generate the baseline.** Against a database built by
   `init-db && reconcile-schema` from current models:
   ```
   flask --app manage db revision --autogenerate -m "baseline: schema as of <date>"
   ```
   Review the emitted revision by hand. Autogenerate does not see everything —
   server defaults, index differences, and the `extend_existing` duplicate-mapped
   tables all need checking.
4. **Archive old revisions.** Move `migrations/versions/*` to
   `migrations/versions/_archive_pre_baseline/` (kept for forensics, excluded from
   the chain). The baseline becomes the single root.
5. **Stamp existing databases.** On every deployed environment:
   `flask --app manage db stamp <baseline_rev>` — records the revision without
   re-running DDL. **Verify against a restored production backup first.**
6. **Switch the deploy command** in `docker-compose.yml` from
   `init-db && reconcile-schema && gunicorn` to
   `db upgrade && gunicorn`, keeping `reconcile-schema --dry-run` as a boot-time
   *assertion* that logs loudly on drift rather than repairing it.
7. **Lift the nullable-only constraint.** Document that `nullable=False` columns are
   now permitted provided the revision includes a backfill.

### Exit criteria

- `flask db upgrade` on a copy of production is a no-op.
- `reconcile-schema --dry-run` reports zero drift on every environment.
- CI's schema-drift gate has been green for one full release cycle.

## Executed (2026-09-26)

What was done, and where it differs from the plan above:

- **Baseline, anchored to the models rather than frozen DDL.**
  `migrations/versions/20260926_baseline.py` calls `ensure_baseline_schema`
  (`app/commands/schema_migrations.py`): the same `create_all(checkfirst=True)`
  `init-db` runs, plus the few columns and indexes only `init-db`'s hand-written
  DDL creates (`UNDECLARED_COLUMNS`, `UNDECLARED_INDEXES`, now shared by both).
  A frozen autogenerated dump was rejected: `init-db` runs before the upgrade on
  every deploy, so a dump would either fail with "already exists" or need the
  same checkfirst logic, and it would be a second definition of the schema to
  keep in step with the models. On a database `init-db` built, the baseline
  changes nothing; on an empty database it builds the same columns, indexes and
  constraints `init-db` would (`tests/test_schema_migrations.py` compares them).
  The baseline has no down step: undoing it would drop every table.
- **History archived, not squashed.** The 108 pre-baseline revisions moved to
  `migrations/versions/_archive_pre_baseline/`, which Alembic does not load. The
  directory had no `env.py`, so the chain had never been runnable from this
  tree; `migrations/env.py` is new and runs each revision in its own transaction.
- **No manual stamp step.** `flask schema-upgrade` replaces plan step 5. On an
  unversioned database it runs the baseline (a no-op) and records it. On a
  database recorded at an archived revision it re-stamps the baseline (record
  only, no DDL) and prints the old id so it can be put back. It holds a
  PostgreSQL advisory lock so two deploys cannot upgrade at once, acquired
  with a bounded poll (`acquire_upgrade_lock`, 60s default) rather than the
  blocking `pg_advisory_lock`: a stuck or crashed holder stops the deploy with
  `SchemaUpgradeLocked` instead of hanging it forever.
- **Deploy order** (`scripts/database/deploy-schema.sh`, and every CI job that
  builds a schema): `init-db`, then `schema-upgrade`, then `reconcile-schema`.
  A failed revision stops the deploy before new code starts; PostgreSQL DDL is
  transactional, so the failed revision leaves nothing half-applied.
- **The image ships the revisions.** `.dockerignore` no longer excludes
  `migrations/versions/`: `schema-upgrade` runs inside the deployed image, so
  a revision the image does not carry can never be applied — the build stops
  there rather than the deploy silently no-op'ing or stamping a stale head.
- **`migrations/env.py` reuses the app's own logging.** It does not call
  Alembic's default `fileConfig()`; `schema-upgrade` runs inside the already-
  booted Flask app (`@with_appcontext`), whose logging is already configured
  (`app/services/core/logging_config.py`), and reconfiguring the root logger a
  second time from `alembic.ini` would fight that.
- **`reconcile-schema` stays applying, as the drift detector.** It still adds
  nullable columns models gain (every consolidation's expand step relies on it)
  and lists each one; the `schema-drift` gate still runs its dry run. What it
  cannot do - relax or tighten NOT NULL, retype or widen, add a constraint after
  a backfill - is now a revision.
- **Expand and contract.** Revisions use the helpers in
  `app/commands/schema_migrations.py`: `relax_not_null` and `widen_varchar`
  (expand), `tighten_not_null` and `narrow_varchar` (contract, and the down steps
  of expands). Each inspects the live column first, so re-running is a no-op, and
  each contract step raises `ContractBlocked` rather than discard a value. A
  change follows: expand in one revision; backfill with a `flask` command run one
  organisation at a time; contract in a later revision once the backfill is
  measured complete. A down step that would lose data refuses and changes
  nothing; the fix is then forward, in a new revision.
- **Worked examples**, shipped as revisions for later changes to consume:
  `20260926_relax_owner_app` (`application_owners.application_id` allows NULL,
  so one ownership record can point at an element that is not an application)
  and `20260926_widen_element_name` (`archimate_elements.name` from 100 to 500
  characters). Both are metadata-only on PostgreSQL. `ApplicationOwner.application_id`
  and `ArchiMateElement.name` (both definitions, `app/models/models.py` and the
  `APP_FAST_INIT` one in `app/models/archimate_core.py`) are updated in the same
  change, so the models never disagree with a database these revisions have run
  against — a divergent model would pass the live schema but rebuild the wrong
  one wherever tests or a from-scratch deploy use `create_all()`. The behaviour
  each relaxed/widened column enables (an ownership record with no application;
  an element name over 100 characters) still waits on the changes that use it;
  this change only makes the column and its model agree.

### Deploy and rollback

- Deploy: unchanged command (`scripts/deploy_verified.sh` or the production
  deploy workflow). The schema-deploy container now logs
  `schema-upgrade: at 20260926_widen_element_name (was ...)`.
- Code rollback to a commit before this change is safe without touching the
  schema: both example revisions only expand, and older code never reads
  `alembic_version`.
- Schema rollback, only while this code is deployed:
  `flask --app manage db downgrade 20260926_baseline`. It refuses, changing
  nothing, if a name longer than 100 characters or an ownership row without an
  application exists by then.

## Consequences

- Until step 6, the drift gate catches *model-vs-database* divergence in CI but
  production still self-mutates at boot. That is a genuine reduction in risk, not a
  fix.
- New columns must stay nullable (or carry a server default) until step 7. This is
  now written down rather than being folklore.
- `manage.py init_db`'s hand-written `ALTER TABLE` block should be deleted at step 6;
  the baseline supersedes it. Do not add to it before then.

## Alternatives considered

- **Delete `migrations/` entirely, commit to `reconcile-schema`.** Honest about
  current practice and simpler, but permanently forfeits non-nullable columns,
  backfills, data migrations, and any reviewable record of schema change. Rejected.
- **Keep both indefinitely.** The status quo. Rejected: two sources of truth means
  neither is trusted, and the reconcile path silently constrains modelling.
