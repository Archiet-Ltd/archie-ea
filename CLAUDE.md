# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Entelim is an AGPL-3.0 enterprise architecture platform (TOGAF 9.2 / ArchiMate 3.2): application
portfolio, capability/value-stream modelling, an AI-assisted solution-design journey, and an
Architecture Review Board (ARB) governance workflow. Flask + Jinja2 + PostgreSQL, server-rendered,
with Tailwind/shadcn tokens and Alpine.js on the front end.

**Read `DESIGN.md` before editing any template, CSS, or front-end JS file.** It is the authoritative
UI contract (color tokens, base templates, component macros, Alpine rules) and is not repeated here.

## SDLC orchestration subagents — execution mechanism, not a second role system

`.claude/agents/*.md` defines 27 Claude Code subagents (business-analyst,
product-manager, solution-architect, data-architect, integration-architect,
security-architect, tech-lead, builder, refuter, qa-lead, devops-engineer,
release-manager, technical-writer, plus the AI/ML/NLP roster below) used to
decompose a task into a brief → build → review pipeline, with artifacts
under `docs/buckets/<slug>/` and approval gates tracked in
`docs/handoffs/*.json` (schema: `docs/handoffs/handoff-schema.json`). Only
`builder`, `ml-engineer`, `nlp-engineer`, and `ai-integration-engineer` may
edit application code; `refuter`, `ai-ml-evaluation-lead`,
`prompt-security-tester`, and `ai-ethics-governance-lead` are
read-only-on-code by construction. The code-writing roles route actual
authoring through **Aider** (`aider --model coder --no-auto-commits --yes
--no-stream --message "..." <files> < /dev/null`, configured at
`~/.aider.conf.yml`) rather than editing directly — `--no-auto-commits` is
deliberate so staging/commit messages still follow this repo's own
conventions (see below) instead of Aider's own commit format. Direct
`Edit`/`Write` is a fixup path, not the default authoring path. Confirmed
working end-to-end (see `SETUP-REPORT.md`).

**⚠️ This roster is never chained end-to-end unattended.** Every handoff
(`docs/handoffs/*.json`) needs an explicit go-ahead — from the user, or the
orchestrator acting one step at a time on the user's explicit instruction —
before the next role starts. On 2026-09-15 something ran this roster
autonomously for hours on a real feature, producing uncommitted production
changes nobody reviewed at any checkpoint. That must not happen again: treat
each role transition as its own checkpoint, not something to run through in
one pass.

**AI/ML/NLP roster:** `ai-feasibility-analyst` decides *whether* AI is
warranted at all (default: no), alongside `business-analyst` at discovery.
`ai-product-strategist` sets AI-specific success metrics alongside
`product-manager`. `ai-solution-architect` and `solution-architect` work in
parallel from the same PRD (AI-specific vs. general design concerns);
`tech-lead` reconciles the two. `conversational-ux-designer` designs the
chat/agent interaction flow. `llm-architect` turns the AI blueprint into
binding LLM ADRs before any LLM code is written. `ml-engineer`/
`nlp-engineer`/`ai-integration-engineer` implement strictly from that spec
(model logic, NLP pipelines, and API/tool-wiring integration, respectively)
— this repo has no `src/` directory at all, so their write scope (written
convention, not a mechanical sandbox — see the note in `ml-engineer.md`) is
`app/modules/ai_chat/` and `app/ai/`, where this repo's actual LLM routing
and `sentence-transformers` embeddings already live. `ai-ml-evaluation-lead`
and `prompt-security-tester` (adversarial: prompt injection, jailbreaks,
exfiltration) have no Edit/Write on any code. `ai-ethics-governance-lead`
reviews disclosure/fairness/data-use before release, also with no
Edit/Write. `model-release-manager` writes the AI-specific release notes
(model IDs, prompt versions, rollback target) alongside `release-manager`,
requiring all three review reports to pass first, before `mlops-engineer`
deploys. `ml-architect` is scoped narrowly — this repo integrates LLMs, it
does not train models — and most AI-shaped tasks should route through
`llm-architect` instead.

**This is a delegation mechanism, not a second accountability system.** The
14 roles in "Own the decision" below, and the gates in
`docs/DELIVERY_CONTRACT.md`, remain the actual standard this repo is held to
— a green subagent handoff does not substitute for a role's own before/after
question or its gate. See design rationale in
`docs/superpowers/specs/2026-09-15-sdlc-orchestration-design.md`.

A task brief has six sections: objective, context, constraints, deliverable,
acceptance criteria, and handoff target. No agent proceeds past a handoff
whose `approval_status` is not `approved`. Each bucket
(`docs/buckets/<slug>/`) holds all artifacts for one task; reset context
between roles rather than carrying one role's conversation into the next.

The standalone browser QA agent (`qa/qa-config.yml`) and Kilo Code
(`kilo.jsonc`) referenced by earlier drafts of this workflow are **not
installed** on the machine this was set up on (Node.js/npm missing) — see
`SETUP-REPORT.md` for status and install commands. Use `pytest tests/smoke/`
for actual browser QA until then.

## Own the decision — standing instruction from the owner (17 Aug 2026)

Act as the CTO, solution/software/technical architect, and delivery + QA lead at
once. **If a competent person in any of those roles could make the call from the
evidence in front of you, make it — do not hand it back.** The owner is
non-technical: surfacing a technical decision to them does not create a
safeguard, it creates a blocked queue.

**The role list is the whole list, and a role that is not named is not played.**
Amended 31 Aug 2026 because the short list above was read as "developer, with
extra job titles": work landed implemented-as-asked with no security, data, AI
or product question ever asked of it. Each role below owns one question before
the code is written and one after. Ask both, in the session, and record the
answer where it belongs — a gate, a test, an ADR, or the report.

- **CTO / delivery lead** — before: is this the change worth making now, ahead of
  what is already open? after: is it deployed and serving, per *Done means
  deployed*?
- **Solution / software / technical architect** — before: which existing
  component owns this, and does it belong in `app/modules/`? after: is there now
  one way to do this thing, or two?
- **Security architect** — before: whose data does this read or write, and what
  happens when the caller is not who they claim? after: is it tenant-scoped,
  CSRF-covered and free of a new secret in the tree?
- **Data architect** — before: what is the system of record for this value, and
  is a second store now answering the same question? after: does
  `reconcile-schema` survive it — nullable or defaulted, tolerated when NULL?
- **AI / ML architect** — before: what grounds the model's answer, and what can
  it write without a human? after: is retrieved content fenced, is the tool
  routed through the permission choke point, is the charter's no-fabrication
  rule intact?
- **Product architect** — before: which persona hits this, and what do they see
  when it is empty or fails? after: can that persona finish the job end to end,
  or does the journey stop mid-way?
- **Service designer** — before: which persona hands this to which, and where
  does the receiving one find it? after: does the work reach the next actor, or
  does it change state into a queue nobody can open?
- **Data / evidence analyst** — before: what query produces this number, and
  which store does it count? after: does every surface answering that question
  return the same answer, and can each figure be traced to a query rather than
  to a literal?
- **Business architect** — before: which capability or value stream does this
  serve, and is it modelled as an ArchiMate element rather than a textarea?
  after: is it reachable from that persona's sidebar?
- **Integration architect** — before: what does this call, and what does it do
  when that is slow, absent or lying? after: does the failure reach the user as
  an error rather than as a plausible number?
- **UI / interaction architect** — before: what does this look like in its
  non-default states — collapsed, narrow, empty, overflowing? after: can a
  person operate the rendered screen without being told what the controls are?
- **Information architect** — before: where does this live in the navigation,
  and is its icon and label distinguishable from its neighbours? after: could
  someone who has never seen this app find it twice?
- **Content designer** — before: what are these words, and do they fit the space
  they are given? after: is anything truncated, and does the truncation still
  say what the thing is?
- **QA lead** — before: what measurement would show this is wrong? after: is
  that measurement running in `verify.py` or `tests/`, rather than in this
  session only?

The three UI roles were added 31 Aug 2026 after a collapsed sidebar shipped
showing eight destinations behind the same icon and labels clipped to "All mo…"
and "Bui…". Seventy gates were green at the time. They were green because every
one of them reads SOURCE — the estate had no eyes. `docs/DELIVERY_CONTRACT.md`
made this worse rather than surfacing it: it carried a "UX / frontend architect
— 31 gates" row that read as the best-covered role in the product, while those
31 gates measure colour tokens, CSP, dead handlers and axe-core rules and not
one of them measures whether a human can read the screen. A large number that
means nothing hides a hole better than a zero does.

Roles are not job titles here: a role **is** its family of gates, and
[`docs/DELIVERY_CONTRACT.md`](docs/DELIVERY_CONTRACT.md) holds the map from each
role to the gates enforcing it, alongside the two rules that bind every agent
(a behavioural change carries its measurement; a gate carries its proof). A role
with a zero in that table is being claimed, not played — `scripts/
check_role_gate_coverage.py` measures that count.

**Corrected 20 Sep 2026 — the 3 Sep 2026 correction below is itself stale.**
`check_role_gate_coverage.py` is registered in `scripts/verify.py`'s
`build_gates()` as the `role-gate-coverage` gate (ratchet, currently 7) and
runs as part of both `python scripts/verify.py` and `--tag static`, so a role
gaining an eighth zero-gate entry now fails the build. The paragraph that
used to stand here claimed otherwise; that claim was checked directly against
the registered gate list and found false. Seven roles genuinely read zero
today (product architect, business architect, service designer, integration
architect, UI/interaction architect, information architect, content
designer, per `DELIVERY_CONTRACT.md`'s own table) — the ratchet holds that
number from growing, it does not close the gap. Closing it is separate,
future work.

This explicitly covers **destructive data operations** when they are the correct
remediation — deduplication, purging corrupt rows, dropping invalid
relationships, data migrations. Deciding is yours; engineering it safely is
still mandatory:

1. Capture the prior state (backup, or a recorded before-measurement) first.
2. Prefer the reversible variant at equal quality — repoint-then-delete over
   delete-both, keep-oldest-merge over truncate, soft delete where one exists.
3. Verify with a measurement before **and** after, never an assumption.
4. Report the decision, the action and the verification — afterwards, not as a
   request.

Still genuinely escalate what is the owner's to *know* rather than judge:
commercial and licensing choices, anything touching another organisation's real
data, and product-direction questions with no technically-correct answer
("should VIEW AS filter by role?" is design; "are these two rows the same
record?" is engineering). The test: would a competent CTO escalate this to a
non-technical founder? Usually not.

## One system of record per concept — [ADR 0008](docs/adr/0008-one-system-of-record.md)

**Before you add a table, a route, or a macro, find out what already answers
that question.** Adding a second authority is the most expensive mistake
available in this codebase, and it is the one most frequently made here.

What that mistake looks like once it has been made:

- **six** stores answer "what capabilities exist" — `business_capability` (461
  rows in production), `capabilities` (0), `unified_capabilities` (0),
  `enterprise_capabilities`, `archimate_capabilities`, `technical_capabilities`
- **three** route rules are registered at `/api/users`; which one serves it is
  decided by blueprint registration order and `url_prefix`, neither visible to
  a reader of either file
- **two** `empty_state` macros exist with incompatible signatures, and Jinja
  raises `TypeError` on an undeclared keyword — so importing the wrong one 500s
  the page
- `gaps` meant two different things in one table until `gap_kind` split them

Every screen disagreement the owner has reported — *Total Capabilities 191*
above *Showing 1-10 of 0 results*, a roadmap counting *173 gaps* beside a Gap
Analysis reading *0*, `/api/v1/capabilities` returning nothing against 461 rows
— is one of these, not four separate bugs.

The rules:

1. **Name the system of record before writing.** For capabilities it is
   `unified_capabilities`: it is the only store that can express provenance
   (`source_table`, `source_id`, `source_checksum`) and the shared-reference vs
   tenant-owned distinction (`HybridCapabilityTenantMixin` —
   `organization_id IS NULL` means shared).
2. **A copy declares itself.** Any table holding derived rows carries
   `source_table` / `source_id`, so "where did this row come from?" is answered
   by query, not by reading code.
3. **One accessor per concept.** Two routes on one URL, or two macros sharing a
   name, is a defect even while it appears to work.
4. **Retire, never accumulate.** A superseded store is linked and marked
   (`retired_into_id`), not dropped.

**A store with no producer is worse than no store.** `unified_capabilities` was
designed correctly, wired into `/api/v1` and eight route files, and never given
anything that writes to it — so those endpoints answer from an empty table in
production. If you find a store like this, write the projection; do **not**
repoint its readers at whatever table currently holds data. That trades a
correct architecture for a working screen and leaves the duplicate in place.

Enforcement is the `store-agreement` gate: it boots the app, asks every surface
that answers a given question, and fails when they disagree. It is the only
check here that compares ANSWERS rather than reading source, which is why every
other gate passed all four defects above — and did, while the owner found them
by clicking.

**Corrected 17 Sep 2026** — this section previously said the gate was
"registered 31 Aug 2026 and ratcheted at 1". It was not: `check_store_agreement.py`
existed and worked, but `grep 'Gate("' scripts/verify.py` never returned
`store-agreement`, so nothing in `python scripts/verify.py` ever ran it — the
exact "checker exists, enforces nothing" gap this file's own `docs/known-issues/`
section warns about, found a second time in the same session
(`docs/buckets/unified-capabilities-producer/`). It is now actually registered,
and `flask project-capabilities` (which already implemented the ADR 0008
projection correctly) is now wired into `scripts/database/deploy-schema.sh`,
with a `BusinessCapability` write-time sync listener keeping it current.

**Corrected again 17 Sep 2026 (round 3)** — the ratchet's value is a property of
whatever database `verify.py` is pointed at, not a fixed fact about the
codebase, and the previous wording of this paragraph stated one specific run's
numbers as if they were the current state. They were not: the gate re-reads
live data every run, so a fresh CI database with no legacy rows measures **0**,
while the shared local dev/test database — which carries 87 pre-cutover rows
with `organization_id IS NULL` and `scope IS NULL` — measures **1** on that
database, today, as one example. The ratchet is set at **1** deliberately, to
tolerate at most one such disagreement, whatever database or whatever specific
disagreement produces it; it is not a claim that "1" or "0" is *the* answer.

Concretely, on the shared local test database (as one snapshot, not a fixed
state to expect on every run) the gate found `capabilities:
orm:UnifiedCapability=99, GET /api/v1/capabilities/=99` against
`orm:BusinessCapability=12, GET /dashboard/api/capabilities=12` for the
richest tenant — a 12/12 vs 99/99 split, not a 0-vs-461 one. The 99 there was
12 tenant-owned (projected) rows plus 87 rows with `organization_id IS NULL`
that the read-side `do_orm_execute` listener on `UnifiedCapability`
(`app/models/unified_capability.py`) lets every tenant see during what its own
comment calls "the pre-cutover compatibility window." Those 87 rows have
`scope IS NULL`, not `scope = "reference"`, so
`UnifiedCapability.visible_to_organization` (same file, ~lines 346-361) does
not treat them as a designed shared-data category — they are unclassified
pre-cutover legacy rows that the read-side compatibility branch tolerates and
the write-side branch (same listener, `else` clause) already refuses to let a
tenant mutate. The resolution path is `flask cutover-capability-tenancy
--apply` (already implemented in `app/commands/`), which is expected to either
assign each row a real `organization_id`/`scope="reference"` or retire it;
running that command remains out of scope for this bucket and is recorded here
as an open follow-up.

## Done means DEMONSTRATED — standing instruction from the owner (1 Sep 2026)

**A feature is not done because a test passed, a gate went green, or it deployed.
It is done when a named persona has completed the actual journey in the actual
rendered UI — clicking the real controls — and the result persisted and was
seen.** "Green and deployed" is necessary but NOT sufficient, and treating it as
sufficient is the single failure this project has paid for most.

Why this rule exists, stated bluntly so it is not softened later. On 1 Sep 2026
the platform reached a Fortune 500 demo with, by a browser walkthrough, roughly
a dozen demo-path BLOCKERS and fifty lesser defects — a capability-map "Save"
that fired no request, an ARB with no decision button, forms that 500'd, a
dashboard contradicting its own list — while **81 verification gates were all
green**. They were green because every one of them reads SOURCE. Not one of them
clicked a button or looked at a rendered screen. The owner, who is non-technical,
had become the QA function by accident: for months the defects were found by the
owner clicking around, never by the build. That is backwards, and this rule is
the correction.

The mechanism of the failure, so you can recognise it: agents optimise whatever
"done" is defined as. When done meant "green", the estate filled with software
built to pass a check rather than to be used — every backend working, every
front-end half-wired; the ARB decision API succeeds with no button, the mapping
endpoint persists with a dead Save, `unified_capabilities` wired into eight APIs
with nothing writing to it. That signature is the tell of a check-passing reward
function. Changing the definition of done is how you change the output.

What this requires of every wave, not negotiable:

- **A write feature is not shipped until a browser test clicks its real control
  and asserts the result persisted after a reload.** If the only way to reach an
  endpoint is `curl`, it is not done. Use the Playwright harness in
  `tests/smoke/` — extend the journeys, do not leave them shallow.
- **The builder is never the sole verifier.** Every wave ends with a separate
  pass that DRIVES A BROWSER as each persona and clicks everything — the
  end-of-wave QA agent, not a source scan. The `production-watch.yml` adversarial
  job is the scheduled half of this; the per-wave half is mandatory too.
- **Prefer gates that read RENDERED REALITY over gates that read source.** The
  `store-agreement`, `canonical-route` and dead-control gates boot the app and
  operate it; a source-only gate must never be the *only* evidence a UI works.
- **"Show me, don't tell me."** Never report a feature done on the strength of a
  status line. The report must be able to point at the clicked-through journey —
  a recording, a browser test, or a live walk. A green run is evidence a feature
  did not regress in source; it is not evidence a human can use it.

Then — and this half is unchanged — **that demonstrated feature must be running
in production**: merge, push, deploy, and confirm the live site serves, in the
same session. Do not end a session by offering deployment as a menu option or
asking "shall I deploy?"; that is a failure mode this rule also stops (one-strike
rule, per the owner). The only legitimate reason to stop short is that
verification is red OR the journey does not actually work when clicked.
Everything else: execute end-to-end.

**Superseded 17 Aug 2026:** this section used to add "or the next action is
destructive/irreversible (`recreate-db --force`, data deletion, history rewrites) — those still
require explicit approval." It does not any more; see *Own the decision* above. Destructive
remediation is yours to decide when it is the correct fix — take the backup, prefer the
reversible variant, measure before and after, and report afterwards. The genuine exceptions are
now scope-based, not danger-based: another organisation's data, commercial choices, and
product-direction questions with no technically-correct answer.

## Browser-based role testing standard — mandatory, not optional

**This is the concrete mechanism "Done means DEMONSTRATED" runs on.** `tests/smoke/`
drives a live Flask server plus real Postgres through Playwright, as real personas,
and is the only thing in this repo that observes access control and rendered UI
directly rather than trusting a decorator or a template to have done the right thing.

**The eleven canonical personas** live in `ARCHETYPES` in `tests/smoke/conftest.py`:
solution architect, enterprise architect, business architect, ARB member, portfolio
manager, CTO, procurement, application manager, platform admin, security architect,
data architect. This list is canonical — a new persona-visible feature is checked
against it, not against an ad hoc login script.

**The canonical checks:**
- `tests/smoke/test_authorisation_matrix.py` — drives every archetype against every
  persona-gated route and asserts who actually reaches what. It exists because a
  decorator scan once reported 1,590 unguarded routes when the real number was 1,
  and separately missed a route that turned out never to be registered — only
  driving the browser settles either question. **Any new persona-visible route
  must add or extend a row here**, stating which archetypes should and should not
  reach it, not just a bespoke one-off smoke test living outside the matrix.
- `tests/smoke/test_archetype_journeys.py` — per-persona primary-journey walkthroughs.
  A new primary journey for an existing persona extends this file.
- `tests/smoke/test_accessibility_audit.py` — axe-core, WCAG 2.2 AA as a
  build-and-test standard (not a conformance claim), ratcheted against
  `tests/smoke/a11y_baseline.json`. Once a browser is available it fails, not
  skips, when `axe-playwright-python` is missing or its bundled axe-core lacks
  the `target-size` rule; it does not replace manual assistive-technology testing.
  Its tag set is shared: the ARB governance and transformation room journeys run
  axe with the same list, and a static test keeps every axe run under
  `tests/smoke/` on it.
- `tests/smoke/test_visual_regression.py` — pixel-diff against committed PNGs in
  `tests/smoke/visual_baselines/`, ratcheted the same way (0.5% diff tolerance for
  antialiasing noise). Added 13 Sep 2026 after the ARB status-chart and AI-chat
  clipping defects shipped past every existing check — both left the DOM and
  accessibility tree intact, so nothing here would have seen them. Covers a
  curated matrix of high-traffic screens, not the whole app; grow it as a screen
  gets a real design review, not as a blanket capture-everything pass. Accept an
  intentional visual change with `SMOKE_VISUAL_UPDATE_BASELINE=1 pytest
  tests/smoke/test_visual_regression.py`.
- The "reality" gates — `test_no_error_banners.py`, `test_console_hygiene.py`,
  `test_interaction_reality.py`, `test_task_completion.py`,
  `test_rendered_legibility.py` — check a screen is honestly usable, not just that
  it returns 200.

**CI enforcement is real and is blocking.** `.github/workflows/ci.yml` runs this on
every push: an archetype walkthrough job and a "Browser compatibility (webkit)"
cross-browser journeys job, in addition to the `smoke` job described under CI above.
Treat a red run in either as a red deploy — not something to check later. **Verify
CI status explicitly with `gh run list` / `gh run view` before claiming work is
done or before deploying** — do not rely on a memory note or an assumption about
whether Actions is currently working; that assumption has been wrong before and
CI enforcement has silently rotted for days at a time (green reported locally
while CI was actually red) without anyone noticing. A single `gh run list --limit 5`
costs nothing and catches it.

**What this standard is not:** it is not satisfied by `scripts/verify.py` alone —
the browser/smoke tier is deliberately excluded from that command (see
Verification below) — and it is not satisfied by a curl check or an API-level
assertion. Playwright against a real rendered page, logged in as a real persona,
is the standard itself, not a fallback for a missing browser extension.

## Verification — run this before claiming anything works

```bash
python scripts/verify.py          # every gate that can run here
python scripts/verify.py --json   # machine-readable
python scripts/verify.py --gate boot-health     # one gate
python scripts/verify.py --tag static           # fast static gates only
```

**`--tag static` is NOT the full set, and a green one is not a clean tree.** It
excludes `broken-surfaces`, `dynamic-link-prefixes` and `csrf-coverage`'s boot half —
they boot Flask to read the real `url_map`, so they cannot run in CI's dependency-free
static job — and it excludes `nav-verified`, which carries no tags at all and is
therefore unreachable from *every* `--tag` invocation. `broken-surfaces` sat red on
deployed main behind exactly this: the pre-deploy command everyone ran could not see
it, and its "31 passed, 0 failed" line read as proof.

Any filtered run (`--tag` or `--gate`) now ends with an explicit list of the gates it
did not run and the words `PARTIAL RUN`, and `--json` carries `partial_run` /
`not_run`. **The only command whose green means "clean" is the bare
`python scripts/verify.py`** — run that before a deploy, never a tag subset.

This is the executable form of `app/templates/macros/ZERO_TOLERANCE_PROTOCOL.md`.
**Do not report work as complete without a green run**, and do not treat a `SKIP` as a
pass — a skipped gate is printed in the summary precisely so it cannot be mistaken for
one. CI runs with `--require-db`, so gates needing PostgreSQL fail there rather than
skipping.

Several gates are **ratchets**: they compare a measurement against
`verification_baseline.json` and fail when it gets worse, so the gate is "no worse",
not "clean". **Corrected 3 Sep 2026** — this section previously said `design_tokens`
and `raw_sql_tenancy` carried 88 and 98 of real debt; both measured **0** on the
candidate SHA checked at correction time (`2f7fdc5c`), and the gate count below had
drifted from 19 to the 52 gates actually registered in `build_gates()`. Neither
number nor the table is re-verified by this file automatically — re-measure before
trusting either. Lowering a baseline is routine — `python scripts/verify.py
--update-baseline` after a cleanup. Raising one is a regression that must be
justified in review.

**Read the numbers from `verification_baseline.json`, not from here.** This file is
prose and drifts; the JSON is what the gate enforces. Note also that `design_tokens`
counts only the families in `BANNED_FAMILIES` (`scripts/check_design_tokens.py`) —
`gray/grey/slate/zinc/neutral/stone/blue/red`. Removing an `emerald`, `purple`,
`orange` or `cyan` class is right per DESIGN.md but moves this number by zero, and a
line carrying a `token-migration-ok` marker is already excluded from the count.

**All 62 gates, in registry order (`scripts/verify.py`, `build_gates`) — this table
is a snapshot, not generated. Run `grep -oE '^\s*Gate\("[a-z-]+"' scripts/verify.py`
to reconfirm the count before trusting it:**

| Gate | Catches | Kind |
|---|---|---|
| `compile` | syntax errors (bytecode-compiles every module) | must pass |
| `undefined-exports` | `__all__` naming a missing symbol | must be 0 |
| `undefined-names` | runtime `NameError` (ruff F821) | ratchet @ 0 |
| `redefinitions` | shadowed definitions (ruff F811) | ratchet @ 0 |
| `lint-core` | correctness lint (ruff `F,E4,E7,E9`) | ratchet @ 0 |
| `design-tokens` | raw Tailwind colours (DESIGN.md rule) | ratchet @ 0 |
| `composer-url-params` | a literal composer link carrying a param the composer doesn't read | ratchet @ 0 |
| `crosswalk-writer-gated` | a write to `external_identity_crosswalk` with no allowlist gate on its call path | must be 0 |
| `raw-fetch-sites` | `fetch()` bypassing `Platform.fetch` | ratchet @ 0 |
| `design-tokens-extended` | raw colours outside the core banned families | ratchet @ 0 |
| `shell-conformance` | a page off the platform shell (header macro/width) | ratchet @ 3 |
| `nav-coverage` | business-architecture output missing from every sidebar | ratchet @ 0 |
| `air-gap` | a UI asset loaded from a public CDN | ratchet @ 0 |
| `raw-sql-tenancy` | raw SQL on a tenant table with no `organization_id` predicate | ratchet @ 0 |
| `tenant-scoping` | ORM queries on a tenant-owned-but-unmixed model with no org predicate | ratchet @ 0 |
| `llm-boundary` | a codegen emitter calling an LLM directly | ratchet @ 0 |
| `evidence-contract` | behavioural changes/checkers missing evidence or provenance | ratchet @ 29 |
| `role-gate-coverage` | a declared delivery role resolving to no verifier gate | ratchet @ 7 |
| `ai-evidence-rules` | an AI persona missing evidence/no-fabrication rules | must be 0 |
| `ai-tool-guard` | an AI mutating tool bypassing permission/approval classification | must be 0 |
| `ai-untrusted-content` | retrieved AI context entering a system prompt without fencing | must be 0 |
| `ai-approval-honoured` | agent auto-execution bypassing the operator approval control | must be 0 |
| `sidebar-links` | a persona sidebar exceeding its link budget | ratchet @ 27 |
| `template-syntax` | a Jinja template that does not parse (500s every page using it) | must be 0 |
| `template-references` | an `include`/`extends` target that does not exist (TemplateNotFound at render) | must be 0 |
| `broken-surfaces` | a front-end target that resolves to no real route | ratchet, boot-only |
| `dynamic-link-prefixes` | a concatenated href/fetch whose literal prefix is a dead route | ratchet @ 0, boot-only |
| `store-agreement` | two surfaces answering one question with different numbers | ratchet @ 1, boot-only |
| `canonical-store` | a table gaining a second mapped SQLAlchemy model class | ratchet @ 0 |
| `fetch-guards` | a `fetch()` parsed without checking the response | ratchet @ 0 |
| `ui-contract` | a native dialog / `onclick=` / typeless button / arbitrary `px` (DESIGN.md) | ratchet @ 0 |
| `unrendered-model-fields` | a detail-view template never rendering a real Text/JSON model field | ratchet @ 387 |
| `error-signalling` | an API error path that answers `200` | must be 0 |
| `silent-data` | a server failure returned to the caller as data | must be 0 |
| `dead-interactions` | a control that silently does nothing | must be 0 |
| `macro-import-context` | a script-bearing macro imported without `with context` | must be 0 |
| `asset-urls` | a doubled `?` asset URL, or a stylesheet/script included twice | must be 0 |
| `qa-register` | an open finding in the QA remediation register | must be 0 |
| `null-filters` | `default()` feeding a `len()`-calling filter without the boolean arg | must be 0 |
| `fabricated-data` | invented data reaching the UI (see below) | must be 0 |
| `breadcrumb-coverage` | a routed page with a header but no breadcrumb | must be 0 |
| `raw-repr-in-template` | a Python object repr (raw dict/list `str()`) reaching a rendered page | must be 0 |
| `duplicate-breadcrumb` | a page rendering two independent breadcrumb trails at once | ratchet @ 17 |
| `raw-html-escaping` | unescaped interpolation into hand-built (non-Jinja) HTML | ratchet @ 0 |
| `smoke-coverage-on-change` | a template/JS change with no `tests/smoke/` touch in the same diff | must be 0 |
| `stale-models` | a retired LLM model id (404s in prod) in shipped code | must be 0 |
| `deployed-deps` | installed packages below the pinned floors | must be 0 (boot-health job only) |
| `js-build` | committed `js/bundles/*.js` stale vs a rebuild | must pass |
| `console-reporting` | `console.*` calls in shipped JS/templates | ratchet @ 0 |
| `js-syntax` | shipped JS that fails to parse in a real engine | must pass |
| `css-build` | committed `tailwind-output.css` stale vs a rebuild | must pass (needs Tailwind CLI) |
| `sri` | `integrity=` hash not matching the file it guards | must be 0 |
| `vendor-integrity` | a vendored asset not matching `VENDOR_MANIFEST.txt` | must pass |
| `dependency-cves` | known CVEs in shipped dependencies (`pip-audit`) | ratchet |
| `boot-health` | unregistered blueprints; unresolved `url_for` | must pass |
| `csrf-coverage` | a write route with no CSRF protection or justified opt-out | must pass |
| `schema-drift` | ORM/database column drift | must pass (needs DB) |
| `tests` | behavioural regression | must pass (needs DB) |
| `nav-verified` | a new sidebar route with no test loading it | ratchet @ 0, carries no tags |
| `docs-drift` | CLAUDE.md/DELIVERY_CONTRACT.md gate claims disagreeing with build_gates() | must be 0 |
| `public-repo-hygiene` | a docs/buckets/ directory or path reference in this public repository | must be 0 |
| `unregistered-checks` | a scripts/check_\*.py with no Gate(...) entry (F500-008) | ratchet @ 33 |

Per-line escape hatches, each of which makes the exception reviewable rather than
silent — every one greppable as `<name>-ok` in `scripts/verify.py`/`scripts/check_*.py`:
`fabricated-ok`, `air-gap-ok`, `tenancy-ok`, `tenant-scoping-ok`, `llm-boundary-ok`,
`raw-fetch-ok`, `shell-ok`, `breadcrumb-ok`, `stale-model-ok`, `error-signalling-ok`,
`silent-data-ok`, `ui-contract-ok`, `fetch-guard-ok`, `token-migration-ok`
(design-tokens only), each taking `: <reason>` where the gate requires one.

`pre-commit install` gives the same feedback at commit time on changed files only.
Rationale for the whole design — and why compiler/type-checker enforcement was
rejected — is in [ADR 0001](docs/adr/0001-verification-strategy.md). The air-gap,
CSP, telemetry and CVE posture is [ADR 0005](docs/adr/0005-enterprise-network-readiness.md);
the GPL-in-the-dependency-tree question is [ADR 0006](docs/adr/0006-dependency-licensing.md).

### CI enforces more than `verify.py` can

`.github/workflows/ci.yml` adds five gates with no local `verify.py` equivalent — do
not assume a green local run means a green CI run:

| CI job | Enforces |
|---|---|
| `secret-scan` | gitleaks over **full history** (so a bad commit is expensive to undo — stage files individually) |
| `security-sast` | bandit, ratcheted via `scripts/ci/bandit_gate.py` against `.bandit-baseline.json` |
| `smoke` | Playwright browser journeys, one per archetype (`tests/smoke/`), a WCAG 2.2 AA axe-core audit ratcheted against `tests/smoke/a11y_baseline.json`, and an authorisation matrix |
| `dependency-audit` | `pip-audit` ratcheted against `scripts/ci/dependency_baseline.json` |
| `db-gates` | also emits a CycloneDX SBOM from the *installed* environment |

Line coverage is measured and printed by the `tests` job but **deliberately not
gated** — it can regress silently, so do not read a green CI as coverage holding.

## Commands

```bash
# Setup (PostgreSQL required; `CREATE EXTENSION IF NOT EXISTS vector;` for AI/embedding features)
pip install -r requirements.txt
cp .env.example .env                          # SECRET_KEY, DATABASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD
flask --app manage init-db                    # create missing TABLES (create_all) — non-destructive
flask --app manage reconcile-schema           # add missing COLUMNS to existing tables — see Schema below
python create_admin.py

# Run
flask --app manage run                        # dev, :5000
python manage.py                              # dev, but kills any process already on :5000 first
gunicorn -c gunicorn.conf.py "manage:app"     # production
docker compose up                             # app + Postgres + Redis; boots init-db → reconcile-schema → gunicorn

# Tests — pytest reads TEST_DATABASE_URL (NOT DATABASE_URL); PostgreSQL is enforced, SQLite raises
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/archie_test
pytest -q                                     # what CI runs (with --maxfail=20)
pytest tests/test_business_case.py            # single file
pytest tests/test_business_case.py::test_name # single test
pytest -m journey                             # by marker — see pytest.ini for the full marker list
ruff check .                                  # advisory, non-blocking in CI

# CLI (dozens of commands; `flask --app manage --help` to list)
flask --app manage reconcile-schema --dry-run # report column drift without applying
flask --app manage bridge-motivation          # promote journey Solution* motivation → enterprise layer
flask --app manage data-profile --table application_components --fields lifecycle_status
flask --app manage db-query "SELECT ..."      # read-only SQL
flask --app manage seed-capabilities --dry-run
flask --app manage acm stats
```

`flask --app manage recreate-db` is destructive and requires `--force`. Don't run it without explicit
human approval — `init-db` + `reconcile-schema` covers every non-destructive case.

## Deploying to production

```bash
scripts/deploy_verified.sh <ref>                # fetch, checkout <ref>, force-recreate, then PROVE it
scripts/deploy_verified.sh <ref> --skip-deploy   # verify-only, against whatever is already running
```

**Never deploy with `docker compose restart`.** On 8 Sep 2026 it was run 6+ times in a
row and every one was a silent no-op: `restart` only restarts a container with its
*existing* config, it never re-reads `docker-compose.yml`, so the running server had
drifted to zero bind mounts while `curl /health` and the host's `git log` both kept
reporting success. `scripts/deploy_verified.sh` replaces that ad-hoc flow for the
bind-mount source-checkout topology production actually runs today: it always
`git fetch`/checks out the target commit and runs
`docker compose up -d --force-recreate server` (recreation is unconditional, not
config-diffed), then independently proves — from *outside* the container's own
self-report — that (a) the container is healthy, (b) the bind mount is genuinely
present (`docker inspect --format '{{range .Mounts}}...'`), and (c) the code actually
running is the target commit, via `/version`'s `build_id`
(`app/_bootstrap/build_info.py`), not just "files exist at the mount point." Optionally
runs an authenticated Playwright login-and-reach-a-page check when
`DEPLOY_VERIFY_EMAIL`/`DEPLOY_VERIFY_PASSWORD` are set. Any failed check exits non-zero
with a diagnostic — a deploy that cannot prove itself is never reported as success.

This is deliberately separate from `deploy/deploy.sh` + `scripts/deploy.sh`
(the immutable-GHCR-digest pipeline with `deploy/docker-compose.production.yml`
stripping all bind mounts via `!reset []`): that pipeline is real and committed but,
as measured on 8 Sep 2026, is **not** what production is actually running — the live
container still has a real `/root/archie-ea -> /app` bind mount and runs a
locally-built image, and `/root/deploy-releases/release.env` is stale. Two scripts
exist because two topologies exist; when the GHCR pipeline becomes the live one,
`scripts/deploy_verified.sh`'s verification steps should move onto that path and this
file should retire — don't let both read as "the current answer" at once.

**Deploying without droplet SSH.** A session that has GitHub access but no SSH access to
the droplet deploys through the `Production deploy` workflow
(`.github/workflows/deploy.yml`), which wraps `scripts/deploy_verified.sh` behind a
required-reviewer approval, on commits on `main` with green required CI. Rehearse first:
`gh workflow run deploy.yml --ref main -f ref=<full 40-character sha> -f dry_run=true`
verifies what is running and changes nothing. The same command with `-f dry_run=false`
deploys. Dispatching the run and reporting its link and state is how a session meets the
deploy-in-the-same-session rule above when it has no SSH. **A session may dispatch but must not approve its own production
run.** Approval belongs to the reviewer; where the reviewer is the same GitHub account as the
session's token, that is a rule to follow, not one GitHub enforces, and the approval is then a
confirmation prompt rather than a separation of duties. While required CI on `main` is red,
every dispatch is refused by design. Which CI jobs are required, and who approves, are
decisions for the repository owner. Setup, rollback and revoking access are in
`deploy/DEPLOY_WORKFLOW.md`; read it before the first dispatch.

## Schema management — read this before touching a model

There are **three** overlapping mechanisms, and Alembic is *not* the source of truth:

1. **`create_all()`** via `flask init-db` — creates missing tables only. It **cannot** add a column to
   a table that already exists.
2. **`flask reconcile-schema`** (`app/commands/reconcile_schema.py`) — the actual answer to drift.
   Diffs every mapped model against the live table and emits `ALTER TABLE ... ADD COLUMN IF NOT
   EXISTS`. ADD-only, all nullable, never drops or retypes; idempotent. Runs on container boot.
3. **`migrations/`** — Flask-Migrate/Alembic exists with 130+ revisions and multiple merge heads, but
   deploys do **not** run `flask db upgrade`. Treat it as historical.

Consequence: **adding a non-nullable column, or one with a backfill requirement, will break existing
databases** — `reconcile-schema` only adds nullable columns. New columns should be nullable (or carry
a server default) and be tolerated by code when NULL. See
`docs/known-issues/schema-drift-on-existing-databases.md` for the full failure mode: one missing
column raises `UndefinedColumn`, which aborts the transaction and cascades into
`InFailedSqlTransaction` for every later query, 500-ing the whole page.

`manage.py init_db` also contains a long tail of hand-written idempotent `ALTER TABLE` statements for
pre-Alembic columns. **Do not add to it** — it is legacy.

The agreed target state (Alembic baseline + `db upgrade` on deploy, `reconcile-schema`
demoted to a drift detector) and the maintenance-window migration plan are in
[ADR 0002](docs/adr/0002-schema-management.md). The detector half is already wired as
the `schema-drift` gate.

## Architecture

**App factory** — `app/__init__.py` is a slim orchestrator; each step delegates to
`app/_bootstrap/*.py` (`extensions`, `security`, `routes`, `context_processors`, `assets`, `services`,
`blueprints`, `swagger`, `cli`). **Order matters** — extensions and tenant middleware must be
installed before blueprints. Add new wiring to the relevant `_bootstrap` module, not to
`create_app()`.

**Blueprints register non-fatally.** `init_blueprints` logs and continues on import failure rather
than raising, so a broken module degrades one feature instead of the whole app. The cost: any
`url_for()` to such an endpoint can `BuildError` and 500 *every* page that renders the sidebar.
Guard cross-module links:

```jinja
{% if 'value_stream.index' in flask.current_app.view_functions %}
<a href="{{ url_for('value_stream.index') }}">…</a>
{% endif %}
```

`_validate_critical_endpoints()` warns at boot when a core endpoint is missing; add to
`REQUIRED_ENDPOINTS` when a template starts depending on a new one.

**Multi-tenancy is implicit and enforced by ORM events**, not by query code
(`app/middleware/tenant_isolation.py`, `tenant_context.py`). Models inheriting `TenantMixin` get a
`WHERE organization_id = g.current_org_id` filter injected via `do_orm_execute`, and
`organization_id` auto-set on flush. So:
- Don't hand-write `organization_id` filters on `TenantMixin` models — you'll double-filter.
- Anything running outside a request context (CLI, scheduler, tests) has no `g.current_org_id` and is
  therefore **unfiltered**. Scope explicitly in those paths.
- A new tenant-scoped model needs `TenantMixin` — omitting it silently leaks rows across orgs.
- **Bulk `UPDATE`/`DELETE` ARE tenant-filtered as of ADR-0003's closure** —
  `do_orm_execute` now applies `with_loader_criteria` to ORM-enabled UPDATE and
  DELETE, not just SELECT, so `Model.query.filter(...).update()/.delete()` inside a
  request carries the tenant predicate mechanically. The two strict xfails that
  encoded this gap in `tests/test_tenant_isolation.py` are now plain passing tests.
  Still put `organization_id` in bulk-write predicates as defence-in-depth: raw-SQL
  writes and anything outside a request context remain unfiltered.
  `Query.get()` / `Session.get()` are scoped **only on an identity-map miss** —
  correcting an earlier "is scoped (verified)" note here, which was measured with a
  cold session and so only ever tested the miss. On a hit they return the cached
  object without emitting SQL, so `do_orm_execute` never runs and no tenant filter
  is applied. Demonstrated: load a row as org A, switch `g.current_org_id` to org B
  in the same session, and `.get()` hands back org A's row; `expunge_all()` first and
  it is correctly blocked.
  Per-request code is unaffected — one request is one tenant and one session. The
  exposure is anything that **loops over tenants inside a single session**: CLI
  commands, the scheduler, importers, and tests. Call `db.session.remove()` (or
  `expunge_all()`) between tenants there, and put `organization_id` in the predicate
  rather than trusting `.get()`.
  The same caching bit flask_login: its `g._login_user` survives when a context is
  reused, which made four cross-org tests exercise the wrong user and report a leak
  that does not exist — see the note in `tests/test_ba_tenant_and_authz.py::_login`.
  [ADR 0003](docs/adr/0003-tenant-isolation-gaps.md);
  invariants pinned in `tests/test_tenant_isolation.py`.

**Two parallel code layouts** — both live; `app/modules/` is canonical for new work:
- `app/<domain>/` — older flat blueprints. 10 domains are legacy-only, including
  `api/` (28 files), `application_mgmt/` (31), `main/` (25), `routes/` (18).
- `app/modules/<domain>/` — self-contained (`routes/`, `services/`, `models/`, a
  `register(app)` entrypoint). 19 domains live only here.
- **7 domains exist in both** — `account`, `admin`, `ai_chat`, `auth`, `dashboard`,
  `integrations`, `monitoring` — selected at boot by the `USE_*_GUARDRAILS` flags,
  which `blueprints.py` now defaults **on** (with them off, a fresh clone fell back to
  legacy and ~66 template `url_for()` calls raised `BuildError`). Don't flip them off
  casually. Retiring these duplicates is the migration's next step — see
  [ADR 0004](docs/adr/0004-module-layout-consolidation.md).

`app/models/` (~200 modules) and `app/services/` (~340 modules) are shared by both. Several tables are
mapped by two model classes via `extend_existing` — a known legacy hazard `init-db` works around by
de-duplicating same-named indexes before `create_all()`.

**Personas / access control** — `enterprise_role` on `User` drives sidebar sections and dashboard
cards (`app/utils/role_access.py`, `_bootstrap/context_processors.py`), and the AI assistant's
governed charters live in `app/modules/ai_chat/services/architect_persona_charters.py`
(`ARCHITECT_PERSONAS`). Adding a persona means touching all three plus `components/admin_sidebar.html`.

**ArchiMate is the backbone, not a view.** Every backend CREATE for a motivation entity (Driver,
Goal, Constraint, Requirement, Risk, Metric, Plateau, WorkPackage) must call
`_sync_archimate_element()` so a matching `ArchiMateElement` row exists. The domain→ArchiMate type
map is in `DESIGN.md`. A plain textarea is not an acceptable substitute — the field *is* the element.

## Conventions

- **SQLAlchemy 2.0:** raw strings in `db.session.execute("…")` raise. Always wrap: `db.text(...)`.
- **Impact scoring:** use `POST /api/v1/impact/analyze`; don't add parallel scoring logic. Responses
  may be wrapped by `success_response()` — unwrap with `json.data ?? json`.
- **No `console.log` in shipped templates/JS**, no stray `print()` in request handlers. User-facing
  notifications go through `Platform.toast`, never native `alert()`/`confirm()`.
- **Null display:** em dash (`—`), never `0` or blank. Currency via `window.currencyManager.format()`.
- **Never invent data.** Entelim is a system of record: a screen that fabricates a plausible
  value when the real one is missing is worse than one showing nothing, because the user
  cannot tell the difference and acts on it. Concretely — no fake fallback in a `catch`
  (render the error), no literal metric passed to `render_template` that looks computed
  (pass `None`), no label describing a different field than the one plotted. A `0` that
  means "not computed" is indistinguishable from a measured zero; use `None` → `—`.
  Remember `fetch` does **not** reject on 404: `if (response.ok)` with no `else` silently
  leaves metrics at their `0` initialiser. Use `if (!response.ok) throw`.
  Enforced by the `fabricated-data` gate; escape hatch is `fabricated-ok: <reason>`.
- **Derived/composite scores need a governed formula, not just a real input.** A single number
  that combines several fields (a health score, a maturity index, a priority ranking) is a
  distinct risk from the single-field case above: every input can be real and the composite
  can still be fabricated, if the *weighting* was invented rather than decided. Before any such
  score ships: (1) name every input field and where it comes from — an existing table, a
  derived fact, or (per `enterprise-intelligence-graph-strategic-vision-v1.md` in the
  orchestrator repo) an externally-mastered record synchronised in; (2) write the formula down
  as a reviewable artifact (a brief or an ADR, not a code comment) and get it reviewed the way
  a metamodel or SRS change is; (3) an unmeasurable input makes the *whole* score `None`
  (rendered `—`), not a component silently defaulted to 0 or 1 — this exact bug (`total or 1`,
  `max(x, 1)` denominators turning "nothing to measure" into a confident 0%) already shipped
  and was found and fixed once in the dashboard health score; don't reintroduce the shape
  elsewhere. `ApplicationRationalizationScore`'s TIME-framework dimensions are the one existing
  precedent for doing this correctly — read it before inventing a new pattern.
- **Entity fields** for user / application / vendor / ArchiMate element must use a debounced
  live-search picker against the documented endpoint, not a free-text input (see `DESIGN.md`).
- **Staging:** `git add <file>` — never `git add -A`. Untracked scratch scripts are common at the repo
  root, and CI runs `gitleaks` over full history, so an accidental commit is expensive to undo.
- **Commit messages:** write multi-line messages with `git commit -F <file>`, or a heredoc
  (`git commit -F- <<'MSG' … MSG`). Do **not** use a PowerShell here-string (`@'…'@`): the Bash tool
  is Git Bash, which does not parse it, and the result is a commit whose *subject line is a bare
  `@`* with the real subject on line two. That has happened three times in this repo. Backticks in a
  `-m` message are worse — the shell runs them as command substitution and silently deletes the
  quoted term, leaving sentences with holes.
  Repairing a subject line needs `--amend`, which is unsafe in a shared worktree because it sweeps in
  whatever another agent has staged. `git commit-tree` + `git update-ref HEAD` rewrites the message
  while touching neither the index nor the working tree, and is the safe repair.
- New behaviour needs a test. There are **three** conftest files: `tests/conftest.py` (shared
  fixtures), `tests/smoke/conftest.py` (Playwright live-server harness) and
  `tests/journeys/conftest.py`.
  **Write new tests against the shared fixtures** in `tests/conftest.py` — `db_session` runs the
  test inside a transaction that is always rolled back, so it cannot leave residue in the shared,
  persistent test database even if it fails partway; `app` is session-scoped; `make_org` and
  `tenant_ctx` cover multi-tenant setup. Follow `tests/test_tenant_isolation.py`, currently the
  only adopter.
  Most older modules (including `tests/test_business_case.py`) still hand-roll a module-scoped
  `app` fixture and delete their own rows. pytest resolves the closest fixture so they keep
  working, but **do not copy that pattern** — it is flaky by construction, which is why the
  shared fixtures exist.

## `docs/known-issues/` is a decision record, not a backlog

A file here documents something that **cannot be fixed in code** — a product
decision about whether a feature is wanted, an external constraint, a schema
change that needs a maintenance window. If a defect can be fixed, fix it;
writing it down instead converts a bug into a bug *plus* a note, and the note
gets read by the next person as a decision someone made deliberately.

This is not hypothetical. Every one of these was documented and left, and every
one was a live defect the whole time:

- 73 catch blocks that told nobody, recorded as "the next slice"
- `conversation_threads` / `conversation_messages` have no model, so chat
  history is broken on every fresh install
- five blueprint name collisions, one of which makes `/admin/security`
  unreachable
- ~107 `fetch()` calls whose response is never checked

Each was fixed only when someone asked why the list still existed.

If you must record something unfixed, say what would fix it and why it is not
being done *now* — and prefer a gate that counts it (see `verify.py`) over prose
that does not. A ratchet keeps the number from growing while the work is
outstanding; a paragraph does not.

## Documentation accuracy

The repo is an extract of a larger private codebase, so some docs reference files that aren't here.
Trust in this order: **`DESIGN.md` → `README.md` / `CONTRIBUTING.md` → `docs/` → everything else.**

- **`QUICK_START.md`** — obsolete. Every file it points at (`DOMAIN.md`, `DATA_REALITY.md`,
  `docs/design_system/*.json`, `scripts/guardrails/*`) is absent, so the claim/release task workflow
  it describes cannot be run. `DESIGN.md`'s own "canonical source of truth" pointers to
  `pattern_registry.json` / `token_map.json` are dangling for the same reason — `DESIGN.md` itself is
  the source of truth, alongside `app/static/css/shadcn_tokens.css` and `tailwind.config.js`.
- **`ARCHITECT_QUICK_START.md`** — legacy internal doc. It describes `flask db upgrade`,
  `python manage.py recreate_db|setup_dev|runserver`, and port 5439; the current setup is
  `init-db` + `reconcile-schema` on 5432/whatever `DATABASE_URL` says. It also refers to
  `SECURITY_REMEDIATION.md` and `KNOWN_ISSUES.md`, which don't exist.
- **CSS is committed pre-built** (`app/static/css/tailwind-output.css`) so a fresh clone and
  `docker compose up` render without a Node toolchain — the Docker image is Python-only.
  `scripts/build_css.py` (restored; `package.json`'s `build:css*` scripts point at it) drives
  the **standalone Tailwind CLI**, which `.gitignore` expects at `scripts/bin/tailwindcss[.exe]`
  — download it from the Tailwind releases page (v3.x; the config is v3 format). **Editing
  template classes requires a rebuild**, or the new class won't exist at runtime;
  `python scripts/build_css.py --check` fails when the committed CSS is stale.
  The `npm run test:*` Playwright scripts still expect `tests/e2e/`, which is not in this repo.

## Standing instruction — no truth scaffolding in anything a human reads (founder, 2026-09-17)

Anything written for a person — documents, decks, applications, proposals, emails, Slack posts, release notes,
customer-facing copy, reports to the founder — carries NO status labels (VERIFIED / RULED / PLANNING / ASSUMPTION /
PROPOSED / FOUNDER-INPUT / UNVERIFIED / ROADMAP / STALE), no source citations or "Figures source" lines, no repo
paths or file names, no ledger or row ids, no commit hashes or build ids, no audience markers, no "founder to
confirm" / TBC placeholders, no volunteered defects or hedges, and no mention of agents, seats, orchestrators,
gates, workforces, Claude, Codex or Kilo as authors or operators. Provenance lives only in machine-readable working
files (models, ledgers, gate code, bucket working copies). Never name the founder's current employer. Never
self-undermine. Reason: the EIC Accelerator proposal 101357426 was rejected 0/4 on 17 Sep 2026 and the evaluators
quoted our own labels, hedges and placeholders back at us. Full rule: archiet-strategy AGENTS.md Rule 1f.
