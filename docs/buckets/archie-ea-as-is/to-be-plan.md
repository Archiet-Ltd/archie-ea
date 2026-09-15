# To-Be: archie-ea remediation plan

Author: `tech-lead`, reconciling `solution-architect-lane.md`,
`data-architect-lane.md`, `security-architect-lane.md`,
`integration-architect-lane.md` against `current-state.md`.

## Reconciliation: where the four lanes agree, disagree, and reinforce

All four lanes independently converge on the same root cause pattern:
**this codebase accumulates parallel answers to one question** (two code
layouts, six capability stores, two deploy pipelines, three schema
mechanisms) faster than it retires them. Nobody recommended a fifth thing to
build — every lane recommended *retiring* something. That consistency is
itself a signal: the highest-value work available right now is
consolidation, not new capability.

**Live evidence from a concurrent bucket, not hypothetical:** while this
as-is/to-be analysis was in progress, the (separately tracked)
`sap-s4-interface-register` bucket hit exactly the failure mode
`data-architect-lane.md` warned about — a new nullable column
(`Plateau.initiative_id`) plus a unique partial index needed
`reconcile-schema` run by hand against the test database before tests would
pass, and the first attempt used the wrong env var (`DATABASE_URL` instead
of `TestingConfig`'s `TEST_DATABASE_URL`) and silently reconciled nothing.
This is ADR 0002's documented risk, freshly reproduced. It raises this
lane's priority rather than being a coincidence to set aside.

**One disagreement to flag, not silently resolve:** `solution-architect`
recommends sequencing layout consolidation (ADR 0004) *before* capability
store consolidation (ADR 0008), reasoning that some of the 7 overlapping
domains likely touch capability code paths. `data-architect` doesn't take a
position on this ordering directly but its own priority (ADR 0002 migration)
is orthogonal to both — it can run in parallel with either. Resolution below
sequences all three explicitly rather than picking one lane's view over
another's by default.

## Open questions from `current-state.md` — answered or still open

1. *Is the GHCR/immutable-digest deploy pipeline still the intended target
   state?* — **Still open.** `integration-architect-lane.md` recommends
   retiring one pipeline but does not have enough evidence to say which;
   this needs a decision from whoever owns the production deploy
   relationship, not an architecture-lane guess. Flagging for the user
   rather than picking one.
2. *What's the actual current CI status?* — **Answered.** Verified directly
   (`gh run list --limit 10`) during the security-architect pass: main CI is
   red on exactly 2 jobs right now (`Tests`, `Browser journeys`), not the
   broad, multi-surface red state from earlier session notes — someone has
   been actively fixing CI throughout the day (SAST, deps, broken-surfaces
   fixes visible in recent commit history). This is a meaningfully better
   starting point than assumed; the to-be plan below treats CI as "two
   known jobs failing," not "broadly red."
3. *Retirement order for the 7 duplicated domains?* — **Still open**, no
   evidence surveyed yet for which of the 8 is highest-risk/highest-value
   to retire first. Sequenced as its own investigation step below rather
   than guessed at.

## Ordered to-be plan

**Priority 0 (elevated by refuter review — not a Phase 0 item, above all
else):** `tests/test_tenant_isolation_matrix.py::test_every_unscoped_model_is_a_deliberate_decision`
is failing in CI right now: "1 model(s) carry organization_id without
TenantMixin and without a reason." This is a live tenant-isolation gate
regression, not architecture debt — per this repo's own ADR 0003 standard it
is a potential cross-tenant data-leak risk class and should be found and
fixed before anything else in this plan, independent of sequencing.

**Phase 0 (parallel, no dependencies, start immediately):**
- Fix the CI failures in `Tests` and `Browser journeys` — **corrected by
  refuter review: this is not one small, bounded item.** The `Tests` job's
  failure spans at least four unrelated root causes: the Priority-0 tenant
  gate regression above; a `jinja2.exceptions.UndefinedError: 'flask' is
  undefined` template-context bug (`test_procurement_utilization_honesty.py`,
  4 tests); two sidebar-link-budget overshoots that are likely one root
  cause (`test_sidebar_budgets.py` and `test_sidebar_render.py` — a new
  sidebar link landed without updating the pinned budget, unconfirmed
  without reading the actual diff); and an unrelated set-equality assertion
  failure in `test_tool_mutates_flag.py`. `Browser journeys` fails across 5
  distinct steps. Treat this as 4-5 independent fixes, not one, and size
  each on its own rather than as a single bounded ticket.
- Audit the 7 overlapping `app/<domain>/` vs `app/modules/<domain>/` pairs
  to produce a retirement order (answers open question 3) — read-only
  investigation, no code change yet.

**Phase 1 (provisionally sequenced — corrected by refuter review: this
ordering is a hypothesis pending Phase 0's audit, not a firm decision. If
the audit finds most of the 7 overlapping domains have no capability-code
overlap, step 1 may not need to precede step 2 for those domains):**
1. Retire the 7 overlapping legacy domains, one at a time, in the order
   Phase 0's audit produces — each is its own bucket with its own
   refuter-approved handoff, not one giant PR.
2. Once layout duplication is resolved, tackle the capability-store
   consolidation (ADR 0008): write the missing producer for
   `unified_capabilities`, then retire the 5 redundant stores in order of
   lowest-usage first.
3. **Independent of 1 and 2, on its own track:** schedule the ADR 0002
   schema migration (squash 130+ Alembic revisions, switch deploy to
   `flask db upgrade`) — needs a maintenance window and a verified backup,
   per the ADR's own migration plan. Does not block or get blocked by the
   layout/capability work.

**Phase 2 (after Phase 1's layout work, informed by its findings):**
- Decide and execute the deploy-pipeline consolidation (retire one of
  `scripts/deploy_verified.sh` vs. the GHCR pipeline) — deferred behind
  Phase 1 because the answer may depend on what production topology looks
  like once the module layout is no longer split.

**Ongoing, not gated on anything above:**
- `prompt-security-tester` runs one baseline adversarial pass against
  `app/modules/ai_chat/` now — this doesn't depend on any of the
  consolidation work and establishes a reference point before the next AI
  feature makes it harder to isolate new findings from old ones.
- The narrow `Query.get()` tenant-isolation gap (ADR 0003) stays open,
  documented, capped by its existing strict xfails — revisit only if a
  future feature (e.g. an importer) starts looping over tenants in one
  session.

## What this plan deliberately does not do

- It does not schedule new feature work. Every phase above is remediation.
- It does not resolve the deploy-pipeline choice (open question 1) — that's
  flagged for a decision-owner, not guessed.
- It does not bundle the `interface_register`/SAP-S4 bucket into this plan —
  that bucket is tracked separately, on its own branch, with its own
  refuter approvals; this to-be plan is about the platform's structural
  debt, not that feature's completion.

**Handoff:** to `refuter` for review of this plan's reasoning (not code —
there is no code in this bucket), pending your go-ahead per the standing
rule against unattended chaining.
