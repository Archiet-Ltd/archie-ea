"""motivation_bridge_service must pass organization_id when creating Outcome/Principle/
Driver/Goal/MotivationBridgeLink.

Once Outcome and Principle gained TenantMixin (organization_id nullable), the
existing _find_or_create_outcome()/_find_or_create_principle() -- which run
under the request-context-free `flask bridge-motivation` CLI -- silently
started writing organization_id=NULL on every multi-org install, because
they built the model without passing the org_id parameter they already
receive. NULL never matches TenantMixin's `organization_id = g.current_org_id`
SELECT filter, so every such row becomes permanently invisible to every
tenant-scoped view -- the exact backlog app/commands/backfill_outcome_org.py
exists to drain, growing on every future run with no error signal.

_create_archimate_element() (same file) already had the correct pattern and
an explanatory comment; _find_or_create_outcome/_principle just didn't apply
it to the models they construct directly.

Both assertions below are made deliberately with NO tenant context active
around the _find_or_create_* call itself (matching the real CLI's
request-context-free execution), guarded two ways -- both needed, discovered
the hard way while writing this test:

1. A second, decoy organization is created first. Without it,
   app/models/mixins/core.py's _default_org_id() single-org fallback can
   silently make the test pass even when the code under test never passes
   org_id at all, whenever the test database happens to hold exactly one
   organization at that moment.
2. g.current_org_id is explicitly cleared after the tenant_ctx `with` block
   used to set up fixture data. This repo's own tests/conftest.py documents
   why: db_session holds ONE app context open for the whole test, and
   flask.g is bound to the app context, not the (possibly nested) request
   context tenant_ctx pushes -- so g.current_org_id set inside `with
   tenant_ctx(...)` survives past the block and leaks into code that runs
   after it in the same test (see the login_as fixture's docstring for the
   identical trap with a different g attribute).

Without BOTH guards, this test passed against the deliberately-broken,
pre-fix code during development -- twice, for two different accidental
reasons -- which is exactly the failure mode ("a test that would pass either
way") this repo's own delivery contract calls out as worse than no test.

A later review found the identical gap in _find_or_create_driver/
_find_or_create_goal and the MotivationBridgeLink construction in
_promote_one once Driver, Goal and MotivationBridgeLink also gained
TenantMixin: the same file already passed organization_id for
Outcome/Principle but not for these three. Tests below extend this file's
own established pattern to them.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _clear_leaked_org_context():
    """Undo tenant_ctx's g.current_org_id leak (see module docstring)."""
    from flask import g, has_app_context

    if has_app_context() and hasattr(g, "current_org_id"):
        delattr(g, "current_org_id")


def _make_problem(db_session, make_org, suffix):
    from app.models.organization import Organization  # noqa: F401  (decoy org uses this)
    from app.models.solution_architect_models import SolutionAnalysisSession, SolutionProblemDefinition
    from app.models.solution_models import Solution
    from app.models.user import User

    org = make_org(f"bridge-org-scoping-{suffix}")
    # Decoy: guarantees >1 organization exists, so _default_org_id()'s
    # single-org fallback can never silently mask a missing organization_id.
    make_org(f"bridge-org-scoping-decoy-{suffix}")

    user = User(email=f"bridge-scope-{suffix}@example.com", first_name="Bridge", last_name="Scope")
    db_session.add(user)
    db_session.flush()

    session = SolutionAnalysisSession(
        name=f"session-{suffix}", created_by_id=user.id, organization_id=org.id
    )
    db_session.add(session)
    db_session.flush()

    solution = Solution(
        name=f"solution-{suffix}", created_by_id=user.id, analysis_session_id=session.id, organization_id=org.id
    )
    db_session.add(solution)
    db_session.flush()

    problem = SolutionProblemDefinition(
        session_id=session.id, problem_description="test", organization_id=org.id
    )
    db_session.add(problem)
    db_session.flush()

    return org, solution, problem, user


def test_find_or_create_outcome_sets_organization_id(db_session, make_org, tenant_ctx):
    """Outside a request context, org_id must be passed explicitly -- not left
    to TenantMixin's before_flush default, which cannot see a CLI's tenant."""
    from app.models.solution_outcomes import OutcomeType, SolutionOutcome, TrackingStatus
    from app.services.motivation_bridge_service import _find_or_create_outcome

    org, solution, problem, user = _make_problem(db_session, make_org, "outcome")

    with tenant_ctx(org.id):
        so = SolutionOutcome(
            solution_id=solution.id,
            outcome_type=OutcomeType.COST,
            name="Reduce onboarding time",
            description="test outcome",
            tracking_status=TrackingStatus.NOT_STARTED,
            created_by_id=user.id,
        )
        db_session.add(so)
        db_session.flush()

    _clear_leaked_org_context()
    # Deliberately outside tenant_ctx -- matches `flask bridge-motivation`'s
    # real execution environment (no request context at all).
    outcome, created = _find_or_create_outcome(so, "Reduce onboarding time", org_id=org.id)

    assert created is True
    assert outcome.organization_id == org.id, (
        "regression: Outcome created by the bridge-motivation CLI with organization_id=NULL "
        "is permanently invisible to every tenant-scoped view"
    )


def test_find_or_create_principle_sets_organization_id(db_session, make_org, tenant_ctx):
    from app.models.solution_architect_models import SolutionPrinciple
    from app.services.motivation_bridge_service import _find_or_create_principle

    org, solution, problem, user = _make_problem(db_session, make_org, "principle")

    with tenant_ctx(org.id):
        sp = SolutionPrinciple(
            problem_id=problem.id,
            name="Cloud-first",
            statement="Prefer cloud-native services over self-hosted infrastructure",
        )
        db_session.add(sp)
        db_session.flush()

    _clear_leaked_org_context()
    # Deliberately outside tenant_ctx -- see above.
    principle, created = _find_or_create_principle(sp, "Cloud-first", org_id=org.id)

    assert created is True
    assert principle.organization_id == org.id, (
        "regression: Principle created by the bridge-motivation CLI with organization_id=NULL "
        "is permanently invisible to every tenant-scoped view"
    )


def test_find_or_create_driver_sets_organization_id(db_session, make_org, tenant_ctx):
    """Outside a request context, org_id must be passed explicitly -- not left
    to TenantMixin's before_flush default, which cannot see a CLI's tenant."""
    from app.models.solution_architect_models import DriverType, SolutionDriver
    from app.services.motivation_bridge_service import _find_or_create_driver

    org, solution, problem, user = _make_problem(db_session, make_org, "driver")

    with tenant_ctx(org.id):
        sd = SolutionDriver(
            problem_id=problem.id,
            name="Regulatory pressure",
            description="test driver",
            driver_type=DriverType.EXTERNAL,
            source="Market analysis",
        )
        db_session.add(sd)
        db_session.flush()

    _clear_leaked_org_context()
    # Deliberately outside tenant_ctx -- matches `flask bridge-motivation`'s
    # real execution environment (no request context at all).
    driver, created = _find_or_create_driver(sd, "Regulatory pressure", org_id=org.id)

    assert created is True
    assert driver.organization_id == org.id, (
        "regression: Driver created by the bridge-motivation CLI with organization_id=NULL "
        "is permanently invisible to every tenant-scoped view"
    )


def test_find_or_create_goal_sets_organization_id(db_session, make_org, tenant_ctx):
    from app.models.solution_architect_models import SolutionGoal
    from app.services.motivation_bridge_service import _find_or_create_goal

    org, solution, problem, user = _make_problem(db_session, make_org, "goal")

    with tenant_ctx(org.id):
        sg = SolutionGoal(
            problem_id=problem.id,
            name="Cut onboarding time in half",
            description="test goal",
            priority=3,
        )
        db_session.add(sg)
        db_session.flush()

    _clear_leaked_org_context()
    # Deliberately outside tenant_ctx -- see above.
    goal, created = _find_or_create_goal(sg, "Cut onboarding time in half", org_id=org.id)

    assert created is True
    assert goal.organization_id == org.id, (
        "regression: Goal created by the bridge-motivation CLI with organization_id=NULL "
        "is permanently invisible to every tenant-scoped view"
    )


def test_promote_solution_motivation_sets_organization_id_on_the_bridge_link(
    db_session, make_org, tenant_ctx
):
    """The full bridge-motivation command path: a MotivationBridgeLink built by
    _promote_one must carry the solution's own organisation, and a second
    organisation's run must never see the first organisation's bridged rows."""
    from app.models.motivation import MotivationBridgeLink
    from app.models.solution_architect_models import DriverType, SolutionDriver
    from app.services.motivation_bridge_service import promote_solution_motivation

    org_a, solution_a, problem_a, user_a = _make_problem(db_session, make_org, "promote-a")
    org_b, solution_b, problem_b, user_b = _make_problem(db_session, make_org, "promote-b")

    with tenant_ctx(org_a.id):
        db_session.add(SolutionDriver(
            problem_id=problem_a.id, name="Org A driver", driver_type=DriverType.INTERNAL,
        ))
        db_session.flush()

    _clear_leaked_org_context()
    # Deliberately outside tenant_ctx -- matches the real `flask
    # bridge-motivation` execution environment (no request context at all).
    promote_solution_motivation(solution_a.id)

    link = db_session.query(MotivationBridgeLink).filter_by(solution_id=solution_a.id).first()
    assert link is not None, "expected promote_solution_motivation to bridge the seeded driver"
    assert link.organization_id == org_a.id, (
        "regression: MotivationBridgeLink created by the bridge-motivation CLI with "
        "organization_id=NULL is permanently invisible to every tenant-scoped view"
    )

    # Org B's own (empty) run must never surface org A's bridged link.
    promote_solution_motivation(solution_b.id)
    org_b_links = db_session.query(MotivationBridgeLink).filter_by(solution_id=solution_b.id).all()
    assert all(row.organization_id != org_a.id for row in org_b_links)
