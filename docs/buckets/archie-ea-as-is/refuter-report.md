# Refuter report: to-be-plan.md

Author: `refuter`. Read-only review of `to-be-plan.md`'s reasoning — no
code in this bucket to review, so this is a review of the plan's claims
against evidence, same standard as a code review.

**Post-hoc correction (Phase 0 execution, `fix/phase0-ci-and-audit` commit
`29ee0a15`):** Finding 1 below characterizes the `ErrorEvent` tenant-isolation
test failure as a "gate regression"/"correctness issue"/"live data-leak risk
class." That was itself wrong, made from the test's failure message without
reading the model. `ErrorEvent`'s own docstring documents a deliberate,
correct design decision (cross-tenant error visibility for platform admins);
the actual defect was a missing `INTENTIONALLY_GLOBAL` allowlist entry, now
fixed. The rest of Finding 1 — the "small, bounded" CI sizing claim being
unsupported, and the other 3-4 root causes bundled into the `Tests` job —
stands as originally written; only the tenant-isolation severity assessment
was wrong. Left in place below rather than edited away, so the mistake is
visible.

## Finding 1 (CONFIRMED): Phase 0's CI sizing claim is unsupported and wrong

**Claim in the plan:** "Fix the 2 currently-failing CI jobs (`Tests`,
`Browser journeys`) — small, bounded, unblocks trusting any future green
run."

**What I actually found** (`gh run view 34960831362 --json jobs`, then the
full log): the `Tests` job's failure is not one thing. Its short test
summary shows at minimum four unrelated defect categories in the same run:

- `jinja2.exceptions.UndefinedError: 'flask' is undefined` — 4 tests in
  `tests/test_procurement_utilization_honesty.py`, a template-context
  registration bug (some template global/proxy not available in whatever
  render path this test exercises).
- **Two separate sidebar-link-budget overshoots**: `test_sidebar_budgets.py`
  asserts `27 == 25` and fails; `test_sidebar_render.py` separately asserts
  the platform_admin sidebar renders exactly 28 links and gets 30. These are
  two different tests catching what's likely the same root cause (a new
  sidebar link landed without updating the pinned budget) — but that's an
  inference, not confirmed by reading the actual diff that added the link.
- `test_tenant_isolation_matrix.py::test_every_unscoped_model_is_a_deliberate_decision`
  — "1 model(s) carry organization_id without TenantMixin and without a
  reason." This is a **tenant-isolation gate regression**, not a flaky test —
  exactly the class of defect this repo's own `CLAUDE.md` treats as
  highest-severity.
- `test_tool_mutates_flag.py::test_the_helper_returns_the_same_set` — a set
  equality assertion failure, unrelated to the above three.

`Browser journeys` fails across 5 distinct steps (archetype journeys,
adversarial regression probes, AI protocol/persistence journeys, saved-guide
history journeys, plus its own "no skips" check) — also not evidence of one
small bounded issue.

**Why this matters for the plan:** "small, bounded" is used to justify
starting Phase 0 immediately with an implied low time cost. The evidence
says otherwise — this is at minimum 4-5 independent root causes across two
jobs, one of which (the tenant-isolation gate regression) is a correctness
issue this repo treats as severe, not a quick fix. Sizing this as "small"
risks the plan's own credibility once someone starts Phase 0 and finds a
tenant-isolation regression instead of a quick fix.

**Recommendation:** rewrite Phase 0's CI item to name the actual failure
categories found above, drop the "small, bounded" characterization, and
treat the tenant-isolation regression as its own priority item — it's
arguably more urgent than anything else in Phase 0 or Phase 1, since an
unscoped model is a live data-leak risk class per this repo's own ADR 0003
standard, not architecture debt.

## Finding 2 (PLAUSIBLE, not fully confirmed): Phase 1's sequencing decision precedes its own evidence

The plan sequences "retire the 7 overlapping domains" before "capability
store consolidation," citing `solution-architect`'s reasoning that some
overlapping domains "likely" touch capability code paths. But Phase 0 also
schedules an audit of those same 7 domains to determine retirement order —
and that audit hasn't run yet. The plan commits to an ordering (layout
before capability, in general) before the investigation that's supposed to
inform it has produced anything.

This may still be the right call — but it's stated as a firm sequencing
decision rather than a hypothesis pending Phase 0's audit. If the audit
finds, say, only 1 of 7 domains touches capability code, the blanket
"layout first" sequencing wouldn't need to hold for the other 6.

**Recommendation:** rephrase Phase 1 step 1 as conditional on Phase 0's
audit findings, not asserted upfront.

## What I checked and found no issue with

- The "2 of 3 open questions resolved" claim — verified: I independently
  re-ran the CI check myself as part of Finding 1 and confirm the job names
  and count are accurate as of this review, even though the *characterization*
  of the failures needs correction (Finding 1).
- The reconcile-schema evidence cited from the concurrent SAP-S4 bucket —
  I did not re-verify this myself (it's from the other tech-lead pass, not
  this review's scope), but it's consistent with ADR 0002's documented
  behavior and isn't a load-bearing claim for this plan's own reasoning.
- The explicit exclusions (no new features, no forced deploy-pipeline
  decision, not bundling SAP-S4) — reasonable, no defect.

## Verdict

Not approved as written. Two changes needed before this plan is ready: (1)
correct Phase 0's CI sizing claim with the real failure categories above and
elevate the tenant-isolation regression to its own priority item, (2)
soften Phase 1's sequencing from a firm decision to conditional-on-audit.
Neither change alters the plan's overall shape or conclusions — both are
corrections to stated confidence, not a rejection of the underlying
direction.
