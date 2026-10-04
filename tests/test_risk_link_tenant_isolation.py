"""Linking a risk to another organisation's element must be refused.

Before this, risk_service.add_risk_link created the RiskEntityLink row
whatever organisation entity_id actually belonged to -- only the risk side
was tenant-scoped (via Risk.query.get_or_404), never the entity side. This
pins the fix for all three linkable entity types, plus the HTTP route that
already wraps add_risk_link, using two real organisations throughout.

Uses the shared fixtures in tests/conftest.py.
"""

import pytest

from app.services import risk_service

pytestmark = pytest.mark.usefixtures("db_session")


def _risk_in(org, title="Cross-tenant test risk"):
    return risk_service.create_risk(
        solution_id=None, title=title, description=None,
        likelihood=2, impact=2, owner=None, mitigation_plan=None,
    )


def test_linking_to_another_organisations_application_is_refused(
        app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk_entity_link import RiskEntityLink

    org_a, org_b = make_org("link-app-a"), make_org("link-app-b")
    with tenant_ctx(org_b.id):
        other_org_app = ApplicationComponent(name="Org B's App", organization_id=org_b.id)
        db_session.add(other_org_app)
        db_session.flush()
        other_org_app_id = other_org_app.id

    with tenant_ctx(org_a.id):
        risk = _risk_in(org_a)
        with pytest.raises(ValueError):
            risk_service.add_risk_link(risk.id, "application", other_org_app_id)

        assert RiskEntityLink.query.filter_by(risk_id=risk.id).count() == 0


def test_linking_to_another_organisations_solution_is_refused(
        app, db_session, make_org, tenant_ctx):
    from app.models.solution_models import Solution

    org_a, org_b = make_org("link-sol-a"), make_org("link-sol-b")
    with tenant_ctx(org_b.id):
        other_org_solution = Solution(name="Org B's Solution", organization_id=org_b.id)
        db_session.add(other_org_solution)
        db_session.flush()
        other_org_solution_id = other_org_solution.id

    with tenant_ctx(org_a.id):
        risk = _risk_in(org_a)
        with pytest.raises(ValueError):
            risk_service.add_risk_link(risk.id, "solution", other_org_solution_id)


def test_linking_to_another_organisations_programme_is_refused(
        app, db_session, make_org, tenant_ctx):
    from app.models.strategic import StrategicInitiative

    org_a, org_b = make_org("link-prog-a"), make_org("link-prog-b")
    with tenant_ctx(org_b.id):
        other_org_programme = StrategicInitiative(name="Org B's Programme", organization_id=org_b.id)
        db_session.add(other_org_programme)
        db_session.flush()
        other_org_programme_id = other_org_programme.id

    with tenant_ctx(org_a.id):
        risk = _risk_in(org_a)
        with pytest.raises(ValueError):
            risk_service.add_risk_link(risk.id, "programme", other_org_programme_id)


def test_linking_to_another_organisations_constraint_is_refused(
        app, db_session, make_org, tenant_ctx):
    """PR 316 re-review: "constraint" is a linkable entity type too (the
    canonical Risk model has no constraint_id column, so a risk derived
    from a constraint keeps that relationship as a RiskEntityLink) -- it
    must be refused cross-organisation exactly like the other three."""
    from app.models.solution_architect_models import (
        ConstraintType,
        SolutionAnalysisSession,
        SolutionConstraint,
        SolutionProblemDefinition,
    )
    from app.models.user import User

    org_a, org_b = make_org("link-constraint-a"), make_org("link-constraint-b")
    with tenant_ctx(org_b.id):
        other_org_user = User(email="link-constraint-org-b@example.com",
                              first_name="Org", last_name="B",
                              organization_id=org_b.id, confirmed=True)
        other_org_user.password = "not-used-in-tests-123"
        db_session.add(other_org_user)
        db_session.flush()

        session_obj = SolutionAnalysisSession(
            name="Org B Session", created_by_id=other_org_user.id,
        )
        db_session.add(session_obj)
        db_session.flush()

        problem_def = SolutionProblemDefinition(
            session_id=session_obj.id, problem_description="Org B's problem",
        )
        db_session.add(problem_def)
        db_session.flush()

        other_org_constraint = SolutionConstraint(
            problem_id=problem_def.id, name="Org B's Constraint",
            description="Org B only", constraint_type=ConstraintType.BUDGET,
        )
        db_session.add(other_org_constraint)
        db_session.flush()
        other_org_constraint_id = other_org_constraint.id

    with tenant_ctx(org_a.id):
        risk = _risk_in(org_a)
        with pytest.raises(ValueError):
            risk_service.add_risk_link(risk.id, "constraint", other_org_constraint_id)

        from app.models.risk_entity_link import RiskEntityLink
        assert RiskEntityLink.query.filter_by(risk_id=risk.id).count() == 0


def test_linking_within_the_same_organisation_still_works(app, db_session, make_org, tenant_ctx):
    """The fix must refuse a cross-tenant link without breaking the ordinary,
    same-organisation case the H1 tests already cover."""
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("link-same-org")
    with tenant_ctx(org.id):
        component = ApplicationComponent(name="Same Org App", organization_id=org.id)
        db_session.add(component)
        db_session.flush()

        risk = _risk_in(org)
        link = risk_service.add_risk_link(risk.id, "application", component.id)
        assert link.entity_id == component.id


def test_http_route_refuses_a_cross_organisation_link(app, db_session, make_org, client, login_as):
    """The existing POST /api/risks/<id>/links route already calls
    risk_service.add_risk_link -- confirm the refusal reaches the caller as a
    400, not a 500 or a silently-created link, with no route code changed."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk
    from app.models.user import User

    org_a, org_b = make_org("link-http-a"), make_org("link-http-b")

    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_b.id
        other_org_app = ApplicationComponent(name="Org B HTTP App", organization_id=org_b.id)
        db_session.add(other_org_app)
        db_session.flush()
        other_org_app_id = other_org_app.id

    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_a.id
        risk = Risk(organization_id=org_a.id, title="HTTP cross-org risk",
                    likelihood=1, impact=1)
        db_session.add(risk)
        user = User(email="risk-http-tester@example.com", first_name="Risk",
                    last_name="Tester", organization_id=org_a.id, confirmed=True)
        user.password = "not-used-in-tests-123"
        db_session.add(user)
        db_session.flush()
        risk_id = risk.id

    login_as(client, user)
    resp = client.post(
        f"/api/risks/{risk_id}/links",
        json={"entity_type": "application", "entity_id": other_org_app_id},
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "error" in resp.get_json()
