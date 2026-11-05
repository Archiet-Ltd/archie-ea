"""Every writer answers a bad risk value with a plain 400 and stores nothing, and every
screen that lists the risks on an element reads them through risk_seeds (the element column
plus the application links, a closed risk reaching nothing)."""

import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    from app.models.user import User

    user = User(email="risk-w-%s@example.com" % uuid.uuid4().hex[:8], first_name="Risk", last_name="Writer",
                organization_id=org.id, confirmed=True)
    user.password = "not-used-in-tests-123"
    db_session.add(user)
    db_session.flush()
    return user


def _risk(db_session, org, title, *, element=None, status="open", likelihood=3, impact=3):
    from app.models.risk import Risk, RiskStatus

    row = Risk(title=title, likelihood=likelihood, impact=impact, organization_id=org.id,
               archimate_element_id=element if element is not None else None, status=RiskStatus(status))
    db_session.add(row)
    db_session.flush()
    return row


def _application(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _link(db_session, org, risk, application):
    from app.models.risk_entity_link import RiskEntityLink

    db_session.add(RiskEntityLink(risk_id=risk.id, entity_type="application", entity_id=application.id,
                                  organization_id=org.id))
    db_session.flush()


def _solution(db_session, org):
    from app.models.solution_models import Solution

    row = Solution(name="Writers %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


# ---- N3: the solution risk edit ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["", None, "   "])
def test_solution_risk_put_with_an_empty_name_answers_400_and_stores_nothing(
        app, db_session, make_org, client, login_as, name):
    from app.models.risk import Risk

    org = make_org("sol-put")
    solution = _solution(db_session, org)
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me")
    risk.solution_id = solution.id
    db_session.flush()
    created = {"id": risk.id}

    response = client.put("/solutions/%d/risks/%d" % (solution.id, created["id"]), json={"risk_name": name})

    assert response.status_code == 400, response.get_data(as_text=True)
    body = response.get_json()
    assert body["field"] == "title" and "Title is required" in body["error"], body
    assert Risk.query.filter_by(id=created["id"]).one().title == "Keep me"


# ---- N4: the one validator checks the title's type and length ----------------------------------

@pytest.mark.parametrize("title", ["x" * 300, ["a"], 42, {"a": 1}])
def test_a_title_that_does_not_fit_is_refused_on_update_and_create_and_nothing_is_stored(
        app, db_session, make_org, client, login_as, title):
    from app.models.risk import Risk

    org = make_org("title-fit")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me")
    count = Risk.query.filter_by(organization_id=org.id).count()

    update = client.patch("/api/risks/%d" % risk.id, json={"title": title})
    create = client.post("/api/risks", json={"title": title, "likelihood": 2, "impact": 2})

    for response in (update, create):
        assert response.status_code == 400, response.get_data(as_text=True)
        body = response.get_json()
        assert body["field"] == "title", body
        assert "Traceback" not in body["error"] and "DataError" not in body["error"]
    if isinstance(title, str):
        assert "255" in update.get_json()["error"]
    db_session.refresh(risk)
    assert risk.title == "Keep me"
    assert Risk.query.filter_by(organization_id=org.id).count() == count


def test_a_title_of_exactly_255_characters_is_stored(app, db_session, make_org, client, login_as):
    org = make_org("title-255")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Old")
    response = client.patch("/api/risks/%d" % risk.id, json={"title": "y" * 255})
    assert response.status_code == 200, response.get_data(as_text=True)


# ---- N9: PATCH validates every field before it stores any --------------------------------------

def test_patch_with_a_valid_field_and_a_bogus_status_stores_nothing_and_lists_the_statuses(
        app, db_session, make_org, client, login_as):
    org = make_org("patch-status")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me", likelihood=2)

    response = client.patch("/api/risks/%d" % risk.id, json={"likelihood": 5, "status": "bogus"})

    assert response.status_code == 400, response.get_data(as_text=True)
    body = response.get_json()
    assert body["field"] == "status"
    for allowed in ("open", "mitigated", "accepted", "closed"):
        assert allowed in body["error"], body
    assert "RiskStatus" not in body["error"], body
    db_session.refresh(risk)
    assert risk.likelihood == 2 and risk.status.value == "open"


def test_patch_with_a_bad_field_and_a_valid_status_stores_nothing(app, db_session, make_org, client, login_as):
    org = make_org("patch-status-2")
    login_as(client, _user(db_session, org))
    risk = _risk(db_session, org, "Keep me")
    response = client.patch("/api/risks/%d" % risk.id, json={"likelihood": 9, "status": "closed"})
    assert response.status_code == 400
    db_session.refresh(risk)
    assert risk.status.value == "open"


# ---- N6: the solution record and the investment ranking read risk_seeds -------------------------

def _seeded_estate(db_session, org):
    """A direct risk on the application's element, a risk linked only through the link table,
    and a closed risk linked to the same application."""
    application = _application(db_session, org, "Ledger %s" % uuid.uuid4().hex[:5])
    assert application.archimate_element_id is not None
    direct = _risk(db_session, org, "Direct on element", element=application.archimate_element_id)
    linked_only = _risk(db_session, org, "Linked only")
    _link(db_session, org, linked_only, application)
    closed = _risk(db_session, org, "Closed and linked", status="closed")
    _link(db_session, org, closed, application)
    return application, direct, linked_only, closed


def test_the_solution_record_lists_the_same_risks_for_an_element_as_risk_seeds(
        app, db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.solution_record_service import risks_for
    from app.services import risk_service

    org = make_org("record-seeds")
    solution = _solution(db_session, org)
    application, direct, linked_only, closed = _seeded_estate(db_session, org)
    element = application.archimate_element_id
    with tenant_ctx(org.id):
        wanted = set(risk_service.risk_seeds(org.id, [element]).risk_ids())
        groups = [{"elements": [{"id": element, "name": application.name}]}]
        shown = {row["id"] for row in risks_for(solution, groups)["rows"] if isinstance(row["id"], int)}

    assert wanted == {direct.id, linked_only.id}
    assert shown == wanted
    assert closed.id not in shown


def test_the_investment_ranking_lists_the_same_risks_for_an_application_as_risk_seeds(
        app, db_session, make_org, tenant_ctx):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )
    from app.services import risk_service

    org = make_org("invest-seeds")
    application, direct, linked_only, closed = _seeded_estate(db_session, org)
    mappings = {7: [SimpleNamespace(application_component_id=application.id)]}
    with tenant_ctx(org.id):
        wanted = set(risk_service.risk_seeds(org.id, [application.archimate_element_id]).risk_ids())
        found = InvestmentPrioritizationService()._risks_by_capability(mappings)

    shown = {r["id"] for r in found.get(7, [])}
    assert wanted == {direct.id, linked_only.id}
    assert shown == wanted
    assert closed.id not in shown
    assert all(r["exposure"] == 9 for r in found[7])


def test_no_second_derivation_of_risks_on_an_element_remains():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app" / "modules" / "solutions_strategic" / "v2" / "services"
    for name in ("solution_record_service.py", "investment_prioritization_service.py"):
        text = (root / name).read_text()
        assert "RiskEntityLink" not in text, name
        assert "Risk.archimate_element_id" not in text, name
