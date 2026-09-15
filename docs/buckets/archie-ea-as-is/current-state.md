# As-Is: archie-oss current state

Author: `business-analyst` (step 1 of the as-is/to-be bucket). Every claim
below is sourced from a file in this repo, not inferred.

## What the platform is

Flask + Jinja2 + PostgreSQL, server-rendered, Tailwind/shadcn tokens,
Alpine.js. TOGAF 9.2 / ArchiMate 3.2 enterprise-architecture platform:
application portfolio, capability/value-stream modelling, an AI-assisted
solution-design journey, and an Architecture Review Board (ARB) governance
workflow. (`CLAUDE.md`)

## Personas actually tested end-to-end

11 canonical archetypes drive `tests/smoke/`: solution architect, enterprise
architect, business architect, ARB member, portfolio manager, CTO,
procurement, application manager, platform admin, security architect, data
architect (`tests/smoke/conftest.py::ARCHETYPES`). 8 persona journey tests
exist in `test_archetype_journeys.py`.

## Architecture debt, as the repo already documents it

**Duplicated code layout (ADR 0004).** Two parallel structures both live:
48 domains under `app/<domain>/` (legacy flat blueprints), 36 under
`app/modules/<domain>/` (canonical, self-contained). **7 domains exist in
both** — `account`, `admin`, `ai_chat`, `auth`, `dashboard`, `integrations`,
`monitoring` — selected at boot by `USE_*_GUARDRAILS` flags. Retiring the
duplicates is ADR 0004's stated next step, still open.

**Multiple systems of record for one concept (ADR 0008).** The canonical
example already measured in this repo: 6 stores answer "what capabilities
exist" (`business_capability` — 461 rows in production —, `capabilities`,
`unified_capabilities`, `enterprise_capabilities`, `archimate_capabilities`,
`technical_capabilities`). `unified_capabilities` is the designed system of
record but has no producer writing to it, so 8 route files reading from it
answer from an empty table. The `store-agreement` gate is ratcheted at **1**
— a deliberately-not-zero baseline recording this exact live disagreement.

**Tenant isolation gaps (ADR 0003).** Bulk UPDATE/DELETE are now tenant-
filtered mechanically via `do_orm_execute`. Remaining, documented exposure:
`Query.get()`/`Session.get()` bypass the tenant filter on an identity-map
*hit* (only filtered on a miss) — safe for normal per-request code, a real
gap for anything that loops over tenants inside one session (CLI commands,
scheduler, importers, tests).

**Three overlapping schema mechanisms, no single source of truth.**
`create_all()` (tables only), `flask reconcile-schema` (the actual drift
answer — nullable ADD COLUMN only), and 130+ Alembic revisions that are
**not run on deploy** (ADR 0002). Consequence: a non-nullable column or one
needing backfill breaks existing databases silently until `UndefinedColumn`
cascades into `InFailedSqlTransaction` for the whole page
(`docs/known-issues/schema-drift-on-existing-databases.md`).

**Two deploy pipelines, only one is actually live.** `scripts/deploy_verified.sh`
(bind-mount source-checkout, what production actually runs, verified
2026-09-08) vs. `deploy/deploy.sh` + immutable GHCR digests (real, committed,
**not** what's live). Two exist because two topologies exist; not yet
reconciled.

## Verification posture (measured, not estimated)

From `verification_baseline.json` (56 gates registered in
`scripts/verify.py::build_gates()`), the live ratchet numbers as of last
baseline update:

| Ratchet | Value | Reading |
|---|---|---|
| `evidence_contract` | 29 | behavioural changes/checkers missing evidence — real, tracked debt |
| `unregistered_checks` | 41 | `scripts/check_*.py` files with no `Gate(...)` entry — checks that exist but don't run |
| `role_gate_coverage` | 7 | delivery roles resolving to no verifier gate |
| `sidebar_links` | 27 | persona sidebar over its link budget |
| `shell_conformance` | 3 | pages off the platform shell |
| everything else tracked | 0 | undefined names, redefinitions, lint, design tokens, tenancy, air-gap, etc. — clean at last baseline |

Most gates read **0**, meaning the *mechanical* debt (lint, undefined names,
raw SQL tenancy, design tokens) is largely paid down — the real debt is
architectural (duplication, systems of record) and process (evidence
contract, unregistered checks), not code hygiene.

**CI health:** per prior session notes, main CI has been broadly red since
before 2026-09-15 (pre-existing rot across CVEs, SAST, tests, browser jobs)
— this needs re-verification (`gh run list`) before the to-be plan assumes
a green baseline to build from, not taken as current fact here.

## Known, documented, unfixed defects

`docs/known-issues/` — each is a *decision*, not a backlog item, per this
repo's own rule that a known-issue file should record something that
genuinely cannot be fixed in code:
- `conversation-tables-not-created-on-fresh-install.md` — chat history
  broken on every fresh install (no model for `conversation_threads`/
  `conversation_messages`)
- `schema-drift-on-existing-databases.md` — see above
- `smoke-suite-order-dependent-failures.md`
- `swallowed-catches-remaining.md`
- `unreachable-pages.md`
- `ai-chat-parameter-effects.md`

## AI surface (relevant to the AI/ML/NLP roster's future as-is pass)

No `src/` directory anywhere in this repo. AI/LLM code lives in
`app/modules/ai_chat/` (`llm_router.py`, `llm_service_impl.py`, persona
charters in `architect_persona_charters.py`) and `app/ai/`. Local embeddings
via `sentence-transformers` (required to boot, per `requirements.txt`) power
semantic search — there is no custom model-training pipeline in this repo.

## Open questions for the next pass (not resolved here)

- Is the GHCR/immutable-digest deploy pipeline (ADR 0004's sibling doc)
  still the intended target state, or has `scripts/deploy_verified.sh`'s
  bind-mount topology become the de facto permanent answer?
- What's the actual current CI status right now — needs `gh run list`
  before `tech-lead` sequences a to-be remediation plan on top of it.
- Of the 7 domains existing in both layouts, is there an agreed order for
  retiring the legacy side, or does each need its own bucket?

**Handoff:** to `solution-architect`, `data-architect`, `security-architect`,
`integration-architect` for parallel as-is deep-dives in their lanes,
pending your go-ahead.
