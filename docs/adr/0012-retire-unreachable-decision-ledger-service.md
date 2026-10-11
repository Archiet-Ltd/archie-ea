# ADR 0012 — Retire the unreachable decision-ledger service and ARB workflow service

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

The decision register consolidation found that `app/services/decision_ledger.py`'s
`DecisionLedger` class — an in-memory ledger with cryptographic provenance, distinct from the
`DecisionLedger` **model** in `app/models/decision_ledger.py` — has two real defects: its
constructor loads every organisation's rows with no tenant predicate when run outside a request
context, and its `record_decision()` writes a new row with `organization_id` left NULL and no
pairing into the canonical `architecture_decisions` register.

Both files were added in the same commit (`f35ff1a6`) and were never wired to anything reachable:

### Evidence

```
$ git grep -n "from app.services.decision_ledger import\|from app\.services import decision_ledger\|services\.decision_ledger" origin/main -- app tests
origin/main:app/models/decision_ledger.py:18:    app.services.decision_ledger.DecisionLedger._load_existing_ledger()
origin/main:app/services/arb_workflow.py:26:from app.services.decision_ledger import DecisionLedger, DecisionStatus
```

The first hit is a docstring cross-reference in the DB model (not a real import). The second is the
only real caller: `app/services/arb_workflow.py`'s `ARBWorkflowService`, whose own `__init__`
constructs `DecisionLedger()` eagerly — which is exactly how the unfenced read reproduces.

```
$ git grep -n "from app.services.arb_workflow import\|import arb_workflow\b" origin/main -- app tests
(no output)
```

`ARBWorkflowService` itself has zero importers anywhere in the codebase — no route, job, CLI
command or test constructs it. (It is not the same class as `app/services/arb_workflow_service.py`'s
`ARBWorkflowService`, a same-named class in a sibling file that *is* live, used by
`app/routes/enterprise_api.py`, and does not reference `decision_ledger.py` at all — this was flagged
as a possible source of confusion in an earlier review round.) Both `decision_ledger.py`'s
service classes and all of `arb_workflow.py` are unreachable from the running product.

## Decision

Remove both modules and their one cross-reference:

- delete `app/services/decision_ledger.py` (`DecisionType`, `DecisionStatus`, `DecisionEntry`,
  `DecisionLedger` — the service classes; the DB model of the same name in
  `app/models/decision_ledger.py` is untouched and stays live, fenced by TenantMixin per an earlier
  fix in this same consolidation)
- delete `app/services/arb_workflow.py` (`WorkflowStage`, `WorkflowAction`, `WorkflowStep`,
  `WorkflowInstance`, `ARBWorkflowService` — its only caller)
- update the docstring cross-reference in `app/models/decision_ledger.py` to stop naming the now-
  deleted service

## Successor

None needed. ARB workflow tracking is live today through `app/services/arb_workflow_service.py`'s
`ARBWorkflowService` (a different class, used by `app/routes/enterprise_api.py` and
`arb_workflow_routes.py`) and the `architecture_decisions` canonical register this brief
consolidates into. If a cryptographically-chained decision ledger is wanted again, it should be
built as a genuinely tenant-scoped, paired writer from the start rather than resurrecting this one.

## Consequences

- No behaviour change: neither module was reachable, so no route, job or live test exercised them.
- Closes a final-check finding (decision_ledger service leaves a second writable,
  unfenced, unpaired register) by removing the register rather than fixing a path nothing used.
- `tests/test_decision_ledger_service_retired.py` pins the removal — asserts both modules can no
  longer be imported and nothing else in the codebase still references them.

## Reversible variant

```
git log --diff-filter=D --format=%H -1 -- app/services/decision_ledger.py
git show <that sha>^:app/services/decision_ledger.py > app/services/decision_ledger.py
git show <that sha>^:app/services/arb_workflow.py > app/services/arb_workflow.py
```
