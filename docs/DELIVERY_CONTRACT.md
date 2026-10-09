# Delivery contract

Binding on every agent that works this repository — human or model. Two rules,
intended to be enforced by the `evidence-contract` gate
(`scripts/check_evidence_contract.py`) so neither depends on anyone
remembering them. **As of 3 Sep 2026 that gate is not registered in
`scripts/verify.py`'s `build_gates()` and does not run automatically** — see
the correction note under "Roles are gate families" below. Until it is wired
in, both rules currently *do* depend on someone remembering them.

## Why this exists

On 30 August 2026 the model running this repository announced three conclusions
it had not measured: that the audit harness was broken (it had just measured
1,700 page loads), that the Health Scorecard was hanging with a scalability
defect (it answers in 0.04s), and that the product lacked personas it has. All
three were corrected within minutes and none reached production — because the
*artifacts* were measured even while the *narration* was not.

That asymmetry is the design principle. An unverified sentence in a report
costs an hour. An unverified change that ships costs production. This contract
makes the measurement the deliverable, so the second cannot happen quietly.

It follows the same logic as the rest of `verify.py`: assume the builder is
unreliable and check the work, rather than asking the builder to be careful.

## Rule 1 — a behavioural change carries its measurement

A commit that changes behaviour under `app/` lands with **either**:

- a test change in the same commit, **or**
- an `Evidence:` trailer naming the command that was run and what it returned.

```
Evidence: pytest tests/journeys/test_journey_cto.py -q -> 4 passed
Evidence: curl -o /dev/null -w '%{http_code} %{time_total}' /dashboard/health -> 200 in 0.45s
```

"I checked it" is not evidence. A command and its output is. Prefer the test:
a trailer proves it worked once, a test proves it keeps working.

Exempt, because they change nothing a user can observe: `docs/`, `tests/`,
`scripts/`, `migrations/`, and `.md`/`.json`/`.css` files.

## Rule 2 — a gate carries its proof

`TESTING_STANDARD.md` rule 7 has always required that a new gate be proven
against known-bad input: *reintroduce the defect, watch the gate go red,
restore, watch it go green.* Nothing enforced it — which is exactly the hole an
agent walks through by writing a checker that has never once gone red and
reporting it as coverage.

Every checker registered in `verify.py` must carry a `Proven-against:` line in
its module docstring, naming the input it was observed to fail on:

```
Proven-against: the "CTO" entry removed from DEFAULT_GROUP_ROLE_MAP — red at 1
naming 'cto' as unprovisionable, green at 0 when restored.
```

This is a ratchet, not a wall. The checkers that predate the rule are counted
as debt and the number can only go down. Every **new** gate carries its proof
on the day it lands.

## Roles are gate families, not job titles

A role nobody has built machinery for is a role being claimed rather than
played. A role is therefore defined here as a family of gates in
`scripts/verify.py`, identified by the `tags=[...]` those gates carry — so
"did we act as the security architect?" is a question with an answer, not an
assertion in a report.

**Corrected 4 Sep 2026 — the table below is measured from the registry.**
The 31 Aug re-measurement claimed 67 gates; `build_gates()` in `scripts/
verify.py` now registers **54**, and `docs-drift` re-derives each role's count
from those gates' actual `tags=[...]` rather than trusting this prose.

The four `ai-*` checkers, `evidence-contract`, and `role-gate-coverage` are
registered and execute in the normal verifier. Seven declared roles still
resolve to no gate and remain visible ratcheted debt; a zero in the table is
not represented as qualification evidence.

| Role | Gate tags | Gates (re-measured 3 Sep 2026, `docs-drift` keeps this row honest from here on) |
|---|---|---|
| UX / frontend architect (lint only — see note) | `ui`, `a11y` | 25 (no gate carries `a11y`) |
| security architect | `security`, `airgap` | 17 |
| QA / test lead | `qa`, `runtime` | 8 |
| software / technical architect | `architecture`, `correctness` | 3 (`correctness` plus one `architecture` gate) |
| data architect | `schema`, `db` | 2 (`db` only; no gate carries `schema`) |
| CTO / delivery lead | `process`, `deps` | 2 (`deps` only; no gate carries `process`) |
| AI / ML architect | `ai` | 4 |
| product architect | `product`, `journey` | 0 |
| business architect | `business` | 0 |
| service designer | `handoff` | 0 |
| data / evidence analyst | `evidence` | 2 |
| integration architect | `integration` | 0 |
| UI / interaction architect | `rendered` | 0 |
| information architect | `wayfinding` | 0 |
| content designer | `content` | 0 |

Seven of fifteen roles still read zero. Closing them requires applying a
missing tag to a gate that already covers the role's real concern, building
the missing machinery, or recording a reviewable `role-gate-ok: <reason>`
exception. The running `role-gate-coverage` ratchet prevents that debt from
growing silently.

Re-run the map when adding a role or a gate. A zero here is a coverage hole,
and it is worth more than any amount of deliberation about whether coverage is
adequate. **Once `role-gate-coverage` is registered** it should ratchet the
count of zero-gate roles so a role added to this table without machinery
fails the build; the per-row escape hatch is `role-gate-ok: <reason>`.

## Running it

```bash
python scripts/check_evidence_contract.py                  # HEAD
python scripts/check_evidence_contract.py --staged         # pre-commit
python scripts/check_evidence_contract.py --range A..B     # a range
python scripts/check_evidence_contract.py --rule provenance
```

## A gate that was built and deliberately not shipped

30 Aug 2026. The QA audit's High #1 was a click handler calling a method its
component did not have (`submitCreateWorkPackage is not a function`) -- a
complete create modal wired to nothing, failing silently. A static gate for that
class was written, and then dropped.

It reached 357 findings, and the sampled ones were false: it could not resolve
methods across `dataTable.extend` mixins, bundled components, or factory bodies
longer than its scan window, so it reported handlers as missing that were
defined and working. The weaker, zero-false-positive variant -- flag a name
that appears nowhere in any shipped JS -- would not have caught the motivating
defect, because that method DID exist, in a different template's component.

So the class is real and the static check is not honest enough to enforce it.
It belongs to the runtime layers instead: the archetype walkthrough clicking
the control, and `production_readiness_audit.py`'s console-error capture, which
is how the audit found it in the first place.

Recorded here rather than quietly abandoned, because "no gate" and "a gate we
gave up on" are different facts, and the next person deserves the second one.
A gate with false positives is worse than no gate -- that rule applies to gates
written to satisfy a request, too.
