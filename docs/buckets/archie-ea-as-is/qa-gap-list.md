# QA gap list: to-be-plan.md vs. actual tests/smoke/ coverage

Author: `qa-lead`. Input: `to-be-plan.md` (approved reasoning),
`refuter-report.md`. Surveyed `tests/smoke/`'s 79 test files directly rather
than assuming coverage from file names.

## Priority 0 (tenant-isolation regression) — already caught, not a smoke gap

`tests/test_tenant_isolation_matrix.py::test_every_unscoped_model_is_a_deliberate_decision`
is a **unit-level** test, not `tests/smoke/`, and it's already failing in CI
— this is the mechanism working as intended (it caught the regression before
this analysis even started). No gap here; this item just needs a fix, not
new test coverage.

## Gap 1 (real, confirmed): no browser-level check that capability stores agree

`tests/smoke/test_capability_journey.py` explicitly documents, in its own
comment, that it deliberately tests the `business_capability` surface and
**not** `unified_capabilities`, "because `unified_capabilities` still has no
producer." This means: once the ADR 0008 capability-store consolidation
(Phase 1 step 2 of the to-be plan) lands a producer and starts retiring the
5 redundant stores, **no smoke test will notice if it breaks** — the only
thing that currently catches capability-store disagreement is the
`store-agreement` gate in `scripts/verify.py`, which is a source/API
comparison at boot, not a Playwright browser journey.

**Recommendation:** before Phase 1 step 2 starts, extend
`test_capability_journey.py` (or add a new smoke test) to assert that the
capability count shown in the UI matches what `unified_capabilities` answers
via the API, once the producer exists — the reverse of today's test, which
deliberately avoids that comparison because it would fail right now.

## Gap 2 (real, confirmed): uneven smoke coverage across the 7 overlapping domains

Grepped actual smoke-test file references per domain name:

| Domain | Smoke test files touching it | Retirement risk |
|---|---|---|
| `account` | 51 | Low — well covered |
| `admin` | 36 | Low — well covered |
| `dashboard` | 24 | Low — well covered |
| `auth` | 15 | Low-medium |
| `ai_chat` | 2 | **High** — thin safety net |
| `integrations` | 1 | **High** — thin safety net |
| `monitoring` | 1 | **High** — thin safety net |

**This directly informs the to-be plan's Phase 0 audit** (retirement order
for the 7 domains): `ai_chat`, `integrations`, and `monitoring` carry the
highest regression risk *specifically because there's the least test
coverage to catch a mistake*, independent of whichever domain the
architecture audit flags as highest business-value. Recommend the Phase 0
audit combine both signals — architectural risk (already planned) and this
test-coverage signal (not yet planned) — rather than treating retirement
order as a purely architectural question.

**Recommendation:** for `ai_chat`, `integrations`, and `monitoring`
specifically, write smoke coverage *before* retiring the legacy copy, not
after — retiring the least-tested domains first, with no safety net, is the
highest-risk order available.

## Gap 3 (real, confirmed): no test coverage exists or is possible for the deploy-pipeline question

Neither `tests/smoke/` nor `tests/` can meaningfully test "which deploy
pipeline is canonical" — that's infrastructure topology, not application
behavior. Not a gap to close with a test; flagging so nobody expects
`qa-lead` to produce one. The existing verification for
`scripts/deploy_verified.sh` (its own independent proof-of-deploy checks) is
the right mechanism and already exists; the GHCR pipeline's equivalent
verification, if it becomes canonical, would need the same treatment.

## Gap 4 (real, confirmed): ADR 0002 schema migration has a detector but no smoke coverage

`schema-drift` gate (`scripts/verify.py`) catches drift at the ORM/DB level,
which is the right layer for that specific check — but nothing in
`tests/smoke/` would catch a *behavioral* regression from the Alembic squash
itself (e.g., a broken migration ordering that drops data silently during
the actual `flask db upgrade` cutover). This is deliberately out of smoke's
scope (it needs a maintenance-window rehearsal against a production-like
snapshot, per the ADR's own migration plan, not a routine test run).

**Recommendation:** the ADR 0002 migration's own plan should include a
dry-run rehearsal against a copy of production data before the real
maintenance window — this is a one-time operational check, not a
repeatable test to add to the suite.

## Summary for the user

Two gaps need action before their corresponding to-be-plan phases start:
Gap 1 (capability-store agreement has no browser check) and Gap 2 (the 3
least-tested overlapping domains should either get smoke coverage first or
be retired last, not first). Gaps 3 and 4 are not test gaps to close — they
need operational/rehearsal treatment instead, and are flagged so that
expectation is explicit rather than assumed.

**Handoff:** this bucket's SDLC pass (business-analyst → architects →
tech-lead → refuter → qa-lead) is now complete. No further role needs to run
automatically — the next real-world step is executing Phase 0 of the to-be
plan, which is new work outside this analysis bucket's scope.
