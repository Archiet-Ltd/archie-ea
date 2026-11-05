"""Every risk writer checks all of its input before storing anything and answers bad input with a
plain 400; the investment ranking reaches every linked risk and reads the one effective score."""

import uuid
from types import SimpleNamespace

import pytest

from tests.test_risk_writers_agree import _application, _link, _risk, _solution, _user

pytestmark = pytest.mark.usefixtures("db_session")

STATUS_SENTENCE = "Status must be one of: open, mitigated, accepted, closed."


@pytest.mark.parametrize("status", ["bogus", ["open"], 5, {"a": 1}, "OPEN"])
def test_solution_risk_put_with_a_bad_status_stores_nothing_and_lists_the_statuses(
        app, db_session, make_org, client, login_as, status):
    from app.models.risk import Risk

    org = make_org("d3-put")
    solution = _solution(db_session, org)
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me", likelihood=2)
    risk.solution_id = solution.id
    db_session.flush()

    response = client.put("/solutions/%d/risks/%d" % (solution.id, risk.id),
                          json={"risk_name": "Changed", "probability": "high", "status": status})

    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["error"] == STATUS_SENTENCE, response.get_json()
    db_session.expire_all()
    stored = db_session.get(Risk, risk.id)
    assert (stored.title, stored.likelihood) == ("Keep me", 2)


@pytest.mark.parametrize("field,value,sentence", [
    ("owner", "o" * 200, "Owner must be 128 characters or fewer; this one is 200."),
    ("owner", ["a"], "Owner must be text."),
    ("owner", {"x": 1}, "Owner must be text."),
    ("description", ["a"], "Description must be text."),
    ("description", 5, "Description must be text."),
    ("mitigation_plan", ["a"], "Mitigation plan must be text."),
])
def test_owner_and_description_are_checked_by_every_writer(
        app, db_session, make_org, client, login_as, field, value, sentence):
    from app.models.risk import Risk

    org = make_org("d4")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep")
    before = Risk.query.filter_by(organization_id=org.id).count()

    patched = client.patch("/api/risks/%d" % risk.id, json={field: value})
    created = client.post("/api/risks", json={"title": "New", "likelihood": 2, "impact": 2, field: value})

    for response in (patched, created):
        assert response.status_code == 400, response.get_data(as_text=True)
        assert response.get_json()["error"] == sentence, response.get_json()
        assert response.get_json()["field"] == field
    db_session.expire_all()
    stored = db_session.get(Risk, risk.id)
    assert not getattr(stored, field)
    assert Risk.query.filter_by(organization_id=org.id).count() == before


def test_an_owner_at_the_column_limit_is_stored(app, db_session, make_org, client, login_as):
    org = make_org("d4-ok")
    login_as(client, _user(db_session, org))
    response = client.post("/api/risks", json={"title": "Fits", "likelihood": 2, "impact": 2, "owner": "o" * 128})
    assert response.status_code == 201, response.get_data(as_text=True)


def test_the_owner_input_carries_the_column_limit(app, db_session, make_org, client, login_as):
    org = make_org("d4-form")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Form")
    page = client.get("/risks/%d" % risk.id).get_data(as_text=True)
    owner_input = [line for line in page.split("<input") if 'owner"' in line.split(">")[0]]
    assert owner_input and 'maxlength="128"' in owner_input[0], owner_input[:1]


def test_a_risk_linked_to_an_application_with_no_element_reaches_the_ranking(app, db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org = make_org("d5")
    bare = _application(db_session, org, "Bare %s" % uuid.uuid4().hex[:4])
    bare.archimate_element_id = None
    db_session.flush()
    risk = _risk(db_session, org, "Linked to a bare application")
    _link(db_session, org, risk, bare)
    with tenant_ctx(org.id):
        found = InvestmentPrioritizationService()._risks_by_capability(
            {1: [SimpleNamespace(application_component_id=bare.id)]})
    assert [r["id"] for r in found.get(1, [])] == [risk.id], found


def test_the_ranking_reads_the_effective_score(app, db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org = make_org("d6")
    application = _application(db_session, org, "Eff %s" % uuid.uuid4().hex[:4])
    risk = _risk(db_session, org, "Residual differs", likelihood=2, impact=5)
    risk.residual_likelihood, risk.residual_impact = 4, 4
    db_session.flush()
    _link(db_session, org, risk, application)
    with tenant_ctx(org.id):
        found = InvestmentPrioritizationService()._risks_by_capability(
            {1: [SimpleNamespace(application_component_id=application.id)]})
    assert risk.score_text == "Residual 16"
    assert found[1][0]["exposure"] == 16, found
