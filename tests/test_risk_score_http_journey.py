"""PR 316 ruling item 2: an HTTP-level test as the security architect
persona -- create one risk, link three elements, set inherent and residual
scores, read it back, change the residual score, read again, and see two
residual history rows. The full browser journey (the element picker) is PR 2;
this proves the backend flow works end to end over the real HTTP surface, not
only at the risk_service layer (tests/test_risk_score_history.py already
covers that, and tests/test_risk_link_tenant_isolation.py already covers
cross-organisation link refusal over HTTP).

Uses the shared fixtures in tests/conftest.py.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org, name="Score Journey Solution"):
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


def _security_architect(db_session, org):
    from app.models.user import User

    user = User(email=f"sec-arch-{uuid.uuid4().hex[:8]}@example.com",
                first_name="Security", last_name="Architect",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def test_security_architect_creates_links_scores_and_rescoreds_a_risk_over_http(
        app, db_session, make_org, client, login_as):
    org = make_org("sec-arch-score")
    solution = _solution(db_session, org)
    application = _application(db_session, org, "Billing Gateway")
    programme = _programme(db_session, org, "Core Modernisation")
    architect = _security_architect(db_session, org)

    login_as(client, architect)

    # Create one risk.
    create = client.post("/api/risks", json={
        "title": "Unencrypted data at rest on the billing gateway",
        "description": "PII stored without column-level encryption.",
        "likelihood": 3, "impact": 4, "owner": "Security team",
    })
    assert create.status_code == 201, create.get_data(as_text=True)
    risk_id = create.get_json()["id"]

    # Link three elements: a solution, an application and a programme.
    for entity_type, entity_id in (
        ("solution", solution.id),
        ("application", application.id),
        ("programme", programme.id),
    ):
        link_resp = client.post(
            f"/api/risks/{risk_id}/links",
            json={"entity_type": entity_type, "entity_id": entity_id},
        )
        assert link_resp.status_code == 201, link_resp.get_data(as_text=True)

    links = client.get(f"/api/risks/{risk_id}/links").get_json()
    assert {link["entity_type"] for link in links} == {"solution", "application", "programme"}
    assert len(links) == 3

    # Set inherent and residual scores.
    inherent = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "inherent", "likelihood": 4, "impact": 5},
    )
    assert inherent.status_code == 200, inherent.get_data(as_text=True)
    residual = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "residual", "likelihood": 2, "impact": 2},
    )
    assert residual.status_code == 200, residual.get_data(as_text=True)

    # Read it back: both scores and the three links are visible.
    read_back = client.get(f"/api/risks/{risk_id}").get_json()
    assert (read_back["inherent_likelihood"], read_back["inherent_impact"]) == (4, 5)
    assert (read_back["residual_likelihood"], read_back["residual_impact"]) == (2, 2)
    assert len(read_back["entity_links"]) == 3

    # Change the residual score.
    residual_again = client.post(
        f"/api/risks/{risk_id}/scores",
        json={"score_kind": "residual", "likelihood": 1, "impact": 1},
    )
    assert residual_again.status_code == 200, residual_again.get_data(as_text=True)

    # Read again: the risk reflects the new residual score ...
    read_again = client.get(f"/api/risks/{risk_id}").get_json()
    assert (read_again["residual_likelihood"], read_again["residual_impact"]) == (1, 1)
    # ... the inherent score is untouched by the residual change ...
    assert (read_again["inherent_likelihood"], read_again["inherent_impact"]) == (4, 5)

    # ... and two residual history rows exist, oldest first.
    history = client.get(f"/api/risks/{risk_id}/scores?score_kind=residual").get_json()
    assert len(history) == 2
    assert [(row["likelihood"], row["impact"]) for row in history] == [(2, 2), (1, 1)]

    # The inherent history is untouched by the residual changes.
    inherent_history = client.get(f"/api/risks/{risk_id}/scores?score_kind=inherent").get_json()
    assert len(inherent_history) == 1


def test_invalid_score_kind_over_http_is_refused_not_500(
        app, db_session, make_org, client, login_as):
    org = make_org("sec-arch-score-bad")
    architect = _security_architect(db_session, org)
    login_as(client, architect)

    created = client.post("/api/risks", json={
        "title": "Some risk", "likelihood": 2, "impact": 2,
    }).get_json()
    resp = client.post(
        f"/api/risks/{created['id']}/scores",
        json={"score_kind": "residual_but_typo", "likelihood": 1, "impact": 1},
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()
