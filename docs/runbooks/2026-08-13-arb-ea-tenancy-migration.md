# Runbook: ARB/EA tenancy migration (Wave 4)

Deploys the tenant-scoping of ARB (Architecture Review Board) and EA-workflow
governance data. In this wave, Phase A (nullable `organization_id` columns +
backfill CLI) and Phase B (`TenantMixin` filtering) are commits on the **same
branch**, so there is only **one deploy** — but the ordering invariant below
still applies and this runbook documents it precisely.

## Background

14 models across `app/models/architecture_review_board.py` and
`app/models/workflow_models.py` gained a nullable `organization_id` column
(Task 1). A CLI command, `flask --app manage backfill-arb-ea-tenancy`, derives
each row's org from its FK parents (Task 2). 11 of the 14 are per-tenant and
now carry `TenantMixin`, which auto-injects `WHERE organization_id =
g.current_org_id` on every ORM query (Task 3). The other 3 —
`arb_governance_standards`, `arb_workflow_stages`, `ea_workflow_definitions` —
are global reference/catalog data and are intentionally **not** TenantMixin;
their org column stays unused and NULL forever.

**Per-tenant tables (11 — must reach 0 NULL org before Phase B is live):**
`arb_review_items`, `arb_review_comments`, `arb_capability_impacts`,
`arb_board_members`, `arb_exceptions`, `arb_documents`, `arb_audit_logs`,
`ea_workflow_instances`, `ea_workflow_step_executions`,
`ea_workflow_schedules`, `ea_workflow_notifications`.

**Global-reference tables (3 — never assigned, never filtered):**
`arb_governance_standards`, `arb_workflow_stages`, `ea_workflow_definitions`.

## The ordering invariant

`TenantMixin`'s filter is `WHERE organization_id = g.current_org_id`. A row
with `organization_id IS NULL` never matches that predicate for **any**
org — so if the filtering code (Phase B) is live before a row has been
backfilled, that row is invisible to everyone. Concretely:

> **The backfill must complete before the TenantMixin filter starts being
> evaluated against real requests.**

Because Phase A and Phase B ship in the same commit here, the moment the new
code boots, `reconcile-schema` adds the columns (still all NULL) **and** the
TenantMixin filtering is already compiled into the model classes. There is a
window — from the instant gunicorn starts serving traffic to the instant the
backfill finishes — during which every per-tenant ARB/EA list page renders
**empty** for every org (not wrong-org data, just nothing: the `= org_id`
predicate matches zero rows). This is expected, self-limiting, and not a
data-loss event — the rows are still on disk, invisible only until the
`organization_id` column on each is populated.

**Mitigation — minimize the window:** run the backfill as the very next
command after the deploy's boot completes, before announcing the deploy or
directing users to ARB/EA pages. The steps below are written in that order:
deploy → wait for healthy → backfill dry-run → backfill real → verify 0 NULL
→ only then treat the deploy as done.

## Pre-deploy gate

Run the full static gate locally before pushing anything to the droplet —
never a hand-picked subset, even for what looks like a small change:

```bash
python scripts/verify.py --tag static     # must be 22/22, 0 skipped
```

## Phase A+B deploy (single branch, two logical phases)

All commands run on the droplet (`ssh root@134.122.105.56`), repo at
`/root/archie-ea`, unless noted. Prod boot runs a **9-command serial CLI
chain** (`init-db && reconcile-schema && backfill-... (x7) && gunicorn`)
before gunicorn starts — a clean restart legitimately takes **8-12 minutes**
before `/health` responds. Poll with a 15-20 minute bound; do not diagnose a
hang before then (check `/proc/loadavg` ~1.0 and no `WORKER TIMEOUT` in logs
first if in doubt).

### Step 1 — deploy the branch

```bash
ssh root@134.122.105.56 "cd /root/archie-ea && git fetch && git checkout <this-branch> && git log --oneline -3"
scripts/deploy_verified.sh <this-branch>
```

Use the deploy script, not a bare `docker compose up`: it takes a backup, builds, runs the schema
steps while the old server keeps serving, and only then replaces the server (see
`CLAUDE.md`, "Deploying to production"). The 8-12 minute figure above predates this; the gap
is now the new server's start-up only.

`reconcile-schema` (part of the boot chain) adds the 14 nullable
`organization_id` columns on this boot — non-destructive, ADD-only.

### Step 2 — wait for healthy

```bash
ssh root@134.122.105.56 "for i in $(seq 1 60); do curl -sf http://localhost:5000/health && break; sleep 15; done"
```

Confirm via logs that boot actually finished (`Listening at: http://0.0.0.0:5000`), not just that
some earlier command in the chain returned.

### Step 3 — verify the columns landed, all NULL

```bash
ssh root@134.122.105.56 "docker compose exec -T postgres psql -U postgres -d archie -c \"
SELECT 'arb_review_items' t, count(*) total, count(organization_id) has_org FROM arb_review_items
UNION ALL SELECT 'arb_review_comments', count(*), count(organization_id) FROM arb_review_comments
UNION ALL SELECT 'arb_capability_impacts', count(*), count(organization_id) FROM arb_capability_impacts
UNION ALL SELECT 'arb_board_members', count(*), count(organization_id) FROM arb_board_members
UNION ALL SELECT 'arb_exceptions', count(*), count(organization_id) FROM arb_exceptions
UNION ALL SELECT 'arb_documents', count(*), count(organization_id) FROM arb_documents
UNION ALL SELECT 'arb_audit_logs', count(*), count(organization_id) FROM arb_audit_logs
UNION ALL SELECT 'ea_workflow_instances', count(*), count(organization_id) FROM ea_workflow_instances
UNION ALL SELECT 'ea_workflow_step_executions', count(*), count(organization_id) FROM ea_workflow_step_executions
UNION ALL SELECT 'ea_workflow_schedules', count(*), count(organization_id) FROM ea_workflow_schedules
UNION ALL SELECT 'ea_workflow_notifications', count(*), count(organization_id) FROM ea_workflow_notifications;
\""
```

Expect `has_org = 0` on every row at this point — data is global-but-intact,
not yet filtered incorrectly, because the backfill has not run yet. (The
window described above is open from here until Step 6 completes.)

### Step 4 — backfill dry-run (immediately — do not delay)

```bash
ssh root@134.122.105.56 "cd /root/archie-ea && docker compose run --rm --no-deps --user root \
  -v /root/archie-ea:/app server sh -lc \"/venv/bin/flask --app manage backfill-arb-ea-tenancy --dry-run\""
```

Inspect the per-model report. Each of the 11 per-tenant models prints three
buckets: **derivable** (org resolved from an FK parent — safe to write),
**orphan** (no derivable org — a genuine per-tenant row with nothing to
derive from), **global** (only for the 3 reference tables — always
untouched, not a concern).

**STOP condition:** if any per-tenant model reports a nonzero **orphan**
count, do **not** proceed to Step 5. An orphan is a governance row (an ARB
review item, exception, workflow instance, etc.) that cannot be safely
assigned to any tenant automatically — guessing would mis-assign a
governance record to the wrong org, which is a tenant-isolation breach, not
a cosmetic bug. Surface the orphan list to a human for a manual `--org-id`
decision per row/table before continuing. Do not deploy Phase B filtering
(it already shipped in this same commit — see "if you must stop" below)
while unresolved orphans exist.

*If you must stop here*: because Phase A and B are one branch, stopping
means either (a) resolving orphans immediately with a human-directed
`--org-id N` run scoped to the specific orphaned rows, or (b) rolling back
to the pre-deploy branch (see Rollback) until a decision is made — do not
leave the site serving with unresolved orphans and TenantMixin filtering
live, since those specific orphan rows will be invisible to every org until
resolved.

### Step 5 — backfill for real

```bash
ssh root@134.122.105.56 "cd /root/archie-ea && docker compose run --rm --no-deps --user root \
  -v /root/archie-ea:/app server sh -lc \"/venv/bin/flask --app manage backfill-arb-ea-tenancy\""
```

Exits non-zero if any genuine per-tenant orphan remains and no `--org-id` was
given — treat a non-zero exit here the same as the Step 4 STOP condition.

### Step 6 — verify 0 NULL on the 11 per-tenant tables

Re-run the Step 3 query. Every per-tenant table must now show
`has_org = total` (0 remaining NULLs). The 3 global-reference tables
(`arb_governance_standards`, `arb_workflow_stages`, `ea_workflow_definitions`)
are expected to stay at `has_org = 0` forever — that is correct, not a defect.

Once this is confirmed, the empty-window described above is closed: every
per-tenant row now has an org, so the TenantMixin filter (already live since
Step 1) shows the correct, complete set of rows to each org. **Only now**
announce the deploy as complete / direct users to ARB/EA pages.

## Rollback

```bash
ssh root@134.122.105.56 "cd /root/archie-ea && git checkout <previous-branch> && docker compose restart server"
```

Safe at any point in this sequence:
- The `organization_id` columns are nullable — old code that doesn't know
  about them ignores them harmlessly.
- Any values the backfill already wrote are harmless under old code too —
  the old code has no TenantMixin filter reading that column, so a populated
  `organization_id` is inert, not disruptive.
- Rolling back does **not** need to undo the backfill.

## Post-deploy verification — authenticated two-org check

After Step 6, confirm the actual isolation behaviour end-to-end, not just the
column counts:

1. Log in as a user in org A. Open an ARB review list and an EA workflow
   instance list. Confirm only org A's rows are visible (not empty, not
   org B's rows).
2. Log in as a user in org B. Confirm the equivalent, disjoint set.
3. Spot-check one of the 3 global-reference screens (e.g. governance
   standards) from both orgs — confirm the same shared catalog is visible to
   both (this is correct: they are intentionally unscoped).
4. Confirm no 500s / `InFailedSqlTransaction` in `docker compose logs server`
   for the ARB/EA blueprints during this check.

This is the exact regression Wave 3 found for other tenant tables — the
governance-data equivalent of it must not exist after this deploy.
