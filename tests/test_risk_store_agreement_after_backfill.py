"""PR 316 ruling item 3: show the `risks` store-agreement concept
(scripts/check_store_agreement.py) passing on this branch once the backfill
has run against a seeded database that holds solution risks -- the review
found no measurement covering this acceptance item at all.

The gate and its "risks" concept (orm:Risk vs GET /api/risks) already exist
on main (scripts/check_store_agreement.py, added by PR #249) and already pass
there against directly-seeded Risk rows -- see
tests/test_store_agreement_concepts.py::test_two_organisations_never_change_each_others_counts,
which seeds Risk rows and asserts the concept agrees, unchanged by this PR and
green on both branches. What main cannot do is run this specific scenario:
`flask backfill-solution-risk-merge` (this branch's own command) does not
exist there, so solution_risks rows never reach the canonical store at all,
and there is nothing for the concept to observe.

Uses the shared fixtures in tests/conftest.py.
"""
import importlib.util
import os
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_store_agreement.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_store_agreement", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org):
    from app.models.solution_models import Solution

    solution = Solution(name=f"Store Agreement Solution {uuid.uuid4().hex[:6]}",
                        organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _solution_risk(db_session, org, solution, **overrides):
    from app.models.solution_lifecycle_models import SolutionRisk

    fields = dict(
        solution_id=solution.id, organization_id=org.id,
        risk_name="Vendor lock-in", risk_description="No viable exit path.",
        impact="high", probability="medium", status="open",
    )
    fields.update(overrides)
    risk = SolutionRisk(**fields)
    db_session.add(risk)
    db_session.flush()
    return risk


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"store-agree-{uuid.uuid4().hex[:8]}@example.com",
                first_name="Store", last_name="Agreement",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _run_backfill(app, organization_id):
    runner = app.test_cli_runner()
    return runner.invoke(
        args=["backfill-solution-risk-merge", "--organization-id", str(organization_id)]
    )


def test_risks_concept_agrees_after_the_backfill_merges_solution_risks(
        app, db_session, make_org, tenant_ctx):
    from app import db

    org = make_org("store-agree-risks")
    solution = _solution(db_session, org)
    _solution_risk(db_session, org, solution, risk_name="Vendor lock-in")
    _solution_risk(db_session, org, solution, risk_name="Key-person dependency")
    user = _user(db_session, org)
    db_session.commit()
    org_id = org.id

    result = _run_backfill(app, org_id)
    assert result.exit_code == 0, result.output

    with tenant_ctx(org_id):
        observations, notes = gate.observe_tenant(
            app, db, org_id, user, concepts={"risks": gate.CONCEPTS["risks"]})
    findings, more_notes = gate.compare(observations)
    notes = notes + more_notes

    assert findings == [], (findings, notes, observations)
    assert not any("no-evidence" in note for note in notes), notes

    # Real evidence, not an accidental all-zero pass: the two backfilled rows
    # are visible through both surfaces the concept compares.
    counts = {name: count for name, count, _scope, _unscoped in observations["risks"]}
    assert counts["orm:Risk"] == 2
    assert counts["GET /api/risks"] == 2


def test_risks_concept_also_passes_on_directly_seeded_data_with_no_backfill_involved(
        app, db_session, make_org, tenant_ctx):
    """The part of this concept that predates this PR -- orm:Risk vs
    GET /api/risks over directly-seeded Risk rows, with no solution_risks or
    backfill involved at all -- already passes on main (same assertion as
    tests/test_store_agreement_concepts.py's own two-organisation test), so no
    fail-on-main line is claimed for this half."""
    from app import db
    from app.models.risk import Risk

    org = make_org("store-agree-risks-direct")
    user = _user(db_session, org)
    for i in range(3):
        db_session.add(Risk(title=f"Direct risk {i}", likelihood=2, impact=3,
                            organization_id=org.id))
    db_session.commit()
    org_id = org.id

    with tenant_ctx(org_id):
        observations, notes = gate.observe_tenant(
            app, db, org_id, user, concepts={"risks": gate.CONCEPTS["risks"]})
    findings, more_notes = gate.compare(observations)
    notes = notes + more_notes

    assert findings == [], (findings, notes, observations)
    counts = {name: count for name, count, _scope, _unscoped in observations["risks"]}
    assert counts["orm:Risk"] == 3
    assert counts["GET /api/risks"] == 3
