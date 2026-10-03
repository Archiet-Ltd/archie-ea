"""PR 2: programme risk routes — the programme screen can now create risks,
link them to elements (applications, solutions, programmes), and set scores,
all through the canonical risk_service. Two-organisation isolation is enforced.

Uses the shared fixtures in tests/conftest.py.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org, name="Prog Risk Solution"):
    from app.models.solution_models import Solution

    solution = Solution(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    return component


def _programme(db_session, org, name):
    from app.models.strategic import StrategicInitiative

    programme = StrategicInitiative(name=name, organization_id=org.id)
    db_session.add(programme)
    db_session.flush()
    return programme


def _user(db_session, org, label):
    from app.models.user import User

    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
                first_name="Prog", last_name="Risk",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def test_programme_can_create_risk_and_link_to_elements(
        app, db_session, make_org, client, login_as):
    """A risk created via the programme screen is a canonical Risk linked to
    the programme via risk_entity_links (entity_type="programme")."""
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    org = make_org("prog-risk-create")
    programme = _programme(db_session, org, "Test Programme")
    user = _user(db_session, org, "prog-risk-creator")
    programme_id = programme.id

    login_as(client, user)

    # Create a risk via the canonical API (what the programme screen does)
    create = client.post("/api/risks", json={
        "title": "Programme risk from cockpit",
        "description": "Risk raised from programme screen",
        "likelihood": 3,
        "impact": 4,
        "owner": "Programme manager",
        "mitigation_plan": "Monitor weekly",
    })
    assert create.status_code == 201, create.get_data(as_text=True)
    risk_id = create.get_json()["id"]

    # Link it to the programme
    link_resp = client.post(
        f"/api/risks/{risk_id}/links",
        json={"entity_type": "programme", "entity_id": programme_id},
    )
    assert link_resp.status_code == 201, link_resp.get_data(as_text=True)

    # Verify the canonical risk exists and is linked
    risk = Risk.query.filter_by(id=risk_id).one()
    assert risk.title == "Programme risk from cockpit"
    assert risk.organization_id == org.id
    link = RiskEntityLink.query.filter_by(risk_id=risk_id).one()
    assert (link.entity_type, link.entity_id) == ("programme", programme_id)

    # The GET /api/entities/programme/<id>/risks endpoint returns it
    list_resp = client.get(f"/api/entities/programme/{programme_id}/risks")
    assert list_resp.status_code == 200
    risks = list_resp.get_json()
    assert len(risks) == 1
    assert risks[0]["id"] == risk_id
    assert risks[0]["title"] == "Programme risk from cockpit"


def test_programme_risk_can_link_multiple_elements(
        app, db_session, make_org, client, login_as):
    """A single risk can be linked to multiple elements (application, solution,
    programme) in one action each."""
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    org = make_org("prog-risk-multi-link")
    programme = _programme(db_session, org, "Multi-link Programme")
    solution = _solution(db_session, org, "Linked Solution")
    application = _application(db_session, org, "Linked App")
    user = _user(db_session, org, "prog-risk-multi")
    programme_id = programme.id

    login_as(client, user)

    create = client.post("/api/risks", json={
        "title": "Cross-element risk",
        "likelihood": 2, "impact": 3,
    })
    assert create.status_code == 201
    risk_id = create.get_json()["id"]

    # Link to three different entity types
    for entity_type, entity_id in (
        ("programme", programme_id),
        ("solution", solution.id),
        ("application", application.id),
    ):
        link_resp = client.post(
            f"/api/risks/{risk_id}/links",
            json={"entity_type": entity_type, "entity_id": entity_id},
        )
        assert link_resp.status_code == 201, link_resp.get_data(as_text=True)

    links = client.get(f"/api/risks/{risk_id}/links").get_json()
    assert {link["entity_type"] for link in links} == {"programme", "solution", "application"}
    assert len(links) == 3

    # The programme's risk list shows the risk with all three links
    list_resp = client.get(f"/api/entities/programme/{programme_id}/risks")
    risks = list_resp.get_json()
    assert len(risks) == 1
    assert len(risks[0]["entity_links"]) == 3


def test_programme_risk_scores_work_through_canonical_api(
        app, db_session, make_org, client, login_as):
    """Inherent and residual scores set via the programme screen use the
    canonical risk_service.set_risk_score and produce history rows."""
    from app.models.risk import Risk
    from app.models.risk_score_history import RiskScoreHistory

    org = make_org("prog-risk-scores")
    programme = _programme(db_session, org, "Score Programme")
    user = _user(db_session, org, "prog-risk-scorer")
    programme_id = programme.id

    login_as(client, user)

    create = client.post("/api/risks", json={
        "title": "Scored programme risk",
        "likelihood": 3, "impact": 3,
    })
    assert create.status_code == 201
    risk_id = create.get_json()["id"]

    client.post(f"/api/risks/{risk_id}/links",
                json={"entity_type": "programme", "entity_id": programme_id})

    # Set inherent score
    inherent = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "inherent", "likelihood": 4, "impact": 5},
    )
    assert inherent.status_code == 200

    # Set residual score
    residual = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "residual", "likelihood": 2, "impact": 2},
    )
    assert residual.status_code == 200

    # Read back: both scores visible
    read_back = client.get(f"/api/risks/{risk_id}").get_json()
    assert (read_back["inherent_likelihood"], read_back["inherent_impact"]) == (4, 5)
    assert (read_back["residual_likelihood"], read_back["residual_impact"]) == (2, 2)

    # Change residual score
    residual_again = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "residual", "likelihood": 1, "impact": 1},
    )
    assert residual_again.status_code == 200

    # Two residual history rows
    history = client.get(f"/api/risks/{risk_id}/scores?score_kind=residual").get_json()
    assert len(history) == 2
    assert [(row["likelihood"], row["impact"]) for row in history] == [(2, 2), (1, 1)]

    # Inherent history untouched
    inherent_history = client.get(f"/api/risks/{risk_id}/scores?score_kind=inherent").get_json()
    assert len(inherent_history) == 1


def test_programme_risk_cross_organisation_isolation(
        app, db_session, make_org, client, login_as, tenant_ctx):
    """Organisation B cannot link a risk to organisation A's programme,
    nor see organisation A's risks on its own programme."""
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    org_a, org_b = make_org("prog-risk-a"), make_org("prog-risk-b")
    programme_a = _programme(db_session, org_a, "Org A Programme")
    programme_b = _programme(db_session, org_b, "Org B Programme")
    user_a = _user(db_session, org_a, "prog-risk-user-a")
    user_b = _user(db_session, org_b, "prog-risk-user-b")
    programme_a_id = programme_a.id
    programme_b_id = programme_b.id

    # Org A creates a risk and links it to their programme
    login_as(client, user_a)
    create_a = client.post("/api/risks", json={
        "title": "Org A risk", "likelihood": 2, "impact": 2,
    })
    assert create_a.status_code == 201
    risk_a_id = create_a.get_json()["id"]
    client.post(f"/api/risks/{risk_a_id}/links",
                json={"entity_type": "programme", "entity_id": programme_a_id})

    # Org B tries to link to Org A's programme - refused
    login_as(client, user_b)
    create_b = client.post("/api/risks", json={
        "title": "Org B risk", "likelihood": 2, "impact": 2,
    })
    assert create_b.status_code == 201
    risk_b_id = create_b.get_json()["id"]

    link_resp = client.post(
        f"/api/risks/{risk_b_id}/links",
        json={"entity_type": "programme", "entity_id": programme_a_id},
    )
    assert link_resp.status_code == 400, link_resp.get_data(as_text=True)
    assert "error" in link_resp.get_json()

    # Org B's risk list for their own programme shows only their risk
    client.post(f"/api/risks/{risk_b_id}/links",
                json={"entity_type": "programme", "entity_id": programme_b_id})
    list_b = client.get(f"/api/entities/programme/{programme_b_id}/risks").get_json()
    assert len(list_b) == 1
    assert list_b[0]["id"] == risk_b_id

    # Org A's risk list shows only their risk
    login_as(client, user_a)
    list_a = client.get(f"/api/entities/programme/{programme_a_id}/risks").get_json()
    assert len(list_a) == 1
    assert list_a[0]["id"] == risk_a_id

    # At ORM level, cross-tenant queries return nothing
    with tenant_ctx(org_b.id):
        assert Risk.query.filter_by(id=risk_a_id).first() is None
    with tenant_ctx(org_a.id):
        assert Risk.query.filter_by(id=risk_b_id).first() is None


def test_programme_rollup_reads_canonical_risks(
        app, db_session, make_org, tenant_ctx):
    """ProgrammeGovernanceService.rollup reads risks from the canonical
    risks table via risk_service, not from solution_risks."""
    from app.modules.solutions_strategic.v2.services.programme_governance_service import (
        ProgrammeGovernanceService,
    )
    from app.services import risk_service
    from app.models.risk import Risk

    org = make_org("prog-rollup-risks")
    programme = _programme(db_session, org, "Rollup Programme")
    solution = _solution(db_session, org, "Member Solution")
    solution.initiative_id = programme.id
    db_session.flush()
    programme_id = programme.id
    solution_id = solution.id

    with tenant_ctx(org.id):
        # Create a canonical risk linked to the programme
        risk = risk_service.create_risk(
            solution_id=None, title="Canonical programme risk",
            description="Direct canonical", likelihood=3, impact=4,
            owner="PM", mitigation_plan="Mitigate",
        )
        risk_service.add_risk_link(risk.id, "programme", programme_id)

        # Also create a solution risk linked to the member solution
        sol_risk = risk_service.create_risk(
            solution_id=solution_id, title="Solution risk",
            description="On member solution", likelihood=2, impact=3,
            owner="SM", mitigation_plan="Handle",
        )
        risk_service.add_risk_link(sol_risk.id, "solution", solution_id)

        rollup = ProgrammeGovernanceService.rollup(programme_id)
        assert rollup is not None
        # The rollup counts risks linked to the programme (entity_type=programme)
        # The solution risk is linked to the solution, not the programme directly
        assert rollup["risks"]["total"] == 1
        assert rollup["risks"]["by_impact"].get("high", 0) == 1  # 3*4=12 -> high


def test_entities_risks_endpoint_refuses_invalid_entity_type(
        app, db_session, make_org, client, login_as):
    """GET /api/entities/<type>/<id>/risks validates entity_type."""
    org = make_org("prog-risk-invalid-type")
    user = _user(db_session, org, "prog-risk-invalid")
    login_as(client, user)

    resp = client.get("/api/entities/invalid_type/123/risks")
    assert resp.status_code == 400
    assert "error" in resp.get_json()