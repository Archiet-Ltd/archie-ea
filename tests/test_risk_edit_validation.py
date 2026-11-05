"""Editing a risk is validated on the server, writes are fenced to the signed-in organisation,
and one function decides which risks reach an element (a closed risk reaches nothing)."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    from app.models.user import User

    user = User(email="risk-edit-%s@example.com" % uuid.uuid4().hex[:8], first_name="Risk", last_name="Editor",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def _risk(db_session, org, title, *, element=None, status=None, likelihood=3, impact=3):
    from app.models.risk import Risk, RiskStatus

    row = Risk(title=title, likelihood=likelihood, impact=impact, organization_id=org.id,
               archimate_element_id=element.id if element is not None else None,
               status=RiskStatus(status) if status else RiskStatus.OPEN)
    db_session.add(row)
    db_session.flush()
    return row


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.mark.parametrize("payload,field,words", [
    ({"title": ""}, "title", "Title is required"),
    ({"title": "   "}, "title", "Title is required"),
    ({"likelihood": 0}, "likelihood", "Likelihood must be a whole number from 1 to 5"),
    ({"likelihood": 9}, "likelihood", "Likelihood must be a whole number from 1 to 5"),
    ({"likelihood": "abc"}, "likelihood", "Likelihood must be a whole number from 1 to 5"),
    ({"impact": 6}, "impact", "Impact must be a whole number from 1 to 5"),
    ({"impact": 2.5}, "impact", "Impact must be a whole number from 1 to 5"),
])
def test_an_invalid_edit_is_refused_in_plain_words_and_nothing_is_stored(
        app, db_session, make_org, client, login_as, payload, field, words):
    org = make_org("edit-validation")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me", likelihood=2, impact=4)
    before = (risk.title, risk.likelihood, risk.impact)

    response = client.patch("/api/risks/%d" % risk.id, json=payload)

    assert response.status_code == 400, response.get_data(as_text=True)
    body = response.get_json()
    assert words in body["error"], body
    assert "invalid literal" not in body["error"] and "int()" not in body["error"]
    assert body["field"] == field
    db_session.refresh(risk)
    assert (risk.title, risk.likelihood, risk.impact) == before


def test_a_valid_edit_is_stored(app, db_session, make_org, client, login_as):
    org = make_org("edit-valid")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Old", likelihood=2, impact=4)

    response = client.patch("/api/risks/%d" % risk.id, json={"title": " New title ", "likelihood": "5", "impact": 1})

    assert response.status_code == 200, response.get_data(as_text=True)
    db_session.refresh(risk)
    assert (risk.title, risk.likelihood, risk.impact) == ("New title", 5, 1)


@pytest.mark.parametrize("payload,field", [
    ({"title": "", "likelihood": 2, "impact": 2}, "title"),
    ({"title": "T", "likelihood": 0, "impact": 2}, "likelihood"),
    ({"title": "T", "likelihood": 2, "impact": 9}, "impact"),
    ({"title": "T", "likelihood": "abc", "impact": 2}, "likelihood"),
    ({"title": "T", "impact": 2}, "likelihood"),
])
def test_create_uses_the_same_check(app, db_session, make_org, client, login_as, payload, field):
    from app.models.risk import Risk

    org = make_org("create-validation")
    login_as(client, _user(db_session, org))

    response = client.post("/api/risks", json=payload)

    assert response.status_code == 400, response.get_data(as_text=True)
    assert response.get_json()["field"] == field
    assert Risk.query.filter_by(organization_id=org.id).count() == 0


def test_another_organisations_risk_and_link_ids_are_refused(
        app, db_session, make_org, client, login_as, tenant_ctx):
    from app.models.risk_entity_link import RiskEntityLink
    from app.services import risk_service

    mine, theirs = make_org("fence-mine"), make_org("fence-theirs")
    login_as(client, _user(db_session, mine))
    foreign_app = _application(db_session, theirs, "Their ledger")
    foreign_risk = _risk(db_session, theirs, "Their secret", likelihood=2, impact=2)
    my_risk = _risk(db_session, mine, "Mine")
    my_app = _application(db_session, mine, "My ledger")
    foreign_link = risk_service.add_risk_link(foreign_risk.id, "application", foreign_app.id,
                                              organization_id=theirs.id)
    db_session.flush()
    foreign_link_id = foreign_link.id if hasattr(foreign_link, "id") else foreign_link["id"]
    mine_id, theirs_id = mine.id, theirs.id
    ids = {"foreign_risk": foreign_risk.id, "my_risk": my_risk.id, "foreign_app": foreign_app.id, "my_app": my_app.id}
    # A get() is answered from the identity map without the tenant filter; a request starts cold.
    db_session.expunge_all()

    assert client.post("/api/risks/%d/links" % ids['foreign_risk'],
                       json={"entity_type": "application", "entity_id": ids['my_app']}).status_code == 404
    assert client.get("/api/risks/%d/links" % ids['foreign_risk']).status_code == 404
    assert client.post("/api/risks/%d/links" % ids['my_risk'],
                       json={"entity_type": "application", "entity_id": ids['foreign_app']}).status_code == 400
    assert client.delete("/api/risks/%d/links/%d" % (ids['foreign_risk'], foreign_link_id)).status_code == 404
    assert client.delete("/api/risks/%d/links/%d" % (ids['my_risk'], foreign_link_id)).status_code == 404
    for payload in ({"title": "Hijacked"}, {"likelihood": 5}, {"status": "closed"}):
        assert client.patch("/api/risks/%d" % ids['foreign_risk'], json=payload).status_code == 404
    assert client.get("/api/risks/%d" % ids['foreign_risk']).status_code == 404

    db_session.expire_all()
    with tenant_ctx(mine_id):
        assert RiskEntityLink.query.count() == 0
    with tenant_ctx(theirs_id):
        assert RiskEntityLink.query.count() == 1
    from app.models.risk import Risk

    with tenant_ctx(theirs_id):
        stored = Risk.query.filter_by(id=ids["foreign_risk"]).one()
        assert (stored.title, stored.likelihood) == ("Their secret", 2)


def test_one_function_decides_which_risks_reach_an_element_and_a_closed_risk_reaches_nothing(
        app, db_session, make_org, tenant_ctx):
    """Register, ranking, propagation and the answer agree: an open linked risk reaches the application,
    a closed one is listed (so the counts agree) but reaches nothing."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services import risk_service

    org = make_org("one-derivation")
    ledger = _application(db_session, org, "Calibration Ledger")
    db_session.flush()
    element_id = ledger.archimate_element_id
    assert element_id is not None
    open_risk = _risk(db_session, org, "Open linked")
    closed_risk = _risk(db_session, org, "Closed linked", status="closed")
    with tenant_ctx(org.id):
        risk_service.add_risk_link(open_risk.id, "application", ledger.id)
        risk_service.add_risk_link(closed_risk.id, "application", ledger.id)
        db_session.flush()

        pairs = risk_service.risk_seeds(org.id, [element_id]).pairs
        assert {p[0] for p in pairs} == {open_risk.id}

        answer = IntelligenceQueryService.risk_for_element(element_id)
        assert [r["risk_id"] for r in answer["risks"]] == [open_risk.id]

        propagation = IntelligenceQueryService.risk_propagation(element_id)
        assert [r["risk_id"] for r in propagation["rows"]] == [open_risk.id]

        assert [r["risk_id"] for r in IntelligenceQueryService.risks_on_element(element_id)] == [open_risk.id]

        ranking = {r["risk_id"]: r for r in risk_service.ranked_risks()["rows"]}
        assert set(ranking) == {open_risk.id, closed_risk.id}, "the register lists both, so its total agrees"
        assert ranking[closed_risk.id]["exposure"] is None
        assert "closed" in ranking[closed_risk.id]["reason"]
        assert risk_service.ranked_risks()["total"] == 2


def test_there_is_one_derivation_of_the_risks_reaching_an_element():
    """The three old derivations are gone: the answer, the propagation and the ranking all call risk_seeds."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    query = (root / "modules/intelligence/services/query_service.py").read_text()
    service = (root / "services/risk_service.py").read_text()
    assert query.count("risk_seeds(") >= 3
    assert "RiskEntityLink" not in query, "the link table is read in one place, risk_seeds"
    assert service.count("def risk_seeds") == 1
